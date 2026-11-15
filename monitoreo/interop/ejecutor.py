"""Ejecutor controlado de suites (v2.2.1): el único punto por el que Jarvis o Autonomous Worker
ejecutan pruebas de Cauce.

Garantías (cada una tiene su prueba en tests/test_interop_jarvis.py):
- Solo suites de jarvis/test-manifest.json; la solicitud trae un identificador, nunca un comando.
- Solo en proyectos autorizados: la carpeta de esta instalación y las que liste la variable
  CAUCE_JARVIS_RAICES (separadas por el separador de rutas del sistema).
- Entorno aislado: el proceso hijo recibe una lista corta de variables del sistema (PATH, TEMP…)
  más las fijas del manifiesto. Se descartan tokens, la URL de la BD, la del portal, proxies, etc.
  Con MONITOREO_IGNORAR_DOTENV=1 tampoco hereda el .env de la instalación. La BD por omisión es
  un SQLite temporal que se borra al terminar.
- PostgreSQL solo si se pide (--con-postgres), la variable MONITOREO_TEST_PG existe y el nombre de
  la base indica que es de pruebas (las pruebas VACÍAN esa base: nunca debe ser la real).
- Límite de tiempo y de salida; si se supera, se detiene el árbol de procesos completo.
- Cancelación segura: Ctrl+C / SIGTERM o un archivo CANCELAR en la carpeta de resultados.
- Una ejecución a la vez por carpeta de resultados (candado con PID; uno huérfano se recupera).
- Comprueba que el repositorio (git status) y data/ quedaron igual que antes.
- Resultado estructurado en resultados/jarvis/<id>/resultado.json (+ salida.txt y junit.xml).

Lo que NO hace: aislar la red a nivel del sistema operativo (las pruebas solo usan 127.0.0.1, pero
el ejecutor no lo puede impedir), fusionar, desplegar ni modificar código.

Códigos de salida: 0 aprobada · 1 fallida · 2 solicitud no permitida · 3 tiempo agotado ·
4 cancelada · 5 error interno · 6 ocupado (otra ejecución en curso).
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psutil

from . import manifiesto as mf
from .comun import (RAIZ, ErrorSolicitud, ahora_utc, escribir_json, estado_git, huella_directorio,
                    huella_git_status, sha256_archivo, version_proyecto)
from .saneado import sanear, sanear_texto

FORMATO_RESULTADO = "cauce.jarvis.run-result/1"
SALIDA_APROBADA, SALIDA_FALLIDA, SALIDA_SOLICITUD, SALIDA_TIEMPO, SALIDA_CANCELADA, SALIDA_ERROR, SALIDA_OCUPADO = range(7)
RESULTADO_POR_CODIGO = {SALIDA_APROBADA: "APROBADA", SALIDA_FALLIDA: "FALLIDA", SALIDA_TIEMPO: "TIEMPO_AGOTADO",
                        SALIDA_CANCELADA: "CANCELADA", SALIDA_ERROR: "ERROR"}

# Variables del sistema que el hijo necesita para funcionar (Windows y Linux). Nada más pasa.
VARIABLES_SISTEMA = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
                     "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                     "LANG", "LC_ALL", "LC_CTYPE", "TZ", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
                     "PLAYWRIGHT_BROWSERS_PATH", "VIRTUAL_ENV", "NODE_PATH", "XDG_CACHE_HOME")
# NODE_OPTIONS no pasa a propósito: permite cargar código arbitrario en Node (--require).
_BD_PRUEBAS = re.compile(r"(^|[_-])(test|tests|prueba|pruebas|ci)([_-]|$)", re.IGNORECASE)
GRACIA_S = 5.0


def raices_autorizadas() -> list[Path]:
    extra = [Path(x).resolve() for x in os.environ.get("CAUCE_JARVIS_RAICES", "").split(os.pathsep) if x.strip()]
    return [RAIZ.resolve(), *extra]


def comprobar_raiz(raiz: Path, autorizadas: list[Path] | None = None) -> Path:
    r = Path(raiz).resolve()
    if r not in (autorizadas if autorizadas is not None else raices_autorizadas()):
        raise ErrorSolicitud("ruta de proyecto no autorizada")
    if not (r / "jarvis" / mf.ARCHIVO).is_file():
        raise ErrorSolicitud("la ruta no contiene jarvis/test-manifest.json")
    return r


def url_pg_de_pruebas(url: str | None) -> str | None:
    """Acepta la URL de PostgreSQL solo si el nombre de la base indica que es de pruebas."""
    if not url:
        return None
    try:
        nombre = urlparse(url.replace("postgresql+psycopg", "postgresql")).path.lstrip("/")
    except ValueError:
        return None
    return url if url.startswith("postgresql") and _BD_PRUEBAS.search(nombre or "") else None


def entorno_hijo(m: mf.Manifiesto, suite: mf.Suite, bd_tmp: Path, con_postgres: bool,
                 origen: dict | None = None) -> tuple[dict, list[str]]:
    origen = os.environ if origen is None else origen
    env = {k: origen[k] for k in VARIABLES_SISTEMA if k in origen}
    avisos: list[str] = []
    env.update(m.entorno_fijo)
    env.update({"MONITOREO_IGNORAR_DOTENV": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1", "MONITOREO_DB_URL": f"sqlite:///{bd_tmp.as_posix()}",
                "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    if suite.postgres == "opcional" and con_postgres:
        pg = url_pg_de_pruebas(origen.get("MONITOREO_TEST_PG"))
        if pg:
            env["MONITOREO_TEST_PG"] = pg
        else:
            avisos.append("PostgreSQL pedido pero no usado: falta MONITOREO_TEST_PG o el nombre de la base no "
                          "indica que sea de pruebas (test/pruebas/ci). Las pruebas vacían esa base.")
    return env, avisos


class _Candado:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.tomado = False

    def __enter__(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.tomado = True
                return self
            except FileExistsError:
                try:
                    pid = int(self.ruta.read_text().strip() or "0")
                except (OSError, ValueError):
                    pid = 0
                if pid and psutil.pid_exists(pid) and pid != os.getpid():
                    raise ErrorOcupado() from None
                self.ruta.unlink(missing_ok=True)  # candado huérfano
        raise ErrorOcupado()

    def __exit__(self, *exc):
        if self.tomado:
            self.ruta.unlink(missing_ok=True)


class ErrorOcupado(Exception):
    pass


def detener_arbol(proc: subprocess.Popen, gracia_s: float = GRACIA_S) -> None:
    """Detiene el proceso y TODOS sus descendientes (pytest lanza servidores y navegadores)."""
    try:
        padre = psutil.Process(proc.pid)
        procesos = [padre, *padre.children(recursive=True)]
    except psutil.NoSuchProcess:
        return
    for p in procesos:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, vivos = psutil.wait_procs(procesos, timeout=gracia_s)
    for p in vivos:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(vivos, timeout=gracia_s)


def _lector(flujo, destino: Path, limite: int, estado: dict, cola: deque):
    with open(destino, "wb") as f:
        while True:
            bloque = flujo.read1(65536) if hasattr(flujo, "read1") else flujo.read(65536)
            if not bloque:
                break
            estado["bytes_totales"] += len(bloque)
            libre = limite - estado["bytes_escritos"]
            if libre > 0:
                f.write(bloque[:libre])
                estado["bytes_escritos"] += min(libre, len(bloque))
            cola.append(bloque)
        if estado["bytes_totales"] > estado["bytes_escritos"]:
            f.write(f"\n[salida truncada: {estado['bytes_totales'] - estado['bytes_escritos']} bytes "
                    f"descartados por el límite de {limite}]\n".encode())


def leer_junit(ruta: Path) -> dict | None:
    if not ruta.is_file():
        return None
    try:
        raiz = ET.parse(ruta).getroot()
    except ET.ParseError:
        return None
    suites = [raiz] if raiz.tag == "testsuite" else list(raiz.iter("testsuite"))
    c = {"total": 0, "aprobadas": 0, "fallidas": 0, "errores": 0, "omitidas": 0}
    for s in suites:
        c["total"] += int(s.get("tests", 0))
        c["fallidas"] += int(s.get("failures", 0))
        c["errores"] += int(s.get("errors", 0))
        c["omitidas"] += int(s.get("skipped", 0))
    c["aprobadas"] = max(0, c["total"] - c["fallidas"] - c["errores"] - c["omitidas"])
    return c


def _ultima_linea_resumen(texto: str) -> str:
    for linea in reversed(texto.splitlines()):
        t = linea.strip().strip("=").strip()
        if re.search(r"\b(passed|failed|error|errors|skipped|no tests ran)\b", t):
            return t[:300]
    return ""


def ejecutar(suite_id: str, *, raiz: Path | None = None, salida: Path | None = None, con_postgres: bool = False,
             autorizadas: list[Path] | None = None, cancelar: threading.Event | None = None,
             tiempo_s: int | None = None) -> tuple[int, dict | None]:
    """Ejecuta una suite. Devuelve (código de salida, resultado). Nunca lanza por la suite."""
    raiz = comprobar_raiz(raiz or RAIZ, autorizadas)
    m = mf.cargar(raiz)
    suite = m.suite(suite_id)
    limite_t = min(suite.tiempo_s, tiempo_s) if tiempo_s else suite.tiempo_s
    salida = Path(salida).resolve() if salida else raiz / "resultados" / "jarvis"
    _comprobar_salida(salida, raiz)
    if "escribe-data" in suite.etiquetas and _parece_instalacion_real(raiz):
        raise ErrorSolicitud(f"la suite {suite.id} escribe en data/ (tests/test_restart.py arranca el servidor real "
                             "en la carpeta del proyecto) y aquí data/ tiene una base de datos: ejecútala en una "
                             "copia de desarrollo")
    cancelar = cancelar or threading.Event()
    with _Candado(salida / ".ejecutor.lock"):
        return _ejecutar(m, suite, raiz, salida, con_postgres, cancelar, limite_t)


def _parece_instalacion_real(raiz: Path) -> bool:
    """data/ con una base SQLite o un .env que define MONITOREO_DB_URL: probablemente atiende personas."""
    d = raiz / "data"
    if d.is_dir() and any(d.glob("*.db")):
        return True
    env = raiz / ".env"
    try:
        return env.is_file() and "MONITOREO_DB_URL" in env.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return True


def _comprobar_salida(salida: Path, raiz: Path) -> None:
    """Los resultados van fuera del proyecto o en resultados/ (ignorado por git); nunca en data/."""
    try:
        rel = salida.relative_to(raiz)
    except ValueError:
        return
    if not rel.parts or rel.parts[0] != "resultados":
        raise ErrorSolicitud("la carpeta de resultados debe estar en resultados/ o fuera del proyecto")


def _ejecutar(m, suite, raiz, salida, con_postgres, cancelar, limite_t):
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    eid = f"{marca}-{suite.id}"
    dir_e = salida / eid
    dir_e.mkdir(parents=True)
    archivo_cancelar = [salida / "CANCELAR", dir_e / "CANCELAR"]
    antes_git, antes_data = huella_git_status(raiz), huella_directorio(raiz / "data")
    git_inicio = estado_git(raiz)
    tmp = Path(tempfile.mkdtemp(prefix="cauce-jarvis-"))
    junit = dir_e / "junit.xml"
    argv = [sys.executable if a == "{python}" else a for a in suite.argv] + [f"--junitxml={junit}"]
    env, avisos = entorno_hijo(m, suite, tmp / "aislada.db", con_postgres)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(tmp)
    estado = {"bytes_totales": 0, "bytes_escritos": 0}
    cola: deque = deque(maxlen=8)
    inicio_iso, t0 = ahora_utc(), time.monotonic()
    motivo, codigo_proc = None, None
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    previos = _instalar_senales(cancelar)
    try:
        proc = subprocess.Popen(argv, cwd=raiz, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **flags)
        hilo = threading.Thread(target=_lector, args=(proc.stdout, dir_e / "salida.txt", suite.salida_bytes,
                                                      estado, cola), daemon=True)
        hilo.start()
        while proc.poll() is None:
            if time.monotonic() - t0 > limite_t:
                motivo = "TIEMPO_AGOTADO"
            elif cancelar.is_set() or any(p.exists() for p in archivo_cancelar):
                motivo = "CANCELADA"
            if motivo:
                detener_arbol(proc)
                break
            time.sleep(0.2)
        codigo_proc = proc.wait()
        hilo.join(timeout=30)
    except OSError as e:
        motivo = "ERROR"
        avisos.append(f"no se pudo iniciar la suite: {type(e).__name__}")
    finally:
        _restaurar_senales(previos)
        shutil.rmtree(tmp, ignore_errors=True)
    duracion = round(time.monotonic() - t0, 3)
    conteo = leer_junit(junit)
    if motivo == "TIEMPO_AGOTADO":
        codigo = SALIDA_TIEMPO
    elif motivo == "CANCELADA":
        codigo = SALIDA_CANCELADA
    elif motivo == "ERROR" or codigo_proc not in (0, 1) or conteo is None:
        codigo = SALIDA_ERROR  # pytest 2-5: interrumpido, error interno, uso, sin pruebas
    else:
        codigo = SALIDA_APROBADA if codigo_proc == 0 else SALIDA_FALLIDA
    if codigo == SALIDA_APROBADA and conteo and (conteo["fallidas"] or conteo["errores"]):
        codigo = SALIDA_FALLIDA
    cola_txt = b"".join(cola)[-4096:].decode("utf-8", errors="replace")
    despues_git, despues_data = huella_git_status(raiz), huella_directorio(raiz / "data")
    archivo_salida = dir_e / "salida.txt"
    resultado = {
        "formato": FORMATO_RESULTADO,
        "id": eid,
        "proyecto": m.proyecto,
        "version": version_proyecto(),
        "suite": suite.id,
        "descripcion": suite.descripcion,
        "comando": list(suite.argv),
        "limites": {"tiempo_s": limite_t, "salida_bytes": suite.salida_bytes},
        "git": {"rama": git_inicio["rama"], "commit": git_inicio["commit"], "limpio": git_inicio["limpio"]},
        "inicio": inicio_iso,
        "fin": ahora_utc(),
        "duracion_s": duracion,
        "codigo_salida": codigo,
        "codigo_pytest": codigo_proc,
        "resultado": RESULTADO_POR_CODIGO[codigo],
        "conteo": conteo,
        "bases_de_datos": ["sqlite"] + (["postgresql"] if "MONITOREO_TEST_PG" in env else []),
        "salida": {"archivo": "salida.txt", "bytes": estado["bytes_totales"],
                   "truncada": estado["bytes_totales"] > estado["bytes_escritos"],
                   "sha256": sha256_archivo(archivo_salida) if archivo_salida.exists() else None,
                   "resumen_final": sanear_texto(_ultima_linea_resumen(cola_txt), raiz)},
        "junit": {"archivo": "junit.xml", "sha256": sha256_archivo(junit) if junit.exists() else None},
        "integridad": {"git_sin_cambios": antes_git == despues_git, "data_sin_cambios": antes_data == despues_data},
        "avisos": avisos,
    }
    if not resultado["integridad"]["git_sin_cambios"] or not resultado["integridad"]["data_sin_cambios"]:
        resultado["avisos"].append("la ejecución cambió el repositorio o data/: revisar antes de confiar en ella")
    resultado = sanear(resultado, raiz)
    escribir_json(dir_e / "resultado.json", resultado)
    return codigo, resultado


def _instalar_senales(cancelar: threading.Event):
    if threading.current_thread() is not threading.main_thread():
        return None
    previos = {}

    def manejar(signum, frame):  # noqa: ARG001
        cancelar.set()

    for nombre in ("SIGINT", "SIGTERM", "SIGBREAK"):
        s = getattr(signal, nombre, None)
        if s is not None:
            try:
                previos[s] = signal.signal(s, manejar)
            except (ValueError, OSError):
                pass
    return previos


def _restaurar_senales(previos):
    for s, h in (previos or {}).items():
        try:
            signal.signal(s, h)
        except (ValueError, OSError):
            pass

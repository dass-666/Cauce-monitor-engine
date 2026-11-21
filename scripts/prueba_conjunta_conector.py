#!/usr/bin/env python3
"""Prueba conjunta Monitor <-> Conector Universal (cauce.conector.v1). HERRAMIENTA DE PRUEBAS, no flujo operativo.

- No usa ni activa `aislamiento.puede_lanzar()` (sigue en False). Lanza el conector REAL como subproceso solo aquí,
  en un directorio temporal, con entorno mínimo, formulario de laboratorio en 127.0.0.1 y datos ficticios.
- Monitor aporta: esquemas, validar_solicitud, validar_respuesta (filtro PII, huella) y el Registro de estados.
- El conector se lee de una copia local de SOLO LECTURA indicada en CONECTOR_REPO (commit esperado: aa1bb02).
- Linux: además se controla el navegador por CDP (envoltorio del ejecutable) para actuar como la persona
  (cerrar la ventana) y para leer qué quedó escrito en el formulario. En Windows esos casos se omiten (ver informe).
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from monitoreo.conector import aislamiento, contrato as C            # noqa: E402
from monitoreo.conector.registro import ErrorRegistro, Registro       # noqa: E402

CONECTOR = Path(os.environ.get("CONECTOR_REPO", "")).resolve() if os.environ.get("CONECTOR_REPO") else None
# Datos FICTICIOS; el prefijo «zz» permite buscarlos en cualquier salida
VALORES = {"nombre": "Zzmariana", "apellidos": "Zzejemplo Prueba", "email": "zz.mariana@example.com",
           "telefono": "5587654321", "pais": "MX", "acepta": "si"}
CAMPOS = {"nombre": "registro::nombre", "apellidos": "registro::apellidos", "email": "registro::email",
          "telefono": "registro::telefono", "pais": "registro::pais", "acepta": "registro::acepta_terminos"}
CAMPO_DOM = {"nombre": "nombre", "apellidos": "apellidos", "email": "email", "telefono": "telefono", "pais": "pais"}
ES_LINUX = sys.platform.startswith("linux")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sitio:
    """Formulario de laboratorio local. Cuenta peticiones por método: un POST sería un envío."""

    def __init__(self, paginas: dict[str, str]):
        self.paginas, self.peticiones = dict(paginas), []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _resp(self):
                outer.peticiones.append((self.command, self.path.split("?")[0]))
                cuerpo = outer.paginas.get(self.path.split("?")[0])
                if cuerpo is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = cuerpo.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _resp

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def metodos(self, ruta: str | None = None) -> list[str]:
        return [m for m, r in self.peticiones if ruta in (None, r) and r != "/favicon.ico"]

    def cerrar(self):
        self.srv.shutdown()


def _chromium_real() -> str:
    """Chromium que usará el conector: el de Playwright si existe; si no (versión de Playwright distinta), uno instalado."""
    import glob
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if Path(p.chromium.executable_path).exists():
                return str(p.chromium.executable_path)
    except Exception:
        pass
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    c = sorted(glob.glob(os.path.join(base, "chromium-*", "chrome-linux*", "chrome")))
    return c[-1] if c else ""


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Resultado:
    def __init__(self, sol, stdout, stderr, codigo, resp, valores_pagina=None, nota=""):
        self.sol, self.stdout, self.stderr, self.codigo = sol, stdout, stderr, codigo
        self.resp, self.valores_pagina, self.nota = resp, valores_pagina, nota


class Entorno:
    """Directorio temporal aislado + servidor de laboratorio + perfil aprobado por una 'persona' (aprobar-perfil)."""

    def __init__(self, tmp: Path | None = None, html: str | None = None):
        if CONECTOR is None or not (CONECTOR / "cauce_form_connector").is_dir():
            raise RuntimeError("defina CONECTOR_REPO con una copia de solo lectura del Conector Universal")
        self.dir = Path(tmp or tempfile.mkdtemp(prefix="cauce_conjunta_"))
        for d in ("perfiles", "estado", "trabajo", "home"):
            (self.dir / d).mkdir(parents=True, exist_ok=True)
        # temporal del navegador: ruta CORTA (el socket de Chromium en Linux tiene un límite de ~100 caracteres)
        self.tmp = Path(tempfile.mkdtemp(prefix="cj", dir="/tmp" if ES_LINUX else None))
        lab = CONECTOR / "lab_forms"
        self.html = html or (lab / "01_tradicional.html").read_text(encoding="utf-8")
        self.sitio = Sitio({"/f.html": self.html, "/captcha.html": (lab / "04_captcha_simulado.html").read_text("utf-8")})
        (self.dir / "hosts.json").write_text('{"allowed_hosts": []}', encoding="utf-8")
        self.puerto_cdp = _puerto_libre()
        self.registro = Registro()
        self.url = self.sitio.base + "/f.html"
        self.info = self.aprobar(self.url)
        self.sitio.peticiones.clear()                  # solo cuentan las peticiones de las pruebas, no la aprobación del perfil

    # -- entorno mínimo del hijo: sin variables de Monitor, sin .env, sin BD
    def env(self, *, cdp: bool = False) -> dict:
        e = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.dir / "home"), "TMPDIR": str(self.tmp),
             "TEMP": str(self.tmp), "TMP": str(self.tmp), "PYTHONPATH": str(CONECTOR),
             "CAUCE_PROFILES_DIR": str(self.dir / "perfiles"), "CAUCE_STATE_DIR": str(self.dir / "estado"),
             "CAUCE_ALLOWED_HOSTS_FILE": str(self.dir / "hosts.json"), "CAUCE_AUDIT_PATH": str(self.dir / "audit.jsonl"),
             "CAUCE_HEADLESS": "1", "CAUCE_TYPING_DELAY_MS": "5", "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PLAYWRIGHT_BROWSERS_PATH", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "PROGRAMFILES", "PROGRAMDATA"):
            if k in os.environ and k not in e:
                e[k] = os.environ[k]
        if "PLAYWRIGHT_BROWSERS_PATH" not in e and ES_LINUX:      # HOME apunta al temporal: fijar dónde está Chromium
            e["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / ".cache" / "ms-playwright")
        real = _chromium_real()
        if ES_LINUX and real:                      # envoltorio: --no-sandbox solo si es root (contenedores) y CDP si se pide
            e["CAUCE_BROWSER_PATH"] = self._envoltorio(real)
        elif real:
            e["CAUCE_BROWSER_PATH"] = real
        if cdp:
            e["CDP_PORT"] = str(self.puerto_cdp)
        return e

    def _envoltorio(self, real: str) -> str:
        """Arranca el MISMO Chromium; añade el puerto de depuración si CDP_PORT existe y --no-sandbox si se corre como root."""
        w = self.dir / "chromium_envoltorio.sh"
        extra = " --no-sandbox" if hasattr(os, "geteuid") and os.geteuid() == 0 else ""
        w.write_text(f'#!/bin/sh\nif [ -n "$CDP_PORT" ]; then exec "{real}" "$@"{extra} --remote-debugging-port="$CDP_PORT"; '
                     f'else exec "{real}" "$@"{extra}; fi\n', encoding="utf-8")
        w.chmod(0o755)
        return str(w)

    def aprobar(self, url: str, nombre: str = "perfil_lab", formulario_id: str = "frm_lab") -> dict:
        """Lo que hace una persona: `aprobar-perfil` sobre el formulario real; imprime sha256 y firma v2."""
        campos = self.dir / "campos_aprobados.json"
        campos.write_text(json.dumps(CAMPOS), encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "cauce_form_connector", "aprobar-perfil", "--url", url, "--perfil", nombre,
                            "--formulario-id", formulario_id, "--aprobado-por", "rev_lab", "--campos", str(campos)],
                           cwd=self.dir / "trabajo", env=self.env(), capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"aprobar-perfil falló ({p.returncode}): {p.stderr[-600:]}")
        d = json.loads(p.stdout)
        return {"perfil": nombre, "sha": d["perfil_sha256"], "firma": d["firma_estructura_esperada"],
                "formulario_id": formulario_id, "url": url, "archivo": Path(d["archivo"])}

    def solicitud(self, *, valores=None, espera=0, ahora=None, **over) -> dict:
        ahora = ahora or datetime.now(timezone.utc)
        i = self.info
        sol = {"contrato": C.VERSION, "tipo": "preparar_formulario", "solicitud_id": "sol_" + secrets.token_hex(8),
               "emitida_en": iso(ahora - timedelta(seconds=5)), "caduca_en": iso(ahora + timedelta(seconds=600)),
               "autorizacion": {"ref": "aut_lab", "formulario_id": i["formulario_id"], "otorgada_por_ref": "rev_lab",
                                "vigente_hasta": (ahora + timedelta(days=2)).date().isoformat(),
                                "consentimiento_vigente": True},
               "formulario": {"formulario_id": i["formulario_id"], "url": i["url"], "perfil": i["perfil"],
                              "perfil_sha256": i["sha"], "firma_estructura_esperada": i["firma"]},
               "campos": dict(VALORES if valores is None else valores),
               "opciones": {"espera_revision_max_s": espera, "pausa_tecla_ms": 0}}
        sol.update(over)
        return sol

    # ------------------------------------------------------------------ Monitor: puerta simulada + lanzamiento de PRUEBAS
    def autorizar(self, sol: dict) -> None:
        """Puerta simulada de Monitor (consentimiento y ventana); NO es la puerta operativa ni activa puede_lanzar()."""
        ahora = datetime.now(timezone.utc)
        errores = C.validar_solicitud(sol, ahora)
        if errores:
            raise ErrorRegistro("SOLICITUD_INVALIDA", C.resumen_error_seguro(errores))
        if not sol["autorizacion"]["consentimiento_vigente"]:
            raise ErrorRegistro("CONSENTIMIENTO_AUSENTE", "sin consentimiento vigente no se lanza")
        self.registro.crear(sol, "clave-" + sol["solicitud_id"], ahora)
        self.registro.mover(sol["solicitud_id"], "AUTORIZADA", ahora)

    def lanzar(self, sol: dict, accion=None, *, cdp: bool = False, plazo_s: float = 90.0,
               registrar: bool = True) -> Resultado:
        """Lanza UN proceso `preparar` con la solicitud por stdin. `accion(pagina_cdp, proceso)` actúa como la persona."""
        if registrar:                                   # False: ensayo de que el CONECTOR rechaza por sí solo un reenvío
            self.registro.mover(sol["solicitud_id"], "LANZADA", datetime.now(timezone.utc))
        proc = subprocess.Popen([sys.executable, "-m", "cauce_form_connector", "preparar"], cwd=self.dir / "trabajo",
                                env=self.env(cdp=cdp), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        proc.stdin.write(json.dumps(sol))
        proc.stdin.close()
        salida = {}

        def leer(nombre, f):
            salida[nombre] = f.read()
        hilos = [threading.Thread(target=leer, args=(n, f), daemon=True) for n, f in (("o", proc.stdout), ("e", proc.stderr))]
        [h.start() for h in hilos]
        vistos, nota = None, ""
        if accion:
            try:
                vistos, nota = accion(self, proc)
            except Exception as ex:                       # un fallo del arnés no debe dejar procesos huérfanos
                nota = f"arnes:{type(ex).__name__}"
        try:
            proc.wait(timeout=plazo_s)
        except subprocess.TimeoutExpired:
            _matar_arbol(proc)
            nota += " TIEMPO_ARNES"
        [h.join(5) for h in hilos]
        out, err = salida.get("o", ""), salida.get("e", "")
        resp = None
        if out.strip():
            try:
                resp = json.loads(out)
            except ValueError:
                nota += " STDOUT_NO_JSON"
        return Resultado(sol, out, err, proc.returncode, resp, vistos, nota.strip())

    def recibir(self, r: Resultado) -> dict:
        """Monitor valida y registra lo recibido (o declara INCIERTA si no hubo respuesta)."""
        ahora = datetime.now(timezone.utc)
        sid = r.sol["solicitud_id"]
        if r.resp is None:
            return self.registro.proceso_terminado_sin_respuesta(sid, ahora)
        return self.registro.recibir(r.sol, r.resp, ahora, r.codigo)

    def cerrar(self):
        self.sitio.cerrar()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hallazgos_en_archivos(self) -> list[str]:
        """Busca los valores ficticios en TODO el directorio temporal (perfiles, estado, auditoría, trabajo, tmp)."""
        malos = []
        for f in list(self.dir.rglob("*")) + list(self.tmp.rglob("*")):
            if f.is_file() and f.name not in ("campos_aprobados.json",):
                try:
                    t = f.read_bytes().decode("utf-8", "ignore")
                except OSError:
                    continue
                malos += [f"{f.name}:{v}" for v in VALORES.values() if len(v) >= 3 and v in t]
        return malos


def _matar_arbol(proc: subprocess.Popen) -> None:
    try:
        import psutil
        p = psutil.Process(proc.pid)
        for h in p.children(recursive=True):
            h.kill()
        p.kill()
    except Exception:
        proc.kill()


# ---------------------------------------------------------------------------- acciones de la 'persona' (solo Linux, por CDP)
def _pagina(ent: Entorno, t: float = 40.0):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        try:
            br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{ent.puerto_cdp}")
            for ctx in br.contexts:
                for pg in ctx.pages:
                    if pg.url.startswith(ent.sitio.base + "/f.html"):
                        return pw, br, pg
        except Exception:
            pass
        time.sleep(0.3)
    pw.stop()
    raise TimeoutError("sin pagina")


def _valores(pg) -> dict:
    return pg.evaluate("Object.fromEntries([...document.querySelectorAll('input,textarea,select')]"
                       ".filter(e=>e.type!=='radio'&&e.type!=='hidden').map(e => [e.name||e.id, e.type==='checkbox'?e.checked:e.value]))")


def _esperar_relleno(pg, t: float = 40.0) -> dict:
    fin = time.monotonic() + t
    v = {}
    while time.monotonic() < fin:
        v = _valores(pg)
        if v.get("email") == VALORES["email"] and v.get("acepta_terminos") is True:
            time.sleep(0.5)
            return _valores(pg)
        time.sleep(0.2)
    return v


def persona_cierra_ventana(ent, proc):
    pw, br, pg = _pagina(ent)
    try:
        v = _esperar_relleno(pg)
        pg.close()                                      # la persona cierra la ventana
        return v, "ventana_cerrada"
    finally:
        pw.stop()


def navegador_muere(ent, proc):
    pw, br, pg = _pagina(ent)
    try:
        v = _esperar_relleno(pg)
    finally:
        pw.stop()
    import psutil
    for h in psutil.Process(proc.pid).children(recursive=True):
        if "chrom" in h.name().lower() and "--type=" not in " ".join(h.cmdline()):
            h.kill()                                    # el navegador desaparece (no es la persona quien cierra)
            return v, "navegador_matado"
    return v, "navegador_no_encontrado"


def monitor_cancela(ent, proc):
    pw, br, pg = _pagina(ent)
    try:
        v = _esperar_relleno(pg)
    finally:
        pw.stop()
    proc.send_signal(signal.SIGTERM)                    # Monitor revoca la autorización a media ejecución
    return v, "terminado_por_monitor"


def persona_no_hace_nada(ent, proc):
    pw, br, pg = _pagina(ent)
    try:
        return _esperar_relleno(pg), "sin_accion"
    finally:
        pw.stop()


def _escenario_valores(r: Resultado) -> dict:
    return r.valores_pagina or {}


if __name__ == "__main__":
    sys.exit("Use: python -m pytest tests/test_prueba_conjunta.py (con CONECTOR_REPO definido)")

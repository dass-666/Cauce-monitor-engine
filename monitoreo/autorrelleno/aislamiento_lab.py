"""Aislamiento de Forms en el LABORATORIO: Ventanilla lanza Forms como OTRA cuenta del sistema y, antes de cada
lanzamiento, COMPRUEBA que esa cuenta no puede leer los datos privados de Monitor.

Se activa solo con `MONITOREO_AUTORRELLENO_AISLAMIENTO=cuenta_restringida` dentro del laboratorio de autorrellenado
(`MONITOREO_AUTORRELLENO_LAB=1`, nunca en producción: lo garantiza `adaptador.configuracion()`). Sin la variable el
laboratorio funciona como antes (Forms con el mismo usuario que Monitor).

Qué hace, en este orden (docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md, fase F3):
1. Configuración: la cuenta (`MONITOREO_FORMS_CUENTA`) y su carpeta de trabajo (`MONITOREO_FORMS_TRABAJO`, donde
   van TEMP, HOME y el perfil de Chromium). En Windows la clave de la cuenta se lee de un archivo cifrado con DPAPI
   de la cuenta de Monitor (`MONITOREO_FORMS_CLAVE_DPAPI`, se crea con scripts/guardar_clave_conector.py); nunca va
   en el `.env`. En Linux (solo pruebas en contenedor/CI) Monitor debe correr como root para cambiar de usuario.
2. Comprobación ACTIVA antes de cada lanzamiento (`verificar()`): Monitor ejecuta una sonda COMO la cuenta, con el
   mismo lanzador y el mismo Python que usará Forms. La sonda intenta abrir la base, listar la carpeta de datos, los
   respaldos y el diario, leer la clave del diario, el `.env`, las copias externas, y escribir en el código de Monitor
   y de Forms. Solo si TODO da «permiso denegado», la cuenta es otra distinta de la de Monitor y puede escribir en su
   carpeta de trabajo, se lanza Forms. Cualquier otra cosa (legible, error raro, sin respuesta) = no se lanza
   (falla cerrado) con el código AISLAMIENTO_NO_VERIFICADO.
3. Antes de lanzar se buscan procesos vivos de la cuenta (huérfanos de un Monitor que murió): se terminan; si no se
   puede, no se lanza (CUENTA_OCUPADA).

Límites (no afirmar lo contrario):
- `conector.aislamiento.puede_lanzar()` SIGUE en False: esto es laboratorio, con datos ficticios.
- Entre la comprobación y el lanzamiento pasan milisegundos: si alguien cambia los permisos justo ahí, no se detecta
  hasta el siguiente lanzamiento.
- Un administrador o SYSTEM (o root) puede leerlo todo; la cuenta del conector nunca debe serlo.
- Las rutas que se comprueban son las que Monitor conoce (base, datos, respaldos, diario, .env, copias). Otra
  carpeta con datos (una exportación a mano, por ejemplo) se añade con `MONITOREO_AISLAMIENTO_RUTAS_EXTRA`.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

VARIABLE_MODO = "MONITOREO_AUTORRELLENO_AISLAMIENTO"
MODO_CUENTA = "cuenta_restringida"
VARIABLE_CUENTA = "MONITOREO_FORMS_CUENTA"
VARIABLE_CLAVE = "MONITOREO_FORMS_CLAVE_DPAPI"
VARIABLE_TRABAJO = "MONITOREO_FORMS_TRABAJO"
VARIABLE_EXTRA = "MONITOREO_AISLAMIENTO_RUTAS_EXTRA"
ES_WINDOWS = sys.platform == "win32"
PLAZO_SONDA_S = 60.0
_ENTROPIA = b"cauce-cuenta-conector-v1"

# Variables que apuntarían al perfil de la cuenta de Monitor: en modo aislado se sustituyen por la carpeta de trabajo.
_PERFIL = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP")
_SESION = ("XDG_RUNTIME_DIR", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")


class ErrorAislamiento(Exception):
    def __init__(self, codigo: str, mensaje: str, http: int = 409, informe: dict | None = None):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.http, self.informe = codigo, mensaje, http, informe


@dataclass
class ConfigAislamiento:
    cuenta: str
    trabajo: Path
    clave_archivo: Path | None = None
    rutas_extra: list = field(default_factory=list)

    def vista(self) -> dict:
        return {"modo": MODO_CUENTA, "cuenta": self.cuenta}


# ------------------------------------------------------------------------------------------- configuración
def leer_config(env: dict) -> ConfigAislamiento | None:
    modo = (env.get(VARIABLE_MODO) or "").strip().lower()
    if not modo:
        return None
    if modo != MODO_CUENTA:
        raise ErrorAislamiento("AISLAMIENTO_INVALIDO", f"{VARIABLE_MODO} solo admite «{MODO_CUENTA}».", 403)
    cuenta = (env.get(VARIABLE_CUENTA) or "").strip()
    trabajo = (env.get(VARIABLE_TRABAJO) or "").strip()
    if not cuenta or not trabajo:
        raise ErrorAislamiento("AISLAMIENTO_INCOMPLETO", f"Faltan {VARIABLE_CUENTA} o {VARIABLE_TRABAJO}.", 403)
    clave = None
    if ES_WINDOWS:
        c = (env.get(VARIABLE_CLAVE) or "").strip()
        if not c or not Path(c).is_file():
            raise ErrorAislamiento("AISLAMIENTO_INCOMPLETO", f"Falta el archivo de clave cifrada ({VARIABLE_CLAVE}): "
                                   "créalo con scripts\\guardar_clave_conector.py.", 403)
        clave = Path(c)
    else:
        try:
            import pwd
            pw = pwd.getpwnam(cuenta)
        except (ImportError, KeyError):
            raise ErrorAislamiento("CUENTA_NO_EXISTE", "La cuenta del conector no existe en este equipo.", 403)
        if os.geteuid() != 0:
            raise ErrorAislamiento("AISLAMIENTO_NO_DISPONIBLE", "En Linux el laboratorio aislado necesita que Monitor "
                                   "corra como root para lanzar Forms con otra cuenta (solo pruebas).", 403)
        if pw.pw_uid == 0:
            raise ErrorAislamiento("AISLAMIENTO_INVALIDO", "La cuenta del conector no puede ser root.", 403)
    extra = [Path(x) for x in (env.get(VARIABLE_EXTRA) or "").split(os.pathsep) if x.strip()]
    return ConfigAislamiento(cuenta, Path(trabajo), clave, extra)


def entorno(env_forms: dict, cfg: ConfigAislamiento) -> dict:
    """Entorno de Forms en modo aislado: nada del perfil de la cuenta de Monitor; TEMP/HOME en la carpeta de trabajo."""
    out = {k: v for k, v in env_forms.items() if k not in _PERFIL and k not in _SESION}
    t = str(cfg.trabajo)
    tmp = str(cfg.trabajo / "tmp")
    out.update({"HOME": t, "USERPROFILE": t, "APPDATA": t, "LOCALAPPDATA": t, "TEMP": tmp, "TMP": tmp})
    return out


# ------------------------------------------------------------------------------------------- rutas privadas
def rutas_privadas(cfg: ConfigAislamiento) -> list[dict]:
    """Lo que la cuenta NO debe poder abrir. `tipo`: archivo (leer 1 byte) o carpeta (listar). Solo las que existen."""
    from .. import recuperacion
    from ..config import BASE_DIR, settings
    out: list[dict] = []

    def add(ref: str, p: Path | None, tipo: str | None = None):
        if p is None:
            return
        p = Path(p)
        if not p.exists():
            return
        out.append({"ref": ref, "ruta": str(p), "tipo": tipo or ("carpeta" if p.is_dir() else "archivo")})
    from sqlalchemy.engine import make_url
    try:
        u = make_url(settings.db_url)
        db = Path(u.database) if u.get_backend_name() == "sqlite" and u.database not in (None, "", ":memory:") else None
    except Exception:  # noqa: BLE001 - una URL rara no puede abrir el paso: sin base no hay «base» comprobada
        db = None
    if db is not None:
        add("base", db, "archivo")
        for suf in ("-wal", "-shm", "-journal"):
            add(f"base{suf}", Path(str(db) + suf), "archivo")
    add("carpeta_de_datos", settings.data_dir, "carpeta")
    add("respaldos", settings.data_dir / "respaldos", "carpeta")
    add("claves", settings.data_dir / "claves", "carpeta")
    add("clave_del_diario", recuperacion.ruta_clave(), "archivo")
    add("diario", recuperacion.carpeta(), "carpeta")
    copia = recuperacion.ruta_copia()
    add("copia_del_diario", copia.parent if copia else None, "carpeta")
    ext = (os.environ.get("MONITOREO_RESPALDO_EXTERNO") or "").strip()
    add("respaldo_externo", Path(ext) if ext else None, "carpeta")
    add(".env", BASE_DIR / ".env", "archivo")
    for i, p in enumerate(cfg.rutas_extra):
        add(f"extra_{i + 1}", p)
    return out


def rutas_no_escribibles(forms_dir: str | None) -> list[dict]:
    from ..config import BASE_DIR
    out = [{"ref": "codigo_de_monitor", "ruta": str(BASE_DIR / "monitoreo")}]
    if forms_dir:
        out.append({"ref": "codigo_de_forms", "ruta": str(forms_dir)})
    return out


# La sonda corre COMO la cuenta, con el Python de Forms. Solo biblioteca estándar; llega por stdin (no hace falta que
# la cuenta pueda leer el código de Monitor). Nunca devuelve contenido: solo «denegado / legible / error».
SONDA = r'''
import json, os, sys, uuid
d = json.loads(sys.stdin.read())
def usuario():
    if sys.platform == "win32":
        import ctypes
        b = ctypes.create_unicode_buffer(256); n = ctypes.c_ulong(256)
        ctypes.windll.advapi32.GetUserNameW(b, ctypes.byref(n)); return b.value
    import pwd; return pwd.getpwuid(os.getuid()).pw_name
def intento(fn):
    try:
        fn(); return "PERMITIDO"
    except PermissionError:
        return "DENEGADO"
    except FileNotFoundError:
        return "NO_EXISTE"
    except Exception as e:
        return "ERROR_" + type(e).__name__
def leer(p):
    with open(p, "rb") as f: f.read(1)
def escribir(carpeta):
    p = os.path.join(carpeta, ".sonda_" + uuid.uuid4().hex)
    with open(p, "wb") as f: f.write(b"x")
    os.remove(p)
r = {"usuario": usuario(), "privadas": [], "escritura": []}
for x in d["privadas"]:
    fn = (lambda p=x["ruta"]: os.listdir(p)) if x["tipo"] == "carpeta" else (lambda p=x["ruta"]: leer(p))
    r["privadas"].append({"ref": x["ref"], "resultado": intento(fn)})
for x in d["no_escribibles"]:
    r["escritura"].append({"ref": x["ref"], "resultado": intento(lambda p=x["ruta"]: escribir(p))})
r["trabajo"] = intento(lambda: escribir(d["trabajo"]))
print(json.dumps(r))
'''


def _usuario_actual() -> str:
    if ES_WINDOWS:
        import ctypes
        b = ctypes.create_unicode_buffer(256)
        n = ctypes.c_ulong(256)
        ctypes.windll.advapi32.GetUserNameW(b, ctypes.byref(n))
        return b.value
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def evaluar_sonda(salida: dict | None, cfg: ConfigAislamiento, privadas: list[dict]) -> dict:
    """Veredicto a partir de la respuesta de la sonda. Solo «DENEGADO» cuenta como protegido."""
    fallos = []
    if not isinstance(salida, dict):
        return {"ok": False, "fallos": ["SONDA_SIN_RESPUESTA"], "comprobadas": 0}
    usuario = str(salida.get("usuario") or "")
    if not usuario or usuario.lower() == _usuario_actual().lower():
        fallos.append("MISMA_CUENTA_QUE_MONITOR")
    if usuario.lower().split("\\")[-1] != cfg.cuenta.lower().split("\\")[-1]:
        fallos.append("CUENTA_INESPERADA")
    vistas = {x.get("ref"): x.get("resultado") for x in salida.get("privadas", []) if isinstance(x, dict)}
    for p in privadas:
        res = vistas.get(p["ref"])
        if res != "DENEGADO":
            fallos.append(f"{p['ref']}:{res or 'SIN_DATO'}")
    for x in salida.get("escritura", []):
        if x.get("resultado") != "DENEGADO":
            fallos.append(f"escribir_{x.get('ref')}:{x.get('resultado')}")
    if salida.get("trabajo") != "PERMITIDO":
        fallos.append(f"trabajo:{salida.get('trabajo')}")
    if not privadas:
        fallos.append("SIN_RUTAS_PRIVADAS")
    return {"ok": not fallos, "fallos": fallos[:20], "comprobadas": len(privadas),
            "escritura_comprobada": len(salida.get("escritura", []))}


# ------------------------------------------------------------------------------------------- lanzador y clave
def _dpapi(datos: bytes, cifrar: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if cifrar else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    e, _b1 = blob(datos)
    ent, _b2 = blob(_ENTROPIA)
    s = BLOB()
    if not fn(ctypes.byref(e), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(s)):
        raise ErrorAislamiento("CLAVE_NO_DISPONIBLE", "No se pudo abrir la clave de la cuenta del conector (DPAPI): "
                               "¿se creó con otra cuenta de Windows?", 403)
    try:
        return ctypes.string_at(s.pbData, s.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(s.pbData, ctypes.c_void_p))


def proteger_clave(clave: str) -> bytes:
    if not ES_WINDOWS:
        raise ErrorAislamiento("AISLAMIENTO_NO_DISPONIBLE", "La clave cifrada solo existe en Windows (DPAPI).", 403)
    return _dpapi(clave.encode("utf-8"), True)


def leer_clave(ruta: Path) -> str:
    return _dpapi(Path(ruta).read_bytes(), False).decode("utf-8")


def fabrica(cfg: ConfigAislamiento):
    """El lanzador que ejecuta procesos COMO la cuenta: Windows = CreateProcessWithLogonW + Job Object;
    Linux (solo pruebas) = cambio de usuario al lanzar, mismo control del árbol que el lanzador de laboratorio."""
    from . import lanzador_restringido as LR
    if ES_WINDOWS:
        return LR.LanzadorRestringido(cuenta=cfg.cuenta, clave=leer_clave(cfg.clave_archivo), modo_laboratorio=True)
    return LR.LanzadorCuentaPosix(cuenta=cfg.cuenta, modo_laboratorio=True)


# ------------------------------------------------------------------------------------------- procesos huérfanos
def procesos_de_la_cuenta(cuenta: str) -> list:
    import psutil
    nombre = cuenta.lower().split("\\")[-1]
    out = []
    for p in psutil.process_iter(["username"]):
        u = (p.info.get("username") or "").lower().split("\\")[-1]
        if u == nombre and p.pid != os.getpid():
            out.append(p)
    return out


def limpiar_huerfanos(cuenta: str, espera_s: float = 5.0) -> int:
    """La cuenta es SOLO del conector: cualquier proceso suyo vivo antes de lanzar es un resto (de un Monitor que
    murió de golpe en Linux, por ejemplo). Se terminan; devuelve cuántos quedan vivos (debe ser 0)."""
    import psutil
    vivos = procesos_de_la_cuenta(cuenta)
    for p in vivos:
        try:
            p.kill()
        except psutil.Error:
            pass
    fin = time.monotonic() + espera_s
    while time.monotonic() < fin:
        restos = [p for p in procesos_de_la_cuenta(cuenta) if p.status() != psutil.STATUS_ZOMBIE]
        if not restos:
            return 0
        time.sleep(0.1)
    return len([p for p in procesos_de_la_cuenta(cuenta) if p.status() != psutil.STATUS_ZOMBIE])


# ------------------------------------------------------------------------------------------- comprobación activa
def verificar(cfg: ConfigAislamiento, python: str, cwd: str | None, env: dict, forms_dir: str | None) -> dict:
    """Ejecuta la sonda como la cuenta. Lanza ErrorAislamiento si no está demostrado; devuelve el resumen si sí."""
    if limpiar_huerfanos(cfg.cuenta):
        raise ErrorAislamiento("CUENTA_OCUPADA", "Hay procesos de la cuenta del conector que no se pudieron terminar: "
                               "no se lanza Forms.")
    try:
        cfg.trabajo.joinpath("tmp").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass                            # si Monitor no puede crearla, la sonda lo detecta (trabajo no escribible)
    privadas = rutas_privadas(cfg)
    entrada = json.dumps({"privadas": privadas, "no_escribibles": rutas_no_escribibles(forms_dir),
                          "trabajo": str(cfg.trabajo)})
    try:
        lz = fabrica(cfg)
        res = lz.lanzar([python, "-I", "-c", python_sonda()], entrada=SONDA_LINEA + "\n" + entrada, cwd=cwd,
                        env=env, plazo_s=PLAZO_SONDA_S)
    except ErrorAislamiento:
        raise
    except Exception as e:  # noqa: BLE001 - sin texto de la excepción (podría llevar rutas o la cuenta)
        raise ErrorAislamiento("AISLAMIENTO_NO_VERIFICADO", "No se pudo ejecutar la comprobación como la cuenta del "
                               f"conector ({type(e).__name__}): no se lanza Forms.",
                               informe={"ok": False, "fallos": [f"LANZAMIENTO:{type(e).__name__}"]}) from None
    try:
        salida = json.loads(res.stdout) if res.estado == "TERMINO" and res.stdout.strip() else None
    except ValueError:
        salida = None
    informe = evaluar_sonda(salida, cfg, privadas)
    informe["procesos_restantes"] = len(res.sobrevivientes)
    if res.sobrevivientes:
        informe["ok"] = False
        informe["fallos"].append("PROCESOS_RESTANTES")
    if not informe["ok"]:
        raise ErrorAislamiento("AISLAMIENTO_NO_VERIFICADO", "La cuenta del conector puede leer datos privados de "
                               "Monitor o la comprobación no terminó bien: no se lanza Forms.", informe=informe)
    return informe


# La primera línea de stdin es la sonda (en una sola línea, como literal JSON); el resto, los datos.
SONDA_LINEA = json.dumps(SONDA)


def python_sonda() -> str:
    return "import json,sys;exec(json.loads(sys.stdin.readline()))"

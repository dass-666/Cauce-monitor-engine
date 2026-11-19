"""v2.4 · Recuperación segura tras restaurar un respaldo.

Problema: `python -m monitoreo.migrate restaurar` devuelve la base a un momento anterior. Lo ocurrido
después se pierde: una revocación de consentimiento (el sistema podría volver a operar contra la voluntad
de la persona) o una cita confirmada en el portal (la base ya no la conoce). Este módulo guarda esos hechos
FUERA de la base, detecta cuando la base no los refleja y bloquea las operaciones con el portal hasta que un
administrador concilie y levante el bloqueo.

Piezas:
1. Diario de eventos críticos `<datos>/diario/eventos-criticos.jsonl` (solo añadir, una línea JSON por evento):
   REVOCACION, CONFIRMACION_INTENTO (antes de llamar al portal), CONFIRMACION_RESULTADO,
   CANCELACION_INTENTO, CANCELACION_RESULTADO y los de control (INICIO, RESTAURACION, BLOQUEO,
   CONCILIADO, LEVANTAMIENTO).
   - Autenticidad: cada línea lleva un HMAC-SHA256 (clave secreta) sobre su contenido y sobre el HMAC de la
     anterior. Sin la clave no se puede alterar, insertar ni reordenar una línea sin que se note. Un SHA-256
     sin clave no basta: cualquiera podría recalcular la cadena.
   - Sin identificadores en claro: códigos de expediente, de consentimiento, folios, claves e ids de la
     base se guardan como SEUDÓNIMOS (HMAC con otra subclave). Sin la clave no se pueden relacionar con
     una persona; con la clave solo se pueden comprobar contra la base.
   - Truncamiento: la base guarda un ancla (número y HMAC de la última línea escrita). Un diario más corto
     que el ancla, o con otra línea en esa posición, es un diario truncado o sustituido. La copia externa
     (si se configura) es un segundo punto de referencia.
2. Clave: `<datos>/claves/diario.clave` (o MONITOREO_DIARIO_CLAVE_ARCHIVO). En Windows se cifra con DPAPI
   (ligada a la cuenta de Windows que ejecuta el motor); en Linux/macOS, archivo legible solo por su dueño.
   No va en la base ni en los respaldos. Si falta y el diario tiene eventos: bloqueo (no se inventa otra).
3. Copia externa opcional: MONITOREO_DIARIO_COPIA=<carpeta en otra unidad>. Desactivada por omisión. Si
   está configurada y falla, se bloquea. Si no está configurada, el autodiagnóstico lo avisa: la
   recuperación NO está protegida contra la pérdida del disco.
4. Bloqueo RECUPERACION_PENDIENTE: tras cualquier restauración, con discrepancias, con el diario dañado o
   sin clave, o si falló una escritura del diario. Persiste al reiniciar (está en el diario firmado y en la
   base). Bloquea solo las escrituras en el portal (`_exigir_proveedor`) y la recuperación automática.
5. Conciliación: acciones administrativas explícitas, auditadas (audit_log y línea CONCILIADO). Ninguna
   repite una confirmación en el portal. El bloqueo solo se levanta con 0 discrepancias pendientes.

Consola:  python -m monitoreo.recuperacion estado | exportar-clave <archivo> | importar-clave <archivo>
          | marcar-restauracion <nombre>   (tras un pg_restore manual)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

log = logging.getLogger("monitoreo.recuperacion")

ARCHIVO = "eventos-criticos.jsonl"
VERSION_LINEA = 1
CERO = "0" * 64
TTL_CACHE_S = 30.0

# Claves de la tabla settings (no hay tablas nuevas)
K_ANCLA, K_FALLO, K_RESTAURADA = "recuperacion_ancla", "recuperacion_fallo_diario", "recuperacion_restaurada"

TIPOS_CONTROL = ("RESTAURACION", "BLOQUEO", "LEVANTAMIENTO")

# Discrepancias y sus acciones posibles
ACCIONES = {
    "REVOCACION_AUSENTE": ["reaplicar_revocacion"],
    "REVOCACION_SIN_CONSENTIMIENTO": ["reconocer"],
    "CONFIRMACION_AUSENTE": ["registrar_folio", "no_existe"],
    "CONFIRMACION_INCIERTA": ["registrar_folio", "no_existe"],
    "CONFIRMACION_SIN_RESERVACION": ["reconocer"],
    "CANCELACION_AUSENTE": ["aplicar_cancelacion", "sigue_vigente"],
    "CANCELACION_INCIERTA": ["aplicar_cancelacion", "sigue_vigente"],
    "CANCELACION_SIN_RESERVACION": ["reconocer"],
}
EXPLICACION = {
    "REVOCACION_AUSENTE": "La persona revocó su consentimiento después del respaldo y la base restaurada lo tiene "
                          "vigente. Se debe reaplicar la revocación antes de desbloquear.",
    "REVOCACION_SIN_CONSENTIMIENTO": "Hay una revocación en el diario de un consentimiento que la base restaurada no "
                                     "tiene (se registró después del respaldo). Revísalo y reconócelo.",
    "CONFIRMACION_AUSENTE": "El portal confirmó una cita después del respaldo y la base restaurada no la tiene. "
                            "Comprueba la cita en el portal y registra su folio, o indica que no existe.",
    "CONFIRMACION_INCIERTA": "Se intentó confirmar una cita y no consta el resultado. Comprueba en el portal si "
                             "existe. El sistema NO la vuelve a confirmar solo.",
    "CONFIRMACION_SIN_RESERVACION": "El portal tiene (o pudo tener) una cita de una reservación que la base restaurada "
                                    "no conoce. Revísala en el portal y reconócela.",
    "CANCELACION_AUSENTE": "Una cita se canceló en el portal después del respaldo y la base restaurada la tiene "
                           "confirmada.",
    "CANCELACION_INCIERTA": "Se intentó cancelar una cita en el portal y no consta el resultado.",
    "CANCELACION_SIN_RESERVACION": "Hay una cancelación en el diario de una reservación que la base no conoce.",
}
MOTIVOS = {
    "RESTAURACION_PENDIENTE": "Se restauró un respaldo: un administrador debe revisar la recuperación",
    "BLOQUEO_PENDIENTE": "Hay un bloqueo de recuperación sin levantar",
    "DISCREPANCIAS": "La base no refleja eventos críticos del diario",
    "DIARIO_MANIPULADO": "El diario de eventos críticos no supera la verificación (alterado o de otra clave)",
    "DIARIO_ILEGIBLE": "No se puede leer el diario de eventos críticos",
    "DIARIO_TRUNCADO": "El diario es más corto o distinto que su punto de referencia (truncado o sustituido)",
    "DIARIO_AUSENTE": "Falta el diario de eventos críticos y la base indica que tenía eventos",
    "CLAVE_NO_DISPONIBLE": "No se puede usar la clave del diario",
    "FALLO_ESCRITURA": "Falló una escritura del diario de eventos críticos",
    "COPIA_NO_DISPONIBLE": "La copia externa del diario está configurada pero no se puede usar",
    "COPIA_DIVERGENTE": "La copia externa del diario no coincide con el diario principal",
    "ERROR_VERIFICACION": "La verificación del diario falló por un error inesperado",
}


class ErrorDiario(Exception):
    """No se pudo escribir o usar el diario. Quien la recibe NO debe continuar con una operación peligrosa."""


class ErrorRecuperacion(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


_lock = threading.RLock()
_cache: dict[str, tuple[tuple, float, dict]] = {}
_fallo_memoria: dict[str, str] = {}      # carpeta del diario -> motivo (si la base tampoco pudo guardarlo)
_generacion = [0]


def _invalidar() -> None:
    with _lock:
        _generacion[0] += 1
        _cache.clear()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ============================================================================ rutas y configuración
def carpeta() -> Path:
    v = (os.environ.get("MONITOREO_DIARIO_DIR") or "").strip()
    return Path(v).expanduser().resolve() if v else settings.data_dir / "diario"


def ruta_diario() -> Path:
    return carpeta() / ARCHIVO


def ruta_copia() -> Path | None:
    v = (os.environ.get("MONITOREO_DIARIO_COPIA") or "").strip()
    return Path(v).expanduser() / ARCHIVO if v else None


def ruta_clave() -> Path:
    v = (os.environ.get("MONITOREO_DIARIO_CLAVE_ARCHIVO") or "").strip()
    return Path(v).expanduser() if v else settings.data_dir / "claves" / "diario.clave"


def _proteger(p: Path) -> None:
    from .captura.retencion import proteger
    proteger(p)


# ============================================================================ clave (DPAPI en Windows)
def _dpapi(datos: bytes, cifrar: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData con alcance de la cuenta de Windows actual (sin dependencias)."""
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
    entrada, _b1 = blob(datos)
    entropia, _b2 = blob(b"cauce-diario-eventos-criticos-v1")
    salida = BLOB()
    if not fn(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, 0x1, ctypes.byref(salida)):
        raise ErrorDiario(f"DPAPI no pudo {'proteger' if cifrar else 'abrir'} la clave "
                          f"(error {ctypes.get_last_error()}): ¿otra cuenta de Windows?")
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(salida.pbData, ctypes.c_void_p))


def _guardar_clave(raw: bytes) -> str:
    """Crea el archivo de clave en exclusiva (O_EXCL): nunca sustituye una clave existente, ni aunque dos
    hilos o procesos la creen a la vez (el segundo recibe FileExistsError y usa la del primero)."""
    p = ruta_clave()
    p.parent.mkdir(parents=True, exist_ok=True)
    _proteger(p.parent)
    if os.name == "nt":
        cont = {"v": 1, "proteccion": "dpapi-usuario", "clave": base64.b64encode(_dpapi(raw, True)).decode()}
    else:
        cont = {"v": 1, "proteccion": "archivo-solo-dueno", "clave": base64.b64encode(raw).decode()}
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cont, f)
        f.flush()
        os.fsync(f.fileno())
    _proteger(p)
    return cont["proteccion"]


def _leer_clave() -> bytes | None:
    p = ruta_clave()
    if not p.exists():
        return None
    try:
        cont = json.loads(p.read_text("utf-8"))
        raw = base64.b64decode(cont["clave"])
        if cont.get("proteccion") == "dpapi-usuario":
            if os.name != "nt":
                raise ErrorDiario("La clave está protegida con DPAPI de Windows y este equipo no es Windows")
            raw = _dpapi(raw, False)
    except ErrorDiario:
        raise
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ErrorDiario(f"Archivo de clave ilegible: {type(e).__name__}") from e
    if len(raw) < 32:
        raise ErrorDiario("Clave del diario demasiado corta")
    return raw


def proteccion_clave() -> str | None:
    try:
        return json.loads(ruta_clave().read_text("utf-8")).get("proteccion")
    except (OSError, ValueError):
        return None


class _Claves:
    def __init__(self, raw: bytes):
        self.mac = hmac.new(raw, b"cauce-diario/mac", hashlib.sha256).digest()
        self.seud = hmac.new(raw, b"cauce-diario/seudonimo", hashlib.sha256).digest()
        self.id = hmac.new(raw, b"cauce-diario/id", hashlib.sha256).hexdigest()[:16]


def _claves(crear: bool) -> _Claves:
    with _lock:
        try:
            raw = _leer_clave()
            if raw is None:
                if not crear:
                    raise ErrorDiario("No existe la clave del diario")
                raw = secrets.token_bytes(32)
                try:
                    prot = _guardar_clave(raw)
                    log.warning("diario de eventos críticos: clave nueva creada (%s)", prot)
                except FileExistsError:
                    raw = _leer_clave()          # otro proceso la creó a la vez: se usa esa
                    if raw is None:
                        raise ErrorDiario("La clave del diario apareció vacía") from None
        except ErrorDiario:
            raise
        except OSError as e:
            raise ErrorDiario(f"No se pudo usar la clave del diario: {type(e).__name__}") from e
    return _Claves(raw)


def seud(k: _Claves, dominio: str, valor) -> str:
    return hmac.new(k.seud, f"{dominio}:{valor}".encode(), hashlib.sha256).hexdigest()[:32]


def _ts(dt) -> str:
    if dt is None:
        return ""
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


# Identidades estables: el código (EXP-000007) y el id se REUTILIZAN tras restaurar (se generan con el id de
# la fila), así que se combinan con la hora de creación de la fila.
def ident_exp(k, exp) -> str:
    return seud(k, "exp", f"{exp.codigo}|{_ts(exp.created_at)}")


def ident_cons(k, c) -> str:
    return seud(k, "cons", f"{c.codigo}|{_ts(c.created_at)}")


def ident_res(k, r) -> str:
    return seud(k, "res", f"{r.id}|{_ts(r.created_at)}")


# ============================================================================ lectura y escritura
def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _firmar(k: _Claves, obj: dict) -> str:
    return hmac.new(k.mac, _canon(obj), hashlib.sha256).hexdigest()


class Lectura:
    def __init__(self):
        self.existe = False
        self.eventos: list[dict] = []
        self.fragmentos = 0
        self.problema: str | None = None
        self.detalle = ""
        self.ultimo = (0, CERO)
        self.lineas: list[str] = []      # líneas válidas, tal cual (para la copia)


def _leer(ruta: Path, k: _Claves | None) -> Lectura:
    out = Lectura()
    if not ruta.exists():
        return out
    out.existe = True
    try:
        texto = ruta.read_bytes().decode("utf-8", errors="replace")
    except OSError as e:
        out.problema, out.detalle = "DIARIO_ILEGIBLE", type(e).__name__
        return out
    crudas = texto.split("\n")
    n_prev, mac_prev = 0, CERO
    for linea in crudas:
        if not linea.strip():
            continue
        try:
            obj = json.loads(linea)
            if not isinstance(obj, dict) or "mac" not in obj:
                raise ValueError
        except ValueError:
            # Fragmento: escritura cortada a mitad (corte de luz). No tiene HMAC válido, así que no aporta
            # nada; si alguien borró una línea completa, la cadena de la siguiente lo delata.
            out.fragmentos += 1
            continue
        if k is None:
            out.problema, out.detalle = "CLAVE_NO_DISPONIBLE", "sin clave no se puede verificar el diario"
            return out
        mac = obj.pop("mac")
        if not hmac.compare_digest(_firmar(k, obj), str(mac)):
            out.problema, out.detalle = "DIARIO_MANIPULADO", f"línea {obj.get('n')}: HMAC no válido"
            return out
        if obj.get("n") != n_prev + 1 or obj.get("ant") != mac_prev:
            out.problema, out.detalle = "DIARIO_MANIPULADO", (f"cadena rota en la línea {obj.get('n')} "
                                                              f"(se esperaba {n_prev + 1})")
            return out
        n_prev, mac_prev = obj["n"], mac
        obj["mac"] = mac
        out.eventos.append(obj)
        out.lineas.append(linea)
    out.ultimo = (n_prev, mac_prev)
    return out


def _escribir_linea(ruta: Path, linea: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "ab") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            with open(ruta, "rb") as r:
                r.seek(-1, os.SEEK_END)
                if r.read(1) != b"\n":
                    f.write(b"\n")       # aísla un fragmento cortado
        f.write(linea.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def _anexar(tipo: str, d: dict, crear_clave: bool = True) -> dict:
    """Añade una línea firmada al diario (y a la copia, si está configurada). Lanza ErrorDiario."""
    with _lock:
        try:
            k = _claves(crear=crear_clave and not _hay_eventos())
            ruta = ruta_diario()
            lect = _leer(ruta, k)
            if lect.problema:
                raise ErrorDiario(f"{lect.problema}: {lect.detalle}")
            n, ant = lect.ultimo
            if n == 0:   # primera línea: INICIO con la huella de la clave
                ini = {"v": VERSION_LINEA, "n": 1, "t": _utc(), "tipo": "INICIO", "ant": CERO,
                       "d": {"diario": secrets.token_hex(8), "clave_id": k.id}}
                ini["mac"] = _firmar(k, ini)
                linea = json.dumps(ini, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                _escribir_linea(ruta, linea)
                _proteger(ruta.parent)
                _proteger(ruta)
                _copiar_linea(linea)
                n, ant = 1, ini["mac"]
            obj = {"v": VERSION_LINEA, "n": n + 1, "t": _utc(), "tipo": tipo, "ant": ant, "d": d}
            obj["mac"] = _firmar(k, obj)
            linea = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            _escribir_linea(ruta, linea)
            _copiar_linea(linea)
            return obj
        except ErrorDiario:
            raise
        except OSError as e:
            raise ErrorDiario(f"No se pudo escribir el diario: {type(e).__name__}") from e
        finally:
            _invalidar()


def _hay_eventos() -> bool:
    p = ruta_diario()
    try:
        return p.exists() and p.stat().st_size > 0
    except OSError:
        return True


def _copiar_linea(linea: str) -> None:
    c = ruta_copia()
    if c is None:
        return
    try:
        _sincronizar_copia()          # pone al día la copia (si estaba atrasada) antes de añadir
    except ErrorDiario:
        raise
    except OSError as e:
        raise ErrorDiario(f"COPIA_NO_DISPONIBLE: {type(e).__name__}") from e


def _sincronizar_copia() -> None:
    """La copia debe ser un prefijo del principal o igual. Completa lo que le falta."""
    c = ruta_copia()
    if c is None:
        return
    if not c.parent.is_dir():
        raise ErrorDiario("COPIA_NO_DISPONIBLE: la carpeta de la copia no existe o no está montada")
    p = ruta_diario()
    prin = [x for x in p.read_text("utf-8", errors="replace").split("\n") if x.strip()] if p.exists() else []
    cop = [x for x in c.read_text("utf-8", errors="replace").split("\n") if x.strip()] if c.exists() else []
    if cop[:len(prin)] == prin and len(cop) > len(prin):
        raise ErrorDiario("DIARIO_TRUNCADO: la copia externa tiene más eventos que el diario principal")
    if prin[:len(cop)] != cop:
        raise ErrorDiario("COPIA_DIVERGENTE: la copia externa no coincide con el diario principal")
    for x in prin[len(cop):]:
        _escribir_linea(c, x)


# ============================================================================ estado en la base
def _get_setting(s, key):
    from .models import Setting
    row = s.get(Setting, key)
    return row.value if row is not None else None


def _set_setting(s, key, value) -> None:
    from .models import Setting
    row = s.get(Setting, key)
    if value is None:
        if row is not None:
            s.delete(row)
    elif row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


def _clave_url(s_or_db) -> str:
    try:
        url = s_or_db.url if hasattr(s_or_db, "url") and isinstance(s_or_db.url, str) else str(s_or_db.get_bind().url)
    except Exception:  # noqa: BLE001
        url = "?"
    return f"{carpeta()}|{url}"


def _anclar(db, ev: dict) -> None:
    try:
        with db.session() as s:
            prev = _get_setting(s, K_ANCLA) or {}
            if int(prev.get("n", 0)) < ev["n"]:
                _set_setting(s, K_ANCLA, {"n": ev["n"], "mac": ev["mac"], "t": ev["t"]})
    except Exception as e:  # noqa: BLE001 - el ancla es una referencia adicional; su fallo se ve en el diagnóstico
        log.warning("diario: no se pudo actualizar el ancla en la base: %s", type(e).__name__)


def _marcar_fallo(db, motivo: str) -> None:
    """Un fallo de escritura del diario nunca pasa inadvertido: queda en la base y en memoria, y bloquea."""
    log.critical("DIARIO DE EVENTOS CRÍTICOS: fallo de escritura (%s). Operaciones con el portal BLOQUEADAS "
                 "hasta que un administrador lo revise.", motivo[:160])
    with _lock:
        _fallo_memoria[str(carpeta())] = motivo[:200]
    try:
        with db.session() as s:
            _set_setting(s, K_FALLO, {"motivo": motivo[:200], "t": _utc()})
            from .security import audit
            audit(s, "motor", "diario_fallo_escritura", "", {"motivo": motivo[:200]})
    except Exception as e:  # noqa: BLE001
        log.critical("diario: tampoco se pudo guardar el fallo en la base (%s); queda en memoria", type(e).__name__)
    _invalidar()


def registrar(db, tipo: str, d: dict) -> dict:
    """Escribe un evento. Si falla: marca el fallo (bloqueo) y relanza ErrorDiario."""
    try:
        ev = _anexar(tipo, d)
    except ErrorDiario as e:
        _marcar_fallo(db, f"{tipo}: {e}")
        raise
    except Exception as e:  # noqa: BLE001 - un fallo inesperado del diario también bloquea
        _marcar_fallo(db, f"{tipo}: {type(e).__name__}")
        raise ErrorDiario(type(e).__name__) from e
    _anclar(db, ev)
    return ev


# ============================================================================ enganches del motor
def anotar_revocacion(db, consentimiento_id: int, reaplicada_de: int | None = None) -> None:
    """Después de confirmar la transacción de la revocación. Si falla, la revocación sigue en la base
    (es lo restrictivo), pero el motor queda bloqueado: una restauración futura podría perderla."""
    from .models import Consentimiento, Expediente
    try:
        k = _claves(crear=not _hay_eventos())
        with db.session() as s:
            c = s.get(Consentimiento, consentimiento_id)
            e = s.get(Expediente, c.expediente_id)
            d = {"cons": ident_cons(k, c), "exp": ident_exp(k, e), "medio": c.revocacion_medio,
                 "motivo": c.revocacion_motivo}
        if reaplicada_de:
            d["reaplicada_de"] = reaplicada_de
        registrar(db, "REVOCACION", d)
    except ErrorDiario as e:
        _marcar_fallo(db, f"REVOCACION: {e}")
    except Exception as e:  # noqa: BLE001
        _marcar_fallo(db, f"REVOCACION: {type(e).__name__}")


def _datos_res(k, s, res_id: int) -> tuple[dict, object]:
    from .models import Expediente, Reservacion
    r = s.get(Reservacion, res_id)
    e = s.get(Expediente, r.expediente_id)
    return {"res": ident_res(k, r), "exp": ident_exp(k, e)}, r


def confirmacion_intento(db, res_id: int) -> None:
    """ANTES de llamar al portal. Lanza ErrorDiario: quien llama NO debe llamar al portal."""
    try:
        k = _claves(crear=not _hay_eventos())
        with db.session() as s:
            d, r = _datos_res(k, s, res_id)
            d["clave"] = seud(k, "clave", r.clave or "")
    except ErrorDiario as e:
        _marcar_fallo(db, f"CONFIRMACION_INTENTO: {e}")
        raise
    except Exception as e:  # noqa: BLE001 - cualquier fallo: no se llama al portal
        _marcar_fallo(db, f"CONFIRMACION_INTENTO: {type(e).__name__}")
        raise ErrorDiario(type(e).__name__) from e
    registrar(db, "CONFIRMACION_INTENTO", d)


def _resultado_confirmacion(estado: str) -> str:
    if estado == "CONFIRMADA":
        return "CONFIRMADA"
    if estado in ("CONFIRMANDO", "EN_VERIFICACION"):
        return "INCIERTO"
    return "NO_CONFIRMADA"


def confirmacion_resultado(db, res_id: int, origen: str = "motor") -> None:
    """Después de la llamada al portal (o de la recuperación). Nunca lanza: si falla, bloquea."""
    try:
        k = _claves(crear=False)
        with db.session() as s:
            d, r = _datos_res(k, s, res_id)
            d["resultado"] = _resultado_confirmacion(r.estado)
            if r.folio and d["resultado"] == "CONFIRMADA":
                d["folio"] = seud(k, "folio", r.folio)
            d["origen"] = origen
        registrar(db, "CONFIRMACION_RESULTADO", d)
    except ErrorDiario:
        pass                                    # registrar() ya marcó el fallo
    except Exception as e:  # noqa: BLE001
        _marcar_fallo(db, f"CONFIRMACION_RESULTADO: {type(e).__name__}")


def cancelacion_intento(db, res_id: int) -> None:
    try:
        k = _claves(crear=not _hay_eventos())
        with db.session() as s:
            d, r = _datos_res(k, s, res_id)
            d["folio"] = seud(k, "folio", r.folio or "")
    except ErrorDiario as e:
        _marcar_fallo(db, f"CANCELACION_INTENTO: {e}")
        raise
    except Exception as e:  # noqa: BLE001
        _marcar_fallo(db, f"CANCELACION_INTENTO: {type(e).__name__}")
        raise ErrorDiario(type(e).__name__) from e
    registrar(db, "CANCELACION_INTENTO", d)


def cancelacion_resultado(db, res_id: int) -> None:
    try:
        k = _claves(crear=False)
        with db.session() as s:
            d, r = _datos_res(k, s, res_id)
            d["resultado"] = "CANCELADA" if r.estado == "CANCELADA" else "NO_CANCELADA"
        registrar(db, "CANCELACION_RESULTADO", d)
    except ErrorDiario:
        pass
    except Exception as e:  # noqa: BLE001
        _marcar_fallo(db, f"CANCELACION_RESULTADO: {type(e).__name__}")


def anotar_restauracion(respaldo: str, previo: str | None) -> dict:
    """La llama `migrate.restore` ANTES de sustituir la base. Si no se puede escribir, la restauración
    no se hace (sin esta marca nada impediría operar sobre una base que retrocedió)."""
    return _anexar("RESTAURACION", {"respaldo": Path(respaldo).name[:120],
                                    "previo": Path(previo).name[:120] if previo else None})


# ============================================================================ evaluación
def _comparar(s, k: _Claves, eventos: list[dict]) -> tuple[list[dict], set[str]]:
    """Compara lo que el diario dice que pasó con lo que la base tiene. Devuelve (discrepancias,
    seudónimos de expedientes con una revocación que la base no refleja)."""
    from sqlalchemy import select

    from .models import Consentimiento, Expediente, Reservacion
    revs: dict[str, dict] = {}                 # cons -> último evento de revocación
    esperado: dict[str, dict] = {}             # res -> {"estado", "folio", "ref", "exp", "clave"}
    reconocidas: set[tuple[int, str]] = set()
    for ev in eventos:
        t, d = ev["tipo"], ev.get("d") or {}
        if t == "REVOCACION":
            revs[d["cons"]] = {**d, "ref": ev["n"], "t": ev["t"]}
        elif t == "CONFIRMACION_INTENTO":
            esperado[d["res"]] = {"estado": "INCIERTO_CONF", "folio": None, "ref": ev["n"], "t": ev["t"],
                                  "exp": d.get("exp"), "clave": d.get("clave")}
        elif t == "CONFIRMACION_RESULTADO":
            prev = esperado.get(d["res"], {})
            est = {"CONFIRMADA": "CONFIRMADA", "INCIERTO": "INCIERTO_CONF"}.get(d.get("resultado"), "NINGUNO")
            esperado[d["res"]] = {**prev, "estado": est, "folio": d.get("folio"), "ref": ev["n"], "t": ev["t"],
                                  "exp": d.get("exp") or prev.get("exp")}
        elif t == "CANCELACION_INTENTO":
            prev = esperado.get(d["res"], {})
            esperado[d["res"]] = {**prev, "estado": "INCIERTO_CANC", "folio": d.get("folio") or prev.get("folio"),
                                  "ref": ev["n"], "t": ev["t"], "exp": d.get("exp") or prev.get("exp")}
        elif t == "CANCELACION_RESULTADO":
            prev = esperado.get(d["res"], {})
            est = "CANCELADA" if d.get("resultado") == "CANCELADA" else "CONFIRMADA"
            esperado[d["res"]] = {**prev, "estado": est, "ref": ev["n"], "t": ev["t"]}
        elif t == "CONCILIADO":
            reconocidas.add((int(d.get("ref", 0)), d.get("disc", "")))
            if d.get("res"):
                prev = esperado.get(d["res"], {})
                efecto = d.get("efecto")
                if efecto == "CONFIRMADA":
                    esperado[d["res"]] = {**prev, "estado": "CONFIRMADA", "folio": d.get("folio") or prev.get("folio"),
                                          "ref": ev["n"], "t": ev["t"]}
                elif efecto in ("NINGUNO", "CANCELADA"):
                    esperado[d["res"]] = {**prev, "estado": efecto, "ref": ev["n"], "t": ev["t"]}
    out: list[dict] = []
    pendientes_exp: set[str] = set()
    if revs:
        cons = {ident_cons(k, c): c for c in s.scalars(select(Consentimiento))}
        for ci, ev in revs.items():
            c = cons.get(ci)
            if c is not None and c.estado == "REVOCADO":
                continue
            tipo = "REVOCACION_AUSENTE" if c is not None else "REVOCACION_SIN_CONSENTIMIENTO"
            item = {"ref": ev["ref"], "tipo": tipo, "hora_evento": ev["t"],
                    "consentimiento_id": c.id if c else None, "expediente_id": c.expediente_id if c else None,
                    "medio": ev.get("medio"), "motivo": ev.get("motivo")}
            out.append(item)
            if c is not None:
                pendientes_exp.add(ev.get("exp"))
    if esperado:
        filas = {ident_res(k, r): r for r in s.scalars(select(Reservacion))}
        for ri, e in esperado.items():
            est = e.get("estado")
            if est == "NINGUNO":
                continue
            r = filas.get(ri)
            folio_bd = seud(k, "folio", r.folio) if r is not None and r.folio else None
            tipo = None
            if est == "CONFIRMADA":
                if r is None:
                    tipo = "CONFIRMACION_SIN_RESERVACION"
                elif not (r.estado == "CONFIRMADA" and (e.get("folio") is None or folio_bd == e.get("folio"))):
                    tipo = "CONFIRMACION_AUSENTE"
            elif est == "INCIERTO_CONF":
                if r is None:
                    tipo = "CONFIRMACION_SIN_RESERVACION"
                elif r.estado not in ("CONFIRMANDO", "EN_VERIFICACION", "CONFIRMADA"):
                    tipo = "CONFIRMACION_INCIERTA"
            elif est == "INCIERTO_CANC":
                if r is None:
                    tipo = "CANCELACION_SIN_RESERVACION"
                elif r.estado != "CANCELADA":
                    tipo = "CANCELACION_INCIERTA"
            elif est == "CANCELADA":
                if r is None:
                    tipo = "CANCELACION_SIN_RESERVACION"
                elif r.estado != "CANCELADA":
                    tipo = "CANCELACION_AUSENTE"
            if tipo:
                out.append({"ref": e["ref"], "tipo": tipo, "hora_evento": e.get("t"),
                            "reservacion_id": r.id if r is not None else None,
                            "expediente_id": r.expediente_id if r is not None else None,
                            "estado_en_base": r.estado if r is not None else None,
                            "folio_en_diario": bool(e.get("folio"))})
    for x in out:
        x["reconocida"] = (x["ref"], x["tipo"]) in reconocidas and x["tipo"] in (
            "REVOCACION_SIN_CONSENTIMIENTO", "CONFIRMACION_SIN_RESERVACION", "CANCELACION_SIN_RESERVACION")
        x["acciones"] = [] if x["reconocida"] else ACCIONES[x["tipo"]]
        x["explicacion"] = EXPLICACION[x["tipo"]]
        if x.get("expediente_id"):
            e = s.get(Expediente, x["expediente_id"])
            x["expediente"] = e.codigo if e else None
    out.sort(key=lambda x: x["ref"])
    return out, {p for p in pendientes_exp if p}


def _firma() -> tuple:
    def st(p):
        try:
            x = p.stat()
            return (x.st_size, x.st_mtime_ns)
        except (OSError, AttributeError):
            return None
    return (st(ruta_diario()), st(ruta_copia()) if ruta_copia() else None, st(ruta_clave()), _generacion[0])


def evaluar(s, usar_cache: bool = True) -> dict:
    """Estado de recuperación con la sesión dada (solo lectura en la base). Ante cualquier error inesperado
    el resultado es BLOQUEADO (falla cerrado)."""
    clave_c = _clave_url(s)
    f = _firma()
    if usar_cache:
        with _lock:
            c = _cache.get(clave_c)
            if c and c[0] == f and time.monotonic() - c[1] < TTL_CACHE_S:
                return c[2]
    try:
        r = _evaluar(s)
    except Exception as e:  # noqa: BLE001
        log.exception("recuperación: la verificación falló")
        r = {"bloqueado": True, "motivos": [{"codigo": "ERROR_VERIFICACION",
                                             "mensaje": f"{MOTIVOS['ERROR_VERIFICACION']}: {type(e).__name__}"}],
             "discrepancias": [], "_rev_pendientes": set(), "diario": {}, "copia": {}, "avisos": []}
    with _lock:
        _cache[clave_c] = (_firma(), time.monotonic(), r)
    return r


def _evaluar(s) -> dict:
    motivos: list[str] = []
    avisos: list[str] = []
    detalle: dict = {}
    k = None
    try:
        raw = _leer_clave()
        k = _Claves(raw) if raw is not None else None
    except ErrorDiario as e:
        detalle["clave"] = str(e)
        motivos.append("CLAVE_NO_DISPONIBLE")
    ruta = ruta_diario()
    lect = _leer(ruta, k)
    if lect.problema:
        motivos.append(lect.problema)
        detalle["diario"] = lect.detalle
    elif k is None and lect.eventos:
        motivos.append("CLAVE_NO_DISPONIBLE")
    if lect.fragmentos:
        avisos.append(f"{lect.fragmentos} fragmento(s) de línea cortada en el diario (escritura interrumpida)")
    ancla = _get_setting(s, K_ANCLA) or {}
    n_ancla = int(ancla.get("n", 0) or 0)
    if n_ancla and not lect.problema:
        if not lect.existe:
            motivos.append("DIARIO_AUSENTE")
        elif lect.ultimo[0] < n_ancla:
            motivos.append("DIARIO_TRUNCADO")
            detalle["truncado"] = f"la base registró hasta la línea {n_ancla}; el diario llega a {lect.ultimo[0]}"
        else:
            ev = next((x for x in lect.eventos if x["n"] == n_ancla), None)
            if ev is None or ev["mac"] != ancla.get("mac"):
                motivos.append("DIARIO_TRUNCADO")
                detalle["truncado"] = f"la línea {n_ancla} no es la que la base registró (diario sustituido)"
    copia = {"configurada": ruta_copia() is not None}
    if ruta_copia() is not None and not lect.problema:
        c = ruta_copia()
        try:
            if not c.parent.is_dir():
                raise ErrorDiario("COPIA_NO_DISPONIBLE")
            lc = _leer(c, k)
            if lc.problema:
                raise ErrorDiario("COPIA_DIVERGENTE")
            if lc.ultimo[0] > lect.ultimo[0]:
                motivos.append("DIARIO_TRUNCADO")
                detalle["truncado"] = "la copia externa tiene más eventos que el diario principal"
            elif lect.lineas[:len(lc.lineas)] != lc.lineas:
                motivos.append("COPIA_DIVERGENTE")
            copia.update(eventos=lc.ultimo[0], al_dia=lc.ultimo[0] == lect.ultimo[0])
        except ErrorDiario as e:
            motivos.append(str(e).split(":")[0] if str(e).startswith("COPIA") else "COPIA_NO_DISPONIBLE")
        except OSError:
            motivos.append("COPIA_NO_DISPONIBLE")
    elif ruta_copia() is None:
        avisos.append("Sin copia externa del diario: la recuperación NO está protegida contra la pérdida o el daño "
                      "del disco (MONITOREO_DIARIO_COPIA sin configurar)")
    fallo = _get_setting(s, K_FALLO) or ({"motivo": _fallo_memoria[str(carpeta())]}
                                         if str(carpeta()) in _fallo_memoria else None)
    if fallo:
        motivos.append("FALLO_ESCRITURA")
        detalle["fallo"] = fallo.get("motivo")
    restaurada = _get_setting(s, K_RESTAURADA)
    control = next((x for x in reversed(lect.eventos) if x["tipo"] in TIPOS_CONTROL), None)
    if control is not None and control["tipo"] == "RESTAURACION" or (restaurada and not (
            control is not None and control["tipo"] == "LEVANTAMIENTO" and control["t"] > str(restaurada.get("t", "")))):
        motivos.append("RESTAURACION_PENDIENTE")
    elif control is not None and control["tipo"] == "BLOQUEO":
        motivos.append("BLOQUEO_PENDIENTE")
    disc, rev_pend = ([], set())
    if k is not None and not lect.problema:
        disc, rev_pend = _comparar(s, k, lect.eventos)
    pend = [x for x in disc if not x["reconocida"]]
    if pend:
        motivos.append("DISCREPANCIAS")
        if not any(m in motivos for m in ("RESTAURACION_PENDIENTE", "BLOQUEO_PENDIENTE")):
            # Discrepancias sin restauración registrada (p. ej. base copiada a mano o pg_restore): se deja un
            # BLOQUEO firmado para que el desbloqueo exija siempre una acción administrativa.
            try:
                _anexar("BLOQUEO", {"motivo": "DISCREPANCIAS", "pendientes": len(pend)}, crear_clave=False)
                motivos.append("BLOQUEO_PENDIENTE")
            except ErrorDiario as e:
                log.critical("recuperación: no se pudo registrar el BLOQUEO en el diario: %s", e)
    motivos = list(dict.fromkeys(motivos))
    return {"bloqueado": bool(motivos),
            "motivos": [{"codigo": m, "mensaje": MOTIVOS[m]} for m in motivos],
            "discrepancias": disc, "pendientes": len(pend), "_rev_pendientes": rev_pend,
            "diario": {"ruta": str(ruta), "eventos": lect.ultimo[0], "fragmentos": lect.fragmentos,
                       "ancla_en_base": n_ancla, "clave": proteccion_clave(), **detalle},
            "copia": copia, "avisos": avisos,
            "ultimo_control": {"tipo": control["tipo"], "hora": control["t"]} if control else None}


def publico(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


# ============================================================================ puertas
def exigir_operable(s, error_cls=ErrorRecuperacion) -> None:
    """Puerta de las escrituras en el portal. 409 RECUPERACION_PENDIENTE si hay que revisar algo."""
    e = evaluar(s)
    if e["bloqueado"]:
        cods = [m["codigo"] for m in e["motivos"]]
        raise error_cls(409, "Operaciones con el portal bloqueadas: hay una recuperación pendiente de revisar por "
                             "un administrador (" + ", ".join(cods) + ")",
                        {"codigo": "RECUPERACION_PENDIENTE", "motivos": cods, "pendientes": e.get("pendientes", 0)})


def bloqueado(db) -> bool:
    with db.session() as s:
        return evaluar(s)["bloqueado"]


def revocacion_pendiente(s, exp) -> bool:
    """True si el diario tiene una revocación de este expediente que la base no refleja: el consentimiento
    NO se considera vigente aunque la base restaurada diga VIGENTE (nunca se reactiva solo)."""
    try:
        if not _hay_eventos():
            return False
        e = evaluar(s)
        if not e["_rev_pendientes"]:
            return False
        raw = _leer_clave()
        return raw is not None and ident_exp(_Claves(raw), exp) in e["_rev_pendientes"]
    except Exception:  # noqa: BLE001 - ante la duda, no vigente
        return True


# ============================================================================ arranque y relleno
def preparar(db) -> dict:
    """Al arrancar el motor y en el autodiagnóstico. Fuera de cualquier sesión. Completa el diario con
    revocaciones y citas confirmadas que la base tiene y el diario no (p. ej. anteriores a la v2.4 o una
    escritura que falló), sincroniza la copia y devuelve el estado."""
    rellenadas = 0
    with db.session() as s:
        e0 = evaluar(s, usar_cache=False)
    sin_relleno = {"DIARIO_MANIPULADO", "DIARIO_ILEGIBLE", "CLAVE_NO_DISPONIBLE", "DIARIO_TRUNCADO",
                   "DIARIO_AUSENTE", "COPIA_DIVERGENTE", "ERROR_VERIFICACION"}
    if not any(m["codigo"] in sin_relleno for m in e0["motivos"]):
        try:
            rellenadas = _rellenar(db)
        except ErrorDiario as e:
            _marcar_fallo(db, f"RELLENO: {e}")
        if ruta_copia() is not None:
            try:
                with _lock:
                    _sincronizar_copia()
            except (ErrorDiario, OSError) as e:
                log.error("diario: la copia externa no se pudo sincronizar: %s", e)
            _invalidar()
    with db.session() as s:
        e = evaluar(s, usar_cache=False)
        n = e["diario"].get("eventos", 0)
    if n and not e["motivos"]:
        lect = _leer(ruta_diario(), _Claves(_leer_clave()))
        if lect.eventos:
            _anclar(db, lect.eventos[-1])
    if e["bloqueado"]:
        log.warning("RECUPERACIÓN PENDIENTE: operaciones con el portal bloqueadas (%s); %d discrepancia(s)",
                    ", ".join(m["codigo"] for m in e["motivos"]), e.get("pendientes", 0))
    out = publico(e)
    out["rellenadas"] = rellenadas
    return out


def _rellenar(db) -> int:
    from sqlalchemy import select

    from .models import Consentimiento, Expediente, Reservacion
    if not _hay_eventos() and _leer_clave() is None:
        with db.session() as s:
            hay = s.scalar(select(Consentimiento.id).where(Consentimiento.estado == "REVOCADO").limit(1)) or \
                s.scalar(select(Reservacion.id).where(Reservacion.estado == "CONFIRMADA").limit(1))
        if not hay:
            return 0
    k = _claves(crear=not _hay_eventos())
    lect = _leer(ruta_diario(), k)
    if lect.problema:
        return 0
    ya_rev = {(x.get("d") or {}).get("cons") for x in lect.eventos if x["tipo"] == "REVOCACION"}
    ya_res = {(x.get("d") or {}).get("res") for x in lect.eventos if (x.get("d") or {}).get("res")}
    nuevas = []
    with db.session() as s:
        for c in s.scalars(select(Consentimiento).where(Consentimiento.estado == "REVOCADO")):
            ci = ident_cons(k, c)
            if ci not in ya_rev:
                e = s.get(Expediente, c.expediente_id)
                nuevas.append(("REVOCACION", {"cons": ci, "exp": ident_exp(k, e), "medio": c.revocacion_medio,
                                              "motivo": c.revocacion_motivo, "origen": "relleno"}))
        for r in s.scalars(select(Reservacion).where(Reservacion.estado == "CONFIRMADA")):
            ri = ident_res(k, r)
            if ri not in ya_res:
                e = s.get(Expediente, r.expediente_id)
                nuevas.append(("CONFIRMACION_RESULTADO", {"res": ri, "exp": ident_exp(k, e), "resultado": "CONFIRMADA",
                                                          "folio": seud(k, "folio", r.folio) if r.folio else None,
                                                          "origen": "relleno"}))
    for tipo, d in nuevas:
        registrar(db, tipo, d)
    if nuevas:
        log.info("diario de eventos críticos: %d evento(s) existentes en la base añadidos", len(nuevas))
    return len(nuevas)


# ============================================================================ conciliación (administrador)
def _audit(db, user, accion: str, objetivo: str, detalle: dict, ip: str) -> None:
    from .security import audit
    with db.session() as s:
        audit(s, user.username, accion, objetivo, detalle, ip)


def resolver(db, user, ref: int, tipo: str, accion: str, motivo: str, folio: str | None = None,
             ip: str = "") -> dict:
    """Resuelve UNA discrepancia con una acción explícita. Nunca llama al portal."""
    from datetime import datetime as _dt

    from .captura import consentimiento as cs
    from .captura import reservaciones as rs
    from .models import Reservacion
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorRecuperacion(422, "Escribe el motivo de la resolución (mínimo 5 caracteres)")
    with db.session() as s:
        e = evaluar(s, usar_cache=False)
    if any(m["codigo"] in ("DIARIO_MANIPULADO", "DIARIO_ILEGIBLE", "CLAVE_NO_DISPONIBLE", "DIARIO_TRUNCADO",
                           "DIARIO_AUSENTE", "ERROR_VERIFICACION") for m in e["motivos"]):
        raise ErrorRecuperacion(409, "El diario no supera la verificación: resuelve eso primero",
                                {"motivos": [m["codigo"] for m in e["motivos"]]})
    disc = next((x for x in e["discrepancias"] if x["ref"] == ref and x["tipo"] == tipo), None)
    if disc is None:
        raise ErrorRecuperacion(404, "Esa discrepancia no existe o ya está resuelta")
    if accion not in disc["acciones"]:
        raise ErrorRecuperacion(422, f"Acción no válida para {tipo}", {"acciones": disc["acciones"]})
    k = _claves(crear=False)
    now = _dt.now(timezone.utc).replace(tzinfo=None)
    d = {"ref": ref, "disc": tipo, "accion": accion, "usr": seud(k, "usr", user.username)}
    efecto = None
    if accion == "reaplicar_revocacion":
        cs.revocar(db, disc["consentimiento_id"], {"medio": disc.get("medio") or "ESCRITO",
                                                    "motivo": disc.get("motivo") or "PERSONA_LO_PIDE"},
                   user, now, ip, reaplicada_de=ref)
        efecto = "REVOCADA"
    elif accion == "reconocer":
        efecto = "RECONOCIDO"
    else:
        rid = disc["reservacion_id"]
        with db.session() as s:
            r = s.get(Reservacion, rid)
            d["res"] = ident_res(k, r)
            clave, estado_r = r.clave, r.estado
        if accion == "registrar_folio":
            folio = (folio or "").strip()
            if not folio or len(folio) > 32:
                raise ErrorRecuperacion(422, "Escribe el folio que muestra el portal")
            if disc.get("folio_en_diario"):
                esperado = _folio_en_diario(k, ref)
                if esperado and not hmac.compare_digest(seud(k, "folio", folio), esperado):
                    raise ErrorRecuperacion(422, "Ese folio no es el que el portal entregó según el diario. "
                                                 "Compruébalo en el portal.", {"codigo": "FOLIO_NO_COINCIDE"})
            if estado_r not in ("CONFIRMANDO", "EN_VERIFICACION", "RETENIDA", "FECHA_ELEGIDA", "CONFIRMADA"):
                raise ErrorRecuperacion(409, f"La reservación está {estado_r}: no se puede marcar confirmada")
            rp = {"estado": "CONFIRMADA", "clave": clave, "folio": folio}
            v = rs._finalizar(db, rid, rp, "conciliacion_manual", user.username[:64], now)
            if v.get("estado") != "CONFIRMADA" or v.get("folio") != folio:
                raise ErrorRecuperacion(409, "No se pudo registrar la cita (¿folio de otra reservación?)",
                                        {"reservacion": v})
            d["folio"] = seud(k, "folio", folio)
            efecto = "CONFIRMADA"
        elif accion == "no_existe":
            efecto = "NINGUNO"
        elif accion == "aplicar_cancelacion":
            _cancelar_local(db, rid, user.username[:64], now)
            efecto = "CANCELADA"
        elif accion == "sigue_vigente":
            if estado_r != "CONFIRMADA":
                raise ErrorRecuperacion(409, f"La reservación está {estado_r} en la base")
            efecto = "CONFIRMADA"
    d["efecto"] = efecto
    try:
        registrar(db, "CONCILIADO", d)
    except ErrorDiario as ex_:
        raise ErrorRecuperacion(503, f"La acción se aplicó en la base pero no se pudo registrar en el diario: {ex_}",
                                {"codigo": "DIARIO_NO_DISPONIBLE"}) from ex_
    _audit(db, user, "recuperacion_conciliada", f"diario#{ref}",
           {"discrepancia": tipo, "accion": accion, "efecto": efecto, "motivo": motivo[:300],
            "reservacion": disc.get("reservacion_id"), "consentimiento": disc.get("consentimiento_id")}, ip)
    with db.session() as s:
        return publico(evaluar(s, usar_cache=False))


def _folio_en_diario(k, ref: int) -> str | None:
    lect = _leer(ruta_diario(), k)
    ev = next((x for x in lect.eventos if x["n"] == ref), None)
    return (ev.get("d") or {}).get("folio") if ev else None


def _cancelar_local(db, rid: int, actor: str, now) -> None:
    from sqlalchemy import update

    from .captura import expedientes as ex
    from .models import Expediente, Reservacion
    with db.session() as s:
        t = Reservacion.__table__
        r = s.get(Reservacion, rid)
        s.execute(update(t).where(t.c.id == rid).values(
            estado="CANCELADA", viva_expediente_id=None, updated_at=now,
            evidencia={**(r.evidencia or {}), "cancelada_en_portal": "conciliacion_manual"}))
        exp = s.get(Expediente, r.expediente_id)
        ex.evento(s, exp.id, "RESERVACION_CANCELADA", actor, now, reservacion_id=rid, evidencia="conciliacion_manual")
        if exp.estado != ex.CERRADO:
            ex.cerrar(s, exp, "CANCELADO", actor, now, nota="cancelación conciliada tras restaurar")


def levantar(db, user, motivo: str, ip: str = "") -> dict:
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorRecuperacion(422, "Escribe el motivo del desbloqueo (mínimo 5 caracteres)")
    e = preparar(db)
    impiden = [m["codigo"] for m in e["motivos"] if m["codigo"] not in ("RESTAURACION_PENDIENTE", "BLOQUEO_PENDIENTE",
                                                                         "FALLO_ESCRITURA")]
    if impiden or e.get("pendientes"):
        raise ErrorRecuperacion(409, "No se puede levantar el bloqueo todavía", {"motivos": impiden,
                                                                                 "pendientes": e.get("pendientes", 0)})
    if not e["bloqueado"]:
        return {**e, "levantado": False, "aviso": "No había bloqueo"}
    k = _claves(crear=False)
    try:
        registrar(db, "LEVANTAMIENTO", {"usr": seud(k, "usr", user.username),
                                        "motivos": [m["codigo"] for m in e["motivos"]]})
    except ErrorDiario as ex_:
        raise ErrorRecuperacion(503, f"No se pudo registrar el desbloqueo en el diario: {ex_}") from ex_
    with db.session() as s:
        _set_setting(s, K_RESTAURADA, None)
        _set_setting(s, K_FALLO, None)
    with _lock:
        _fallo_memoria.pop(str(carpeta()), None)
    _invalidar()
    _audit(db, user, "recuperacion_levantada", "diario", {"motivo": motivo[:300],
                                                          "motivos_previos": [m["codigo"] for m in e["motivos"]]}, ip)
    log.warning("recuperación: bloqueo levantado por %s", user.username)
    out = preparar(db)
    out["levantado"] = not out["bloqueado"]
    return out


def reiniciar_diario(db, user, motivo: str, ip: str = "") -> dict:
    """Solo si el diario no supera la verificación o falta la clave. Archiva el diario actual (no se borra),
    empieza uno nuevo con un BLOQUEO y lo rellena con lo que tiene la base. Lo que solo estaba en el diario
    anterior se pierde para la detección: hay que revisarlo a mano."""
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorRecuperacion(422, "Escribe el motivo (mínimo 5 caracteres)")
    with db.session() as s:
        e = evaluar(s, usar_cache=False)
    permitidos = {"DIARIO_MANIPULADO", "DIARIO_ILEGIBLE", "CLAVE_NO_DISPONIBLE", "DIARIO_TRUNCADO", "DIARIO_AUSENTE",
                  "COPIA_DIVERGENTE"}
    if not any(m["codigo"] in permitidos for m in e["motivos"]):
        raise ErrorRecuperacion(409, "El diario está íntegro: no hace falta reiniciarlo")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archivados = []
    with _lock:
        for p in (ruta_diario(), ruta_copia()):
            if p is not None and p.exists():
                dst = p.with_name(f"eventos-criticos.archivado-{stamp}.jsonl")
                huella = hashlib.sha256(p.read_bytes()).hexdigest()
                os.replace(p, dst)
                archivados.append({"archivo": dst.name, "sha256": huella})
        try:
            _leer_clave()
        except ErrorDiario:
            p = ruta_clave()
            os.replace(p, p.with_name(f"diario.clave.archivada-{stamp}"))
        _invalidar()
    with db.session() as s:
        _set_setting(s, K_ANCLA, None)
    try:
        registrar(db, "BLOQUEO", {"motivo": "DIARIO_REINICIADO", "archivados": [a["sha256"] for a in archivados]})
    except ErrorDiario as ex_:
        raise ErrorRecuperacion(503, f"No se pudo iniciar el diario nuevo: {ex_}") from ex_
    _audit(db, user, "recuperacion_diario_reiniciado", "diario",
           {"motivo": motivo[:300], "archivados": archivados, "motivos": [m["codigo"] for m in e["motivos"]]}, ip)
    out = preparar(db)
    out["archivados"] = archivados
    out["aviso"] = ("Los eventos que solo estaban en el diario anterior ya no se comparan con la base. Revisa a mano "
                    "las revocaciones y las citas posteriores al último respaldo antes de levantar el bloqueo.")
    return out


# ============================================================================ consola
def _db():
    from .db import Database
    return Database(settings.db_url)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "estado"
    if cmd == "estado":
        print(json.dumps(preparar(_db()), ensure_ascii=False, indent=2, default=str))
        return 0
    if cmd == "exportar-clave" and len(argv) == 2:
        raw = _leer_clave()
        if raw is None:
            print("No hay clave del diario.", file=sys.stderr)
            return 1
        dst = Path(argv[1])
        if dst.exists():
            print("El archivo de destino ya existe: no se sobrescribe.", file=sys.stderr)
            return 1
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("CLAVE DEL DIARIO DE EVENTOS CRÍTICOS DE CAUCE. Guárdala fuera de este equipo (sin ella la copia "
                    "externa del diario no se puede verificar).\n" + base64.b64encode(raw).decode() + "\n")
        print(f"Clave exportada a {dst}. Guárdala en un lugar seguro y bórrala de este equipo.")
        return 0
    if cmd == "importar-clave" and len(argv) == 2:
        if ruta_clave().exists():
            print("Ya hay una clave del diario: no se sobrescribe.", file=sys.stderr)
            return 1
        lineas = [x.strip() for x in Path(argv[1]).read_text("utf-8").splitlines() if x.strip()]
        raw = base64.b64decode(lineas[-1])
        if len(raw) < 32:
            print("La clave no es válida.", file=sys.stderr)
            return 1
        print(f"Clave importada ({_guardar_clave(raw)}).")
        return 0
    if cmd == "marcar-restauracion" and len(argv) == 2:
        anotar_restauracion(argv[1], None)
        print("Restauración registrada en el diario: el motor arrancará con las operaciones del portal bloqueadas.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

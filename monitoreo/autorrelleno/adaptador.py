"""Adaptador Ventanilla -> Forms (contrato `cauce.conector.v1`), solo para el portal de LABORATORIO.

Qué hace, en este orden, cada vez que el operador pulsa «Autorrellenar formulario»:

1. Comprueba la configuración de laboratorio (`configuracion()`): variable explícita, portal propio en
   127.0.0.1 con token de control, Forms instalado y un perfil de formulario APROBADO cuya huella coincide.
2. Comprueba, con la base y las reglas de Monitor (`evaluar()`), que ESTE operador puede trabajar ESTE
   expediente ahora: su atención abierta, su región/sucursal, el estado del expediente, el consentimiento
   vigente (otorgado, no revocado, no vencido, del mismo trámite, sin revocación pendiente en el diario) con
   su huella SHA-256 y su cadena de eventos íntegra, y que no hay una recuperación pendiente.
   Si algo falta, NO se lanza nada: se niega con un código y un motivo legible (`Negado`).
3. Construye la solicitud con SOLO los campos automáticos del contrato del portal que el perfil aprobado
   declara (nunca el nombre, la CURP, notas ni campos manuales). La autorización de la solicitud se deriva
   del consentimiento real (`aut_<código del consentimiento>`) y del usuario que pulsó (`rev_<usuario>`).
4. Lanza Forms (`python -m cauce_form_connector preparar`, una solicitud por proceso, stdin/stdout) con un
   entorno LIMPIO (sin variables MONITOREO_*: ni claves ni tokens de Monitor) mediante el lanzador de
   laboratorio, que termina todo el árbol de procesos. Forms abre una ventana visible, teclea y verifica;
   no envía. Mientras la ventana está abierta, Monitor vigila el consentimiento y la atención: si se
   revoca o se cierra, termina el proceso (CANCELADA).
5. Valida la respuesta con las reglas del contrato (incluida la búsqueda de datos personales) y guarda en
   el historial del expediente solo estados y nombres de campo. Nunca valores.

`conector.aislamiento.puede_lanzar()` sigue en False: este camino existe solo en laboratorio porque el
proceso de Forms corre con el mismo usuario de Windows que Monitor (podría leer su base). Con datos reales
eso no es aceptable sin el aislamiento descrito en docs/CONECTOR_CONTRATO_v1.md §4.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .. import entorno, recuperacion, security
from ..captura import consentimiento as cs
from ..captura import expedientes as ex
from ..captura import portal
from ..captura import ventanilla as vt
from ..captura.formularios import contrato as ct
from ..conector import contrato as C
from ..conector.registro import ErrorRegistro, Registro
from ..db import Database, utcnow
from ..models import Atencion, Expediente
from .lanzador_lab import LanzadorLab

log = logging.getLogger("monitoreo.autorrelleno")

VARIABLE = "MONITOREO_AUTORRELLENO_LAB"
_SI = ("1", "true", "si", "sí", "yes")
ESTADOS_PERMITIDOS = (ex.LISTO, ex.ASIGNADO, ex.CAPTURA)
ESPERA_POR_OMISION_S = 600
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# Forms solo escribe códigos fijos en stderr («ERROR_INTERNO TimeoutError»). Solo líneas con esa forma se
# conservan como diagnóstico; cualquier otra cosa (que podría llevar datos) se descarta.
_DIAG = re.compile(r"^[A-Z][A-Z_]{2,40}( [A-Za-z_][A-Za-z0-9_.]{0,60})?$")
_EN_CURSO = ("CREADA", "AUTORIZADA", "LANZADA")
# Variables del sistema que Forms necesita para arrancar Python y Chromium (nada de Monitor).
_ENV_SISTEMA = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME",
                "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                "LANG", "LC_ALL", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
# PLAYWRIGHT_BROWSERS_PATH de Monitor NO se pasa: es el Chromium de Playwright para Node (otra versión). Forms usa
# el suyo (<Forms>\.pw-browsers, el que instala su instalar_windows.bat).

MENSAJES = {
    "LANZADA": "Forms está rellenando el formulario del portal de laboratorio en una ventana del navegador. "
               "Revísalo con la persona, completa los campos manuales y cierra la ventana al terminar. "
               "Forms no envía nada.",
    "PREPARADO": "Forms rellenó y verificó el formulario; la ventana se cerró sin envío.",
    "ENVIADA_POR_PERSONA": "Se observó que alguien envió el formulario desde la ventana. Es solo un indicio: "
                           "no es una cita confirmada ni la registra en Monitor.",
    "CON_INCIDENCIAS": "Forms rellenó el formulario, pero algún campo o confirmación no quedó como se esperaba: "
                       "revísalo en la ventana.",
    "DETENIDA": "Forms se detuvo antes de teclear: la página cambió o pide intervención humana.",
    "RECHAZADA": "Forms rechazó la solicitud (perfil o autorización no válidos). No se tecleó nada.",
    "CADUCADA": "Forms rellenó y verificó el formulario, pero la ventana siguió abierta más del tiempo de "
                "revisión y se cerró sola.",
    "ERROR": "Forms tuvo un error interno. No se envió nada.",
    "CANCELADA": "Monitor detuvo el autorrellenado.",
    "INCIERTA": "Forms terminó sin una respuesta válida: no se sabe qué quedó en la ventana. Revisa el portal.",
}


class Negado(Exception):
    """El autorrellenado no procede. `codigo` estable para pruebas; `mensaje` para el operador (sin datos)."""

    def __init__(self, codigo: str, mensaje: str, http: int = 409):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.http = codigo, mensaje, http


# ---------------------------------------------------------------- configuración de laboratorio
@dataclass
class Config:
    comando: list[str]
    cwd: str | None
    env: dict
    perfil: dict
    espera_s: int


def _es_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("127.0.0.1", "localhost")


def _env_forms(perfil: dict, env: dict, forms_dir: Path | None = None) -> dict:
    """Entorno mínimo para el proceso de Forms: variables del sistema y las CAUCE_* que él entiende."""
    out = {k: env[k] for k in _ENV_SISTEMA if env.get(k)}
    if forms_dir is not None and (forms_dir / ".pw-browsers").is_dir():
        out["PLAYWRIGHT_BROWSERS_PATH"] = str(forms_dir / ".pw-browsers")
    if perfil.get("profiles_dir"):
        out["CAUCE_PROFILES_DIR"] = str(perfil["profiles_dir"])
    if perfil.get("state_dir"):
        out["CAUCE_STATE_DIR"] = str(perfil["state_dir"])
    if env.get("MONITOREO_FORMS_BROWSER_PATH"):
        out["CAUCE_BROWSER_PATH"] = env["MONITOREO_FORMS_BROWSER_PATH"]
    out["CAUCE_HEADLESS"] = "1" if (env.get("MONITOREO_FORMS_HEADLESS") or "").strip().lower() in _SI else "0"
    out["PYTHONIOENCODING"] = "utf-8"
    return out


def _python_forms(forms_dir: Path) -> Path:
    if sys.platform == "win32":
        return forms_dir / ".venv" / "Scripts" / "python.exe"
    return forms_dir / ".venv" / "bin" / "python"


def leer_perfil(ruta: Path) -> dict:
    try:
        p = json.loads(ruta.read_text(encoding="utf-8"))
        ok = (p.get("version") == 1 and re.fullmatch(r"frm_[a-z0-9_]{1,40}", p["formulario_id"])
              and re.fullmatch(r"[a-z0-9_]{1,40}", p["perfil"]) and _HEX64.match(p["perfil_sha256"])
              and re.fullmatch(r"v2:[0-9a-f]{16}", p["firma_estructura_esperada"])
              and isinstance(p["campos"], list) and p["campos"] and p.get("modo") in ("POR_DIA", "POR_HORARIO")
              and re.fullmatch(r"rev_[a-z0-9_]{1,40}", p["aprobado_por"]) and _es_local(p["url"]))
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise Negado("PERFIL_NO_APROBADO", "El perfil del formulario de laboratorio no está aprobado o es inválido: "
                     "vuelve a abrir el laboratorio con Forms (autorrelleno_laboratorio.bat).")
    return p


def configuracion(env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    if (env.get(VARIABLE) or "").strip().lower() not in _SI:
        raise Negado("AUTORRELLENO_DESACTIVADO", "El autorrellenado con Forms solo existe en el laboratorio "
                     f"({VARIABLE}=1).", 403)
    if (env.get("MONITOREO_ENTORNO") or "").strip().lower().replace("ó", "o") == entorno.PRODUCCION:
        raise Negado("AUTORRELLENO_DESACTIVADO", "El autorrellenado con Forms no existe en producción.", 403)
    url_portal = env.get("MONITOREO_PORTAL_URL") or portal.url()
    if not (env.get("MONITOREO_PORTAL_TOKEN_CONTROL") and _es_local(url_portal)):
        raise Negado("PORTAL_NO_LABORATORIO", "El portal configurado no es el portal de laboratorio propio "
                     "(127.0.0.1): no se autorrellena nada.", 403)
    ruta = env.get("MONITOREO_AUTORRELLENO_PERFIL")
    if not ruta:
        raise Negado("PERFIL_NO_APROBADO", "Falta el perfil aprobado del formulario (MONITOREO_AUTORRELLENO_PERFIL).")
    perfil = leer_perfil(Path(ruta))
    a, b = urlsplit(perfil["url"]), urlsplit(url_portal)
    if (a.scheme, a.hostname, a.port) != (b.scheme, b.hostname, b.port):
        raise Negado("PERFIL_DE_OTRO_PORTAL", "El perfil aprobado es de otro portal (otro puerto): vuelve a "
                     "aprobarlo abriendo el laboratorio con Forms.")
    try:
        espera = max(30, min(int(env.get("MONITOREO_AUTORRELLENO_ESPERA_S") or ESPERA_POR_OMISION_S), 900))
    except ValueError:
        espera = ESPERA_POR_OMISION_S
    if env.get("MONITOREO_FORMS_COMANDO"):           # pruebas: un conector de prueba en lugar de Forms
        comando, cwd, forms = json.loads(env["MONITOREO_FORMS_COMANDO"]), None, None
    else:
        forms = Path(env.get("MONITOREO_FORMS_DIR") or "")
        py = Path(env.get("MONITOREO_FORMS_PYTHON") or _python_forms(forms))
        if not (forms.is_dir() and (forms / "cauce_form_connector" / "integracion.py").is_file() and py.is_file()):
            raise Negado("FORMS_NO_INSTALADO", "No se encontró Forms instalado (MONITOREO_FORMS_DIR y su .venv).")
        comando, cwd = [str(py), "-m", "cauce_form_connector", "preparar"], str(forms)
    if perfil.get("profiles_dir"):
        archivo = Path(perfil["profiles_dir"]) / f"{perfil['perfil']}.json"
        try:
            sha = hashlib.sha256(archivo.read_bytes()).hexdigest()
        except OSError:
            sha = ""
        if sha != perfil["perfil_sha256"]:
            raise Negado("PERFIL_ALTERADO", "El perfil guardado en Forms no coincide con el aprobado: no se usa.")
    return Config(comando, cwd, _env_forms(perfil, env, forms), perfil, espera)


def estado_configuracion() -> dict:
    try:
        cfg = configuracion()
    except Negado as n:
        return {"habilitado": False, "codigo": n.codigo, "motivo": n.mensaje}
    return {"habilitado": True, "codigo": "", "motivo": "", "formulario_id": cfg.perfil["formulario_id"],
            "espera_revision_s": cfg.espera_s}


# ---------------------------------------------------------------- reglas de Monitor
def _ref(prefijo: str, texto: str) -> str:
    limpio = re.sub(r"[^a-z0-9_]", "_", (texto or "").lower()).strip("_")[:40] or "x"
    return f"{prefijo}_{limpio}"


@dataclass
class Contexto:
    atencion_id: int
    expediente_id: int
    expediente_codigo: str
    consentimiento_id: int
    consentimiento_codigo: str
    consentimiento_hasta: datetime | None
    campos: dict = field(repr=False)        # valores: nunca se registran
    sin_dato: list = field(default_factory=list)
    manuales: list = field(default_factory=list)
    etiquetas: dict = field(default_factory=dict)


def evaluar(db: Database, user, aid: int, now: datetime, cfg: Config) -> Contexto:
    """Todas las puertas de Monitor. Lanza `Negado` con el primer motivo encontrado."""
    with db.session() as s:
        try:
            a = vt._visible(s, aid, user)
        except vt.ErrorVentanilla as e:
            raise Negado("ATENCION_NO_ENCONTRADA", e.mensaje, 404) from e
        if a.username != user.username:
            raise Negado("NO_ES_TU_ATENCION", "Solo quien atiende a la persona puede autorrellenar su formulario.", 403)
        exp = s.get(Expediente, a.expediente_id) if a.expediente_id else None
        if exp is None:
            raise Negado("SIN_EXPEDIENTE", "La atención todavía no tiene expediente.")
        # El consentimiento va primero (es el motivo más importante) y se exige SIEMPRE aquí, aunque
        # MONITOREO_EXIGIR_CONSENTIMIENTO esté desactivado: sin consentimiento vigente no viaja ningún dato.
        c = cs.vigente(s, exp, now)
        if c is None:
            m = cs.motivo_bloqueo(s, exp, now)
            raise Negado(m["codigo"], m["mensaje"])
        if not vt.abierta(a):
            raise Negado("ATENCION_CERRADA", "La atención ya está cerrada.")
        if user.operator_id is None:
            raise Negado("SIN_OPERADOR", "Tu usuario no está vinculado a un operador.", 403)
        disponible, motivo = vt._disponible(s, exp, user)
        if not disponible:
            raise Negado("PERMISO_DENEGADO", f"No puedes trabajar este expediente: {motivo}.", 403)
        if exp.estado not in ESTADOS_PERMITIDOS:
            raise Negado("ESTADO_NO_PERMITIDO", f"El expediente está {exp.estado}: solo se autorrellena un expediente "
                         "LISTO o tomado por ti en Ventanilla.")
        if exp.estado != ex.LISTO and exp.operator_id != user.operator_id:
            raise Negado("PERMISO_DENEGADO", "El expediente está a cargo de otro operador.", 403)
        if exp.modo != cfg.perfil["modo"]:
            raise Negado("MODO_NO_SOPORTADO", f"El perfil aprobado es del formulario {cfg.perfil['modo']} y el "
                         f"expediente se captura {exp.modo}.")
        if not _HEX64.match(c.evidencia_sha256 or ""):
            raise Negado("CONSENTIMIENTO_SIN_HUELLA", "El consentimiento no tiene la huella del documento firmado.")
        if recuperacion.evaluar(s)["bloqueado"]:
            raise Negado("RECUPERACION_PENDIENTE", "Hay una recuperación pendiente de revisar por un administrador.")
        contrato = portal.contrato(exp.modo)
        declarados = set(cfg.perfil["campos"])
        todos = ct.valores_desde(contrato, exp)
        valores: dict[str, str] = {}
        sin_dato: list[str] = []
        for campo in contrato.automaticos:
            if campo.nombre not in declarados:
                continue                     # el perfil aprobado no lo declara: no viaja
            v = todos.get(campo.nombre)
            if campo.requerido_si and not ct.requerido(campo, todos):
                continue                     # p. ej. folio anterior: solo al reprogramar
            if v is None:
                if ct.requerido(campo, todos):
                    sin_dato.append(campo.nombre)
                continue
            valores[campo.nombre] = v
        ctx = Contexto(a.id, exp.id, exp.codigo, c.id, c.codigo, c.vigente_hasta, valores, sin_dato,
                       [m.etiqueta for m in contrato.manuales], {x.nombre: x.etiqueta for x in contrato.campos})
    verif = cs.verificar(db, ctx.consentimiento_id)
    if not verif["ok"]:
        raise Negado("CONSENTIMIENTO_NO_VERIFICABLE", "La cadena de eventos del consentimiento no cuadra: un "
                     "administrador debe revisarla antes de usarlo.")
    if not ctx.campos:
        raise Negado("SIN_DATOS", "El expediente no tiene ninguno de los datos que pide el formulario.")
    return ctx


def construir_solicitud(ctx: Contexto, cfg: Config, user, ahora: datetime | None = None) -> dict:
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    hasta = ahora.date()
    if ctx.consentimiento_hasta is not None:
        hasta = min(hasta, ctx.consentimiento_hasta.date())
    p = cfg.perfil
    return {
        "contrato": C.VERSION, "tipo": "preparar_formulario", "solicitud_id": "sol_" + secrets.token_hex(12),
        "emitida_en": f(ahora - timedelta(seconds=1)), "caduca_en": f(ahora + timedelta(seconds=300)),
        "autorizacion": {"ref": _ref("aut", ctx.consentimiento_codigo), "formulario_id": p["formulario_id"],
                         "otorgada_por_ref": _ref("rev", user.username), "vigente_hasta": hasta.isoformat(),
                         "consentimiento_vigente": True},
        "formulario": {"formulario_id": p["formulario_id"], "url": p["url"], "perfil": p["perfil"],
                       "perfil_sha256": p["perfil_sha256"], "firma_estructura_esperada": p["firma_estructura_esperada"]},
        "campos": dict(ctx.campos),
        "opciones": {"espera_revision_max_s": cfg.espera_s, "pausa_tecla_ms": 40},
    }


# ---------------------------------------------------------------- ejecución
@dataclass
class Corrida:
    solicitud_id: str
    expediente_id: int
    expediente_codigo: str
    atencion_id: int
    username: str
    consentimiento_codigo: str
    iniciada_en: datetime
    estado: str = "CREADA"
    codigo: str = ""
    informe: dict | None = None
    terminada_en: datetime | None = None
    cancelacion: str = ""
    procesos_restantes: int = 0              # procesos del árbol de Forms que no se pudo terminar (debe ser 0)
    diagnostico: list = field(default_factory=list)   # códigos fijos de stderr de Forms (nunca texto libre)
    lanzador: LanzadorLab | None = field(default=None, repr=False)

    @property
    def en_curso(self) -> bool:
        return self.estado in _EN_CURSO

    def vista(self) -> dict:
        from ..api.serializers import iso
        return {"solicitud_id": self.solicitud_id, "expediente": self.expediente_codigo, "estado": self.estado,
                "codigo": self.codigo, "en_curso": self.en_curso, "mensaje": MENSAJES.get(self.estado, ""),
                "consentimiento": self.consentimiento_codigo, "informe": self.informe,
                "procesos_restantes": self.procesos_restantes, "diagnostico": self.diagnostico,
                "iniciada_en": iso(self.iniciada_en), "terminada_en": iso(self.terminada_en)}


_LOCK = threading.Lock()
_CORRIDAS: dict[int, Corrida] = {}           # por expediente: la última corrida
_REGISTRO = Registro()                       # máquina de estados del contrato (en memoria, sin datos)


def corrida_de(exp_id: int) -> Corrida | None:
    return _CORRIDAS.get(exp_id)


def en_curso() -> Corrida | None:
    return next((c for c in _CORRIDAS.values() if c.en_curso), None)


def apagar(espera_s: float = 15.0) -> Corrida | None:
    """Al detener Monitor: cancela la corrida en curso (termina Forms y su Chromium) y espera a que acabe.
    Sin esto, una ventana abierta seguiría viva hasta su propio plazo tras reiniciar Monitor."""
    c = en_curso()
    if c is None:
        return None
    c.cancelacion = c.cancelacion or "MONITOR_DETENIDO"
    if c.lanzador is not None:
        c.lanzador.cancelar()
    fin = time.monotonic() + espera_s
    while c.en_curso and time.monotonic() < fin:
        time.sleep(0.1)
    return c


def _auditar(db: Database, user, accion: str, objetivo: str, detalle: dict) -> None:
    try:
        with db.session() as s:
            security.audit(s, user.username, accion, objetivo, detalle)
    except Exception:  # noqa: BLE001 - la auditoría no debe tumbar la respuesta al operador
        log.exception("no se pudo auditar %s", accion)


def informe_sin_valores(resp: dict, ctx: Contexto) -> dict:
    """Lo que ve el operador: etiqueta, estado y si coincide. Ni valores ni longitudes."""
    campos = [{"campo": c["campo"], "etiqueta": ctx.etiquetas.get(c["campo"], c["campo"]), "estado": c["estado"],
               "correcto": c["estado"] in ("ok", "ok_normalizado")} for c in resp.get("campos", [])]
    return {"estado_forms": resp.get("estado"), "campos": campos,
            "confirmaciones": [{"campo": c["campo"], "etiqueta": ctx.etiquetas.get(c["campo"], c["campo"]),
                                "coinciden": bool(c["coinciden"])} for c in resp.get("confirmaciones", [])],
            "errores": [e["codigo"] for e in resp.get("errores", [])],
            "sin_dato": [{"campo": n, "etiqueta": ctx.etiquetas.get(n, n)} for n in ctx.sin_dato],
            "manuales": ctx.manuales,
            "estructura_coincide": bool(resp.get("formulario", {}).get("estructura_coincide")),
            "envio_realizado_por_conector": bool(resp.get("envio", {}).get("realizado_por_conector")),
            "envio_humano_observado": bool(resp.get("envio", {}).get("envio_humano_observado")),
            "revision_humana": resp.get("revision_humana", {}).get("resultado")}


def iniciar(db: Database, user, aid: int, cfg: Config | None = None, esperar: bool = False) -> dict:
    """Comprueba todo y lanza Forms en segundo plano. `esperar=True` (pruebas) espera a que termine."""
    try:
        cfg = cfg or configuracion()
        now = utcnow()
        ctx = evaluar(db, user, aid, now, cfg)
        with _LOCK:
            otra = en_curso()
            if otra is not None:
                raise Negado("YA_HAY_UNA_EN_CURSO", "Ya hay un autorrellenado abierto (expediente "
                             f"{otra.expediente_codigo}). Cierra esa ventana primero.")
            sol = construir_solicitud(ctx, cfg, user)
            ahora = datetime.now(timezone.utc)
            try:
                _REGISTRO.crear(sol, f"autorrelleno:{ctx.expediente_codigo}:{sol['solicitud_id']}", ahora)
                _REGISTRO.mover(sol["solicitud_id"], "AUTORIZADA", ahora)
            except ErrorRegistro as e:
                raise Negado("SOLICITUD_INVALIDA", f"La solicitud no cumple el contrato ({e.codigo}).") from e
            corrida = Corrida(sol["solicitud_id"], ctx.expediente_id, ctx.expediente_codigo, aid, user.username,
                              ctx.consentimiento_codigo, now, estado="AUTORIZADA")
            _CORRIDAS[ctx.expediente_id] = corrida
    except Negado as n:
        _auditar(db, user, "autorrelleno_negado", f"atencion:{aid}", {"codigo": n.codigo})
        raise
    detalle = {"solicitud_id": sol["solicitud_id"], "consentimiento": ctx.consentimiento_codigo,
               "formulario_id": cfg.perfil["formulario_id"], "campos": sorted(ctx.campos), "sin_dato": ctx.sin_dato}
    with db.session() as s:
        ex.evento(s, ctx.expediente_id, "AUTORRELLENO_SOLICITADO", vt._actor(user), now, **detalle)
        security.audit(s, user.username, "autorrelleno_solicitado", ctx.expediente_codigo,
                       {"solicitud_id": sol["solicitud_id"], "consentimiento": ctx.consentimiento_codigo})
    hilo = threading.Thread(target=_correr, args=(db, corrida, sol, cfg, ctx), daemon=True,
                            name=f"autorrelleno-{ctx.expediente_codigo}")
    hilo.start()
    if esperar:
        hilo.join(cfg.espera_s + C.MARGEN_PROCESO_S + 30)
    return corrida.vista()


def _vigilar(db: Database, corrida: Corrida, parar: threading.Event, periodo_s: float = 1.0) -> None:
    """Mientras la ventana está abierta: si el consentimiento deja de estar vigente o la atención se cierra,
    se termina el proceso de Forms (y todo lo que abrió)."""
    while not parar.wait(periodo_s):
        motivo = ""
        try:
            with db.session() as s:
                exp = s.get(Expediente, corrida.expediente_id)
                a = s.get(Atencion, corrida.atencion_id)
                if exp is None or cs.vigente(s, exp, utcnow()) is None:
                    motivo = "CONSENTIMIENTO_NO_VIGENTE"
                elif a is None or not vt.abierta(a):
                    motivo = "ATENCION_CERRADA"
        except Exception:  # noqa: BLE001 - si no se puede comprobar, se para (falla cerrado)
            motivo = "NO_SE_PUDO_VERIFICAR"
        if motivo:
            corrida.cancelacion = motivo
            if corrida.lanzador is not None:
                corrida.lanzador.cancelar()
            return


def _correr(db: Database, corrida: Corrida, sol: dict, cfg: Config, ctx: Contexto) -> None:
    sid = sol["solicitud_id"]
    parar = threading.Event()
    resp = None
    try:
        lz = LanzadorLab(modo_laboratorio=True)
        corrida.lanzador = lz
        if corrida.cancelacion:              # Monitor se está deteniendo: no se llega a lanzar nada útil
            lz.cancelar()
        _REGISTRO.mover(sid, "LANZADA", datetime.now(timezone.utc))
        corrida.estado = "LANZADA"
        threading.Thread(target=_vigilar, args=(db, corrida, parar), daemon=True).start()
        res = lz.lanzar(cfg.comando, entrada=json.dumps(sol, ensure_ascii=False), cwd=cfg.cwd, env=cfg.env,
                        plazo_s=cfg.espera_s + C.MARGEN_PROCESO_S)
        ahora = datetime.now(timezone.utc)
        corrida.procesos_restantes = len(res.sobrevivientes)
        # stderr NUNCA se guarda tal cual: solo las líneas con forma de código fijo (ver _DIAG).
        corrida.diagnostico = [x.strip() for x in res.stderr.splitlines() if _DIAG.match(x.strip())][:3]
        if res.estado == "CANCELADO":
            reg = _REGISTRO.mover(sid, "CANCELADA", ahora, corrida.cancelacion or "CANCELADA")
        elif res.estado == "PLAZO" or not res.stdout.strip():
            reg = _REGISTRO.proceso_terminado_sin_respuesta(sid, ahora)
        else:
            try:
                resp = json.loads(res.stdout)
            except ValueError:
                resp = None
            reg = _REGISTRO.recibir(sol, resp, ahora, res.codigo_salida)
            if reg["estado"] == "INCIERTA":
                resp = None              # una respuesta que no pasa el contrato no se muestra
        corrida.estado, corrida.codigo = reg["estado"], reg["codigo"]
    except Exception as e:  # noqa: BLE001 - fallo seguro: sin texto de la excepción
        log.error("autorrelleno %s: fallo interno %s", sid, type(e).__name__)
        corrida.estado, corrida.codigo, resp = "ERROR", "ERROR_INTERNO_MONITOR", None
    finally:
        parar.set()
        corrida.terminada_en = utcnow()
    corrida.informe = informe_sin_valores(resp, ctx) if resp else None
    inf = corrida.informe or {}
    detalle = {"solicitud_id": sid, "estado": corrida.estado, "codigo": corrida.codigo,
               "campos_correctos": sum(1 for c in inf.get("campos", []) if c["correcto"]),
               "campos_total": len(inf.get("campos", [])),
               "confirmaciones_coinciden": sum(1 for c in inf.get("confirmaciones", []) if c["coinciden"]),
               "envio_por_conector": bool(inf.get("envio_realizado_por_conector")),
               "envio_humano_observado": bool(inf.get("envio_humano_observado")),
               "procesos_restantes": corrida.procesos_restantes, "diagnostico": corrida.diagnostico}
    try:
        with db.session() as s:
            ex.evento(s, corrida.expediente_id, "AUTORRELLENO_TERMINADO", f"{corrida.username} (ventanilla)",
                      utcnow(), **detalle)
            security.audit(s, corrida.username, "autorrelleno_terminado", corrida.expediente_codigo,
                           {"solicitud_id": sid, "estado": corrida.estado, "codigo": corrida.codigo})
    except Exception:  # noqa: BLE001
        log.exception("no se pudo registrar el fin del autorrellenado %s", sid)


def reiniciar_para_pruebas() -> None:
    global _REGISTRO
    with _LOCK:
        _CORRIDAS.clear()
        _REGISTRO = Registro()

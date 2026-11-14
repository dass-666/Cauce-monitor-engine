"""v2.2 · Consentimiento verificable por expediente.

Qué garantiza:

1. **Sin consentimiento vigente no hay operación.** Un consentimiento está vigente si fue otorgado
   (no en el futuro), no está revocado, no venció y es del mismo trámite que el expediente. Sin él:
   - el expediente no pasa a LISTO (queda INCOMPLETO con el faltante «consentimiento»), así que la
     cola del motor nunca lo recibe;
   - no entra al plan del día ni se toma en Ventanilla;
   - no se prepara, no se envían datos, no se consulta el calendario con su sesión, no se elige
     día y no se confirma.

   Cada una de esas puertas llama a `exigir()`. Confirmar la bloquea con la fila del
   consentimiento: una revocación y una confirmación simultáneas se ordenan, nunca se cruzan.

2. **La revocación detiene lo pendiente** (`aplicar_revocacion`, y el mantenimiento lo reintenta):
   - se retira del plan;
   - se descarta el intento sin confirmar;
   - se cancela la solicitud de la cola, aunque estuviera asignada;
   - se cierra la atención abierta;
   - se cierra el expediente con motivo CONSENTIMIENTO_REVOCADO.

   Una confirmación que **ya se había enviado** al portal antes de revocar termina (la
   recuperación la resuelve). Una cita ya confirmada NO se cancela sola: cancelarla es otra
   operación que decide la persona. Se registra un aviso para el responsable.

3. **Registro verificable.** Cada otorgamiento y cada revocación se añade a una cadena SHA-256 por
   consentimiento (`consentimiento_eventos`). `verificar()` recalcula las cadenas y detecta
   eventos cambiados, borrados o intercalados, y estados que no cuadran con sus eventos.

   Es integridad interna: detecta la manipulación de la base, pero no sustituye una firma ante
   terceros. El documento firmado no se guarda: solo su huella SHA-256 y una referencia corta de
   dónde está el original.

La exigencia se controla con `MONITOREO_EXIGIR_CONSENTIMIENTO` (por omisión 1 = sí). Desactivarla
solo tiene sentido para recorridos de versiones anteriores. Desactivada, nada cambia respecto a la 2.1.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta

from sqlalchemy import select, update

from ..models import Atencion, Consentimiento, ConsentimientoEvento, Expediente, PlanElemento, Reservacion
from ..security import audit

log = logging.getLogger("monitoreo.consentimiento")

FINALIDADES = {"GESTION_CITA": "Gestionar la cita del trámite indicado: buscar fecha, preparar los datos, "
                               "confirmar y dar seguimiento"}
MEDIOS = {"FIRMA_PAPEL": "Formato firmado en papel", "FIRMA_DIGITAL": "Firma digital o electrónica",
          "PRESENCIAL_VENTANILLA": "Presencial ante el operador, con formato firmado"}
MEDIOS_REVOCACION = {"ESCRITO": "Por escrito", "PRESENCIAL": "En persona", "TELEFONO": "Por teléfono",
                     "CORREO": "Por correo"}
MOTIVOS_REVOCACION = {"PERSONA_LO_PIDE": "La persona lo pidió", "YA_NO_REQUIERE": "Ya no requiere la cita",
                      "ERROR_REGISTRO": "Se registró por error", "OTRO": "Otro"}
VIGENTE, REVOCADO = "VIGENTE", "REVOCADO"
MOTIVO_CIERRE = "CONSENTIMIENTO_REVOCADO"
MAX_ANTIGUEDAD_DIAS = 365          # un consentimiento otorgado hace más de un año no se acepta como nuevo
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_AVISO = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_REF = re.compile(r"^[\w ./:#-]{0,64}$")
VIVAS_SIN_CONFIRMAR = ("PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "FECHA_ELEGIDA")
EN_CONFIRMACION = ("CONFIRMANDO", "EN_VERIFICACION")


class ErrorConsentimiento(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def exigido() -> bool:
    return os.environ.get("MONITOREO_EXIGIR_CONSENTIMIENTO", "1").strip().lower() not in ("0", "no", "false")


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ---------------------------------------------------------------- consulta y puertas
def vigente(s, exp: Expediente, now: datetime, bloquear: bool = False) -> Consentimiento | None:
    q = (select(Consentimiento).where(Consentimiento.expediente_id == exp.id,
                                      Consentimiento.tipo_tramite == exp.tipo_tramite,
                                      Consentimiento.estado == VIGENTE, Consentimiento.otorgado_en <= now)
         .order_by(Consentimiento.id.desc()))
    if bloquear:
        q = q.with_for_update()
    for c in s.scalars(q):
        if c.vigente_hasta is None or c.vigente_hasta > now:
            return c
    return None


def motivo_bloqueo(s, exp: Expediente, now: datetime) -> dict:
    """Por qué no hay consentimiento vigente (para el mensaje al operador). Sin datos personales."""
    ultimo = s.scalar(select(Consentimiento).where(Consentimiento.expediente_id == exp.id)
                      .order_by(Consentimiento.id.desc()).limit(1))
    if ultimo is None:
        return {"codigo": "SIN_CONSENTIMIENTO", "mensaje": "El expediente no tiene consentimiento registrado"}
    if ultimo.estado == REVOCADO:
        return {"codigo": MOTIVO_CIERRE, "consentimiento": ultimo.codigo,
                "mensaje": f"La persona revocó su consentimiento ({ultimo.codigo}, "
                           f"{ultimo.revocado_en:%Y-%m-%d %H:%M} UTC): no se permiten nuevas operaciones"}
    if ultimo.tipo_tramite != exp.tipo_tramite:
        return {"codigo": "CONSENTIMIENTO_OTRO_TRAMITE", "consentimiento": ultimo.codigo,
                "mensaje": f"El consentimiento {ultimo.codigo} es para otro trámite"}
    if ultimo.vigente_hasta is not None and ultimo.vigente_hasta <= now:
        return {"codigo": "CONSENTIMIENTO_VENCIDO", "consentimiento": ultimo.codigo,
                "mensaje": f"El consentimiento {ultimo.codigo} venció el {ultimo.vigente_hasta:%Y-%m-%d}"}
    return {"codigo": "SIN_CONSENTIMIENTO", "mensaje": "El expediente no tiene un consentimiento vigente"}


def exigir(s, exp: Expediente, now: datetime, accion: str, error_cls=ErrorConsentimiento,
           bloquear: bool = False) -> None:
    """Puerta común: lanza `error_cls(409, mensaje, detalle)` si la exigencia está activa y no hay
    consentimiento vigente. `bloquear=True` toma la fila del consentimiento (confirmar)."""
    if not exigido():
        return
    if vigente(s, exp, now, bloquear=bloquear) is not None:
        return
    m = motivo_bloqueo(s, exp, now)
    log.info("operación bloqueada por consentimiento: %s %s (%s)", accion, exp.codigo, m["codigo"])
    raise error_cls(409, m["mensaje"], {"codigo": m["codigo"], "accion": accion, "expediente": exp.codigo})


def revocado(s, exp_id: int) -> Consentimiento | None:
    """El último consentimiento del expediente, si está REVOCADO y no hay otro vigente."""
    cs = list(s.scalars(select(Consentimiento).where(Consentimiento.expediente_id == exp_id)
                        .order_by(Consentimiento.id.desc())))
    if not cs or any(c.estado == VIGENTE for c in cs):
        return None
    return cs[0]


# ---------------------------------------------------------------- cadena verificable
def _semilla(codigo: str) -> str:
    return hashlib.sha256(f"CONSENTIMIENTO|{codigo}".encode()).hexdigest()


def _canon(ev: dict) -> str:
    return json.dumps(ev, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _contenido(c_codigo: str, tipo: str, ts: datetime, actor: str, detalle: dict) -> dict:
    return {"consentimiento": c_codigo, "tipo": tipo, "ts": _naive(ts).isoformat(timespec="microseconds"),
            "actor": actor, "detalle": detalle}


def _sellar(anterior: str, contenido: dict) -> str:
    return hashlib.sha256((anterior + "|" + _canon(contenido)).encode()).hexdigest()


def _agregar_evento(s, c: Consentimiento, tipo: str, actor: str, now: datetime, detalle: dict) -> ConsentimientoEvento:
    ultimo = s.scalar(select(ConsentimientoEvento.sello).where(ConsentimientoEvento.consentimiento_id == c.id)
                      .order_by(ConsentimientoEvento.id.desc()).limit(1))
    anterior = ultimo or _semilla(c.codigo)
    ts = _naive(now)
    ev = ConsentimientoEvento(consentimiento_id=c.id, expediente_id=c.expediente_id, tipo=tipo, ts=ts, actor=actor[:64],
                              detalle=detalle, anterior=anterior,
                              sello=_sellar(anterior, _contenido(c.codigo, tipo, ts, actor[:64], detalle)))
    s.add(ev)
    s.flush()
    return ev


def _estado_registrado(c: Consentimiento) -> dict:
    """Lo que el evento guarda del consentimiento: si alguien cambia la fila después, verificar() lo ve."""
    return {"expediente_id": c.expediente_id, "tipo_tramite": c.tipo_tramite, "finalidad": c.finalidad,
            "medio": c.medio, "otorgado_en": _naive(c.otorgado_en).isoformat(timespec="seconds"),
            "vigente_hasta": _naive(c.vigente_hasta).isoformat(timespec="seconds") if c.vigente_hasta else None,
            "responsable": c.responsable, "aviso_privacidad": c.aviso_privacidad,
            "evidencia_sha256": c.evidencia_sha256, "evidencia_ref": c.evidencia_ref}


def verificar(db, consentimiento_id: int | None = None) -> dict:
    """Recalcula las cadenas. Devuelve {ok, consentimientos, eventos, problemas:[...]}. Solo lectura."""
    problemas: list[dict] = []
    n_ev = 0
    with db.session() as s:
        q = select(Consentimiento).order_by(Consentimiento.id)
        if consentimiento_id is not None:
            q = q.where(Consentimiento.id == consentimiento_id)
        cs = list(s.scalars(q))
        for c in cs:
            evs = list(s.scalars(select(ConsentimientoEvento).where(ConsentimientoEvento.consentimiento_id == c.id)
                                 .order_by(ConsentimientoEvento.id)))
            n_ev += len(evs)
            anterior = _semilla(c.codigo)
            for ev in evs:
                esperado = _sellar(anterior, _contenido(c.codigo, ev.tipo, ev.ts, ev.actor, ev.detalle or {}))
                if ev.anterior != anterior or ev.sello != esperado:
                    problemas.append({"consentimiento": c.codigo, "evento": ev.id, "problema": "cadena rota (evento "
                                      "cambiado, borrado o intercalado)"})
                    break
                anterior = ev.sello
            tipos = [e.tipo for e in evs]
            if not tipos or tipos[0] != "OTORGADO":
                problemas.append({"consentimiento": c.codigo, "problema": "no empieza con OTORGADO"})
            elif (evs[0].detalle or {}).get("registro") != _estado_registrado(c):
                problemas.append({"consentimiento": c.codigo, "problema": "los datos del consentimiento no coinciden "
                                  "con los sellados al otorgarlo"})
            if len(evs) != c.version:
                problemas.append({"consentimiento": c.codigo, "problema": f"{len(evs)} evento(s) y versión {c.version}"})
            if (c.estado == REVOCADO) != ("REVOCADO" in tipos):
                problemas.append({"consentimiento": c.codigo, "problema": f"estado {c.estado} sin su evento"})
    return {"ok": not problemas, "consentimientos": len(cs), "eventos": n_ev, "problemas": problemas}


# ---------------------------------------------------------------- otorgar
def _fecha(v, campo: str, errores: dict) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        return _naive(v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00")))
    except ValueError:
        errores[campo] = "Fecha y hora no válidas (AAAA-MM-DDTHH:MM)"
        return None


def validar_datos(d: dict, exp: Expediente, now: datetime) -> tuple[dict, dict]:
    e: dict[str, str] = {}
    out: dict = {}
    tramite = (d.get("tipo_tramite") or exp.tipo_tramite or "").strip()
    if tramite != exp.tipo_tramite:
        e["tipo_tramite"] = "El consentimiento debe ser para el trámite del expediente"
    out["tipo_tramite"] = tramite
    f = (d.get("finalidad") or "GESTION_CITA").strip().upper()
    if f not in FINALIDADES:
        e["finalidad"] = "Finalidad no reconocida"
    out["finalidad"] = f
    m = (d.get("medio") or "").strip().upper()
    if m not in MEDIOS:
        e["medio"] = "Indica cómo se otorgó: " + ", ".join(MEDIOS)
    out["medio"] = m
    otorgado = _fecha(d.get("otorgado_en"), "otorgado_en", e) or (None if "otorgado_en" in e else now)
    if otorgado is not None:
        if otorgado > now + timedelta(minutes=5):
            e["otorgado_en"] = "No puede ser futura"
        elif otorgado < now - timedelta(days=MAX_ANTIGUEDAD_DIAS):
            e["otorgado_en"] = f"Tiene más de {MAX_ANTIGUEDAD_DIAS} días: pide uno nuevo"
    out["otorgado_en"] = otorgado
    hasta = _fecha(d.get("vigente_hasta"), "vigente_hasta", e)
    if hasta is not None and otorgado is not None and hasta <= otorgado:
        e["vigente_hasta"] = "Debe ser posterior al otorgamiento"
    out["vigente_hasta"] = hasta
    aviso = str(d.get("aviso_privacidad") or "").strip()
    if not _AVISO.match(aviso):
        e["aviso_privacidad"] = "Versión del aviso de privacidad que se le mostró (p. ej. AP-2026-01)"
    out["aviso_privacidad"] = aviso
    h = str(d.get("evidencia_sha256") or "").strip().lower()
    if not _HEX64.match(h):
        e["evidencia_sha256"] = "Huella SHA-256 (64 caracteres hexadecimales) del documento firmado"
    out["evidencia_sha256"] = h
    ref = " ".join(str(d.get("evidencia_ref") or "").split())
    if not _REF.match(ref) or re.search(r"\d{8,}|@", ref):
        e["evidencia_ref"] = "Referencia corta de dónde está el original, sin datos personales"
    out["evidencia_ref"] = ref
    return out, e


def otorgar(s, exp: Expediente, d: dict, actor: str, now: datetime, ip: str = "") -> Consentimiento:
    """Registra un consentimiento en la sesión dada (misma transacción que el alta, si viene con ella)."""
    if exp.estado in ("CERRADO",):
        raise ErrorConsentimiento(409, "El expediente está cerrado: abre uno nuevo")
    datos, errores = validar_datos(d, exp, now)
    if errores:
        raise ErrorConsentimiento(422, "Datos del consentimiento inválidos", {"errores": errores})
    # Un solo consentimiento vigente por expediente y trámite. El cambio de versión del expediente
    # serializa dos registros simultáneos (el segundo ve el primero o choca y reintenta el usuario).
    if s.execute(update(Expediente).where(Expediente.id == exp.id, Expediente.version == exp.version)
                 .values(version=Expediente.version + 1, updated_at=now)).rowcount != 1:
        raise ErrorConsentimiento(409, "El expediente cambió mientras tanto: recárgalo")
    s.refresh(exp)
    if vigente(s, exp, now) is not None:
        raise ErrorConsentimiento(409, "Ya hay un consentimiento vigente para este expediente y trámite")
    c = Consentimiento(codigo=f"TMP-{os.urandom(6).hex()}", expediente_id=exp.id, responsable=actor[:64], estado=VIGENTE,
                       version=1, created_at=_naive(now), **datos)
    s.add(c)
    s.flush()
    c.codigo = f"CON-{c.id:06d}"
    s.flush()
    _agregar_evento(s, c, "OTORGADO", actor, now, {"registro": _estado_registrado(c)})
    from . import expedientes as ex
    ex.evento(s, exp.id, "CONSENTIMIENTO_REGISTRADO", actor[:64], now, consentimiento=c.codigo,
              finalidad=c.finalidad, medio=c.medio)
    audit(s, actor, "consentimiento_otorgado", c.codigo, {"expediente": exp.codigo, "finalidad": c.finalidad,
                                                          "medio": c.medio}, ip)
    if exp.estado in (ex.NUEVO, ex.INCOMPLETO):
        ex.validar_expediente(s, exp, actor[:64], now)
    return c


def otorgar_seguro(db, exp_id: int, d: dict, user, now: datetime, ip: str = "") -> dict:
    from . import expedientes as ex
    actor = getattr(user, "username", str(user))
    with db.session() as s:
        exp = s.get(Expediente, exp_id)
        if exp is None or not ex.puede_ver(user, exp):
            raise ErrorConsentimiento(404, "Expediente no encontrado")
        c = otorgar(s, exp, d, actor, now, ip)
        return {"consentimiento": vista(c), "expediente": {"codigo": exp.codigo, "estado": exp.estado}}


# ---------------------------------------------------------------- revocar
def revocar(db, consentimiento_id: int, d: dict, user, now: datetime, ip: str = "") -> dict:
    """Registra la revocación (definitiva) y aplica sus efectos. Idempotente: revocar dos veces
    devuelve la misma revocación."""
    from . import expedientes as ex
    actor = getattr(user, "username", str(user))
    medio = (d.get("medio") or "").strip().upper()
    motivo = (d.get("motivo") or "PERSONA_LO_PIDE").strip().upper()
    errores = {}
    if medio not in MEDIOS_REVOCACION:
        errores["medio"] = "Indica cómo la pidió: " + ", ".join(MEDIOS_REVOCACION)
    if motivo not in MOTIVOS_REVOCACION:
        errores["motivo"] = "Motivo no reconocido"
    if errores:
        raise ErrorConsentimiento(422, "Datos de la revocación inválidos", {"errores": errores})
    with db.session() as s:
        c = s.get(Consentimiento, consentimiento_id)
        exp = s.get(Expediente, c.expediente_id) if c else None
        if c is None or exp is None or not ex.puede_ver(user, exp):
            raise ErrorConsentimiento(404, "Consentimiento no encontrado")
        if c.estado == REVOCADO:
            return {"consentimiento": vista(c), "repetida": True, "efectos": None}
        t = Consentimiento.__table__
        # Condicional sobre estado y versión: una revocación simultánea o una confirmación que tiene
        # la fila bloqueada se ordenan (la segunda ve el resultado de la primera).
        ok = s.execute(update(t).where(t.c.id == c.id, t.c.estado == VIGENTE, t.c.version == c.version).values(
            estado=REVOCADO, revocado_en=_naive(now), revocado_por=actor[:64], revocacion_medio=medio,
            revocacion_motivo=motivo, version=t.c.version + 1)).rowcount
        s.refresh(c)
        if ok != 1:
            if c.estado == REVOCADO:
                return {"consentimiento": vista(c), "repetida": True, "efectos": None}
            raise ErrorConsentimiento(409, "El consentimiento cambió mientras tanto: recárgalo")
        _agregar_evento(s, c, "REVOCADO", actor, now, {"medio": medio, "motivo": motivo})
        ex.evento(s, exp.id, "CONSENTIMIENTO_REVOCADO", actor[:64], now, consentimiento=c.codigo, medio=medio,
                  motivo=motivo)
        audit(s, actor, "consentimiento_revocado", c.codigo, {"expediente": exp.codigo, "medio": medio,
                                                              "motivo": motivo}, ip)
        # Lo primero, en la MISMA transacción: que el plan no le asigne una unidad más.
        _retirar_del_plan(s, exp.id, actor, now)
        exp.ultimo_error = f"{MOTIVO_CIERRE}: no se permiten nuevas operaciones"
        vc = vista(c)
    efectos = aplicar_revocacion(db, c.expediente_id, actor, now)
    return {"consentimiento": vc, "repetida": False, "efectos": efectos}


def _retirar_del_plan(s, exp_id: int, actor: str, now: datetime) -> int:
    from ..planificador import plan as pl
    from . import expedientes as ex
    n = 0
    for el in s.scalars(select(PlanElemento).where(PlanElemento.activo_expediente_id == exp_id)):
        if el.reservacion_id and s.scalar(select(Reservacion.estado).where(Reservacion.id == el.reservacion_id)) == "CONFIRMADA":
            continue        # cita ya confirmada: el plan debe seguir contándola (lo refleja su mantenimiento)
        if el.estado in (pl.EN_ESPERA, pl.ASIGNADO, pl.PREPARADO, pl.NO_DISPONIBLE):
            antes = el.estado
            el.estado, el.activo_expediente_id, el.updated_at, el.version = pl.RETIRADO, None, now, el.version + 1
            el.explicacion = {**(el.explicacion or {}), "retirado_por": MOTIVO_CIERRE}
            ex.evento(s, exp_id, "PLAN_RETIRADO", actor[:64], now, motivo=MOTIVO_CIERRE, estado_anterior=antes,
                      elemento=el.id)
            n += 1
    return n


def aplicar_revocacion(db, exp_id: int, actor: str, now: datetime, cerrar_atencion: bool = True) -> dict:
    """Detiene lo pendiente del expediente cuyo consentimiento se revocó. Idempotente; si algo no
    puede hacerse aún (confirmación en curso), lo deja para el mantenimiento."""
    from ..engine import assignment as asg
    from . import expedientes as ex
    from . import reservaciones as rs
    out = {"plan_retirados": 0, "intento_descartado": None, "solicitud_cancelada": False, "atencion_cerrada": None,
           "expediente_cerrado": False, "pendiente": None, "aviso_cita_confirmada": False}
    with db.session() as s:
        exp = s.get(Expediente, exp_id)
        if exp is None or revocado(s, exp_id) is None:
            return out
        out["plan_retirados"] = _retirar_del_plan(s, exp_id, actor, now)
        res = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
        res_id, res_estado = (res.id, res.estado) if res else (None, None)
        estado_exp, req_id = exp.estado, exp.request_id
    if estado_exp == ex.RES_CONFIRMADA or res_estado == "CONFIRMADA":
        with db.session() as s:
            ya = s.scalar(select(Expediente.id).join(
                ConsentimientoEvento, ConsentimientoEvento.expediente_id == Expediente.id).where(
                Expediente.id == exp_id, ConsentimientoEvento.tipo == "AVISO_CITA_CONFIRMADA"))
            if not ya:
                c = revocado(s, exp_id)
                _agregar_evento(s, c, "AVISO_CITA_CONFIRMADA", "motor", now,
                                {"nota": "cita ya confirmada: su cancelación la decide la persona"})
                c.version += 1
                ex.evento(s, exp_id, "CONSENTIMIENTO_REVOCADO_CON_CITA", "motor", now)
                ex._aviso(s, exp_id, "CONSENTIMIENTO_REVOCADO_CON_CITA", now, None,
                          {"mensaje": "Consentimiento revocado con una cita ya confirmada: acuerda con la persona "
                                      "si se cancela (no se cancela sola)"})
        out["aviso_cita_confirmada"] = True
        return out
    if res_estado in EN_CONFIRMACION:
        out["pendiente"] = "confirmación ya enviada al portal: se espera su resultado"
        return out
    if res_estado in VIVAS_SIN_CONFIRMAR:
        v = rs._cerrar_intento(db, res_id, rs.CANCELADA, MOTIVO_CIERRE, "consentimiento revocado", actor, now,
                               desde=VIVAS_SIN_CONFIRMAR)
        out["intento_descartado"] = bool(v)
        if not v:
            out["pendiente"] = "el intento cambió mientras tanto"
            return out
    if req_id:
        out["solicitud_cancelada"] = asg.cancel_request(db, req_id, now, actor[:64])
    with db.session() as s:
        exp = s.get(Expediente, exp_id)
        a = s.scalar(select(Atencion).where(Atencion.viva_expediente_id == exp_id)) if cerrar_atencion else None
        if a is not None:
            a.estado, a.resultado, a.viva_expediente_id, a.cerrada_en, a.updated_at = (
                "CANCELADA", MOTIVO_CIERRE, None, now, now)
            out["atencion_cerrada"] = a.codigo
        if exp.estado in ex.ACTIVOS:
            if s.scalar(select(Reservacion.id).where(Reservacion.viva_expediente_id == exp_id)):
                out["pendiente"] = "queda un intento vivo"
                return out
            ex.cerrar(s, exp, MOTIVO_CIERRE, actor[:64], now, nota="consentimiento revocado")
            out["expediente_cerrado"] = True
    return out


def aplicar_revocaciones(db, now: datetime, limite: int = 100) -> list[dict]:
    """Mantenimiento: reintenta los efectos de revocaciones cuyo expediente sigue activo."""
    from . import expedientes as ex
    with db.session() as s:
        ids = list(s.scalars(select(Expediente.id).join(Consentimiento, Consentimiento.expediente_id == Expediente.id)
                             .where(Consentimiento.estado == REVOCADO, Expediente.estado.in_(ex.ACTIVOS))
                             .distinct().limit(limite)))
        # Una confirmación que estaba en curso al revocar y que después se resolvió como CONFIRMADA:
        # el expediente ya no está activo, pero falta el aviso a la persona responsable.
        con_aviso = select(ConsentimientoEvento.expediente_id).where(ConsentimientoEvento.tipo == "AVISO_CITA_CONFIRMADA")
        ids += [i for i in s.scalars(
            select(Expediente.id).join(Consentimiento, Consentimiento.expediente_id == Expediente.id)
            .where(Consentimiento.estado == REVOCADO, Expediente.estado == ex.RES_CONFIRMADA,
                   Expediente.id.notin_(con_aviso)).distinct().limit(limite)) if i not in ids]
    out = []
    for i in ids:
        try:
            r = aplicar_revocacion(db, i, "motor", now)
        except Exception as e:  # noqa: BLE001 - se reintenta en el siguiente ciclo
            log.warning("aplicar revocación de %s falló: %s", i, type(e).__name__)
            continue
        if r["expediente_cerrado"] or r["intento_descartado"] or r["solicitud_cancelada"] or r["aviso_cita_confirmada"]:
            out.append({"expediente_id": i, **r})
    return out


# ---------------------------------------------------------------- vistas
def vista(c: Consentimiento) -> dict:
    return {"id": c.id, "codigo": c.codigo, "expediente_id": c.expediente_id, "tipo_tramite": c.tipo_tramite,
            "finalidad": c.finalidad, "finalidad_texto": FINALIDADES.get(c.finalidad, c.finalidad), "medio": c.medio,
            "otorgado_en": _naive(c.otorgado_en).isoformat() + "Z",
            "vigente_hasta": _naive(c.vigente_hasta).isoformat() + "Z" if c.vigente_hasta else None,
            "responsable": c.responsable, "aviso_privacidad": c.aviso_privacidad, "evidencia_sha256": c.evidencia_sha256,
            "evidencia_ref": c.evidencia_ref, "estado": c.estado,
            "revocado_en": _naive(c.revocado_en).isoformat() + "Z" if c.revocado_en else None,
            "revocado_por": c.revocado_por, "revocacion_medio": c.revocacion_medio,
            "revocacion_motivo": c.revocacion_motivo, "version": c.version}


def de_expediente(s, exp_id: int) -> list[dict]:
    return [vista(c) for c in s.scalars(select(Consentimiento).where(Consentimiento.expediente_id == exp_id)
                                        .order_by(Consentimiento.id))]


def eventos_de_expediente(s, exp_id: int) -> list[dict]:
    return [{"ts": _naive(e.ts).isoformat() + "Z", "tipo": e.tipo, "actor": e.actor, "detalle": e.detalle,
             "sello": e.sello[:12]} for e in s.scalars(select(ConsentimientoEvento).where(
                 ConsentimientoEvento.expediente_id == exp_id).order_by(ConsentimientoEvento.id))]

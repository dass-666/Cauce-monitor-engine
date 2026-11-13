"""Expedientes (v1.5, estados nuevos en v1.6): alta, validación, estados, permisos y trazabilidad.

Estados (v1.6) y transiciones permitidas (`TRANSICIONES`; cualquier otra se rechaza):

  NUEVO ──> EN_VALIDACION ──> LISTO ──> ASIGNADO ──> CAPTURA_EN_PROCESO ──> RESERVACION_PENDIENTE
    │            │  │           ▲  │        │                 │                      │
    └> INCOMPLETO┘  └> REQUIERE_REVISION    └────── (asignación liberada o rechazada: vuelve a LISTO)
                                                                              RESERVACION_CONFIRMADA
  CERRADO (con motivo: CANCELADO, FALLIDO, SIN_DISPONIBILIDAD, RETENCION...) desde cualquier estado
  sin operación activa.

- Un expediente puede abrirse INCOMPLETO (preparación anticipada) con nombre, región y trámite.
  NUNCA se ofrece a un operador hasta estar LISTO: la `Request` del motor se crea SOLO al pasar
  a LISTO (una por "ronda"; código `EXP-000123`, luego `EXP-000123-R2`...).
- Tres identificadores distintos que nunca se mezclan: `id_registro_previo` (el que el titular ya
  tiene en el portal), `codigo` (interno del expediente) y el `folio` final (en la reservación).
- La `Request` NUNCA lleva datos personales: su titular es el código del expediente.
- Las confirmaciones de teléfono y correo solo se comparan al validar la entrada: no se guardan.
"""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Assignment, CalendarioDia, Expediente, ExpedienteEvento, Operator, Region, Request
from . import validacion as val

# ---------------------------------------------------------------- estados
NUEVO, INCOMPLETO, EN_VALIDACION, LISTO = "NUEVO", "INCOMPLETO", "EN_VALIDACION", "LISTO"
ASIGNADO, CAPTURA, RES_PENDIENTE = "ASIGNADO", "CAPTURA_EN_PROCESO", "RESERVACION_PENDIENTE"
RES_CONFIRMADA, REVISION, CERRADO = "RESERVACION_CONFIRMADA", "REQUIERE_REVISION", "CERRADO"
ESTADOS = (NUEVO, INCOMPLETO, EN_VALIDACION, LISTO, ASIGNADO, CAPTURA, RES_PENDIENTE, RES_CONFIRMADA, REVISION,
           CERRADO)
PREPARACION = (NUEVO, INCOMPLETO, EN_VALIDACION)
OPERACION_ACTIVA = (ASIGNADO, CAPTURA, RES_PENDIENTE)       # un operador trabaja en él: no se borra ni se toca
ACTIVOS = (NUEVO, INCOMPLETO, EN_VALIDACION, LISTO, ASIGNADO, CAPTURA, RES_PENDIENTE, REVISION)
CERRADOS = (RES_CONFIRMADA, CERRADO)       # ya no esperan disponibilidad (la huella de duplicado se libera)
TRANSICIONES = {
    NUEVO: {INCOMPLETO, EN_VALIDACION, CERRADO},
    INCOMPLETO: {NUEVO, EN_VALIDACION, CERRADO},
    EN_VALIDACION: {LISTO, INCOMPLETO, REVISION, CERRADO},
    LISTO: {ASIGNADO, NUEVO, REVISION, CERRADO},
    ASIGNADO: {CAPTURA, LISTO, REVISION, CERRADO},
    CAPTURA: {RES_PENDIENTE, LISTO, REVISION, CERRADO, RES_CONFIRMADA},
    RES_PENDIENTE: {RES_CONFIRMADA, CAPTURA, LISTO, REVISION, CERRADO},
    RES_CONFIRMADA: {CERRADO},
    REVISION: {NUEVO, LISTO, CAPTURA, RES_CONFIRMADA, CERRADO},
    CERRADO: set(),
}
GRUPO = {NUEVO: "preparacion", INCOMPLETO: "preparacion", EN_VALIDACION: "preparacion", LISTO: "pendiente",
         ASIGNADO: "pendiente", CAPTURA: "en_proceso", RES_PENDIENTE: "en_proceso", RES_CONFIRMADA: "confirmado",
         REVISION: "revision", CERRADO: "cerrado"}
MOTIVOS_CIERRE = ("CANCELADO", "FALLIDO", "SIN_DISPONIBILIDAD", "DUPLICADO", "RETENCION", "REPROGRAMADO", "OTRO")
TIPO_RECURSO = {"POR_HORARIO": "cita", "POR_DIA": "cita_dia"}
CAMPOS_COINCIDENCIA = {"region", "sucursal", "sucursales_alternativas", "tipo_tramite", "modo"}

# Eventos normalizados (v1.5) y de historial
EVENTOS_NORMALIZADOS = ("DISPONIBILIDAD_DETECTADA", "RECURSO_SELECCIONADO", "FORMULARIO_PREPARADO",
                        "RESERVACION_PENDIENTE", "RESERVACION_CONFIRMADA", "RESERVACION_RECHAZADA",
                        "RESERVACION_EXPIRADA")
# Motivos con los que un operador puede rechazar una asignación (v1.6)
MOTIVOS_RECHAZO = {"NO_DISPONIBLE": "El operador no puede atenderlo ahora",
                   "CARGA_EXCESIVA": "El operador tiene demasiados casos",
                   "CONFLICTO_DE_INTERES": "Conflicto de interés con el titular",
                   "DATOS_DUDOSOS": "Los datos del expediente parecen incorrectos",
                   "OTRO": "Otro motivo (ver nota)"}


class ErrorExpediente(Exception):
    def __init__(self, http: int, mensaje: str, errores: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.errores = http, mensaje, errores or {}


def evento(s: Session, exp_id: int, tipo: str, actor: str, now: datetime, assignment_id: int | None = None,
           reservacion_id: int | None = None, change_event_id: int | None = None, **detalle) -> None:
    """Registra un evento del expediente. `detalle` nunca debe llevar datos personales."""
    s.add(ExpedienteEvento(expediente_id=exp_id, ts=now, tipo=tipo, actor=actor[:64], assignment_id=assignment_id,
                           reservacion_id=reservacion_id, change_event_id=change_event_id, detalle=detalle))


def cambiar_estado(s: Session, exp: Expediente, nuevo: str, actor: str, now: datetime, **detalle) -> None:
    """Único punto que cambia el estado: valida la transición y deja rastro (ESTADO antes/después)."""
    antes = exp.estado
    if antes == nuevo:
        return
    if nuevo not in TRANSICIONES.get(antes, set()):
        raise ErrorExpediente(409, f"Cambio de estado no permitido: {antes} -> {nuevo}")
    exp.estado, exp.updated_at = nuevo, now
    exp.version += 1
    evento(s, exp.id, "ESTADO", actor, now, antes=antes, despues=nuevo, **detalle)


def _regiones(s: Session) -> set[str]:
    return set(s.scalars(select(Region.code)))


def _contrato(exp: Expediente):
    from .portal import contrato
    return contrato(exp.modo)


def _condiciones(exp: Expediente) -> dict:
    """Condiciones de la solicitud del motor (sin datos personales).
    POR_DIA: atributos del recurso (trámite y sucursal preferida + alternativas), con preferencia.
    POR_HORARIO (v1.5): la sucursal va en el nombre del recurso."""
    if exp.modo == "POR_DIA":
        c: dict = {"atributos": {"tramite": exp.tipo_tramite}}
        sucs = [x for x in [exp.sucursal, *(exp.sucursales_alternativas or [])] if x]
        if sucs:
            c["atributos"]["sucursal"] = sucs
        if exp.sucursal:
            c["preferida"] = {"sucursal": exp.sucursal}
        return c
    return {"nombre_contiene": f"({exp.sucursal})"} if exp.sucursal else {}


def _nueva_solicitud(s: Session, exp: Expediente, now: datetime) -> Request:
    exp.ronda = (exp.ronda or 0) + 1
    code = exp.codigo if exp.ronda == 1 else f"{exp.codigo}-R{exp.ronda}"
    req = Request(code=code, holder=exp.codigo, region=exp.region, resource_type=TIPO_RECURSO[exp.modo],
                  priority=exp.prioridad, quantity=1, conditions=_condiciones(exp), status="PENDIENTE",
                  created_at=now, updated_at=now)
    s.add(req)
    s.flush()
    exp.request_id = req.id
    return req


def _cancelar_solicitud_en_sesion(s: Session, exp: Expediente, actor: str, now: datetime) -> None:
    """Cancela la solicitud aún no asignada (misma transacción). Si el motor la asignó justo ahora,
    se aborta todo con 409 y el usuario vuelve a intentarlo (nunca se le quita a un operador en silencio)."""
    from ..engine import assignment as asg
    if not exp.request_id:
        return
    req = s.get(Request, exp.request_id)
    if req is None or req.status in asg.TERMINAL:
        return
    vivo = s.scalar(select(Assignment.id).where(Assignment.request_id == req.id,
                                                Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
    if vivo or req.status in (asg.ASIGNADO, asg.EN_PROCESO):
        raise ErrorExpediente(409, "El motor acaba de asignar este expediente: recárgalo antes de cambiarlo")
    try:
        asg.transition(s, req, asg.CANCELADO, actor, "expediente modificado: se vuelve a validar", now)
    except (asg.ConcurrentChange, asg.TransitionError) as e:
        raise ErrorExpediente(409, "El expediente cambió mientras tanto: recárgalo") from e


# ---------------------------------------------------------------- validación (NUEVO -> LISTO)
def faltantes(exp: Expediente) -> list[str]:
    from .formularios import contrato as ct
    return ct.faltantes_expediente(_contrato(exp), exp)


def _region_de_sucursal(s: Session, suc: str) -> str | None:
    return s.scalar(select(CalendarioDia.region).where(CalendarioDia.sucursal == suc).limit(1))


def validar_expediente(s: Session, exp: Expediente, actor: str, now: datetime) -> str:
    """NUEVO/INCOMPLETO -> EN_VALIDACION -> LISTO | INCOMPLETO | REQUIERE_REVISION. Devuelve el estado.
    Al llegar a LISTO crea la solicitud del motor (solo entonces puede asignarse)."""
    if exp.estado not in (NUEVO, INCOMPLETO):
        return exp.estado
    falta = faltantes(exp)
    if falta:
        if exp.estado == NUEVO:
            cambiar_estado(s, exp, INCOMPLETO, actor, now, faltan=falta)
        return exp.estado
    cambiar_estado(s, exp, EN_VALIDACION, actor, now)
    problemas = []
    # duplicado: la huella exige la fecha de nacimiento (al crear incompleto aún no existía)
    huella = val.huella_duplicado(exp.nombre, exp.fecha_nacimiento, exp.tipo_tramite)
    otro = s.scalar(select(Expediente.id).where(Expediente.dedup_activo == huella, Expediente.id != exp.id))
    if otro:
        problemas.append("DUPLICADO_PROBABLE")
    if exp.id_registro_previo and s.scalar(select(Expediente.id).where(
            Expediente.id != exp.id, Expediente.id_registro_previo == exp.id_registro_previo,
            Expediente.tipo_tramite == exp.tipo_tramite, Expediente.estado.in_(ACTIVOS))):
        problemas.append("REGISTRO_PREVIO_REPETIDO")
    for suc in [x for x in [exp.sucursal, *(exp.sucursales_alternativas or [])] if x]:
        reg = _region_de_sucursal(s, suc)
        if reg is not None and reg != exp.region:
            problemas.append(f"SUCURSAL_FUERA_DE_REGION:{suc}")
    if problemas:
        cambiar_estado(s, exp, REVISION, actor, now, problemas=problemas)
        exp.ultimo_error = "Requiere revisión: " + ", ".join(problemas)
        return exp.estado
    if exp.dedup_activo != huella:
        try:
            with s.begin_nested():          # la huella es UNIQUE: si otro la tomó a la vez, revisión
                exp.dedup_activo = huella
                s.flush()
        except IntegrityError:
            s.refresh(exp)
            cambiar_estado(s, exp, REVISION, actor, now, problemas=["DUPLICADO_PROBABLE"])
            return exp.estado
    cambiar_estado(s, exp, LISTO, actor, now)
    exp.validado_en, exp.ultimo_error = now, None
    req = _nueva_solicitud(s, exp, now)
    evento(s, exp.id, "SOLICITUD_CREADA", actor, now, solicitud=req.code, ronda=exp.ronda, modo=exp.modo)
    return exp.estado


def validar_pendientes(db, now: datetime, limite: int = 200) -> dict:
    """Trabajo periódico: valida los expedientes NUEVO (p. ej. creados por importación)."""
    out = {"listos": 0, "incompletos": 0, "revision": 0}
    with db.session() as s:
        ids = list(s.scalars(select(Expediente.id).where(Expediente.estado == NUEVO).order_by(Expediente.id)
                             .limit(limite)))
    for eid in ids:
        try:
            with db.session() as s:
                e = s.get(Expediente, eid)
                r = validar_expediente(s, e, "motor", now)
                out[{"LISTO": "listos", "INCOMPLETO": "incompletos"}.get(r, "revision")] += 1
        except (ErrorExpediente, IntegrityError):
            continue
    return out


# ---------------------------------------------------------------- alta, modificación, cierre
def crear(s: Session, entrada: dict, actor: str, now: datetime, modo_defecto: str = "POR_DIA") -> Expediente:
    r = val.validar(entrada, _regiones(s), incompleto=True)
    if not r.ok:
        raise ErrorExpediente(422, "Datos inválidos", r.errores)
    d = r.datos
    d.setdefault("modo", modo_defecto)
    d.setdefault("sucursales_alternativas", [])
    huella = None
    if d.get("fecha_nacimiento"):
        huella = val.huella_duplicado(d["nombre"], d["fecha_nacimiento"], d["tipo_tramite"])
        if s.scalar(select(Expediente.id).where(Expediente.dedup_activo == huella)):
            raise ErrorExpediente(409, "Ya existe un expediente activo para esa persona y ese trámite")
    if d.get("id_externo") and s.scalar(select(Expediente.id).where(Expediente.id_externo == d["id_externo"])):
        raise ErrorExpediente(409, "Ese identificador externo ya está registrado")
    # código provisional único y luego el definitivo a partir del id (sin carreras entre altas)
    exp = Expediente(codigo=f"TMP-{secrets.token_hex(8)}", estado=NUEVO, dedup_activo=huella, creado_por=actor[:64],
                     ronda=0, created_at=now, updated_at=now, **d)
    s.add(exp)
    s.flush()
    exp.codigo = f"EXP-{exp.id:06d}"
    evento(s, exp.id, "CREADO", actor, now, campos=sorted(d), modo=exp.modo)
    validar_expediente(s, exp, actor, now)
    exp.version = 1          # la validación inicial es parte del alta: la primera versión editable es la 1
    return exp


def crear_seguro(db, entrada: dict, actor: str, now: datetime, modo_defecto: str | None = None) -> dict:
    """Crea en su propia transacción; dos altas simultáneas del mismo titular: una gana y la
    otra recibe 409 (lo garantiza el UNIQUE de `dedup_activo`, no solo la comprobación previa)."""
    from ..engine.params import get_params
    try:
        with db.session() as s:
            modo = modo_defecto or str(get_params(s).get("captura_modo", "POR_DIA"))
            exp = crear(s, entrada, actor, now, modo)
            return vista(s, exp, completa=True)
    except IntegrityError as e:
        raise ErrorExpediente(409, "Expediente duplicado (alta simultánea)") from e


def modificar(s: Session, exp: Expediente, entrada: dict, version: int, actor: str, now: datetime) -> list[str]:
    """Modificación con control de versión: si otro la cambió antes, 409 (no se pisa).
    - En preparación o LISTO: si cambia algo que decide a quién/qué se ofrece (región, sucursales,
      trámite, modalidad) la solicitud aún no asignada se cancela y el expediente se vuelve a validar.
    - Con un operador trabajando (ASIGNADO/CAPTURA): solo cambios que lo dejen completo y sin
      tocar región, sucursal, trámite ni modalidad (para eso, liberar antes la asignación)."""
    if exp.estado in (RES_PENDIENTE, RES_CONFIRMADA, CERRADO):
        raise ErrorExpediente(409, f"No se puede modificar un expediente {exp.estado}")
    r = val.validar(entrada, _regiones(s), parcial=True)
    if not r.ok:
        raise ErrorExpediente(422, "Datos inválidos", r.errores)
    cambios = {k: v for k, v in r.datos.items() if getattr(exp, k) != v}
    if not cambios:
        return []
    if exp.estado in OPERACION_ACTIVA and CAMPOS_COINCIDENCIA & set(cambios):
        raise ErrorExpediente(409, "Hay un operador trabajando en este expediente: libera la asignación antes de "
                                   "cambiar región, sucursal, trámite o modalidad")
    nuevo = {c: cambios.get(c, getattr(exp, c)) for c in ("nombre", "fecha_nacimiento", "tipo_tramite")}
    huella = (val.huella_duplicado(nuevo["nombre"], nuevo["fecha_nacimiento"], nuevo["tipo_tramite"])
              if nuevo["fecha_nacimiento"] else None)
    if huella and huella != exp.dedup_activo and s.scalar(select(Expediente.id).where(Expediente.dedup_activo == huella)):
        raise ErrorExpediente(409, "Con esos datos ya existe otro expediente activo")
    t = Expediente.__table__
    hecho = s.execute(update(t).where(t.c.id == exp.id, t.c.version == version)
                      .values(**cambios, dedup_activo=huella, version=t.c.version + 1, updated_at=now)).rowcount
    if hecho != 1:
        raise ErrorExpediente(409, "El expediente cambió mientras lo editabas. Recárgalo.")
    s.expire(exp)
    if exp.estado in OPERACION_ACTIVA and faltantes(exp):
        raise ErrorExpediente(422, "Con un operador trabajando en él, el expediente debe seguir completo")
    # historial: SOLO nombres de campos, nunca valores (minimización)
    evento(s, exp.id, "MODIFICADO", actor, now, campos=sorted(cambios), version=version + 1)
    revalidar = exp.estado in (NUEVO, INCOMPLETO) or (exp.estado == LISTO and (
        CAMPOS_COINCIDENCIA & set(cambios) or faltantes(exp)))
    if revalidar:
        if exp.estado == LISTO:
            _cancelar_solicitud_en_sesion(s, exp, actor, now)
            cambiar_estado(s, exp, NUEVO, actor, now, motivo="modificado")
        elif exp.estado == INCOMPLETO:
            cambiar_estado(s, exp, NUEVO, actor, now, motivo="modificado")
        validar_expediente(s, exp, actor, now)
    elif {"prioridad"} & set(cambios) and exp.request_id:
        req = s.get(Request, exp.request_id)
        if req.status in ("PENDIENTE", "ELEGIBLE", "REINTENTO", "ERROR"):
            req.priority = exp.prioridad
    return sorted(cambios)


def revalidar(s: Session, exp: Expediente, actor: str, now: datetime) -> str:
    """Tras corregir un expediente en REQUIERE_REVISION (o INCOMPLETO): volver a validarlo."""
    if exp.estado not in (REVISION, INCOMPLETO):
        raise ErrorExpediente(409, f"Solo se revalida un expediente en revisión o incompleto (está {exp.estado})")
    if exp.estado == REVISION:
        from .reservaciones import viva_bloquea
        if viva_bloquea(s, exp.id):
            raise ErrorExpediente(409, "Hay una reservación en verificación: espera su resultado")
        if exp.request_id:
            req = s.get(Request, exp.request_id)
            from ..engine import assignment as asg
            if req is not None and req.status not in asg.TERMINAL:
                _cancelar_solicitud_en_sesion(s, exp, actor, now)
        exp.operator_id = None
    cambiar_estado(s, exp, NUEVO, actor, now, motivo="revalidar")
    return validar_expediente(s, exp, actor, now)


def cerrar(s: Session, exp: Expediente, motivo: str, actor: str, now: datetime, nota: str = "") -> None:
    """CERRADO con motivo. La huella de duplicado se libera (el titular puede volver a solicitar)."""
    motivo = motivo if motivo in MOTIVOS_CIERRE else "OTRO"
    cambiar_estado(s, exp, CERRADO, actor, now, motivo=motivo, nota=nota[:200])
    exp.motivo_cierre, exp.dedup_activo = motivo, None


def confirmar(s: Session, exp: Expediente, actor: str, now: datetime, folio: str) -> None:
    cambiar_estado(s, exp, RES_CONFIRMADA, actor, now, folio=folio)
    exp.dedup_activo, exp.ultimo_error = None, None


def a_revision(s: Session, exp: Expediente, actor: str, now: datetime, motivo: str) -> None:
    if exp.estado != REVISION:
        cambiar_estado(s, exp, REVISION, actor, now, motivo=motivo)
    exp.ultimo_error = f"Requiere revisión: {motivo}"[:500]


def coherencia(s: Session) -> list[dict]:
    """Auditoría de coherencia entre estados (la usan las pruebas y el panel). Lista vacía = todo bien."""
    from ..engine import assignment as asg
    from ..models import Reservacion
    malos = []
    for e in s.scalars(select(Expediente)):
        req = s.get(Request, e.request_id) if e.request_id else None
        vivo = s.scalar(select(Assignment.id).where(Assignment.request_id == e.request_id,
                                                    Assignment.status.in_(["ACTIVA", "EN_PROCESO"]))) if req else None
        conf = s.scalar(select(Reservacion.id).where(Reservacion.expediente_id == e.id,
                                                     Reservacion.estado == "CONFIRMADA"))
        if e.estado in PREPARACION and req is not None and req.status not in asg.TERMINAL:
            malos.append({"expediente": e.codigo, "problema": "solicitud viva sin estar LISTO"})
        if e.estado in (INCOMPLETO,) and vivo:
            malos.append({"expediente": e.codigo, "problema": "incompleto con asignación"})
        if e.estado in (CAPTURA, RES_PENDIENTE) and e.operator_id is None:
            malos.append({"expediente": e.codigo, "problema": f"{e.estado} sin operador"})
        if e.estado == RES_CONFIRMADA and not conf:
            malos.append({"expediente": e.codigo, "problema": "confirmado sin reservación CONFIRMADA"})
        if conf and e.estado not in (RES_CONFIRMADA, CERRADO):
            malos.append({"expediente": e.codigo, "problema": f"reservación CONFIRMADA con expediente {e.estado}"})
        if e.estado in CERRADOS and e.dedup_activo is not None:
            malos.append({"expediente": e.codigo, "problema": "cerrado que retiene la huella de duplicado"})
    return malos


# ---------------------------------------------------------------- permisos y vistas
def puede_ver(user, exp: Expediente) -> bool:
    """El administrador ve todo. Un operador solo ve los expedientes asignados a él."""
    return user.is_admin or (user.operator_id is not None and exp.operator_id == user.operator_id)


def consulta_visible(user):
    q = select(Expediente)
    if not user.is_admin:
        q = q.where(Expediente.operator_id == (user.operator_id if user.operator_id is not None else -1))
    return q


def vista(s: Session, exp: Expediente, completa: bool = False) -> dict:
    from ..api.serializers import iso
    op = s.get(Operator, exp.operator_id) if exp.operator_id else None
    base = {"id": exp.id, "codigo": exp.codigo, "estado": exp.estado, "grupo": GRUPO.get(exp.estado),
            "region": exp.region, "sucursal": exp.sucursal, "sucursales_alternativas": exp.sucursales_alternativas or [],
            "tipo_tramite": exp.tipo_tramite, "modo": exp.modo, "ronda": exp.ronda,
            "operacion": exp.operacion, "prioridad": exp.prioridad, "operador": op.name if op else None,
            "operator_id": exp.operator_id, "request_id": exp.request_id, "version": exp.version,
            "motivo_cierre": exp.motivo_cierre, "faltan": faltantes(exp) if exp.estado in PREPARACION else [],
            "creado": iso(exp.created_at), "actualizado": iso(exp.updated_at), "validado": iso(exp.validado_en),
            "ultimo_error": exp.ultimo_error, "anonimizado": exp.anonimizado_en is not None}
    if exp.anonimizado_en is not None:
        base.update(nombre="(anonimizado)", fecha_nacimiento=None, telefono=None, correo=None,
                    id_registro_previo=None, id_externo=None, folio_anterior=None, datos_completos=False)
    elif completa:
        base.update(nombre=exp.nombre, fecha_nacimiento=exp.fecha_nacimiento.isoformat() if exp.fecha_nacimiento else None,
                    telefono=exp.telefono, correo=exp.correo, id_registro_previo=exp.id_registro_previo,
                    id_externo=exp.id_externo, folio_anterior=exp.folio_anterior, datos_completos=True)
    else:
        base.update(nombre=val.enmascarar_nombre(exp.nombre),
                    fecha_nacimiento="••••-••-••" if exp.fecha_nacimiento else None,
                    telefono=val.enmascarar_telefono(exp.telefono) if exp.telefono else None,
                    correo=val.enmascarar_correo(exp.correo) if exp.correo else None,
                    id_registro_previo=("•••" + exp.id_registro_previo[-3:]) if exp.id_registro_previo else None,
                    id_externo=exp.id_externo, folio_anterior="•••" if exp.folio_anterior else None,
                    datos_completos=False)
    return base


def historial(s: Session, exp_id: int) -> list[dict]:
    from ..api.serializers import iso
    q = select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == exp_id).order_by(
        ExpedienteEvento.ts, ExpedienteEvento.id)
    return [{"id": e.id, "ts": iso(e.ts), "tipo": e.tipo, "actor": e.actor, "asignacion": e.assignment_id,
             "reservacion": e.reservacion_id, "evento_cambio": e.change_event_id, "detalle": e.detalle,
             "normalizado": e.tipo in EVENTOS_NORMALIZADOS} for e in s.scalars(q)]

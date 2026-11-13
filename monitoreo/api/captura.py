"""API de captura y reservaciones de laboratorio (v1.5; POR DÍA, estados y rechazos en v1.6).

Permisos: el administrador ve y actúa sobre todo; un operador solo sobre los expedientes que el
motor le asignó (los demás responden 404, igual que si no existieran). Los listados siempre
enmascaran los datos personales; la ficha completa solo la ve el operador asignado o un
administrador, y cada consulta de la ficha completa queda auditada (sin valores).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .. import security
from ..captura import calendario as cal
from ..captura import expedientes as ex
from ..captura import reservaciones as rs
from ..captura.portal import contrato as contrato_actual
from ..db import Database, utcnow
from ..models import Expediente, ExpedienteEvento, Reservacion
from .deps import CurrentUser, current_user, get_db

router = APIRouter(prefix="/api")

_s = Field(default=None, max_length=128)


class ExpedienteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str | None = _s
    fecha_nacimiento: str | None = Field(default=None, max_length=10)
    telefono: str | None = Field(default=None, max_length=20)
    telefono_confirmacion: str | None = Field(default=None, max_length=20)
    correo: str | None = _s
    correo_confirmacion: str | None = _s
    region: str | None = Field(default=None, max_length=32)
    sucursal: str | None = Field(default=None, max_length=32)
    tipo_tramite: str | None = Field(default=None, max_length=32)
    operacion: str | None = Field(default=None, max_length=16)
    prioridad: int | None = None
    id_externo: str | None = Field(default=None, max_length=64)
    id_registro_previo: str | None = Field(default=None, max_length=32)
    # v1.6
    modo: Literal["POR_DIA", "POR_HORARIO"] | None = None
    sucursales_alternativas: list[str] | None = Field(default=None, max_length=5)
    folio_anterior: str | None = Field(default=None, max_length=32)


class ExpedientePatch(ExpedienteIn):
    version: int = Field(ge=1)


class EnviarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revisado: bool = False
    # v1.6: lo que TECLEÓ el operador en los campos MANUAL_REQUIRED (no se guarda)
    manuales: dict[str, str] | None = Field(default=None, max_length=10)
    sucursal: str | None = Field(default=None, max_length=32)
    mes: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class SeleccionDiaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sucursal: str | None = Field(default=None, max_length=32)


class RechazoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    motivo: Literal["NO_DISPONIBLE", "CARGA_EXCESIVA", "CONFLICTO_DE_INTERES", "DATOS_DUDOSOS", "OTRO"]
    nota: str = Field(default="", max_length=200)


class SeleccionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horario: str = Field(min_length=3, max_length=64)
    region: str | None = Field(default=None, max_length=32)
    sucursal: str | None = Field(default=None, max_length=32)


class ConfirmarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmo: bool = False


def _err(e):
    if isinstance(e, ex.ErrorExpediente):
        raise HTTPException(e.http, {"mensaje": e.mensaje, "errores": e.errores})
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle})


def _puede_capturar(user: CurrentUser) -> None:
    if not user.is_admin and user.operator_id is None:
        raise HTTPException(403, "Tu usuario no está vinculado a un operador")


# ======================================================================= resumen
@router.get("/captura/resumen")
def resumen(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = ex.consulta_visible(user).subquery()
        por_estado = dict(s.execute(select(q.c.estado, func.count()).group_by(q.c.estado)).all())
        rq = select(Reservacion.estado, func.count()).group_by(Reservacion.estado)
        if not user.is_admin:
            rq = rq.where(Reservacion.operator_id == (user.operator_id or -1))
        res = dict(s.execute(rq).all())
    grupos = {g: 0 for g in dict.fromkeys(ex.GRUPO.values())}
    for k, n in por_estado.items():
        grupos[ex.GRUPO.get(k, "preparacion")] += n
    out = {"expedientes": {"por_estado": por_estado, "por_grupo": grupos},
           "reservaciones": {"por_estado": res}}
    # v1.6: lo que necesita el panel en una sola llamada (operadores, cola, calendarios, errores)
    from ..captura import calendario_dias as cd
    from ..engine.params import get_params
    from ..models import Assignment, Operator, Request
    now = utcnow()
    with db.session() as s:
        ops = s.scalars(select(Operator).where(Operator.active.is_(True))).all()
        tipos = ["cita", "cita_dia"]
        out["operadores"] = {"activos": len(ops), "con_trabajo": sum(1 for o in ops if o.current_load > 0),
                             "capacidad_libre": sum(max(0, o.max_concurrent - o.current_load) for o in ops)}
        out["asignaciones"] = {
            "por_tomar": s.scalar(select(func.count()).select_from(Assignment).join(Request, Request.id == Assignment.request_id)
                                  .where(Assignment.status == "ACTIVA", Request.resource_type.in_(tipos))) or 0,
            "en_cola": s.scalar(select(func.count()).select_from(Request).where(
                Request.status.in_(["ELEGIBLE", "REINTENTO", "PENDIENTE"]), Request.resource_type.in_(tipos))) or 0}
        frescura = float(get_params(s)["calendario_frescura_s"])
        regs, sucs = _alcance_operador(s, user)
        v = cd.vista(s, now, frescura)
        cals = []
        for su in v["sucursales"]:
            if (regs and su["region"] not in regs) or (sucs and su["sucursal"] not in sucs):
                continue
            cuenta: dict[str, int] = {}
            for dias in su["tramites"].values():
                for d in dias:
                    cuenta[d["estado"]] = cuenta.get(d["estado"], 0) + 1
            cals.append({"sucursal": su["sucursal"], "nombre": su["nombre"], "region": su["region"],
                         "source_id": su["source_id"], "dias": cuenta})
        out["calendarios"] = {"zona_horaria": v["zona_horaria"], "hoy": v["hoy"], "fuentes": v["fuentes"],
                              "sucursales": cals}
        errores = []
        for f in v["fuentes"]:
            if f["sin_verificar"]:
                errores.append({"tipo": "CALENDARIO_SIN_VERIFICAR", "detalle": f"fuente {f['source_id']}: "
                                f"{f['ultimo_error_codigo'] or 'sin verificar'}"})
        rq = select(Reservacion.id, Reservacion.estado, Reservacion.error_codigo).where(
            Reservacion.viva_expediente_id.is_not(None), Reservacion.error_codigo.is_not(None))
        if not user.is_admin:
            rq = rq.where(Reservacion.operator_id == (user.operator_id or -1))
        for rid, est, cod in s.execute(rq.limit(50)):
            errores.append({"tipo": "RESERVACION", "detalle": f"reservación #{rid} {est}: {cod}"})
        out["errores"] = errores
    return out


# ======================================================================= v1.7: mi trabajo (consola del operador)
# Paso del recorrido de 12 del operador en el que está cada expediente, con la acción que toca.
PASOS = {
    "preparar": (6, "preparar el formulario (revisa datos y validaciones)", True),
    "manuales": (7, "completar los campos manuales y enviar los datos", True),
    "disponibilidad": (8, "consultar disponibilidad y elegir el día", True),
    "confirmar": (10, "confirmar la reservación", True),
    "verificacion": (11, "esperar la verificación del folio (lo hace el motor)", False),
    "confirmado": (11, "reservación confirmada: consulta el folio", False),
    "revision": (5, "requiere revisión del administrador", False),
}


def _paso(exp: Expediente, viva: Reservacion | None) -> str:
    if exp.estado == ex.REVISION:
        return "revision"
    if exp.estado == ex.RES_CONFIRMADA:
        return "confirmado"
    if viva is None or viva.estado == rs.PREPARADA:
        return "preparar" if viva is None else "manuales"
    if viva.estado == rs.DATOS_ENVIADOS:
        return "disponibilidad"
    if viva.estado in rs.LISTAS_PARA_CONFIRMAR:
        return "confirmar"
    return "verificacion"


@router.get("/captura/mi-trabajo")
def mi_trabajo(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Lo que el operador tiene que hacer, ordenado por urgencia y con el siguiente paso. Solo sus
    expedientes y SIN datos personales (código, trámite, sucursal, prioridad): la ficha completa se ve
    al abrir el expediente (auditado). Para un administrador: los que tiene asignados a su operador."""
    from ..models import Assignment
    from ..captura import zona
    now = utcnow()
    oid = user.operator_id if user.operator_id is not None else -1
    hoy_utc = now - (zona.a_local(now) - zona.a_local(now).replace(hour=0, minute=0, second=0, microsecond=0))
    with db.session() as s:
        exps = list(s.scalars(select(Expediente).where(
            Expediente.operator_id == oid, Expediente.estado.in_(list(ex.OPERACION_ACTIVA) + [ex.REVISION]))))
        vivas = {r.viva_expediente_id: r for r in s.scalars(select(Reservacion).where(
            Reservacion.viva_expediente_id.in_([e.id for e in exps] or [-1])))}
        asgs = {a.request_id: a for a in s.scalars(select(Assignment).where(
            Assignment.request_id.in_([e.request_id for e in exps if e.request_id] or [-1]),
            Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))}
        items = []
        for e in exps:
            v = vivas.get(e.id)
            a = asgs.get(e.request_id)
            clave = _paso(e, v)
            n, texto, accion = PASOS[clave]
            avisos = []
            vence = None
            if v is not None and v.vence_en and v.estado in rs.LISTAS_PARA_CONFIRMAR:
                vence = v.vence_en
                avisos.append(f"El horario apartado vence en {max(0, int((v.vence_en - now).total_seconds() // 60))} min")
            elif a is not None and a.status == "ACTIVA":
                vence = a.expires_at
                avisos.append(f"La asignación vence en {max(0, int((a.expires_at - now).total_seconds() // 60))} min "
                              "si no empiezas")
            if v is not None and v.error_codigo:
                avisos.append(f"Último aviso del portal: {v.error_codigo}")
            urgente = e.prioridad <= 1 or (vence is not None and (vence - now).total_seconds() < 600)
            items.append({
                "id": e.id, "codigo": e.codigo, "estado": e.estado, "prioridad": e.prioridad,
                "tipo_tramite": e.tipo_tramite, "sucursal": e.sucursal, "modo": e.modo,
                "faltan": ex.faltantes(e) if e.estado in ex.PREPARACION else [],
                "paso": {"clave": clave, "n": n, "texto": texto, "accion": accion},
                "interrumpido": v is not None and v.estado in (rs.PREPARADA, rs.DATOS_ENVIADOS, *rs.LISTAS_PARA_CONFIRMAR),
                "urgente": urgente, "avisos": avisos,
                "reservacion": {"id": v.id, "estado": v.estado} if v is not None else None,
                "asignacion": {"id": a.id, "estado": a.status} if a is not None else None,
                "_orden": (0 if accion else 1, not urgente, e.prioridad, -(1 if v is not None else 0), e.updated_at or now),
            })
        items.sort(key=lambda x: x.pop("_orden"))
        confirmados_hoy = s.scalar(select(func.count()).select_from(Reservacion).where(
            Reservacion.operator_id == oid, Reservacion.estado == rs.CONFIRMADA, Reservacion.updated_at >= hoy_utc)) or 0
    return {"items": items, "resumen": {
        "por_empezar": sum(1 for x in items if x["paso"]["clave"] == "preparar"),
        "a_medias": sum(1 for x in items if x["interrumpido"]),
        "en_verificacion": sum(1 for x in items if x["paso"]["clave"] == "verificacion"),
        "confirmados_hoy": confirmados_hoy}}


# ======================================================================= expedientes
@router.get("/expedientes")
def listar(estado: str | None = Query(None, max_length=24), grupo: str | None = Query(None, max_length=16),
           limit: int = Query(200, ge=1, le=2000), user: CurrentUser = Depends(current_user),
           db: Database = Depends(get_db)):
    with db.session() as s:
        q = ex.consulta_visible(user)
        if estado:
            q = q.where(Expediente.estado == estado)
        if grupo:
            q = q.where(Expediente.estado.in_([k for k, v in ex.GRUPO.items() if v == grupo]))
        q = q.order_by(Expediente.prioridad.asc(), Expediente.created_at.desc()).limit(limit)
        return [ex.vista(s, e) for e in s.scalars(q)]


@router.post("/expedientes", status_code=201)
def crear(body: ExpedienteIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    _puede_capturar(user)
    now = utcnow()
    try:
        out = ex.crear_seguro(db, body.model_dump(exclude_none=True), user.username, now)
    except ex.ErrorExpediente as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "expediente_creado", out["codigo"],
                       {"campos": sorted(k for k, v in body.model_dump(exclude_none=True).items()
                                         if not k.endswith("_confirmacion"))})
    # Quien lo crea no necesariamente lo atenderá: se devuelve enmascarado.
    with db.session() as s:
        return ex.vista(s, s.get(Expediente, out["id"]))


@router.get("/expedientes/{eid}")
def ficha(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        out = ex.vista(s, e, completa=True)
        out["reservaciones"] = [rs.vista(r) for r in s.scalars(
            select(Reservacion).where(Reservacion.expediente_id == eid).order_by(Reservacion.id.desc()))]
        out["historial"] = ex.historial(s, eid)
        security.audit(s, user.username, "expediente_consultado", e.codigo, {"datos_completos": True})
        return out


@router.patch("/expedientes/{eid}")
def modificar(eid: int, body: ExpedientePatch, user: CurrentUser = Depends(current_user),
              db: Database = Depends(get_db)):
    now = utcnow()
    try:
        with db.session() as s:
            e = s.get(Expediente, eid)
            if e is None or not ex.puede_ver(user, e):
                raise HTTPException(404, "Expediente no encontrado")
            d = body.model_dump(exclude_none=True)
            version = d.pop("version")
            campos = ex.modificar(s, e, d, version, user.username, now)
            security.audit(s, user.username, "expediente_modificado", e.codigo, {"campos": campos})
            return {**ex.vista(s, e, completa=True), "campos_modificados": campos}
    except ex.ErrorExpediente as err:
        _err(err)


@router.post("/expedientes/{eid}/cancelar")
def cancelar_expediente(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    now = utcnow()
    with db.session() as s:
        e = s.get(Expediente, eid)
        if e is None or not ex.puede_ver(user, e):
            raise HTTPException(404, "Expediente no encontrado")
        if e.estado in ex.CERRADOS:
            raise HTTPException(409, {"mensaje": f"El expediente ya está {e.estado}"
                                      + (": cancela la reservación con su folio" if e.estado == ex.RES_CONFIRMADA else "")})
        viva = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == eid))
        if viva is not None and viva.estado in (rs.CONFIRMANDO, rs.EN_VERIFICACION):
            raise HTTPException(409, {"mensaje": "Hay una confirmación en verificación: espera su resultado"})
        if viva is not None:
            viva.estado, viva.viva_expediente_id, viva.updated_at = rs.CANCELADA, None, now
            viva.error_codigo, viva.error = "EXPEDIENTE_CANCELADO", "expediente cancelado"
        try:
            ex.cerrar(s, e, "CANCELADO", user.username, now, nota="cancelado por el usuario")
        except ex.ErrorExpediente as err:
            _err(err)
        security.audit(s, user.username, "expediente_cancelado", e.codigo)
        rid = e.request_id
    if rid:
        from ..engine import assignment as asg
        asg.cancel_request(db, rid, now, user.username)
    return {"ok": True}


@router.post("/expedientes/{eid}/rechazar")
def rechazar_asignacion(eid: int, body: RechazoIn, user: CurrentUser = Depends(current_user),
                        db: Database = Depends(get_db)):
    """v1.6: el operador rechaza la asignación con un motivo (las reglas del motor deciden qué sigue)."""
    from ..captura import seguimiento
    try:
        out = seguimiento.rechazar(db, eid, user, body.motivo, body.nota, utcnow())
    except ex.ErrorExpediente as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "asignacion_rechazada", s.get(Expediente, eid).codigo,
                       {"motivo": body.motivo})
    return out


@router.post("/expedientes/{eid}/revalidar")
def revalidar(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """v1.6: vuelve a validar un expediente INCOMPLETO o en REQUIERE_REVISION (tras corregirlo)."""
    now = utcnow()
    try:
        with db.session() as s:
            e = s.get(Expediente, eid)
            if e is None or not (user.is_admin or ex.puede_ver(user, e)):
                raise HTTPException(404, "Expediente no encontrado")
            estado = ex.revalidar(s, e, user.username, now)
            security.audit(s, user.username, "expediente_revalidado", e.codigo, {"estado": estado})
            out = ex.vista(s, e)
    except ex.ErrorExpediente as err:
        _err(err)
    if estado == ex.LISTO:   # la solicitud anterior (si la había) se cancela fuera de la transacción
        from ..captura import seguimiento
        seguimiento.sincronizar(db, now)
    return out


@router.post("/expedientes/{eid}/preparar")
def preparar(eid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    _puede_capturar(user)
    try:
        return rs.preparar(db, eid, user, utcnow())
    except rs.ErrorReservacion as e:
        _err(e)


# ======================================================================= reservaciones
@router.get("/reservaciones")
def listar_reservaciones(estado: str | None = Query(None, max_length=16), limit: int = Query(200, ge=1, le=2000),
                         user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(Reservacion, Expediente.codigo).join(Expediente, Expediente.id == Reservacion.expediente_id)
        if not user.is_admin:
            q = q.where(Reservacion.operator_id == (user.operator_id or -1),
                        Expediente.operator_id == (user.operator_id or -1))
        if estado:
            q = q.where(Reservacion.estado == estado)
        q = q.order_by(Reservacion.updated_at.desc()).limit(limit)
        return [{**rs.vista(r), "expediente": codigo} for r, codigo in s.execute(q)]


@router.post("/reservaciones/{rid}/enviar-datos")
def enviar_datos(rid: int, body: EnviarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return rs.enviar_datos(db, rid, user, utcnow(), body.revisado, body.manuales, body.sucursal, body.mes)
    except rs.ErrorReservacion as e:
        _err(e)


@router.get("/reservaciones/{rid}/calendario")
def calendario_reservacion(rid: int, region: str | None = Query(None, max_length=32),
                           sucursal: str | None = Query(None, max_length=32),
                           mes: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
                           user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        return rs.calendario(db, rid, user, utcnow(), region, sucursal, mes)
    except rs.ErrorReservacion as e:
        _err(e)


@router.post("/reservaciones/{rid}/seleccionar")
def seleccionar(rid: int, body: SeleccionIn, user: CurrentUser = Depends(current_user),
                db: Database = Depends(get_db)):
    try:
        return rs.seleccionar(db, rid, user, utcnow(), body.horario, body.region, body.sucursal)
    except rs.ErrorReservacion as e:
        _err(e)


@router.post("/reservaciones/{rid}/seleccionar-dia")
def seleccionar_dia(rid: int, body: SeleccionDiaIn, user: CurrentUser = Depends(current_user),
                    db: Database = Depends(get_db)):
    try:
        return rs.seleccionar_dia(db, rid, user, utcnow(), body.fecha, body.sucursal)
    except rs.ErrorReservacion as e:
        _err(e)


@router.post("/reservaciones/{rid}/confirmar")
def confirmar(rid: int, body: ConfirmarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        code, out = rs.confirmar(db, rid, user, utcnow(), body.confirmo)
    except rs.ErrorReservacion as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "reservacion_confirmar", str(rid),
                       {"resultado": out["reservacion"]["estado"], "folio": out["reservacion"].get("folio")})
    return JSONResponse(out, status_code=code)


@router.post("/reservaciones/{rid}/cancelar")
def cancelar_reservacion(rid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    try:
        out = rs.cancelar(db, rid, user, utcnow())
    except rs.ErrorReservacion as e:
        _err(e)
    with db.session() as s:
        security.audit(s, user.username, "reservacion_cancelada", str(rid), {"estado": out["estado"]})
    return out


# ======================================================================= calendario, historial, contrato
@router.get("/captura/calendario")
def calendario_laboratorio(region: str | None = Query(None, max_length=32),
                           sucursal: str | None = Query(None, max_length=32),
                           tramite: Literal["alta", "renovacion", "constancia"] | None = None,
                           user: CurrentUser = Depends(current_user)):
    try:
        return cal.leer(region, sucursal, tramite)
    except cal.CalendarioNoDisponible as e:
        raise HTTPException(503, {"mensaje": str(e)})


@router.get("/captura/historial")
def historial(limit: int = Query(200, ge=1, le=2000), tipo: str | None = Query(None, max_length=32),
              user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = (select(ExpedienteEvento, Expediente.codigo)
             .join(Expediente, Expediente.id == ExpedienteEvento.expediente_id))
        if not user.is_admin:
            q = q.where(Expediente.operator_id == (user.operator_id or -1))
        if tipo:
            q = q.where(ExpedienteEvento.tipo == tipo)
        q = q.order_by(ExpedienteEvento.id.desc()).limit(limit)
        from .serializers import iso
        return [{"id": e.id, "ts": iso(e.ts), "expediente": codigo, "expediente_id": e.expediente_id, "tipo": e.tipo,
                 "actor": e.actor, "asignacion": e.assignment_id, "reservacion": e.reservacion_id,
                 "evento_cambio": e.change_event_id, "detalle": e.detalle,
                 "normalizado": e.tipo in ex.EVENTOS_NORMALIZADOS} for e, codigo in s.execute(q)]


@router.get("/captura/contrato")
def contrato(modo: Literal["POR_DIA", "POR_HORARIO"] | None = None, user: CurrentUser = Depends(current_user),
             db: Database = Depends(get_db)):
    if modo is None:
        from ..engine.params import get_params
        with db.session() as s:
            modo = get_params(s).get("captura_modo", "POR_DIA")
    c = contrato_actual(modo)
    return {"nombre": c.nombre, "version": c.version, "modo": c.modo,
            "campos": [{"campo": x.nombre, "etiqueta": x.etiqueta, "tipo": x.tipo, "origen": x.origen,
                        "obligatorio": x.obligatorio, "modo": x.modo, "requerido_si": x.requerido_si,
                        "instrucciones": x.instrucciones, "sin_pegar": x.sin_pegar, "guardar": x.guardar,
                        "validacion": x.validacion or {}} for x in c.campos]}


# ======================================================================= v1.6: calendario POR DÍA del motor
def _alcance_operador(s, user) -> tuple[list[str], list[str]]:
    from ..models import Operator
    if user.is_admin or user.operator_id is None:
        return [], []
    op = s.get(Operator, user.operator_id)
    return list(op.regions or []), list(op.sucursales or [])


@router.get("/captura/calendario-dias")
def calendario_dias(region: str | None = Query(None, max_length=32), sucursal: str | None = Query(None, max_length=32),
                    tramite: str | None = Query(None, max_length=32), desde: str | None = Query(None, max_length=10),
                    hasta: str | None = Query(None, max_length=10),
                    user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Lo que el MOTOR sabe de cada día (no consulta al portal): estado mostrado, si es información
    ACTUAL o ALMACENADA y la hora real de la última verificación. Un operador solo ve sus regiones
    y sucursales."""
    from ..captura import calendario_dias as cd
    from ..engine.params import get_params
    now = utcnow()
    with db.session() as s:
        frescura = float(get_params(s)["calendario_frescura_s"])
        regs, sucs = _alcance_operador(s, user)
        v = cd.vista(s, now, frescura, region, sucursal, tramite, desde, hasta)
    if regs or sucs:
        v["sucursales"] = [x for x in v["sucursales"] if (not regs or x["region"] in regs)
                           and (not sucs or x["sucursal"] in sucs)]
    return v


@router.get("/captura/calendario-dias/cambios")
def calendario_cambios(desde: int = Query(0, ge=0), limite: int = Query(200, ge=1, le=1000), ultimos: bool = False,
                       user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Cambios detectados (para actualizar el panel sin recargar: se pide 'desde' el último id visto;
    `ultimos=true` devuelve los más recientes)."""
    from ..captura import calendario_dias as cd
    with db.session() as s:
        regs, sucs = _alcance_operador(s, user)
        out = cd.cambios_desde(s, desde, limite, ultimos)
    if sucs:
        out = [c for c in out if c["sucursal"] is None or c["sucursal"] in sucs]
    return {"cambios": out, "ultimo": out[-1]["id"] if out else desde}


@router.get("/captura/coherencia")
def coherencia(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "Solo administradores")
    with db.session() as s:
        return {"problemas": ex.coherencia(s)}


# ======================================================================= v1.6: retención, exportación
@router.get("/captura/retencion")
def retencion_vista(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Vista previa de la política (qué se anonimizaría o borraría ahora). Solo administradores."""
    if not user.is_admin:
        raise HTTPException(403, "Solo administradores")
    from ..captura import retencion
    return retencion.aplicar(db, utcnow(), actor=user.username, simular=True)


class RetencionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmo: bool = False


@router.post("/captura/retencion/aplicar")
def retencion_aplicar(body: RetencionIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(403, "Solo administradores")
    if not body.confirmo:
        raise HTTPException(422, {"mensaje": "Revisa la vista previa y marca 'confirmo' para aplicar la política"})
    from ..captura import retencion
    out = retencion.aplicar(db, utcnow(), actor=user.username)
    with db.session() as s:
        security.audit(s, user.username, "retencion_manual", "expedientes",
                       {k: out[k] for k in ("anonimizados", "borrados", "cerrados", "omitidos")})
    return out


@router.get("/expedientes-exportar")
def exportar(completo: bool = False, motivo: str = Query("", max_length=120),
             estado: str | None = Query(None, max_length=24),
             user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Exportación CSV. Solo administradores. Por defecto ENMASCARADA; la completa exige un motivo
    escrito y queda auditada (quién, cuándo, cuántos, por qué; nunca los valores)."""
    import csv
    import io
    from fastapi.responses import Response
    if not user.is_admin:
        raise HTTPException(403, "Solo administradores")
    if completo and len(motivo.strip()) < 10:
        raise HTTPException(422, {"mensaje": "La exportación completa exige un motivo (mínimo 10 caracteres)"})
    cols = ["codigo", "estado", "motivo_cierre", "modo", "region", "sucursal", "tipo_tramite", "operacion", "prioridad",
            "nombre", "fecha_nacimiento", "telefono", "correo", "id_registro_previo", "creado", "actualizado",
            "anonimizado"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    with db.session() as s:
        q = select(Expediente).order_by(Expediente.id)
        if estado:
            q = q.where(Expediente.estado == estado)
        n = 0
        for e in s.scalars(q):
            w.writerow(ex.vista(s, e, completa=completo))
            n += 1
        security.audit(s, user.username, "expedientes_exportados", "csv",
                       {"filas": n, "completo": completo, "motivo": motivo.strip()[:120], "estado": estado})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="expedientes.csv"',
                             "Cache-Control": "no-store"})

"""API REST del motor. Todas las rutas (salvo /health y login) exigen sesión."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest
from sqlalchemy import func, or_, select

from .. import security
from ..config import settings
from ..connectors.base import ConnectorError
from ..connectors.registry import REGISTRY, build_connector
from ..db import Database, utcnow
from ..engine import alerts as al
from ..engine import assignment as asg
from ..engine.clock import RealClock
from ..engine.integrity import check as integrity_check
from ..engine.params import get_params, set_params
from ..engine.pipeline import _assignment_alert, poll_source
from ..models import (Alert, Assignment, AuditLog, ChangeEvent, Channel, Delivery, NotificationLog, Operator,
                      PollLog, Region, Request, RequestEvent, SimItem, SimRun, SlotState, Source, User)
from ..simulator.world import World
from . import schemas as sc
from . import serializers as ser
from .deps import CurrentUser, client_ip, current_user, get_db, require_admin

router = APIRouter(prefix="/api")
# Laboratorio de simulación: NO se monta en producción (ver monitoreo/entorno.py).
lab_router = APIRouter(prefix="/api")
throttle = security.LoginThrottle()
clock = RealClock()


def _norm_dt(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.isoformat()


def _404(what: str):
    raise HTTPException(404, f"{what} no encontrado")


# ======================================================================= auth
@router.get("/health")
def health(request: HttpRequest):
    """Público y mínimo (sin datos internos): apto para un monitor externo."""
    sch = getattr(request.app.state, "scheduler", None)
    db: Database = request.app.state.db
    db_ok = True
    try:
        with db.session() as s:
            s.execute(select(1))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"ok": db_ok, "base_de_datos": "OK" if db_ok else "SIN_CONEXION",
            "motor_activo": bool(sch and sch.running), "rol": sch.role if sch else "SIN_MOTOR",
            "ciclos": sch.cycles if sch else 0}


@router.post("/auth/login")
def login(body: sc.LoginIn, request: HttpRequest, db: Database = Depends(get_db)):
    key = f"{body.username}|{client_ip(request)}"
    if throttle.blocked(key):
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
    with db.session() as s:
        user = s.scalar(select(User).where(User.username == body.username))
        if user is None or not user.active or not security.verify_password(body.password, user.password_hash):
            throttle.fail(key)
            security.audit(s, body.username, "login_fallido", ip=client_ip(request))
            raise HTTPException(401, "Usuario o contraseña incorrectos")  # mensaje genérico: no revela qué falló
        throttle.reset(key)
        token = security.issue_token(s, user, settings.token_ttl_hours)
        security.audit(s, user.username, "login", ip=client_ip(request))
        return {"token": token, "username": user.username, "role": user.role,
                "expires_in_s": settings.token_ttl_hours * 3600}


@router.post("/auth/logout")
def logout(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        security.revoke_token(s, user.token)
        security.audit(s, user.username, "logout")
    return {"ok": True}


@router.get("/info")
def info(request: HttpRequest, user: CurrentUser = Depends(current_user)):
    """Entorno y funciones disponibles (el panel oculta lo que no existe en producción)."""
    from ..app import VERSION
    ent = request.app.state.entorno
    return {"version": VERSION, "entorno": ent.entorno, "laboratorio": ent.entorno != "produccion",
            "avisos_configuracion": ent.avisos if user.role == "admin" else []}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"username": user.username, "role": user.role, "operator_id": user.operator_id}


@router.post("/auth/password")
def change_password(body: sc.PasswordIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    if problems := security.password_problems(body.new_password):
        raise HTTPException(422, "Contraseña débil: " + ", ".join(problems))
    with db.session() as s:
        u = s.get(User, user.id)
        if not security.verify_password(body.current_password, u.password_hash):
            raise HTTPException(401, "Contraseña actual incorrecta")
        u.password_hash = security.hash_password(body.new_password)
        security.audit(s, user.username, "cambio_contrasena")
    return {"ok": True}


# ======================================================================= panel principal
@router.get("/dashboard")
def dashboard(request: HttpRequest, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    since = utcnow() - timedelta(hours=1)
    with db.session() as s:
        regions = []
        for r in s.scalars(select(Region).order_by(Region.code)):
            srcs = list(s.scalars(select(Source).where(Source.region == r.code)))
            regions.append({
                "code": r.code, "name": r.name,
                "fuentes": len(srcs), "fuentes_activas": sum(1 for x in srcs if x.status == "ACTIVA"),
                "fuentes_con_error": sum(1 for x in srcs if x.status == "ERROR" or x.circuit_state != "CERRADO"),
                "unidades_libres": s.scalar(select(func.coalesce(func.sum(SlotState.available - SlotState.held), 0))
                                            .where(SlotState.region == r.code, SlotState.status == "ACTIVO")),
                "solicitudes_en_cola": s.scalar(select(func.count()).select_from(Request).where(
                    Request.region == r.code, Request.status.in_(asg.MATCHABLE + (asg.PENDIENTE,)))),
                "asignadas": s.scalar(select(func.count()).select_from(Request).where(
                    Request.region == r.code, Request.status.in_([asg.ASIGNADO, asg.EN_PROCESO]))),
            })
        sch = getattr(request.app.state, "scheduler", None)
        return {
            "motor": {"activo": bool(sch and sch.running), "ciclos": sch.cycles if sch else 0,
                      "ultimo_error": sch.last_error if sch else None},
            "regiones": regions,
            "fuentes": {st: n for st, n in s.execute(select(Source.status, func.count()).group_by(Source.status))},
            "solicitudes": {st: n for st, n in s.execute(select(Request.status, func.count()).group_by(Request.status))},
            "operadores_activos": s.scalar(select(func.count()).select_from(Operator).where(Operator.active.is_(True))),
            "operadores": [ser.operator(o) for o in s.scalars(select(Operator).order_by(Operator.id))],
            "ultimas_disponibilidades": [ser.event(e) for e in s.scalars(
                select(ChangeEvent).where(ChangeEvent.change_type.in_(["NUEVA_DISPONIBILIDAD", "REAPARICION", "AUMENTO"]))
                .order_by(ChangeEvent.id.desc()).limit(10))],
            "alertas_recientes": [ser.alert(a) for a in s.scalars(select(Alert).order_by(Alert.id.desc()).limit(10))],
            "alertas_sin_confirmar": s.scalar(select(func.count()).select_from(Alert)
                                              .where(Alert.acknowledged_at.is_(None))),
            "errores_recientes": [ser.poll(p) for p in s.scalars(
                select(PollLog).where(PollLog.outcome != "OK").order_by(PollLog.id.desc()).limit(10))],
            "ultima_hora": {
                "consultas": s.scalar(select(func.count()).select_from(PollLog).where(PollLog.started_at >= since)),
                "errores": s.scalar(select(func.count()).select_from(PollLog).where(
                    PollLog.started_at >= since, PollLog.outcome != "OK")),
                "eventos": s.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.detected_at >= since)),
                "alertas": s.scalar(select(func.count()).select_from(Alert).where(Alert.created_at >= since)),
            },
        }


@router.get("/regions")
def regions(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        return [{"code": r.code, "name": r.name} for r in s.scalars(select(Region).order_by(Region.code))]


# ======================================================================= fuentes
@router.get("/sources")
def list_sources(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        return [ser.source(x) for x in s.scalars(select(Source).order_by(Source.region, Source.code))]


@router.get("/connectors")
def connector_types(user: CurrentUser = Depends(current_user)):
    return sorted(REGISTRY)


@router.post("/sources", status_code=201)
def create_source(body: sc.SourceIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        build_connector(body.connector_type, body.config, {"db": db})  # valida la configuración
    except ConnectorError as e:
        raise HTTPException(422, f"Configuración inválida: {e}")
    with db.session() as s:
        if s.get(Region, body.region) is None:
            raise HTTPException(422, "Región inexistente")
        if s.scalar(select(Source.id).where(Source.code == body.code)):
            raise HTTPException(409, "Ya existe una fuente con ese código")
        src = Source(**body.model_dump())
        s.add(src)
        s.flush()
        security.audit(s, user.username, "crear_fuente", src.code, body.model_dump())
        return ser.source(src)


@router.patch("/sources/{sid}")
def patch_source(sid: int, body: sc.SourcePatch, user: CurrentUser = Depends(require_admin),
                 db: Database = Depends(get_db)):
    with db.session() as s:
        src = s.get(Source, sid) or _404("Fuente")
        changes = body.model_dump(exclude_none=True)
        if "config" in changes:
            # Los secretos enmascarados ("********") no sobrescriben el valor guardado.
            merged = {**(src.config or {})}
            for k, v in changes["config"].items():
                if v != "********":
                    merged[k] = v
            try:
                build_connector(src.connector_type, merged, {"db": db})
            except ConnectorError as e:
                raise HTTPException(422, f"Configuración inválida: {e}")
            changes["config"] = merged
        for k, v in changes.items():
            setattr(src, k, v)
        security.audit(s, user.username, "editar_fuente", src.code, changes)
        return ser.source(src)


@router.post("/sources/{sid}/{action}")
def source_action(sid: int, action: str, request: HttpRequest, user: CurrentUser = Depends(require_admin),
                  db: Database = Depends(get_db)):
    if action not in ("suspender", "reanudar", "consultar"):
        raise HTTPException(404, "Acción desconocida")
    with db.session() as s:
        src = s.get(Source, sid) or _404("Fuente")
        if action == "suspender":
            src.status = "SUSPENDIDA"
            src.status_reason, src.status_changed_at = f"Suspendida manualmente por {user.username}", utcnow()
        elif action == "reanudar":
            src.status_reason, src.status_changed_at = f"Reanudada por {user.username}", utcnow()
            src.status, src.circuit_state, src.circuit_opened_at = "ACTIVA", "CERRADO", None
            src.consecutive_failures, src.circuit_open_count, src.next_poll_at = 0, 0, None
        security.audit(s, user.username, f"fuente_{action}", src.code)
    if action == "consultar":
        sch = getattr(request.app.state, "scheduler", None)
        runtime = sch.runtime if sch else _fallback_runtime(request)
        out = poll_source(db, sid, clock, runtime)
        return {"resultado": out.outcome, "eventos": out.events, "ms": round(out.duration_ms, 2), "error": out.error}
    with db.session() as s:
        return ser.source(s.get(Source, sid))


def _fallback_runtime(request: HttpRequest):
    from ..engine.pipeline import RuntimeState
    if not hasattr(request.app.state, "runtime"):
        request.app.state.runtime = RuntimeState()
    return request.app.state.runtime


@router.get("/polls")
def list_polls(source_id: int | None = None, outcome: str | None = None, limit: int = Query(100, ge=1, le=1000),
               user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(PollLog).order_by(PollLog.id.desc()).limit(limit)
        if source_id:
            q = q.where(PollLog.source_id == source_id)
        if outcome:
            q = q.where(PollLog.outcome == outcome)
        return [ser.poll(p) for p in s.scalars(q)]


@router.get("/events")
def list_events(region: str | None = None, change_type: str | None = None, relevant: bool | None = None,
                limit: int = Query(100, ge=1, le=1000), user: CurrentUser = Depends(current_user),
                db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(ChangeEvent).order_by(ChangeEvent.id.desc()).limit(limit)
        if region:
            q = q.where(ChangeEvent.region == region)
        if change_type:
            q = q.where(ChangeEvent.change_type == change_type)
        if relevant is not None:
            q = q.where(ChangeEvent.relevant.is_(relevant))
        return [ser.event(e) for e in s.scalars(q)]


@router.get("/slots")
def list_slots(region: str | None = None, only_free: bool = False, user: CurrentUser = Depends(current_user),
               db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(SlotState).order_by(SlotState.region, SlotState.name)
        if region:
            q = q.where(SlotState.region == region)
        if only_free:
            q = q.where(SlotState.status == "ACTIVO", SlotState.available > SlotState.held)
        return [ser.slot(x) for x in s.scalars(q.limit(2000))]


# ======================================================================= solicitudes
@router.get("/requests")
def list_requests(region: str | None = None, status: str | None = None, q: str | None = Query(None, max_length=64),
                  limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0),
                  user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        query = select(Request)
        if region:
            query = query.where(Request.region == region)
        if status:
            query = query.where(Request.status == status)
        if q:
            like = f"%{q}%"
            query = query.where(or_(Request.code.ilike(like), Request.holder.ilike(like)))
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        query = query.order_by(Request.priority, Request.created_at).limit(limit).offset(offset)
        ops = {o.id: o.name for o in s.scalars(select(Operator))}
        return {"total": total, "items": [ser.request(r, ops.get(r.operator_id)) for r in s.scalars(query)]}


@router.post("/requests", status_code=201)
def create_request(body: sc.RequestIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    now = utcnow()
    with db.session() as s:
        if s.get(Region, body.region) is None:
            raise HTTPException(422, "Región inexistente")
        code = body.code or f"SOL-{uuid.uuid4().hex[:8].upper()}"
        if s.scalar(select(Request.id).where(Request.code == code)):
            raise HTTPException(409, "Código de solicitud duplicado")
        cond = {k: (_norm_dt(v) if isinstance(v, datetime) else v)
                for k, v in body.conditions.model_dump(exclude_none=True).items()}
        r = Request(code=code, holder=body.holder, region=body.region, resource_type=body.resource_type,
                    priority=body.priority, quantity=body.quantity, conditions=cond, status=asg.PENDIENTE,
                    created_at=now, updated_at=now)
        s.add(r)
        s.flush()
        s.add(RequestEvent(request_id=r.id, ts=now, from_status=None, to_status=asg.PENDIENTE,
                           actor=user.username, note="registrada"))
        asg.validate_request(s, r, now, actor=user.username)
        security.audit(s, user.username, "crear_solicitud", code, body.model_dump(mode="json"))
        return ser.request(r)


@router.get("/requests/{rid}")
def get_request(rid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        r = s.get(Request, rid) or _404("Solicitud")
        op = s.get(Operator, r.operator_id) if r.operator_id else None
        hist = [{"ts": ser.iso(e.ts), "from": e.from_status, "to": e.to_status, "actor": e.actor, "note": e.note}
                for e in s.scalars(select(RequestEvent).where(RequestEvent.request_id == rid).order_by(RequestEvent.id))]
        asgs = [ser.assignment(a, s.get(SlotState, a.slot_id), r, s.get(Operator, a.operator_id))
                for a in s.scalars(select(Assignment).where(Assignment.request_id == rid).order_by(Assignment.id))]
        return {**ser.request(r, op.name if op else None), "historial": hist, "asignaciones": asgs}


@router.patch("/requests/{rid}")
def patch_request(rid: int, body: sc.RequestPatch, user: CurrentUser = Depends(require_admin),
                  db: Database = Depends(get_db)):
    with db.session() as s:
        r = s.get(Request, rid) or _404("Solicitud")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(r, k, v)
        security.audit(s, user.username, "editar_solicitud", r.code, body.model_dump(exclude_none=True))
        return ser.request(r)


@router.post("/requests/{rid}/cancelar")
def cancel_request(rid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    if not asg.cancel_request(db, rid, utcnow(), user.username):
        raise HTTPException(409, "La solicitud no existe o ya está cerrada")
    with db.session() as s:
        security.audit(s, user.username, "cancelar_solicitud", str(rid))
    return {"ok": True}


@router.post("/requests/{rid}/reactivar")
def reactivate_request(rid: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        r = s.get(Request, rid) or _404("Solicitud")
        try:
            asg.transition(s, r, asg.ELEGIBLE, user.username, "reactivada manualmente", utcnow())
        except asg.TransitionError as e:
            raise HTTPException(409, str(e))
        security.audit(s, user.username, "reactivar_solicitud", r.code)
        return ser.request(r)


# ======================================================================= asignaciones
@router.get("/assignments")
def list_assignments(status: str | None = None, mine: bool = False, limit: int = Query(200, ge=1, le=2000),
                     user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(Assignment).order_by(Assignment.id.desc()).limit(limit)
        if status:
            q = q.where(Assignment.status == status)
        if mine or not user.is_admin:  # un operador solo ve sus propias asignaciones
            q = q.where(Assignment.operator_id == (user.operator_id or -1))
        out = []
        for a in s.scalars(q):
            out.append(ser.assignment(a, s.get(SlotState, a.slot_id), s.get(Request, a.request_id),
                                      s.get(Operator, a.operator_id)))
        return out


def _op_scope(user: CurrentUser) -> int | None:
    """El administrador puede actuar sobre cualquier asignación; el operador solo sobre las suyas."""
    if user.is_admin:
        return None
    if user.operator_id is None:
        raise HTTPException(403, "Tu usuario no está vinculado a un operador")
    return user.operator_id


@router.post("/assignments/{aid}/{action}")
def assignment_action(aid: int, action: str, user: CurrentUser = Depends(current_user),
                      db: Database = Depends(get_db)):
    now = utcnow()
    scope = _op_scope(user)
    with db.session() as s:
        params = get_params(s)
    if action == "tomar":
        ok = asg.take_assignment(db, aid, scope, now, user.username)
    elif action == "completar":
        ok = asg.complete_assignment(db, aid, scope, now, user.username)
    elif action == "rechazar":
        ok = asg.reject_assignment(db, aid, scope, now, user.username, params)
    else:
        raise HTTPException(404, "Acción desconocida")
    if not ok:
        raise HTTPException(409, "No se pudo: la asignación no existe, no es tuya, expiró o cambió de estado")
    with db.session() as s:
        security.audit(s, user.username, f"asignacion_{action}", str(aid))
    return {"ok": True}


# ======================================================================= alertas
@router.get("/alerts")
def list_alerts(region: str | None = None, status: str | None = None, unacked: bool = False,
                limit: int = Query(100, ge=1, le=1000), after_id: int | None = None,
                user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(Alert).order_by(Alert.id.desc()).limit(limit)
        if region:
            q = q.where(Alert.region == region)
        if status:
            q = q.where(Alert.status == status)
        if unacked:
            q = q.where(Alert.acknowledged_at.is_(None))
        if after_id:
            q = q.where(Alert.id > after_id)
        return [ser.alert(a) for a in s.scalars(q)]


@router.get("/alerts/{aid}")
def get_alert(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        a = s.get(Alert, aid) or _404("Alerta")
        deliveries = []
        for d in s.scalars(select(Delivery).where(Delivery.alert_id == aid)):
            ch = s.get(Channel, d.channel_id)
            deliveries.append({"canal": ch.name, "tipo": ch.type, "estado": d.status, "intentos": d.attempts,
                               "ultimo_error": d.last_error, "entregada": ser.iso(d.delivered_at),
                               "latencia_ms": d.latency_ms})
        logs = [{"ts": ser.iso(n.ts), "canal": n.channel, "resultado": n.outcome, "detalle": n.detail}
                for n in s.scalars(select(NotificationLog).where(NotificationLog.alert_id == aid)
                                   .order_by(NotificationLog.id))]
        return {**ser.alert(a), "entregas": deliveries, "registro": logs}


@router.post("/alerts/{aid}/confirmar")
def ack_alert(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    ok = al.acknowledge(db, aid, user.username, utcnow())
    with db.session() as s:
        if s.get(Alert, aid) is None:
            _404("Alerta")
        if ok:
            security.audit(s, user.username, "confirmar_alerta", str(aid))
    return {"ok": True, "ya_confirmada": not ok}


# ======================================================================= operadores, canales, usuarios
@router.get("/operators")
def list_operators(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        return [ser.operator(o) for o in s.scalars(select(Operator).order_by(Operator.id))]


@router.post("/operators", status_code=201)
def create_operator(body: sc.OperatorIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        _check_regions(s, body.regions)
        op = Operator(**body.model_dump())
        s.add(op)
        s.flush()
        security.audit(s, user.username, "crear_operador", op.name, body.model_dump())
        return ser.operator(op)


def _check_regions(s, regions: list[str]):
    valid = {r for (r,) in s.execute(select(Region.code))}
    if bad := [r for r in regions if r not in valid]:
        raise HTTPException(422, f"Regiones inexistentes: {bad}")


@router.patch("/operators/{oid}")
def patch_operator(oid: int, body: sc.OperatorPatch, user: CurrentUser = Depends(require_admin),
                   db: Database = Depends(get_db)):
    with db.session() as s:
        op = s.get(Operator, oid) or _404("Operador")
        changes = body.model_dump(exclude_none=True)
        if "regions" in changes:
            _check_regions(s, changes["regions"])
        for k, v in changes.items():
            setattr(op, k, v)
        security.audit(s, user.username, "editar_operador", op.name, changes)
        return ser.operator(op)


@router.get("/channels")
def list_channels(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        return [ser.channel(c) for c in s.scalars(select(Channel).order_by(Channel.id))]


@router.post("/channels", status_code=201)
def create_channel(body: sc.ChannelIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        if s.scalar(select(Channel.id).where(Channel.name == body.name)):
            raise HTTPException(409, "Ya existe un canal con ese nombre")
        ch = Channel(**body.model_dump())
        s.add(ch)
        s.flush()
        security.audit(s, user.username, "crear_canal", ch.name, body.model_dump())
        return ser.channel(ch)


@router.patch("/channels/{cid}")
def patch_channel(cid: int, body: sc.ChannelPatch, user: CurrentUser = Depends(require_admin),
                  db: Database = Depends(get_db)):
    with db.session() as s:
        ch = s.get(Channel, cid) or _404("Canal")
        if body.config is not None:
            merged = dict(ch.config or {})
            for k, v in body.config.items():
                if v != "********":
                    merged[k] = v
            ch.config = merged
        if body.enabled is not None:
            ch.enabled = body.enabled
        security.audit(s, user.username, "editar_canal", ch.name, body.model_dump(exclude_none=True))
        return ser.channel(ch)


@router.get("/users")
def list_users(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        return [{"id": u.id, "username": u.username, "role": u.role, "active": u.active,
                 "created_at": ser.iso(u.created_at)} for u in s.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(body: sc.UserIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    if problems := security.password_problems(body.password):
        raise HTTPException(422, "Contraseña débil: " + ", ".join(problems))
    with db.session() as s:
        if s.scalar(select(User.id).where(User.username == body.username)):
            raise HTTPException(409, "El usuario ya existe")
        u = User(username=body.username, password_hash=security.hash_password(body.password), role=body.role)
        s.add(u)
        s.flush()
        if body.role == "operador":
            _check_regions(s, body.regions)
            s.add(Operator(name=body.operator_name or body.username, user_id=u.id, regions=body.regions))
        security.audit(s, user.username, "crear_usuario", u.username, {"role": body.role})
        return {"id": u.id, "username": u.username, "role": u.role}


@router.post("/users/{uid}/desactivar")
def deactivate_user(uid: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    if uid == user.id:
        raise HTTPException(409, "No puedes desactivarte a ti mismo")
    with db.session() as s:
        u = s.get(User, uid) or _404("Usuario")
        u.active = False
        security.audit(s, user.username, "desactivar_usuario", u.username)
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=2000), user: CurrentUser = Depends(require_admin),
              db: Database = Depends(get_db)):
    with db.session() as s:
        return [{"ts": ser.iso(a.ts), "usuario": a.username, "accion": a.action, "objetivo": a.target,
                 "detalle": a.detail, "ip": a.ip} for a in s.scalars(select(AuditLog).order_by(AuditLog.id.desc())
                                                                     .limit(limit))]


@router.get("/settings/engine")
def get_engine_settings(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        return get_params(s)


@router.put("/settings/engine")
def put_engine_settings(body: dict, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        try:
            result = set_params(s, body)
        except ValueError as e:
            raise HTTPException(422, str(e))
        security.audit(s, user.username, "editar_parametros", "motor", body)
        return result


# ======================================================================= métricas
_proc = psutil.Process()


@router.get("/metrics")
def metrics(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        lat = [x for (x,) in s.execute(select(PollLog.duration_ms).order_by(PollLog.id.desc()).limit(2000))]
        det = [x for (x,) in s.execute(select(ChangeEvent.detection_latency_ms)
                                       .where(ChangeEvent.detection_latency_ms.is_not(None))
                                       .order_by(ChangeEvent.id.desc()).limit(2000))]
        dl = [x for (x,) in s.execute(select(Delivery.latency_ms).where(Delivery.latency_ms.is_not(None))
                                      .order_by(Delivery.id.desc()).limit(2000))]
        from ..simulator.accelerated import _pct
        mem = _proc.memory_info()
        return {
            "proceso": {"cpu_pct": _proc.cpu_percent(interval=None), "memoria_mb": round(mem.rss / 1048576, 1),
                        "hilos": _proc.num_threads()},
            "consulta_ms": _pct(lat), "deteccion_ms": _pct(det), "entrega_alerta_ms": _pct(dl),
            "totales": {"consultas": s.scalar(select(func.count()).select_from(PollLog)),
                        "eventos": s.scalar(select(func.count()).select_from(ChangeEvent)),
                        "alertas": s.scalar(select(func.count()).select_from(Alert)),
                        "asignaciones": s.scalar(select(func.count()).select_from(Assignment))},
            "integridad": integrity_check(s),
        }


# ======================================================================= laboratorio
lab_jobs: dict[str, dict] = {}


@lab_router.get("/lab/items")
def lab_items(feed: str | None = None, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        q = select(SimItem).order_by(SimItem.region, SimItem.name)
        if feed:
            q = q.where(SimItem.feed == feed)
        return [ser.sim_item(i) for i in s.scalars(q)]


@lab_router.post("/lab/items", status_code=201)
def lab_create_item(body: sc.SimItemIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Region, body.region) is None:
            raise HTTPException(422, "Región inexistente")
        if s.scalar(select(SimItem.id).where(SimItem.feed == body.feed, SimItem.resource_key == body.resource_key)):
            raise HTTPException(409, "Ya existe ese recurso en el feed")
    item = World(db).create_item(body.feed, body.resource_key, body.name, body.region, body.resource_type,
                                 body.capacity, body.available, body.starts_at.replace(tzinfo=None) if body.starts_at else None)
    with db.session() as s:
        security.audit(s, user.username, "lab_crear_recurso", body.resource_key, body.model_dump(mode="json"))
    return ser.sim_item(item)


@lab_router.post("/lab/items/{iid}/{action}")
def lab_item_action(iid: int, action: str, body: dict | None = None, user: CurrentUser = Depends(require_admin),
                    db: Database = Depends(get_db)):
    w = World(db)
    body = body or {}
    try:
        if action == "liberar":
            ch = w.release(iid, sc.UnitsIn(**body).units)
        elif action == "consumir":
            ch = w.consume(iid, sc.UnitsIn(**body).units)
        elif action == "agotar":
            ch = w.exhaust(iid)
        elif action == "cancelar":
            ch = w.cancel(iid)
        elif action == "restaurar":
            ch = w.restore(iid)
        elif action == "reprogramar":
            ch = w.reschedule(iid, sc.RescheduleIn(**body).hours)
        else:
            raise HTTPException(404, "Acción desconocida")
    except KeyError:
        _404("Recurso simulado")
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.session() as s:
        security.audit(s, user.username, f"lab_{action}", str(iid), body)
    return {"cambio": ch.kind, "feed": ch.feed, "recurso": ch.resource_key, "detalle": ch.detail}


@lab_router.post("/lab/random")
def lab_random(body: sc.RandomStepIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    w = World(db)
    changes = []
    for _ in range(body.steps):
        changes += w.random_step(burst=body.burst)
    with db.session() as s:
        security.audit(s, user.username, "lab_aleatorio", "", body.model_dump())
    return {"cambios": len(changes), "detalle": [c.__dict__ for c in changes[:50]]}


@lab_router.post("/lab/competencia")
def lab_competition(body: sc.CompetitionIn, user: CurrentUser = Depends(require_admin),
                    db: Database = Depends(get_db)):
    """Crea N solicitudes compitiendo por el mismo recurso y libera `units` unidades."""
    now = utcnow()
    with db.session() as s:
        item = s.get(SimItem, body.item_id) or _404("Recurso simulado")
        tag = uuid.uuid4().hex[:5].upper()
        for k in range(body.requests):
            r = Request(code=f"COMP-{tag}-{k:03d}", holder=f"Competidor Ficticio {k + 1}", region=item.region,
                        resource_type=item.resource_type, priority=1 + (k % 5), quantity=1,
                        conditions={"nombre_contiene": item.name}, status=asg.PENDIENTE,
                        created_at=now - timedelta(seconds=body.requests - k), updated_at=now)
            s.add(r)
            s.flush()
            asg.validate_request(s, r, now, actor=user.username)
        iid = item.id
    World(db).release(iid, body.units)
    with db.session() as s:
        security.audit(s, user.username, "lab_competencia", str(iid), body.model_dump())
    return {"solicitudes_creadas": body.requests, "unidades_liberadas": body.units, "grupo": f"COMP-{tag}"}


@lab_router.post("/lab/acelerada", status_code=202)
def lab_accelerated(body: sc.AcceleratedIn, user: CurrentUser = Depends(require_admin),
                    db: Database = Depends(get_db)):
    if any(j["estado"] == "EN_CURSO" for j in lab_jobs.values()):
        raise HTTPException(409, "Ya hay una simulación acelerada en curso")
    with db.session() as s:
        run = SimRun(params=body.model_dump(), status="EN_CURSO")
        s.add(run)
        s.flush()
        run_id = run.id
        security.audit(s, user.username, "lab_acelerada", str(run_id), body.model_dump())
    lab_jobs[str(run_id)] = {"estado": "EN_CURSO"}

    def job():
        from ..simulator.accelerated import run_accelerated
        try:
            res = run_accelerated(hours=body.hours, step_s=body.step_s, seed=body.seed,
                                  requests_per_region=body.requests_per_region, error_rate=body.error_rate,
                                  notify_fail_rate=body.notify_fail_rate, intensity=body.intensity)
            status = "TERMINADA"
        except Exception as e:  # el resultado se guarda tal cual, incluido el error
            res, status = {"error": f"{e.__class__.__name__}: {e}"}, "FALLIDA"
        with db.session() as s:
            r = s.get(SimRun, run_id)
            r.results, r.status, r.finished_at = res, status, utcnow()
        lab_jobs[str(run_id)]["estado"] = status

    threading.Thread(target=job, daemon=True, name=f"sim-{run_id}").start()
    return {"run_id": run_id, "estado": "EN_CURSO"}


@lab_router.get("/lab/runs")
def lab_runs(user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    with db.session() as s:
        return [{"id": r.id, "started_at": ser.iso(r.started_at), "finished_at": ser.iso(r.finished_at),
                 "status": r.status, "params": r.params, "results": r.results}
                for r in s.scalars(select(SimRun).order_by(SimRun.id.desc()).limit(20))]

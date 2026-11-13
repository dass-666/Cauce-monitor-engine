"""v1.7: CENTRO DE OPERACIONES y SUPERVISIÓN de operadores.

Todas las cifras salen de la base de datos (consultas agregadas) o de marcas de tiempo reales
guardadas por el motor; ninguna se inventa ni se estima en el panel. Cada bloque dice de dónde sale
(`origen`). Funciona también en modo producción: la parte de expedientes y reservaciones solo
aparece cuando el laboratorio de captura está habilitado (fuera de producción).

Filtros (todos opcionales): región, sucursal, operador, estado del expediente y periodo
(1h, hoy, 24h, 7d). Un operador solo ve lo suyo: el filtro de operador se le fuerza a sí mismo.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import security
from ..db import Database, utcnow
from ..engine import assignment as asg
from ..engine.metricas import percentiles, service_metrics
from ..engine.params import get_params
from ..models import (Alert, ApiToken, Assignment, ChangeEvent, Operator, PollLog, Region, Request, SlotState, Source)
from . import serializers as ser
from .deps import CurrentUser, current_user, get_db, require_admin

router = APIRouter(prefix="/api")

PERIODOS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
OFERTA = ("NUEVA_DISPONIBILIDAD", "AUMENTO", "REAPARICION")
AGOTA = ("AGOTADO", "DESAPARECIDO", "REDUCCION")
NIVELES_IMPORTANTES = ("CRITICA", "ALTA")


def _desde(periodo: str, now: datetime) -> datetime:
    if periodo == "hoy":
        from ..captura import zona
        local = zona.a_local(now)
        inicio_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return now - (local - inicio_local)
    return now - PERIODOS.get(periodo, PERIODOS["24h"])


def captura_habilitada(request: HttpRequest) -> bool:
    review = getattr(request.app.state, "entorno", None)
    return not (review and review.entorno == "produccion")


# ======================================================================= centro de operaciones
@router.get("/centro")
def centro(request: HttpRequest, region: str | None = Query(None, max_length=32),
           sucursal: str | None = Query(None, max_length=32), operador: int | None = None,
           estado: str | None = Query(None, max_length=32), periodo: str = Query("24h", pattern="^(1h|hoy|24h|7d)$"),
           user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    now = utcnow()
    since = _desde(periodo, now)
    if not user.is_admin:
        operador = user.operator_id if user.operator_id is not None else -1   # un operador solo se ve a sí mismo
    sch = getattr(request.app.state, "scheduler", None)
    with db.session() as s:
        params = get_params(s)
        reg_f = [Source.region == region] if region else []

        # ---------------------------------------------------------------- estado general
        fuentes = list(s.execute(select(Source.connector_type, Source.status, Source.circuit_state, Source.region,
                                        Source.last_success_at, Source.consecutive_failures).where(*reg_f)))
        conectores: dict[str, int] = {}
        for ct, st, *_ in fuentes:
            if st == "ACTIVA":
                conectores[ct] = conectores.get(ct, 0) + 1
        errores_com = [f for f in fuentes if f[1] == "ERROR" or (f[1] == "ACTIVA" and f[5] > 0)]
        recuperacion = [f for f in fuentes if f[1] == "ACTIVA" and f[2] != "CERRADO"]
        ultima_ok = max((f[4] for f in fuentes if f[4]), default=None)
        regiones = list(s.scalars(select(Region.code).order_by(Region.code)))
        sucursales = sorted({(sl.attributes or {}).get("sucursal") for sl in s.scalars(
            select(SlotState).where(SlotState.status == "ACTIVO", *([SlotState.region == region] if region else [])))
            } - {None})

        presentes = _operadores_conectados(s, now, params)
        q_req = select(Request.status, func.count()).where(*([Request.region == region] if region else []))
        if operador is not None:
            q_req = q_req.where(Request.operator_id == operador)
        req = dict(s.execute(q_req.group_by(Request.status)).all())
        cola = sum(req.get(k, 0) for k in asg.MATCHABLE + (asg.PENDIENTE,))

        q_asg = select(Assignment.status, func.count())
        q_asg = _filtro_asg(q_asg, region, operador)
        asg_est = dict(s.execute(q_asg.group_by(Assignment.status)).all())
        abandonadas = s.scalar(_filtro_asg(select(func.count()).select_from(Assignment), region, operador).where(
            Assignment.status == "EN_PROCESO", Assignment.inactive_alerted_at.is_not(None))) or 0

        alertas_q = select(Alert).where(Alert.acknowledged_at.is_(None),
                                        or_(Alert.nivel.in_(NIVELES_IMPORTANTES), Alert.nivel.is_(None)),
                                        *([Alert.region == region] if region else []))
        if operador is not None:   # un operador solo ve las alertas de SUS asignaciones (y las generales)
            alertas_q = alertas_q.where(_alertas_de_operador(operador))
        importantes = [ser.alert(a) for a in s.scalars(alertas_q.order_by(Alert.id.desc()).limit(8))]
        n_importantes = s.scalar(select(func.count()).select_from(alertas_q.subquery())) or 0

        # ---------------------------------------------------------------- indicadores del periodo
        ev_q = select(ChangeEvent.change_type, func.count()).where(ChangeEvent.detected_at >= since,
                                                                    *([ChangeEvent.region == region] if region else []))
        cambios = dict(s.execute(ev_q.group_by(ChangeEvent.change_type)).all())
        pq = select(PollLog.outcome, func.count()).where(PollLog.started_at >= since)
        if region:
            pq = pq.join(Source, Source.id == PollLog.source_id).where(Source.region == region)
        polls = dict(s.execute(pq.group_by(PollLog.outcome)).all())
        total_polls = sum(polls.values())
        errores_polls = total_polls - polls.get("OK", 0) - polls.get("LIMITADO", 0)
        sids = None
        if region:
            sids = list(s.scalars(select(Source.id).where(Source.region == region)))
        met = service_metrics(s, since=since, now=now, source_ids=sids)

        out = {
            "generado_en": ser.iso(now), "periodo": periodo, "desde": ser.iso(since),
            "filtros": {"region": region, "sucursal": sucursal, "operador": operador, "estado": estado},
            "origen": "consultas a la base de datos del motor; tiempos medidos con marcas de tiempo reales",
            "estado_general": {
                "motor": {"activo": bool(sch and sch.running), "ciclos": sch.cycles if sch else 0,
                          "ultimo_error": sch.last_error if sch else None},
                "conectores_activos": conectores,
                "fuentes": {"total": len(fuentes), "activas": sum(1 for f in fuentes if f[1] == "ACTIVA"),
                            "con_error_de_comunicacion": len(errores_com), "en_recuperacion": len(recuperacion)},
                "regiones": regiones, "sucursales": sucursales,
                "ultima_actualizacion_verificada": ser.iso(ultima_ok),
                "operadores_conectados": len(presentes),
                "solicitudes_pendientes": cola,
                "alertas_importantes": n_importantes,
            },
            "indicadores": {
                "disponibilidades_nuevas": sum(cambios.get(k, 0) for k in OFERTA),
                "disponibilidades_agotadas": sum(cambios.get(k, 0) for k in AGOTA),
                "asignaciones_activas": asg_est.get("ACTIVA", 0) + asg_est.get("EN_PROCESO", 0),
                "operaciones_abandonadas": abandonadas,
                "consultas": total_polls, "consultas_con_error": errores_polls,
                "tasa_de_errores_pct": round(100 * errores_polls / total_polls, 2) if total_polls else None,
                "tiempo_deteccion_ms": met["tiempos_ms"]["deteccion"],
                "tiempo_asignacion_ms": met["tiempos_ms"]["hasta_asignacion"],
            },
            "alertas_importantes": importantes,
        }
        if captura_habilitada(request):
            out["captura"] = _bloque_captura(s, now, since, region, sucursal, operador, estado)
            out["estado_general"]["expedientes_preparados"] = out["captura"]["expedientes"]["preparados"]
            out["estado_general"]["reservaciones_confirmadas"] = out["captura"]["reservaciones"]["confirmadas_periodo"]
            out["estado_general"]["operaciones_en_verificacion"] = out["captura"]["reservaciones"]["en_verificacion"]
        return out


def _alertas_de_operador(operator_id: int):
    """Las de sus asignaciones y los avisos generales (fuentes, motor, disponibilidad). Los avisos de
    expedientes sin asignación (incompletos, revisión) son del administrador."""
    return or_(Alert.assignment_id.in_(select(Assignment.id).where(Assignment.operator_id == operator_id)),
               (Alert.assignment_id.is_(None)) & (Alert.kind != "CAPTURA"))


def alertas_visibles(user: CurrentUser):
    """Condición SQL de las alertas que puede ver un usuario no administrador."""
    return _alertas_de_operador(user.operator_id if user.operator_id is not None else -1)


def _filtro_asg(q, region, operador):
    if region:
        q = q.join(SlotState, SlotState.id == Assignment.slot_id).where(SlotState.region == region)
    if operador is not None:
        q = q.where(Assignment.operator_id == operador)
    return q


def _bloque_captura(s, now, since, region, sucursal, operador, estado) -> dict:
    from ..captura import calendario_dias as cd
    from ..captura import expedientes as ex
    from ..models import CalendarioCambio, CalendarioFuente, Expediente, ExpedienteEvento, Reservacion
    f = []
    if region:
        f.append(Expediente.region == region)
    if sucursal:
        f.append(Expediente.sucursal == sucursal)
    if operador is not None:
        f.append(Expediente.operator_id == operador)
    if estado:
        f.append(Expediente.estado == estado)
    por_estado = dict(s.execute(select(Expediente.estado, func.count()).where(*f).group_by(Expediente.estado)).all())

    rf = []
    if sucursal:
        rf.append(Reservacion.sucursal == sucursal)
    if operador is not None:
        rf.append(Reservacion.operator_id == operador)
    if region or estado:
        rf.append(Reservacion.expediente_id.in_(select(Expediente.id).where(*f)))
    conf = list(s.execute(select(Reservacion.sucursal, func.count()).where(
        Reservacion.estado == "CONFIRMADA", Reservacion.updated_at >= since, *rf).group_by(Reservacion.sucursal)))
    en_verif = s.scalar(select(func.count()).select_from(Reservacion).where(
        Reservacion.estado == "EN_VERIFICACION", *rf)) or 0

    # tiempo hasta confirmación: ASIGNADO -> RESERVACION_CONFIRMADA del mismo expediente (historial)
    eids = select(Expediente.id).where(*f) if f else None
    qe = select(ExpedienteEvento.expediente_id, ExpedienteEvento.ts, ExpedienteEvento.tipo, ExpedienteEvento.detalle).where(
        ExpedienteEvento.ts >= since - timedelta(days=1),
        or_(ExpedienteEvento.tipo == "RESERVACION_CONFIRMADA", ExpedienteEvento.tipo == "ESTADO"))
    if eids is not None:
        qe = qe.where(ExpedienteEvento.expediente_id.in_(eids))
    asignado: dict[int, datetime] = {}
    t_conf = []
    for eid, ts, tipo, det in s.execute(qe.order_by(ExpedienteEvento.id)):
        if tipo == "ESTADO" and (det or {}).get("despues") == ex.ASIGNADO:
            asignado[eid] = ts
        elif tipo == "RESERVACION_CONFIRMADA" and ts >= since and eid in asignado:
            t_conf.append((ts - asignado[eid]).total_seconds() * 1000)

    # calendario por día: frescura y cambios del periodo
    fuentes = []
    for cf in s.scalars(select(CalendarioFuente)):
        fuentes.append({"source_id": cf.source_id, "mecanismo": cf.mecanismo, "conectado": cf.conectado,
                        "sin_verificar": cd.sin_verificar(cf), "verificado_en": ser.iso(cf.verificado_en),
                        "ultimo_error": cf.ultimo_error_codigo})
    cq = select(CalendarioCambio.tipo, func.count()).where(CalendarioCambio.detectado_en >= since)
    if sucursal:
        cq = cq.where(CalendarioCambio.sucursal == sucursal)
    cambios_cal = dict(s.execute(cq.group_by(CalendarioCambio.tipo)).all())
    return {
        "origen": "tablas expedientes, reservaciones, expediente_eventos y calendario_cambios",
        "expedientes": {
            "por_estado": por_estado,
            "pendientes": sum(por_estado.get(k, 0) for k in (ex.NUEVO, ex.EN_VALIDACION)),
            "incompletos": por_estado.get(ex.INCOMPLETO, 0),
            "listos": por_estado.get(ex.LISTO, 0),
            "preparados": sum(por_estado.get(k, 0) for k in (ex.LISTO, ex.ASIGNADO)),
            "en_proceso": sum(por_estado.get(k, 0) for k in (ex.CAPTURA, ex.RES_PENDIENTE)),
            "requieren_revision": por_estado.get(ex.REVISION, 0),
        },
        "reservaciones": {
            "confirmadas_periodo": sum(n for _, n in conf),
            "confirmadas_por_sucursal": {k or "—": n for k, n in sorted(conf, key=lambda x: x[0] or "")},
            "en_verificacion": en_verif,
        },
        "tiempo_hasta_confirmacion_ms": percentiles(t_conf),
        "calendario": {"fuentes": fuentes,
                       "disponibilidades_nuevas": sum(cambios_cal.get(k, 0) for k in ("APERTURA", "REAPERTURA")),
                       "dias_agotados": cambios_cal.get("AGOTAMIENTO", 0),
                       "errores_de_comunicacion": sum(cambios_cal.get(k, 0) for k in ("DESCONEXION", "SESION_CADUCADA"))},
    }


# ======================================================================= supervisión
def _estado_presencia(s, o: Operator, now: datetime, params: dict) -> str:
    """CONECTADO (usa el panel), CONEXION_PERDIDA (sin señal un rato: puede volver), INACTIVO (sin
    señal mucho tiempo), DESCONECTADO (cerró sesión o la sesión caducó), SIN_USUARIO (canal externo)."""
    if o.user_id is None:
        return "SIN_USUARIO"
    sesion_viva = s.scalar(select(func.count()).select_from(ApiToken).where(
        ApiToken.user_id == o.user_id, ApiToken.revoked.is_(False), ApiToken.expires_at > now)) or 0
    if not sesion_viva:
        return "DESCONECTADO"
    if o.visto_en is None:
        return "INACTIVO"
    hueco = (now - o.visto_en).total_seconds()
    if hueco <= float(params["operador_conexion_perdida_s"]):
        return "CONECTADO"
    if hueco <= float(params["operador_inactivo_s"]):
        return "CONEXION_PERDIDA"
    return "INACTIVO"


def _operadores_conectados(s, now, params) -> list[int]:
    lim = now - timedelta(seconds=float(params["operador_conexion_perdida_s"]))
    return list(s.scalars(select(Operator.id).where(Operator.active.is_(True), Operator.visto_en >= lim)))


@router.get("/supervision")
def supervision(request: HttpRequest, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    now = utcnow()
    hoy = _desde("hoy", now)
    cap = captura_habilitada(request)
    with db.session() as s:
        params = get_params(s)
        out = []
        for o in s.scalars(select(Operator).order_by(Operator.name)):
            est = dict(s.execute(select(Assignment.status, func.count()).where(
                Assignment.operator_id == o.id).group_by(Assignment.status)).all())
            hoy_est = dict(s.execute(select(Assignment.status, func.count()).where(
                Assignment.operator_id == o.id, Assignment.created_at >= hoy).group_by(Assignment.status)).all())
            ult = s.scalar(select(func.max(func.coalesce(Assignment.last_activity_at, Assignment.taken_at,
                                                         Assignment.created_at))).where(Assignment.operator_id == o.id))
            sin_atencion = s.scalar(select(func.count()).select_from(Assignment).where(
                Assignment.operator_id == o.id, or_(
                    (Assignment.status == "EN_PROCESO") & Assignment.inactive_alerted_at.is_not(None),
                    (Assignment.status == "ACTIVA") & (Assignment.created_at <= now - timedelta(
                        seconds=float(params["operador_conexion_perdida_s"])))))) or 0
            fila = {
                "id": o.id, "nombre": o.name, "activo": o.active, "regiones": o.regions or [],
                "sucursales": o.sucursales or [], "carga": o.current_load, "max": o.max_concurrent,
                "presencia": _estado_presencia(s, o, now, params),
                "visto_hace_s": round((now - o.visto_en).total_seconds()) if o.visto_en else None,
                "ultima_actividad_hace_s": round((now - ult).total_seconds()) if ult else None,
                "asignadas": est.get("ACTIVA", 0), "en_proceso": est.get("EN_PROCESO", 0),
                "completadas": est.get("COMPLETADA", 0), "completadas_hoy": hoy_est.get("COMPLETADA", 0),
                "rechazos": est.get("RECHAZADA", 0) + est.get("DECLINADA", 0),
                "expiradas": est.get("EXPIRADA", 0), "canceladas": est.get("CANCELADA", 0),
                "sin_atencion": sin_atencion,
            }
            if cap:
                from ..models import Expediente, ExpedienteEvento, Reservacion
                fila["expedientes"] = dict(s.execute(select(Expediente.estado, func.count()).where(
                    Expediente.operator_id == o.id).group_by(Expediente.estado)).all())
                fila["reasignaciones"] = s.scalar(select(func.count()).select_from(ExpedienteEvento).where(
                    ExpedienteEvento.tipo.in_(["ASIGNACION_CAMBIADA", "ASIGNACION_LIBERADA", "REDISTRIBUIDO"]),
                    ExpedienteEvento.detalle["operador_anterior"].as_integer() == o.id)) or 0
                fila["errores"] = s.scalar(select(func.count()).select_from(Reservacion).where(
                    Reservacion.operator_id == o.id, Reservacion.error_codigo.is_not(None))) or 0
            out.append(fila)
        acciones = [{"ts": ser.iso(a.ts), "actor": a.username, "accion": a.action, "objetivo": a.target,
                     "detalle": a.detail} for a in s.scalars(
            select(security.AuditLog).where(security.AuditLog.action.in_(
                ["operador_redistribuido", "asignacion_recuperar", "asignacion_rechazada", "editar_operador", "crear_operador", "editar_parametros"]))
            .order_by(security.AuditLog.id.desc()).limit(30))]
        return {"generado_en": ser.iso(now), "parametros": {
            k: params[k] for k in ("operador_conexion_perdida_s", "operador_inactivo_s", "operador_ausente_s", "reparto_modo")},
            "operadores": out, "acciones_recientes": acciones}


class RedistribuirIn(BaseModel):
    confirmo: bool = False
    motivo: str = Field(min_length=5, max_length=200)
    incluir_en_proceso: bool = False      # las tomadas solo si NO hay señal reciente ni confirmación en curso
    desactivar: bool = False              # además, no recibe asignaciones nuevas hasta reactivarlo


@router.post("/supervision/operadores/{oid}/redistribuir")
def redistribuir(oid: int, body: RedistribuirIn, request: HttpRequest, user: CurrentUser = Depends(require_admin),
                 db: Database = Depends(get_db)):
    """Devuelve a la cola el trabajo de un operador que dejó de estar disponible.
    - Asignaciones NO tomadas (ACTIVA): se liberan siempre (nadie trabaja en ellas).
    - Tomadas (EN_PROCESO): solo con `incluir_en_proceso` y solo si el operador no da señal hace más
      de `operador_conexion_perdida_s` y el expediente no tiene una confirmación en curso o en
      verificación. Si no se cumple, se listan como "no liberadas" con el motivo.
    Cada liberación es una transacción con UPDATE condicional (si el operador la tomó o completó
    mientras tanto, no se toca). Todo queda en la auditoría. El operador no vuelve a recibir esas solicitudes."""
    if not body.confirmo:
        raise HTTPException(422, "Falta confirmar: revisa antes con el operador si sigue trabajando")
    now = utcnow()
    cap = captura_habilitada(request)
    with db.session() as s:
        o = s.get(Operator, oid)
        if o is None:
            raise HTTPException(404, "Operador no encontrado")
        params = get_params(s)
        lim = float(params["operador_conexion_perdida_s"])
        senal_reciente = o.visto_en is not None and (now - o.visto_en).total_seconds() <= lim
        filas = list(s.execute(select(Assignment.id, Assignment.status, Assignment.request_id).where(
            Assignment.operator_id == oid, Assignment.status.in_(["ACTIVA", "EN_PROCESO"]))))
        bloqueadas: dict[int, str] = {}
        if cap:
            from ..captura import reservaciones as rs
            from ..models import Expediente
            for aid, st, rid in filas:
                e = s.scalar(select(Expediente).where(Expediente.request_id == rid))
                if e is not None and rs.viva_bloquea(s, e.id):
                    bloqueadas[aid] = "hay una confirmación en curso o en verificación: la resuelve el motor"
        if body.desactivar:
            o.active = False
        nombre = o.name
    liberadas, no_liberadas = [], []
    actor = f"{user.username} (redistribución)"
    for aid, st, _rid in filas:
        if st == "EN_PROCESO" and not body.incluir_en_proceso:
            no_liberadas.append({"asignacion": aid, "motivo": "tomada por el operador (no se incluyeron las tomadas)"})
            continue
        if st == "EN_PROCESO" and senal_reciente:
            no_liberadas.append({"asignacion": aid, "motivo": "el operador dio señal hace poco: puede estar trabajando"})
            continue
        if aid in bloqueadas:
            no_liberadas.append({"asignacion": aid, "motivo": bloqueadas[aid]})
            continue
        ok = asg.reject_assignment(db, aid, oid, now, actor, params, reason=f"redistribución: {body.motivo}",
                                   only_status=(st,), status="DECLINADA")
        (liberadas if ok else no_liberadas).append(
            {"asignacion": aid} if ok else {"asignacion": aid, "motivo": "cambió de estado mientras tanto"})
    if cap and liberadas:
        from ..captura import seguimiento
        seguimiento.sincronizar(db, now)          # los expedientes vuelven a LISTO sin esperar al ciclo
    with db.session() as s:
        security.audit(s, user.username, "operador_redistribuido", nombre,
                       {"motivo": body.motivo, "liberadas": len(liberadas), "no_liberadas": len(no_liberadas),
                        "incluir_en_proceso": body.incluir_en_proceso, "desactivado": body.desactivar})
    return {"operador": nombre, "liberadas": liberadas, "no_liberadas": no_liberadas, "desactivado": body.desactivar}


@router.get("/asignaciones/{aid}/explicacion")
def explicacion(aid: int, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Por qué el motor eligió a ese operador (texto legible + detalle)."""
    with db.session() as s:
        a = s.get(Assignment, aid)
        if a is None or (not user.is_admin and a.operator_id != user.operator_id):
            raise HTTPException(404, "Asignación no encontrada")
        e = a.explicacion or {}
        if not user.is_admin:   # el operador ve el resumen, no la lista de compañeros descartados
            e = {"resumen": re.split(r" (?:Otros candidatos|Descartados):", e.get("resumen") or "")[0],
                 "criterio": e.get("criterio")}
        return {"asignacion": aid, "explicacion": e or None,
                "nota": None if e else "Asignación anterior a la v1.7: no se guardó explicación"}


# ======================================================================= centro de notificaciones
@router.get("/notificaciones")
def notificaciones(categoria: str | None = Query(None, max_length=32), nivel: str | None = Query(None, max_length=8),
                   estado: str | None = Query(None, max_length=16), region: str | None = Query(None, max_length=32),
                   sin_confirmar: bool = False, periodo: str = Query("7d", pattern="^(1h|hoy|24h|7d)$"),
                   limit: int = Query(100, ge=1, le=500), antes_de: int | None = None,
                   user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Historial filtrable + resumen por categoría y nivel + indicadores de entrega por canal.
    Un operador solo ve las de sus asignaciones y los avisos generales."""
    from ..engine import alerts as al
    from ..models import Channel, Delivery
    now = utcnow()
    since = _desde(periodo, now)
    with db.session() as s:
        params = get_params(s)
        base = [Alert.created_at >= since]
        if not user.is_admin:
            base.append(alertas_visibles(user))
        if region:
            base.append(Alert.region == region)
        f = list(base)
        if categoria:
            f.append(Alert.categoria == categoria)
        if nivel:
            f.append(Alert.nivel == nivel)
        if estado:
            f.append(Alert.status == estado)
        if sin_confirmar:
            f.append(Alert.acknowledged_at.is_(None))
        if antes_de:
            f.append(Alert.id < antes_de)
        filas = list(s.scalars(select(Alert).where(*f).order_by(Alert.id.desc()).limit(limit)))
        ids = [a.id for a in filas]
        entregas: dict[int, dict] = {}
        if ids:
            for aid, st, n in s.execute(select(Delivery.alert_id, Delivery.status, func.count()).where(
                    Delivery.alert_id.in_(ids)).group_by(Delivery.alert_id, Delivery.status)):
                entregas.setdefault(aid, {})[st] = n
        items = [{**ser.alert(a), "entregas": entregas.get(a.id, {})} for a in filas]
        # resumen del periodo (con los filtros de alcance y región, sin los de categoría/nivel)
        resumen = {}
        for cat, niv, ack, n, rep in s.execute(select(Alert.categoria, Alert.nivel, Alert.acknowledged_at.is_(None),
                                                      func.count(), func.sum(Alert.repeticiones)).where(*base)
                                               .group_by(Alert.categoria, Alert.nivel, Alert.acknowledged_at.is_(None))):
            k = cat or "SIN_CLASIFICAR"
            r = resumen.setdefault(k, {"total": 0, "sin_confirmar": 0, "repeticiones": 0, "nivel": niv})
            r["total"] += n
            r["repeticiones"] += int(rep or 0)
            if ack:
                r["sin_confirmar"] += n
        canales = []
        if user.is_admin:
            for ch_id, nombre, tipo, habil in s.execute(select(Channel.id, Channel.name, Channel.type, Channel.enabled)):
                est = dict(s.execute(select(Delivery.status, func.count()).join(Alert, Alert.id == Delivery.alert_id)
                                     .where(Delivery.channel_id == ch_id, Alert.created_at >= since)
                                     .group_by(Delivery.status)).all())
                lat = [x for x in s.scalars(select(Delivery.latency_ms).join(Alert, Alert.id == Delivery.alert_id).where(
                    Delivery.channel_id == ch_id, Alert.created_at >= since, Delivery.latency_ms.is_not(None))
                    .order_by(Delivery.id.desc()).limit(500))]
                canales.append({"canal": nombre, "tipo": tipo, "habilitado": habil, "entregas": est,
                                "latencia_ms": percentiles(lat)})
        return {
            "generado_en": ser.iso(now), "desde": ser.iso(since),
            "categorias": [{"clave": k, "nombre": v[0], "nivel": v[1], "silenciable": k in al.SILENCIABLES,
                            "silenciada": k in (params.get("alertas_silenciadas") or [])}
                           for k, v in al.CATEGORIAS.items()],
            "agrupar_s": params.get("alertas_agrupar_s"),
            "resumen": resumen, "canales": canales, "items": items,
            "siguiente": ids[-1] if len(ids) == limit else None,
        }


class ConfirmarIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/notificaciones/confirmar")
def confirmar_varias(body: ConfirmarIn, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    """Confirma de una vez varias alertas visibles para el usuario (idempotente, auditado)."""
    from sqlalchemy import update
    now = utcnow()
    with db.session() as s:
        cond = [Alert.id.in_(body.ids), Alert.acknowledged_at.is_(None)]
        if not user.is_admin:
            cond.append(alertas_visibles(user))
        visibles = list(s.scalars(select(Alert.id).where(*cond)))
        n = 0
        if visibles:
            n = s.execute(update(Alert).where(Alert.id.in_(visibles), Alert.acknowledged_at.is_(None)).values(
                status="CONFIRMADA", acknowledged_at=now, acknowledged_by=user.username)).rowcount
            security.audit(s, user.username, "confirmar_alertas", f"{n} alertas", {"ids": visibles[:50]})
    return {"confirmadas": n, "ignoradas": len(set(body.ids)) - n}

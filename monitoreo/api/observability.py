"""Observabilidad y diagnóstico (v1.1)."""
from __future__ import annotations

import time
from datetime import timedelta

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest
from sqlalchemy import func, select

from .. import security
from ..db import Database, utcnow
from ..engine import alerts as al
from ..engine.params import get_params
from ..engine.retention import get_watermark, poll_stats
from ..models import (Alert, AuditLog, ChangeEvent, Channel, Delivery, MaintenanceLog, PollLog, Region, Request,
                      SchedulerLease, Setting, SlotState, Source)
from ..notifiers.telegram import telegram_status
from . import serializers as ser
from .deps import CurrentUser, current_user, get_db, require_admin

router = APIRouter(prefix="/api")
_proc = psutil.Process()


def _pcts(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "p50": None, "p95": None}
    v = sorted(values)
    return {"n": len(v), "p50": round(v[len(v) // 2], 1), "p95": round(v[min(len(v) - 1, int(0.95 * (len(v) - 1)))], 1)}


def _engine_status(request: HttpRequest, db: Database) -> dict:
    """Abre sus propias sesiones: llamarla FUERA de cualquier otra sesión (ver nota en observability)."""
    sch = getattr(request.app.state, "scheduler", None)
    if sch is not None:
        st = sch.status()
    else:
        st = {"activo": False, "rol": "SIN_MOTOR", "instancia": None}
    with db.session() as s:
        lease = s.get(SchedulerLease, "planificador")
    now = utcnow()
    st["lider_global"] = None if lease is None else {
        "titular": lease.holder, "vigente": lease.expires_at > now, "vence": ser.iso(lease.expires_at),
        "renovado_hace_s": round((now - lease.renewed_at).total_seconds(), 1)}
    return st


@router.get("/observabilidad")
def observability(request: HttpRequest, user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    now = utcnow()
    since = now - timedelta(hours=24)
    # Estas dos funciones abren su propia sesión: deben llamarse ANTES de abrir la nuestra.
    # En SQLite cada transacción toma el candado de escritura (BEGIN IMMEDIATE) y una sesión
    # anidada esperaría a la exterior hasta agotar el tiempo (defecto detectado en pruebas).
    stats = {x["source_id"]: x for x in poll_stats(db, since, "source")}
    wm = get_watermark(db)
    engine = _engine_status(request, db)
    t0 = time.perf_counter()
    with db.session() as s:
        s.execute(select(1))
        db_ms = round((time.perf_counter() - t0) * 1000, 2)
        params = get_params(s)
        lat_rows = s.execute(select(ChangeEvent.source_id, ChangeEvent.region, ChangeEvent.detection_latency_ms)
                             .where(ChangeEvent.detected_at >= since,
                                    ChangeEvent.detection_latency_ms.is_not(None))).all()
        lat_src: dict = {}
        lat_reg: dict = {}
        for sid, reg, ms in lat_rows:
            lat_src.setdefault(sid, []).append(ms)
            lat_reg.setdefault(reg, []).append(ms)
        sources = []
        for src in s.scalars(select(Source).order_by(Source.region, Source.code)):
            age = (now - src.last_success_at).total_seconds() if src.last_success_at else None
            stale = src.status == "ACTIVA" and (age is None or age > max(3 * src.interval_s, 60))
            st = stats.get(src.id, {})
            sources.append({
                "id": src.id, "code": src.code, "name": src.name, "region": src.region, "status": src.status,
                "motivo": src.status_reason, "circuito": src.circuit_state, "errores_seguidos": src.consecutive_failures,
                "ultimo_resultado": src.last_outcome, "ultima_consulta_ok": ser.iso(src.last_success_at),
                "segundos_desde_ok": round(age, 1) if age is not None else None, "desactualizada": stale,
                "proxima_consulta": ser.iso(src.next_poll_at), "intervalo_s": src.interval_s,
                "latencia_deteccion_ms": _pcts(lat_src.get(src.id, [])),
                "consultas_24h": st.get("consultas", 0), "tasa_error_24h": st.get("tasa_error", 0),
                "latencia_consulta_ms": st.get("latencia_media_ms"),
            })
        regions = []
        for r in s.scalars(select(Region).order_by(Region.code)):
            rs = [x for x in sources if x["region"] == r.code]
            regions.append({
                "code": r.code, "name": r.name, "fuentes": len(rs),
                "fuentes_ok": sum(1 for x in rs if x["status"] == "ACTIVA" and not x["desactualizada"]
                                  and x["circuito"] == "CERRADO"),
                "fuentes_con_problemas": sum(1 for x in rs if x["status"] != "ACTIVA" or x["desactualizada"]
                                             or x["circuito"] != "CERRADO"),
                "ultima_consulta_ok": max((x["ultima_consulta_ok"] for x in rs if x["ultima_consulta_ok"]), default=None),
                "solicitudes_en_espera": s.scalar(select(func.count()).select_from(Request).where(
                    Request.region == r.code, Request.status.in_(["PENDIENTE", "ELEGIBLE", "REINTENTO"]))),
                "unidades_libres": s.scalar(select(func.coalesce(func.sum(SlotState.available - SlotState.held), 0))
                                            .where(SlotState.region == r.code, SlotState.status == "ACTIVO")),
                "alertas_sin_confirmar": s.scalar(select(func.count()).select_from(Alert).where(
                    Alert.region == r.code, Alert.acknowledged_at.is_(None))),
                "latencia_deteccion_ms": _pcts(lat_reg.get(r.code, [])),
            })
        last_hist = s.scalar(select(MaintenanceLog).where(MaintenanceLog.task == "historial")
                             .order_by(MaintenanceLog.id.desc()).limit(1))
        rec = s.get(Setting, "recuperacion.ultima")
        mem = _proc.memory_info()
        return {
            "momento": ser.iso(now), "motor": engine,
            "base_de_datos": {"tipo": "sqlite" if db.is_sqlite else "postgresql", "latencia_ms": db_ms},
            "colas": {
                "solicitudes_en_espera": s.scalar(select(func.count()).select_from(Request).where(
                    Request.status.in_(["PENDIENTE", "ELEGIBLE", "REINTENTO"]))),
                "solicitudes_en_proceso": s.scalar(select(func.count()).select_from(Request).where(
                    Request.status.in_(["ASIGNADO", "EN_PROCESO"]))),
                "eventos_sin_procesar": s.scalar(select(func.count()).select_from(ChangeEvent).where(
                    ChangeEvent.processed.is_(False))),
                "entregas_pendientes": s.scalar(select(func.count()).select_from(Delivery).where(
                    Delivery.status.in_(["PENDIENTE", "ENVIANDO"]))),
                "entregas_fallidas_24h": s.scalar(select(func.count()).select_from(Delivery).where(
                    Delivery.status == "FALLIDA", Delivery.next_attempt_at >= since)),
                "alertas_sin_confirmar": s.scalar(select(func.count()).select_from(Alert).where(
                    Alert.acknowledged_at.is_(None))),
            },
            "regiones": regions, "fuentes": sources,
            "telegram": telegram_status(),
            "historial": {"agregado_hasta": wm.isoformat() + "Z" if wm else None,
                          "filas_poll_log": s.scalar(select(func.count()).select_from(PollLog)),
                          "retencion_consultas_dias": params["retencion_consultas_dias"],
                          "ultimo_mantenimiento": {"ts": ser.iso(last_hist.ts), "detalle": last_hist.detail,
                                                   "ms": round(last_hist.duration_ms, 1)} if last_hist else None},
            "ultima_recuperacion": rec.value if rec else None,
            "proceso": {"pid": _proc.pid, "cpu_pct": _proc.cpu_percent(interval=None),
                        "memoria_mb": round(mem.rss / 1048576, 1), "hilos": _proc.num_threads()},
        }


@router.get("/diagnostico/fuentes/{sid}")
def diagnose_source(sid: int, request: HttpRequest, user: CurrentUser = Depends(current_user),
                    db: Database = Depends(get_db)):
    """Explica en lenguaje claro por qué una fuente no se está actualizando."""
    now = utcnow()
    engine = _engine_status(request, db)
    with db.session() as s:
        src = s.get(Source, sid)
        if src is None:
            raise HTTPException(404, "Fuente no encontrada")
        params = get_params(s)
        recent = list(s.scalars(select(PollLog).where(PollLog.source_id == sid).order_by(PollLog.id.desc()).limit(50)))
        causes: list[dict] = []

        def add(sev, title, detail, action=""):
            causes.append({"gravedad": sev, "causa": title, "detalle": detail, "accion": action})

        leader = engine.get("lider_global")
        if not leader or not leader["vigente"]:
            add("CRITICO", "El planificador no está activo en ninguna instancia",
                "Nadie tiene el arrendamiento vigente: ninguna fuente se está consultando.",
                "Arranca el sistema (iniciar.bat) o revisa los errores del motor en Salud.")
        elif engine.get("rol") == "EN_ESPERA":
            add("INFO", "Esta instancia está en espera",
                f"Otra instancia ({leader['titular']}) es la líder y consulta las fuentes.", "")
        if engine.get("errores_ciclo_seguidos"):
            add("CRITICO", "El ciclo del motor está fallando",
                f"{engine['errores_ciclo_seguidos']} ciclos seguidos con error: {engine.get('ultimo_error')}",
                "Suele ser la base de datos sin conexión. Revisa que esté disponible.")
        if src.status == "SUSPENDIDA":
            who = s.scalar(select(AuditLog).where(AuditLog.target == src.code, AuditLog.action == "fuente_suspender")
                           .order_by(AuditLog.id.desc()).limit(1))
            add("AVISO", "Suspendida manualmente",
                f"Por {who.username} el {ser.iso(who.ts)}" if who else "Suspendida (sin registro de quién).",
                "Pulsa Reanudar en Monitor si debe volver a consultarse.")
        elif src.status == "ERROR":
            add("CRITICO", "Detenida por errores", src.status_reason or src.last_error or "sin detalle",
                "Corrige la causa (configuración, credencial, disponibilidad de la fuente) y pulsa Reanudar.")
        if src.circuit_state == "ABIERTO" and src.circuit_opened_at:
            until = src.circuit_opened_at + timedelta(seconds=params["enfriamiento_circuito_s"])
            add("AVISO", "Circuito abierto (pausa automática)",
                f"{src.consecutive_failures} fallos seguidos. Próximo intento de prueba: {ser.iso(until)}.",
                "Es automático. Si persiste, revisa el último error.")
        if src.status == "ACTIVA" and src.next_poll_at and src.next_poll_at > now:
            wait = (src.next_poll_at - now).total_seconds()
            if src.last_outcome == "LIMITADO":
                add("AVISO", "La fuente pidió bajar el ritmo",
                    f"Se respeta su espera: próxima consulta en {wait:.0f} s.",
                    "No hacer nada; si es frecuente, aumenta el intervalo o baja el límite por minuto.")
            elif src.consecutive_failures:
                add("AVISO", "Retroceso exponencial tras errores",
                    f"{src.consecutive_failures} fallo(s) seguidos; próxima consulta en {wait:.0f} s.", "")
            elif src.last_outcome == "LIMITE_LOCAL":
                add("AVISO", "Límite local de solicitudes alcanzado",
                    f"El límite de {src.rate_limit_per_min}/min retrasa la consulta {wait:.0f} s.",
                    "Si el intervalo es más corto que lo que permite el límite, ajusta uno de los dos.")
        if src.rate_limit_per_min < 60 / max(src.interval_s, 0.001):
            add("AVISO", "Configuración incoherente",
                f"Intervalo {src.interval_s} s exige {60 / src.interval_s:.1f} consultas/min pero el límite es "
                f"{src.rate_limit_per_min}/min.", "Sube el límite o alarga el intervalo.")
        age = (now - src.last_success_at).total_seconds() if src.last_success_at else None
        if src.status == "ACTIVA" and (age is None or age > max(3 * src.interval_s, 60)):
            add("AVISO", "Sin datos frescos",
                "Nunca ha respondido correctamente." if age is None else
                f"Última consulta correcta hace {age:.0f} s (intervalo {src.interval_s:.0f} s).",
                "Mira los últimos errores abajo.")
        ok_recent = [p for p in recent if p.outcome == "OK"]
        if ok_recent and ok_recent[0].items == 0:
            add("AVISO", "La fuente responde vacía", "La última consulta correcta no devolvió recursos.",
                "Comprueba que la configuración apunta al lugar correcto.")
        if not causes:
            add("OK", "Funcionando con normalidad",
                f"Última consulta correcta hace {age:.0f} s." if age is not None else "", "")
        hist: dict = {}
        for p in recent:
            hist[p.outcome] = hist.get(p.outcome, 0) + 1
        sev_order = {"CRITICO": 0, "AVISO": 1, "INFO": 2, "OK": 3}
        causes.sort(key=lambda c: sev_order[c["gravedad"]])
        return {
            "fuente": ser.source(src), "motivo_estado": src.status_reason, "estado_desde": ser.iso(src.status_changed_at),
            "veredicto": causes[0]["gravedad"], "causas": causes,
            "ultimas_50_consultas": hist,
            "ultimos_errores": [ser.poll(p) for p in recent if p.outcome != "OK"][:10],
            "ultimas_consultas": [ser.poll(p) for p in recent[:10]],
            "motor": {k: engine.get(k) for k in ("rol", "activo", "instancia", "lider_global", "ultimo_error")},
        }


@router.get("/estadisticas")
def statistics(group: str = Query("region", pattern="^(region|source)$"), days: float = Query(7, gt=0, le=365),
               user: CurrentUser = Depends(current_user), db: Database = Depends(get_db)):
    return poll_stats(db, utcnow() - timedelta(days=days), group)


@router.post("/channels/{cid}/probar")
def test_channel(cid: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Envía un mensaje de prueba por el canal (no crea alerta ni deduplica)."""
    with db.session() as s:
        ch = s.get(Channel, cid)
        if ch is None:
            raise HTTPException(404, "Canal no encontrado")
        sender = al.SENDERS.get(ch.type, al.NotConfiguredSender)(ch)
        security.audit(s, user.username, "probar_canal", ch.name)
    payload = {"id": 0, "dedup_key": f"prueba:{utcnow().isoformat()}", "tipo": "SISTEMA",
               "titulo": "Mensaje de prueba del Motor de Monitoreo", "fecha_hora": ser.iso(utcnow())}
    try:
        return {"ok": True, "detalle": sender.send(payload, {})}
    except al.DeliveryError as e:
        return {"ok": False, "detalle": str(e)}


@router.get("/autodiagnostico")
def self_diagnosis(request: HttpRequest, profundo: bool = False, user: CurrentUser = Depends(require_admin),
                   db: Database = Depends(get_db)):
    """Revisión completa del motor con acciones sugeridas (v1.2)."""
    from .. import diagnostico
    return diagnostico.run(db, getattr(request.app.state, "scheduler", None), deep=profundo)

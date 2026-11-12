"""Historial: agregados horarios/diarios y limpieza gradual por lotes.

Qué se agrega: cada hora cerrada de `poll_log` se resume por fuente en `poll_stats_hourly`,
y cada día en `poll_stats_daily`. El resumen se recalcula entero para esa hora/día
(borrar + insertar en la misma transacción), así que repetirlo es inofensivo (idempotente).

Qué se borra (y solo después de estar resumido):
  - poll_log más antiguo que `retencion_consultas_dias`
  - change_events NO relevantes, ya procesados, sin alertas ni asignaciones que los
    referencien, más antiguos que `retencion_eventos_sin_alerta_dias`
  - poll_stats_hourly más antiguos que `retencion_estadisticas_horarias_dias`
  - notification_log solo si `retencion_registro_notificaciones_dias` > 0

Qué NUNCA se borra: auditoría, solicitudes y su historial, asignaciones, alertas, entregas,
eventos relevantes, estado actual de recursos, agregados diarios.

El borrado va en lotes pequeños, cada uno en su propia transacción corta, con una pausa
entre lotes y un tope por pasada: el motor sigue trabajando mientras tanto.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import case, delete, exists, func, select

from ..db import Database
from ..models import (Alert, Assignment, ChangeEvent, MaintenanceLog, NotificationLog, PollLog, PollStatDaily,
                      PollStatHourly, Setting, Source)
from .params import get_params

WATERMARK_KEY = "retencion.agregado_hasta"
PROTECTED_TABLES = ("audit_log", "requests", "request_events", "assignments", "alerts", "deliveries",
                    "slot_states", "poll_stats_daily", "change_events(relevantes)")


def _floor_hour(d: datetime) -> datetime:
    return d.replace(minute=0, second=0, microsecond=0)


def _get_watermark(s) -> datetime | None:
    row = s.get(Setting, WATERMARK_KEY)
    return datetime.fromisoformat(row.value) if row and row.value else None


def get_watermark(db: Database) -> datetime | None:
    with db.session() as s:
        return _get_watermark(s)


def aggregate(db: Database, now: datetime, max_hours: int = 24 * 14) -> dict:
    """Resume las horas cerradas pendientes. Devuelve cuántas horas y días se procesaron."""
    limit = _floor_hour(now)
    with db.session() as s:
        wm = _get_watermark(s)
        if wm is None:
            first = s.scalar(select(func.min(PollLog.started_at)))
            if first is None:
                return {"horas": 0, "dias": 0}
            wm = _floor_hour(first)
    hours = days_touched = 0
    days: set[datetime] = set()
    while wm < limit and hours < max_hours:
        h0, h1 = wm, wm + timedelta(hours=1)
        with db.session() as s:
            rows = s.execute(
                select(PollLog.source_id, func.count(),
                       func.sum(case((PollLog.outcome == "OK", 1), else_=0)),
                       func.sum(case((PollLog.outcome.in_(["ERROR", "ERROR_TEMPORAL"]), 1), else_=0)),
                       func.sum(case((PollLog.outcome == "LIMITADO", 1), else_=0)),
                       func.coalesce(func.sum(PollLog.items), 0), func.coalesce(func.sum(PollLog.changes), 0),
                       func.coalesce(func.sum(PollLog.duplicates), 0), func.coalesce(func.sum(PollLog.duration_ms), 0),
                       func.coalesce(func.max(PollLog.duration_ms), 0))
                .where(PollLog.started_at >= h0, PollLog.started_at < h1).group_by(PollLog.source_id)).all()
            regions = dict(s.execute(select(Source.id, Source.region)).all())
            s.execute(delete(PollStatHourly).where(PollStatHourly.hour == h0))
            for sid, n, ok, err, lim, items, ch, dup, sm, mx in rows:
                s.add(PollStatHourly(source_id=sid, region=regions.get(sid, "?"), hour=h0, polls=n, ok=ok or 0,
                                     errors=err or 0, rate_limited=lim or 0, items=items, changes=ch,
                                     duplicates=dup, sum_ms=sm, max_ms=mx))
            _set_watermark(s, h1)
        if rows:
            days.add(h0.replace(hour=0))
        wm = h1
        hours += 1
    for d0 in sorted(days):
        _rebuild_day(db, d0)
        days_touched += 1
    return {"horas": hours, "dias": days_touched, "agregado_hasta": wm.isoformat()}


def _set_watermark(s, value: datetime) -> None:
    row = s.get(Setting, WATERMARK_KEY)
    if row is None:
        s.add(Setting(key=WATERMARK_KEY, value=value.isoformat()))
    else:
        row.value = value.isoformat()


def _rebuild_day(db: Database, d0: datetime) -> None:
    d1 = d0 + timedelta(days=1)
    H = PollStatHourly
    with db.session() as s:
        rows = s.execute(select(H.source_id, H.region, func.sum(H.polls), func.sum(H.ok), func.sum(H.errors),
                                func.sum(H.rate_limited), func.sum(H.items), func.sum(H.changes),
                                func.sum(H.duplicates), func.sum(H.sum_ms), func.max(H.max_ms))
                         .where(H.hour >= d0, H.hour < d1).group_by(H.source_id, H.region)).all()
        s.execute(delete(PollStatDaily).where(PollStatDaily.day == d0))
        for sid, reg, n, ok, err, lim, items, ch, dup, sm, mx in rows:
            s.add(PollStatDaily(source_id=sid, region=reg, day=d0, polls=n, ok=ok, errors=err, rate_limited=lim,
                                items=items, changes=ch, duplicates=dup, sum_ms=sm, max_ms=mx))


def _purge_batches(db: Database, id_query_fn, model, params: dict, sleep_fn, budget: list[int]) -> int:
    total = 0
    while budget[0] > 0:
        with db.session() as s:
            ids = list(s.scalars(id_query_fn().limit(int(params["retencion_lote"]))))
            if not ids:
                break
            s.execute(delete(model).where(model.id.in_(ids)))
        total += len(ids)
        budget[0] -= 1
        sleep_fn(float(params["retencion_pausa_lote_s"]))
    return total


def purge(db: Database, now: datetime, sleep_fn=time.sleep) -> dict:
    with db.session() as s:
        params = get_params(s)
        wm = _get_watermark(s)
    budget = [int(params["retencion_lotes_por_pasada"])]
    out = {"consultas": 0, "eventos_sin_alerta": 0, "estadisticas_horarias": 0, "registro_notificaciones": 0}
    days = float(params["retencion_consultas_dias"])
    if days > 0 and wm is not None:
        # Nunca borrar algo que aún no se ha resumido (cutoff <= marca de agregación).
        cutoff = min(now - timedelta(days=days), wm)
        out["consultas"] = _purge_batches(
            db, lambda: select(PollLog.id).where(PollLog.started_at < cutoff).order_by(PollLog.id),
            PollLog, params, sleep_fn, budget)
    days = float(params["retencion_eventos_sin_alerta_dias"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        E = ChangeEvent
        q = lambda: (select(E.id).where(  # noqa: E731
            E.relevant.is_(False), E.processed.is_(True), E.detected_at < cutoff,
            ~exists().where(Alert.change_event_id == E.id),
            ~exists().where(Assignment.change_event_id == E.id)).order_by(E.id))
        out["eventos_sin_alerta"] = _purge_batches(db, q, ChangeEvent, params, sleep_fn, budget)
    days = float(params["retencion_estadisticas_horarias_dias"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        out["estadisticas_horarias"] = _purge_batches(
            db, lambda: select(PollStatHourly.id).where(PollStatHourly.hour < cutoff).order_by(PollStatHourly.id),
            PollStatHourly, params, sleep_fn, budget)
    days = float(params["retencion_registro_notificaciones_dias"])
    if days > 0:
        cutoff = now - timedelta(days=days)
        out["registro_notificaciones"] = _purge_batches(
            db, lambda: select(NotificationLog.id).where(NotificationLog.ts < cutoff).order_by(NotificationLog.id),
            NotificationLog, params, sleep_fn, budget)
    out["pendiente_otra_pasada"] = budget[0] <= 0
    return out


def run_history_maintenance(db: Database, now: datetime, holder: str = "", sleep_fn=time.sleep) -> dict:
    t0 = time.perf_counter()
    agg = aggregate(db, now)
    pur = purge(db, now, sleep_fn=sleep_fn)
    detail = {"agregado": agg, "borrado": pur}
    with db.session() as s:
        s.add(MaintenanceLog(ts=now, task="historial", holder=holder,
                             duration_ms=(time.perf_counter() - t0) * 1000, detail=detail))
    return detail


# ---------------------------------------------------------------- estadísticas
def poll_stats(db: Database, since: datetime, group: str = "source") -> list[dict]:
    """Estadísticas desde `since`: agregados horarios + filas aún no resumidas."""
    with db.session() as s:
        wm = _get_watermark(s) or since
        H = PollStatHourly
        key_h = H.source_id if group == "source" else H.region
        acc: dict = {}

        def add(k, n, ok, err, lim, ch, sm, mx):
            a = acc.setdefault(k, {"consultas": 0, "ok": 0, "errores": 0, "limitadas": 0, "cambios": 0,
                                   "suma_ms": 0.0, "max_ms": 0.0})
            a["consultas"] += n or 0; a["ok"] += ok or 0; a["errores"] += err or 0  # noqa: E702
            a["limitadas"] += lim or 0; a["cambios"] += ch or 0; a["suma_ms"] += sm or 0  # noqa: E702
            a["max_ms"] = max(a["max_ms"], mx or 0)

        for row in s.execute(select(key_h, func.sum(H.polls), func.sum(H.ok), func.sum(H.errors),
                                    func.sum(H.rate_limited), func.sum(H.changes), func.sum(H.sum_ms),
                                    func.max(H.max_ms)).where(H.hour >= since, H.hour < wm).group_by(key_h)):
            add(*row)
        raw_since = max(since, wm)
        key_r = PollLog.source_id if group == "source" else Source.region
        q = (select(key_r, func.count(), func.sum(case((PollLog.outcome == "OK", 1), else_=0)),
                    func.sum(case((PollLog.outcome.in_(["ERROR", "ERROR_TEMPORAL"]), 1), else_=0)),
                    func.sum(case((PollLog.outcome == "LIMITADO", 1), else_=0)),
                    func.sum(PollLog.changes), func.sum(PollLog.duration_ms), func.max(PollLog.duration_ms))
             .where(PollLog.started_at >= raw_since))
        if group != "source":
            q = q.join(Source, Source.id == PollLog.source_id)
        for row in s.execute(q.group_by(key_r)):
            add(*row)
        names = dict(s.execute(select(Source.id, Source.code)).all())
        out = []
        for k, a in sorted(acc.items(), key=lambda x: str(x[0])):
            a["latencia_media_ms"] = round(a.pop("suma_ms") / a["consultas"], 2) if a["consultas"] else None
            a["tasa_error"] = round(a["errores"] / a["consultas"], 4) if a["consultas"] else 0
            out.append({("fuente" if group == "source" else "region"): names.get(k, k) if group == "source" else k,
                        **({"source_id": k} if group == "source" else {}), **a})
        return out

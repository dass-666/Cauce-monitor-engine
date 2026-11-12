"""Métricas de servicio (v1.3): tiempos de punta a punta y salud de las consultas.

Todas las cifras salen de marcas de tiempo REALES guardadas por el motor:
- observed_at   cuándo cambió el recurso en la fuente (lo informa la fuente)
- detected_at   cuándo lo detectó el motor
- processed_at  cuándo terminó de procesar el evento (asignación + alertas)
- Assignment.created_at, Alert.created_at, Delivery.delivered_at

Tramos (cada uno se mide desde el cambio en la fuente, salvo "procesamiento"):
  detección       observed_at  -> detected_at
  procesamiento   detected_at  -> processed_at
  asignación      observed_at  -> asignación creada
  alerta          observed_at  -> alerta creada
  notificación    observed_at  -> entrega confirmada por el canal (por tipo de canal)

Nota: si la fuente y el motor estuvieran en máquinas con relojes distintos, los tramos que
empiezan en observed_at incluirían ese desfase. En las pruebas de la v1.3 ambos procesos
comparten reloj (misma máquina).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Alert, Assignment, ChangeEvent, Channel, Delivery, PollLog, Source


def percentiles(values: list[float]) -> dict:
    """p50/p95/p99 por rango más cercano (sin interpolar), en milisegundos."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}

    def q(p: float) -> float:
        return round(v[min(len(v) - 1, max(0, int(round(p * len(v) + 0.5)) - 1))], 1)
    return {"n": len(v), "p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": round(v[-1], 1)}


def _ms(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds() * 1000


def service_metrics(s: Session, since: datetime | None = None, now: datetime | None = None,
                    source_ids: list[int] | None = None) -> dict:
    now = now or utcnow()
    since = since or now - timedelta(hours=1)
    ev_q = select(ChangeEvent).where(ChangeEvent.detected_at >= since)
    if source_ids is not None:
        ev_q = ev_q.where(ChangeEvent.source_id.in_(source_ids))
    events = list(s.scalars(ev_q))
    by_id = {e.id: e for e in events}
    det = [e.detection_latency_ms for e in events if e.observed_at is not None]
    proc = [_ms(e.detected_at, e.processed_at) for e in events if e.processed_at is not None]

    asg_rows = list(s.scalars(select(Assignment).where(Assignment.change_event_id.in_(list(by_id) or [-1]))))
    t_asg = [_ms(by_id[a.change_event_id].observed_at, a.created_at) for a in asg_rows
             if by_id[a.change_event_id].observed_at is not None]
    alerts = list(s.scalars(select(Alert).where(Alert.change_event_id.in_(list(by_id) or [-1]))))
    t_alert = [_ms(by_id[a.change_event_id].observed_at, a.created_at) for a in alerts
               if by_id[a.change_event_id].observed_at is not None]
    alert_ev = {a.id: by_id[a.change_event_id] for a in alerts}
    notif: dict[str, list[float]] = {}
    if alert_ev:
        for d, ctype in s.execute(select(Delivery, Channel.type).join(Channel, Channel.id == Delivery.channel_id)
                                  .where(Delivery.alert_id.in_(list(alert_ev)), Delivery.status == "ENVIADA")):
            ev = alert_ev[d.alert_id]
            if ev.observed_at is not None and d.delivered_at is not None:
                notif.setdefault(ctype, []).append(_ms(ev.observed_at, d.delivered_at))

    # Consultas
    pq = select(PollLog.source_id, PollLog.outcome, func.count(), func.avg(PollLog.duration_ms)).where(
        PollLog.started_at >= since)
    if source_ids is not None:
        pq = pq.where(PollLog.source_id.in_(source_ids))
    polls: dict[int, dict] = {}
    for sid, outcome, n, avg in s.execute(pq.group_by(PollLog.source_id, PollLog.outcome)):
        d = polls.setdefault(sid, {"total": 0, "resultados": {}})
        d["total"] += n
        d["resultados"][outcome] = n
    sources = {x.id: x for x in s.scalars(select(Source).where(Source.id.in_(list(polls) or [-1])))}
    per_region: dict[str, dict] = {}
    total = ok = 0
    for sid, d in polls.items():
        src = sources.get(sid)
        reg = per_region.setdefault(src.region if src else "?", {"consultas": 0, "ok": 0})
        # Éxito = la fuente respondió y los datos se usaron (OK). LIMITADO/OBSOLETA no son fallo de la
        # fuente, pero tampoco aportaron datos: se informan aparte.
        n_ok = d["resultados"].get("OK", 0)
        reg["consultas"] += d["total"]
        reg["ok"] += n_ok
        total += d["total"]
        ok += n_ok
    for reg in per_region.values():
        reg["exito_pct"] = round(100 * reg["ok"] / reg["consultas"], 2) if reg["consultas"] else None
    outcomes: dict[str, int] = {}
    for d in polls.values():
        for k, v in d["resultados"].items():
            outcomes[k] = outcomes.get(k, 0) + v

    supply = [e for e in events if e.change_type in ("NUEVA_DISPONIBILIDAD", "AUMENTO", "REAPARICION")]
    with_asg = {a.change_event_id for a in asg_rows}
    with_alert = {a.change_event_id for a in alerts}
    vanished = [e for e in supply if e.processed and e.relevant and e.id not in with_asg and e.id not in with_alert]
    cancelled_by_source = s.scalar(select(func.count()).select_from(Assignment).where(
        Assignment.closed_at >= since, Assignment.status == "CANCELADA",
        Assignment.reason.contains("dejó de estar disponible"))) or 0
    pending = s.scalar(select(func.min(ChangeEvent.detected_at)).where(ChangeEvent.processed.is_(False)))
    pending_n = s.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.processed.is_(False))) or 0
    oldest_delivery = s.scalar(select(func.min(Delivery.next_attempt_at)).where(Delivery.status == "PENDIENTE"))

    return {
        "desde": since.isoformat() + "Z", "hasta": now.isoformat() + "Z", "origen": "medido (marcas de tiempo del motor)",
        "tiempos_ms": {
            "deteccion": percentiles(det), "procesamiento": percentiles(proc),
            "hasta_asignacion": percentiles(t_asg), "hasta_alerta": percentiles(t_alert),
            "hasta_notificacion": {k: percentiles(v) for k, v in sorted(notif.items())},
        },
        "consultas": {"total": total, "ok": ok, "exito_pct": round(100 * ok / total, 2) if total else None,
                      "por_resultado": outcomes, "por_region": per_region},
        "cambios": {"detectados": len(events), "de_oferta": len(supply), "con_asignacion": len(with_asg),
                    "ofertas_desaparecidas_antes_de_usarse": len(vanished),
                    "asignaciones_anuladas_por_la_fuente": cancelled_by_source},
        "retrasos": {"eventos_sin_procesar": pending_n,
                     "evento_mas_antiguo_sin_procesar_s": round((now - pending).total_seconds(), 1) if pending else 0,
                     "entrega_pendiente_mas_antigua_s":
                         round(max(0.0, (now - oldest_delivery).total_seconds()), 1) if oldest_delivery else 0},
    }


def trace_event(s: Session, event_id: int) -> dict | None:
    """Recorrido completo de un cambio: fuente -> detección -> procesamiento -> asignación -> alertas -> entregas."""
    ev = s.get(ChangeEvent, event_id)
    if ev is None:
        return None
    src = s.get(Source, ev.source_id)
    t0 = ev.observed_at or ev.detected_at

    def step(name: str, t: datetime | None, **extra) -> dict:
        return {"paso": name, "en": t.isoformat() + "Z" if t else None,
                "desde_el_cambio_ms": round(_ms(t0, t), 1) if t and t0 else None, **extra}
    steps = [step("cambio en la fuente", ev.observed_at, fuente=src.code if src else None,
                  conector=src.connector_type if src else None),
             step("detectado por el motor", ev.detected_at, tipo=ev.change_type, antes=ev.before.get("available"),
                  despues=ev.after.get("available")),
             step("procesado", ev.processed_at, pendiente=not ev.processed)]
    for a in s.scalars(select(Assignment).where(Assignment.change_event_id == ev.id).order_by(Assignment.id)):
        steps.append(step("asignación", a.created_at, asignacion=a.id, solicitud=a.request_id, operador=a.operator_id,
                          estado=a.status, motivo=a.reason or None))
    for al in s.scalars(select(Alert).where(Alert.change_event_id == ev.id).order_by(Alert.id)):
        steps.append(step("alerta", al.created_at, alerta=al.id, tipo=al.kind, titulo=al.title))
        for d, ch in s.execute(select(Delivery, Channel).join(Channel, Channel.id == Delivery.channel_id)
                               .where(Delivery.alert_id == al.id).order_by(Delivery.id)):
            steps.append(step(f"entrega por {ch.name}", d.delivered_at, canal=ch.type, estado=d.status,
                              intentos=d.attempts, error=d.last_error))
    return {"evento": ev.id, "recurso": ev.resource_name, "region": ev.region, "pasos": steps}

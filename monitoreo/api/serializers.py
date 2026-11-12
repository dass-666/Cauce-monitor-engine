from __future__ import annotations

from datetime import datetime

SECRET_KEYS = ("secret", "token", "password", "clave")


def iso(d: datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


def mask(cfg: dict | None) -> dict:
    """Nunca devolver secretos al navegador."""
    out = {}
    for k, v in (cfg or {}).items():
        if any(x in k.lower() for x in SECRET_KEYS) and not k.lower().endswith("_env"):
            out[k] = "********" if v else ""
        else:
            out[k] = v
    return out


def source(s) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "region": s.region, "category": s.category,
            "availability_type": s.availability_type, "connector_type": s.connector_type, "config": mask(s.config),
            "status": s.status, "interval_s": s.interval_s, "jitter_pct": s.jitter_pct,
            "rate_limit_per_min": s.rate_limit_per_min, "max_retries": s.max_retries,
            "circuit_state": s.circuit_state, "consecutive_failures": s.consecutive_failures,
            "next_poll_at": iso(s.next_poll_at), "last_polled_at": iso(s.last_polled_at),
            "last_success_at": iso(s.last_success_at), "last_result": s.last_result, "last_error": s.last_error,
            "metrics": {"consultas": s.total_polls, "errores": s.total_errors, "cambios": s.total_changes,
                        "latencia_media_ms": round(s.avg_latency_ms, 2),
                        "tasa_error": round(s.total_errors / s.total_polls, 4) if s.total_polls else 0}}


def poll(p) -> dict:
    return {"id": p.id, "source_id": p.source_id, "started_at": iso(p.started_at), "duration_ms": round(p.duration_ms, 2),
            "outcome": p.outcome, "attempts": p.attempts, "items": p.items, "changes": p.changes,
            "duplicates": p.duplicates, "unchanged": p.unchanged, "error": p.error}


def event(e) -> dict:
    return {"id": e.id, "event_key": e.event_key[:16], "source_id": e.source_id, "region": e.region,
            "resource_key": e.resource_key, "resource_name": e.resource_name, "resource_type": e.resource_type,
            "change_type": e.change_type, "before": e.before, "after": e.after, "relevant": e.relevant,
            "processed": e.processed, "observed_at": iso(e.observed_at), "detected_at": iso(e.detected_at),
            "detection_latency_ms": round(e.detection_latency_ms, 1) if e.detection_latency_ms is not None else None}


def request(r, operator_name: str | None = None) -> dict:
    return {"id": r.id, "code": r.code, "holder": r.holder, "region": r.region, "resource_type": r.resource_type,
            "priority": r.priority, "quantity": r.quantity, "conditions": r.conditions, "status": r.status,
            "operator_id": r.operator_id, "operator_name": operator_name, "attempts": r.attempts,
            "last_error": r.last_error, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}


def assignment(a, slot=None, req=None, op=None) -> dict:
    return {"id": a.id, "request_id": a.request_id, "request_code": req.code if req else None,
            "slot_id": a.slot_id, "resource": slot.name if slot else None, "region": slot.region if slot else None,
            "operator_id": a.operator_id, "operator_name": op.name if op else None, "quantity": a.quantity,
            "status": a.status, "created_at": iso(a.created_at), "expires_at": iso(a.expires_at),
            "closed_at": iso(a.closed_at), "reason": a.reason}


def alert(a) -> dict:
    return {"id": a.id, "kind": a.kind, "title": a.title, "source_code": a.source_code, "region": a.region,
            "resource": a.resource, "priority": a.priority, "operator_name": a.operator_name,
            "request_id": a.request_id, "status": a.status, "payload": a.payload, "created_at": iso(a.created_at),
            "acknowledged_at": iso(a.acknowledged_at), "acknowledged_by": a.acknowledged_by}


def operator(o) -> dict:
    return {"id": o.id, "name": o.name, "user_id": o.user_id, "regions": o.regions, "active": o.active,
            "max_concurrent": o.max_concurrent, "current_load": o.current_load,
            "last_assigned_at": iso(o.last_assigned_at)}


def channel(c) -> dict:
    return {"id": c.id, "name": c.name, "type": c.type, "config": mask(c.config), "enabled": c.enabled}


def slot(st) -> dict:
    return {"id": st.id, "source_id": st.source_id, "resource_key": st.resource_key, "name": st.name,
            "region": st.region, "resource_type": st.resource_type, "starts_at": iso(st.starts_at),
            "capacity": st.capacity, "available": st.available, "held": st.held, "status": st.status,
            "version": st.version, "last_seen_at": iso(st.last_seen_at)}


def sim_item(i) -> dict:
    return {"id": i.id, "feed": i.feed, "resource_key": i.resource_key, "name": i.name, "region": i.region,
            "resource_type": i.resource_type, "capacity": i.capacity, "available": i.available,
            "cancelled": i.cancelled, "starts_at": iso(i.starts_at), "changed_at": iso(i.changed_at)}

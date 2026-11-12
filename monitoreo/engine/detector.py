"""Motor de detección de cambios.

Compara el último estado conocido de cada recurso (tabla slot_states) con lo
que acaba de devolver la fuente, y produce *eventos de cambio* solo cuando
algo relevante cambió. Una consulta sin cambios no produce eventos ni alertas.

Idempotencia: cada evento tiene un `event_key` estable, calculado a partir de
(fuente, recurso, tipo de cambio, versión anterior, huella nueva). Procesar dos
veces la misma transición genera la misma clave y la restricción UNIQUE de la
base de datos impide el duplicado.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contracts import Observation, Snapshot
from ..models import ChangeEvent, SlotState, Source

# Tipos de cambio
NUEVA = "NUEVA_DISPONIBILIDAD"
AGOTADO = "AGOTADO"
HORARIO = "CAMBIO_HORARIO"
AUMENTO = "AUMENTO"
REDUCCION = "REDUCCION"
CANCELACION = "CANCELACION"
REAPARICION = "REAPARICION"
NUEVO_RECURSO = "NUEVO_RECURSO"  # recurso nuevo pero sin disponibilidad (no alerta)

ALL_TYPES = [NUEVA, AGOTADO, HORARIO, AUMENTO, REDUCCION, CANCELACION, REAPARICION, NUEVO_RECURSO]
# Tipos que significan "hay unidades libres": disparan la búsqueda de solicitudes.
SUPPLY_TYPES = {NUEVA, AUMENTO, REAPARICION}


@dataclass
class DetectionResult:
    events: list[ChangeEvent] = field(default_factory=list)
    items: int = 0
    duplicates: int = 0
    unchanged: int = 0


def event_key(source_id: int, resource_key: str, change_type: str, prev_version: int, new_fp: str) -> str:
    raw = f"{source_id}|{resource_key}|{change_type}|{prev_version}|{new_fp}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _state_dict(st: SlotState | None) -> dict:
    if st is None:
        return {}
    return {"available": st.available, "capacity": st.capacity, "status": st.status,
            "starts_at": st.starts_at.isoformat() if st.starts_at else None}


def _obs_dict(o: Observation) -> dict:
    return {"available": o.available, "capacity": o.capacity, "status": o.status,
            "starts_at": o.starts_at.isoformat() if o.starts_at else None}


def classify(prev: SlotState | None, o: Observation) -> list[str]:
    """Reglas de clasificación (función pura, fácil de probar)."""
    if prev is None:
        if o.status == "CANCELADO":
            return []
        return [NUEVA] if o.available > 0 else [NUEVO_RECURSO]
    types: list[str] = []
    if o.status == "CANCELADO":
        return [CANCELACION] if prev.status != "CANCELADO" else []
    if prev.status in ("CANCELADO", "DESAPARECIDO"):
        return [REAPARICION] if o.available > 0 else []
    if prev.starts_at != o.starts_at:
        types.append(HORARIO)
    if prev.available == 0 and o.available > 0:
        types.append(REAPARICION if prev.ever_available else NUEVA)
    elif prev.available > 0 and o.available == 0:
        types.append(AGOTADO)
    elif o.available > prev.available:
        types.append(AUMENTO)
    elif o.available < prev.available:
        types.append(REDUCCION)
    return types


def is_relevant(change_type: str, before: dict, after: dict, params: dict) -> bool:
    if change_type not in params.get("tipos_que_alertan", []):
        return False
    if change_type == AUMENTO:
        return (after.get("available", 0) - before.get("available", 0)) >= int(params.get("alerta_minimo_aumento", 1))
    return True


def detect(session: Session, source: Source, snapshot: Snapshot, now: datetime, params: dict) -> DetectionResult:
    res = DetectionResult()
    existing = {s.resource_key: s for s in session.scalars(select(SlotState).where(SlotState.source_id == source.id))}

    # 1) Duplicados dentro de la misma respuesta: se queda la última aparición.
    latest: dict[str, Observation] = {}
    for o in snapshot.observations:
        if o.resource_key in latest:
            res.duplicates += 1
        latest[o.resource_key] = o
    res.items = len(latest)

    def emit(st: SlotState | None, o_key: str, name: str, rtype: str, region: str, ctype: str,
             before: dict, after: dict, prev_version: int, new_fp: str, observed_at: datetime | None):
        key = event_key(source.id, o_key, ctype, prev_version, new_fp)
        if session.scalar(select(ChangeEvent.id).where(ChangeEvent.event_key == key)) is not None:
            res.duplicates += 1  # transición ya registrada (reprocesamiento)
            return
        lat = (now - observed_at).total_seconds() * 1000 if observed_at else None
        ev = ChangeEvent(event_key=key, source_id=source.id, slot_id=st.id if st else None, region=region,
                         resource_key=o_key, resource_type=rtype, resource_name=name, change_type=ctype,
                         before=before, after=after, relevant=is_relevant(ctype, before, after, params),
                         observed_at=observed_at, detected_at=now,
                         detection_latency_ms=lat if lat is None or lat >= 0 else 0, processed=False)
        session.add(ev)
        res.events.append(ev)

    # 2) Recursos presentes en la respuesta
    for key, o in latest.items():
        st = existing.get(key)
        fp = o.fingerprint()
        region = o.region or source.region
        if st is not None and st.fingerprint == fp and st.status != "DESAPARECIDO":
            res.unchanged += 1  # resultado repetido: no hay cambio, no hay alerta
            st.last_seen_at = now
            st.missing_count = 0
            continue
        before = _state_dict(st)
        types = classify(st, o)
        prev_version = st.version if st else 0
        if st is None:
            st = SlotState(source_id=source.id, resource_key=key, region=region, resource_type=o.resource_type,
                           name=o.name, starts_at=o.starts_at, capacity=o.capacity, available=o.available,
                           held=0, status=o.status, fingerprint=fp, version=1, ever_available=o.available > 0,
                           missing_count=0, first_seen_at=now, last_seen_at=now, attributes=o.attributes)
            session.add(st)
            session.flush()  # obtiene st.id para enlazar el evento
        else:
            st.region, st.resource_type, st.name = region, o.resource_type, o.name
            st.starts_at, st.capacity, st.available, st.status = o.starts_at, o.capacity, o.available, o.status
            st.fingerprint, st.version = fp, st.version + 1
            st.ever_available = st.ever_available or o.available > 0
            st.missing_count, st.last_seen_at, st.attributes = 0, now, o.attributes
        after = _obs_dict(o)
        if st.held > st.available:
            after["conflicto_retenidas"] = st.held
        for t in types:
            emit(st, key, o.name, o.resource_type, region, t, before, after, prev_version, fp, o.changed_at)

    # 3) Recursos que desaparecieron (solo si la respuesta es completa)
    if snapshot.complete:
        threshold = int(params.get("ausencias_para_cancelar", 2))
        for key, st in existing.items():
            if key in latest or st.status in ("CANCELADO", "DESAPARECIDO"):
                continue
            st.missing_count += 1
            if st.missing_count >= threshold:
                before = _state_dict(st)
                prev_version = st.version
                st.status, st.available, st.version = "DESAPARECIDO", 0, st.version + 1
                st.fingerprint = hashlib.sha256(f"desaparecido|{st.version}".encode()).hexdigest()
                after = {**_state_dict(st), "motivo": f"ausente en {st.missing_count} consultas completas"}
                emit(st, key, st.name, st.resource_type, st.region, CANCELACION, before, after,
                     prev_version, st.fingerprint, None)
    session.flush()
    return res

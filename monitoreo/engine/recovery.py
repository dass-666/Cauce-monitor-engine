"""Recuperación al arrancar (o al tomar el relevo de otra instancia).

Todo el estado operativo vive en la base de datos, así que "recuperar" consiste en
devolver a la cola lo que quedó a medias y dejar constancia de qué se encontró:

- Entregas de alertas en 'ENVIANDO' -> 'PENDIENTE' (entrega al menos una vez; el receptor
  deduplica con X-Id-Alerta / el emulador y Telegram reciben el mismo id en el texto).
- Eventos sin procesar -> se procesan en el siguiente ciclo (outbox, idempotente).
- Asignaciones vencidas sin tomar -> expiran en el siguiente mantenimiento y vuelven a la cola.
- Solicitudes EN_PROCESO -> se respetan (las está atendiendo una persona).
- Fuentes suspendidas, en ERROR o con circuito abierto -> se respetan tal cual.
- Esperas por limitación (Retry-After) y retrocesos -> se respetan: next_poll_at está en BD.
- Simulaciones aceleradas a medias -> INTERRUMPIDA.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update

from ..db import Database
from ..models import (Assignment, ChangeEvent, Delivery, MaintenanceLog, Request, Setting, SimRun, Source)
from . import alerts as al


def run_recovery(db: Database, now: datetime, holder: str = "", takeover_stale_s: float = 0) -> dict:
    reset = al.recover_inflight(db, now, stale_s=takeover_stale_s)
    with db.session() as s:
        interrupted = s.execute(update(SimRun).where(SimRun.status == "EN_CURSO")
                                .values(status="INTERRUMPIDA")).rowcount if not takeover_stale_s else 0

        def c(model, *w):
            return s.scalar(select(func.count()).select_from(model).where(*w))
        report = {
            "momento": now.isoformat() + "Z", "instancia": holder,
            "entregas_reencoladas": reset,
            "entregas_pendientes": c(Delivery, Delivery.status == "PENDIENTE"),
            "eventos_sin_procesar": c(ChangeEvent, ChangeEvent.processed.is_(False)),
            "asignaciones_activas": c(Assignment, Assignment.status == "ACTIVA"),
            "asignaciones_vencidas_por_expirar": c(Assignment, Assignment.status == "ACTIVA",
                                                   Assignment.expires_at <= now),
            "solicitudes_en_proceso": c(Request, Request.status == "EN_PROCESO"),
            "fuentes_suspendidas": c(Source, Source.status == "SUSPENDIDA"),
            "fuentes_en_error": c(Source, Source.status == "ERROR"),
            "fuentes_circuito_abierto": c(Source, Source.circuit_state != "CERRADO"),
            "fuentes_en_espera_por_limitacion_o_retroceso": c(Source, Source.status == "ACTIVA",
                                                              Source.next_poll_at > now),
            "simulaciones_interrumpidas": interrupted,
        }
        row = s.get(Setting, "recuperacion.ultima")
        if row is None:
            s.add(Setting(key="recuperacion.ultima", value=report))
        else:
            row.value = report
        s.add(MaintenanceLog(ts=now, task="recuperacion", holder=holder, detail=report))
    return report

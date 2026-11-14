"""v2.1 · P3 · Motor de coincidencias: una fecha disponible -> UN expediente del plan del día.

Regla determinista (la misma entrada da siempre el mismo resultado):
  1. Compatibles: elemento EN_ESPERA de un plan ABIERTO de la jornada, mismo trámite, la sucursal
     del día entre sus sucursales, la fecha entre sus fechas aceptables y expediente aún LISTO.
  2. Orden: prioridad (1 primero) -> orden de alta en el plan -> id del expediente.
  3. Se asigna el primero con un UPDATE condicional (`estado = 'EN_ESPERA'`); si otro evento lo
     tomó en ese instante, se intenta el siguiente. Los demás compatibles SIGUEN EN ESPERA.

Garantías:
- Un evento se atiende una sola vez: `NUEVO -> EN_PROCESO` es un UPDATE condicional.
- Un expediente no se consume dos veces: el UPDATE condicional del elemento y, además,
  `activo_expediente_id` UNIQUE impiden que esté vivo en dos elementos.
- Todo ocurre en UNA transacción: o queda asignado con su explicación, o no cambia nada.
- Si el día ya no está DISPONIBLE al atender el evento, no se asigna nada (DESAPARECIDO).
- Asignar NO reserva nada ni habla con el portal: deja la operación lista para que una persona la
  lleve por el flujo normal (campos manuales, confirmación humana, folio verificado por API).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select, update

from ..captura import expedientes as ex
from ..captura import zona
from ..models import Expediente, ObservacionDia, ObservacionEvento, PlanDiario, PlanElemento
from . import plan as pl
from .observaciones import DIA_DISPONIBLE, NUEVO

log = logging.getLogger("monitoreo.planificador")
REGLA = "prioridad (1 primero) -> orden de alta en el plan -> id del expediente"
ASIGNADO_EV, SIN_CANDIDATOS, DESAPARECIDO, EN_PROCESO = "ASIGNADO", "SIN_CANDIDATOS", "DESAPARECIDO", "EN_PROCESO"


def _motivo(el: PlanElemento, exp: Expediente | None, ev: ObservacionEvento) -> str | None:
    if el.estado != pl.EN_ESPERA:
        return f"ya está {el.estado}"
    if el.tramite != ev.tramite:
        return f"otro trámite ({el.tramite})"
    if ev.sucursal not in (el.sucursales or []):
        return "no acepta esa sucursal"
    if ev.fecha not in (el.fechas_aceptables or []):
        return "no acepta esa fecha"
    m = pl.motivo_inelegible(exp)
    if m:
        return m
    return None


def atender(db, evento_id: int, now: datetime, jornada: date | None = None) -> dict:
    with db.session() as s:
        t = ObservacionEvento.__table__
        if s.execute(update(t).where(t.c.id == evento_id, t.c.consumo == NUEVO, t.c.tipo == DIA_DISPONIBLE)
                     .values(consumo=EN_PROCESO)).rowcount != 1:
            e = s.get(ObservacionEvento, evento_id)
            return {"evento_id": evento_id, "asignado": False,
                    "motivo": "evento inexistente" if e is None else f"el evento ya está {e.consumo}"}
        ev = s.get(ObservacionEvento, evento_id)
        dia = s.get(ObservacionDia, ev.clave)
        jornada = jornada or zona.hoy(now)
        base = {"evento": ev.id, "dia": {"fecha": ev.fecha, "sucursal": ev.sucursal, "tramite": ev.tramite},
                "jornada": jornada.isoformat(), "regla": REGLA, "atendido_en": now.isoformat() + "Z"}
        if dia is None or dia.estado != "DISPONIBLE":
            ev.consumo = DESAPARECIDO
            ev.explicacion = {**base, "motivo": "el día ya no estaba disponible al atender el evento",
                              "estado_actual": dia.estado if dia else None}
            return {"evento_id": ev.id, "asignado": False, "motivo": ev.explicacion["motivo"]}
        planes = [p.id for p in s.scalars(select(PlanDiario).where(PlanDiario.estado == "ABIERTO",
                                                                    PlanDiario.fecha_jornada == jornada))]
        els = list(s.scalars(select(PlanElemento).where(PlanElemento.plan_id.in_(planes),
                                                        PlanElemento.estado.in_(pl.VIVOS)))) if planes else []
        exps = {x.id: x for x in s.scalars(select(Expediente).where(
            Expediente.id.in_([el.expediente_id for el in els])))} if els else {}
        compatibles, descartados = [], []
        for el in els:
            m = _motivo(el, exps.get(el.expediente_id), ev)
            fila = {"elemento_id": el.id, "codigo": exps[el.expediente_id].codigo if el.expediente_id in exps else None,
                    "prioridad": el.prioridad, "orden": el.orden}
            if m:
                descartados.append({**fila, "motivo": m})
            else:
                compatibles.append((el.prioridad, el.orden, el.expediente_id, el.id, fila))
        compatibles.sort()
        pe = PlanElemento.__table__
        elegido = None
        for _, _, _, el_id, fila in compatibles:
            ok = s.execute(update(pe).where(pe.c.id == el_id, pe.c.estado == pl.EN_ESPERA).values(
                estado=pl.ASIGNADO, evento_id=ev.id, fecha_asignada=ev.fecha, sucursal_asignada=ev.sucursal,
                version=pe.c.version + 1, updated_at=now)).rowcount
            if ok == 1:
                elegido = fila
                break
            descartados.append({**fila, "motivo": "lo tomó otro evento en ese instante"})
        en_espera = [f for *_, f in compatibles if elegido is None or f["elemento_id"] != elegido["elemento_id"]]
        en_espera = [f for f in en_espera if not any(d["elemento_id"] == f["elemento_id"] for d in descartados)]
        expl = {**base, "elegido": elegido, "compatibles_en_espera": en_espera, "descartados": descartados,
                "por_que": (f"prioridad {elegido['prioridad']}, alta n.º {elegido['orden']} en el plan; "
                            f"{len(en_espera)} compatible(s) más quedan en espera") if elegido else
                           "ningún expediente del plan acepta ese día"}
        ev.explicacion = expl
        if elegido is None:
            ev.consumo = SIN_CANDIDATOS
            return {"evento_id": ev.id, "asignado": False, "motivo": expl["por_que"], "explicacion": expl}
        ev.consumo, ev.plan_elemento_id = ASIGNADO_EV, elegido["elemento_id"]
        el = s.get(PlanElemento, elegido["elemento_id"])
        s.refresh(el)
        el.explicacion = expl
        ex.evento(s, el.expediente_id, "PLAN_COINCIDENCIA", "motor", now, fecha=ev.fecha, sucursal=ev.sucursal,
                  evento_observacion=ev.id, prioridad=el.prioridad)
    log.info("coincidencia: evento %s (%s %s) -> elemento %s (%s)", evento_id, ev.sucursal, ev.fecha,
             elegido["elemento_id"], elegido["codigo"])
    return {"evento_id": evento_id, "asignado": True, "elemento_id": elegido["elemento_id"], "explicacion": expl}


def atender_pendientes(db, now: datetime, jornada: date | None = None, limite: int = 100) -> list[dict]:
    with db.session() as s:
        ids = list(s.scalars(select(ObservacionEvento.id).where(ObservacionEvento.tipo == DIA_DISPONIBLE,
                                                                ObservacionEvento.consumo == NUEVO)
                             .order_by(ObservacionEvento.id).limit(limite)))
    return [atender(db, i, now, jornada) for i in ids]


def liberar_desaparecidos(db, now: datetime) -> list[int]:
    """Elementos ASIGNADOS (aún sin operación preparada) cuyo día dejó de estar DISPONIBLE: vuelven a
    EN_ESPERA con su explicación. Se conserva el expediente en la lista del día."""
    vueltos = []
    with db.session() as s:
        for el in s.scalars(select(PlanElemento).where(PlanElemento.estado == pl.ASIGNADO)):
            clave = f"{el.sucursal_asignada}|{el.tramite}|{el.fecha_asignada}"
            dia = s.get(ObservacionDia, clave)
            if dia is not None and dia.estado != "DISPONIBLE":
                pe = PlanElemento.__table__
                if s.execute(update(pe).where(pe.c.id == el.id, pe.c.estado == pl.ASIGNADO, pe.c.version == el.version)
                             .values(estado=pl.EN_ESPERA, evento_id=None, fecha_asignada=None, sucursal_asignada=None,
                                     version=pe.c.version + 1, updated_at=now,
                                     explicacion={**(el.explicacion or {}), "devuelto": {
                                         "motivo": f"el día {el.fecha_asignada} dejó de estar disponible ({dia.estado}) "
                                                   "antes de preparar la operación", "en": now.isoformat() + "Z"}})
                             ).rowcount:
                    ex.evento(s, el.expediente_id, "PLAN_DEVUELTO_A_ESPERA", "motor", now, motivo="DIA_DESAPARECIO")
                    vueltos.append(el.id)
    return vueltos

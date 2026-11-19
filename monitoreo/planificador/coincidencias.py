"""v2.1 · P3 · Motor de coincidencias: unidades disponibles de una fecha -> expedientes del plan del día.

Regla determinista (la misma entrada da siempre el mismo resultado):
  1. Compatibles: elemento EN_ESPERA de un plan ABIERTO de la jornada, mismo trámite, la sucursal
     del día entre sus sucursales, la fecha entre sus fechas aceptables y expediente aún LISTO.
  2. Orden: prioridad (1 primero) -> orden de alta en el plan -> id del expediente.
  3. Se asignan tantos como CUPO haya, en ese orden, cada uno con un UPDATE condicional
     (`estado = 'EN_ESPERA'`); si otro evento lo tomó en ese instante, se intenta el siguiente.
     Los demás compatibles SIGUEN EN ESPERA.

Unidades (v2.1): cupo = unidades libres que muestra la página (1 si solo se vio el color) menos las
COMPROMETIDAS de ese día: elementos ASIGNADOS o con operación preparada, más los ya confirmados cuya
confirmación el observador todavía no reflejó (la página aún los cuenta como libres). Todo bajo el
candado de la fila del día (`observacion_dias`): dos eventos del mismo día se atienden en serie, así
que la suma de asignaciones nunca supera las unidades vistas. Si las unidades bajan, `ajustar()`
devuelve a la espera los ASIGNADOS sobrantes (los de menor prioridad primero). Una operación ya
preparada no se retira sola: si sobra, el portal la rechazará al confirmar y vuelve a la espera.

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

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..captura import expedientes as ex
from ..captura import zona
from ..models import Expediente, ObservacionDia, ObservacionEvento, PlanDiario, PlanElemento
from . import plan as pl
from ..captura import reservaciones as rs
from ..models import Reservacion
from .observaciones import NUEVO, OPORTUNIDADES, REEVALUACION, _huella, efectivas

log = logging.getLogger("monitoreo.planificador")
REGLA = "prioridad (1 primero) -> orden de alta en el plan -> id del expediente"
ASIGNADO_EV, SIN_CANDIDATOS, DESAPARECIDO, EN_PROCESO = "ASIGNADO", "SIN_CANDIDATOS", "DESAPARECIDO", "EN_PROCESO"
SIN_CUPO = "SIN_CUPO"
INCIERTAS = (rs.CONFIRMANDO, rs.EN_VERIFICACION)


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


def _bloquear_dia(s, clave: str) -> None:
    """Candado de la fila del día (UPDATE sin cambio): serializa a quien asigna o libera ese día."""
    t = ObservacionDia.__table__
    s.execute(update(t).where(t.c.clave == clave).values(version=t.c.version))


def _de_la_clave(clave: str):
    suc, tram, fecha = clave.split("|", 2)
    return (PlanElemento.sucursal_asignada == suc, PlanElemento.tramite == tram, PlanElemento.fecha_asignada == fecha)


def comprometidas(s, clave: str, dia: ObservacionDia | None) -> list[PlanElemento]:
    """Elementos que ocupan (o pueden ocupar) una de las unidades que la página muestra libres."""
    out = []
    for el in s.scalars(select(PlanElemento).where(*_de_la_clave(clave),
                                                   PlanElemento.estado.in_([pl.ASIGNADO, pl.PREPARADO, pl.CONFIRMADO]))):
        res = s.get(Reservacion, el.reservacion_id) if el.reservacion_id else None
        if el.estado == pl.ASIGNADO or res is None or res.estado in rs.VIVAS or res.estado in INCIERTAS:
            if el.estado != pl.CONFIRMADO:
                out.append(el)          # ASIGNADO, preparada o confirmación incierta: cuenta (prudente)
            continue
        if res.estado == rs.CONFIRMADA:
            # Ya consumió una unidad en el portal. La página la sigue contando como libre hasta que el
            # observador registra el cambio: cuenta solo si se confirmó DESPUÉS del último cambio visto.
            if dia is None or res.updated_at is None or res.updated_at > dia.actualizado_en:
                out.append(el)
    return out


def _clave_orden(el: PlanElemento):
    return (el.prioridad, el.orden, el.expediente_id, el.id)


def atender(db, evento_id: int, now: datetime, jornada: date | None = None) -> dict:
    with db.session() as s:
        ev = s.get(ObservacionEvento, evento_id)
        if ev is None:
            return {"evento_id": evento_id, "asignado": False, "motivo": "evento inexistente"}
        if ev.tipo in OPORTUNIDADES:
            _bloquear_dia(s, ev.clave)        # primero el día, luego el evento (mismo orden que el registro)
        t = ObservacionEvento.__table__
        if s.execute(update(t).where(t.c.id == evento_id, t.c.consumo == NUEVO, t.c.tipo.in_(list(OPORTUNIDADES)))
                     .values(consumo=EN_PROCESO)).rowcount != 1:
            s.refresh(ev)
            return {"evento_id": evento_id, "asignado": False, "motivo": f"el evento ya está {ev.consumo}"}
        s.refresh(ev)
        dia = s.get(ObservacionDia, ev.clave)
        if dia is not None:
            s.refresh(dia)
        jornada = jornada or zona.hoy(now)
        base = {"evento": ev.id, "tipo_evento": ev.tipo,
                "dia": {"fecha": ev.fecha, "sucursal": ev.sucursal, "tramite": ev.tramite},
                "jornada": jornada.isoformat(), "regla": REGLA, "atendido_en": now.isoformat() + "Z"}
        if dia is None or dia.estado != "DISPONIBLE":
            ev.consumo = DESAPARECIDO
            ev.explicacion = {**base, "motivo": "el día ya no estaba disponible al atender el evento",
                              "estado_actual": dia.estado if dia else None}
            return {"evento_id": ev.id, "asignado": False, "motivo": ev.explicacion["motivo"]}
        comp = comprometidas(s, ev.clave, dia)
        libres = efectivas(dia.libres)
        cupo = max(0, libres - len(comp))
        unidades = {"libres_vistas": dia.libres, "con_las_que_se_cuenta": libres, "comprometidas": len(comp),
                    "cupo": cupo, "comprometidas_ids": [x.id for x in comp]}
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
                compatibles.append((_clave_orden(el), fila))
        compatibles.sort(key=lambda x: x[0])
        pe = PlanElemento.__table__
        elegidos = []
        for _, fila in compatibles:
            if len(elegidos) >= cupo:
                break
            ok = s.execute(update(pe).where(pe.c.id == fila["elemento_id"], pe.c.estado == pl.EN_ESPERA).values(
                estado=pl.ASIGNADO, evento_id=ev.id, fecha_asignada=ev.fecha, sucursal_asignada=ev.sucursal,
                version=pe.c.version + 1, updated_at=now)).rowcount
            if ok == 1:
                elegidos.append(fila)
            else:
                descartados.append({**fila, "motivo": "lo tomó otro evento en ese instante"})
        ids = {f["elemento_id"] for f in elegidos} | {d["elemento_id"] for d in descartados}
        en_espera = [f for _, f in compatibles if f["elemento_id"] not in ids]
        if elegidos:
            por_que = (f"{len(elegidos)} de {cupo} unidad(es) con cupo: "
                       + "; ".join(f"{f['codigo']} (prioridad {f['prioridad']}, alta n.º {f['orden']})" for f in elegidos)
                       + f"; {len(en_espera)} compatible(s) más quedan en espera")
        elif compatibles and cupo == 0:
            por_que = f"las {libres} unidad(es) del día ya están comprometidas con {len(comp)} expediente(s)"
        else:
            por_que = "ningún expediente del plan acepta ese día"
        expl = {**base, "unidades": unidades, "elegidos": elegidos, "elegido": elegidos[0] if elegidos else None,
                "compatibles_en_espera": en_espera, "descartados": descartados, "por_que": por_que}
        ev.explicacion = expl
        if not elegidos:
            ev.consumo = SIN_CUPO if compatibles and cupo == 0 else SIN_CANDIDATOS
            return {"evento_id": ev.id, "asignado": False, "motivo": por_que, "explicacion": expl}
        ev.consumo, ev.plan_elemento_id = ASIGNADO_EV, elegidos[0]["elemento_id"]
        for f in elegidos:
            el = s.get(PlanElemento, f["elemento_id"])
            s.refresh(el)
            el.explicacion = {**expl, "elegido": f}
            ex.evento(s, el.expediente_id, "PLAN_COINCIDENCIA", "motor", now, fecha=ev.fecha, sucursal=ev.sucursal,
                      evento_observacion=ev.id, prioridad=el.prioridad, unidades=dia.libres)
    log.info("coincidencia: evento %s (%s %s, cupo %s) -> %s", evento_id, ev.sucursal, ev.fecha, cupo,
             [f["codigo"] for f in elegidos])
    return {"evento_id": evento_id, "asignado": True, "elemento_id": elegidos[0]["elemento_id"],
            "elementos": [f["elemento_id"] for f in elegidos], "explicacion": expl}


def atender_pendientes(db, now: datetime, jornada: date | None = None, limite: int = 100) -> list[dict]:
    with db.session() as s:
        ids = list(s.scalars(select(ObservacionEvento.id).where(ObservacionEvento.tipo.in_(list(OPORTUNIDADES)),
                                                                ObservacionEvento.consumo == NUEVO)
                             .order_by(ObservacionEvento.id).limit(limite)))
    return [atender(db, i, now, jornada) for i in ids]


def ajustar(db, clave: str, now: datetime) -> dict:
    """Las unidades del día bajaron (o desapareció): devuelve a la espera los ASIGNADOS que sobran,
    los de MENOR prioridad primero. Las operaciones ya preparadas no se tocan (las resuelve el portal
    al confirmar); si aun así sobran, se informa como sobrecompromiso."""
    vueltos = []
    with db.session() as s:
        _bloquear_dia(s, clave)
        dia = s.get(ObservacionDia, clave)
        if dia is None:
            return {"clave": clave, "devueltos": [], "sobrecompromiso": 0}
        s.refresh(dia)
        libres = efectivas(dia.libres, dia.estado)
        comp = comprometidas(s, clave, dia)
        exceso = len(comp) - libres
        if exceso > 0:
            asignados = sorted((x for x in comp if x.estado == pl.ASIGNADO), key=_clave_orden, reverse=True)
            pe = PlanElemento.__table__
            for el in asignados[:exceso]:
                motivo = (f"el día {el.fecha_asignada} dejó de estar disponible ({dia.estado})" if dia.estado != "DISPONIBLE"
                          else f"las unidades del día bajaron a {dia.libres} y este expediente era el de menor prioridad")
                if s.execute(update(pe).where(pe.c.id == el.id, pe.c.estado == pl.ASIGNADO, pe.c.version == el.version)
                             .values(estado=pl.EN_ESPERA, evento_id=None, fecha_asignada=None, sucursal_asignada=None,
                                     version=pe.c.version + 1, updated_at=now,
                                     explicacion={**(el.explicacion or {}), "devuelto": {
                                         "motivo": motivo + " antes de preparar la operación", "en": now.isoformat() + "Z"}})
                             ).rowcount:
                    ex.evento(s, el.expediente_id, "PLAN_DEVUELTO_A_ESPERA", "motor", now,
                              motivo="DIA_DESAPARECIO" if dia.estado != "DISPONIBLE" else "UNIDADES_REDUCIDAS")
                    vueltos.append(el.id)
        sobre = max(0, exceso - len(vueltos))
        if sobre:
            log.warning("día %s: %s operación(es) preparada(s) más que unidades libres (%s); el portal decidirá",
                        clave, sobre, dia.libres)
    return {"clave": clave, "devueltos": vueltos, "sobrecompromiso": sobre}


def liberar_desaparecidos(db, now: datetime) -> list[int]:
    """Ajusta todos los días con elementos asignados (lo llama el mantenimiento y el observador)."""
    with db.session() as s:
        claves = {f"{su}|{tr}|{fe}" for su, tr, fe in s.execute(
            select(PlanElemento.sucursal_asignada, PlanElemento.tramite, PlanElemento.fecha_asignada)
            .where(PlanElemento.estado == pl.ASIGNADO)).all()}
    vueltos = []
    for c in sorted(claves):
        vueltos += ajustar(db, c, now)["devueltos"]
    return vueltos


def reevaluar(db, now: datetime, jornada: date | None = None) -> list[dict]:
    """Una unidad ya vista volvió a quedar libre (operación sin cita, devolución, unidades que suben
    sin cambio de color...) y hay compatibles en espera: se crea un evento REEVALUACION y se atiende.
    Sin evento nuevo del observador, nada más volvería a mirar ese día."""
    jornada = jornada or zona.hoy(now)
    hoy = zona.hoy(now).isoformat()
    creados = []
    with db.session() as s:
        planes = [p.id for p in s.scalars(select(PlanDiario).where(PlanDiario.estado == "ABIERTO",
                                                                    PlanDiario.fecha_jornada == jornada))]
        espera = list(s.scalars(select(PlanElemento).where(PlanElemento.plan_id.in_(planes),
                                                           PlanElemento.estado == pl.EN_ESPERA))) if planes else []
        if not espera:
            return []
        pendientes = set(s.scalars(select(ObservacionEvento.clave).where(
            ObservacionEvento.tipo.in_(list(OPORTUNIDADES)), ObservacionEvento.consumo.in_([NUEVO, EN_PROCESO]))))
        for dia in s.scalars(select(ObservacionDia).where(ObservacionDia.estado == "DISPONIBLE",
                                                          ObservacionDia.fecha > hoy)):
            if dia.clave in pendientes:
                continue
            if not any(el.tramite == dia.tramite and dia.sucursal in (el.sucursales or []) and
                       dia.fecha in (el.fechas_aceptables or []) for el in espera):
                continue
            n = len(comprometidas(s, dia.clave, dia))
            if efectivas(dia.libres) - n <= 0:
                continue
            creados.append((dia.clave, dia.sucursal, dia.tramite, dia.fecha, dia.version, n, dia.libres))
    ids = []
    for clave, suc, tram, fecha, version, n, libres in creados:
        with db.session() as s:
            previas = s.scalar(select(func.count()).select_from(ObservacionEvento).where(
                ObservacionEvento.clave == clave, ObservacionEvento.tipo == REEVALUACION)) or 0
            e = ObservacionEvento(huella=_huella("reeval", clave, version, n, previas), tipo=REEVALUACION, clave=clave,
                                  sucursal=suc, tramite=tram, fecha=fecha, estado_anterior="DISPONIBLE",
                                  estado="DISPONIBLE", estrategia="motor", observador="motor", detectado_en=now,
                                  registrado_en=now, evidencia={"motivo": "unidad libre sin asignar y compatibles en espera"},
                                  detecciones={}, consumo=NUEVO, explicacion={}, unidades=libres)
            try:
                with s.begin_nested():
                    s.add(e)
                    s.flush()
                ids.append(e.id)
            except IntegrityError:
                continue
    return [atender(db, i, now, jornada) for i in ids]

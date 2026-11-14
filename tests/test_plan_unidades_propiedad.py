"""v2.1 · Varias unidades por fecha (P2) y una sola autoridad de asignación (P4).
Datos ficticios; portal de laboratorio propio (proceso local). SQLite y PostgreSQL."""
from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import select

from monitoreo.engine import assignment as asg
from monitoreo.models import (Assignment, Expediente, ObservacionDia, Operator, PlanElemento,
                              Request, SlotState)
from monitoreo.planificador import coincidencias, operacion, propiedad

from .test_captura_dia import lab, portal  # noqa: F401  (fixtures)
from .test_planificador import _elementos, _f, _listos, _plan, _prioridad, _ver


def _plan_con(lab, n, fechas, capacidad=None, prioridades=None):
    exps = _listos(lab, n, tipo_tramite="alta")
    for i, p in enumerate(prioridades or []):
        _prioridad(lab, exps[i]["id"], p)
    pid = _plan(lab, capacidad=capacidad or max(6, n))
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": fechas} for e in exps]}).json()
    assert len(r["aceptados"]) == n, r
    return pid, exps


def _estado_req(lab, exp_id):
    with lab.db.session() as s:
        e = s.get(Expediente, exp_id)
        return s.get(Request, e.request_id).status


def _asignados(lab, pid):
    return [e for e in _elementos(lab, pid).values() if e["estado"] == "ASIGNADO"]


# ================================================================ P2 · unidades
def test_varias_unidades_se_reparten_entre_compatibles_sin_exceder(lab):
    dia = _f(30)
    pid, exps = _plan_con(lab, 4, [dia], prioridades=[3, 1, 2, 4])
    nuevos = _ver(lab, fecha=dia, libres=3)
    assert len(nuevos) == 1 and nuevos[0]["unidades"] == 3
    out = coincidencias.atender(lab.db, nuevos[0]["id"], _hoy_dt())
    assert out["asignado"] and len(out["elementos"]) == 3, out["explicacion"]["descartados"]
    expl = out["explicacion"]
    assert expl["unidades"]["cupo"] == 3 and expl["unidades"]["comprometidas"] == 0
    els = _elementos(lab, pid)
    asign = {eid for eid, e in els.items() if e["estado"] == "ASIGNADO"}
    assert asign == {exps[1]["id"], exps[2]["id"], exps[0]["id"]}        # prioridades 1, 2, 3
    assert els[exps[3]["id"]]["estado"] == "EN_ESPERA"                     # la 4.ª queda en espera
    # la misma observación repetida (otra estrategia, otro observador) no crea otro evento
    assert _ver(lab, fecha=dia, libres=3) == [] and _ver(lab, fecha=dia, estrategia="visual") == []
    assert _ver(lab, fecha=dia, obs="otro", libres=3) == []
    # una unidad más: solo la 4.ª
    out2 = coincidencias.atender(lab.db, _ver(lab, fecha=dia, libres=4)[0]["id"], _hoy_dt())
    assert out2["elementos"] == [els[exps[3]["id"]]["id"]], out2


def _hoy_dt():
    from monitoreo.db import utcnow
    return utcnow()


def test_eventos_concurrentes_del_mismo_dia_no_superan_las_unidades(lab):
    dia = _f(31)
    pid, _ = _plan_con(lab, 6, [dia])
    ids = [_ver(lab, fecha=dia, libres=n)[0]["id"] for n in (1, 2, 3)]   # tres eventos NUEVO del mismo día
    res, barrera = [], threading.Barrier(len(ids) * 2)

    def go(i):
        barrera.wait()
        try:
            res.append(coincidencias.atender(lab.db, i, _hoy_dt()))
        except Exception as ex:  # noqa: BLE001 - se cuenta como fallo
            res.append({"asignado": False, "error": repr(ex)})
    hilos = [threading.Thread(target=go, args=(i,)) for i in ids * 2]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    assert not [x for x in res if x.get("error")], res
    assert len(_asignados(lab, pid)) == 3, (_asignados(lab, pid), res)       # 3 unidades, ni una más
    with lab.db.session() as s:
        exps = [el.expediente_id for el in s.scalars(select(PlanElemento).where(PlanElemento.estado == "ASIGNADO"))]
    assert len(exps) == len(set(exps))


def test_disminucion_de_unidades_devuelve_a_los_de_menor_prioridad(lab):
    dia = _f(32)
    pid, exps = _plan_con(lab, 3, [dia], prioridades=[2, 1, 3])
    coincidencias.atender(lab.db, _ver(lab, fecha=dia, libres=3)[0]["id"], _hoy_dt())
    assert len(_asignados(lab, pid)) == 3
    ev = _ver(lab, fecha=dia, libres=1)
    assert ev and ev[0]["tipo"] == "UNIDADES_REDUCIDAS" and ev[0]["unidades_anteriores"] == 3
    r = coincidencias.ajustar(lab.db, f"NOR-1|{exps[0]['tipo_tramite']}|{dia}", _hoy_dt())
    assert len(r["devueltos"]) == 2 and r["sobrecompromiso"] == 0
    els = _elementos(lab, pid)
    assert els[exps[1]["id"]]["estado"] == "ASIGNADO"                       # prioridad 1 conserva la unidad
    for i in (0, 2):
        assert els[exps[i]["id"]]["estado"] == "EN_ESPERA"
        assert "bajaron a 1" in els[exps[i]["id"]]["explicacion"]["devuelto"]["motivo"]
    # sube otra vez a 2: un evento nuevo y la siguiente en prioridad (2) vuelve a tener unidad
    out = coincidencias.atender(lab.db, _ver(lab, fecha=dia, libres=2)[0]["id"], _hoy_dt())
    assert out["elementos"] == [els[exps[0]["id"]]["id"]], out


def test_unidad_liberada_por_operacion_sin_cita_se_reofrece(lab):
    """Recuperación: una operación preparada termina sin cita -> el expediente vuelve AL PLAN (no a la
    cola general) y la unidad, que sigue libre en el portal, se reofrece con un evento REEVALUACION."""
    dia = _f(33)
    pid, exps = _plan_con(lab, 3, [dia], prioridades=[1, 2, 3])
    coincidencias.atender(lab.db, _ver(lab, fecha=dia, libres=2)[0]["id"], _hoy_dt())
    els = _elementos(lab, pid)
    primero = els[exps[0]["id"]]
    r = lab.c.post(f"/api/planificador/elementos/{primero['id']}/preparar", headers=lab.ops["op1"])
    assert r.status_code == 200, r.text
    assert _estado_req(lab, exps[0]["id"]) == "EN_VENTANILLA"
    assert coincidencias.reevaluar(lab.db, _hoy_dt()) == []                  # sin cupo libre: nada
    r = lab.c.post(f"/api/planificador/elementos/{primero['id']}/devolver", headers=lab.ops["op1"],
                   json={"motivo": "la persona no pudo"})
    assert r.status_code == 200 and r.json()["estado"] == "EN_ESPERA", r.text
    assert _estado_req(lab, exps[0]["id"]) == "EN_PLAN"                      # vuelve al plan, no a la cola
    with lab.db.session() as s:
        assert s.get(Expediente, exps[0]["id"]).estado == "LISTO"
    out = coincidencias.reevaluar(lab.db, _hoy_dt())
    assert len(out) == 1 and out[0]["asignado"] and out[0]["explicacion"]["tipo_evento"] == "REEVALUACION"
    assert out[0]["elementos"] == [primero["id"]]                            # prioridad 1 otra vez
    assert len(_asignados(lab, pid)) == 2 and coincidencias.reevaluar(lab.db, _hoy_dt()) == []


def test_confirmada_cuenta_hasta_que_el_observador_ve_la_baja(lab):
    """El portal descuenta la unidad al confirmar; la página lo refleja un instante después. Mientras
    tanto la confirmada sigue contando como comprometida (no se reparte de más) y, cuando llega la
    baja, no se devuelve a nadie por error."""
    from datetime import date
    tram = "alta"
    d = lab.portal.ctl("GET", "/control/dias")
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == tram}
    fecha = next(f for f in ((date.fromisoformat(d["hoy"]) + timedelta(days=i)).isoformat() for i in range(100, 118))
                 if f not in usadas)
    pid, exps = _plan_con(lab, 3, [fecha], prioridades=[1, 2, 3])
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite=tram, fecha=fecha, capacidad=2)
    coincidencias.atender(lab.db, _ver(lab, tramite=tram, fecha=fecha, libres=2)[0]["id"], _hoy_dt())
    els = _elementos(lab, pid)
    el1 = els[exps[0]["id"]]
    from .captura_util import MANUALES
    rid = lab.c.post(f"/api/planificador/elementos/{el1['id']}/preparar", headers=lab.ops["op1"]).json()["reservacion_id"]
    lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=lab.ops["op1"],
               json={"revisado": True, "manuales": dict(MANUALES), "sucursal": "NOR-1"})
    lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=lab.ops["op1"], json={"fecha": fecha, "sucursal": "NOR-1"})
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops["op1"], json={"confirmo": True})
    assert r.json()["reservacion"]["estado"] == "CONFIRMADA", r.text
    assert operacion.sincronizar(lab.db, _hoy_dt())["confirmados"] == 1
    clave = f"NOR-1|{tram}|{fecha}"
    # la página aún muestra 2 libres: confirmada + asignada = 2 comprometidas -> sin cupo
    assert coincidencias.reevaluar(lab.db, _hoy_dt()) == []
    with lab.db.session() as s:
        assert len(coincidencias.comprometidas(s, clave, s.get(ObservacionDia, clave))) == 2
    # llega la baja (2 -> 1): la confirmada deja de contar; la otra asignada conserva su unidad
    assert _ver(lab, tramite=tram, fecha=fecha, libres=1)[0]["tipo"] == "UNIDADES_REDUCIDAS"
    assert coincidencias.ajustar(lab.db, clave, _hoy_dt())["devueltos"] == []
    assert _elementos(lab, pid)[exps[1]["id"]]["estado"] == "ASIGNADO"
    assert _elementos(lab, pid)[exps[2]["id"]]["estado"] == "EN_ESPERA"


# ================================================================ P4 · propiedad
def _slot_para(lab, exp_id):
    with lab.db.session() as s:
        e = s.get(Expediente, exp_id)
        req = s.get(Request, e.request_id)
        sl = SlotState(source_id=lab.source_id, resource_key=f"prueba-{exp_id}", region=req.region,
                       resource_type=req.resource_type, name="recurso de prueba", available=5, held=0,
                       status="ACTIVO", fingerprint="x", attributes={})
        s.add(sl)
        s.flush()
        op = s.scalar(select(Operator.id).where(Operator.active.is_(True)).order_by(Operator.id))
        return sl.id, req.id, op


def test_la_cola_general_no_asigna_un_expediente_del_plan(lab):
    pid, (e,) = _plan_con(lab, 1, [_f(40)])
    assert _estado_req(lab, e["id"]) == "EN_PLAN"
    slot, req, op = _slot_para(lab, e["id"])
    with lab.db.session() as s:
        sl = s.get(SlotState, slot)
        assert req not in [r.id for r in asg.candidate_requests(s, sl, "portal-lab-dias", 5)]
    out = asg.try_assign(lab.db, slot, req, op, _hoy_dt(), 60)
    assert out.assignment_id is None and "ya no está disponible" in out.reason
    # ventanilla directa tampoco: se atiende desde el plan
    a = lab.c.post("/api/ventanilla/atenciones", headers=lab.ops["op1"], json={"clave": "plan-prop-0001"}).json()
    r = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/expediente", headers=lab.ops["op1"],
                   json={"expediente_id": e["id"]})
    assert r.status_code == 409 and "plan de la jornada" in r.text
    b = lab.c.post(f"/api/ventanilla/atenciones/{a['id']}/buscar", headers=lab.ops["op1"], json={"codigo": e["codigo"]}).json()
    assert b["expedientes"][0]["puede_atenderse"] is False
    # al retirarlo del plan vuelve a la cola general y el motor ya puede asignarlo
    elid = _elementos(lab, pid)[e["id"]]["id"]
    assert lab.c.delete(f"/api/planes/elementos/{elid}", headers=lab.h).status_code == 200
    assert _estado_req(lab, e["id"]) == "ELEGIBLE"
    assert asg.try_assign(lab.db, slot, req, op, _hoy_dt(), 60).assignment_id is not None


def test_cola_ya_asignada_no_entra_al_plan(lab):
    (e,) = _listos(lab, 1)
    slot, req, op = _slot_para(lab, e["id"])
    with lab.db.session() as s:
        r = s.get(Request, req)
        if r.status == asg.PENDIENTE:
            asg.validate_request(s, r, _hoy_dt())
    assert asg.try_assign(lab.db, slot, req, op, _hoy_dt(), 60).assignment_id is not None
    pid = _plan(lab)
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [_f(41)]}]}).json()
    assert r["aceptados"] == [] and "cola general ya lo asignó" in r["rechazados"][0]["motivo"], r
    assert _estado_req(lab, e["id"]) == "ASIGNADO"


def test_carrera_plan_contra_cola_un_solo_ganador(lab):
    rondas = 8
    exps = _listos(lab, rondas, tipo_tramite="alta")
    pid = _plan(lab, capacidad=rondas)
    resultados = []
    for e in exps:
        slot, req, op = _slot_para(lab, e["id"])
        with lab.db.session() as s:
            r = s.get(Request, req)
            if r.status == asg.PENDIENTE:
                asg.validate_request(s, r, _hoy_dt())
        res, barrera = {}, threading.Barrier(2)

        def plan_():
            barrera.wait()
            res["plan"] = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
                {"expediente_id": e["id"], "fechas_aceptables": [_f(42)]}]}).json()

        def cola_():
            barrera.wait()
            res["cola"] = asg.try_assign(lab.db, slot, req, op, _hoy_dt(), 60)
        hs = [threading.Thread(target=plan_), threading.Thread(target=cola_)]
        for h in hs:
            h.start()
        for h in hs:
            h.join(60)
        en_plan = bool(res["plan"]["aceptados"])
        en_cola = res["cola"].assignment_id is not None
        resultados.append((en_plan, en_cola))
        assert en_plan != en_cola, (res["plan"], res["cola"])               # exactamente uno
        estado = _estado_req(lab, e["id"])
        assert estado == ("EN_PLAN" if en_plan else "ASIGNADO"), estado
        with lab.db.session() as s:
            vivas = s.scalar(select(Assignment.id).where(Assignment.live_request_id == req))
            vivo = s.scalar(select(PlanElemento.id).where(PlanElemento.activo_expediente_id == e["id"]))
        assert bool(vivas) != bool(vivo)
        if en_cola:                                                          # libera la carga del operador
            with lab.db.session() as s:
                a = s.get(Assignment, res["cola"].assignment_id)
                asg._release(s, a, "CANCELADA", _hoy_dt(), "fin de la ronda")


def test_reconciliar_y_fin_de_jornada(lab):
    pid, exps = _plan_con(lab, 2, [_f(43)])
    # una solicitud de un plan creado antes de la regla (simulado): vuelve a EN_PLAN
    with lab.db.session() as s:
        req = s.get(Request, s.get(Expediente, exps[0]["id"]).request_id)
        asg.transition(s, req, asg.ELEGIBLE, "prueba", "como antes de la regla", _hoy_dt())
    assert propiedad.reconciliar(lab.db, _hoy_dt())["a_plan"] == 1
    assert _estado_req(lab, exps[0]["id"]) == "EN_PLAN"
    assert propiedad.reconciliar(lab.db, _hoy_dt()) == {"a_plan": 0, "conflictos": 0, "retirados": 0,
                                                       "fin_jornada": 0, "huerfanas": 0}       # idempotente
    # al día siguiente la jornada terminó: el plan se cierra y los expedientes vuelven a la cola
    r = propiedad.reconciliar(lab.db, _hoy_dt() + timedelta(days=1))
    assert r["fin_jornada"] == 2, r
    for e in exps:
        assert _estado_req(lab, e["id"]) == "ELEGIBLE"
        assert _elementos(lab, pid)[e["id"]]["estado"] == "FIN_JORNADA"
    assert lab.c.get(f"/api/planes/{pid}", headers=lab.h).json()["estado"] == "CERRADO"

"""v2.1 · Planificador diario (P1), coincidencias (P3), recorrido hasta folio (P4) y resistencia de
la parte transaccional (P5). Datos ficticios; portal de laboratorio propio como proceso (127.0.0.1).
El observador con navegador real se prueba en tests/test_observador.py."""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select

from monitoreo.captura import zona
from monitoreo.db import utcnow
from monitoreo.models import Expediente, ObservacionEvento, PlanElemento
from monitoreo.planificador import coincidencias, observaciones, operacion

from .captura_util import MANUALES
from .test_captura_dia import lab, portal  # noqa: F401  (fixtures)


def _hoy():
    return zona.hoy(utcnow())


def _f(dias: int) -> str:
    return (_hoy() + timedelta(days=dias)).isoformat()


def _plan(lab, capacidad=6, jornada=None):
    r = lab.c.post("/api/planes", headers=lab.h, json={"fecha_jornada": (jornada or _hoy()).isoformat(),
                                                       "capacidad": capacidad})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _listos(lab, n, **kw):
    out = []
    for _ in range(n):
        e = lab.crear(**kw)
        assert e["estado"] == "LISTO", e
        out.append(e)
    return out


def _prioridad(lab, eid, p):
    with lab.db.session() as s:
        s.get(Expediente, eid).prioridad = p


def _ver(lab, ctx_suc="NOR-1", tramite="alta", fecha=None, estado="DISPONIBLE", estrategia="dom", obs="obs-test",
         tipo="CELDA"):
    ctx = {"observador": obs, "sucursal": ctx_suc, "tramite": tramite}
    o = ({"tipo": "CELDA", "estrategia": estrategia, "fecha": fecha, "estado": estado, "ts_ms": utcnow().timestamp() * 1000}
         if tipo == "CELDA" else {"tipo": "INICIAL", "estrategia": estrategia, "ts_ms": utcnow().timestamp() * 1000,
                                  "celdas": [{"fecha": fecha, "estado": estado}]})
    return observaciones.registrar(lab.db, o, ctx, utcnow())


def _elementos(lab, plan_id):
    return {e["expediente_id"]: e for e in lab.c.get(f"/api/planes/{plan_id}", headers=lab.h).json()["elementos"]}


# ---------------------------------------------------------------- P1
def test_plan_seis_sin_duplicados_ni_incompatibles_y_persistente(lab):
    exps = _listos(lab, 8)
    for i, e in enumerate(exps):
        _prioridad(lab, e["id"], [3, 1, 2, 5, 1, 4, 2, 3][i])
    inc = lab.crear(telefono=None, telefono_confirmacion=None)
    assert inc["estado"] == "INCOMPLETO"
    pid = _plan(lab)
    r = lab.c.post(f"/api/planes/{pid}/seleccionar", headers=lab.h, json={"fechas_desde": _f(1), "fechas_hasta": _f(40)})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["vivos"] == 6 and plan["capacidad"] == 6
    elegidos = [e["expediente_id"] for e in plan["elementos"]]
    # determinista: prioridad y luego antigüedad
    esperado = [e["id"] for _, _, e in sorted((e2["prioridad"], i, e) for i, e in enumerate(exps)
                                              for e2 in [{"prioridad": [3, 1, 2, 5, 1, 4, 2, 3][i]}])][:6]
    assert elegidos == esperado, (elegidos, esperado)
    assert inc["id"] not in elegidos
    fuera = [e["id"] for e in exps if e["id"] not in elegidos]
    # lleno: el 7.º se rechaza con su motivo
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [{"expediente_id": fuera[0]}]})
    assert r.json()["rechazados"][0]["motivo"].startswith("El plan está completo")
    # ampliar y agregar; duplicado, incompleto, sucursal/trámite/fecha incompatibles: rechazados
    assert lab.c.post(f"/api/planes/{pid}/ampliar", headers=lab.h, json={"capacidad": 10}).status_code == 200
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": fuera[0], "prioridad": 2, "fechas_aceptables": [_f(5), _f(6)]},
        {"expediente_id": elegidos[0]},
        {"expediente_id": inc["id"]},
        {"expediente_id": fuera[1], "sucursales": ["SUR-1"]},
        {"expediente_id": fuera[1], "tramite": "otro"},
        {"expediente_id": fuera[1], "fechas_aceptables": [_f(-3)]},
        {"expediente_id": 999999}]}).json()
    assert [a["expediente_id"] for a in r["aceptados"]] == [fuera[0]]
    motivos = [x["motivo"] for x in r["rechazados"]]
    assert motivos[0] == "El expediente ya está en este plan"
    assert "incompleto" in motivos[1]
    assert motivos[2].startswith("Sucursal incompatible") and motivos[3].startswith("Trámite incompatible")
    assert "anteriores a la jornada" in motivos[4] and motivos[5] == "el expediente no existe"
    # otro plan (otra jornada) no puede tener vivo un expediente que ya está en este
    pid2 = _plan(lab, jornada=_hoy() + timedelta(days=1))
    r = lab.c.post(f"/api/planes/{pid2}/elementos", headers=lab.h, json={"elementos": [{"expediente_id": elegidos[0]}]})
    assert r.json()["rechazados"][0]["motivo"].startswith("El expediente ya está en otro plan")
    assert lab.c.post("/api/planes", headers=lab.h, json={"fecha_jornada": _hoy().isoformat()}).status_code == 409
    assert lab.c.post("/api/planes", headers=lab.ops["op1"], json={"fecha_jornada": _f(3)}).status_code == 403
    # reinicio: otra aplicación sobre la misma base ve el mismo plan
    from fastapi.testclient import TestClient
    from monitoreo.app import create_app
    antes = lab.c.get(f"/api/planes/{pid}", headers=lab.h).json()
    with TestClient(create_app(lab.db.url, start_engine=False, seed_demo=False)) as c2:
        h2 = {"Authorization": lab.h["Authorization"]}
        r2 = c2.get(f"/api/planes/{pid}", headers=h2)
        if r2.status_code == 401:                 # la sesión es por proceso en algunas configuraciones
            from .captura_util import ADMIN_PWD
            h2 = {"Authorization": "Bearer " + c2.post("/api/auth/login", json={"username": "admin",
                                                                                 "password": ADMIN_PWD}).json()["token"]}
            r2 = c2.get(f"/api/planes/{pid}", headers=h2)
        assert r2.json() == antes


# ---------------------------------------------------------------- P3
def test_coincidencia_determinista_explicada_y_deduplicada(lab):
    a, b, c, d = _listos(lab, 4, tipo_tramite="alta")
    for x, p in ((a, 2), (b, 1), (c, 1), (d, 1)):
        _prioridad(lab, x["id"], p)
    pid = _plan(lab)
    dia = _f(7)
    r = lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": a["id"], "fechas_aceptables": [dia]},
        {"expediente_id": b["id"], "fechas_aceptables": [dia], "sucursales": ["NOR-2"]},   # otra sucursal
        {"expediente_id": c["id"], "fechas_aceptables": [dia]},
        {"expediente_id": d["id"], "fechas_aceptables": [dia]}]}).json()
    assert len(r["aceptados"]) == 4, r
    nuevos = _ver(lab, fecha=dia)
    assert len(nuevos) == 1 and nuevos[0]["tipo"] == "DIA_DISPONIBLE"
    # la otra estrategia (u otro observador) ve el mismo cambio: NO hay evento nuevo, se anota la detección
    assert _ver(lab, fecha=dia, estrategia="visual") == []
    assert _ver(lab, fecha=dia, estrategia="dom", obs="otro-observador") == []
    ev = observaciones.listar(lab.db)[0]
    assert set(ev["detecciones"]) == {"dom", "visual"}
    out = coincidencias.atender(lab.db, ev["id"], utcnow())
    assert out["asignado"], out
    els = _elementos(lab, pid)
    # b tiene la mejor prioridad pero no acepta NOR-1; entre c y d (prioridad 1) gana el primero en entrar
    assert els[c["id"]]["estado"] == "ASIGNADO" and els[c["id"]]["fecha_asignada"] == dia
    assert els[a["id"]]["estado"] == els[b["id"]]["estado"] == els[d["id"]]["estado"] == "EN_ESPERA"
    expl = out["explicacion"]
    assert expl["elegido"]["elemento_id"] == els[c["id"]]["id"] and "prioridad" in expl["regla"]
    assert [x["elemento_id"] for x in expl["compatibles_en_espera"]] == [els[d["id"]]["id"], els[a["id"]]["id"]]
    assert expl["descartados"] == [{"elemento_id": els[b["id"]]["id"], "codigo": b["codigo"], "prioridad": 1,
                                    "orden": 2, "motivo": "no acepta esa sucursal"}]
    # un evento se atiende una sola vez
    assert coincidencias.atender(lab.db, ev["id"], utcnow())["asignado"] is False
    with lab.db.session() as s:
        assert s.get(ObservacionEvento, ev["id"]).consumo == "ASIGNADO"
    # mismo resultado con la misma entrada: otro día igual elige a d (siguiente en el orden)
    _ver(lab, fecha=dia, estado="AGOTADO")
    out2 = coincidencias.atender(lab.db, _ver(lab, fecha=dia)[0]["id"], utcnow())
    assert out2["elemento_id"] == els[d["id"]]["id"], out2


def test_sin_disponibilidad_o_sin_candidatos_conserva_la_lista(lab):
    exps = _listos(lab, 3, tipo_tramite="alta")
    pid = _plan(lab)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [_f(9)]} for e in exps]})
    tram = exps[0]["tipo_tramite"]
    _ver(lab, tramite=tram, fecha=_f(20))                                     # fecha que nadie acepta
    _ver(lab, ctx_suc="SUR-1", tramite=tram, fecha=_f(9))                       # sucursal que nadie acepta
    r = coincidencias.atender_pendientes(lab.db, utcnow())
    assert len(r) == 2 and not any(x["asignado"] for x in r)
    assert all(e["estado"] == "EN_ESPERA" for e in _elementos(lab, pid).values())
    with lab.db.session() as s:
        assert {e.consumo for e in s.scalars(select(ObservacionEvento))} == {"SIN_CANDIDATOS"}


# ---------------------------------------------------------------- P5 (parte transaccional)
def test_eventos_simultaneos_no_consumen_dos_veces_el_mismo_expediente(lab):
    (e,) = _listos(lab, 1, tipo_tramite="alta")
    tram = e["tipo_tramite"]
    pid = _plan(lab)
    dias = [_f(10 + i) for i in range(6)]
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": dias}]})
    ids = [_ver(lab, tramite=tram, fecha=d)[0]["id"] for d in dias]
    res, barrera = [], threading.Barrier(len(ids) * 2)

    def go(i):
        barrera.wait()
        try:
            res.append(coincidencias.atender(lab.db, i, utcnow()))
        except Exception as ex:  # noqa: BLE001 - se cuenta como fallo
            res.append({"asignado": False, "error": repr(ex)})
    hilos = [threading.Thread(target=go, args=(i,)) for i in ids * 2]           # cada evento, dos veces a la vez
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    assert sum(1 for x in res if x.get("asignado")) == 1, res
    with lab.db.session() as s:
        el = s.scalar(select(PlanElemento).where(PlanElemento.plan_id == pid))
        assert el.estado == "ASIGNADO" and el.fecha_asignada in dias
        consumos = sorted(x.consumo for x in s.scalars(select(ObservacionEvento)))
    assert consumos.count("ASIGNADO") == 1 and consumos.count("SIN_CANDIDATOS") == len(dias) - 1


def test_cambio_que_desaparece_devuelve_a_espera(lab):
    (e,) = _listos(lab, 1)
    tram = e["tipo_tramite"]
    pid = _plan(lab)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [_f(8), _f(12)]}]})
    # 1. aparece y desaparece ANTES de atenderse: el evento queda DESAPARECIDO, nada se asigna
    ev = _ver(lab, tramite=tram, fecha=_f(8))[0]
    assert _ver(lab, tramite=tram, fecha=_f(8), estado="AGOTADO")[0]["tipo"] == "DIA_NO_DISPONIBLE"
    assert coincidencias.atender(lab.db, ev["id"], utcnow())["asignado"] is False
    with lab.db.session() as s:
        assert s.get(ObservacionEvento, ev["id"]).consumo == "DESAPARECIDO"
    # 2. aparece, se asigna y desaparece antes de preparar la operación: vuelve a EN_ESPERA
    ev = _ver(lab, tramite=tram, fecha=_f(12))[0]
    assert coincidencias.atender(lab.db, ev["id"], utcnow())["asignado"]
    _ver(lab, tramite=tram, fecha=_f(12), estado="DESHABILITADO")
    assert coincidencias.liberar_desaparecidos(lab.db, utcnow())
    el = _elementos(lab, pid)[e["id"]]
    assert el["estado"] == "EN_ESPERA" and "dejó de estar disponible" in el["explicacion"]["devuelto"]["motivo"]
    # 3. vuelve a aparecer: es un evento NUEVO (otra transición) y se asigna otra vez
    ev2 = _ver(lab, tramite=tram, fecha=_f(12))
    assert ev2 and ev2[0]["id"] != ev["id"] and coincidencias.atender(lab.db, ev2[0]["id"], utcnow())["asignado"]


def test_dos_operadores_compiten_por_la_misma_coincidencia(lab):
    (e,) = _listos(lab, 1)
    tram = e["tipo_tramite"]
    pid = _plan(lab)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [_f(14)]}]})
    coincidencias.atender(lab.db, _ver(lab, tramite=tram, fecha=_f(14))[0]["id"], utcnow())
    elid = _elementos(lab, pid)[e["id"]]["id"]
    vistos = [x["id"] for x in lab.c.get("/api/planificador/coincidencias", headers=lab.ops["op1"]).json()["coincidencias"]]
    assert elid in vistos
    res, barrera = {}, threading.Barrier(2)

    def go(u):
        barrera.wait()
        res[u] = lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=lab.ops[u])
    hs = [threading.Thread(target=go, args=(u,)) for u in ("op1", "op2")]
    for h in hs:
        h.start()
    for h in hs:
        h.join(60)
    codigos = sorted(r.status_code for r in res.values())
    assert codigos == [200, 409], {u: (r.status_code, r.text[:200]) for u, r in res.items()}
    ganador = next(u for u, r in res.items() if r.status_code == 200)
    perdedor = "op2" if ganador == "op1" else "op1"
    assert lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=lab.ops[perdedor]).status_code == 409
    # el ganador puede retomar (idempotente) y el perdedor ya no la ve
    assert lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=lab.ops[ganador]).json()["retomada"]
    assert elid not in [x["id"] for x in lab.c.get("/api/planificador/coincidencias",
                                                     headers=lab.ops[perdedor]).json()["coincidencias"]]


# ---------------------------------------------------------------- P4 (sin navegador)
def test_recorrido_plan_evento_operacion_confirmacion_humana_folio(lab):
    exps = _listos(lab, 6)
    pid = _plan(lab)
    r = lab.c.post(f"/api/planes/{pid}/seleccionar", headers=lab.h, json={"fechas_desde": _f(2), "fechas_hasta": _f(45)})
    assert r.json()["plan"]["vivos"] == 6
    objetivo = exps[0]
    tram = objetivo["tipo_tramite"]
    # el laboratorio habilita una fecha NUEVA para ese trámite en NOR-1
    hoy_portal = lab.portal.ctl("GET", "/control/dias")["hoy"]
    usadas = {x["fecha"] for x in lab.portal.ctl("GET", "/control/dias")["dias"].values()
              if x["sucursal"] == "NOR-1" and x["tramite"] == tram}
    from datetime import date
    fecha = next(d for d in ((date.fromisoformat(hoy_portal) + timedelta(days=i)).isoformat() for i in range(25, 45))
                 if d not in usadas)
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite=tram, fecha=fecha, capacidad=1)
    ev = _ver(lab, tramite=tram, fecha=fecha)[0]                 # lo que emitiría el observador
    out = coincidencias.atender(lab.db, ev["id"], utcnow())
    assert out["asignado"], out
    elid = out["elemento_id"]
    with lab.db.session() as s:
        el = s.get(PlanElemento, elid)
        exp_id = el.expediente_id
    # operación preparada por un operador (NO confirma nada por su cuenta)
    op = "op1"
    r = lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=lab.ops[op])
    assert r.status_code == 200, r.text
    rid = r.json()["reservacion_id"]
    r = lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=lab.ops[op],
                   json={"revisado": True, "manuales": dict(MANUALES), "sucursal": "NOR-1"})
    assert r.status_code == 200, r.text
    r = lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=lab.ops[op], json={"fecha": fecha, "sucursal": "NOR-1"})
    assert r.status_code == 200, r.text
    assert operacion.sincronizar(lab.db, utcnow())["confirmados"] == 0         # sin confirmación humana: nada
    r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=lab.ops[op], json={"confirmo": True})
    assert r.status_code == 200, r.text
    folio = r.json()["reservacion"]["folio"]
    assert folio and r.json()["reservacion"]["estado"] == "CONFIRMADA"
    assert operacion.sincronizar(lab.db, utcnow())["confirmados"] == 1
    els = _elementos(lab, pid)
    assert els[exp_id]["estado"] == "CONFIRMADO" and els[exp_id]["explicacion"]["confirmado"]["folio"] == folio
    assert sum(1 for e in els.values() if e["estado"] == "EN_ESPERA") == 5     # los demás siguen esperando
    from .test_captura_dia import api_portal
    del api_portal                                                              # el folio ya se comprobó por la API


def test_dia_agotado_al_elegir_devuelve_a_espera_y_a_la_cola(lab):
    (e,) = _listos(lab, 1)
    tram = e["tipo_tramite"]
    pid = _plan(lab)
    hoy_portal = lab.portal.ctl("GET", "/control/dias")["hoy"]
    from datetime import date
    usadas = {x["fecha"] for x in lab.portal.ctl("GET", "/control/dias")["dias"].values()
              if x["sucursal"] == "NOR-1" and x["tramite"] == tram}
    fecha = next(d for d in ((date.fromisoformat(hoy_portal) + timedelta(days=i)).isoformat() for i in range(46, 70))
                 if d not in usadas)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [fecha]}]})
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite=tram, fecha=fecha, capacidad=1)
    out = coincidencias.atender(lab.db, _ver(lab, tramite=tram, fecha=fecha)[0]["id"], utcnow())
    elid = out["elemento_id"]
    rid = lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=lab.ops["op1"]).json()["reservacion_id"]
    lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=lab.ops["op1"],
               json={"revisado": True, "manuales": dict(MANUALES), "sucursal": "NOR-1"})
    lab.portal.ctl("POST", "/control/dias/agotar", sucursal="NOR-1", tramite=tram, fecha=fecha)   # otro la ocupó
    r = lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=lab.ops["op1"], json={"fecha": fecha, "sucursal": "NOR-1"})
    assert r.status_code == 409 and "ya no está disponible" in r.text
    r = lab.c.post(f"/api/planificador/elementos/{elid}/devolver", headers=lab.ops["op1"], json={"motivo": "día agotado"})
    assert r.status_code == 200 and r.json()["estado"] == "EN_ESPERA", r.text
    with lab.db.session() as s:
        assert s.get(Expediente, e["id"]).estado == "LISTO"          # vuelve a la cola, no se pierde


def test_confirmacion_incierta_no_se_da_por_hecha_hasta_verificarla(lab, monkeypatch):
    """P5: la respuesta de la confirmación se pierde y el portal no puede verificarse: el elemento
    sigue OPERACION_PREPARADA (ni confirmado ni devuelto); al volver el portal, se verifica y queda
    CONFIRMADO con el folio del portal."""
    from datetime import date

    from monitoreo.captura import seguimiento
    from monitoreo.captura.formularios.navegador import NavegadorHTTP, ResultadoIncierto
    (e,) = _listos(lab, 1)
    tram = e["tipo_tramite"]
    pid = _plan(lab)
    d = lab.portal.ctl("GET", "/control/dias")
    usadas = {x["fecha"] for x in d["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == tram}
    fecha = next(f for f in ((date.fromisoformat(d["hoy"]) + timedelta(days=i)).isoformat() for i in range(71, 99))
                 if f not in usadas)
    lab.c.post(f"/api/planes/{pid}/elementos", headers=lab.h, json={"elementos": [
        {"expediente_id": e["id"], "fechas_aceptables": [fecha]}]})
    lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite=tram, fecha=fecha, capacidad=1)
    elid = coincidencias.atender(lab.db, _ver(lab, tramite=tram, fecha=fecha)[0]["id"], utcnow())["elemento_id"]
    h = lab.ops["op1"]
    rid = lab.c.post(f"/api/planificador/elementos/{elid}/preparar", headers=h).json()["reservacion_id"]
    lab.c.post(f"/api/reservaciones/{rid}/enviar-datos", headers=h, json={"revisado": True, "manuales": dict(MANUALES),
                                                                          "sucursal": "NOR-1"})
    assert lab.c.post(f"/api/reservaciones/{rid}/seleccionar-dia", headers=h,
                      json={"fecha": fecha, "sucursal": "NOR-1"}).status_code == 200
    lab.portal.fallos(modo="cortar_tras_confirmar", restantes=1)

    def caido(self, *a, **kw):
        raise ResultadoIncierto("RED", "simulado: el motor perdió la red")
    with monkeypatch.context() as m:
        m.setattr(NavegadorHTTP, "buscar_por_clave", caido)
        m.setattr(NavegadorHTTP, "verificar_folio", caido)
        r = lab.c.post(f"/api/reservaciones/{rid}/confirmar", headers=h, json={"confirmo": True})
        assert r.status_code == 202 and r.json()["reservacion"]["estado"] == "EN_VERIFICACION"
        seguimiento.mantenimiento(lab.db, utcnow())
        assert _elementos(lab, pid)[e["id"]]["estado"] == "OPERACION_PREPARADA"
    seguimiento.mantenimiento(lab.db, utcnow())        # el portal responde: se verifica por la clave
    seguimiento.mantenimiento(lab.db, utcnow())        # y el plan lo refleja
    el = _elementos(lab, pid)[e["id"]]
    assert el["estado"] == "CONFIRMADO" and el["explicacion"]["confirmado"]["folio"], el

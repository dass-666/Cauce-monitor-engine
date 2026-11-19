"""RC1 · Importación administrativa (.csv/.xlsx), permisos, bloqueo por consentimiento, reimportación sin
duplicados y planificación por selección. Datos FICTICIOS. Pruebas proporcionales: lo que el administrador
hace con un archivo de verdad, no cada rama del lector."""
from __future__ import annotations

import base64
import hashlib
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from monitoreo.app import create_app
from monitoreo.captura import importacion as im
from monitoreo.captura.xlsx_escritura import Formula, libro
from monitoreo.config import settings
from monitoreo.models import Expediente, ImportacionFila, PlanElemento

ADMIN_PWD = "Admin-Prueba-123"
HOY = date.today()
D1, D2 = (HOY + timedelta(days=3)).isoformat(), (HOY + timedelta(days=20)).isoformat()
ENC = ["Nombre completo", "Fecha de nacimiento", "Registro previo", "Teléfono", "Correo", "Región", "Sucursal",
       "Trámite", "Acepta desde", "Acepta hasta", "Contraseña del portal", "Consentimiento"]


def filas_demo():
    return [
        ["Ana Prueba Uno", "15/03/1985", "00450001", "5550000001", "ana.uno@ejemplo.test", "Norte", "NOR-1", "Alta", D1, D2, "secreto1", "sí"],
        ["Beto Prueba Dos", "1990-07-02", "00450002", "5550000002", "beto.dos@ejemplo.test", "NORTE", "NOR-1", "alta", D1, D2, "secreto2", "sí"],
        ["Carla Prueba Tres", "", "00450003", "5550000003", "carla@ejemplo.test", "NORTE", "NOR-2", "alta", D1, D2, "", ""],   # falta nacimiento
        ["Dani", "01/01/1980", "00450004", "5550000004", "dani@ejemplo.test", "NORTE", "NOR-1", "alta", "", "", "", ""],      # nombre inválido
        ["=CMD|' /C calc'!A0", "01/01/1980", "00450005", "5550000005", "x@ejemplo.test", "NORTE", "NOR-1", "alta", "", "", "", ""],  # fórmula
        ["Ana Prueba Uno", "15/03/1985", "00450001", "5550000001", "ana.uno@ejemplo.test", "NORTE", "NOR-1", "alta", D1, D2, "", ""],  # repetida
    ]


def csv_bytes(filas, enc=ENC):
    import csv
    import io
    b = io.StringIO()
    w = csv.writer(b)
    w.writerow(enc)
    w.writerows(filas)
    return b.getvalue().encode("utf-8")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PWD)
    monkeypatch.setenv("MONITOREO_EXIGIR_CONSENTIMIENTO", "1")
    url = f"sqlite:///{(tmp_path / 'imp.db').as_posix()}"
    with TestClient(create_app(url, start_engine=False, seed_demo=False)) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PWD})
        c.headers["Authorization"] = "Bearer " + r.json()["token"]
        yield c


def body(nombre, datos, **kw):
    return {"archivo": nombre, "contenido_b64": base64.b64encode(datos).decode(), **kw}


def test_csv_vista_previa_importacion_y_reimportacion(app):
    datos = csv_bytes(filas_demo())
    pv = app.post("/api/admin/importaciones/previsualizar", json=body("clientes.csv", datos)).json()
    tipos = {x["texto"]: x["tipo"] for x in pv["encabezados"]}
    assert tipos["Contraseña del portal"] == "PROHIBIDA" and tipos["Consentimiento"] == "CONSENTIMIENTO"
    assert all("secreto" not in str(f["valores"]) for f in pv["filas"])           # la contraseña no viaja
    res = {f["fila"]: f["resultado"] for f in pv["filas"]}
    assert res == {2: "CREADO", 3: "CREADO", 4: "CREADO", 5: "RECHAZADO", 6: "RECHAZADO", 7: "REPETIDA_EN_ARCHIVO"}
    por = {f["fila"]: f for f in pv["filas"]}
    assert por[2]["valores"]["id_registro_previo"] == "00450001"                  # ceros iniciales intactos
    assert por[2]["valores"]["fecha_nacimiento"] == "1985-03-15"                  # DD/MM/AAAA
    assert "fecha_nacimiento" in por[4]["faltan"] and "consentimiento" in por[4]["faltan"]
    assert "fórmula" in por[6]["errores"]["nombre"]
    assert pv["resumen"]["nuevos"] == 3 and pv["resumen"]["rechazadas"] == 2

    lote = app.post("/api/admin/importaciones", json=body("clientes.csv", datos)).json()
    assert (lote["creadas"], lote["rechazadas"], lote["existentes"]) == (3, 2, 1)
    estados = {f["fila"]: (f["expediente"] or {}).get("estado") for f in lote["filas_detalle"]}
    # importar NO es consentir: todos quedan bloqueados (INCOMPLETO) aunque el archivo diga «sí»
    assert estados[2] == estados[3] == estados[4] == "INCOMPLETO"
    rep = app.get(f"/api/admin/importaciones/{lote['id']}/reporte.csv").text
    assert "RECHAZADO" in rep and "Ana" not in rep and "secreto" not in rep        # reporte sin datos personales

    # segunda carga del MISMO archivo: nada nuevo
    lote2 = app.post("/api/admin/importaciones", json=body("clientes (copia).csv", datos)).json()
    assert lote2["creadas"] == 0 and lote2["existentes"] == 4 and any("ya se cargó" in a for a in lote2["avisos"])
    exps = app.get("/api/admin/expedientes").json()["expedientes"]
    assert len(exps) == 3 and {e["lote"] for e in exps} == {lote["codigo"]}
    assert {e["situacion"] for e in exps} == {"SIN_CONSENTIMIENTO", "INCOMPLETO"}


def test_xlsx_hojas_fechas_ceros_y_formulas(app):
    datos = libro({"Notas": [["no es esta"]], "Clientes": [
        ["Nombre(s)", "Apellido paterno", "Apellido materno", "F. Nacimiento", "No. de registro", "Celular", "E-mail",
         "Región", "Sucursal", "Trámite"],
        ["Elena", "Prueba", "Cuatro", date(1979, 5, 6), "00450006", 5550000006, "elena@ejemplo.test", "NORTE", "NOR-1", "Renovación"],
        ["Fede", "Prueba", "Cinco", 30000, "00450007", 5550000007, "fede@ejemplo.test", "NORTE", "NOR-1", Formula("=A1")],
    ]})
    pv = app.post("/api/admin/importaciones/previsualizar", json=body("lista.xlsx", datos, hoja="Clientes")).json()
    assert pv["hojas"] == ["Notas", "Clientes"] and pv["hoja"] == "Clientes"
    f2, f3 = pv["filas"]
    assert f2["resultado"] == "CREADO" and f2["valores"]["nombre"] == "Elena Prueba Cuatro"
    assert f2["valores"]["fecha_nacimiento"] == "1979-05-06" and f2["valores"]["id_registro_previo"] == "00450006"
    assert f2["valores"]["tipo_tramite"] == "renovacion"
    assert f3["resultado"] == "RECHAZADO" and "fórmula" in f3["errores"]["tipo_tramite"]
    # asignación manual: una columna prohibida no puede asignarse
    r = app.post("/api/admin/importaciones/previsualizar", json=body(
        "x.csv", csv_bytes([["Ana Prueba Uno", "x"]], ["Nombre", "Contraseña"]), mapeo={"0": "nombre", "1": "correo"}))
    assert r.status_code == 422 and "contraseñas" in r.text


def test_archivos_peligrosos_rechazados(app):
    def err(nombre, datos):
        r = app.post("/api/admin/importaciones/previsualizar", json=body(nombre, datos))
        assert r.status_code in (413, 415, 422), r.text
        return r.json()["detail"]
    assert "Solo se admiten" in str(err("lista.exe", b"MZ..."))
    assert "macros" in str(err("lista.xlsm", b"PK"))
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
    assert "DTD" in str(err("bomba.xlsx", b.getvalue()))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/vbaProject.bin", b"\0")
    assert "macros" in str(err("macro.xlsx", b.getvalue()))


def test_permisos_solo_administrador(app):
    r = app.post("/api/users", json={"username": "op9", "password": "Operador-123", "role": "operador",
                                     "regions": ["NORTE"]})
    assert r.status_code == 201
    tok = app.post("/api/auth/login", json={"username": "op9", "password": "Operador-123"}).json()["token"]
    h = {"Authorization": "Bearer " + tok}
    datos = csv_bytes(filas_demo())
    for metodo, ruta, js in (("post", "/api/admin/importaciones/previsualizar", body("c.csv", datos)),
                             ("post", "/api/admin/importaciones", body("c.csv", datos)),
                             ("get", "/api/admin/panel", None), ("get", "/api/admin/expedientes", None),
                             ("post", "/api/admin/plan/agregar", {"expediente_ids": [1]})):
        r = app.post(ruta, json=js, headers=h) if metodo == "post" else app.get(ruta, headers=h)
        assert r.status_code == 403, ruta


def test_consentimiento_desbloquea_y_plan_por_seleccion(app):
    lote = app.post("/api/admin/importaciones", json=body("c.csv", csv_bytes(filas_demo()))).json()
    ids = {f["fila"]: f["expediente"]["id"] for f in lote["filas_detalle"] if f["resultado"] == "CREADO"}
    # sin consentimiento NO entra al plan, con su motivo
    r = app.post("/api/admin/plan/agregar", json={"expediente_ids": list(ids.values())}).json()
    assert not r["aceptados"] and len(r["rechazados"]) == 3
    # el administrador registra el consentimiento (con la huella del documento) de las filas 2 y 3
    for fila in (2, 3):
        c = app.post(f"/api/expedientes/{ids[fila]}/consentimientos", json={
            "medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01", "evidencia_ref": f"Carpeta LAB hoja {fila}",
            "evidencia_sha256": hashlib.sha256(f"formato {fila}".encode()).hexdigest()})
        assert c.status_code == 201, c.text
    sit = {e["id"]: e["situacion"] for e in app.get("/api/admin/expedientes").json()["expedientes"]}
    assert sit[ids[2]] == sit[ids[3]] == "LISTO" and sit[ids[4]] == "INCOMPLETO"
    r = app.post("/api/admin/plan/agregar", json={"expediente_ids": list(ids.values())}).json()
    assert {x["expediente_id"] for x in r["aceptados"]} == {ids[2], ids[3]}
    assert [x["expediente_id"] for x in r["rechazados"]] == [ids[4]]
    # el plan usa el rango aceptable de CADA expediente (importado), no uno genérico
    el = next(x for x in r["aceptados"] if x["expediente_id"] == ids[2])
    assert el["fechas_aceptables"][0] == D1 and el["fechas_aceptables"][-1] == D2
    panel = app.get("/api/admin/panel").json()
    assert panel["cifras"]["en_plan"] == 2 and panel["cifras"]["incompletos"] == 1
    assert panel["cifras"]["confirmadas_con_folio"] == 0 and panel["cifras"]["coincidencias"] == 0
    filtro = app.get("/api/admin/expedientes", params={"bloqueados": "true"}).json()["expedientes"]
    assert [e["id"] for e in filtro] == [ids[4]]


def test_dos_cargas_simultaneas_no_duplican(app):
    datos = csv_bytes(filas_demo()[:3])
    db = app.app.state.db
    errores = []

    def cargar():
        try:
            im.importar(db, "c.csv", datos, "admin", __import__("monitoreo.db", fromlist=["utcnow"]).utcnow())
        except Exception as e:  # noqa: BLE001
            errores.append(e)
    hs = [threading.Thread(target=cargar) for _ in range(3)]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Expediente)) == 3
        assert s.scalar(select(func.count()).select_from(ImportacionFila).where(ImportacionFila.resultado == "CREADO")) == 3
        assert s.scalar(select(func.count()).select_from(PlanElemento)) == 0
    assert not errores

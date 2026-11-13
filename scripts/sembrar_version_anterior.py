r"""Crea datos FICTICIOS en una instalación de una versión anterior, con el código de esa versión.

Lo usa la integración continua para probar la actualización desde v1.1, v1.2 y v1.3 con bases
reales (no inventadas por la versión nueva). Uso, desde la carpeta de la versión anterior:
    <anterior>\.venv\Scripts\python.exe <nueva>\scripts\sembrar_version_anterior.py data\monitoreo.db
Contraseña del admin: MONITOREO_ADMIN_PASSWORD (o la de prueba "Version-Anterior-123").
Solo crea datos ficticios; no se conecta a nada externo.
"""
import os, sys, time
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from monitoreo.app import create_app
from monitoreo.config import settings
from monitoreo.engine.clock import RealClock
from monitoreo.engine import pipeline
path = str(__import__("pathlib").Path(sys.argv[1]).resolve())
PWD = os.environ.get("MONITOREO_ADMIN_PASSWORD") or "Version-Anterior-123"
settings.admin_password = PWD
settings.data_dir = __import__("pathlib").Path(path).parent
url = f"sqlite:///{__import__('pathlib').Path(path).as_posix()}"
app = create_app(url, start_engine=False, seed_demo=True)
with TestClient(app) as c:
    h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]}
    regions = [r["code"] for r in c.get("/api/regions", headers=h).json()] if c.get("/api/regions", headers=h).status_code == 200 else ["NORTE"]
    for k in range(30):
        r = c.post("/api/requests", headers=h, json={"code": f"ANT-{k:03d}", "holder": f"Titular Ficticio {k}",
               "region": regions[k % len(regions)], "resource_type": ["entrada", "mesa", "unidad", "turno"][k % 4],
               "priority": 1 + k % 5})
        assert r.status_code == 201, r.text
    for rnd in range(3):
        for s in c.get("/api/sources", headers=h).json():
            if s["connector_type"] == "simulacion":
                c.patch(f"/api/sources/{s['id']}", headers=h, json={"config": {**{k: v for k, v in s["config"].items() if v != "***"}, "error_rate": 0}})
            c.post(f"/api/sources/{s['id']}/consultar", headers=h)
        items = c.get("/api/lab/items", headers=h).json()
        for i in items[: 12]:
            c.post(f"/api/lab/items/{i['id']}/liberar", headers=h, json={"units": 1})
        time.sleep(1.1)
        for s in c.get("/api/sources", headers=h).json():
            c.post(f"/api/sources/{s['id']}/consultar", headers=h)
        db = app.state.db
        pipeline.maintenance(db, RealClock()); pipeline.process_pending_events(db, RealClock())
    asg = c.get("/api/assignments", headers=h).json()
    act = [a for a in asg if a["status"] == "ACTIVA"]
    if act:
        c.post(f"/api/assignments/{act[0]['id']}/tomar", headers=h)
    if len(act) > 1:
        c.post(f"/api/assignments/{act[1]['id']}/tomar", headers=h); c.post(f"/api/assignments/{act[1]['id']}/completar", headers=h)
    print("asignaciones", len(asg), "estados", sorted({a["status"] for a in c.get("/api/assignments", headers=h).json()}))

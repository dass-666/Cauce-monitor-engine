"""Reinicio inesperado: se mata el servidor real con SIGKILL a mitad de trabajo y se comprueba
que, al volver a arrancar, no se pierden datos y la integridad se mantiene."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PWD = "Reinicio-Prueba-123"


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _start(db, port, **extra):
    env = {**os.environ, **extra, "MONITOREO_DB_URL": f"sqlite:///{db.as_posix()}", "MONITOREO_ADMIN_PASSWORD": PWD,
           "MONITOREO_PUERTO": str(port), "MONITOREO_INICIAR_MOTOR": "1", "MONITOREO_LOG": "warning"}
    p = subprocess.Popen([sys.executable, "-m", "monitoreo"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return p
        except httpx.HTTPError:
            time.sleep(0.2)
    p.kill()
    raise RuntimeError("el servidor no arrancó")


def test_kill_minus_9_and_restart(tmp_path):
    db, port = tmp_path / "r.db", _port()
    base = f"http://127.0.0.1:{port}"
    p = _start(db, port)
    try:
        tok = httpx.post(f"{base}/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        codes = []
        for i in range(30):
            r = httpx.post(f"{base}/api/requests", headers=H, json={"code": f"RK-{i}", "holder": "Ficticio",
                                                                      "region": "NORTE", "resource_type": "entrada",
                                                                      "priority": 1})
            codes.append(r.json()["code"])
        items = [i for i in httpx.get(f"{base}/api/lab/items", headers=H).json() if i["region"] == "NORTE"]
        for it in items[:5]:
            httpx.post(f"{base}/api/lab/items/{it['id']}/liberar", headers=H, json={"units": 2})
        time.sleep(7)  # el motor detecta y asigna mientras tanto
        before = httpx.get(f"{base}/api/requests?q=RK-&limit=100", headers=H).json()
        n_alerts_before = len(httpx.get(f"{base}/api/alerts?limit=1000", headers=H).json())
    finally:
        p.kill()   # corte brusco: SIGKILL en Linux/macOS, TerminateProcess en Windows
        p.wait()
    p = _start(db, port)
    try:
        tok = httpx.post(f"{base}/api/auth/login", json={"username": "admin", "password": PWD}).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        after = httpx.get(f"{base}/api/requests?q=RK-&limit=100", headers=H).json()
        assert after["total"] == 30
        assigned_before = {r["code"] for r in before["items"] if r["status"] == "ASIGNADO"}
        assert assigned_before, "el motor debió asignar algo antes del corte"
        still = {r["code"] for r in after["items"] if r["status"] in ("ASIGNADO", "EN_PROCESO")}
        assert assigned_before <= still
        assert len(httpx.get(f"{base}/api/alerts?limit=1000", headers=H).json()) >= n_alerts_before
        assert httpx.get(f"{base}/api/metrics", headers=H).json()["integridad"] == []
    finally:
        p.terminate()
        p.wait(10)


def test_two_processes_one_leader_and_failover(tmp_path):
    """Dos servidores reales sobre la misma base: solo uno consulta; si muere, el otro releva."""
    db = tmp_path / "dos.db"
    pa, pb = _port(), _port()
    a = _start(db, pa, MONITOREO_LEASE_S="3")
    b = _start(db, pb, MONITOREO_LEASE_S="3")
    try:
        time.sleep(2)
        roles = {httpx.get(f"http://127.0.0.1:{p}/api/health").json()["rol"] for p in (pa, pb)}
        assert roles == {"LIDER", "EN_ESPERA"}, roles
        leader_port = pa if httpx.get(f"http://127.0.0.1:{pa}/api/health").json()["rol"] == "LIDER" else pb
        other_port = pb if leader_port == pa else pa
        tok = httpx.post(f"http://127.0.0.1:{other_port}/api/auth/login",
                         json={"username": "admin", "password": PWD}).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        src = next(s for s in httpx.get(f"http://127.0.0.1:{other_port}/api/sources", headers=H).json()
                   if s["code"] == "sim-norte")
        n0 = len(httpx.get(f"http://127.0.0.1:{other_port}/api/polls?source_id={src['id']}&limit=1000", headers=H).json())
        time.sleep(10)
        n1 = len(httpx.get(f"http://127.0.0.1:{other_port}/api/polls?source_id={src['id']}&limit=1000", headers=H).json())
        # intervalo de 5 s ±15 %: una sola instancia hace ~2 consultas en 10 s; dos harían ~4
        assert 1 <= n1 - n0 <= 3, n1 - n0
        (a if leader_port == pa else b).kill()
        deadline = time.time() + 10
        role = None
        while time.time() < deadline:
            role = httpx.get(f"http://127.0.0.1:{other_port}/api/health").json()["rol"]
            if role == "LIDER":
                break
            time.sleep(0.5)
        assert role == "LIDER"
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.terminate()
                p.wait(10)

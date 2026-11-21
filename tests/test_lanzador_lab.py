"""Lanzador de laboratorio: terminación normal, forzada, hijos resistentes y desertores. Sin conector real ni datos."""
from __future__ import annotations

import ast
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from monitoreo.conector import aislamiento
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lanzador_lab import LanzadorBloqueado, LanzadorLab  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve().parent / "herramientas" / "arbol_prueba.py"
WIN = sys.platform == "win32"


def correr(tmp_path, modo, *, plazo=20.0, gracia=0.5, antes=None, lanzador=None):
    pids = tmp_path / "pids"
    pids.mkdir(exist_ok=True)
    ent = lanzador or LanzadorLab(modo_laboratorio=True, gracia_s=gracia)
    if antes:
        antes(ent)
    import json
    res = ent.lanzar([sys.executable, str(HELPER)], entrada=json.dumps({"modo": modo, "pids": str(pids)}),
                     env={"PATH": __import__("os").environ.get("PATH", ""), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
                     plazo_s=plazo)
    return res, pids


def procesos_vivos(pids: Path) -> list[int]:
    vivos = []
    for f in pids.glob("*.pid"):
        pid = int(f.read_text())
        try:
            p = psutil.Process(pid)
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                vivos.append(pid)
        except psutil.NoSuchProcess:
            pass
    return vivos


def esperar_pids(pids: Path, n: int, t=15.0):
    fin = time.monotonic() + t
    while time.monotonic() < fin and len(list(pids.glob("*.pid"))) < n:
        time.sleep(0.05)


# ---------------------------------------------------------------- puerta de laboratorio
def test_sin_modo_laboratorio_no_se_puede_crear():
    with pytest.raises(LanzadorBloqueado):
        LanzadorLab()
    with pytest.raises(LanzadorBloqueado):
        LanzadorLab(modo_laboratorio=False)


def test_puede_lanzar_sigue_bloqueado_y_ningun_modulo_operativo_importa_el_lanzador():
    assert aislamiento.puede_lanzar()[0] is False
    assert not (RAIZ / "monitoreo" / "conector" / "lanzador_lab.py").exists()
    for f in (RAIZ / "monitoreo").rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            nombres = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                ([n.module or ""] + [a.name for a in n.names]) if isinstance(n, ast.ImportFrom) else []
            assert not any("lanzador_lab" in x for x in nombres), f"{f} importa el lanzador de laboratorio"


# ---------------------------------------------------------------- terminación normal
def test_termino_normal_sin_forzar_y_sin_huerfanos(tmp_path):
    r, pids = correr(tmp_path, "normal")
    assert (r.estado, r.codigo_salida) == ("TERMINO", 0) and "listo" in r.stdout
    assert not r.forzado and r.eliminados == 0 and r.sobrevivientes == []
    assert procesos_vivos(pids) == []


# ---------------------------------------------------------------- forzada
def test_plazo_agotado_mata_a_un_padre_que_ignora_la_terminacion(tmp_path):
    t0 = time.monotonic()
    r, pids = correr(tmp_path, "ignora_sigterm", plazo=2.0, gracia=0.5)
    assert r.estado == "PLAZO" and r.forzado and r.sobrevivientes == []
    assert time.monotonic() - t0 < 12
    assert procesos_vivos(pids) == []


def test_cancelacion_desde_otro_hilo_termina_el_arbol(tmp_path):
    ent = LanzadorLab(modo_laboratorio=True, gracia_s=0.5)
    threading.Timer(2.0, ent.cancelar).start()
    r, pids = correr(tmp_path, "ignora_sigterm", plazo=60, lanzador=ent)
    assert r.estado == "CANCELADO" and r.sobrevivientes == [] and procesos_vivos(pids) == []


# ---------------------------------------------------------------- hijos resistentes
def test_hijos_y_nietos_que_ignoran_la_terminacion_se_eliminan(tmp_path):
    r, pids = correr(tmp_path, "hijos_resistentes", plazo=4.0, gracia=0.5)
    assert r.estado == "PLAZO" and r.forzado
    assert len(list(pids.glob("*.pid"))) >= 7                      # padre + 3 hijos + 3 nietos (no se probó en vacío)
    assert r.sobrevivientes == [] and procesos_vivos(pids) == []


def test_desertor_que_sobrevive_al_padre_se_encuentra_y_se_elimina(tmp_path):
    r, pids = correr(tmp_path, "desertor", plazo=20.0)
    assert r.estado == "TERMINO" and r.codigo_salida == 0           # el padre acabó solo…
    assert len(list(pids.glob("desertor_*.pid"))) == 1              # …dejando un hijo huérfano separado
    assert r.eliminados >= 1 and r.sobrevivientes == [] and procesos_vivos(pids) == []


def test_proceso_que_sigue_lanzando_hijos_no_deja_ninguno(tmp_path):
    r, pids = correr(tmp_path, "spawner", plazo=2.0, gracia=0.3)
    assert r.estado == "PLAZO" and r.sobrevivientes == []
    time.sleep(0.5)
    assert procesos_vivos(pids) == []
    assert len(list(pids.glob("*.pid"))) >= 3


def test_la_salida_tiene_limite(tmp_path):
    import json
    ent = LanzadorLab(modo_laboratorio=True)
    codigo = "import sys; sys.stdout.write('x' * 3_000_000)"
    r = ent.lanzar([sys.executable, "-c", codigo], entrada=json.dumps({}), plazo_s=30)
    assert r.estado == "TERMINO" and len(r.stdout) <= 1_000_000

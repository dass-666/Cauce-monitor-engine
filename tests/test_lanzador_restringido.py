"""Lanzador como cuenta restringida (Windows) y sonda de aislamiento: lo que se puede comprobar fuera de Windows.

La prueba real (cuenta local + ACL + Firewall + CreateProcessWithLogonW) corre en el workflow
`.github/workflows/aislamiento.yml` sobre runners efímeros de Windows: scripts/aislamiento_windows_lanzador.py.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from monitoreo.autorrelleno import lanzador_restringido as LR
from monitoreo.autorrelleno.lanzador_lab import LanzadorBloqueado
from monitoreo.conector import aislamiento

RAIZ = Path(__file__).resolve().parents[1]


def test_exige_modo_laboratorio_y_windows():
    with pytest.raises(LanzadorBloqueado):
        LR.LanzadorRestringido(cuenta="c", clave="k")
    if sys.platform != "win32":
        with pytest.raises(LanzadorBloqueado, match="solo existe en Windows"):
            LR.LanzadorRestringido(cuenta="c", clave="k", modo_laboratorio=True)


def test_puede_lanzar_sigue_bloqueado_y_solo_el_autorrellenado_importa_el_lanzador_restringido():
    assert aislamiento.puede_lanzar()[0] is False
    for f in (RAIZ / "monitoreo").rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            nombres = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                ([n.module or ""] + [a.name for a in n.names]) if isinstance(n, ast.ImportFrom) else []
            if any("lanzador_restringido" in x for x in nombres):
                assert f.parent == RAIZ / "monitoreo" / "autorrelleno", f"{f} importa el lanzador restringido"


def test_bloque_de_entorno_windows():
    b = LR.bloque_entorno({"b": "2", "A": "1", "PATH": r"C:\x"})
    assert b == "A=1\0b=2\0PATH=C:\\x\0\0"
    assert LR.bloque_entorno({}) == "\0\0"
    for malo in ({"": "x"}, {"A=B": "x"}, {"A": "x\0y"}):
        with pytest.raises(ValueError):
            LR.bloque_entorno(malo)


def test_linea_de_comando_con_espacios_y_sin_shell():
    assert LR.linea_de_comando([r"C:\Program Files\py\python.exe", "-m", "x y"]) == \
        r'"C:\Program Files\py\python.exe" -m "x y"'
    with pytest.raises(ValueError):
        LR.linea_de_comando([])


def test_la_prueba_de_extremo_a_extremo_se_niega_fuera_de_un_equipo_efimero():
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "aislamiento_windows_lanzador.py"), "--salida",
                        "no_debe_existir.json"], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "CAUCE_AISLAMIENTO_EFIMERO"})
    assert r.returncode == 2
    assert not Path("no_debe_existir.json").exists()


def test_control_negativo_sin_acl_la_sonda_detecta_la_fuga(tmp_path):
    """Sin cuenta aparte ni ACL (aquí, el mismo usuario), la sonda DEBE marcar la base, .env y respaldos como legibles.
    Así se sabe que un «ok» en Windows no es porque la sonda no mire."""
    for d in ("data", "respaldos", "diario", "conector/codigo", "conector/trabajo"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "data" / "monitoreo.db").write_text("ficticio")
    (tmp_path / "respaldos" / "copia.db").write_text("ficticio")
    (tmp_path / "diario" / "clave_diario.key").write_text("ficticio")
    (tmp_path / ".env").write_text("X=1")
    (tmp_path / "conector" / "codigo" / "leeme.txt").write_text("x")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    r = subprocess.run([sys.executable, str(RAIZ / "scripts" / "aislamiento_sonda.py"), "pruebas", str(tmp_path),
                        str(srv.getsockname()[1])], input=b"hola", capture_output=True, timeout=60)
    srv.close()
    s = json.loads(r.stdout)
    fallidas = {p["prueba"] for p in s["pruebas"] if not p["ok"]}
    for clave in ("A1 leer data/monitoreo.db", "A3 leer .env", "A4 leer respaldos/copia.db", "A4b leer la clave del diario"):
        assert clave in fallidas                    # mismo usuario: todo legible = el aislamiento NO existe
    assert {"data/monitoreo.db", ".env", "respaldos/copia.db", "diario/clave_diario.key"} <= \
        {x.replace("\\", "/") for x in s["barrido"]["legibles_fuera_de_conector"]}
    assert all(p["ok"] for p in s["pruebas"] if p["prueba"].startswith(("A6", "A7")))
    import hashlib
    assert s["stdin_sha256"] == hashlib.sha256(b"hola").hexdigest()

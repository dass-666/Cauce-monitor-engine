"""El portal del laboratorio conserva su puerto al reabrir la misma carpeta.

El motor registra en su base la identidad del portal (host y PUERTO). Con un puerto distinto en cada
arranque, al reabrir la carpeta el motor veía «otro portal» (OTRO_PROVEEDOR) y no preparaba citas.
"""
import json
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from ventanilla_laboratorio import Laboratorio  # noqa: E402


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _base_con_identidad(carpeta: Path, destino: str) -> None:
    con = sqlite3.connect(carpeta / "cauce_lab.db")
    con.execute("create table settings (key text primary key, value text)")
    con.execute("insert into settings values ('proveedor.identidad', ?)",
                (json.dumps({"id": "x", "destino": destino, "contratos": []}),))
    con.commit()
    con.close()


def test_sin_historial_no_hay_preferencia(tmp_path):
    assert Laboratorio(tmp_path)._puerto_portal_estable() is None


def test_reutiliza_el_puerto_registrado_en_la_base(tmp_path):
    p = _puerto_libre()
    _base_con_identidad(tmp_path, f"http://127.0.0.1:{p}")
    assert Laboratorio(tmp_path)._puerto_portal_estable() == p


def test_reutiliza_el_ultimo_puerto_guardado(tmp_path):
    p = _puerto_libre()
    (tmp_path / "laboratorio.json").write_text(json.dumps({"token": "a", "token_control": "b", "secret": "c",
                                                           "puerto_portal": p}), "utf-8")
    assert Laboratorio(tmp_path)._puerto_portal_estable() == p


def test_puerto_ocupado_no_se_usa(tmp_path):
    with socket.socket() as ocupado:
        ocupado.bind(("127.0.0.1", 0))
        p = ocupado.getsockname()[1]
        _base_con_identidad(tmp_path, f"http://127.0.0.1:{p}")
        assert Laboratorio(tmp_path)._puerto_portal_estable() is None


def test_destino_que_no_es_local_se_ignora(tmp_path):
    _base_con_identidad(tmp_path, "https://portal.ejemplo.test:443")
    assert Laboratorio(tmp_path)._puerto_registrado() is None

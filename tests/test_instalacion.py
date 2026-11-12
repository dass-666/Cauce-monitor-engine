"""Instalación y actualización (v1.2): scripts/preparar.py nunca sobrescribe ni pierde datos."""
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "preparar.py"


def run(folder: Path, *args: str) -> subprocess.CompletedProcess:
    # -I: modo aislado; el ayudante debe funcionar sin dependencias instaladas.
    # preparar.py escribe en UTF-8 cuando su salida está redirigida en Windows (ver su final).
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--carpeta", str(folder), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


def fake_install(folder: Path, rows: int = 50, port: int | None = None, wal: bool = False) -> Path:
    (folder / "monitoreo").mkdir(parents=True)
    (folder / "data" / "logs").mkdir(parents=True)
    (folder / "data" / "logs" / "monitoreo.log").write_text("registro\n", encoding="utf-8")
    (folder / ".env").write_text(f"MONITOREO_ADMIN_PASSWORD=Ficticia-123\nMONITOREO_PUERTO={port or free_port()}\n",
                                 encoding="utf-8")
    db = folder / "data" / "monitoreo.db"
    con = sqlite3.connect(db)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, code TEXT)")
    con.executemany("INSERT INTO requests (code) VALUES (?)", [(f"SOL-{i}",) for i in range(rows)])
    con.commit()
    con.close()
    return db


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def count(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    finally:
        con.close()


def test_requirements_check_passes_on_clean_folder(tmp_path):
    folder = tmp_path / "motor"
    folder.mkdir()
    (folder / ".env").write_text(f"MONITOREO_PUERTO={free_port()}\n", encoding="utf-8")
    r = run(folder, "requisitos")
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert "Python" in r.stdout and "Se puede continuar" in r.stdout


def test_requirements_detects_port_in_use(tmp_path):
    folder = tmp_path / "motor"
    folder.mkdir()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        (folder / ".env").write_text(f"MONITOREO_PUERTO={busy.getsockname()[1]}\n", encoding="utf-8")
        r = run(folder, "requisitos")
    assert "lo usa otro programa" in r.stdout and r.returncode == 1


def test_copy_data_from_previous_install_is_verified_and_leaves_original_untouched(tmp_path):
    old, new = tmp_path / "v11", tmp_path / "v12"
    old_db = fake_install(old, rows=500, wal=True)
    before = old_db.read_bytes()
    (new / "monitoreo").mkdir(parents=True)
    r = run(new, "copiar-datos", "--desde", str(old))
    assert r.returncode == 0, r.stdout + r.stderr
    assert count(new / "data" / "monitoreo.db") == 500
    assert (new / ".env").read_text(encoding="utf-8") == (old / ".env").read_text(encoding="utf-8")
    assert (new / "data" / "logs" / "monitoreo.log").exists() and (new / "data" / "COPIADO_DESDE.txt").exists()
    assert old_db.read_bytes() == before and count(old_db) == 500           # la anterior no se toca
    assert "no se ha modificado" in r.stdout


def test_copy_data_refuses_to_overwrite(tmp_path):
    old, new = tmp_path / "v11", tmp_path / "v12"
    fake_install(old, rows=10)
    fake_install(new, rows=3)                                                # la nueva ya tiene datos
    r = run(new, "copiar-datos", "--desde", str(old))
    assert r.returncode == 2 and "no se sobrescriben" in r.stdout
    assert count(new / "data" / "monitoreo.db") == 3


def test_copy_data_refuses_same_folder_and_non_install(tmp_path):
    old = tmp_path / "v11"
    fake_install(old)
    assert run(old, "copiar-datos", "--desde", str(old)).returncode == 2
    other = tmp_path / "documentos"
    other.mkdir()
    new = tmp_path / "v12"
    (new / "monitoreo").mkdir(parents=True)
    r = run(new, "copiar-datos", "--desde", str(other))
    assert r.returncode == 2 and "no parece una instalación" in r.stdout


def test_copy_data_refuses_while_database_in_use(tmp_path):
    old, new = tmp_path / "v11", tmp_path / "v12"
    db = fake_install(old)
    (new / "monitoreo").mkdir(parents=True)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")                                        # simula el motor escribiendo
    try:
        r = run(new, "copiar-datos", "--desde", str(old))
    finally:
        holder.rollback()
        holder.close()
    assert r.returncode == 2 and "en uso" in r.stdout
    assert not (new / "data" / "monitoreo.db").exists()


def test_backup_before_reinstall_in_place(tmp_path):
    folder = tmp_path / "motor"
    fake_install(folder, rows=42)
    r = run(folder, "respaldar")
    assert r.returncode == 0, r.stdout + r.stderr
    [dest] = list((folder / "respaldos-instalacion").iterdir())
    assert count(dest / "data" / "monitoreo.db") == 42 and (dest / ".env").exists()
    assert not (dest / "data" / "logs").exists()                             # los registros no se duplican
    assert count(folder / "data" / "monitoreo.db") == 42


def test_backup_on_fresh_folder_does_nothing(tmp_path):
    r = run(tmp_path, "respaldar")
    assert r.returncode == 0 and "No hay datos" in r.stdout
    assert not (tmp_path / "respaldos-instalacion").exists()


def _crear_env(tmp_path, **env):
    import os
    import shutil
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "crear_env.py", tmp_path / "scripts")
    shutil.copy(ROOT / ".env.ejemplo", tmp_path)
    return subprocess.run([sys.executable, "-I", str(tmp_path / "scripts" / "crear_env.py")], capture_output=True,
                          text=True, timeout=60, env={**os.environ, **env})


def test_production_env_file_passes_production_review(tmp_path, monkeypatch):
    r = _crear_env(tmp_path, MON_PWD="Produccion-Ficticia-1", MON_ENTORNO="produccion")
    assert r.returncode == 0, r.stderr
    import os
    from monitoreo import entorno
    for k in [k for k in os.environ if k.startswith("MONITOREO_")]:
        monkeypatch.delenv(k)                       # se restauran al terminar la prueba
    for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            monkeypatch.setenv(k.strip(), v.strip())  # setenv: al terminar se eliminan
    rev = entorno.review()
    assert rev.entorno == "produccion" and not rev.errores, rev.as_dict()
    # no se sobrescribe un .env existente
    r2 = subprocess.run([sys.executable, "-I", str(tmp_path / "scripts" / "crear_env.py")], capture_output=True,
                        text=True, timeout=60, env={**{k: v for k, v in os.environ.items() if k != "MON_ENTORNO"}, "MON_PWD": "Otra-Clave-12345"})
    assert r2.returncode != 0 and "no se sobrescribe" in r2.stderr


def test_production_env_rejects_short_password(tmp_path):
    r = _crear_env(tmp_path, MON_PWD="Corta-12345", MON_ENTORNO="produccion")      # 11 caracteres
    assert r.returncode != 0 and "12 caracteres" in r.stderr and not (tmp_path / ".env").exists()


def test_env_file_saved_with_bom_by_notepad_is_read(tmp_path, monkeypatch):
    """Regresión v1.2: el Bloc de notas de Windows 10 guarda UTF-8 con BOM y la primera clave se perdía."""
    from monitoreo.config import _load_dotenv
    monkeypatch.delenv("MONITOREO_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("MONITOREO_ADMIN_USER", raising=False)
    p = tmp_path / ".env"
    p.write_bytes("﻿MONITOREO_ADMIN_PASSWORD=Con-Bom-12345\nMONITOREO_ADMIN_USER=ñandú\n".encode("utf-8"))
    try:
        _load_dotenv(p)
        import os
        assert os.environ.get("MONITOREO_ADMIN_PASSWORD") == "Con-Bom-12345"
        assert os.environ.get("MONITOREO_ADMIN_USER") == "ñandú"
    finally:
        import os
        os.environ.pop("﻿MONITOREO_ADMIN_PASSWORD", None)
        os.environ.pop("MONITOREO_ADMIN_PASSWORD", None)
        os.environ.pop("MONITOREO_ADMIN_USER", None)

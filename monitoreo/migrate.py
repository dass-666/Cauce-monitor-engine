"""Migraciones de esquema (Alembic) con respaldo previo.

Reglas de seguridad:
- Nunca se borra ni se reemplaza una base existente sin respaldarla antes (restaurar guarda la actual).
- Antes de cambiar el esquema de una base con datos se hace un respaldo:
    SQLite      -> copia consistente con la API de respaldo de SQLite (data/respaldos/*.db)
    PostgreSQL  -> pg_dump en formato custom (data/respaldos/*.dump). Si pg_dump no está
                   disponible la migración se DETIENE, salvo MONITOREO_RESPALDO_PG=omitir.
- Una base creada con la v1.0 (sin tabla alembic_version) se reconoce, se comprueba que
  tiene las tablas de la v1.0 y se marca como revisión 0001 antes de actualizar.
- Dos procesos que arranquen a la vez no migran en paralelo: SQLite serializa con
  BEGIN IMMEDIATE y PostgreSQL usa un candado consultivo (pg_advisory_xact_lock).

Uso por consola:
    python -m monitoreo.migrate estado
    python -m monitoreo.migrate actualizar
    python -m monitoreo.migrate respaldar
    python -m monitoreo.migrate revertir 0001_v1_0     (hace respaldo antes)
    python -m monitoreo.migrate restaurar data/respaldos/<archivo>.db   (motor detenido; guarda la actual)
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url

from .config import settings

log = logging.getLogger("monitoreo.migraciones")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
V1_0_REVISION = "0001_v1_0"
V1_0_TABLES = {"users", "api_tokens", "audit_log", "settings", "regions", "sources", "slot_states", "poll_log",
               "change_events", "operators", "requests", "request_events", "assignments", "channels", "alerts",
               "deliveries", "notification_log", "sim_items", "sim_runs"}
PG_LOCK_ID = 727274  # identificador arbitrario del candado consultivo


class MigrationError(Exception):
    pass


@dataclass
class MigrationReport:
    before: str | None
    after: str | None
    legacy_detected: bool = False
    backup: str | None = None
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def _engine(url: str):
    """Motor dedicado a migraciones. En SQLite sin claves foráneas activas (necesario para que
    Alembic pueda recrear tablas en modo batch) y con BEGIN IMMEDIATE para serializar."""
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"timeout": 60})

        @event.listens_for(eng, "connect")
        def _c(dbapi_conn, _):
            dbapi_conn.isolation_level = None

        @event.listens_for(eng, "begin")
        def _b(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return eng
    return create_engine(url)


def inspect_state(url: str) -> dict:
    eng = _engine(url)
    try:
        with eng.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            current = MigrationContext.configure(conn).get_current_revision()
            rows = 0
            if "requests" in tables:
                rows = conn.execute(text("SELECT COUNT(*) FROM requests")).scalar()
    finally:
        eng.dispose()
    return {"tablas": len(tables), "revision_actual": current, "revision_objetivo": head_revision(),
            "base_v1_0_sin_versionar": current is None and V1_0_TABLES <= tables,
            "vacia": not (tables - {"alembic_version"}), "solicitudes": rows,
            "tablas_desconocidas_v1_0": sorted(V1_0_TABLES - tables) if current is None and tables else []}


# ---------------------------------------------------------------- respaldo
def backup(url: str, label: str = "") -> str:
    dest_dir = settings.data_dir / "respaldos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    if url.startswith("sqlite"):
        src_path = make_url(url).database
        if not src_path or src_path == ":memory:":
            raise MigrationError("No se puede respaldar una base en memoria")
        dest = dest_dir / f"monitoreo-{stamp}{('-' + label) if label else ''}.db"
        src = sqlite3.connect(src_path, timeout=60)
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)  # copia consistente aunque haya otro proceso leyendo/escribiendo
        finally:
            dst.close()
            src.close()
        _verify_sqlite_backup(dest)
        return str(dest)
    # PostgreSQL
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        if os.environ.get("MONITOREO_RESPALDO_PG", "").lower() == "omitir":
            log.warning("pg_dump no disponible: migrando SIN respaldo por MONITOREO_RESPALDO_PG=omitir")
            return "omitido"
        raise MigrationError("pg_dump no está instalado: no se migra sin respaldo. Instálalo o define "
                             "MONITOREO_RESPALDO_PG=omitir bajo tu responsabilidad.")
    u = make_url(url)
    dest = dest_dir / f"monitoreo-{stamp}{('-' + label) if label else ''}.dump"
    env = {**os.environ}
    if u.password:
        env["PGPASSWORD"] = u.password  # por entorno: nunca en la línea de comandos visible
    args = [pg_dump, "-Fc", "-f", str(dest), "-h", u.host or "localhost", "-p", str(u.port or 5432),
            "-U", u.username or "postgres", u.database]
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise MigrationError(f"pg_dump falló: {r.stderr.strip()[:300]}")
    return str(dest)


def _verify_sqlite_backup(path: Path) -> None:
    try:
        c = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        ok = str(e)
    if ok != "ok":
        raise MigrationError(f"El respaldo {path} no pasó la verificación de integridad: {ok}")


# ---------------------------------------------------------------- restauración
def restore(url: str, backup_file: str, force_running: bool = False) -> dict:
    """Restaura un respaldo SQLite de forma segura (v1.4).

    Copiar el archivo encima de `monitoreo.db` (el método que documentaba la v1.3) es inseguro:
    si quedó un `monitoreo.db-wal` de un cierre brusco, SQLite vuelve a aplicar esas páginas
    sobre el respaldo y la base "restaurada" ya no es el respaldo (puede incluso dañarse).
    Aquí la copia se hace con la API de respaldo de SQLite a través de una conexión normal,
    que tiene en cuenta el WAL, y antes se guarda la base actual.
    """
    if not url.startswith("sqlite"):
        raise MigrationError("Con PostgreSQL usa: pg_restore --clean -d <base> data/respaldos/<archivo>.dump "
                             "(con el motor detenido). Ver README, apartado Respaldos.")
    src = Path(backup_file)
    if not src.is_file():
        raise MigrationError(f"No existe el respaldo {src}")
    _verify_sqlite_backup(src)
    target = make_url(url).database
    if not target or target == ":memory:":
        raise MigrationError("No se puede restaurar sobre una base en memoria")
    if Path(target).resolve() == src.resolve():
        raise MigrationError("El respaldo y la base de destino son el mismo archivo")
    report = {"restaurado": str(src), "destino": target, "respaldo_previo": None}
    if Path(target).exists():
        c = sqlite3.connect(target, timeout=30)
        try:
            row = None
            try:
                row = c.execute("SELECT holder, expires_at FROM scheduler_leases "
                                "WHERE name='planificador'").fetchone()
            except sqlite3.DatabaseError:
                pass
        finally:
            c.close()
        if row and not force_running:
            from .db import utcnow
            if str(row[1]) > utcnow().isoformat(sep=" "):
                raise MigrationError(f"El motor parece estar en marcha (planificador de {row[0]}). "
                                     "Ciérralo y espera 15 s antes de restaurar.")
        report["respaldo_previo"] = backup(url, label="antes-de-restaurar")
    dst = sqlite3.connect(target, timeout=60)
    s = sqlite3.connect(src)
    try:
        s.backup(dst)
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        s.close()
        dst.close()
    if ok != "ok":
        raise MigrationError(f"La base restaurada no pasó la verificación: {ok}. "
                             f"La anterior está en {report['respaldo_previo']}")
    report["revision"] = inspect_state(url)["revision_actual"]
    return report


# ---------------------------------------------------------------- actualización / reversión
def _run(url: str, fn) -> None:
    eng = _engine(url)
    try:
        with eng.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": PG_LOCK_ID})
            cfg = _config()
            cfg.attributes["connection"] = conn
            fn(cfg, conn)
    finally:
        eng.dispose()


def upgrade(url: str, target: str = "head", make_backup: bool = True) -> MigrationReport:
    state = inspect_state(url)
    report = MigrationReport(before=state["revision_actual"], after=None)
    if state["revision_actual"] is None and not state["vacia"] and not state["base_v1_0_sin_versionar"]:
        raise MigrationError("La base tiene tablas pero no es una v1.0 reconocible; faltan: "
                             f"{state['tablas_desconocidas_v1_0']}. No se modifica nada.")
    target_rev = head_revision() if target == "head" else target
    needs_change = state["revision_actual"] != target_rev
    if needs_change and make_backup and not state["vacia"]:
        report.backup = backup(url, label=f"antes-de-{target_rev}")
        report.steps.append(f"respaldo: {report.backup}")

    def _do(cfg, conn):
        current = MigrationContext.configure(conn).get_current_revision()
        tables = set(inspect(conn).get_table_names())
        if current is None and V1_0_TABLES <= tables:
            command.stamp(cfg, V1_0_REVISION)  # base v1.0: se marca su versión sin tocar datos
            report.legacy_detected = True
            report.steps.append("base v1.0 reconocida y marcada como 0001_v1_0")
        command.upgrade(cfg, target)

    if needs_change:
        _run(url, _do)
        report.steps.append(f"actualizada a {target_rev}")
    report.after = inspect_state(url)["revision_actual"]
    return report


def downgrade(url: str, target: str, make_backup: bool = True) -> MigrationReport:
    """Reversión. Solo es segura si aceptas perder lo que añadió la versión revertida
    (estadísticas agregadas, arrendamientos, registro de mantenimiento); por eso siempre respalda."""
    state = inspect_state(url)
    report = MigrationReport(before=state["revision_actual"], after=None)
    if make_backup:
        report.backup = backup(url, label=f"antes-de-revertir-a-{target}")
    _run(url, lambda cfg, conn: command.downgrade(cfg, target))
    report.after = inspect_state(url)["revision_actual"]
    return report


def main(argv: list[str]) -> int:
    url = settings.db_url
    cmd = argv[0] if argv else "estado"
    try:
        if cmd == "estado":
            print(inspect_state(url))
        elif cmd == "actualizar":
            print(upgrade(url).as_dict())
        elif cmd == "respaldar":
            print(backup(url, "manual"))
        elif cmd == "restaurar" and len(argv) in (2, 3):
            print(restore(url, argv[1], force_running="--forzar" in argv[2:]))
        elif cmd == "revertir" and len(argv) == 2:
            print(downgrade(url, argv[1]).as_dict())
        else:
            print(__doc__)
            return 2
    except MigrationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

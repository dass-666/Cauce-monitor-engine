"""Conexión a la base de datos (SQLite o PostgreSQL) con SQLAlchemy 2.

- SQLite: opción por defecto, cero instalación (ideal para Windows y desarrollo).
  Se activa WAL (lecturas concurrentes) y `BEGIN IMMEDIATE` para que cada
  transacción tome el candado de escritura al empezar y no falle a mitad.
- PostgreSQL: recomendado en producción (varios procesos, más volumen).

Las garantías de "no asignar dos veces" NO dependen del motor de base de datos:
se basan en UPDATE condicionales atómicos + restricciones UNIQUE, que funcionan
igual en ambos.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Hora UTC sin zona (convención interna: todo se guarda en UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":///" in url and not url.endswith(":memory:"):
            from pathlib import Path
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            dbapi_conn.isolation_level = None  # control manual de transacciones
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return eng
    return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)


class Database:
    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    def create_all(self) -> None:
        from . import models  # noqa: F401  (registra tablas)
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        from . import models  # noqa: F401
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

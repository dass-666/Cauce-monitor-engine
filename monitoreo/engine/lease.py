"""Coordinación entre instancias con un "arrendamiento" (lease) en la base de datos.

Problema: si se arrancan dos procesos contra la misma base (dos ventanas de iniciar.bat,
un servicio y una prueba manual...), ambos consultarían las mismas fuentes, duplicando la
carga sobre los proveedores y compitiendo por el trabajo.

Solución: una fila `scheduler_leases(name='planificador')` con titular y vencimiento.
Solo la instancia que la tiene vigente ejecuta el planificador; las demás sirven el panel y
la API y esperan. El titular la renueva en cada ciclo; si muere, la fila vence y otra
instancia la toma (recuperación automática). Adquirir y renovar es UN `UPDATE` condicional,
atómico en SQLite y PostgreSQL. `fencing_token` aumenta con cada cambio de titular.

Margen de seguridad: el titular se considera líder solo hasta `vencimiento - ttl/3`, para que
un proceso lento deje de escribir antes de que otro pueda tomar el relevo. Supone relojes
sincronizados entre máquinas (en un solo equipo se cumple trivialmente).
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import case, update
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..models import SchedulerLease


def make_holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Lease:
    def __init__(self, db: Database, name: str = "planificador", holder: str | None = None, ttl_s: float = 15):
        self.db, self.name, self.ttl_s = db, name, ttl_s
        self.holder = holder or make_holder_id()
        self.valid_until: datetime | None = None
        self.fencing_token: int | None = None

    def acquire_or_renew(self, now: datetime) -> bool:
        exp = now + timedelta(seconds=self.ttl_s)
        L = SchedulerLease
        with self.db.session() as s:
            r = s.execute(update(L).where(L.name == self.name, (L.holder == self.holder) | (L.expires_at < now))
                          .values(acquired_at=case((L.holder == self.holder, L.acquired_at), else_=now),
                                  fencing_token=case((L.holder == self.holder, L.fencing_token),
                                                     else_=L.fencing_token + 1),
                                  holder=self.holder, expires_at=exp, renewed_at=now)).rowcount
            if r == 1:
                self.fencing_token = s.get(L, self.name).fencing_token
                self.valid_until = now + timedelta(seconds=self.ttl_s * 2 / 3)
                return True
            if s.get(L, self.name) is None:
                try:
                    with s.begin_nested():
                        s.add(L(name=self.name, holder=self.holder, acquired_at=now, expires_at=exp,
                                renewed_at=now, fencing_token=1))
                        s.flush()
                    self.fencing_token = 1
                    self.valid_until = now + timedelta(seconds=self.ttl_s * 2 / 3)
                    return True
                except IntegrityError:
                    pass
        self.valid_until = None
        return False

    def is_valid(self, now: datetime) -> bool:
        return self.valid_until is not None and now < self.valid_until

    def release(self, now: datetime) -> None:
        with self.db.session() as s:
            s.execute(update(SchedulerLease).where(SchedulerLease.name == self.name,
                                                   SchedulerLease.holder == self.holder)
                      .values(expires_at=now - timedelta(seconds=1)))
        self.valid_until = None

    def info(self) -> dict | None:
        with self.db.session() as s:
            row = s.get(SchedulerLease, self.name)
            if row is None:
                return None
            return {"titular": row.holder, "soy_titular": row.holder == self.holder,
                    "adquirido": row.acquired_at.isoformat() + "Z", "vence": row.expires_at.isoformat() + "Z",
                    "renovado": row.renewed_at.isoformat() + "Z", "fencing_token": row.fencing_token}

"""Laboratorio: mundo de disponibilidad ficticio.

Todos los nombres, eventos, titulares y lugares son inventados. El mundo vive en
la tabla `sim_items`; los conectores de simulación y la API de prueba local lo
leen exactamente como leerían una fuente real.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import Database
from ..models import SimItem

REGIONS = [
    ("NORTE", "Región Norte (ficticia)"), ("SUR", "Región Sur (ficticia)"), ("ESTE", "Región Este (ficticia)"),
    ("OESTE", "Región Oeste (ficticia)"), ("CENTRO", "Región Centro (ficticia)"),
]
# (región, tipo de disponibilidad, tipo de recurso, prefijo de nombres)
FEEDS = [
    ("NORTE", "evento", "entrada", ["Concierto Aurora", "Festival Brisa", "Gala Cometa", "Teatro Lumen"]),
    ("SUR", "reservacion", "mesa", ["Mesa Terraza", "Mesa Salón", "Mesa Jardín", "Mesa Barra"]),
    ("ESTE", "inventario", "unidad", ["Kit Alfa", "Kit Beta", "Kit Gamma", "Kit Delta"]),
    ("OESTE", "calendario", "turno", ["Turno Consulta A", "Turno Consulta B", "Turno Taller", "Turno Asesoría"]),
    ("CENTRO", "evento", "entrada", ["Feria Nébula", "Expo Prisma", "Foro Atlas", "Muestra Orión"]),
]


def feed_code(region: str) -> str:
    return f"sim-{region.lower()}"


@dataclass
class SimChange:
    kind: str
    feed: str
    resource_key: str
    detail: dict = field(default_factory=dict)


class World:
    def __init__(self, db: Database, clock=None, rng: random.Random | None = None):
        from ..engine.clock import RealClock
        self.db = db
        self.clock = clock or RealClock()
        self.rng = rng or random.Random()

    # ---------------------------------------------------------------- creación
    def create_item(self, feed: str, resource_key: str, name: str, region: str, resource_type: str,
                    capacity: int, available: int = 0, starts_at: datetime | None = None) -> SimItem:
        with self.db.session() as s:
            item = SimItem(feed=feed, resource_key=resource_key, name=name, region=region,
                           resource_type=resource_type, capacity=capacity,
                           available=max(0, min(available, capacity)), starts_at=starts_at,
                           changed_at=self.clock.now())
            s.add(item)
            s.flush()
            return item

    def seed_feed(self, region: str, resource_type: str, names: list[str], per_name: int = 3,
                  start_available: bool = False) -> int:
        n = 0
        base = self.clock.now().replace(minute=0, second=0, microsecond=0)
        feed = feed_code(region)
        for i, nm in enumerate(names):
            for k in range(per_name):
                cap = self.rng.choice([2, 4, 6, 8, 10])
                self.create_item(feed, f"{region[:2]}-{i}{k}", f"{nm} · sesión {k + 1}", region, resource_type,
                                 capacity=cap, available=self.rng.randint(0, cap) if start_available else 0,
                                 starts_at=base + timedelta(days=1 + i, hours=10 + 3 * k))
                n += 1
        return n

    # ---------------------------------------------------------------- operaciones manuales
    def _get(self, s, item_id: int) -> SimItem:
        item = s.get(SimItem, item_id)
        if item is None:
            raise KeyError(f"recurso simulado {item_id} no existe")
        return item

    def release(self, item_id: int, units: int = 1) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            before = it.available
            it.cancelled = False
            it.available = min(it.capacity, it.available + max(1, units))
            it.changed_at = self.clock.now()
            return SimChange("LIBERACION", it.feed, it.resource_key, {"antes": before, "despues": it.available})

    def exhaust(self, item_id: int) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            before, it.available, it.changed_at = it.available, 0, self.clock.now()
            return SimChange("AGOTAMIENTO", it.feed, it.resource_key, {"antes": before, "despues": 0})

    def consume(self, item_id: int, units: int = 1) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            before = it.available
            it.available = max(0, it.available - units)
            it.changed_at = self.clock.now()
            return SimChange("CONSUMO", it.feed, it.resource_key, {"antes": before, "despues": it.available})

    def cancel(self, item_id: int) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            it.cancelled, it.changed_at = True, self.clock.now()
            return SimChange("CANCELACION", it.feed, it.resource_key)

    def reschedule(self, item_id: int, hours: float) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            it.starts_at = (it.starts_at or self.clock.now()) + timedelta(hours=hours)
            it.changed_at = self.clock.now()
            return SimChange("CAMBIO_HORARIO", it.feed, it.resource_key, {"inicio": it.starts_at.isoformat()})

    def restore(self, item_id: int) -> SimChange:
        with self.db.session() as s:
            it = self._get(s, item_id)
            it.cancelled, it.changed_at = False, self.clock.now()
            return SimChange("RESTAURACION", it.feed, it.resource_key)

    # ---------------------------------------------------------------- dinámica aleatoria
    def random_step(self, intensity: float = 1.0, burst: bool = False) -> list[SimChange]:
        """Un paso del mundo: liberaciones, consumos, agotamientos, cancelaciones y cambios de horario.

        `burst=True` simula un pico de demanda: muchos cambios simultáneos en varias regiones.
        """
        with self.db.session() as s:
            ids = list(s.scalars(select(SimItem.id)))
        if not ids:
            return []
        changes: list[SimChange] = []
        n = max(1, int(self.rng.expovariate(1 / (3 * intensity)))) if not burst else max(5, len(ids) // 3)
        for item_id in self.rng.sample(ids, min(n, len(ids))):
            r = self.rng.random()
            if r < 0.40:
                changes.append(self.release(item_id, self.rng.randint(1, 3)))
            elif r < 0.70:
                changes.append(self.consume(item_id, self.rng.randint(1, 2)))
            elif r < 0.82:
                changes.append(self.exhaust(item_id))
            elif r < 0.88:
                changes.append(self.cancel(item_id))
            elif r < 0.94:
                changes.append(self.restore(item_id))
            else:
                changes.append(self.reschedule(item_id, self.rng.choice([-2, 1, 3])))
        return changes

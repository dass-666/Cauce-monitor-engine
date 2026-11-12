"""Datos iniciales ficticios: 5 regiones, fuentes, operadores, solicitudes y canales."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from sqlalchemy import select

from ..db import Database
from ..models import Channel, Operator, Region, Request, Source
from .world import FEEDS, REGIONS, World, feed_code

HOLDERS = ["Expediente Lince", "Expediente Ceiba", "Expediente Quetzal", "Expediente Jaguar", "Expediente Colibrí",
           "Expediente Nopal", "Expediente Ámbar", "Expediente Jade", "Expediente Ópalo", "Expediente Cobre"]


def seed_base(db: Database) -> None:
    """Regiones y canales mínimos (siempre necesarios)."""
    with db.session() as s:
        for code, name in REGIONS:
            if s.get(Region, code) is None:
                s.add(Region(code=code, name=name))
        if not s.scalar(select(Channel.id).limit(1)):
            s.add(Channel(name="Panel", type="panel", enabled=True))
            s.add(Channel(name="Registro", type="registro", enabled=True))
            s.add(Channel(name="Simulado", type="simulado", config={"fail_rate": 0.0}, enabled=True))
            s.add(Channel(name="Webhook local", type="webhook",
                          config={"url": "http://127.0.0.1:8000/api-prueba/webhook"}, enabled=False))
            s.add(Channel(name="Telegram", type="telegram", config={}, enabled=False))
            s.add(Channel(name="Correo", type="correo", config={}, enabled=False))


def seed_demo(db: Database, rng: random.Random | None = None, requests_per_region: int = 8,
              interval_s: float = 5, with_file_sources: bool = True, port: int = 8000, clock=None) -> None:
    rng = rng or random.Random(42)
    seed_base(db)
    with db.session() as s:
        if s.scalar(select(Source.id).limit(1)):
            return  # ya sembrado
    world = World(db, clock=clock, rng=rng)
    with db.session() as s:
        for region, atype, rtype, names in FEEDS:
            s.add(Source(code=feed_code(region), name=f"Simulador {region.title()} ({atype})", region=region,
                         category=atype, availability_type=atype, connector_type="simulacion",
                         config={"feed": feed_code(region), "error_rate": 0.02, "rate_limit_rate": 0.0},
                         interval_s=interval_s, jitter_pct=0.15, rate_limit_per_min=30, max_retries=2))
        for i in range(1, 7):
            regions = [REGIONS[(i - 1) % 5][0]] if i <= 5 else []
            s.add(Operator(name=f"Operador {i:02d}" + ("" if regions else " (todas)"), regions=regions,
                           max_concurrent=3, active=True))
    for region, _atype, rtype, names in FEEDS:
        world.seed_feed(region, rtype, names, per_name=3, start_available=False)
    now = world.clock.now()
    with db.session() as s:
        n = 0
        for region, _atype, rtype, _ in FEEDS:
            for k in range(requests_per_region):
                n += 1
                s.add(Request(code=f"SOL-{n:04d}", holder=f"{rng.choice(HOLDERS)} {n:03d}", region=region,
                              resource_type=rtype, priority=rng.choice([1, 2, 3, 3, 4, 5]),
                              quantity=rng.choice([1, 1, 1, 2]), conditions={}, status="PENDIENTE",
                              created_at=now - timedelta(minutes=rng.randint(1, 600))))
    if with_file_sources:
        _seed_file_sources(db, port)


def _seed_file_sources(db: Database, port: int) -> None:
    from ..connectors.files import feeds_dir
    d = feeds_dir()
    (d / "centro_calendario.json").write_text(json.dumps({"complete": True, "items": [
        {"resource_key": "CJ-1", "name": "Agenda Ficticia · bloque 1", "resource_type": "turno", "available": 0,
         "capacity": 3},
        {"resource_key": "CJ-2", "name": "Agenda Ficticia · bloque 2", "resource_type": "turno", "available": 1,
         "capacity": 3}]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "norte_inventario.csv").write_text(
        "clave,nombre,tipo,disponibles,capacidad,estado\n"
        "NC-1,Lote Ficticio A,unidad,0,5,ACTIVO\nNC-2,Lote Ficticio B,unidad,2,5,ACTIVO\n", encoding="utf-8")
    with db.session() as s:
        s.add(Source(code="json-centro", name="Archivo JSON Centro", region="CENTRO", category="calendario",
                     availability_type="calendario", connector_type="json", config={"path": "centro_calendario.json"},
                     interval_s=10, jitter_pct=0.1, rate_limit_per_min=12))
        s.add(Source(code="csv-norte", name="Archivo CSV Norte", region="NORTE", category="inventario",
                     availability_type="inventario", connector_type="csv", config={"path": "norte_inventario.csv"},
                     interval_s=10, jitter_pct=0.1, rate_limit_per_min=12))
        s.add(Source(code="api-sur", name="API de prueba local Sur", region="SUR", category="reservacion",
                     availability_type="reservacion", connector_type="http",
                     config={"url": f"http://127.0.0.1:{port}/api-prueba/feeds/api-sur", "timeout_s": 3},
                     interval_s=10, jitter_pct=0.1, rate_limit_per_min=12))
    # Inventario propio de la API de prueba (feed distinto para no duplicar recursos de sim-sur)
    w = World(db)
    for k in range(3):
        w.create_item("api-sur", f"AS-{k}", f"Reserva API Ficticia · turno {k + 1}", "SUR", "mesa", capacity=4)

"""v1.4 (optimización medida): una consulta con cambios despierta el ciclo del planificador.

Antes, los cambios detectados esperaban al siguiente tick (`tick_segundos`, 0,5 s) para
asignarse: en la v1.3 eso era ~40 % del tiempo entre la detección y la asignación.

v2.2.2 (aislamiento, autorizado): la prueba mide SOLO el camino «la consulta trae cambios → el bucle
despierta → se procesan los eventos». Las tareas de fondo del planificador que no son objeto de la
prueba (seguimiento de expedientes, envío de alertas, mantenimiento de historial, autodiagnóstico,
oyentes de calendario, anuncio del proveedor y recuperación inicial) se sustituyen por funciones
vacías, como ya se hacía con `maintenance`. Motivo medido: en el 100 % de las rondas el seguimiento,
el envío y el historial reales corrían en hilos a la vez que la consulta falsa y competían con ella
por la CPU y el GIL (docs/INFORME_v2.2.2.md, sección 4 y anexo de la corrección).
El planificador real (bucle, espera, despertar, arrendamiento en la base) NO se sustituye.
El umbral de 0,4 s NO cambia. Se comprueba además que el planificador sigue LANZANDO esas tareas:
solo se vacía su contenido.
"""
import asyncio
import time

from monitoreo.engine import scheduler as sch
from monitoreo.engine.params import set_params
from monitoreo.engine.pipeline import PollOutcome


def _run(db, monkeypatch, events):
    with db.session() as s:
        set_params(s, {"tick_segundos": 2.0})
    calls, polled = [], []
    monkeypatch.setattr(sch, "due_sources", lambda db, now: [] if polled else [1])

    def fake_poll(*a, **kw):
        polled.append(time.monotonic())
        time.sleep(0.05)
        return PollOutcome("OK", events=events)
    monkeypatch.setattr(sch, "poll_source", fake_poll)
    monkeypatch.setattr(sch, "process_pending_events", lambda *a, **kw: calls.append(time.monotonic()))
    monkeypatch.setattr(sch, "maintenance", lambda *a, **kw: None)
    fondo = _aislar_tareas_de_fondo(monkeypatch)

    async def scenario():
        s1 = sch.Scheduler(db, holder="uno", lease_ttl_s=10)
        s1.start()
        await asyncio.sleep(1.0)
        await s1.stop()
        return s1.wakeups
    wakeups = asyncio.run(scenario())
    # el planificador siguió lanzando sus tareas de fondo (solo se vació su contenido)
    assert {"recuperacion", "envio", "captura", "historial"} <= set(fondo), fondo
    return wakeups, calls, polled


def _aislar_tareas_de_fondo(monkeypatch) -> list[str]:
    """Sustituye por funciones vacías las tareas de fondo que no son objeto de la prueba y devuelve la
    lista (compartida) de las que el planificador lanzó."""
    lanzadas: list[str] = []

    def vacia_sync(nombre):
        def f(*a, **kw):
            lanzadas.append(nombre)
        return f

    def vacia_async(nombre):
        async def f(*a, **kw):
            lanzadas.append(nombre)
        return f
    monkeypatch.setattr(sch, "run_recovery", vacia_sync("recuperacion"))
    monkeypatch.setattr(sch.Scheduler, "_dispatch", vacia_async("envio"))
    monkeypatch.setattr(sch.Scheduler, "_captura", vacia_async("captura"))
    monkeypatch.setattr(sch.Scheduler, "_history", vacia_async("historial"))
    monkeypatch.setattr(sch.Scheduler, "_selfcheck", vacia_async("autodiagnostico"))
    monkeypatch.setattr(sch.Scheduler, "_revisar_eventos", vacia_sync("oyentes_calendario"))
    monkeypatch.setattr(sch.Scheduler, "_anunciar_proveedor", vacia_sync("proveedor"))
    return lanzadas


def test_changes_wake_the_cycle(db, monkeypatch):
    wakeups, calls, polled = _run(db, monkeypatch, events=3)
    assert wakeups >= 1
    after = [c for c in calls if c > polled[0] + 0.05]
    assert after and after[0] - polled[0] < 0.4        # sin esperar los 2 s del tick


def test_no_changes_no_extra_cycles(db, monkeypatch):
    wakeups, calls, polled = _run(db, monkeypatch, events=0)
    assert wakeups == 0 and len(calls) == 1            # el tick de 2 s se respeta

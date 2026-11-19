"""v2.2.2 · Corrección del Supervisor de observadores: un estado terminal (DETENIDO_POR_FALLOS o
DETENIDO) no se reactiva por accidente.

Defecto corregido: `Supervisor.ciclo` leía la configuración una vez al principio; al detener por fallos
un observador lo marcaba DETENIDO_POR_FALLOS, pero el segundo bucle usaba la lectura vieja (ACTIVO) y lo
volvía a arrancar en el mismo ciclo. Además, entre esa lectura y el arranque, otro proceso podía
detenerlo o marcarlo como fallido.

Estas pruebas NO usan Node ni navegador: usan observadores falsos o un proceso falso, así que corren en
Linux y Windows sin dependencias. La prueba con navegador real sigue en test_observador_persistente.py.

Comportamientos válidos que se comprueban que SIGUEN igual:
- la política de reinicios del observador (max_reinicios en ventana_reinicios_s) y su mensaje;
- un observador sano no se relanza en cada ciclo;
- `desactivar` deja DETENIDO; `configurar` es lo único que reactiva una configuración detenida;
- tras un apagado ordenado, lo que estaba ACTIVO se restaura al volver a arrancar;
- un solo proceso ejecuta cada observador (arrendamiento).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from monitoreo.db import Database
from monitoreo.engine import lease as lease_mod
from monitoreo.models import ObservadorConfig
from monitoreo.planificador import observador as ob

CLAVE = "NOR-1|alta"


def _ahora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Registro:
    """Fábrica de observadores falsos que cuenta cuántos se crean (cada uno sería un navegador)."""

    def __init__(self):
        self.creados: list[_Falso] = []
        self._lock = threading.Lock()

    def __call__(self, c):
        o = _Falso(c)
        with self._lock:
            self.creados.append(o)
        return o

    def de(self, clave):
        return [o for o in self.creados if o.clave == clave]


class _Falso:
    def __init__(self, c):
        self.clave = c.clave
        self.fallo, self.stats = None, {"reinicios_navegador": 0}
        self.iniciado = self.detenido = False

    def iniciar(self):
        self.iniciado = True

    def detener(self, *a, **k):
        self.detenido = True

    def estado(self):
        return {"nombre": self.clave, "fallo": self.fallo}


def _cfg(db, clave=CLAVE):
    with db.session() as s:
        c = s.get(ObservadorConfig, clave)
        return None if c is None else ob.vista_config(c)


def _configurar(db, sucursal="NOR-1", tramite="alta"):
    return ob.configurar(db, sucursal, tramite, None, "dom", 250, "admin", _ahora())


def _esperar(cond, t=10.0, paso=0.02):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        v = cond()
        if v:
            return v
        time.sleep(paso)
    return cond()


# --------------------------------------------------------------------------- regresión directa

def test_supervisor_no_relanza_en_el_mismo_ciclo_un_observador_detenido_por_fallos(db):
    """La reproducción determinista del defecto (antes era xfail estricto en test_estabilizacion_v222)."""
    reg = _Registro()
    sup = ob.Supervisor(db, fabrica=reg, holder="estabilizacion")
    _configurar(db)
    assert sup.ciclo(_ahora())["arrancados"] == [CLAVE]
    reg.creados[0].fallo = "el navegador se relanzó 3 veces en 10 min"
    reg.creados[0].stats["reinicios_navegador"] = 3
    r = sup.ciclo(_ahora())
    assert r["fallidos"] == [CLAVE] and r["arrancados"] == [], r
    assert len(reg.creados) == 1 and not sup.activos and reg.creados[0].detenido
    c = _cfg(db)
    assert c["estado"] == ob.DETENIDO_POR_FALLOS and not c["activo"]


# --------------------------------------------------------------------------- fallos consecutivos y persistencia

def test_fallos_consecutivos_no_reactivan_y_el_estado_persiste(db):
    reg = _Registro()
    sup = ob.Supervisor(db, fabrica=reg, holder="p1")
    _configurar(db)
    sup.ciclo(_ahora())
    o = reg.creados[0]
    # ciclos sanos: el observador vivo NO se relanza en cada ciclo (comportamiento válido que se conserva)
    for _ in range(3):
        assert sup.ciclo(_ahora())["arrancados"] == []
    assert len(reg.creados) == 1
    o.fallo, o.stats["reinicios_navegador"] = "el navegador se relanzó 9 veces en 10 min; se detiene", 9
    resultados = [sup.ciclo(_ahora()) for _ in range(5)]
    assert [r["fallidos"] for r in resultados] == [[CLAVE], [], [], [], []]   # se informa una sola vez
    assert all(r["arrancados"] == [] for r in resultados)
    assert len(reg.creados) == 1
    c = _cfg(db)
    assert c["estado"] == ob.DETENIDO_POR_FALLOS and c["activo"] is False
    assert c["motivo"].startswith("el navegador se relanzó 9 veces") and c["reinicios"] == 9
    # el arrendamiento se soltó: ningún proceso lo tiene
    assert sup.arriendos == {}


def test_un_fallo_no_arrastra_a_otros_observadores(db):
    reg = _Registro()
    sup = ob.Supervisor(db, fabrica=reg, holder="p1")
    _configurar(db, "NOR-1", "alta")
    _configurar(db, "SUR-2", "alta")
    assert sorted(sup.ciclo(_ahora())["arrancados"]) == ["NOR-1|alta", "SUR-2|alta"]
    reg.de("NOR-1|alta")[0].fallo = "límite"
    r = sup.ciclo(_ahora())
    assert r["fallidos"] == ["NOR-1|alta"] and r["arrancados"] == []
    assert list(sup.activos) == ["SUR-2|alta"] and len(reg.de("SUR-2|alta")) == 1
    assert _cfg(db, "SUR-2|alta")["estado"] == ob.ACTIVO


def test_fallo_al_iniciar_sin_node_queda_detenido_y_no_se_reintenta(db):
    """Política existente: si iniciar() falla (sin Node/Playwright) queda DETENIDO_POR_FALLOS y no se
    insiste en bucle. Se comprueba que la corrección no la cambió."""
    intentos = []

    class SinNode(_Falso):
        def iniciar(self):
            intentos.append(1)
            raise RuntimeError("Node.js no está disponible: el observador necesita Node + Playwright")

    sup = ob.Supervisor(db, fabrica=SinNode, holder="p1")
    _configurar(db)
    r = sup.ciclo(_ahora())
    assert r["fallidos"] == [CLAVE] and r["arrancados"] == []
    for _ in range(3):
        assert sup.ciclo(_ahora())["arrancados"] == []
    assert len(intentos) == 1 and not sup.activos and sup.arriendos == {}
    c = _cfg(db)
    assert c["estado"] == ob.DETENIDO_POR_FALLOS and "Node.js" in c["motivo"]


# --------------------------------------------------------------------------- límite de reintentos (política real)

class _ProcesoQueCae:
    """Sustituye a Node: termina en seguida con código 1, como un navegador que se cae al arrancar."""
    lanzados = 0

    def __init__(self, *a, **k):
        type(self).lanzados += 1
        self.returncode, self.stdout, self.stderr = 1, iter(()), iter(())
        self.stdin = None

    def wait(self, *a):
        return 1

    def poll(self):
        return 1

    def kill(self):
        pass


def test_limite_de_reintentos_real_detiene_y_el_supervisor_no_lo_relanza(db, monkeypatch):
    """Usa el bucle real de `Observador` (espera creciente 1, 2 s… y límite max_reinicios) con un proceso
    falso que cae siempre. Con max_reinicios=2 se lanzan exactamente 3 procesos y se detiene con su motivo;
    después el Supervisor lo marca DETENIDO_POR_FALLOS y NO lanza un cuarto."""
    monkeypatch.setattr(ob.shutil, "which", lambda n: "node")
    monkeypatch.setattr(ob.subprocess, "Popen", _ProcesoQueCae)
    _ProcesoQueCae.lanzados = 0
    fabrica = lambda c: ob.Observador(db, c.sucursal, c.tramite, c.mes, c.estrategia,  # noqa: E731
                                      c.intervalo_visual_ms, atender=False, max_reinicios=2)
    sup = ob.Supervisor(db, fabrica=fabrica, holder="p1")
    _configurar(db)
    assert sup.ciclo(_ahora())["arrancados"] == [CLAVE]
    o = sup.activos[CLAVE]
    assert _esperar(lambda: o.fallo, 20), o.estado()
    assert "se relanzó 3 veces" in o.fallo                 # mensaje y límite de siempre
    assert _ProcesoQueCae.lanzados == 3 and o.stats["arranques"] == 3
    r = sup.ciclo(_ahora())
    assert r["fallidos"] == [CLAVE] and r["arrancados"] == [], r
    for _ in range(3):
        assert sup.ciclo(_ahora())["arrancados"] == []
    time.sleep(1.5)                                          # el bucle del observador ya terminó: nada relanza
    assert _ProcesoQueCae.lanzados == 3
    c = _cfg(db)
    assert c["estado"] == ob.DETENIDO_POR_FALLOS and c["reinicios"] == 3 and "relanzó" in c["motivo"]


# --------------------------------------------------------------------------- reinicio del Supervisor y del programa

def test_reinicio_del_supervisor_no_reactiva_lo_detenido_por_fallos(db):
    reg = _Registro()
    a = ob.Supervisor(db, fabrica=reg, holder="p1")
    _configurar(db)
    a.ciclo(_ahora())
    reg.creados[0].fallo = "límite"
    a.ciclo(_ahora())
    a.stop()
    b = ob.Supervisor(db, fabrica=reg, holder="p1")     # mismo proceso, supervisor nuevo
    for _ in range(3):
        assert b.ciclo(_ahora())["arrancados"] == []
    assert len(reg.creados) == 1 and _cfg(db)["estado"] == ob.DETENIDO_POR_FALLOS


def test_reinicio_ordenado_sigue_restaurando_lo_activo(db):
    """Comportamiento válido que se conserva: lo ACTIVO se restaura tras un apagado ordenado."""
    reg = _Registro()
    a = ob.Supervisor(db, fabrica=reg, holder="p1")
    _configurar(db)
    a.ciclo(_ahora())
    a.stop()                                    # suelta el arrendamiento
    b = ob.Supervisor(db, fabrica=reg, holder="p2")
    assert b.ciclo(_ahora())["arrancados"] == [CLAVE]
    assert len(reg.creados) == 2 and _cfg(db)["titular"] == "p2"


def test_recuperacion_tras_reiniciar_el_programa(db):
    """Simula que el programa termina y vuelve a arrancar: conexión nueva a la misma base, supervisor
    nuevo. Lo detenido por fallos sigue detenido (con motivo y reinicios) hasta que alguien lo vuelve a
    configurar; lo ACTIVO se restaura."""
    reg = _Registro()
    a = ob.Supervisor(db, fabrica=reg, holder="antes")
    _configurar(db, "NOR-1", "alta")
    _configurar(db, "SUR-2", "alta")
    a.ciclo(_ahora())
    o = reg.de("NOR-1|alta")[0]
    o.fallo, o.stats["reinicios_navegador"] = "el navegador se relanzó 9 veces en 10 min", 9
    a.ciclo(_ahora())
    a.stop()
    db.engine.dispose()

    db2 = Database(db.url)                      # "programa" nuevo sobre la misma base
    try:
        reg2 = _Registro()
        b = ob.Supervisor(db2, fabrica=reg2, holder="despues")
        r = b.ciclo(_ahora())
        assert r["arrancados"] == ["SUR-2|alta"], r
        assert reg2.de("NOR-1|alta") == []
        c = _cfg(db2, "NOR-1|alta")
        assert c["estado"] == ob.DETENIDO_POR_FALLOS and not c["activo"] and c["reinicios"] == 9
        assert c["motivo"].startswith("el navegador se relanzó 9 veces")
        # la única vía para reactivarlo es una decisión explícita (configurar), que reinicia contadores
        v = ob.configurar(db2, "NOR-1", "alta", None, "dom", 250, "admin", _ahora())
        assert v["ya_existia"] is False and v["estado"] == ob.ACTIVO and v["reinicios"] == 0 and v["motivo"] == ""
        assert b.ciclo(_ahora())["arrancados"] == ["NOR-1|alta"]
        b.stop()
    finally:
        db2.engine.dispose()


def test_desactivar_sigue_dejando_detenido_y_no_se_relanza(db):
    reg = _Registro()
    sup = ob.Supervisor(db, fabrica=reg, holder="p1")
    _configurar(db)
    sup.ciclo(_ahora())
    assert ob.desactivar(db, "admin", _ahora()) == 1
    assert sup.ciclo(_ahora())["detenidos"] == [CLAVE]
    assert sup.ciclo(_ahora())["arrancados"] == [] and len(reg.creados) == 1
    assert _cfg(db)["estado"] == ob.DETENIDO


# --------------------------------------------------------------------------- concurrencia

def test_estado_terminal_escrito_por_otro_proceso_entre_lectura_y_arranque(db, monkeypatch):
    """Carrera entre procesos, de forma determinista: el proceso B lee la configuración (ACTIVO) y, antes
    de arrancar, el proceso A la marca DETENIDO_POR_FALLOS. B debe releer y NO arrancar."""
    original = lease_mod.Lease.acquire_or_renew

    def adquirir_y_otro_proceso_falla(self, now):
        ok = original(self, now)
        if self.holder == "B":
            with db.session() as s:
                c = s.get(ObservadorConfig, CLAVE)
                c.activo, c.estado, c.motivo = False, ob.DETENIDO_POR_FALLOS, "fallo en el proceso A"
        return ok

    _configurar(db)
    monkeypatch.setattr(lease_mod.Lease, "acquire_or_renew", adquirir_y_otro_proceso_falla)
    reg = _Registro()
    b = ob.Supervisor(db, fabrica=reg, holder="B")
    r = b.ciclo(_ahora())
    assert r["arrancados"] == [] and reg.creados == [] and not b.activos, r
    assert b.arriendos == {}                                  # no se queda con el arrendamiento
    c = _cfg(db)
    assert c["estado"] == ob.DETENIDO_POR_FALLOS and c["motivo"] == "fallo en el proceso A"


def test_dos_procesos_concurrentes_un_fallo_no_se_relanza_en_ninguno(db):
    """Dos supervisores (dos procesos del motor) con sus hilos reales girando rápido sobre la misma base.
    Al fallar el observador, A suelta el arrendamiento; B podría tomarlo al instante. En ningún momento
    debe abrirse un segundo observador."""
    reg = _Registro()
    _configurar(db)
    a = ob.Supervisor(db, periodo_s=0.02, fabrica=reg, holder="A")
    assert a.ciclo(_ahora())["arrancados"] == [CLAVE]
    b = ob.Supervisor(db, periodo_s=0.02, fabrica=reg, holder="B")
    a.start()
    b.start()
    try:
        time.sleep(0.3)
        assert len(reg.creados) == 1
        reg.creados[0].fallo = "límite"
        assert _esperar(lambda: _cfg(db)["estado"] == ob.DETENIDO_POR_FALLOS, 10)
        time.sleep(1.0)                                     # decenas de ciclos de los dos procesos
        assert len(reg.creados) == 1, [o.clave for o in reg.creados]
        assert not a.activos and not b.activos
        assert a.ultimo_error is None and b.ultimo_error is None
    finally:
        a.stop()
        b.stop()
    assert _cfg(db)["estado"] == ob.DETENIDO_POR_FALLOS


def test_ciclos_concurrentes_en_el_mismo_supervisor(db):
    """El hilo del supervisor y otra llamada (p. ej. una prueba o la API) ejecutan `ciclo` a la vez: el
    fallo se registra una sola vez entre todos y no se relanza."""
    reg = _Registro()
    sup = ob.Supervisor(db, periodo_s=0.01, fabrica=reg, holder="p1")
    resultados, lock = [], threading.Lock()
    original = sup.ciclo

    def ciclo_registrado(now):
        r = original(now)
        with lock:
            resultados.append(r)
        return r
    sup.ciclo = ciclo_registrado                          # también lo usa el hilo (_run llama a self.ciclo)
    _configurar(db)
    sup.start()
    try:
        assert _esperar(lambda: sup.activos.get(CLAVE), 5)
        reg.creados[0].fallo = "límite"
        hilos = [threading.Thread(target=lambda: [sup.ciclo(_ahora()) for _ in range(10)]) for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(30)
        time.sleep(0.2)
    finally:
        sup.stop()
    with lock:
        fallidos = [c for r in resultados for c in r["fallidos"]]
        arrancados = [c for r in resultados for c in r["arrancados"]]
    assert fallidos == [CLAVE] and arrancados == [CLAVE] and len(reg.creados) == 1, (fallidos, arrancados)
    assert _cfg(db)["estado"] == ob.DETENIDO_POR_FALLOS


@pytest.mark.parametrize("vuelta", range(20))
def test_repeticion_del_escenario_intermitente(db, vuelta):
    """El escenario que fallaba de forma intermitente en Windows (el hilo del supervisor hace el ciclo
    antes que la prueba), repetido 20 veces por motor de base de datos con el hilo girando rápido."""
    reg = _Registro()
    sup = ob.Supervisor(db, periodo_s=0.005, fabrica=reg, holder=f"r{vuelta}")
    _configurar(db)
    sup.start()
    try:
        assert _esperar(lambda: sup.activos.get(CLAVE), 5)
        reg.creados[0].fallo = "límite"
        assert _esperar(lambda: _cfg(db)["estado"] == ob.DETENIDO_POR_FALLOS, 5)
        time.sleep(0.1)
        assert len(reg.creados) == 1 and not sup.activos
    finally:
        sup.stop()

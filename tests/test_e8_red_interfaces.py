"""E8: hallazgos M1 (filas ocultas y total falso) y M2 (nombres que se corren) de la auditoría E7."""
import json

from monitoreo.red_lectura import servicio as SV

from .test_red_servicio import Falso, eventos, servicio, vista

KINDS = ["ethernet", "wifi", "mobile", "tether"]


def conns(n, caidas=(), ids=None):
    out = []
    for i in range(n):
        cid = ids[i] if ids else f"sim-{i:02d}"
        abajo = i in caidas
        out.append({"id": cid, "kind": KINDS[i % 4], "link": "down" if abajo else "up", "available": not abajo,
                    "measured": True, "health": "down" if abajo else "healthy", "status": "down" if abajo else "ok",
                    "availability_pct": 0.0 if abajo else 100.0, "avg_latency_ms": None if abajo else 20.0 + i})
    return out


def leer(f, cs, svc=None, reloj=None):
    f.conn = [vista(conns=cs)]
    if svc is None:
        svc, reloj = servicio(f)
    else:
        reloj.t += 11
    svc.paso()
    return svc, reloj, svc.estado()


# ---------------------------------------------------------------- M1
def test_veinte_interfaces_con_dos_caidas_al_final_se_ven_todas():
    """Reproducción de E7: 20 interfaces, las dos últimas caídas. RC2 mostraba 16 y ninguna caída."""
    _, _, e = leer(Falso(), conns(20, caidas=(18, 19)))
    assert len(e["interfaces"]) == 20
    assert sum(i["veredicto"] == "CAÍDA" for i in e["interfaces"]) == 2
    r = e["resumen_interfaces"]
    assert (r["total"], r["caidas"], r["mostradas"], r["ocultas"]) == (20, 2, 20, 0)


def test_mas_interfaces_que_filas_el_total_es_real_y_las_alertas_van_primero():
    n = SV.MAX_FILAS + 10
    caidas = tuple(range(n - 5, n))                         # las 5 últimas en el orden: las que antes se perdían
    _, _, e = leer(Falso(), conns(n, caidas=caidas))
    r = e["resumen_interfaces"]
    assert (r["total"], r["caidas"], r["mostradas"], r["ocultas"]) == (n, 5, SV.MAX_FILAS, 10)
    assert [i["veredicto"] for i in e["interfaces"][:5]] == ["CAÍDA"] * 5
    assert sum(i["veredicto"] == "CAÍDA" for i in e["interfaces"]) == 5, "ninguna caída queda oculta"


def test_desactualizado_sigue_contando_todas():
    f = Falso()
    svc, reloj, _ = leer(f, conns(20, caidas=(19,)))
    reloj.t += svc.umbral_desactualizado_s() + 1             # sin lectura nueva: la copia envejece
    e = svc.estado()
    assert e["network_engine"] == "desactualizado"
    r = e["resumen_interfaces"]
    assert (r["total"], r["mostradas"], r["ocultas"]) == (20, 20, 0)
    assert all(i["salud"] == "SIN VERIFICAR" for i in e["interfaces"])


def test_api_red_expone_el_resumen_y_ningun_id():
    _, _, e = leer(Falso(), conns(20, caidas=(18, 19), ids=[f"Ethernet-SECRETO-{i}" for i in range(20)]))
    texto = json.dumps(e, ensure_ascii=False)
    assert "SECRETO" not in texto and "sim-" not in texto


# ---------------------------------------------------------------- M2
def test_nombre_estable_si_aparece_otra_interfaz_antes_en_el_orden():
    """RC2 numeraba por posición: una interfaz nueva con id menor «corría» el nombre de las demás."""
    f = Falso()
    viejas = conns(2, ids=["b-eth", "c-eth"])
    for c in viejas:
        c["kind"] = "ethernet"
    svc, reloj, e1 = leer(f, viejas)
    assert [i["nombre"] for i in e1["interfaces"]] == ["Cable (Ethernet) 1", "Cable (Ethernet) 2"]
    nueva = dict(viejas[0], id="a-eth", avg_latency_ms=99.0)
    _, _, e2 = leer(f, [nueva] + viejas, svc, reloj)
    assert [(i["nombre"], i["latencia_ms"]) for i in e2["interfaces"]] == [
        ("Cable (Ethernet) 3", 99.0), ("Cable (Ethernet) 1", 20.0), ("Cable (Ethernet) 2", 21.0)]


def test_nombre_estable_tras_desaparecer_y_volver():
    f = Falso()

    def wifi():
        cs = conns(3, ids=["x1", "x2", "x3"])
        for c in cs:
            c["kind"] = "wifi"
        return cs
    svc, reloj, e1 = leer(f, wifi())
    nombres = [i["nombre"] for i in e1["interfaces"]]
    _, _, e2 = leer(f, [c for c in wifi() if c["id"] != "x1"], svc, reloj)     # se desconecta la primera
    assert [i["nombre"] for i in e2["interfaces"]] == nombres[1:]
    _, _, e3 = leer(f, wifi(), svc, reloj)
    assert [i["nombre"] for i in e3["interfaces"]] == nombres


def test_reidentificada_conserva_el_nombre_y_el_evento_lo_usa():
    f = Falso()
    f.evs = [eventos([], nxt=5),
             eventos([{"seq": 6, "ts": "2026-11-27T01:00:10Z", "kind": "inventory.reidentified",
                       "connection_id": "usb-9", "previous_id": "usb-3"}], nxt=6)]
    c1 = conns(2, ids=["eth-1", "usb-3"])
    c1[1]["kind"] = "tether"
    svc, reloj, e1 = leer(f, c1)
    tel = [i["nombre"] for i in e1["interfaces"] if i["tipo"] == "Teléfono (anclaje)"][0]
    c2 = conns(2, ids=["eth-1", "usb-9"])
    c2[1]["kind"] = "tether"
    _, _, e2 = leer(f, c2, svc, reloj)
    assert [i["nombre"] for i in e2["interfaces"] if i["tipo"] == "Teléfono (anclaje)"] == [tel]
    assert e2["eventos"][0]["interfaz"] == tel
    assert "usb-" not in json.dumps(e2)

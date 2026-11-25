"""Demostración integral de Cauce Monitor en este equipo (LABORATORIO, datos FICTICIOS, solo 127.0.0.1).

Reúne lo que ya existe, sin funciones nuevas:
  - el laboratorio de Ventanilla (portal propio + motor) con «Autorrellenar» de Forms activo y su ventana VISIBLE;
  - expedientes ficticios con consentimiento, sucursales, trámite y fechas aceptables, ya en el plan del día;
  - una página de laboratorio lista para observar: queda en «Páginas observadas» como plantilla DETENIDA
    («Usar como base» -> «Probar» -> «Guardar y observar»);
  - el «Calendario lab» para provocar la disponibilidad («Abrir» un día).

La carpeta (por omisión ..\\demo-integral, junto a esta instalación) conserva todo entre arranques.
  python scripts/demo_integral.py --abrir            arranca (y la primera vez siembra los datos ficticios)
  python scripts/demo_integral.py --nuevo --abrir    borra la carpeta de la demostración y empieza de cero
No toca data\\ de esta instalación, ni C:\\cauce-msi, ni D:\\cauce-forms-interfaz, ni respaldos.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import sys
import time
import webbrowser
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from laboratorio_web.servidor import PERFIL_CALENDARIO_PORTAL  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402

PUERTO_MONITOR = 8765          # fijo si está libre: así el acceso directo y el navegador siempre van al mismo sitio
ESPERA_FORMS_S = 300           # la ventana de Forms queda abierta hasta 5 min para revisar los 8 campos
FUENTE = "calendario-lab"      # plantilla de la página de laboratorio (queda DETENIDA; se usa «Usar como base»)
CLAVE_IDENTIDAD = "QXZ482915"  # campo manual del portal de laboratorio (ficticio)


def libre(p: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            return False
    return True


def cliente(base: str, usuario: str, clave: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=60, trust_env=False)
    r = c.post("/api/auth/login", json={"username": usuario, "password": clave})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


def sembrar(lab: Laboratorio, d: dict) -> dict:
    """Primera vez: plan del día, 2 expedientes ficticios (uno coincidirá, otro no) y la plantilla de la página."""
    adm = cliente(d["motor"], "admin", ADMIN_PWD)
    op = cliente(d["motor"], *OPERADOR)
    ctl = httpx.Client(base_url=d["portal"], timeout=15, trust_env=False,
                       headers={"X-Control-Token": lab.cfg["token_control"]})
    dias = ctl.get("/control/dias").json()
    hoy = date.fromisoformat(dias["hoy"])
    regs = [ctl.get("/control/registro-ejemplo", params={"n": n}).json() for n in range(0, 40)]
    alta = [r for r in regs if r.get("tramite") == "alta"] or regs
    tramite = alta[0]["tramite"]
    usadas = {x["fecha"] for x in dias["dias"].values() if x["tramite"] == tramite}
    # mes siguiente (el que abre por omisión «Calendario lab»); un día sin publicar con sus vecinos también libres
    s = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1)
    candidatos = [s + timedelta(days=i) for i in range(4, 24)]
    ok = [f for f in candidatos if f > hoy + timedelta(days=2)
          and all((f + timedelta(days=k)).isoformat() not in usadas for k in (-1, 0, 1))]
    fecha = ok[0]
    fecha_b = next(f for f in ok if abs((f - fecha).days) >= 6)
    adm.post("/api/planes", json={"fecha_jornada": hoy.isoformat(), "capacidad": 6})

    personas = [
        {"nombre": "Lucía Demo Coincidencia", "telefono": "5550001111", "correo": "lucia.demo@ejemplo.test",
         "reg": alta[0], "sucursal": "NOR-1", "alternativas": ["NOR-2"], "fecha": fecha},
        {"nombre": "Mario Demo Espera", "telefono": "5550002222", "correo": "mario.demo@ejemplo.test",
         "reg": alta[1], "sucursal": "NOR-2", "alternativas": [], "fecha": fecha_b},
    ]
    expedientes = []
    for i, p in enumerate(personas):
        at = op.post("/api/ventanilla/atenciones", json={"clave": secrets.token_hex(16)}).json()
        datos = {"nombre": p["nombre"], "fecha_nacimiento": p["reg"]["fecha_nacimiento"], "telefono": p["telefono"],
                 "telefono_confirmacion": p["telefono"], "correo": p["correo"], "correo_confirmacion": p["correo"],
                 "region": "NORTE", "sucursal": p["sucursal"], "sucursales_alternativas": p["alternativas"],
                 "tipo_tramite": p["reg"]["tramite"], "id_registro_previo": p["reg"]["identificador"],
                 "consentimiento": {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01",
                                    "evidencia_ref": f"DEMO-{i + 1:02d}", "evidencia_sha256": secrets.token_hex(32)}}
        r = op.post(f"/api/ventanilla/atenciones/{at['id']}/expediente", json={"datos": datos})
        r.raise_for_status()
        exp_id = r.json()["expediente"]["id"]
        f = p["fecha"]
        op.post(f"/api/ventanilla/atenciones/{at['id']}/plan",
                json={"fechas_desde": (f - timedelta(days=1)).isoformat(),
                      "fechas_hasta": (f + timedelta(days=1)).isoformat()}).raise_for_status()
        expedientes.append({"id": exp_id, "nombre": p["nombre"], "sucursal": p["sucursal"],
                            "tramite": p["reg"]["tramite"], "registro_previo": p["reg"]["identificador"],
                            "fecha_nacimiento": p["reg"]["fecha_nacimiento"], "telefono": p["telefono"],
                            "correo": p["correo"],
                            "fechas": [(f - timedelta(days=1)).isoformat(), (f + timedelta(days=1)).isoformat()]})
    # plantilla de la página de laboratorio: se guarda y se detiene, para que Diego la configure desde la pantalla
    fuente = {"nombre": FUENTE, "tramite": tramite, "sucursal": "NOR-1", "estrategia": "dom",
              "url": f"{d['portal']}/observar/calendario?sucursal=NOR-1&tramite={tramite}&mes={fecha.isoformat()[:7]}",
              "perfil": PERFIL_CALENDARIO_PORTAL}
    r = adm.post("/api/fuentes-web", json=fuente)
    r.raise_for_status()
    adm.delete(f"/api/fuentes-web/{FUENTE}").raise_for_status()
    for c in (adm, op, ctl):
        c.close()
    return {"fecha_coincidencia": fecha.isoformat(), "sucursal": "NOR-1", "tramite": tramite,
            "pagina": fuente["url"], "expedientes": expedientes, "sembrado": time.strftime("%Y-%m-%d %H:%M")}


def guia(d: dict, demo: dict) -> str:
    f = demo["fecha_coincidencia"]
    e1, e2 = demo["expedientes"][0], demo["expedientes"][1]
    return f"""CAUCE MONITOR · DEMOSTRACIÓN (laboratorio, datos ficticios, solo este equipo)

Monitor: {d['motor']}
Entrar con:  admin / {ADMIN_PWD}   (administrador: Páginas observadas y Calendario lab)
             {OPERADOR[0]} / {OPERADOR[1]}   (operador: Ventanilla)

Expedientes ficticios ya creados y en el plan del día:
  #{e1['id']} {e1['nombre']} · {e1['sucursal']} · acepta del {e1['fechas'][0]} al {e1['fechas'][1]}  <- coincidirá
  #{e2['id']} {e2['nombre']} · {e2['sucursal']} · acepta del {e2['fechas'][0]} al {e2['fechas'][1]}  <- queda en espera

1. (admin) Menú «Operación» -> «Páginas observadas» -> en «{FUENTE}» pulsa «Usar como base»
   -> «Probar (vista previa)» -> «Guardar y observar». La página queda «Mirando».
   Todavía no hay ningún día en las fechas de los expedientes: nada coincide (ambos «en espera»).
2. (admin) Menú «Laboratorio» -> «Calendario lab» (mes {f[:7]}) -> pulsa «Abrir» en el día {int(f[8:])}.
   En unos segundos Monitor lo ve y lo asigna a {e1['nombre']} (fecha {f}, sucursal {demo['sucursal']}).
3. «Salir» y entra como {OPERADOR[0]}. «Ventanilla» -> «Plan del día»: la coincidencia EXP-{e1['id']:06d}
   con fecha y sucursal -> «Preparar operación».
4. En la operación pulsa «Autorrellenar formulario». Se abre una ventana de Chromium (Forms) con los 8 campos
   rellenos; en Ventanilla aparece la verificación campo por campo. Revísala y CIERRA esa ventana
   (Forms no envía nada; si pulsas «Continuar» dentro de ella, Forms lo registra y la cierra).
5. Haz de la persona en otra pestaña: {d['portal']}/cita
   Nueva cita · Alta · {e1['registro_previo']} · nacimiento {e1.get('fecha_nacimiento', '')} · tel {e1.get('telefono', '')} (dos veces)
   · correo {e1.get('correo', '')} (dos veces) · clave de identidad {CLAVE_IDENTIDAD} (tecleada) · acepta el aviso
   -> «Continuar» -> sucursal «Plaza Aurora Norte» -> «Mes siguiente» -> día {f} -> «Continuar». Anota el folio.
6. En Ventanilla escribe el folio dos veces -> «Registrar folio». Monitor lo verifica con el portal:
   reservación CONFIRMADA, plan CONFIRMADO y expediente con reservación confirmada.
7. Historial: «Salir», entra como admin -> «Expedientes» -> fila EXP-{e1['id']:06d} -> «Historial»
   (estado, folio, atenciones, plan del día, operación con el portal y línea de tiempo completa).
   EXP-{e2['id']:06d} sigue «En el plan»: es el caso sin disponibilidad.

Para cerrar: cierra la ventana negra de «Cauce demo». Al volver a abrirla, todo sigue donde quedó.
Para empezar de cero: «Cauce demo - reiniciar».
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carpeta", default=str(ROOT.parent / "demo-integral"))
    ap.add_argument("--forms", default=str(ROOT.parent / "cauce-forms"))
    ap.add_argument("--nuevo", action="store_true", help="borrar la carpeta de la demostración y empezar de cero")
    ap.add_argument("--abrir", action="store_true", help="abrir el navegador en Monitor")
    ap.add_argument("--sin-ventana", action="store_true", help="Forms sin ventana (solo pruebas automáticas)")
    ap.add_argument("--espera-s", type=int, default=ESPERA_FORMS_S)
    a = ap.parse_args()
    carpeta = Path(a.carpeta)
    if not a.nuevo and not libre(PUERTO_MONITOR):
        # Ya hay una demostración abierta (doble clic repetido): no se arranca otra sobre la misma carpeta.
        try:
            if httpx.get(f"http://127.0.0.1:{PUERTO_MONITOR}/api/health", timeout=3, trust_env=False).json().get("ok"):
                print("La demostración ya está abierta: abro el navegador.")
                if a.abrir:
                    webbrowser.open(f"http://127.0.0.1:{PUERTO_MONITOR}")
                time.sleep(3)
                return 0
        except Exception:  # noqa: BLE001 - el puerto lo usa otro programa: se sigue con otro puerto
            pass
    if not os.environ.get("MONITOREO_PUERTO_LAB") and libre(PUERTO_MONITOR):
        os.environ["MONITOREO_PUERTO_LAB"] = str(PUERTO_MONITOR)
    lab = Laboratorio(carpeta, a.nuevo, forms=Path(a.forms))
    lab.env_extra.update({"MONITOREO_FORMS_HEADLESS": "1" if a.sin_ventana else "0",
                          "MONITOREO_AUTORRELLENO_ESPERA_S": str(a.espera_s)})
    try:
        d = lab.iniciar()
        demo_json = carpeta / "demo.json"
        if demo_json.exists():
            demo = json.loads(demo_json.read_text("utf-8"))
        else:
            demo = sembrar(lab, d)
            demo_json.write_text(json.dumps(demo, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:  # noqa: BLE001
        lab.detener()
        print(f"No se pudo arrancar la demostración: {e}\nRevisa {carpeta}\\servidor-motor-lab.log y portal.log")
        return 1
    texto = guia(d, demo)
    (carpeta / "GUIA_DEMO.txt").write_text(texto, "utf-8")
    (carpeta / "estado.json").write_text(json.dumps({**d, "demo": demo}, ensure_ascii=False, indent=2), "utf-8")
    print(texto)
    print("Deja esta ventana abierta mientras usas la demostración.\n")
    if a.abrir:
        webbrowser.open(d["motor"])
    parar = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: parar.update(v=True))
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: parar.update(v=True))
    try:
        while not parar["v"]:
            if lab.motor.proc.poll() is not None:
                print("El motor terminó; revisa el registro en la carpeta.")
                break
            time.sleep(0.5)
    finally:
        lab.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())

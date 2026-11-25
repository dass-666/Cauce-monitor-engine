"""Laboratorio · recorrido de PRINCIPIO A FIN del objetivo original, con los componentes reales y datos FICTICIOS.

  1. Ventanilla: expediente ficticio con consentimiento y fechas aceptables -> queda en el plan del día.
  2. El motor lanza el OBSERVADOR real (Node + Chromium) sobre el calendario del portal de laboratorio.
  3. El laboratorio habilita un día dentro de esas fechas: el observador lo ve y el motor de coincidencias lo asigna.
     Comprobación: la coincidencia NO es una cita (no hay reservación ni folio).
  4. El operador prepara la operación y pulsa «Autorrellenar»: la coincidencia viaja con la solicitud a Forms REAL,
     que rellena y verifica el formulario /cita SIN enviar. La operación queda LISTA PARA CONFIRMACIÓN SUPERVISADA.
  5. La PERSONA confirma en el portal de laboratorio (aquí SIMULADA por este script: campos manuales, día de la
     coincidencia, «Continuar»). Es una reservación del portal de laboratorio, nunca de un portal real.
  6. El operador teclea el folio: uno inexistente y uno de otra persona se rechazan; el correcto se comprueba con la
     API del portal (titular, trámite, fecha, sucursal) -> CONFIRMADA, plan CONFIRMADO.
  7. Ningún archivo de texto ni registro contiene los datos personales ficticios.

Con --interfaz, los pasos 4 y 6 los hace el operador SOLO con la pantalla de Ventanilla (Chromium automatizado,
scripts/recorrido_coincidencia_ui.js): pulsa «Autorrellenar», ve la operación lista y teclea los folios.

Con --aislado CUENTA (solo Linux como root, equipo de pruebas): Forms corre como esa OTRA cuenta del sistema
(monitoreo/autorrelleno/aislamiento_lab.py). Monitor comprueba antes de lanzar que la cuenta no puede leer su base,
datos, respaldos ni diario; el recorrido comprueba además que el navegador de Forms es de esa cuenta y que no queda
ningún proceso suyo al terminar. En Windows el equivalente se prueba en el workflow «Aislamiento Forms Windows».
Con --aislado también: regla de red de la cuenta (iptables --uid-owner, se quita al terminar) y comprobación de que
Monitor llega al destino de prueba y la cuenta no; la API de Monitor rechaza a la cuenta sin sesión; un segundo clic
con la ventana abierta no lanza otro Forms. Con --corte (solo API, no --interfaz): a mitad del primer autorrellenado
se MATA Monitor (kill -9), se vuelve a arrancar y se comprueba la recuperación (ningún proceso de la cuenta, historial
INTERRUMPIDO, reabrir exige motivo y usa la misma reservación).

Uso: python scripts/recorrido_coincidencia_forms.py --forms <carpeta de Forms instalado> [--salida DIR] [--con-ventana]
     [--observador calendario|web] [--interfaz] [--aislado CUENTA [--red-destino HOST:PUERTO] [--corte]]
Código 0 si todo pasa, 1 si algo falla, 3 si falta Forms o Node.js/Playwright.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from autorrelleno_forms import FormsNoListo, buscar_datos  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, Laboratorio  # noqa: E402

from monitoreo import navegador  # noqa: E402

MANUALES = {"clave_identidad": "QXZ482915", "acepto_privacidad": "si"}     # ficticios: los teclea la «persona»


def esperar(f, t=60.0, paso=0.5):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        try:
            v = f()
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v
        time.sleep(paso)
    return None


def cliente(base, usuario, clave):
    c = httpx.Client(base_url=base, timeout=60, trust_env=False)
    r = c.post("/api/auth/login", json={"username": usuario, "password": clave})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


def persona_confirma(portal_base: str, p: dict, fecha: str, sucursal: str) -> str:
    """La persona (SIMULADA) termina la cita en el portal de LABORATORIO y lee el folio de la pantalla."""
    with httpx.Client(base_url=portal_base, follow_redirects=True, timeout=15, trust_env=False) as c:
        c.get("/cita")
        datos = {"operacion": "nueva", "tramite": p["tramite"], "identificador": p["identificador"],
                 "fecha_nacimiento": p["fecha_nacimiento"], "telefono": p["telefono"],
                 "telefono_confirmacion": p["telefono"], "correo": p["correo"], "correo_confirmacion": p["correo"],
                 **MANUALES}
        r = c.post("/cita/datos", data=datos)
        if 'data-pantalla="calendario"' not in r.text:
            raise RuntimeError(f"el portal no aceptó los datos (HTTP {r.status_code})")
        q = {"estado": "norte", "sucursal": sucursal, "mes": fecha[:7]}
        clave = re.search(r'name="clave" value="([0-9a-f]+)"', c.get("/cita/calendario", params=q).text).group(1)
        r = c.post("/cita/continuar", data={**q, "clave": clave, "dia": fecha})
        m = re.search(r'data-folio="([^"]+)"', r.text)
        if not m:
            raise RuntimeError(f"el portal no mostró folio (HTTP {r.status_code})")
        return m.group(1)


def preparar_aislado(cuenta: str) -> Path | None:
    """Carpeta del conector: perfil/ (la cuenta lo lee) y trabajo/ (solo la cuenta escribe). Linux como root."""
    import os
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        print("--aislado solo en Linux como root (equipo de pruebas). En Windows: workflow «Aislamiento Forms Windows».")
        return None
    import pwd
    try:
        pw = pwd.getpwnam(cuenta)
    except KeyError:
        print(f"La cuenta {cuenta} no existe (useradd -M -s /usr/sbin/nologin {cuenta}).")
        return None
    raiz = Path(tempfile.mkdtemp(prefix="cauce_conector_", dir="/tmp"))
    os.chmod(raiz, 0o755)
    (raiz / "perfil").mkdir()
    (raiz / "trabajo" / "tmp").mkdir(parents=True)
    for d in (raiz / "trabajo", raiz / "trabajo" / "tmp"):
        os.chown(d, pw.pw_uid, pw.pw_gid)
    os.chmod(raiz / "trabajo", 0o700)
    return raiz


def regla_red(cuenta: str, poner: bool) -> bool:
    """La cuenta del conector no sale a la red salvo 127.0.0.1 (lo que en Windows hace la regla del cortafuegos)."""
    regla = ["OUTPUT", "-m", "owner", "--uid-owner", cuenta, "!", "-o", "lo", "-j", "REJECT",
             "--reject-with", "icmp-admin-prohibited"]
    if not shutil.which("iptables"):
        return False
    while subprocess.run(["iptables", "-D", *regla], capture_output=True).returncode == 0:
        pass
    return not poner or subprocess.run(["iptables", "-A", *regla], capture_output=True).returncode == 0


def permisos_aislado(raiz: Path, cuenta: str, estado: Path) -> None:
    import os
    import pwd
    pw = pwd.getpwnam(cuenta)
    for d in (raiz / "perfil", *(raiz / "perfil").rglob("*")):
        os.chmod(d, 0o755 if d.is_dir() else 0o644)      # el perfil aprobado: la cuenta lo lee, no lo cambia
    for d in (estado, *estado.rglob("*")):
        os.chown(d, pw.pw_uid, pw.pw_gid)                 # el estado de Forms: lo escribe la cuenta


class VigiaCuenta:
    """Mira cada 0,2 s los procesos de la cuenta mientras Forms trabaja: ¿hubo un navegador suyo?"""

    def __init__(self, cuenta: str):
        import threading
        sys.path.insert(0, str(ROOT))
        from monitoreo.autorrelleno import aislamiento_lab as al
        self.al, self.cuenta, self.navegador, self.maximo = al, cuenta, False, 0
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._mirar, daemon=True)
        self._hilo.start()

    def _mirar(self):
        while not self._parar.wait(0.2):
            try:
                ps = self.al.procesos_de_la_cuenta(self.cuenta)
                self.maximo = max(self.maximo, len(ps))
                self.navegador = self.navegador or any("chrom" in (p.name() or "").lower() for p in ps)
            except Exception:  # noqa: BLE001
                pass

    def parar(self):
        self._parar.set()
        self._hilo.join(2)

    def restos(self) -> list[int]:
        import psutil
        fin = time.monotonic() + 10
        while time.monotonic() < fin:
            vivos = [p.pid for p in self.al.procesos_de_la_cuenta(self.cuenta) if p.status() != psutil.STATUS_ZOMBIE]
            if not vivos:
                return []
            time.sleep(0.2)
        return vivos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", required=True)
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_coincidencia_forms"))
    ap.add_argument("--espera-s", type=int, default=5, help="tiempo de revisión de la ventana de Forms")
    ap.add_argument("--con-ventana", action="store_true")
    ap.add_argument("--observador", choices=("calendario", "web"), default="calendario",
                    help="web: el OBSERVADOR UNIVERSAL (fuente configurada por URL + perfil, sin código del portal)")
    ap.add_argument("--interfaz", action="store_true",
                    help="pasos 4 y 6 con la pantalla de Ventanilla (navegador automatizado), no con la API")
    ap.add_argument("--aislado", metavar="CUENTA", default="",
                    help="Forms como OTRA cuenta del sistema (solo Linux como root, equipo de pruebas)")
    ap.add_argument("--red-destino", default="1.1.1.1:443",
                    help="con --aislado: host:puerto que Monitor alcanza y la cuenta no (solo se abre la conexión TCP)")
    ap.add_argument("--corte", action="store_true",
                    help="con --aislado: matar Monitor a mitad del primer autorrellenado y comprobar la recuperación")
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    if not shutil.which("node"):
        print("Falta Node.js para el observador del calendario.")
        return 3
    t0 = time.time() - 1
    tmp = Path(tempfile.mkdtemp(prefix="coincidencia-forms-"))
    raiz_conector = None
    if a.corte and (a.interfaz or not a.aislado):
        print("--corte va con --aislado y sin --interfaz.")
        return 3
    if a.aislado:
        raiz_conector = preparar_aislado(a.aislado)
        if raiz_conector is None:
            return 3
        if not regla_red(a.aislado, True):
            print("No se pudo poner la regla de red de la cuenta (iptables): la red no se puede demostrar.")
            return 3
    lab = Laboratorio(tmp / "lab", nuevo=True, forms=Path(a.forms),
                      carpeta_forms=(raiz_conector / "perfil") if raiz_conector else None)
    if raiz_conector:
        lab.env_extra.update({"MONITOREO_AUTORRELLENO_AISLAMIENTO": "cuenta_restringida",
                              "MONITOREO_FORMS_CUENTA": a.aislado,
                              "MONITOREO_FORMS_TRABAJO": str(raiz_conector / "trabajo"),
                              "MONITOREO_AISLAMIENTO_RED_DESTINO": a.red_destino,
                              "MONITOREO_AISLAMIENTO_API": "http://127.0.0.1:{puerto}/api/expedientes"})
    lab.env_extra.update({"MONITOREO_AUTORRELLENO_ESPERA_S": str(a.espera_s),
                          "MONITOREO_FORMS_HEADLESS": "0" if a.con_ventana else "1"})
    ck: list[dict] = []
    res: dict = {"recorrido": "coincidencia_forms_folio_laboratorio", "interfaz": None if not a.interfaz else {}, "plataforma": platform.platform(),
                 "python": platform.python_version(), "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "datos": "FICTICIOS; portal de laboratorio propio en 127.0.0.1; la persona del paso 5 es SIMULADA"}
    tiempos: dict = {}

    def check(nombre, ok, detalle=None):
        ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK   " if ok else "FALLA") + " " + nombre + ("" if ok else f"  {detalle}"))
        return ok

    def interfaz(fase, entrada):
        """Ejecuta una fase de recorrido_coincidencia_ui.js y registra sus pasos como comprobaciones."""
        (tmp / f"ui_{fase}.json").write_text(json.dumps({"operador": list(OPERADOR), **entrada}), "utf-8")
        p = subprocess.run([shutil.which("node"), str(ROOT / "scripts" / "recorrido_coincidencia_ui.js"), fase, d["motor"],
                            str(out / "capturas_interfaz"), str(tmp / f"ui_{fase}.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                           env=navegador.entorno(), cwd=str(ROOT / "scripts"))
        linea = next((x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")), None)
        if linea is None:
            check(f"interfaz · {fase}: el navegador automatizado se ejecutó", False, (p.stderr or p.stdout)[-400:])
            return None
        nav = json.loads(linea[len("RESULTADO "):])
        res.setdefault("interfaz", {})[fase] = nav
        for x in nav["pasos"]:
            check(f"interfaz · {fase} · {x['paso']}", x["ok"], x["detalle"])
        check(f"interfaz · {fase}: sin interrupciones", not nav.get("fallo"), nav.get("fallo"))
        return nav

    persona = {}
    try:
        try:
            d = lab.iniciar()
        except FormsNoListo as e:
            print(e)
            return 3
        if raiz_conector:
            estado = Path(json.loads(lab.perfil_forms.read_text(encoding="utf-8"))["state_dir"])
            permisos_aislado(raiz_conector, a.aislado, estado)
            res["aislado"] = {"cuenta": a.aislado, "monitor_datos": str(tmp / "lab")}
        pin = json.loads(lab.perfil_forms.read_text(encoding="utf-8"))
        res["forms"] = {"commit": pin.get("forms_commit"), "perfil_sha256": pin["perfil_sha256"], "url": pin["url"]}
        adm = cliente(d["motor"], "admin", ADMIN_PWD)
        op = cliente(d["motor"], *OPERADOR)
        ctl = httpx.Client(base_url=d["portal"], timeout=10, trust_env=False,
                           headers={"X-Control-Token": lab.cfg["token_control"]})
        reg = ctl.get("/control/registro-ejemplo", params={"n": 7}).json()
        persona = {"nombre": "Lucia Ficticia Coincidencia", "telefono": "5550007788",
                   "correo": "lucia.ficticia@ejemplo.test", "fecha_nacimiento": reg["fecha_nacimiento"],
                   "identificador": reg["identificador"], "tramite": reg["tramite"]}
        dias = ctl.get("/control/dias").json()
        hoy = date.fromisoformat(dias["hoy"])
        usadas = {x["fecha"] for x in dias["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == persona["tramite"]}
        # un día sin registro en el calendario, con sus dos vecinos también libres: el rango aceptable de la persona
        # (fecha-1 .. fecha+1) no contiene ningún día ya publicado, así que solo el día que se habilite puede coincidir
        def libre(i):
            return all((hoy + timedelta(days=i + k)).isoformat() not in usadas for k in (-1, 0, 1))
        fecha = (hoy + timedelta(days=next(i for i in range(20, 90) if libre(i)))).isoformat()
        res["coincidencia_esperada"] = {"fecha": fecha, "sucursal": "NOR-1", "tramite": persona["tramite"]}

        # 1. expediente con fechas aceptables -> plan del día
        r = adm.post("/api/planes", json={"fecha_jornada": hoy.isoformat(), "capacidad": 6})
        check("1 · plan del día creado", r.status_code in (201, 409), r.text[:200])
        at = op.post("/api/ventanilla/atenciones", json={"clave": "a" * 32}).json()
        datos = {"nombre": persona["nombre"], "fecha_nacimiento": persona["fecha_nacimiento"],
                 "telefono": persona["telefono"], "telefono_confirmacion": persona["telefono"],
                 "correo": persona["correo"], "correo_confirmacion": persona["correo"], "region": "NORTE",
                 "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"], "tipo_tramite": persona["tramite"],
                 "id_registro_previo": persona["identificador"],
                 "consentimiento": {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01", "evidencia_ref": "LAB-01",
                                    "evidencia_sha256": "c" * 64}}
        r = op.post(f"/api/ventanilla/atenciones/{at['id']}/expediente", json={"datos": datos})
        check("1 · expediente ficticio con consentimiento registrado en Ventanilla", r.status_code == 200, r.text[:200])
        exp_id = r.json()["expediente"]["id"]
        f0 = date.fromisoformat(fecha)
        r = op.post(f"/api/ventanilla/atenciones/{at['id']}/plan", json={"fechas_desde": (f0 - timedelta(days=1)).isoformat(),
                                                                         "fechas_hasta": (f0 + timedelta(days=1)).isoformat()})
        check("1 · la persona queda en el plan del día con sus fechas aceptables", r.status_code == 200, r.text[:200])

        # 2. observador real sobre el calendario del portal
        res["observador"] = a.observador
        if a.observador == "web":
            from laboratorio_web.servidor import PERFIL_CALENDARIO_PORTAL
            fuente = {"nombre": "calendario-lab", "tramite": persona["tramite"], "sucursal": "NOR-1",
                      "url": f"{d['portal']}/observar/calendario?sucursal=NOR-1&tramite={persona['tramite']}&mes={fecha[:7]}",
                      "perfil": PERFIL_CALENDARIO_PORTAL, "estrategia": "ambas"}
            pr = adm.post("/api/fuentes-web/probar", json=fuente)
            check("2 · vista previa del perfil: la página se lee sin código del portal",
                  pr.status_code == 200 and pr.json()["vista"] and pr.json()["vista"]["estado"] == "ACTIVA", pr.text[:300])
            r = adm.post("/api/fuentes-web", json=fuente)
            check("2 · el motor lanzó el OBSERVADOR UNIVERSAL (fuente por URL + perfil)", r.status_code == 201, r.text[:300])
        else:
            r = adm.post("/api/observador", json={"sucursal": "NOR-1", "tramite": persona["tramite"], "mes": fecha[:7],
                                                  "estrategia": "ambas"})
            check("2 · el motor lanzó el observador (Chromium) del calendario", r.status_code == 200, r.text[:300])
        activo = esperar(lambda: (adm.get("/api/observador").json()["observadores"][0].get("vista") or {}).get("dom") == "ACTIVA", 90)
        check("2 · calendario abierto y ACTIVO", bool(activo), adm.get("/api/observador").json())
        time.sleep(3)
        sin = [e for p in adm.get("/api/planes").json()["planes"] for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"]
               if e["expediente_id"] == exp_id]
        check("2 · sin día disponible en sus fechas: el expediente sigue EN ESPERA", sin and sin[0]["estado"] == "EN_ESPERA",
              [e["estado"] for e in sin])

        # 3. el laboratorio habilita el día -> detección -> coincidencia
        t = time.perf_counter()
        ctl.post("/control/dias/habilitar", json={"sucursal": "NOR-1", "tramite": persona["tramite"], "fecha": fecha,
                                                  "capacidad": 1}).raise_for_status()

        def elemento():
            for p in adm.get("/api/planes").json()["planes"]:
                pl = adm.get(f"/api/planes/{p['id']}").json()
                for e in pl["elementos"]:
                    if e["expediente_id"] == exp_id and e["estado"] == "ASIGNADO":
                        return e
        el = esperar(elemento, 60, 0.2)
        tiempos["habilitar_a_coincidencia_s"] = round(time.perf_counter() - t, 2)
        diag = None if el else {"observaciones": [{k: e.get(k) for k in ("tipo", "fecha", "estado", "sucursal", "tramite")}
                                                  for e in (adm.get("/api/observaciones").json() or {}).get("eventos", [])][-6:],
                                "plan": [{k: e.get(k) for k in ("expediente_id", "estado", "tramite", "sucursales")}
                                         | {"fechas": len(e.get("fechas_aceptables") or [])}
                                         for p in adm.get("/api/planes").json()["planes"]
                                         for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"]]}
        check("3 · el observador vio el día y el motor asignó la coincidencia al expediente",
              bool(el) and el.get("fecha_asignada") == fecha, el or diag)
        if not el:
            return 1
        exp = adm.get(f"/api/expedientes/{exp_id}").json()
        check("3 · una coincidencia detectada NO es una cita (sin folio, sin reservación confirmada)",
              exp["estado"] != "RESERVACION_CONFIRMADA" and not exp.get("folio"), exp.get("estado"))

        # 4. operación preparada -> Forms real
        r = op.post(f"/api/planificador/elementos/{el['id']}/preparar")
        check("4 · el operador preparó la operación", r.status_code == 200, r.text[:200])
        rid = r.json()["reservacion_id"]
        at2 = op.post("/api/ventanilla/atenciones", json={"clave": "b" * 32}).json()
        op.post(f"/api/ventanilla/atenciones/{at2['id']}/expediente", json={"expediente_id": exp_id}).raise_for_status()
        st = op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()
        check("4 · Ventanilla muestra la coincidencia y el botón disponible",
              st["disponible"] and (st["coincidencia"] or {}).get("fecha") == fecha, st)
        if a.corte:
            # 4 corte. Monitor muere de golpe con el formulario abierto -> arranque -> recuperación
            vg = VigiaCuenta(a.aislado)
            r = op.post(f"/api/autorrelleno/atenciones/{at2['id']}")
            check("4 corte · autorrellenar lanzado (Forms real, como la cuenta)", r.status_code == 202, r.text[:200])
            sid_cortada = r.json().get("solicitud_id") if r.status_code == 202 else None
            vio = esperar(lambda: vg.navegador, 90, 0.2)
            check("4 corte · el navegador de Forms está abierto como la cuenta", bool(vio), vg.maximo)
            antes = len(vg.al.procesos_de_la_cuenta(a.aislado))
            lab.motor.proc.kill()                                    # kill -9: sin apagado ordenado
            lab.motor.proc.wait(20)
            lab.motor.log.close()
            vivos_tras_matar = len([p for p in vg.al.procesos_de_la_cuenta(a.aislado)])
            lab.motor.start()
            lab.motor.wait_ready()
            vg.parar()
            restos = vg.restos()
            res["corte"] = {"procesos_antes": antes, "procesos_tras_matar_monitor": vivos_tras_matar,
                            "procesos_tras_arrancar": len(restos)}
            check("4 corte · tras arrancar Monitor no queda ningún proceso de la cuenta (Forms ni Chromium)",
                  not restos, res["corte"])
            op = cliente(d["motor"], *OPERADOR)
            adm = cliente(d["motor"], "admin", ADMIN_PWD)
            tl = adm.get(f"/api/expedientes/{exp_id}/trazabilidad").json().get("linea_de_tiempo", [])
            inter = [x for x in tl if x.get("tipo") == "AUTORRELLENO_TERMINADO"]
            check("4 corte · el historial cierra el autorrellenado cortado como INTERRUMPIDO",
                  bool(inter) and "INTERRUMPIDA" in json.dumps(inter[-1], ensure_ascii=False), inter[-1:] or tl[-3:])
            st = op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()
            k0 = st.get("coincidencia") or {}
            check("4 corte · la operación queda INTERRUMPIDA (misma reservación, sigue PREPARADA, sin folio)",
                  k0.get("interrumpida") is True and k0.get("reservacion_id") == rid
                  and k0.get("estado_reservacion") == "PREPARADA" and not k0.get("folio"), k0)
            r = op.post(f"/api/autorrelleno/atenciones/{at2['id']}")
            check("4 corte · reabrir sin motivo se rechaza (hay que comprobar antes que la persona no confirmó)",
                  r.status_code == 409 and r.json()["detail"]["codigo"] == "OPERACION_INTERRUMPIDA", r.text[:200])
            res["corte"]["solicitud_cortada"] = sid_cortada
        t = time.perf_counter()
        vigia = VigiaCuenta(a.aislado) if a.aislado else None
        if a.interfaz:
            interfaz("autorrellenar", {"atencion": at2["id"], "fecha": fecha, "sucursal": "NOR-1", "espera_s": a.espera_s})
        else:
            r = op.post(f"/api/autorrelleno/atenciones/{at2['id']}",
                        json={"reintento": "VENTANA_CERRADA"} if a.corte else {})
            check("4 · autorrellenar lanzado (Forms real)" + (" con motivo, tras el corte" if a.corte else ""),
                  r.status_code == 202, r.text[:200])
            if a.aislado and r.status_code == 202:
                ais0 = r.json().get("aislamiento") or {}
                check("4 · aislado: red demostrada (Monitor llega al destino de prueba, la cuenta no) y la API de "
                      "Monitor rechaza a la cuenta sin sesión",
                      ais0.get("red_bloqueada") == 1 and ais0.get("api_sin_sesion") in ("HTTP_401", "HTTP_403"), ais0)
                r2 = op.post(f"/api/autorrelleno/atenciones/{at2['id']}")
                check("4 · un segundo clic con la ventana abierta no lanza otro Forms",
                      r2.status_code == 409 and r2.json()["detail"]["codigo"] in ("YA_HAY_UNA_EN_CURSO",
                                                                                  "OPERACION_INTERRUMPIDA"), r2.text[:200])
        fin = esperar(lambda: (lambda c: c if c and not c["en_curso"] else None)(
            op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()["corrida"]), a.espera_s + 180, 0.5)
        tiempos["forms_s"] = round(time.perf_counter() - t, 2)
        inf = (fin or {}).get("informe") or {}
        res["forms_resultado"] = {k: (fin or {}).get(k) for k in ("estado", "codigo", "resultado_operacion", "mensaje")}
        check("4 · Forms rellenó y verificó 8 campos y 2 confirmaciones sin enviar",
              fin and fin["estado"] in ("PREPARADO", "CADUCADA") and sum(c["correcto"] for c in inf.get("campos", [])) == 8
              and all(c["coinciden"] for c in inf.get("confirmaciones", [])) and not inf.get("envio_realizado_por_conector"),
              res["forms_resultado"])
        check("4 · la operación queda LISTA PARA CONFIRMACIÓN SUPERVISADA (no es cita)",
              fin and fin.get("resultado_operacion") == "LISTA_PARA_CONFIRMACION", res["forms_resultado"])
        if vigia is not None:
            vigia.parar()
            ais = (fin or {}).get("aislamiento") or {}
            res["aislado"].update(comprobacion=ais, navegador_de_la_cuenta=vigia.navegador, maximo_procesos=vigia.maximo)
            check("4 · aislado: Monitor comprobó como la cuenta que no puede leer sus datos antes de lanzar Forms",
                  ais.get("modo") == "cuenta_restringida" and ais.get("verificado") is True
                  and ais.get("rutas_denegadas", 0) >= 3, ais)
            check("4 · aislado: el navegador de Forms corrió como la cuenta del conector", vigia.navegador, vigia.maximo)
            restos = vigia.restos()
            check("4 · aislado: al terminar no queda ningún proceso de la cuenta", not restos, restos)
        res_ = op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()["coincidencia"]
        check("4 · reservación sigue PREPARADA, sin folio", res_ and res_["estado_reservacion"] == "PREPARADA"
              and not res_["folio"] and res_["lista_para_confirmacion"], res_)

        def reservaciones_del_expediente():
            return [x for x in adm.get("/api/reservaciones").json() if x.get("expediente_id") == exp_id]
        if a.interfaz:
            # 4 bis. reintento desde el botón, con motivo obligatorio: misma reservación, ninguna nueva
            antes = reservaciones_del_expediente()
            interfaz("reintento", {"atencion": at2["id"], "fecha": fecha, "sucursal": "NOR-1", "espera_s": a.espera_s})
            esperar(lambda: (lambda c: c if c and not c["en_curso"] else None)(
                op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()["corrida"]), a.espera_s + 180, 0.5)
            despues = reservaciones_del_expediente()
            k2 = op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()["coincidencia"] or {}
            res["reintento"] = {"reservaciones_antes": [x["id"] for x in antes], "reservaciones_despues": [x["id"] for x in despues],
                                "reintentos": k2.get("reintentos"), "reservacion": k2.get("reservacion_id")}
            check("4 bis · el reintento usó la MISMA reservación (no se duplicó) y quedó registrado con su motivo",
                  [x["id"] for x in antes] == [x["id"] for x in despues] == [rid] and k2.get("reservacion_id") == rid
                  and k2.get("reintentos") == 1 and k2.get("lista_para_confirmacion"), res["reintento"])
        # nada aparece como cita confirmada sin folio oficial validado
        exp = adm.get(f"/api/expedientes/{exp_id}").json()
        k3 = op.get(f"/api/autorrelleno/atenciones/{at2['id']}").json()["coincidencia"] or {}
        plan_estado = next((e["estado"] for p in adm.get("/api/planes").json()["planes"]
                            for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"] if e["expediente_id"] == exp_id), None)
        res["antes_del_folio"] = {"expediente": exp.get("estado"), "reservacion": k3.get("estado_reservacion"),
                                  "confirmada": k3.get("confirmada"), "folio": k3.get("folio"), "plan": plan_estado}
        check("4 · sin folio validado, nada aparece como cita confirmada (expediente, reservación, tarjeta y plan)",
              exp.get("estado") != "RESERVACION_CONFIRMADA" and not exp.get("folio") and k3.get("estado_reservacion") == "PREPARADA"
              and not k3.get("confirmada") and not k3.get("folio") and plan_estado != "CONFIRMADO", res["antes_del_folio"])

        # 5. la persona (SIMULADA) confirma en el portal de laboratorio
        folio = persona_confirma(d["portal"], persona, fecha, "NOR-1")
        check("5 · el portal de laboratorio mostró un folio a la persona", bool(folio), folio)
        res["folio_portal"] = folio

        # 6. folio -> verificación con el portal
        def mandar(f1, f2=None):
            return op.post(f"/api/autorrelleno/reservaciones/{rid}/folio", json={"folio": f1, "folio_confirmacion": f2 or f1})
        r = mandar(folio, folio + "X")
        check("6 · un folio tecleado distinto las dos veces se rechaza", r.status_code == 422, r.text[:200])
        if a.interfaz:
            interfaz("folio", {"atencion": at2["id"], "fecha": fecha, "sucursal": "NOR-1",
                               "folio": folio, "folio_falso": "DIA-20991231-99999Z"})
            vista_res = next((x for x in adm.get("/api/reservaciones").json() if x["id"] == rid), {})
            ok = vista_res.get("estado") == "CONFIRMADA"
            r_json = {"reservacion": vista_res,
                      "estado_plan": next((e["estado"] for p in adm.get("/api/planes").json()["planes"]
                                           for e in adm.get(f"/api/planes/{p['id']}").json()["elementos"]
                                           if e["expediente_id"] == exp_id), None)}
            check("6 · tras los folios tecleados en pantalla, la reservación está CONFIRMADA", ok, vista_res.get("estado"))
        else:
            r = mandar("DIA-20991231-99999Z")
            check("6 · un folio inexistente se rechaza y no cambia nada",
                  r.status_code == 409 and r.json()["detail"]["codigo"] == "FOLIO_INEXISTENTE", r.text[:200])
            r = mandar(folio)
            ok = r.status_code == 200
            r_json = r.json() if ok else {}
            check("6 · el folio correcto se COMPRUEBA con la API del portal y la cita queda CONFIRMADA",
                  ok and r_json["reservacion"]["estado"] == "CONFIRMADA", r.text[:300])
        if ok:
            ev = r_json["reservacion"].get("evidencia") or {}
            res["evidencia_confirmacion"] = {k: ev.get(k) for k in ("fuente", "verificada_con_proveedor", "comprobado",
                                                                    "estado_portal", "fecha_portal", "sucursal_portal")}
            check("6 · comprobado: estado, folio, titular, trámite, fecha y sucursal",
                  ev.get("comprobado") and all(ev["comprobado"].values()) and len(ev["comprobado"]) == 6, ev.get("comprobado"))
            check("6 · el plan pasa a CONFIRMADO", r_json.get("estado_plan") == "CONFIRMADO", r_json.get("estado_plan"))
        exp = adm.get(f"/api/expedientes/{exp_id}").json()
        check("6 · el expediente queda RESERVACION_CONFIRMADA", exp["estado"] == "RESERVACION_CONFIRMADA", exp["estado"])
        todas = reservaciones_del_expediente()
        check("6 · una sola reservación para el expediente (ni el reintento ni el corte crearon otra), con la fecha y "
              "la sucursal de la coincidencia", [x["id"] for x in todas] == [rid] and todas[0].get("fecha") == fecha
              and todas[0].get("sucursal") == "NOR-1", [{k: x.get(k) for k in ("id", "estado", "fecha", "sucursal")}
                                                        for x in todas])
        t = adm.get(f"/api/expedientes/{exp_id}/trazabilidad").json()
        tipos = [x.get("tipo") for x in t.get("linea_de_tiempo", [])]
        orden = ["PLAN_OPERACION_PREPARADA", "COINCIDENCIA_ENTREGADA_A_FORMS", "AUTORRELLENO_TERMINADO",
                 "OPERACION_LISTA_CONFIRMACION", "FOLIO_RECHAZADO", "RESERVACION_CONFIRMADA"]
        pos = [tipos.index(x) if x in tipos else -1 for x in orden]
        check("6 · historial en orden: preparada -> entregada a Forms -> lista -> rechazo -> confirmada",
              -1 not in pos and pos == sorted(pos), dict(zip(orden, pos)))
        res["linea_de_tiempo"] = tipos
        if a.observador == "web":
            evs = [e for e in adm.get("/api/observaciones").json()["eventos"] if e["tipo"] == "DIA_DISPONIBLE" and e["fecha"] == fecha]
            ev = (evs or [{}])[0].get("evidencia") or {}
            check("6 · el cambio quedó registrado con hora, fuente y evidencia (SHA-256 y captura)",
                  bool(evs) and evs[0]["observador"] == "web-calendario-lab" and len(ev.get("sha256", "")) == 64
                  and len(ev.get("imagen_sha256", "")) == 64, evs[:1])
            res["evento_observado"] = {k: (evs or [{}])[0].get(k) for k in ("detectado_en", "registrado_en", "observador",
                                                                            "sucursal", "fecha", "estado")}
            res["evento_observado"]["evidencia"] = {k: ev.get(k) for k in ("sha256", "imagen_sha256", "url")}
            adm.delete("/api/fuentes-web/calendario-lab")
        adm.delete("/api/observador")

        # 7. sin datos personales en registros
        import sqlite3
        con = sqlite3.connect(str(tmp / "lab" / "cauce_lab.db"))
        try:
            textos = " ".join(str(x) for x in con.execute("select detail, target from audit_log"))
            textos += " ".join(str(x) for x in con.execute("select detalle from expediente_eventos"))
        finally:
            con.close()
        fugas = [k for k in ("telefono", "correo", "fecha_nacimiento", "identificador") if persona[k] in textos]
        check("7 · auditoría e historial sin los datos personales ficticios", not fugas, fugas)
        valores = {k: persona[k] for k in ("nombre", "telefono", "correo", "fecha_nacimiento", "identificador")}
        valores["clave_identidad"] = MANUALES["clave_identidad"]
        forms = Path(a.forms)
        carpetas = [tmp / "lab", forms / "logs", forms / "state", out] + ([raiz_conector] if raiz_conector else [])
        hall = buscar_datos(carpetas, valores, desde=t0,
                            excluir=(tmp / "lab" / "portal.json",))
        check("7 · ningún archivo de texto (motor, portal, Forms, evidencia) contiene los datos ficticios", not hall, hall)
    except Exception as e:  # noqa: BLE001
        check("el recorrido se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        lab.detener()
        if a.aislado:
            regla_red(a.aislado, False)
        res["tiempos"] = tiempos
        res["comprobaciones"] = ck
        res["ok"] = bool(ck) and all(x["ok"] for x in ck)
        res["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (out / "recorrido_coincidencia_forms.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
        print(f"\n{sum(x['ok'] for x in ck)}/{len(ck)} comprobaciones. Evidencia: {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

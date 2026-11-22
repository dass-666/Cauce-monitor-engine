"""v2.3/v2.4 · Recorrido integral del laboratorio: respaldo, restauración, RECUPERACIÓN SEGURA, continuidad,
cierre y retención.

Reutiliza lo que ya existe y NO reimplementa nada:
  - `ventanilla_laboratorio.Laboratorio`: motor + portal de laboratorio propio (127.0.0.1) en una carpeta
    aislada, con consentimiento exigido (no toca `data/` del proyecto);
  - la API del motor tal como la usan demo_v22 y la Ventanilla (expedientes, consentimiento, plan,
    observador, operación, confirmación);
  - `python -m monitoreo.migrate respaldar | restaurar` (respaldo y restauración reales);
  - `monitoreo.captura.retencion.aplicar(db, now)` con una FECHA SIMULADA (+2 días). El reloj del equipo
    no se toca y los plazos solo cambian en la base del laboratorio.
El recorrido por la interfaz (abrir Ventanilla → folio → historial) ya lo demuestra
`recorrido_ventanilla_v23.py`; aquí se demuestra lo que pasa DESPUÉS y ALREDEDOR de un respaldo.

Etapas (un mismo laboratorio, datos ficticios):
  1. Alta: A, B y G con consentimiento (al plan); E con consentimiento (fuera del plan); D sin consentimiento.
  2. Observador + día 1 -> A asignado -> operación -> confirmación SIMULADA -> folio F1.
  3. Día 2 -> B asignado -> operación preparada y día elegido, SIN confirmar (operación a medias).
  4. RESPALDO con el motor en marcha + huella del estado.
  5. Cambios POSTERIORES al respaldo: se confirma B (folio F2 en el portal), se da de alta X y se revoca G.
  6. Motor detenido -> RESTAURAR -> motor arrancado sobre la misma carpeta.
  7. Comprobaciones: el estado de la base es el del respaldo, X no existe, la base tiene a G con su
     consentimiento VIGENTE (pero el motor NO lo trata como vigente), cadenas íntegras, un solo observador.
  7b. v2.4 · Recuperación segura: el diario de eventos críticos (fuera de la base) delata la confirmación de B
     y la revocación de G; las operaciones con el portal quedan BLOQUEADAS (también tras reiniciar); el
     administrador concilia (folio de B comprobado en el portal, revocación de G reaplicada), con motivo y
     auditoría, y solo entonces levanta el bloqueo.
  8. Continuidad: B queda CONFIRMADA con el MISMO folio del portal y una sola cita; el plan se sincroniza.
  9. Retención con fecha simulada +2 días: anonimiza solo lo que corresponde (G, revocada y cerrada, incluida).
 10. Nada fuera del laboratorio: `git status` y `data/` del proyecto sin cambios; registros sin datos personales.

Uso: python scripts/recorrido_integral.py [--salida resultados/recorrido_integral]
Salida: <salida>/recorrido_integral.json. Código 0 si todo pasa, 1 si algo falla.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from verificar_arranque import base_env  # noqa: E402
from ventanilla_laboratorio import ADMIN_PWD, OPERADOR, ZONA, Laboratorio  # noqa: E402

MANUALES = {"clave_identidad": "QXZ482915", "acepto_privacidad": "si"}     # ficticios (los mismos que demo_v22)
DIAS_SIMULADOS = 2


def cons(n: int) -> dict:
    return {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01", "evidencia_ref": f"Carpeta LAB-{n:02d}",
            "evidencia_sha256": hashlib.sha256(f"formato ficticio integral {n}".encode()).hexdigest()}


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


def esperar(pred, t=30.0, paso=0.3):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        try:
            v = pred()
        except (httpx.HTTPError, ValueError, KeyError, IndexError, StopIteration, TypeError):
            v = None
        if v:
            return v
        time.sleep(paso)
    return None


def huella_proyecto() -> dict:
    """Lo que NO debe cambiar fuera del laboratorio: el árbol git y la carpeta data/ del proyecto."""
    st = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain=v1", "-uall"], capture_output=True, text=True)
    data = ROOT / "data"
    archivos = sorted((str(p.relative_to(ROOT)), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in data.rglob("*") if p.is_file()) if data.exists() else []
    return {"git": st.stdout if st.returncode == 0 else None, "data": hashlib.sha256(repr(archivos).encode()).hexdigest()}


class Recorrido:
    def __init__(self, carpeta: Path, externa: Path | None = None, misma_unidad: bool = False):
        self.lab = Laboratorio(carpeta, nuevo=True)
        # Piloto: con --externa el diario se copia y la base se respalda en OTRO medio (USB) y se restaura desde él.
        self.externa, self.misma_unidad = externa, misma_unidad
        if externa is not None:
            (externa / "diario").mkdir(parents=True, exist_ok=True)
            (externa / "respaldos").mkdir(parents=True, exist_ok=True)
            self.lab.env_extra["MONITOREO_DIARIO_COPIA"] = str(externa / "diario")
        self.ck: list[dict] = []
        self.res: dict = {"version": "2.4", "plataforma": platform.platform(), "python": platform.python_version(),
                          "inicio": time.strftime("%Y-%m-%dT%H:%M:%S"), "confirmacion_humana": "SIMULADA por el script",
                          "fecha_retencion": f"SIMULADA: ahora + {DIAS_SIMULADOS} días (el reloj del equipo no cambia)",
                          "datos": "ficticios; portal de laboratorio propio en 127.0.0.1"}
        self.personales: list[str] = []

    # ------------------------------------------------------------ utilidades
    def check(self, nombre, ok, detalle=None):
        self.ck.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})
        print(("OK    " if ok else "FALLA ") + nombre + ("" if ok else f"  {str(detalle)[:400]}"), flush=True)
        return ok

    def env_bd(self) -> dict:
        c = self.lab.carpeta
        env = base_env(c, "desarrollo")
        env.update({"MONITOREO_DB_URL": f"sqlite:///{(c / 'cauce_lab.db').as_posix()}",
                    "MONITOREO_DATA_DIR": str(c / "datos"), "MONITOREO_SECRET_KEY": self.lab.cfg["secret"],
                    "MONITOREO_IGNORAR_DOTENV": "1"})
        if self.externa is not None:
            env["MONITOREO_DIARIO_COPIA"] = str(self.externa / "diario")
            env["MONITOREO_RESPALDO_EXTERNO"] = str(self.externa / "respaldos")
        return env

    def respaldo_externo(self, *args) -> tuple[int, dict]:
        extra = ["--laboratorio"] if self.misma_unidad else []
        r = subprocess.run([sys.executable, "-m", "monitoreo.respaldo_externo", *args, *extra], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=self.env_bd(), cwd=str(ROOT), timeout=300)
        try:
            return r.returncode, json.loads(r.stdout)
        except ValueError:
            return r.returncode, {"salida": (r.stdout + r.stderr)[-400:]}

    def migrate(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "monitoreo.migrate", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.env_bd(), cwd=str(ROOT), timeout=120)

    def conectar(self):
        base = self.lab.motor.base
        self.adm = login(base, "admin", ADMIN_PWD)
        self.op = login(base, *OPERADOR)

    def plan(self) -> dict:
        return {x["expediente_id"]: x for x in self.adm.get(f"/api/planes/{self.pid}").json()["elementos"]}

    def exp(self, e) -> dict:
        return self.adm.get(f"/api/expedientes/{e['id']}").json()

    def traza(self, e) -> dict:
        return self.adm.get(f"/api/expedientes/{e['id']}/trazabilidad").json()

    def portal_reservas(self) -> list[dict]:
        return self.lab.portal.ctl("GET", "/control/reservaciones")["reservaciones"]

    def ronda(self, fecha: str):
        self.lab.portal.ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
        return esperar(lambda: next((x for x in self.plan().values() if x["fecha_asignada"] == fecha
                                     and x["estado"] in ("ASIGNADO", "OPERACION_PREPARADA")), None), 45)

    def preparar(self, el: dict, fecha: str) -> int:
        r = self.op.post(f"/api/planificador/elementos/{el['id']}/preparar")
        r.raise_for_status()
        rid = r.json()["reservacion_id"]
        self.op.post(f"/api/reservaciones/{rid}/enviar-datos",
                     json={"revisado": True, "manuales": MANUALES, "sucursal": "NOR-1"}).raise_for_status()
        self.op.post(f"/api/reservaciones/{rid}/seleccionar-dia", json={"fecha": fecha, "sucursal": "NOR-1"}).raise_for_status()
        return rid

    def confirmar(self, rid: int) -> httpx.Response:
        return self.op.post(f"/api/reservaciones/{rid}/confirmar", json={"confirmo": True})   # SIMULADA

    def estado_bd(self) -> dict:
        """Huella de lo que importa tras un respaldo: expedientes, consentimientos, plan, reservaciones y observador."""
        exps = sorted((x["codigo"], x["estado"]) for x in self.adm.get("/api/expedientes", params={"limit": 2000}).json())
        res = {}
        for nombre, e in self.e.items():
            t = self.traza(e)
            res[nombre] = {"estado": t["expediente"]["estado"] if "expediente" in t else self.exp(e)["estado"],
                           "consentimientos": [c["estado"] for c in self.adm.get(
                               f"/api/expedientes/{e['id']}/consentimientos").json()["consentimientos"]],
                           "reservaciones": sorted((r.get("estado"), r.get("folio")) for r in t.get("reservaciones", [])),
                           "cadena_ok": (t.get("verificacion_consentimientos") or {}).get("ok")}
        planes = self.adm.get("/api/planes").json()
        planes = planes.get("planes", planes) if isinstance(planes, dict) else planes
        obs = self.adm.get("/api/observador").json()["observadores"]
        return {"expedientes": exps, "detalle": res,
                "plan": sorted((x["codigo"], x["estado"]) for x in self.plan().values()),
                "planes": len(planes), "observadores": len(obs)}

    # ------------------------------------------------------------ v2.4: recuperación segura
    def recuperacion(self, rid_b: int, folio2: str | None, f2: str) -> None:
        lab, ck = self.lab, self.check
        rec = self.adm.get("/api/recuperacion").json()
        mot = {m["codigo"] for m in rec.get("motivos", [])}
        disc = {d["tipo"]: d for d in rec.get("discrepancias", [])}
        self.res["recuperacion_detectada"] = {"motivos": sorted(mot), "discrepancias": [
            {k: d.get(k) for k in ("ref", "tipo", "expediente", "reservacion_id", "acciones")} for d in disc.values()]}
        ck("v2.4 detección: tras restaurar el motor arranca con RECUPERACION_PENDIENTE",
           rec.get("bloqueado") and {"RESTAURACION_PENDIENTE", "DISCREPANCIAS"} <= mot, sorted(mot))
        ck("v2.4 detección: exactamente 2 discrepancias, la confirmación de B y la revocación de G",
           set(disc) == {"CONFIRMACION_AUSENTE", "REVOCACION_AUSENTE"}
           and disc.get("CONFIRMACION_AUSENTE", {}).get("reservacion_id") == rid_b
           and disc.get("REVOCACION_AUSENTE", {}).get("expediente_id") == self.e["G"]["id"],
           self.res["recuperacion_detectada"])
        citas_antes = [x.get("folio") for x in self.portal_reservas() if x.get("estado") == "CONFIRMADA" and x.get("fecha") == f2]
        r = self.confirmar(rid_b)
        cod = (r.json().get("detail") or {}).get("codigo") if r.status_code == 409 else None
        ck("v2.4 bloqueo: confirmar B (operador) responde 409 RECUPERACION_PENDIENTE", cod == "RECUPERACION_PENDIENTE",
           {"http": r.status_code, "cuerpo": r.text[:200]})
        r = self.adm.post(f"/api/reservaciones/{rid_b}/confirmar", json={"confirmo": True})
        ck("v2.4 bloqueo: tampoco el administrador puede operar con el portal", r.status_code == 409, r.status_code)
        citas = [x.get("folio") for x in self.portal_reservas() if x.get("estado") == "CONFIRMADA" and x.get("fecha") == f2]
        ck("v2.4 bloqueo: nada se repitió solo: el portal sigue con una sola cita de ese día", citas == citas_antes
           and len(citas) == 1, citas)
        r = self.adm.post("/api/recuperacion/levantar", json={"motivo": "intento sin conciliar"})
        ck("v2.4 bloqueo: no se puede levantar con discrepancias pendientes", r.status_code == 409, r.text[:200])
        lab.motor.stop()
        lab.motor.start()
        lab.motor.wait_ready()
        self.conectar()
        rec = self.adm.get("/api/recuperacion").json()
        ck("v2.4 bloqueo: persiste tras reiniciar el motor", bool(rec.get("bloqueado")), rec.get("motivos"))

        def resolver(d, accion, **kw):
            return self.adm.post(f"/api/recuperacion/discrepancias/{d['ref']}/resolver",
                                 json={"tipo": d["tipo"], "accion": accion, **kw})
        dc, dr = disc.get("CONFIRMACION_AUSENTE", {"ref": 0, "tipo": "?"}), disc.get("REVOCACION_AUSENTE", {"ref": 0, "tipo": "?"})
        r = self.op.post(f"/api/recuperacion/discrepancias/{dc['ref']}/resolver",
                         json={"tipo": dc["tipo"], "accion": "registrar_folio", "motivo": "operador", "folio": folio2})
        ck("v2.4 conciliación: un operador no puede conciliar (403)", r.status_code == 403, r.status_code)
        r = resolver(dc, "registrar_folio", motivo="folio comprobado en el portal", folio="F-QUE-NO-ES")
        ck("v2.4 conciliación: un folio que no es el del diario se rechaza (FOLIO_NO_COINCIDE)",
           r.status_code == 422 and (r.json().get("detail") or {}).get("codigo") == "FOLIO_NO_COINCIDE", r.text[:200])
        # SIMULADO: el administrador lee el folio en el portal (aquí, el calendario de control del laboratorio)
        folio_portal = citas[0] if citas else None
        r = resolver(dc, "registrar_folio", motivo="folio comprobado en el portal de laboratorio", folio=folio_portal)
        ck("v2.4 conciliación: con el folio del portal, B queda CONFIRMADA (evidencia conciliacion_manual)",
           r.status_code == 200 and folio_portal == folio2, {"http": r.status_code, "folio": folio_portal, "f2": folio2})
        r = resolver(dr, "reaplicar_revocacion", motivo="revocación por escrito, registrada en el diario")
        cg = self.adm.get(f"/api/expedientes/{self.e['G']['id']}/consentimientos").json()
        ck("v2.4 conciliación: la revocación de G se reaplica (cadena de sellos íntegra)",
           r.status_code == 200 and cg["consentimientos"][-1]["estado"] == "REVOCADO"
           and self.adm.get("/api/consentimientos/verificar").json().get("ok"), {"http": r.status_code, "texto": r.text[:200]})
        rec = r.json() if r.status_code == 200 else {}
        ck("v2.4 conciliación: 0 pendientes, pero el bloqueo sigue hasta que el administrador lo levante",
           rec.get("pendientes") == 0 and rec.get("bloqueado"), {k: rec.get(k) for k in ("pendientes", "bloqueado")})
        r = self.adm.post("/api/recuperacion/levantar", json={"motivo": "conciliación revisada por el administrador"})
        ck("v2.4 desbloqueo: se levanta solo ahora", r.status_code == 200 and r.json().get("levantado"), r.text[:200])
        aud = [a["accion"] for a in self.adm.get("/api/audit", params={"limit": 200}).json()]
        ck("v2.4 auditoría: 2 conciliaciones y el desbloqueo quedan en el registro de auditoría",
           aud.count("recuperacion_conciliada") == 2 and "recuperacion_levantada" in aud,
           {"conciliadas": aud.count("recuperacion_conciliada"), "levantada": "recuperacion_levantada" in aud})
        diario = lab.carpeta / "datos" / "diario" / "eventos-criticos.jsonl"
        texto = diario.read_text("utf-8") if diario.exists() else ""
        ck("v2.4 diario: fuera de la base, sin códigos de expediente, folios ni datos personales en claro",
           bool(texto) and not any(x and x in texto for x in [folio2, *self.personales, *(e.get("codigo") for e in self.e.values())]),
           {"existe": diario.exists()})
        self.res["recuperacion_final"] = {k: rec.get(k) for k in ("pendientes",)}

    # ------------------------------------------------------------ etapas
    def run(self) -> None:
        lab, ck = self.lab, self.check
        proyecto_antes = huella_proyecto()
        d = lab.iniciar()
        self.conectar()
        adm, op, ctl = self.adm, self.op, lab.portal.ctl
        ck("laboratorio en marcha: motor y portal propios en 127.0.0.1",
           d["motor"].startswith("http://127.0.0.1") and d["portal"].startswith("http://127.0.0.1"), d)

        # 1. alta ----------------------------------------------------------------
        dias = ctl("GET", "/control/dias")
        hoy = date.fromisoformat(dias["hoy"])
        ocupadas = {x["fecha"] for x in dias["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
        mes = None
        for k in range(2, 10):
            y, m = hoy.year + (hoy.month - 1 + k) // 12, (hoy.month - 1 + k) % 12 + 1
            if not any(f.startswith(f"{y:04d}-{m:02d}") for f in ocupadas):
                mes = (y, m)
                break
        y, m = mes
        libres = [date(y, m, k).isoformat() for k in range(1, calendar.monthrange(y, m)[1] + 1)]
        regs, n = [], 300
        while len(regs) < 7 and n < 1200:
            r = lab.portal.registro(n)
            if r["tramite"] == "alta":
                regs.append(r)
            n += 1

        def ficha(i: int, nombre: str, prioridad: int) -> dict:
            f = {"nombre": f"Persona Integral {nombre}", "fecha_nacimiento": regs[i]["fecha_nacimiento"],
                 "telefono": f"55500077{i:02d}", "telefono_confirmacion": f"55500077{i:02d}",
                 "correo": f"integral{i}@ejemplo.test", "correo_confirmacion": f"integral{i}@ejemplo.test",
                 "region": "NORTE", "sucursal": "NOR-1", "sucursales_alternativas": [], "tipo_tramite": "alta",
                 "id_registro_previo": regs[i]["identificador"], "prioridad": prioridad}
            self.personales += [f["nombre"], f["telefono"], f["correo"]]
            return f
        self.e = {}
        for i, (k, pr) in enumerate((("A", 1), ("B", 2), ("G", 3), ("E", 4))):
            r = adm.post("/api/expedientes", json={**ficha(i, k, pr), "consentimiento": cons(i)})
            self.e[k] = r.json() if r.status_code == 201 else {"id": None, "estado": f"HTTP {r.status_code}"}
        r = adm.post("/api/expedientes", json=ficha(4, "D", 5))
        self.e["D"] = r.json()
        ck("alta: A, B, G y E con consentimiento (LISTO); D sin consentimiento (INCOMPLETO)",
           [self.e[k].get("estado") for k in "ABGED"] == ["LISTO"] * 4 + ["INCOMPLETO"],
           {k: v.get("estado") for k, v in self.e.items()})
        jornada = datetime.now(__import__("zoneinfo").ZoneInfo(ZONA)).date().isoformat()
        self.pid = adm.post("/api/planes", json={"fecha_jornada": jornada, "capacidad": 3}).json()["id"]
        r = adm.post(f"/api/planes/{self.pid}/elementos", json={"elementos": [
            {"expediente_id": self.e[k]["id"], "fechas_desde": libres[0], "fechas_hasta": libres[-1]} for k in "ABG"]}).json()
        ck("plan del día con A, B y G", len(r["aceptados"]) == 3, r.get("rechazados"))
        ctl("POST", "/control/vista", refresco_ms=500)
        r = adm.post("/api/observador", json={"sucursal": "NOR-1", "tramite": "alta", "mes": f"{y:04d}-{m:02d}",
                                              "estrategia": "dom"})
        activo = esperar(lambda: (adm.get("/api/observador").json()["observadores"][0]["vista"] or {}).get("dom") == "ACTIVA", 90)
        ck("observador del calendario del laboratorio ACTIVO", r.status_code == 200 and bool(activo), r.text[:160])

        # 2. A hasta folio ----------------------------------------------------------
        f1, f2 = libres[9], libres[11]
        el = self.ronda(f1)
        ck("día 1 -> A asignado (mayor prioridad)", el and el["expediente_id"] == self.e["A"]["id"], el)
        rid_a = self.preparar(el, f1)
        r = self.confirmar(rid_a)
        folio1 = r.json().get("reservacion", {}).get("folio") if r.status_code == 200 else None
        ck("A: confirmación SIMULADA -> folio F1", bool(folio1), r.text[:200])
        ok = esperar(lambda: self.plan()[self.e["A"]["id"]]["estado"] == "CONFIRMADO", 30)
        ck("A: el plan se sincroniza a CONFIRMADO", bool(ok), self.plan()[self.e["A"]["id"]]["estado"])

        # 3. B a medias -------------------------------------------------------------
        el = self.ronda(f2)
        ck("día 2 -> B asignado", el and el["expediente_id"] == self.e["B"]["id"], el)
        rid_b = self.preparar(el, f2)
        rb = next(x for x in self.traza(self.e["B"])["reservaciones"] if x["id"] == rid_b) \
            if any(x.get("id") == rid_b for x in self.traza(self.e["B"]).get("reservaciones", [])) else {}
        ck("B: operación preparada y día elegido, SIN confirmar", rb.get("estado") not in (None, "CONFIRMADA"), rb.get("estado"))

        # 4. respaldo -----------------------------------------------------------------
        h_respaldo = self.estado_bd()
        portal_respaldo = self.portal_reservas()
        if self.externa is None:
            r = self.migrate("respaldar")
            archivo = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None
            ck("respaldo con el motor en marcha", bool(archivo) and Path(archivo).is_file()
               and Path(archivo).resolve().is_relative_to(lab.carpeta.resolve()), {"rc": r.returncode, "salida": (r.stdout + r.stderr)[-300:]})
        else:
            rc_, rj = self.respaldo_externo("respaldar")
            archivo = rj.get("archivo")
            ck("respaldo EXTERNO con el motor en marcha: copia releída del medio, SHA-256 y manifiesto",
               rc_ == 0 and bool(archivo) and Path(archivo).is_file()
               and Path(archivo).resolve().is_relative_to(self.externa.resolve()), rj)
            rc_, rv = self.respaldo_externo("ensayar", archivo or "-")
            ck("ensayo de restauración en carpeta temporal (sin tocar la base del laboratorio)", rc_ == 0 and rv.get("ok"),
               rv.get("problemas") or rv.get("salida"))
            self.res["respaldo_externo"] = {"sha256": rj.get("sha256"), "bytes": rj.get("bytes"),
                                            "avisos": rj.get("avisos"), "ensayo_filas": rv.get("filas")}
        self.res["respaldo"] = archivo and Path(archivo).name

        # 5. cambios posteriores -------------------------------------------------------
        r = self.confirmar(rid_b)
        folio2 = r.json().get("reservacion", {}).get("folio") if r.status_code == 200 else None
        ck("después del respaldo: B confirmada en el portal (folio F2)", bool(folio2), r.text[:200])
        r = adm.post("/api/expedientes", json={**ficha(5, "X", 5), "consentimiento": cons(5)})
        x_codigo = r.json().get("codigo") if r.status_code == 201 else None
        cid = adm.get(f"/api/expedientes/{self.e['G']['id']}/consentimientos").json()["consentimientos"][-1]["id"]
        rv = adm.post(f"/api/consentimientos/{cid}/revocar", json={"medio": "ESCRITO", "motivo": "PERSONA_LO_PIDE"})
        ck("después del respaldo: alta de X y revocación de G", bool(x_codigo) and rv.status_code == 200,
           {"x": x_codigo, "revocar": rv.status_code})

        # 6. restaurar -------------------------------------------------------------------
        lab.motor.stop()
        t0 = time.monotonic()
        r = self.migrate("restaurar", archivo)
        while r.returncode != 0 and "en marcha" in (r.stdout + r.stderr) and time.monotonic() - t0 < 40:
            time.sleep(2)                 # el arriendo del planificador caduca a los 15 s de parar
            r = self.migrate("restaurar", archivo)
        ck("restauración con el motor detenido (y respaldo previo automático)", r.returncode == 0,
           (r.stdout + r.stderr)[-400:])
        if self.externa is not None:
            copia = self.externa / "diario" / "eventos-criticos.jsonl"
            principal = lab.carpeta / "datos" / "diario" / "eventos-criticos.jsonl"
            ck("la copia externa del diario es idéntica al principal tras restaurar (incluida la marca de restauración)",
               copia.is_file() and principal.is_file() and copia.read_bytes() == principal.read_bytes(),
               {"copia": copia.is_file(), "principal": principal.is_file()})
        lab.motor.start()
        lab.motor.wait_ready()
        self.conectar()
        adm, op = self.adm, self.op

        # 7. el estado es el del respaldo ------------------------------------------------
        activo = esperar(lambda: (adm.get("/api/observador").json()["observadores"][0]["vista"] or {}).get("dom") == "ACTIVA", 90)
        h_rest = self.estado_bd()
        ck("tras restaurar: expedientes, consentimientos, plan y reservaciones = los del respaldo",
           h_rest == h_respaldo, {"respaldo": h_respaldo, "restaurado": h_rest} if h_rest != h_respaldo else None)
        ck("tras restaurar: X (alta posterior) no existe",
           x_codigo not in {c for c, _ in h_rest["expedientes"]}, x_codigo)
        ck("tras restaurar: la base tiene a G con su consentimiento VIGENTE (la revocación era posterior)",
           h_rest["detalle"]["G"]["consentimientos"][-1] != "REVOCADO", h_rest["detalle"]["G"])
        cg = adm.get(f"/api/expedientes/{self.e['G']['id']}/consentimientos").json()
        ck("v2.4: pero el motor NO trata a G como vigente: la revocación está en el diario (nunca se reactiva sola)",
           cg["vigente"] is None and (cg.get("bloqueo") or {}).get("codigo") == "REVOCACION_EN_DIARIO", cg.get("bloqueo"))
        ck("tras restaurar: cadenas de sellos de consentimiento íntegras",
           all(v["cadena_ok"] for v in h_rest["detalle"].values()), {k: v["cadena_ok"] for k, v in h_rest["detalle"].items()})
        ck("tras restaurar: un solo plan y un solo observador, y el observador vuelve solo",
           h_rest["planes"] == 1 and h_rest["observadores"] == 1 and bool(activo),
           {"planes": h_rest["planes"], "observadores": h_rest["observadores"], "activo": bool(activo)})
        ck("tras restaurar: F1 sigue CONFIRMADA en el motor", ("CONFIRMADA", folio1) in h_rest["detalle"]["A"]["reservaciones"],
           h_rest["detalle"]["A"]["reservaciones"])

        # 7b. recuperación segura (v2.4) -----------------------------------------------------
        self.recuperacion(rid_b, folio2, f2)
        adm, op = self.adm, self.op

        # 8. continuidad: B quedó a medias en la base pero confirmada en el portal ---------------
        r = self.confirmar(rid_b)
        cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"texto": r.text[:200]}
        folio_b = (cuerpo.get("reservacion") or {}).get("folio")
        en_portal = self.portal_reservas()
        de_b = [x for x in en_portal if x.get("estado") == "CONFIRMADA" and x.get("fecha") == f2]
        self.res["continuidad_b"] = {"http": r.status_code, "folio_motor": folio_b, "folio_portal_tras_respaldo": folio2,
                                     "citas_confirmadas_ese_dia_en_portal": [x.get("folio") for x in de_b]}
        ck("continuidad: tras desbloquear, la operación de B ya está CONFIRMADA (se responde sin llamar al portal)",
           r.status_code == 200 and bool(cuerpo.get("repetida")), {"http": r.status_code, "repetida": cuerpo.get("repetida")})
        ck("continuidad: confirmar otra vez la operación de B NO crea una segunda cita en el portal",
           len(de_b) == 1, self.res["continuidad_b"])
        ck("continuidad: el motor termina con el MISMO folio que tiene el portal (F2)",
           folio_b == folio2 or any(r_[1] == folio2 for r_ in self.estado_bd()["detalle"]["B"]["reservaciones"]),
           self.res["continuidad_b"])
        ok = esperar(lambda: self.plan()[self.e["B"]["id"]]["estado"] == "CONFIRMADO", 30)
        ck("continuidad: el plan de B se sincroniza a CONFIRMADO", bool(ok), self.plan()[self.e["B"]["id"]]["estado"])
        ck("portal: las citas anteriores al respaldo siguen igual",
           all(any(y.get("folio") == x.get("folio") and y.get("estado") == x.get("estado") for y in en_portal)
               for x in portal_respaldo), None)

        # 9. retención con fecha simulada -----------------------------------------------------
        adm.put("/api/settings/engine", json={"retencion_confirmados_dias": 1, "retencion_cerrados_dias": 1,
                                              "retencion_incompletos_dias": 1, "retencion_respaldos_dias": 1,
                                              "retencion_accion": "ANONIMIZAR"}).raise_for_status()
        antes_ret = {k: self.exp(v) for k, v in self.e.items()}
        dir_resp = lab.carpeta / "datos" / "respaldos"
        n_resp = len([f for f in dir_resp.iterdir() if f.is_file() and f.name.startswith("monitoreo-")]) if dir_resp.exists() else 0
        codigo = ("import json,sys;from datetime import timedelta;from monitoreo.db import Database,utcnow;"
                  "from monitoreo.config import settings;from monitoreo.captura import retencion;"
                  f"r=retencion.aplicar(Database(settings.db_url),utcnow()+timedelta(days={DIAS_SIMULADOS}),actor='recorrido-integral');"
                  "print(json.dumps(r,default=str))")
        p = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=self.env_bd(), cwd=str(ROOT), timeout=120)
        ret = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {"error": p.stderr[-400:]}
        self.res["retencion"] = ret
        despues = {k: self.exp(v) for k, v in self.e.items()}
        anon = {k: bool(v.get("anonimizado")) for k, v in despues.items()}
        ck("retención +2 días: A y B (citas confirmadas) y D (incompleto) se anonimizan",
           anon["A"] and anon["B"] and anon["D"], anon)
        ck("retención: D (sin consentimiento, sin cambios) se cierra por RETENCION",
           despues["D"]["estado"] == "CERRADO" and (despues["D"].get("motivo_cierre") or "").upper() == "RETENCION",
           {"estado": despues["D"]["estado"], "motivo": despues["D"].get("motivo_cierre")})
        ck("retención: E (LISTO, esperando) NO se toca",
           not anon["E"] and despues["E"]["estado"] == antes_ret["E"]["estado"],
           {"E": [antes_ret["E"]["estado"], despues["E"]["estado"], anon["E"]]})
        ck("retención: G (consentimiento revocado y reaplicado, expediente cerrado) se anonimiza",
           antes_ret["G"]["estado"] == "CERRADO" and anon["G"],
           {"G": [antes_ret["G"]["estado"], despues["G"]["estado"], anon["G"]]})
        ta, tb = self.traza(self.e["A"]), self.traza(self.e["B"])
        ck("retención: se conservan código, folio e historial; desaparecen nombre, teléfono y correo",
           despues["A"]["codigo"] == antes_ret["A"]["codigo"]
           and any(r_.get("folio") == folio1 for r_ in ta.get("reservaciones", []))
           and any(r_.get("folio") == folio2 for r_ in tb.get("reservaciones", []))
           and len(ta.get("linea_de_tiempo", [])) > 0
           and "Persona Integral A" not in json.dumps(despues["A"], ensure_ascii=False)
           and not despues["A"].get("telefono") and not despues["A"].get("correo"),
           {"nombre": despues["A"].get("nombre"), "telefono": despues["A"].get("telefono")})
        ck("retención: las cadenas de consentimiento siguen íntegras tras anonimizar",
           all((self.traza(v).get("verificacion_consentimientos") or {}).get("ok") for v in self.e.values()),
           {k: (self.traza(v).get("verificacion_consentimientos") or {}).get("ok") for k, v in self.e.items()})
        quedan = len([f for f in dir_resp.iterdir() if f.is_file() and f.name.startswith("monitoreo-")]) if dir_resp.exists() else 0
        ck("retención: los respaldos del laboratorio se purgan conservando los 3 más recientes",
           n_resp > 3 and quedan == 3 and (ret.get("respaldos") or {}).get("borrados") == n_resp - 3,
           {"antes": n_resp, "quedan": quedan, "purga": ret.get("respaldos")})
        ck("portal: las citas confirmadas no cambian por la retención",
           sum(x.get("estado") == "CONFIRMADA" for x in self.portal_reservas()) == sum(x.get("estado") == "CONFIRMADA" for x in en_portal),
           None)

        # 10. nada fuera del laboratorio -----------------------------------------------------
        lab.detener()
        logs = "".join(f.read_text("utf-8", errors="replace") for f in lab.carpeta.rglob("*.log"))
        fugas = sorted({x for x in self.personales if x in logs})
        ck("registros del motor y del portal sin los datos personales ficticios", not fugas, fugas[:5])
        proyecto_despues = huella_proyecto()
        ck("fuera del laboratorio no cambió nada (git y data/ del proyecto)", proyecto_despues == proyecto_antes,
           {"antes": proyecto_antes, "despues": proyecto_despues} if proyecto_despues != proyecto_antes else None)
        self.res.update(folio_A=folio1, folio_B=folio2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(ROOT / "resultados" / "recorrido_integral"))
    ap.add_argument("--externa", help="carpeta en OTRO medio (USB): copia del diario y respaldo externo de la base; "
                                      "se crea dentro una subcarpeta del simulacro que se borra al terminar")
    ap.add_argument("--laboratorio-misma-unidad", action="store_true",
                    help="solo CI: acepta que --externa esté en la misma unidad (queda escrito en el resultado)")
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="integral-"))
    externa = None
    if a.externa:
        externa = Path(a.externa) / f"cauce-simulacro-{time.strftime('%Y%m%d-%H%M%S')}"
    rec = Recorrido(tmp / "lab", externa=externa, misma_unidad=a.laboratorio_misma_unidad)
    if externa is not None:
        rec.res["medio_externo"] = {"carpeta": str(externa), "misma_unidad_aceptada": a.laboratorio_misma_unidad}
    try:
        rec.run()
    except Exception as e:  # noqa: BLE001
        rec.check("el recorrido se detuvo por un error", False, f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        rec.lab.detener()
        for f in rec.lab.carpeta.glob("*.log"):
            shutil.copy(f, out / f.name)
        rec.res["comprobaciones"] = rec.ck
        rec.res["aprobadas"], rec.res["total"] = sum(x["ok"] for x in rec.ck), len(rec.ck)
        (out / "recorrido_integral.json").write_text(json.dumps(rec.res, ensure_ascii=False, indent=2), "utf-8")
        shutil.rmtree(tmp, ignore_errors=True)
        if externa is not None:
            shutil.rmtree(externa, ignore_errors=True)
    print(f"\nRESULTADO: {rec.res['aprobadas']}/{rec.res['total']} comprobaciones · evidencia en {out / 'recorrido_integral.json'}")
    return 0 if rec.ck and all(x["ok"] for x in rec.ck) else 1


if __name__ == "__main__":
    sys.exit(main())

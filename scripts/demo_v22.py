"""v2.2 · Demostración completa y VERIFICABLE del consentimiento con procesos y navegador reales.

Todo con expedientes FICTICIOS y el portal de LABORATORIO propio (127.0.0.1). No se conecta a ningún
portal externo. La exigencia de consentimiento está ACTIVA (MONITOREO_EXIGIR_CONSENTIMIENTO=1).

Procesos: portal de laboratorio (`python -m portal_reservas`) y motor (`python -m monitoreo`, con su
planificador en marcha). El observador (Node + Playwright/Chromium) lo lanza el motor por su API.

  1. Registro: 6 expedientes con consentimiento (LISTO); uno sin consentimiento (INCOMPLETO, el plan lo
     rechaza) y, al registrar su consentimiento, pasa a LISTO; un consentimiento inválido no crea nada.
  2. Duplicados: uno exacto (409) y uno parecido (acentos y una letra): revisión y descarte por el admin.
  3. Plan del día con los 7 y observador abierto sobre el calendario del laboratorio.
  4. Revocación EN EL PLAN: el de mayor prioridad revoca; la fecha nueva va al siguiente por la regla.
  5. Fecha -> operación -> datos manuales -> día -> CONFIRMACIÓN HUMANA DE LABORATORIO (SIMULADA: la
     «persona» es este script) -> folio comprobado en el portal.
  6. Revocación con el DÍA ELEGIDO: el intento se descarta, confirmar da 409 y el portal no recibe nada.
  7. Revocación con la CITA CONFIRMADA: aviso a una persona; la cita NO se cancela sola.
  8. Revocación con la fecha ASIGNADA (sin preparar): se retira del plan y se cierra.
  9. Ventanilla con navegador real: consentimiento en el alta, en su paso propio y revocación.
 10. Motor matado y reiniciado: nada revocado se reabre; cadenas íntegras.
 11. (Opcional, --pg-parar/--pg-arrancar) PostgreSQL detenido y vuelto a arrancar.
 12. Cadenas de sellos, autodiagnóstico, trazabilidad sin datos personales y registros sin datos personales.

Uso: python scripts/demo_v22.py --salida docs/resultados/v2.2 [--bd URL --pg-parar CMD --pg-arrancar CMD]
Salida: <salida>/demo_v22_<bd>.json (código 0 si todas las comprobaciones pasan).
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from portal_reservas.proceso import PortalProceso  # noqa: E402
from verificar_arranque import Checks, Server, base_env  # noqa: E402

from monitoreo import navegador as ruta_navegador  # noqa: E402
from monitoreo.texto_seguro import RespuestaInesperada, sanitizar  # noqa: E402
from monitoreo.texto_seguro import cuerpo_esperado as datos  # noqa: E402

PWD = "Demo-Consentimiento-2026"
OP_PWD = "Operador-Consentimiento-2026"
ZONA = "America/Mexico_City"
MANUALES = {"clave_identidad": "QXZ482915", "acepto_privacidad": "si"}     # ficticios
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ULTIMO_FALLO: list[str] = []


def cons(n: int, **kw) -> dict:
    d = {"medio": "FIRMA_PAPEL", "aviso_privacidad": "AP-2026-01", "evidencia_ref": f"Carpeta LAB-{n:02d}",
         "evidencia_sha256": hashlib.sha256(f"formato ficticio {n}".encode()).hexdigest()}
    d.update(kw)
    return d


def login(base: str, u: str, p: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30, trust_env=False)
    c.headers["Authorization"] = "Bearer " + datos(c.post("/api/auth/login", json={"username": u, "password": p}), "token")
    return c


def esperar(pred, t=30.0, paso=0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < t:
        try:
            v = pred()
        except RespuestaInesperada as e:
            ULTIMO_FALLO[:] = [str(e)]
            v = None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, StopIteration):
            v = None
        if v:
            return v
        time.sleep(paso)
    return None


def run(a) -> dict:
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="demo22-"))
    ck = Checks()
    t_inicio = time.time()
    portal = PortalProceso(tmp / "portal.json", token=secrets.token_urlsafe(16), token_control=secrets.token_urlsafe(16),
                           log=out / "portal_v22.log", extra=["--zona-horaria", ZONA])
    portal.iniciar()
    env = base_env(tmp, "desarrollo")
    env.update({"MONITOREO_DATOS_DEMO": "0", "MONITOREO_ADMIN_PASSWORD": PWD, "MONITOREO_PORTAL_URL": portal.base,
                "MONITOREO_PORTAL_TOKEN": portal.token, "MONITOREO_ZONA_HORARIA": ZONA,
                "MONITOREO_EXIGIR_CONSENTIMIENTO": "1"})
    if a.bd:
        env["MONITOREO_DB_URL"] = a.bd
    motor = Server("motor-v22", env, out)
    motor.start()
    personales: list[str] = []
    resumen: dict = {}
    try:
        ck("motor y portal arrancan como procesos independientes", True, f"{motor.wait_ready()} s")
        c = login(motor.base, "admin", PWD)
        c.put("/api/settings/engine", json={"captura_modo": "POR_DIA"}).raise_for_status()
        cat = datos(c.get("/api/consentimientos/catalogos"))
        ck("la exigencia de consentimiento está ACTIVA", cat["exigido"] is True, cat["exigido"])
        r = c.post("/api/users", json={"username": "operador1", "password": OP_PWD, "role": "operador", "regions": ["NORTE"],
                                       "sucursales": ["NOR-1", "NOR-2"], "operator_name": "Operador Consentimiento"})
        ck("operador con usuario", r.status_code == 201, r.status_code)
        op = login(motor.base, "operador1", OP_PWD)
        ctl = portal.ctl
        dias = ctl("GET", "/control/dias")
        hoy = date.fromisoformat(dias["hoy"])
        ocupadas = {x["fecha"] for x in dias["dias"].values() if x["sucursal"] == "NOR-1" and x["tramite"] == "alta"}
        m0, mes = hoy + timedelta(days=20), None
        for k in range(8):
            y, m = m0.year + (m0.month - 1 + k) // 12, (m0.month - 1 + k) % 12 + 1
            if not any(f.startswith(f"{y:04d}-{m:02d}") for f in ocupadas):
                mes = (y, m)
                break
        y, m = mes
        libres = [date(y, m, d).isoformat() for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        candidatas = [f for f in libres if date.fromisoformat(f) > hoy]

        # ---------------------------------------------------------------- 1. registro
        regs, n = [], 60
        while len(regs) < 11 and n < 900:
            reg = portal.registro(n)
            if reg["tramite"] == "alta":
                regs.append(reg)
            n += 1

        def ficha(i: int, reg: dict, **kw) -> dict:
            d = {"nombre": f"Persona Consentimiento {LETRAS[i]}{LETRAS[(i * 7) % 26]}", "fecha_nacimiento": reg["fecha_nacimiento"],
                 "telefono": f"55500052{i:02d}", "telefono_confirmacion": f"55500052{i:02d}",
                 "correo": f"consent{i}@ejemplo.test", "correo_confirmacion": f"consent{i}@ejemplo.test",
                 "region": "NORTE", "sucursal": "NOR-1", "sucursales_alternativas": ["NOR-2"], "tipo_tramite": "alta",
                 "id_registro_previo": reg["identificador"], "prioridad": min(i + 1, 5)}
            d.update(kw)
            personales.extend([d["nombre"], d["telefono"], d["correo"]])
            return d
        exps = []
        for i in range(6):
            r = c.post("/api/expedientes", json={**ficha(i, regs[i]), "consentimiento": cons(i)})
            exps.append(r.json() if r.status_code == 201 else {"estado": f"HTTP {r.status_code}"})
        ck("6 expedientes con consentimiento quedan LISTOS", [e.get("estado") for e in exps] == ["LISTO"] * 6,
           [e.get("estado") for e in exps])
        r = c.post("/api/expedientes", json=ficha(6, regs[6]))
        sin = r.json()
        ck("sin consentimiento: INCOMPLETO (nunca entra a la cola)", sin.get("estado") == "INCOMPLETO", sin.get("estado"))
        antes = len(datos(c.get("/api/expedientes", params={"limit": 2000})))
        r = c.post("/api/expedientes", json={**ficha(7, regs[7]), "consentimiento": cons(7, evidencia_sha256="abc")})
        despues = len(datos(c.get("/api/expedientes", params={"limit": 2000})))
        ck("consentimiento inválido: 422 y el expediente NO se crea", r.status_code == 422 and antes == despues,
           {"http": r.status_code, "antes": antes, "despues": despues})

        # ---------------------------------------------------------------- 2. duplicados
        r = c.post("/api/expedientes", json={**ficha(0, regs[0]), "consentimiento": cons(20)})
        ck("duplicado EXACTO: 409", r.status_code == 409, r.status_code)
        nombre0 = f"Persona Consentimiento {LETRAS[0]}{LETRAS[0]}"
        parecido = nombre0.replace("Consentimiento", "Consentimeinto")
        r = c.post("/api/expedientes", json={**ficha(8, regs[8], nombre=parecido, fecha_nacimiento=regs[0]["fecha_nacimiento"]),
                                             "consentimiento": cons(8)})
        dup = r.json()
        personales.append(parecido)
        ck("duplicado PARECIDO (letras cambiadas, mismo nacimiento): REQUIERE_REVISION",
           dup.get("estado") == "REQUIERE_REVISION" and exps[0]["codigo"] in (dup.get("ultimo_error") or ""), dup.get("ultimo_error"))
        r = op.post(f"/api/expedientes/{dup['id']}/duplicados/descartar", json={"otro": exps[0]["codigo"],
                                                                                "motivo": "Formatos distintos revisados"})
        ck("el operador NO puede descartar un posible duplicado", r.status_code == 403, r.status_code)
        r = c.post(f"/api/expedientes/{dup['id']}/duplicados/descartar", json={"otro": exps[0]["codigo"],
                                                                               "motivo": "Formatos distintos revisados"})
        ck("el admin lo descarta con motivo y el expediente sigue (LISTO)", r.status_code == 200 and r.json()["estado"] == "LISTO",
           r.text[:200])

        # ---------------------------------------------------------------- 3. plan del día + observador
        jornada = datetime.now(__import__("zoneinfo").ZoneInfo(ZONA)).date().isoformat()
        pid = datos(c.post("/api/planes", json={"fecha_jornada": jornada, "capacidad": 7}), "id")
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [
            {"expediente_id": e["id"], "fechas_desde": libres[0], "fechas_hasta": libres[-1]} for e in exps]}).json()
        ck("plan del día: entran los 6 con consentimiento", len(r["aceptados"]) == 6, r.get("rechazados"))
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [{"expediente_id": sin["id"]}]}).json()
        ck("el plan rechaza al que no tiene consentimiento, con su motivo", not r["aceptados"], r.get("rechazados"))
        r = c.post(f"/api/expedientes/{sin['id']}/consentimientos", json=cons(6))
        ck("al registrar su consentimiento pasa a LISTO", r.status_code == 201 and r.json()["expediente"]["estado"] == "LISTO",
           r.text[:200])
        r = c.post(f"/api/planes/{pid}/elementos", json={"elementos": [
            {"expediente_id": sin["id"], "fechas_desde": libres[0], "fechas_hasta": libres[-1]}]}).json()
        ck("y ahora sí entra al plan", len(r["aceptados"]) == 1, r.get("rechazados"))
        exps.append(sin)
        portal.ctl("POST", "/control/vista", refresco_ms=500)
        r = c.post("/api/observador", json={"sucursal": "NOR-1", "tramite": "alta", "mes": f"{y:04d}-{m:02d}",
                                            "estrategia": "dom"})
        activo = esperar(lambda: (datos(c.get("/api/observador"), "observadores")[0]["vista"] or {}).get("dom") == "ACTIVA", 60)
        ck("observador (Chromium) abierto y ACTIVO sobre el calendario del laboratorio", bool(activo) and r.status_code == 200,
           r.text[:120])

        def elems():
            return {x["expediente_id"]: x for x in datos(c.get(f"/api/planes/{pid}"), "elementos")}

        def revocar(e, quien=c, medio="ESCRITO"):
            cid = datos(quien.get(f"/api/expedientes/{e['id']}/consentimientos"), "consentimientos")[-1]["id"]
            r = quien.post(f"/api/consentimientos/{cid}/revocar", json={"medio": medio, "motivo": "PERSONA_LO_PIDE"})
            return r.status_code, (r.json() if r.status_code == 200 else r.text[:200])

        def exp(e):
            return datos(c.get(f"/api/expedientes/{e['id']}"))

        def ronda(fecha):
            ctl("POST", "/control/dias/habilitar", sucursal="NOR-1", tramite="alta", fecha=fecha, capacidad=1)
            return esperar(lambda: next((x for x in elems().values() if x["fecha_asignada"] == fecha
                                         and x["estado"] in ("ASIGNADO", "OPERACION_PREPARADA")), None), 30, 0.3)

        # ---------------------------------------------------------------- 4. revocación en el plan
        st, rv = revocar(exps[0])
        ck("revocación con el expediente EN ESPERA del plan", st == 200 and rv["consentimiento"]["estado"] == "REVOCADO", rv)
        ck("se retira del plan (RETIRADO) y el expediente se cierra",
           elems()[exps[0]["id"]]["estado"] == "RETIRADO" and exp(exps[0])["estado"] == "CERRADO",
           {"plan": elems()[exps[0]["id"]]["estado"], "exp": exp(exps[0])["estado"]})
        el1 = ronda(candidatas[0])
        ck("la fecha nueva va al SIGUIENTE por prioridad, nunca al revocado",
           el1 and el1["expediente_id"] == exps[1]["id"], el1 and el1["codigo"])

        # ---------------------------------------------------------------- 5. operación hasta folio
        t = time.perf_counter()
        r = op.post(f"/api/planificador/elementos/{el1['id']}/preparar")
        rid1 = datos(r, "reservacion_id")
        op.post(f"/api/reservaciones/{rid1}/enviar-datos", json={"revisado": True, "manuales": MANUALES, "sucursal": "NOR-1"}).raise_for_status()
        op.post(f"/api/reservaciones/{rid1}/seleccionar-dia", json={"fecha": candidatas[0], "sucursal": "NOR-1"}).raise_for_status()
        r = op.post(f"/api/reservaciones/{rid1}/confirmar", json={"confirmo": True})     # SIMULADA: la «persona» es el script
        folio = r.json().get("reservacion", {}).get("folio") if r.status_code == 200 else None
        resumen["operacion_hasta_folio_ms"] = round((time.perf_counter() - t) * 1000, 1)
        rp = httpx.get(portal.base + f"/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {portal.token}"}, trust_env=False)
        ck("confirmación humana de laboratorio (SIMULADA) -> folio CONFIRMADO en el portal",
           bool(folio) and rp.status_code == 200 and rp.json().get("estado") == "CONFIRMADA", r.text[:160])

        # ---------------------------------------------------------------- 6. revocación con el día elegido
        el2 = ronda(candidatas[2])
        ck("segunda fecha asignada al siguiente (prioridad 3)", el2 and el2["expediente_id"] == exps[2]["id"], el2 and el2["codigo"])
        rid2 = datos(op.post(f"/api/planificador/elementos/{el2['id']}/preparar"), "reservacion_id")
        op.post(f"/api/reservaciones/{rid2}/enviar-datos", json={"revisado": True, "manuales": MANUALES, "sucursal": "NOR-1"}).raise_for_status()
        op.post(f"/api/reservaciones/{rid2}/seleccionar-dia", json={"fecha": candidatas[2], "sucursal": "NOR-1"}).raise_for_status()
        conf_antes = sum(x.get("estado") == "CONFIRMADA" for x in ctl("GET", "/control/reservaciones")["reservaciones"])
        st, rv = revocar(exps[2], op, "PRESENCIAL")
        ck("revocación con el DÍA ELEGIDO: intento descartado y expediente cerrado",
           st == 200 and rv["efectos"]["intento_descartado"] and rv["efectos"]["expediente_cerrado"], rv if st != 200 else rv["efectos"])
        r = op.post(f"/api/reservaciones/{rid2}/confirmar", json={"confirmo": True})
        conf_despues = sum(x.get("estado") == "CONFIRMADA" for x in ctl("GET", "/control/reservaciones")["reservaciones"])
        ck("confirmar después de revocar: 409 y el portal no recibe ninguna confirmación",
           r.status_code == 409 and conf_antes == conf_despues, {"http": r.status_code, "antes": conf_antes, "despues": conf_despues})

        # ---------------------------------------------------------------- 7. revocación con cita confirmada
        st, rv = revocar(exps[1], op)
        rp = httpx.get(portal.base + f"/api/reservaciones/{folio}", headers={"Authorization": f"Bearer {portal.token}"}, trust_env=False)
        alertas = datos(c.get("/api/alerts", params={"limit": 100}))
        alertas = alertas.get("items", alertas) if isinstance(alertas, dict) else alertas
        ck("revocación con CITA CONFIRMADA: aviso a una persona y la cita sigue en el portal (no se cancela sola)",
           st == 200 and rv["efectos"]["aviso_cita_confirmada"] and rp.json().get("estado") == "CONFIRMADA"
           and any("consentimiento revocado con cita" in (x.get("title") or "") for x in alertas),
           rv["efectos"] if st == 200 else rv)

        conf_plan = esperar(lambda: elems()[exps[1]["id"]]["estado"] == "CONFIRMADO", 30, 0.5)
        ck("el plan sigue contando esa cita como CONFIRMADA (no la retira)", bool(conf_plan), elems()[exps[1]["id"]]["estado"])

        # ---------------------------------------------------------------- 8. revocación con fecha asignada
        # (la fecha del intento descartado en el paso 6 puede reofrecerse a otro: por eso se calcula a quién toca)
        espera = sorted((x["prioridad"], x["orden"], x["expediente_id"]) for x in elems().values() if x["estado"] == "EN_ESPERA")
        esperado = espera[0][2] if espera else None
        el3 = ronda(candidatas[4])
        objetivo = next((e for e in exps if el3 and e["id"] == el3["expediente_id"]), None)
        st, rv = revocar(objetivo) if objetivo else (None, None)
        e3 = exp(objetivo) if objetivo else {}
        ck("revocación con la fecha ASIGNADA (sin preparar): retirado del plan, solicitud cancelada, cerrado",
           el3 and el3["expediente_id"] == esperado and st == 200 and elems()[objetivo["id"]]["estado"] == "RETIRADO"
           and e3.get("estado") == "CERRADO", {"asignado_a": el3 and el3["codigo"], "esperado": esperado,
                                               "plan": objetivo and elems()[objetivo["id"]]["estado"], "exp": e3.get("estado")})
        r = op.post(f"/api/planificador/elementos/{el3['id']}/preparar") if el3 else None
        ck("ya no se puede preparar la operación del revocado", r is not None and r.status_code in (404, 409), r and r.status_code)
        resumen["tras_revocar_asignado"] = {"fecha": candidatas[4], "reasignada_a": next(
            (x["codigo"] for x in elems().values() if x["fecha_asignada"] == candidatas[4] and objetivo
             and x["expediente_id"] != objetivo["id"]),
            None), "nota": "OBSERVADO: si la unidad liberada se reofrece depende de que el observador vuelva a verla"}

        # ---------------------------------------------------------------- 9. Ventanilla con navegador real
        pers = []
        for j, reg in enumerate(regs[9:11]):
            p = {"nombre": f"Persona Ventanilla {LETRAS[j]}{LETRAS[j + 3]}", "fecha_nacimiento": reg["fecha_nacimiento"],
                 "telefono": f"55500063{j:02d}", "correo": f"ventanilla{j}@ejemplo.test", "registro": reg["identificador"],
                 "tramite": "alta"}
            pers.append(p)
            personales.extend([p["nombre"], p["telefono"], p["correo"]])
        pj = tmp / "personas.json"
        pj.write_text(json.dumps(pers, ensure_ascii=False), encoding="utf-8")
        if shutil.which("node"):
            pr = subprocess.run(["node", str(ROOT / "scripts" / "demo_v22_ventanilla.js"), motor.base, "operador1", OP_PWD,
                                 str(out / "capturas"), str(pj)], capture_output=True, encoding="utf-8", errors="replace",
                                timeout=600, env=ruta_navegador.entorno())
            lin = [x for x in pr.stdout.splitlines() if x.startswith("RESULTADO ")]
            v = json.loads(lin[-1][10:]) if lin else {"error": sanitizar(pr.stderr, 300)}
            pa = {x["caso"]: x for x in v.get("pasos", [])}
            ck("Ventanilla A: alta CON consentimiento (huella calculada en el navegador) -> «Preparar»",
               pa.get("A", {}).get("siguiente") == "preparar" and v.get("huella_coincide") is True, pa.get("A") or v.get("error"))
            ck("Ventanilla B: alta SIN consentimiento -> se detiene en «Consentimiento»; registrado -> «Preparar»",
               pa.get("B", {}).get("siguiente_antes") == "consentimiento" and pa["B"]["paso_consentimiento"]
               and not pa["B"]["flujo_antes"] and pa["B"]["siguiente_despues"] == "preparar", pa.get("B") or v.get("error"))
            ck("Ventanilla C: revocación desde la atención (pide confirmar) -> atención terminada sin cita",
               pa.get("C", {}).get("aviso_sin_casilla") and pa["C"]["estado_atencion"] == "CANCELADA", pa.get("C") or v.get("error"))
            ck("Ventanilla: sin errores de JavaScript", not v.get("errores_js"), v.get("errores_js"))
            n_cons_b = len(datos(c.get(f"/api/expedientes/{_exp_de_atencion(c, pa.get('B', {}).get('atencion'))}/consentimientos"),
                                 "consentimientos")) if pa.get("B") else None
            ck("Ventanilla: el doble clic registra UN solo consentimiento", n_cons_b == 1, n_cons_b)
            resumen["ventanilla"] = v
        else:
            ck("Ventanilla con navegador (Node no disponible)", False, "falta Node.js")

        # ---------------------------------------------------------------- 10. reinicio del motor
        revocados = [exps[0], exps[2]] + ([objetivo] if objetivo else [])
        cerrados = {e["codigo"]: exp(e)["estado"] for e in revocados}
        ver_antes = datos(c.get("/api/consentimientos/verificar"))
        motor.proc.kill()
        motor.proc.wait(20)
        motor.log.close()
        motor.start()
        resumen["reinicio_s"] = motor.wait_ready()
        c = login(motor.base, "admin", PWD)
        time.sleep(4)                                    # al menos un ciclo de mantenimiento
        ver = datos(c.get("/api/consentimientos/verificar"))
        ck("tras matar y reiniciar el motor: nada revocado se reabre y las cadenas siguen íntegras",
           {k: datos(c.get(f"/api/expedientes/{e['id']}"))["estado"] for k, e in
            zip(cerrados, revocados)} == cerrados and ver["ok"] and ver["eventos"] == ver_antes["eventos"],
           {"verificacion": ver, "antes": ver_antes["eventos"]})

        # ---------------------------------------------------------------- 11. PostgreSQL detenido
        if a.pg_parar and a.pg_arrancar:
            subprocess.run(a.pg_parar, shell=True, check=False, timeout=60)
            time.sleep(2)
            salud = httpx.get(motor.base + "/api/health", timeout=10, trust_env=False).status_code
            cid = None
            try:
                rr = c.get(f"/api/expedientes/{exps[5]['id']}/consentimientos")
                durante = rr.status_code
            except httpx.HTTPError as e:
                durante = type(e).__name__
            subprocess.run(a.pg_arrancar, shell=True, check=False, timeout=60)
            ok = esperar(lambda: httpx.get(motor.base + "/api/health", timeout=5, trust_env=False).status_code == 200, 60, 0.5)
            vivo = next(e for e in exps if e not in revocados and e is not exps[1])
            st, rv = revocar(vivo)
            ck("PostgreSQL detenido: el motor responde 503 y no inventa nada", salud == 503 and durante != 200,
               {"salud": salud, "consulta": durante})
            ck("al volver PostgreSQL, se revoca con normalidad y la cadena cuadra",
               bool(ok) and st == 200 and datos(c.get("/api/consentimientos/verificar"))["ok"], rv if st != 200 else rv["efectos"])
            resumen["pg"] = {"salud_durante": salud, "consulta_durante": durante, "cid": cid}

        # ---------------------------------------------------------------- 12. verificación final
        ver = datos(c.get("/api/consentimientos/verificar"))
        ck("cadenas de sellos de TODOS los consentimientos íntegras", ver["ok"], ver)
        diag = datos(c.get("/api/autodiagnostico"))
        cons_items = [x for x in diag["comprobaciones"] if x["area"] == "Consentimiento"]
        ck("el autodiagnóstico revisa los consentimientos y no ve problemas",
           cons_items and all(x["estado"] == "OK" for x in cons_items), cons_items)
        traz = datos(c.get(f"/api/expedientes/{exps[1]['id']}/trazabilidad"))
        (out / "trazabilidad_ejemplo.json").write_text(json.dumps(traz, ensure_ascii=False, indent=2), encoding="utf-8")
        tipos = [x["tipo"] for x in traz["linea_de_tiempo"]]
        blob = json.dumps(traz, ensure_ascii=False)
        ck("trazabilidad de punta a punta (alta, consentimiento, plan, folio, revocación, aviso) y sin datos personales",
           all(t in tipos for t in ("CREADO", "CONSENTIMIENTO_OTORGADO", "RESERVACION_CONFIRMADA", "CONSENTIMIENTO_REVOCADO",
                                    "CONSENTIMIENTO_AVISO_CITA_CONFIRMADA")) and traz["planes"]
           and not [p for p in personales if p in blob], [t for t in tipos][:40])
        resumen["verificacion"] = ver
        c.delete("/api/observador")
    except RespuestaInesperada as e:
        ck("la demostración se detuvo: respuesta inesperada del motor", False, str(e))
    except Exception as e:  # noqa: BLE001 - el informe se escribe igual, con el motivo saneado
        donde = traceback.extract_tb(e.__traceback__)[-1]
        ck("la demostración se detuvo por un error", False, f"{type(e).__name__} en {Path(donde.filename).name}:{donde.lineno}: "
           f"{sanitizar(str(e), 240)}" + (f" (última espera fallida: {ULTIMO_FALLO[0]})" if ULTIMO_FALLO else ""))
    finally:
        try:
            motor.stop()
        except Exception:  # noqa: BLE001
            pass
        portal.detener()
    textos = "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.glob("servidor-motor-v22*.log"))
    fugas = [p for p in personales if p in textos]
    ck("sin datos personales en los registros del motor", not fugas, len(fugas))
    return {"version": "2.2.0-dev", "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "plataforma": platform.platform(),
            "python": platform.python_version(), "duracion_s": round(time.time() - t_inicio, 1),
            "base_de_datos": "postgresql" if a.bd else "sqlite", "resumen": resumen, "ok": ck.ok, "comprobaciones": ck.items,
            "nota": "Portal de LABORATORIO propio y datos FICTICIOS. La confirmación «humana» la hace este script: "
                    "SIMULADA. Nada aquí demuestra el comportamiento de un portal externo."}


def _exp_de_atencion(c, aid):
    if not aid:
        return None
    return (datos(c.get(f"/api/ventanilla/atenciones/{aid}")).get("expediente") or {}).get("id")


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="resultados/demo_v22")
    ap.add_argument("--bd", default=None)
    ap.add_argument("--pg-parar", default=None)
    ap.add_argument("--pg-arrancar", default=None)
    a = ap.parse_args()
    rep = run(a)
    nombre = "demo_v22" + ("_postgresql" if a.bd else "_sqlite") + ".json"
    Path(a.salida, nombre).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in rep["comprobaciones"]:
        print(("OK   " if it["ok"] else "FALLA") + " " + it["comprobacion"] + (f" — {it['detalle']}" if it.get("detalle") else ""))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

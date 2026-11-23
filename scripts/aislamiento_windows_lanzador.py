"""Prueba de aislamiento de extremo a extremo en Windows CON EL LANZADOR DE MONITOR (no con Start-Process).

Qué hace, en un equipo de pruebas EFÍMERO (runner de CI) y con datos FICTICIOS:
1. Crea una cuenta local de un solo uso (no administradora) con clave aleatoria.
2. Crea un árbol que imita una instalación de Monitor: data\\monitoreo.db (SQLite ficticia), .env, respaldos\\,
   diario\\clave_diario.key, y conector\\codigo (solo lectura) + conector\\trabajo (escritura).
3. Aplica las ACL NTFS de la especificación (sin herencia; denegación explícita a data, respaldos, diario, .env).
4. (C8) Regla de Firewall de salida para esa cuenta: solo loopback.
5. Lanza la sonda COMO esa cuenta con `monitoreo.autorrelleno.lanzador_restringido` (CreateProcessWithLogonW +
   Job Object) y comprueba A1-A9, el barrido, la identidad y el eco de stdin.
6. A10: cancelación, plazo, padre que deja un hijo, y Monitor que muere de golpe: no debe quedar ningún proceso
   de la cuenta.
7. A11 (opcional, --forms-dir): lanza el Forms real como esa cuenta y comprueba que responde por stdout.
8. Borra la cuenta, su perfil, la regla de Firewall y el árbol.

NO toca ninguna instalación real. Se niega a ejecutarse si no es Windows o no es administrador, y si la variable
CAUCE_AISLAMIENTO_EFIMERO no vale 1 (para que nadie lo lance por accidente en la MSI).
Uso: python scripts/aislamiento_windows_lanzador.py --salida aislamiento_lanzador.json [--forms-dir DIR]
Veredicto: AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO solo dice que la técnica funciona en ESE equipo.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_REPO))
CUENTA = "cauce_conector_t"


def sh(args: list[str], comprobar: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if comprobar and r.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} falló ({r.returncode})")
    return r


def ps(script: str, comprobar: bool = True) -> str:
    return sh(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], comprobar).stdout.strip()


def es_admin() -> bool:
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def construir_arbol(raiz: Path) -> None:
    for d in ("data", "respaldos", "diario", "conector/codigo", "conector/trabajo/tmp"):
        (raiz / d).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(raiz / "data" / "monitoreo.db")
    con.execute("create table expedientes (curp text, nombre text)")
    con.execute("insert into expedientes values ('XEXX010101HNEXXXA4', 'PERSONA FICTICIA zzsecreto')")
    con.commit()
    con.close()
    shutil.copy(raiz / "data" / "monitoreo.db", raiz / "respaldos" / "copia.db")
    (raiz / ".env").write_text("MONITOREO_SECRET_KEY=zzsecreto-ficticio\n")
    (raiz / "diario" / "clave_diario.key").write_bytes(b"clave ficticia zzsecreto")
    (raiz / "conector" / "codigo" / "leeme.txt").write_text("código del conector (solo lectura)")
    shutil.copy(RAIZ_REPO / "scripts" / "aislamiento_sonda.py", raiz / "conector" / "codigo" / "aislamiento_sonda.py")


def aplicar_acl(raiz: Path, cuenta: str, yo: str) -> None:
    """Las mismas ACL que se propondrán para la instalación real (docs/PLAN_AISLAMIENTO_FORMS_WINDOWS.md)."""
    r = str(raiz)
    sh(["icacls", r, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", f"{yo}:(OI)(CI)F"])
    sh(["icacls", r, "/grant", f"{cuenta}:(RX)"])                                  # solo esta carpeta: atravesar
    sh(["icacls", str(raiz / "conector"), "/grant", f"{cuenta}:(RX)"])
    sh(["icacls", str(raiz / "conector" / "codigo"), "/grant", f"{cuenta}:(OI)(CI)RX"])
    sh(["icacls", str(raiz / "conector" / "trabajo"), "/grant", f"{cuenta}:(OI)(CI)M"])
    for p in ("data", "respaldos", "diario"):
        sh(["icacls", str(raiz / p), "/deny", f"{cuenta}:(OI)(CI)F"])
    sh(["icacls", str(raiz / ".env"), "/deny", f"{cuenta}:F"])


def sid_de(cuenta: str) -> str:
    return ps(f"(New-Object System.Security.Principal.NTAccount('{cuenta}'))"
              ".Translate([System.Security.Principal.SecurityIdentifier]).Value")


def firewall_solo_loopback(sid: str, nombre: str) -> dict:
    activo = ps("(Get-NetFirewallProfile | Where-Object { $_.Enabled -eq 'True' }).Count", comprobar=False)
    rangos = "'0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'"
    ps(f"New-NetFirewallRule -DisplayName '{nombre}' -Direction Outbound -Action Block "
       f"-LocalUser 'D:(A;;CC;;;{sid})' -RemoteAddress {rangos} | Out-Null")
    return {"perfiles_activos": activo, "regla": nombre}


def procesos_de(cuenta: str) -> list[dict]:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "username"]):
        u = (p.info.get("username") or "")
        if u.lower().endswith("\\" + cuenta.lower()):
            out.append({"pid": p.info["pid"], "nombre": p.info["name"]})
    return out


def esperar_sin_procesos(cuenta: str, s: float = 5.0) -> list[dict]:
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        if not procesos_de(cuenta):
            return []
        time.sleep(0.2)
    return procesos_de(cuenta)


def entorno_hijo(raiz: Path) -> dict:
    tmp = str(raiz / "conector" / "trabajo" / "tmp")
    py_dir = str(Path(sys.executable).parent)
    system32 = str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32")
    sistema = {k: os.environ[k] for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT") if k in os.environ}
    return {**sistema, "PATH": f"{py_dir};{system32}",
            "TEMP": tmp, "TMP": tmp, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def hijo_monitor(cuenta: str, raiz: str) -> int:
    """Proceso que hace de «Monitor»: lanza un árbol y espera a que lo maten de golpe (clave por el entorno)."""
    clave = os.environ.pop("CAUCE_AISLAMIENTO_CLAVE")
    from monitoreo.autorrelleno.lanzador_restringido import LanzadorRestringido
    lz = LanzadorRestringido(cuenta=cuenta, clave=clave, modo_laboratorio=True)
    sonda = str(Path(raiz) / "conector" / "codigo" / "aislamiento_sonda.py")
    lz.lanzar([sys.executable, sonda, "arbol"], cwd=str(Path(raiz) / "conector" / "trabajo"),
              env=entorno_hijo(Path(raiz)), plazo_s=600)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="aislamiento_lanzador.json")
    ap.add_argument("--forms-dir", default="")
    ap.add_argument("--hijo-monitor", nargs=2, metavar=("CUENTA", "RAIZ"))
    a = ap.parse_args()
    if a.hijo_monitor:
        return hijo_monitor(*a.hijo_monitor)
    if sys.platform != "win32":
        print("Solo Windows.", file=sys.stderr)
        return 2
    if os.environ.get("CAUCE_AISLAMIENTO_EFIMERO") != "1":
        print("Se niega: crea y borra una cuenta local. Solo en un equipo efímero con CAUCE_AISLAMIENTO_EFIMERO=1.",
              file=sys.stderr)
        return 2
    if not es_admin():
        print("Requiere administrador.", file=sys.stderr)
        return 2
    from monitoreo.autorrelleno.lanzador_restringido import LanzadorRestringido

    raiz = Path(tempfile.gettempdir()) / f"aisl_{secrets.token_hex(4)}"
    clave = "Aa1!" + secrets.token_urlsafe(24)
    yo = f"{os.environ.get('USERDOMAIN')}\\{os.environ.get('USERNAME')}"
    regla = f"cauce-aislamiento-{secrets.token_hex(3)}"
    res: dict = {"cuenta": CUENTA, "python": sys.executable, "fallos": [], "veredicto": "NO_EJECUTADO"}
    sid = ""
    servidor = socket.socket()
    try:
        construir_arbol(raiz)
        if a.forms_dir:      # Forms vive DENTRO de conector\codigo (solo lectura para la cuenta), como en el plan
            shutil.copytree(a.forms_dir, raiz / "conector" / "codigo" / "forms", ignore=shutil.ignore_patterns(".git"))
        sh(["net", "user", CUENTA, clave, "/add", "/expires:never", "/y"])
        sid = sid_de(CUENTA)
        res["grupos_de_la_cuenta"] = sh(["net", "user", CUENTA], comprobar=False).stdout.splitlines()[-4:]
        aplicar_acl(raiz, CUENTA, yo)
        res["firewall"] = firewall_solo_loopback(sid, regla)
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(8)
        puerto = servidor.getsockname()[1]
        def aceptar():
            try:
                for _ in range(8):
                    servidor.accept()[0].close()
            except OSError:          # el socket se cierra al terminar la prueba
                pass
        threading.Thread(target=aceptar, daemon=True).start()
        env = entorno_hijo(raiz)
        cwd = str(raiz / "conector" / "trabajo")
        sonda = str(raiz / "conector" / "codigo" / "aislamiento_sonda.py")

        def lz():
            return LanzadorRestringido(cuenta=CUENTA, clave=clave, modo_laboratorio=True)

        # ---- A1-A9, barrido, identidad, stdin
        entrada = '{"solicitud": "ficticia", "nombre": "PERSONA FICTICIA"}'
        r = lz().lanzar([sys.executable, sonda, "pruebas", str(raiz), str(puerto)], entrada=entrada, cwd=cwd, env=env,
                        plazo_s=90)
        res["sonda_estado"] = {"estado": r.estado, "codigo": r.codigo_salida, "stderr": r.stderr[-2000:]}
        s = json.loads(r.stdout) if r.stdout.strip() else None
        res["sonda"] = s
        if not s:
            res["fallos"].append("la sonda no produjo salida")
        else:
            res["fallos"] += [f"{p['prueba']}: esperado {p['esperado']}, observado {p['observado']}"
                              for p in s["pruebas"] if not p["ok"]]
            if s["usuario"].lower() != CUENTA.lower():
                res["fallos"].append(f"A8 la sonda no corrió como la cuenta restringida (usuario={s['usuario']})")
            if s["barrido"]["legibles_fuera_de_conector"]:
                res["fallos"].append(f"barrido: legible fuera de conector/: {s['barrido']['legibles_fuera_de_conector']}")
            import hashlib
            if s["stdin_sha256"] != hashlib.sha256(entrada.encode()).hexdigest():
                res["fallos"].append("stdin no llegó íntegro a la sonda")

        # ---- A10: cancelación, plazo, desertor, Monitor muere de golpe
        a10 = {}
        l1 = lz()
        threading.Timer(3.0, l1.cancelar).start()
        r = l1.lanzar([sys.executable, sonda, "arbol"], cwd=cwd, env=env, plazo_s=60)
        a10["cancelacion"] = {"estado": r.estado, "eliminados": r.eliminados, "restos": r.sobrevivientes,
                              "procesos_de_la_cuenta": esperar_sin_procesos(CUENTA)}
        r = lz().lanzar([sys.executable, sonda, "dormir"], cwd=cwd, env=env, plazo_s=3)
        a10["plazo"] = {"estado": r.estado, "restos": r.sobrevivientes, "procesos_de_la_cuenta": esperar_sin_procesos(CUENTA)}
        r = lz().lanzar([sys.executable, sonda, "desertor"], cwd=cwd, env=env, plazo_s=30)
        a10["desertor"] = {"estado": r.estado, "eliminados": r.eliminados, "restos": r.sobrevivientes,
                           "procesos_de_la_cuenta": esperar_sin_procesos(CUENTA)}
        m = subprocess.Popen([sys.executable, __file__, "--hijo-monitor", CUENTA, str(raiz)],
                             env={**os.environ, "CAUCE_AISLAMIENTO_CLAVE": clave})
        visto = []
        fin = time.monotonic() + 30
        while time.monotonic() < fin and len(visto) < 3:
            visto = procesos_de(CUENTA)
            time.sleep(0.3)
        m.kill()
        a10["monitor_muere"] = {"procesos_antes": len(visto), "procesos_de_la_cuenta": esperar_sin_procesos(CUENTA, 10)}
        res["A10"] = a10
        for k, v in a10.items():
            if v["procesos_de_la_cuenta"] or v.get("restos"):
                res["fallos"].append(f"A10 {k}: quedaron procesos de la cuenta: {v['procesos_de_la_cuenta'] or v['restos']}")
        if a10["cancelacion"]["estado"] != "CANCELADO" or a10["plazo"]["estado"] != "PLAZO":
            res["fallos"].append("A10: estados de cancelación/plazo inesperados")
        if a10["desertor"]["eliminados"] < 1:
            res["fallos"].append("A10 desertor: el hijo abandonado no se contó como eliminado")
        if a10["monitor_muere"]["procesos_antes"] < 1:
            res["fallos"].append("A10 monitor_muere: no se llegó a ver el árbol antes de matar a Monitor")

        # ---- A11 (opcional): el Forms real arranca como la cuenta y responde por stdout
        if a.forms_dir:
            fd = raiz / "conector" / "codigo" / "forms"
            py = fd / ".venv" / "Scripts" / "python.exe"
            envf = {**env, "PATH": f"{py.parent};{env['PATH']}", "CAUCE_HEADLESS": "1",
                    "LOCALAPPDATA": env["TEMP"], "APPDATA": env["TEMP"], "USERPROFILE": env["TEMP"]}
            if (fd / ".pw-browsers").is_dir():
                envf["PLAYWRIGHT_BROWSERS_PATH"] = str(fd / ".pw-browsers")
            imp = lz().lanzar([str(py), "-c", "import cauce_form_connector, playwright; print('IMPORT_OK')"], cwd=str(fd),
                              env=envf, plazo_s=120)
            r = lz().lanzar([str(py), "-m", "cauce_form_connector", "preparar"], entrada="{}", cwd=str(fd), env=envf,
                            plazo_s=120)
            try:
                resp = json.loads(r.stdout)
            except ValueError:
                resp = None
            res["A11"] = {"importa_forms_y_playwright": imp.stdout.strip() == "IMPORT_OK", "estado_proceso": r.estado,
                          "codigo": r.codigo_salida,
                          "respuesta_estado": (resp or {}).get("estado"), "stderr_final": r.stderr[-600:],
                          "procesos_de_la_cuenta": esperar_sin_procesos(CUENTA)}
            # Una solicitud vacía es inválida: Forms debe terminar por sí mismo con su código 6 («solicitud inválida»,
            # tabla del conector) o con una respuesta JSON; un fallo de Python (no puede leer su código o su intérprete)
            # da 1 con traza en stderr. El recorrido completo con el portal de laboratorio se hace en la fase F2.
            if not res["A11"]["importa_forms_y_playwright"]:
                res["fallos"].append("A11: Forms o Playwright no se pueden importar como la cuenta restringida")
            if r.estado != "TERMINO" or not (r.codigo_salida == 6 or (isinstance(resp, dict) and resp.get("estado"))) \
                    or "Traceback" in r.stderr:
                res["fallos"].append(f"A11: el Forms real no respondió como se esperaba (código {r.codigo_salida})")
        res["veredicto"] = "AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO" if not res["fallos"] else "FALLA"
    except Exception as e:  # noqa: BLE001
        res["fallos"].append(f"{type(e).__name__}: {e}")
        res["veredicto"] = "NO_EJECUTADO" if "sonda" not in res else "FALLA"
    finally:
        servidor.close()
        sh(["net", "user", CUENTA, "/delete"], comprobar=False)
        if sid:
            ps(f"Get-CimInstance Win32_UserProfile | Where-Object {{ $_.SID -eq '{sid}' }} | Remove-CimInstance",
               comprobar=False)
        ps(f"Remove-NetFirewallRule -DisplayName '{regla}' -ErrorAction SilentlyContinue", comprobar=False)
        sh(["icacls", str(raiz), "/reset", "/T", "/Q"], comprobar=False)
        shutil.rmtree(raiz, ignore_errors=True)
        res["limpieza"] = {"cuenta_borrada": sh(["net", "user", CUENTA], comprobar=False).returncode != 0,
                           "arbol_borrado": not raiz.exists()}
    texto = json.dumps(res, ensure_ascii=False, indent=2)
    Path(a.salida).write_text(texto, encoding="utf-8")
    print(texto)
    return 0 if res["veredicto"] == "AISLAMIENTO_DEMOSTRADO_EN_ESTE_ENTORNO" else 1


if __name__ == "__main__":
    sys.exit(main())

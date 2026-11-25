"""F3 · Rotación segura de la contraseña del administrador (modo normal) de una instalación Windows.

NUNCA imprime, registra ni recibe por la línea de comandos una contraseña. El valor anterior se lee de
MONITOREO_ADMIN_PASSWORD en el .env de la instalación solo para compararlo; el nuevo lo escribe el
administrador en el panel (Usuarios → «Cambiar mi contraseña») o, en emergencia, con getpass.

Uso (con el Python del venv de la instalación):
  python rotar_contrasena_admin.py --instalacion C:\\cauce-msi --salida DIR <orden> [opciones]

Órdenes:
  inventario            usuarios (sin hashes), ¿la contraseña del .env es la vigente?, archivos que la contienen
  instantanea ETIQUETA  integridad y huella por tabla de la base (Monitor detenido)
  comparar A B          tablas que cambiaron entre dos instantáneas
  prueba-emergencia     ensaya el restablecimiento de emergencia en una COPIA de la base (no toca la real)
  login ESPERADO        POST /api/auth/login con la contraseña del .env; ESPERADO = 200 o 401
  auditoria DESDE       eventos de inicio de sesión/cambio de contraseña del admin desde DESDE (UTC ISO)
  retirar --confirmo    tras verificar la rotación: quita la línea del .env y la copia en texto plano viva
  escanear --valor-desde ENV RUTA...  busca el valor de MONITOREO_ADMIN_PASSWORD de ese .env (p. ej. el de un
                        respaldo) en las rutas dadas; imprime solo rutas
  emergencia --confirmo restablece la contraseña del admin con getpass (Monitor detenido; copia previa)
Salida: JSON en DIR y una línea de resumen. Código 0 = OK, 1 = comprobación fallida, 2 = uso indebido.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLAVE = "MONITOREO_ADMIN_PASSWORD"
TXT = Path("data") / "contrasena_admin_modo_normal.txt"
TABLAS_PROTEGIDAS = ("expedientes", "expediente_eventos", "reservaciones", "consentimientos", "consentimiento_eventos",
                     "atenciones", "importaciones", "importacion_filas", "capturas_manuales", "planes_diarios",
                     "plan_elementos", "operators", "requests", "request_events", "assignments")
LIMITE_ESCANEO = 200 * 1024 * 1024


# ------------------------------------------------------------------ utilidades (sin dependencias de la app)
def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def leer_env(inst: Path) -> tuple[list[bytes], dict[str, str]]:
    return leer_env_archivo(inst / ".env")


def leer_env_archivo(ruta: Path) -> tuple[list[bytes], dict[str, str]]:
    crudo = ruta.read_bytes()
    lineas = crudo.splitlines(keepends=True)
    valores = {}
    for ln in lineas:
        t = ln.decode("utf-8-sig", "replace").strip()
        if t and not t.startswith("#") and "=" in t:
            k, v = t.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    return lineas, valores


def verificar(pwd: str, guardado: str) -> bool:  # misma lógica que monitoreo.security.verify_password
    try:
        _, n, r, p, sal, dk = guardado.split("$")
        calc = hashlib.scrypt(pwd.encode(), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def hashear(pwd: str) -> str:  # misma lógica que monitoreo.security.hash_password
    sal = secrets.token_bytes(16)
    dk = hashlib.scrypt(pwd.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${2 ** 14}$8$1${base64.b64encode(sal).decode()}${base64.b64encode(dk).decode()}"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def base(inst: Path) -> Path:
    return inst / "data" / "monitoreo.db"


def abrir_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def puerto_ocupado(puerto: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def admin(con: sqlite3.Connection, usuario: str):
    return con.execute("SELECT id, password_hash, active FROM users WHERE username = ?", (usuario,)).fetchone()


def escanear(raices: list[Path], valor: str) -> list[dict]:
    agujas = [valor.encode("utf-8"), valor.encode("utf-16-le")]
    largo = max(len(a) for a in agujas)
    hallados = []
    for raiz in raices:
        if not raiz.exists():
            continue
        archivos = [raiz] if raiz.is_file() else (p for p in raiz.rglob("*") if p.is_file())
        for p in archivos:
            try:
                if p.stat().st_size > LIMITE_ESCANEO:
                    continue
                cola = b""
                with p.open("rb") as f:
                    for bloque in iter(lambda: f.read(1 << 20), b""):
                        trozo = cola + bloque
                        if any(a in trozo for a in agujas):
                            hallados.append({"ruta": str(p), "tamano": p.stat().st_size})
                            break
                        cola = trozo[-largo:]
            except OSError as e:
                hallados.append({"ruta": str(p), "error_lectura": type(e).__name__})
    return hallados


def raices_escaneo(inst: Path) -> list[Path]:
    perfil = Path(os.environ.get("USERPROFILE", ""))
    return [inst, Path(r"C:\cauce-respaldos"), Path(r"D:\cauce-respaldos"), perfil / "Cauce-AI-Center",
            perfil / r"AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine"]


def guardar(salida: Path, nombre: str, datos: dict) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    (salida / nombre).write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ órdenes
def o_inventario(a, inst, salida, env):
    valor = env.get(CLAVE) or ""
    usuario = env.get("MONITOREO_ADMIN_USER", "admin")
    con = abrir_ro(base(inst))
    usuarios = [{"usuario": u, "rol": r, "activo": bool(ac)} for u, r, ac in
                con.execute("SELECT username, role, active FROM users ORDER BY id")]
    fila = admin(con, usuario)
    con.close()
    txt = inst / TXT
    raices = raices_escaneo(inst)
    d = {"cuando": ahora(), "entorno": env.get("MONITOREO_ENTORNO", "desarrollo"), "usuario_admin": usuario,
         "usuarios": usuarios, "admins_activos": sum(1 for u in usuarios if u["rol"] == "admin" and u["activo"]),
         "env_tiene_clave": bool(valor), "txt_existe": txt.exists(),
         "txt_coincide_con_env": bool(valor) and txt.exists() and txt.read_text("utf-8-sig", "replace").strip() == valor,
         "env_es_la_vigente": bool(valor) and fila is not None and verificar(valor, fila[1]),
         "monitor_en_marcha": puerto_ocupado(int(env.get("MONITOREO_PUERTO", "8000"))),
         "archivos_con_la_contrasena": escanear(raices, valor) if valor else "sin valor en .env: no se escanea"}
    guardar(salida, f"inventario_{ahora().replace(':', '')}.json", d)
    n = len(d["archivos_con_la_contrasena"]) if isinstance(d["archivos_con_la_contrasena"], list) else "-"
    print(f"inventario: admins_activos={d['admins_activos']} env_es_la_vigente={d['env_es_la_vigente']} "
          f"txt_coincide={d['txt_coincide_con_env']} archivos={n}")
    return 0


def huella(db: Path) -> dict:
    con = abrir_ro(db)
    d = {"integridad": con.execute("PRAGMA integrity_check").fetchone()[0], "tablas": {}}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        h, n = hashlib.sha256(), 0
        for fila in con.execute(f'SELECT * FROM "{t}" ORDER BY 1'):
            h.update(repr(fila).encode()); n += 1
        d["tablas"][t] = {"filas": n, "sha256": h.hexdigest()}
    con.close()
    return d


def o_instantanea(a, inst, salida, env):
    if puerto_ocupado(int(env.get("MONITOREO_PUERTO", "8000"))):
        print("ERROR: Monitor está en marcha; detenlo para una instantánea coherente"); return 2
    d = {"cuando": ahora(), "etiqueta": a.etiqueta, **huella(base(inst))}
    guardar(salida, f"instantanea_{a.etiqueta}.json", d)
    print(f"instantanea {a.etiqueta}: integridad={d['integridad']} tablas={len(d['tablas'])}")
    return 0 if d["integridad"] == "ok" else 1


def o_comparar(a, inst, salida, env):
    x = json.loads((salida / f"instantanea_{a.a}.json").read_text("utf-8"))["tablas"]
    y = json.loads((salida / f"instantanea_{a.b}.json").read_text("utf-8"))["tablas"]
    cambios = {t: {"filas_antes": x.get(t, {}).get("filas"), "filas_despues": y.get(t, {}).get("filas")}
               for t in sorted(set(x) | set(y)) if x.get(t) != y.get(t)}
    protegidas = [t for t in cambios if t in TABLAS_PROTEGIDAS]
    d = {"cuando": ahora(), "a": a.a, "b": a.b, "tablas_cambiadas": cambios, "protegidas_cambiadas": protegidas}
    guardar(salida, f"comparacion_{a.a}_{a.b}.json", d)
    print(f"comparar {a.a}->{a.b}: cambiadas={sorted(cambios)} protegidas_cambiadas={protegidas}")
    return 1 if protegidas else 0


def restablecer(db: Path, usuario: str, nueva: str, motivo: str) -> int:
    con = sqlite3.connect(str(db))
    try:
        fila = admin(con, usuario)
        if fila is None:
            raise SystemExit(f"ERROR: no existe el usuario {usuario}")
        con.execute("UPDATE users SET password_hash = ?, active = 1 WHERE id = ?", (hashear(nueva), fila[0]))
        cerradas = con.execute("UPDATE api_tokens SET revoked = 1 WHERE user_id = ? AND revoked = 0", (fila[0],)).rowcount
        con.execute("INSERT INTO audit_log (ts, username, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
                    (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "), usuario,
                     "restablecimiento_emergencia", usuario, json.dumps({"motivo": motivo, "sesiones_cerradas": cerradas}), "local"))
        con.commit()
        return cerradas
    finally:
        con.close()


def copiar_base(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    src, dst = abrir_ro(origen), sqlite3.connect(str(destino))
    src.backup(dst); dst.close(); src.close()


def o_prueba_emergencia(a, inst, salida, env):
    usuario = env.get("MONITOREO_ADMIN_USER", "admin")
    copia = salida / "prueba_emergencia" / "copia.db"
    con = abrir_ro(base(inst)); real_antes = admin(con, usuario)[1]; con.close()
    copiar_base(base(inst), copia)
    con = abrir_ro(copia); antes = admin(con, usuario)[1]; con.close()
    temporal = "Prueba-" + secrets.token_urlsafe(18)   # nunca se muestra; solo vive en memoria
    restablecer(copia, usuario, temporal, "ensayo en copia")
    con = abrir_ro(copia); despues = admin(con, usuario)[1]
    evento = con.execute("SELECT COUNT(*) FROM audit_log WHERE action='restablecimiento_emergencia'").fetchone()[0]
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]; con.close()
    d = {"cuando": ahora(), "hash_cambiado": antes != despues, "nueva_verifica": verificar(temporal, despues),
         "env_ya_no_verifica": not verificar(env.get(CLAVE) or "", despues), "auditado": evento > 0,
         "integridad_copia": integ}
    con = abrir_ro(base(inst)); d["base_real_intacta"] = admin(con, usuario)[1] == real_antes; con.close()
    temporal = None
    for p in copia.parent.glob("copia.db*"):
        p.unlink()
    d["copia_borrada"] = not any(copia.parent.glob("copia.db*"))
    ok = all([d["hash_cambiado"], d["nueva_verifica"], d["env_ya_no_verifica"], d["auditado"], integ == "ok", d["copia_borrada"],
              d["base_real_intacta"]])
    guardar(salida, "prueba_emergencia.json", d)
    print(f"prueba-emergencia: {'OK' if ok else 'FALLA'} {d}")
    return 0 if ok else 1


def o_login(a, inst, salida, env):
    puerto = int(env.get("MONITOREO_PUERTO", "8000"))
    cuerpo = json.dumps({"username": env.get("MONITOREO_ADMIN_USER", "admin"), "password": env.get(CLAVE) or ""}).encode()
    pet = urllib.request.Request(f"http://127.0.0.1:{puerto}/api/auth/login", data=cuerpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pet, timeout=15) as r:
            codigo, token = r.status, json.loads(r.read()).get("token")
    except urllib.error.HTTPError as e:
        codigo, token = e.code, None
    cerrada = None
    if token:  # no se deja una sesión abierta
        salir = urllib.request.Request(f"http://127.0.0.1:{puerto}/api/auth/logout", data=b"{}", method="POST",
                                       headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(salir, timeout=15) as r:
            cerrada = r.status == 200
    token = None
    d = {"cuando": ahora(), "codigo": codigo, "esperado": a.esperado, "sesion_cerrada": cerrada}
    guardar(salida, f"login_{a.esperado}_{ahora().replace(':', '')}.json", d)
    print(f"login con la contraseña del .env: codigo={codigo} esperado={a.esperado}")
    return 0 if codigo == a.esperado else 1


def o_auditoria(a, inst, salida, env):
    usuario = env.get("MONITOREO_ADMIN_USER", "admin")
    con = abrir_ro(base(inst))
    filas = [{"ts": ts, "accion": ac, "ip": ip, "detalle": json.loads(de) if de else {}} for ts, ac, ip, de in con.execute(
        "SELECT ts, action, ip, detail FROM audit_log WHERE username = ? AND ts >= ? AND action IN "
        "('login','login_fallido','logout','cambio_contrasena','restablecimiento_emergencia') ORDER BY ts",
        (usuario, a.desde.replace("T", " ").rstrip("Z")))]
    con.close()
    guardar(salida, f"auditoria_{ahora().replace(':', '')}.json", {"desde": a.desde, "eventos": filas})
    for f in filas:
        print(f"{f['ts']}  {f['accion']}  {f['ip']}  {f['detalle']}")
    return 0


def o_retirar(a, inst, salida, env):
    if not a.confirmo:
        print("ERROR: falta --confirmo"); return 2
    valor, usuario = env.get(CLAVE) or "", env.get("MONITOREO_ADMIN_USER", "admin")
    if not valor:
        print("ERROR: el .env ya no tiene la clave"); return 2
    con = abrir_ro(base(inst)); fila = admin(con, usuario); con.close()
    if fila is None or verificar(valor, fila[1]):
        print("ERROR: la contraseña del .env SIGUE siendo la vigente: primero cambia la contraseña en el panel"); return 1
    resp = a.respaldos or [Path(r"C:\cauce-respaldos\f3_0_20261125_0627\cauce-msi"),
                           Path(r"D:\cauce-respaldos\f3_0_20261125_0627\cauce-msi")]
    d = {"cuando": ahora(), "acciones": []}
    for rel in (Path(".env"), TXT):  # solo se retira si hay dos copias idénticas verificadas en los respaldos
        viva = inst / rel
        if not viva.exists():
            d["acciones"].append({"archivo": str(rel), "estado": "no existe"}); continue
        h = sha256(viva)
        if not all((r / rel).exists() and sha256(r / rel) == h for r in resp):
            print(f"ERROR: {rel} no tiene copia idéntica en todos los respaldos indicados; no se toca nada"); return 1
    lineas, _ = leer_env(inst)
    quedan = [ln for ln in lineas if not ln.decode("utf-8-sig", "replace").strip().startswith(CLAVE + "=")]
    with (inst / ".env").open("r+b") as f:  # en el mismo archivo: conserva sus permisos NTFS
        f.seek(0); f.write(b"".join(quedan)); f.truncate()
    d["acciones"].append({"archivo": ".env", "estado": "línea retirada", "lineas_antes": len(lineas), "lineas_despues": len(quedan)})
    (inst / TXT).unlink()
    d["acciones"].append({"archivo": str(TXT), "estado": "borrado (copias idénticas en C: y D: verificadas)"})
    _, env2 = leer_env(inst)
    d["env_sin_clave"] = CLAVE not in env2
    d["archivos_con_la_contrasena_tras_retirar"] = escanear(raices_escaneo(inst), valor)
    valor = None
    guardar(salida, "retirada.json", d)
    print(f"retirar: {d}")
    return 0 if d["env_sin_clave"] else 1


def o_escanear(a, inst, salida, env):
    _, otro = leer_env_archivo(a.valor_desde)
    valor = otro.get(CLAVE) or ""
    if not valor:
        print("ERROR: ese .env no tiene la clave"); return 2
    hallados = escanear(a.rutas, valor)
    valor = None
    d = {"cuando": ahora(), "rutas": [str(r) for r in a.rutas], "archivos_con_la_contrasena": hallados}
    guardar(salida, f"escaneo_{ahora().replace(':', '')}.json", d)
    print(f"escanear: {len(hallados)} archivo(s)")
    for h in hallados:
        print("  " + h["ruta"])
    return 0


def o_emergencia(a, inst, salida, env):
    import getpass
    if not a.confirmo:
        print("ERROR: falta --confirmo"); return 2
    if puerto_ocupado(int(env.get("MONITOREO_PUERTO", "8000"))):
        print("ERROR: detén Monitor antes del restablecimiento de emergencia"); return 2
    p1 = getpass.getpass("Nueva contraseña del administrador: "); p2 = getpass.getpass("Repítela: ")
    if p1 != p2 or len(p1) < 10 or p1.isalpha() or p1.isdigit():
        print("ERROR: no coinciden o es débil (mín. 10, letras y números)"); return 1
    copia = salida / f"antes_emergencia_{ahora().replace(':', '')}.db"
    copiar_base(base(inst), copia)
    cerradas = restablecer(base(inst), env.get("MONITOREO_ADMIN_USER", "admin"), p1, "emergencia manual")
    p1 = p2 = None
    print(f"emergencia: contraseña restablecida, sesiones cerradas={cerradas}, copia previa={copia}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instalacion", required=True, type=Path)
    ap.add_argument("--salida", required=True, type=Path)
    sub = ap.add_subparsers(dest="orden", required=True)
    sub.add_parser("inventario")
    sub.add_parser("instantanea").add_argument("etiqueta")
    c = sub.add_parser("comparar"); c.add_argument("a"); c.add_argument("b")
    sub.add_parser("prueba-emergencia")
    sub.add_parser("login").add_argument("esperado", type=int, choices=(200, 401))
    sub.add_parser("auditoria").add_argument("desde")
    r = sub.add_parser("retirar"); r.add_argument("--confirmo", action="store_true")
    r.add_argument("--respaldos", type=Path, nargs="+", help="copias de la instalación donde .env y el .txt deben ser idénticos")
    e = sub.add_parser("escanear"); e.add_argument("--valor-desde", required=True, type=Path); e.add_argument("rutas", type=Path, nargs="+")
    sub.add_parser("emergencia").add_argument("--confirmo", action="store_true")
    a = ap.parse_args()
    _, env = leer_env(a.instalacion)
    return {"inventario": o_inventario, "instantanea": o_instantanea, "comparar": o_comparar,
            "prueba-emergencia": o_prueba_emergencia, "login": o_login, "auditoria": o_auditoria,
            "retirar": o_retirar, "escanear": o_escanear, "emergencia": o_emergencia}[a.orden](a, a.instalacion, a.salida, env)


if __name__ == "__main__":
    sys.exit(main())

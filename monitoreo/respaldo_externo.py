"""Respaldo de la base a un medio externo, con verificación y ensayo de restauración (piloto).

Por qué existe: `python -m monitoreo.migrate respaldar` deja el respaldo en `data/respaldos/`, es decir, en el
MISMO disco que la base. Si el disco se pierde o se daña, la base y sus respaldos se pierden juntos. Este
módulo copia un respaldo consistente a OTRA unidad (memoria USB, segundo disco o carpeta de red), comprueba
que lo escrito en el medio es idéntico a lo respaldado y permite ensayar la restauración en una carpeta
temporal SIN tocar la base real ni el diario de eventos críticos.

Qué hace cada orden (solo SQLite; con PostgreSQL se rechaza con un mensaje claro):

    python -m monitoreo.respaldo_externo revisar   [carpeta]   # ¿sirve la carpeta? (no escribe datos)
    python -m monitoreo.respaldo_externo respaldar [carpeta]   # respaldo + copia verificada + manifiesto
    python -m monitoreo.respaldo_externo listar    [carpeta]   # respaldos del medio y su estado
    python -m monitoreo.respaldo_externo verificar <archivo.db> # huella SHA-256 + integridad, sin restaurar
    python -m monitoreo.respaldo_externo ensayar   <archivo.db> # restauración de prueba en carpeta temporal

Si no se indica carpeta se usa MONITOREO_RESPALDO_EXTERNO. La restauración real sigue siendo
`python -m monitoreo.migrate restaurar <archivo.db>`: si junto al archivo está su manifiesto, `migrate`
comprueba la huella ANTES de tocar nada y se niega si no coincide.

Garantías (ver tests/test_respaldo_externo_base.py):
- La copia se escribe con un nombre provisional y solo se renombra al final, tras releerla del medio y
  comprobar su SHA-256 y `PRAGMA integrity_check`. Un corte (USB desconectada, disco lleno) no deja un
  respaldo a medias con nombre definitivo ni borra respaldos anteriores.
- La rotación (`--conservar N`, 14 por omisión) solo borra respaldos antiguos DESPUÉS de verificar el nuevo.
- El manifiesto solo contiene huellas, tamaños, revisión del esquema y número de filas por tabla: ningún dato
  de personas.
- El destino debe estar en otra unidad que la base y fuera de la carpeta de datos. `--laboratorio` permite la
  misma unidad SOLO para pruebas automatizadas; el manifiesto lo deja escrito («misma_unidad»: true).
- El respaldo NO va cifrado por Cauce: contiene datos de expedientes. En el piloto, el medio debe estar cifrado
  (BitLocker To Go en Windows) y guardado bajo llave. Ver docs/PROTECCION_DATOS_PILOTO.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings

PREFIJO = "cauce-respaldo-"
CONSERVAR = 14
ULTIMO = "respaldo_externo_ultimo.json"     # en data/: fecha y huella del último respaldo externo verificado
_BLOQUE = 1024 * 1024


class ErrorRespaldo(Exception):
    pass


# ---------------------------------------------------------------- utilidades
def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(_BLOQUE), b""):
            h.update(b)
    return h.hexdigest()


def manifiesto_de(archivo: Path) -> Path:
    return archivo.with_suffix(".json")


def carpeta_configurada() -> Path | None:
    v = (os.environ.get("MONITOREO_RESPALDO_EXTERNO") or "").strip()
    return Path(v).expanduser() if v else None


def _ruta_base(url: str) -> Path:
    if not url.startswith("sqlite"):
        raise ErrorRespaldo("El respaldo externo verificado solo existe para SQLite. Con PostgreSQL usa pg_dump a un "
                            "archivo en el medio externo y pg_restore en una base de prueba (ver docs/PRODUCCION.md).")
    p = make_url(url).database
    if not p or p == ":memory:":
        raise ErrorRespaldo("No se puede respaldar una base en memoria")
    return Path(p)


def _volumen(p: Path):
    from .recuperacion import _volumen as v
    return v(p)


def _integridad(ruta: Path) -> str:
    try:
        c = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return str(c.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        return f"{type(e).__name__}: {e}"


def _filas(ruta: Path) -> dict[str, int]:
    c = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tablas = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                          "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: int(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tablas}
    finally:
        c.close()


def _revision(ruta: Path) -> str | None:
    c = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        c.close()


def _proteger(p: Path) -> None:
    from .captura.retencion import proteger
    proteger(p)


# ---------------------------------------------------------------- revisar el destino
def revisar(carpeta: str | Path | None, url: str | None = None, laboratorio: bool = False) -> dict:
    """¿Sirve la carpeta como destino? No escribe datos (solo un archivo de prueba vacío que se borra)."""
    url = url or settings.db_url
    out: dict = {"carpeta": None, "apta": False, "problemas": [], "avisos": []}
    c = Path(carpeta).expanduser() if carpeta else carpeta_configurada()
    if c is None:
        out["problemas"].append("No hay carpeta de destino (indica una o define MONITOREO_RESPALDO_EXTERNO)")
        return out
    out["carpeta"] = str(c)
    try:
        base = _ruta_base(url).resolve()
    except ErrorRespaldo as e:
        out["problemas"].append(str(e))
        return out
    if not c.is_dir():
        out["problemas"].append("La carpeta no existe o la unidad no está conectada")
        return out
    res = c.resolve()
    datos = settings.data_dir.resolve()
    if res == datos or datos in res.parents:
        out["problemas"].append("La carpeta está dentro de la carpeta de datos de Cauce: se perdería con ella")
    misma = _volumen(res) is not None and _volumen(res) == _volumen(base)
    out["misma_unidad"] = misma
    if misma:
        msg = ("La carpeta está en la MISMA unidad que la base: no protege contra la pérdida o el daño del disco. "
               "Usa una memoria USB cifrada, un segundo disco o una carpeta de red.")
        (out["avisos"] if laboratorio else out["problemas"]).append(msg + (" (aceptado: --laboratorio)" if laboratorio else ""))
    diario_copia = (os.environ.get("MONITOREO_DIARIO_COPIA") or "").strip()
    if diario_copia:
        try:
            if Path(diario_copia).expanduser().resolve() == res:
                out["avisos"].append("Es la misma carpeta que la copia del diario: funciona, pero conviene una "
                                     "subcarpeta distinta (p. ej. E:\\Cauce\\respaldos y E:\\Cauce\\diario)")
        except OSError:
            pass
    if not out["problemas"]:
        prueba = res / f".cauce-prueba-{os.getpid()}"
        try:
            fd = os.open(prueba, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            prueba.unlink()
            libre = shutil.disk_usage(res).free
            out["libre_mb"] = round(libre / 1e6, 1)
            if base.exists() and libre < base.stat().st_size * 3:
                out["problemas"].append(f"Poco espacio libre en el destino ({out['libre_mb']} MB) para el tamaño de la base")
        except OSError as e:
            out["problemas"].append(f"No se puede escribir en la carpeta: {type(e).__name__}")
    out["apta"] = not out["problemas"]
    return out


# ---------------------------------------------------------------- respaldar
def _copiar_verificado(origen: Path, destino: Path) -> str:
    """Copia por bloques a un nombre provisional, fsync, relee del medio, compara SHA-256 y renombra."""
    parcial = destino.with_name(destino.name + ".parcial")
    h_origen = sha256(origen)
    try:
        with open(origen, "rb") as fi, open(parcial, "xb") as fo:
            for b in iter(lambda: fi.read(_BLOQUE), b""):
                fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
        h_destino = sha256(parcial)
        if h_destino != h_origen:
            raise ErrorRespaldo("La copia en el medio externo no coincide con el respaldo (SHA-256 distinto): "
                                "el medio puede estar dañado. No se conserva.")
        ok = _integridad(parcial)
        if ok != "ok":
            raise ErrorRespaldo(f"La copia en el medio externo no pasó la verificación de integridad: {ok}")
        os.replace(parcial, destino)
    except BaseException:
        try:
            parcial.unlink()
        except OSError:
            pass
        raise
    return h_origen


def _escribir_json(ruta: Path, obj: dict) -> None:
    tmp = ruta.with_name(ruta.name + ".parcial")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def respaldos_en(carpeta: Path) -> list[Path]:
    if not carpeta.is_dir():
        return []
    return sorted((p for p in carpeta.iterdir() if p.is_file() and p.name.startswith(PREFIJO) and p.suffix == ".db"),
                  key=lambda p: p.name, reverse=True)


def respaldar(carpeta: str | Path | None = None, url: str | None = None, conservar: int = CONSERVAR,
              laboratorio: bool = False) -> dict:
    from . import migrate
    url = url or settings.db_url
    rev = revisar(carpeta, url, laboratorio=laboratorio)
    if not rev["apta"]:
        raise ErrorRespaldo("El destino no es apto: " + "; ".join(rev["problemas"]))
    destino_dir = Path(rev["carpeta"]).resolve()
    try:
        local = Path(migrate.backup(url, "externo"))      # copia consistente (API de respaldo de SQLite) + integridad
    except migrate.MigrationError as e:
        raise ErrorRespaldo(str(e)) from e
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destino = destino_dir / f"{PREFIJO}{stamp}.db"
    huella = _copiar_verificado(local, destino)
    _proteger(destino)
    try:
        from . import recuperacion as rc
        raw = rc._leer_clave()
        huella_clave = rc.huella_clave(raw) if raw else None
    except Exception:  # noqa: BLE001 - la clave del diario es informativa aquí
        huella_clave = None
    man = {"formato": "cauce.respaldo_externo/1", "archivo": destino.name, "sha256": huella,
           "bytes": destino.stat().st_size, "creado": _utc(), "revision": _revision(destino),
           "filas": _filas(destino), "integridad": "ok", "misma_unidad": bool(rev.get("misma_unidad")),
           "huella_clave_diario": huella_clave,
           "nota": "Sin datos de personas. El respaldo .db SÍ los contiene: medio cifrado y bajo llave."}
    _escribir_json(manifiesto_de(destino), man)
    _proteger(manifiesto_de(destino))
    borrados = []
    if conservar > 0:
        for viejo in respaldos_en(destino_dir)[conservar:]:
            for p in (viejo, manifiesto_de(viejo)):
                try:
                    p.unlink()
                except OSError:
                    pass
            borrados.append(viejo.name)
    ultimo = {"hora": man["creado"], "archivo": str(destino), "sha256": huella, "misma_unidad": man["misma_unidad"]}
    _escribir_json(settings.data_dir / ULTIMO, ultimo)
    return {"ok": True, "archivo": str(destino), "sha256": huella, "bytes": man["bytes"], "revision": man["revision"],
            "respaldo_local": str(local), "rotacion_borrados": borrados, "avisos": rev["avisos"]}


# ---------------------------------------------------------------- verificar / ensayar
def verificar(archivo: str | Path, exigir_manifiesto: bool = True) -> dict:
    a = Path(archivo)
    out: dict = {"archivo": str(a), "ok": False, "problemas": []}
    if not a.is_file():
        out["problemas"].append("El archivo no existe (¿unidad desconectada?)")
        return out
    m = manifiesto_de(a)
    man = None
    if m.is_file():
        try:
            man = json.loads(m.read_text("utf-8"))
        except (OSError, ValueError) as e:
            out["problemas"].append(f"Manifiesto ilegible: {type(e).__name__}")
    elif exigir_manifiesto:
        out["problemas"].append("Falta el manifiesto (.json) junto al respaldo: no se puede comprobar su huella")
    out["sha256"] = sha256(a)
    if man is not None:
        out["manifiesto"] = {k: man.get(k) for k in ("creado", "revision", "bytes", "misma_unidad")}
        if man.get("sha256") != out["sha256"]:
            out["problemas"].append("La huella SHA-256 no coincide con el manifiesto: el archivo cambió o está dañado")
        if man.get("bytes") != a.stat().st_size:
            out["problemas"].append("El tamaño no coincide con el manifiesto")
    ok = _integridad(a)
    out["integridad"] = ok
    if ok != "ok":
        out["problemas"].append(f"La base del respaldo no pasa PRAGMA integrity_check: {ok[:200]}")
    out["ok"] = not out["problemas"]
    return out


def comprobar_antes_de_restaurar(archivo: str | Path) -> None:
    """Llamado por migrate.restore: si el respaldo tiene manifiesto, su huella debe coincidir."""
    a = Path(archivo)
    if manifiesto_de(a).is_file() and a.name.startswith(PREFIJO):
        v = verificar(a)
        if not v["ok"]:
            raise ErrorRespaldo("El respaldo no supera la verificación y no se restaura: " + "; ".join(v["problemas"]))


def ensayar(archivo: str | Path) -> dict:
    """Restauración de PRUEBA en una carpeta temporal: no toca la base real, ni data/, ni el diario.
    Comprueba la huella, copia con la API de respaldo, compara las filas con el manifiesto, aplica las
    migraciones pendientes a la COPIA (lo que pasaría al arrancar la versión instalada) y revisa claves foráneas."""
    from . import migrate
    v = verificar(archivo)
    out: dict = {"archivo": str(archivo), "verificacion": v, "ok": False, "problemas": list(v["problemas"])}
    if not v["ok"]:
        return out
    man = json.loads(manifiesto_de(Path(archivo)).read_text("utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="cauce-ensayo-"))
    try:
        copia = tmp / "ensayo.db"
        s, d = sqlite3.connect(f"{Path(archivo).resolve().as_uri()}?mode=ro", uri=True), sqlite3.connect(copia)
        try:
            s.backup(d)
        finally:
            s.close()
            d.close()
        filas = _filas(copia)
        dif = {t: (man["filas"].get(t), filas.get(t)) for t in set(man["filas"]) | set(filas)
               if man["filas"].get(t) != filas.get(t)}
        if dif:
            out["problemas"].append(f"Las filas restauradas no coinciden con el manifiesto: {dif}")
        url = f"sqlite:///{copia.as_posix()}"
        out["revision_respaldo"] = _revision(copia)
        out["revision_instalada"] = migrate.head_revision()
        try:
            rep = migrate.upgrade(url, make_backup=False)
            out["migracion"] = rep.steps or ["sin cambios de esquema"]
        except Exception as e:  # noqa: BLE001
            out["problemas"].append(f"La versión instalada no puede abrir este respaldo: {type(e).__name__}: {str(e)[:200]}")
        c = sqlite3.connect(copia)
        try:
            fk = c.execute("PRAGMA foreign_key_check").fetchall()
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
        if fk:
            out["problemas"].append(f"Claves foráneas rotas en la copia restaurada: {len(fk)}")
        if ok != "ok":
            out["problemas"].append(f"La copia restaurada no pasa integrity_check: {ok}")
        out["filas"] = {k: filas[k] for k in ("expedientes", "consentimientos", "reservaciones", "audit_log") if k in filas}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["ok"] = not out["problemas"]
    return out


def listar(carpeta: str | Path | None = None) -> dict:
    c = Path(carpeta).expanduser() if carpeta else carpeta_configurada()
    if c is None:
        return {"carpeta": None, "respaldos": [], "problemas": ["Sin carpeta (MONITOREO_RESPALDO_EXTERNO vacía)"]}
    out = []
    for p in respaldos_en(c):
        try:
            man = json.loads(manifiesto_de(p).read_text("utf-8"))
        except (OSError, ValueError):
            man = {}
        out.append({"archivo": p.name, "creado": man.get("creado"), "bytes": p.stat().st_size,
                    "revision": man.get("revision"), "manifiesto": bool(man)})
    return {"carpeta": str(c), "conectada": c.is_dir(), "respaldos": out}


def ultimo() -> dict | None:
    try:
        return json.loads((settings.data_dir / ULTIMO).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def horas_desde_ultimo(now: datetime | None = None) -> float | None:
    u = ultimo()
    if not u or not u.get("hora"):
        return None
    t = datetime.strptime(u["hora"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - t).total_seconds() / 3600


# ---------------------------------------------------------------- consola
def main(argv: list[str]) -> int:
    lab = "--laboratorio" in argv
    conservar = CONSERVAR
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--laboratorio":
            pass
        elif argv[i] == "--conservar" and i + 1 < len(argv):
            conservar = int(argv[i + 1])
            i += 1
        else:
            args.append(argv[i])
        i += 1
    cmd = args[0] if args else ""
    arg = args[1] if len(args) > 1 else None
    try:
        if cmd == "revisar":
            r = revisar(arg, laboratorio=lab)
        elif cmd == "respaldar":
            r = respaldar(arg, conservar=conservar, laboratorio=lab)
        elif cmd == "listar":
            r = listar(arg)
            r["ok"] = r.get("conectada", False)
        elif cmd == "verificar" and arg:
            r = verificar(arg)
        elif cmd == "ensayar" and arg:
            r = ensayar(arg)
        else:
            print(__doc__)
            return 2
    except ErrorRespaldo as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    ok = r.get("apta", r.get("ok", False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

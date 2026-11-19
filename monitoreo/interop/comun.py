"""Utilidades comunes de la interoperabilidad: raíz del proyecto, git de solo lectura, JSON estable."""
from __future__ import annotations

import hashlib
import re
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
DIR_JARVIS = "jarvis"
PROYECTO_ID = "cauce-monitor-engine"

ESTADOS = ("VERIFICADO", "REPORTADO", "SIMULADO", "PENDIENTE")


class ErrorSolicitud(Exception):
    """La solicitud no está permitida (suite desconocida, ruta no autorizada, manifiesto inválido)."""


def ahora_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 16), b""):
            h.update(bloque)
    return h.hexdigest()


def json_canonico(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def escribir_json(ruta: Path, obj) -> None:
    """Escritura atómica: un lector (Jarvis) nunca ve un archivo a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def git(raiz: Path, *args: str, timeout: float = 30) -> tuple[int, str]:
    """git de SOLO LECTURA. GIT_OPTIONAL_LOCKS=0 evita que `git status` reescriba el índice.
    Devuelve (código, salida). Sin git instalado o fuera de un repositorio: (127, "")."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        r = subprocess.run(["git", "-C", str(raiz), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return r.returncode, r.stdout.strip()


def estado_git(raiz: Path) -> dict:
    """Rama, commit completo y si el árbol de trabajo está limpio. Nunca falla: si no hay git,
    lo dice con estado DESCONOCIDO."""
    c1, commit = git(raiz, "rev-parse", "HEAD")
    if c1 != 0 or len(commit) != 40:
        return {"rama": None, "commit": None, "commit_corto": None, "estado": "DESCONOCIDO",
                "limpio": None, "cambios": {"modificados": 0, "sin_seguimiento": 0}}
    _, rama = git(raiz, "rev-parse", "--abbrev-ref", "HEAD")
    _, porcelana = git(raiz, "status", "--porcelain=v1", "-uall")
    lineas = [x for x in porcelana.splitlines() if x.strip()]
    sin_seg = sum(1 for x in lineas if x.startswith("??"))
    return {"rama": None if rama == "HEAD" else rama, "commit": commit, "commit_corto": commit[:7],
            "estado": "LIMPIO" if not lineas else "CON_CAMBIOS", "limpio": not lineas,
            "cambios": {"modificados": len(lineas) - sin_seg, "sin_seguimiento": sin_seg}}


def huella_git_status(raiz: Path) -> str:
    """Huella del `git status` (para comprobar que una ejecución no cambió el repositorio)."""
    _, porcelana = git(raiz, "status", "--porcelain=v1", "-uall")
    return hashlib.sha256(porcelana.encode("utf-8")).hexdigest()


def huella_directorio(d: Path) -> str:
    """Huella de nombres, tamaños y fechas de un directorio (p. ej. data/). Vacío si no existe."""
    if not d.is_dir():
        return hashlib.sha256(b"").hexdigest()
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        try:
            if p.is_file():
                st = p.stat()
                h.update(f"{p.relative_to(d).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        except OSError:
            continue
    return h.hexdigest()


def version_proyecto() -> str:
    from ..version import VERSION
    return VERSION


def leer_proyecto(raiz: Path) -> dict:
    from . import yaml_simple
    return yaml_simple.cargar((raiz / DIR_JARVIS / "project.yaml").read_text(encoding="utf-8")) or {}


def relacion_con_fijado(raiz: Path, g: dict | None = None) -> dict:
    """v2.2.2: compara HEAD con la rama y el commit fijados en jarvis/project.yaml (`integracion`).

    relacion:
      FIJADO                    HEAD es el commit fijado
      POSTERIOR_SOLO_DOCUMENTOS HEAD desciende del fijado y solo cambian rutas de documentación
      POSTERIOR_CON_CODIGO      HEAD desciende del fijado y cambia código: hay que volver a fijar
      DISTINTO                  HEAD no desciende del fijado (otra rama u otra historia)
      SIN_DECLARAR              project.yaml no declara rama o commit
      DESCONOCIDO               sin git o el commit fijado no está en este clon
    """
    g = g or estado_git(raiz)
    try:
        integ = (leer_proyecto(raiz).get("integracion") or {})
    except (OSError, ValueError):
        integ = {}
    rama, fijado = integ.get("rama"), integ.get("commit_fijado")
    rutas = [str(x) for x in integ.get("rutas_solo_documentacion") or []]
    out = {"rama_declarada": rama if isinstance(rama, str) else None,
           "commit_fijado": fijado if isinstance(fijado, str) and re.fullmatch(r"[0-9a-f]{40}", fijado) else None,
           "rama_actual_coincide": None, "relacion": "SIN_DECLARAR", "commits_desde_fijado": None,
           "archivos_de_codigo_cambiados": None}
    if not out["rama_declarada"] or not out["commit_fijado"]:
        return out
    out["rama_actual_coincide"] = g.get("rama") == out["rama_declarada"] if g.get("rama") else None
    head = g.get("commit")
    if not head:
        out["relacion"] = "DESCONOCIDO"
        return out
    if head == fijado:
        out.update(relacion="FIJADO", commits_desde_fijado=0, archivos_de_codigo_cambiados=0)
        return out
    c, _ = git(raiz, "cat-file", "-e", f"{fijado}^{{commit}}")
    if c != 0:
        out["relacion"] = "DESCONOCIDO"
        return out
    c, _ = git(raiz, "merge-base", "--is-ancestor", fijado, head)
    if c != 0:
        out["relacion"] = "DISTINTO"
        return out
    # v2.2.2: las rutas de documentación valen las del commit FIJADO si las declara (lo verificado fija
    # sus propias reglas); así, ampliar la lista después no puede ocultar un cambio de código.
    c, texto_fijado = git(raiz, "show", f"{fijado}:{PROYECTO_YAML}")
    if c == 0:
        from . import yaml_simple
        try:
            r_fij = ((yaml_simple.cargar(texto_fijado) or {}).get("integracion") or {}).get("rutas_solo_documentacion")
        except (ValueError, AttributeError):
            r_fij = None
        if isinstance(r_fij, list):
            rutas = [str(x) for x in r_fij]
    _, n = git(raiz, "rev-list", "--count", f"{fijado}..{head}")
    _, archivos = git(raiz, "diff", "--name-only", fijado, head)
    codigo = archivos_de_codigo(raiz, fijado, head, [a for a in archivos.splitlines() if a.strip()], rutas)
    out.update(relacion="POSTERIOR_CON_CODIGO" if codigo else "POSTERIOR_SOLO_DOCUMENTOS",
               commits_desde_fijado=int(n) if n.isdigit() else None, archivos_de_codigo_cambiados=len(codigo))
    return out


# v2.2.2 (integración): partes del contrato con Jarvis que DEFINEN lo que se ejecuta o cómo se valida.
# Nunca cuentan como documentación aunque `rutas_solo_documentacion` diga `jarvis/`: cambiarlas después
# del commit fijado cambia lo verificado.
NUNCA_DOCUMENTACION = ("jarvis/test-manifest.json", "jarvis/report.schema.json")
PROYECTO_YAML = "jarvis/project.yaml"


def _proyecto_sin_fijado(texto: str | None) -> dict | None:
    """project.yaml sin el bloque `integracion`: lo único que puede cambiar tras fijar sin tocar lo verificado."""
    from . import yaml_simple
    if texto is None:
        return {}
    try:
        d = yaml_simple.cargar(texto) or {}
    except ValueError:
        return None
    d.pop("integracion", None)
    return d


def archivos_de_codigo(raiz: Path, fijado: str, head: str, archivos: list[str], rutas: list[str]) -> list[str]:
    """Archivos cambiados desde el commit fijado que NO son solo documentación.

    Reglas (v2.2.2): (1) el manifiesto y el esquema de Jarvis siempre son código; (2) project.yaml es
    documentación solo si lo único que cambió es su bloque `integracion` (rama y commit fijados): sus
    herramientas, argv y políticas sí son contrato; (3) el resto, según `rutas_solo_documentacion`."""
    codigo = []
    for a in archivos:
        if a in NUNCA_DOCUMENTACION:
            codigo.append(a)
        elif a == PROYECTO_YAML:
            c, antes = git(raiz, "show", f"{fijado}:{PROYECTO_YAML}")
            c2, ahora = git(raiz, "show", f"{head}:{PROYECTO_YAML}")
            x, y = _proyecto_sin_fijado(antes if c == 0 else None), _proyecto_sin_fijado(ahora if c2 == 0 else None)
            if x is None or y is None or x != y:
                codigo.append(a)
        elif not any(a == r or (r.endswith("/") and a.startswith(r)) for r in rutas):
            codigo.append(a)
    return codigo

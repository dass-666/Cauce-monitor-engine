"""Inventario de SOLO LECTURA del repositorio (v2.2.1), para Jarvis y para el informe consolidado.

Lee git (ramas locales y remotas tal como están en el clon; no descarga nada), las migraciones de
monitoreo/migrations/versions (sin abrir ninguna base de datos), las pruebas (conteo estático de
funciones test_*, no de casos parametrizados), los scripts de Windows (con las mismas reglas de
codificación que comprueba CI) y los informes de docs/.

Límite: las ramas remotas son las del último `git fetch`. Si el clon es viejo, el inventario también.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .comun import RAIZ, ahora_utc, estado_git, git, version_proyecto
from .saneado import sanear

_VERSION_INFORME = re.compile(r"INFORME(?:_v([0-9][0-9.]*[0-9])(?:_([a-z0-9]+))?)?\.md$")


def ramas(raiz: Path, principal: str = "master") -> list[dict]:
    fmt = "%(refname)|%(objectname)|%(committerdate:iso-strict)|%(subject)"
    _, out = git(raiz, "for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes")
    vistas: dict[str, dict] = {}
    for linea in out.splitlines():
        ref, commit, fecha, asunto = (linea.split("|", 3) + ["", "", ""])[:4]
        if ref.endswith("/HEAD"):
            continue
        nombre = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/origin/")
        if ref.startswith("refs/remotes/") and not ref.startswith("refs/remotes/origin/"):
            continue
        tipo = "remota" if ref.startswith("refs/remotes/") else "local"
        r = vistas.setdefault(nombre, {"nombre": nombre, "commit": commit, "fecha": fecha, "asunto": asunto[:160],
                                       "local": False, "remota": False})
        r[tipo] = True
        if tipo == "remota":  # la remota manda: es lo que está publicado
            if r["local"] and r["commit"] != commit:
                r["commit_local_distinto"] = r["commit"]
            r.update(commit=commit, fecha=fecha, asunto=asunto[:160])
        elif r["remota"] and r["commit"] != commit:
            r["commit_local_distinto"] = commit
    base = f"origin/{principal}"
    for r in vistas.values():
        ref = f"origin/{r['nombre']}" if r["remota"] else r["nombre"]
        c, cuenta = git(raiz, "rev-list", "--left-right", "--count", f"{base}...{ref}")
        if c == 0 and cuenta:
            detras, delante = (int(x) for x in cuenta.split())
            r["respecto_a_principal"] = {"delante": delante, "detras": detras}
    return sorted(vistas.values(), key=lambda r: r["fecha"], reverse=True)


def migraciones(raiz: Path) -> dict:
    d = raiz / "monitoreo" / "migrations" / "versions"
    revs = []
    for p in sorted(d.glob("*.py")):
        texto = p.read_text(encoding="utf-8")
        rev = re.search(r'^revision\s*=\s*["\']([^"\']+)', texto, re.M)
        down = re.search(r'^down_revision\s*=\s*(None|["\']([^"\']+))', texto, re.M)
        if rev:
            revs.append({"archivo": p.name, "revision": rev.group(1),
                         "anterior": down.group(2) if down and down.group(2) else None})
    anteriores = {r["anterior"] for r in revs}
    cabezas = [r["revision"] for r in revs if r["revision"] not in anteriores]
    return {"total": len(revs), "cabezas": cabezas, "lineal": len(cabezas) == 1, "revisiones": revs,
            "nota": "Revisiones del código. Cuáles faltan en una base concreta lo dice el autodiagnóstico "
                    "(python -m monitoreo.diagnostico) sobre esa base; este inventario no abre ninguna."}


def pruebas(raiz: Path) -> dict:
    archivos = []
    for p in sorted((raiz / "tests").glob("test_*.py")):
        try:
            arbol = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            archivos.append({"archivo": p.name, "funciones": None, "error": "no se pudo analizar"})
            continue
        n = sum(1 for x in ast.walk(arbol) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                and x.name.startswith("test_"))
        archivos.append({"archivo": p.name, "funciones": n})
    return {"archivos": len(archivos), "funciones_test": sum(a["funciones"] or 0 for a in archivos),
            "detalle": archivos,
            "nota": "Conteo estático de funciones test_*. pytest ejecuta más casos (parametrizados por SQLite y "
                    "PostgreSQL); el número real sale de una ejecución."}


def scripts_windows(raiz: Path) -> list[dict]:
    salida = []
    for p in sorted([*raiz.glob("*.bat"), *raiz.glob("*.ps1"), *(raiz / "scripts").glob("*.ps1")]):
        datos = p.read_bytes()
        ascii_ = all(b < 128 for b in datos)
        bom = datos.startswith(b"\xef\xbb\xbf")
        if p.suffix == ".bat":
            ok, regla = ascii_, ".bat solo ASCII"
        else:
            ok, regla = ascii_ or bom, ".ps1 con acentos necesita BOM (PowerShell 5.1)"
        salida.append({"archivo": p.relative_to(raiz).as_posix(), "bytes": len(datos), "codificacion_ok": ok,
                       "regla": regla, "crlf": b"\r\n" in datos})
    return salida


def informes(raiz: Path) -> list[dict]:
    salida = []
    for p in sorted((raiz / "docs").glob("INFORME*.md")):
        m = _VERSION_INFORME.search(p.name)
        version = m.group(1) if m and m.group(1) else "1.0"
        salida.append({"archivo": p.relative_to(raiz).as_posix(), "version": version,
                       "variante": m.group(2) if m else None})

    def clave(x):
        return tuple(int(n) for n in x["version"].split(".")), x["variante"] or ""
    salida.sort(key=clave)
    for x in salida:
        x["vigente"] = False
    if salida:
        ultima = max(clave(x)[0] for x in salida)
        for x in salida:
            x["vigente"] = clave(x)[0] == ultima
    return salida


def construir(raiz: Path = RAIZ, principal: str = "master") -> dict:
    g = estado_git(raiz)
    inv = {
        "formato": "cauce.jarvis.inventory/1",
        "proyecto": "cauce-monitor-engine",
        "version": version_proyecto(),
        "generado_en": ahora_utc(),
        "git": g,
        "rama_principal": principal,
        "ramas": ramas(raiz, principal),
        "migraciones": migraciones(raiz),
        "pruebas": pruebas(raiz),
        "scripts_windows": scripts_windows(raiz),
        "informes": informes(raiz),
        "modulos": sorted(p.name for p in (raiz / "monitoreo").iterdir()
                          if p.is_dir() and (p / "__init__.py").exists()),
    }
    return sanear(inv, raiz)

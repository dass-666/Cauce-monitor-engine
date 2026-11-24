"""Instalación de una sola ruta y menú agrupado (preparación para el MSI).

- Los lanzadores nuevos son ASCII con CRLF (cmd.exe) y respetan MONITOREO_SIN_PAUSA.
- comprobar_equipo.py es de solo lectura: con --sin-informe no crea ni cambia ningún archivo.
- El menú agrupado de app.js reparte TODAS las secciones de VIEWS (ninguna se pierde ni se repite).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_lanzadores_ascii_crlf_y_sin_pausa():
    for nombre in ("instalar_cauce.bat", "comprobar_equipo.bat", "cauce_rc1.bat"):
        datos = (RAIZ / nombre).read_bytes()
        assert all(b < 128 for b in datos), f"{nombre}: caracteres no ASCII"
        assert b"\r\n" in datos and b"\n" not in datos.replace(b"\r\n", b""), f"{nombre}: no es CRLF"
    for nombre in ("instalar_cauce.bat", "comprobar_equipo.bat"):
        assert "MONITOREO_SIN_PAUSA" in (RAIZ / nombre).read_text(encoding="ascii")
    inst = (RAIZ / "instalar_cauce.bat").read_text(encoding="ascii")
    # Se detiene antes de instalar si la comprobación previa da CRÍTICO; el navegador solo si falta.
    assert inst.index("comprobar_equipo.py --antes") < inst.index("powershell -NoProfile")
    assert "preparar_navegador.py --comprobar" in inst
    for prohibido in ("rmdir", "rd /s", "del /", "erase ", "--nuevo", "produccion_flujo_admin"):
        assert prohibido not in inst.lower(), prohibido


def _instantanea(raiz: Path) -> dict[str, float]:
    out = {}
    for p in raiz.rglob("*"):
        rel = p.relative_to(raiz).as_posix()
        # resultados/ lo escribe la propia CI mientras corre pytest (junit, tee); de ahí solo cuenta el informe
        if rel.startswith((".git/", ".venv/", "node_modules/", ".browsers/", ".pytest_cache/")) or "__pycache__" in rel \
                or rel.startswith("resultados/") and rel != "resultados/comprobacion_equipo.txt":
            continue
        if p.is_file():
            out[rel] = p.stat().st_mtime_ns
    return out


def test_comprobar_equipo_solo_lectura():
    antes = _instantanea(RAIZ)
    p = subprocess.run([sys.executable, "-I", str(RAIZ / "scripts" / "comprobar_equipo.py"), "--sin-informe"],
                       capture_output=True, text=True, timeout=180, cwd=str(RAIZ),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, encoding="utf-8", errors="replace")
    assert p.returncode in (0, 1, 2), p.stderr
    for clave in ("Python", "Node.js", "127.0.0.1", "escritura", "RESULTADO:"):
        assert clave in p.stdout, (clave, p.stdout)
    assert _instantanea(RAIZ) == antes, "comprobar_equipo.py cambió archivos de la carpeta"


def test_menu_agrupado_reparte_todas_las_secciones():
    js = (RAIZ / "monitoreo" / "web" / "app.js").read_text(encoding="utf-8")
    bloque = js[js.index("const VIEWS = {"):js.index("};", js.index("const VIEWS = {"))]
    vistas = re.findall(r'^\s+"?([a-z-]+)"?: \{ t:', bloque, re.M)
    assert len(vistas) == 18, vistas   # +1: «Páginas observadas» (observador universal)
    principal = re.search(r"const NAV_PRINCIPAL = \[(.*?)\];", js, re.S).group(1)
    grupos = re.findall(r'\["[a-z]+", "[^"]+", \[(.*?)\]\]', js)
    assert len(grupos) == 3, grupos
    listadas = re.findall(r'"([a-z-]+)"', principal) + [k for g in grupos for k in re.findall(r'"([a-z-]+)"', g)]
    assert sorted(listadas) == sorted(vistas), (sorted(listadas), sorted(vistas))
    # Las secciones de laboratorio siguen marcadas como tales (vistaVisible las oculta en producción).
    for k in ("laboratorio", "laboratorio-calendario", "conexiones", "tramites"):
        assert re.search(rf'"?{k}"?: \{{[^\n]*lab: true', bloque), k

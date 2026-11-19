"""Exportador de SOLO LECTURA del informe para Jarvis (v2.2.1). Formato: jarvis/report.schema.json.

Qué lee: git (sin escribir el índice), jarvis/declaraciones.json, los resultado.json que dejó el
ejecutor y los archivos de docs/resultados/ citados como históricos. No abre la base de datos, no
ejecuta pruebas y solo escribe el archivo de salida que se le indique.

Estados (reglas fijas, no opinables):
- VERIFICADO  una ejecución del ejecutor cuyo resultado.json está íntegro (las huellas SHA-256 de
              salida.txt y junit.xml coinciden), se hizo sobre el MISMO commit que hoy está en HEAD,
              con el árbol limpio y dentro de la antigüedad máxima (24 h por omisión).
- REPORTADO   todo lo demás que alguien midió antes: resultados de docs/resultados/, CI anterior o
              una ejecución que no cumple alguna condición de arriba (se dice cuál).
- SIMULADO    resultados contra el laboratorio o simulaciones (p. ej. la consulta de Jarvis de la
              demostración). Nunca se presentan como uso real.
- PENDIENTE   lo que nadie ha comprobado todavía (Windows físico, Telegram real, Jarvis real…).
Un resultado histórico nunca puede salir como VERIFICADO: solo el ejecutor produce ejecuciones, y
el anexo de declaraciones no acepta ese estado.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

from . import esquema
from .comun import (DIR_JARVIS, ESTADOS, PROYECTO_ID, RAIZ, escribir_json, estado_git, git, json_canonico,
                    relacion_con_fijado, sha256_archivo, version_proyecto)
from .saneado import buscar_sensibles, sanear

FORMATO = "cauce.jarvis.report/1"
ANTIGUEDAD_MAX_H = 24
# Campos que cambian en cada repetición aunque el estado sea el mismo: no entran en la huella.
VOLATILES = ("generado_en", "inicio", "fin", "duracion_s", "antiguedad_h", "ejecucion_id", "resumen_final",
             "sha256", "bytes", "ruta", "huella_contenido")


class ErrorInforme(Exception):
    pass


def _rel(ruta: Path, raiz: Path) -> str:
    try:
        return ruta.resolve().relative_to(raiz.resolve()).as_posix()
    except ValueError:
        return "<resultados>/" + "/".join(ruta.parts[-2:])


def _fecha(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def revisar_ejecucion(ruta: Path, raiz: Path, head: str | None, max_h: float, ahora: datetime) -> tuple[dict, list]:
    """Convierte un resultado.json del ejecutor en una entrada del informe y decide su estado."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if datos.get("formato") != "cauce.jarvis.run-result/1":
        raise ErrorInforme(f"{_rel(ruta, raiz)} no es un resultado del ejecutor")
    motivos = []
    integra = True
    for clave in ("salida", "junit"):
        info = datos.get(clave) or {}
        archivo = ruta.parent / str(info.get("archivo", ""))
        if not info.get("sha256") or not archivo.is_file() or sha256_archivo(archivo) != info["sha256"]:
            integra = False
            motivos.append(f"la evidencia {clave} falta o no coincide con su huella")
    commit = (datos.get("git") or {}).get("commit")
    if not head or commit != head:
        motivos.append("se ejecutó sobre otro commit")
    if not (datos.get("git") or {}).get("limpio"):
        motivos.append("se ejecutó con cambios locales sin confirmar")
    integ = datos.get("integridad") or {}
    if not (integ.get("git_sin_cambios") and integ.get("data_sin_cambios")):
        motivos.append("la ejecución cambió el repositorio o data/")
    fin = _fecha(datos.get("fin", ""))
    antig = round((ahora - fin).total_seconds() / 3600, 2) if fin else None
    if antig is None or antig > max_h or antig < -0.1:
        motivos.append(f"tiene más de {max_h:g} h o su fecha no es válida")
    entrada = {
        "suite": datos.get("suite"),
        "estado": "VERIFICADO" if not motivos else "REPORTADO",
        "motivos_no_verificado": motivos,
        "ejecucion_id": datos.get("id"),
        "resultado": datos.get("resultado"),
        "codigo_salida": datos.get("codigo_salida"),
        "inicio": datos.get("inicio"),
        "fin": datos.get("fin"),
        "duracion_s": datos.get("duracion_s"),
        "antiguedad_h": antig,
        "commit": commit,
        "comando": datos.get("comando"),
        "limites": datos.get("limites"),
        "conteo": datos.get("conteo"),
        "bases_de_datos": datos.get("bases_de_datos") or ["sqlite"],
        "salida_truncada": bool((datos.get("salida") or {}).get("truncada")),
        "resumen_final": (datos.get("salida") or {}).get("resumen_final") or "",
        "avisos": datos.get("avisos") or [],
    }
    evid = [{"tipo": "resultado_ejecucion", "ruta": _rel(ruta, raiz), "sha256": sha256_archivo(ruta),
             "estado": entrada["estado"]}]
    if integra:
        for clave, tipo in (("salida", "salida_pruebas"), ("junit", "junit")):
            evid.append({"tipo": tipo, "ruta": _rel(ruta.parent / datos[clave]["archivo"], raiz),
                         "sha256": datos[clave]["sha256"], "estado": entrada["estado"]})
    return entrada, evid


def ultimas_ejecuciones(dir_resultados: Path) -> list[Path]:
    """La última ejecución de cada suite en resultados/jarvis/ (por nombre: empieza con la fecha)."""
    if not dir_resultados.is_dir():
        return []
    ultimas: dict[str, Path] = {}
    for d in sorted(p for p in dir_resultados.iterdir() if p.is_dir()):
        r = d / "resultado.json"
        if r.is_file():
            ultimas[d.name.split("-", 1)[-1]] = r
    return [ultimas[k] for k in sorted(ultimas)]


def _historicos(decl: dict, raiz: Path) -> tuple[list, list]:
    salida, evid = [], []
    for h in decl.get("historicos", []):
        fuente = raiz / h["fuente"]
        existe = fuente.is_file()
        _, commit_fuente = git(raiz, "log", "-1", "--format=%H", "--", h["fuente"])
        resumen = h.get("resumen", "")
        if existe and h.get("extraer") == "ultima_linea_pytest":
            lineas = [x.strip("= ").strip() for x in fuente.read_text(encoding="utf-8", errors="replace").splitlines()
                      if re.search(r"\b(passed|failed)\b", x)]
            resumen = lineas[-1] if lineas else resumen
        salida.append({"id": h["id"], "estado": "SIMULADO" if h.get("naturaleza") == "laboratorio" else "REPORTADO",
                       "naturaleza": h.get("naturaleza", "pruebas_automatizadas"),
                       "descripcion": h["descripcion"], "fuente": h["fuente"], "fuente_existe": existe,
                       "commit_fuente": commit_fuente if re.fullmatch(r"[0-9a-f]{40}", commit_fuente or "") else None,
                       "resumen": resumen, "ejecutado_por_este_informe": False})
        if existe:
            evid.append({"tipo": "historico", "ruta": h["fuente"], "sha256": sha256_archivo(fuente),
                         "estado": salida[-1]["estado"]})
    return salida, evid


def _item(x: dict, estado_por_omision: str) -> dict:
    estado = x.get("estado", estado_por_omision)
    if estado not in ESTADOS or estado == "VERIFICADO":
        raise ErrorInforme(f"declaración {x.get('id')!r}: estado {estado!r} no permitido en una declaración "
                           "(VERIFICADO solo lo produce el ejecutor)")
    return {"id": x["id"], "estado": estado, "descripcion": x["descripcion"],
            **({"como_verificar": x["como_verificar"]} if x.get("como_verificar") else {}),
            **({"fuente": x["fuente"]} if x.get("fuente") else {})}


def _entorno(bases: set[str]) -> dict:
    nombre_ci = "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else (
        "ci" if os.environ.get("CI") else None)
    return {"sistema": platform.system() or "desconocido", "version_sistema": platform.release() or "",
            "arquitectura": platform.machine() or "", "python": platform.python_version(),
            "implementacion": platform.python_implementation(), "bases_de_datos": sorted(bases) or ["sqlite"],
            "ci": nombre_ci is not None, "nombre_ci": nombre_ci}


def huella(informe: dict) -> str:
    def limpiar(o):
        if isinstance(o, dict):
            return {k: limpiar(v) for k, v in o.items() if k not in VOLATILES}
        if isinstance(o, list):
            return [limpiar(x) for x in o]
        return o
    return hashlib.sha256(json_canonico(limpiar(informe)).encode("utf-8")).hexdigest()


def cargar_esquema(raiz: Path = RAIZ) -> dict:
    return json.loads((raiz / DIR_JARVIS / "report.schema.json").read_text(encoding="utf-8"))


def construir(raiz: Path = RAIZ, ejecuciones: list[Path] | None = None, anexos: list[Path] | None = None,
              max_h: float = ANTIGUEDAD_MAX_H, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    g = estado_git(raiz)
    decl = json.loads((raiz / DIR_JARVIS / "declaraciones.json").read_text(encoding="utf-8"))
    if ejecuciones is None:
        ejecuciones = ultimas_ejecuciones(raiz / "resultados" / "jarvis")
    pruebas, evidencia, bases = [], [], set()
    for r in ejecuciones:
        entrada, ev = revisar_ejecucion(Path(r), raiz, g["commit"], max_h, ahora)
        pruebas.append(entrada)
        evidencia += ev
        if entrada["estado"] == "VERIFICADO":
            bases.update(entrada["bases_de_datos"])
    historicos, ev_h = _historicos(decl, raiz)
    evidencia += ev_h
    simulaciones = [_item(x, "SIMULADO") for x in decl.get("simulaciones", [])]
    pendientes = [_item(x, "PENDIENTE") for x in decl.get("pendientes", [])]
    for a in anexos or []:
        extra = json.loads(Path(a).read_text(encoding="utf-8"))
        simulaciones += [_item(x, "SIMULADO") for x in extra.get("simulaciones", [])]
        pendientes += [_item(x, "PENDIENTE") for x in extra.get("pendientes", [])]
    bloqueos = [_item(x, "PENDIENTE") for x in decl.get("bloqueos", [])]
    verificadas = [p for p in pruebas if p["estado"] == "VERIFICADO"]
    if not verificadas:
        global_ = "SIN_VERIFICACION_RECIENTE"
    elif any(p["resultado"] != "APROBADA" for p in verificadas):
        global_ = "FALLIDO"
    elif not g["limpio"]:
        global_ = "APROBADO_CON_CAMBIOS_LOCALES"
    else:
        global_ = "APROBADO"
    conteo = {e: 0 for e in ESTADOS}
    for grupo in (pruebas, historicos, simulaciones, pendientes, bloqueos):
        for x in grupo:
            conteo[x["estado"]] += 1
    informe = {
        "formato": FORMATO,
        "proyecto": {"id": PROYECTO_ID, "nombre": decl.get("nombre", "Cauce Monitor Engine")},
        "version": version_proyecto(),
        "generado_en": ahora.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generado_por": "python -m monitoreo.interop informe",
        "git": g,
        "integracion": relacion_con_fijado(raiz, g),
        "entorno": _entorno(bases),
        "pruebas": pruebas,
        "resultados_historicos": historicos,
        "simulaciones": simulaciones,
        "pendientes": pendientes,
        "bloqueos": bloqueos,
        "proximos_pasos": [{"id": x["id"], "descripcion": x["descripcion"], "prioridad": x.get("prioridad", "media")}
                           for x in decl.get("proximos_pasos", [])],
        "evidencia": evidencia,
        "resumen": {
            "estado_global": global_,
            "suites_verificadas": len(verificadas),
            "suites_aprobadas": sum(1 for p in verificadas if p["resultado"] == "APROBADA"),
            "conteo_estados": conteo,
            "alcance": ("APROBADO indica solo que las suites listadas pasaron en este commit y entorno. No es "
                        "integración con Jarvis, ni validación en Windows físico, ni autorización de uso real."),
        },
        "huella_excluye": list(VOLATILES),
    }
    informe = sanear(informe, raiz)
    informe["huella_contenido"] = huella(informe)
    return informe


def comprobar(informe: dict, raiz: Path = RAIZ) -> list[str]:
    """Errores de esquema + datos sensibles + huella. Vacío = el informe se puede entregar."""
    errores = esquema.validar(informe, cargar_esquema(raiz))
    errores += [f"dato sensible: {h}" for h in buscar_sensibles(informe, raiz=raiz)]
    if isinstance(informe, dict) and informe.get("huella_contenido") != huella(informe):
        errores.append("huella_contenido no coincide con el contenido")
    return errores


def exportar(destino: Path, **kw) -> dict:
    raiz = kw.get("raiz", RAIZ)
    informe = construir(**kw)
    errores = comprobar(informe, raiz)
    if errores:
        raise ErrorInforme("el informe no se escribe porque no pasa sus comprobaciones:\n  " + "\n  ".join(errores))
    escribir_json(Path(destino), informe)
    return informe


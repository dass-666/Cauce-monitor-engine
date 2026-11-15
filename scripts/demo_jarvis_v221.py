"""Demostración v2.2.1: lo que haría Jarvis con Cauce, de punta a punta, dos veces.

    python scripts/demo_jarvis_v221.py [--suite humo] [--con-postgres] [--salida docs/resultados/v2.2.1/demo.json]

Pasos (Fase 5 de la misión):
  1. inventario  2. manifiesto (lista blanca)  3. ejecutar una suite pequeña permitida
  4. informe JSON  5. validarlo contra el esquema  6. SIMULAR la consulta de Jarvis
  7. repetir 3-6 y comparar la huella (idempotencia)
  y además intentar pedir un comando no permitido (debe negarse con código 2).

La "consulta de Jarvis" es una SIMULACIÓN escrita aquí: no es código de Jarvis ni lo sustituye. Se
limita a lo que un supervisor debería comprobar antes de confiar en un informe. Todo pasa por la
línea de comandos (subprocess), igual que lo haría un proceso externo.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from monitoreo.interop import esquema  # noqa: E402
from monitoreo.interop.comun import escribir_json, estado_git  # noqa: E402
from monitoreo.interop.saneado import sanear  # noqa: E402


def cli(*args: str, timeout: int = 3600) -> tuple[int, dict, float]:
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "monitoreo.interop", *args], cwd=RAIZ, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    ultima = (r.stdout.strip().splitlines() or ["{}"])[-1]
    return r.returncode, json.loads(ultima), round(time.monotonic() - t0, 2)


def consulta_jarvis_simulada(ruta_informe: Path, esquema_informe: dict, commit_esperado: str) -> dict:
    """SIMULACIÓN: cómo un supervisor externo debería leer el informe (no se fía de lo que dice)."""
    inf = json.loads(ruta_informe.read_text(encoding="utf-8"))
    comprobaciones = {
        "esquema_valido": esquema.validar(inf, esquema_informe) == [],
        "proyecto_correcto": inf.get("proyecto", {}).get("id") == "cauce-monitor-engine",
        "commit_es_el_actual": inf.get("git", {}).get("commit") == commit_esperado,
        "hay_pruebas_verificadas": inf["resumen"]["suites_verificadas"] > 0,
        "historicos_no_son_verificados": all(h["estado"] != "VERIFICADO" and not h["ejecutado_por_este_informe"]
                                             for h in inf["resultados_historicos"]),
        "verificadas_con_evidencia": all(
            any(e["tipo"] == "junit" and e["estado"] == "VERIFICADO" for e in inf["evidencia"])
            for p in inf["pruebas"] if p["estado"] == "VERIFICADO"),
    }
    apto = all(comprobaciones.values()) and inf["resumen"]["estado_global"] == "APROBADO"
    return {
        "estado": "SIMULADO",
        "comprobaciones": comprobaciones,
        "decision_simulada": "APTO_PARA_REVISION_HUMANA" if apto else "NO_APTO",
        "nota": "Aun siendo apto, Jarvis no fusiona ni despliega: solo informa a una persona.",
        "pendientes_que_jarvis_mostraria": [p["id"] for p in inf["pendientes"]],
        "estado_global": inf["resumen"]["estado_global"],
        "huella_contenido": inf["huella_contenido"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="humo")
    ap.add_argument("--con-postgres", action="store_true")
    ap.add_argument("--salida", default=str(RAIZ / "resultados" / "jarvis" / "demo_jarvis_v221.json"))
    a = ap.parse_args()
    esquema_informe = json.loads((RAIZ / "jarvis" / "report.schema.json").read_text(encoding="utf-8"))
    g = estado_git(RAIZ)
    pasos, rondas, comprobaciones = [], [], {}

    c, inv, d = cli("inventario")
    pasos.append({"paso": "1-inventario", "codigo": c, "duracion_s": d, "commit": inv["git"]["commit"],
                  "ramas": len(inv["ramas"]), "migraciones": inv["migraciones"]["cabezas"],
                  "archivos_de_prueba": inv["pruebas"]["archivos"]})
    c, suites, d = cli("suites")
    pasos.append({"paso": "2-manifiesto", "codigo": c, "duracion_s": d, "suites": [s["id"] for s in suites["suites"]]})

    for ronda in (1, 2):
        extra = ["--con-postgres"] if a.con_postgres else []
        c, ej, d = cli("ejecutar", "--suite", a.suite, *extra)
        ruta_inf = RAIZ / "resultados" / "jarvis" / f"informe_ronda{ronda}.json"
        resultado = RAIZ / "resultados" / "jarvis" / ej["id"] / "resultado.json"
        ci, inf, di = cli("informe", "--ejecucion", str(resultado), "--salida", str(ruta_inf))
        cv, val, dv = cli("validar", str(ruta_inf))
        consulta = consulta_jarvis_simulada(ruta_inf, esquema_informe, g["commit"])
        bases = json.loads(resultado.read_text(encoding="utf-8"))["bases_de_datos"]
        rondas.append({"ronda": ronda, "ejecutar": {"codigo": c, "resultado": ej["resultado"], "conteo": ej["conteo"],
                                                    "duracion_s": ej["duracion_s"], "bases_de_datos": bases,
                                                    "integridad": ej["integridad"]},
                       "informe": {"codigo": ci, "estado_global": inf.get("estado_global"),
                                   "conteo_estados": inf.get("conteo_estados"), "duracion_s": di},
                       "validar": {"codigo": cv, "valido": val.get("valido"), "duracion_s": dv},
                       "consulta_jarvis": consulta})

    c, neg, d = cli("ejecutar", "--suite", "humo && del /s *")
    comprobaciones["comando_no_permitido_rechazado"] = c == 2 and neg.get("error") == "SOLICITUD_NO_PERMITIDA"
    comprobaciones["inventario_ok"] = pasos[0]["codigo"] == 0 and pasos[0]["commit"] == g["commit"]
    comprobaciones["manifiesto_ok"] = pasos[1]["codigo"] == 0 and a.suite in pasos[1]["suites"]
    for r in rondas:
        n = r["ronda"]
        comprobaciones[f"ronda{n}_suite_aprobada"] = r["ejecutar"]["codigo"] == 0
        comprobaciones[f"ronda{n}_sin_cambios_en_repo_ni_data"] = all(r["ejecutar"]["integridad"].values())
        comprobaciones[f"ronda{n}_informe_valido"] = r["informe"]["codigo"] == 0 and r["validar"]["valido"] is True
        comprobaciones[f"ronda{n}_consulta_apta"] = r["consulta_jarvis"]["decision_simulada"] == "APTO_PARA_REVISION_HUMANA"
    comprobaciones["idempotente_misma_huella"] = (rondas[0]["consulta_jarvis"]["huella_contenido"] ==
                                                 rondas[1]["consulta_jarvis"]["huella_contenido"])
    comprobaciones["idempotente_mismo_conteo"] = rondas[0]["ejecutar"]["conteo"] == rondas[1]["ejecutar"]["conteo"]
    if a.con_postgres:
        comprobaciones["postgres_usado"] = all("postgresql" in r["ejecutar"]["bases_de_datos"] for r in rondas)

    ok = sum(1 for v in comprobaciones.values() if v)
    res = {"version": "2.2.1-dev", "demostracion": "jarvis-v2.2.1", "suite": a.suite,
           "plataforma": platform.system(), "python": platform.python_version(),
           "commit": g["commit"], "arbol_limpio": g["limpio"], "pasos": pasos, "rondas": rondas,
           "comprobaciones": comprobaciones, "resultado": f"{ok}/{len(comprobaciones)}",
           "nota": ("La consulta de Jarvis es SIMULADA por este script. Si el árbol tenía cambios locales, las "
                    "ejecuciones salen como REPORTADO y la consulta como NO_APTO: es lo esperado.")}
    escribir_json(Path(a.salida), sanear(res, RAIZ))
    print(json.dumps({"resultado": res["resultado"], "fallidas": [k for k, v in comprobaciones.items() if not v],
                      "salida": sanear(a.salida, RAIZ)}, ensure_ascii=False))
    return 0 if ok == len(comprobaciones) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Comandos para Jarvis (v2.2.1).  python -m monitoreo.interop <comando>

  inventario [--salida F]            estado del repositorio (solo lectura)
  suites                             suites permitidas del manifiesto (lista blanca)
  ejecutar --suite ID [--con-postgres] [--tiempo-s N] [--salida-dir D]
                                     ejecuta UNA suite permitida con límites y entorno aislado
  informe [--ejecucion R ...] [--anexo A ...] [--salida F] [--max-antiguedad-h H]
                                     exporta el informe JSON (solo lectura) y lo valida antes de escribirlo
  validar ARCHIVO                    valida un informe contra jarvis/report.schema.json
  ciclo --suite ID [...]             ejecutar + informe + validar (lo que haría Jarvis)

Todo lo que se imprime es JSON en una línea al final (para que una máquina lo lea) y no contiene
rutas privadas ni secretos. Códigos de salida: ver monitoreo/interop/ejecutor.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ejecutor, informe, inventario, manifiesto
from .comun import RAIZ, ErrorSolicitud, escribir_json
from .saneado import sanear


def _imprimir(obj) -> None:
    sys.stdout.write(json.dumps(sanear(obj, RAIZ), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m monitoreo.interop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inventario")
    p.add_argument("--salida")
    p.add_argument("--principal", default="master")
    sub.add_parser("suites")
    for nombre in ("ejecutar", "ciclo"):
        p = sub.add_parser(nombre)
        p.add_argument("--suite", required=True)
        p.add_argument("--con-postgres", action="store_true")
        p.add_argument("--tiempo-s", type=int)
        p.add_argument("--salida-dir")
        if nombre == "ciclo":
            p.add_argument("--salida")
    p = sub.add_parser("informe")
    p.add_argument("--ejecucion", action="append")
    p.add_argument("--anexo", action="append")
    p.add_argument("--salida", default=str(RAIZ / "resultados" / "jarvis" / "informe.json"))
    p.add_argument("--max-antiguedad-h", type=float, default=informe.ANTIGUEDAD_MAX_H)
    p = sub.add_parser("validar")
    p.add_argument("archivo")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "inventario":
            inv = inventario.construir(RAIZ, a.principal)
            if a.salida:
                escribir_json(Path(a.salida), inv)
                _imprimir({"ok": True, "archivo": a.salida, "commit": inv["git"]["commit"]})
            else:
                _imprimir(inv)
            return 0
        if a.cmd == "suites":
            m = manifiesto.cargar(RAIZ)
            _imprimir({"proyecto": m.proyecto, "suites": [
                {"id": s.id, "descripcion": s.descripcion, "tiempo_s": s.tiempo_s, "postgres": s.postgres,
                 "etiquetas": list(s.etiquetas), "duracion_referencia_s": s.duracion_referencia_s}
                for s in m.suites.values()]})
            return 0
        if a.cmd in ("ejecutar", "ciclo"):
            codigo, res = ejecutor.ejecutar(a.suite, salida=Path(a.salida_dir) if a.salida_dir else None,
                                            con_postgres=a.con_postgres, tiempo_s=a.tiempo_s)
            if a.cmd == "ejecutar":
                _imprimir({k: res[k] for k in ("id", "suite", "resultado", "codigo_salida", "duracion_s", "conteo",
                                               "integridad", "avisos")})
                return codigo
            dir_res = Path(a.salida_dir).resolve() if a.salida_dir else RAIZ / "resultados" / "jarvis"
            destino = Path(a.salida) if a.salida else dir_res / "informe.json"
            inf = informe.exportar(destino, ejecuciones=[dir_res / res["id"] / "resultado.json"])
            _imprimir({"resultado": res["resultado"], "codigo_salida": codigo, "informe": str(destino),
                       "estado_global": inf["resumen"]["estado_global"], "huella_contenido": inf["huella_contenido"]})
            return codigo
        if a.cmd == "informe":
            inf = informe.exportar(Path(a.salida), ejecuciones=[Path(x) for x in a.ejecucion] if a.ejecucion else None,
                                   anexos=[Path(x) for x in a.anexo or []], max_h=a.max_antiguedad_h)
            _imprimir({"ok": True, "archivo": a.salida, "estado_global": inf["resumen"]["estado_global"],
                       "conteo_estados": inf["resumen"]["conteo_estados"], "huella_contenido": inf["huella_contenido"]})
            return 0
        if a.cmd == "validar":
            datos = json.loads(Path(a.archivo).read_text(encoding="utf-8"))
            errores = informe.comprobar(datos, RAIZ)
            _imprimir({"valido": not errores, "errores": errores})
            return 0 if not errores else 1
    except ErrorSolicitud as e:
        _imprimir({"error": "SOLICITUD_NO_PERMITIDA", "detalle": str(e)})
        return ejecutor.SALIDA_SOLICITUD
    except ejecutor.ErrorOcupado:
        _imprimir({"error": "OCUPADO", "detalle": "otra ejecución está en curso en esa carpeta de resultados"})
        return ejecutor.SALIDA_OCUPADO
    except informe.ErrorInforme as e:
        _imprimir({"error": "INFORME_INVALIDO", "detalle": str(e)})
        return ejecutor.SALIDA_ERROR
    return ejecutor.SALIDA_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

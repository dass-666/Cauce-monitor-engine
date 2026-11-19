"""RC1 · Lista de DEMOSTRACIÓN con seis clientes FICTICIOS (Excel y CSV).

Nombres inventados, teléfonos del prefijo de laboratorio 555, correos .test y registros previos ficticios
(00450001…) que el portal de laboratorio de la RC1 reconoce. Las fechas aceptables se calculan desde HOY para
que la lista siga siendo válida: el «mes de la demostración» es el primer día de dentro de dos meses (un mes
sin días abiertos en el calendario del laboratorio, para que la única coincidencia sea la que se abre).

Qué debe pasar con cada fila (lo comprueba scripts/recorrido_rc1.py):
  1 Ana      completa                -> con consentimiento entra al plan; el día abierto le corresponde
  2 Bruno    completa                -> con consentimiento entra al plan; su rango NO incluye ese día: espera
  3 Carmen   completa                -> SIN consentimiento registrado: queda bloqueada
  4 Daniela  falta fecha de nacimiento -> incompleta, bloqueada
  5 Esteban  faltan teléfono y correo  -> incompleto, bloqueado
  6 Fabiola  completa (constancia)     -> con consentimiento entra al plan; espera (otro trámite)
La columna «Contraseña del portal» trae un valor ficticio para demostrar que se DESCARTA, y «Consentimiento»
dice «Sí» para demostrar que importar no equivale a consentir.

Uso: python scripts/demo_rc1_datos.py [carpeta]   (por omisión docs/rc1)
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitoreo.captura.xlsx_escritura import libro  # noqa: E402

ENCABEZADOS = ["Nombre completo", "Fecha de nacimiento", "Registro previo", "Teléfono", "Correo", "Región", "Sucursal",
               "Trámite", "Fecha solicitada", "Acepta desde", "Acepta hasta", "Prioridad", "Contraseña del portal",
               "Consentimiento"]


def mes_demo(hoy: date | None = None) -> date:
    hoy = hoy or date.today()
    y, m = hoy.year + (hoy.month + 1) // 12, (hoy.month + 1) % 12 + 1
    return date(y, m, 1)


def dia_coincidencia(mes: date) -> date:
    """Un día hábil hacia la mitad del mes (el que el administrador abre en el calendario del laboratorio)."""
    d = mes + timedelta(days=13)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def filas(mes: date) -> list[list]:
    x = dia_coincidencia(mes)
    fin = mes + timedelta(days=26)
    f = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731  (como lo escribiría una persona en México)
    return [
        ["Ana Ficticia Uno", "15/03/1985", "00450001", "5550100001", "ana.uno@ejemplo.test", "Norte", "NOR-1", "Alta",
         f(x), f(mes), f(fin), 1, "no-usar-1234", "Sí"],
        ["Bruno Ficticio Dos", "02/07/1990", "00450002", "5550100002", "bruno.dos@ejemplo.test", "Norte", "NOR-1", "Alta",
         f(x + timedelta(days=7)), f(x + timedelta(days=5)), f(fin), 2, "", "Sí"],
        ["Carmen Ficticia Tres", "23/11/1978", "00450003", "5550100003", "carmen.tres@ejemplo.test", "Norte", "NOR-2",
         "Renovación", f(x), f(mes), f(fin), 3, "", "Sí"],
        ["Daniela Ficticia Cuatro", "", "00450004", "5550100004", "daniela.cuatro@ejemplo.test", "Norte", "NOR-1", "Alta",
         f(x), f(mes), f(fin), 3, "", ""],
        ["Esteban Ficticio Cinco", "09/06/1983", "00450005", "", "", "Norte", "NOR-1", "Alta",
         f(x), f(mes), f(fin), 3, "", ""],
        ["Fabiola Ficticia Seis", "12/09/1975", "00450006", "5550100006", "fabiola.seis@ejemplo.test", "Norte", "NOR-1",
         "Constancia", f(x), f(mes), f(fin), 3, "", "Sí"],
    ]


def generar(carpeta: Path, hoy: date | None = None) -> dict:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    mes = mes_demo(hoy)
    datos = filas(mes)
    xlsx = carpeta / "clientes_ficticios_rc1.xlsx"
    xlsx.write_bytes(libro({"Instrucciones": [["Lista de DEMOSTRACIÓN con datos ficticios. Importa la hoja «Clientes»."]],
                            "Clientes": [ENCABEZADOS, *datos]}))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ENCABEZADOS)
    w.writerows(datos)
    csv_p = carpeta / "clientes_ficticios_rc1.csv"
    csv_p.write_text(buf.getvalue(), encoding="utf-8-sig")
    return {"xlsx": str(xlsx), "csv": str(csv_p), "mes": mes.strftime("%Y-%m"), "dia": dia_coincidencia(mes).isoformat(),
            "generado": (hoy or date.today()).isoformat()}


if __name__ == "__main__":
    print(generar(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "rc1"))

"""RC1 · Escritor mínimo de libros .xlsx (biblioteca estándar, sin dependencias).

Sirve para la PLANTILLA que descarga el administrador desde «Importar» y para generar los archivos de
demostración y de prueba. Escribe texto (como texto: los identificadores conservan sus ceros), números,
fechas (con formato de fecha) y, solo para pruebas, fórmulas (`Formula("...")`), que el importador debe
rechazar.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date
from xml.sax.saxutils import escape


class Formula(str):
    """Celda con fórmula (solo para comprobar que el importador NO las ejecuta)."""


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _celda(ref: str, v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, Formula):
        return f'<c r="{ref}"><f>{escape(str(v).lstrip("="))}</f><v>0</v></c>'
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"><v>{int(v)}</v></c>'
    if isinstance(v, date):
        serie = (v - date(1899, 12, 30)).days
        return f'<c r="{ref}" s="1"><v>{serie}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr" s="2"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def libro(hojas: dict[str, list[list]], columnas_texto: dict[str, list[int]] | None = None) -> bytes:
    """hojas: {nombre: filas}. `columnas_texto` {hoja: [índices]}: columnas con formato Texto (@), para que
    Excel no convierta en número lo que se teclee después (p. ej. «00450001» perdería sus ceros).
    Devuelve el .xlsx en bytes."""
    columnas_texto = columnas_texto or {}
    buf = io.BytesIO()
    nombres = list(hojas)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(nombres))) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(nombres))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(nombres)))
                   + f'<Relationship Id="rId{len(nombres) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        # estilos: 0 general, 1 fecha DD/MM/AAAA, 2 texto (@)
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
                   '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                   '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                   "</styleSheet>")
        for i, n in enumerate(nombres):
            filas = "".join(f'<row r="{r + 1}">' + "".join(_celda(f"{_col(c)}{r + 1}", v) for c, v in enumerate(f)) + "</row>"
                            for r, f in enumerate(hojas[n]))
            cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="22" style="2" customWidth="1"/>'
                           for c in sorted(columnas_texto.get(n, [])))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       + (f"<cols>{cols}</cols>" if cols else "") + f"<sheetData>{filas}</sheetData></worksheet>")
    return buf.getvalue()


PLANTILLA_ENCABEZADOS = ["Nombre completo", "Operación", "Trámite", "Registro previo", "Fecha de nacimiento",
                         "Teléfono", "Correo", "Folio de la cita anterior", "Región", "Sucursal",
                         "Fecha solicitada", "Acepta desde", "Acepta hasta", "Prioridad"]
# Columnas que deben ser TEXTO en Excel: identificadores (ceros iniciales) y teléfono
PLANTILLA_TEXTO = [PLANTILLA_ENCABEZADOS.index(c) for c in ("Registro previo", "Teléfono", "Folio de la cita anterior")]


def plantilla() -> bytes:
    """Plantilla vacía con los encabezados que el importador reconoce sin asignación manual."""
    return libro({"Clientes": [PLANTILLA_ENCABEZADOS]}, {"Clientes": PLANTILLA_TEXTO})

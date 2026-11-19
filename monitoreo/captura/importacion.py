"""RC1 · Importación administrativa de expedientes desde un archivo .csv o .xlsx.

Flujo: el administrador sube el archivo -> elige la hoja (Excel) -> revisa la VISTA PREVIA (columnas
reconocidas, asignación a campos, resultado previsto de cada fila con sus errores) -> importa. Cada fila
válida crea un expediente por la MISMA vía que el alta manual (`expedientes.crear`: mismas validaciones,
mismos estados, misma detección de duplicados). Las filas con errores quedan en el reporte del lote.

Reglas de seguridad (todas se aplican en el servidor):
- Solo .csv y .xlsx, como máximo `MAX_BYTES`, `MAX_FILAS` filas y `MAX_COLUMNAS` columnas. Un .xlsx se lee
  como ZIP + XML con la biblioteca estándar: sin macros (`vbaProject.bin` se rechaza), sin vínculos
  externos, sin DTD/entidades XML (se rechaza cualquier `<!DOCTYPE`), con límite de tamaño descomprimido.
- Las fórmulas de Excel NUNCA se ejecutan ni se usa su último valor guardado: la celda se rechaza con
  su explicación («pega como valores»).
- Un texto que empieza por `=`, `+`, `-`, `@` o un tabulador (forma de fórmula/comando, «inyección CSV»),
  o que contiene rutas o llamadas a programas, se rechaza.
- Las columnas que parecen contraseñas, claves de acceso, NIP o tokens se DESCARTAN: no se muestran en la
  vista previa, no se pueden asignar a ningún campo y no se guardan.
- Importar NO es consentir: una columna «consentimiento» del archivo se ignora (se avisa). El expediente
  queda bloqueado (INCOMPLETO, falta «consentimiento») hasta que se registre uno con su evidencia.

Idempotencia: cada fila tiene una CLAVE de persona (identificador externo, o nombre + nacimiento + trámite,
o, si falta el nacimiento, nombre + trámite + contactos). `importacion_filas.clave_creado` es UNIQUE: la
misma persona del archivo no crea dos expedientes aunque el archivo se cargue dos veces o a la vez. Además
se respetan las reglas de siempre (huella de duplicado activa, identificador externo único).

No se guarda el archivo: solo su nombre, su huella SHA-256, la hoja, la asignación de columnas y, por fila,
el resultado, los NOMBRES de campos con error y el mensaje (nunca los valores personales).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import security
from ..models import CalendarioDia, Expediente, Importacion, ImportacionFila, Region
from . import expedientes as ex
from . import validacion as val

MAX_BYTES = 5 * 1024 * 1024
MAX_FILAS = 5000
MAX_COLUMNAS = 60
MAX_XML_BYTES = 60 * 1024 * 1024          # descomprimido (protección contra «bombas ZIP»)
VISTA_FILAS = 300                         # filas que se devuelven en la vista previa (el resumen cuenta todas)
FORMATOS_FECHA = ("DMA", "MDA")           # DD/MM/AAAA (México) o MM/DD/AAAA; AAAA-MM-DD siempre se acepta

CREADO, YA_EXISTE, RECHAZADO, REPETIDA = "CREADO", "YA_EXISTE", "RECHAZADO", "REPETIDA_EN_ARCHIVO"

# campo -> (etiqueta, encabezados habituales ya normalizados)
CAMPOS: dict[str, tuple[str, tuple[str, ...]]] = {
    "nombre": ("Nombre completo", ("nombre", "nombre completo", "nombre del trabajador", "nombre del cliente",
                                   "trabajador", "cliente", "titular", "persona", "nombre y apellidos")),
    "nombres": ("Nombre(s)", ("nombres", "nombre s", "primer nombre")),
    "apellido_paterno": ("Apellido paterno", ("apellido paterno", "primer apellido", "paterno", "ap paterno")),
    "apellido_materno": ("Apellido materno", ("apellido materno", "segundo apellido", "materno", "ap materno")),
    "apellidos": ("Apellidos", ("apellidos",)),
    "fecha_nacimiento": ("Fecha de nacimiento", ("fecha de nacimiento", "fecha nacimiento", "nacimiento",
                                                 "f nacimiento", "fecha nac", "fec nac", "f nac")),
    "id_registro_previo": ("Registro o identificador previo", (
        "registro", "registro previo", "id registro", "id registro previo", "identificador", "identificador previo",
        "numero de registro", "no registro", "num registro", "no de registro", "numero registro",
        # etiqueta del formulario del laboratorio y nombres del propietario (RC1)
        "identificador de registro", "identificador de registro previo", "folio de registro", "folio de registro previo",
        "identificador de cliente", "identificador del cliente")),
    "id_externo": ("Identificador interno (opcional)", ("id externo", "id interno", "clave interna", "numero de empleado",
                                                        "no empleado", "num empleado", "id")),
    "telefono": ("Teléfono", ("telefono", "tel", "celular", "movil", "telefono celular", "telefono movil",
                              "telefono de contacto")),
    # Confirmaciones: solo se COMPARAN con el dato; no existen como columnas de la base de datos
    "telefono_confirmacion": ("Confirmación de teléfono (no se guarda)", (
        "confirmacion de telefono", "confirmacion del telefono", "confirmacion telefono", "confirmar telefono",
        "telefono confirmacion", "telefono confirmado", "confirma telefono", "confirma el telefono")),
    "correo": ("Correo", ("correo", "email", "e mail", "correo electronico", "mail", "correo de contacto")),
    "correo_confirmacion": ("Confirmación de correo (no se guarda)", (
        "confirmacion de correo", "confirmacion del correo", "confirmacion correo", "confirmar correo",
        "correo confirmacion", "confirmacion de correo electronico", "confirmacion del correo electronico",
        "confirma correo", "confirma el correo")),
    "region": ("Región", ("region", "zona")),
    "sucursal": ("Sucursal", ("sucursal", "oficina", "sede", "sucursal preferida")),
    "sucursales_alternativas": ("Sucursales alternativas", ("sucursales alternativas", "otras sucursales", "alternativas")),
    "tipo_tramite": ("Trámite", ("tramite", "tipo de tramite", "tipo tramite", "servicio")),
    "operacion": ("Operación (nueva o reprogramar)", ("operacion", "tipo de cita", "tipo de operacion",
                                                     "operacion a realizar")),
    "prioridad": ("Prioridad (1 a 5)", ("prioridad",)),
    "fecha_solicitada": ("Fecha solicitada por la persona", ("fecha solicitada", "fecha deseada", "fecha preferida",
                                                             "fecha pedida", "fecha de cita deseada")),
    "fechas_desde": ("Acepta desde", ("desde", "fecha desde", "acepta desde", "fecha inicial", "disponible desde",
                                      "rango desde", "fecha aceptable desde")),
    "fechas_hasta": ("Acepta hasta", ("hasta", "fecha hasta", "acepta hasta", "fecha final", "disponible hasta",
                                      "rango hasta", "fecha aceptable hasta")),
    "folio_anterior": ("Folio de la cita anterior", ("folio anterior", "folio cita anterior")),
}
CAMPOS_FECHA = ("fecha_nacimiento", "fecha_solicitada", "fechas_desde", "fechas_hasta")
CAMPOS_IDENT = ("id_registro_previo", "id_externo", "folio_anterior")
CAMPOS_TELEFONO = ("telefono", "telefono_confirmacion")
# Palabras que delatan un secreto: la columna se descarta siempre (no se muestra ni se guarda).
_PROHIBIDAS = ("contrasena", "password", "passwd", "clave de acceso", "clave acceso", "clave de identidad",
               "clave identidad", "nip", "pin", "token", "usuario del portal", "usuario portal", "secreto")
_CONSENTIMIENTO = ("consentimiento", "autorizacion", "autoriza", "acepto aviso", "aviso de privacidad", "firma")
_TRAMITES = {"alta": "alta", "alta de registro": "alta", "renovacion": "renovacion", "constancia": "constancia"}
_PELIGRO = re.compile(r"^[=+\-@\t\r]|(?i:\b(cmd|powershell|mshta|rundll32|wscript)\b)|(?i:file:/)|\\\\|\|")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class ErrorImportacion(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


@dataclass
class Celda:
    valor: object                      # str | float | bool | None
    tipo: str = "texto"                # texto | numero | fecha | bool | formula | error | vacia
    formato: str = ""                  # código de formato numérico (Excel), p. ej. "00000000"


@dataclass
class Libro:
    formato: str                       # csv | xlsx
    hojas: list[str]
    filas: dict[str, list[list[Celda]]] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def normalizar_encabezado(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def clasificar_encabezado(texto) -> tuple[str, str | None]:
    """-> (tipo, campo). tipo: CAMPO | PROHIBIDA | CONSENTIMIENTO | SIN_USO."""
    n = normalizar_encabezado(texto)
    if not n:
        return "SIN_USO", None
    for campo, (etiqueta, sinonimos) in CAMPOS.items():
        if n in sinonimos or n == normalizar_encabezado(etiqueta):      # también su propia etiqueta (plantilla)
            return "CAMPO", campo
    palabras = f" {n} "
    if any(f" {p} " in palabras for p in _PROHIBIDAS) or n.startswith("clave") and "interna" not in n:
        return "PROHIBIDA", None
    if any(f" {p} " in palabras for p in _CONSENTIMIENTO):       # palabras completas («confirmación» ≠ «firma»)
        return "CONSENTIMIENTO", None
    return "SIN_USO", None


# ====================================================================== lectura de archivos
def leer(nombre: str, contenido: bytes) -> Libro:
    if not contenido:
        raise ErrorImportacion(422, "El archivo está vacío")
    if len(contenido) > MAX_BYTES:
        raise ErrorImportacion(413, f"El archivo pasa de {MAX_BYTES // (1024 * 1024)} MB")
    ext = nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
    if ext == "xlsx":
        if not contenido.startswith(b"PK"):
            raise ErrorImportacion(422, "El archivo .xlsx no es un libro de Excel válido")
        return _leer_xlsx(contenido)
    if ext == "csv":
        if contenido.startswith(b"PK") or b"\x00" in contenido[:4096]:
            raise ErrorImportacion(422, "El archivo .csv contiene datos binarios: no es un texto separado por comas")
        return _leer_csv(contenido)
    if ext in ("xlsm", "xlsb", "xls", "xltm"):
        raise ErrorImportacion(415, "Formato no admitido: guarda el libro como .xlsx (sin macros) o .csv")
    raise ErrorImportacion(415, "Solo se admiten archivos .xlsx o .csv")


def _leer_csv(contenido: bytes) -> Libro:
    for cod in ("utf-8-sig", "cp1252"):
        try:
            texto = contenido.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.splitlines()[0] if texto.strip() else ""
    delim = max((";", ",", "\t", "|"), key=lambda d: primera.count(d))
    filas = []
    for i, f in enumerate(csv.reader(io.StringIO(texto), delimiter=delim)):
        if i > MAX_FILAS:
            raise ErrorImportacion(413, f"El archivo tiene más de {MAX_FILAS} filas: divídelo en varios")
        if len(f) > MAX_COLUMNAS:
            raise ErrorImportacion(413, f"Más de {MAX_COLUMNAS} columnas: deja solo las necesarias")
        filas.append([Celda(v, "texto") if v.strip() else Celda(None, "vacia") for v in f])
    lib = Libro("csv", ["CSV"], {"CSV": filas})
    if cod == "cp1252":
        lib.avisos.append("El archivo no estaba en UTF-8: se leyó como Windows-1252 (revisa acentos en la vista previa)")
    return lib


def _xml(z: zipfile.ZipFile, nombre: str, total: list[int]) -> ET.Element | None:
    try:
        info = z.getinfo(nombre)
    except KeyError:
        return None
    total[0] += info.file_size
    if total[0] > MAX_XML_BYTES:
        raise ErrorImportacion(413, "El libro de Excel es demasiado grande al descomprimirse")
    datos = z.read(info)
    if b"<!DOCTYPE" in datos or b"<!ENTITY" in datos:
        raise ErrorImportacion(422, "El libro contiene declaraciones XML no admitidas (DTD/entidades)")
    return ET.fromstring(datos)


_FECHA_FMT_ID = set(range(14, 23)) | set(range(45, 48)) | {27, 30, 36, 50, 57}


def _es_formato_fecha(codigo: str) -> bool:
    c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", codigo.lower())
    return bool(re.search(r"[dy]", c)) or ("m" in c and ("d" in c or "y" in c))


def _leer_xlsx(contenido: bytes) -> Libro:
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile:
        raise ErrorImportacion(422, "El archivo .xlsx está dañado o no es un libro de Excel") from None
    nombres = z.namelist()
    if len(nombres) > 500:
        raise ErrorImportacion(422, "El libro tiene demasiadas partes internas")
    if any(n.lower().endswith("vbaproject.bin") for n in nombres):
        raise ErrorImportacion(422, "El libro contiene macros: guárdalo como .xlsx sin macros")
    if any(n.startswith("/") or ".." in n.split("/") for n in nombres):
        raise ErrorImportacion(422, "El libro contiene rutas internas no válidas")
    total = [0]
    avisos = []
    if any(n.startswith("xl/externalLinks/") for n in nombres):
        avisos.append("El libro tenía vínculos a otros archivos: se ignoraron (no se abre nada externo)")
    wb = _xml(z, "xl/workbook.xml", total)
    if wb is None:
        raise ErrorImportacion(422, "El archivo .xlsx no tiene libro de trabajo")
    fecha1904 = any((p.get("date1904") or "").lower() in ("1", "true") for p in wb.iter(f"{{{_NS['m']}}}workbookPr"))
    rels = _xml(z, "xl/_rels/workbook.xml.rels", total)
    destinos = {}
    for r in (rels if rels is not None else []):
        t = r.get("Target", "")
        t = t.lstrip("/") if t.startswith("/") else "xl/" + t
        destinos[r.get("Id")] = t
    hojas = []
    for h in wb.iter(f"{{{_NS['m']}}}sheet"):
        if (h.get("state") or "visible") != "visible":
            continue
        hojas.append((h.get("name") or "Hoja", destinos.get(h.get(_R_ID))))
    if not hojas:
        raise ErrorImportacion(422, "El libro no tiene hojas visibles")
    compartidas = []
    ss = _xml(z, "xl/sharedStrings.xml", total)
    if ss is not None:
        for si in ss.findall("m:si", _NS):
            compartidas.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    formatos_celda: list[str] = []
    st = _xml(z, "xl/styles.xml", total)
    if st is not None:
        codigos = {int(n.get("numFmtId")): n.get("formatCode") or "" for n in st.iter(f"{{{_NS['m']}}}numFmt")}
        xfs = st.find("m:cellXfs", _NS)
        for xf in (xfs if xfs is not None else []):
            fid = int(xf.get("numFmtId") or 0)
            if fid in _FECHA_FMT_ID:
                formatos_celda.append("@fecha")
            elif fid == 49:
                formatos_celda.append("@")
            else:
                cod = codigos.get(fid, "")
                formatos_celda.append("@fecha" if cod and _es_formato_fecha(cod) else cod)
    lib = Libro("xlsx", [n for n, _ in hojas], {}, avisos)
    for nombre, ruta in hojas:
        lib.filas[nombre] = _leer_hoja(z, ruta, total, compartidas, formatos_celda, fecha1904) if ruta else []
    return lib


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _leer_hoja(z, ruta, total, compartidas, formatos, fecha1904) -> list[list[Celda]]:
    raiz = _xml(z, ruta, total)
    if raiz is None:
        return []
    m = f"{{{_NS['m']}}}"
    filas: list[list[Celda]] = []
    for row in raiz.iter(m + "row"):
        idx = int(row.get("r") or (len(filas) + 1)) - 1
        if idx > MAX_FILAS:
            raise ErrorImportacion(413, f"La hoja tiene más de {MAX_FILAS} filas: divídela en varios archivos")
        while len(filas) < idx:
            filas.append([])
        celdas: list[Celda] = []
        for c in row.findall(m + "c"):
            col = _col(c.get("r") or "") if c.get("r") else len(celdas)
            if col >= MAX_COLUMNAS:
                raise ErrorImportacion(413, f"Más de {MAX_COLUMNAS} columnas: deja solo las necesarias")
            while len(celdas) < col:
                celdas.append(Celda(None, "vacia"))
            t = c.get("t") or "n"
            s_idx = int(c.get("s") or 0)
            fmt = formatos[s_idx] if s_idx < len(formatos) else ""
            v = c.find(m + "v")
            if c.find(m + "f") is not None:
                celdas.append(Celda(None, "formula"))
            elif t == "s" and v is not None:
                k = int(v.text or 0)
                celdas.append(Celda(compartidas[k] if k < len(compartidas) else "", "texto"))
            elif t == "inlineStr":
                celdas.append(Celda("".join(x.text or "" for x in c.iter(m + "t")), "texto"))
            elif t == "str":
                celdas.append(Celda(v.text if v is not None else "", "texto"))
            elif t == "b":
                celdas.append(Celda((v.text or "0") == "1", "bool"))
            elif t == "e":
                celdas.append(Celda(v.text if v is not None else "#ERROR", "error"))
            elif v is not None and v.text not in (None, ""):
                try:
                    num = float(v.text)
                except ValueError:
                    celdas.append(Celda(v.text, "texto"))
                    continue
                if fmt == "@fecha":
                    base = date(1904, 1, 1) if fecha1904 else date(1899, 12, 30)
                    celdas.append(Celda((base + timedelta(days=int(num))).isoformat(), "fecha"))
                else:
                    celdas.append(Celda(num, "numero", fmt))
            else:
                celdas.append(Celda(None, "vacia"))
        filas.append(celdas)
    return filas


# ====================================================================== conversión de celdas a campos
def _texto(c: Celda) -> str:
    if c.tipo == "numero":
        x = float(c.valor)
        return str(int(x)) if x.is_integer() else str(x)
    return " ".join(str(c.valor if c.valor is not None else "").split())


def _fecha_texto(t: str, formato: str) -> date:
    t = t.strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", t)
    if m:
        if len(m[3]) != 4:
            raise ValueError("Escribe el año con cuatro dígitos")
        a, b = int(m[1]), int(m[2])
        dia, mes = (a, b) if formato == "DMA" else (b, a)
        return date(int(m[3]), mes, dia)
    raise ValueError("Fecha no reconocida: usa DD/MM/AAAA o AAAA-MM-DD")


def convertir(campo: str, c: Celda, formato_fecha: str, avisos: list[str]) -> tuple[object, str | None]:
    """-> (valor, error). El valor ya está listo para `validacion.validar` (texto o fecha ISO)."""
    if c.tipo == "vacia" or c.valor is None and c.tipo not in ("formula",):
        return None, None
    if c.tipo == "formula":
        return None, "La celda tiene una fórmula de Excel: Cauce no ejecuta fórmulas. Copia la columna y pégala como valores"
    if c.tipo == "error":
        return None, "La celda tiene un error de Excel"
    if campo in CAMPOS_FECHA:
        if c.tipo == "fecha":
            return c.valor, None
        if c.tipo == "numero":
            x = float(c.valor)
            if x.is_integer() and 1 <= x <= 2958465:          # número de serie de Excel sin formato de fecha
                avisos.append(f"{CAMPOS[campo][0]}: era un número de serie de Excel y se interpretó como fecha")
                return (date(1899, 12, 30) + timedelta(days=int(x))).isoformat(), None
            return None, "Fecha no válida"
        try:
            return _fecha_texto(_texto(c), formato_fecha).isoformat(), None
        except ValueError as e:
            return None, str(e) if "año" in str(e) or "usa" in str(e) else "Fecha no válida"
    if c.tipo == "numero":
        x = float(c.valor)
        if campo in CAMPOS_IDENT or campo in CAMPOS_TELEFONO:
            if not x.is_integer():
                return None, "Identificador no válido"
            t = str(int(x))
            if re.fullmatch(r"0+", c.formato or "") and len(c.formato) > len(t):
                return t.zfill(len(c.formato)), None                  # formato «00000000»: se respetan los ceros
            if campo in CAMPOS_IDENT:
                avisos.append(f"{CAMPOS[campo][0]}: Excel lo guardó como número; si llevaba ceros a la izquierda "
                              "se perdieron (formatea la columna como Texto)")
            return t, None
        return _texto(c), None
    if c.tipo == "bool":
        return None, "Valor no válido (verdadero/falso)"
    t = _texto(c)
    if not t:
        return None, None
    if campo in CAMPOS_TELEFONO:
        if re.fullmatch(r"\+?[\d ()\-.]{7,20}", t):
            return t, None
        return None, "Teléfono no válido"
    if _PELIGRO.search(t):
        return None, "El valor tiene forma de fórmula, ruta o comando y no se acepta"
    return t, None


# ====================================================================== mapeo y filas
def sugerir_mapeo(encabezados: list[str]) -> tuple[dict[str, str], list[dict]]:
    mapeo, info, usados = {}, [], set()
    for i, t in enumerate(encabezados):
        tipo, campo = clasificar_encabezado(t)
        if tipo == "CAMPO" and campo in usados:
            tipo, campo = "SIN_USO", None
        if campo:
            usados.add(campo)
            mapeo[str(i)] = campo
        info.append({"indice": i, "texto": str(t or "")[:64], "tipo": tipo, "campo_sugerido": campo})
    return mapeo, info


def _validar_mapeo(mapeo: dict, info: list[dict]) -> dict[int, str]:
    out: dict[int, str] = {}
    vistos = set()
    tipos = {x["indice"]: x["tipo"] for x in info}
    for k, campo in (mapeo or {}).items():
        if not campo:
            continue
        try:
            i = int(k)
        except (TypeError, ValueError):
            raise ErrorImportacion(422, "Asignación de columnas no válida") from None
        if i not in tipos:
            raise ErrorImportacion(422, f"La columna {i + 1} no existe en la hoja")
        if tipos[i] == "PROHIBIDA":
            raise ErrorImportacion(422, f"La columna {i + 1} parece contener contraseñas o claves: no se importa")
        if campo not in CAMPOS:
            raise ErrorImportacion(422, f"Campo desconocido: {str(campo)[:32]}")
        if campo in vistos:
            raise ErrorImportacion(422, f"El campo «{CAMPOS[campo][0]}» está asignado a dos columnas")
        vistos.add(campo)
        out[i] = campo
    return out


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def claves(d: dict) -> tuple[str | None, str | None]:
    """(clave principal, clave secundaria) de la persona de una fila ya validada."""
    nombre = val.clave_nombre(d.get("nombre") or "")
    tramite = d.get("tipo_tramite") or ""
    sec = _sha("sin|" + "|".join([nombre, tramite, d.get("id_registro_previo") or "", d.get("telefono") or "",
                                  d.get("correo") or ""])) if nombre else None
    if d.get("id_externo"):
        return _sha("ext|" + d["id_externo"]), sec
    if nombre and d.get("fecha_nacimiento") and tramite:
        return _sha("per|" + val.huella_duplicado(d["nombre"], d["fecha_nacimiento"], tramite)), sec
    return sec, sec


@dataclass
class Fila:
    fila: int                          # número de fila en la hoja (1 = encabezados)
    entrada: dict
    errores: dict
    avisos: list
    resultado: str = ""
    faltan: list = field(default_factory=list)
    clave: str | None = None
    clave2: str | None = None
    existente: dict | None = None
    mensaje: str = ""


def _region_de_sucursal(s, suc: str) -> str | None:
    return s.scalar(select(CalendarioDia.region).where(CalendarioDia.sucursal == suc).limit(1))


def _de_catalogo(valor, catalogo: dict[str, str], alias: dict[str, str]) -> str:
    """Acepta la clave del catálogo («reprogramar») o su etiqueta visible («Nueva cita»,
    «Renovación (ficticia)»), sin distinguir acentos ni mayúsculas. Si no coincide se deja tal cual
    y `validacion.validar` lo rechaza con su mensaje."""
    n = normalizar_encabezado(valor)
    for clave, etiqueta in catalogo.items():
        sin_nota = re.sub(r"\s*\(.*?\)", "", etiqueta)
        if n in (normalizar_encabezado(clave), normalizar_encabezado(etiqueta), normalizar_encabezado(sin_nota)):
            return clave
    return alias.get(n, str(valor))


def _filas(s, hoja: list[list[Celda]], cols: dict[int, str], formato_fecha: str, hoy: date) -> list[Fila]:
    regiones = set(s.scalars(select(Region.code)))
    columnas_conf = {c for c in cols.values() if c.endswith("_confirmacion")}
    out = []
    for n, celdas in enumerate(hoja[1:], start=2):
        if not any(c.tipo != "vacia" and (c.valor not in (None, "")) or c.tipo == "formula" for c in celdas):
            continue                     # fila vacía
        crudo, errores, avisos = {}, {}, []
        for i, campo in cols.items():
            c = celdas[i] if i < len(celdas) else Celda(None, "vacia")
            v, err = convertir(campo, c, formato_fecha, avisos)
            if err:
                errores[campo] = err
            elif v not in (None, ""):
                crudo[campo] = v
        # nombre: completo, o nombre(s) + apellidos
        partes = [crudo.pop(k, "") for k in ("nombres", "apellido_paterno", "apellido_materno", "apellidos")]
        if not crudo.get("nombre") and any(partes):
            crudo["nombre"] = " ".join(p for p in partes if p)
        for k in ("nombres", "apellido_paterno", "apellido_materno", "apellidos"):
            if k in errores:
                errores["nombre"] = errores.pop(k)
        if crudo.get("tipo_tramite"):
            crudo["tipo_tramite"] = _de_catalogo(crudo["tipo_tramite"], val.TRAMITES, _TRAMITES)
        if crudo.get("operacion"):
            crudo["operacion"] = _de_catalogo(crudo["operacion"], val.OPERACIONES, {})
        if crudo.get("region"):
            r = normalizar_encabezado(crudo["region"]).upper()
            crudo["region"] = r[7:] if r.startswith("REGION ") else r
        elif crudo.get("sucursal") and "region" not in errores:
            r = _region_de_sucursal(s, str(crudo["sucursal"]).strip().upper())
            if r:
                crudo["region"] = r
                avisos.append("Región deducida de la sucursal")
        entrada = dict(crudo)
        # El archivo es la fuente: no hay «segunda captura» que comparar (la confirmación existe para lo tecleado)
        # Si el archivo trae columna de confirmación, se compara con el dato (validacion.validar); nunca se guarda.
        for campo in ("telefono", "correo"):
            conf = campo + "_confirmacion"
            if campo in entrada and conf not in columnas_conf:
                entrada[conf] = entrada[campo]
            elif conf in columnas_conf and campo not in entrada and entrada.get(conf):
                errores.setdefault(campo, "Hay confirmación pero falta el dato")
        r = val.validar(entrada, regiones, incompleto=True, hoy=hoy)
        for k, msg in r.errores.items():
            k = k.replace("_confirmacion", "")
            if k not in errores:
                if k == "region" and msg == "Campo obligatorio":
                    msg = "Falta la región (y no se pudo deducir de la sucursal)"
                elif msg == "Confirma el dato":
                    msg = "La columna de confirmación está vacía en esta fila"
                errores[k] = msg
        f = Fila(n, r.datos if not errores else {}, errores, avisos)
        if not errores:
            f.clave, f.clave2 = claves(r.datos)
        out.append(f)
    return out


def _clasificar(s, filas: list[Fila], now: datetime) -> None:
    """Resultado PREVISTO de cada fila (sin escribir nada)."""
    from . import consentimiento as cs
    vistas: dict[str, int] = {}
    for f in filas:
        if f.errores:
            f.resultado, f.mensaje = RECHAZADO, "; ".join(f"{CAMPOS.get(k, (k,))[0]}: {v}" for k, v in f.errores.items())
            continue
        rep = vistas.get(f.clave) or (vistas.get(f.clave2) if f.clave2 else None)
        if rep:
            f.resultado, f.mensaje = REPETIDA, f"La misma persona ya aparece en la fila {rep}"
            continue
        vistas[f.clave] = f.fila
        if f.clave2:
            vistas.setdefault(f.clave2, f.fila)
        f.existente = existente(s, f)
        if f.existente:
            f.resultado = YA_EXISTE
            f.mensaje = f"Ya existe: {f.existente['codigo']} ({f.existente['estado']})" + (
                f", importado en {f.existente['lote']}" if f.existente.get("lote") else "")
            continue
        f.resultado = CREADO
        tmp = Expediente(modo="POR_DIA", **{k: v for k, v in f.entrada.items() if k not in ("sucursales_alternativas",)})
        faltan = ex.faltantes(tmp)
        if cs.exigido():
            faltan.append("consentimiento")
        f.faltan = faltan


def existente(s, f: Fila) -> dict | None:
    """¿Esta persona ya tiene expediente? Por una carga anterior (clave de fila), por la huella de
    duplicado activa o por el identificador externo."""
    ks = [k for k in (f.clave, f.clave2) if k]
    q = (select(ImportacionFila, Importacion.codigo).join(Importacion, Importacion.id == ImportacionFila.importacion_id)
         .where(ImportacionFila.resultado == CREADO,
                or_(ImportacionFila.clave_creado.in_(ks), ImportacionFila.clave_fila.in_(ks))).limit(1))
    row = s.execute(q).first()
    exp = None
    lote = None
    if row:
        exp, lote = s.get(Expediente, row[0].expediente_id), row[1]
    d = f.entrada
    if exp is None and d.get("fecha_nacimiento"):
        h = val.huella_duplicado(d["nombre"], d["fecha_nacimiento"], d["tipo_tramite"])
        exp = s.scalar(select(Expediente).where(Expediente.dedup_activo == h))
    if exp is None and d.get("id_externo"):
        exp = s.scalar(select(Expediente).where(Expediente.id_externo == d["id_externo"]))
    if exp is None:
        return None
    return {"id": exp.id, "codigo": exp.codigo, "estado": exp.estado, "lote": lote}


# ====================================================================== API del módulo
def _preparar(s, contenido: bytes, archivo: str, hoja: str | None, mapeo: dict | None, formato_fecha: str,
              now: datetime):
    if formato_fecha not in FORMATOS_FECHA:
        raise ErrorImportacion(422, "Formato de fecha no válido (DMA o MDA)")
    lib = leer(archivo, contenido)

    def recortar(filas):
        while filas and not any(c.tipo != "vacia" for c in filas[0]):
            filas = filas[1:]            # filas vacías antes de los encabezados
        return filas
    if hoja not in lib.hojas:
        # sin hoja elegida: la primera cuyos encabezados reconocen el nombre (p. ej. salta «Instrucciones»)
        hoja = next((h for h in lib.hojas if recortar(lib.filas.get(h) or []) and {"nombre", "nombres", "apellido_paterno"}
                     & set(sugerir_mapeo([_texto(c) for c in recortar(lib.filas[h])[0]])[0].values())), lib.hojas[0])
    datos = recortar(lib.filas.get(hoja) or [])
    if not datos:
        raise ErrorImportacion(422, f"La hoja «{hoja}» está vacía")
    encabezados = [_texto(c) for c in datos[0]]
    sugerido, info = sugerir_mapeo(encabezados)
    cols = _validar_mapeo(mapeo if mapeo is not None else sugerido, info)
    if "nombre" not in cols.values() and not ({"nombres", "apellido_paterno", "apellidos"} & set(cols.values())):
        raise ErrorImportacion(422, "Asigna al menos la columna del nombre", {"encabezados": info, "mapeo": sugerido})
    from . import zona
    filas = _filas(s, datos, cols, formato_fecha, zona.hoy(now))
    _clasificar(s, filas, now)
    avisos = list(lib.avisos)
    for x in info:
        if x["tipo"] == "PROHIBIDA":
            avisos.append(f"Columna «{x['texto']}» descartada: parece contener contraseñas o claves de acceso. "
                          "Cauce no las importa ni las guarda")
        elif x["tipo"] == "CONSENTIMIENTO":
            avisos.append(f"Columna «{x['texto']}» ignorada: importar no equivale a tener consentimiento; se registra "
                          "en Cauce con su evidencia (documento firmado)")
    return lib, hoja, datos, info, sugerido, cols, filas, avisos


def _resumen(filas: list[Fila]) -> dict:
    nuevos = [f for f in filas if f.resultado == CREADO]
    return {"filas": len(filas), "nuevos": len(nuevos),
            "nuevos_completos_sin_consentimiento": sum(1 for f in nuevos if f.faltan == ["consentimiento"]),
            "nuevos_con_datos_faltantes": sum(1 for f in nuevos if [x for x in f.faltan if x != "consentimiento"]),
            "ya_existen": sum(1 for f in filas if f.resultado == YA_EXISTE),
            "repetidas_en_archivo": sum(1 for f in filas if f.resultado == REPETIDA),
            "rechazadas": sum(1 for f in filas if f.resultado == RECHAZADO)}


def _vista_valor(campo: str, v) -> str:
    return v.isoformat() if isinstance(v, date) else (", ".join(v) if isinstance(v, list) else str(v))


def previsualizar(db, archivo: str, contenido: bytes, now: datetime, hoja: str | None = None,
                  mapeo: dict | None = None, formato_fecha: str = "DMA") -> dict:
    with db.session() as s:
        lib, hoja, datos, info, sugerido, cols, filas, avisos = _preparar(s, contenido, archivo, hoja, mapeo,
                                                                          formato_fecha, now)
        sha = hashlib.sha256(contenido).hexdigest()
        previas = [{"codigo": c, "fecha": t.isoformat() + "Z"} for c, t in s.execute(
            select(Importacion.codigo, Importacion.created_at).where(Importacion.archivo_sha256 == sha)
            .order_by(Importacion.id))]
        return {"archivo": archivo[:128], "formato": lib.formato, "hojas": lib.hojas, "hoja": hoja,
                "archivo_sha256": sha, "cargas_anteriores": previas, "formato_fecha": formato_fecha,
                "encabezados": info, "mapeo_sugerido": sugerido, "mapeo": {str(k): v for k, v in cols.items()},
                "campos": {k: v[0] for k, v in CAMPOS.items()}, "avisos": avisos, "resumen": _resumen(filas),
                "filas": [{"fila": f.fila, "resultado": f.resultado, "mensaje": f.mensaje, "errores": f.errores,
                           "faltan": f.faltan, "avisos": f.avisos, "existente": f.existente,
                           "valores": {k: _vista_valor(k, v) for k, v in f.entrada.items()
                                       if not k.endswith("_confirmacion")}}
                          for f in filas[:VISTA_FILAS]],
                "filas_omitidas_en_vista": max(0, len(filas) - VISTA_FILAS)}


def importar(db, archivo: str, contenido: bytes, actor: str, now: datetime, hoja: str | None = None,
             mapeo: dict | None = None, formato_fecha: str = "DMA") -> dict:
    """Importa las filas válidas. Cada fila en su propia transacción: una fila que falla no deshace las demás."""
    with db.session() as s:
        lib, hoja, datos, info, sugerido, cols, filas, avisos = _preparar(s, contenido, archivo, hoja, mapeo,
                                                                          formato_fecha, now)
        sha = hashlib.sha256(contenido).hexdigest()
        previa = s.scalar(select(Importacion.codigo).where(Importacion.archivo_sha256 == sha).order_by(Importacion.id).limit(1))
        if previa:
            avisos.insert(0, f"Este mismo archivo ya se cargó en {previa}: las personas ya importadas no se duplican")
        lote = Importacion(codigo=f"TMP-{hashlib.sha256(str(now).encode() + actor.encode()).hexdigest()[:12]}",
                           archivo=_nombre_seguro(archivo), archivo_sha256=sha, formato=lib.formato,
                           hoja=hoja[:64] if lib.formato == "xlsx" else None,
                           mapeo={str(k): v for k, v in cols.items()}, formato_fecha=formato_fecha, total=len(filas),
                           creadas=0, existentes=0, rechazadas=0, avisos=avisos[:20], creado_por=actor[:64],
                           created_at=now)
        s.add(lote)
        s.flush()
        lote.codigo = f"LOT-{lote.id:06d}"
        lote_id, codigo = lote.id, lote.codigo
        security.audit(s, actor, "importacion_iniciada", codigo, {"filas": len(filas), "formato": lib.formato,
                                                                  "archivo_sha256": sha[:16]})
    token = ex.SIN_AVISO_INCOMPLETO.set(True)
    try:
        for f in filas:
            _importar_fila(db, lote_id, codigo, f, actor, now)
    finally:
        ex.SIN_AVISO_INCOMPLETO.reset(token)
    with db.session() as s:
        lote = s.get(Importacion, lote_id)
        res = [f.resultado for f in filas]
        lote.creadas, lote.existentes = res.count(CREADO), res.count(YA_EXISTE) + res.count(REPETIDA)
        lote.rechazadas = res.count(RECHAZADO)
        bloqueados = s.scalar(select(func.count()).select_from(ImportacionFila).join(
            Expediente, Expediente.id == ImportacionFila.expediente_id).where(
            ImportacionFila.importacion_id == lote_id, ImportacionFila.resultado == CREADO,
            Expediente.estado.in_(ex.PREPARACION))) or 0
        security.audit(s, actor, "importacion_terminada", codigo, {"creadas": lote.creadas, "existentes": lote.existentes,
                                                                   "rechazadas": lote.rechazadas, "bloqueados": bloqueados})
        if lote.creadas and bloqueados:
            from ..engine import alerts as al
            al.create_alert(s, dedup_key=f"lote:{codigo}:incompletos", kind="CAPTURA",
                            title=f"{codigo}: {bloqueados} expediente(s) importados esperan datos o consentimiento",
                            source_code="importacion", region="-", resource="-", now=now, priority=3,
                            categoria="EXPEDIENTE_INCOMPLETO",
                            payload={"categoria": "EXPEDIENTE_INCOMPLETO", "lote": codigo, "bloqueados": bloqueados})
    return lote_detalle(db, lote_id)


def _nombre_seguro(n: str) -> str:
    base = re.split(r"[\\/]", str(n or ""))[-1]
    return re.sub(r"[^\w .()-]", "_", base)[:128] or "archivo"


def _importar_fila(db, lote_id: int, codigo: str, f: Fila, actor: str, now: datetime) -> None:
    def guardar(resultado, exp_id=None, estado=None, errores=None, faltan=None, clave_creado=None):
        with db.session() as s2:
            s2.add(ImportacionFila(importacion_id=lote_id, fila=f.fila, resultado=resultado, expediente_id=exp_id,
                                   clave_fila=f.clave2 or f.clave, clave_creado=clave_creado, estado_expediente=estado,
                                   errores=errores or {}, faltan=faltan or []))
    if f.resultado in (RECHAZADO, REPETIDA):
        guardar(f.resultado, errores=f.errores or {"_": f.mensaje})
        return
    if f.resultado == YA_EXISTE:
        guardar(YA_EXISTE, f.existente["id"], f.existente["estado"], {"_": f.mensaje})
        return
    try:
        with db.session() as s:
            ya = existente(s, f)            # otra carga pudo crearla entre la vista previa y ahora
            if ya:
                raise _Existe(ya)
            entrada = dict(f.entrada)
            for k in ("telefono", "correo"):          # el archivo es la fuente (ver _filas)
                if entrada.get(k):
                    entrada[f"{k}_confirmacion"] = entrada[k]
            exp = ex.crear(s, entrada, actor, now)
            exp.version = 1
            s.add(ImportacionFila(importacion_id=lote_id, fila=f.fila, resultado=CREADO, expediente_id=exp.id,
                                  clave_fila=f.clave2 or f.clave, clave_creado=f.clave, estado_expediente=exp.estado,
                                  errores={}, faltan=_faltan(s, exp, now)))
            ex.evento(s, exp.id, "IMPORTADO", actor[:64], now, lote=codigo, fila=f.fila)
            s.flush()
            f.resultado = CREADO
    except _Existe as e:
        f.resultado, f.existente = YA_EXISTE, e.args[0]
        guardar(YA_EXISTE, e.args[0]["id"], e.args[0]["estado"], {"_": f"Ya existe: {e.args[0]['codigo']}"})
    except ex.ErrorExpediente as e:
        if e.http == 409:
            f.resultado = YA_EXISTE
            guardar(YA_EXISTE, errores={"_": e.mensaje})
        else:
            f.resultado = RECHAZADO
            guardar(RECHAZADO, errores=e.errores or {"_": e.mensaje})
    except IntegrityError:
        # la misma persona se creó a la vez desde otra carga: la restricción UNIQUE lo impidió
        f.resultado = YA_EXISTE
        guardar(YA_EXISTE, errores={"_": "Se importó a la vez desde otra carga"})


class _Existe(Exception):
    pass


def _faltan(s, exp: Expediente, now: datetime) -> list[str]:
    from . import consentimiento as cs
    faltan = ex.faltantes(exp) if exp.estado in ex.PREPARACION else []
    if exp.estado in ex.PREPARACION and cs.exigido() and cs.vigente(s, exp, now) is None:
        faltan.append("consentimiento")
    return faltan


def lote_detalle(db, lote_id: int) -> dict:
    with db.session() as s:
        lote = s.get(Importacion, lote_id)
        if lote is None:
            raise ErrorImportacion(404, "Lote no encontrado")
        filas = []
        for fi in s.scalars(select(ImportacionFila).where(ImportacionFila.importacion_id == lote_id)
                            .order_by(ImportacionFila.fila, ImportacionFila.id)):
            e = s.get(Expediente, fi.expediente_id) if fi.expediente_id else None
            filas.append({"fila": fi.fila, "resultado": fi.resultado, "errores": fi.errores or {}, "faltan": fi.faltan or [],
                          "expediente": ({"id": e.id, "codigo": e.codigo, "estado": e.estado} if e else None),
                          "estado_al_importar": fi.estado_expediente})
        return {**_vista_lote(lote), "filas_detalle": filas}


def _vista_lote(lote: Importacion) -> dict:
    return {"id": lote.id, "codigo": lote.codigo, "archivo": lote.archivo, "archivo_sha256": lote.archivo_sha256,
            "formato": lote.formato, "hoja": lote.hoja, "mapeo": lote.mapeo, "formato_fecha": lote.formato_fecha,
            "filas": lote.total, "creadas": lote.creadas, "existentes": lote.existentes, "rechazadas": lote.rechazadas,
            "avisos": lote.avisos or [], "creado_por": lote.creado_por, "fecha": lote.created_at.isoformat() + "Z"}


def listar(db, limite: int = 50) -> list[dict]:
    with db.session() as s:
        return [_vista_lote(x) for x in s.scalars(select(Importacion).order_by(Importacion.id.desc()).limit(limite))]


def reporte_csv(db, lote_id: int) -> str:
    """Reporte del lote para descargar: fila, resultado, expediente, estado y errores. Sin valores personales.
    Cada celda que empezaría por un carácter de fórmula se protege con un apóstrofo."""
    d = lote_detalle(db, lote_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["lote", "fila", "resultado", "expediente", "estado_actual", "faltan", "errores"])

    def seguro(x: str) -> str:
        return "'" + x if x[:1] in ("=", "+", "-", "@", "\t", "\r") else x
    for f in d["filas_detalle"]:
        w.writerow([d["codigo"], f["fila"], f["resultado"], (f["expediente"] or {}).get("codigo", ""),
                    (f["expediente"] or {}).get("estado", ""), seguro(", ".join(f["faltan"])),
                    seguro("; ".join(f"{CAMPOS.get(k, (k,))[0] if k != '_' else ''}{': ' if k != '_' else ''}{v}"
                                     for k, v in f["errores"].items()))])
    return buf.getvalue()


def de_expediente(s, exp_id: int) -> dict | None:
    """Procedencia: lote y fila de los que salió el expediente (None si se capturó a mano)."""
    row = s.execute(select(Importacion.codigo, Importacion.archivo, ImportacionFila.fila, Importacion.created_at)
                    .join(ImportacionFila, ImportacionFila.importacion_id == Importacion.id)
                    .where(ImportacionFila.expediente_id == exp_id, ImportacionFila.resultado == CREADO).limit(1)).first()
    return {"lote": row[0], "archivo": row[1], "fila": row[2], "fecha": row[3].isoformat() + "Z"} if row else None

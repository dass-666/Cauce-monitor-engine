"""Validación de datos de expedientes (v1.5). Funciones puras: no tocan la base de datos.

Reglas:
- Ningún expediente se guarda incompleto: todos los campos obligatorios deben venir y ser válidos.
- Teléfono y correo llegan con su confirmación; la confirmación SOLO se compara aquí y se
  descarta (no se guarda una copia).
- Solo datos sintéticos (laboratorio): el correo debe usar un dominio reservado para pruebas
  (RFC 2606/6761: .test, .example, .invalid, example.com/net/org) y el teléfono el prefijo de
  laboratorio 555. Así un dato real escrito por error se rechaza en lugar de almacenarse.
- La huella de duplicado se calcula con el nombre normalizado (sin acentos, mayúsculas ni
  espacios repetidos), la fecha de nacimiento y el trámite.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

TRAMITES = {"alta": "Alta de registro (ficticio)", "renovacion": "Renovación (ficticia)",
            "constancia": "Constancia (ficticia)"}
OPERACIONES = {"nueva": "Nueva cita", "reprogramar": "Reprogramar"}
DOMINIOS_PRUEBA = ("example.com", "example.net", "example.org")
TLD_PRUEBA = (".test", ".example", ".invalid", ".localhost")
PREFIJO_TELEFONO_LAB = "555"

_NOMBRE = re.compile(r"^[A-Za-zÀ-ÿ'´.-]+( [A-Za-zÀ-ÿ'´.-]+)+$")
_CORREO = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}$")
_IDENT = re.compile(r"^[A-Z0-9-]{3,32}$")
_SUCURSAL = re.compile(r"^[A-Z0-9-]{2,32}$")


def solo_sinteticos() -> bool:
    """Por defecto SÍ. Desactivarlo exige una decisión explícita (variable de entorno)."""
    return os.environ.get("MONITOREO_CAPTURA_SOLO_SINTETICOS", "1").strip().lower() not in ("0", "no", "false")


@dataclass
class Resultado:
    datos: dict = field(default_factory=dict)          # valores normalizados (sin confirmaciones)
    errores: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errores


def normalizar_nombre(n: str) -> str:
    n = " ".join(str(n or "").split())
    return n


def clave_nombre(n: str) -> str:
    base = unicodedata.normalize("NFKD", normalizar_nombre(n)).encode("ascii", "ignore").decode()
    return " ".join(base.upper().replace(".", " ").split())


def digitos(t: str) -> str:
    return "".join(ch for ch in str(t or "") if ch.isdigit())


def huella_duplicado(nombre: str, fecha_nacimiento: date | str, tipo_tramite: str) -> str:
    f = fecha_nacimiento.isoformat() if isinstance(fecha_nacimiento, date) else str(fecha_nacimiento)
    return hashlib.sha256(f"{clave_nombre(nombre)}|{f}|{tipo_tramite}".encode()).hexdigest()


def correo_de_prueba(correo: str) -> bool:
    dom = correo.rsplit("@", 1)[-1]
    return dom in DOMINIOS_PRUEBA or dom.endswith(TLD_PRUEBA) or any(dom.endswith("." + d) for d in DOMINIOS_PRUEBA)


MINIMOS = ("nombre", "region", "tipo_tramite")      # v1.6: lo mínimo para abrir un expediente
MODOS = ("POR_DIA", "POR_HORARIO")
MAX_ALTERNATIVAS = 5


def validar(entrada: dict, regiones: set[str], parcial: bool = False, hoy: date | None = None,
            incompleto: bool = False) -> Resultado:
    """Valida y normaliza. `parcial=True` (modificación) valida solo los campos presentes, pero
    si cambia el teléfono o el correo exige su confirmación igual que al crear.
    v1.6 `incompleto=True` (alta en preparación): solo exige nombre, región y trámite; lo que
    venga se valida igual de estricto (y con su confirmación), lo que falte deja el expediente
    INCOMPLETO en lugar de rechazar el alta."""
    hoy = hoy or date.today()
    r = Resultado()
    e = r.errores
    presentes = {k for k, v in entrada.items() if v is not None and str(v).strip() != ""}

    def falta(campo: str) -> bool:
        if campo in presentes:
            return False
        if not parcial and (not incompleto or campo in MINIMOS):
            e[campo] = "Campo obligatorio"
        return True

    if not falta("nombre"):
        n = normalizar_nombre(entrada["nombre"])
        if not (5 <= len(n) <= 128) or not _NOMBRE.match(n):
            e["nombre"] = "Nombre completo: al menos nombre y apellido, solo letras"
        else:
            r.datos["nombre"] = n
    if not falta("fecha_nacimiento"):
        try:
            f = entrada["fecha_nacimiento"]
            f = f if isinstance(f, date) else date.fromisoformat(str(f).strip())
            if f > hoy:
                e["fecha_nacimiento"] = "La fecha de nacimiento no puede ser futura"
            elif (hoy - f).days > 120 * 366:
                e["fecha_nacimiento"] = "Fecha de nacimiento fuera de rango"
            else:
                r.datos["fecha_nacimiento"] = f
        except ValueError:
            e["fecha_nacimiento"] = "Fecha no válida (AAAA-MM-DD)"
    for campo, conf in (("telefono", "telefono_confirmacion"), ("correo", "correo_confirmacion")):
        if falta(campo):
            continue
        if campo == "telefono":
            v, c = digitos(entrada[campo]), digitos(entrada.get(conf))
            if len(v) != 10:
                e[campo] = "El teléfono debe tener 10 dígitos"
            elif solo_sinteticos() and not v.startswith(PREFIJO_TELEFONO_LAB):
                e[campo] = f"Laboratorio: usa un teléfono ficticio que empiece por {PREFIJO_TELEFONO_LAB}"
        else:
            v, c = str(entrada[campo]).strip().lower(), str(entrada.get(conf) or "").strip().lower()
            if not _CORREO.match(v):
                e[campo] = "Correo no válido"
            elif solo_sinteticos() and not correo_de_prueba(v):
                e[campo] = "Laboratorio: usa un correo ficticio (dominio .test, .example o example.com)"
        if conf not in presentes:
            e[conf] = "Confirma el dato"
        elif campo not in e and c != v:
            e[conf] = "Los teléfonos no coinciden" if campo == "telefono" else "Los correos no coinciden"
        if campo not in e and conf not in e:
            r.datos[campo] = v
    if not falta("region"):
        reg = str(entrada["region"]).strip().upper()
        if reg not in regiones:
            e["region"] = "Región desconocida"
        else:
            r.datos["region"] = reg
    if not falta("tipo_tramite"):
        t = str(entrada["tipo_tramite"]).strip().lower()
        if t not in TRAMITES:
            e["tipo_tramite"] = "Tipo de trámite no válido"
        else:
            r.datos["tipo_tramite"] = t
    if "operacion" in presentes:
        o = str(entrada["operacion"]).strip().lower()
        if o not in OPERACIONES:
            e["operacion"] = "Operación no válida"
        else:
            r.datos["operacion"] = o
    elif not parcial:
        r.datos["operacion"] = "nueva"
    if "sucursal" in presentes:
        s = str(entrada["sucursal"]).strip().upper()
        if not _SUCURSAL.match(s):
            e["sucursal"] = "Sucursal no válida"
        else:
            r.datos["sucursal"] = s
    for campo in ("id_registro_previo", "id_externo"):
        if campo in presentes:
            v = str(entrada[campo]).strip().upper()
            if not _IDENT.match(v):
                e[campo] = "Identificador no válido (3-32 letras, números o guiones)"
            else:
                r.datos[campo] = v
    if "prioridad" in presentes:
        try:
            p = int(entrada["prioridad"])
            if not 1 <= p <= 5:
                raise ValueError
            r.datos["prioridad"] = p
        except (TypeError, ValueError):
            e["prioridad"] = "Prioridad de 1 (máxima) a 5"
    elif not parcial:
        r.datos["prioridad"] = 3
    # v1.6: modalidad, sucursales alternativas y folio de la cita anterior (al reprogramar)
    if "modo" in presentes:
        m = str(entrada["modo"]).strip().upper()
        if m not in MODOS:
            e["modo"] = "Modalidad no válida (POR_DIA o POR_HORARIO)"
        else:
            r.datos["modo"] = m
    if entrada.get("sucursales_alternativas") is not None:
        alts = entrada["sucursales_alternativas"]
        if isinstance(alts, str):
            alts = [a for a in alts.replace(";", ",").split(",")]
        limpias = []
        for a in alts if isinstance(alts, list) else [None]:
            a = str(a or "").strip().upper()
            if not _SUCURSAL.match(a):
                e["sucursales_alternativas"] = "Sucursal alternativa no válida"
                break
            if a not in limpias and a != r.datos.get("sucursal"):
                limpias.append(a)
        if len(limpias) > MAX_ALTERNATIVAS:
            e["sucursales_alternativas"] = f"Máximo {MAX_ALTERNATIVAS} sucursales alternativas"
        if "sucursales_alternativas" not in e:
            r.datos["sucursales_alternativas"] = limpias
    if "folio_anterior" in presentes:
        v = str(entrada["folio_anterior"]).strip().upper()
        if not _IDENT.match(v):
            e["folio_anterior"] = "Folio no válido"
        else:
            r.datos["folio_anterior"] = v
    # Coherencia entre campos
    if (r.datos.get("operacion") == "reprogramar" and not incompleto
            and not (entrada.get("id_registro_previo") or parcial)):
        e.setdefault("id_registro_previo", "Para reprogramar hace falta el identificador de registro previo")
    return r


# ---------------------------------------------------------------- minimización en pantallas y listados
def enmascarar_nombre(n: str) -> str:
    return " ".join(p[:1] + "•" * max(2, len(p) - 1) for p in str(n).split())


def enmascarar_telefono(t: str) -> str:
    return "•" * 6 + str(t)[-4:]


def enmascarar_correo(c: str) -> str:
    u, _, d = str(c).partition("@")
    return (u[:1] + "•••@" + d) if d else "•••"


CAMPOS_PERSONALES = ("nombre", "fecha_nacimiento", "telefono", "correo", "id_registro_previo", "id_externo",
                     "folio_anterior")

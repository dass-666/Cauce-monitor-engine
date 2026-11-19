"""v2.0 · P2 · Contrato genérico de adaptadores de proveedor.

Un adaptador es la ÚNICA pieza que sabe hablar con un proveedor concreto. El resto del motor solo
ve los tipos normalizados de este módulo. Cuatro etapas, separadas a propósito:

  1. preparación   qué datos pide el proveedor y cómo se validan/normalizan (sin red)
  2. observación   consultar disponibilidad (solo si `capacidades().observar`)
  3. captura manual  el operador hace el trámite en el flujo OFICIAL y registra el resultado
  4. confirmación  solo con una integración AUTORIZADA (`capacidades().confirmar`); si no, el
                   adaptador lanza NoAutorizado y el camino es la captura manual

Reglas que todo adaptador cumple (las prueba tests/test_adaptadores.py):
- 429/403 del proveedor -> Resultado RESTRICCION: se registra el motivo y la operación SE DETIENE.
  No se reintenta, no se cambia de conexión ni de identidad. No hay rotación de IP ni nada que
  sortee cuotas, CAPTCHA o controles de acceso.
- Una respuesta perdida (corte, 5xx, tiempo agotado) es INCIERTO: nunca se da por buena ni por mala.
- Nada se da por CONFIRMADO sin evidencia del proveedor; lo que registra una persona queda DECLARADO
  hasta que una integración autorizada lo verifica.
- Ningún tipo de este módulo lleva datos personales.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

# ---------------------------------------------------------------- resultados normalizados
OK, RECHAZO, RESTRICCION, INCIERTO, RED, NO_AUTORIZADO = (
    "OK", "RECHAZO", "RESTRICCION", "INCIERTO", "RED", "NO_AUTORIZADO")
TIPOS_RESULTADO = (OK, RECHAZO, RESTRICCION, INCIERTO, RED, NO_AUTORIZADO)


@dataclass(frozen=True)
class Capacidades:
    nombre: str
    descripcion: str
    version_contrato: str = "1"
    preparar: bool = True                # sabe qué datos pide el proveedor
    observar: bool = False               # puede consultar disponibilidad
    confirmar: bool = False              # confirma por sí mismo (requiere integración AUTORIZADA)
    verificar: bool = False              # puede comprobar un folio por una vía autorizada
    captura_manual: bool = True          # admite registrar un resultado hecho a mano
    autorizado: bool = False             # hay autorización y credenciales para hablar con el proveedor
    red: bool = False                    # habla por la red (False: todo en memoria)
    modos: tuple[str, ...] = ("POR_DIA",)
    formato_folio: str = r"^[A-Za-z0-9-]{4,64}$"
    notas: str = ""

    def como_dict(self) -> dict:
        d = asdict(self)
        d["modos"] = list(self.modos)
        return d


@dataclass(frozen=True)
class Resultado:
    """Cómo terminó una operación con el proveedor, en términos que el motor entiende."""
    tipo: str
    codigo: str = ""
    mensaje: str = ""
    http: int | None = None
    reintentar_tras_s: float | None = None   # informativo (Retry-After); el motor NO reintenta solo
    transaccion_id: str | None = None

    def __post_init__(self):
        if self.tipo not in TIPOS_RESULTADO:
            raise ValueError(f"tipo de resultado desconocido: {self.tipo}")

    @property
    def detener(self) -> bool:
        """True si la operación debe pararse ya (sin reintentos de ningún tipo)."""
        return self.tipo in (RESTRICCION, NO_AUTORIZADO)

    def como_dict(self) -> dict:
        return {**asdict(self), "detener": self.detener}


@dataclass(frozen=True)
class Disponibilidad:
    fecha: str                       # ISO AAAA-MM-DD
    sucursal: str | None
    estado: str                      # DISPONIBLE | AGOTADO | CERRADO | ...
    libres: int | None = None
    fuente: str = ""


@dataclass(frozen=True)
class Observacion:
    resultado: Resultado
    dias: tuple[Disponibilidad, ...] = ()
    observada_en: str = ""

    def como_dict(self) -> dict:
        return {"resultado": self.resultado.como_dict(), "dias": [asdict(d) for d in self.dias],
                "observada_en": self.observada_en}


@dataclass(frozen=True)
class Preparacion:
    """Lo que el operador necesita para hacer el trámite (o lo que el motor enviaría)."""
    campos: tuple[dict, ...]                        # [{campo, etiqueta, obligatorio, origen}]
    faltantes: tuple[str, ...] = ()
    pasos_manuales: tuple[str, ...] = ()
    listo: bool = False

    def como_dict(self) -> dict:
        return {"campos": list(self.campos), "faltantes": list(self.faltantes),
                "pasos_manuales": list(self.pasos_manuales), "listo": self.listo}


@dataclass(frozen=True)
class Verificacion:
    """Respuesta de la vía autorizada sobre un folio. `coincide` solo es True con evidencia."""
    resultado: Resultado
    existe: bool | None = None       # None: no se pudo saber
    coincide: bool | None = None
    estado_proveedor: str | None = None
    fecha: str | None = None
    sucursal: str | None = None
    evidencia: dict = field(default_factory=dict)


# ---------------------------------------------------------------- errores
class NoAutorizado(Exception):
    """La operación exige una integración autorizada que este adaptador no tiene."""


class DatoInvalido(ValueError):
    def __init__(self, campo: str, mensaje: str):
        super().__init__(f"{campo}: {mensaje}")
        self.campo, self.mensaje = campo, mensaje


# ---------------------------------------------------------------- utilidades comunes
_FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y")


def normalizar_fecha(valor: str | date | datetime) -> str:
    """Cualquier formato habitual -> ISO AAAA-MM-DD. No adivina: un formato ambiguo o inválido falla.
    (MM/DD/AAAA NO se acepta: en México 03/04 es 3 de abril y un error aquí da una cita en otro día.)"""
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    texto = str(valor or "").strip()
    for f in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, f).date().isoformat()
        except ValueError:
            continue
    raise DatoInvalido("fecha", f"formato de fecha no reconocido ({texto[:20]!r}); usa AAAA-MM-DD o DD/MM/AAAA")


def interpretar_http(status: int, headers: dict | None = None, cuerpo: dict | None = None) -> Resultado:
    """Traducción GENÉRICA de un código HTTP a un Resultado. Cada adaptador puede afinarla, pero
    nunca puede convertir 429/403 en algo que se reintente."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    cuerpo = cuerpo or {}
    tx = cuerpo.get("transaccion_id") or headers.get("x-transaccion-id") or headers.get("x-request-id")
    if status in (429, 403):
        espera = headers.get("retry-after")
        try:
            espera_s = float(espera) if espera is not None else None
        except ValueError:
            espera_s = None
        motivo = ("el proveedor limitó las solicitudes (HTTP 429)" if status == 429
                  else "el proveedor negó el acceso (HTTP 403)")
        return Resultado(RESTRICCION, "RESTRICCION_PROVEEDOR", motivo + ": la operación se detiene", status,
                         espera_s, tx)
    if status >= 500:
        return Resultado(INCIERTO, "ERROR_PROVEEDOR", f"el proveedor respondió HTTP {status}", status, None, tx)
    if status in (401,):
        return Resultado(NO_AUTORIZADO, "CREDENCIALES", "credenciales inválidas o ausentes", status, None, tx)
    if 400 <= status < 500:
        return Resultado(RECHAZO, str(cuerpo.get("codigo") or f"HTTP_{status}")[:32],
                         str(cuerpo.get("mensaje") or cuerpo.get("detail") or f"HTTP {status}")[:300], status, None, tx)
    return Resultado(OK, "", "", status, None, tx)


def validar_folio(folio: str, patron: str) -> str:
    f = (folio or "").strip().upper()
    if not re.fullmatch(patron, f):
        raise DatoInvalido("folio", "el folio no tiene el formato que usa este proveedor")
    return f


# ---------------------------------------------------------------- el contrato
class Adaptador(ABC):
    """Contrato que implementa cada proveedor. Ver docs/CONTRATO_ADAPTADORES.md."""

    @abstractmethod
    def capacidades(self) -> Capacidades: ...

    # 1. preparación (sin red) --------------------------------------------------------
    @abstractmethod
    def preparar(self, datos: dict) -> Preparacion: ...

    # 2. observación ------------------------------------------------------------------
    def observar(self, filtros: dict) -> Observacion:
        raise NoAutorizado(f"{self.capacidades().nombre}: este adaptador no consulta disponibilidad")

    # 4. confirmación (solo con integración autorizada) --------------------------------
    def confirmar(self, preparacion_ref: dict) -> Resultado:
        raise NoAutorizado(f"{self.capacidades().nombre}: sin integración autorizada para confirmar; "
                           "el operador completa el trámite en el flujo oficial y registra el resultado")

    # verificación de un folio (para la captura manual) -------------------------------
    def verificar(self, folio: str, fecha: str, sucursal: str | None) -> Verificacion:
        return Verificacion(Resultado(NO_AUTORIZADO, "SIN_VIA_AUTORIZADA",
                                      "este adaptador no tiene una vía autorizada para comprobar folios"))

    # interpretación de respuestas (común; un adaptador puede ampliarla) ---------------
    def interpretar(self, status: int, headers: dict | None = None, cuerpo: dict | None = None) -> Resultado:
        return interpretar_http(status, headers, cuerpo)

    def normalizar_fecha(self, valor) -> str:
        return normalizar_fecha(valor)

    def validar_folio(self, folio: str) -> str:
        return validar_folio(folio, self.capacidades().formato_folio)

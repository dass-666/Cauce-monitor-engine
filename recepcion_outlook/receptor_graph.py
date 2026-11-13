"""Recepción de correo de Outlook.com mediante Microsoft Graph (solo lectura).

Diseño de seguridad:
- Nunca recibe ni guarda contraseñas. El propietario del buzón inicia sesión
  él mismo en la página de Microsoft (flujo "device code") y da su consentimiento.
- Permiso mínimo: Mail.Read (delegado). No puede enviar, borrar ni mover correo.
- Los tokens nunca se imprimen ni se escriben en el informe.
- Ante un 429 se respeta Retry-After y se detiene; no se reintenta en bucle
  ni se cambia de cuenta, red o aplicación para esquivar el límite.
- Antes de leer se comprueba que la cuenta que inició sesión es la dirección
  esperada, para no vincular un buzón equivocado a un expediente.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

GRAPH = "https://graph.microsoft.com/v1.0"
AUTORIDAD_CONSUMIDOR = "https://login.microsoftonline.com/consumers"
PERMISOS = ["Mail.Read"]  # MSAL añade offline_access / openid / profile
CAMPOS = "id,internetMessageId,receivedDateTime,from,subject,hasAttachments"


# Estados de conexión que la bandeja central debe mostrar por buzón.
OK = "RECIBE_OK"
NECESITA_CONSENTIMIENTO = "NECESITA_CONSENTIMIENTO"   # nunca autorizado o token revocado/caducado
CUENTA_DISTINTA = "CUENTA_DISTINTA"                   # inició sesión otra dirección
PERMISO_DENEGADO = "PERMISO_DENEGADO"                 # 401/403 con token válido
LIMITADO = "LIMITADO_POR_MICROSOFT"                   # 429/503 con Retry-After
ERROR_RED = "ERROR_RED"
APP_MAL_CONFIGURADA = "APP_MAL_CONFIGURADA"           # registro de Azure incorrecto (no es culpa del buzón)
ERROR_DESCONOCIDO = "ERROR_DESCONOCIDO"


class ErrorRecepcion(Exception):
    def __init__(self, estado: str, detalle: str, reintentar_en: int | None = None):
        super().__init__(f"{estado}: {detalle}")
        self.estado = estado
        self.detalle = detalle
        self.reintentar_en = reintentar_en


def clasificar_respuesta(status: int, cuerpo: dict | None, cabeceras: dict) -> ErrorRecepcion | None:
    """Traduce una respuesta HTTP de Graph a un estado de buzón (None = correcto)."""
    if 200 <= status < 300:
        return None
    codigo = ((cuerpo or {}).get("error") or {}).get("code", "")
    if status in (429, 503):
        try:
            espera = int(cabeceras.get("Retry-After", "60"))
        except ValueError:
            espera = 60
        return ErrorRecepcion(LIMITADO, f"HTTP {status} {codigo}".strip(), reintentar_en=espera)
    if status == 401:
        return ErrorRecepcion(NECESITA_CONSENTIMIENTO, f"HTTP 401 {codigo}".strip())
    if status == 403:
        return ErrorRecepcion(PERMISO_DENEGADO, f"HTTP 403 {codigo}".strip())
    return ErrorRecepcion(ERROR_DESCONOCIDO, f"HTTP {status} {codigo}".strip())


def clasificar_error_msal(resultado: dict) -> ErrorRecepcion:
    err = resultado.get("error", "desconocido")
    desc = (resultado.get("error_description") or "").split("\n")[0][:200]
    if err in ("invalid_grant", "interaction_required", "consent_required",
               "authorization_declined", "expired_token", "access_denied"):
        return ErrorRecepcion(NECESITA_CONSENTIMIENTO, f"{err}: {desc}")
    if err in ("unauthorized_client", "invalid_client") or "AADSTS7000218" in desc:
        return ErrorRecepcion(APP_MAL_CONFIGURADA, f"{err}: {desc}")
    return ErrorRecepcion(ERROR_DESCONOCIDO, f"{err}: {desc}")


def normalizar(direccion: str) -> str:
    return (direccion or "").strip().lower()


def comprobar_cuenta(esperada: str, claims: dict) -> None:
    real = normalizar(claims.get("preferred_username") or claims.get("email") or "")
    if real != normalizar(esperada):
        raise ErrorRecepcion(CUENTA_DISTINTA, f"se esperaba {esperada!r} e inició sesión {real or '(desconocida)'!r}")


@dataclass
class Mensaje:
    id: str
    internet_message_id: str
    recibido: str
    remitente: str
    asunto: str
    adjuntos: bool

    @classmethod
    def desde_graph(cls, m: dict) -> "Mensaje":
        de = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
        return cls(m.get("id", ""), m.get("internetMessageId", ""), m.get("receivedDateTime", ""),
                   de, m.get("subject") or "", bool(m.get("hasAttachments")))


@dataclass
class ResultadoSync:
    estado: str
    mensajes: list[Mensaje] = field(default_factory=list)
    delta_link: str | None = None
    detalle: str = ""
    reintentar_en: int | None = None


class ReceptorGraph:
    """Lee la Bandeja de entrada de UN buzón de forma incremental (delta).

    `obtener_token` devuelve un access token válido o lanza ErrorRecepcion.
    `http_get(url, headers, timeout)` devuelve (status, json|None, cabeceras).
    Ambos se inyectan para poder probar sin red.
    """

    def __init__(self, obtener_token: Callable[[], str],
                 http_get: Callable[[str, dict, float], tuple[int, Any, dict]],
                 max_paginas: int = 20, timeout: float = 30.0):
        self.obtener_token = obtener_token
        self.http_get = http_get
        self.max_paginas = max_paginas
        self.timeout = timeout

    def sincronizar(self, delta_link: str | None = None) -> ResultadoSync:
        """Primera vez (sin delta_link): trae la bandeja actual y un delta_link.
        Siguientes veces: solo lo nuevo o cambiado desde ese delta_link."""
        url = delta_link or (f"{GRAPH}/me/mailFolders/inbox/messages/delta?$select={CAMPOS}")
        mensajes: list[Mensaje] = []
        try:
            token = self.obtener_token()
            for _ in range(self.max_paginas):
                try:
                    status, cuerpo, cab = self.http_get(
                        url, {"Authorization": f"Bearer {token}", "Prefer": "odata.maxpagesize=50"}, self.timeout)
                except OSError as e:  # incluye errores de conexión/timeout de requests
                    raise ErrorRecepcion(ERROR_RED, type(e).__name__)
                fallo = clasificar_respuesta(status, cuerpo, cab or {})
                if fallo:
                    raise fallo
                cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
                for m in (cuerpo or {}).get("value", []):
                    if "@removed" in m:
                        continue  # borrado/movido por el dueño; la bandeja central conserva su copia
                    mensajes.append(Mensaje.desde_graph(m))
                if "@odata.nextLink" in cuerpo:
                    url = cuerpo["@odata.nextLink"]
                    continue
                return ResultadoSync(OK, mensajes, cuerpo.get("@odata.deltaLink"))
            # Demasiadas páginas: se entrega lo leído y se continúa en la próxima pasada.
            return ResultadoSync(OK, mensajes, None, detalle=f"continúa en {url[:60]}…")
        except ErrorRecepcion as e:
            return ResultadoSync(e.estado, mensajes, None, detalle=e.detalle, reintentar_en=e.reintentar_en)


# --------------------------------------------------------------------------
# Autenticación con MSAL (device code). Solo se usa en la prueba real.
# --------------------------------------------------------------------------

def abrir_cache(ruta: str):
    import msal
    cache = msal.SerializableTokenCache()
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def guardar_cache(cache, ruta: str) -> None:
    if not cache.has_state_changed:
        return
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(cache.serialize())
    os.replace(tmp, ruta)


class AutenticadorMSAL:
    def __init__(self, client_id: str, direccion: str, ruta_cache: str,
                 avisar: Callable[[str], None] = print, interactivo: bool = True):
        import msal
        self.direccion = direccion
        self.ruta_cache = ruta_cache
        self.cache = abrir_cache(ruta_cache)
        self.avisar = avisar
        self.interactivo = interactivo
        self.app = msal.PublicClientApplication(client_id, authority=AUTORIDAD_CONSUMIDOR,
                                                token_cache=self.cache)

    def _cuenta(self):
        for c in self.app.get_accounts():
            if normalizar(c.get("username")) == normalizar(self.direccion):
                return c
        return None

    def obtener_token(self) -> str:
        resultado = None
        cuenta = self._cuenta()
        if cuenta:
            resultado = self.app.acquire_token_silent(PERMISOS, account=cuenta)
        if not resultado:
            if not self.interactivo:
                raise ErrorRecepcion(NECESITA_CONSENTIMIENTO, "sin token guardado; el dueño debe autorizar")
            flujo = self.app.initiate_device_flow(scopes=PERMISOS)
            if "user_code" not in flujo:
                raise clasificar_error_msal(flujo)
            self.avisar(flujo["message"])
            resultado = self.app.acquire_token_by_device_flow(flujo)
        if "access_token" not in resultado:
            raise clasificar_error_msal(resultado)
        comprobar_cuenta(self.direccion, resultado.get("id_token_claims") or
                         {"preferred_username": (self._cuenta() or {}).get("username", "")})
        guardar_cache(self.cache, self.ruta_cache)
        return resultado["access_token"]


def http_get_requests(url: str, headers: dict, timeout: float):
    import requests
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise OSError(type(e).__name__) from e
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None
    return r.status_code, cuerpo, dict(r.headers)

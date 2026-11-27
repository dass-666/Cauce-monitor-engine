"""Consumidor OPCIONAL y de SOLO LECTURA de Network Engine («¿hay alguna conexión utilizable según el motor de red?»).

Reglas (todas con prueba en tests/test_red_lectura.py):
- Monitor NO depende de Network Engine: nada de esto se importa al arrancar Monitor; sin configuración o sin el paquete
  `cauce`, el lector queda inactivo y devuelve DESCONOCIDO sin hacer ninguna petición.
- Solo la vista `read`: únicamente `GET /api/v1/status/connectivity`, con una credencial `read` propia (ruta del archivo en
  MONITOREO_NETWORK_TOKEN_FILE; el texto del token nunca pasa por esta capa). Se rechaza cualquier cliente con métodos de
  administración (p. ej. `CauceClient`).
- Tres estados: DISPONIBLE, NO_DISPONIBLE y DESCONOCIDO. `usable=None` JAMÁS se convierte en NO_DISPONIBLE; un motor que no
  responde, un 503 o una respuesta rara es DESCONOCIDO («sin información»), no «sin internet».
- 401/403/400 (credencial inválida, revocada o permiso insuficiente): el lector SE DETIENE (cero peticiones) hasta `reanudar()`.
- Interruptor: MONITOREO_NETWORK_DESACTIVADO=1, CAUCE_STATUS_DISABLED=1 o un archivo (MONITOREO_NETWORK_ARCHIVO_APAGADO).
- Nada sensible: no guarda ni registra IP, MAC, identificadores de conexión, rutas, texto de excepciones ni el token.

Esto es laboratorio: validado contra el simulador de Network Engine (loopback, datos ficticios), no con hardware.
"""
from .lector import (INTERRUPTORES_ENV, Internet, Lectura, LectorConectividad, ClienteNoPermitido,  # noqa: F401
                     desde_entorno)

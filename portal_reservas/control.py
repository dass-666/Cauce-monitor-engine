"""Cliente de las rutas de CONTROL del portal de laboratorio (`/control/...`).

El protocolo de control (cabecera y token) pertenece al portal de laboratorio, no al motor: el núcleo de
`monitoreo/` no debe conocerlo (tests/test_integracion_http.py::test_engine_core_has_no_provider_specific_code).
El motor (rutas de laboratorio, v2.3) llama a esta función y nada más.
"""
from __future__ import annotations

import httpx

CABECERA = "X-Control-Token"


def peticion(base: str, token: str, metodo: str, ruta: str, timeout: float = 10, **kw) -> httpx.Response:
    return httpx.request(metodo, base.rstrip("/") + ruta, headers={CABECERA: token}, timeout=timeout,
                         trust_env=False, **kw)

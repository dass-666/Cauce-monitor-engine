# Cómo integrar una nueva fuente (incluida una API externa autorizada)

El motor solo entiende un contrato: `monitoreo/contracts.py`.

```python
Observation(resource_key="F-123", name="Función 20:00", resource_type="entrada",
            region="NORTE", starts_at=datetime(...), capacity=100, available=4,
            status="ACTIVO" | "CANCELADO", changed_at=datetime(...) | None)
Snapshot(observations=[...], complete=True)   # complete=False si la respuesta es parcial/paginada
```

Un conector **solo traduce** el formato de la fuente a ese contrato. No decide ritmo,
alertas ni asignaciones: eso no cambia nunca al añadir fuentes.

## Pasos

1. Crea `monitoreo/connectors/mi_proveedor.py`:

```python
from .base import Connector, ConnectorError, RateLimited, TemporaryError
from ..contracts import Observation, Snapshot

class MiProveedorConnector(Connector):
    type_name = "mi_proveedor"

    def validate_config(self):
        if not self.config.get("url"):
            raise ConnectorError("Falta url")

    def fetch(self) -> Snapshot:
        # 1. Llamar a la API AUTORIZADA (con su credencial oficial, ver token_env).
        # 2. 429/503 -> raise RateLimited(retry_after=...)   (el motor esperará)
        #    timeouts/5xx -> raise TemporaryError(...)        (el motor reintenta con retroceso)
        #    401/403 -> raise ConnectorError(...)             (la fuente pasa a ERROR, sin insistir)
        # 3. Traducir cada elemento a Observation.
        return Snapshot(observations=[...], complete=True)
```

2. Regístralo en `connectors/registry.py` añadiéndolo a la tupla de `REGISTRY`.
3. Añade el valor a `SourceIn.connector_type` en `api/schemas.py`.
4. Crea la fuente desde la API (`POST /api/sources`) con su `config`.
5. Escribe una prueba como las de `tests/test_connectors.py` (respuesta normal, 429, 5xx, formato inválido).

`HttpApiConnector` (`connectors/http_api.py`) ya sirve como plantilla completa.

## Reglas para fuentes externas

- Usar solo APIs con autorización y respetar sus términos y límites. Configura
  `rate_limit_per_min` y `interval_s` por debajo de lo que permita el proveedor.
- Credenciales: define una variable de entorno (p. ej. `PROVEEDOR_TOKEN`) y pon
  `"token_env": "PROVEEDOR_TOKEN"` en la configuración. Nunca la escribas en la BD.
- Añade el host a `MONITOREO_WEBHOOK_HOSTS` (lista de hosts permitidos para salidas HTTP).
- `resource_key` debe ser **estable** entre consultas (el id del proveedor, no la posición en la lista).
- Si la API pagina, o envías todo con `complete=True`, o marca `complete=False` (si no, el
  motor creería que lo no recibido se canceló).
- Si la API informa cuándo cambió el recurso, rellena `changed_at`: así se mide el tiempo real de detección.

## Conectores incluidos

| Tipo | Configuración | Notas |
|---|---|---|
| `simulacion` | `feed`, `error_rate`, `rate_limit_rate`, `retry_after_s`, `duplicate_rate`, `latency_ms` | Lee el mundo ficticio del laboratorio, con inyección de fallos |
| `json` | `path` (dentro de `data/feeds`) | `{"complete": true, "items": [...]}` o lista. Acepta alias en español (`clave`, `nombre`, `disponibles`...) |
| `csv` | `path`, `delimiter` | Cabeceras como en JSON |
| `http` | `url`, `timeout_s`, `token_env` | Contrato interno en JSON. Probado contra `/api-prueba/feeds/{feed}` |

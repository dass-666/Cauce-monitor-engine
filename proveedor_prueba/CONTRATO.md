# Contrato HTTP del proveedor de prueba (v1)

> **Qué es:** un servicio HTTP **nuestro**, con **datos ficticios**, que se ejecuta como
> proceso independiente en `127.0.0.1`. Sirve para probar el motor con red real
> (peticiones, latencias, códigos HTTP y cortes de conexión reales) sin depender de nadie.
>
> **Qué no es:** no imita a ningún proveedor real ni a su API. Una integración futura con un
> proveedor externo autorizado necesitará su propio conector, escrito a partir del contrato y
> de las condiciones de uso de ese proveedor.

## Arranque

```
set PROVEEDOR_TOKEN=un-token-de-prueba-inventado
set PROVEEDOR_TOKEN_CONTROL=otro-token-de-prueba-inventado
python -m proveedor_prueba --puerto 8100 --datos data\proveedor\estado.json
```

| Opción | Por defecto | Para qué |
|---|---|---|
| `--puerto` | 8100 | puerto local |
| `--host` | 127.0.0.1 | solo se acepta una dirección local (no se publica en la red) |
| `--datos` | data/proveedor/estado.json | archivo propio del proveedor; **no** es la base del motor |
| `--semilla` | 7 | datos iniciales reproducibles |
| `--recursos-por-region` | 12 | tamaño del inventario |
| `--cuota-por-min` | 1200 | cuota real por credencial; al superarla responde 429 con `Retry-After` (0 = sin cuota) |

El estado se guarda con escritura atómica (archivo temporal + reemplazo) como máximo cada
0,5 s y al detenerse de forma ordenada. Un corte brusco (`kill -9`) puede perder los cambios
del último medio segundo; el motor lo detecta como "reinicio del proveedor" y converge.

## Modelo (ficticio)

- 5 regiones: `norte`, `sur`, `este`, `oeste`, `centro`.
- Cada región tiene recursos con fecha (`inicio`/`fin`), `capacidad` y tipo
  (`entrada`, `mesa`, `unidad`, `turno`).
- **Liberaciones estables** (`libres`) y **ventanas** que vencen solas (disponibilidad que expira).
- **Reservas** que consumen unidades (primero de las ventanas) y cancelaciones que las devuelven.
- **Liberaciones programadas** para un momento futuro.
- **Cancelación de recursos** completa.
- Cada cambio incrementa la `version` de su región y queda en el historial (`/control/historial`).

## API del proveedor (`/v1`)

Todas requieren `Authorization: Bearer <PROVEEDOR_TOKEN>`. Sin ella o con otra: **401**.

### `GET /v1/regiones`
```json
{"regiones": [{"id": "norte", "nombre": "Norte", "version": 14, "recursos": 12}, ...]}
```

### `GET /v1/regiones/{region}/disponibilidad?limite=50&cursor=...`

```json
{
  "region": "norte",
  "version": 14,
  "generado_en": "2026-11-12T19:21:14.122857Z",
  "total": 12,
  "recursos": [
    {"id": "NOR-001", "nombre": "Sala Aurora Norte · 1", "tipo": "entrada",
     "inicio": "2026-11-14T04:00:00Z", "fin": "2026-11-14T06:00:00Z",
     "capacidad": 6, "disponibles": 2, "estado": "ABIERTO",
     "disponible_hasta": "2026-11-12T19:21:17Z",
     "ventanas": [{"unidades": 2, "hasta": "2026-11-12T19:21:17Z"}],
     "actualizado_en": "2026-11-12T19:21:14.132704Z"}
  ],
  "siguiente": "MTQ6NTA"
}
```

- `estado`: `ABIERTO`, `CANCELADO` o `CERRADO` (ya pasó su `fin`).
- `disponibles`: unidades libres **en el momento de generar la respuesta** (incluye ventanas vivas).
- `ventanas`: qué parte de `disponibles` vence y cuándo.
- `siguiente`: cursor de la página siguiente, o `null` en la última. El cursor lleva la
  versión: si los datos cambian entre páginas, la siguiente petición responde
  **409** `{"error": "cursor_obsoleto", "version_actual": N}` y hay que empezar de nuevo.
- Cabecera `ETag: W/"norte-14"`. Con `If-None-Match` igual a la versión actual responde
  **304** sin cuerpo.
- Cabecera `X-Tiempo-Servidor-ms`: lo que tardó el servidor (para separar red de servidor).

| Código | Significado | Qué hace el motor |
|---|---|---|
| 200 | página de datos | compara y detecta cambios |
| 304 | sin cambios desde el ETag enviado | no compara nada |
| 401 / 403 | credencial ausente, inválida o revocada | detiene la fuente (ERROR) y avisa; no insiste |
| 404 | región desconocida | error definitivo |
| 409 | cursor obsoleto (datos cambiaron entre páginas) | reinicia una vez; si se repite, consulta INCOMPLETA |
| 429 / 503 | cuota superada o servicio no disponible, con `Retry-After` | espera lo pedido (LIMITADO); no cuenta como fallo |
| 500 | fallo interno | error temporal: reintento corto y retroceso exponencial |

### `POST /v1/reservas` · `DELETE /v1/reservas/{id}`
`{"recurso": "NOR-001", "unidades": 1, "titular": "Titular Ficticio 001"}` → 201 con la reserva,
404 si el recurso no existe, 409 si no hay unidades suficientes.

## Rutas de laboratorio (`/control`)

Requieren `X-Control-Token: <PROVEEDOR_TOKEN_CONTROL>` (si no, **403**). Un proveedor real
nunca ofrecería esto: existen solo para provocar situaciones y auditar al motor.

| Ruta | Cuerpo | Efecto |
|---|---|---|
| `GET /control/estado` | | versiones, contadores, fallos activos, métricas de peticiones |
| `GET /control/historial?desde=N` | | cambios con número `n`, hora `t`, región, recurso, antes/después, motivo |
| `POST /control/liberar` | `{"recurso", "unidades", "ventana_s"?}` | libera unidades (con `ventana_s`, vencen solas) |
| `POST /control/programar` | `{"recurso", "unidades", "en_s", "ventana_s"?}` | liberación programada |
| `POST /control/cancelar-recurso` | `{"recurso"}` | cancela el recurso |
| `POST /control/simulacion` | `{"activa", "cada_ms", "ventana_s"?, "p_rafaga"?, "p_cancelar_recurso"?}` | actividad aleatoria de fondo |
| `POST /control/fallos` | ver abajo | inyecta fallos en `/v1/.../disponibilidad` |
| `POST /control/reiniciar` | | vuelve al inventario inicial, sin fallos ni simulación |
| `POST /control/guardar` | | fuerza el guardado del estado |

`/control/fallos` acepta:

- `modo`: `normal`, `429`, `503`, `500`, `401`, `lento` (1–3 s), `colgar` (`colgar_s`),
  `cortar` (envía la mitad del cuerpo y cierra la conexión) o `malformada` (a un elemento le
  falta `disponibles`).
- `restantes`: cuántas peticiones afecta (sin indicarlo, todas). `prob_fallo`: probabilidad por petición.
- `regiones`: lista de regiones afectadas (sin indicarlo, todas).
- `retry_after`: segundos que se piden en 429/503.
- `latencia_ms`: número o `[mín, máx]`, latencia añadida a TODAS las respuestas de datos.
- `repetir_antigua`: cuántas veces servir una respuesta anterior ya servida (caché obsoleta).
- `cambiar_entre_paginas`: cuántas veces cambiar un recurso justo después de servir una página.
- `reset: true` vuelve todo a normal.

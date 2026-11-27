# Retiro progresivo del gestor propio de conexiones (`monitoreo/conexiones/`)

> **Estado:** propuesta (diagnóstico + plan). **No se borró ni se cambió nada de `monitoreo/conexiones/`** en esta rama.
> Marcas: **[V]** comprobado leyendo el código o con pruebas · **[I]** inferencia · **[P]** pendiente.

## 1. Por qué hay dos gestores

| | `monitoreo/conexiones/` (Monitor v2.0) | Network Engine (`cauce`) |
|---|---|---|
| Descubre interfaces | Sí (`descubrimiento.py`: `/sys/class/net` o `Get-NetAdapter`) [V] | Sí, más completo (prueba de salida por interfaz) [V] |
| Salud | Fallos por operación + `sonda_tcp` [V] | Sondeo continuo con histéresis (3 fallos / 2 éxitos) y espera creciente [V] |
| Elige salida | Fija la IP de origen (bind) [V] | Igual: IP de origen por concesión de operación [V] |
| Auditoría | Tabla `conexion_eventos` de Monitor [V] | JSONL encadenado propio [V] |
| ¿Lo usa algo en producción? | **Sí:** `captura/portal.py:52` (`navegador()`) en todos los entornos, no solo en el laboratorio [V] | No: solo lo lee la pantalla *Red* de esta rama [V] |
| Reglas de negocio que **no** existen en NE | 429/403 = restricción, se detiene y no se reintenta por otra vía; `continuar_sesion`/`confirmar` nunca cambian de conexión; `LOCAL` para 127.0.0.1 [V: docstring de `gestor.py`, `tests/test_conexiones.py`] | Equivalentes parciales (política de restricción, concesiones), no idénticos [I] |

Conclusión: el duplicado real es **descubrimiento + salud**. Las **reglas de negocio** del gestor (qué operación admite respaldo,
qué hacer ante 429/403) son de Monitor y deben quedarse en Monitor aunque la salud venga de NE.

## 2. Qué NO conviene hacer ahora
- **Borrar el gestor**: rompe `captura/portal.py` (el piloto lo usa) y no hay sustituto validado.
- **Hacer que Monitor dependa de NE para operar**: NE no tiene servicio de arranque ni prueba física de conmutación (auditoría,
  D1–D3). Si NE no arranca, Monitor no debe quedarse sin red.
- **Retirarlo antes de la prueba física E1** (`cauce-network-engine/docs/VALIDACION_FISICA_WINDOWS.md`). Si E1 falla en F6, quizá la
  conmutación tenga que ir al router, y entonces ni NE ni el gestor deberían elegir salida (solo observar).

## 3. Plan reversible por fases

Cada fase se activa con una variable y se revierte quitándola. Ninguna borra datos.

| Fase | Qué cambia | Interruptor | Requisito para entrar | Cómo se revierte |
|---|---|---|---|---|
| **0 (esta rama)** | NE solo se **muestra** (pantalla *Red*). El gestor sigue decidiendo. | `MONITOREO_NETWORK_TOKEN_FILE` | Ninguno | Quitar la variable |
| **1** | El gestor **consulta** a NE como opinión extra de salud: si NE dice que una interfaz está caída, el gestor no la elige para operaciones *nuevas*. La elección y las reglas siguen en Monitor. Si NE no responde, se ignora (comportamiento actual). | `MONITOREO_CONEXIONES_SALUD=network_engine` (por defecto `propia`) | E1 con F2, F3, F6 y F7 en PASA | Valor `propia` |
| **2** | El descubrimiento propio pasa a ser respaldo: se usan los nombres/tipos de NE; `descubrimiento.py` solo corre si NE no está. | `MONITOREO_CONEXIONES_DESCUBRIR=network_engine` | Fase 1 estable 2 semanas en el piloto sin incidentes de red | Valor `propia` |
| **3** | Se retira `descubrimiento.py` y la sonda TCP; el gestor queda como «reglas de operación» sobre datos de NE. | Commit propio, revertible con `git revert` | NE con servicio de arranque, y fase 2 estable | `git revert` |
| **Alternativa** | Si E1 demuestra que el PC no puede conmutar (F6 falla), el failover va al **router** (MikroTik/OpenWrt) y Monitor y NE **solo observan** (adaptadores de `cauce/routers/`). El gestor se reduce a `LOCAL` + reglas de restricción. | — | E1 fallido | — |

## 4. Pruebas de regresión que deben seguir en verde en todas las fases
- `tests/test_conexiones.py` (13): reglas 1–5 del gestor, incluida la de 429/403 y la de sesiones que no cambian de conexión.
- `tests/test_produccion_flujo_admin.py` (5): en producción no se publican `/api/conexiones` ni rutas de laboratorio.
- `tests/test_adaptadores.py` (8) y las pruebas del portal que usan `captura.portal.navegador()`.
- `tests/test_red_lectura.py` (25) y `tests/test_red_servicio.py` (10): el consumidor no bloquea y no filtra IP/MAC/ID.
- **Nuevas en la fase 1 [P]:** (a) con NE apagado, la elección es idéntica a la de `propia`; (b) NE dice «caída» ⇒ no se elige para
  `nueva_sesion`, pero `confirmar` sigue por su conexión fija y termina como «resultado incierto», sin repetirse; (c) 429/403 nunca
  provoca cambio de conexión aunque NE diga que otra está sana.

## 5. Costo estimado [I]
Fase 1: 1–2 días (adaptador + 3 pruebas nuevas). Fase 2: 1 día. Fase 3: medio día. No conviene empezar la fase 1 hasta tener E1.

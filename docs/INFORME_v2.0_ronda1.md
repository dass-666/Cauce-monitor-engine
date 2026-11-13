# Informe de avance de la versión 2.0: modo ventanilla y gestor de conexiones

Fecha: 13 de noviembre de 2026 · Rama `fase-2.0` (sale de `fase-1.7` `cc0b4b7`; **sin fusionar en ninguna rama**)
Estado: **P0 terminado y probado; P1 parcialmente terminado; P2 sin empezar.** Este informe no es
de cierre completo de la 2.0: dice qué está hecho, con qué evidencia, y qué falta.

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos, interfaces o
actores ficticios a propósito) · **ESTIMADO** (deducción) · **PENDIENTE** (no verificado).

## 1. Estado inicial encontrado

La 2.0 se trató como la fase siguiente a la 1.7 (indicación del coordinador). La base ya tenía:
expedientes con 10 estados y recuperación tras reinicios, persistencia SQLite/PostgreSQL,
operadores y supervisión (1.7), reservaciones con evidencia por clave y folio (1.5-1.6), auditoría,
cola con reparto explicado, notificaciones, e instalación y actualización en Windows probadas en CI.
Línea base de pruebas: 513 pasan y 4 omitidas en SQLite + PostgreSQL (MEDIDO, rama 1.7).
Lo que **no** existía: atención presencial (una persona delante del operador), meta diaria de
citas, ni ningún control de por qué red sale el motor.

## 2. Funciones desarrolladas

**Modo ventanilla (P0).** Pantalla «Ventanilla» para atender a una persona de principio a fin:
buscar su expediente (solo coincidencia exacta, datos enmascarados) o registrarla, enviar datos,
elegir día, confirmar y cerrar con folio. Estados de la atención: PENDIENTE, EN_PROCESO, CONFIRMADA,
SIN_DISPONIBILIDAD, ERROR, CANCELADA. Detalles:
- **Idempotente**: la atención la identifica una clave que genera el navegador; pulsar dos veces o
  reintentar tras un corte devuelve la misma atención.
- **Duplicados**: si la persona ya tiene expediente, se avisa y se atiende el existente; no se crea otro.
- **Retomar**: tras cerrar el navegador o reiniciar el motor, la atención abierta vuelve al paso exacto.
- **Integridad con el motor**: mientras se atiende en ventanilla, la solicitud queda EN_VENTANILLA
  y el motor no la asigna a otro operador; si el motor se adelanta para OTRO operador, la ventanilla
  recibe 409 y no se la quita.
- **Sin cita**: la atención termina sin folio y el expediente vuelve a la cola del motor, que sigue
  buscando por la persona.
- **Confirmación incierta**: la atención queda PENDIENTE y se resuelve sola verificando por clave.
  Nunca se marca CONFIRMADA sin una reservación CONFIRMADA con evidencia del portal.
- **Meta diaria configurable** (`meta_citas_dia`, 6 por defecto): solo cuentan citas con folio
  comprobado; se distingue «atención terminada con cita» de «atención terminada sin cita».

**Gestor de conexiones (P1).** `monitoreo/conexiones/`:
- Descubre las interfaces de red en Linux (datos del núcleo) y en Windows (`Get-NetAdapter`, o el
  nombre si PowerShell no responde) y las clasifica en Ethernet, Wi-Fi, USB, módem, loopback o virtual.
- Usa **una** conexión, la preferida por tipo (configurable). No reparte ni rota conexiones.
- **Respaldo solo cuando la conexión no llega a establecerse** (nada alcanzó al portal): se repite
  esa misma petición una vez por la siguiente conexión. Una sesión del portal ya abierta sigue
  siempre por su conexión; una confirmación nunca se repite por otra vía (se verifica por clave).
- **Restricción del proveedor (HTTP 429 o 403)**: se registra el motivo, la operación se detiene y
  **no** se reintenta, ni por la misma conexión ni por otra. No cuenta como fallo de red.
- Registra cada operación con el portal (qué conexión, qué resultado) y cada cambio, caída,
  recuperación y restricción en `conexion_eventos`; la reservación y la atención guardan su conexión.
- Pantalla «Conexiones» (administrador) con estado, latencia, fallos, restricciones y eventos.

## 3. Archivos y componentes modificados

Nuevos: `monitoreo/captura/ventanilla.py`, `monitoreo/api/ventanilla.py`, `monitoreo/web/ventanilla.js`,
`monitoreo/conexiones/{__init__,descubrimiento,gestor}.py`, `monitoreo/api/conexiones.py`,
migraciones `0009_v2_0_ventanilla.py` y `0010_v2_0_conexiones.py` (solo AÑADEN tablas y una
columna), `tests/test_ventanilla.py`, `tests/test_conexiones.py`, `scripts/demo_v20.py`,
`scripts/recorrido_v20.js`.
Modificados: `engine/assignment.py` (estado EN_VENTANILLA y paso de ASIGNADO a ventanilla del mismo
operador), `captura/seguimiento.py`, `captura/reservaciones.py` y `captura/portal.py` (todas las
peticiones al portal pasan por el gestor), `captura/formularios/navegador.py` (dirección de origen,
restricción 429/403, respaldo seguro), `models.py`, `config.py`, `engine/params.py`, panel
(`app.js`, `captura.js`, `app.css`, `index.html`), `portal_reservas/servidor.py` (fallo simulado
«limite» = 429), `.github/workflows/ci.yml`, `scripts/e2e_panel.js`.
55 archivos, +5 591 líneas (incluye resultados y capturas).

## 4. Problemas detectados y resueltos

Encontrados por la demostración con navegador real (cada uno con su prueba):

| # | Problema | Gravedad | Corrección y prueba |
|---|---|---|---|
| 1 | **Carrera motor/ventanilla**: si el motor asignaba la solicitud recién creada al MISMO operador antes de sincronizar, la ventanilla respondía 409 y la persona se quedaba sin atender (con un expediente creado y huérfano) | Alta | Se cancela esa asignación (libera cupo y carga) y pasa a EN_VENTANILLA con UPDATE condicional; nunca se le quita a otro operador. `test_motor_asigna_al_mismo_operador_antes_del_seguimiento` falló con el código anterior |
| 2 | La pantalla se pintaba **dos veces** al empezar una atención y, con el servidor lento, **borraba lo que el operador ya había tecleado** | Media | Un solo repintado. La demo lo reproduce siempre retrasando las respuestas 400 ms (falló antes, pasa después) |
| 3 | «Atender este» (duplicado) estaba dentro del formulario y lo **enviaba otra vez** (dos peticiones) | Baja | `type="button"`; la demo exige un único 409 |
| 4 | El aviso de duplicado mostraba `[object HTMLDivElement]` en vez de la lista | Baja | Corregido; comprobado en la demo |
| 5 | La prueba de navegador del CI seguía esperando el Inicio de la v1.6 (fallaba en Python 3.13 Linux y Windows desde la 1.7) | Baja (prueba) | Actualizada; pasa en local (37/37) |

## 5. Pruebas ejecutadas y resultados reales

| Prueba | Resultado | Archivo |
|---|---|---|
| Suite completa SQLite + PostgreSQL 16 | **560 pasan, 4 omitidas**, 0 fallan (MEDIDO) | — (ejecución local, 9 min) |
| `test_ventanilla.py` | 17 pruebas × 2 bases | — |
| `test_conexiones.py` | 12 pruebas (descubrimiento Linux/Windows con datos fijos, elección, respaldo, restricción, sesión fija, sondeo, integración real con el portal de prueba) | — |
| Demo en navegador real (Chromium) SQLite | **27/27** (MEDIDO) | `docs/resultados/v2.0/demo_v20.json` |
| Demo en navegador real PostgreSQL | 26/26 dos veces seguidas (antes de añadir la comprobación de la pantalla Conexiones) | `docs/resultados/v2.0/postgresql/` |
| CI de GitHub (Windows y Linux) | **22/22** en verde, con la demo en Windows ×3 y Linux ×2 | corrida 43400570380 |

La demo hace: 6 atenciones seguidas hasta el folio (meta cumplida), 1 sin disponibilidad, 1
duplicado recuperado hasta su folio, 1 a medias con **kill del motor** y retomada hasta el folio, 1
en teléfono; folios comprobados de forma independiente en el portal; 9 con cita, 1 sin cita,
ninguna abierta; sin errores de JavaScript; sin datos personales en los registros.
Interacciones por atención: **20** (11 campos, 3 casillas, 6 clics), MEDIDO con navegador
automatizado; el tiempo de una persona real será mucho mayor que los 1,7 s medidos.

Escenarios de resistencia pedidos (fase 6):

| # | Escenario | Estado | Evidencia |
|---|---|---|---|
| 1 | Seis atenciones consecutivas | Hecho | Demo + `test_seis_atenciones_consecutivas_con_folio_verificado_y_meta` |
| 2 | Diez atenciones en una jornada | Hecho | Demo (10 atenciones) |
| 3 | Veinte solicitudes en cola | Parcial | Laboratorio 1.7 (600 expedientes, 10 operadores); no combinado con ventanilla |
| 4 | Dos operadores a la vez | Hecho | `test_dos_operadores_toman_el_mismo_expediente_a_la_vez` |
| 5 | Caída de Internet durante una atención | Hecho (SIMULADO) | `test_respaldo_real_si_la_conexion_no_se_establece` |
| 6 | Recuperación de la conexión | Hecho (SIMULADO) | `test_respaldo_solo_tras_fallos_de_red_y_vuelta_a_la_preferida_cuando_es_estable` |
| 7 | Reinicio inesperado del programa | Hecho | Demo (kill del motor, retomar hasta folio) |
| 8 | Reinicio de la computadora | Parcial | Se reinicia el motor; no motor + portal + base a la vez en la 2.0 (sí en la demo de la 1.6) |
| 9 | Duplicación accidental | Hecho | Demo + `test_duplicado_se_detecta_y_se_recupera_el_existente` + idempotencia |
| 10 | Respuesta tardía del proveedor | Hecho | `test_confirmacion_incierta_queda_pendiente_y_se_resuelve_sola` |
| 11 | Sin citas disponibles | Hecho | Demo + `test_sin_disponibilidad_devuelve_a_la_cola_y_el_motor_sigue` |
| 12 | Límite de solicitudes alcanzado | Hecho | `test_restriccion_429_detiene_la_operacion_sin_cambiar_de_conexion` (una sola petición, sin reintento) |
| 13 | Caída y recuperación de la base de datos | **PENDIENTE** | No se ha probado en la 2.0 |
| 14 | Conmutación de red en operaciones permitidas | Hecho (SIMULADO) | Pruebas 5 y 6 |
| 15 | Confirmación pendiente verificada | Hecho | Prueba 10 |

## 6. Limitaciones identificadas

- **Gestor de conexiones**: elige la red fijando la dirección de origen. Que el sistema saque el
  tráfico por esa interfaz depende de su tabla de rutas (en Linux con varias salidas hace falta
  enrutamiento por origen). **No verificado con interfaces físicas** (el entorno solo tiene una).
- En Windows sin PowerShell disponible, un teléfono compartiendo internet por USB se clasifica como
  Ethernet (solo se ve el nombre «Ethernet 2»). Declarado y probado como limitación.
- El respaldo automático solo actúa cuando la conexión ni siquiera se establece. Si se corta a
  mitad, la operación queda como «resultado incierto» (por diseño: no se arriesga una doble
  reservación) y la siguiente operación nueva ya usa el respaldo cuando se alcanza el umbral.
- El sondeo de conexiones es manual («Medir ahora») o implícito en cada operación; no hay sondeo
  periódico para no generar tráfico innecesario hacia el proveedor.
- La supervisión optimizada de la 1.7 (`1470d2e`) **no** está en esta rama: llevarla aquí es una
  fusión entre ramas y necesita tu aprobación.

## 7. Funciones simuladas frente a funciones reales

| Función | Real | Simulado |
|---|---|---|
| Navegador del operador | Chromium real (Playwright) | El operador lo maneja un script |
| Portal de citas | — | Portal de prueba propio en 127.0.0.1 |
| Personas | — | Datos sintéticos |
| Interfaces de red | Descubrimiento real del equipo | Wi-Fi, USB y módem: interfaces simuladas en pruebas |
| Conmutación | Sockets reales (una dirección de origen inexistente = cable desenchufado) | Las «redes» son direcciones locales |
| Restricción 429 | Respuesta HTTP real del portal de prueba | Provocada a propósito |
| Adaptadores de plataformas (fase 4) | — | **No desarrollado todavía** |

## 8. Resultado de las pruebas de conectividad

- Descubrimiento en este equipo (Linux): `eth0` Ethernet, `lo` loopback, `ifb0/ifb1` virtuales. Correcto.
- Con el portal en 127.0.0.1 el gestor registra «LOCAL» y no aparenta probar redes físicas (la
  pantalla lo dice; comprobado en la demo).
- Respaldo de extremo a extremo con el portal de prueba: la primera conexión no conecta, la misma
  petición se repite por la segunda, la sesión queda ligada a ella y la cita se confirma (prueba).
- 429: una sola petición llega al portal, la reservación guarda `RESTRICCION_PROVEEDOR`, la conexión
  no cambia (prueba).
- Windows real: el descubrimiento se ejecutará en el CI de Windows (`test_descubrimiento_real_de_este_equipo_no_falla`); en el MSI, PENDIENTE.

## 9. Rendimiento y consumo de recursos

No hay medición nueva de rendimiento en la 2.0. Las cifras de referencia son las de la 1.7 (servidor
de un proceso saturado a unas 20-40 peticiones/s; ver `INFORME_v1.7.md` §11). La ventanilla añade
pocas consultas por atención (ESTIMADO). La demo completa (10 atenciones, reinicio incluido) tarda
31 s en SQLite.

## 10. Cambios pendientes de validar en Windows

**CI de GitHub, corrida 43400570380 (commit `5c28b6f`): 22 de 22 trabajos en verde** (MEDIDO):
pruebas completas en Windows y Linux con Python 3.11, 3.12 y 3.13 (incluye `test_ventanilla.py` y
`test_conexiones.py`, con el descubrimiento real de interfaces en Windows); la demo `demo_v20.py`
con navegador real en Windows ×3 y en Linux con SQLite y PostgreSQL; migraciones 0009 y 0010 en la
actualización desde v1.1-v1.6 con PowerShell 5.1.
**Nada de la 2.0 está probado en el MSI** (PENDIENTE): en particular, la clasificación de su Wi-Fi,
cable o teléfono por USB reales. No hay todavía script `.bat` para la ventanilla ni guía de uso.

## 11. Rama y commit final

Rama `fase-2.0`: `b77b3c6` (ventanilla), `2f3796d` (demo y 3 defectos), `1e6f13a` (gestor de
conexiones), `5c28b6f` (prueba e2e y demo 27/27) y el de este informe.

## 12. Siguientes pasos recomendados

1. Decidir si traer a esta rama el cierre de la 1.7 (`1470d2e`: supervisión optimizada).
2. Escenarios pendientes: caída y recuperación de la base (13) y reinicio completo (8).
3. P2: contrato de adaptadores y adaptador simulado (fase 4), incluido el modo demostración en el
   que el operador completa el trámite oficial a mano y registra el folio verificado.
4. Probar en el MSI: instalación, `demo_v20.py` y la pantalla Conexiones con su Wi-Fi y cable reales.

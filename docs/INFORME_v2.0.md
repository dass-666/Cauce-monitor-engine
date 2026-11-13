# Informe de avance de la versión 2.0 (ronda 2): resistencia, adaptadores, conectividad y Windows

Fecha: 13 de noviembre de 2026 · Rama `fase-2.0` (**sin fusionar en `master`**, sin desplegar)
Ronda anterior (ventanilla P0 y gestor de conexiones P1): `docs/INFORME_v2.0_ronda1.md`.

**Estado: la 2.0 NO está terminada.** Los siete trabajos pedidos en esta ronda están hechos y
probados en Linux y en las máquinas Windows de GitHub. Falta lo esencial que no puede hacerse desde
este entorno: el equipo MSI físico, redes físicas reales y un proveedor real autorizado (§6 y §12).

Leyenda: **MEDIDO** (ejecución real con archivo de resultados) · **SIMULADO** (datos, interfaces o
actores ficticios a propósito) · **ESTIMADO** (deducción) · **PENDIENTE** (no verificado).

## 1. Estado inicial de esta ronda

- `fase-2.0` tenía ventanilla (P0) y gestor de conexiones (P1): suite 560/4 y CI 22/22
  (corrida 43400570380). Faltaban: la supervisión optimizada de la 1.7, la caída de la base
  (escenario 13), el reinicio completo (8), los adaptadores (P2), los scripts de Windows y la guía.
- **Comparación de ramas antes de integrar (autorizada por Diego):** a `fase-2.0` solo le faltaban
  2 commits de `fase-1.7` (`1470d2e`, `68daadf`): `api/centro.py`, `app.py`, `scripts/rendimiento_v17.py`,
  la prueba de paridad y documentos/resultados de la 1.7. `scripts/e2e_panel.js` ya era idéntico.
  Se integró con un merge (`788c651`) sin conflictos y sin tocar ventanilla ni conexiones.
- Hallazgo: la corrida de CI 43403982589 (commit `aef71d9`, que solo cambiaba el informe) **falló**
  en Windows · Python 3.11, en la demo de ventanilla. El informe anterior citaba la corrida verde
  previa y no esta. Causa y corrección en §4 (defecto 6).

## 2. Funciones desarrolladas

**1. Supervisión + ventanilla.** Con las consultas agrupadas de la 1.7 ya integradas, la supervisión
muestra por operador sus **atenciones de ventanilla abiertas** (columna «Ventanilla») y la
redistribución **no** las libera: las lista como «atención presencial abierta: se cierra desde
Ventanilla». Comprobado con EN_VENTANILLA, ASIGNADO y reasignaciones
(`test_supervision_ve_la_ventanilla_y_la_redistribucion_no_la_toca`, SQLite y PG).

**2 y 3. Resistencia con PostgreSQL real y reinicio completo** (`scripts/resistencia_v20.py`).
PostgreSQL se detiene **de verdad** (`pg_ctl stop -m fast`; y `-m immediate`, equivalente a un corte de luz).
- A: la base cae **a mitad de una confirmación que el portal sí procesó**. El operador recibe 503
  (no un éxito falso) y `/api/health` da 503. El motor sigue vivo y se recupera solo. El reintento del
  operador **no** vuelve a confirmar en el portal (409). La confirmación se recupera por su clave
  (CONFIRMADA con evidencia) en 8,2 s y la atención cierra con ese folio.
- B: la base cae con una atención a medias. Todas las operaciones dan 503 y **nada llega al
  portal**. Al volver, la atención sigue en el paso exacto y llega a su folio.
- C: **motor y portal matados y PostgreSQL detenido de golpe a la vez**, con una confirmación en
  vuelo. Todo arranca sobre los mismos datos en 2,9 s y los folios anteriores siguen CONFIRMADOS.
  Las dos atenciones abiertas se retoman. La confirmación en vuelo se resuelve sola: se reabre
  porque el portal no la había procesado. Ambas llegan a su folio.
- Final: 4 personas, **4 folios distintos y exactamente 4 confirmaciones en el portal** (ninguna
  duplicada). La coherencia del motor está limpia, el resumen del día da 4 con cita y 0 abiertas,
  y no hay datos personales en los registros.

**4. Adaptadores P2** (`monitoreo/adaptadores/`; contrato en `docs/CONTRATO_ADAPTADORES.md`).
- El contrato separa las cuatro etapas:
  - **preparación**: sin red y sin devolver datos personales;
  - **observación**;
  - **captura manual**;
  - **confirmación**: solo con integración autorizada; si no la hay, `NoAutorizado`.
- Resultados normalizados:
  - 429/403 = `RESTRICCION`, que **detiene** la operación;
  - 5xx = `INCIERTO`;
  - 401 = `NO_AUTORIZADO`;
  - se guarda el identificador de transacción;
  - las fechas se normalizan **sin adivinar** (MM/DD/AAAA se rechaza).
- Tres adaptadores:
  - `simulado`: en memoria y determinista;
  - `laboratorio`: el portal propio, por el gestor de conexiones; observa `/api/dias` y verifica folios;
  - `oficial_manual`: sin integración, no se conecta a nada.
- **Captura manual** (tabla nueva `capturas_manuales`, migración `0011`): el operador hace el
  trámite en el flujo oficial y registra el folio y la fecha. Empieza como DECLARADA y pasa a:
  - VERIFICADA: solo si el proveedor lo confirma por vía autorizada y coinciden folio y fecha;
  - DISCREPANCIA: el expediente pasa a revisión;
  - SIN_VERIFICACION: el adaptador no tiene forma autorizada de comprobarlo.

  Nunca cuenta como cita confirmada por el motor. Se registra desde la pantalla nueva **«Trámites a mano»**.

**5. Conectividad.** Área nueva **«Conectividad»** en el autodiagnóstico (`diagnostico.bat` y panel). Revisa:
- interfaces utilizables y si hay respaldo;
- resolución del destino;
- conexión TCP de prueba en modo profundo (sin enviar nada);
- restricciones del proveedor y caídas de las últimas 24 h, con qué hacer en cada caso.

La pantalla «Conexiones» añade las peticiones, restricciones y caídas de las últimas 24 h. Son
persistentes: sobreviven a reinicios. **Ante 403/429 el comportamiento no cambia**: se detiene sin
reintentar, y el diagnóstico solo aconseja revisarlo con el proveedor.

**6. Windows.**
- `ventanilla.bat`: abre directamente la pantalla Ventanilla.
- `demo_ventanilla.bat`: demostración automática; comprueba Node.js y prepara el navegador la primera vez.
- `docs/GUIA_VENTANILLA_WINDOWS.md`: guía de instalación y uso.

**El MSI físico no se ha probado.**

**7. Validación**: ver §5.

## 3. Archivos modificados en esta ronda

10 commits sobre `aef71d9`.

**Nuevos:**
- `monitoreo/adaptadores/{__init__,base,comun,simulado,laboratorio,sin_integracion,capturas}.py`
- `monitoreo/api/adaptadores.py`
- `monitoreo/conexiones/diagnostico.py`
- `migrations/versions/0011_v2_0_capturas_manuales.py`
- `scripts/resistencia_v20.py`
- `tests/test_adaptadores.py`
- `ventanilla.bat` y `demo_ventanilla.bat`
- `docs/CONTRATO_ADAPTADORES.md` y `docs/GUIA_VENTANILLA_WINDOWS.md`
- `docs/resultados/v2.0/resistencia/`

**Modificados:**
- `app.py`: 503 con la base caída
- `api/routes.py`: health
- `api/centro.py`: supervisión
- `api/conexiones.py`
- `captura/reservaciones.py`: registro de la recuperación
- `conexiones/gestor.py`
- `diagnostico.py`
- `models.py`
- `__main__.py`
- panel: `app.js`, `ventanilla.js`, `app.css`
- `portal_reservas/servidor.py`: 429 también en su API, solo si se pide
- `scripts/preparar.py`, `scripts/demo_v20.py`, `scripts/recorrido_v20.js`
- `.github/workflows/ci.yml`
- pruebas

## 4. Defectos detectados y corregidos (cada uno con prueba)

| # | Defecto | Gravedad | Cómo se encontró | Corrección y comprobación |
|---|---|---|---|---|
| 1 | Con la base caída, una petición autenticada **cortaba la conexión sin respuesta** («Connection reset by peer»): el navegador no sabía si se había guardado algo | Alta | `resistencia_v20.py` contra PostgreSQL real | Respuesta 503 `BD_NO_DISPONIBLE` con `Retry-After`, sin detalles internos. `test_v20_bd_caida_responde_503_explicito_sin_cortar_conexion` **falla sin el arreglo y pasa con él** |
| 2 | `/api/health` respondía **200** con la base caída (un monitor externo no lo notaba) | Media | La misma prueba | 503 con el mismo cuerpo; `preparar.py` reconoce el 503. `test_v20_health_da_503_si_la_base_no_responde` |
| 3 | Una interfaz que **desaparecía** (adaptador USB desconectado) seguía como utilizable con su dirección vieja | Media | Prueba nueva de diagnóstico | Queda inactiva y sin dirección; se conserva su historial. `test_diagnostico_de_conectividad_y_cifras_24h_tras_una_restriccion` |
| 4 | El adaptador de laboratorio no encontraba días: el portal usa la región en minúsculas | Baja | `test_laboratorio_observa_verifica_y_se_detiene_ante_429` | Normalización en el adaptador |
| 5 | El script de resistencia dejaba **procesos huérfanos** si fallaba a mitad. Un motor huérfano, conectado a la misma base pero a otro portal, «recuperó» una confirmación reabriéndola porque su portal no la conocía | Alta para las pruebas (no es un defecto del producto) | Primera corrida | El script detiene siempre lo que lanza. **Riesgo real que deja al descubierto**: ver §6, limitación 1 |
| 6 | Comprobación de la demo con **carrera**: «el expediente vuelve a LISTO» se miraba en un instante concreto, y el motor puede volver a tomarlo enseguida | Baja (prueba) | Fallo intermitente en CI Windows 3.11 (corrida 43403982589) | Se comprueba por el historial del expediente |

## 5. Pruebas ejecutadas y resultados reales

| Prueba | Resultado | Evidencia |
|---|---|---|
| Suite completa SQLite + PostgreSQL 16 (Linux) | **584 pasan, 4 omitidas, 0 fallan** (MEDIDO, 8 min 50 s; las 4 omitidas son carreras que solo existen en PG y se ejecutan en su variante PG) | `docs/resultados/v2.0/pytest_sqlite_y_postgresql.txt` |
| Integración HTTP (procesos reales) | **15/15** (MEDIDO, modo rápido, SQLite) | `docs/resultados/v2.0/integracion_http/` |
| Resistencia con PostgreSQL real (A, B, C) | **26/26** (MEDIDO), dos corridas limpias seguidas | `docs/resultados/v2.0/resistencia/` |
| Demo en navegador real (Chromium), SQLite | **28/28** (MEDIDO) | `docs/resultados/v2.0/demo_v20.json` |
| Demo en navegador real, PostgreSQL | **28/28** (MEDIDO) | `docs/resultados/v2.0/postgresql/` |
| GitHub Actions (commit `6874862`) | **22/22 trabajos en verde** (MEDIDO), incluida la resistencia con el contenedor de PostgreSQL detenido y matado de verdad (Linux) y `demo_ventanilla.bat` desde la carpeta instalada (Windows, PowerShell 7) | corrida 43522562620 |

`tests/test_adaptadores.py` tiene 10 pruebas; las que usan base de datos se ejecutan en SQLite y en PG:
- contrato, una vez por cada uno de los 3 adaptadores;
- normalización de fechas;
- adaptador sin integración;
- adaptador simulado;
- captura manual;
- discrepancia que manda el expediente a revisión;
- laboratorio contra el portal real, con 429;
- API.

Escenarios de resistencia (fase 6), estado actualizado:

| # | Escenario | Estado | Evidencia |
|---|---|---|---|
| 1 | Seis atenciones consecutivas | Hecho | Demo + prueba |
| 2 | Diez atenciones en una jornada | Hecho | Demo |
| 3 | Veinte solicitudes en cola | Parcial | Laboratorio 1.7 (600 expedientes); no combinado con ventanilla |
| 4 | Dos operadores a la vez | Hecho | Prueba |
| 5 | Caída de Internet durante una atención | Hecho (SIMULADO) | Prueba de respaldo con sockets reales |
| 6 | Recuperación de la conexión | Hecho (SIMULADO) | Prueba |
| 7 | Reinicio inesperado del programa | Hecho | Demo (kill) |
| 8 | Reinicio de la computadora | **Hecho (equivalente)** | Resistencia C: motor, portal y PG detenidos de golpe a la vez. No es un reinicio físico del equipo |
| 9 | Duplicación accidental | Hecho | Demo + pruebas |
| 10 | Respuesta tardía del proveedor | Hecho | Prueba + resistencia A (latencia de 4 s) |
| 11 | Sin citas disponibles | Hecho | Demo |
| 12 | Límite de solicitudes | Hecho | Pruebas (pantallas y, ahora, también la API) |
| 13 | Caída y recuperación de la base | **Hecho** | Resistencia A y B con PostgreSQL detenido de verdad |
| 14 | Conmutación de red | Hecho (SIMULADO) | Pruebas |
| 15 | Confirmación pendiente verificada | Hecho | Pruebas + resistencia A y C |

## 6. Limitaciones (honestas)

1. **Dos instancias del motor sobre la misma base deben apuntar al mismo portal.** Si no, el líder
   puede «recuperar» una confirmación preguntando a un portal que no la conoce, y reabrirla
   (lo dejó al descubierto el defecto 5). Hoy no hay ningún control que lo impida. Recomendación:
   guardar una huella del portal configurado y que el autodiagnóstico avise si dos instancias
   difieren (ESTIMADO: 1 día).
2. La captura manual **no** cierra la atención ni cuenta en «citas del día» (es deliberado). Unirlas
   requiere una decisión de producto.
3. El único adaptador con red es el de laboratorio. **Ningún proveedor real** está conectado ni probado.
4. Gestor de conexiones: no verificado con interfaces físicas (este entorno solo tiene una).
5. Rendimiento de un solo proceso: sin cambios respecto a la 1.7 (se satura hacia 20-40 peticiones/s).

## 7. Simulado frente a real

| Función | Real | Simulado |
|---|---|---|
| PostgreSQL detenido o matado | **Real** (`pg_ctl`; en CI, `docker stop` y `docker kill`) | — |
| Motor y portal matados | Procesos reales | — |
| Navegador | Chromium real | El operador lo maneja un script |
| Portal de citas | — | Portal de prueba propio (127.0.0.1) |
| Adaptadores | Laboratorio contra el portal real de prueba | `simulado`, y el «flujo oficial» de la demo |
| Interfaces de red | Descubrimiento real del equipo | Wi-Fi, USB y módem en las pruebas |
| Personas | — | Datos sintéticos |

## 8. Conectividad

- Autodiagnóstico real en Linux: el área Conectividad ve `eth0` como utilizable. Con el portal en
  local dice «en este mismo equipo», que es lo correcto: no aparenta medir redes.
- Prueba con dos interfaces simuladas:
  - un 429 real del portal aparece en las cifras de 24 h y como AVISO con «revisa la cuota»;
  - en la sonda TCP, la interfaz inexistente da AVISO y la buena da OK;
  - sin interfaces, da CRÍTICO con qué hacer.
- Demo: la pantalla Conexiones muestra 0 restricciones en 24 h tras 10 atenciones.

## 9. Rendimiento

Sin medición nueva de carga. Tiempos MEDIDOS:
- `/api/health` vuelve a OK unos 0,02 s después de que vuelve PostgreSQL;
- la confirmación se recupera por su clave en 8,2 s (con una gracia de 5 s);
- el arranque completo tras el corte simultáneo tarda 2,9 s;
- la demo completa tarda unos 40 s en SQLite.

Supervisión integrada de la 1.7: de 2,6 s a 0,64 s (p50) en PG, escala 3 (medido en la 1.7).

## 10. Pendiente en Windows

- **MSI físico: nada probado.** Falta la instalación, `ventanilla.bat`, `demo_ventanilla.bat`, el
  Wi-Fi y el cable reales, y suspender el equipo a mitad de una atención. La lista está en
  `GUIA_VENTANILLA_WINDOWS.md` §G.
- En las máquinas Windows de GitHub corren la suite y la demo de ventanilla ×3. Es nuevo que
  `demo_ventanilla.bat` se ejecuta **desde la carpeta instalada** con `instalar.ps1` (resultado del CI en §5).
- La resistencia con PostgreSQL solo corre en Linux (contenedor). En Windows no se ha probado la caída de la base.

## 11. Rama y commits

Rama `fase-2.0`, sin fusionar:
- `788c651`: integración de la 1.7;
- `c677cfc`: supervisión y ventanilla;
- `d12eab9`: resistencia y defectos 1-2;
- `af53a75`: adaptadores P2;
- `8cbacb2`: conectividad y defecto 3;
- `736ac82`: Windows;
- y los de la corrección de la demo y este informe (§5 enlaza la corrida final del CI).

## 12. Siguientes pasos

Lo que **impide declarar terminada la 2.0** (no se puede resolver desde este entorno):
1. Probar en el MSI (sección G de la guía): instalación, ventanilla a mano y demo automática.
2. Conmutación con redes físicas reales (cable + Wi-Fi) contra un portal en otro equipo.
3. Para usar un proveedor real: autorización formal, credenciales y un adaptador nuevo.

Recomendado después (lo puedo hacer yo):

4. Huella del portal por instancia (limitación 1 de §6).
5. Decidir si la captura manual cierra la atención de ventanilla.
6. Escenario 3 combinado: 20 solicitudes en cola con ventanilla y motor a la vez.

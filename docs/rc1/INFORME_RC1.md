# Cauce Monitor Engine · RC1 · informe de entrega

Fecha: 2026-11-19. Rama: `claude/project-thread-y5dh74` (sin fusionar a master, sin despliegue, sin tocar
Jarvis, sin conectar proveedores externos). Todo con datos **ficticios** y el portal de laboratorio **propio**.

**Lo que NO está verificado:** el MSI físico (no hubo acceso). Windows solo se valida en GitHub Actions.

## 1. Rama y commits

| Commit | Qué |
|---|---|
| `e63d5e6` | Integración: `claude/recorrido-integral-v2.3` (f23cb84) + fusión de `claude/project-thread-2wwz8e` (v2.3.1, 21d5a96). Sin conflictos. |
| `a9f2938` | RC1: importador, mesa de control, expedientes, planificación por selección, lanzador, demostración, pruebas. |
| `96bea73` | Restaura capturas de v2.0 que la regresión `demo_v20` sobrescribió (error mío al commitear). |
| (siguiente) | Este informe con el resultado de la CI. |

Ancestros comprobados con `git merge-base`: v2.3.1 desciende de integración v2.2.2 (2546ed1); el recorrido
integral sale de 93328a3 (v2.3.1 sin sus dos últimos cambios, que eran solo documentación). Ninguna rama
original se modificó. El fijado de Jarvis sigue en 9f79a3c (no se tocó `jarvis/`).

## 2. Qué se integró en una sola instalación

| Función | Estado en RC1 | Dónde |
|---|---|---|
| Mesa de control (pantalla principal del admin) | **Nuevo** | menú «Mesa de control» (`#panel`) |
| Importador Excel/CSV por lotes | **Nuevo** | «Importar» |
| Expedientes: filtros, consentimiento, fechas, al plan por selección | **Nuevo** | «Expedientes» |
| Expedientes y consentimiento verificable (v2.2) | Reutilizado sin cambios de reglas | API de consentimiento |
| Planificador (capacidad, compatibilidad, duplicados, propiedad) | Reutilizado; ahora usa el rango aceptable del expediente | `planificador/plan.py` |
| Observador del calendario | Reutilizado | tarjeta en la Mesa de control |
| Calendario del laboratorio | Reutilizado | «Calendario lab» |
| Ventanilla v2.3.1 | Intacta, función secundaria | «Ventanilla» |
| Historial y folios | Reutilizado (+ evento `IMPORTADO` con lote y fila) | «Historial» en cada expediente |
| Respaldo, restauración, retención | Sin cambios | `python -m monitoreo.migrate`, `recorrido_integral.bat` |

Punto de entrada Windows: **`cauce_rc1.bat`** (instalación aislada en `resultados\cauce_rc1`). Guía:
[GUIA_WINDOWS_RC1.md](GUIA_WINDOWS_RC1.md).

## 3. Decisiones (y por qué)

1. **No había importación masiva** (solo exportación CSV y un comentario «creados por importación» en
   `validar_pendientes`). Se implementó reutilizando `expedientes.crear`: mismas validaciones, estados y
   detección de duplicados que el alta manual. No hay un segundo camino de alta.
2. **Lector .xlsx con la biblioteca estándar**, sin añadir `openpyxl`. Ventaja: cero dependencias nuevas que
   instalar en Windows y control total de la seguridad (sin macros, sin DTD/entidades, sin vínculos externos,
   límite de tamaño descomprimido). Desventaja: menos tolerante a libros raros. Se comprobó que lee un libro
   generado con openpyxl (como lo haría Excel): hojas ocultas, fórmulas, fechas y formato `00000000`.
3. **Fórmulas:** la celda se rechaza con explicación; no se ejecuta ni se usa su último valor guardado
   (podría estar desactualizado o manipulado). Coste: una lista con fórmulas inocentes exige «pegar como valores».
4. **Importar ≠ consentir.** Todo expediente importado queda INCOMPLETO con «falta consentimiento» hasta que el
   administrador registra uno con la huella del documento firmado. La columna «consentimiento» del archivo se
   ignora y se avisa. Consecuencia honesta: el consentimiento sigue siendo **uno por persona** (es la garantía
   legal), pero ya no hace falta abrir una atención por persona.
5. **Contraseñas:** columnas con encabezados de contraseña/clave/NIP/token se descartan antes de la vista
   previa; no pueden asignarse a ningún campo. Comprobado en la base: el valor ficticio no aparece en ninguna tabla.
6. **No duplicar al reimportar:** clave por persona (identificador externo, o nombre+nacimiento+trámite, o, si
   falta el nacimiento, nombre+trámite+contactos) con restricción **UNIQUE** en `importacion_filas.clave_creado`.
   También funciona con dos cargas a la vez (prueba con 3 hilos). Si una persona ya importada se vuelve a cargar
   con datos corregidos, se informa «Ya existe» y se corrige en su expediente (no se actualiza en masa: evita
   sobrescribir datos revisados).
7. **Teléfono y correo importados:** la «confirmación» (doble tecleo) existe para lo que se escribe a mano; en
   la importación el archivo es la fuente y se usa el mismo valor. Es una decisión explícita, no un atajo oculto.
8. **Operar una coincidencia** usa el flujo de Ventanilla (campos manuales, día, confirmación humana, folio
   comprobado). La atención se abre sola al pulsar «Preparar operación»; no es un paso manual de la importación.
9. **El administrador también opera:** el lanzador RC1 vincula el usuario `admin` a un operador (todas las
   regiones). No se añadieron cuentas ni permisos nuevos; las rutas `/api/admin/*` exigen rol administrador.
10. **Avisos:** durante una importación el aviso «le faltan datos» se agrupa en **una** alerta por lote (antes
    habría sido una por fila). Los demás avisos no cambian.

## 4. Campos

Inventario completo con procedencia, validaciones e importabilidad: [INVENTARIO_CAMPOS_RC1.md](INVENTARIO_CAMPOS_RC1.md).
Único campo nuevo: fecha solicitada y rango aceptable (pedido explícito). El formulario conocido es el del
portal de **laboratorio**: catálogo de trámites, sucursales y formato del registro previo **falta confirmarlos
con documentación real**.

## 5. Evidencia del recorrido (con la interfaz, 26/26)

`scripts/recorrido_rc1.py` arranca el laboratorio en una carpeta temporal, genera la lista ficticia y un
Chromium hace solo clics y teclas como administrador. Luego el script comprueba por su cuenta (portal, base y
registros). Resultado: [recorrido_rc1.json](recorrido_rc1.json), 19 capturas en [capturas/](capturas/).

| Paso pedido | Resultado | Captura |
|---|---|---|
| 1 Abre Cauce | entra a la Mesa de control | 01 |
| 2 Importa 6 | vista previa 6 nuevos, lote LOT-000001 con 6 creados; contraseña descartada | 02, 03 |
| 3 Clasifica y detecta faltantes | 4 «falta consentimiento», 2 «faltan datos» (nacimiento; teléfono y correo) | 04 |
| 4 Incompletos bloqueados | ninguno seleccionable para el plan | 04 |
| 5 Revisa los incorporables | consentimiento de 3 con huella → 3 «Listo para planificar» | 05, 06 |
| 6 Plan solo con elegibles | 3 aceptados; los 3 bloqueados fuera | 07, 08 |
| 7 Calendario publica fecha | observador NOR-1/alta activo; se abre 2027-01-14 | 09, 10 |
| 8 Detecta la coincidencia | EXP-000001 (Ana); KPI coincidencias 1, confirmadas 0 | 11 |
| 9 Operación simulada y folio | folio `DIA-20270114-00001T`, **confirmación SIMULADA** | 12–14 |
| 10 Historial conserva | folio en historial con IMPORTADO, consentimiento, plan; aparece en el panel | 15, 16 |
| 11 Reimportar no duplica | vista previa 0 nuevos / 6 ya existen; LOT-000002 creó 0; siguen 6 expedientes | 17, 18 |

Comprobaciones independientes: el folio está CONFIRMADO en el portal y es del día abierto; solo EXP-000001 tiene
cita; Bruno (rango que no incluye el día) y Fabiola (otro trámite) siguen esperando; la cadena de sellos del
consentimiento es íntegra; ni datos personales ni la contraseña del archivo aparecen en los registros.

Lista de demostración: [clientes_ficticios_rc1.xlsx](clientes_ficticios_rc1.xlsx) y `.csv` (generadas hoy, mes
2027-01; `cauce_rc1.bat` genera una con fechas vigentes cada vez que arranca).

## 6. Pruebas y CI

Nuevas (proporcionales): `tests/test_importacion_rc1.py`, 6 pruebas: CSV con vista previa, importación y
reimportación; Excel con hojas, fechas, ceros y fórmulas; archivos peligrosos; permisos (operador recibe 403 en
las 5 rutas); consentimiento → plan con rango propio; tres cargas simultáneas sin duplicar.

Regresiones locales (Linux, SQLite) sobre la rama RC1:

| Prueba | Resultado |
|---|---|
| `recorrido_rc1.py` (nuevo) | 26/26 |
| `recorrido_ventanilla_v23.py` (v2.3.1) | 21/21 |
| `recorrido_integral.py` (respaldo/restauración/retención) | 32/32 |
| `demo_v20.py` / `aceptacion_v21.py` / `demo_v22.py` | 28/28 · 42/42 · 36/36 |
| Suite completa pytest (SQLite) | 601 pasan, 8 omitidas (4 carreras solo PostgreSQL, 1 rama v2.2.2, 2 sin yaml/jsonschema, 1 sin PG), 0 fallan |

CI de GitHub, corrida 95 (commit 96bea73): 26 de 30 trabajos pasan. Fallaron los 4 trabajos «Reservaciones de
laboratorio» (Linux y Windows 3.11/3.12/3.13), solo en el paso nuevo RC1: la CI pasa `--salida` como ruta
relativa y el navegador corre desde `scripts/`, así que no encontraba el `.xlsx` (ENOENT). Error mío del script
de demostración, no del producto; en local había usado una ruta absoluta. Corregido (ruta resuelta a absoluta) y
reproducido en local con ruta relativa: 26/26. Las regresiones v2.3.1 (21/21) e integral (32/32) pasaron en esa
misma corrida. Resultado de la corrida con la corrección: RESULTADO_CI2

Ajustes a pruebas existentes (no son umbrales): `test_captura_datos` y `test_interop_jarvis` esperaban la
migración 0015 como última; ahora es 0016. `scripts/e2e_panel.js` entra por `#inicio` porque el admin del
laboratorio ahora aterriza en la Mesa de control.

## 7. Respaldo y restauración

Sin cambios de código; el recorrido integral sigue pasando (32/32) con las tablas nuevas incluidas en el respaldo
SQLite. **Riesgo abierto:** restaurar un respaldo anterior puede devolver estados viejos; hoy no hay bloqueo de
operaciones inciertas tras restaurar ni diario de eventos críticos fuera de la base. El diseño
(`DISENO_recuperacion_segura.md`) espera tu aprobación y **no** se implementó aquí. La no duplicación tras
restaurar se vio solo con el portal de laboratorio, que respeta la clave de idempotencia: **no demuestra** que
un proveedor externo lo haga. Restauración en PostgreSQL: no cubierta.

**Integración prevista del componente de recuperación** (lo implementa otra rama, aprobado por el propietario el
2026-11-19; aquí no se tocó). Para que se conecte sin conflictos: (1) la última migración de RC1 es
`0016_rc1`; la suya deberá encadenarse después o resolver dos cabezas al fusionar. (2) RC1 no añade caminos nuevos
para los eventos críticos: consentimiento y revocación siguen en `captura/consentimiento`, la confirmación con folio
sigue en el flujo de Ventanilla/ejecutor, y la importación usa `expedientes.crear`; enganchar el diario ahí cubre
también la RC1. (3) Las rutas `/api/admin/plan/agregar` y «Preparar operación» de la mesa de control deberán
respetar el bloqueo `RECUPERACION_PENDIENTE`; la mesa de control es el sitio natural para mostrarlo.

## 8. Limitaciones y riesgos conocidos

- El importador y la mesa de control **no se probaron en PostgreSQL** (las pruebas nuevas usan SQLite; la CI de
  Linux corre la suite con PostgreSQL, pero mi archivo de pruebas no se parametrizó para él).
- La mesa de control calcula la situación expediente por expediente: adecuado para cientos; con miles no está
  medido.
- `monitoreo/version.py` sigue diciendo `2.2.2-dev` (cambiarlo afecta el contrato con Jarvis; queda para cuando
  decidas el número de versión).
- El menú tiene 16 secciones; la Mesa de control es la primera, pero el resto sigue visible. Simplificarlo es un
  cambio de producto que conviene decidir contigo.

## 9. Bloqueos reales para usar el sistema en una computadora física

1. **No probado en el MSI.** Primer paso: `recorrido_rc1.bat` (guía, sección 3).
2. **Node.js + Chromium** son necesarios para el observador (`preparar_navegador.py`); sin ellos el plan no
   recibe fechas.
3. **Recuperación segura sin implementar** (sección 7): no restaurar respaldos en uso real hasta resolverlo.
4. **Sin proveedor real ni autorización:** la confirmación es simulada contra el portal de laboratorio; las
   validaciones «solo datos sintéticos» (teléfonos 555, correos .test) deben seguir activas.
5. **Catálogos reales** (trámites, sucursales, formato del registro previo) y política de consentimiento por
   documentar antes de cargar listas reales.

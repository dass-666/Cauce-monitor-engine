# Contrato Monitor ↔ conector `cauce.conector/1` — primera capa

Estado: **PROPUESTO por Monitor**. Implementado en esta rama: esquemas, validadores, simulador, registro en memoria y pruebas (`monitoreo/conector/`, `tests/test_conector_contrato.py`, 38 pruebas locales). **No implementado**: lanzamiento de procesos con datos de expedientes, tabla/migración, diario `CONECTOR_*`, aislamiento de sistema operativo, conector real.

## 1. Comparación con otros contratos
| Fuente | Disponible | Resultado |
|---|---|---|
| Informe previo de Monitor (`INFORME_INTEGRACION_CONECTOR_v1.md`) | sí | Se unifica abajo; cambios: respuesta sin texto libre, `INCIERTO` como estado propio, huella de campos devuelta por el conector, `envio_permitido:false` obligatorio. |
| Auditoría de Monitor (`AUDITORIA_INTEGRACION_FORM_NETWORK.md`) | sí | Su preferencia por HTTP se concilia: Monitor es proceso padre y única puerta; HTTP sigue siendo el transporte alterno si el conector va a otra máquina. |
| Contrato / auditoría del **Conector Universal** | **NO** (no está en el proyecto ni en la carpeta compartida) | **No contrastable.** Nada aquí afirma qué hace el conector real. Cada fila marcada «A contrastar» es una suposición de Monitor. |
| Network Engine | **NO** | No contrastable; fuera de esta capa. |

Puntos a contrastar con el conector real (todos PENDIENTES): versión de protocolo (`cauce.conector/1`), nombres de campo, lista de estados y códigos, uso de UUID v4, huella SHA-256 de JSON canónico (claves ordenadas, UTF-8, sin espacios), caducidad máxima de 120 s, si el conector puede tolerar «una solicitud por proceso» y salida sin texto libre.

## 2. Contrato unificado (esquemas en `monitoreo/conector/esquemas/`)
- **Versión:** `contrato:"cauce.conector/1"`; cualquier otro valor se rechaza (`CONTRATO_NO_SOPORTADO`).
- **Identificadores:** `id_solicitud` UUID v4 generado por Monitor; `clave_idempotencia` opaca (`^[A-Za-z0-9_.:-]{8,128}$`, sin datos personales).
- **Acciones:** solo `PREPARAR` y `VERIFICAR`. No existe acción de envío; `envio_permitido` debe ser `false` y la respuesta `enviado:false`.
- **Estados del conector (respuesta):** `PREPARADO`, `RECHAZADO`, `ERROR`, `CADUCADO`, `INCIERTO`. **Estados de Monitor (por solicitud):** `CREADA → AUTORIZADA → LANZADA → PREPARADO | RECHAZADA | ERROR | CADUCADA | INCIERTA | CANCELADA`. `PREPARADO` ⇔ código `OK` y solo él; no significa «enviado».
- **Códigos:** `OK, ACCION_NO_PERMITIDA, PORTAL_NO_PERMITIDO, PORTAL_NO_DISPONIBLE, CAMPO_NO_RECONOCIDO, HUELLA_NO_COINCIDE, SOLICITUD_CADUCADA, TIEMPO_AGOTADO, CONTRATO_NO_SOPORTADO, ERROR_INTERNO`. Salidas de proceso: 0 respuesta emitida, 2 entrada inválida (reutiliza la convención del ejecutor; 3 tiempo y 4 cancelada los fija quien lance).
- **Caducidad y revisión humana:** `caduca − emitida ≤ 120 s`; `tiempo_max_s ≤ 120`; la solicitud lleva `autorizacion` con `revision_humana:true`, `consentimiento_vigente:true`, quien aprobó (`usr-N`) y cuándo (no posterior a la emisión). Una solicitud caducada no se lanza; una respuesta tardía se descarta (estado `CADUCADA`).
- **Idempotencia:** misma `clave_idempotencia` + mismo contenido ⇒ se devuelve el registro existente, no se relanza; mismo clave con otra huella/acción ⇒ `IDEMPOTENCIA_CONFLICTO`.
- **Integridad:** `huella_campos` (SHA-256 del JSON canónico de `campos`); el conector la devuelve en `evidencia.huella_campos` y Monitor exige que coincida; `huella_pagina` es obligatoria para `PREPARADO`.
- **Resultados inciertos:** respuesta inválida, con datos personales, incoherente, o `INCIERTO` ⇒ la solicitud pasa a `INCIERTA`; **solo una persona** la concilia (`conciliado_por`), nunca se asume éxito ni se reintenta sola.
- **Datos personales:** (a) la respuesta no tiene texto libre: solo enums, nombres de campo `^[a-z][a-z0-9_]{0,39}$`, huellas y enteros; (b) red de seguridad: se rechaza cualquier cadena que repita un valor enviado o parezca correo, teléfono, fecha o CURP; (c) los mensajes de error y el diario guardan solo la clase del error, jamás el valor; (d) **stderr del conector no debe guardarse ni mostrarse** (pendiente al implementar el lanzador). Portal solo `http://127.0.0.1:<puerto>`.

## 3. Lo que existe y lo que no
Existe y se reutiliza: `monitoreo/interop/esquema.py` (validador sin dependencias), `consentimiento.exigir`, `ejecutor.detener_arbol`/`entorno_hijo`. **No existe:** tabla de solicitudes y migración, evento de diario `CONECTOR_*`, rol «conector», lanzador, aislamiento, y cualquier conector real.

## 4. Aislamiento de sistema operativo (capacidad BLOQUEADA)
`monitoreo/conector/aislamiento.py::puede_lanzar()` devuelve siempre `False` y una prueba lo exige. No se consideran suficientes entorno limpio, Job Object ni «sin credenciales»: un proceso Python del mismo usuario puede abrir `data\*.db` y `.env`.
Diseño que sí lo impediría en Windows (PROPUESTO, sin probar):
1. Cuenta local dedicada sin privilegios (p. ej. `cauce-conector`), sin sesión interactiva ni pertenencia a Administradores.
2. ACL NTFS: la carpeta de datos, respaldos, `.env` y `.browsers` de Monitor con permiso **solo** para la cuenta de Monitor y SYSTEM, sin herencia hacia la cuenta del conector; el conector solo lee su carpeta de programa y escribe en un directorio temporal propio.
3. Lanzar el hijo con esa cuenta (credenciales protegidas por DPAPI de Monitor o servicio dedicado), más Job Object **además** (memoria, árbol, `KILL_ON_JOB_CLOSE`), nunca en lugar de lo anterior.
4. **Condición de desbloqueo:** una prueba automática (Windows físico y CI) en la que el hijo intenta abrir la base de Monitor, `.env` y la clave del diario y obtiene `PermissionError`, y una prueba positiva de que sí lee lo que necesita. Sin esa evidencia, `puede_lanzar()` sigue en `False`. En CI hosted (usuario administrador) esta prueba probablemente solo se pueda hacer en el MSI.
5. Linux (CI): equivalente con usuario/permisos 0700; no sustituye a la prueba en Windows.

## 5. Riesgos pendientes
Contrato del conector real sin contrastar; aislamiento inexistente (bloqueo vigente); sin cifrado en reposo de datos personales ni rol «conector»; registro solo en memoria (reinicio de Monitor pierde idempotencia hasta existir tabla); falso negativo posible del detector de datos personales con formatos no previstos (por eso la respuesta no admite texto libre); el flake del observador en Windows 3.11 sigue sin causa demostrada (sección 6).

## 6. Investigación del fallo de CI (run 108, Windows 3.11)
- Artefacto `reservaciones-windows-py3.11` (ID 14601731259): **no se pudo descargar**; el proxy del entorno bloquea `productionresultssa8.blob.core.windows.net`. Hace falta permitir ese host (o bajar el zip a mano).
- Del log del trabajo (sí accesible) — VERIFICADO: en el mismo trabajo, `test_configuracion_persistente_restaura_tras_reinicio_y_no_duplica` midió `restauracion_tras_caida_s=14.1` y pasó; el recorrido v2.1 (`scripts/recorrido_v21.py:316`) esperó 90 s sin ver el observador «ACTIVA». En el log no hay lista de procesos: **no hay evidencia a favor ni en contra de procesos Node/Chromium huérfanos**. Hipótesis (`Popen.kill()` no mata el árbol en Windows) NO demostrada; no se corrigió ni se tocó ningún timeout.
- Siguiente paso: con el artefacto, buscar en el log del motor tras el reinicio errores del observador (perfil bloqueado, `DETENIDO_POR_FALLOS`, arriendo `obs:` ajeno); si no basta, añadir al recorrido un diagnóstico (lista de procesos node/chrome antes y después del `kill`) que se imprima solo al fallar.

## 7. Siguiente paso recomendado
Subir el contrato/auditoría del Conector Universal y contrastar la sección 1; permitir el host del artefacto para cerrar la sección 6; después, con autorización, tabla de solicitudes + migración (sobre una base de pruebas, nunca la del MSI) que pase las mismas pruebas del registro en memoria. El lanzador queda bloqueado hasta la prueba de la sección 4.

# Solución de problemas · v1.7

Primero, siempre: doble clic en **`diagnostico.bat`** (o *Configuración → Autodiagnóstico*). No
cambia nada y dice, para cada problema, qué significa y qué hacer. Lo que sigue son los casos más
habituales.

## Instalación y arranque

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| "python no se reconoce" | Python sin *Add python.exe to PATH*. | Reinstala Python 3.13 marcando la casilla; cierra y abre PowerShell. |
| `instalar.ps1` no se ejecuta | Política de PowerShell. | Usa exactamente `powershell -ExecutionPolicy Bypass -File .\instalar.ps1`. |
| El panel no abre | El motor no arrancó o el puerto está ocupado. | Mira la ventana de `iniciar.bat`; abre a mano http://127.0.0.1:8000. |
| "database is locked" o la base se daña | Carpeta dentro de OneDrive u otro sincronizador. | Mueve la instalación a `C:\` (con `actualizar.ps1`, que copia y verifica los datos). |
| Tras actualizar no entra con su contraseña | Se usó otra carpeta de datos. | `actualizar.ps1 -Anterior <carpeta anterior>` copia `data\` y `.env`; la anterior queda intacta. |

## Operación diaria

| Síntoma | Qué significa | Qué hacer |
|---|---|---|
| Franja **Sin conexión** en el panel | El navegador no llega al motor. Los datos en pantalla se conservan pero pueden estar desactualizados. | Comprueba que el motor sigue en marcha. Al volver la conexión, la franja desaparece sola. |
| Un expediente se queda **INCOMPLETO** | Faltan datos obligatorios. | Ábrelo: la lista de *faltan* dice cuáles. El motor no lo asigna hasta completarlo. |
| Una reservación queda **En verificación** | El portal no confirmó con claridad. | No confirmes otra vez. El motor consulta el portal; si pasa de 30 min, el diagnóstico lo marca CRÍTICO y pide intervención. |
| *Mi trabajo* muestra **A medias** | Una operación se interrumpió (navegador cerrado, red, reinicio del motor). | **Continuar** la retoma en el mismo paso. |
| El calendario salta al **mes siguiente** | Este mes ya no tiene días libres. | Es intencionado (v1.7) y lo indica un aviso. |
| Calendario **sin comunicación** | No se pudo comprobar el portal. Los días se muestran como *desconocidos*. | Revisa la fuente en *Fuentes*; el diagnóstico indica si hay errores repetidos. |
| Un operador no recibe trabajo | Inactivo, desactivado tras una redistribución, sin la región/sucursal o con `operador_ausente_s` activado. | *Supervisión* muestra su presencia; la explicación de las asignaciones (**¿Por qué?**) dice por qué se descartó. |
| Un operador aparece **CONEXION_PERDIDA** | Sesión abierta sin señal reciente. | Pregúntale antes de redistribuir: puede seguir trabajando. |
| No llegan notificaciones por un canal | Canal deshabilitado, limitado a ciertas categorías, categoría silenciada o destino caído. | *Notificaciones* → tabla de canales (entregas por estado). Las CRÍTICAS no se pueden silenciar. |
| Muchas notificaciones iguales | Una causa que se repite. | Se agrupan solas dentro de `alertas_agrupar_s` (300 s) con su número de repeticiones. |

## Avisos del autodiagnóstico (v1.7)

| Aviso | Qué hacer |
|---|---|
| *Cargas de operadores no cuadran* | Se repara solo en el siguiente autodiagnóstico y queda en la auditoría. |
| *Notificaciones atascadas en «enviando»* | Se devuelven a la cola solas. |
| *Entregas huérfanas* | Botón **Reparar (con confirmación)**: borra filas, pide motivo. |
| *Operaciones abandonadas* | Avisa al operador o redistribuye desde *Supervisión*. |
| *Confirmaciones en verificación > 30 min* (CRÍTICO) | Comprueba el folio en el portal y resuelve a mano. |
| *Expedientes incoherentes* | El detalle dice cuál y por qué; repórtalo con el diagnóstico adjunto. |

## Comprobaciones completas

- `pruebas.bat`: toda la batería de pruebas (unos minutos).
- `laboratorio.bat`: laboratorio de unos 10 minutos con datos ficticios en carpetas temporales;
  escribe `resultados\laboratorio_v17\A\laboratorio.json` con cada comprobación OK o FALLO.

## Volver atrás

- Si actualizaste con `actualizar.ps1`, la carpeta anterior está intacta: usa su `iniciar.bat`.
- Para restaurar un respaldo (con el motor cerrado):
  `.\.venv\Scripts\python -m monitoreo.migrate restaurar data\respaldos\<archivo>.db`.
  No copies archivos de base de datos a mano.

## Al pedir ayuda, adjunta

1. La salida de `diagnostico.bat` (en JSON: `.\.venv\Scripts\python -m monitoreo.diagnostico --json > diagnostico.json`).
2. `data\logs\` del día.
3. Qué estabas haciendo y a qué hora. **No** envíes la base de datos: contiene datos de expedientes.

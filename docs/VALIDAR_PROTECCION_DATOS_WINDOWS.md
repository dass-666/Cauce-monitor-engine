# Validación física de la protección de datos (Windows, entorno aislado)

Para la ventana del domingo. Unos **50 minutos**. Solo **datos ficticios**.

**No se toca `C:\cauce-msi` ni `C:\cauce-respaldos`.** Todo ocurre en una carpeta nueva,
`C:\cauce-piloto-prueba`, y en una memoria USB sin datos reales. En los ejemplos la USB es `E:`. Si es otra
letra, cámbiala.

## 0. Antes de empezar (2 min)

Anota el estado de la MSI para comprobar al final que no cambió:

```powershell
Get-ChildItem C:\cauce-msi -Recurse -File | Measure-Object -Property Length -Sum | Select-Object Count,Sum
Get-ChildItem C:\cauce-msi\data -File | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name,LastWriteTime
```

**No arranques la MSI durante la validación.** Si arranca, sus archivos cambian por sí solos y esta comparación ya no sirve.

## 1. Instalación aislada (10 min)

```powershell
git clone --branch claude/project-thread-wa0xqd https://github.com/dass-666/Cauce-monitor-engine C:\cauce-piloto-prueba
cd C:\cauce-piloto-prueba
.\instalar_cauce.bat
```

Esperado: termina sin errores. Hay `.venv\` y `node_modules\playwright`.

## 2. Pruebas automatizadas en este equipo (5 min)

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_respaldo_externo_base.py tests/test_respaldo_externo_diario.py tests/test_recuperacion_v24.py
```

Esperado: todo pasa, **incluida la prueba de DPAPI** (`test_clave_protegida_con_dpapi_en_windows`). Hasta hoy solo
había pasado en Windows de GitHub, nunca en el equipo físico.

## 3. La USB se reconoce como otra unidad (2 min)

```powershell
mkdir E:\CaucePrueba\diario, E:\CaucePrueba\respaldos
.\revisar_copia_diario.bat E:\CaucePrueba\diario
.venv\Scripts\python.exe -m monitoreo.respaldo_externo revisar E:\CaucePrueba\respaldos
```

Esperado: las dos dicen **apta**, con `"misma_unidad": false`. Si alguna dice «MISMA unidad» siendo la USB,
**anótalo**: es el caso de `st_dev` que aún no se ha verificado.

## 3b. Cifrado BitLocker To Go y su recuperación (10 min)

El equipo del piloto tiene Windows 11 Pro, así que puede cifrar. Haz esta prueba con la USB de prueba. **Antes de
guardar datos reales, repítela con la USB del piloto.**

1. En el Explorador, haz clic derecho sobre `E:` y elige «Activar BitLocker». Elige «Usar una contraseña».
2. Guarda la **clave de recuperación** (48 dígitos) en un lugar **distinto** de la USB y del equipo: impresa
   en un sobre cerrado o en la cuenta Microsoft de la empresa. **No** la guardes junto a la clave exportada del
   diario.
3. Elige «Cifrar solo el espacio usado» y «Modo compatible». Espera a que termine.
4. Comprueba, en PowerShell como administrador: `manage-bde -status E:`. Esperado: `Estado de protección:
   Protección activada` y `Porcentaje cifrado: 100%`.
5. Retira la USB y vuelve a conectarla. Esperado: Windows **pide la contraseña** y sin ella no se ve el contenido.
6. **Prueba de recuperación:** desbloquéala con la clave de recuperación en lugar de la contraseña («Más
   opciones» → «Escribir clave de recuperación»). Esperado: se abre. Si la clave guardada no funciona, **para**:
   esa USB no sirve para datos reales hasta tener una clave de recuperación que funcione.
7. Ya desbloqueada, repite el paso 3 (`revisar`). Esperado: sigue siendo **apta** y con `"misma_unidad": false`.

**Importante para el día a día:** si el equipo se reinicia, la USB queda bloqueada hasta que alguien escriba la
contraseña. Mientras tanto, el motor marca `COPIA_NO_DISPONIBLE` y bloquea las operaciones con el portal (es lo
esperado). Puedes activar «Desbloquear automáticamente en este equipo»: la USB se abre sola en ese equipo, pero
sigue cifrada si se pierde o se la llevan. Es la opción recomendada para el piloto.

## 4. Simulacro completo con la USB (8 min)

```powershell
.\simulacro_proteccion.bat E:\CaucePrueba
```

Monta un laboratorio aislado en una carpeta temporal y hace lo siguiente:
- copia el diario y respalda la base **en la USB**;
- ensaya la restauración;
- hace cambios posteriores al respaldo;
- **restaura desde la USB** y comprueba el bloqueo;
- concilia, comprueba la continuidad y aplica la retención.

Esperado: `RESULTADO: 52/52`. Evidencia en `resultados\simulacro_proteccion\recorrido_integral.json`. Al terminar,
borra su subcarpeta de la USB.

## 5. Desconectar la USB con el motor en marcha (10 min, a mano)

1. En `C:\cauce-piloto-prueba\.env`, añade estas líneas:
   ```
   MONITOREO_DIARIO_COPIA=E:\CaucePrueba\diario
   MONITOREO_RESPALDO_EXTERNO=E:\CaucePrueba\respaldos
   ```
2. Ejecuta `iniciar.bat` y entra como administrador. Usa solo datos de demostración y no registres personas reales.
3. Ejecuta `respaldar_externo.bat`. Esperado: `respaldo externo hecho y verificado`.
4. Ejecuta `verificar_respaldo.bat E:\CaucePrueba\respaldos\<el archivo que salió>.db`. Esperado: `integro y se puede restaurar`.
5. **Retira la USB** (sin expulsarla). En «Recuperación», pulsa «Actualizar». Esperado: `COPIA_NO_DISPONIBLE` y las operaciones con el portal bloqueadas.
6. Con la USB fuera, ejecuta `respaldar_externo.bat`. Esperado: termina con error («no está conectada») y no deja archivos a medias.
7. Vuelve a conectar la USB, con la **misma letra**. Pulsa «Actualizar». Esperado: deja de estar bloqueado.
   - Si sigue bloqueado, **anótalo** con una captura. No lo fuerces.
8. Ejecuta `diagnostico.bat`. Esperado: sin avisos de «Respaldo externo».
9. Detén el motor.

## 6. Comprobar que la MSI no cambió (1 min)

Repite los comandos del paso 0. Esperado: el mismo número de archivos, el mismo tamaño y las mismas fechas.

## 7. Qué enviar

- `resultados\simulacro_proteccion\recorrido_integral.json`
- La salida de los pasos 2 y 3. Basta con copiar el texto.
- La salida de `manage-bde -status E:` (paso 3b.4) y si funcionó la clave de recuperación (paso 3b.6).
- Las capturas de los pasos 5.5 y 5.7.
- Cualquier diferencia con lo esperado, tal cual aparezca.

Al terminar puedes borrar `C:\cauce-piloto-prueba` y `E:\CaucePrueba`.

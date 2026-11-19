# Validación física en Windows (MSI) · rama `claude/project-thread-rlypc5`

Una sola guía para probar en el equipo físico lo que hasta ahora solo se ha verificado en GitHub Actions
(Windows Server) y en Linux. Todo usa **datos ficticios** y el portal de laboratorio **propio** (127.0.0.1).
No se conecta a ningún servicio externo, no activa producción, no activa la copia externa del diario y no
toca la carpeta `data\` de otras instalaciones.

Tiempo aproximado: 15 min de instalación y unos 45 min de pruebas.

| Prueba | Qué demuestra | Cómo |
|---|---|---|
| A | Mesa de control e importación Excel | A mano, con `cauce_rc1.bat` |
| B | Planificación y confirmación simulada | A mano, en la misma sesión |
| C | Restauración con discrepancias | Automática (`recuperacion_a_mano.bat` prepara el escenario) |
| D | Conciliación desde la pantalla «Recuperación» | A mano, en ese escenario |
| E | DPAPI (clave del diario ligada a tu cuenta de Windows) | Una prueba automatizada y la pantalla de D |
| F | Unidad externa para la copia del diario, **sin activarla** | `revisar_copia_diario.bat` |

Apunta en cada prueba **OK** o **FALLA** y, si falla, lo que dice la pantalla o la ventana negra.

---

## 0. Preparar (una vez)

Necesitas:

- Windows 10/11.
- Python 3.11–3.13 (marca *Add python.exe to PATH*).
- Node.js LTS.
- Git, o descargar el ZIP.
- Una memoria USB vacía para la prueba F.

Usa una carpeta nueva, **fuera de OneDrive**. No reutilices la de la RC1.

```powershell
git clone -b claude/project-thread-rlypc5 https://github.com/dass-666/Cauce-monitor-engine C:\cauce-validacion
cd C:\cauce-validacion
git log -1 --oneline
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
.venv\Scripts\python.exe scripts\preparar_navegador.py
```

- `git log -1 --oneline` debe mostrar el commit indicado en la entrega.
- Sin git: en GitHub elige la rama `claude/project-thread-rlypc5` → *Code* → *Download ZIP* → descomprime en
  `C:\cauce-validacion`.

**Esperado:** el instalador termina sin errores. Puede mostrar AVISOS, como «Sin copia externa del diario»;
esos avisos son normales en esta validación.

---

## A. Mesa de control e importación Excel

1. Doble clic en **`cauce_rc1.bat`**. Se abre el navegador en la **Mesa de control**.
   - Usuario `admin`, contraseña `Admin-Laboratorio-2026` (solo laboratorio).
   - La ventana negra indica la ruta del Excel de prueba
     (`resultados\cauce_rc1\lista_demostracion\clientes_ficticios_rc1.xlsx`), el **mes** y el **día** que
     conviene abrir. Anótalos.
2. Comprueba que en la Mesa de control aparece la tarjeta **«Recuperación segura: Operable»**.
3. **Importar** → elige el `.xlsx` → revisa la vista previa:
   - la columna «Contraseña del portal» aparece **descartada**;
   - la columna «Consentimiento» aparece **ignorada**.

   Pulsa **Importar 6 fila(s)**.
4. **Preparar estos expedientes**: verás 4 «Falta consentimiento» y 2 «Faltan datos».
5. En Ana, Bruno y Fabiola pulsa **Registrar consentimiento** y rellena:
   - medio;
   - aviso `AP-2026-01`;
   - cualquier PDF ficticio (solo se calcula su huella);
   - una referencia, por ejemplo `Carpeta LAB hoja 1`.

**Esperado:**
- 6 expedientes importados.
- Contraseña descartada.
- Tras registrar los consentimientos, los 3 quedan «Listo para planificar».

---

## B. Planificación y confirmación simulada

Continúa en la misma sesión.

1. Marca la casilla de cabecera → **Añadir 3 seleccionado(s) al plan**.
2. **Mesa de control** → Observador:
   - sucursal `NOR-1`;
   - trámite Alta;
   - el mes anotado.

   Pulsa **Iniciar observador** y espera a que diga «mirando el calendario».
3. **Calendario lab** → avanza al mes anotado → **Abrir** el día anotado.
4. **Mesa de control** → «Coincidencias de fecha»: aparece el expediente de Ana → **Preparar operación**.
5. En la pantalla de atención:
   - teclea la clave ficticia `QXZ482915`;
   - marca las casillas;
   - pulsa **Enviar**;
   - elige el día y confirma.
6. **Cerrar atención** → historial.
7. **Mesa de control** → «Citas confirmadas con folio». Vuelve a **Importar** el mismo archivo.

**Esperado:**
- La coincidencia aparece en menos de 1 minuto.
- La cita queda con un **folio**. La confirmación es **simulada**, contra el portal propio.
- Al reimportar: «Ya existen 6», «Nuevos 0».

Al terminar, cierra con `Ctrl + C` en la ventana negra.

> Si la coincidencia no aparece, revisa que el observador diga «mirando el calendario» y que abriste el mes y
> el día anotados. En la CI 97 hubo un fallo intermitente parecido en Windows con Python 3.11: repítelo una vez
> y anota si vuelve a pasar.

---

## C y D. Restauración con discrepancias y conciliación en «Recuperación»

1. Doble clic en **`recuperacion_a_mano.bat`**. Durante unos 2 minutos el script prepara solo el escenario, en
   una carpeta temporal:
   - una cita confirmada (B) y otra que no;
   - un **respaldo**;
   - después del respaldo, confirma la cita de B en el portal y revoca el consentimiento de G;
   - **restaura** el respaldo.
2. Cuando termina, la ventana negra muestra:
   - la dirección (`http://127.0.0.1:<puerto>/#recuperacion`);
   - el usuario y la contraseña;
   - las 2 discrepancias;
   - el **folio que muestra el portal de laboratorio**.

   Ahí espera a que pulses Enter. **No la cierres.**
3. **Prueba C · restauración detectada.** Abre la dirección y entra como `admin`.
   - Mesa de control: tarjeta «Recuperación segura: **BLOQUEADA**».
   - Pantalla «Recuperación»:
     - «OPERACIONES CON EL PORTAL BLOQUEADAS»;
     - motivos RESTAURACION PENDIENTE y DISCREPANCIAS;
     - 2 discrepancias: «Cita confirmada perdida» y «Revocación perdida»;
     - **«Levantar el bloqueo» desactivado**.
4. **Prueba D · conciliación.**
   1. «Cita confirmada perdida» → **Resolver** → acción «Registrar el folio del portal».
   2. Escribe el folio falso `F-QUE-NO-ES`, un motivo y marca la casilla → **Aplicar**. Debe **rechazarse** con
      «Ese folio no es el que el portal entregó…».
   3. Sustituye el folio por el que mostró la ventana negra → **Aplicar**. Queda 1 discrepancia.
   4. «Revocación perdida» → **Resolver** → «Reaplicar la revocación», motivo y casilla → **Aplicar**. Quedan
      0 pendientes, pero **sigue bloqueado**.
   5. **Levantar el bloqueo** → motivo y casilla → **Levantar**.
   6. La pantalla pasa a «Operable». El historial muestra 3 acciones (2 «conciliada» y 1 «levantada») con tu
      usuario y tus motivos.
5. Vuelve a la ventana negra y pulsa **Enter**. El script comprueba **por la API** lo que hiciste y termina la
   secuencia: continuidad, retención con fecha simulada y nada fuera del laboratorio.

**Esperado:**
- `RESULTADO: 40/40 comprobaciones` y «la conciliación a mano quedó bien».
- Evidencia en `resultados\recuperacion_a_mano\recorrido_recuperacion_ui.json`.

**Alternativa automática** (la misma secuencia con clics automatizados y 9 capturas): `recorrido_recuperacion_ui.bat`.
Esperado: `49/49`, con las capturas en `resultados\recorrido_recuperacion_ui\capturas\`.

---

## E. Verificación de DPAPI

1. En la prueba D, en la tarjeta «Diario de eventos críticos», **«Protección de la clave»** debe decir
   **«DPAPI (ligada a la cuenta de Windows que ejecuta el motor)»**. En Linux diría «archivo legible solo por su
   dueño».
2. En PowerShell, dentro de `C:\cauce-validacion`:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests\test_recuperacion_v24.py -k dpapi -v
   ```
   **Esperado:** `test_clave_protegida_con_dpapi_en_windows PASSED` y `1 passed`. Si dice `SKIPPED`, no se
   ejecutó en Windows: anótalo.

Opcional, solo si quieres ver el límite: con **otra** cuenta de Windows, la clave creada por la tuya no se
puede abrir. El motor arrancaría con `CLAVE_NO_DISPONIBLE` y bloquearía. La salida es exportar e importar la
clave (ver `docs\RESPALDO_EXTERNO_DIARIO.md`). No hace falta probarlo ahora.

---

## F. Unidad externa para la copia del diario (sin activarla)

1. Conecta la USB. Supongamos que es `E:`.
2. Crea la carpeta `E:\CauceDiario` y ejecuta:
   ```bat
   revisar_copia_diario.bat E:\CauceDiario
   ```
   **Esperado:**
   - `"apta": true` y `"escritura": "OK"`;
   - el mensaje «Carpeta apta. Para activarla, añade a .env…»;
   - «No se ha cambiado ninguna configuración».
3. Prueba el rechazo con una carpeta en el disco del sistema: crea `C:\CauceDiarioPrueba` y ejecuta
   ```bat
   revisar_copia_diario.bat C:\CauceDiarioPrueba
   ```
   **Esperado:** `"apta": false` con «La copia está en la MISMA unidad…».
4. Comprueba que nada quedó activado:
   ```powershell
   Select-String -Path .env -Pattern "^MONITOREO_DIARIO_COPIA="
   dir E:\CauceDiario
   ```
   **Esperado:**
   - la primera orden no muestra nada (la copia sigue desactivada);
   - la carpeta `E:\CauceDiario` está vacía (el archivo de prueba se borró).

**No** añadas la línea a `.env` todavía. Activar la copia es un paso aparte, después de esta validación.

> Si con la USB el resultado es «MISMA unidad», anota la letra y el tipo de unidad: sería un falso positivo de la
> detección por volumen (`st_dev`), que no está verificada en hardware real.

---

## Qué enviar al terminar

- La tabla A–F con OK o FALLA.
- `resultados\recuperacion_a_mano\recorrido_recuperacion_ui.json`, y el de `recorrido_recuperacion_ui` si lo
  ejecutaste.
- La salida de la prueba E (`1 passed` o `SKIPPED`) y de la F (las dos ejecuciones).
- Si algo falla:
  - `resultados\cauce_rc1\servidor-motor-lab.log` y `portal.log`;
  - una captura de la pantalla.

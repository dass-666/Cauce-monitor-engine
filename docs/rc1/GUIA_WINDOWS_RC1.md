# Cauce RC1 en Windows (MSI) · abrir y usar

Todo con **datos ficticios** y el portal de laboratorio **propio** (127.0.0.1). No se conecta a ningún portal
externo, no toca tu carpeta `data\` ni otras instalaciones. **Esta guía no se ha ejecutado todavía en el MSI**:
lo verificado está en GitHub Actions (Windows Server) y en Linux; el equipo físico es lo que falta.

Tiempo aproximado: 10 min de instalación, 2 min de demostración automática, 15 min a mano.

## 0. Qué necesitas

- Windows 10/11, Python 3.11–3.13 (marca *Add python.exe to PATH*), Node.js LTS (https://nodejs.org) para el
  observador del calendario.
- Una carpeta **fuera de OneDrive**, por ejemplo `C:\cauce-rc1` (instalación aislada: no reutilices la de v2.3).

## 1. Bajar la RC1 (una sola rama, sin fusionar nada)

```powershell
git clone -b claude/project-thread-y5dh74 https://github.com/dass-666/Cauce-monitor-engine C:\cauce-rc1
```

Sin git: en GitHub elige la rama `claude/project-thread-y5dh74` → *Code* → *Download ZIP* → descomprime en `C:\cauce-rc1`.

## 2. Instalar (una vez)

En PowerShell dentro de `C:\cauce-rc1`:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
.venv\Scripts\python.exe scripts\preparar_navegador.py
```

## 3. Demostración automática (opcional, prueba que el equipo puede hacerlo)

Doble clic en **`recorrido_rc1.bat`**. Debe terminar con `RESULTADO: el recorrido RC1 paso` y `27/27
comprobaciones`. Deja `resultados\recorrido_rc1\recorrido_rc1.json` y 20 capturas. La confirmación de la cita la
hace el script y **es simulada** (así consta en el JSON).

## 4. Abrir Cauce y usarlo a mano

Doble clic en **`cauce_rc1.bat`**. Se abre el navegador en la **Mesa de control**.

- Usuario: `admin` · contraseña: `Admin-Laboratorio-2026` (solo laboratorio).
- La ventana negra muestra la ruta de una **lista de prueba con fechas vigentes**:
  `resultados\cauce_rc1\lista_demostracion\clientes_ficticios_rc1.xlsx`, el **mes** y el **día** que conviene abrir.
- Para empezar de cero: `cauce_rc1.bat --nuevo`. Para detener: `Ctrl + C` en la ventana negra.

Recorrido sugerido (lo mismo que hace la demostración):

1. **Importar** → elige el `.xlsx`. Se elige sola la hoja «Clientes». Revisa la vista previa: la columna
   «Contraseña del portal» aparece **descartada** y «Consentimiento» **ignorada**. Pulsa **Importar 6 fila(s)**.
2. **Preparar estos expedientes**: verás 4 «Falta consentimiento» y 2 «Faltan datos». Ninguno se puede
   seleccionar para el plan todavía. En Daniela pulsa **Corregir datos**, pon el nacimiento `30/01/1992` y
   guarda: pasa a «Falta consentimiento». Teléfono y correo solo se cambian escribiéndolos dos veces.
3. En Ana, Bruno y Fabiola pulsa **Registrar consentimiento**: medio, aviso `AP-2026-01`, elige cualquier
   PDF ficticio (solo se calcula su huella; no se envía) y una referencia como `Carpeta LAB hoja 1`.
4. Marca la casilla de cabecera (selecciona los «Listo para planificar») → **Añadir 3 seleccionado(s) al plan**.
5. **Mesa de control** → Observador: sucursal `NOR-1`, trámite Alta, mes = el que indicó la ventana negra →
   **Iniciar observador** (espera «mirando el calendario»).
6. **Calendario lab** → avanza al mes indicado → **Abrir** el día indicado.
7. **Mesa de control** → en «Coincidencias de fecha» aparece el expediente de Ana → **Preparar operación**.
8. En la pantalla de atención: teclea la clave ficticia `QXZ482915`, marca las casillas, **Enviar**, elige el día,
   confirma. Aparece el **folio**. **Cerrar atención** muestra el historial.
9. **Mesa de control** → «Citas confirmadas con folio». Vuelve a **Importar** el mismo archivo: «Ya existen 6»,
   «Nuevos 0».

Ventanilla sigue disponible (menú **Ventanilla**) para atender o corregir a una persona concreta.

## 5. Respaldo, restauración y retención (sin cambios respecto a v2.3)

- Demostración automática: `recorrido_integral.bat` (32 comprobaciones; fecha de retención simulada +2 días).
- A mano: `.venv\Scripts\python.exe -m monitoreo.migrate --help` (respaldar / restaurar).
- **Riesgo conocido sin resolver:** restaurar un respaldo anterior a una confirmación puede devolver estados viejos.
  Con el portal de laboratorio no se duplica la cita porque respeta la clave de idempotencia; un proveedor real
  podría no hacerlo. El diseño de «recuperación segura» espera tu aprobación y no está en la RC1.

## 6. Qué anotar si algo falla

`resultados\cauce_rc1\servidor-motor-lab.log`, `portal.log` y, si usaste la demostración,
`resultados\recorrido_rc1\recorrido_rc1.json` y la captura `ZZ_fallo.png`.

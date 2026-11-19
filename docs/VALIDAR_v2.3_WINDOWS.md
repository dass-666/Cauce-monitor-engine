# Validar v2.3 en Windows físico (MSI) · paquete mínimo

Todo con **datos ficticios** y el portal de laboratorio **propio** (127.0.0.1). No se conecta a ningún
portal externo y no toca tu carpeta `data\` ni tu instalación normal.
Tiempo aproximado: 25 minutos (10 de instalación, 1 de demostración automática, 15 a mano).

## 0. Qué necesitas

- Windows 10/11, Python 3.11, 3.12 o 3.13 (*Add python.exe to PATH*), Node.js LTS (https://nodejs.org).
- Una carpeta **fuera de OneDrive**, por ejemplo `C:\cauce-v23`.

## 1. Bajar la rama (sin fusionar nada)

Con git:

```powershell
git clone -b claude/project-thread-2wwz8e https://github.com/dass-666/Cauce-monitor-engine C:\cauce-v23
```

Sin git: en GitHub elige la rama `claude/project-thread-2wwz8e` → *Code* → *Download ZIP* y descomprímelo
en `C:\cauce-v23`.

## 2. Instalar (una vez)

En PowerShell dentro de `C:\cauce-v23`:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
.venv\Scripts\python.exe scripts\preparar_navegador.py
```

La contraseña que pide `instalar.ps1` es para tu instalación normal; el laboratorio usa las suyas (abajo).

## 3. Demostración automática (prueba que el equipo lo puede hacer)

Doble clic en **`recorrido_ventanilla.bat`** (unos 30-60 s). Al final debe decir
`RESULTADO: el recorrido completo paso` y `21/21 comprobaciones`.
Deja el resultado en `resultados\recorrido_v23\recorrido_v23.json` y 20 capturas en
`resultados\recorrido_v23\capturas\`. La «confirmación humana» la hace el script (así consta en el JSON).

## 4. Recorrido a mano (lo que se pide validar)

Doble clic en **`ventanilla_laboratorio.bat`**. Se abre el navegador en Ventanilla y la ventana negra
muestra las direcciones. Datos del laboratorio:

| Usuario | Clave | Rol |
|---|---|---|
| `admin` | `Admin-Laboratorio-2026` | plan del día, observador, calendario lab, duplicados |
| `operador1` | `Operador-Laboratorio-2026` | atiende a la persona |

Persona de prueba (inventada): nombre `Lucía Ficticia Laboratorio`, teléfono `5550001234`, correo
`lucia.ficticia@ejemplo.test`, región NORTE, sucursal NOR-1. El registro previo y la fecha de nacimiento
cópialos de **Calendario lab → registros ficticios** (son los únicos que el portal de laboratorio acepta).
Consentimiento: aviso `AP-2026-01`, cualquier archivo de tu equipo como «documento firmado» (solo se
calcula su huella; no se envía) y «Dónde está el original» **sin comas**, p. ej. `Carpeta 2026-11 hoja 14`.

Usa dos navegadores (o una ventana privada) para tener admin y operador a la vez y sigue la tabla de
`docs\GUIA_RECORRIDO_VENTANILLA_v2.3.md` §2 (10 pasos). Anota cualquier paso que no se pueda hacer con la
pantalla.

Comprobaciones a mano que más importan:

1. El operador, en **su** atención, pulsa «Dejarla en el plan del día» y la persona aparece *En espera* en
   el plan. El operador **no** ve botones para crear o ampliar el plan, ni el observador, ni Calendario lab.
2. Tras «Confirmar», la atención muestra el **folio**; en *Plan del día* la fila pasa a **Confirmado**
   (puede tardar unos segundos; pulsa *Actualizar estado*).
3. El historial del expediente muestra atenciones, plan, operación con folio y consentimiento íntegro.

## 5. Detenerlo correctamente

- En la ventana negra de `ventanilla_laboratorio.bat` pulsa **`Ctrl + C`** una vez y espera el mensaje
  final. Si Windows pregunta «¿Desea terminar el trabajo por lotes (S/N)?», responde **S**.
- **No** la cierres con la X mientras el motor escribe: puede quedar un proceso sin cerrar
  (no comprobado en Windows; con `Ctrl + C` sí se detienen motor y portal en Linux y en la CI).
- Comprueba que no quedó nada abierto: `tasklist | findstr /i python` no debe mostrar procesos del
  laboratorio. Si quedara alguno: `taskkill /f /im python.exe` (cierra **todos** los Python abiertos).
- Al volver a abrir `ventanilla_laboratorio.bat`, todo sigue donde quedó. Para empezar de cero:
  `ventanilla_laboratorio.bat --nuevo`.

## 6. Conservar el resultado

Con el laboratorio **detenido** (la base de datos no debe estar en uso), en PowerShell dentro de
`C:\cauce-v23`:

```powershell
Compress-Archive -Path resultados\recorrido_v23, resultados\ventanilla_lab -DestinationPath "$HOME\Desktop\validacion_v23_$(Get-Date -Format yyyyMMdd_HHmm).zip"
```

El ZIP contiene el JSON de la demostración con sus capturas, y la carpeta del laboratorio (base de datos
SQLite, registros del motor y del portal). Solo hay datos ficticios; `laboratorio.json` guarda las claves
del portal de laboratorio, que solo sirven en ese equipo. Compártelo junto con tus anotaciones del paso 4.

## 7. Qué reportar

- Versión de Windows y de Python (`python --version`).
- Resultado del paso 3 (`N/21`) y, si falló, el ZIP.
- Pasos del recorrido a mano que no se pudieron hacer desde la pantalla, con captura.
- Si `Ctrl + C` dejó algún proceso abierto (paso 5).

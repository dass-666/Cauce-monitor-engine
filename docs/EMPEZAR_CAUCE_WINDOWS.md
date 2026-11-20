# Empezar con Cauce en Windows

Guía corta para instalar y usar Cauce en tu equipo (el MSI). No hace falta saber programar.

Todo es **laboratorio**: datos ficticios, un portal de prueba propio en tu equipo (127.0.0.1) y confirmaciones
**simuladas**. No se conecta a ningún servicio externo y producción queda desactivada.

| | |
|---|---|
| Rama | `claude/project-thread-3mgurm` |
| Commit | el último de esa rama (lo indica la entrega; `comprobar_equipo.bat` lo muestra en su primera línea) |
| Tiempo | unos 15 minutos para instalar y 20 minutos para el recorrido |

---

## 1. Antes de empezar (una sola vez)

Instala estos programas si no los tienes (siguiente, siguiente…):

1. **Python 3.13** de 64 bits: <https://www.python.org/downloads/>. Marca **«Add python.exe to PATH»**.
2. **Node.js LTS**: <https://nodejs.org>.
3. **Git** (opcional): <https://git-scm.com>. Sin Git puedes descargar un ZIP.

Después **cierra y vuelve a abrir** cualquier ventana de PowerShell o CMD.

## 2. Descargar en una carpeta nueva

Usa una carpeta **nueva**, corta y **fuera de OneDrive**, por ejemplo `C:\cauce-msi`. No reutilices la
carpeta de pruebas anteriores: así esta instalación no toca ninguna otra.

**Con Git** (en PowerShell):

```powershell
git clone -b claude/project-thread-3mgurm https://github.com/dass-666/Cauce-monitor-engine C:\cauce-msi
```

**Sin Git:** en GitHub elige la rama `claude/project-thread-3mgurm` → **Code** → **Download ZIP** y
descomprímelo en `C:\cauce-msi`. El repositorio es privado: tienes que haber iniciado sesión en GitHub.

## 3. Instalar

En `C:\cauce-msi`, doble clic en **`instalar_cauce.bat`**. Hace cuatro pasos y se detiene con un mensaje
claro si algo falta:

1. Comprueba el equipo: Python, Node.js, puertos locales, permisos y espacio. Si falta algo **no instala nada**.
2. Prepara el entorno de Cauce, la configuración y la base de datos.
   - Te pide una contraseña para el usuario `admin` del **modo normal** (`iniciar.bat`). Elige una cualquiera
     de al menos 10 caracteres con letras y números. **El laboratorio no la usa.**
3. Instala Playwright y Chromium dentro de la carpeta, solo si faltan (unos 150 MB, solo la primera vez).
4. Vuelve a comprobarlo todo, incluido abrir Chromium sin ventana.

**Debes ver:** `LISTO. Para abrir Cauce: doble clic en cauce_rc1.bat`. Algún **AVISO** es normal, por ejemplo
«Sin copia externa del diario».

Se puede repetir sin miedo: conserva lo que ya está preparado y no borra datos.

## 4. Abrir Cauce

Doble clic en **`cauce_rc1.bat`**. Se abre el navegador en la **Mesa de control**.

- Usuario: `admin`
- Contraseña: `Admin-Laboratorio-2026`

Estas credenciales son **exclusivas del laboratorio**. Nunca las uses en otro sitio.

La ventana negra muestra también:

- la ruta del Excel de prueba, `resultados\cauce_rc1\lista_demostracion\clientes_ficticios_rc1.xlsx`;
- el **mes** y el **día** que conviene abrir en el calendario.

Anota el mes y el día. **No cierres la ventana negra** mientras uses Cauce.

### El menú

Arriba ves lo del día a día: **Mesa de control, Importar, Expedientes, Ventanilla y Recuperación**.
Si «Recuperación» muestra la etiqueta roja **Bloqueada**, entra ahí antes de seguir operando.

Lo demás está en tres menús desplegables:

- **Operación**: Captura, Solicitudes, Notificaciones, Supervisión y Trámites a mano.
- **Sistema**: Inicio, Monitor, Salud, Conexiones y Configuración.
- **Laboratorio**: Calendario lab y Laboratorio. Este menú no existe en producción.

Con el teclado: usa `Tab` para moverte, `Enter` para abrir un menú y `Esc` para cerrarlo.

## 5. Recorrido: de los 6 expedientes ficticios a un folio

1. **Importar** → elige el Excel de prueba. En la vista previa, la contraseña del portal aparece **descartada**
   y el consentimiento, **ignorado**. Pulsa **Importar 6 fila(s)**.
   - **Debes ver:** 6 nuevos. Luego, 4 «Falta consentimiento» y 2 «Faltan datos».
2. **Expedientes** → en Ana, Bruno y Fabiola, **Registrar consentimiento**:
   - aviso `AP-2026-01`;
   - cualquier PDF (solo se guarda su huella);
   - referencia `Carpeta LAB hoja 1`.

   **Debes ver:** los 3 pasan a «Listo para planificar».
3. **Planificar**: marca los 3 → **Añadir al plan**. En la **Mesa de control**, inicia el observador con
   sucursal `NOR-1`, trámite Alta y el mes anotado. Espera a que diga «mirando el calendario».
4. **Coincidencias**: menú **Laboratorio → Calendario lab** → ve al mes anotado → **Abrir** el día anotado.
   Vuelve a la **Mesa de control**.
   - **Debes ver:** en «Coincidencias de fecha», el expediente de Ana en menos de 1 minuto.
   - Una coincidencia **no es una cita**.
5. **Folio simulado**: pulsa **Preparar** o **Continuar operación**. Después:
   1. teclea la clave ficticia `QXZ482915`;
   2. marca las casillas y pulsa **Enviar**;
   3. elige el día y **confirma**.

   **Debes ver:** «Cita confirmada» con un **folio**. Es una confirmación simulada contra el portal propio.
6. **Historial**: en la Mesa de control → «Citas confirmadas con folio» → **Historial**.
   - **Debes ver:** importación, consentimiento, plan y folio en una sola línea de tiempo.
7. **Recuperación**: entra en **Recuperación** (menú superior).
   - **Debes ver:** «Operable», sin la etiqueta **Bloqueada**.
   - En la tarjeta del diario, «Protección de la clave» debe decir **DPAPI**.

Si quieres comprobar que no se duplican expedientes, vuelve a importar el mismo archivo. Debes ver «Nuevos 0» y
«Ya existen 6».

## 6. Comprobar la recuperación tras restaurar un respaldo

Cierra antes Cauce (punto 7). Después elige una de estas dos formas:

- **Automática** (unos 3 minutos): doble clic en **`recorrido_recuperacion_ui.bat`**.
  - **Debes ver:** `RESULTADO: 49/49 comprobaciones`.
  - Las capturas quedan en `resultados\recorrido_recuperacion_ui\capturas\`.
- **A mano**: doble clic en **`recuperacion_a_mano.bat`** y sigue lo que indica la ventana negra. Prepara un
  Cauce **bloqueado** con 2 discrepancias para que las resuelvas tú en **Recuperación**.
  - **Debes ver:** al final, `40/40`.
  - Los pasos detallados están en `docs\GUIA_VALIDACION_WINDOWS.md`, apartados C y D.

## 7. Cerrar correctamente

1. En el navegador pulsa **Salir**.
2. En la ventana negra de `cauce_rc1.bat` pulsa **`Ctrl + C`**. Espera a que se detengan el motor y el portal.
   Si pregunta «¿Desea terminar el trabajo por lotes (S/N)?», responde `S`.
3. Ya puedes cerrar la ventana.

Lo que hiciste se conserva en `resultados\cauce_rc1\`. Al volver a abrir `cauce_rc1.bat` sigue ahí. Para
empezar de cero, en una ventana CMD dentro de la carpeta ejecuta `cauce_rc1.bat --nuevo`. **Esto borra solo esa
sesión de laboratorio.**

## 8. Si algo falla

1. Doble clic en **`comprobar_equipo.bat`**. Solo comprueba: no instala ni cambia nada. Escribe su resultado en
   `resultados\comprobacion_equipo.txt`.
2. Comparte estos archivos. Todos contienen solo datos ficticios y ninguna contraseña:

| Archivo | Cuándo |
|---|---|
| `resultados\comprobacion_equipo.txt` | siempre |
| una foto o el texto de la ventana negra | siempre |
| `data\logs\instalacion-*.log` (el más reciente) | si falló la instalación |
| `resultados\cauce_rc1\servidor-motor-lab.log` y `resultados\cauce_rc1\portal.log` | si falló Cauce o el observador |
| `resultados\recorrido_recuperacion_ui\recorrido_recuperacion_ui.json` | si falló la prueba de recuperación |

**No compartas** el archivo `.env` ni la carpeta `data\`: contienen claves de tu instalación.

## Qué está comprobado y qué no

- **Comprobado en GitHub Actions** (máquinas virtuales Windows Server de GitHub, con Python 3.11 a 3.13):
  - instalación desde ZIP con `instalar_cauce.bat`;
  - el recorrido RC1;
  - la recuperación por la interfaz;
  - DPAPI.
- **No comprobado todavía en tu MSI.** Esta guía es justamente esa prueba.
- Anota en cada paso **OK** o **FALLA**, con lo que diga la pantalla.

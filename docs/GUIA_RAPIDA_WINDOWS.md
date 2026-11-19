# Guía rápida para Windows (v1.7)

Para el equipo MSI o cualquier Windows 10/11. Tiempo aproximado: 10 minutos.
Todo funciona con datos ficticios y solo en tu equipo (http://127.0.0.1:8000).

## Antes de empezar

- Python **3.13** instalado desde https://www.python.org/downloads/ con la casilla
  *Add python.exe to PATH* marcada. (3.11, 3.12 y 3.14 también sirven.)
- Una carpeta **fuera de OneDrive**: lo más sencillo es `C:\`. El Escritorio y Documentos
  suelen estar sincronizados y pueden dañar la base de datos.

## A. Instalación nueva

1. Descomprime `motor-monitoreo-v1.7.zip` en `C:\`: se crea la carpeta `C:\motor-monitoreo-v1.7`.
2. Abre esa carpeta, haz **Shift + clic derecho** en un espacio vacío → *Abrir ventana de
   PowerShell aquí* (o *Abrir en Terminal*) y escribe:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   Te pide dos veces la contraseña del usuario `admin` (no se ve al escribir; mínimo 10
   caracteres con letras y números). Al final verás el **autodiagnóstico**: debe decir OK o AVISO.
3. Doble clic en **`iniciar.bat`**. El navegador se abre solo. Entra con `admin` y tu contraseña.
   Desde la v1.5 también se abre, **minimizada**, una segunda ventana con el **portal de
   reservaciones de prueba** (http://127.0.0.1:8200). Es un sitio ficticio que solo existe en tu
   equipo. Si no lo quieres, crea la variable `MONITOREO_SIN_PORTAL=1` o ciérralo.
4. Para detenerlo: `Ctrl + C` en la ventana negra y cierra también la del portal.

## B. Actualizar desde una versión anterior (v1.0 a v1.6)

1. Cierra el motor anterior (`Ctrl + C`).
2. Descomprime la v1.7 en `C:\` (crea la carpeta nueva `C:\motor-monitoreo-v1.7`). No la pongas encima
   de la anterior.
3. En PowerShell dentro de la carpeta nueva (cambia la ruta por la carpeta de tu versión anterior):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\actualizar.ps1 -Anterior C:\motor-monitoreo-v1.6
   ```
   Copia (no mueve) tus datos y tu `.env`, hace un respaldo antes de migrar y comprueba todo.
   La carpeta anterior queda intacta: para volver atrás basta con su `iniciar.bat`.
4. Inicia la nueva con su `iniciar.bat`. Tu contraseña de `admin` es la de antes.

**Qué cambia al pasar de la v1.5 a la v1.6** (lo hace solo `actualizar.ps1`, con respaldo previo):

- La base recibe la migración **0007**: no borra nada. Tus expedientes de la v1.5 siguen siendo
  *por horario* y sus estados se traducen a los nuevos (por ejemplo, `CONFIRMADO` pasa a
  `RESERVACION_CONFIRMADA`).
- Los expedientes **nuevos** serán *por día* (parámetro `captura_modo` en *Configuración*; puedes
  volver a `POR_HORARIO`).
- Recomendado: añade a tu `.env` la línea `MONITOREO_ZONA_HORARIA=America/Mexico_City` (si falta,
  se usa la zona de Windows, que normalmente es la misma).
- Entra la **política de retención**: los expedientes con folio se anonimizan a los 90 días sin
  cambios, los cerrados a los 30 y los incompletos a los 60. Los plazos se cambian en
  *Configuración* (de 1 a 3650 días); antes de aplicarla a mano verás una vista previa.
- Los operadores pueden limitarse a sucursales concretas (*Configuración → Usuarios*).

**Qué cambia al pasar de la v1.6 a la v1.7** (lo hace solo `actualizar.ps1`, con respaldo previo):

- La base recibe la migración **0008**: añade columnas, no borra nada. Las alertas antiguas reciben
  su categoría automáticamente.
- El reparto de trabajo sigue igual (`menor_carga`). El nuevo `equitativo_dia` es opcional.
- Los operadores ahora ven **solo sus** notificaciones y solicitudes (antes veían las de todos).
- Menús nuevos: **Supervisión** (solo administradores) y **Notificaciones** (antes *Alertas*).
  Para los operadores, *Captura* abre **Mi trabajo**.
- Guías nuevas: `docs/GUIA_OPERACION.md`, `docs/GUIA_ADMINISTRADOR.md`, `docs/SOLUCION_PROBLEMAS.md`.

## C. Probar la captura y las reservaciones (v1.5, 5 minutos)

1. Con `iniciar.bat` en marcha, entra al panel como `admin` y abre **Captura**.
2. En *Expedientes* crea un expediente ficticio. Usa solo datos inventados: teléfono que empiece
   por **555** y correo terminado en **@ejemplo.test** (el sistema rechaza otros a propósito).
   El *registro previo*, su fecha de nacimiento y el trámite deben coincidir con uno del portal de
   prueba. Para ver algunos válidos: `.\.venv\Scripts\python scripts\registros_portal.py`
3. Crea un operador en *Configuración → Usuarios* con la región del expediente. El motor le
   asigna el expediente en pocos segundos.
4. Entra como ese operador → *Captura* → pulsa el expediente → **Preparar formulario** → marca
   *He revisado* → **Enviar** → elige un horario → marca *Confirmo* → **Confirmar**. Verás el folio.

Nada de esto sale de tu equipo: el portal es un programa local con datos ficticios.

## D. Probar la reservación por día (v1.6, 5 minutos)

1. Con `iniciar.bat` en marcha, entra como `admin` → **Captura** → *Resumen*: verás los
   indicadores y el calendario de cada sucursal.
2. Crea un expediente ficticio como en el paso C (deja *Modo* en **Por día**). Si te falta algún
   dato, queda **INCOMPLETO** y el motor no lo asigna hasta que lo completes.
3. Entra como el operador → abre el expediente → **Preparar formulario**. Teclea a mano la
   **clave de identidad** que te dicte el solicitante (el sistema no la guarda ni deja pegarla) y
   marca el aviso de privacidad solo si acepta → *He revisado* → **Enviar**.
4. Elige un **día** verde en el calendario del mes (no hay horas) → marca *Confirmo* →
   **Confirmar**. Verás el folio y la fecha.
5. Si el calendario muestra un aviso naranja de **sin comunicación**, los días aparecen como
   *desconocidos*: no significa que no haya lugar, solo que no se pudo comprobar.

## E. Probar la v1.7 (10 minutos)

1. Como `admin`, en **Inicio** verás el centro de operaciones: cambia la región o el periodo y las
   cifras se recalculan.
2. Como operador, **Captura** abre **Mi trabajo**: pulsa **Continuar**, completa el flujo y, tras el
   folio, **Siguiente expediente**. Cierra el navegador a mitad de uno: al volver aparece **A medias**
   y se retoma en el mismo paso.
3. Como `admin`, **Supervisión** muestra la presencia de cada operador y permite **Redistribuir…**.
4. Opcional: doble clic en **`laboratorio.bat`** (unos 10 minutos, datos ficticios en carpetas
   temporales, no toca tus datos). Debe terminar con "todas las comprobaciones del laboratorio pasaron".
   *Pendiente de validar en el MSI:* hasta ahora solo se ha ejecutado en Linux y en Windows de CI.

## Si algo falla

| Qué ves | Qué hacer |
|---|---|
| "python no se reconoce" | Reinstala Python marcando *Add python.exe to PATH*; cierra y abre PowerShell. |
| El navegador no abre | Abre a mano http://127.0.0.1:8000 |
| *Captura* dice que el portal no responde | Comprueba que la ventana del portal sigue abierta, o ejecuta `iniciar_portal.bat`. |
| Cualquier otra duda | Doble clic en **`diagnostico.bat`**: dice qué revisar y no cambia nada. |
| Quieres volver a un respaldo | Con el motor cerrado: `.\.venv\Scripts\python -m monitoreo.migrate restaurar data\respaldos\<archivo>.db` (guarda antes la base actual). No copies el archivo a mano. |

Los registros de la instalación quedan en `data\logs\instalacion-<fecha>.log`.

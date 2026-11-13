# Guía rápida para Windows (v1.5)

Para el equipo MSI o cualquier Windows 10/11. Tiempo aproximado: 10 minutos.
Todo funciona con datos ficticios y solo en tu equipo (http://127.0.0.1:8000).

## Antes de empezar

- Python **3.13** instalado desde https://www.python.org/downloads/ con la casilla
  *Add python.exe to PATH* marcada. (3.11, 3.12 y 3.14 también sirven.)
- Una carpeta **fuera de OneDrive**: lo más sencillo es `C:\`. El Escritorio y Documentos
  suelen estar sincronizados y pueden dañar la base de datos.

## A. Instalación nueva

1. Descomprime `motor-monitoreo-v1.5.zip` en `C:\`: se crea la carpeta `C:\motor-monitoreo-v1.5`.
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

## B. Actualizar desde v1.0 a v1.4

1. Cierra el motor anterior (`Ctrl + C`).
2. Descomprime la v1.5 en `C:\` (crea la carpeta nueva `C:\motor-monitoreo-v1.5`). No la pongas encima
   de la anterior.
3. En PowerShell dentro de la carpeta nueva (cambia la ruta por la carpeta de tu versión anterior):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\actualizar.ps1 -Anterior C:\motor-monitoreo-v1.4
   ```
   Copia (no mueve) tus datos y tu `.env`, hace un respaldo antes de migrar y comprueba todo.
   La carpeta anterior queda intacta: para volver atrás basta con su `iniciar.bat`.
4. Inicia la nueva con su `iniciar.bat`. Tu contraseña de `admin` es la de antes.

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

## Si algo falla

| Qué ves | Qué hacer |
|---|---|
| "python no se reconoce" | Reinstala Python marcando *Add python.exe to PATH*; cierra y abre PowerShell. |
| El navegador no abre | Abre a mano http://127.0.0.1:8000 |
| *Captura* dice que el portal no responde | Comprueba que la ventana del portal sigue abierta, o ejecuta `iniciar_portal.bat`. |
| Cualquier otra duda | Doble clic en **`diagnostico.bat`**: dice qué revisar y no cambia nada. |
| Quieres volver a un respaldo | Con el motor cerrado: `.\.venv\Scripts\python -m monitoreo.migrate restaurar data\respaldos\<archivo>.db` (guarda antes la base actual). No copies el archivo a mano. |

Los registros de la instalación quedan en `data\logs\instalacion-<fecha>.log`.

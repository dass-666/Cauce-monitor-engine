# Guía rápida para Windows (v1.4)

Para el equipo MSI o cualquier Windows 10/11. Tiempo aproximado: 10 minutos.
Todo funciona con datos ficticios y solo en tu equipo (http://127.0.0.1:8000).

## Antes de empezar

- Python **3.13** instalado desde https://www.python.org/downloads/ con la casilla
  *Add python.exe to PATH* marcada. (3.11, 3.12 y 3.14 también sirven.)
- Una carpeta **fuera de OneDrive**: lo más sencillo es `C:\`. El Escritorio y Documentos
  suelen estar sincronizados y pueden dañar la base de datos.

## A. Instalación nueva

1. Descomprime `motor-monitoreo-v1.4.zip` en `C:\`: se crea la carpeta `C:\motor-monitoreo-v1.4`.
2. Abre esa carpeta, haz **Shift + clic derecho** en un espacio vacío → *Abrir ventana de
   PowerShell aquí* (o *Abrir en Terminal*) y escribe:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   Te pide dos veces la contraseña del usuario `admin` (no se ve al escribir; mínimo 10
   caracteres con letras y números). Al final verás el **autodiagnóstico**: debe decir OK o AVISO.
3. Doble clic en **`iniciar.bat`**. El navegador se abre solo. Entra con `admin` y tu contraseña.
4. Para detenerlo: `Ctrl + C` en la ventana negra.

## B. Actualizar desde v1.0, v1.1, v1.2 o v1.3

1. Cierra el motor anterior (`Ctrl + C`).
2. Descomprime la v1.4 en `C:\` (crea la carpeta nueva `C:\motor-monitoreo-v1.4`). No la pongas encima
   de la anterior.
3. En PowerShell dentro de la carpeta nueva (cambia la ruta por la carpeta de tu versión anterior):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\actualizar.ps1 -Anterior C:\motor-monitoreo-v1.3
   ```
   Copia (no mueve) tus datos y tu `.env`, hace un respaldo antes de migrar y comprueba todo.
   La carpeta anterior queda intacta: para volver atrás basta con su `iniciar.bat`.
4. Inicia la nueva con su `iniciar.bat`. Tu contraseña de `admin` es la de antes.

## Si algo falla

| Qué ves | Qué hacer |
|---|---|
| "python no se reconoce" | Reinstala Python marcando *Add python.exe to PATH*; cierra y abre PowerShell. |
| El navegador no abre | Abre a mano http://127.0.0.1:8000 |
| Cualquier otra duda | Doble clic en **`diagnostico.bat`**: dice qué revisar y no cambia nada. |
| Quieres volver a un respaldo | Con el motor cerrado: `.\.venv\Scripts\python -m monitoreo.migrate restaurar data\respaldos\<archivo>.db` (guarda antes la base actual). No copies el archivo a mano. |

Los registros de la instalación quedan en `data\logs\instalacion-<fecha>.log`.

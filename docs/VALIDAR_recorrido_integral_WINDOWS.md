# Validar el recorrido integral en Windows físico

Usa datos **ficticios** y el portal de laboratorio **propio** (127.0.0.1). Trabaja en una carpeta
temporal: no toca `data\` ni tu instalación normal. El reloj del equipo **no** se cambia, porque la fecha
+2 días es una simulación dentro del script.

Tarda unos 15 minutos si ya hiciste la instalación de `docs\VALIDAR_v2.3_WINDOWS.md` (pasos 0 a 2).

## 1. Bajar la rama (sin fusionar)

```powershell
git clone -b claude/recorrido-integral-v2.3 https://github.com/dass-666/cauce-monitor-engine C:\cauce-integral
cd C:\cauce-integral
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
.venv\Scripts\python.exe scripts\preparar_navegador.py
```

Usa una carpeta fuera de OneDrive. Si no tienes git, en GitHub elige la rama, pulsa *Code* → *Download
ZIP* y descomprímela.

## 2. Ejecutar

Haz doble clic en **`recorrido_integral.bat`**. Tarda de 1 a 2 minutos.

Al final debe decir:
- `RESULTADO: el recorrido integral paso`
- `32/32 comprobaciones`

La ventana espera una tecla antes de cerrarse. El motor y el portal se detienen solos al terminar.

## 3. Comprobar que no quedó nada abierto

```powershell
tasklist | findstr /i "python node chrome"
```

No deben quedar procesos del recorrido.

## 4. Conservar el resultado

```powershell
Compress-Archive -Path resultados\recorrido_integral -DestinationPath "$HOME\Desktop\recorrido_integral_$(Get-Date -Format yyyyMMdd_HHmm).zip"
```

El ZIP contiene:
- `recorrido_integral.json`: cada comprobación con su detalle, los folios F1 y F2, el resultado de la
  retención y el nombre del respaldo;
- los registros del motor y del portal.

Solo contiene datos ficticios.

## 5. Qué reportar

- La versión de Windows (`winver`) y la de Python (`python --version`).
- La línea `N/32`. Si alguna comprobación falló, envía también el ZIP.
- Si el paso 3 mostró procesos que quedaron abiertos.

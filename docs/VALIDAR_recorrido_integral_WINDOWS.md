# Validar el recorrido integral en Windows físico (v2.4: con recuperación segura)

Usa datos **ficticios** y el portal de laboratorio **propio** (127.0.0.1). Trabaja en una carpeta
temporal: no toca `data\` ni tu instalación normal. El reloj del equipo **no** se cambia, porque la fecha
+2 días es una simulación dentro del script.

Tarda unos 15 minutos si ya hiciste la instalación de `docs\VALIDAR_v2.3_WINDOWS.md` (pasos 0 a 2).

## 1. Bajar la rama (sin fusionar)

```powershell
git clone -b claude/recuperacion-segura-v2.4 https://github.com/dass-666/cauce-monitor-engine C:\cauce-integral
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
- `50/50 comprobaciones`

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
- La línea `N/50`. Si alguna comprobación falló, envía también el ZIP.
- Si el paso 3 mostró procesos que quedaron abiertos.

## 6. Comprobaciones propias de Windows para la recuperación segura (v2.4)

El recorrido crea su propio diario y su propia clave dentro de la carpeta temporal del laboratorio. No
usa `data\` y no deja la clave de tu instalación.

Mientras el recorrido está en marcha, o con `ventanilla_laboratorio.bat`, comprueba esto en la carpeta
`datos\` del laboratorio:

1. **La clave va cifrada con DPAPI.** `datos\claves\diario.clave` debe decir
   `"proteccion": "dpapi-usuario"`.
2. **La clave no se abre con otra cuenta.** Copia la clave a otra cuenta de Windows y ejecuta allí
   `python -m monitoreo.recuperacion estado`. Debe responder `CLAVE_NO_DISPONIBLE`.
3. **El antivirus no frena el diario.** Revisa en el registro del motor que las confirmaciones no tarden
   segundos de más. Cada línea del diario se escribe con `fsync`.
4. **Pruebas de la recuperación en este equipo:**

   ```powershell
   .venv\Scripts\python.exe -m pytest tests\test_recuperacion_v24.py -q
   ```

   Deben pasar todas. En Windows la prueba de DPAPI se ejecuta, y la de permisos POSIX se omite. La prueba
   de la copia externa usa carpetas temporales, así que no hace falta una USB. El laboratorio no hereda
   `MONITOREO_DIARIO_COPIA`, a propósito: la copia externa en una ruta real se configura después, en la
   instalación definitiva.

Para reportar, anota el resultado de los 4 puntos.

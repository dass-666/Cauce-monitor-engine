# Validar la candidata de piloto en Windows (domingo) · sin tocar la MSI

**Objetivo:** comprobar en el Windows físico que la candidata de piloto asistido funciona, en una carpeta
**aparte**, con datos **ficticios**. La instalación de la MSI (`C:\cauce-msi`), sus datos y sus respaldos
(`C:\cauce-respaldos`) **no se tocan**: no se abren, no se actualizan, no se detienen.

Tiempo estimado: 45 a 60 minutos. Necesitas Internet solo para descargar el código y, si falta, el navegador.

## 0. Antes de empezar
- No ejecutes nada dentro de `C:\cauce-msi`. Todo lo de esta guía ocurre en `C:\cauce-piloto-validacion`.
- Si la MSI está en marcha, puede seguir así: el modo piloto usa un puerto libre distinto.
- Ten a mano una USB (o una segunda unidad) solo si vas a probar también el respaldo externo del otro PR.

## 1. Copia aparte (10 min)
1. Descarga el ZIP de la rama de la candidata desde GitHub (botón **Code › Download ZIP** en la rama que indica
   el PR) y descomprímelo en `C:\cauce-piloto-validacion`.
2. En esa carpeta, ejecuta `instalar_cauce.bat`. Crea su propio entorno (`.venv`) y, si falta, el navegador en
   `.browsers` **dentro de esa carpeta**. No cambia nada fuera de ella.
3. Comprueba: `comprobar_equipo.bat` (solo lectura) → anota el resultado.

## 2. Pruebas automáticas del piloto (10 min)
En `C:\cauce-piloto-validacion`, en una consola:

```
.venv\Scripts\python.exe -m pytest -q tests\test_piloto_asistido.py tests\test_recuperacion_v24.py tests\test_migrations.py
```

Esperado: todo `passed` (PostgreSQL aparece como omitido si no está configurado: es normal).
Anota el número de pruebas y si alguna falla (copia el texto completo del fallo).

## 3. Recorrido automático con navegador (5 min)
```
piloto_asistido.bat --recorrido
```
Arranca Cauce **en modo piloto** (producción + flujo administrativo, sin portal) en
`resultados\piloto_asistido`, crea un operador y un expediente ficticio y recorre la interfaz: revisión,
cierre del navegador sin aprobar nada, frase exacta, folio tecleado dos veces, cita registrada y atención
cerrada. Esperado: `RESULTADO: 17/17 comprobaciones`.
Evidencia: `resultados\piloto_asistido\recorrido_asistido.json` y `resultados\piloto_asistido\capturas\`.

## 4. Recorrido a mano, como lo haría el operador (20 min)
```
piloto_asistido.bat
```
Abre la dirección que imprime y entra como **operador1** (la clave aparece en la consola). Hay un expediente
ficticio ya tomado en Ventanilla. Comprueba con tus propios ojos:

| # | Acción | Resultado esperado | ✔ |
|---|---|---|---|
| 1 | Ventanilla › Retomar | Aparece «Agendar a mano en el portal oficial»; NO aparece «Preparar formulario» | |
| 2 | Pulsa «Agendar a mano…» | Se ven los datos COMPLETOS a teclear, quién abrió y la hora | |
| 3 | Escribe la frase y confirma enseguida (<5 s) | Rechazado: «al menos 5 s» | |
| 4 | Cierra el navegador, vuelve a entrar, Retomar | Sigue en el paso 1 (revisión ABIERTA), nada aprobado | |
| 5 | Frase en minúsculas | Rechazado: «Escribe exactamente…» | |
| 6 | Frase correcta | Pasa al paso 2: formulario del folio | |
| 7 | Intenta pegar el folio en «Vuelve a teclear» | No se pega; avisa que se teclee | |
| 8 | Folios distintos | Rechazado: «Los dos folios no coinciden» | |
| 9 | Elige un PDF cualquiera como comprobante | Se rellena la huella (64 caracteres) | |
| 10 | Registrar con todo bien | «Cita agendada por una persona en el portal oficial», con folio y comprobante | |
| 11 | Guardar y cerrar la atención | La atención queda con cita y el contador sube a 1 | |
| 12 | Entra como admin › Mesa de control | La cita aparece con folio | |
| 13 | Admin › Recuperación | «Operable», sin discrepancias | |

Termina con **Ctrl+C** en la consola.

## 5. Qué enviar de vuelta
- Resultado del paso 2 (número de pruebas, fallos si los hay).
- `resultados\piloto_asistido\recorrido_asistido.json`.
- La tabla del paso 4 marcada, y capturas de lo que no se vea como se espera.
- Versión de Windows, de Python (`.venv\Scripts\python.exe --version`) y de Node (`node --version`).

## 6. Al terminar
Puedes borrar `C:\cauce-piloto-validacion` entera. La MSI sigue exactamente como estaba.

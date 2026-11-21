# Análisis de los fallos intermitentes de la CI (2026-11-21)

Fuente: 60 ejecuciones completadas de `ci.yml` (2026-11-14 → 2026-11-21), `scripts/analizar_ci.py` (solo lectura de la API de GitHub; reproducible). Marcas: **[V]** verificado en logs/código · **[I]** inferido · **[P]** pendiente. No se cambió ningún tiempo de espera ni prueba.

## 1. Hechos
- **[V]** Fallos por job (fallos/ejecuciones): Reservaciones Windows 3.11 **10/44**, 3.13 **7/43**, 3.12 **6/42**; Linux 3.13 **5/44**; Integración HTTP Windows 3/46; el resto 1–3 por job.
- **[V]** El **mismo commit** da resultados distintos: `aea34ce` (PR: 1 job rojo; push, 2.º intento: 0), `5b3c6f6` (push: 2 rojos; PR: 0), `21d5a96` y `eb74cfe` (PR: 1 rojo; push: 0). Por tanto el fallo no lo determina el código del commit.
- **[V]** Rama vs base: `claude/lab-puerto-estable` (base) tuvo ejecuciones con fallos en 3 de 5 (`3cda555`, `9e7f88d` push y `9e7f88d` lanzada a mano hoy: 3 jobs rojos, incluido «Reservaciones Windows 3.12»). La rama del PR #4 (solo añade archivos de prueba/herramientas) tuvo 1 de 10, y ese caso pasó en el segundo intento. **No hay evidencia de que la rama empeore la CI.**
- **[V]** No hay una sola causa. Huellas distintas observadas (primera línea de error por job rojo): `UnicodeEncodeError 'charmap' … '→'` (3), `ENOENT … stat '…\scripts\resultados\recorrido_rc1\lista_demostracion…'` (3), `page.waitForSelector: Timeout` en el calendario (3), `test_limite_de_reinicios_detiene_con_motivo` del observador en Windows (3), `ERR_NO_BUFFER_SPACE` (agotamiento de búferes de red de Windows, visto hoy en la base), «cambios duraderos no detectados fuera de ventanas de fallo» (2), «revocación con el DÍA ELEGIDO…» (2).
- **[V]** Dos de esas huellas **eran defectos reales ya corregidos por código**, no ruido: (a) `ENOENT` por una ruta relativa que el navegador (cwd=`scripts/`) no encontraba — hoy `recorrido_rc1.py` usa `Path(...).resolve()`; (b) `UnicodeEncodeError` al imprimir «→» con la salida en cp1252 — corregido en `fda7600` con `reconfigure(errors="replace")`. Moraleja: «intermitente» puede esconder un defecto determinista que solo aparece en una condición.
- **[V]** Otras huellas (`assert ['api/laboratorio_portal.py'] == []`, `rama_declarada`/`commit_fijado`) son pruebas **dependientes de la rama**: fallan en ramas que no son la esperada; no se vieron en la rama del PR.

## 2. Lo que no está demostrado
- Los fallos de Windows «Reservaciones» (calendario no visible a 30 s, observador sin reinicio, `ERR_NO_BUFFER_SPACE`) **no tienen causa demostrada**. Hipótesis [I]: saturación de recursos del runner de Windows (búferes de red, CPU) con muchos procesos simultáneos (motor, portal, observador, Chromium). Falta: logs del motor y de Chromium de un job rojo (los artefactos están bloqueados por el proxy de este entorno) y una medición de recursos en el runner.
- Hoy, en la base, el mismo job falló en un paso distinto (ventanilla en móvil, `ERR_NO_BUFFER_SPACE`) que en la rama (calendario): **no se puede afirmar que sea el mismo fallo**.

## 3. Qué se hizo y qué no
Se añadió `scripts/analizar_ci.py` (reproduce estas cifras). **No** se amplió ningún tiempo de espera, no se debilitó ninguna prueba y no se repitieron ejecuciones salvo una (permitida) en el PR.

## 4. Siguiente paso propuesto (si se autoriza)
1. Instrumentar el job «Reservaciones Windows» para que, **al fallar**, suba el log del motor/Chromium y una medición de recursos (CPU, conexiones/puertos en uso) sin alterar la prueba.
2. Con 10–20 ejecuciones instrumentadas, agrupar por causa y corregir **la causa** (no el umbral) de cada grupo.
3. Mantener «mismo commit, resultado distinto» como señal de alarma: si reaparece en una rama, comparar con la base antes de culpar al cambio.

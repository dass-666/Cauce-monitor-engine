# Registro de Cauce Monitor Engine en Jarvis (v2.2.1)

Esta guía explica cómo registrar el proyecto en Jarvis Personal E9.1.2 y qué puede hacer Jarvis con él.
Lo que Cauce expone es **solo lectura o ejecución de pruebas aisladas**. No hay comandos para
fusionar, desplegar, editar código, tocar la base de datos real ni hablar con portales externos.

> **Aviso de verificación.** No tuve acceso al código de Jarvis (no existe un repositorio de Jarvis
> en GitHub bajo esta cuenta). Los pasos de Jarvis de la sección 3 son **INFERENCIA**: describen lo
> que Jarvis necesita leer, no los menús reales de E9.1.2. El lado de Cauce (secciones 2, 4 y 5) sí
> está probado en Linux y en Windows Server de GitHub Actions, **no** en el equipo Windows físico.

## 1. Qué archivos tiene que conocer Jarvis

| Archivo | Para qué sirve |
|---|---|
| `jarvis/project.yaml` | Identidad del proyecto, versión, herramientas autorizadas (comandos exactos) y políticas. |
| `jarvis/test-manifest.json` | Lista blanca de suites. Jarvis pide un **id**; nunca envía un comando. |
| `jarvis/report.schema.json` | Esquema JSON (Draft 2020-12) del informe que Jarvis recibe. |
| `jarvis/declaraciones.json` | Lo que Cauce no puede medir solo: históricos, pendientes, bloqueos y próximos pasos. |

Todos son texto plano UTF-8, sin credenciales, sin datos personales y sin rutas absolutas. `{python}`
significa "el Python del entorno virtual de esta instalación" y las rutas son relativas a la carpeta
del proyecto.

## 2. Comandos que Jarvis puede llamar

Siempre desde la carpeta del proyecto y con su entorno virtual. En Windows:

```powershell
cd <carpeta del proyecto>
.\.venv\Scripts\python.exe -m monitoreo.interop suites
```

| Herramienta | Comando | Efecto |
|---|---|---|
| inventario | `{python} -m monitoreo.interop inventario` | Solo lectura: ramas, commits, migraciones, pruebas, scripts, informes. |
| suites | `{python} -m monitoreo.interop suites` | Solo lectura: lista blanca y límites. |
| ejecutar | `{python} -m monitoreo.interop ejecutar --suite <id>` | Ejecuta una suite aislada; escribe solo en `resultados/jarvis/`. |
| informe | `{python} -m monitoreo.interop informe` | Solo lectura; escribe `resultados/jarvis/informe.json` si pasa sus comprobaciones. |
| validar | `{python} -m monitoreo.interop validar <archivo>` | Valida un informe (esquema, datos sensibles, huella). |
| ciclo | `{python} -m monitoreo.interop ciclo --suite <id>` | ejecutar + informe + validar. |

Cada comando imprime **una línea JSON** al final. Códigos de salida: `0` aprobada · `1` fallida ·
`2` solicitud no permitida · `3` tiempo agotado · `4` cancelada · `5` error interno · `6` ocupado.

**Cancelar:** crear el archivo `resultados/jarvis/CANCELAR` (y borrarlo después), o enviar Ctrl+C /
SIGTERM al proceso del ejecutor. El ejecutor detiene todo el árbol de procesos de la suite.

**PostgreSQL (opcional):** `ejecutar --suite humo --con-postgres` usa `MONITOREO_TEST_PG` solo si
el nombre de la base indica que es de pruebas (`test`, `pruebas` o `ci`). Las pruebas **vacían** esa
base. Nunca apuntes `MONITOREO_TEST_PG` a la base real.

## 3. Registro en Jarvis E9.1.2 (INFERENCIA: adaptar a los menús reales)

1. Clonar o copiar Cauce en una carpeta **de desarrollo**, distinta de la instalación que atiende
   la ventanilla. Motivo: las pruebas no deben compartir carpeta con datos reales, aunque el
   ejecutor compruebe que `data/` no cambia.
2. Crear su entorno virtual: `py -3.13 -m venv .venv` y
   `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. En Jarvis, registrar el proyecto apuntando a esa carpeta y al archivo `jarvis/project.yaml`.
   Jarvis debe leer de ahí el `id` (`cauce-monitor-engine`) y la lista `herramientas_autorizadas`.
4. Configurar Jarvis para que **solo** use los `argv` de `herramientas_autorizadas`, sustituyendo
   `{python}` por la ruta del Python del entorno virtual y `{suite}` por un id que exista en
   `jarvis/test-manifest.json` (consultable con `suites`).
5. Primera comprobación desde Jarvis: `ciclo --suite humo`. El resultado esperado es código 0 y
   `estado_global` = `APROBADO`. Si el árbol de git tiene cambios locales, sale
   `APROBADO_CON_CAMBIOS_LOCALES` y las ejecuciones quedan como REPORTADO: es lo correcto.
6. Jarvis lee `resultados/jarvis/informe.json`, lo valida con `jarvis/report.schema.json` y muestra a
   la persona: `resumen.estado_global`, `pruebas[*]`, `pendientes` y `bloqueos`.

### Cómo debe leer Jarvis el informe (reglas mínimas)

- Comprobar que `git.commit` es el commit que Jarvis espera supervisar.
- Solo `pruebas[*].estado == "VERIFICADO"` cuenta como medido ahora. `REPORTADO` es historia
  (o una ejecución que falló alguna condición, explicada en `motivos_no_verificado`).
- `SIMULADO` es laboratorio o simulación, nunca uso real. `PENDIENTE` nadie lo ha comprobado.
- `APROBADO` no significa "listo para producción" ni "integrado con Jarvis": ver `resumen.alcance`.
- La huella `huella_contenido` sirve para saber si dos informes dicen lo mismo (idempotencia): ignora
  fechas, duraciones e identificadores de ejecución (lista en `huella_excluye`).

## 4. Lo que Cauce garantiza y lo que no

Garantizado y probado (tests/test_interop_jarvis.py):
- solo suites de la lista blanca; un id con `;`, `&&`, espacios o rutas se rechaza con código 2;
- el manifiesto solo admite `{python} -m pytest` con rutas dentro de `tests/` y opciones conocidas;
- el hijo no recibe tokens, la URL de la base, la del portal ni proxies, ni lee el `.env`;
- límite de tiempo y de salida, con detención del árbol de procesos;
- cancelación por archivo, Ctrl+C o SIGTERM;
- una ejecución a la vez (candado con PID; uno huérfano se recupera);
- se compara `git status` y `data/` antes y después;
- el informe no se escribe si no cumple el esquema o si contiene un dato sensible;
- un resultado histórico, de otro commit, alterado o de más de 24 h nunca sale como VERIFICADO.

No garantizado:
- **aislamiento de red** a nivel del sistema operativo (las pruebas usan 127.0.0.1, pero el
  ejecutor no lo impide); en Windows se puede reforzar con el firewall;
- que un `resultado.json` no haya sido fabricado por alguien con acceso a la carpeta: las huellas
  detectan cambios en la salida, no una falsificación completa. No hay firma criptográfica;
- validación en el equipo Windows físico.

## 5. Comprobaciones pendientes en el equipo Windows físico

Hacerlas en una copia de desarrollo, nunca en la carpeta de la ventanilla:

| # | Comprobación | Comando | Resultado esperado |
|---|---|---|---|
| W1 | Python del entorno virtual | `.\.venv\Scripts\python.exe --version` | 3.11 a 3.13 |
| W2 | Lista blanca | `... -m monitoreo.interop suites` | JSON con `interop`, `humo`, … |
| W3 | Suite de contratos | `... -m monitoreo.interop ejecutar --suite interop` | código 0, 0 fallidas |
| W4 | Ciclo completo | `... -m monitoreo.interop ciclo --suite humo` | código 0, `APROBADO` |
| W5 | Cancelación | lanzar `ejecutar --suite completa`; crear `resultados\jarvis\CANCELAR` | código 4 y ningún `python.exe` huérfano en el Administrador de tareas |
| W6 | Tiempo agotado | `ejecutar --suite completa --tiempo-s 30` | código 3 |
| W7 | Comando no permitido | `ejecutar --suite "humo & dir"` | código 2 |
| W8 | Demostración | `... scripts\demo_jarvis_v221.py --suite humo` | todas las comprobaciones en verde |
| W9 | Acentos y consola | revisar que la salida JSON se lee bien en PowerShell 5.1 | sin caracteres rotos |
| W10 | Antivirus | ver si el antivirus bloquea o ralentiza los procesos hijos | anotar tiempos de W4 |

Adjunta `resultados\jarvis\informe.json` y `resultados\jarvis\demo_jarvis_v221.json` al informe de
validación. Hasta entonces, Windows físico es **PENDIENTE**.

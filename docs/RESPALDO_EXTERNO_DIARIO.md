# Respaldo externo del diario de eventos críticos

Guía operativa para el administrador. Complementa [RECUPERACION_SEGURA.md](RECUPERACION_SEGURA.md).
Código: `monitoreo/recuperacion.py` (`revisar_copia`, `exportar-clave`, `importar-clave`).

## Por qué hace falta

El diario (`data\diario\eventos-criticos.jsonl`) guarda fuera de la base las revocaciones de consentimiento y
las citas confirmadas o canceladas. Si se restaura un respaldo, el motor compara la base con el diario y
bloquea lo que no cuadre.

Pero si **se pierde o se daña el disco**, la base y el diario se pierden juntos. La copia externa es una
segunda copia del diario, en **otra unidad**, que el motor mantiene al día línea a línea.

Para que esa copia sirva en otro equipo hace falta también **la clave del diario**, que se exporta aparte.

| Pieza | Dónde vive | Quién la escribe |
|---|---|---|
| Diario principal | `data\diario\eventos-criticos.jsonl` | El motor |
| Copia externa | `<carpeta de MONITOREO_DIARIO_COPIA>\eventos-criticos.jsonl` | El motor, en cada evento y al arrancar |
| Clave del diario | `data\claves\diario.clave` (cifrada con DPAPI en Windows) | El motor, la primera vez |
| Clave exportada | Un archivo de texto que tú guardas **en otro medio** | `exportar-clave` |

## Qué medio elegir

El motor **bloquea las operaciones con el portal** si la copia está configurada y no se puede escribir
(`COPIA_NO_DISPONIBLE`). Es a propósito: un evento que no llega a la copia es un evento que se perdería con el
disco. Por eso el medio debe estar **siempre conectado** mientras el motor trabaja.

| Medio | Ventaja | Riesgo | Recomendación |
|---|---|---|---|
| Segundo disco interno | Siempre presente, rápido | Se pierde si roban o se quema el equipo entero | **Recomendado** como primera opción |
| Memoria USB dedicada, fija en el equipo | Barato; se puede llevar a otro equipo | Si alguien la desconecta, el motor bloquea hasta reconectarla | Aceptable si nadie la usa para otra cosa |
| Carpeta de red (`\\servidor\cauce`) | Fuera del equipo | Una caída de la red o del servidor bloquea el motor | Solo con una red estable |

**No sirve** una carpeta en la misma unidad que `data\` (p. ej. `C:\CauceCopia` si Cauce está en `C:`): no
protege contra la pérdida del disco y `revisar-copia` la rechaza.

## Paso 1. Revisar la carpeta (no activa nada)

Con la unidad conectada:

```bat
revisar_copia_diario.bat E:\CauceDiario
```

o, lo mismo:

```powershell
.venv\Scripts\python.exe -m monitoreo.recuperacion revisar-copia E:\CauceDiario
```

Comprueba, sin cambiar ninguna configuración:

- que la carpeta exista (la unidad está conectada);
- que esté en **otra unidad** que el diario principal y fuera de la carpeta `data\`;
- que se pueda escribir (crea y borra un archivo de prueba vacío, sin datos);
- si ya hay un diario en esa carpeta (por ejemplo, de otra instalación);
- si la clave del diario se exportó.

Termina con código 0 y la línea exacta para `.env` si la carpeta es apta, o con código 1 y la lista de
problemas si no lo es.

## Paso 2. Activar la copia

1. Detén el motor.
2. Añade a `.env` la línea que imprimió el paso 1:
   ```
   MONITOREO_DIARIO_COPIA=E:\CauceDiario
   ```
3. Arranca el motor. Al arrancar, completa la copia con todo lo que tiene el diario principal.
4. Comprueba:
   - **Pantalla «Recuperación»** (administrador), tarjeta «Diario de eventos críticos»: «Copia externa» muestra la
     carpeta, el mismo número de eventos que el diario y «al día».
   - **`diagnostico.bat`**: desaparece el aviso «Sin copia externa del diario».

## Paso 3. Exportar la clave

```powershell
.venv\Scripts\python.exe -m monitoreo.recuperacion exportar-clave F:\resguardo\cauce-diario-clave.txt
```

- Guárdala en un **medio distinto** de la copia del diario (otra memoria, un gestor de contraseñas o un sobre
  cerrado impreso). Quien tenga la copia del diario y la clave puede comprobar qué expedientes tuvieron
  eventos; por separado, ninguna de las dos lo permite.
- El archivo incluye una **huella** de 16 caracteres. La pantalla «Recuperación» y el autodiagnóstico
  indican si la clave actual ya se exportó; el aviso «La clave del diario no se ha exportado» desaparece al
  hacerlo.
- La orden nunca sobrescribe un archivo existente.
- Borra el archivo exportado del equipo si lo escribiste en una carpeta local.

### DPAPI: la clave está ligada a la cuenta de Windows

En Windows, `data\claves\diario.clave` está cifrada con **DPAPI de la cuenta que ejecuta el motor**. Eso
significa:

- **Ejecuta `exportar-clave` con la misma cuenta que ejecuta el motor.** Con otra cuenta, DPAPI no puede abrir
  la clave y la orden falla.
- Si el motor pasa a ejecutarse con **otra cuenta** (otro usuario, un servicio, Windows reinstalado o el disco
  movido a otro equipo), esa cuenta no puede abrir la clave: el motor arranca con `CLAVE_NO_DISPONIBLE` y
  bloquea. La salida es **importar la clave exportada** con la cuenta nueva (abajo).
- La clave **no** va en la base ni en los respaldos de la base. Si no se exportó y se pierde la cuenta, el
  diario anterior ya no se puede verificar: solo queda reiniciarlo desde la pantalla «Recuperación» y revisar
  a mano lo posterior al último respaldo.

## Recuperar en otro equipo o con otra cuenta

1. Instala Cauce (`instalar.ps1`) y **no** lo arranques todavía.
2. Copia `E:\CauceDiario\eventos-criticos.jsonl` a `data\diario\eventos-criticos.jsonl` (crea la carpeta `data\diario` si no existe).
3. Importa la clave con la cuenta que ejecutará el motor:
   ```powershell
   .venv\Scripts\python.exe -m monitoreo.recuperacion importar-clave F:\resguardo\cauce-diario-clave.txt
   ```
   Imprime la huella: debe coincidir con la del archivo exportado.
4. Restaura la base: `python -m monitoreo.migrate restaurar <respaldo>`. En PostgreSQL, tras `pg_restore`,
   ejecuta `python -m monitoreo.recuperacion marcar-restauracion "<nombre>"`.
5. Configura `MONITOREO_DIARIO_COPIA` (pasos 1 y 2) y arranca.
6. El motor arranca **bloqueado** (restauración pendiente). Concilia en la pantalla «Recuperación» y levanta el
   bloqueo.

## Si algo falla

| Lo que ves | Causa probable | Qué hacer |
|---|---|---|
| `COPIA_NO_DISPONIBLE` | La unidad se desconectó o cambió de letra | Reconéctala y pulsa «Actualizar» en «Recuperación». Si cambió de letra, corrige `.env` con el motor detenido. |
| `COPIA_DIVERGENTE` | La carpeta tiene el diario de otra instalación, o alguien lo editó | No lo borres. Muévelo a otro sitio, deja la carpeta vacía y reinicia el motor: la copia se rehace desde el principal. |
| `DIARIO_TRUNCADO` con copia más larga | El diario principal perdió líneas que la copia sí tiene | Detén el motor, conserva ambos archivos e investiga antes de reiniciar el diario. |
| Aviso «MISMA unidad» | La copia no protege contra la pérdida del disco | Elige una carpeta en otra unidad. |
| `CLAVE_NO_DISPONIBLE` | El motor corre con otra cuenta de Windows | `importar-clave` con esa cuenta. |

## Qué está verificado y qué no

Verificado (pruebas automatizadas, Linux, carpetas temporales; ver `tests/test_respaldo_externo_diario.py` y
`tests/test_recuperacion_v24.py`):

- `revisar-copia` rechaza una carpeta inexistente, dentro de `data\` o en la misma unidad, y acepta otra unidad
  sin dejar archivos ni cambiar la configuración.
- `exportar-clave` deja la huella y la marca; `importar-clave` en otra carpeta de datos recupera la misma clave.
- El autodiagnóstico avisa de la clave sin exportar y de una copia en la misma unidad.
- La copia configurada se mantiene al día y su falta o divergencia bloquea (pruebas de la v2.4).
- DPAPI en los Windows de GitHub Actions (prueba de la v2.4).

**No verificado:**

- En el equipo físico de Diego (MSI): ni DPAPI, ni una memoria USB real, ni una carpeta de red.
- Que `st_dev` distinga unidades en todos los casos de Windows (unidades de red mapeadas, carpetas montadas).
  Si `revisar-copia` dice «misma unidad» siendo otra, compruébalo a mano antes de activarla.
- El comportamiento cuando la USB se desconecta a mitad de una escritura (se espera `COPIA_NO_DISPONIBLE` y
  bloqueo, pero no se ha provocado con hardware real).

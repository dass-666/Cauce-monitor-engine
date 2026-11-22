# Protección y recuperación de datos para el piloto asistido

Para el administrador. Resume **qué protege a los datos del piloto, la rutina diaria y qué hacer si algo falla**.
Detalle técnico: [RECUPERACION_SEGURA.md](RECUPERACION_SEGURA.md) (diario y conciliación) y
[RESPALDO_EXTERNO_DIARIO.md](RESPALDO_EXTERNO_DIARIO.md) (copia externa del diario y su clave).

## Las cuatro piezas

| Pieza | Protege contra | Dónde vive | Cómo se activa |
|---|---|---|---|
| Diario de eventos críticos | Restaurar un respaldo viejo y «olvidar» una revocación o una cita | `data\diario\` | Siempre activo |
| Copia externa del diario | Perder el disco con la base y el diario a la vez | `E:\Cauce\diario` | `MONITOREO_DIARIO_COPIA` en `.env` |
| **Respaldo externo de la base** (nuevo) | Perder el disco con la base y sus respaldos locales | `E:\Cauce\respaldos` | `MONITOREO_RESPALDO_EXTERNO` en `.env` + `respaldar_externo.bat` |
| Clave del diario exportada | Perder la cuenta de Windows o el equipo (DPAPI) | Un segundo medio, guardado aparte | `python -m monitoreo.recuperacion exportar-clave` |

`python -m monitoreo.migrate respaldar` deja los respaldos en `data\respaldos\`, en el **mismo disco** que la
base. Por eso existe el respaldo externo: copia un respaldo consistente al otro medio, lo **relee del medio**,
compara su SHA-256, pasa `PRAGMA integrity_check` y deja junto a él un manifiesto (`.json`) con la huella, el
tamaño, la revisión del esquema y el número de filas por tabla. El manifiesto no lleva datos de personas.

## Configuración recomendada (una memoria USB fija y cifrada)

```
MONITOREO_DIARIO_COPIA=E:\Cauce\diario
MONITOREO_RESPALDO_EXTERNO=E:\Cauce\respaldos
```

- **Cifra la USB con BitLocker To Go** antes de poner datos reales (el equipo del piloto tiene Windows 11 Pro). Cauce
  **no cifra** el respaldo `.db`, que contiene los expedientes: una USB perdida sin cifrar es una fuga de datos
  personales. Guarda la clave de recuperación de BitLocker aparte y comprueba que funciona: es el paso 3b de la
  guía de validación. Activa «Desbloquear automáticamente en este equipo», porque si no, tras cada reinicio la
  USB queda bloqueada y el motor también.
- Déjala **siempre conectada** mientras el motor trabaja: si la copia del diario no se puede escribir, el motor
  bloquea las operaciones con el portal (es a propósito).
- No uses esa USB para nada más.
- Exporta la clave del diario a **otro** medio (no a esa USB).

Antes de activarla: `revisar_copia_diario.bat E:\Cauce\diario` y
`.venv\Scripts\python.exe -m monitoreo.respaldo_externo revisar E:\Cauce\respaldos`. Las dos deben decir «apta»
y `"misma_unidad": false`.

## Rutina del piloto

| Cuándo | Qué | Resultado esperado |
|---|---|---|
| Al abrir | `diagnostico.bat` | Sin CRÍTICOS. Sin avisos de «Recuperación» ni de «Respaldo externo» |
| Al cerrar el día | `respaldar_externo.bat` (puede ser con el motor en marcha) | `RESULTADO: respaldo externo hecho y verificado` |
| Una vez por semana | `verificar_respaldo.bat E:\Cauce\respaldos\<el más reciente>.db` | `RESULTADO: el respaldo es integro y se puede restaurar` |

Se conservan los 14 respaldos externos más recientes; los antiguos solo se borran **después** de verificar el
nuevo. El autodiagnóstico avisa si el último respaldo externo verificado tiene más de 26 horas o si el medio no
está conectado.

## Restaurar desde la USB

1. Detén el motor y espera 15 s.
2. `verificar_respaldo.bat E:\Cauce\respaldos\<archivo>.db`: debe pasar.
3. `.venv\Scripts\python.exe -m monitoreo.migrate restaurar E:\Cauce\respaldos\<archivo>.db`
   - Guarda antes la base actual en `data\respaldos\`.
   - Si la huella del archivo no coincide con su manifiesto, **se niega** y no toca nada.
4. Arranca el motor. Arrancará **bloqueado** (restauración pendiente). En la pantalla «Recuperación», concilia
   cada discrepancia (folio comprobado en el portal, revocaciones reaplicadas) y levanta el bloqueo.

Restaurar dos veces el mismo archivo no duplica discrepancias ni citas: está probado.

## Si algo falla

| Lo que ves | Qué hacer |
|---|---|
| `respaldar_externo.bat` dice «no está conectada» | Conecta la USB y repítelo. No se borró ningún respaldo anterior |
| «La copia en el medio externo no coincide» | La USB puede estar dañada. No se conserva esa copia. Prueba otra USB |
| `verificar_respaldo.bat` falla en un respaldo | No lo uses. Verifica el anterior |
| `COPIA_NO_DISPONIBLE` en «Recuperación» | La USB se desconectó. Reconéctala y pulsa «Actualizar» |
| `COPIA_DIVERGENTE` o `DIARIO_MANIPULADO` | **Detén el piloto.** Conserva todos los archivos y avisa antes de tocar nada |

## Criterios para detener el piloto (datos)

- El diario aparece manipulado o divergente.
- Dos cierres seguidos sin un respaldo externo verificado.
- Se pierde la USB y no estaba cifrada.

## Qué está verificado y qué no

Verificado en Linux con pruebas automatizadas (`tests/test_respaldo_externo_base.py`) y con el recorrido integral
`--externa`, que da 52 de 52:
- el destino se rechaza si no existe, si está en la misma unidad o si está dentro de `data\`;
- la copia del medio coincide con su SHA-256 y su manifiesto no contiene datos de personas;
- un corte a mitad de copia no deja un respaldo con nombre definitivo ni borra los anteriores;
- un byte alterado se detecta, y `migrate restaurar` se niega a usar ese archivo;
- el ensayo no toca la base real ni el diario;
- restaurar dos veces es idempotente, y la cita sigue siendo una sola con el mismo folio.

**No verificado todavía:**
- Una USB física real, su desconexión con el motor en marcha, y BitLocker To Go con su clave de recuperación. Están en
  [VALIDAR_PROTECCION_DATOS_WINDOWS.md](VALIDAR_PROTECCION_DATOS_WINDOWS.md), para el domingo.
- Que `st_dev` distinga una USB de `C:` en el equipo físico. En la CI de Windows se prueba con `C:` frente a la
  unidad del repositorio cuando son distintas.
- PostgreSQL: el respaldo externo verificado solo existe para SQLite, que es la base del piloto.

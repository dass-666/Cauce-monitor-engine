# Guía · Recorrido de Ventanilla de principio a fin (v2.3, laboratorio)

Para recorrer Cauce **solo con la pantalla**, con datos ficticios y el portal de laboratorio propio
(127.0.0.1). No se conecta a ningún portal externo y no toca la carpeta `data\`.

## 1. Abrir el programa

- **Windows:** doble clic en `ventanilla_laboratorio.bat` (abre el navegador solo).
- **Linux / consola:** `python scripts/ventanilla_laboratorio.py --abrir`

La ventana muestra las direcciones y las claves:

| Usuario | Clave | Para qué |
|---|---|---|
| `admin` | `Admin-Laboratorio-2026` | Plan del día, observador, calendario del laboratorio, duplicados |
| `operador1` | `Operador-Laboratorio-2026` | Atender a la persona |

Todo se guarda en `resultados\ventanilla_lab\`. Si cierras con `Ctrl+C` y vuelves a abrir, todo sigue
donde quedó (las atenciones abiertas se **retoman**). Para empezar de cero: `--nuevo`.

> Usa dos navegadores (o una ventana privada) para tener al administrador y al operador a la vez.

## 2. Recorrido

| # | Quién | Dónde | Qué hacer |
|---|---|---|---|
| 1 | Operador | Ventanilla | Entra. Ves la meta del día, tus atenciones y el plan del día. |
| 2 | Admin | Ventanilla → Plan del día | **Crear el plan y elegir expedientes**. En *Observador del calendario* elige sucursal, trámite y mes (por ejemplo dos meses adelante) y pulsa **Iniciar observador**. Con *Actualizar estado* verás «mirando el calendario». |
| 3 | Admin | Calendario lab | Abajo están los **registros ficticios** que el portal reconoce (registro previo + fecha de nacimiento). Dáselos al operador. |
| 3 | Operador | Ventanilla → Nueva atención → Persona nueva | Escribe nombre inventado, la fecha y el registro ficticios, teléfono `555…` y correo `@ejemplo.test`, NORTE / NOR-1. |
| 4 | Operador | mismo formulario | Consentimiento: cómo lo autorizó, versión del aviso (`AP-2026-01`), elige el documento firmado (la huella se calcula en el equipo; el documento no se envía) y dónde está el original (sin comas). **Registrar y continuar.** Arriba aparece «Consentimiento CON-… vigente». |
| 5 | Operador | Nueva atención | Si registras otra vez a la misma persona, el motor lo detiene y ofrece **Atender este**. Si el nombre se parece mucho, el expediente queda **en revisión** y la pantalla lo explica. |
| 5 | Admin | Ventanilla → Posibles duplicados por revisar | Decide: **Son personas distintas** (con motivo) o **Es la misma persona: cancelar este**. |
| 6 | Operador | la atención de la persona | **¿Hoy no hay día que le sirva? Dejarla en el plan del día** → fechas aceptables (desde/hasta) → **Dejarla en el plan del día**. La atención se cierra sin cita y la persona entra al plan. |
| 7 | Operador | Ventanilla → Plan del día | La fila muestra su estado (*En espera*). Pulsa la fila para ver fechas aceptables y por qué se le asigna (o no) un día. |
| 8 | Admin | Calendario lab | Ve al mes del observador y pulsa **Abrir** en un día que la persona acepte. En segundos el plan lo asigna. |
| 8 | Operador | Ventanilla → Plan del día | Aparece **Preparar operación** (*Coincidencia*). Púlsalo: teclea los campos manuales con la persona presente, marca *He revisado*, envía, elige el día en el calendario del portal, marca *Confirmo* y **Confirmar**. |
| 9 | Operador | la atención | «Cita confirmada» con el **folio** (solo aparece si el portal lo devolvió y el motor lo comprobó). |
| 10 | Operador | la atención | **Guardar y cerrar la atención**. Debajo, **Historial completo del expediente**: atenciones, plan, operaciones con el portal, consentimiento (cadena íntegra) y la línea de tiempo. |

## 3. Demostración automática

`recorrido_ventanilla.bat` (o `python scripts/recorrido_ventanilla_v23.py`) hace el mismo recorrido con
un navegador automatizado (unos 30 s) y deja `resultados\recorrido_v23\recorrido_v23.json` y 20 capturas.
La «persona» que confirma la cita es el script: la evidencia lo dice (`confirmacion_humana: SIMULADA`).
Necesita Node.js y el navegador (`python scripts\preparar_navegador.py`).

## 4. Límites conocidos

- Crear el plan, ampliarlo, el observador y el calendario del laboratorio son del **administrador**.
- El operador ve el historial mientras el expediente está a su cargo (y tras confirmar la cita). Cuando lo
  deja en el plan, el historial lo consulta el administrador.
- La Ventanilla no se refresca sola (para no borrar lo que se teclea): usa *Actualizar estado* o vuelve a
  la lista para ver coincidencias nuevas.

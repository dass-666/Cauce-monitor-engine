# Observador universal de páginas web (fuente por URL + perfil)

Misma rama que `docs/COINCIDENCIA_FORMS_FOLIO.md`. No es otro sistema: extiende el observador del calendario
(v2.1) para que una fuente sea **una URL elegida por quien configura y un perfil de extracción**, sin código por
proveedor. Funciona hoy con sitios LOCALES de laboratorio; otro sitio solo con permiso (ver §3).

## 1. Arquitectura mínima

```
 observadores_config (+ url, perfil, autorizacion; migración 0018)
        │  Supervisor (ya existía): arrendamiento entre procesos, relanzamiento, límites, restauración al reiniciar
        ▼
 ObservadorWeb (observador.py) ── node scripts/observador_web.js ── Chromium con la página ABIERTA
        │  mismo protocolo de líneas: INICIAL / CELDA / VISTA / LATIDO (+ CAMBIO_VISUAL)
        ▼
 observaciones.registrar (ya existía: deduplicación sucursal|trámite|fecha con versión)
        ▼
 coincidencias (ya existía: plan del día, fechas y sucursales aceptables, prioridad, cupo)
        ▼
 operación preparada → Forms → lista para confirmación → folio verificado (eslabón de este PR)
```

Piezas nuevas: `monitoreo/planificador/fuentes_web.py` (validación, persistencia, vista previa),
`scripts/observador_web.js` (navegador genérico), `ObservadorWeb` (observador.py), API `/api/fuentes-web`
(administrador), `laboratorio_web/` (sitio local de prueba). Cambio en lo existente: `observaciones.registrar`
acepta la sucursal de cada línea (una fuente puede listar varias sucursales).

## 2. Cómo se configura una página (sin programar)

`POST /api/fuentes-web/probar` (vista previa, no guarda nada) y luego `POST /api/fuentes-web`:

```json
{"nombre": "turnos-lab", "url": "http://127.0.0.1:8300/turnos.html", "tramite": "alta",
 "estrategia": "dom",
 "perfil": {
   "zona": "#turnos", "elemento": ".turno",
   "fecha":  {"selector": ".dia", "patron": "(\\d{2}/\\d{2}/\\d{4})", "formato": "DD/MM/AAAA"},
   "estado": {"selector": ".estado", "disponible": ["Disponible"], "no_disponible": ["Sin lugares"]},
   "libres": {"selector": ".cupo", "patron": "(\\d+)"},
   "hora":   {"selector": ".hora", "patron": "(\\d{1,2}:\\d{2})"},
   "ubicacion": {"selector": ".lugar"},
   "ubicaciones": {"Oficina Norte Centro": "NOR-1", "Oficina Norte Poniente": "NOR-2"},
   "ignorar": [".reloj", ".anuncio", ".spinner"],
   "estabilidad_ms": 600,
   "sesion_perdida": "#sesion-expirada"}}
```

Reglas: `selector` (opcional, dentro del elemento), `atributo` (opcional; si no, el texto) y `patron` (opcional).
Lo que no se reconoce **no se interpreta**: un estado fuera de las listas, una fecha que no cuadra con el formato o
una ubicación sin fila en la tabla hacen que el elemento se ignore y se cuente (la vista previa dice cuántos y por qué).
El calendario del portal de laboratorio se observa con el mismo script y solo un perfil
(`laboratorio_web/servidor.py: PERFIL_CALENDARIO_PORTAL`).

**Cambio relevante frente a ruido:** solo cuentan mutaciones dentro de `zona` y fuera de `ignorar`; el resultado de
la extracción (no el HTML) es lo que se compara; un cambio debe mantenerse `estabilidad_ms` (se lee dos veces) antes
de registrarse. La estrategia visual (`visual`/`ambas`) compara capturas de la zona con el ruido TAPADO y, si cambian,
dispara una relectura: **no crea días a partir de píxeles** (no se pueden leer fechas de una imagen con fiabilidad).

**Evidencia por cambio:** hora de la página (`detectado_en`) y de registro, fuente (`observador`, `url`), fragmento
HTML y SHA-256 de lo extraído, captura de la zona (PNG en `<datos>/evidencia_observador/<fuente>/`) y su SHA-256.

## 3. Límites de acceso (no configurables)

- 127.0.0.1/localhost: solo fuera de producción. Cualquier otro sitio: https + host en `MONITOREO_OBSERVADOR_HOSTS`
  (lo escribe quien instala) + autorización escrita vigente (`ref`, `documento_sha256`, `vigente_hasta`).
- CAPTCHA, campo de contraseña, o 401/403/429 del sitio → el observador se **detiene** (`DETENIDO_POR_FALLOS`,
  «control de acceso del sitio») y no reintenta. No se resuelven, no se inicia sesión, no se rota nada.
- Toda navegación que no sea GET se aborta: observar nunca envía formularios ni reserva.
- La página no se recarga para observar; si la sesión de consulta se pierde, se reabre con espera creciente
  (mecanismo existente) y la deduplicación evita repetir coincidencias.

## 4. Evidencia (contenedor Linux, 2026-11-24)

- `tests/test_observador_web.py`: **9/9** con Chromium real: validación de permisos; vista previa (ubicación sin
  equivalencia y estado desconocido ignorados); 4 s de reloj/anuncio/animación → 0 eventos; parpadeo de 250 ms →
  0 eventos; aparición real → 1 evento con evidencia y captura; sin duplicados; desaparición; coincidencia solo con
  la sucursal y fecha aceptables; navegador matado + día nuevo mientras estaba caído → 2 eventos, ninguno repetido;
  sesión perdida → reapertura sin duplicar; CAPTCHA y HTTP 429 → se detiene y no insiste; 0 envíos al sitio;
  calendario del portal con el script genérico.
- `scripts/recorrido_coincidencia_forms.py --observador web` con Forms real ff117da: **26/26**
  (`docs/evidencia_coincidencia/recorrido_observador_web.json`). Día habilitado → coincidencia: 1,3 s (frente a
  0,45 s del observador específico: es el precio de la ventana de estabilidad).
- Regresión: `test_observador`, `test_planificador`, `test_captura_datos`, `test_interop_jarvis`,
  `test_plan_unidades_propiedad`, `test_autorrelleno_coincidencia`: 142 aprobadas.

## 5. Lo que NO hace (todavía)

- No hay interfaz para escribir el perfil: hoy es JSON por la API (con vista previa). Siguiente paso razonable.
- No se ha probado contra ningún sitio real (no hay uno autorizado). Sitios con iframes, shadow DOM o paginación
  necesitarán más reglas de perfil.
- Las coincidencias son por día y sucursal; la hora se guarda como evidencia, no se compara con preferencias.
- Una coincidencia de un sitio genérico solo se confirma con verificación por API si ese portal tiene adaptador
  autorizado; si no, con la confirmación asistida.

## 6. Prueba física en la MSI

En la misma carpeta nueva de `docs/COINCIDENCIA_FORMS_FOLIO.md` §5 (`C:\cauce-coincidencia`), después de instalar:

```
C:\cauce-coincidencia\monitor\observador_web_laboratorio.bat C:\cauce-coincidencia\cauce-forms
```

Esperado: `9 passed` y `26/26 comprobaciones`. Enviar `resultados\recorrido_observador_web\recorrido_coincidencia_forms.json`.

"""v2.1 · Planificador diario, observación de calendarios y motor de coincidencias.

- `plan`          P1: lista diaria de expedientes (fecha de jornada, prioridad, sucursales, trámite,
                  fechas aceptables). Persistente en la base: sobrevive a reinicios.
- `observaciones` P2: eventos normalizados y deduplicados que produce el observador del calendario.
- `coincidencias` P3: ante una fecha disponible, elige UN expediente compatible de forma
                  determinista y transaccional, y explica por qué.
- `operacion`     P4: el operador toma la coincidencia y la lleva por el flujo normal de reservación
                  (campos manuales tecleados por una persona, confirmación humana, folio verificado).
"""

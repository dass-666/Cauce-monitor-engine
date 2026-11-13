"""Captura y reservaciones de LABORATORIO (v1.5).

Módulos independientes:
- validacion      reglas de datos (puras, sin base de datos)
- expedientes     alta, modificación, permisos, minimización e historial
- formularios/    contratos de campos (JSON) + navegador HTTP que los aplica
- calendario      disponibilidad de horarios leída del portal (vista de calendario)
- reservaciones   retención, confirmación idempotente con evidencia, recuperación, cancelación
- seguimiento     enlace con asignaciones del motor, expiraciones y eventos normalizados

Solo trabaja con el portal de PRUEBA propio (`portal_reservas`) y datos sintéticos. La
confirmación de una reservación exige siempre la acción explícita de un operador.
"""

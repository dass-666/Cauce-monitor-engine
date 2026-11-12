"""Proveedor de disponibilidad de PRUEBA (v1.3).

Servicio HTTP independiente del motor, con datos ficticios, para probar la integración con
red real (peticiones, tiempos, errores HTTP y cortes de conexión reales) sin depender de
ningún tercero. No comparte base de datos con el motor: guarda su estado en su propio JSON.
"""

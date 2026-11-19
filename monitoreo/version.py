"""Versión del motor (v2.2.1): una sola fuente para la API (/api/info), el exportador para Jarvis
y jarvis/project.yaml (una prueba comprueba que coinciden).

Hasta la v2.2 app.py decía "2.0.0-dev" aunque el código ya era 2.2: la API informaba una versión
equivocada. Este módulo no importa nada, así que el exportador lo puede leer sin cargar FastAPI.
"""
VERSION = "2.2.2-dev"

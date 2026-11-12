"""Punto de entrada: python -m monitoreo"""
import os

import uvicorn

from .app import create_app

if __name__ == "__main__":
    host = os.environ.get("MONITOREO_HOST", "127.0.0.1")
    port = int(os.environ.get("MONITOREO_PUERTO", "8000"))
    uvicorn.run(create_app(), host=host, port=port, log_level=os.environ.get("MONITOREO_LOG", "info"))

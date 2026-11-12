"""Punto de entrada: python -m monitoreo"""
import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .app import create_app


def _open_browser_when_ready(url: str, timeout_s: float = 60) -> None:
    """Abre el navegador solo cuando /api/health responde (evita la página de error inicial)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # nunca a través de un proxy
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with opener.open(url + "/api/health", timeout=2):
                webbrowser.open(url)
                return
        except Exception:  # noqa: BLE001 - el servidor aún no responde
            time.sleep(0.5)


if __name__ == "__main__":
    host = os.environ.get("MONITOREO_HOST", "127.0.0.1")
    port = int(os.environ.get("MONITOREO_PUERTO", "8000"))
    if os.environ.get("MONITOREO_ABRIR_NAVEGADOR") == "1":
        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        print(f"El panel se abrirá en http://{shown}:{port} cuando el servidor esté listo.", flush=True)
        threading.Thread(target=_open_browser_when_ready, args=(f"http://{shown}:{port}",), daemon=True).start()
    uvicorn.run(create_app(), host=host, port=port, log_level=os.environ.get("MONITOREO_LOG", "info"))

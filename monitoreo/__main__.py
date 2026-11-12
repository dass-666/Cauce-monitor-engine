"""Punto de entrada: python -m monitoreo"""
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from . import entorno
from .app import create_app
from .config import settings


_TOKEN_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


class RedactSecrets(logging.Filter):
    """Oculta tokens de bot de Telegram (httpx registra la URL completa de cada petición en INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = _TOKEN_RE.sub("/bot<oculto>", msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def configure_logging(env: str) -> None:
    """Producción: registro en data/logs/monitoreo.log con rotación (10 MB x 5) además de la consola.
    Los registros nunca incluyen contraseñas ni tokens (la auditoría los redacta)."""
    level = os.environ.get("MONITOREO_LOG", "info").upper()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for h in root.handlers:
        if not any(isinstance(f, RedactSecrets) for f in h.filters):
            h.addFilter(RedactSecrets())
    if env == entorno.PRODUCCION:
        logs = settings.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(logs / "monitoreo.log", maxBytes=10 * 1024 * 1024,
                                                  backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(RedactSecrets())
        root.addHandler(fh)


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


def main() -> int:
    try:
        env = entorno.current()
        configure_logging(env)
        app = create_app()
    except entorno.ConfigError as e:
        print("\nEL SERVIDOR NO ARRANCA: la configuración no es segura para este entorno.\n", file=sys.stderr)
        for p in e.problems:
            print("  - " + p, file=sys.stderr)
        print("\nCorrige .env y vuelve a iniciar. Revisión completa:  python -m monitoreo.diagnostico\n",
              file=sys.stderr)
        return 2
    host = os.environ.get("MONITOREO_HOST", "127.0.0.1")
    port = int(os.environ.get("MONITOREO_PUERTO", "8000"))
    if os.environ.get("MONITOREO_ABRIR_NAVEGADOR") == "1":
        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        print(f"El panel se abrirá en http://{shown}:{port} cuando el servidor esté listo.", flush=True)
        threading.Thread(target=_open_browser_when_ready, args=(f"http://{shown}:{port}",), daemon=True).start()
    # Detrás de un proxy inverso: solo se acepta X-Forwarded-For/Proto de la IP indicada.
    proxy = os.environ.get("MONITOREO_PROXY_CONFIABLE", "")
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("MONITOREO_LOG", "info"),
                proxy_headers=bool(proxy), forwarded_allow_ips=proxy or None,
                server_header=False, log_config=None if env == entorno.PRODUCCION else uvicorn.config.LOGGING_CONFIG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Arrancar/detener el portal de prueba como PROCESO independiente (pruebas, CI, laboratorio)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from proveedor_prueba.proceso import puerto_libre

ROOT = Path(__file__).resolve().parent.parent
WIN = os.name == "nt"


class PortalProceso:
    def __init__(self, datos: Path, puerto: int | None = None, token: str = "token-prueba-portal",
                 token_control: str = "token-prueba-portal-control", log: Path | None = None,
                 extra: list[str] | None = None):
        self.datos, self.puerto = Path(datos), puerto or puerto_libre()
        self.token, self.token_control = token, token_control
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.log_path = log or self.datos.with_suffix(".log")
        self.extra = extra or []
        self.proc: subprocess.Popen | None = None
        self.http = httpx.Client(timeout=10, trust_env=False)

    def iniciar(self, espera: float = 60) -> float:
        env = {**os.environ, "PORTAL_TOKEN": self.token, "PORTAL_TOKEN_CONTROL": self.token_control,
               "PYTHONIOENCODING": "utf-8"}
        self._log = open(self.log_path, "ab")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0
        self.proc = subprocess.Popen([sys.executable, "-m", "portal_reservas", "--puerto", str(self.puerto),
                                      "--datos", str(self.datos), *self.extra], cwd=ROOT, env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        t0 = time.monotonic()
        while time.monotonic() - t0 < espera:
            if self.proc.poll() is not None:
                raise RuntimeError(f"el portal terminó al arrancar (código {self.proc.returncode}); ver {self.log_path}")
            try:
                if self.http.get(self.base + "/salud", timeout=1).json().get("ok"):
                    return time.monotonic() - t0
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError("el portal no respondió a tiempo")

    def detener(self, forzar: bool = False) -> None:
        """forzar=True simula un corte brusco (kill -9 / TerminateProcess)."""
        if self.proc and self.proc.poll() is None:
            if forzar:
                self.proc.kill()
            else:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        if getattr(self, "_log", None):
            self._log.close()

    def ctl(self, metodo: str, ruta: str, **json):
        r = self.http.request(metodo, self.base + ruta, headers={"X-Control-Token": self.token_control},
                              json=json or None)
        r.raise_for_status()
        return r.json()

    def fallos(self, **kw):
        return self.ctl("POST", "/control/fallos", **kw)

    def registro(self, n: int = 0) -> dict:
        r = self.http.get(self.base + "/control/registro-ejemplo", params={"n": n},
                          headers={"X-Control-Token": self.token_control})
        r.raise_for_status()
        return r.json()

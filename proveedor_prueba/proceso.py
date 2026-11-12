"""Arrancar/detener el proveedor de prueba como PROCESO independiente (pruebas, CI, laboratorio)."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
WIN = os.name == "nt"


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProveedorProceso:
    def __init__(self, datos: Path, puerto: int | None = None, token: str = "token-prueba-proveedor",
                 token_control: str = "token-prueba-control", log: Path | None = None, extra: list[str] | None = None):
        self.datos, self.puerto = Path(datos), puerto or puerto_libre()
        self.token, self.token_control = token, token_control
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.log_path = log or self.datos.with_suffix(".log")
        self.extra = extra or []
        self.proc: subprocess.Popen | None = None
        self.http = httpx.Client(timeout=10, trust_env=False)

    def iniciar(self, espera: float = 60) -> float:
        env = {**os.environ, "PROVEEDOR_TOKEN": self.token, "PROVEEDOR_TOKEN_CONTROL": self.token_control,
               "PYTHONIOENCODING": "utf-8"}
        self._log = open(self.log_path, "ab")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0
        self.proc = subprocess.Popen([sys.executable, "-m", "proveedor_prueba", "--puerto", str(self.puerto),
                                      "--datos", str(self.datos), *self.extra], cwd=ROOT, env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        t0 = time.monotonic()
        while time.monotonic() - t0 < espera:
            if self.proc.poll() is not None:
                raise RuntimeError(f"el proveedor terminó al arrancar (código {self.proc.returncode}); "
                                   f"ver {self.log_path}")
            try:
                if self.http.get(self.base + "/salud", timeout=1).json().get("ok"):
                    return time.monotonic() - t0
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError("el proveedor no respondió a tiempo")

    def detener(self, forzar: bool = False) -> None:
        """forzar=True simula un corte brusco (kill -9 / TerminateProcess): no guarda nada."""
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

    # ------------------------------------------------------------------ rutas de laboratorio
    def ctl(self, metodo: str, ruta: str, **json):
        r = self.http.request(metodo, self.base + ruta, headers={"X-Control-Token": self.token_control},
                              json=json or None)
        r.raise_for_status()
        return r.json()

    def fallos(self, **kw):
        return self.ctl("POST", "/control/fallos", **kw)

    def liberar(self, recurso: str, unidades: int = 1, ventana_s: float | None = None):
        return self.ctl("POST", "/control/liberar", recurso=recurso, unidades=unidades, ventana_s=ventana_s)

    def historial(self, desde: int = 0):
        r = self.http.get(self.base + "/control/historial", params={"desde": desde},
                          headers={"X-Control-Token": self.token_control})
        r.raise_for_status()
        return r.json()

    def estado(self):
        return self.ctl("GET", "/control/estado")

    def disponibilidad(self, region: str) -> list[dict]:
        out, cursor = [], None
        while True:
            r = self.http.get(f"{self.base}/v1/regiones/{region}/disponibilidad",
                              params={"limite": 200, **({"cursor": cursor} if cursor else {})},
                              headers={"Authorization": f"Bearer {self.token}"})
            r.raise_for_status()
            d = r.json()
            out += d["recursos"]
            cursor = d["siguiente"]
            if not cursor:
                return out

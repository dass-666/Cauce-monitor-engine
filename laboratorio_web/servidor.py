"""Servidor del sitio de laboratorio del observador universal: SOLO 127.0.0.1, datos ficticios.

  GET  /turnos.html        la página (contenido renderizado por JavaScript, con reloj y anuncio como ruido)
  GET  /api/turnos.json    los turnos actuales (los cambia la prueba con `poner()`)
  POST /reservar           responde 405 y cuenta el intento (el observador NUNCA debe llegar aquí)
Uso desde pruebas:  s = SitioTurnos(); s.iniciar(); s.poner(turnos=[...]); s.codigo_api = 429; s.detener()
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTML = (Path(__file__).with_name("turnos.html")).read_bytes()


class SitioTurnos:
    def __init__(self, puerto: int = 0):
        self.estado = {"turnos": [], "sesion": True, "captcha": False}
        self.codigo_api = 200
        self.envios = 0
        self.peticiones = 0
        sitio = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencioso
                pass

            def _enviar(self, codigo, cuerpo: bytes, tipo="text/html; charset=utf-8"):
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                sitio.peticiones += 1
                if self.path.startswith("/turnos.html"):
                    return self._enviar(200, HTML)
                if self.path.startswith("/api/turnos.json"):
                    if sitio.codigo_api != 200:
                        return self._enviar(sitio.codigo_api, b"{}", "application/json")
                    return self._enviar(200, json.dumps(sitio.estado).encode(), "application/json")
                return self._enviar(404, b"no")

            def do_POST(self):
                sitio.envios += 1
                return self._enviar(405, b"no")

        self.srv = ThreadingHTTPServer(("127.0.0.1", puerto), H)
        self.puerto = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.url = self.base + "/turnos.html"

    def iniciar(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def poner(self, **kw):
        self.estado = {**self.estado, **kw}

    def detener(self):
        self.srv.shutdown()
        self.srv.server_close()


PERFIL_TURNOS = {
    "zona": "#turnos", "elemento": ".turno",
    "fecha": {"selector": ".dia", "patron": r"(\d{2}/\d{2}/\d{4})", "formato": "DD/MM/AAAA"},
    "estado": {"selector": ".estado", "disponible": ["Disponible"], "no_disponible": ["Sin lugares", "Agotado"]},
    "libres": {"selector": ".cupo", "patron": r"(\d+)"},
    "hora": {"selector": ".hora", "patron": r"(\d{1,2}:\d{2})"},
    "ubicacion": {"selector": ".lugar"},
    "ubicaciones": {"Oficina Norte Centro": "NOR-1", "Oficina Norte Poniente": "NOR-2"},
    "ignorar": [".reloj", ".anuncio", ".spinner"],
    "estabilidad_ms": 600,
    "sesion_perdida": "#sesion-expirada",
}

# El calendario del PORTAL de laboratorio, observado con el mismo script genérico (sin su código específico).
PERFIL_CALENDARIO_PORTAL = {
    "zona": "[data-calendario=mensual]", "elemento": "[data-dia]",
    "fecha": {"atributo": "data-dia", "formato": "ISO"},
    "estado": {"atributo": "data-estado", "disponible": ["DISPONIBLE"],
               "no_disponible": ["AGOTADO", "DESHABILITADO", "NO_HABILITADO"]},
    "libres": {"atributo": "data-libres"},
    "estabilidad_ms": 300,
}

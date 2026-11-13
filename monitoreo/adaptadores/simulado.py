"""Adaptador TOTALMENTE SIMULADO: todo en memoria, sin red, determinista. Sirve para pruebas, para
la demostración y para comprobar que el resto del motor respeta el contrato (restricciones,
respuestas inciertas, folios). Todo lo que produce está marcado como SIMULADO."""
from __future__ import annotations

import random
import threading
from datetime import date, timedelta

from .base import (OK, RECHAZO, RED, Adaptador, Capacidades, DatoInvalido, Disponibilidad,
                   Observacion, Preparacion, Resultado, Verificacion, normalizar_fecha)

CAMPOS = (("nombre", "Nombre completo", True), ("fecha_nacimiento", "Fecha de nacimiento", True),
          ("correo", "Correo electrónico", True), ("telefono", "Teléfono", False))


class AdaptadorSimulado(Adaptador):
    def __init__(self, semilla: int = 7, sucursales: tuple[str, ...] = ("SIM-1", "SIM-2"), dias: int = 14,
                 hoy: date | None = None):
        self._lock = threading.Lock()
        self._rng = random.Random(semilla)
        self._hoy = hoy or date.today()
        self._inventario: dict[tuple[str, str], int] = {}
        for suc in sucursales:
            for i in range(1, dias + 1):
                self._inventario[(suc, (self._hoy + timedelta(days=i)).isoformat())] = self._rng.choice((0, 1, 2, 3))
        self._folios: dict[str, dict] = {}
        self._seq = 0
        self.proximo_fallo: str | None = None   # "429" | "403" | "500" | "red": lo consume la siguiente operación
        self.llamadas = 0

    def capacidades(self) -> Capacidades:
        return Capacidades(nombre="simulado", descripcion="Proveedor simulado en memoria (SIMULADO, sin red)",
                           observar=True, confirmar=True, verificar=True, autorizado=True, red=False,
                           formato_folio=r"^SIM-\d{6}$",
                           notas="Todo resultado de este adaptador es SIMULADO; no representa a ningún proveedor real.")

    def _fallo(self) -> Resultado | None:
        f, self.proximo_fallo = self.proximo_fallo, None
        if f == "429":
            return self.interpretar(429, {"Retry-After": "30"})
        if f == "403":
            return self.interpretar(403)
        if f == "500":
            return self.interpretar(500)
        if f == "red":
            return Resultado(RED, "RED", "no se pudo conectar (simulado)")
        return None

    def preparar(self, datos: dict) -> Preparacion:
        campos = tuple({"campo": c, "etiqueta": e, "obligatorio": o, "origen": c, "manual": False} for c, e, o in CAMPOS)
        faltan = tuple(c for c, _, o in CAMPOS if o and not datos.get(c))
        return Preparacion(campos, faltan, (), listo=not faltan)

    def observar(self, filtros: dict) -> Observacion:
        with self._lock:
            self.llamadas += 1
            f = self._fallo()
            if f is not None:
                return Observacion(f)
            suc = filtros.get("sucursal")
            dias = tuple(Disponibilidad(fecha=k[1], sucursal=k[0], estado="DISPONIBLE" if n > 0 else "AGOTADO",
                                        libres=n, fuente="simulado")
                         for k, n in sorted(self._inventario.items()) if not suc or k[0] == suc)
            return Observacion(Resultado(OK), dias, observada_en="SIMULADO")

    def confirmar(self, ref: dict) -> Resultado:
        """ref: {sucursal, fecha, clave}. Idempotente por clave: repetir no crea otra cita."""
        with self._lock:
            self.llamadas += 1
            f = self._fallo()
            if f is not None:
                return f
            fecha = normalizar_fecha(ref["fecha"])
            clave = str(ref.get("clave") or "")
            if not clave:
                raise DatoInvalido("clave", "la confirmación exige una clave de idempotencia")
            for folio, r in self._folios.items():
                if r["clave"] == clave:
                    return Resultado(OK, "REPETIDA", "ya estaba confirmada (misma clave)", 200, None, folio)
            k = (ref["sucursal"], fecha)
            if self._inventario.get(k, 0) <= 0:
                return Resultado(RECHAZO, "DIA_AGOTADO", "no hay lugares ese día (simulado)", 409)
            self._inventario[k] -= 1
            self._seq += 1
            folio = f"SIM-{self._seq:06d}"
            self._folios[folio] = {"sucursal": ref["sucursal"], "fecha": fecha, "clave": clave, "estado": "CONFIRMADA"}
            return Resultado(OK, "CONFIRMADA", "confirmada (SIMULADO)", 200, None, folio)

    def verificar(self, folio: str, fecha: str, sucursal: str | None) -> Verificacion:
        with self._lock:
            self.llamadas += 1
            f = self._fallo()
            if f is not None:
                return Verificacion(f)
            r = self._folios.get(folio)
            if r is None:
                return Verificacion(Resultado(OK), existe=False, coincide=False)
            coincide = r["estado"] == "CONFIRMADA" and r["fecha"] == fecha and (not sucursal or r["sucursal"] == sucursal)
            return Verificacion(Resultado(OK), True, coincide, r["estado"], r["fecha"], r["sucursal"],
                                {"fuente": "simulado", "folio": folio})

    # utilidades de prueba ------------------------------------------------------------------
    def registrar_externo(self, sucursal: str, fecha: str) -> str:
        """Simula que el operador hizo el trámite en el flujo «oficial» (aquí, el simulador)."""
        with self._lock:
            self._seq += 1
            folio = f"SIM-{self._seq:06d}"
            self._folios[folio] = {"sucursal": sucursal, "fecha": normalizar_fecha(fecha), "clave": f"ext-{self._seq}",
                                   "estado": "CONFIRMADA"}
            return folio


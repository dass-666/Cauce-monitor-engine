"""Adaptador del PORTAL DE LABORATORIO (portal_reservas, nuestro, en 127.0.0.1).

Usa las mismas piezas que el flujo de reservaciones: el contrato de formulario POR_DIA y el
navegador del motor con su gestor de conexiones (por lo que hereda que 429/403 detienen la
operación sin cambiar de conexión). La «integración autorizada» existe porque el portal es
nuestro y su token es de PRUEBA; por configuración el motor no puede apuntar a un host externo.

La confirmación de citas NO pasa por aquí: la hace el flujo de reservaciones (con su máquina de
estados, idempotencia por clave y recuperación). Este adaptador cubre observación y verificación,
que es lo que necesitan la captura manual y el diagnóstico."""
from __future__ import annotations

from datetime import datetime, timezone

from ..captura import portal
from ..captura.formularios.navegador import ErrorFormulario, RestriccionProveedor, ResultadoIncierto
from .base import (INCIERTO, OK, RED, RESTRICCION, Adaptador, Capacidades, Disponibilidad, Observacion, Preparacion,
                   Resultado, Verificacion)
from .comun import preparar_con_contrato


def _por_excepcion(e: ErrorFormulario) -> Resultado:
    if isinstance(e, RestriccionProveedor):
        return Resultado(RESTRICCION, e.codigo, e.mensaje, e.http)
    if isinstance(e, ResultadoIncierto):
        return Resultado(RED if e.codigo == "RED" else INCIERTO, e.codigo, e.mensaje, e.http)
    return Resultado(INCIERTO, e.codigo, e.mensaje, e.http)


class AdaptadorLaboratorio(Adaptador):
    def __init__(self, timeout: float = 5):
        self.timeout = timeout

    def capacidades(self) -> Capacidades:
        return Capacidades(nombre="laboratorio", descripcion="Portal de reservaciones de PRUEBA (127.0.0.1)",
                           observar=True, confirmar=False, verificar=True, autorizado=True, red=True,
                           formato_folio=r"^DIA-\d{8}-\d{5}[A-Z]$",
                           notas="La confirmación la hace el flujo de reservaciones del motor, no el adaptador.")

    def preparar(self, datos: dict) -> Preparacion:
        return preparar_con_contrato(portal.contrato("POR_DIA"), datos)

    def _nav(self, operacion: str):
        return portal.navegador(timeout=self.timeout, modo="POR_DIA", operacion=operacion)

    def observar(self, filtros: dict) -> Observacion:
        # El portal de laboratorio usa la región en minúsculas («norte»); el motor, en mayúsculas.
        params = {k: v for k, v in {"region": (filtros.get("region") or "").lower() or None,
                                    "sucursal": filtros.get("sucursal"),
                                    "desde": filtros.get("desde"), "hasta": filtros.get("hasta")}.items() if v}
        nav = self._nav("nueva_sesion")
        try:
            r = nav._api("GET", "/api/dias", params=params)
        except ErrorFormulario as e:
            return Observacion(_por_excepcion(e))
        finally:
            nav.cerrar()
        res = self.interpretar(r.status_code, dict(r.headers))
        if res.tipo != OK:
            return Observacion(res)
        dias = tuple(Disponibilidad(fecha=self.normalizar_fecha(d["fecha"]), sucursal=d.get("sucursal"),
                                    estado=d.get("estado", "DESCONOCIDO"), libres=d.get("libres"), fuente="laboratorio")
                     for d in r.json().get("dias", []))
        return Observacion(Resultado(OK, http=r.status_code), dias,
                           observada_en=datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z")

    def verificar(self, folio: str, fecha: str, sucursal: str | None) -> Verificacion:
        nav = self._nav("verificacion")
        try:
            rp = nav.verificar_folio(folio)
        except ErrorFormulario as e:
            return Verificacion(_por_excepcion(e))
        finally:
            nav.cerrar()
        if rp is None:
            return Verificacion(Resultado(OK), existe=False, coincide=False)
        f = self.normalizar_fecha(rp["fecha"]) if rp.get("fecha") else None
        coincide = (rp.get("estado") == "CONFIRMADA" and rp.get("folio") == folio and f == fecha
                    and (not sucursal or rp.get("sucursal") == sucursal))
        # Evidencia sin datos de la persona (el portal devuelve también su identificador: no se guarda).
        evid = {"fuente": "api_por_folio", "estado_portal": rp.get("estado"), "fecha_portal": f,
                "sucursal_portal": rp.get("sucursal"), "confirmada_en_portal": rp.get("confirmada_en")}
        return Verificacion(Resultado(OK), True, coincide, rp.get("estado"), f, rp.get("sucursal"), evid)

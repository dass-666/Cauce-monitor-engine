"""Registro EN MEMORIA de solicitudes: máquina de estados e idempotencia (modelo de referencia).

No persiste nada ni toca la base de Monitor: sirve para fijar y probar las reglas antes de la tabla y la
migración (PENDIENTES, no autorizadas). La versión con base de datos deberá reproducir estas pruebas.
"""
from __future__ import annotations

from datetime import datetime

from . import contrato as C


class ErrorRegistro(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo


class Registro:
    def __init__(self):
        self._por_id: dict[str, dict] = {}
        self._por_clave: dict[str, str] = {}
        self.diario: list[dict] = []        # eventos sin datos personales

    def _evento(self, id_sol: str, de: str | None, a: str, codigo: str = "") -> None:
        self.diario.append({"id_solicitud": id_sol, "de": de, "a": a, "codigo": codigo})

    def crear(self, solicitud: dict, ahora: datetime) -> tuple[dict, bool]:
        """Devuelve (registro, es_nuevo). Misma clave de idempotencia = mismo registro, sin duplicar."""
        errores = C.validar_solicitud(solicitud, ahora)
        if errores:
            raise ErrorRegistro("SOLICITUD_INVALIDA", C.resumen_error_seguro(errores))
        clave = solicitud["clave_idempotencia"]
        if clave in self._por_clave:
            previo = self._por_id[self._por_clave[clave]]
            if previo["huella_campos"] != solicitud["huella_campos"] or previo["accion"] != solicitud["accion"]:
                raise ErrorRegistro("IDEMPOTENCIA_CONFLICTO", "misma clave con contenido distinto")
            return previo, False
        if solicitud["id_solicitud"] in self._por_id:
            raise ErrorRegistro("ID_REPETIDO", "id_solicitud ya usado")
        reg = {"id_solicitud": solicitud["id_solicitud"], "clave": clave, "accion": solicitud["accion"],
               "huella_campos": solicitud["huella_campos"], "caduca": solicitud["caduca"], "estado": "CREADA",
               "codigo": ""}
        self._por_id[reg["id_solicitud"]] = reg
        self._por_clave[clave] = reg["id_solicitud"]
        self._evento(reg["id_solicitud"], None, "CREADA")
        return reg, True

    def mover(self, id_sol: str, a: str, ahora: datetime, codigo: str = "", conciliado_por: str | None = None) -> dict:
        reg = self._por_id[id_sol]
        de = reg["estado"]
        if a not in C.TRANSICIONES.get(de, ()):
            raise ErrorRegistro("TRANSICION_NO_PERMITIDA", f"{de} -> {a}")
        if de == "INCIERTA" and not conciliado_por:
            raise ErrorRegistro("CONCILIACION_REQUERIDA", "una solicitud INCIERTA solo la resuelve una persona")
        if a == "LANZADA" and ahora >= C._ts(reg["caduca"]):
            raise ErrorRegistro("SOLICITUD_CADUCADA", "no se lanza una solicitud caducada")
        reg["estado"], reg["codigo"] = a, codigo
        self._evento(id_sol, de, a, codigo)
        return reg

    def recibir(self, solicitud: dict, respuesta, ahora: datetime) -> dict:
        """Aplica la respuesta del conector. Respuesta inválida o tardía NUNCA da PREPARADO."""
        reg = self._por_id[solicitud["id_solicitud"]]
        if reg["estado"] != "LANZADA":
            raise ErrorRegistro("TRANSICION_NO_PERMITIDA", f"respuesta recibida en estado {reg['estado']}")
        if ahora >= C._ts(solicitud["caduca"]):
            return self.mover(reg["id_solicitud"], "CADUCADA", ahora, "RESPUESTA_TARDIA_DESCARTADA")
        errores = C.validar_respuesta(respuesta, solicitud)
        if errores:      # no se sabe qué hizo el conector: incierta, con motivo sin valores
            return self.mover(reg["id_solicitud"], "INCIERTA", ahora, "RESPUESTA_INVALIDA")
        return self.mover(reg["id_solicitud"], C.MAPA_ESTADO[respuesta["estado"]], ahora, respuesta["codigo"])

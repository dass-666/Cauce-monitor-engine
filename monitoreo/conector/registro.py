"""Registro EN MEMORIA de solicitudes: máquina de estados e idempotencia (modelo de referencia).

No persiste nada ni toca la base de Monitor: fija y prueba las reglas antes de la tabla y la migración (PENDIENTES,
no autorizadas). Dos claves distintas: `solicitud_id` (viaja al conector, un solo uso) y `clave_idempotencia`
(interna de Monitor, NO viaja: liga la solicitud con su expediente/reservación/acción).
"""
from __future__ import annotations

from datetime import datetime, timedelta

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
        self.diario.append({"solicitud_id": id_sol, "de": de, "a": a, "codigo": codigo})

    def crear(self, solicitud: dict, clave_idempotencia: str, ahora: datetime,
              solo_local: bool = True) -> tuple[dict, bool]:
        """Devuelve (registro, es_nuevo). Misma clave y mismo contenido = mismo registro, sin duplicar.
        `solo_local=False` solo para un perfil EXTERNO ya comprobado por `autorrelleno.adaptador.configuracion()`."""
        errores = C.validar_solicitud(solicitud, ahora, solo_local=solo_local)
        if errores:
            raise ErrorRegistro("SOLICITUD_INVALIDA", C.resumen_error_seguro(errores))
        if not 8 <= len(clave_idempotencia) <= 128:
            raise ErrorRegistro("CLAVE_INVALIDA", "clave_idempotencia de 8 a 128 caracteres")
        contenido = (solicitud["formulario"]["perfil_sha256"], C._canon(solicitud["campos"]))
        if clave_idempotencia in self._por_clave:
            previo = self._por_id[self._por_clave[clave_idempotencia]]
            if previo["contenido"] != contenido:
                raise ErrorRegistro("IDEMPOTENCIA_CONFLICTO", "misma clave con contenido distinto")
            return previo, False
        if solicitud["solicitud_id"] in self._por_id:
            raise ErrorRegistro("SOLICITUD_REPETIDA", "solicitud_id ya usado (un solo uso)")
        reg = {"solicitud_id": solicitud["solicitud_id"], "clave": clave_idempotencia, "contenido": contenido,
               "caduca": C.ts(solicitud["caduca_en"]), "espera_s": solicitud["opciones"]["espera_revision_max_s"],
               "limite": None, "estado": "CREADA", "codigo": ""}
        self._por_id[reg["solicitud_id"]] = reg
        self._por_clave[clave_idempotencia] = reg["solicitud_id"]
        self._evento(reg["solicitud_id"], None, "CREADA")
        return reg, True

    def mover(self, id_sol: str, a: str, ahora: datetime, codigo: str = "", conciliado_por: str | None = None) -> dict:
        reg = self._por_id[id_sol]
        de = reg["estado"]
        if a not in C.TRANSICIONES.get(de, ()):
            raise ErrorRegistro("TRANSICION_NO_PERMITIDA", f"{de} -> {a}")
        if de == "INCIERTA" and not conciliado_por:
            raise ErrorRegistro("CONCILIACION_REQUERIDA", "una solicitud INCIERTA solo la resuelve una persona")
        if a == "LANZADA":
            if ahora >= reg["caduca"]:
                raise ErrorRegistro("SOLICITUD_CADUCADA", "no se lanza una solicitud caducada")
            # plazo del proceso: la espera de revisión humana más un margen; pasado, Monitor lo mata (INCIERTA)
            reg["limite"] = ahora + timedelta(seconds=reg["espera_s"] + C.MARGEN_PROCESO_S)
        reg["estado"], reg["codigo"] = a, codigo
        self._evento(id_sol, de, a, codigo)
        return reg

    def recibir(self, solicitud: dict, respuesta, ahora: datetime, codigo_salida: int | None = None) -> dict:
        """Aplica la respuesta. Inválida, ajena o tardía NUNCA da un estado exitoso: pasa a INCIERTA o se descarta."""
        reg = self._por_id[solicitud["solicitud_id"]]
        if reg["estado"] != "LANZADA":
            raise ErrorRegistro("TRANSICION_NO_PERMITIDA", f"respuesta recibida en estado {reg['estado']}")
        if ahora >= reg["limite"]:      # el proceso ya debía haber sido terminado: no se sabe qué pasó
            return self.mover(reg["solicitud_id"], "INCIERTA", ahora, "RESPUESTA_FUERA_DE_PLAZO")
        errores = C.validar_respuesta(respuesta, solicitud, codigo_salida)
        if errores:
            return self.mover(reg["solicitud_id"], "INCIERTA", ahora, "RESPUESTA_INVALIDA")
        return self.mover(reg["solicitud_id"], C.MAPA_ESTADO[respuesta["estado"]], ahora, respuesta["estado"])

    def proceso_terminado_sin_respuesta(self, id_sol: str, ahora: datetime) -> dict:
        """Tiempo agotado, proceso muerto o sin salida: INCIERTA (nunca se asume éxito ni se reintenta solo)."""
        return self.mover(id_sol, "INCIERTA", ahora, "SIN_RESPUESTA")

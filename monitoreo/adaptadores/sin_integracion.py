"""Adaptador para un portal OFICIAL con el que NO hay integración autorizada.

El motor no se conecta a nada: no observa, no confirma y no verifica. Solo prepara (qué datos
pedirá el trámite, a partir del contrato de formulario) y admite la CAPTURA MANUAL: el operador
hace el trámite en el sitio oficial, con la persona presente, y registra aquí el folio y la fecha
que le dio el portal. Ese registro queda DECLARADO (nunca «confirmado por el motor»).

Cuando exista una autorización formal y credenciales del proveedor, se escribe un adaptador nuevo
que implemente `observar`/`verificar` contra la API autorizada; este no cambia."""
from __future__ import annotations

from ..captura import portal
from .base import Adaptador, Capacidades, Preparacion
from .comun import preparar_con_contrato

PASOS = ("Abre el sitio oficial del trámite en el navegador del equipo de ventanilla.",
         "Captura los datos con la persona presente; resuelve tú cualquier verificación del sitio.",
         "Al terminar, copia el folio y la fecha que muestra el sitio y regístralos en el motor.",
         "No registres nada si el sitio no mostró un folio: eso NO es una cita.")


class AdaptadorSinIntegracion(Adaptador):
    def __init__(self, nombre: str = "oficial_manual", formato_folio: str = r"^[A-Z0-9-]{4,64}$"):
        self._nombre, self._formato = nombre, formato_folio

    def capacidades(self) -> Capacidades:
        return Capacidades(nombre=self._nombre, descripcion="Portal oficial SIN integración autorizada (solo captura manual)",
                           observar=False, confirmar=False, verificar=False, autorizado=False, red=False,
                           formato_folio=self._formato,
                           notas="El motor no se conecta al portal. Los registros quedan DECLARADOS por el operador.")

    def preparar(self, datos: dict) -> Preparacion:
        p = preparar_con_contrato(portal.contrato("POR_DIA"), datos)
        return Preparacion(p.campos, p.faltantes, PASOS + p.pasos_manuales, p.listo)

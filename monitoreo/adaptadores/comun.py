"""Piezas compartidas por los adaptadores que usan un contrato de formulario (preparación sin red)."""
from __future__ import annotations

from ..captura.formularios.contrato import Contrato, requerido
from .base import Preparacion


def preparar_con_contrato(contrato: Contrato, datos: dict) -> Preparacion:
    """Qué pide el proveedor y qué falta, SIN devolver valores (aquí no viajan datos personales).
    `datos` usa los nombres del expediente (origen): nombre, correo, telefono, ..."""
    valores = {c.nombre: (None if datos.get(c.origen) in (None, "") else str(datos.get(c.origen)))
               for c in contrato.automaticos}
    campos, faltan, pasos = [], [], []
    for c in contrato.campos:
        if c.modo == "SOLO_LECTURA":
            continue
        req = requerido(c, valores)
        campos.append({"campo": c.nombre, "etiqueta": c.etiqueta, "obligatorio": req, "origen": c.origen,
                       "manual": c.manual})
        if c.manual:
            pasos.append(f"{c.etiqueta}: {c.instrucciones or 'la teclea el operador con la persona presente'}")
        elif req and valores.get(c.nombre) is None and c.origen not in faltan:
            faltan.append(c.origen)
    return Preparacion(tuple(campos), tuple(faltan), tuple(pasos), listo=not faltan)

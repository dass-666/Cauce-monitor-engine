"""v2.2 · Trazabilidad de un expediente: una sola línea de tiempo con todo lo que le pasó.

Reúne, ordenado por fecha:
- el historial del expediente (cambios de estado, campos modificados SOLO por nombre, eventos);
- los consentimientos y su cadena de sellos, con la verificación de cada cadena;
- las solicitudes en la cola del motor y sus cambios de estado;
- su paso por los planes del día;
- las atenciones en Ventanilla;
- los intentos de reservación con su folio.

No incluye datos personales: identifica a la persona por el código del expediente. Es de solo
lectura y sirve para responder «quién hizo qué, cuándo y con qué autorización».
"""
from __future__ import annotations

from sqlalchemy import or_, select

from ..models import (Atencion, Expediente, PlanDiario, PlanElemento, Request, RequestEvent,
                      Reservacion)
from ..db import utcnow
from . import consentimiento as cs
from . import expedientes as ex


def _iso(dt):
    if dt is None:
        return None
    return (dt.replace(tzinfo=None) if dt.tzinfo else dt).isoformat() + "Z"


def de_expediente(db, exp_id: int) -> dict:
    with db.session() as s:
        e = s.get(Expediente, exp_id)
        if e is None:
            return {}
        linea: list[dict] = []
        for h in ex.historial(s, exp_id):
            linea.append({"ts": h["ts"], "fuente": "expediente", "tipo": h["tipo"], "actor": h["actor"],
                          "detalle": h["detalle"]})
        for ce in cs.eventos_de_expediente(s, exp_id):
            linea.append({"ts": ce["ts"], "fuente": "consentimiento", "tipo": "CONSENTIMIENTO_" + ce["tipo"],
                          "actor": ce["actor"], "detalle": {**(ce["detalle"] or {}), "sello": ce["sello"]}})
        codigos = [e.codigo] + [f"{e.codigo}-R{i}" for i in range(2, (e.ronda or 1) + 1)]
        reqs = list(s.scalars(select(Request).where(or_(Request.holder == e.codigo, Request.code.in_(codigos)))))
        for r in reqs:
            for re_ in s.scalars(select(RequestEvent).where(RequestEvent.request_id == r.id).order_by(RequestEvent.id)):
                linea.append({"ts": _iso(re_.ts), "fuente": "cola", "tipo": f"SOLICITUD_{re_.to_status}",
                              "actor": re_.actor, "detalle": {"solicitud": r.code, "desde": re_.from_status,
                                                              "nota": (re_.note or "")[:120]}})
        planes = []
        for el, fecha in s.execute(select(PlanElemento, PlanDiario.fecha_jornada).join(PlanDiario, PlanDiario.id == PlanElemento.plan_id)
                                   .where(PlanElemento.expediente_id == exp_id).order_by(PlanElemento.id)):
            planes.append({"jornada": str(fecha), "elemento": el.id, "estado": el.estado, "prioridad": el.prioridad,
                           "fecha_asignada": el.fecha_asignada, "sucursal_asignada": el.sucursal_asignada})
        atenciones = [{"codigo": a.codigo, "estado": a.estado, "resultado": a.resultado, "usuario": a.username,
                       "iniciada_en": _iso(a.iniciada_en), "cerrada_en": _iso(a.cerrada_en), "folio": a.folio}
                      for a in s.scalars(select(Atencion).where(Atencion.expediente_id == exp_id).order_by(Atencion.id))]
        reservas = [{"id": r.id, "estado": r.estado, "modo": r.modo, "fecha": r.fecha, "sucursal": r.sucursal,
                     "folio": r.folio, "confirmada_por": r.confirmada_por, "error_codigo": r.error_codigo,
                     "creada_en": _iso(r.created_at), "actualizada_en": _iso(r.updated_at)}
                    for r in s.scalars(select(Reservacion).where(Reservacion.expediente_id == exp_id).order_by(Reservacion.id))]
        cons = cs.de_expediente(s, exp_id)
        ids = [c["id"] for c in cons]
        vig = cs.vigente(s, e, utcnow())
        resumen = {"codigo": e.codigo, "estado": e.estado, "motivo_cierre": e.motivo_cierre,
                   "tipo_tramite": e.tipo_tramite, "creado_por": e.creado_por, "creado_en": _iso(e.created_at),
                   "consentimiento_vigente": vig.codigo if vig else None}
    verif = [cs.verificar(db, i) for i in ids]
    linea.sort(key=lambda x: x["ts"] or "")          # estable: a igual hora, el orden en que se registró
    return {"expediente": resumen, "consentimientos": cons,
            "verificacion_consentimientos": {"ok": all(v["ok"] for v in verif), "consentimientos": len(ids),
                                             "problemas": [p for v in verif for p in v["problemas"]]},
            "planes": planes, "atenciones": atenciones, "reservaciones": reservas, "linea_de_tiempo": linea}

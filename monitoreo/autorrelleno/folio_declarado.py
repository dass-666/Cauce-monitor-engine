"""Folio DECLARADO por el operador cuando el portal no tiene una vía AUTORIZADA para comprobar folios.

Eslabón final del flujo coincidencia -> Forms -> folio (ver `coincidencia.py`). Reglas:
- Queda PENDIENTE_DE_VERIFICACION. La reservación sigue PREPARADA; el expediente y el plan NO pasan a confirmado y
  nada lo presenta como cita. Nunca se marca CONFIRMADA sin evidencia del portal por una vía autorizada.
- Se guarda en `capturas_manuales` (estado DECLARADA) con operador, fecha de registro, portal, folio y procedencia,
  y queda en el historial del expediente (FOLIO_DECLARADO) y en la auditoría.
- Sin duplicados: el mismo folio no se registra dos veces para el mismo portal (restricción única) y una operación
  no admite un segundo folio declarado distinto mientras el primero siga pendiente.
- Verificación posterior (`verificar`): sin vía autorizada sigue PENDIENTE y se anota el intento; con vía, se
  comprueba como cualquier folio (`coincidencia.verificar_folio`) y solo si coincide la reservación pasa a CONFIRMADA.
- Si el portal SÍ tiene vía autorizada, declarar se niega: se usa «Registrar folio» (comprobación inmediata).
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import security
from ..captura import consentimiento as cs
from ..captura import expedientes as ex
from ..captura import reservaciones as rs
from ..captura.asistida import normalizar_folio
from ..db import Database
from ..models import CapturaManual, Expediente, PlanElemento, Reservacion
from ..planificador import plan as pl
from . import coincidencia as cz

PENDIENTE = "PENDIENTE_DE_VERIFICACION"
PROCEDENCIA = "OPERADOR_VENTANILLA_TRAS_FORMS"
_FOLIO_OK = re.compile(r"[A-Z0-9][A-Z0-9-]{3,39}")
ErrorFolio = cz.ErrorFolio


def ruta_verificacion(res: Reservacion) -> str | None:
    """Adaptador autorizado para comprobar el folio de esta reservación, o None si no hay vía."""
    forms = (res.evidencia or {}).get("forms") or {}
    return None if forms.get("externo") else cz.ADAPTADOR_POR_CONTRATO.get(res.contrato)


def de_reservacion(s, res_id: int, exp_id: int) -> CapturaManual | None:
    for c in s.scalars(select(CapturaManual).where(CapturaManual.expediente_id == exp_id)
                       .order_by(CapturaManual.id.desc())):
        if (c.detalle or {}).get("reservacion_id") == res_id:
            return c
    return None


def vista(c: CapturaManual) -> dict:
    from ..api.serializers import iso
    d = c.detalle or {}
    v = d.get("verificacion") or {}
    return {"captura_id": c.id, "folio": c.folio, "estado": PENDIENTE if c.estado == "DECLARADA" else c.estado,
            "operador": c.operador, "registrado_en": iso(c.creada), "portal": d.get("portal"),
            "procedencia": d.get("procedencia"), "fecha": c.fecha, "sucursal": c.sucursal,
            "ultimo_intento": v.get("ultimo_intento"), "motivo": v.get("motivo"), "es_cita_confirmada": False}


def _propia(user, res: Reservacion | None) -> bool:
    return res is not None and (user.is_admin or (user.operator_id is not None and res.operator_id == user.operator_id))


def declarar(db: Database, user, res_id: int, folio: str, folio_confirmacion: str, folio_visto: bool,
             now: datetime) -> dict:
    f1, f2 = normalizar_folio(folio), normalizar_folio(folio_confirmacion)
    if not f1 or f1 != f2:
        raise ErrorFolio(422, "FOLIOS_DISTINTOS", "Teclea el mismo folio dos veces.")
    if not _FOLIO_OK.fullmatch(f1):
        raise ErrorFolio(422, "FOLIO_INVALIDO", "El folio solo puede llevar letras, números y guiones (4 a 40).")
    if not folio_visto:
        raise ErrorFolio(422, "FOLIO_NO_VISTO", "Marca que el portal mostró este folio: sin folio visible no se "
                         "registra.")
    actor = f"user:{user.username}"
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if not _propia(user, res):
            raise ErrorFolio(404, "NO_ENCONTRADA", "Reservación no encontrada.")
        previo = de_reservacion(s, res_id, res.expediente_id)
        if previo is not None and previo.estado == "DECLARADA":
            if previo.folio == f1:
                return {"declarado": vista(previo), "repetido": True}
            raise ErrorFolio(409, "YA_DECLARADO", "Esta operación ya tiene un folio declarado pendiente de "
                             f"verificación ({previo.folio}). No se registra otro.")
        if res.estado != rs.PREPARADA:
            raise ErrorFolio(409, "ESTADO_NO_PERMITIDO", f"La reservación está {res.estado}: no se declara un folio.")
        el = s.scalar(select(PlanElemento).where(PlanElemento.reservacion_id == res_id,
                                                 PlanElemento.estado == pl.PREPARADO))
        if el is None or not el.fecha_asignada:
            raise ErrorFolio(409, "SIN_COINCIDENCIA", "Esta reservación no viene de una coincidencia del plan.")
        forms = (res.evidencia or {}).get("forms") or {}
        if forms.get("resultado") != cz.LISTA:
            raise ErrorFolio(409, "FORMS_NO_PREPARO", "Forms no dejó el formulario rellenado y verificado para esta "
                             "operación: no se declara un folio.")
        if ruta_verificacion(res) is not None:
            raise ErrorFolio(409, "VERIFICACION_DISPONIBLE", "Este portal tiene una vía autorizada para comprobar "
                             "folios: usa «Registrar folio» (se comprueba antes de confirmar).")
        exp = s.get(Expediente, res.expediente_id)
        if cs.vigente(s, exp, now) is None:
            m = cs.motivo_bloqueo(s, exp, now)
            raise ErrorFolio(409, m["codigo"], m["mensaje"])
        sitio = forms.get("sitio") or "desconocido"
        c = CapturaManual(creada=now, adaptador=f"portal:{sitio}"[:32], expediente_id=exp.id, atencion_id=None,
                          operador=user.username[:64], folio=f1, fecha=el.fecha_asignada,
                          sucursal=el.sucursal_asignada, estado="DECLARADA",
                          detalle={"declarada_por": user.username, "portal": sitio, "procedencia": PROCEDENCIA,
                                   "reservacion_id": res_id, "elemento_id": el.id,
                                   "solicitud_forms": forms.get("solicitud_id"), "folio_visto_por_operador": True,
                                   "estado_operacion": PENDIENTE})
        s.add(c)
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            raise ErrorFolio(409, "FOLIO_DUPLICADO", "Ese folio ya está registrado para este portal en Monitor: no "
                             "se registra dos veces.") from None
        t = Reservacion.__table__
        s.execute(update(t).where(t.c.id == res_id, t.c.estado == rs.PREPARADA).values(
            evidencia={**(res.evidencia or {}), "folio_declarado": {
                "captura_id": c.id, "folio": f1, "estado": PENDIENTE, "operador": user.username,
                "registrado_en": now.isoformat() + "Z", "portal": sitio, "procedencia": PROCEDENCIA}},
            updated_at=now))
        ex.evento(s, exp.id, "FOLIO_DECLARADO", actor, now, reservacion_id=res_id, folio=f1, portal=sitio,
                  estado=PENDIENTE, procedencia=PROCEDENCIA, es_cita=False)
        security.audit(s, user.username, "folio_declarado", f1,
                       {"reservacion_id": res_id, "captura_id": c.id, "portal": sitio})
        s.flush()
        return {"declarado": vista(c), "repetido": False}


def verificar(db: Database, user, res_id: int, now: datetime) -> dict:
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if not _propia(user, res):
            raise ErrorFolio(404, "NO_ENCONTRADA", "Reservación no encontrada.")
        c = de_reservacion(s, res_id, res.expediente_id)
        if c is None:
            raise ErrorFolio(404, "SIN_FOLIO_DECLARADO", "Esta operación no tiene un folio declarado.")
        if c.estado != "DECLARADA":
            return {"declarado": vista(c)}
        cid, folio, ruta = c.id, c.folio, ruta_verificacion(res)

    def anotar(estado: str, info: dict) -> dict:
        with db.session() as s:
            c = s.get(CapturaManual, cid)
            c.estado = estado
            c.detalle = {**(c.detalle or {}), "verificacion": {"ultimo_intento": now.isoformat() + "Z", **info}}
            if estado != "DECLARADA":
                c.verificada_en = now
                ex.evento(s, c.expediente_id, f"FOLIO_DECLARADO_{estado}"[:32], "motor", now, folio=c.folio,
                          reservacion_id=res_id)
            s.flush()
            return vista(c)

    if ruta is None:
        d = anotar("DECLARADA", {"resultado": "SIN_VIA",
                                 "motivo": "el portal no tiene una vía autorizada para comprobar folios"})
        raise ErrorFolio(409, "SIN_VERIFICACION_AUTORIZADA", "Sigue PENDIENTE DE VERIFICACIÓN: este portal no tiene "
                         "una vía autorizada para comprobar folios.", {"declarado": d})
    try:
        out = cz.verificar_folio(db, user, res_id, folio, folio, now)
    except ErrorFolio as e:
        estado = "DISCREPANCIA" if e.codigo in ("FOLIO_INEXISTENTE", "FOLIO_NO_COINCIDE") else "DECLARADA"
        e.detalle["declarado"] = anotar(estado, {"resultado": e.codigo, "motivo": e.mensaje[:200]})
        raise
    out["declarado"] = anotar("VERIFICADA", {"resultado": "OK"})
    return out

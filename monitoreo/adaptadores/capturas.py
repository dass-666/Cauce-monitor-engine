"""v2.0 · P2 · Captura manual: el operador hizo el trámite en el flujo oficial y registra el resultado.

Estados (models.CapturaManual): DECLARADA -> VERIFICADA | DISCREPANCIA | SIN_VERIFICACION.
- Nunca se marca VERIFICADA sin una respuesta del proveedor por una vía AUTORIZADA que coincida
  en folio y fecha (y sucursal si se dio).
- Si el proveedor restringe (429/403), la verificación se detiene, se anota el motivo y la captura
  sigue DECLARADA: no se reintenta sola. Una persona puede pedir otra verificación más tarde.
- No cambia el estado del expediente ni de ninguna reservación del motor: es un registro aparte,
  auditado, para no mezclar «lo que dijo el operador» con «lo que confirmó el motor».
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import adaptadores, security
from ..captura import expedientes as ex
from ..db import Database
from ..models import Atencion, CapturaManual, Expediente
from .base import OK, RESTRICCION, DatoInvalido

DECLARADA, VERIFICADA, DISCREPANCIA, SIN_VERIFICACION = "DECLARADA", "VERIFICADA", "DISCREPANCIA", "SIN_VERIFICACION"


class ErrorCaptura(Exception):
    def __init__(self, status: int, mensaje: str, extra: dict | None = None):
        super().__init__(mensaje)
        self.status, self.mensaje, self.extra = status, mensaje, extra or {}


def vista(c: CapturaManual) -> dict:
    from ..api.serializers import iso
    return {"id": c.id, "adaptador": c.adaptador, "expediente_id": c.expediente_id, "atencion_id": c.atencion_id,
            "operador": c.operador, "folio": c.folio, "fecha": c.fecha, "sucursal": c.sucursal,
            "transaccion_id": c.transaccion_id, "estado": c.estado, "creada": iso(c.creada),
            "verificada_en": iso(c.verificada_en), "detalle": c.detalle or {},
            "es_cita_confirmada_por_el_motor": False}


def _actor(user) -> str:
    return f"{user.username}"[:64]


def registrar(db: Database, user, nombre_adaptador: str, folio: str, fecha: str, sucursal: str | None,
              now: datetime, expediente_id: int | None = None, atencion_id: int | None = None,
              transaccion_id: str | None = None, folio_visto: bool = False) -> dict:
    if not folio_visto:
        raise ErrorCaptura(422, "Marca que el sitio oficial mostró este folio: sin folio visible no hay cita")
    try:
        ad = adaptadores.obtener(nombre_adaptador)
    except KeyError:
        raise ErrorCaptura(404, "Adaptador desconocido") from None
    cap = ad.capacidades()
    if not cap.captura_manual:
        raise ErrorCaptura(409, "Este adaptador no admite captura manual")
    try:
        folio_n = ad.validar_folio(folio)
        fecha_n = ad.normalizar_fecha(fecha)
    except DatoInvalido as e:
        raise ErrorCaptura(422, e.mensaje, {"campo": e.campo}) from e
    suc = (sucursal or "").strip().upper()[:32] or None
    with db.session() as s:
        if expediente_id is not None:
            e = s.get(Expediente, expediente_id)
            if e is None or not ex.puede_ver(user, e):
                raise ErrorCaptura(404, "Expediente no encontrado")
        if atencion_id is not None:
            a = s.get(Atencion, atencion_id)
            if a is None or (not user.is_admin and a.operator_id != user.operator_id):
                raise ErrorCaptura(404, "Atención no encontrada")
            if expediente_id is None:
                expediente_id = a.expediente_id
        c = CapturaManual(creada=now, adaptador=cap.nombre, expediente_id=expediente_id, atencion_id=atencion_id,
                          operador=_actor(user), folio=folio_n, fecha=fecha_n, sucursal=suc,
                          transaccion_id=(transaccion_id or "").strip()[:64] or None, estado=DECLARADA,
                          detalle={"declarada_por": _actor(user)})
        s.add(c)
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            raise ErrorCaptura(409, "Ese folio ya está registrado para este proveedor") from None
        if expediente_id is not None:
            ex.evento(s, expediente_id, "CAPTURA_MANUAL", _actor(user), now, folio=folio_n, adaptador=cap.nombre)
        security.audit(s, user.username, "captura_manual_registrada", folio_n, {"adaptador": cap.nombre, "id": c.id})
        cid = c.id
    return verificar(db, user, cid, now, _primera=True)


def verificar(db: Database, user, cid: int, now: datetime, _primera: bool = False) -> dict:
    with db.session() as s:
        c = s.get(CapturaManual, cid)
        if c is None or (not user.is_admin and c.operador != _actor(user)):
            raise ErrorCaptura(404, "Captura no encontrada")
        if c.estado == VERIFICADA:
            return vista(c)
        nombre, folio, fecha, suc = c.adaptador, c.folio, c.fecha, c.sucursal
    ad = adaptadores.obtener(nombre)
    if not ad.capacidades().verificar:
        return _cerrar(db, cid, SIN_VERIFICACION, now, {"motivo": "el adaptador no tiene una vía autorizada para comprobar folios"})
    v = ad.verificar(folio, fecha, suc)
    if v.resultado.tipo != OK:
        # Incierto, sin red o RESTRINGIDO: sigue DECLARADA. Con restricción, se anota y NO se reintenta solo.
        info = {"ultimo_intento": now.isoformat() + "Z", "resultado": v.resultado.tipo, "codigo": v.resultado.codigo,
                "motivo": v.resultado.mensaje[:200], "detenida": v.resultado.tipo == RESTRICCION}
        return _cerrar(db, cid, DECLARADA, now, info, verificada=False)
    if v.coincide:
        return _cerrar(db, cid, VERIFICADA, now, {"evidencia": v.evidencia})
    return _cerrar(db, cid, DISCREPANCIA, now, {"existe_en_proveedor": v.existe, "estado_proveedor": v.estado_proveedor,
                                                 "fecha_proveedor": v.fecha, "sucursal_proveedor": v.sucursal})


def _cerrar(db: Database, cid: int, estado: str, now: datetime, info: dict, verificada: bool = True) -> dict:
    with db.session() as s:
        c = s.get(CapturaManual, cid)
        c.estado = estado
        c.detalle = {**(c.detalle or {}), "verificacion": info}
        if verificada and estado in (VERIFICADA, DISCREPANCIA):
            c.verificada_en = now
        if c.expediente_id is not None and estado != DECLARADA:
            ex.evento(s, c.expediente_id, f"CAPTURA_MANUAL_{estado}"[:32], "motor", now, folio=c.folio)
        if estado == DISCREPANCIA and c.expediente_id is not None:
            e = s.get(Expediente, c.expediente_id)
            if e is not None and e.estado not in (ex.CERRADO,):
                ex.a_revision(s, e, "motor", now, "CAPTURA_MANUAL_DISCREPANCIA")
        s.flush()
        return vista(c)


def listar(db: Database, user, limite: int = 200) -> list[dict]:
    with db.session() as s:
        q = select(CapturaManual).order_by(CapturaManual.id.desc()).limit(limite)
        if not user.is_admin:
            q = q.where(CapturaManual.operador == _actor(user))
        return [vista(c) for c in s.scalars(q)]

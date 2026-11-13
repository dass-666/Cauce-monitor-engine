"""Expedientes (v1.5): alta, modificación, permisos, minimización y trazabilidad.

Integración con el motor existente (sin duplicar colas ni asignación): cada expediente crea
una `Request` del motor (tipo de recurso `cita`, región del expediente y, si la hay, la
sucursal como condición). Cuando el conector del portal detecta disponibilidad, el motor
asigna esa solicitud a un operador con su lógica de siempre; `seguimiento.sincronizar` lleva
esa asignación al expediente (evento DISPONIBILIDAD_DETECTADA).

La `Request` NUNCA lleva datos personales: su titular es el código del expediente, porque las
solicitudes aparecen en alertas, Telegram y registros.
"""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Expediente, ExpedienteEvento, Operator, Region, Request
from . import validacion as val

# Estados del expediente
PENDIENTE, ASIGNADO, EN_CAPTURA, RESERVANDO = "PENDIENTE", "ASIGNADO", "EN_CAPTURA", "RESERVANDO"
CONFIRMADO, FALLIDO, CANCELADO = "CONFIRMADO", "FALLIDO", "CANCELADO"
ACTIVOS = (PENDIENTE, ASIGNADO, EN_CAPTURA, RESERVANDO)
CERRADOS = (CONFIRMADO, FALLIDO, CANCELADO)
GRUPO = {PENDIENTE: "pendiente", ASIGNADO: "pendiente", EN_CAPTURA: "en_proceso", RESERVANDO: "en_proceso",
         CONFIRMADO: "confirmado", FALLIDO: "fallido", CANCELADO: "fallido"}
TIPO_RECURSO = "cita"

# Eventos normalizados (los pide la especificación v1.5) y de historial
EVENTOS_NORMALIZADOS = ("DISPONIBILIDAD_DETECTADA", "RECURSO_SELECCIONADO", "FORMULARIO_PREPARADO",
                        "RESERVACION_PENDIENTE", "RESERVACION_CONFIRMADA", "RESERVACION_RECHAZADA",
                        "RESERVACION_EXPIRADA")


class ErrorExpediente(Exception):
    def __init__(self, http: int, mensaje: str, errores: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.errores = http, mensaje, errores or {}


def evento(s: Session, exp_id: int, tipo: str, actor: str, now: datetime, assignment_id: int | None = None,
           reservacion_id: int | None = None, change_event_id: int | None = None, **detalle) -> None:
    """Registra un evento del expediente. `detalle` nunca debe llevar datos personales."""
    s.add(ExpedienteEvento(expediente_id=exp_id, ts=now, tipo=tipo, actor=actor[:64], assignment_id=assignment_id,
                           reservacion_id=reservacion_id, change_event_id=change_event_id, detalle=detalle))


def _regiones(s: Session) -> set[str]:
    return set(s.scalars(select(Region.code)))


def _condiciones(exp: Expediente) -> dict:
    """Condiciones de la solicitud del motor. Solo las fuentes del portal publican recursos de
    tipo `cita`, así que no hace falta fijar la fuente; la sucursal va en el nombre del recurso."""
    return {"nombre_contiene": f"({exp.sucursal})"} if exp.sucursal else {}


def crear(s: Session, entrada: dict, actor: str, now: datetime) -> Expediente:
    r = val.validar(entrada, _regiones(s))
    if not r.ok:
        raise ErrorExpediente(422, "Datos incompletos o inválidos", r.errores)
    d = r.datos
    huella = val.huella_duplicado(d["nombre"], d["fecha_nacimiento"], d["tipo_tramite"])
    if s.scalar(select(Expediente.id).where(Expediente.dedup_activo == huella)):
        raise ErrorExpediente(409, "Ya existe un expediente activo para esa persona y ese trámite")
    if d.get("id_externo") and s.scalar(select(Expediente.id).where(Expediente.id_externo == d["id_externo"])):
        raise ErrorExpediente(409, "Ese identificador externo ya está registrado")
    # código provisional único y luego el definitivo a partir del id (sin carreras entre altas)
    exp = Expediente(codigo=f"TMP-{secrets.token_hex(8)}", estado=PENDIENTE, dedup_activo=huella, creado_por=actor[:64],
                     created_at=now, updated_at=now, **{k: v for k, v in d.items()})
    s.add(exp)
    s.flush()
    exp.codigo = f"EXP-{exp.id:06d}"
    req = Request(code=exp.codigo, holder=exp.codigo, region=exp.region, resource_type=TIPO_RECURSO,
                  priority=exp.prioridad, quantity=1, conditions=_condiciones(exp),
                  status="PENDIENTE", created_at=now, updated_at=now)
    s.add(req)
    s.flush()
    exp.request_id = req.id
    evento(s, exp.id, "CREADO", actor, now, solicitud=req.code, campos=sorted(d))
    return exp


def crear_seguro(db, entrada: dict, actor: str, now: datetime) -> dict:
    """Crea en su propia transacción; dos altas simultáneas del mismo titular: una gana y la
    otra recibe 409 (lo garantiza el UNIQUE de `dedup_activo`, no solo la comprobación previa)."""
    try:
        with db.session() as s:
            exp = crear(s, entrada, actor, now)
            return vista(s, exp, completa=True)
    except IntegrityError as e:
        raise ErrorExpediente(409, "Expediente duplicado (alta simultánea)") from e


def modificar(s: Session, exp: Expediente, entrada: dict, version: int, actor: str, now: datetime) -> list[str]:
    """Modificación con control de versión: si otro la cambió antes, 409 (no se pisa)."""
    if exp.estado not in (PENDIENTE, ASIGNADO, EN_CAPTURA):
        raise ErrorExpediente(409, f"No se puede modificar un expediente {exp.estado}")
    r = val.validar(entrada, _regiones(s), parcial=True)
    if not r.ok:
        raise ErrorExpediente(422, "Datos inválidos", r.errores)
    cambios = {k: v for k, v in r.datos.items() if getattr(exp, k) != v}
    if not cambios:
        return []
    nuevo = {c: cambios.get(c, getattr(exp, c)) for c in ("nombre", "fecha_nacimiento", "tipo_tramite")}
    huella = val.huella_duplicado(nuevo["nombre"], nuevo["fecha_nacimiento"], nuevo["tipo_tramite"])
    if huella != exp.dedup_activo and s.scalar(select(Expediente.id).where(Expediente.dedup_activo == huella)):
        raise ErrorExpediente(409, "Con esos datos ya existe otro expediente activo")
    t = Expediente.__table__
    hecho = s.execute(update(t).where(t.c.id == exp.id, t.c.version == version)
                      .values(**cambios, dedup_activo=huella, version=t.c.version + 1, updated_at=now)).rowcount
    if hecho != 1:
        raise ErrorExpediente(409, "El expediente cambió mientras lo editabas. Recárgalo.")
    s.expire(exp)
    if {"region", "sucursal", "prioridad"} & set(cambios) and exp.request_id:
        req = s.get(Request, exp.request_id)
        if req.status in ("PENDIENTE", "ELEGIBLE", "REINTENTO", "ERROR"):
            req.region, req.priority = exp.region, exp.prioridad
            req.conditions = _condiciones(exp)
    # historial: SOLO nombres de campos, nunca valores (minimización)
    evento(s, exp.id, "MODIFICADO", actor, now, campos=sorted(cambios), version=version + 1)
    return sorted(cambios)


def cerrar(s: Session, exp: Expediente, estado: str, actor: str, now: datetime, nota: str = "") -> None:
    exp.estado, exp.updated_at, exp.dedup_activo = estado, now, None   # libera la huella de duplicado
    exp.version += 1
    evento(s, exp.id, estado, actor, now, nota=nota)


# ---------------------------------------------------------------- permisos y vistas
def puede_ver(user, exp: Expediente) -> bool:
    """El administrador ve todo. Un operador solo ve los expedientes asignados a él."""
    return user.is_admin or (user.operator_id is not None and exp.operator_id == user.operator_id)


def consulta_visible(user):
    q = select(Expediente)
    if not user.is_admin:
        q = q.where(Expediente.operator_id == (user.operator_id if user.operator_id is not None else -1))
    return q


def vista(s: Session, exp: Expediente, completa: bool = False) -> dict:
    from ..api.serializers import iso
    op = s.get(Operator, exp.operator_id) if exp.operator_id else None
    base = {"id": exp.id, "codigo": exp.codigo, "estado": exp.estado, "grupo": GRUPO.get(exp.estado),
            "region": exp.region, "sucursal": exp.sucursal, "tipo_tramite": exp.tipo_tramite,
            "operacion": exp.operacion, "prioridad": exp.prioridad, "operador": op.name if op else None,
            "operator_id": exp.operator_id, "request_id": exp.request_id, "version": exp.version,
            "creado": iso(exp.created_at), "actualizado": iso(exp.updated_at), "ultimo_error": exp.ultimo_error}
    if completa:
        base.update(nombre=exp.nombre, fecha_nacimiento=exp.fecha_nacimiento.isoformat(), telefono=exp.telefono,
                    correo=exp.correo, id_registro_previo=exp.id_registro_previo, id_externo=exp.id_externo,
                    datos_completos=True)
    else:
        base.update(nombre=val.enmascarar_nombre(exp.nombre), fecha_nacimiento="••••-••-••",
                    telefono=val.enmascarar_telefono(exp.telefono), correo=val.enmascarar_correo(exp.correo),
                    id_registro_previo=("•••" + exp.id_registro_previo[-3:]) if exp.id_registro_previo else None,
                    id_externo=exp.id_externo, datos_completos=False)
    return base


def historial(s: Session, exp_id: int) -> list[dict]:
    from ..api.serializers import iso
    q = select(ExpedienteEvento).where(ExpedienteEvento.expediente_id == exp_id).order_by(
        ExpedienteEvento.ts, ExpedienteEvento.id)
    return [{"id": e.id, "ts": iso(e.ts), "tipo": e.tipo, "actor": e.actor, "asignacion": e.assignment_id,
             "reservacion": e.reservacion_id, "evento_cambio": e.change_event_id, "detalle": e.detalle,
             "normalizado": e.tipo in EVENTOS_NORMALIZADOS} for e in s.scalars(q)]

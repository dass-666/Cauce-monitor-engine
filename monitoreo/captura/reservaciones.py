"""Reservaciones de laboratorio (v1.5; POR DÍA en v1.6): del expediente al folio, con un operador
en cada paso. El motor NUNCA confirma solo.

POR_HORARIO (v1.5)                         POR_DIA (v1.6, sin hora)
  preparar       FORMULARIO_PREPARADO        preparar        igual (incluye campos MANUAL_REQUIRED)
  enviar_datos   pantalla 1 + calendario     enviar_datos    pantalla 1 con lo que TECLEÓ el operador
  seleccionar    horario apartado (RETENIDA) seleccionar_dia FECHA_ELEGIDA (el portal no aparta días)
  confirmar      CONFIRMADA con evidencia    confirmar       "Continuar" -> CONFIRMADA con evidencia

Estados de la reservación: PREPARADA -> DATOS_ENVIADOS -> (RETENIDA | FECHA_ELEGIDA) -> CONFIRMANDO
-> CONFIRMADA, o EN_VERIFICACION si el resultado es incierto (respuesta perdida, error del portal,
evidencia que no se pudo leer). EN_VERIFICACION se resuelve SOLO preguntando al portal por la clave.

Campos MANUAL_REQUIRED (p. ej. clave de identidad, aceptación del aviso): llegan en la petición
del operador, se validan con el contrato y se envían al portal en ese mismo momento; NUNCA se
guardan en la base, ni en el historial, ni en registros. Si la sesión del portal caduca, el
operador debe volver a teclearlos (es intencional).

Garantías:
- Un intento vivo por expediente (`viva_expediente_id` UNIQUE) y un único confirmador:
  RETENIDA -> CONFIRMANDO es un UPDATE condicional que se GUARDA antes de llamar al portal.
- CONFIRMADA exige leer de la API del portal una reservación CONFIRMADA con la misma clave.
  La redirección con folio no basta (podría ser de otra sesión o perderse).
- Respuesta perdida (corte, 5xx, tiempo agotado): el intento queda CONFIRMANDO y se resuelve
  preguntando al portal por la clave. La clave de confirmación es idempotente en el portal:
  repetir la confirmación nunca crea una segunda reservación.
- Ninguna llamada al portal se hace con una transacción de base de datos abierta.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import conexiones, proveedor
from ..db import Database
from ..engine import assignment as asg
from ..engine.params import get_params
from ..models import Assignment, Expediente, Reservacion
from . import expedientes as ex
from . import portal
from .formularios import contrato as ct
from .formularios.navegador import (DatosRechazados, ErrorFormulario, Rechazo, ResultadoIncierto,
                                    RevisionRequerida, SesionCaducada)

log = logging.getLogger("monitoreo.captura")

PREPARADA, DATOS_ENVIADOS, RETENIDA, CONFIRMANDO = "PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "CONFIRMANDO"
FECHA_ELEGIDA, EN_VERIFICACION = "FECHA_ELEGIDA", "EN_VERIFICACION"
CONFIRMADA, RECHAZADA, EXPIRADA, CANCELADA = "CONFIRMADA", "RECHAZADA", "EXPIRADA", "CANCELADA"
VIVAS = (PREPARADA, DATOS_ENVIADOS, RETENIDA, FECHA_ELEGIDA, CONFIRMANDO, EN_VERIFICACION)
LISTAS_PARA_CONFIRMAR = (RETENIDA, FECHA_ELEGIDA)
BLOQUEANTES = (RETENIDA, CONFIRMANDO, EN_VERIFICACION)    # hay (o puede haber) algo en el portal
GRUPO = {PREPARADA: "en_proceso", DATOS_ENVIADOS: "en_proceso", RETENIDA: "pendiente", FECHA_ELEGIDA: "pendiente",
         CONFIRMANDO: "pendiente", EN_VERIFICACION: "verificacion", CONFIRMADA: "confirmado", RECHAZADA: "fallido",
         EXPIRADA: "fallido", CANCELADA: "fallido"}
DIA_NO_DISPONIBLE = ("DIA_AGOTADO", "DIA_DESHABILITADO", "DIA_NO_HABILITADO")


def viva_bloquea(s, exp_id: int) -> bool:
    return s.scalar(select(Reservacion.id).where(Reservacion.viva_expediente_id == exp_id,
                                                 Reservacion.estado.in_(BLOQUEANTES))) is not None


class ErrorReservacion(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _naive(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _nav(db: Database, sesion: str | None = None, modo: str | None = None, operacion: str | None = None,
         conexion: str | None = None, ref: dict | None = None, timeout: float | None = None):
    """v2.0: navegador del portal por la conexión que decide el gestor de conexiones."""
    with db.session() as s:
        p = get_params(s)
    try:
        return portal.navegador(sesion=sesion, timeout=timeout or float(p["captura_timeout_portal_s"]), modo=modo,
                                operacion=operacion, conexion=conexion, params=p, ref=ref)
    except conexiones.SinConexion as e:
        raise ErrorReservacion(503, f"Sin conexión de red utilizable: {e}", {"tipo": "SIN_CONEXION"}) from e


def _cerrar(db: Database, nav) -> None:
    nav.cerrar()
    try:
        conexiones.volcar(db)
    except Exception:          # noqa: BLE001 - el registro de conexiones no debe tumbar la operación
        pass


def _timeout(db: Database) -> float:
    with db.session() as s:
        return float(get_params(s)["captura_timeout_portal_s"])


def _exp_visible(s, exp_id: int, user) -> Expediente:
    exp = s.get(Expediente, exp_id)
    if exp is None or not ex.puede_ver(user, exp):
        raise ErrorReservacion(404, "Expediente no encontrado")     # 404 también si no es suyo: no se revela
    return exp


def _res_visible(s, res_id: int, user) -> Reservacion:
    res = s.get(Reservacion, res_id)
    if res is None:
        raise ErrorReservacion(404, "Reservación no encontrada")
    exp = s.get(Expediente, res.expediente_id)
    if not (user.is_admin or (user.operator_id is not None and res.operator_id == user.operator_id
                              and exp.operator_id == user.operator_id)):
        raise ErrorReservacion(404, "Reservación no encontrada")
    return res


def _actor(user) -> str:
    return user.username[:64]


def _tocar(db: Database, aid: int | None, user, now: datetime) -> None:
    if aid:
        asg.touch_assignment(db, aid, None, now, _actor(user))


def vista(r: Reservacion) -> dict:
    from ..api.serializers import iso
    return {"id": r.id, "expediente_id": r.expediente_id, "estado": r.estado, "grupo": GRUPO.get(r.estado),
            "asignacion": r.assignment_id, "operator_id": r.operator_id, "horario": r.horario_id,
            "sucursal": r.sucursal, "fecha": r.fecha, "hora": r.hora, "folio": r.folio,
            "vence_en": iso(r.vence_en), "error_codigo": r.error_codigo, "error": r.error,
            "intentos_confirmacion": r.intentos_confirmacion, "confirmada_por": r.confirmada_por, "modo": r.modo,
            "evidencia": r.evidencia or {}, "creada": iso(r.created_at), "actualizada": iso(r.updated_at),
            "sesion_portal": "•••" if r.sesion_portal else None}


# ---------------------------------------------------------------- 1. preparar
def preparar(db: Database, exp_id: int, user, now: datetime) -> dict:
    """Abre (o retoma) el intento vivo del expediente y prepara los campos para revisión."""
    with db.session() as s:
        exp = _exp_visible(s, exp_id, user)
        if exp.estado not in (ex.ASIGNADO, ex.CAPTURA, ex.RES_PENDIENTE):
            raise ErrorReservacion(409, f"El expediente está {exp.estado}: no se puede preparar")
        if exp.operator_id is None:
            raise ErrorReservacion(409, "El expediente aún no tiene operador asignado")
        live = s.scalar(select(Assignment).where(Assignment.request_id == exp.request_id,
                                                 Assignment.status.in_(["ACTIVA", "EN_PROCESO"])))
        aid, astatus, op_id = (live.id, live.status, exp.operator_id) if live else (None, None, exp.operator_id)
    if aid and astatus == "ACTIVA":
        # El operador toma la asignación del motor (ASIGNADO -> EN_PROCESO); solo uno puede lograrlo.
        if not asg.take_assignment(db, aid, op_id, now, _actor(user), idempotent=True):
            raise ErrorReservacion(409, "La asignación ya no está disponible (expiró o la tomó otro)")
    _tocar(db, aid, user, now)
    with db.session() as s:
        modo = s.get(Expediente, exp_id).modo
    c = portal.contrato(modo)
    try:
        with db.session() as s:
            if not proveedor.es_compatible(s):
                # Una instancia con otro portal no crea trabajo nuevo: su recuperación quedaría a cargo de
                # un líder que pregunta a un portal que no lo conoce.
                raise ErrorReservacion(409, "Esta instancia apunta a un portal distinto del registrado en la base: "
                                            "no se preparan reservaciones nuevas desde aquí",
                                       {"codigo": "OTRO_PROVEEDOR"})
            exp = s.get(Expediente, exp_id)
            res = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
            if res is None:
                res = Reservacion(expediente_id=exp_id, viva_expediente_id=exp_id, assignment_id=aid,
                                  operator_id=exp.operator_id, estado=PREPARADA, contrato=c.nombre, modo=c.modo,
                                  proveedor=proveedor.identidad_local()["id"], created_at=now, updated_at=now)
                s.add(res)
                s.flush()
            elif res.operator_id != exp.operator_id and not user.is_admin:
                raise ErrorReservacion(409, "Otro operador tiene un intento abierto de este expediente")
            else:
                _exigir_proveedor(s, res)
            if exp.estado == ex.ASIGNADO:
                ex.cambiar_estado(s, exp, ex.CAPTURA, _actor(user), now)
            revision = ct.preparar(c, exp)
            revision["sucursales_permitidas"] = _sucursales_permitidas(s, exp, aid)
            ex.evento(s, exp_id, "FORMULARIO_PREPARADO", _actor(user), now, assignment_id=aid, reservacion_id=res.id,
                      contrato=c.nombre, version_contrato=c.version, faltan=revision["faltan"])
            return {"reservacion": vista(res), "revision": revision}
    except IntegrityError as e:   # dos operadores (o dos pestañas) abriendo a la vez
        raise ErrorReservacion(409, "Ya hay un intento abierto para este expediente") from e


# ---------------------------------------------------------------- 2. enviar datos y calendario
OTRO_PROVEEDOR = ("Esta reservación pertenece a otro proveedor (portal) distinto del configurado en esta "
                  "instancia; no se toca desde aquí")


def _exigir_proveedor(s, res: Reservacion) -> None:
    """v2.0: una instancia solo opera reservaciones creadas con SU portal (ver monitoreo/proveedor.py)."""
    if proveedor.de_reservacion(s, res) != proveedor.identidad_local()["id"]:
        raise ErrorReservacion(409, OTRO_PROVEEDOR, {"codigo": "OTRO_PROVEEDOR"})


def _datos_intento(db: Database, res_id: int, user, estados: tuple) -> tuple[dict, Expediente]:
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        _exigir_proveedor(s, res)
        if res.estado not in estados:
            raise ErrorReservacion(409, f"La reservación está {res.estado}")
        exp = s.get(Expediente, res.expediente_id)
        s.expunge(exp)
        return {"id": res.id, "sesion": res.sesion_portal, "estado": res.estado, "aid": res.assignment_id,
                "clave": res.clave, "vence_en": res.vence_en, "folio": res.folio, "modo": res.modo,
                "sucursal": res.sucursal, "fecha": res.fecha, "conexion": res.conexion,
                "permitidas": _sucursales_permitidas(s, exp, res.assignment_id)}, exp


def _filtros(exp: Expediente, region: str | None = None, sucursal: str | None = None) -> dict:
    return {"region": region or exp.region, "sucursal": sucursal if sucursal is not None else exp.sucursal}


def _sucursales_permitidas(s, exp: Expediente, aid: int | None) -> list[str]:
    """POR_DIA: la sucursal del recurso asignado primero, luego la preferida y las alternativas
    (lista vacía = cualquiera de la región). El operador no puede salirse de esta lista."""
    from ..models import SlotState
    out = []
    if aid:
        a = s.get(Assignment, aid)
        slot = s.get(SlotState, a.slot_id) if a else None
        if slot is not None and (slot.attributes or {}).get("sucursal"):
            out.append(slot.attributes["sucursal"])
    for x in [exp.sucursal, *(exp.sucursales_alternativas or [])]:
        if x and x not in out:
            out.append(x)
    return out


def _filtros_dia(exp: Expediente, permitidas: list[str], sucursal: str | None, mes: str | None) -> dict:
    if sucursal and permitidas and sucursal not in permitidas:
        raise ErrorReservacion(403, "Esa sucursal no está entre las permitidas para este expediente",
                               {"permitidas": permitidas})
    suc = sucursal or (permitidas[0] if permitidas else None)
    if not suc:
        raise ErrorReservacion(422, "Elige una sucursal de la región del expediente")
    f = {"region": exp.region, "sucursal": suc}
    if mes:
        f["mes"] = mes
    return f


def _calendario_con_libres(nav, filtros: dict, mes_pedido: str | None) -> dict:
    """v1.7: el portal muestra un MES. Si el operador no pidió un mes concreto y el mes en curso no
    tiene ningún día disponible (p. ej. a fin de mes), se muestra el siguiente con un aviso, en vez de
    un calendario vacío que obliga a buscar con «Mes siguiente». Solo un salto: no se recorre el año."""
    cal = nav.calendario_dia(filtros)
    if mes_pedido or any(x["estado"] == "DISPONIBLE" for x in cal.get("dias", [])) or not cal.get("mes"):
        return cal
    y, m = (int(x) for x in cal["mes"].split("-"))
    sig = f"{y + m // 12:04d}-{m % 12 + 1:02d}"
    cal2 = nav.calendario_dia({**filtros, "sucursal": cal.get("sucursal") or filtros.get("sucursal"), "mes": sig})
    if any(x["estado"] == "DISPONIBLE" for x in cal2.get("dias", [])):
        cal2["aviso"] = f"El mes {cal['mes']} no tiene días disponibles en esta sucursal: se muestra {sig}."
        return cal2
    return cal


def _guardar_error(db: Database, res_id: int, e: ErrorFormulario, now: datetime, estado: str | None = None) -> None:
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        res.error_codigo, res.error, res.updated_at = e.codigo[:32], e.mensaje[:500], now
        if estado:
            res.estado = estado
        s.get(Expediente, res.expediente_id).ultimo_error = f"{e.codigo}: {e.mensaje}"[:500]


def enviar_datos(db: Database, res_id: int, user, now: datetime, revisado: bool, manuales: dict | None = None,
                 sucursal: str | None = None, mes: str | None = None) -> dict:
    if not revisado:
        raise ErrorReservacion(422, "Revisa los campos preparados y marca 'revisado' antes de enviarlos")
    d, exp = _datos_intento(db, res_id, user, (PREPARADA, DATOS_ENVIADOS, FECHA_ELEGIDA))
    c = portal.contrato(d["modo"])
    auto = ct.valores_desde(c, exp)
    limpios, errores = ct.validar_manuales(c, manuales, auto)
    if errores:
        # No se llama al portal; el estado del formulario lo conserva quien lo envió (nada se guarda aquí).
        raise ErrorReservacion(422, "Revisa los campos de captura manual", {"tipo": "CAPTURA_MANUAL",
                                                                             "errores": errores})
    filtros = _filtros_dia(exp, d["permitidas"], sucursal, mes) if d["modo"] == "POR_DIA" else _filtros(exp)
    _tocar(db, d["aid"], user, now)
    nav = _nav(db, modo=d["modo"], ref={"reservacion_id": res_id})   # sesión nueva del portal en cada envío
    try:
        nav.enviar_datos({**auto, **limpios})
        del limpios, manuales                           # los manuales no sobreviven a esta llamada
        if d["modo"] == "POR_DIA":
            cal = _calendario_con_libres(nav, filtros, mes)
            nav.verificar_solo_lectura(cal, exp)
        else:
            cal = nav.calendario(filtros)
    except (RevisionRequerida, DatosRechazados) as e:
        _guardar_error(db, res_id, e, now, PREPARADA)
        raise ErrorReservacion(422, e.mensaje, e.como_dict()) from e
    except ErrorFormulario as e:
        _guardar_error(db, res_id, e, now)
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    finally:
        sesion, conexion_usada = nav.sesion, nav.conexion
        _cerrar(db, nav)
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if res.estado not in (PREPARADA, DATOS_ENVIADOS, FECHA_ELEGIDA):
            raise ErrorReservacion(409, f"La reservación cambió a {res.estado} mientras tanto")
        res.estado, res.sesion_portal, res.error_codigo, res.error, res.updated_at = DATOS_ENVIADOS, sesion, None, None, now
        res.conexion = conexion_usada                  # el resto de la sesión sigue por esta conexión
        if res.modo == "POR_DIA":
            res.clave, res.sucursal, res.fecha = None, cal.get("sucursal"), None
        s.get(Expediente, res.expediente_id).ultimo_error = None
        visibles = (sum(1 for x in cal["dias"] if x["estado"] == "DISPONIBLE") if res.modo == "POR_DIA"
                    else len(cal["horarios"]))
        ex.evento(s, res.expediente_id, "DATOS_ENVIADOS", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, disponibles_visibles=visibles,
                  manuales_capturados=sorted(m.nombre for m in c.manuales))   # solo NOMBRES, nunca valores
        return {"reservacion": vista(res), "calendario": cal, "permitidas": d["permitidas"]}


def calendario(db: Database, res_id: int, user, now: datetime, region: str | None = None,
               sucursal: str | None = None, mes: str | None = None) -> dict:
    d, exp = _datos_intento(db, res_id, user, (DATOS_ENVIADOS, RETENIDA, FECHA_ELEGIDA))
    nav = _nav(db, sesion=d["sesion"], modo=d["modo"], conexion=d["conexion"], ref={"reservacion_id": res_id})
    try:
        if d["modo"] == "POR_DIA":
            cal = _calendario_con_libres(nav, _filtros_dia(exp, d["permitidas"], sucursal, mes), mes)
            nav.verificar_solo_lectura(cal, exp)
            return {"calendario": cal, "permitidas": d["permitidas"]}
        return {"calendario": nav.calendario(_filtros(exp, region, sucursal))}
    except SesionCaducada as e:
        _guardar_error(db, res_id, e, now, PREPARADA if d["estado"] == DATOS_ENVIADOS else None)
        raise ErrorReservacion(409, "La sesión del portal caducó: vuelve a enviar los datos", e.como_dict()) from e
    except ErrorFormulario as e:
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    finally:
        _cerrar(db, nav)


# ---------------------------------------------------------------- 3. seleccionar horario
def seleccionar(db: Database, res_id: int, user, now: datetime, horario_id: str, region: str | None = None,
                sucursal: str | None = None) -> dict:
    if not horario_id or len(horario_id) > 64:
        raise ErrorReservacion(422, "Horario no válido")
    d, exp = _datos_intento(db, res_id, user, (DATOS_ENVIADOS, RETENIDA))
    _tocar(db, d["aid"], user, now)
    nav = _nav(db, sesion=d["sesion"], conexion=d["conexion"], ref={"reservacion_id": res_id})
    filtros = _filtros(exp, region, sucursal)
    try:
        conf = nav.seleccionar(horario_id, filtros)
    except Rechazo as e:
        _guardar_error(db, res_id, e, now)
        try:
            cal = nav.calendario(filtros)
        except ErrorFormulario:
            cal = None
        _cerrar(db, nav)
        with db.session() as s:
            ex.evento(s, exp.id, "HORARIO_NO_DISPONIBLE", _actor(user), now, assignment_id=d["aid"],
                      reservacion_id=res_id, horario=horario_id, codigo=e.codigo)
        raise ErrorReservacion(409, e.mensaje, {**e.como_dict(), "calendario": cal}) from e
    except SesionCaducada as e:
        _cerrar(db, nav)
        _guardar_error(db, res_id, e, now, PREPARADA)
        raise ErrorReservacion(409, "La sesión del portal caducó: vuelve a enviar los datos", e.como_dict()) from e
    except ErrorFormulario as e:
        _cerrar(db, nav)
        _guardar_error(db, res_id, e, now)
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    _cerrar(db, nav)
    resumen = conf["resumen"]
    try:
        vence = _naive(datetime.fromisoformat(resumen.get("vence", "").replace("Z", "+00:00")))
    except ValueError:
        vence = now + timedelta(minutes=5)
    with db.session() as s:
        t = Reservacion.__table__
        ok = s.execute(update(t).where(t.c.id == res_id, t.c.estado.in_([DATOS_ENVIADOS, RETENIDA])).values(
            estado=RETENIDA, horario_id=horario_id, sucursal="-".join(horario_id.split("-")[:2])[:32],
            fecha=resumen.get("fecha"), hora=resumen.get("hora"), clave=conf["clave"], vence_en=vence,
            error_codigo=None, error=None, updated_at=now)).rowcount
        if ok != 1:
            raise ErrorReservacion(409, "La reservación cambió mientras tanto")
        res = s.get(Reservacion, res_id)
        s.refresh(res)
        e_exp = s.get(Expediente, res.expediente_id)
        ex.cambiar_estado(s, e_exp, ex.RES_PENDIENTE, _actor(user), now)
        e_exp.ultimo_error = None
        ex.evento(s, e_exp.id, "RECURSO_SELECCIONADO", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, horario=horario_id, fecha=res.fecha, hora=res.hora)
        ex.evento(s, e_exp.id, "RESERVACION_PENDIENTE", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, vence_en=vence.isoformat() if vence else None)
        return {"reservacion": vista(res), "resumen": resumen}


# ---------------------------------------------------------------- 3b. elegir día (POR_DIA)
def seleccionar_dia(db: Database, res_id: int, user, now: datetime, fecha: str, sucursal: str | None = None) -> dict:
    """Comprueba en el portal (calendario recién leído) que el día sigue DISPONIBLE y lo deja
    elegido. El portal no aparta días: la unidad se gana o se pierde al confirmar."""
    try:
        date.fromisoformat(fecha or "")
    except ValueError:
        raise ErrorReservacion(422, "Fecha no válida (AAAA-MM-DD)") from None
    d, exp = _datos_intento(db, res_id, user, (DATOS_ENVIADOS, FECHA_ELEGIDA))
    if d["modo"] != "POR_DIA":
        raise ErrorReservacion(409, "Esta reservación es por horario: usa 'seleccionar'")
    suc = sucursal or d["sucursal"]
    filtros = _filtros_dia(exp, d["permitidas"], suc, fecha[:7])
    _tocar(db, d["aid"], user, now)
    nav = _nav(db, sesion=d["sesion"], modo="POR_DIA", conexion=d["conexion"], ref={"reservacion_id": res_id})
    try:
        cal = nav.calendario_dia(filtros)
        nav.verificar_solo_lectura(cal, exp)
    except SesionCaducada as e:
        _guardar_error(db, res_id, e, now, PREPARADA)
        raise ErrorReservacion(409, "La sesión del portal caducó: vuelve a enviar los datos", e.como_dict()) from e
    except ErrorFormulario as e:
        _guardar_error(db, res_id, e, now)
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    finally:
        _cerrar(db, nav)
    dia = next((x for x in cal["dias"] if x["fecha"] == fecha), None)
    if dia is None or dia["estado"] != "DISPONIBLE":
        with db.session() as s:
            ex.evento(s, exp.id, "DIA_NO_DISPONIBLE", _actor(user), now, assignment_id=d["aid"], reservacion_id=res_id,
                      fecha=fecha, sucursal=filtros["sucursal"], estado_portal=dia["estado"] if dia else "NO_HABILITADO")
        raise ErrorReservacion(409, "Ese día ya no está disponible", {"codigo": "DIA_AGOTADO", "calendario": cal})
    if not cal.get("clave"):
        raise ErrorReservacion(502, "El portal no entregó la clave de confirmación del calendario")
    with db.session() as s:
        t = Reservacion.__table__
        ok = s.execute(update(t).where(t.c.id == res_id, t.c.estado.in_([DATOS_ENVIADOS, FECHA_ELEGIDA])).values(
            estado=FECHA_ELEGIDA, sucursal=filtros["sucursal"], fecha=fecha, hora=None, horario_id=None,
            clave=cal["clave"], error_codigo=None, error=None, updated_at=now)).rowcount
        if ok != 1:
            raise ErrorReservacion(409, "La reservación cambió mientras tanto")
        res = s.get(Reservacion, res_id)
        s.refresh(res)
        e_exp = s.get(Expediente, res.expediente_id)
        ex.cambiar_estado(s, e_exp, ex.RES_PENDIENTE, _actor(user), now)
        e_exp.ultimo_error = None
        ex.evento(s, e_exp.id, "RECURSO_SELECCIONADO", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, sucursal=res.sucursal, fecha=fecha, libres_vistas=dia.get("libres"))
        ex.evento(s, e_exp.id, "RESERVACION_PENDIENTE", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, fecha=fecha)
        return {"reservacion": vista(res), "dia": dia, "calendario": cal}


# ---------------------------------------------------------------- 4. confirmar (con evidencia)
def _evidencia_valida(rp: dict | None, clave: str) -> bool:
    return bool(rp) and rp.get("estado") == CONFIRMADA and rp.get("clave") == clave and bool(rp.get("folio"))


def _finalizar(db: Database, res_id: int, rp: dict, fuente: str, actor: str, now: datetime) -> dict:
    """Marca CONFIRMADA (desde CONFIRMANDO, EN_VERIFICACION o lista para confirmar) con la evidencia
    leída del portal. Si el folio ya pertenece a OTRA reservación (folio duplicado del portal), NO
    se confirma: queda EN_VERIFICACION y el expediente pasa a REQUIERE_REVISION."""
    evid = {"folio": rp["folio"], "estado_portal": rp["estado"], "clave_coincide": True, "fuente": fuente,
            "horario_portal": rp.get("horario"), "dia_portal": rp.get("dia"), "confirmada_en_portal": rp.get("confirmada_en"),
            "verificada_en": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}
    desde = [CONFIRMANDO, EN_VERIFICACION, *LISTAS_PARA_CONFIRMAR]
    try:
        with db.session() as s:
            res = s.get(Reservacion, res_id)
            if res.estado == CONFIRMADA:
                return vista(res)
            t = Reservacion.__table__
            ok = s.execute(update(t).where(t.c.id == res_id, t.c.estado.in_(desde)).values(
                estado=CONFIRMADA, folio=rp["folio"], evidencia=evid, horario_id=rp.get("horario") or res.horario_id,
                fecha=rp.get("fecha") or res.fecha, hora=rp.get("hora") or res.hora,
                sucursal=rp.get("sucursal") or res.sucursal, error=None, error_codigo=None,
                confirmada_por=res.confirmada_por or actor, viva_expediente_id=res.expediente_id,
                updated_at=now)).rowcount
            if ok != 1:
                s.refresh(res)
                return vista(res)
            s.refresh(res)
            exp = s.get(Expediente, res.expediente_id)
            ex.evento(s, exp.id, "RESERVACION_CONFIRMADA", actor, now, assignment_id=res.assignment_id,
                      reservacion_id=res.id, folio=rp["folio"], evidencia=fuente)
            ex.confirmar(s, exp, actor, now, rp["folio"])
            aid, out = res.assignment_id, vista(res)
    except IntegrityError:
        return _folio_duplicado(db, res_id, rp, fuente, actor, now)
    if aid:   # fuera de la transacción anterior (no se anidan sesiones)
        if not asg.complete_assignment(db, aid, None, now, actor):
            with db.session() as s:
                a = s.get(Assignment, aid)
                ex.evento(s, out["expediente_id"], "ASIGNACION_NO_COMPLETADA", "motor", now, assignment_id=aid,
                          reservacion_id=res_id, estado_asignacion=a.status if a else None)
    return out


def _folio_duplicado(db: Database, res_id: int, rp: dict, fuente: str, actor: str, now: datetime) -> dict:
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        otra = s.scalar(select(Reservacion.id).where(Reservacion.folio == rp["folio"]))
        res.estado, res.error_codigo, res.updated_at = EN_VERIFICACION, "FOLIO_DUPLICADO", now
        res.error = (f"El portal entregó el folio {rp['folio']}, que ya pertenece a otra reservación (#{otra}). "
                     "No se confirma: un administrador debe revisarlo con el portal.")[:500]
        res.evidencia = {**(res.evidencia or {}), "folio_duplicado": rp["folio"], "otra_reservacion": otra,
                         "fuente": fuente}
        exp = s.get(Expediente, res.expediente_id)
        ex.evento(s, exp.id, "FOLIO_DUPLICADO", actor, now, reservacion_id=res.id, otra_reservacion=otra)
        ex.a_revision(s, exp, actor, now, "FOLIO_DUPLICADO")
        return vista(res)


def _cerrar_intento(db: Database, res_id: int, estado: str, codigo: str, mensaje: str, actor: str, now: datetime,
                    desde: tuple = (CONFIRMANDO, EN_VERIFICACION, RETENIDA)) -> dict | None:
    with db.session() as s:
        t = Reservacion.__table__
        ok = s.execute(update(t).where(t.c.id == res_id, t.c.estado.in_(list(desde))).values(
            estado=estado, error_codigo=codigo[:32], error=mensaje[:500], viva_expediente_id=None,
            updated_at=now)).rowcount
        res = s.get(Reservacion, res_id)
        s.refresh(res)
        if ok != 1:
            return None
        exp = s.get(Expediente, res.expediente_id)
        if exp.estado == ex.RES_PENDIENTE:
            ex.cambiar_estado(s, exp, ex.CAPTURA, actor, now, motivo=codigo)   # el operador puede intentar otro
        exp.ultimo_error, exp.updated_at = f"{codigo}: {mensaje}"[:500], now
        tipo = "RESERVACION_EXPIRADA" if estado == EXPIRADA else (
            "RESERVACION_RECHAZADA" if estado == RECHAZADA else "RESERVACION_DESCARTADA")
        ex.evento(s, exp.id, tipo, actor, now, assignment_id=res.assignment_id, reservacion_id=res.id, codigo=codigo)
        return vista(res)


def _regresar(db: Database, res_id: int, estado: str, codigo: str, mensaje: str, actor: str, now: datetime) -> dict:
    """POR_DIA: el día se agotó (DATOS_ENVIADOS, misma sesión) o la sesión caducó (PREPARADA)."""
    with db.session() as s:
        t = Reservacion.__table__
        s.execute(update(t).where(t.c.id == res_id, t.c.estado == CONFIRMANDO).values(
            estado=estado, error_codigo=codigo[:32], error=mensaje[:500], clave=None, fecha=None, updated_at=now))
        res = s.get(Reservacion, res_id)
        s.refresh(res)
        exp = s.get(Expediente, res.expediente_id)
        if exp.estado == ex.RES_PENDIENTE:
            ex.cambiar_estado(s, exp, ex.CAPTURA, actor, now, motivo=codigo)
        exp.ultimo_error = f"{codigo}: {mensaje}"[:500]
        ex.evento(s, exp.id, "DIA_NO_DISPONIBLE" if codigo in DIA_NO_DISPONIBLE else "RESERVACION_REINICIADA", actor,
                  now, assignment_id=res.assignment_id, reservacion_id=res.id, codigo=codigo)
        return vista(res)


def _a_verificacion(db: Database, res_id: int, actor: str, now: datetime, motivo: str, folio: str | None) -> dict:
    with db.session() as s:
        t = Reservacion.__table__
        s.execute(update(t).where(t.c.id == res_id, t.c.estado == CONFIRMANDO).values(
            estado=EN_VERIFICACION, updated_at=now, error_codigo=(motivo or "SIN_EVIDENCIA")[:32],
            error="Resultado incierto: el motor lo verificará con el portal por la clave de confirmación"))
        r = s.get(Reservacion, res_id)
        s.refresh(r)
        ex.evento(s, r.expediente_id, "CONFIRMACION_EN_VERIFICACION", actor, now, assignment_id=r.assignment_id,
                  reservacion_id=r.id, folio_redireccion=folio, motivo=motivo)
        return vista(r)


def confirmar(db: Database, res_id: int, user, now: datetime, confirmo: bool) -> tuple[int, dict]:
    """Devuelve (código HTTP, cuerpo). 200 CONFIRMADA; 202 EN_VERIFICACION (resultado incierto)."""
    if not confirmo:
        raise ErrorReservacion(422, "La confirmación exige que el operador la marque expresamente")
    actor = _actor(user)
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        if res.estado == CONFIRMADA:
            return 200, {"reservacion": vista(res), "repetida": True}
        _exigir_proveedor(s, res)
        t = Reservacion.__table__
        cond = [t.c.id == res_id, t.c.estado.in_(list(LISTAS_PARA_CONFIRMAR))]
        if not user.is_admin:
            cond.append(t.c.operator_id == user.operator_id)
        # Se GUARDA que se está confirmando ANTES de hablar con el portal: si el motor se cae a mitad,
        # la recuperación sabe que debe preguntar al portal por esta clave.
        ok = s.execute(update(t).where(*cond).values(estado=CONFIRMANDO, confirmando_desde=now,
                                                     intentos_confirmacion=t.c.intentos_confirmacion + 1,
                                                     confirmada_por=actor, updated_at=now)).rowcount
        if ok != 1:
            s.refresh(res)
            if res.estado in (CONFIRMANDO, EN_VERIFICACION):
                raise ErrorReservacion(409, "Esta reservación ya se está confirmando o verificando",
                                       {"estado": res.estado})
            raise ErrorReservacion(409, f"No se puede confirmar: la reservación está {res.estado}",
                                   {"estado": res.estado})
        clave, sesion, aid, modo = res.clave, res.sesion_portal, res.assignment_id, res.modo
        conexion = res.conexion
        sucursal, fecha = res.sucursal, res.fecha
    _tocar(db, aid, user, now)
    nav = _nav(db, sesion=sesion, modo=modo, operacion="confirmar", conexion=conexion, ref={"reservacion_id": res_id})
    try:
        folio = None
        try:
            folio = nav.continuar_dia(clave, sucursal, fecha) if modo == "POR_DIA" else nav.confirmar(clave)
        except ResultadoIncierto as e:
            with db.session() as s:
                r = s.get(Reservacion, res_id)
                r.error_codigo, r.error = e.codigo[:32], f"respuesta perdida: {e.mensaje}"[:500]
        except (Rechazo, SesionCaducada) as e:
            # ¿Ya estaba confirmada (p. ej. reintento tras perder la respuesta)? Se pregunta por la clave.
            try:
                rp = nav.buscar_por_clave(clave)
            except ResultadoIncierto:
                return 202, {"reservacion": _a_verificacion(db, res_id, actor, now, "SIN_VERIFICAR_RECHAZO", None),
                             "aviso": "El portal rechazó la confirmación pero no se pudo comprobar por la clave."}
            if _evidencia_valida(rp, clave):
                return 200, {"reservacion": _finalizar(db, res_id, rp, "api_por_clave", actor, now)}
            if modo == "POR_DIA" and (e.codigo in DIA_NO_DISPONIBLE or isinstance(e, SesionCaducada)):
                destino = PREPARADA if isinstance(e, SesionCaducada) else DATOS_ENVIADOS
                v = _regresar(db, res_id, destino, e.codigo, e.mensaje, actor, now)
                cal = None
                if destino == DATOS_ENVIADOS:
                    try:
                        with db.session() as s:
                            exp = s.get(Expediente, v["expediente_id"])
                            s.expunge(exp)
                        cal = nav.calendario_dia({"region": exp.region, "sucursal": sucursal, "mes": (fecha or "")[:7]})
                    except ErrorFormulario:
                        cal = None
                raise ErrorReservacion(409, e.mensaje, {"codigo": e.codigo, "reservacion": v, "calendario": cal,
                                                        "tipo": "SESION" if destino == PREPARADA else "DIA"}) from e
            estado = EXPIRADA if e.codigo in ("RETENCION_VENCIDA", "SESION_CADUCADA", "SESION_INEXISTENTE") else RECHAZADA
            v = _cerrar_intento(db, res_id, estado, e.codigo, e.mensaje, actor, now, desde=(CONFIRMANDO,))
            if estado == RECHAZADA and modo == "POR_DIA":
                with db.session() as s:   # p. ej. cita duplicada o folio anterior inválido: lo revisa una persona
                    ex.a_revision(s, s.get(Expediente, v["expediente_id"]), actor, now, e.codigo)
            raise ErrorReservacion(409, e.mensaje, {"codigo": e.codigo, "reservacion": v}) from e
        # Evidencia: lo que el portal dice por su API (por folio si lo hubo, si no por clave).
        try:
            rp = nav.verificar_folio(folio) if folio else nav.buscar_por_clave(clave)
            fuente = "api_por_folio" if folio else "api_por_clave"
            if folio and not _evidencia_valida(rp, clave):
                rp, fuente = nav.buscar_por_clave(clave), "api_por_clave"
        except ResultadoIncierto:
            rp, fuente = None, ""
        if _evidencia_valida(rp, clave):
            out = _finalizar(db, res_id, rp, fuente, actor, now)
            if out["estado"] == CONFIRMADA:
                return 200, {"reservacion": out}
            return 202, {"reservacion": out, "aviso": out.get("error")}
        return 202, {"reservacion": _a_verificacion(db, res_id, actor, now, "SIN_EVIDENCIA", folio),
                     "aviso": "No se pudo comprobar el resultado con el portal. El motor lo verificará por la "
                              "clave de confirmación; no repitas la reservación por otro medio."}
    finally:
        _cerrar(db, nav)


# ---------------------------------------------------------------- cancelación
def cancelar(db: Database, res_id: int, user, now: datetime) -> dict:
    actor = _actor(user)
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        estado, folio, sesion, modo = res.estado, res.folio, res.sesion_portal, res.modo
        if estado == CONFIRMADA:
            _exigir_proveedor(s, res)          # cancelar en el portal: solo desde la instancia de su proveedor
    if estado in (PREPARADA, DATOS_ENVIADOS, RETENIDA, FECHA_ELEGIDA):
        # Descartar un intento sin confirmar: el apartado del portal (si lo hay) vence solo.
        v = _cerrar_intento(db, res_id, CANCELADA, "DESCARTADO", "intento descartado por el operador", actor, now,
                            desde=(PREPARADA, DATOS_ENVIADOS, RETENIDA, FECHA_ELEGIDA))
        if v is None:
            raise ErrorReservacion(409, "La reservación cambió mientras tanto")
        return v
    if estado != CONFIRMADA:
        raise ErrorReservacion(409, f"No se puede cancelar una reservación {estado}")
    nav = _nav(db, modo=modo, operacion="nueva_sesion", ref={"reservacion_id": res_id})      # cancelación: petición nueva a la API
    try:
        rp = nav.cancelar(folio)
    except ErrorFormulario as e:
        raise ErrorReservacion(502, f"El portal no confirmó la cancelación: {e.mensaje}") from e
    finally:
        _cerrar(db, nav)
    if rp.get("estado") != "CANCELADA":
        raise ErrorReservacion(502, "El portal no confirmó la cancelación")
    with db.session() as s:
        t = Reservacion.__table__
        res = s.get(Reservacion, res_id)
        s.execute(update(t).where(t.c.id == res_id, t.c.estado == CONFIRMADA).values(
            estado=CANCELADA, viva_expediente_id=None, updated_at=now,
            evidencia={**(res.evidencia or {}), "cancelada_en_portal": now.isoformat() + "Z"}))
        s.refresh(res)
        exp = s.get(Expediente, res.expediente_id)
        ex.evento(s, exp.id, "RESERVACION_CANCELADA", actor, now, reservacion_id=res.id, folio=folio)
        if exp.estado != ex.CERRADO:
            ex.cerrar(s, exp, "CANCELADO", actor, now, nota="reservación cancelada en el portal")
        return vista(res)


# ---------------------------------------------------------------- recuperación (la ejecuta el motor)
def recuperar(db: Database, now: datetime) -> dict:
    """Resuelve intentos que quedaron a medias: CONFIRMANDO (motor caído a mitad), EN_VERIFICACION
    (respuesta perdida) y RETENIDA vencidos. Siempre pregunta primero al portal por la clave; si el
    portal no responde, el intento SIGUE en verificación (nunca se da por confirmado ni por perdido)."""
    with db.session() as s:
        gracia = float(get_params(s)["captura_gracia_confirmando_s"])
        timeout = min(5.0, float(get_params(s)["captura_timeout_portal_s"]))
        local = proveedor.identidad_local()["id"]
        base = proveedor.asegurar_base(s)["id"]
        todas = [(r.id, r.estado, r.clave, r.confirmando_desde, r.vence_en, r.modo, r.proveedor or base) for r in s.scalars(
            select(Reservacion).where((Reservacion.estado.in_([CONFIRMANDO, EN_VERIFICACION])
                                       & ((Reservacion.error_codigo.is_(None))
                                          | (Reservacion.error_codigo != "FOLIO_DUPLICADO"))) |
                                      ((Reservacion.estado == RETENIDA) & (Reservacion.vence_en <= now))))]
    # v2.0: solo se recupera lo creado con ESTE portal. Preguntar a otro portal por una clave que no
    # conoce reabriría (o expiraría) algo que el portal correcto sí confirmó.
    pend = [r[:6] for r in todas if r[6] == local]
    ajenas = [r[0] for r in todas if r[6] != local]
    out = {"confirmadas": 0, "reabiertas": 0, "expiradas": 0, "sin_portal": 0, "en_verificacion": 0,
           "bloqueadas_otro_proveedor": len(ajenas)}
    if ajenas:
        log.warning("recuperación: %d reservación(es) de otro proveedor (portal) no se tocan desde esta instancia: %s",
                    len(ajenas), ajenas[:20])
    if not pend:
        return out
    navs: dict[str, object] = {}
    try:
        for rid, estado, clave, desde, vence, modo in pend:
            try:
                nav = navs.get(modo) or navs.setdefault(modo, _nav(db, modo=modo, operacion="verificacion",
                                                                   timeout=timeout))
            except ErrorReservacion:
                out["sin_portal"] += 1                  # sin conexión utilizable: se reintenta en la siguiente pasada
                break
            try:
                rp = nav.buscar_por_clave(clave) if clave else None
            except ResultadoIncierto:
                out["sin_portal"] += 1
                break                               # el portal no responde: se reintenta en la siguiente pasada
            log.info("recuperación: reservación %s (%s, %.1f s): el portal %s", rid, estado,
                     (now - desde).total_seconds() if desde else -1,
                     "la tiene confirmada" if _evidencia_valida(rp, clave) else
                     ("la tiene en estado " + str(rp.get("estado"))) if rp else "no tiene esa clave")
            if _evidencia_valida(rp, clave):
                v = _finalizar(db, rid, rp, "recuperacion_por_clave", "motor", now)
                out["confirmadas" if v["estado"] == CONFIRMADA else "en_verificacion"] += 1
            elif estado in (CONFIRMANDO, EN_VERIFICACION) and desde and (now - desde).total_seconds() >= gracia:
                # El portal responde y NO tiene una reservación con esa clave: es seguro reabrir.
                if modo == "POR_DIA" or (vence and vence > now):
                    destino = FECHA_ELEGIDA if modo == "POR_DIA" else RETENIDA
                    with db.session() as s:
                        t = Reservacion.__table__
                        if s.execute(update(t).where(t.c.id == rid, t.c.estado.in_([CONFIRMANDO, EN_VERIFICACION]))
                                     .values(estado=destino, updated_at=now, error_codigo=None,
                                             error="El portal no registró la confirmación: puedes confirmarla de nuevo"
                                             )).rowcount:
                            r = s.get(Reservacion, rid)
                            ex.evento(s, r.expediente_id, "CONFIRMACION_REABIERTA", "motor", now,
                                      reservacion_id=rid, assignment_id=r.assignment_id,
                                      segundos_desde_confirmacion=round((now - desde).total_seconds(), 1))
                            log.warning("recuperación: reservación %s reabierta (el portal no tiene su clave; "
                                        "%.1f s desde que empezó a confirmarse)", rid, (now - desde).total_seconds())
                            out["reabiertas"] += 1
                elif _cerrar_intento(db, rid, EXPIRADA, "RETENCION_VENCIDA",
                                     "El apartado venció sin confirmación registrada en el portal", "motor", now):
                    out["expiradas"] += 1
            elif estado == RETENIDA:
                if _cerrar_intento(db, rid, EXPIRADA, "RETENCION_VENCIDA", "El apartado del horario venció",
                                   "motor", now, desde=(RETENIDA,)):
                    out["expiradas"] += 1
            else:
                out["en_verificacion"] += 1
    finally:
        for nav in navs.values():
            _cerrar(db, nav)
    return out

"""Reservaciones de laboratorio (v1.5): del expediente al folio, con un operador en cada paso.

Flujo (cada paso lo pide un operador desde el panel; el motor nunca confirma solo):
  preparar        -> FORMULARIO_PREPARADO   (revisión humana de los campos)
  enviar_datos    -> pantalla 1 del portal y calendario (requiere `revisado=True`)
  seleccionar     -> RECURSO_SELECCIONADO + RESERVACION_PENDIENTE (horario apartado en el portal)
  confirmar       -> RESERVACION_CONFIRMADA solo con EVIDENCIA (requiere `confirmo=True`)

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

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..engine import assignment as asg
from ..engine.params import get_params
from ..models import Assignment, Expediente, Reservacion
from . import expedientes as ex
from . import portal
from .formularios import contrato as ct
from .formularios.navegador import (DatosRechazados, ErrorFormulario, Rechazo, ResultadoIncierto,
                                    RevisionRequerida, SesionCaducada)

PREPARADA, DATOS_ENVIADOS, RETENIDA, CONFIRMANDO = "PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "CONFIRMANDO"
CONFIRMADA, RECHAZADA, EXPIRADA, CANCELADA = "CONFIRMADA", "RECHAZADA", "EXPIRADA", "CANCELADA"
VIVAS = (PREPARADA, DATOS_ENVIADOS, RETENIDA, CONFIRMANDO)
GRUPO = {PREPARADA: "en_proceso", DATOS_ENVIADOS: "en_proceso", RETENIDA: "pendiente", CONFIRMANDO: "pendiente",
         CONFIRMADA: "confirmado", RECHAZADA: "fallido", EXPIRADA: "fallido", CANCELADA: "fallido"}


class ErrorReservacion(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _naive(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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
            "intentos_confirmacion": r.intentos_confirmacion, "confirmada_por": r.confirmada_por,
            "evidencia": r.evidencia or {}, "creada": iso(r.created_at), "actualizada": iso(r.updated_at),
            "sesion_portal": "•••" if r.sesion_portal else None}


# ---------------------------------------------------------------- 1. preparar
def preparar(db: Database, exp_id: int, user, now: datetime) -> dict:
    """Abre (o retoma) el intento vivo del expediente y prepara los campos para revisión."""
    with db.session() as s:
        exp = _exp_visible(s, exp_id, user)
        if exp.estado not in (ex.ASIGNADO, ex.EN_CAPTURA, ex.RESERVANDO):
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
    c = portal.contrato()
    try:
        with db.session() as s:
            exp = s.get(Expediente, exp_id)
            res = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
            if res is None:
                res = Reservacion(expediente_id=exp_id, viva_expediente_id=exp_id, assignment_id=aid,
                                  operator_id=exp.operator_id, estado=PREPARADA, contrato=c.nombre,
                                  created_at=now, updated_at=now)
                s.add(res)
                s.flush()
            elif res.operator_id != exp.operator_id and not user.is_admin:
                raise ErrorReservacion(409, "Otro operador tiene un intento abierto de este expediente")
            if exp.estado == ex.ASIGNADO:
                exp.estado, exp.updated_at = ex.EN_CAPTURA, now
            revision = ct.preparar(c, exp)
            ex.evento(s, exp_id, "FORMULARIO_PREPARADO", _actor(user), now, assignment_id=aid, reservacion_id=res.id,
                      contrato=c.nombre, version_contrato=c.version, faltan=revision["faltan"])
            return {"reservacion": vista(res), "revision": revision}
    except IntegrityError as e:   # dos operadores (o dos pestañas) abriendo a la vez
        raise ErrorReservacion(409, "Ya hay un intento abierto para este expediente") from e


# ---------------------------------------------------------------- 2. enviar datos y calendario
def _datos_intento(db: Database, res_id: int, user, estados: tuple) -> tuple[dict, Expediente]:
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        if res.estado not in estados:
            raise ErrorReservacion(409, f"La reservación está {res.estado}")
        exp = s.get(Expediente, res.expediente_id)
        s.expunge(exp)
        return {"id": res.id, "sesion": res.sesion_portal, "estado": res.estado, "aid": res.assignment_id,
                "clave": res.clave, "vence_en": res.vence_en, "folio": res.folio}, exp


def _filtros(exp: Expediente, region: str | None = None, sucursal: str | None = None) -> dict:
    return {"region": region or exp.region, "sucursal": sucursal if sucursal is not None else exp.sucursal}


def _guardar_error(db: Database, res_id: int, e: ErrorFormulario, now: datetime, estado: str | None = None) -> None:
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        res.error_codigo, res.error, res.updated_at = e.codigo[:32], e.mensaje[:500], now
        if estado:
            res.estado = estado
        s.get(Expediente, res.expediente_id).ultimo_error = f"{e.codigo}: {e.mensaje}"[:500]


def enviar_datos(db: Database, res_id: int, user, now: datetime, revisado: bool) -> dict:
    if not revisado:
        raise ErrorReservacion(422, "Revisa los campos preparados y marca 'revisado' antes de enviarlos")
    d, exp = _datos_intento(db, res_id, user, (PREPARADA, DATOS_ENVIADOS))
    _tocar(db, d["aid"], user, now)
    c = portal.contrato()
    nav = portal.navegador(timeout=_timeout(db))       # sesión nueva del portal en cada envío de datos
    try:
        nav.enviar_datos(ct.valores_desde(c, exp))
        cal = nav.calendario(_filtros(exp))
    except (RevisionRequerida, DatosRechazados) as e:
        _guardar_error(db, res_id, e, now, PREPARADA)
        raise ErrorReservacion(422, e.mensaje, e.como_dict()) from e
    except ErrorFormulario as e:
        _guardar_error(db, res_id, e, now)
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    finally:
        sesion = nav.sesion
        nav.cerrar()
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if res.estado not in (PREPARADA, DATOS_ENVIADOS):
            raise ErrorReservacion(409, f"La reservación cambió a {res.estado} mientras tanto")
        res.estado, res.sesion_portal, res.error_codigo, res.error, res.updated_at = DATOS_ENVIADOS, sesion, None, None, now
        s.get(Expediente, res.expediente_id).ultimo_error = None
        ex.evento(s, res.expediente_id, "DATOS_ENVIADOS", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, horarios_visibles=len(cal["horarios"]))
        return {"reservacion": vista(res), "calendario": cal}


def calendario(db: Database, res_id: int, user, now: datetime, region: str | None = None,
               sucursal: str | None = None) -> dict:
    d, exp = _datos_intento(db, res_id, user, (DATOS_ENVIADOS, RETENIDA))
    nav = portal.navegador(sesion=d["sesion"], timeout=_timeout(db))
    try:
        return {"calendario": nav.calendario(_filtros(exp, region, sucursal))}
    except SesionCaducada as e:
        _guardar_error(db, res_id, e, now, PREPARADA if d["estado"] == DATOS_ENVIADOS else None)
        raise ErrorReservacion(409, "La sesión del portal caducó: vuelve a enviar los datos", e.como_dict()) from e
    except ErrorFormulario as e:
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    finally:
        nav.cerrar()


# ---------------------------------------------------------------- 3. seleccionar horario
def seleccionar(db: Database, res_id: int, user, now: datetime, horario_id: str, region: str | None = None,
                sucursal: str | None = None) -> dict:
    if not horario_id or len(horario_id) > 64:
        raise ErrorReservacion(422, "Horario no válido")
    d, exp = _datos_intento(db, res_id, user, (DATOS_ENVIADOS, RETENIDA))
    _tocar(db, d["aid"], user, now)
    nav = portal.navegador(sesion=d["sesion"], timeout=_timeout(db))
    filtros = _filtros(exp, region, sucursal)
    try:
        conf = nav.seleccionar(horario_id, filtros)
    except Rechazo as e:
        _guardar_error(db, res_id, e, now)
        try:
            cal = nav.calendario(filtros)
        except ErrorFormulario:
            cal = None
        nav.cerrar()
        with db.session() as s:
            ex.evento(s, exp.id, "HORARIO_NO_DISPONIBLE", _actor(user), now, assignment_id=d["aid"],
                      reservacion_id=res_id, horario=horario_id, codigo=e.codigo)
        raise ErrorReservacion(409, e.mensaje, {**e.como_dict(), "calendario": cal}) from e
    except SesionCaducada as e:
        nav.cerrar()
        _guardar_error(db, res_id, e, now, PREPARADA)
        raise ErrorReservacion(409, "La sesión del portal caducó: vuelve a enviar los datos", e.como_dict()) from e
    except ErrorFormulario as e:
        nav.cerrar()
        _guardar_error(db, res_id, e, now)
        raise ErrorReservacion(502 if isinstance(e, ResultadoIncierto) else 409, e.mensaje, e.como_dict()) from e
    nav.cerrar()
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
        e_exp.estado, e_exp.ultimo_error, e_exp.updated_at = ex.RESERVANDO, None, now
        ex.evento(s, e_exp.id, "RECURSO_SELECCIONADO", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, horario=horario_id, fecha=res.fecha, hora=res.hora)
        ex.evento(s, e_exp.id, "RESERVACION_PENDIENTE", _actor(user), now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, vence_en=vence.isoformat() if vence else None)
        return {"reservacion": vista(res), "resumen": resumen}


# ---------------------------------------------------------------- 4. confirmar (con evidencia)
def _evidencia_valida(rp: dict | None, clave: str) -> bool:
    return bool(rp) and rp.get("estado") == CONFIRMADA and rp.get("clave") == clave and bool(rp.get("folio"))


def _finalizar(db: Database, res_id: int, rp: dict, fuente: str, actor: str, now: datetime) -> dict:
    """Marca CONFIRMADA (solo desde CONFIRMANDO o RETENIDA) con la evidencia leída del portal."""
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if res.estado == CONFIRMADA:
            return vista(res)
        t = Reservacion.__table__
        evid = {"folio": rp["folio"], "estado_portal": rp["estado"], "clave_coincide": True, "fuente": fuente,
                "horario_portal": rp.get("horario"), "confirmada_en_portal": rp.get("confirmada_en"),
                "verificada_en": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}
        ok = s.execute(update(t).where(t.c.id == res_id, t.c.estado.in_([CONFIRMANDO, RETENIDA])).values(
            estado=CONFIRMADA, folio=rp["folio"], evidencia=evid, horario_id=rp.get("horario") or res.horario_id,
            fecha=rp.get("fecha") or res.fecha, hora=rp.get("hora") or res.hora, error=None, error_codigo=None,
            confirmada_por=res.confirmada_por or actor, viva_expediente_id=res.expediente_id, updated_at=now)).rowcount
        if ok != 1:
            s.refresh(res)
            return vista(res)
        s.refresh(res)
        exp = s.get(Expediente, res.expediente_id)
        ex.evento(s, exp.id, "RESERVACION_CONFIRMADA", actor, now, assignment_id=res.assignment_id,
                  reservacion_id=res.id, folio=rp["folio"], evidencia=fuente)
        ex.cerrar(s, exp, ex.CONFIRMADO, actor, now, nota=f"folio {rp['folio']}")
        exp.ultimo_error = None
        aid, out = res.assignment_id, vista(res)
    if aid:   # fuera de la transacción anterior (no se anidan sesiones)
        if not asg.complete_assignment(db, aid, None, now, actor):
            with db.session() as s:
                a = s.get(Assignment, aid)
                ex.evento(s, out["expediente_id"], "ASIGNACION_NO_COMPLETADA", "motor", now, assignment_id=aid,
                          reservacion_id=res_id, estado_asignacion=a.status if a else None)
    return out


def _cerrar_intento(db: Database, res_id: int, estado: str, codigo: str, mensaje: str, actor: str, now: datetime,
                    desde: tuple = (CONFIRMANDO, RETENIDA)) -> dict | None:
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
        if exp.estado in (ex.RESERVANDO, ex.EN_CAPTURA):
            exp.estado = ex.EN_CAPTURA          # el operador puede intentar otro horario
        exp.ultimo_error, exp.updated_at = f"{codigo}: {mensaje}"[:500], now
        tipo = "RESERVACION_EXPIRADA" if estado == EXPIRADA else (
            "RESERVACION_RECHAZADA" if estado == RECHAZADA else "RESERVACION_DESCARTADA")
        ex.evento(s, exp.id, tipo, actor, now, assignment_id=res.assignment_id, reservacion_id=res.id, codigo=codigo)
        return vista(res)


def confirmar(db: Database, res_id: int, user, now: datetime, confirmo: bool) -> tuple[int, dict]:
    """Devuelve (código HTTP, cuerpo). 200 CONFIRMADA; 202 CONFIRMANDO (resultado en verificación)."""
    if not confirmo:
        raise ErrorReservacion(422, "La confirmación exige que el operador la marque expresamente")
    actor = _actor(user)
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        if res.estado == CONFIRMADA:
            return 200, {"reservacion": vista(res), "repetida": True}
        t = Reservacion.__table__
        cond = [t.c.id == res_id, t.c.estado == RETENIDA]
        if not user.is_admin:
            cond.append(t.c.operator_id == user.operator_id)
        # Se GUARDA que se está confirmando ANTES de hablar con el portal: si el motor se cae a mitad,
        # la recuperación sabe que debe preguntar al portal por esta clave.
        ok = s.execute(update(t).where(*cond).values(estado=CONFIRMANDO, confirmando_desde=now,
                                                     intentos_confirmacion=t.c.intentos_confirmacion + 1,
                                                     confirmada_por=actor, updated_at=now)).rowcount
        if ok != 1:
            s.refresh(res)
            if res.estado == CONFIRMANDO:
                raise ErrorReservacion(409, "Esta reservación ya se está confirmando (otro operador o pestaña)",
                                       {"estado": res.estado})
            raise ErrorReservacion(409, f"No se puede confirmar: la reservación está {res.estado}",
                                   {"estado": res.estado})
        clave, sesion, aid = res.clave, res.sesion_portal, res.assignment_id
    _tocar(db, aid, user, now)
    nav = portal.navegador(sesion=sesion, timeout=_timeout(db))
    try:
        folio = None
        try:
            folio = nav.confirmar(clave)
        except ResultadoIncierto as e:
            with db.session() as s:
                r = s.get(Reservacion, res_id)
                r.error_codigo, r.error = e.codigo[:32], f"respuesta perdida: {e.mensaje}"[:500]
        except (Rechazo, SesionCaducada) as e:
            # ¿Ya estaba confirmada (p. ej. reintento tras perder la respuesta)? Se pregunta por la clave.
            try:
                rp = nav.buscar_por_clave(clave)
            except ResultadoIncierto:
                rp = None
            if _evidencia_valida(rp, clave):
                return 200, {"reservacion": _finalizar(db, res_id, rp, "api_por_clave", actor, now)}
            estado = EXPIRADA if e.codigo in ("RETENCION_VENCIDA", "SESION_CADUCADA", "SESION_INEXISTENTE") else RECHAZADA
            v = _cerrar_intento(db, res_id, estado, e.codigo, e.mensaje, actor, now, desde=(CONFIRMANDO,))
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
            return 200, {"reservacion": _finalizar(db, res_id, rp, fuente, actor, now)}
        with db.session() as s:
            r = s.get(Reservacion, res_id)
            ex.evento(s, r.expediente_id, "CONFIRMACION_EN_VERIFICACION", actor, now, assignment_id=r.assignment_id,
                      reservacion_id=r.id, folio_redireccion=folio)
            return 202, {"reservacion": vista(r),
                         "aviso": "No se pudo comprobar el resultado con el portal. El motor lo verificará por la "
                                  "clave de confirmación; no repitas la reservación por otro medio."}
    finally:
        nav.cerrar()


# ---------------------------------------------------------------- cancelación
def cancelar(db: Database, res_id: int, user, now: datetime) -> dict:
    actor = _actor(user)
    with db.session() as s:
        res = _res_visible(s, res_id, user)
        estado, folio, sesion = res.estado, res.folio, res.sesion_portal
    if estado in (PREPARADA, DATOS_ENVIADOS, RETENIDA):
        # Descartar un intento sin confirmar: el apartado del portal vence solo.
        v = _cerrar_intento(db, res_id, CANCELADA, "DESCARTADO", "intento descartado por el operador", actor, now,
                            desde=(PREPARADA, DATOS_ENVIADOS, RETENIDA))
        if v is None:
            raise ErrorReservacion(409, "La reservación cambió mientras tanto")
        return v
    if estado != CONFIRMADA:
        raise ErrorReservacion(409, f"No se puede cancelar una reservación {estado}")
    nav = portal.navegador(sesion=sesion, timeout=_timeout(db))
    try:
        rp = nav.cancelar(folio)
    except ErrorFormulario as e:
        raise ErrorReservacion(502, f"El portal no confirmó la cancelación: {e.mensaje}") from e
    finally:
        nav.cerrar()
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
        exp.estado, exp.updated_at = ex.CANCELADO, now
        ex.evento(s, exp.id, "RESERVACION_CANCELADA", actor, now, reservacion_id=res.id, folio=folio)
        return vista(res)


# ---------------------------------------------------------------- recuperación (la ejecuta el motor)
def recuperar(db: Database, now: datetime) -> dict:
    """Resuelve intentos que quedaron a medias: CONFIRMANDO (respuesta perdida o motor caído a
    mitad) y RETENIDA vencidos. Siempre pregunta primero al portal por la clave."""
    with db.session() as s:
        gracia = float(get_params(s)["captura_gracia_confirmando_s"])
        timeout = min(5.0, float(get_params(s)["captura_timeout_portal_s"]))
        pend = [(r.id, r.estado, r.clave, r.confirmando_desde, r.vence_en) for r in s.scalars(
            select(Reservacion).where((Reservacion.estado == CONFIRMANDO) |
                                      ((Reservacion.estado == RETENIDA) & (Reservacion.vence_en <= now))))]
    out = {"confirmadas": 0, "reabiertas": 0, "expiradas": 0, "sin_portal": 0}
    if not pend:
        return out
    nav = portal.navegador(timeout=timeout)
    try:
        for rid, estado, clave, desde, vence in pend:
            try:
                rp = nav.buscar_por_clave(clave) if clave else None
            except ResultadoIncierto:
                out["sin_portal"] += 1
                break                               # el portal no responde: se reintenta en la siguiente pasada
            if _evidencia_valida(rp, clave):
                _finalizar(db, rid, rp, "recuperacion_por_clave", "motor", now)
                out["confirmadas"] += 1
            elif estado == CONFIRMANDO and desde and (now - desde).total_seconds() >= gracia:
                if vence and vence > now:
                    with db.session() as s:
                        t = Reservacion.__table__
                        if s.execute(update(t).where(t.c.id == rid, t.c.estado == CONFIRMANDO).values(
                                estado=RETENIDA, updated_at=now,
                                error="El portal no registró la confirmación: puedes confirmarla de nuevo")).rowcount:
                            r = s.get(Reservacion, rid)
                            ex.evento(s, r.expediente_id, "CONFIRMACION_REABIERTA", "motor", now,
                                      reservacion_id=rid, assignment_id=r.assignment_id)
                            out["reabiertas"] += 1
                elif _cerrar_intento(db, rid, EXPIRADA, "RETENCION_VENCIDA",
                                     "El apartado venció sin confirmación registrada en el portal", "motor", now):
                    out["expiradas"] += 1
            elif estado == RETENIDA:
                if _cerrar_intento(db, rid, EXPIRADA, "RETENCION_VENCIDA", "El apartado del horario venció",
                                   "motor", now, desde=(RETENIDA,)):
                    out["expiradas"] += 1
    finally:
        nav.cerrar()
    return out

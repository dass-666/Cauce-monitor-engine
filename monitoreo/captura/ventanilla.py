"""v2.0 · Modo ventanilla: atención PRESENCIAL de principio a fin, encima del flujo existente.

No duplica nada: el expediente, la validación, el detector de duplicados, la reservación con
evidencia del portal y la recuperación tras reinicios son los de siempre (v1.5–v1.7). La ventanilla
solo añade la ATENCIÓN (quién atendió a quién, cuándo, con qué resultado) y una forma segura de que
el operador TOME un expediente de la cola mientras la persona está delante:

  solicitud del motor ELEGIBLE/REINTENTO --(UPDATE condicional)--> EN_VENTANILLA
      (el motor no la asigna a nadie más mientras tanto; si el motor se adelantó, 409 y se reintenta)
  al cerrar:  cita CONFIRMADA (con evidencia)    -> solicitud COMPLETADO
              sin cita (sin días, error, retiro) -> solicitud ELEGIBLE y expediente LISTO: el motor
                                                    sigue vigilando por la persona
              confirmación sin respuesta clara   -> atención PENDIENTE (VERIFICACION); la resuelve
                                                    el motor contra el portal (`resolver_pendientes`)

Reglas: nunca se marca CONFIRMADA una atención cuya reservación no lo esté; nada de datos personales
en la atención, en sus eventos ni en la auditoría (solo códigos, estados y tiempos).
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..db import Database, utcnow
from ..engine import assignment as asg
from ..models import Atencion, Expediente, Operator, Request, Reservacion
from .. import security
from . import expedientes as ex
from . import reservaciones as rs
from . import validacion as val

PENDIENTE, EN_PROCESO, CONFIRMADA = "PENDIENTE", "EN_PROCESO", "CONFIRMADA"
SIN_DISPONIBILIDAD, ERROR, CANCELADA = "SIN_DISPONIBILIDAD", "ERROR", "CANCELADA"
ESTADOS = (PENDIENTE, EN_PROCESO, CONFIRMADA, SIN_DISPONIBILIDAD, ERROR, CANCELADA)
SIN_CITA = (SIN_DISPONIBILIDAD, ERROR, CANCELADA)
VERIFICACION = "VERIFICACION"
CLAVE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
NOMBRES = {PENDIENTE: "Pendiente", EN_PROCESO: "En proceso", CONFIRMADA: "Cita confirmada",
           SIN_DISPONIBILIDAD: "Sin disponibilidad", ERROR: "Error", CANCELADA: "Cancelada"}


class ErrorVentanilla(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _actor(user) -> str:
    return f"{user.username} (ventanilla)"


def abierta(a: Atencion) -> bool:
    return a.cerrada_en is None


# ---------------------------------------------------------------- vistas (sin datos personales)
def _paso(a: Atencion, exp: Expediente | None, res: Reservacion | None) -> dict:
    """Siguiente paso recomendado, en palabras de ventanilla."""
    if not abierta(a):
        if a.estado == PENDIENTE and a.resultado == VERIFICACION:
            return {"clave": "verificacion", "texto": "El motor está comprobando la confirmación con el portal. "
                    "No vuelvas a confirmar."}
        return {"clave": "terminada", "texto": "Atención cerrada."}
    if exp is None:
        return {"clave": "expediente", "texto": "Busca el expediente de la persona o crea uno nuevo."}
    if res is not None and res.estado == rs.CONFIRMADA:
        return {"clave": "cerrar", "texto": f"Cita confirmada con folio {res.folio}. Cierra la atención."}
    if res is not None and res.estado in (rs.CONFIRMANDO, rs.EN_VERIFICACION):
        return {"clave": "verificacion", "texto": "La confirmación no tuvo respuesta clara: el motor la verifica "
                "con el portal. Puedes cerrar la atención; quedará pendiente."}
    if exp.estado in ex.PREPARACION or exp.estado == ex.REVISION:
        return {"clave": "datos", "texto": f"El expediente está {exp.estado}: corrige los datos que faltan."}
    if res is None or res.estado not in rs.VIVAS:
        return {"clave": "preparar", "texto": "Prepara el formulario y revisa los datos con la persona."}
    if res.estado == rs.PREPARADA:
        return {"clave": "enviar", "texto": "Teclea los campos manuales, marca «He revisado» y envía."}
    if res.estado == rs.DATOS_ENVIADOS:
        return {"clave": "fecha", "texto": "Elige con la persona un día disponible."}
    return {"clave": "confirmar", "texto": "Marca «Confirmo» y confirma la cita."}


def vista(s, a: Atencion) -> dict:
    from ..api.serializers import iso
    exp = s.get(Expediente, a.expediente_id) if a.expediente_id else None
    res = _reservacion(s, a)
    dur = ((a.cerrada_en or a.updated_at) - a.iniciada_en).total_seconds()
    con = _consentimiento(s, exp)
    siguiente = _paso(a, exp, res)
    falta = ex.faltantes(exp) if exp is not None and exp.estado in ex.PREPARACION else []
    if exp is not None and exp.estado in ex.PREPARACION and con["exigido"] and not con["vigente"]:
        falta = [*falta, "consentimiento"]
        if siguiente["clave"] == "datos" and falta == ["consentimiento"] and abierta(a):
            siguiente = {"clave": "consentimiento",
                         "texto": "Registra el consentimiento firmado de la persona para continuar."}
    return {
        "id": a.id, "codigo": a.codigo, "estado": a.estado, "estado_nombre": NOMBRES.get(a.estado, a.estado),
        "resultado": a.resultado, "folio": a.folio, "abierta": abierta(a), "region": a.region, "sucursal": a.sucursal,
        "iniciada_en": iso(a.iniciada_en), "cerrada_en": iso(a.cerrada_en), "duracion_s": round(dur, 1),
        "conexion": a.conexion, "operador": a.username,
        "expediente": None if exp is None else {
            "id": exp.id, "codigo": exp.codigo, "estado": exp.estado, "tipo_tramite": exp.tipo_tramite,
            "region": exp.region, "sucursal": exp.sucursal, "modo": exp.modo,
            "titular": val.enmascarar_nombre(exp.nombre) if exp.nombre else None,
            "faltan": falta},
        "reservacion": None if res is None else rs.vista(res),
        "siguiente": siguiente,
        "consentimiento": con,
    }


def _consentimiento(s, exp: Expediente | None) -> dict | None:
    """v2.2: estado del consentimiento del expediente, para la pantalla (sin datos personales)."""
    if exp is None:
        return None
    from . import consentimiento as cs
    now = utcnow()
    c = cs.vigente(s, exp, now)
    return {"exigido": cs.exigido(), "vigente": c is not None, "id": c.id if c else None,
            "codigo": c.codigo if c else None, "medio": c.medio if c else None,
            "otorgado_en": c.otorgado_en.isoformat() + "Z" if c else None,
            "bloqueo": None if c else cs.motivo_bloqueo(s, exp, now)}


def _reservacion(s, a: Atencion) -> Reservacion | None:
    """La reservación de ESTA atención: la fijada al cerrar o, si sigue abierta, el intento vivo del
    expediente o el último creado durante la atención."""
    if a.reservacion_id:
        return s.get(Reservacion, a.reservacion_id)
    if not a.expediente_id:
        return None
    viva = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == a.expediente_id))
    if viva is not None:
        return viva
    return s.scalar(select(Reservacion).where(Reservacion.expediente_id == a.expediente_id,
                                              Reservacion.created_at >= a.iniciada_en.replace(microsecond=0))
                    .order_by(Reservacion.id.desc()))


def _visible(s, aid: int, user) -> Atencion:
    a = s.get(Atencion, aid)
    if a is None or not (user.is_admin or a.username == user.username):
        raise ErrorVentanilla(404, "Atención no encontrada")     # 404 también si no es suya: no se revela
    return a


# ---------------------------------------------------------------- 1. iniciar (idempotente)
def iniciar(db: Database, user, clave: str, now: datetime, conexion: str | None = None) -> tuple[bool, dict]:
    """Devuelve (creada, atención). Repetir con la misma clave devuelve la misma atención."""
    if not CLAVE_RE.match(clave or ""):
        raise ErrorVentanilla(422, "Clave de atención no válida (8 a 64 letras, números, - o _)")
    if not user.is_admin and user.operator_id is None:
        raise ErrorVentanilla(403, "Tu usuario no está vinculado a un operador")
    for _ in range(2):
        with db.session() as s:
            a = s.scalar(select(Atencion).where(Atencion.username == user.username, Atencion.clave == clave))
            if a is not None:
                return False, vista(s, a)
        try:
            with db.session() as s:
                a = Atencion(codigo=f"TMP-{secrets.token_hex(8)}", clave=clave, username=user.username,
                             operator_id=user.operator_id, estado=PENDIENTE, pasos=0, iniciada_en=now,
                             updated_at=now, conexion=(conexion or None) and conexion[:64])
                s.add(a)
                s.flush()
                a.codigo = f"ATN-{a.id:06d}"
                security.audit(s, user.username, "atencion_iniciada", a.codigo, {})
                return True, vista(s, a)
        except IntegrityError:          # la misma clave enviada dos veces a la vez: gana una
            continue
    raise ErrorVentanilla(409, "No se pudo iniciar la atención; reinténtalo")


# ---------------------------------------------------------------- 2. buscar un expediente (coincidencia exacta)
def _resumen_exp(s, e: Expediente, user) -> dict:
    disponible, motivo = _disponible(s, e, user)
    if disponible and e.estado == ex.LISTO and e.request_id and \
            s.scalar(select(Request.status).where(Request.id == e.request_id)) == asg.EN_PLAN:
        disponible, motivo = False, "está en el plan de la jornada: se atiende desde «Plan del día»"
    return {"id": e.id, "codigo": e.codigo, "estado": e.estado, "tipo_tramite": e.tipo_tramite, "region": e.region,
            "sucursal": e.sucursal, "titular": val.enmascarar_nombre(e.nombre) if e.nombre else None,
            "puede_atenderse": disponible, "motivo": motivo}


def _disponible(s, e: Expediente, user) -> tuple[bool, str]:
    if not user.is_admin:
        op = s.get(Operator, user.operator_id) if user.operator_id else None
        if op is None:
            return False, "tu usuario no está vinculado a un operador"
        if op.regions and e.region not in op.regions:
            return False, "es de otra región"
        if op.sucursales and e.sucursal and e.sucursal not in op.sucursales and not (
                set(e.sucursales_alternativas or []) & set(op.sucursales)):
            return False, "es de una sucursal que no atiendes"
    if e.estado == ex.RES_CONFIRMADA:
        folio = s.scalar(select(Reservacion.folio).where(Reservacion.expediente_id == e.id,
                                                        Reservacion.estado == rs.CONFIRMADA))
        return False, f"ya tiene una cita confirmada (folio {folio})"
    if e.estado == ex.CERRADO:
        return False, "el expediente está cerrado"
    if e.estado in ex.OPERACION_ACTIVA and e.operator_id not in (user.operator_id,):
        return False, "lo está atendiendo otro operador"
    if e.estado == ex.RES_PENDIENTE and rs.viva_bloquea(s, e.id):
        return True, "tiene una confirmación en verificación"
    return True, ""


def buscar(db: Database, user, aid: int, codigo: str | None = None, nombre: str | None = None,
           fecha_nacimiento: str | None = None, tipo_tramite: str | None = None) -> list[dict]:
    """Solo coincidencias EXACTAS (código, o nombre + fecha de nacimiento + trámite): no permite
    recorrer la base de expedientes. Devuelve datos enmascarados."""
    with db.session() as s:
        a = _visible(s, aid, user)
        if not abierta(a):
            raise ErrorVentanilla(409, "La atención ya está cerrada")
        out: list[Expediente] = []
        if codigo:
            e = s.scalar(select(Expediente).where(Expediente.codigo == codigo.strip().upper()))
            if e is not None:
                out.append(e)
        elif nombre and fecha_nacimiento and tipo_tramite:
            try:
                h = val.huella_duplicado(nombre, fecha_nacimiento, tipo_tramite)
            except ValueError as err:
                raise ErrorVentanilla(422, "Datos de búsqueda no válidos") from err
            e = s.scalar(select(Expediente).where(Expediente.dedup_activo == h))
            if e is not None:
                out.append(e)
        else:
            raise ErrorVentanilla(422, "Busca por código del expediente, o por nombre, fecha de nacimiento y trámite")
        security.audit(s, user.username, "ventanilla_busqueda", a.codigo,
                       {"por": "codigo" if codigo else "titular", "encontrados": len(out)})
        return [_resumen_exp(s, e, user) for e in out]


# ---------------------------------------------------------------- 3. vincular: crear o recuperar y TOMAR
def _tomar(db: Database, user, exp_id: int, now: datetime, desde_plan: bool = False) -> None:
    """La persona está delante: el operador toma el expediente. Transacción con UPDATE condicional
    sobre la solicitud del motor (si el motor la asignó a la vez, gana uno y el otro recibe 409)."""
    with db.session() as s:
        e = s.get(Expediente, exp_id)
        ok, motivo = _disponible(s, e, user)
        if not ok:
            raise ErrorVentanilla(409, f"No se puede atender este expediente: {motivo}")
        if e.estado in ex.PREPARACION or e.estado == ex.REVISION:
            return            # se atiende para completar o corregir datos; no se toma de la cola
        if e.estado in ex.OPERACION_ACTIVA:
            return            # ya es suyo (asignado por el motor o tomado antes en ventanilla)
        from . import consentimiento as cs
        cs.exigir(s, e, now, "tomar en ventanilla", error_cls=ErrorVentanilla)
        if e.estado != ex.LISTO or e.request_id is None:
            raise ErrorVentanilla(409, f"El expediente está {e.estado}")
        req = s.get(Request, e.request_id)
        try:
            if req.status == asg.PENDIENTE:
                asg.validate_request(s, req, now, actor=_actor(user))
            if req.status in asg.MATCHABLE:
                asg.transition(s, req, asg.EN_VENTANILLA, _actor(user), "atención presencial", now)
            elif req.status == asg.EN_PLAN:      # v2.1: su única autoridad es el plan diario
                if not desde_plan:
                    raise ErrorVentanilla(409, "Este expediente está en el plan de la jornada: atiéndelo desde "
                                          "«Plan del día» cuando tenga una coincidencia", {"codigo": "EN_PLAN"})
                asg.transition(s, req, asg.EN_VENTANILLA, _actor(user), "operación del plan diario", now)
            elif not (user.operator_id is not None and          # el motor se lo acaba de asignar A ÉL
                      asg.pasar_a_ventanilla(s, req, user.operator_id, _actor(user), now)):
                raise ErrorVentanilla(409, "El motor acaba de asignar este expediente; vuelve a buscarlo",
                                      {"estado_solicitud": req.status})
        except asg.TransitionError as err:      # incluye ConcurrentChange
            raise ErrorVentanilla(409, "El expediente cambió mientras tanto; vuelve a buscarlo") from err
        op_id = user.operator_id
        if op_id is None:      # un administrador sin operador no puede capturar en el portal
            raise ErrorVentanilla(403, "Para atender en ventanilla tu usuario debe estar vinculado a un operador")
        ex.cambiar_estado(s, e, ex.ASIGNADO, _actor(user), now, operador=op_id, canal="ventanilla")
        e.operator_id = op_id
        ex.evento(s, e.id, "TOMADO_EN_VENTANILLA", _actor(user), now, operador=op_id)


def vincular(db: Database, user, aid: int, now: datetime, expediente_id: int | None = None,
             datos: dict | None = None) -> dict:
    with db.session() as s:
        a = _visible(s, aid, user)
        if not abierta(a):
            raise ErrorVentanilla(409, "La atención ya está cerrada")
        actual = a.expediente_id
    if actual is not None:
        if expediente_id in (None, actual) and datos is None:
            with db.session() as s:
                return vista(s, s.get(Atencion, aid))
        raise ErrorVentanilla(409, "Esta atención ya tiene un expediente; ciérrala y empieza otra")
    if datos is not None:
        r = None
        with db.session() as s:
            r = val.validar(datos, ex._regiones(s), incompleto=False)
        if not r.ok:
            raise ErrorVentanilla(422, "Revisa los datos", {"errores": r.errores})
        try:
            nuevo = ex.crear_seguro(db, datos, _actor(user), now)
        except ex.ErrorExpediente as err:
            if err.http == 409:
                coincide = buscar(db, user, aid, nombre=datos.get("nombre"),
                                  fecha_nacimiento=str(datos.get("fecha_nacimiento") or ""),
                                  tipo_tramite=datos.get("tipo_tramite")) if datos.get("fecha_nacimiento") else []
                raise ErrorVentanilla(409, "Ya existe un expediente activo para esta persona y este trámite: "
                                      "recupéralo en lugar de crear otro", {"duplicado": coincide}) from err
            raise ErrorVentanilla(err.http, err.mensaje, {"errores": err.errores}) from err
        expediente_id = nuevo["id"]
        with db.session() as s:
            security.audit(s, user.username, "expediente_creado", nuevo["codigo"], {"canal": "ventanilla"})
    if expediente_id is None:
        raise ErrorVentanilla(422, "Indica el expediente o los datos para crearlo")
    with db.session() as s:
        if s.get(Expediente, expediente_id) is None:
            raise ErrorVentanilla(404, "Expediente no encontrado")
    _tomar(db, user, expediente_id, now)
    try:
        with db.session() as s:
            a = s.get(Atencion, aid)
            e = s.get(Expediente, expediente_id)
            a.expediente_id, a.viva_expediente_id = e.id, e.id
            a.estado, a.region, a.sucursal, a.updated_at = EN_PROCESO, e.region, e.sucursal, now
            a.pasos += 1
            ex.evento(s, e.id, "ATENCION_INICIADA", _actor(user), now, atencion=a.codigo)
            security.audit(s, user.username, "atencion_expediente", a.codigo, {"expediente": e.codigo})
            return vista(s, a)
    except IntegrityError as err:
        raise ErrorVentanilla(409, "Este expediente ya tiene otra atención abierta") from err


# ---------------------------------------------------------------- v2.2 · consentimiento en ventanilla
def registrar_consentimiento(db: Database, user, aid: int, datos: dict, now: datetime, ip: str = "") -> dict:
    """La persona está presente y firma: el operador registra su consentimiento desde la atención.
    Si con eso el expediente queda LISTO, se toma para esta atención (como al vincularlo)."""
    from . import consentimiento as cs
    with db.session() as s:
        a = _visible(s, aid, user)
        if not abierta(a):
            raise ErrorVentanilla(409, "La atención ya está cerrada")
        if a.expediente_id is None:
            raise ErrorVentanilla(409, "Primero vincula el expediente de la persona")
        e = s.get(Expediente, a.expediente_id)
        try:
            c = cs.otorgar(s, e, datos, _actor(user), now, ip)
        except cs.ErrorConsentimiento as err:
            raise ErrorVentanilla(err.http, err.mensaje, err.detalle) from err
        codigo, estado, exp_id = c.codigo, e.estado, e.id
    if estado == ex.LISTO:
        _tomar(db, user, exp_id, now)
    with db.session() as s:
        return {"consentimiento": codigo, "atencion": vista(s, s.get(Atencion, aid))}


# ---------------------------------------------------------------- 4. cerrar
def _devolver_a_la_cola(db: Database, user, exp_id: int, now: datetime, motivo: str) -> bool:
    """Sin cita: la persona vuelve a la cola del motor (que seguirá vigilando por ella)."""
    with db.session() as s:
        viva = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
        viva_id, viva_estado = (viva.id, viva.estado) if viva else (None, None)
    if viva_id and viva_estado in (rs.PREPARADA, rs.DATOS_ENVIADOS, rs.RETENIDA, rs.FECHA_ELEGIDA):
        rs.cancelar(db, viva_id, user, now)      # descarta el intento; un apartado del portal vence solo
    from . import consentimiento as cs
    with db.session() as s:
        retirado = cs.revocado(s, exp_id) is not None
    if retirado:
        # v2.2: con el consentimiento revocado no vuelve ni al plan ni a la cola: se cierra
        cs.aplicar_revocacion(db, exp_id, _actor(user), now, cerrar_atencion=False)   # la cierra quien llama
        return False
    with db.session() as s:
        e = s.get(Expediente, exp_id)
        req = s.get(Request, e.request_id) if e.request_id else None
        if req is None or req.status != asg.EN_VENTANILLA or e.estado not in ex.OPERACION_ACTIVA:
            return False
        if rs.viva_bloquea(s, e.id):
            return False
        from ..planificador.propiedad import regresar_al_plan
        if not regresar_al_plan(s, e.id, req, _actor(user), now, motivo):   # v2.1: si es del plan, vuelve al plan
            asg.transition(s, req, asg.ELEGIBLE, _actor(user), f"atención cerrada sin cita: {motivo}", now)
        ex.cambiar_estado(s, e, ex.LISTO, _actor(user), now, motivo="atencion_sin_cita")
        e.operator_id = None
        return True


def cerrar(db: Database, user, aid: int, now: datetime, resultado: str | None = None, motivo: str = "",
           cerrar_expediente: bool = False) -> dict:
    with db.session() as s:
        a = _visible(s, aid, user)
        if not abierta(a):
            return vista(s, a)                            # idempotente
        exp_id = a.expediente_id
        res = _reservacion(s, a)
        res_id, res_estado, folio = (res.id, res.estado, res.folio) if res else (None, None, None)
        conexion_portal = res.conexion if res else None     # v2.0: por qué red salió la sesión con el portal
    if res_estado == rs.CONFIRMADA:
        final, codigo = CONFIRMADA, "CITA_CONFIRMADA"
    elif res_estado in (rs.CONFIRMANDO, rs.EN_VERIFICACION):
        final, codigo = PENDIENTE, VERIFICACION
    else:
        if resultado not in SIN_CITA:
            raise ErrorVentanilla(422, "No hay cita confirmada: indica el resultado (SIN_DISPONIBILIDAD, ERROR "
                                       "o CANCELADA)")
        final, codigo = resultado, (re.sub(r"[^A-Z0-9_]", "", (motivo or "").upper())[:32] or resultado)
        if exp_id:
            _devolver_a_la_cola(db, user, exp_id, now, codigo)
            if cerrar_expediente and resultado == CANCELADA:
                with db.session() as s:
                    e = s.get(Expediente, exp_id)
                    if e.estado in (ex.LISTO, ex.NUEVO, ex.INCOMPLETO, ex.REVISION):
                        if e.request_id:
                            ex._cancelar_solicitud_en_sesion(s, e, _actor(user), now)
                        ex.cerrar(s, e, "CANCELADO", _actor(user), now, nota="la persona retiró la solicitud")
    with db.session() as s:
        a = s.get(Atencion, aid)
        if not abierta(a):
            return vista(s, a)
        a.estado, a.resultado, a.cerrada_en, a.updated_at = final, codigo, now, now
        a.reservacion_id = res_id or a.reservacion_id
        a.conexion = conexion_portal or a.conexion
        a.folio = folio if final == CONFIRMADA else None
        if final != PENDIENTE:
            a.viva_expediente_id = None
        a.pasos += 1
        if exp_id:
            ex.evento(s, exp_id, "ATENCION_CERRADA", _actor(user), now, atencion=a.codigo, resultado=final,
                      motivo=codigo)
        security.audit(s, user.username, "atencion_cerrada", a.codigo, {"estado": final, "motivo": codigo})
        out = vista(s, a)
    if final == CONFIRMADA:
        completar_solicitudes(db, now)
    return out


# ---------------------------------------------------------------- 5. lo que resuelve el motor
def resolver_pendientes(db: Database, now: datetime) -> dict:
    """Atenciones cerradas con la confirmación en verificación: cuando el motor obtiene la respuesta
    del portal (recuperación por clave), la atención pasa a CONFIRMADA o a ERROR."""
    out = {"confirmadas": 0, "fallidas": 0}
    with db.session() as s:
        pend = [(a.id, a.reservacion_id, a.expediente_id) for a in s.scalars(select(Atencion).where(
            Atencion.estado == PENDIENTE, Atencion.resultado == VERIFICACION))]
    for aid, rid, eid in pend:
        with db.session() as s:
            r = s.get(Reservacion, rid) if rid else None
            if r is None or r.estado in (rs.CONFIRMANDO, rs.EN_VERIFICACION):
                continue
            a = s.get(Atencion, aid)
            if r.estado == rs.CONFIRMADA:
                a.estado, a.folio, a.resultado = CONFIRMADA, r.folio, "CITA_CONFIRMADA_TRAS_VERIFICAR"
                out["confirmadas"] += 1
            else:
                a.estado, a.resultado = ERROR, f"CONFIRMACION_{r.estado}"[:32]
                out["fallidas"] += 1
            a.viva_expediente_id, a.updated_at = None, now
            ex.evento(s, eid, "ATENCION_RESUELTA", "motor", now, atencion=a.codigo, resultado=a.estado)
    return out


def completar_solicitudes(db: Database, now: datetime) -> int:
    """Solicitud EN_VENTANILLA de un expediente ya confirmado -> COMPLETADO (no "cancelada")."""
    n = 0
    with db.session() as s:
        filas = s.execute(select(Request).join(Expediente, Expediente.request_id == Request.id).where(
            Request.status == asg.EN_VENTANILLA, Expediente.estado == ex.RES_CONFIRMADA)).scalars().all()
        for req in filas:
            try:
                with s.begin_nested():
                    asg.transition(s, req, asg.COMPLETADO, "ventanilla", "cita confirmada en ventanilla", now)
                n += 1
            except asg.TransitionError:
                pass
    return n


# ---------------------------------------------------------------- 6. el día (meta y progreso)
def resumen_hoy(db: Database, user, now: datetime) -> dict:
    from ..api.centro import _desde
    from ..engine.params import get_params
    hoy = _desde("hoy", now)
    with db.session() as s:
        meta = int(get_params(s).get("meta_citas_dia", 6))
        q = select(Atencion).where(Atencion.iniciada_en >= hoy)
        todas = list(s.scalars(q))
        mias = [a for a in todas if a.username == user.username]
        confirmadas_hoy = s.scalar(select(func.count()).select_from(Reservacion).where(
            Reservacion.estado == rs.CONFIRMADA, Reservacion.updated_at >= hoy)) or 0
        abiertas = [vista(s, a) for a in s.scalars(select(Atencion).where(
            Atencion.username == user.username, Atencion.cerrada_en.is_(None)).order_by(Atencion.id))]
        recientes = [vista(s, a) for a in s.scalars(select(Atencion).where(
            Atencion.username == user.username, Atencion.iniciada_en >= hoy).order_by(Atencion.id.desc()).limit(10))]

    def cuenta(lista):
        por = {k: 0 for k in ESTADOS}
        for a in lista:
            por[a.estado] = por.get(a.estado, 0) + 1
        cerr = [((a.cerrada_en - a.iniciada_en).total_seconds()) for a in lista if a.cerrada_en]
        return {"atenciones": len(lista), "por_estado": por,
                "con_cita": por[CONFIRMADA], "sin_cita": sum(por[k] for k in SIN_CITA),
                "pendientes": sum(1 for a in lista if a.cerrada_en is None) + sum(
                    1 for a in lista if a.estado == PENDIENTE and a.cerrada_en is not None),
                "tiempo_medio_s": round(sum(cerr) / len(cerr), 1) if cerr else None}
    return {"meta_citas_dia": meta, "citas_confirmadas_hoy": confirmadas_hoy,
            "progreso_pct": min(100, round(100 * confirmadas_hoy / meta)) if meta else None,
            "equipo": cuenta(todas), "mias": cuenta(mias), "abiertas": abiertas, "recientes": recientes}

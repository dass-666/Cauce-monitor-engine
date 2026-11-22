"""Piloto asistido · la cita la agenda UNA PERSONA en el portal oficial; Cauce guarda la confirmación humana.

Cauce no se conecta al portal oficial (no hay autorización del proveedor para automatizarlo). El operador
trabaja así, todo desde la ficha del expediente:

  1. abrir      Cauce muestra los datos a teclear y guarda QUIÉN abrió la revisión y CUÁNDO, con la huella
                SHA-256 de esos datos (sin guardar los datos en la confirmación).
  2. revisar    la persona escribe la frase exacta «REVISÉ Y APRUEBO» y devuelve la huella que vio; se exige
                un mínimo de segundos con la revisión abierta. Si los datos cambiaron desde que los vio, se
                rechaza (lo revisado tiene que ser lo que hay).
  3. (fuera de Cauce) agenda en el portal oficial, con la persona presente.
  4. registrar  teclea el folio DOS veces, la fecha (y hora y sucursal si las hay) y, si lo tiene, la huella
                del comprobante (el archivo no se sube: se calcula en el navegador o se pega). Se crea la
                reservación CONFIRMADA (contrato `oficial_asistido`), el expediente pasa a
                RESERVACION_CONFIRMADA y la cita queda en el diario de eventos críticos (origen «persona»).
  revocar       antes de registrar, la persona o un administrador anula la confirmación (con motivo).
  anular        DESPUÉS de registrar, si la persona cancela: un administrador declara que canceló la cita en el
                portal oficial (folio tecleado otra vez y motivo). La reservación pasa a CANCELADA, el expediente
                se cierra (CANCELADO) y el diario de eventos críticos lo anota. El folio no se puede reutilizar.

Reglas:
- Ninguna acción automática (cerrar el navegador, perder la red, que pase el tiempo) cuenta como revisión
  ni como registro: el estado solo cambia con estas llamadas explícitas, cada una auditada.
- La cita registrada es «declarada por una persona»: Cauce NO la comprobó con el proveedor. Así se muestra.
- Abrir una revisión exige consentimiento vigente y que no haya una recuperación pendiente (tras restaurar,
  primero se concilia: evita agendar dos veces a alguien cuya cita se perdió en la restauración).
- Registrar una cita YA hecha en el portal no se bloquea por la recuperación pendiente (perder el folio
  sería peor), pero sí exige consentimiento vigente y que el diario de eventos críticos se pueda escribir.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import proveedor, recuperacion, security
from ..db import Database
from ..engine import assignment as asg
from ..models import Assignment, ConfirmacionHumana, Expediente, Reservacion
from . import consentimiento as cs
from . import expedientes as ex
from . import reservaciones as rs

CONTRATO = "oficial_asistido"
FRASE_REVISION = "REVISÉ Y APRUEBO"
MIN_REVISION_S = 5
ABIERTA, REVISADA, CITA_REGISTRADA, REVOCADA, ANULADA = "ABIERTA", "REVISADA", "CITA_REGISTRADA", "REVOCADA", "ANULADA"
VIVAS = (ABIERTA, REVISADA)
ESTADOS_EXPEDIENTE = (ex.ASIGNADO, ex.CAPTURA)
FOLIO_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,31}$")
HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SUCURSAL_RE = re.compile(r"^[A-Z0-9][A-Z0-9 _-]{0,31}$")
REF_RE = re.compile(r"^[^\x00-\x1f<>]{1,64}$")
# Lo que la persona teclea en el portal: si cambia cualquiera de estos campos, la revisión deja de valer.
CAMPOS_HUELLA = ("codigo", "nombre", "fecha_nacimiento", "telefono", "correo", "id_registro_previo", "tipo_tramite",
                 "operacion", "region", "sucursal", "sucursales_alternativas", "folio_anterior", "modo",
                 "fecha_solicitada", "fechas_desde", "fechas_hasta")


class ErrorAsistida(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _actor(user) -> str:
    return user.username[:64]


def huella_datos(exp: Expediente) -> str:
    d = {}
    for k in CAMPOS_HUELLA:
        v = getattr(exp, k)
        d[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def vista(c: ConfirmacionHumana | None, now: datetime | None = None) -> dict | None:
    if c is None:
        return None
    from ..api.serializers import iso
    det = c.detalle or {}
    out = {"id": c.id, "expediente_id": c.expediente_id, "reservacion_id": c.reservacion_id, "persona": c.persona,
           "estado": c.estado, "huella_datos": c.huella_datos, "abierta_en": iso(c.abierta_en),
           "revisada_en": iso(c.revisada_en), "registrada_en": iso(c.registrada_en), "revocada_en": iso(c.revocada_en),
           "revocada_por": c.revocada_por, "motivo_revocacion": det.get("motivo_revocacion"),
           "motivo_anulacion": det.get("motivo_anulacion"),
           "folio": c.folio, "fecha": c.fecha, "hora": c.hora, "sucursal": c.sucursal,
           "comprobante_sha256": c.comprobante_sha256, "comprobante_ref": c.comprobante_ref,
           "comprobante": det.get("comprobante"), "min_revision_s": MIN_REVISION_S, "frase": FRASE_REVISION,
           # Nunca «verificada»: Cauce no consulta al portal oficial.
           "verificada_con_proveedor": False}
    if now is not None and c.estado == ABIERTA:
        out["segundos_abierta"] = round((now - c.abierta_en).total_seconds(), 1)
    return out


def _exp(s, exp_id: int, user) -> Expediente:
    e = s.get(Expediente, exp_id)
    if e is None or not ex.puede_ver(user, e):
        raise ErrorAsistida(404, "Expediente no encontrado")      # 404 también si no es suyo: no se revela
    return e


def _viva(s, exp_id: int) -> ConfirmacionHumana | None:
    return s.scalar(select(ConfirmacionHumana).where(ConfirmacionHumana.viva_expediente_id == exp_id))


def _propia(c: ConfirmacionHumana, user) -> None:
    if c.persona != _actor(user):
        raise ErrorAsistida(409, f"Esta revisión la abrió {c.persona}: solo esa persona puede continuarla "
                                 "(o un administrador revocarla)", {"codigo": "OTRA_PERSONA"})


def estado(db: Database, exp_id: int, user, now: datetime) -> dict:
    with db.session() as s:
        _exp(s, exp_id, user)
        hist = list(s.scalars(select(ConfirmacionHumana).where(ConfirmacionHumana.expediente_id == exp_id)
                              .order_by(ConfirmacionHumana.id.desc())))
        viva = next((c for c in hist if c.viva_expediente_id is not None), None)
        return {"viva": vista(viva, now), "historial": [vista(c) for c in hist], "frase": FRASE_REVISION,
                "min_revision_s": MIN_REVISION_S}


# ---------------------------------------------------------------- 1. abrir la revisión
def abrir(db: Database, exp_id: int, user, now: datetime, ip: str = "") -> dict:
    """Abre (o retoma, si es de la misma persona) la revisión. Devuelve la huella que la persona debe
    devolver al confirmar. Los datos a teclear los muestra la ficha del expediente (vista completa)."""
    actor = _actor(user)
    with db.session() as s:
        e = _exp(s, exp_id, user)
        viva = _viva(s, exp_id)
        if viva is not None:
            _propia(viva, user)
            return {"confirmacion": vista(viva, now), "retomada": True}
        if e.estado not in ESTADOS_EXPEDIENTE:
            raise ErrorAsistida(409, f"El expediente está {e.estado}: para agendar debe estar asignado a un "
                                     "operador (tómalo en Ventanilla o espera la asignación)", {"estado": e.estado})
        if e.operator_id is None:
            raise ErrorAsistida(409, "El expediente aún no tiene operador asignado")
        recuperacion.exigir_operable(s, error_cls=ErrorAsistida)
        cs.exigir(s, e, now, "agendar en el portal oficial", error_cls=ErrorAsistida)
        otra = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
        if otra is not None and otra.estado != rs.PREPARADA:
            raise ErrorAsistida(409, f"Hay otro intento de reservación abierto ({otra.estado}): termínalo o cancélalo "
                                     "antes de agendar a mano", {"codigo": "OTRO_INTENTO", "reservacion": otra.id})
        live = s.scalar(select(Assignment).where(Assignment.request_id == e.request_id,
                                                 Assignment.status.in_(["ACTIVA", "EN_PROCESO"]))) if e.request_id else None
        aid, astatus, op_id = (live.id, live.status, e.operator_id) if live else (None, None, e.operator_id)
    if aid and astatus == "ACTIVA":
        if not asg.take_assignment(db, aid, op_id, now, actor, idempotent=True):
            raise ErrorAsistida(409, "La asignación ya no está disponible (expiró o la tomó otro)")
    try:
        with db.session() as s:
            e = s.get(Expediente, exp_id)
            res = s.scalar(select(Reservacion).where(Reservacion.viva_expediente_id == exp_id))
            if res is not None and res.contrato != CONTRATO:
                # un intento de laboratorio solo PREPARADO (nada enviado a ningún portal): se descarta
                res.estado, res.viva_expediente_id, res.updated_at = rs.CANCELADA, None, now
                res.error_codigo, res.error = "SUSTITUIDA_POR_ASISTIDA", "Sustituida por una cita agendada a mano"
                s.flush()
                res = None
            if res is None:
                res = Reservacion(expediente_id=exp_id, viva_expediente_id=exp_id, assignment_id=aid,
                                  operator_id=e.operator_id, estado=rs.PREPARADA, contrato=CONTRATO, modo=e.modo,
                                  clave="asis_" + secrets.token_hex(16), proveedor=proveedor.identidad_local()["id"],
                                  evidencia={}, created_at=now, updated_at=now)
                s.add(res)
                s.flush()
            if e.estado == ex.ASIGNADO:
                ex.cambiar_estado(s, e, ex.CAPTURA, actor, now, canal="asistido")
            c = ConfirmacionHumana(expediente_id=exp_id, viva_expediente_id=exp_id, reservacion_id=res.id,
                                   persona=actor, estado=ABIERTA, huella_datos=huella_datos(e), abierta_en=now,
                                   detalle={})
            s.add(c)
            s.flush()
            ex.evento(s, exp_id, "REVISION_ASISTIDA_ABIERTA", actor, now, assignment_id=aid, confirmacion=c.id)
            security.audit(s, user.username, "asistida_revision_abierta", e.codigo, {"confirmacion": c.id}, ip)
            return {"confirmacion": vista(c, now), "retomada": False}
    except IntegrityError as err:      # dos pestañas o dos personas abriendo a la vez
        raise ErrorAsistida(409, "Ya hay una revisión abierta para este expediente: recarga la ficha") from err


# ---------------------------------------------------------------- 2. confirmar la revisión
def revisar(db: Database, exp_id: int, user, frase: str, huella_vista: str, now: datetime, ip: str = "") -> dict:
    actor = _actor(user)
    with db.session() as s:
        e = _exp(s, exp_id, user)
        c = _viva(s, exp_id)
        if c is None:
            raise ErrorAsistida(409, "No hay una revisión abierta: ábrela primero", {"codigo": "SIN_REVISION"})
        _propia(c, user)
        if c.estado == REVISADA:
            return {"confirmacion": vista(c), "repetida": True}
        if (now - c.abierta_en).total_seconds() < MIN_REVISION_S:
            raise ErrorAsistida(422, f"Revisa los datos con calma: la revisión debe estar abierta al menos "
                                     f"{MIN_REVISION_S} s", {"codigo": "REVISION_DEMASIADO_RAPIDA"})
        if (frase or "").strip() != FRASE_REVISION:
            raise ErrorAsistida(422, f"Escribe exactamente «{FRASE_REVISION}»", {"codigo": "FRASE_INCORRECTA"})
        if huella_vista != c.huella_datos:
            raise ErrorAsistida(409, "Lo revisado no corresponde a esta revisión: recarga la ficha",
                                {"codigo": "HUELLA_NO_COINCIDE"})
        if huella_datos(e) != c.huella_datos:
            raise ErrorAsistida(409, "Los datos del expediente cambiaron después de abrir la revisión: revócala y "
                                     "ábrela de nuevo", {"codigo": "DATOS_CAMBIARON"})
        cs.exigir(s, e, now, "confirmar la revisión", error_cls=ErrorAsistida)
        t = ConfirmacionHumana.__table__
        if s.execute(update(t).where(t.c.id == c.id, t.c.estado == ABIERTA).values(
                estado=REVISADA, revisada_en=now)).rowcount != 1:
            raise ErrorAsistida(409, "La revisión cambió mientras tanto: recarga la ficha")
        s.refresh(c)
        ex.evento(s, exp_id, "REVISION_ASISTIDA_CONFIRMADA", actor, now, confirmacion=c.id,
                  segundos_revision=round((now - c.abierta_en).total_seconds(), 1))
        security.audit(s, user.username, "asistida_revision_confirmada", e.codigo, {"confirmacion": c.id}, ip)
        return {"confirmacion": vista(c), "repetida": False}


# ---------------------------------------------------------------- 4. registrar la cita hecha en el portal
def _normalizar(folio: str, folio_confirmacion: str, fecha: str, hora: str | None, sucursal: str | None,
                comprobante_sha256: str | None, comprobante_ref: str | None, now: datetime) -> dict:
    errores: dict[str, str] = {}
    f1 = re.sub(r"\s+", "", folio or "").upper()
    f2 = re.sub(r"\s+", "", folio_confirmacion or "").upper()
    if not FOLIO_RE.match(f1):
        errores["folio"] = "Folio no válido: 4 a 32 letras, números o guiones, tal como lo muestra el portal"
    elif f1 != f2:
        errores["folio_confirmacion"] = "Los dos folios no coinciden: vuelve a teclearlo mirando el portal"
    try:
        d = date.fromisoformat((fecha or "").strip())
        hoy = (now - timedelta(days=1)).date()        # margen por zona horaria
        if d < hoy:
            errores["fecha"] = "La fecha de la cita ya pasó"
        elif d > hoy + timedelta(days=400):
            errores["fecha"] = "Fecha demasiado lejana: revísala"
    except ValueError:
        d = None
        errores["fecha"] = "Fecha no válida (AAAA-MM-DD)"
    h = (hora or "").strip() or None
    if h and not HORA_RE.match(h):
        errores["hora"] = "Hora no válida (HH:MM)"
    suc = (sucursal or "").strip().upper() or None
    if suc and not SUCURSAL_RE.match(suc):
        errores["sucursal"] = "Sucursal no válida"
    sha = (comprobante_sha256 or "").strip().lower() or None
    if sha and not SHA_RE.match(sha):
        errores["comprobante_sha256"] = "Huella del comprobante no válida (64 caracteres hexadecimales)"
    ref = (comprobante_ref or "").strip() or None
    if ref and not REF_RE.match(ref):
        errores["comprobante_ref"] = "Referencia no válida (máximo 64 caracteres, sin símbolos < >)"
    if errores:
        raise ErrorAsistida(422, "Revisa los datos de la cita", {"errores": errores})
    tipo = "CON_HUELLA" if sha else ("SOLO_REFERENCIA" if ref else "SIN_COMPROBANTE")
    return {"folio": f1, "fecha": d.isoformat(), "hora": h, "sucursal": suc, "sha": sha, "ref": ref, "tipo": tipo}


def registrar(db: Database, exp_id: int, user, now: datetime, folio: str, folio_confirmacion: str, fecha: str,
              hora: str | None = None, sucursal: str | None = None, comprobante_sha256: str | None = None,
              comprobante_ref: str | None = None, declaro: bool = False, ip: str = "") -> dict:
    if not declaro:
        raise ErrorAsistida(422, "Marca que el portal oficial mostró este folio al terminar: sin folio no hay cita",
                            {"codigo": "SIN_DECLARACION"})
    n = _normalizar(folio, folio_confirmacion, fecha, hora, sucursal, comprobante_sha256, comprobante_ref, now)
    actor = _actor(user)
    with db.session() as s:
        e = _exp(s, exp_id, user)
        c = _viva(s, exp_id)
        if c is None:
            hecha = s.scalar(select(ConfirmacionHumana).where(ConfirmacionHumana.expediente_id == exp_id,
                                                              ConfirmacionHumana.estado == CITA_REGISTRADA)
                             .order_by(ConfirmacionHumana.id.desc()))
            if hecha is not None and hecha.folio == n["folio"]:
                return {"confirmacion": vista(hecha), "repetida": True}       # doble clic / respuesta perdida
            raise ErrorAsistida(409, "No hay una revisión confirmada para este expediente", {"codigo": "SIN_REVISION"})
        _propia(c, user)
        if c.estado != REVISADA:
            raise ErrorAsistida(409, "Primero confirma la revisión de los datos", {"codigo": "SIN_REVISION_CONFIRMADA"})
        if huella_datos(e) != c.huella_datos:
            raise ErrorAsistida(409, "Los datos del expediente cambiaron después de la revisión: un administrador "
                                     "debe revisarlo antes de registrar la cita", {"codigo": "DATOS_CAMBIARON"})
        if e.estado != ex.CAPTURA:
            raise ErrorAsistida(409, f"El expediente está {e.estado}: no se puede registrar la cita",
                                {"estado": e.estado})
        # bloquea la fila del consentimiento: una revocación simultánea espera o gana, nunca se cruza
        cs.exigir(s, e, now, "registrar la cita", error_cls=ErrorAsistida, bloquear=True)
        dup = s.scalar(select(Reservacion.id).where(Reservacion.folio == n["folio"]))
        if dup is not None:
            raise ErrorAsistida(409, "Ese folio ya está registrado en otra cita: compruébalo en el portal",
                                {"codigo": "FOLIO_DUPLICADO"})
        res_id = c.reservacion_id
    # El intento queda en el diario de eventos críticos ANTES de guardar la cita (como en v2.4).
    try:
        recuperacion.confirmacion_intento(db, res_id)
    except recuperacion.ErrorDiario as err:
        raise ErrorAsistida(503, "No se pudo escribir el diario de eventos críticos: la cita NO se guardó. Anota el "
                                 "folio en papel y avisa al administrador (autodiagnóstico).",
                            {"codigo": "DIARIO_NO_DISPONIBLE"}) from err
    try:
        aid = _guardar(db, exp_id, res_id, user, n, now, ip)
    finally:
        recuperacion.confirmacion_resultado(db, res_id, origen="persona")
    if aid:
        if not asg.complete_assignment(db, aid, None, now, actor):
            with db.session() as s:
                a = s.get(Assignment, aid)
                ex.evento(s, exp_id, "ASIGNACION_NO_COMPLETADA", "motor", now, assignment_id=aid,
                          reservacion_id=res_id, estado_asignacion=a.status if a else None)
    from . import ventanilla
    ventanilla.completar_solicitudes(db, now)
    with db.session() as s:
        return {"confirmacion": vista(s.get(ConfirmacionHumana, _ultima(s, exp_id))), "repetida": False}


def _ultima(s, exp_id: int) -> int:
    return s.scalar(select(ConfirmacionHumana.id).where(ConfirmacionHumana.expediente_id == exp_id)
                    .order_by(ConfirmacionHumana.id.desc()).limit(1))


def _guardar(db: Database, exp_id: int, res_id: int, user, n: dict, now: datetime, ip: str) -> int | None:
    actor = _actor(user)
    try:
        with db.session() as s:
            c = _viva(s, exp_id)
            if c is None or c.estado != REVISADA or c.reservacion_id != res_id:
                raise ErrorAsistida(409, "La revisión cambió mientras tanto: recarga la ficha")
            e = s.get(Expediente, exp_id)
            evid = {"fuente": "PERSONA_PORTAL_OFICIAL", "estado_portal": "DECLARADA_POR_PERSONA",
                    "confirmacion_humana": c.id, "revisada_por": c.persona, "revisada_en": c.revisada_en.isoformat() + "Z",
                    "registrada_en": now.isoformat() + "Z", "verificada_en": None, "verificada_con_proveedor": False,
                    "comprobante": n["tipo"], "comprobante_sha256": n["sha"], "comprobante_ref": n["ref"]}
            t = Reservacion.__table__
            if s.execute(update(t).where(t.c.id == res_id, t.c.estado == rs.PREPARADA, t.c.contrato == CONTRATO).values(
                    estado=rs.CONFIRMADA, folio=n["folio"], fecha=n["fecha"], hora=n["hora"], sucursal=n["sucursal"],
                    evidencia=evid, confirmada_por=actor, error=None, error_codigo=None, updated_at=now)).rowcount != 1:
                raise ErrorAsistida(409, "El intento de reservación cambió mientras tanto (¿consentimiento revocado?)",
                                    {"codigo": "RESERVACION_CAMBIO"})
            tc = ConfirmacionHumana.__table__
            s.execute(update(tc).where(tc.c.id == c.id, tc.c.estado == REVISADA).values(
                estado=CITA_REGISTRADA, viva_expediente_id=None, registrada_en=now, folio=n["folio"], fecha=n["fecha"],
                hora=n["hora"], sucursal=n["sucursal"], comprobante_sha256=n["sha"], comprobante_ref=n["ref"],
                detalle={**(c.detalle or {}), "comprobante": n["tipo"]}))
            ex.evento(s, exp_id, "RESERVACION_CONFIRMADA", actor, now, assignment_id=e and s.get(Reservacion, res_id).assignment_id,
                      reservacion_id=res_id, folio=n["folio"], evidencia="persona_portal_oficial")
            ex.evento(s, exp_id, "CITA_REGISTRADA_POR_PERSONA", actor, now, confirmacion=c.id, folio=n["folio"],
                      fecha=n["fecha"], comprobante=n["tipo"])
            ex.confirmar(s, e, actor, now, n["folio"])
            security.audit(s, user.username, "asistida_cita_registrada", e.codigo,
                           {"confirmacion": c.id, "reservacion": res_id, "folio": n["folio"], "comprobante": n["tipo"]}, ip)
            return s.get(Reservacion, res_id).assignment_id
    except IntegrityError as err:
        raise ErrorAsistida(409, "Ese folio ya está registrado en otra cita: compruébalo en el portal",
                            {"codigo": "FOLIO_DUPLICADO"}) from err


# ---------------------------------------------------------------- revocar (antes de registrar)
def revocar(db: Database, exp_id: int, user, motivo: str, now: datetime, ip: str = "") -> dict:
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorAsistida(422, "Escribe el motivo (mínimo 5 caracteres, sin datos personales)")
    actor = _actor(user)
    with db.session() as s:
        e = _exp(s, exp_id, user)
        c = _viva(s, exp_id)
        if c is None:
            raise ErrorAsistida(409, "No hay una revisión abierta que revocar")
        if not user.is_admin:
            _propia(c, user)
        tc = ConfirmacionHumana.__table__
        if s.execute(update(tc).where(tc.c.id == c.id, tc.c.estado.in_(list(VIVAS))).values(
                estado=REVOCADA, viva_expediente_id=None, revocada_en=now, revocada_por=actor,
                detalle={**(c.detalle or {}), "motivo_revocacion": motivo[:200]})).rowcount != 1:
            raise ErrorAsistida(409, "La revisión cambió mientras tanto: recarga la ficha")
        t = Reservacion.__table__
        s.execute(update(t).where(t.c.id == c.reservacion_id, t.c.estado == rs.PREPARADA, t.c.contrato == CONTRATO).values(
            estado=rs.CANCELADA, viva_expediente_id=None, error_codigo="REVISION_REVOCADA",
            error="Revisión asistida revocada antes de registrar la cita", updated_at=now))
        ex.evento(s, exp_id, "REVISION_ASISTIDA_REVOCADA", actor, now, confirmacion=c.id, motivo=motivo[:200])
        security.audit(s, user.username, "asistida_revision_revocada", e.codigo, {"confirmacion": c.id,
                                                                                 "motivo": motivo[:200]}, ip)
        s.refresh(c)
        return {"confirmacion": vista(c)}


# ---------------------------------------------------------------- anular una cita YA registrada
def anular(db: Database, exp_id: int, user, folio: str, motivo: str, declaro: bool, now: datetime,
           ip: str = "") -> dict:
    """La persona canceló la cita: un ADMINISTRADOR, tras cancelarla él mismo en el portal oficial, lo declara.
    Cauce no comprueba la cancelación con el proveedor (igual que no comprobó la cita)."""
    if not user.is_admin:
        raise ErrorAsistida(403, "Solo un administrador puede anular una cita registrada")
    motivo = (motivo or "").strip()
    if len(motivo) < 5:
        raise ErrorAsistida(422, "Escribe el motivo (mínimo 5 caracteres, sin datos personales)")
    if not declaro:
        raise ErrorAsistida(422, "Marca que cancelaste la cita en el portal oficial: sin eso no se anula",
                            {"codigo": "SIN_DECLARACION"})
    actor = _actor(user)
    folio = (folio or "").strip().upper()
    with db.session() as s:
        e = _exp(s, exp_id, user)
        c = s.scalar(select(ConfirmacionHumana).where(ConfirmacionHumana.expediente_id == exp_id,
                                                      ConfirmacionHumana.estado.in_([CITA_REGISTRADA, ANULADA]))
                     .order_by(ConfirmacionHumana.id.desc()).limit(1))
        if c is None:
            raise ErrorAsistida(409, "Este expediente no tiene una cita asistida registrada", {"codigo": "SIN_CITA"})
        if folio != (c.folio or ""):
            raise ErrorAsistida(422, "El folio no coincide con el de la cita registrada", {"codigo": "FOLIO_NO_COINCIDE"})
        if c.estado == ANULADA:
            return {"confirmacion": vista(c), "repetida": True}
        res_id, cid = c.reservacion_id, c.id
        res = s.get(Reservacion, res_id)
        if res is None or res.contrato != CONTRATO or res.estado != rs.CONFIRMADA:
            raise ErrorAsistida(409, f"La reservación de esta cita está {res.estado if res else 'ausente'}: no se anula")
    try:
        recuperacion.cancelacion_intento(db, res_id)
    except recuperacion.ErrorDiario as err:
        raise ErrorAsistida(503, "No se pudo escribir el diario de eventos críticos: la cita NO se anuló en Cauce. "
                                 "Revisa el autodiagnóstico y vuelve a intentarlo.",
                            {"codigo": "DIARIO_NO_DISPONIBLE"}) from err
    try:
        with db.session() as s:
            t, tc = Reservacion.__table__, ConfirmacionHumana.__table__
            res = s.get(Reservacion, res_id)
            evid = {**(res.evidencia or {}), "cancelada_por_persona": {"persona": actor, "en": now.isoformat() + "Z",
                                                                       "verificada_con_proveedor": False}}
            if s.execute(update(t).where(t.c.id == res_id, t.c.estado == rs.CONFIRMADA).values(
                    estado=rs.CANCELADA, viva_expediente_id=None, updated_at=now, evidencia=evid)).rowcount != 1:
                raise ErrorAsistida(409, "La cita cambió mientras tanto: recarga la ficha")
            c = s.get(ConfirmacionHumana, cid)
            s.execute(update(tc).where(tc.c.id == cid).values(
                estado=ANULADA, revocada_en=now, revocada_por=actor,
                detalle={**(c.detalle or {}), "motivo_anulacion": motivo[:200]}))
            e = s.get(Expediente, exp_id)
            ex.evento(s, exp_id, "CITA_ASISTIDA_ANULADA", actor, now, confirmacion=cid, reservacion_id=res_id,
                      folio=res.folio, motivo=motivo[:200])
            if e.estado != ex.CERRADO:
                ex.cerrar(s, e, "CANCELADO", actor, now, nota="cita asistida anulada: cancelada en el portal oficial")
            security.audit(s, user.username, "asistida_cita_anulada", e.codigo, {"confirmacion": cid,
                                                                              "motivo": motivo[:200]}, ip)
            s.flush()
            s.refresh(c)
            out = vista(c)
    finally:
        recuperacion.cancelacion_resultado(db, res_id)
    return {"confirmacion": out, "repetida": False}


# ---------------------------------------------------------------- conciliación tras restaurar un respaldo
def conciliar_folio(db: Database, res_id: int, folio: str, actor: str, now: datetime) -> dict | None:
    """La llama `recuperacion.resolver` (administrador, ya validado el folio contra el diario) cuando la cita
    asistida se registró DESPUÉS del respaldo restaurado: en la base restaurada la reservación sigue PREPARADA.
    Devuelve la vista de la reservación, o None si no es una reservación asistida en ese estado."""
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if res is None or res.contrato != CONTRATO or res.estado != rs.PREPARADA:
            return None
        c = s.scalar(select(ConfirmacionHumana).where(ConfirmacionHumana.reservacion_id == res_id)
                     .order_by(ConfirmacionHumana.id.desc()))
        e = s.get(Expediente, res.expediente_id)
        evid = {"fuente": "PERSONA_PORTAL_OFICIAL", "estado_portal": "DECLARADA_POR_PERSONA",
                "confirmacion_humana": c.id if c else None, "revisada_por": c.persona if c else None,
                "registrada_en": None, "conciliada_en": now.isoformat() + "Z", "conciliada_por": actor,
                "verificada_en": None, "verificada_con_proveedor": False, "comprobante": "CONCILIADA_TRAS_RESTAURAR"}
        t = Reservacion.__table__
        if s.execute(update(t).where(t.c.id == res_id, t.c.estado == rs.PREPARADA).values(
                estado=rs.CONFIRMADA, folio=folio, evidencia=evid, confirmada_por=(c.persona if c else actor),
                updated_at=now)).rowcount != 1:
            return None
        if c is not None and c.estado in VIVAS:
            tc = ConfirmacionHumana.__table__
            s.execute(update(tc).where(tc.c.id == c.id).values(
                estado=CITA_REGISTRADA, viva_expediente_id=None, registrada_en=now, folio=folio,
                detalle={**(c.detalle or {}), "comprobante": "CONCILIADA_TRAS_RESTAURAR", "conciliada_por": actor}))
        ex.evento(s, e.id, "RESERVACION_CONFIRMADA", actor, now, reservacion_id=res_id, folio=folio,
                  evidencia="conciliacion_asistida")
        if e.estado != ex.RES_CONFIRMADA:
            if e.estado == ex.ASIGNADO:
                ex.cambiar_estado(s, e, ex.CAPTURA, actor, now, canal="conciliacion")
            ex.confirmar(s, e, actor, now, folio)
        s.refresh(res)
        return rs.vista(res)

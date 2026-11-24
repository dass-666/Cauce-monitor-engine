"""Eslabón coincidencia -> Forms -> confirmación supervisada -> folio oficial verificado (solo LABORATORIO).

Antes de este módulo el recorrido estaba partido en dos:
  - el planificador detectaba la coincidencia (día disponible dentro de las fechas aceptables del expediente) y
    `operacion.preparar` dejaba la operación preparada con su reservación PREPARADA, fecha y sucursal;
  - Ventanilla podía lanzar Forms («Autorrellenar formulario»), pero Forms NO recibía la coincidencia y su
    resultado no volvía a la reservación: aunque la persona confirmara en la ventana, la cita no quedaba en Monitor.

Este módulo los une sin crear otro motor:

1. `operacion_viva()`: si el expediente de la atención tiene una operación preparada POR ESTE operador (elemento
   del plan OPERACION_PREPARADA con su reservación PREPARADA), el adaptador de Forms la adjunta a la solicitud.
   La fecha y la sucursal de la coincidencia viajan a Forms SOLO si el perfil aprobado del formulario las declara
   (`fecha_cita`, `sucursal_cita`): un formulario de una sola pantalla con fecha las rellena; el portal de
   laboratorio por día las pide en su segunda pantalla (calendario), que elige la persona.
2. `registrar_resultado()`: al terminar Forms, su resultado (sin valores) queda en la reservación
   (`evidencia.forms`). Si Forms rellenó y verificó todo sin enviar, la operación queda LISTA PARA CONFIRMACIÓN
   SUPERVISADA. Eso NO es una cita: la reservación sigue PREPARADA y el plan sigue en OPERACION_PREPARADA.
3. `verificar_folio()`: la persona confirmó en el portal (en la ventana de Forms) y teclea el folio dos veces.
   Monitor lo consulta por la vía AUTORIZADA del adaptador del portal (laboratorio: API por folio) y solo marca
   CONFIRMADA si el portal dice CONFIRMADA y coinciden titular (identificador), trámite, fecha y sucursal de la
   coincidencia. Si algo no coincide, no cambia nada. Después el plan se sincroniza (CONFIRMADO).
   Sin vía de verificación (portal sin API autorizada) no se confirma aquí: se usa la confirmación asistida
   (`captura/asistida.py`), que la deja como «declarada por una persona».

Garantías: ninguna detección ni relleno cuenta como cita; el diario de eventos críticos recibe el intento ANTES de
consultar al portal y el resultado después (igual que el flujo de reservaciones); el folio es único en la base
(un folio repetido va a revisión, no se confirma); en el historial solo hay estados, nombres de campo, fecha,
sucursal y folio: nunca datos de la persona.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .. import adaptadores, recuperacion
from ..adaptadores.base import OK, DatoInvalido
from ..captura import consentimiento as cs
from ..captura import expedientes as ex
from ..captura import portal
from ..captura import reservaciones as rs
from ..captura.asistida import normalizar_folio
from ..captura.formularios import contrato as ct
from ..db import Database
from ..engine import assignment as asg
from ..models import Assignment, Expediente, PlanElemento, Reservacion
from ..planificador import operacion as op
from ..planificador import plan as pl

log = logging.getLogger("monitoreo.autorrelleno")

# Nombres lógicos que un perfil de formulario puede declarar para recibir la coincidencia.
CAMPO_FECHA, CAMPO_SUCURSAL = "fecha_cita", "sucursal_cita"
ETIQUETAS = {CAMPO_FECHA: "Fecha de la cita (coincidencia)", CAMPO_SUCURSAL: "Sucursal de la cita (coincidencia)"}
# Estados de Forms en los que el formulario quedó rellenado y verificado sin que Forms enviara nada.
ESTADOS_LISTOS = ("PREPARADO", "CADUCADA", "ENVIADA_POR_PERSONA")
LISTA, SIN_PREPARAR = "LISTA_PARA_CONFIRMACION", "FORMS_SIN_PREPARAR"
# Adaptador que sabe verificar un folio de cada contrato de reservación. Lo que no esté aquí no se verifica.
ADAPTADOR_POR_CONTRATO = {"portal_laboratorio_dia": "laboratorio"}


class ErrorFolio(Exception):
    def __init__(self, http: int, codigo: str, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.codigo, self.mensaje, self.detalle = http, codigo, mensaje, detalle or {}


def _actor(user) -> str:
    return f"user:{user.username}"


# ---------------------------------------------------------------- 1. ¿hay una coincidencia preparada?
def operacion_viva(s, exp_id: int, operator_id: int | None) -> dict | None:
    """La operación preparada por ESTE operador para el expediente, con su reservación aún PREPARADA."""
    if operator_id is None:
        return None
    el = s.scalar(select(PlanElemento).where(PlanElemento.expediente_id == exp_id, PlanElemento.estado == pl.PREPARADO,
                                             PlanElemento.operador_id == operator_id,
                                             PlanElemento.reservacion_id.is_not(None)))
    if el is None or not el.fecha_asignada or not el.sucursal_asignada:
        return None
    res = s.get(Reservacion, el.reservacion_id)
    if res is None or res.estado != rs.PREPARADA or res.expediente_id != exp_id:
        return None
    return {"elemento_id": el.id, "reservacion_id": res.id, "fecha": el.fecha_asignada,
            "sucursal": el.sucursal_asignada, "tramite": el.tramite}


def campos_de(coin: dict, declarados) -> dict:
    """Lo que viaja a Forms de la coincidencia: solo lo que el perfil aprobado declara."""
    out = {}
    if CAMPO_FECHA in declarados:
        out[CAMPO_FECHA] = coin["fecha"]
    if CAMPO_SUCURSAL in declarados:
        out[CAMPO_SUCURSAL] = coin["sucursal"]
    return out


def vista(s, exp_id: int, operator_id: int | None) -> dict | None:
    """Para la tarjeta: la coincidencia preparada y lo que Forms dejó en la reservación (sin valores)."""
    coin = operacion_viva(s, exp_id, operator_id)
    if coin is None:
        el = s.scalar(select(PlanElemento).where(PlanElemento.expediente_id == exp_id,
                                                 PlanElemento.estado == pl.CONFIRMADO)
                      .order_by(PlanElemento.id.desc()))
        if el is None or not el.reservacion_id:
            return None
        res = s.get(Reservacion, el.reservacion_id)
        if res is None or res.estado != rs.CONFIRMADA or (res.evidencia or {}).get("fuente") != "PORTAL_API_FOLIO":
            return None
        return {"elemento_id": el.id, "reservacion_id": res.id, "fecha": res.fecha, "sucursal": res.sucursal,
                "estado_plan": el.estado, "estado_reservacion": res.estado, "folio": res.folio,
                "lista_para_confirmacion": False, "confirmada": True, "forms": (res.evidencia or {}).get("forms")}
    res = s.get(Reservacion, coin["reservacion_id"])
    f = (res.evidencia or {}).get("forms") or None
    return {**coin, "estado_plan": pl.PREPARADO, "estado_reservacion": res.estado, "folio": None,
            "lista_para_confirmacion": bool(f and f.get("resultado") == LISTA), "confirmada": False, "forms": f,
            "verificable": ADAPTADOR_POR_CONTRATO.get(res.contrato) is not None}


# ---------------------------------------------------------------- reintentos controlados
# Con la operación ya LISTA, el botón «Autorrellenar» se desactiva: lo siguiente es registrar el folio. Volver a
# abrir el formulario solo se permite con uno de estos motivos y hasta MAX_REINTENTOS veces; reutiliza la MISMA
# reservación (no se crea otra operación) y un reintento que salga peor no le quita a la operación su estado LISTA.
MOTIVOS_REINTENTO = {
    "VENTANA_CERRADA": "La ventana del formulario se cerró antes de que la persona confirmara",
    "TIEMPO_AGOTADO": "Se agotó el tiempo de revisión antes de que la persona confirmara",
    "DATO_CORREGIDO": "Se corrigió un dato del expediente y hay que rellenar de nuevo",
}
MAX_REINTENTOS = 2


def forms_de(s, coin: dict | None) -> dict | None:
    if coin is None:
        return None
    res = s.get(Reservacion, coin["reservacion_id"])
    return ((res.evidencia or {}).get("forms") or None) if res is not None else None


def forms_de_id(db: Database, coin: dict) -> bool:
    """¿La operación ya estaba LISTA antes de este lanzamiento? (solo entonces cuenta como reintento)."""
    with db.session() as s:
        f = forms_de(s, coin)
    return bool(f and f.get("resultado") == LISTA)


def reintento_permitido(forms: dict | None, motivo: str | None) -> tuple[bool, str, str]:
    """(permitido, codigo, mensaje). Sin operación LISTA no hace falta motivo: es un autorrellenado normal."""
    if not forms or forms.get("resultado") != LISTA:
        return True, "", ""
    if not motivo:
        return False, "OPERACION_YA_LISTA", ("Forms ya dejó lista esta operación: registra el folio. Solo si hay que "
                                             "volver a abrir el formulario, usa «Reintentar» con su motivo.")
    if motivo not in MOTIVOS_REINTENTO:
        return False, "MOTIVO_NO_VALIDO", "Elige uno de los motivos de reintento."
    if len(forms.get("reintentos") or []) >= MAX_REINTENTOS:
        return False, "REINTENTOS_AGOTADOS", (f"Ya se reintentó {MAX_REINTENTOS} veces: registra el folio o termina la "
                                              "atención sin cita y revisa con un administrador.")
    return True, "", ""


# ---------------------------------------------------------------- 2. resultado de Forms -> reservación
def listo(detalle: dict) -> bool:
    return (detalle.get("estado") in ESTADOS_LISTOS and detalle.get("campos_total", 0) > 0
            and detalle.get("campos_correctos") == detalle.get("campos_total")
            and not detalle.get("envio_por_conector"))


def registrar_resultado(db: Database, coin: dict, exp_id: int, actor: str, detalle: dict, now: datetime,
                        reintento: str | None = None) -> str:
    """Anota en la reservación (todavía PREPARADA) lo que hizo Forms. Devuelve LISTA o FORMS_SIN_PREPARAR.
    En un reintento sobre una operación ya LISTA, el resultado nuevo se añade a `reintentos`; si salió peor, la
    operación sigue LISTA (la persona pudo haber confirmado ya con el formulario anterior)."""
    resultado = LISTA if listo(detalle) else SIN_PREPARAR
    forms = {"resultado": resultado, "solicitud_id": detalle.get("solicitud_id"), "estado": detalle.get("estado"),
             "codigo": detalle.get("codigo"), "campos_correctos": detalle.get("campos_correctos"),
             "campos_total": detalle.get("campos_total"),
             "confirmaciones_coinciden": detalle.get("confirmaciones_coinciden"),
             "envio_por_conector": bool(detalle.get("envio_por_conector")),
             "envio_humano_observado": bool(detalle.get("envio_humano_observado")),
             "fecha_coincidencia": coin["fecha"], "sucursal_coincidencia": coin["sucursal"],
             "terminado_en": now.isoformat() + "Z"}
    with db.session() as s:
        res = s.get(Reservacion, coin["reservacion_id"])
        previo = ((res.evidencia or {}).get("forms") or {}) if res is not None else {}
        if previo.get("resultado") == LISTA:
            intento = {"motivo": reintento or "", "resultado": resultado, "estado": detalle.get("estado"),
                       "codigo": detalle.get("codigo"), "terminado_en": forms["terminado_en"]}
            reintentos = [*(previo.get("reintentos") or []), intento]
            forms = {**(forms if resultado == LISTA else previo), "reintentos": reintentos}
            resultado = LISTA
        if res is None or res.estado != rs.PREPARADA:
            # la reservación siguió por otro camino mientras Forms trabajaba: no se toca
            ex.evento(s, exp_id, "FORMS_RESULTADO_DESCARTADO", actor, now, reservacion_id=coin["reservacion_id"],
                      motivo=f"la reservación está {res.estado if res else 'ausente'}")
            return SIN_PREPARAR
        t = Reservacion.__table__
        s.execute(update(t).where(t.c.id == res.id, t.c.estado == rs.PREPARADA).values(
            evidencia={**(res.evidencia or {}), "forms": forms}, fecha=coin["fecha"], sucursal=coin["sucursal"],
            updated_at=now))
        ex.evento(s, exp_id, "OPERACION_LISTA_CONFIRMACION" if resultado == LISTA else "OPERACION_FORMS_SIN_PREPARAR",
                  actor, now, reservacion_id=res.id, elemento_id=coin["elemento_id"], fecha=coin["fecha"],
                  sucursal=coin["sucursal"], estado_forms=detalle.get("estado"), codigo=detalle.get("codigo"),
                  es_cita=False)
    return resultado


# ---------------------------------------------------------------- 3. folio oficial -> verificación -> CONFIRMADA
def verificar_folio(db: Database, user, res_id: int, folio: str, folio_confirmacion: str, now: datetime) -> dict:
    f1, f2 = normalizar_folio(folio), normalizar_folio(folio_confirmacion)
    if not f1 or f1 != f2:
        raise ErrorFolio(422, "FOLIOS_DISTINTOS", "Teclea el mismo folio dos veces.")
    with db.session() as s:
        res = s.get(Reservacion, res_id)
        if res is None or not (user.is_admin or (user.operator_id is not None and res.operator_id == user.operator_id)):
            raise ErrorFolio(404, "NO_ENCONTRADA", "Reservación no encontrada.")
        if res.estado == rs.CONFIRMADA and res.folio == f1:
            return {"reservacion": rs.vista(res), "repetida": True}
        if res.estado != rs.PREPARADA:
            raise ErrorFolio(409, "ESTADO_NO_PERMITIDO", f"La reservación está {res.estado}: este registro es solo para "
                             "una operación preparada con Forms.")
        el = s.scalar(select(PlanElemento).where(PlanElemento.reservacion_id == res_id,
                                                 PlanElemento.estado == pl.PREPARADO))
        if el is None or not el.fecha_asignada:
            raise ErrorFolio(409, "SIN_COINCIDENCIA", "Esta reservación no viene de una coincidencia del plan.")
        forms = (res.evidencia or {}).get("forms") or {}
        if forms.get("resultado") != LISTA:
            raise ErrorFolio(409, "FORMS_NO_PREPARO", "Forms no dejó el formulario rellenado y verificado para esta "
                             "operación: vuelve a autorrellenar o usa el flujo de siempre.")
        nombre_ad = ADAPTADOR_POR_CONTRATO.get(res.contrato)
        if nombre_ad is None:
            raise ErrorFolio(409, "SIN_VERIFICACION_AUTORIZADA", "Este portal no tiene una vía autorizada para comprobar "
                             "folios: registra la cita con la confirmación asistida (quedará como declarada por ti).")
        exp = s.get(Expediente, res.expediente_id)
        if cs.vigente(s, exp, now) is None:
            m = cs.motivo_bloqueo(s, exp, now)
            raise ErrorFolio(409, m["codigo"], m["mensaje"])
        valores = ct.valores_desde(portal.contrato(exp.modo), exp)
        identificador, tramite = valores.get("identificador"), valores.get("tramite")
        fecha, sucursal, exp_id, elemento_id = el.fecha_asignada, el.sucursal_asignada, exp.id, el.id
    ad = adaptadores.obtener(nombre_ad)
    try:
        f1 = ad.validar_folio(f1)
    except DatoInvalido as e:
        raise ErrorFolio(422, "FOLIO_INVALIDO", f"El folio no tiene el formato del portal ({e}).") from e
    # El intento queda en el diario de eventos críticos ANTES de hablar con el portal (como reservaciones.confirmar).
    try:
        recuperacion.confirmacion_intento(db, res_id)
    except recuperacion.ErrorDiario as e:
        raise ErrorFolio(503, "DIARIO_NO_DISPONIBLE", "No se pudo escribir el diario de eventos críticos: no se "
                         "consultó el portal. Avisa a un administrador.") from e
    try:
        v = ad.verificar(f1, fecha, sucursal, identificador=identificador, tramite=tramite)
        if v.resultado.tipo != OK:
            with db.session() as s:
                ex.evento(s, exp_id, "FOLIO_NO_VERIFICADO", _actor(user), now, reservacion_id=res_id, folio=f1,
                          motivo=v.resultado.tipo, codigo=v.resultado.codigo)
            raise ErrorFolio(503, "PORTAL_NO_RESPONDIO", "El portal no respondió a la verificación: no se marcó nada. "
                             "Vuelve a intentarlo más tarde.", {"tipo": v.resultado.tipo})
        comprobado = {k: bool(x) for k, x in (v.evidencia.get("comprobado") or {}).items()}
        if not v.existe or not v.coincide:
            with db.session() as s:
                ex.evento(s, exp_id, "FOLIO_RECHAZADO", _actor(user), now, reservacion_id=res_id, folio=f1,
                          existe=bool(v.existe), comprobado=comprobado)
            no = [k for k, x in comprobado.items() if not x]
            raise ErrorFolio(409, "FOLIO_NO_COINCIDE" if v.existe else "FOLIO_INEXISTENTE",
                             ("El portal no tiene ese folio." if not v.existe else
                              "El folio existe pero no corresponde a esta operación (" + ", ".join(no) + "). No se "
                              "marcó como confirmada."), {"comprobado": comprobado})
        out = _confirmar(db, res_id, f1, fecha, sucursal, nombre_ad, v, user, now)
    finally:
        recuperacion.confirmacion_resultado(db, res_id, origen="persona_forms")
    sinc = op.sincronizar(db, now)
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        out["estado_plan"] = el.estado if el else None
    out["sincronizacion"] = sinc
    return out


def _confirmar(db: Database, res_id: int, folio: str, fecha: str, sucursal: str, nombre_ad: str, v, user,
               now: datetime) -> dict:
    actor = _actor(user)
    evid = {"fuente": "PORTAL_API_FOLIO", "verificada_con_proveedor": True, "adaptador": nombre_ad,
            "estado_portal": v.estado_proveedor, "fecha_portal": v.fecha, "sucursal_portal": v.sucursal,
            "confirmada_en_portal": v.evidencia.get("confirmada_en_portal"),
            "comprobado": {k: bool(x) for k, x in (v.evidencia.get("comprobado") or {}).items()},
            "folio_tecleado_por": user.username, "verificada_en": now.isoformat() + "Z"}
    try:
        with db.session() as s:
            res = s.get(Reservacion, res_id)
            evid["forms"] = (res.evidencia or {}).get("forms")
            t = Reservacion.__table__
            if s.execute(update(t).where(t.c.id == res_id, t.c.estado == rs.PREPARADA).values(
                    estado=rs.CONFIRMADA, folio=folio, evidencia=evid, fecha=fecha, sucursal=sucursal, error=None,
                    error_codigo=None, confirmada_por=actor, viva_expediente_id=res.expediente_id,
                    updated_at=now)).rowcount != 1:
                s.refresh(res)
                raise ErrorFolio(409, "CAMBIO_CONCURRENTE", f"La reservación cambió mientras tanto (está {res.estado}).")
            s.refresh(res)
            exp = s.get(Expediente, res.expediente_id)
            ex.evento(s, exp.id, "RESERVACION_CONFIRMADA", actor, now, assignment_id=res.assignment_id,
                      reservacion_id=res.id, folio=folio, evidencia="folio_verificado_api")
            ex.confirmar(s, exp, actor, now, folio)
            aid, out = res.assignment_id, rs.vista(res)
    except IntegrityError:
        rp = {"folio": folio, "estado": v.estado_proveedor}
        dup = rs._folio_duplicado(db, res_id, rp, "PORTAL_API_FOLIO", actor, now)
        raise ErrorFolio(409, "FOLIO_DUPLICADO", "Ese folio ya pertenece a otra reservación en Monitor: queda en "
                         "verificación para un administrador.", {"reservacion": dup})
    if aid and not asg.complete_assignment(db, aid, None, now, actor):
        with db.session() as s:
            a = s.get(Assignment, aid)
            ex.evento(s, out["expediente_id"], "ASIGNACION_NO_COMPLETADA", "motor", now, assignment_id=aid,
                      reservacion_id=res_id, estado_asignacion=a.status if a else None)
    return {"reservacion": out, "repetida": False}

"""v2.1 · P1 · Planificador diario.

Un plan por fecha de jornada (`planes_diarios.fecha_jornada` UNIQUE). Cada elemento une un
expediente con lo que se acepta para él: trámite, sucursales, fechas y prioridad (1 = más urgente).

Reglas (todas se comprueban en el servidor, con el motivo en la respuesta):
- Solo entran expedientes LISTO (completos y validados). INCOMPLETO, en validación, en revisión,
  cerrados, ya confirmados o en manos de un operador se rechazan.
- El trámite del elemento debe ser el del expediente; las sucursales deben ser la del expediente o
  sus alternativas; las fechas aceptables deben ser posteriores a la jornada... o la misma, nunca pasadas.
- Un expediente no puede estar dos veces en el mismo plan (UNIQUE plan+expediente) ni vivo en dos
  planes a la vez (`activo_expediente_id` UNIQUE mientras el elemento está vivo).
- `capacidad` limita cuántos elementos vivos admite el plan (6 al principio); se puede ampliar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import security
from ..captura import expedientes as ex
from ..captura import zona
from ..models import Expediente, PlanDiario, PlanElemento

EN_ESPERA, ASIGNADO, PREPARADO = "EN_ESPERA", "ASIGNADO", "OPERACION_PREPARADA"
CONFIRMADO, RETIRADO, NO_DISPONIBLE = "CONFIRMADO", "RETIRADO", "NO_DISPONIBLE"
VIVOS = (EN_ESPERA, ASIGNADO, PREPARADO)
CAPACIDAD_INICIAL, CAPACIDAD_MAXIMA, HORIZONTE_DIAS = 6, 50, 120


class ErrorPlan(Exception):
    def __init__(self, http: int, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.http, self.mensaje, self.detalle = http, mensaje, detalle or {}


def _fecha(x) -> date:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x))
    except ValueError:
        raise ErrorPlan(422, f"Fecha no válida (AAAA-MM-DD): {str(x)[:20]}") from None


def vista_elemento(el: PlanElemento, exp: Expediente | None = None) -> dict:
    return {"id": el.id, "plan_id": el.plan_id, "expediente_id": el.expediente_id,
            "codigo": exp.codigo if exp else None, "estado": el.estado, "prioridad": el.prioridad,
            "tramite": el.tramite, "sucursales": list(el.sucursales or []),
            "fechas_aceptables": list(el.fechas_aceptables or []), "orden": el.orden,
            "evento_id": el.evento_id, "fecha_asignada": el.fecha_asignada, "sucursal_asignada": el.sucursal_asignada,
            "reservacion_id": el.reservacion_id, "explicacion": el.explicacion or {}, "version": el.version}


def vista(s, plan: PlanDiario) -> dict:
    els = list(s.scalars(select(PlanElemento).where(PlanElemento.plan_id == plan.id).order_by(PlanElemento.orden)))
    exps = {e.id: e for e in s.scalars(select(Expediente).where(Expediente.id.in_([x.expediente_id for x in els])))} if els else {}
    vivos = sum(1 for x in els if x.estado in VIVOS)
    return {"id": plan.id, "fecha_jornada": plan.fecha_jornada.isoformat(), "estado": plan.estado,
            "capacidad": plan.capacidad, "vivos": vivos, "creado_por": plan.creado_por,
            "elementos": [vista_elemento(x, exps.get(x.expediente_id)) for x in els]}


def motivo_inelegible(exp: Expediente | None) -> str | None:
    if exp is None:
        return "el expediente no existe"
    if exp.estado in ex.PREPARACION:
        return f"el expediente está incompleto o sin validar ({exp.estado})"
    if exp.estado == ex.REVISION:
        return "el expediente requiere revisión"
    if exp.estado in ex.CERRADOS:
        return f"el expediente ya no espera cita ({exp.estado})"
    if exp.estado in ex.OPERACION_ACTIVA:
        return f"un operador ya lo está atendiendo ({exp.estado})"
    if exp.estado != ex.LISTO:
        return f"el expediente está {exp.estado}"
    faltan = ex.faltantes(exp)
    if faltan:
        return "al expediente le faltan datos: " + ", ".join(sorted(faltan))[:120]
    return None


def _normalizar(exp: Expediente, d: dict, jornada: date) -> dict:
    """Valida y completa un elemento. Lanza ErrorPlan(422) si es incompatible con el expediente."""
    tramite = (d.get("tramite") or exp.tipo_tramite).strip()
    if tramite != exp.tipo_tramite:
        raise ErrorPlan(422, f"Trámite incompatible: el expediente es de {exp.tipo_tramite}")
    permitidas = [x for x in [exp.sucursal, *(exp.sucursales_alternativas or [])] if x]
    sucs = d.get("sucursales") or permitidas
    if not isinstance(sucs, list) or not sucs:
        raise ErrorPlan(422, "Indica al menos una sucursal")
    sucs = list(dict.fromkeys(str(x).strip() for x in sucs))
    ajenas = [x for x in sucs if x not in permitidas]
    if ajenas:
        raise ErrorPlan(422, "Sucursal incompatible con el expediente: " + ", ".join(ajenas)[:80],
                        {"permitidas": permitidas})
    fechas = d.get("fechas_aceptables")
    if not fechas:
        desde = _fecha(d.get("fechas_desde") or jornada)
        hasta = _fecha(d.get("fechas_hasta") or (desde + timedelta(days=30)))
        if hasta < desde:
            raise ErrorPlan(422, "El rango de fechas aceptables está invertido")
        fechas = [(desde + timedelta(days=i)).isoformat() for i in range((hasta - desde).days + 1)]
    fs = sorted({_fecha(x) for x in fechas})
    if fs[0] < jornada:
        raise ErrorPlan(422, "Hay fechas aceptables anteriores a la jornada")
    if fs[-1] > jornada + timedelta(days=HORIZONTE_DIAS):
        raise ErrorPlan(422, f"Las fechas aceptables no pueden pasar de {HORIZONTE_DIAS} días")
    prioridad = int(d.get("prioridad") or exp.prioridad or 3)
    if not 1 <= prioridad <= 5:
        raise ErrorPlan(422, "La prioridad va de 1 (más urgente) a 5")
    return {"tramite": tramite, "sucursales": sucs, "fechas_aceptables": [x.isoformat() for x in fs],
            "prioridad": prioridad}


def crear(db, fecha_jornada, usuario: str, now: datetime, capacidad: int = CAPACIDAD_INICIAL) -> dict:
    jornada = _fecha(fecha_jornada)
    if jornada < zona.hoy(now):
        raise ErrorPlan(422, "La fecha de jornada ya pasó")
    if not 1 <= int(capacidad) <= CAPACIDAD_MAXIMA:
        raise ErrorPlan(422, f"La capacidad va de 1 a {CAPACIDAD_MAXIMA}")
    try:
        with db.session() as s:
            p = PlanDiario(fecha_jornada=jornada, estado="ABIERTO", capacidad=int(capacidad), creado_por=usuario[:64],
                           created_at=now, updated_at=now)
            s.add(p)
            s.flush()
            security.audit(s, usuario, "plan_creado", jornada.isoformat(), {"capacidad": p.capacidad})
            return vista(s, p)
    except IntegrityError as e:
        raise ErrorPlan(409, "Ya existe un plan para esa jornada") from e


def obtener(db, plan_id: int) -> dict:
    with db.session() as s:
        p = s.get(PlanDiario, plan_id)
        if p is None:
            raise ErrorPlan(404, "Plan no encontrado")
        return vista(s, p)


def listar(db) -> list[dict]:
    with db.session() as s:
        return [vista(s, p) for p in s.scalars(select(PlanDiario).order_by(PlanDiario.fecha_jornada.desc()).limit(60))]


def agregar(db, plan_id: int, elementos: list[dict], usuario: str, now: datetime) -> dict:
    """Agrega elementos uno a uno: cada uno entra o se rechaza con su motivo (no es todo o nada)."""
    aceptados, rechazados = [], []
    for d in elementos:
        eid = d.get("expediente_id")
        try:
            with db.session() as s:
                p = s.get(PlanDiario, plan_id)
                if p is None:
                    raise ErrorPlan(404, "Plan no encontrado")
                if p.estado != "ABIERTO":
                    raise ErrorPlan(409, "El plan está cerrado")
                exp = s.get(Expediente, int(eid)) if eid is not None else None
                if exp is not None and s.scalar(select(PlanElemento.id).where(PlanElemento.plan_id == plan_id,
                                                                              PlanElemento.expediente_id == exp.id)):
                    raise ErrorPlan(409, "El expediente ya está en este plan")
                motivo = motivo_inelegible(exp)
                if motivo:
                    raise ErrorPlan(422, motivo)
                n = _normalizar(exp, d, p.fecha_jornada)
                # la capacidad se comprueba al final: el motivo más útil es el del propio expediente
                vivos = s.scalar(select(func.count()).select_from(PlanElemento).where(
                    PlanElemento.plan_id == plan_id, PlanElemento.estado.in_(VIVOS))) or 0
                if vivos >= p.capacidad:
                    raise ErrorPlan(409, f"El plan está completo ({p.capacidad}); amplía la capacidad para agregar más")
                orden = (s.scalar(select(func.max(PlanElemento.orden)).where(PlanElemento.plan_id == plan_id)) or 0) + 1
                el = PlanElemento(plan_id=plan_id, expediente_id=exp.id, activo_expediente_id=exp.id, estado=EN_ESPERA,
                                  orden=orden, created_at=now, updated_at=now, explicacion={}, **n)
                s.add(el)
                s.flush()
                ex.evento(s, exp.id, "PLAN_AGREGADO", usuario[:64], now, plan=p.fecha_jornada.isoformat(),
                          prioridad=n["prioridad"])
                aceptados.append(vista_elemento(el, exp))
        except ErrorPlan as e:
            if e.http == 404:
                raise
            rechazados.append({"expediente_id": eid, "motivo": e.mensaje, **e.detalle})
        except IntegrityError:
            rechazados.append({"expediente_id": eid, "motivo": "El expediente ya está en otro plan vivo o en este"})
        except (TypeError, ValueError):
            rechazados.append({"expediente_id": eid, "motivo": "Elemento no válido"})
    with db.session() as s:
        if aceptados:
            security.audit(s, usuario, "plan_elementos_agregados", str(plan_id),
                           {"aceptados": len(aceptados), "rechazados": len(rechazados)})
        return {"plan": vista(s, s.get(PlanDiario, plan_id)), "aceptados": aceptados, "rechazados": rechazados}


def candidatos(db, n: int = CAPACIDAD_INICIAL, excluir: set[int] | None = None) -> list[Expediente]:
    """Expedientes LISTO ordenados como los tomaría el plan: prioridad, antigüedad, id."""
    excluir = excluir or set()
    with db.session() as s:
        vivos = set(s.scalars(select(PlanElemento.expediente_id).where(PlanElemento.estado.in_(VIVOS))))
        out = []
        for e in s.scalars(select(Expediente).where(Expediente.estado == ex.LISTO)
                           .order_by(Expediente.prioridad, Expediente.created_at, Expediente.id)):
            if e.id in vivos or e.id in excluir or motivo_inelegible(e):
                continue
            s.expunge(e)
            out.append(e)
            if len(out) >= n:
                break
        return out


def seleccionar(db, plan_id: int, usuario: str, now: datetime, n: int | None = None,
                fechas_desde=None, fechas_hasta=None) -> dict:
    """Selección automática: llena el plan (hasta su capacidad o `n`) con los mejores candidatos."""
    with db.session() as s:
        p = s.get(PlanDiario, plan_id)
        if p is None:
            raise ErrorPlan(404, "Plan no encontrado")
        vivos = s.scalar(select(func.count()).select_from(PlanElemento).where(
            PlanElemento.plan_id == plan_id, PlanElemento.estado.in_(VIVOS))) or 0
        libres = max(0, p.capacidad - vivos)
    n = min(libres, n if n is not None else libres)
    elementos = [{"expediente_id": e.id, "fechas_desde": fechas_desde, "fechas_hasta": fechas_hasta}
                 for e in candidatos(db, n)]
    return agregar(db, plan_id, elementos, usuario, now)


def ampliar(db, plan_id: int, capacidad: int, usuario: str, now: datetime) -> dict:
    with db.session() as s:
        p = s.get(PlanDiario, plan_id)
        if p is None:
            raise ErrorPlan(404, "Plan no encontrado")
        if not p.capacidad <= int(capacidad) <= CAPACIDAD_MAXIMA:
            raise ErrorPlan(422, f"La nueva capacidad debe estar entre {p.capacidad} y {CAPACIDAD_MAXIMA}")
        anterior, p.capacidad, p.updated_at = p.capacidad, int(capacidad), now
        security.audit(s, usuario, "plan_ampliado", str(plan_id), {"de": anterior, "a": p.capacidad})
        return vista(s, p)


def retirar(db, elemento_id: int, usuario: str, now: datetime) -> dict:
    with db.session() as s:
        el = s.get(PlanElemento, elemento_id)
        if el is None:
            raise ErrorPlan(404, "Elemento no encontrado")
        if el.estado != EN_ESPERA:
            raise ErrorPlan(409, f"Solo se retira un elemento en espera (está {el.estado})")
        el.estado, el.activo_expediente_id, el.updated_at, el.version = RETIRADO, None, now, el.version + 1
        ex.evento(s, el.expediente_id, "PLAN_RETIRADO", usuario[:64], now)
        return vista_elemento(el)

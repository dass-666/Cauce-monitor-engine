"""v2.1 · P2 · Eventos normalizados y deduplicados del observador de calendario.

Entrada: lo que emite `scripts/observador_calendario.js` (líneas INICIAL, CELDA y VISTA) más el
contexto del observador (qué sucursal y trámite tiene abiertos). Salida: filas de
`observacion_eventos`:

  DIA_DISPONIBLE     un día pasó a DISPONIBLE (o ya lo estaba al abrir): es una oportunidad
  DIA_NO_DISPONIBLE  un día que estaba DISPONIBLE dejó de estarlo (el cambio «desapareció»)
  UNIDADES_REDUCIDAS sigue DISPONIBLE pero con menos unidades libres (v2.1, unidades)
  REEVALUACION       no lo emite el observador: lo crea el motor cuando una unidad ya vista vuelve a
                     quedar libre (p. ej. una operación terminó sin cita) y hay expedientes en espera
  (DIA_DISPONIBLE también se emite cuando un día disponible gana unidades: 1 -> 3)
  VISTA              la página cambió de estado: ACTIVA | SESION_CADUCADA | DESCONECTADO | CONGELADO
                     (una desconexión o una sesión caducada NUNCA se interpreta como «no hay días»)

Deduplicación: `observacion_dias` guarda el último estado conocido de cada (sucursal, trámite,
fecha) con una versión. Solo una TRANSICIÓN crea evento, con un UPDATE condicional (`estado <> nuevo`)
y una huella UNIQUE (clave|estado|versión): dos estrategias, dos observadores o dos procesos que
ven el mismo cambio producen UN evento. La segunda detección se anota en `detecciones` del mismo
evento (así se comparan las estrategias con el mismo cambio).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..models import ObservacionDia, ObservacionEvento, ObservacionVista

log = logging.getLogger("monitoreo.planificador")
ESTADOS_DIA = ("DISPONIBLE", "AGOTADO", "DESHABILITADO", "NO_HABILITADO")
ESTADOS_VISTA = ("ACTIVA", "SESION_CADUCADA", "DESCONECTADO", "CONGELADO")
DIA_DISPONIBLE, DIA_NO_DISPONIBLE, VISTA = "DIA_DISPONIBLE", "DIA_NO_DISPONIBLE", "VISTA"
UNIDADES_REDUCIDAS, REEVALUACION = "UNIDADES_REDUCIDAS", "REEVALUACION"
OPORTUNIDADES = (DIA_DISPONIBLE, REEVALUACION)
NUEVO, NO_APLICA, DESAPARECIDO = "NUEVO", "NO_APLICA", "DESAPARECIDO"


def _ts(ms) -> datetime:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _huella(*partes) -> str:
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()


def _evidencia(ev) -> dict:
    if not isinstance(ev, dict):
        return {}
    out = {}
    for k in ("html", "sha256", "imagen", "caja"):
        if k in ev and ev[k] is not None:
            out[k] = ev[k][:300] if isinstance(ev[k], str) else ev[k]
    return out


def vista_evento(e: ObservacionEvento) -> dict:
    return {"id": e.id, "tipo": e.tipo, "sucursal": e.sucursal, "tramite": e.tramite, "fecha": e.fecha,
            "estado_anterior": e.estado_anterior, "estado": e.estado, "estrategia": e.estrategia,
            "observador": e.observador, "detectado_en": e.detectado_en.isoformat() + "Z",
            "registrado_en": e.registrado_en.isoformat() + "Z", "evidencia": e.evidencia or {},
            "detecciones": e.detecciones or {}, "consumo": e.consumo, "plan_elemento_id": e.plan_elemento_id,
            "explicacion": e.explicacion or {}, "unidades": e.unidades, "unidades_anteriores": e.unidades_anteriores}


def _anotar_deteccion(s, clave: str, estado: str, estrategia: str, ts: datetime) -> None:
    e = s.scalar(select(ObservacionEvento).where(ObservacionEvento.clave == clave, ObservacionEvento.estado == estado)
                 .order_by(ObservacionEvento.id.desc()).limit(1))
    if e is not None and estrategia not in (e.detecciones or {}):
        e.detecciones = {**(e.detecciones or {}), estrategia: ts.isoformat() + "Z"}


def efectivas(libres: int | None, estado: str = "DISPONIBLE") -> int:
    """Unidades con las que se cuenta: las que mostró la página; si solo se vio el color, 1."""
    if estado != "DISPONIBLE":
        return 0
    return max(1, int(libres)) if libres is not None else 1


def _dia(s, ctx: dict, fecha: str, estado: str, libres, estrategia: str, ts: datetime, evidencia: dict,
         now: datetime) -> ObservacionEvento | None:
    if estado not in ESTADOS_DIA or not isinstance(fecha, str) or len(fecha) != 10:
        return None                                   # DESCONOCIDO (visual sin clasificar) u otra cosa: se ignora
    try:
        lib = int(libres) if estado == "DISPONIBLE" and libres is not None and int(libres) > 0 else None
    except (TypeError, ValueError):
        lib = None
    if estado != "DISPONIBLE":
        lib = 0
    clave = f"{ctx['sucursal']}|{ctx['tramite']}|{fecha}"
    t = ObservacionDia.__table__
    fila = s.execute(select(t.c.estado, t.c.libres, t.c.version).where(t.c.clave == clave)).first()
    anterior = ant_lib = None
    if fila is None:
        try:
            with s.begin_nested():
                s.add(ObservacionDia(clave=clave, sucursal=ctx["sucursal"], tramite=ctx["tramite"], fecha=fecha,
                                     estado=estado, libres=lib, version=1, actualizado_en=now, fuente=estrategia))
            version = 1
        except IntegrityError:
            fila = s.execute(select(t.c.estado, t.c.libres, t.c.version).where(t.c.clave == clave)).first()
            if fila is None:
                return None
        else:
            if estado != "DISPONIBLE":
                return None
    if fila is not None:
        anterior, ant_lib, v = fila
        if lib is None:                    # la estrategia no ve cantidades: conserva la última conocida
            lib = ant_lib if anterior == "DISPONIBLE" else None
        if anterior == estado and lib == ant_lib:
            _anotar_deteccion(s, clave, estado, estrategia, ts)
            return None
        n = s.execute(update(t).where(t.c.clave == clave, t.c.version == v)
                      .values(estado=estado, libres=lib, version=v + 1, actualizado_en=now, fuente=estrategia)).rowcount
        if n != 1:                                     # otro proceso lo cambió a la vez: lo registra él
            _anotar_deteccion(s, clave, estado, estrategia, ts)
            return None
        version = v + 1
        if estado != "DISPONIBLE" and anterior != "DISPONIBLE":
            return None                                # p. ej. AGOTADO -> DESHABILITADO: no cambia la oportunidad
    if estado == "DISPONIBLE":
        mas = anterior != "DISPONIBLE" or efectivas(lib) > efectivas(ant_lib)
        tipo = DIA_DISPONIBLE if mas else UNIDADES_REDUCIDAS
    else:
        tipo = DIA_NO_DISPONIBLE
    e = ObservacionEvento(huella=_huella(clave, estado, lib, version), tipo=tipo, clave=clave, sucursal=ctx["sucursal"],
                          tramite=ctx["tramite"], fecha=fecha, estado_anterior=anterior, estado=estado,
                          estrategia=estrategia, observador=ctx["observador"][:64], detectado_en=ts, registrado_en=now,
                          evidencia=evidencia, detecciones={estrategia: ts.isoformat() + "Z"},
                          consumo=NUEVO if tipo == DIA_DISPONIBLE else NO_APLICA, explicacion={},
                          unidades=lib, unidades_anteriores=ant_lib if anterior == "DISPONIBLE" else None)
    try:
        with s.begin_nested():
            s.add(e)
    except IntegrityError:
        return None
    if tipo == DIA_NO_DISPONIBLE:
        # Un DISPONIBLE anterior que aún no se atendió ya no es una oportunidad.
        for viejo in s.scalars(select(ObservacionEvento).where(ObservacionEvento.clave == clave,
                                                                ObservacionEvento.tipo == DIA_DISPONIBLE,
                                                                ObservacionEvento.consumo == NUEVO)):
            viejo.consumo = DESAPARECIDO
            viejo.explicacion = {"motivo": "el día dejó de estar disponible antes de atenderse", "evento": None}
    return e


def _vista(s, ctx: dict, estado: str, estrategia: str, ts: datetime, detalle: str, now: datetime):
    if estado not in ESTADOS_VISTA:
        return None
    clave = f"{ctx['observador']}|{estrategia}"[:96]
    t = ObservacionVista.__table__
    actual = s.get(ObservacionVista, clave)
    if actual is None:
        try:
            with s.begin_nested():
                s.add(ObservacionVista(clave=clave, estado=estado, version=1, actualizado_en=now))
            version, anterior = 1, None
        except IntegrityError:
            return None
    else:
        if actual.estado == estado:
            return None
        anterior = actual.estado
        if s.execute(update(t).where(t.c.clave == clave, t.c.estado == anterior)
                     .values(estado=estado, version=t.c.version + 1, actualizado_en=now)).rowcount != 1:
            return None
        version = actual.version + 1
    e = ObservacionEvento(huella=_huella("vista", clave, estado, version), tipo=VISTA, clave=clave,
                          sucursal=ctx["sucursal"], tramite=ctx["tramite"], fecha=None, estado_anterior=anterior,
                          estado=estado, estrategia=estrategia[:12], observador=ctx["observador"][:64], detectado_en=ts,
                          registrado_en=now, evidencia={"detalle": str(detalle or "")[:200]},
                          detecciones={estrategia: ts.isoformat() + "Z"}, consumo=NO_APLICA, explicacion={})
    try:
        with s.begin_nested():
            s.add(e)
    except IntegrityError:
        return None
    return e


def _libres(d: dict, estrategia: str):
    """Solo la estrategia DOM lee cantidades; la de imagen ve colores (sus «libres» se ignoran)."""
    return None if estrategia == "visual" else d.get("libres")


def registrar(db, obs: dict, ctx: dict, now: datetime) -> list[dict]:
    """Registra una línea del observador. Devuelve los eventos NUEVOS (ya deduplicados)."""
    tipo, estr = obs.get("tipo"), str(obs.get("estrategia") or "")[:12]
    ts = _ts(obs.get("ts_ms"))
    nuevos: list[ObservacionEvento] = []
    with db.session() as s:
        if tipo == "INICIAL":
            for c in obs.get("celdas") or []:
                e = _dia(s, ctx, c.get("fecha"), c.get("estado"), _libres(c, estr), estr, ts, {"inicial": True}, now)
                if e is not None:
                    nuevos.append(e)
        elif tipo == "CELDA":
            e = _dia(s, ctx, obs.get("fecha"), obs.get("estado"), _libres(obs, estr), estr, ts,
                     _evidencia(obs.get("evidencia")), now)
            if e is not None:
                nuevos.append(e)
        elif tipo == "VISTA":
            e = _vista(s, ctx, obs.get("estado"), estr, ts, obs.get("detalle"), now)
            if e is not None:
                nuevos.append(e)
        s.flush()
        out = [vista_evento(e) for e in nuevos]
    for e in out:
        log.info("observación: %s %s %s %s (%s)", e["tipo"], e["sucursal"], e["fecha"] or "", e["estado"], e["estrategia"])
    return out


def listar(db, desde_id: int = 0, limite: int = 200) -> list[dict]:
    with db.session() as s:
        return [vista_evento(e) for e in s.scalars(select(ObservacionEvento).where(ObservacionEvento.id > desde_id)
                                                   .order_by(ObservacionEvento.id).limit(limite))]

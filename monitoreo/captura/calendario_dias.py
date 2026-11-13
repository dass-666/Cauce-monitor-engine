"""Calendario POR DÍA del motor (v1.6): qué sabe el motor de cada día y desde cuándo.

Dos mecanismos para enterarse de los cambios del portal de PRUEBA (se comparan en el laboratorio):
  A. EVENTOS    el portal empuja cada cambio (text/event-stream); ver `eventos_portal.py`.
  B. PERIODICO  el motor pide el calendario completo cada N segundos (conector `portal_dias`).
Ambos terminan aquí, en `aplicar()`, que:
  - descarta lo que llega FUERA DE ORDEN (versión del día menor o igual a la conocida);
  - clasifica el cambio (REAPERTURA, AGOTAMIENTO, NUEVA_FECHA, CANCELACION_DEVUELVE,
    DESHABILITADA, REHABILITADA, DISPONIBILIDAD_EXPIRADA, RETIRADA, CAMBIO_UNIDADES);
  - guarda cuándo lo confirmó el portal (no cuándo lo miró el motor).

Regla de oro: un error de conexión NO es "no hay disponibilidad". Si el calendario no se pudo
verificar, los días se muestran como DESCONOCIDO con su último valor ALMACENADO y la hora real de
la última verificación; el conector se niega a publicar datos sin verificar (no se inventa nada).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import CalendarioCambio, CalendarioDia, CalendarioFuente
from . import zona

DISPONIBLE, AGOTADO, DESHABILITADO, NO_HABILITADO = "DISPONIBLE", "AGOTADO", "DESHABILITADO", "NO_HABILITADO"
PENDIENTE, DESCONOCIDO = "PENDIENTE_ACTUALIZACION", "DESCONOCIDO"
EVENTOS, PERIODICO = "EVENTOS", "PERIODICO"
# Cambios que significan "hay algo nuevo que ofrecer" (los que interesan al operador)
APERTURAS = ("REAPERTURA", "NUEVA_FECHA", "CANCELACION_DEVUELVE", "REHABILITADA")


class CalendarioNoVerificado(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


def fuente(s: Session, source_id: int, mecanismo: str | None = None) -> CalendarioFuente:
    f = s.get(CalendarioFuente, source_id)
    if f is None:
        # El oyente de eventos (otro hilo) y la consulta periódica pueden crearla a la vez: se crea
        # dentro de un punto de guardado y, si otro ganó, se usa la suya (defecto hallado en v1.6).
        try:
            with s.begin_nested():
                f = CalendarioFuente(source_id=source_id, mecanismo=mecanismo or PERIODICO, conectado=False,
                                     ultima_secuencia=0, eventos_recibidos=0, fuera_de_orden=0,
                                     resincronizaciones=0, reconexiones=0)
                s.add(f)
                s.flush()
        except IntegrityError:
            f = s.get(CalendarioFuente, source_id, populate_existing=True)
    if mecanismo:
        f.mecanismo = mecanismo
    return f


def clasificar(antes: str | None, despues: str, causa: str | None, libres_antes: int, libres_despues: int,
               expiro: bool) -> str | None:
    if antes == despues:
        return "CAMBIO_UNIDADES" if libres_antes != libres_despues else None
    if despues == DESHABILITADO:
        return "DESHABILITADA"
    if antes == DESHABILITADO:
        return "REHABILITADA"
    if despues == NO_HABILITADO:
        return "RETIRADA"
    if antes in (None, NO_HABILITADO):
        return "NUEVA_FECHA" if despues == DISPONIBLE else "NUEVA_FECHA_SIN_CUPO"
    if antes == AGOTADO and despues == DISPONIBLE:
        return "CANCELACION_DEVUELVE" if causa in ("CANCELACION", "REPROGRAMADA") else "REAPERTURA"
    if antes == DISPONIBLE and despues == AGOTADO:
        return "DISPONIBILIDAD_EXPIRADA" if (causa == "EXPIRACION" or expiro) else "AGOTAMIENTO"
    return "CAMBIO_ESTADO"


def aplicar(s: Session, source_id: int, dias: list[dict], mecanismo: str, now: datetime, completo: bool,
            ts_portal: datetime | None = None, hoy_portal: str | None = None, zona_portal: str | None = None) -> dict:
    """Aplica una lista de días (completa si viene de una consulta, o uno solo si viene de un
    evento). Devuelve {"cambios": [...], "fuera_de_orden": n, "linea_base": bool}."""
    f = fuente(s, source_id)
    if zona_portal:
        f.zona_horaria = str(zona_portal)[:64]
    existentes = {d.clave: d for d in s.scalars(select(CalendarioDia).where(CalendarioDia.source_id == source_id))}
    linea_base = completo and not existentes
    cambios, fuera = [], 0
    vistos = set()
    for x in dias:
        k = x["clave"]
        vistos.add(k)
        d = existentes.get(k)
        ver = int(x.get("version") or 0)
        hasta = zona.parse_iso(x.get("disponible_hasta"))
        if d is not None and ver <= d.version_portal:
            if ver < d.version_portal:
                fuera += 1                       # llegó tarde: se ignora (el estado conocido es más nuevo)
            else:
                d.verificado_en = now            # misma versión: sigue igual, pero ya está confirmado
            continue
        antes, libres_antes = (d.estado, d.libres) if d else (None, 0)
        expiro = bool(d and d.disponible_hasta and d.disponible_hasta <= now)
        if d is None:
            d = CalendarioDia(source_id=source_id, clave=k, region=str(x.get("region", "")).upper(),
                              sucursal=x["sucursal"], sucursal_nombre=x.get("sucursal_nombre", ""), tramite=x["tramite"],
                              fecha=x["fecha"], estado=x["estado"], libres=0, capacidad=0, version_portal=0,
                              verificado_en=now, cambiado_en=now)
            s.add(d)
        d.estado, d.libres, d.capacidad = x["estado"], int(x.get("libres") or 0), int(x.get("capacidad") or 0)
        d.version_portal, d.disponible_hasta, d.verificado_en = ver, hasta, now
        d.ultima_causa = (x.get("causa") or None)
        tipo = clasificar(antes, d.estado, x.get("causa"), libres_antes, d.libres, expiro)
        if tipo:
            d.cambiado_en = now
        if tipo and not linea_base:
            cambios.append(_cambio(s, source_id, tipo, d, antes, mecanismo, now, ts_portal or zona.parse_iso(x.get("ts")),
                                   {"causa": x.get("causa")} if x.get("causa") else {}))
    if completo and not linea_base:
        limite = hoy_portal or zona.hoy(now, f.zona_horaria).isoformat()
        for k, d in existentes.items():
            if k not in vistos and d.estado != NO_HABILITADO and d.fecha > limite:
                antes = d.estado
                d.estado, d.libres, d.verificado_en, d.cambiado_en = NO_HABILITADO, 0, now, now
                cambios.append(_cambio(s, source_id, "RETIRADA", d, antes, mecanismo, now, ts_portal, {}))
    f.fuera_de_orden += fuera
    if fuera:
        s.add(CalendarioCambio(source_id=source_id, tipo="FUERA_DE_ORDEN", mecanismo=mecanismo, detectado_en=now,
                               detalle={"descartadas": fuera}))
    return {"cambios": cambios, "fuera_de_orden": fuera, "linea_base": linea_base}


def _cambio(s, source_id, tipo, d: CalendarioDia, antes, mecanismo, now, ts_portal, detalle) -> dict:
    c = CalendarioCambio(source_id=source_id, tipo=tipo, clave=d.clave, sucursal=d.sucursal, tramite=d.tramite,
                         fecha=d.fecha, antes=antes, despues=d.estado, libres=d.libres, version_portal=d.version_portal,
                         mecanismo=mecanismo, ts_portal=ts_portal, detectado_en=now, detalle=detalle)
    s.add(c)
    lat = round((now - ts_portal).total_seconds() * 1000, 1) if ts_portal else None
    return {"tipo": tipo, "clave": d.clave, "antes": antes, "despues": d.estado, "latencia_ms": lat}


def marcar_ok(s: Session, source_id: int, mecanismo: str | None, now: datetime, secuencia: int | None = None,
              conectado: bool | None = None) -> None:
    f = fuente(s, source_id, mecanismo)
    f.confirmado_en, f.ultimo_intento_en = now, now
    if secuencia is not None:
        f.ultima_secuencia = max(f.ultima_secuencia or 0, int(secuencia))
    if conectado is not None:
        f.conectado = conectado


def marcar_error(s: Session, source_id: int, codigo: str, mensaje: str, now: datetime, mecanismo: str | None = None,
                 registrar: bool = True) -> None:
    f = fuente(s, source_id, mecanismo)
    nuevo = f.ultimo_error_en is None or (f.confirmado_en and f.ultimo_error_en < f.confirmado_en) \
        or f.ultimo_error_codigo != codigo
    f.ultimo_intento_en, f.ultimo_error_en = now, now
    f.ultimo_error_codigo, f.ultimo_error = codigo[:32], mensaje[:500]
    if codigo in ("DESCONEXION", "SESION_CADUCADA"):
        f.conectado = False
    if registrar and nuevo:
        s.add(CalendarioCambio(source_id=source_id, tipo=codigo[:32], mecanismo=mecanismo or f.mecanismo,
                               detectado_en=now, detalle={"mensaje": mensaje[:200]}))


def sin_verificar(f: CalendarioFuente | None) -> bool:
    """True si el último intento falló después de la última verificación buena (o nunca hubo una)."""
    if f is None or f.confirmado_en is None:
        return True
    return bool(f.ultimo_error_en and f.ultimo_error_en >= f.confirmado_en)


def estado_mostrado(d: CalendarioDia, f: CalendarioFuente | None, now: datetime, frescura_s: float,
                    hoy: str) -> dict:
    """Lo que ve el operador: estado + si es información ACTUAL o ALMACENADA."""
    if d.fecha <= hoy:
        return {"estado": NO_HABILITADO, "origen": "ACTUAL", "almacenado": d.estado}
    if sin_verificar(f):
        return {"estado": DESCONOCIDO, "origen": "ALMACENADO", "almacenado": d.estado}
    ref = max(d.verificado_en, f.confirmado_en) if f and f.confirmado_en else d.verificado_en
    if (now - ref).total_seconds() > frescura_s:
        return {"estado": PENDIENTE, "origen": "ALMACENADO", "almacenado": d.estado}
    if d.estado == DISPONIBLE and d.disponible_hasta and d.disponible_hasta <= now:
        return {"estado": AGOTADO, "origen": "ACTUAL", "almacenado": d.estado}     # venció y aún no llega el aviso
    return {"estado": d.estado, "origen": "ACTUAL", "almacenado": d.estado}


# ---------------------------------------------------------------- consulta periódica (mecanismo B)
def consultar_portal(base: str, token: str, timeout: float, allowed_hosts, params: dict | None = None,
                     client: httpx.Client | None = None) -> dict:
    """GET /api/dias. Lanza CalendarioNoVerificado si no hay respuesta válida (NUNCA devuelve
    una lista vacía en lugar de un error)."""
    from ..connectors.http_api import USER_AGENT, check_url
    check_url(base, allowed_hosts)
    h = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        if client is not None:
            r = client.get(base + "/api/dias", params=params, headers=h, timeout=timeout)
        else:
            r = httpx.get(base + "/api/dias", params=params, headers=h, timeout=timeout, trust_env=False)
    except httpx.HTTPError as e:
        raise CalendarioNoVerificado("DESCONEXION", f"el portal no responde ({e.__class__.__name__})") from e
    if r.status_code == 401:
        raise CalendarioNoVerificado("SESION_CADUCADA", "el portal rechazó la credencial")
    if r.status_code != 200:
        raise CalendarioNoVerificado("DESCONEXION", f"el portal respondió HTTP {r.status_code}")
    try:
        j = r.json()
        if not isinstance(j.get("dias"), list):
            raise ValueError("sin lista de días")
        return j
    except ValueError as e:
        raise CalendarioNoVerificado("RESPUESTA_INVALIDA", f"respuesta con formato inesperado: {e}") from e


# ---------------------------------------------------------------- vista para el panel y el operador
def vista(s: Session, now: datetime, frescura_s: float, region: str | None = None, sucursal: str | None = None,
          tramite: str | None = None, desde: str | None = None, hasta: str | None = None) -> dict:
    from ..api.serializers import iso
    q = select(CalendarioDia)
    if region:
        q = q.where(CalendarioDia.region == region.upper())
    if sucursal:
        q = q.where(CalendarioDia.sucursal == sucursal)
    if tramite:
        q = q.where(CalendarioDia.tramite == tramite)
    if desde:
        q = q.where(CalendarioDia.fecha >= desde)
    if hasta:
        q = q.where(CalendarioDia.fecha <= hasta)
    fuentes = {f.source_id: f for f in s.scalars(select(CalendarioFuente))}
    hoy = zona.hoy(now).isoformat()
    hoy_f = {sid: zona.hoy(now, f.zona_horaria).isoformat() for sid, f in fuentes.items()}
    sucs: dict[str, dict] = {}
    for d in s.scalars(q.order_by(CalendarioDia.sucursal, CalendarioDia.tramite, CalendarioDia.fecha)):
        f = fuentes.get(d.source_id)
        em = estado_mostrado(d, f, now, frescura_s, hoy_f.get(d.source_id, hoy))
        su = sucs.setdefault(d.sucursal, {"sucursal": d.sucursal, "nombre": d.sucursal_nombre, "region": d.region,
                                          "source_id": d.source_id, "tramites": {}})
        su["tramites"].setdefault(d.tramite, []).append({
            "fecha": d.fecha, "estado": em["estado"], "origen": em["origen"], "almacenado": em["almacenado"],
            "libres": d.libres if em["origen"] == "ACTUAL" else None, "libres_almacenadas": d.libres,
            "version": d.version_portal, "verificado_en": iso(d.verificado_en), "cambiado_en": iso(d.cambiado_en),
            "disponible_hasta": iso(d.disponible_hasta)})
    out_f = []
    for sid, f in fuentes.items():
        out_f.append({"source_id": sid, "mecanismo": f.mecanismo, "conectado": f.conectado,
                      "verificado_en": iso(f.confirmado_en), "ultimo_intento_en": iso(f.ultimo_intento_en),
                      "sin_verificar": sin_verificar(f), "ultimo_error_codigo": f.ultimo_error_codigo,
                      "ultimo_error": f.ultimo_error if sin_verificar(f) else None,
                      "segundos_desde_verificacion": round((now - f.confirmado_en).total_seconds(), 1)
                      if f.confirmado_en else None, "eventos_recibidos": f.eventos_recibidos,
                      "fuera_de_orden": f.fuera_de_orden, "resincronizaciones": f.resincronizaciones,
                      "reconexiones": f.reconexiones, "zona_horaria": f.zona_horaria,
                      "hoy": hoy_f.get(sid)})
    return {"zona_horaria": zona.nombre(), "hoy": hoy, "ahora": iso(now), "frescura_s": frescura_s,
            "fuentes": out_f, "sucursales": list(sucs.values())}


def cambios_desde(s: Session, desde_id: int = 0, limite: int = 200) -> list[dict]:
    from ..api.serializers import iso
    q = (select(CalendarioCambio).where(CalendarioCambio.id > desde_id).order_by(CalendarioCambio.id).limit(limite))
    return [{"id": c.id, "tipo": c.tipo, "clave": c.clave, "sucursal": c.sucursal, "tramite": c.tramite,
             "fecha": c.fecha, "antes": c.antes, "despues": c.despues, "libres": c.libres, "mecanismo": c.mecanismo,
             "ts_portal": iso(c.ts_portal), "detectado_en": iso(c.detectado_en), "detalle": c.detalle}
            for c in s.scalars(q)]


def resumen_recursos(s: Session, source_id: int, now: datetime) -> list[dict]:
    """Agregado por (sucursal, trámite) para el núcleo del motor: unidades libres en días futuros."""
    f = s.get(CalendarioFuente, source_id)
    hoy = zona.hoy(now, f.zona_horaria if f else None).isoformat()
    agg: dict[tuple, dict] = {}
    for d in s.scalars(select(CalendarioDia).where(CalendarioDia.source_id == source_id, CalendarioDia.fecha > hoy)):
        a = agg.setdefault((d.sucursal, d.tramite), {"sucursal": d.sucursal, "nombre": d.sucursal_nombre,
                                                     "region": d.region, "tramite": d.tramite, "capacidad": 0,
                                                     "libres": 0, "dias": 0, "primero": None})
        a["capacidad"] += d.capacidad
        vigente = d.estado == DISPONIBLE and not (d.disponible_hasta and d.disponible_hasta <= now)
        if vigente and d.libres > 0:
            a["libres"] += d.libres
            a["dias"] += 1
            a["primero"] = min(a["primero"] or d.fecha, d.fecha)
    return sorted(agg.values(), key=lambda x: (x["sucursal"], x["tramite"]))


def purgar_cambios(s: Session, antes_de: datetime) -> int:
    from sqlalchemy import delete
    return s.execute(delete(CalendarioCambio).where(CalendarioCambio.detectado_en < antes_de)).rowcount or 0


__all__ = ["aplicar", "marcar_ok", "marcar_error", "vista", "cambios_desde", "resumen_recursos", "consultar_portal",
           "CalendarioNoVerificado", "estado_mostrado", "sin_verificar", "timedelta"]

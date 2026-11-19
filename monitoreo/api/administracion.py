"""RC1 · Mesa de control del administrador: panel, importación por lotes, expedientes y planificación.

Todo es SOLO para administradores y SOLO fuera de producción (como la captura de laboratorio). No crea
un segundo camino para nada: la importación usa `expedientes.crear`, la planificación `plan.agregar`
(capacidad, compatibilidad, duplicados y propiedad de siempre), el consentimiento su API de la v2.2 y la
operación de una coincidencia el flujo de Ventanilla de la v2.3 (folio comprobado en el portal).
"""
from __future__ import annotations

import base64
import binascii
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ..captura import consentimiento as cs
from ..captura import expedientes as ex
from ..captura import importacion as im
from ..captura import zona
from ..db import Database, utcnow
from ..models import (Atencion, Expediente, Importacion, ImportacionFila, ObservacionDia, ObservacionEvento,
                      ObservacionVista, PlanDiario, PlanElemento, Reservacion)
from ..planificador import plan as pl
from .deps import CurrentUser, get_db, require_admin

router = APIRouter(prefix="/api/admin")

# Situación de un expediente en el flujo administrativo, de menos a más avanzada. Distingue lo que
# está REGISTRADO, lo que está LISTO para planificar, una COINCIDENCIA (día visto, sin reservar) y una
# CITA CONFIRMADA (folio comprobado en el portal). Una coincidencia nunca es una cita.
SITUACIONES = {
    "INCOMPLETO": "Registrado · le faltan datos",
    "SIN_CONSENTIMIENTO": "Registrado · datos completos, falta consentimiento",
    "REVISION": "En revisión (posible duplicado u otro problema)",
    "LISTO": "Listo para planificar",
    "EN_PLAN": "En el plan · esperando un día compatible",
    "COINCIDENCIA": "Coincidencia de fecha (aún NO es cita)",
    "EN_OPERACION": "Operación en curso (sin confirmar)",
    "VERIFICACION": "Confirmación incierta · bloqueada hasta conciliar con el portal",
    "CONFIRMADA": "Cita confirmada con folio",
    "REVOCADO": "Consentimiento revocado",
    "CERRADO": "Cerrado",
}


class ArchivoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archivo: str = Field(min_length=1, max_length=200)
    contenido_b64: str = Field(min_length=1, max_length=7_200_000)
    hoja: str | None = Field(default=None, max_length=64)
    mapeo: dict[str, str] | None = Field(default=None, max_length=im.MAX_COLUMNAS)
    formato_fecha: str = Field(default="DMA", pattern="^(DMA|MDA)$")


class AlPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expediente_ids: list[int] = Field(min_length=1, max_length=pl.CAPACIDAD_MAXIMA)
    fechas_desde: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    fechas_hasta: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    capacidad: int | None = Field(default=None, ge=1, le=pl.CAPACIDAD_MAXIMA)


def _bytes(body: ArchivoIn) -> bytes:
    try:
        return base64.b64decode(body.contenido_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "El contenido del archivo no llegó bien (base64 no válido)") from None


def _err(e: im.ErrorImportacion):
    raise HTTPException(e.http, {"mensaje": e.mensaje, **e.detalle} if e.detalle else e.mensaje)


# ====================================================================== importación
@router.post("/importaciones/previsualizar")
def previsualizar(body: ArchivoIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return im.previsualizar(db, body.archivo, _bytes(body), utcnow(), body.hoja, body.mapeo, body.formato_fecha)
    except im.ErrorImportacion as e:
        _err(e)


@router.post("/importaciones", status_code=201)
def importar(body: ArchivoIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return im.importar(db, body.archivo, _bytes(body), user.username, utcnow(), body.hoja, body.mapeo,
                           body.formato_fecha)
    except im.ErrorImportacion as e:
        _err(e)


@router.get("/importaciones")
def lotes(user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    return {"lotes": im.listar(db)}


@router.get("/importaciones/plantilla.xlsx")
def plantilla(user: CurrentUser = Depends(require_admin)):
    from ..captura.xlsx_escritura import plantilla as pt
    return Response(pt(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_clientes_cauce.xlsx"'})


@router.get("/importaciones/{lote_id}")
def lote(lote_id: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        return im.lote_detalle(db, lote_id)
    except im.ErrorImportacion as e:
        _err(e)


@router.get("/importaciones/{lote_id}/reporte.csv")
def reporte(lote_id: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    try:
        texto = im.reporte_csv(db, lote_id)
    except im.ErrorImportacion as e:
        _err(e)
    return Response("﻿" + texto, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="reporte_lote_{lote_id}.csv"'})


# ====================================================================== situación de cada expediente
def _elementos_vivos(s) -> dict[int, PlanElemento]:
    return {el.expediente_id: el for el in s.scalars(select(PlanElemento).where(PlanElemento.estado.in_(pl.VIVOS)))}


def _situacion(s, e: Expediente, el: PlanElemento | None, now, cache: dict) -> tuple[str, dict]:
    """-> (situación, detalle). `detalle` explica el bloqueo o la coincidencia (sin datos personales)."""
    det: dict = {}
    res = cache["reservas"].get(e.id)
    if e.estado == ex.RES_CONFIRMADA or (res and res.estado == "CONFIRMADA"):
        conf = res if res and res.estado == "CONFIRMADA" else None
        if conf:
            det.update(folio=conf.folio, fecha_confirmada=conf.fecha, sucursal_confirmada=conf.sucursal)
        return "CONFIRMADA", det
    if e.estado == ex.CERRADO:
        return ("REVOCADO" if e.motivo_cierre == cs.MOTIVO_CIERRE else "CERRADO"), {"motivo": e.motivo_cierre}
    if res and res.estado == "EN_VERIFICACION":
        return "VERIFICACION", {"reservacion": res.id}
    if el is not None:
        if el.estado == pl.ASIGNADO:
            return "COINCIDENCIA", {"elemento": el.id, "fecha": el.fecha_asignada, "sucursal": el.sucursal_asignada}
        if el.estado == pl.PREPARADO:
            return "EN_OPERACION", {"elemento": el.id, "fecha": el.fecha_asignada, "sucursal": el.sucursal_asignada}
        return "EN_PLAN", {"elemento": el.id, "plan": el.plan_id}
    if e.estado in ex.OPERACION_ACTIVA:
        return "EN_OPERACION", {}
    if e.estado == ex.REVISION:
        return "REVISION", {"motivo": e.ultimo_error}
    if e.estado == ex.LISTO:
        return "LISTO", {}
    faltan = ex.faltantes(e)
    if faltan:
        return "INCOMPLETO", {"faltan": faltan}
    if cs.exigido() and cs.vigente(s, e, now) is None:
        rev = cs.revocado(s, e.id)
        return ("REVOCADO" if rev else "SIN_CONSENTIMIENTO"), {"faltan": ["consentimiento"]}
    return "INCOMPLETO", {"faltan": []}


def _cache(s) -> dict:
    reservas: dict[int, Reservacion] = {}
    rango = {"CONFIRMADA": 2, "EN_VERIFICACION": 1}
    for r in s.scalars(select(Reservacion).order_by(Reservacion.id)):
        cur = reservas.get(r.expediente_id)
        if cur is None or rango.get(r.estado, 0) >= rango.get(cur.estado, 0):
            reservas[r.expediente_id] = r
    lotes = dict(s.execute(select(ImportacionFila.expediente_id, Importacion.codigo).join(
        Importacion, Importacion.id == ImportacionFila.importacion_id).where(
        ImportacionFila.resultado == im.CREADO)).all())
    return {"reservas": reservas, "lotes": lotes}


def _fila(s, e: Expediente, el, now, cache) -> dict:
    sit, det = _situacion(s, e, el, now, cache)
    v = ex.vista(s, e, completa=False)
    vig = cs.vigente(s, e, now) if cs.exigido() else None
    return {"id": e.id, "codigo": e.codigo, "nombre": v["nombre"], "estado": e.estado, "situacion": sit,
            "situacion_texto": SITUACIONES[sit], "detalle": det, "region": e.region, "sucursal": e.sucursal,
            "sucursales_alternativas": e.sucursales_alternativas or [], "tipo_tramite": e.tipo_tramite,
            "prioridad": e.prioridad, "fecha_solicitada": v["fecha_solicitada"], "fechas_desde": v["fechas_desde"],
            "fechas_hasta": v["fechas_hasta"],
            "consentimiento": ("VIGENTE" if vig else ("REVOCADO" if cs.revocado(s, e.id) else "FALTA")) if cs.exigido() else "NO_EXIGIDO",
            "lote": cache["lotes"].get(e.id), "ultimo_error": e.ultimo_error, "version": e.version,
            "planificable": sit == "LISTO"}


def _cruza(desde: date | None, hasta: date | None, f1: date | None, f2: date | None) -> bool:
    """¿El rango aceptable [desde, hasta] del expediente se cruza con el filtro [f1, f2]?"""
    if f1 is None and f2 is None:
        return True
    if desde is None and hasta is None:
        return False
    a, b = desde or date.min, hasta or date.max
    return a <= (f2 or date.max) and (f1 or date.min) <= b


@router.get("/expedientes")
def expedientes(situacion: str | None = Query(default=None, max_length=24),
                sucursal: str | None = Query(default=None, max_length=32),
                tramite: str | None = Query(default=None, max_length=32),
                consentimiento: str | None = Query(default=None, pattern="^(VIGENTE|FALTA|REVOCADO)$"),
                desde: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                hasta: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                lote: str | None = Query(default=None, max_length=32),
                bloqueados: bool = False, limite: int = Query(default=500, ge=1, le=2000),
                user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Expedientes con su situación en el flujo, para filtrar y seleccionar. Nombre ENMASCARADO."""
    now = utcnow()
    f1 = date.fromisoformat(desde) if desde else None
    f2 = date.fromisoformat(hasta) if hasta else None
    with db.session() as s:
        cache = _cache(s)
        vivos = _elementos_vivos(s)
        q = select(Expediente).where(Expediente.anonimizado_en.is_(None)).order_by(Expediente.id)
        if sucursal:
            q = q.where(Expediente.sucursal == sucursal.strip().upper())
        if tramite:
            q = q.where(Expediente.tipo_tramite == tramite.strip().lower())
        out, conteo = [], {}
        for e in s.scalars(q):
            if not _cruza(e.fechas_desde, e.fechas_hasta, f1, f2):
                continue
            if lote and cache["lotes"].get(e.id) != lote:
                continue
            fila = _fila(s, e, vivos.get(e.id), now, cache)
            conteo[fila["situacion"]] = conteo.get(fila["situacion"], 0) + 1
            if situacion and fila["situacion"] != situacion:
                continue
            if consentimiento and fila["consentimiento"] != consentimiento:
                continue
            if bloqueados and fila["situacion"] not in ("INCOMPLETO", "SIN_CONSENTIMIENTO", "REVISION", "REVOCADO",
                                                        "VERIFICACION"):
                continue
            out.append(fila)
            if len(out) >= limite:
                break
        return {"expedientes": out, "por_situacion": conteo, "situaciones": SITUACIONES,
                "jornada": zona.hoy(now).isoformat()}


@router.post("/plan/agregar")
def al_plan(body: AlPlanIn, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Añade los expedientes elegidos al plan de HOY (lo crea si no existe). Usa `plan.agregar`: cada
    expediente entra o se rechaza con su motivo (incompleto, sin consentimiento, capacidad...)."""
    now = utcnow()
    hoy = zona.hoy(now)
    with db.session() as s:
        p = s.scalar(select(PlanDiario).where(PlanDiario.fecha_jornada == hoy))
        plan_id = p.id if p else None
        capacidad_actual = p.capacidad if p else None
    try:
        if plan_id is None:
            plan_id = pl.crear(db, hoy, user.username, now, body.capacidad or pl.CAPACIDAD_INICIAL)["id"]
        elif body.capacidad and body.capacidad > capacidad_actual:
            pl.ampliar(db, plan_id, body.capacidad, user.username, now)
        elementos = [{"expediente_id": i, "fechas_desde": body.fechas_desde, "fechas_hasta": body.fechas_hasta}
                     for i in dict.fromkeys(body.expediente_ids)]
        r = pl.agregar(db, plan_id, elementos, user.username, now)
    except pl.ErrorPlan as e:
        raise HTTPException(e.http, e.mensaje) from e
    with db.session() as s:
        codigos = dict(s.execute(select(Expediente.id, Expediente.codigo).where(
            Expediente.id.in_([x.get("expediente_id") for x in r["rechazados"] if x.get("expediente_id")] or [-1]))).all())
    for x in r["rechazados"]:
        x["codigo"] = codigos.get(x.get("expediente_id"))
    return r


# ====================================================================== panel
@router.get("/panel")
def panel(request: Request, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    """Cifras reales de la mesa de control (base de datos del laboratorio)."""
    from ..planificador import observador as ob
    now = utcnow()
    with db.session() as s:
        cache = _cache(s)
        vivos = _elementos_vivos(s)
        sit: dict[str, int] = {}
        completos = incompletos = total = activos = 0
        problemas: list[dict] = []
        coincidencias, confirmadas = [], []
        for e in s.scalars(select(Expediente).order_by(Expediente.id)):
            total += 1
            if e.anonimizado_en is not None:
                continue
            fila = _fila(s, e, vivos.get(e.id), now, cache)
            k = fila["situacion"]
            sit[k] = sit.get(k, 0) + 1
            if e.estado in ex.ACTIVOS:
                activos += 1
                if ex.faltantes(e):
                    incompletos += 1
                else:
                    completos += 1
            if k == "COINCIDENCIA" or k == "EN_OPERACION" and fila["detalle"].get("elemento"):
                coincidencias.append({"codigo": e.codigo, "expediente_id": e.id, "situacion": k, **fila["detalle"]})
            elif k == "CONFIRMADA" and fila["detalle"].get("folio"):
                confirmadas.append({"codigo": e.codigo, "expediente_id": e.id, **fila["detalle"]})
            elif k == "REVISION":
                problemas.append({"tipo": "REVISION", "codigo": e.codigo, "expediente_id": e.id,
                                  "texto": (e.ultimo_error or "Requiere revisión")[:160]})
            elif k == "VERIFICACION":
                problemas.append({"tipo": "VERIFICACION", "codigo": e.codigo, "expediente_id": e.id,
                                  "texto": "Confirmación incierta: no repetir; el motor la concilia con el portal"})
            elif k == "REVOCADO" and e.estado != ex.CERRADO:
                problemas.append({"tipo": "REVOCADO", "codigo": e.codigo, "expediente_id": e.id,
                                  "texto": "Consentimiento revocado: no se opera"})
        en_verif = s.scalar(select(func.count()).select_from(Atencion).where(
            Atencion.cerrada_en.is_(None), Atencion.resultado == "VERIFICACION")) or 0
        confirmadas_folio = s.scalar(select(func.count()).select_from(Reservacion).where(
            Reservacion.estado == "CONFIRMADA", Reservacion.folio.is_not(None))) or 0
        ult_vista = s.scalar(select(func.max(ObservacionVista.actualizado_en)))
        ult_dia = s.scalar(select(func.max(ObservacionDia.actualizado_en)))
        ult_evento = s.scalar(select(func.max(ObservacionEvento.registrado_en)))
        lotes = [{"codigo": c, "rechazadas": r, "creadas": n, "fecha": t.isoformat() + "Z"} for c, r, n, t in s.execute(
            select(Importacion.codigo, Importacion.rechazadas, Importacion.creadas, Importacion.created_at)
            .order_by(Importacion.id.desc()).limit(5))]
        hoy = zona.hoy(now)
        p = s.scalar(select(PlanDiario).where(PlanDiario.fecha_jornada == hoy))
        plan_hoy = None
        if p is not None:
            n_vivos = s.scalar(select(func.count()).select_from(PlanElemento).where(
                PlanElemento.plan_id == p.id, PlanElemento.estado.in_(pl.VIVOS))) or 0
            plan_hoy = {"id": p.id, "capacidad": p.capacidad, "vivos": n_vivos, "estado": p.estado}
    configs = ob.listar_config(db)
    sup = getattr(request.app.state, "supervisor", None)
    obs_vivos = sup.estado()["observadores"] if sup else []
    activos_cfg = [c for c in configs if c.get("activo")]
    if not activos_cfg:
        problemas.append({"tipo": "OBSERVADOR", "texto": "No hay observador del calendario en marcha: el plan no recibe fechas nuevas"})
    for c in configs:
        if c.get("estado") == "DETENIDO_POR_FALLOS":
            problemas.append({"tipo": "OBSERVADOR", "texto": f"Observador {c['clave']} detenido por fallos: {c.get('motivo', '')}"[:160]})
    if en_verif:
        problemas.append({"tipo": "VERIFICACION", "texto": f"{en_verif} atención(es) con confirmación en verificación"})
    for lt in lotes:
        if lt["rechazadas"]:
            problemas.append({"tipo": "IMPORTACION", "lote": lt["codigo"],
                              "texto": f"{lt['codigo']}: {lt['rechazadas']} fila(s) rechazadas; revisa el reporte del lote"})
    iso = lambda t: t.isoformat() + "Z" if t else None  # noqa: E731
    operaciones = sit.get("EN_OPERACION", 0) + sit.get("VERIFICACION", 0)
    return {
        "jornada": hoy.isoformat(), "ahora": iso(now),
        "cifras": {"total": total, "activos": activos, "completos": completos, "incompletos": incompletos,
                   "sin_consentimiento": sit.get("SIN_CONSENTIMIENTO", 0), "listos": sit.get("LISTO", 0),
                   "pendientes_de_cita": activos, "revision": sit.get("REVISION", 0),
                   "revision_duplicados": sum(1 for x in problemas if x["tipo"] == "REVISION" and "DUPLICADO" in x["texto"].upper()),
                   "en_plan": sit.get("EN_PLAN", 0) + sit.get("COINCIDENCIA", 0) + sit.get("EN_OPERACION", 0),
                   "coincidencias": sit.get("COINCIDENCIA", 0), "operaciones_pendientes": operaciones + en_verif,
                   "confirmadas_con_folio": confirmadas_folio, "problemas": len(problemas)},
        "por_situacion": sit, "situaciones": SITUACIONES, "coincidencias": coincidencias[:20],
        "confirmadas": confirmadas[-10:][::-1], "problemas": problemas[:30], "plan_hoy": plan_hoy, "lotes": lotes,
        "observador": {"configurados": configs, "activos": len(activos_cfg), "en_este_proceso": obs_vivos,
                       "ultima_vista": iso(ult_vista), "ultimo_dia_visto": iso(ult_dia), "ultimo_evento": iso(ult_evento)},
        "simulado": "La confirmación de citas es SIMULADA: portal de laboratorio propio en 127.0.0.1, datos ficticios.",
    }


@router.get("/expedientes/{eid}/procedencia")
def procedencia(eid: int, user: CurrentUser = Depends(require_admin), db: Database = Depends(get_db)):
    with db.session() as s:
        if s.get(Expediente, eid) is None:
            raise HTTPException(404, "Expediente no encontrado")
        return {"importacion": im.de_expediente(s, eid)}

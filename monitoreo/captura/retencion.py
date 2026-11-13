"""Política de retención y privacidad de expedientes (v1.6). Resuelve el riesgo abierto de la v1.5
("los expedientes se guardaban indefinidamente").

Reglas (parámetros del motor, editables por el administrador):
  retencion_confirmados_dias   RESERVACION_CONFIRMADA sin cambios hace N días -> se ANONIMIZA
  retencion_cerrados_dias      CERRADO hace N días -> ANONIMIZAR o BORRAR (`retencion_accion`)
  retencion_incompletos_dias   NUEVO / INCOMPLETO / REQUIERE_REVISION sin cambios hace N días ->
                               se cierra (motivo RETENCION) y se anonimiza
  retencion_respaldos_dias     respaldos más viejos se borran (siempre se conservan los 3 últimos)
Cada plazo va de 1 a 3650 días: la política se puede alargar, pero no apagar (decisión de privacidad).

Nunca se toca un expediente con OPERACIÓN ACTIVA (ASIGNADO, CAPTURA_EN_PROCESO,
RESERVACION_PENDIENTE) ni uno con una reservación apartada o en verificación. LISTO tampoco: está
esperando disponibilidad. BORRAR solo se aplica a expedientes que nunca llegaron al portal (sin
reservaciones); los demás se anonimizan aunque la acción sea BORRAR, para conservar la auditoría
(código, estados, folio, fechas y quién hizo qué, sin datos personales).

Anonimizar = borrar nombre, fecha de nacimiento, teléfono, correo, identificadores y folio
anterior; se conservan código, región, sucursal, trámite, estados, historial (que ya no lleva
datos personales) y el folio de la reservación como evidencia.
"""
from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from ..config import settings
from ..db import Database
from ..engine.params import get_params
from ..models import CalendarioCambio, Expediente, ExpedienteEvento, Reservacion
from . import expedientes as ex
from . import reservaciones as rs

ANONIMO = "(anonimizado)"
CONSERVAR_RESPALDOS = 3


def _anonimizar(exp: Expediente, now: datetime) -> None:
    exp.nombre, exp.fecha_nacimiento, exp.telefono, exp.correo = ANONIMO, None, None, None
    exp.id_registro_previo, exp.id_externo, exp.folio_anterior = None, None, None
    exp.dedup_activo, exp.ultimo_error, exp.anonimizado_en, exp.updated_at = None, None, now, now
    exp.version += 1


def candidatos(s, now: datetime, params: dict) -> list[dict]:
    """Qué haría la política ahora (sin cambiar nada). Sirve para la vista previa del administrador."""
    reglas = [((ex.RES_CONFIRMADA,), float(params["retencion_confirmados_dias"]), "ANONIMIZAR"),
              ((ex.CERRADO,), float(params["retencion_cerrados_dias"]), str(params["retencion_accion"])),
              ((ex.NUEVO, ex.INCOMPLETO, ex.REVISION), float(params["retencion_incompletos_dias"]), "CERRAR_Y_ANONIMIZAR")]
    out = []
    for estados, dias, accion in reglas:
        if dias <= 0:
            continue                                   # defensivo: los parámetros exigen >= 1 día (no se puede apagar)
        limite = now - timedelta(days=dias)
        q = select(Expediente).where(Expediente.estado.in_(list(estados)), Expediente.anonimizado_en.is_(None),
                                     Expediente.updated_at < limite)
        for e in s.scalars(q):
            if rs.viva_bloquea(s, e.id):
                continue                               # operación activa en el portal: no se toca
            con_reservas = s.scalar(select(Reservacion.id).where(Reservacion.expediente_id == e.id).limit(1))
            a = accion
            if a == "BORRAR" and con_reservas:
                a = "ANONIMIZAR"                       # tiene evidencia que auditar: no se borra
            out.append({"id": e.id, "codigo": e.codigo, "estado": e.estado, "accion": a,
                        "dias_sin_cambios": round((now - e.updated_at).total_seconds() / 86400, 1)})
    return out


def aplicar(db: Database, now: datetime, actor: str = "motor", simular: bool = False) -> dict:
    from .. import security
    with db.session() as s:
        params = get_params(s)
        plan = candidatos(s, now, params)
    res = {"anonimizados": 0, "borrados": 0, "cerrados": 0, "omitidos": 0, "simulado": simular,
           "expedientes": [{k: p[k] for k in ("codigo", "estado", "accion")} for p in plan]}
    if simular:
        res["respaldos"] = purgar_respaldos(now, float(params["retencion_respaldos_dias"]), simular=True)
        return res
    for p in plan:
        with db.session() as s:
            e = s.get(Expediente, p["id"])
            # se vuelve a comprobar dentro de la transacción (pudo cambiar desde el plan)
            if e is None or e.anonimizado_en is not None or e.estado in ex.OPERACION_ACTIVA or e.estado == ex.LISTO \
                    or rs.viva_bloquea(s, e.id):
                res["omitidos"] += 1
                continue
            if p["accion"] == "BORRAR":
                s.execute(delete(ExpedienteEvento).where(ExpedienteEvento.expediente_id == e.id))
                s.delete(e)
                res["borrados"] += 1
                security.audit(s, actor, "retencion_borrado", p["codigo"], {"estado": p["estado"]})
                continue
            if p["accion"] == "CERRAR_Y_ANONIMIZAR" and e.estado != ex.CERRADO:
                ex.cerrar(s, e, "RETENCION", actor, now, nota="sin cambios dentro del plazo de retención")
                res["cerrados"] += 1
            _anonimizar(e, now)
            ex.evento(s, e.id, "ANONIMIZADO", actor, now, regla=p["accion"], estado=e.estado)
            res["anonimizados"] += 1
    with db.session() as s:
        limite_cal = now - timedelta(days=float(params["retencion_eventos_sin_alerta_dias"]))
        res["cambios_calendario_borrados"] = s.execute(
            delete(CalendarioCambio).where(CalendarioCambio.detectado_en < limite_cal)).rowcount or 0
    res["respaldos"] = purgar_respaldos(now, float(params["retencion_respaldos_dias"]))
    if res["anonimizados"] or res["borrados"] or res["respaldos"]["borrados"]:
        with db.session() as s:
            security.audit(s, actor, "retencion_aplicada", "expedientes",
                           {k: res[k] for k in ("anonimizados", "borrados", "cerrados", "omitidos")}
                           | {"respaldos_borrados": res["respaldos"]["borrados"]})
    return res


# ---------------------------------------------------------------- respaldos
def directorio_respaldos() -> Path:
    return settings.data_dir / "respaldos"


def proteger(ruta: Path) -> None:
    """Solo el dueño puede leer los respaldos (POSIX). En Windows los permisos se heredan de la
    carpeta del usuario: ver GUIA (no se cambian ACL desde aquí)."""
    try:
        if os.name != "nt":
            os.chmod(ruta, stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if ruta.is_dir() else 0))
    except OSError:
        pass


def purgar_respaldos(now: datetime, dias: float, simular: bool = False) -> dict:
    d = directorio_respaldos()
    if dias <= 0 or not d.exists():
        return {"borrados": 0, "conservados": 0, "archivos": []}
    archivos = sorted((p for p in d.iterdir() if p.is_file() and p.name.startswith("monitoreo-")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    limite = (now - timedelta(days=dias)).timestamp()
    viejos = [p for p in archivos[CONSERVAR_RESPALDOS:] if p.stat().st_mtime < limite]
    if not simular:
        for p in viejos:
            try:
                p.unlink()
            except OSError:
                pass
    return {"borrados": len(viejos), "conservados": len(archivos) - len(viejos), "archivos": [p.name for p in viejos]}

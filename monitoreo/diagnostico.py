"""Autodiagnóstico (v1.2): el motor revisa su propio estado y sugiere qué hacer.

Se usa desde tres sitios:
- el panel (Salud → Autodiagnóstico), vía GET /api/autodiagnostico (administrador);
- el planificador, cada `autodiagnostico_cada_s`: guarda el resultado y crea un aviso SISTEMA
  (deduplicado por día) para cada problema CRÍTICO nuevo;
- la consola, SIN necesidad de tener el servidor en marcha (útil al instalar o actualizar):
      python -m monitoreo.diagnostico          (texto)
      python -m monitoreo.diagnostico --json   (para scripts)
  Código de salida: 0 = todo bien, 1 = avisos, 2 = problemas críticos.

Cada comprobación abre su propia sesión, una detrás de otra (nunca anidadas).
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import psutil
from sqlalchemy import case, func, select, text

from . import entorno
from .config import settings
from .db import Database, utcnow

OK, INFO, AVISO, CRITICO = "OK", "INFO", "AVISO", "CRITICO"
_ORDER = {CRITICO: 0, AVISO: 1, INFO: 2, OK: 3}


@dataclass
class Item:
    area: str
    estado: str
    titulo: str
    detalle: str = ""
    accion: str = ""
    clave: str = ""   # identificador estable del problema (para deduplicar avisos)


def _age(ts: datetime | None, now: datetime) -> float | None:
    return None if ts is None else (now - ts).total_seconds()


def _fmt_s(s: float | None) -> str:
    if s is None:
        return "nunca"
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


# ----------------------------------------------------------------------------- comprobaciones
def check_config(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    r = entorno.review()
    for e in r.errores:
        out.append(Item("Configuración", CRITICO, "Configuración insegura", e, "Corrige .env y reinicia.", "cfg:" + e[:40]))
    for w in r.avisos:
        out.append(Item("Configuración", AVISO, "Aviso de configuración", w, "", "cfgw:" + w[:40]))
    if not r.errores and not r.avisos:
        out.append(Item("Configuración", OK, f"Configuración correcta para {r.entorno}"))


def check_database(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    t0 = time.perf_counter()
    try:
        with db.session() as s:
            s.execute(select(1))
    except Exception as e:  # noqa: BLE001
        out.append(Item("Base de datos", CRITICO, "No hay conexión con la base de datos", type(e).__name__,
                        "Comprueba que PostgreSQL esté en marcha o que la carpeta data sea accesible.", "db:conexion"))
        ctx["db_ok"] = False
        return
    ms = (time.perf_counter() - t0) * 1000
    ctx["db_ok"] = True
    st = AVISO if ms > 500 else OK
    out.append(Item("Base de datos", st, f"Conexión correcta ({'SQLite' if db.is_sqlite else 'PostgreSQL'})",
                    f"Latencia {ms:.1f} ms", "Latencia alta: revisa disco o red." if st == AVISO else "", "db:latencia"))
    if db.is_sqlite and ctx.get("profundo"):
        with db.session() as s:
            res = s.execute(text("PRAGMA quick_check")).scalar()
        if res != "ok":
            out.append(Item("Base de datos", CRITICO, "SQLite reporta daños", str(res)[:200],
                            "Detén el motor y restaura el último respaldo de data/respaldos.", "db:quick_check"))
    try:
        du = psutil.disk_usage(str(settings.data_dir if settings.data_dir.exists() else settings.data_dir.parent))
        free_gb = du.free / 1024 ** 3
        if free_gb < 0.2:
            out.append(Item("Recursos", CRITICO, "Disco casi lleno", f"Quedan {free_gb:.2f} GB libres",
                            "Libera espacio o mueve respaldos antiguos de data/respaldos.", "disco:lleno"))
        elif free_gb < 1:
            out.append(Item("Recursos", AVISO, "Poco espacio en disco", f"Quedan {free_gb:.2f} GB libres",
                            "Libera espacio pronto.", "disco:poco"))
        else:
            out.append(Item("Recursos", OK, "Espacio en disco suficiente", f"{free_gb:.1f} GB libres"))
    except OSError:
        pass


def check_schema(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from . import migrate
    try:
        st = migrate.inspect_state(db.url)
    except Exception as e:  # noqa: BLE001
        out.append(Item("Esquema", CRITICO, "No se pudo leer la versión del esquema", type(e).__name__, "", "esq:leer"))
        return
    if st["revision_actual"] != st["revision_objetivo"]:
        out.append(Item("Esquema", CRITICO, "Esquema desactualizado",
                        f"Versión {st['revision_actual']} y el código espera {st['revision_objetivo']}.",
                        "Ejecuta: python -m monitoreo.migrate actualizar (hace respaldo antes).", "esq:version"))
    else:
        out.append(Item("Esquema", OK, f"Esquema al día ({st['revision_actual']})"))
    backups = sorted((settings.data_dir / "respaldos").glob("monitoreo-*")) if (settings.data_dir / "respaldos").exists() else []
    if backups:
        age = time.time() - backups[-1].stat().st_mtime
        out.append(Item("Esquema", INFO, f"{len(backups)} respaldo(s) en data/respaldos",
                        f"El más reciente tiene {_fmt_s(age)}."))


def check_scheduler(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .models import SchedulerLease
    sch = ctx.get("scheduler")
    with db.session() as s:
        lease = s.get(SchedulerLease, "planificador")
        lease = None if lease is None else (lease.holder, lease.expires_at, lease.renewed_at)
    if ctx.get("motor_detenido"):
        # Instalación / actualización: se espera que el motor esté parado.
        if lease is not None and lease[1] > now:
            out.append(Item("Planificador", CRITICO, "El motor está en marcha",
                            f"La instancia {lease[0]} tiene el planificador activo.",
                            "Detén el motor (Ctrl+C en la ventana de iniciar.bat) antes de instalar o actualizar.",
                            "plan:en_marcha"))
        else:
            out.append(Item("Planificador", INFO, "Motor detenido (esperado durante la instalación o actualización)"))
            ctx["sin_lider"] = True
        return
    if lease is None or lease[1] <= now:
        out.append(Item("Planificador", CRITICO, "Ninguna instancia tiene el planificador activo",
                        "Las fuentes no se están consultando." if lease is None else
                        f"El último líder ({lease[0]}) dejó de renovar hace {_fmt_s(_age(lease[2], now))}.",
                        "Inicia el motor (iniciar.bat). Si está iniciado, revisa errores en Salud o en data/logs.",
                        "plan:sin_lider"))
        ctx["sin_lider"] = True
    else:
        out.append(Item("Liderazgo", OK, "Planificador activo", f"Líder: {lease[0]}, renovado hace "
                        f"{_fmt_s(_age(lease[2], now))}."))
    if sch is not None:
        st = sch.status()
        if st.get("errores_ciclo_seguidos"):
            out.append(Item("Planificador", CRITICO if st["errores_ciclo_seguidos"] >= 5 else AVISO,
                            "El ciclo del motor está fallando",
                            f"{st['errores_ciclo_seguidos']} ciclos seguidos con error: {st.get('ultimo_error')}",
                            "Suele ser la base de datos. Revisa la conexión.", "plan:ciclo"))
        if st.get("rol") == "EN_ESPERA":
            out.append(Item("Liderazgo", INFO, "Esta instancia está en espera", "Otra instancia es la líder."))
        lag = st.get("segundos_desde_ultimo_ciclo")
        if st.get("activo") and lag is not None and lag > 60:
            out.append(Item("Planificador", AVISO, "El ciclo va retrasado", f"Último ciclo hace {_fmt_s(lag)}.",
                            "Revisa CPU y la base de datos.", "plan:retraso"))


def check_sources(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .connectors.base import ConnectorError
    from .connectors.registry import build_connector
    from .models import Source
    prod = entorno.is_production()
    with db.session() as s:
        rows = [(x.id, x.code, x.status, x.circuit_state, x.consecutive_failures, x.last_success_at, x.interval_s,
                 x.last_error, x.connector_type, dict(x.config or {}), x.status_reason) for x in s.scalars(select(Source))]
    if not rows:
        out.append(Item("Conectores", AVISO if prod else INFO, "No hay fuentes configuradas",
                        "El motor no tiene nada que vigilar.", "Añade fuentes en Configuración.", "src:ninguna"))
        return
    problems = 0
    stale_no_leader = 0
    for sid, code, status, circ, fails, last_ok, interval, last_err, ctype, cfg, reason in rows:
        try:
            conn = build_connector(ctype, cfg, {})
            conn.validate_config()
            path = getattr(conn, "path", None)
            if path is not None and not path.exists():
                raise ConnectorError(f"no existe el archivo {path.name}")
        except ConnectorError as e:
            problems += 1
            out.append(Item("Conectores", CRITICO, f"{code}: configuración inválida", str(e),
                            "Corrige la configuración de la fuente.", f"src:{sid}:config"))
            continue
        if status == "ERROR":
            problems += 1
            out.append(Item("Conectores", CRITICO, f"{code}: detenida por errores", reason or last_err or "",
                            "Corrige la causa y pulsa Reanudar en Monitor.", f"src:{sid}:error"))
        elif status == "ACTIVA":
            age = _age(last_ok, now)
            if circ == "ABIERTO":
                problems += 1
                out.append(Item("Conectores", AVISO, f"{code}: circuito abierto", f"{fails} fallos seguidos. {last_err or ''}",
                                "Es automático; si persiste revisa el error.", f"src:{sid}:circuito"))
            elif (age is None or age > max(10 * interval, 600)) and ctx.get("sin_lider"):
                stale_no_leader += 1
            elif age is None or age > max(10 * interval, 600):
                problems += 1
                out.append(Item("Errores persistentes", AVISO, f"{code}: sin datos correctos desde hace {_fmt_s(age)}",
                                last_err or "", "Abre el diagnóstico de la fuente en Salud.", f"src:{sid}:viejo"))
        if prod and ctype == "simulacion":
            out.append(Item("Conectores", AVISO, f"{code}: fuente simulada en producción",
                            "Lee el inventario ficticio del laboratorio.", "Suspéndela o elimínala.", f"src:{sid}:sim"))
    if stale_no_leader:
        out.append(Item("Conectores", INFO, f"{stale_no_leader} fuente(s) sin datos recientes",
                        "Es consecuencia de que no hay planificador activo (ver arriba)."))
    elif not problems:
        out.append(Item("Conectores", OK, f"{len(rows)} fuente(s) sin problemas"))


def check_queues(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .models import Assignment, ChangeEvent, Request
    with db.session() as s:
        n_ev, oldest_ev = s.execute(select(func.count(), func.min(ChangeEvent.detected_at))
                                    .where(ChangeEvent.processed.is_(False))).one()
        overdue = s.scalar(select(func.count()).select_from(Assignment).where(
            Assignment.status == "ACTIVA", Assignment.expires_at < now - timedelta(minutes=5)))
        waiting, oldest_req = s.execute(select(func.count(), func.min(Request.created_at)).where(
            Request.status.in_(["PENDIENTE", "ELEGIBLE", "REINTENTO"]))).one()
    age = _age(oldest_ev, now)
    if n_ev and age and age > 300:
        out.append(Item("Cola de tareas", AVISO, f"{n_ev} evento(s) sin procesar", f"El más antiguo tiene {_fmt_s(age)}.",
                        "Comprueba que el planificador esté activo.", "cola:eventos"))
    if overdue:
        out.append(Item("Cola de tareas", AVISO, f"{overdue} asignación(es) vencidas sin cerrar",
                        "El mantenimiento periódico debería haberlas expirado.", "Comprueba el planificador.", "cola:vencidas"))
    if not (n_ev and age and age > 300) and not overdue:
        out.append(Item("Cola de tareas", OK, "Colas al día",
                        f"{waiting} solicitud(es) en espera" + (f"; la más antigua {_fmt_s(_age(oldest_req, now))}" if waiting else "")))


def check_notifications(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .models import Channel, Delivery
    from .notifiers.telegram import telegram_env
    with db.session() as s:
        channels = [(c.id, c.name, c.type, dict(c.config or {})) for c in s.scalars(select(Channel).where(Channel.enabled.is_(True)))]
        stuck = s.scalar(select(func.count()).select_from(Delivery).where(
            Delivery.status == "ENVIANDO", Delivery.claimed_at < now - timedelta(minutes=10)))
        old_pending = s.scalar(select(func.min(Delivery.next_attempt_at)).where(Delivery.status == "PENDIENTE"))
        stats = {cid: (tot, bad) for cid, tot, bad in s.execute(
            select(Delivery.channel_id, func.count(), func.sum(case((Delivery.status == "FALLIDA", 1), else_=0)))
            .where(Delivery.next_attempt_at >= now - timedelta(hours=24)).group_by(Delivery.channel_id))}
    issues = 0
    for cid, name, ctype, cfg in channels:
        if ctype == "telegram":
            env = telegram_env()
            if not env["token"] or not env["chats"]:
                issues += 1
                out.append(Item("Notificaciones", CRITICO, f"{name}: activado pero sin configurar",
                                "Faltan MONITOREO_TELEGRAM_TOKEN o MONITOREO_TELEGRAM_CHATS.",
                                "Configúralo (docs/TELEGRAM.md) o desactiva el canal.", f"ntf:{cid}:config"))
        if ctype == "webhook":
            from urllib.parse import urlparse
            host = urlparse(cfg.get("url", "")).hostname
            if not host or host not in settings.webhook_allowed_hosts:
                issues += 1
                out.append(Item("Notificaciones", CRITICO, f"{name}: URL no válida o host no permitido",
                                f"Host: {host or '(vacío)'}", "Corrige la URL o MONITOREO_WEBHOOK_HOSTS.", f"ntf:{cid}:url"))
        tot, bad = stats.get(cid, (0, 0))
        bad = bad or 0
        if tot >= 10 and bad / tot > 0.2:
            issues += 1
            out.append(Item("Notificaciones", AVISO, f"{name}: {bad} de {tot} entregas fallidas en 24 h",
                            "", "Abre una alerta y mira 'Entregas por canal' para ver el error.", f"ntf:{cid}:fallos"))
    if stuck:
        issues += 1
        out.append(Item("Notificaciones", AVISO, f"{stuck} entrega(s) atascadas en ENVIANDO",
                        "Llevan más de 10 minutos.", "Se reencolan solas al cambiar de líder; si persisten, reinicia.",
                        "ntf:atascadas"))
    age = _age(old_pending, now)
    if age and age > 900:
        issues += 1
        out.append(Item("Notificaciones", AVISO, "Entregas pendientes desde hace tiempo", f"La más antigua: {_fmt_s(age)}.",
                        "Comprueba que el planificador esté activo.", "ntf:pendientes"))
    if not issues:
        out.append(Item("Notificaciones", OK, f"{len(channels)} canal(es) activos sin problemas"))


def check_maintenance(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .engine.params import get_params
    from .models import MaintenanceLog
    with db.session() as s:
        every = float(get_params(s)["mantenimiento_historial_cada_s"])
        last = s.scalar(select(MaintenanceLog.ts).where(MaintenanceLog.task == "historial")
                        .order_by(MaintenanceLog.id.desc()).limit(1))
    age = _age(last, now)
    if ctx.get("scheduler_running") and (age is None or age > 3 * every + 300):
        out.append(Item("Errores persistentes", AVISO, "El mantenimiento del historial no se ejecuta",
                        f"Última ejecución: {_fmt_s(age)}.", "Revisa errores del planificador.", "mant:historial"))


def check_resources(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    p = psutil.Process()
    rss = p.memory_info().rss / 1048576
    out.append(Item("Recursos", AVISO if rss > 1024 else OK, f"Memoria del proceso: {rss:.0f} MB",
                    f"{p.num_threads()} hilos", "Uso de memoria alto: reinicia y avisa." if rss > 1024 else "", "rec:memoria"))


def check_integrity(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .engine.integrity import check
    from .models import Alert, Assignment, Delivery, Operator, Request, SlotState
    with db.session() as s:
        problems = check(s)
        orphans = {
            "entregas sin alerta": s.scalar(select(func.count()).select_from(Delivery).outerjoin(
                Alert, Alert.id == Delivery.alert_id).where(Alert.id.is_(None))),
            "asignaciones sin solicitud": s.scalar(select(func.count()).select_from(Assignment).outerjoin(
                Request, Request.id == Assignment.request_id).where(Request.id.is_(None))),
            "asignaciones sin recurso": s.scalar(select(func.count()).select_from(Assignment).outerjoin(
                SlotState, SlotState.id == Assignment.slot_id).where(SlotState.id.is_(None))),
            "asignaciones sin operador": s.scalar(select(func.count()).select_from(Assignment).outerjoin(
                Operator, Operator.id == Assignment.operator_id).where(Operator.id.is_(None))),
        }
        fk = []
        if db.is_sqlite:
            fk = list(s.execute(text("PRAGMA foreign_key_check")))
    bad = {k: v for k, v in orphans.items() if v}
    if problems or bad or fk:
        detail = "; ".join(problems[:5] + [f"{v} {k}" for k, v in bad.items()] + ([f"{len(fk)} claves foráneas rotas"] if fk else []))
        out.append(Item("Integridad", CRITICO, "Se encontraron incoherencias en los datos", detail,
                        "No borres nada. Haz un respaldo (python -m monitoreo.migrate respaldar) y revisa el informe.",
                        "int:datos"))
    else:
        out.append(Item("Integridad", OK, "Datos coherentes",
                        "Asignaciones, cargas, retenciones y referencias correctas."))


# ----------------------------------------------------------------------------- v1.7
def check_eventos(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .models import ChangeEvent
    with db.session() as s:
        n, viejo = s.execute(select(func.count(), func.min(ChangeEvent.detected_at)).where(
            ChangeEvent.processed.is_(False))).one()
    edad = _age(viejo, now)
    if n and ctx.get("scheduler_running") and edad and edad > 300:
        out.append(Item("Eventos", AVISO, f"{n} cambios de disponibilidad sin procesar",
                        f"El más antiguo espera desde hace {_fmt_s(edad)}.",
                        "El motor los procesa en cada ciclo: si no bajan, revisa los errores del planificador.", "ev:atraso"))
    else:
        out.append(Item("Eventos", OK, "Cambios de disponibilidad al día", f"{n} pendientes de procesar"))


def check_operaciones(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .models import Assignment, Reservacion
    with db.session() as s:
        abandonadas = s.scalar(select(func.count()).select_from(Assignment).where(
            Assignment.status == "EN_PROCESO", Assignment.inactive_alerted_at.is_not(None))) or 0
        dudosas = s.scalar(select(func.count()).select_from(Reservacion).where(
            Reservacion.estado.in_(["CONFIRMANDO", "EN_VERIFICACION"]),
            Reservacion.updated_at < now - timedelta(minutes=30))) or 0
    if abandonadas:
        out.append(Item("Operaciones pendientes", AVISO, f"{abandonadas} operaciones tomadas sin actividad",
                        "Un operador las tomó y no da señales. No se liberan solas: puede estar trabajando.",
                        "Supervisión: habla con el operador; si no responde, «Redistribuir…» o «Recuperar…».", "op:abandonadas"))
    if dudosas:
        out.append(Item("Operaciones pendientes", CRITICO, f"{dudosas} confirmaciones sin resultado hace más de 30 min",
                        "El motor no ha podido comprobar en el portal si se reservó o no.",
                        "No las repitas por otro medio. Revisa la conexión con el portal (Captura › Calendario).",
                        "op:verificacion"))
    if not abandonadas and not dudosas:
        out.append(Item("Operaciones pendientes", OK, "Ninguna operación atascada"))


def check_expedientes(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .captura import expedientes as ex
    from .models import Expediente
    with db.session() as s:
        total = s.scalar(select(func.count()).select_from(Expediente)) or 0
        if total > 3000 and not ctx.get("profundo"):
            out.append(Item("Expedientes", INFO, f"{total} expedientes: revisión de coherencia solo en modo profundo",
                            "Recorre todos los expedientes; con muchos tarda.", "Ejecuta el diagnóstico profundo."))
            return
        malos = ex.coherencia(s) if total else []
    if malos:
        out.append(Item("Expedientes", CRITICO, f"{len(malos)} expedientes con estados que no cuadran",
                        "; ".join(f"{m['expediente']}: {m['problema']}" for m in malos[:5]),
                        "No los edites a mano. Avisa al administrador técnico con este informe.", "exp:coherencia"))
    else:
        out.append(Item("Expedientes", OK, "Estados de los expedientes coherentes", f"{total} revisados"))


def check_retencion(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from .captura import retencion
    from .engine.params import get_params
    with db.session() as s:
        plan = retencion.candidatos(s, now, get_params(s))
    if plan:
        out.append(Item("Retención", AVISO, f"{len(plan)} expedientes superan el plazo de retención",
                        "Siguen guardando datos personales más tiempo del que marca la política.",
                        "Captura › Retención: revisa la vista previa y aplícala (queda auditado).", "ret:pendiente"))
    else:
        out.append(Item("Retención", OK, "Ningún expediente supera el plazo de retención"))


def check_reparaciones(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    from . import reparaciones as rp
    for r in rp.revisar(db, now):
        if r["pendientes"]:
            out.append(Item("Reparaciones", AVISO if r["segura"] else CRITICO, r["titulo"],
                            f"{r['pendientes']} casos. {r['explicacion']}",
                            "Se corrige sola en el próximo autodiagnóstico (queda auditado)." if r["segura"] else
                            "Salud › Autodiagnóstico › Reparar (requiere tu confirmación).", f"rep:{r['clave']}"))


def check_conexiones(out: list[Item], db: Database, now: datetime, ctx: dict) -> None:
    """v2.0: interfaces de red, destino del portal, restricciones del proveedor y caídas (24 h)."""
    if entorno.is_production():
        return                      # en producción no hay portal de reservaciones: nada que revisar
    from .captura import portal
    from .conexiones import diagnostico as dg
    for x in dg.revisar(db, now, portal.url(), profundo=bool(ctx.get("profundo"))):
        out.append(Item("Conectividad", x["estado"], x["titulo"], x["detalle"], x["accion"], x["clave"]))


AREAS = {"check_config": "Configuración", "check_database": "Base de datos", "check_schema": "Esquema",
         "check_scheduler": "Planificador", "check_sources": "Conectores", "check_queues": "Cola de tareas",
         "check_notifications": "Notificaciones", "check_maintenance": "Errores persistentes",
         "check_resources": "Recursos", "check_integrity": "Integridad", "check_eventos": "Eventos",
         "check_operaciones": "Operaciones pendientes", "check_expedientes": "Expedientes",
         "check_retencion": "Retención", "check_reparaciones": "Reparaciones", "check_conexiones": "Conectividad"}
CHECKS = [check_config, check_database, check_schema, check_scheduler, check_sources, check_queues,
          check_notifications, check_maintenance, check_resources, check_integrity, check_eventos,
          check_operaciones, check_expedientes, check_retencion, check_reparaciones, check_conexiones]


def run(db: Database, scheduler=None, now: datetime | None = None, deep: bool = False,
        motor_detenido: bool = False) -> dict:
    """motor_detenido=True: comprobación previa o posterior a instalar/actualizar (el motor debe estar parado)."""
    now = now or utcnow()
    ctx = {"scheduler": scheduler, "scheduler_running": bool(scheduler and scheduler.running), "profundo": deep,
           "motor_detenido": motor_detenido}
    items: list[Item] = []
    t0 = time.perf_counter()
    for chk in CHECKS:
        if chk not in (check_config, check_database, check_resources) and ctx.get("db_ok") is False:
            continue
        try:
            chk(items, db, now, ctx)
        except Exception as e:  # noqa: BLE001 - una comprobación rota no debe ocultar las demás
            items.append(Item(AREAS.get(chk.__name__, chk.__name__), AVISO, "La comprobación falló",
                              f"{type(e).__name__}: {str(e)[:150]}", "", f"chk:{chk.__name__}"))
    items.sort(key=lambda i: _ORDER[i.estado])
    worst = items[0].estado if items else OK
    return {"momento": now.isoformat() + "Z", "duracion_ms": round((time.perf_counter() - t0) * 1000, 1),
            "estado": worst if worst in (CRITICO, AVISO) else OK,
            "criticos": sum(i.estado == CRITICO for i in items), "avisos": sum(i.estado == AVISO for i in items),
            "comprobaciones": [asdict(i) for i in items]}


def run_and_alert(db: Database, scheduler=None, now: datetime | None = None) -> dict:
    """Ejecución periódica desde el planificador: guarda el resultado y crea un aviso SISTEMA por
    cada problema CRÍTICO (como mucho uno al día por problema, gracias a la clave de deduplicación)."""
    from .engine import alerts as al
    from .models import Setting
    now = now or utcnow()
    from . import reparaciones
    try:
        reparadas = reparaciones.aplicar_seguras(db, now)     # v1.7: solo las seguras; auditadas
    except Exception as e:  # noqa: BLE001 - una reparación fallida no debe impedir el diagnóstico
        reparadas = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    res = run(db, scheduler, now)
    res["reparaciones_automaticas"] = reparadas
    with db.session() as s:
        row = s.get(Setting, "autodiagnostico.ultimo")
        summary = {k: res[k] for k in ("momento", "estado", "criticos", "avisos", "duracion_ms", "reparaciones_automaticas")}
        summary["problemas"] = [{k: i[k] for k in ("estado", "area", "titulo")} for i in res["comprobaciones"]
                                if i["estado"] in (CRITICO, AVISO)][:20]
        if row is None:
            s.add(Setting(key="autodiagnostico.ultimo", value=summary))
        else:
            row.value = summary
        for i in res["comprobaciones"]:
            if i["estado"] == CRITICO and i["clave"]:
                al.create_alert(s, dedup_key=f"diag:{i['clave']}:{now.date().isoformat()}"[:190], kind="SISTEMA",
                                title=f"Autodiagnóstico: {i['titulo']}", source_code="motor", region="-",
                                resource=i["area"], now=now,
                                payload={"evento_sistema": "autodiagnostico", "detalle": i["detalle"][:300],
                                         "accion": i["accion"][:300]})
    return res


def main(argv: list[str]) -> int:
    """python -m monitoreo.diagnostico [--json] [--motor-detenido]
    Código de salida: 0 = OK, 1 = hay avisos, 2 = hay problemas críticos."""
    as_json = "--json" in argv
    db = Database(settings.db_url)
    try:
        res = run(db, deep=True, motor_detenido="--motor-detenido" in argv)
    finally:
        db.engine.dispose()
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        marks = {OK: "[ OK ]", INFO: "[INFO]", AVISO: "[AVISO]", CRITICO: "[CRÍTICO]"}
        print(f"Autodiagnóstico del motor ({res['momento']}): {res['estado']} — "
              f"{res['criticos']} crítico(s), {res['avisos']} aviso(s)\n")
        for i in res["comprobaciones"]:
            print(f"{marks[i['estado']]:<10} {i['area']}: {i['titulo']}")
            if i["detalle"]:
                print(f"           {i['detalle']}")
            if i["accion"]:
                print(f"           → {i['accion']}")
    return {OK: 0, AVISO: 1, CRITICO: 2}[res["estado"]]


if __name__ == "__main__":
    try:
        # Con la salida redirigida, Windows usa cp1252 (sin "→"): un carácter no debe abortar el informe.
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main(sys.argv[1:]))

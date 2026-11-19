"""v2.1 · Vista PÚBLICA y EN VIVO del calendario del portal de PRUEBA (para el observador visual).

Simula lo que hacen muchos portales con calendario dinámico: la página se abre una vez y su propio
JavaScript pide cada `refresco_ms` las celdas del mes y actualiza SOLO las que cambiaron, sin recargar
la página. Sirve para que el observador (scripts/observador_calendario.js) compare dos estrategias
(cambios del DOM frente a comparación de imágenes) sobre un calendario que cambia de verdad.

  GET  /observar/calendario?sucursal=&tramite=&mes=   página (crea una sesión de vista que caduca)
  GET  /observar/celdas?sucursal=&tramite=&mes=       estado de las celdas (401 si la sesión caducó)
  POST /control/vista  (X-Control-Token)              refresco_ms, sesion_s, congelado, caducar_todas

Estado de la vista que la página muestra en `#estado-vista[data-estado-vista]`:
  ACTIVA · SESION_CADUCADA (401: hay que volver a abrirla) · DESCONECTADO (la petición falló; reintenta)
`congelado` hace que /observar/celdas no responda (la petición queda colgada): la página deja de
actualizarse SIN mostrar error, como un calendario que se queda congelado.

Es un laboratorio: datos ficticios, solo para este portal propio. No describe ningún portal real.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import secrets
import time
from datetime import date

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .estado import TRAMITES

CSS_VISTA = """.mes{border-collapse:collapse;width:100%;max-width:560px}.mes th{background:#e9ecef}
.mes td{text-align:center;height:44px;padding:2px;width:14%}.dia{display:block;border-radius:6px;padding:8px 0;font-size:14px}
.dia.DISPONIBLE{background:#d3f9d8}.dia.AGOTADO{background:#ffe8cc;color:#868e96;text-decoration:line-through}
.dia.DESHABILITADO{background:#e9ecef;color:#adb5bd}.dia.NO_HABILITADO{background:#ffffff;color:#ced4da}
.leyenda span{margin-right:10px;font-size:12px;padding:4px 8px;border-radius:4px;display:inline-block}
#estado-vista{padding:6px 10px;border-radius:4px;max-width:540px}
#estado-vista[data-estado-vista=ACTIVA]{background:#e7f5ff}#estado-vista[data-estado-vista=SESION_CADUCADA]{background:#ffc9c9}
#estado-vista[data-estado-vista=DESCONECTADO]{background:#ffec99}"""

SCRIPT = r"""
(function(){
  const cfg = JSON.parse(document.getElementById('cfg-vista').textContent);
  const banner = document.getElementById('estado-vista');
  const act = document.getElementById('actualizado');
  const tabla = document.querySelector('[data-calendario=mensual]');
  let fallos = 0, parado = false;
  function vista(est, txt){ if (banner.dataset.estadoVista !== est){ banner.dataset.estadoVista = est; banner.textContent = txt; } }
  async function ciclo(){
    if (parado) return;
    try {
      const r = await fetch(cfg.celdas, {cache: 'no-store', credentials: 'same-origin'});
      if (r.status === 401){ parado = true; vista('SESION_CADUCADA', 'La sesión de consulta caducó: vuelve a abrir el calendario.'); return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      fallos = 0;
      let disponibles = 0;
      for (const c of d.celdas){
        const el = tabla.querySelector('[data-dia="' + c.fecha + '"]');
        if (!el) continue;
        if (c.estado === 'DISPONIBLE') disponibles++;
        if (el.dataset.estado !== c.estado || el.dataset.libres !== String(c.libres)){
          el.className = 'dia ' + c.estado; el.dataset.estado = c.estado; el.dataset.libres = String(c.libres);
        }
      }
      tabla.dataset.disponibles = String(disponibles);
      act.dataset.ts = String(d.ts); act.textContent = 'Actualizado: ' + new Date(d.ts).toLocaleTimeString('es-MX');
      vista('ACTIVA', disponibles ? 'Consultando disponibilidad.' : 'Consultando: sin días disponibles por ahora.');
      setTimeout(ciclo, d.refresco_ms || cfg.refresco_ms);
    } catch (e) {
      fallos++;
      vista('DESCONECTADO', 'Sin conexión con el portal; reintentando…');
      setTimeout(ciclo, Math.min(5000, cfg.refresco_ms * (1 + fallos)));
    }
  }
  setTimeout(ciclo, cfg.refresco_ms);
})();
"""


def e(x) -> str:
    import html
    return html.escape(str(x if x is not None else ""), quote=True)


def montar(app, portal, control) -> None:
    inv = portal.dias
    vista_cfg = {"refresco_ms": 500, "sesion_s": 900.0, "congelado": False}
    sesiones: dict[str, float] = {}           # token -> vence (monotónico)
    deshielo = asyncio.Event()
    app.state.vista = vista_cfg

    def mes_de(mes: str | None) -> tuple[int, int]:
        try:
            y, m = (int(x) for x in (mes or "").split("-"))
            date(y, m, 1)
            return y, m
        except ValueError:
            h = inv.hoy()
            return h.year, h.month

    def celdas(sucursal: str, tramite: str, y: int, m: int) -> list[dict]:
        with portal.lock:
            dias = {x["fecha"]: x for x in inv.listar(sucursal=sucursal, tramite=tramite,
                                                      desde=f"{y:04d}-{m:02d}-01", hasta=f"{y:04d}-{m:02d}-31")}
            hoy = inv.hoy().isoformat()
        out = []
        for f in range(1, calendar.monthrange(y, m)[1] + 1):
            fi = date(y, m, f).isoformat()
            x = dias.get(fi)
            est = x["estado"] if x and fi > hoy else "NO_HABILITADO"
            out.append({"fecha": fi, "estado": est, "libres": (x or {}).get("libres", 0) if est == "DISPONIBLE" else 0})
        return out

    def valida(sucursal: str, tramite: str) -> None:
        if sucursal not in portal.d["sucursales"] or tramite not in TRAMITES:
            raise HTTPException(422, "sucursal o trámite desconocido")

    @app.get("/observar/calendario", response_class=HTMLResponse)
    def observar(sucursal: str, tramite: str, mes: str | None = None):
        valida(sucursal, tramite)
        y, m = mes_de(mes)
        tok = secrets.token_urlsafe(18)
        sesiones[tok] = time.monotonic() + float(vista_cfg["sesion_s"])
        cs = {c["fecha"]: c for c in celdas(sucursal, tramite, y, m)}
        filas = []
        for semana in calendar.Calendar(firstweekday=0).monthdatescalendar(y, m):
            fila = []
            for f in semana:
                if f.month != m:
                    fila.append("<td></td>")
                    continue
                c = cs[f.isoformat()]
                fila.append(f'<td><span class="dia {c["estado"]}" data-dia="{c["fecha"]}" data-estado="{c["estado"]}" '
                            f'data-libres="{c["libres"]}">{f.day}</span></td>')
            filas.append(f"<tr>{''.join(fila)}</tr>")
        disponibles = sum(1 for c in cs.values() if c["estado"] == "DISPONIBLE")
        cfg = {"celdas": f"/observar/celdas?sucursal={sucursal}&tramite={tramite}&mes={y:04d}-{m:02d}",
               "refresco_ms": int(vista_cfg["refresco_ms"])}
        suc = portal.d["sucursales"][sucursal]["nombre"]
        html = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>Disponibilidad (laboratorio)</title>'
                f'<style>body{{font-family:system-ui,sans-serif;margin:16px}}{CSS_VISTA}</style></head>'
                f'<body data-pantalla="observar"><h1>Disponibilidad por día — laboratorio</h1>'
                f'<p class="aviso">Sistema de PRUEBA con datos ficticios. No es un servicio real.</p>'
                f'<p data-sucursal="{e(sucursal)}" data-tramite="{e(tramite)}">{e(suc)} · {e(TRAMITES[tramite])} · '
                f'<strong data-mes="{y:04d}-{m:02d}">{y:04d}-{m:02d}</strong></p>'
                f'<p id="estado-vista" data-estado-vista="ACTIVA">Consultando disponibilidad.</p>'
                f'<table class="mes" data-calendario="mensual" data-disponibles="{disponibles}"><tr>'
                + "".join(f"<th>{d_}</th>" for d_ in ("L", "M", "M", "J", "V", "S", "D")) + f"</tr>{''.join(filas)}</table>"
                '<p class="leyenda"><span class="dia DISPONIBLE" data-leyenda="DISPONIBLE">disponible</span>'
                '<span class="dia AGOTADO" data-leyenda="AGOTADO">agotado</span>'
                '<span class="dia DESHABILITADO" data-leyenda="DESHABILITADO">deshabilitado</span>'
                '<span class="dia NO_HABILITADO" data-leyenda="NO_HABILITADO">no habilitado</span></p>'
                f'<p id="actualizado" data-ts="{int(time.time() * 1000)}">Actualizado: al abrir</p>'
                f'<script type="application/json" id="cfg-vista">{json.dumps(cfg)}</script>'
                f'<script>{SCRIPT}</script></body></html>')
        r = HTMLResponse(html)
        r.set_cookie("portal_vista", tok, httponly=True, samesite="lax")
        return r

    @app.get("/observar/celdas")
    async def observar_celdas(request: Request, sucursal: str, tramite: str, mes: str | None = None):
        tok = request.cookies.get("portal_vista") or ""
        vence = sesiones.get(tok)
        if vence is None or vence < time.monotonic():
            sesiones.pop(tok, None)
            return JSONResponse({"detail": "sesión de consulta caducada", "codigo": "SESION_CADUCADA"}, 401)
        valida(sucursal, tramite)
        if vista_cfg["congelado"]:
            try:                        # la petición queda colgada hasta descongelar (máx. 120 s)
                await asyncio.wait_for(deshielo.wait(), 120)
            except asyncio.TimeoutError:
                pass
        y, m = mes_de(mes)
        return {"ts": int(time.time() * 1000), "refresco_ms": int(vista_cfg["refresco_ms"]),
                "celdas": celdas(sucursal, tramite, y, m)}

    @app.post("/control/vista")
    async def ctl_vista(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json() if (await request.body()) else {}
        if "refresco_ms" in d:
            vista_cfg["refresco_ms"] = max(50, min(10_000, int(d["refresco_ms"])))
        if "sesion_s" in d:
            vista_cfg["sesion_s"] = max(1.0, float(d["sesion_s"]))
        if "congelado" in d:
            vista_cfg["congelado"] = bool(d["congelado"])
            if vista_cfg["congelado"]:
                deshielo.clear()
            else:
                deshielo.set()
        if d.get("caducar_todas"):
            sesiones.clear()
        return {**vista_cfg, "sesiones": len(sesiones)}

"""Rutas del modo POR DÍA del portal de PRUEBA (v1.6). Se montan en la misma aplicación.

Pantallas (sin selección de hora):
  /cita                     1. Datos: 8 campos comunes + 2 de CAPTURA MANUAL + 1 dependiente
  /cita/calendario          2. Ficha (solo lectura), Estado, Sucursal, Trámite, calendario mensual,
                               selección de día y botón Continuar
  /cita/resultado/{folio}   3. Resultado: fecha, sucursal, folio y estado
API (Bearer):
  GET  /api/dias            disponibilidad diaria (con versión por día y secuencia global)
  POST /api/eventos/sesion  sesión de suscripción (caduca)
  GET  /api/eventos         flujo de eventos del servidor (text/event-stream), reanudable
Laboratorio (X-Control-Token): /control/dias/*, /control/reloj, /control/ventana, /control/api
"""
from __future__ import annotations

import asyncio
import calendar
import json
import random
import time
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from .dias import clave_dia
from .estado import OPERACIONES, REGIONES, TRAMITES, ErrorPortal

TITULOS_DIA = {"datos": "1. Datos", "calendario": "2. Calendario", "resultado": "3. Resultado"}
CSS_DIA = """.mes{border-collapse:collapse;width:100%;max-width:560px}.mes th{background:#e9ecef}
.mes td{text-align:center;height:44px;padding:2px}.dia{display:block;border-radius:6px;padding:6px 0;font-size:14px}
.dia.DISPONIBLE{background:#d3f9d8;cursor:pointer}.dia.DISPONIBLE input{width:auto;margin:0 4px 0 0}
.dia.AGOTADO{background:#ffe8cc;color:#868e96;text-decoration:line-through}.dia.DESHABILITADO{background:#e9ecef;color:#adb5bd}
.dia.NO_HABILITADO{color:#ced4da}.leyenda span{margin-right:10px;font-size:12px;padding:2px 6px;border-radius:4px}
.manual{border-left:4px solid #f08c00;padding-left:8px;margin-top:10px}.ayuda{font-size:12px;color:#495057}
input[readonly]{background:#f1f3f5}.casilla{display:flex;gap:8px;align-items:center}.casilla input{width:auto}"""


def e(x) -> str:
    import html
    return html.escape(str(x if x is not None else ""), quote=True)


def montar(app, portal, fallos, auth, control, css: str) -> None:
    inv = portal.dias
    api_estado = {"caida": False, "generacion": 0, "latido_s": 2.0, "sesion_eventos_s": 600.0}
    app.state.api_dias = api_estado

    def pagina(pantalla: str, cuerpo: str, error: str | None = None, codigo: str | None = None) -> str:
        pasos = "".join(f'<span class="{"act" if k == pantalla else ""}">{v}</span>' for k, v in TITULOS_DIA.items())
        alerta = (f'<p class="alerta" role="alert" data-error-general="{e(codigo or "ERROR")}">{e(error)}</p>'
                  if error else "")
        return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>Citas por día (laboratorio) — '
                f'{e(TITULOS_DIA[pantalla])}</title><style>{css}{CSS_DIA}</style></head>'
                f'<body data-pantalla="{pantalla}" data-modo="dia"><header><h1>Citas por día — laboratorio</h1>'
                f'<p class="aviso">Sistema de PRUEBA con datos ficticios. No es un servicio real.</p></header>'
                f'<nav class="pasos">{pasos}</nav>{alerta}{cuerpo}</body></html>')

    def resp(pantalla, cuerpo, status=200, err: ErrorPortal | None = None) -> HTMLResponse:
        return HTMLResponse(pagina(pantalla, cuerpo, err.mensaje if err else None, err.codigo if err else None),
                            status_code=status)

    def opciones(nombre, valores: dict, actual, vacio="Selecciona…", extra="") -> str:
        ops = f'<option value="">{e(vacio)}</option>' + "".join(
            f'<option value="{e(k)}"{" selected" if k == actual else ""}>{e(v)}</option>' for k, v in valores.items())
        return f'<select id="{nombre}" name="{nombre}"{extra}>{ops}</select>'

    def err(errores, campo):
        return f'<span class="error" data-error="{e(campo)}">{e(errores[campo])}</span>' if campo in errores else ""

    # ---------------------------------------------------------------- pantalla 1
    def html_datos(v: dict, errores: dict) -> str:
        texto = [("identificador", "Identificador de registro", "text"), ("fecha_nacimiento", "Fecha de nacimiento", "date"),
                 ("telefono", "Teléfono (10 dígitos)", "tel"), ("telefono_confirmacion", "Confirma tu teléfono", "tel"),
                 ("correo", "Correo electrónico", "email"), ("correo_confirmacion", "Confirma tu correo", "email")]
        filas = [f'<label for="operacion">Operación</label>{opciones("operacion", OPERACIONES, v.get("operacion"))}'
                 f'{err(errores, "operacion")}',
                 f'<label for="tramite">Tipo de trámite</label>{opciones("tramite", TRAMITES, v.get("tramite"))}'
                 f'{err(errores, "tramite")}']
        for c, etq, t in texto:
            filas.append(f'<label for="{c}">{e(etq)}</label><input id="{c}" name="{c}" type="{t}" '
                         f'value="{e(v.get(c, ""))}" autocomplete="off">{err(errores, c)}')
        filas.append(f'<label for="folio_anterior">Folio de la cita anterior <span class="ayuda">(solo al reprogramar)'
                     f'</span></label><input id="folio_anterior" name="folio_anterior" value="{e(v.get("folio_anterior", ""))}" '
                     f'data-requerido-si="operacion=reprogramar" autocomplete="off">{err(errores, "folio_anterior")}')
        # Campos de CAPTURA MANUAL: el portal impide pegar texto (onpaste) y no los guarda.
        filas.append('<div class="manual" data-manual="1"><label for="clave_identidad">Clave de identidad '
                     '(captura manual)</label><p class="ayuda">Escríbela tecleando: no se permite pegar. '
                     'Formato AAA000000.</p><input id="clave_identidad" name="clave_identidad" autocomplete="off" '
                     'data-manual="1" data-sin-pegar="1" onpaste="return false" ondrop="return false">'
                     f'{err(errores, "clave_identidad")}'
                     '<label class="casilla"><input type="checkbox" id="acepto_privacidad" name="acepto_privacidad" '
                     'value="si" data-manual="1"> Acepto el aviso de privacidad (ficticio)</label>'
                     f'{err(errores, "acepto_privacidad")}</div>')
        return (f'<form method="post" action="/cita/datos" data-formulario="datos" novalidate>{"".join(filas)}'
                f'<button type="submit" id="continuar">Continuar</button></form>')

    def sesion_o_reinicio(request: Request):
        tok = request.cookies.get("portal_sesion")
        try:
            return tok, portal.sesion(tok)
        except ErrorPortal as ex:
            r = resp("datos", '<section><a href="/cita" id="reiniciar">Empezar de nuevo</a></section>', ex.http, ex)
            r.delete_cookie("portal_sesion")
            return tok, r

    @app.get("/cita", response_class=HTMLResponse)
    def cita_datos(request: Request):
        tok = request.cookies.get("portal_sesion")
        try:
            s = portal.sesion(tok)
        except ErrorPortal:
            tok, s = portal.nueva_sesion(), None
        r = resp("datos", html_datos({}, {}))
        if s is None:
            r.set_cookie("portal_sesion", tok, httponly=True, samesite="lax")
        return r

    @app.post("/cita/datos", response_class=HTMLResponse)
    async def cita_enviar(request: Request):
        from .servidor import leer_formulario
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        f = {k: str(v) for k, v in (await leer_formulario(request)).items()}
        try:
            errores = portal.validar_datos_dia(tok, f)
        except ErrorPortal as ex:
            return resp("datos", html_datos({}, {}), ex.http, ex)
        if errores:
            # Se conserva lo escrito salvo confirmaciones y campos manuales (se vuelven a teclear).
            valores = {k: v for k, v in f.items() if not k.endswith("_confirmacion") and k != "clave_identidad"}
            return resp("datos", html_datos(valores, errores), 422,
                        ErrorPortal("DATOS_INVALIDOS", "Revisa los campos marcados."))
        return RedirectResponse("/cita/calendario", 303)

    # ---------------------------------------------------------------- pantalla 2
    def mes_actual(mes: str | None) -> tuple[int, int]:
        try:
            y, m = (int(x) for x in (mes or "").split("-"))
            date(y, m, 1)
            return y, m
        except ValueError:
            h = inv.hoy()
            return h.year, h.month

    def html_calendario(s: dict, region: str | None, sucursal: str | None, mes: str | None, errores: dict) -> str:
        d = s["datos"]
        tramite = d["tramite"]
        ficha = (f'<section data-ficha="1"><h2>Ficha del solicitante (ficticio)</h2><dl>'
                 f'<dt>Identificador</dt><dd data-campo="ficha_identificador">{e(d["identificador"])}</dd>'
                 f'<dt>Operación</dt><dd data-campo="ficha_operacion">{e(OPERACIONES[d["operacion"]])}</dd>'
                 f'<dt>Teléfono</dt><dd data-campo="ficha_telefono">••••••{e(d.get("telefono_final", ""))}</dd>'
                 f'</dl></section>')
        sucs = {k: v["nombre"] for k, v in sorted(portal.d["sucursales"].items()) if not region or v["region"] == region}
        y, m = mes_actual(mes)
        filtro = (f'<form method="get" action="/cita/calendario" data-formulario="filtro">'
                  f'<label for="estado">Estado</label>{opciones("estado", REGIONES, region, "Todos")}'
                  f'<label for="sucursal">Sucursal</label>{opciones("sucursal", sucs, sucursal, "Elige una sucursal")}'
                  f'<input type="hidden" name="mes" value="{y:04d}-{m:02d}">'
                  f'<button type="submit" id="filtrar">Ver calendario</button></form>')
        tr = (f'<label for="tramite_ficha">Trámite</label><input id="tramite_ficha" name="tramite_ficha" '
              f'value="{e(TRAMITES[tramite])}" readonly data-solo-lectura="1" data-valor="{e(tramite)}">')
        if not sucursal:
            return (ficha + filtro + f'<section>{tr}<p data-calendario="vacio">Elige estado y sucursal para ver '
                    f'los días.</p></section>')
        dias = {x["fecha"]: x for x in inv.listar(sucursal=sucursal, tramite=tramite,
                                                  desde=f"{y:04d}-{m:02d}-01", hasta=f"{y:04d}-{m:02d}-31")}
        hoy = inv.hoy().isoformat()
        filas = []
        for semana in calendar.Calendar(firstweekday=0).monthdatescalendar(y, m):
            celdas = []
            for f in semana:
                fi = f.isoformat()
                if f.month != m:
                    celdas.append("<td></td>")
                    continue
                x = dias.get(fi)
                est = x["estado"] if x else "NO_HABILITADO"
                if fi <= hoy:
                    est = "NO_HABILITADO"
                if est == "DISPONIBLE":
                    celdas.append(f'<td><label class="dia DISPONIBLE"><input type="radio" name="dia" value="{fi}" '
                                  f'data-estado="DISPONIBLE" data-libres="{x["libres"]}" data-version="{x["version"]}">'
                                  f'{f.day}</label></td>')
                else:
                    celdas.append(f'<td><span class="dia {est}" data-dia="{fi}" data-estado="{est}">{f.day}</span></td>')
            filas.append(f'<tr>{"".join(celdas)}</tr>')
        ant = date(y - (m == 1), 12 if m == 1 else m - 1, 1)
        sig = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        q = lambda mm: "/cita/calendario?" + urlencode({"estado": region or "", "sucursal": sucursal,  # noqa: E731
                                                         "mes": mm.strftime("%Y-%m")})
        nombre_mes = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                      "octubre", "noviembre", "diciembre"][m - 1]
        cal = (f'<p><a id="mes-anterior" href="{e(q(ant))}">◀ Mes anterior</a> · <strong data-mes="{y:04d}-{m:02d}">'
               f'{nombre_mes} {y}</strong> · <a id="mes-siguiente" href="{e(q(sig))}">Mes siguiente ▶</a></p>'
               f'<table class="mes" data-calendario="mensual"><tr>'
               + "".join(f"<th>{d_}</th>" for d_ in ("L", "M", "M", "J", "V", "S", "D")) + f'</tr>{"".join(filas)}</table>'
               '<p class="leyenda"><span class="dia DISPONIBLE">disponible</span><span class="dia AGOTADO">agotado</span>'
               '<span class="dia DESHABILITADO">deshabilitado</span><span class="dia NO_HABILITADO">no habilitado</span></p>')
        return (ficha + filtro +
                f'<form method="post" action="/cita/continuar" data-formulario="dia">{tr}'
                f'<input type="hidden" name="estado" value="{e(region or "")}">'
                f'<input type="hidden" name="sucursal" value="{e(sucursal)}">'
                f'<input type="hidden" name="mes" value="{y:04d}-{m:02d}">'
                f'<input type="hidden" name="clave" value="{e(s["clave"])}">{cal}{err(errores, "dia")}'
                f'<button type="submit" id="continuar-dia">Continuar</button></form>')

    @app.get("/cita/calendario", response_class=HTMLResponse)
    def cita_calendario(request: Request, estado: str | None = None, sucursal: str | None = None,
                        mes: str | None = None):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        if s.get("modo") != "dia" or s["paso"] < 2 or not s["datos"]:
            return RedirectResponse("/cita", 303)
        if s["paso"] == 4 and s.get("folio"):
            return RedirectResponse(f"/cita/resultado/{quote(s['folio'])}", 303)
        with portal.lock:
            return resp("calendario", html_calendario(s, estado or None, sucursal or None, mes, {}))

    @app.post("/cita/continuar", response_class=HTMLResponse)
    async def cita_continuar(request: Request):
        from .servidor import leer_formulario
        form = await leer_formulario(request)
        tok = request.cookies.get("portal_sesion")
        region, sucursal, mes = form.get("estado") or None, form.get("sucursal") or "", form.get("mes")
        try:
            r, _rep = portal.confirmar_dia(tok, str(form.get("clave") or ""), sucursal, str(form.get("dia") or ""))
        except ErrorPortal as ex:
            try:
                s = portal.sesion(tok)
                with portal.lock:
                    cuerpo = html_calendario(s, region, sucursal or None, mes, {"dia": ex.mensaje}) \
                        if s.get("datos") and s.get("modo") == "dia" else ""
            except ErrorPortal:
                cuerpo = '<section><a href="/cita" id="reiniciar">Empezar de nuevo</a></section>'
            return resp("calendario", cuerpo, ex.http, ex)
        if fallos.activo("continuar", {"cortar_tras_confirmar"}):
            cuerpo = b"<!doctype html><html><body data-pantalla=\"resultado\">" + b" " * 200

            async def a_medias():
                yield cuerpo[:40]
                raise ConnectionResetError("corte simulado tras confirmar")
            return StreamingResponse(a_medias(), media_type="text/html", headers={"Content-Length": str(len(cuerpo))})
        llave = portal.d["claves"][r["clave"]]
        return RedirectResponse(f"/cita/resultado/{quote(llave)}", 303)

    # ---------------------------------------------------------------- pantalla 3
    @app.get("/cita/resultado/{llave}", response_class=HTMLResponse)
    def cita_resultado(llave: str, request: Request):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        r = portal.reservacion_llave(llave)
        if r is None or s.get("folio") != llave:
            raise HTTPException(404, "folio no encontrado en esta sesión")
        return resp("resultado",
                    f'<section data-resultado="{e(r["estado"])}"><h2>Resultado de la solicitud</h2><dl>'
                    f'<dt>Estado de la reservación</dt><dd data-campo="estado">{e(r["estado"])}</dd>'
                    f'<dt>Fecha seleccionada</dt><dd data-campo="fecha">{e(r["fecha"])}</dd>'
                    f'<dt>Sucursal</dt><dd data-campo="sucursal">{e(r["sucursal_nombre"])} ({e(r["sucursal"])})</dd>'
                    f'<dt>Trámite</dt><dd data-campo="tramite">{e(TRAMITES.get(r["tramite"], r["tramite"]))}</dd>'
                    f'<dt>Folio de confirmación</dt><dd><strong id="folio" data-folio="{e(r["folio"])}">'
                    f'{e(r["folio"])}</strong></dd></dl><p><a href="/cita">Nueva solicitud</a></p></section>')

    # ---------------------------------------------------------------- API
    def api_disponible():
        if api_estado["caida"]:
            raise HTTPException(503, "API de disponibilidad no disponible (fallo simulado)")

    @app.get("/api/dias")
    def api_dias(region: str | None = None, sucursal: str | None = None, tramite: str | None = None,
                 desde: str | None = None, hasta: str | None = None, authorization: str | None = Header(None)):
        auth(authorization)
        api_disponible()
        with portal.lock:
            return {"generado": inv.ahora().isoformat().replace("+00:00", "Z"), "secuencia": portal.d["seq_dias"],
                    "zona_horaria": inv.zona_nombre, "hoy": inv.hoy().isoformat(),
                    "dias": inv.listar(region=region, sucursal=sucursal, tramite=tramite, desde=desde, hasta=hasta)}

    @app.post("/api/eventos/sesion")
    def api_eventos_sesion(authorization: str | None = Header(None)):
        auth(authorization)
        api_disponible()
        with portal.lock:
            return inv.abrir_sesion_eventos(api_estado["sesion_eventos_s"])

    @app.get("/api/eventos")
    async def api_eventos(request: Request, sesion: str | None = None, desde: int | None = None,
                          authorization: str | None = Header(None), last_event_id: str | None = Header(None)):
        auth(authorization)
        api_disponible()
        with portal.lock:
            if not inv.sesion_eventos_valida(sesion):
                return JSONResponse({"codigo": "SESION_CADUCADA", "detalle": "sesión de eventos caducada"}, 401)
            inicio = int(last_event_id) if (last_event_id or "").isdigit() else (desde if desde is not None
                                                                                else portal.d["seq_dias"])
        generacion = api_estado["generacion"]

        async def flujo():
            seq, latido = inicio, 0.0
            yield f"event: abierto\ndata: {json.dumps({'seq': seq})}\n\n".encode()
            while True:
                if await request.is_disconnected() or api_estado["caida"] or api_estado["generacion"] != generacion:
                    return
                with portal.lock:
                    if not inv.sesion_eventos_valida(sesion):
                        yield b"event: sesion_caducada\ndata: {}\n\n"
                        return
                    evs, hueco = inv.eventos_desde(seq)
                    actual = portal.d["seq_dias"]
                if hueco:
                    yield f"event: resincronizar\ndata: {json.dumps({'seq': actual})}\n\n".encode()
                    seq, evs = actual, []
                for ev in evs:
                    yield f"id: {ev['seq']}\nevent: dia\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
                    seq = ev["seq"]
                if time.monotonic() - latido >= api_estado["latido_s"]:
                    latido = time.monotonic()
                    yield f"event: latido\ndata: {json.dumps({'seq': seq, 'ts': inv.ahora().isoformat()})}\n\n".encode()
                await asyncio.sleep(0.1)
        return StreamingResponse(flujo(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------------- laboratorio
    def k_de(d: dict) -> str:
        k = d.get("clave") or clave_dia(d.get("sucursal", ""), d.get("tramite", ""), d.get("fecha", ""))
        if k not in portal.d["dias"] and d.get("accion") != "habilitar":
            raise HTTPException(404, f"día inexistente: {k}")
        return k

    @app.post("/control/dias/{accion}")
    async def ctl_dias(accion: str, request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json() if (await request.body()) else {}
        with portal.lock:
            try:
                if accion == "habilitar":
                    ev = inv.habilitar(d["sucursal"], d["tramite"], d["fecha"], int(d.get("capacidad", 2)))
                elif accion == "liberar":
                    ev = inv.liberar(k_de(d), int(d.get("unidades", 1)), d.get("vigencia_min"),
                                     causa=d.get("causa", "LIBERACION"))
                elif accion == "agotar":
                    ev = inv.agotar(k_de(d))
                elif accion == "deshabilitar":
                    ev = inv.deshabilitar(k_de(d))
                elif accion == "rehabilitar":
                    ev = inv.rehabilitar(k_de(d))
                elif accion == "reenviar-antiguo":
                    ev = inv.reemitir_antiguo(k_de(d))
                elif accion == "tic":
                    evs = inv.tic(random.Random(d.get("semilla")))
                    portal._guardar()
                    return {"eventos": evs}
                else:
                    raise HTTPException(404, "acción desconocida")
            except ErrorPortal as ex:
                raise HTTPException(ex.http, ex.mensaje) from None
            portal._guardar()
            return ev

    @app.get("/control/dias")
    def ctl_dias_lista(x_control_token: str | None = Header(None)):
        control(x_control_token)
        with portal.lock:
            return {"dias": {k: {**x, **inv.publico(k)} for k, x in portal.d["dias"].items()},
                    "secuencia": portal.d["seq_dias"], "hoy": inv.hoy().isoformat()}

    @app.get("/control/eventos-dias")
    def ctl_eventos(desde: int = 0, x_control_token: str | None = Header(None)):
        """Laboratorio: eventos en memoria (con `ts_real`, la hora real del cambio) para medir
        cuánto tarda el motor en enterarse. Se pierden si el portal se reinicia."""
        control(x_control_token)
        with portal.lock:
            evs, hueco = inv.eventos_desde(desde)
            return {"eventos": evs, "hueco": hueco, "secuencia": portal.d["seq_dias"]}

    @app.post("/control/reloj")
    async def ctl_reloj(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        with portal.lock:
            try:
                est = inv.reloj.configurar(d.get("fijar"), d.get("desplazar_s"), d.get("factor"), bool(d.get("real")))
            except ValueError as ex:
                raise HTTPException(422, str(ex)) from None
            portal._guardar()
            return est

    @app.post("/control/ventana")
    async def ctl_ventana(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        with portal.lock:
            v = portal.d["ventana"]
            for k in ("activa", "inicio", "fin", "prob", "max_unidades", "vigencia_min"):
                if k in d:
                    v[k] = d[k]
            portal._guardar()
            return {**v, "en_ventana_ahora": inv.en_ventana()}

    @app.post("/control/api")
    async def ctl_api(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        if "caida" in d:
            api_estado["caida"] = bool(d["caida"])
        if d.get("cortar_eventos"):
            api_estado["generacion"] += 1
        for k in ("latido_s", "sesion_eventos_s"):
            if k in d:
                api_estado[k] = float(d[k])
        if d.get("caducar_sesiones_eventos"):
            with portal.lock:
                inv.caducar_sesiones_eventos()
        return dict(api_estado)

    @app.post("/control/repetir-folio")
    def ctl_repetir(x_control_token: str | None = Header(None)):
        control(x_control_token)
        portal.repetir_folio = True
        return {"ok": True}

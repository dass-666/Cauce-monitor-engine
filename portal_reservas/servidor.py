"""Servidor HTTP del PORTAL DE RESERVACIONES DE PRUEBA (proceso independiente del motor).

Tres familias de rutas:
- /reservar/...   Las 4 pantallas HTML (Datos, Disponibilidad, Confirmación, Resultado). Es lo que
                  usaría una persona en un navegador y lo que recorre el motor de formularios.
                  Sesión por cookie HttpOnly `portal_sesion`.
- /api/...        API JSON de consulta para el motor (disponibilidad, verificación de folios por
                  folio o por clave, cancelación). Requiere `Authorization: Bearer`.
- /control/...    Laboratorio: inyectar fallos, caducar sesiones, agotar o liberar horarios, leer
                  el historial. Requiere `X-Control-Token`. Un portal real nunca tendría estas rutas.

El contrato estable (nombres de campos y marcadores) está en CONTRATO.md. Todo es ficticio.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from .estado import OPERACIONES, REGIONES, TRAMITES, ErrorPortal, Portal


async def leer_formulario(request: Request) -> dict:
    """Lee un formulario HTML (application/x-www-form-urlencoded) sin depender de python-multipart,
    que no forma parte de las dependencias del proyecto. Cuerpo limitado a 64 KB."""
    cuerpo = await request.body()
    if len(cuerpo) > 65536:
        raise HTTPException(413, "formulario demasiado grande")
    return dict(parse_qsl(cuerpo.decode("utf-8", "replace"), keep_blank_values=True))

COOKIE = "portal_sesion"
MODOS = {"normal", "error_interno", "cortar", "cortar_tras_confirmar", "lento", "renombrar_campo", "limite"}
CAMPOS_DATOS = ["operacion", "tramite", "identificador", "fecha_nacimiento", "telefono", "telefono_confirmacion",
                "correo", "correo_confirmacion"]


class Fallos:
    """Fallos inyectados en las rutas /reservar (las pantallas)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.modo, self.restantes, self.pantallas = "normal", None, None
        self.latencia_ms = 0
        self.renombrar = {"telefono": "telefono_movil"}     # para modo renombrar_campo
        self.aplicados: dict[str, int] = {}

    def configurar(self, d: dict) -> dict:
        with self.lock:
            if d.get("reset"):
                self.reset()
            if "modo" in d:
                if d["modo"] not in MODOS:
                    raise HTTPException(422, f"modo desconocido; válidos: {sorted(MODOS)}")
                self.modo = d["modo"]
                if "restantes" not in d:
                    self.restantes = None
            for k in ("restantes", "pantallas", "latencia_ms", "renombrar"):
                if k in d:
                    setattr(self, k, d[k])
            return self.estado()

    def estado(self) -> dict:
        return {k: getattr(self, k) for k in ("modo", "restantes", "pantallas", "latencia_ms", "renombrar",
                                               "aplicados")}

    def activo(self, pantalla: str, modos: set[str]) -> str | None:
        """Devuelve el modo si aplica a esta petición (y descuenta `restantes`)."""
        with self.lock:
            if self.modo not in modos or (self.pantallas and pantalla not in self.pantallas):
                return None
            if self.restantes is not None and self.restantes <= 0:
                return None
            if self.restantes is not None and self.modo != "renombrar_campo":
                self.restantes -= 1
            self.aplicados[self.modo] = self.aplicados.get(self.modo, 0) + 1
            return self.modo


# ---------------------------------------------------------------------- HTML
CSS = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;
color:#1d2433;background:#f6f7f9}header{border-bottom:3px solid #3b5bdb;margin-bottom:16px}
.aviso{background:#fff3bf;padding:6px 10px;border-radius:6px;font-size:13px}
.pasos{display:flex;gap:6px;font-size:13px;margin:10px 0}.pasos span{padding:4px 8px;border-radius:12px;background:#e9ecef}
.pasos .act{background:#3b5bdb;color:#fff}form,section{background:#fff;padding:16px;border-radius:8px;
box-shadow:0 1px 3px #0002}label{display:block;margin:10px 0 2px;font-weight:600;font-size:14px}
input,select{padding:7px;width:100%;max-width:360px;box-sizing:border-box;border:1px solid #adb5bd;border-radius:5px}
button{margin-top:14px;padding:9px 18px;background:#3b5bdb;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}
.error{color:#c92a2a;font-size:13px;display:block}.alerta{background:#ffe3e3;color:#c92a2a;padding:8px;border-radius:6px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #dee2e6;padding:6px;font-size:13px;vertical-align:top}
.slot{display:inline-block;margin:2px}.slot input{width:auto}.agotado{color:#868e96;text-decoration:line-through}
#folio{font-size:22px;color:#2b8a3e}dl{display:grid;grid-template-columns:180px 1fr;gap:4px 10px}dt{font-weight:600}"""

TITULOS = {"datos": "1. Datos", "disponibilidad": "2. Disponibilidad", "confirmacion": "3. Confirmación",
           "resultado": "4. Resultado"}


def e(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def pagina(pantalla: str, cuerpo: str, error_general: str | None = None, codigo: str | None = None) -> str:
    pasos = "".join(f'<span class="{"act" if k == pantalla else ""}">{v}</span>' for k, v in TITULOS.items())
    alerta = (f'<p class="alerta" role="alert" data-error-general="{e(codigo or "ERROR")}">{e(error_general)}</p>'
              if error_general else "")
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Portal de laboratorio — {e(TITULOS[pantalla])}</title>'
            f'<style>{CSS}</style></head><body data-pantalla="{pantalla}"><header><h1>Portal de reservaciones '
            f'de laboratorio</h1><p class="aviso">Sistema de PRUEBA con datos ficticios. No es un servicio real.</p>'
            f'</header><nav class="pasos">{pasos}</nav>{alerta}{cuerpo}</body></html>')


def opciones(nombre: str, valores: dict, actual: str | None, vacio: str = "Selecciona…") -> str:
    ops = f'<option value="">{e(vacio)}</option>' + "".join(
        f'<option value="{e(k)}"{" selected" if k == actual else ""}>{e(v)}</option>' for k, v in valores.items())
    return f'<select id="{nombre}" name="{nombre}">{ops}</select>'


def campo_err(errores: dict, campo: str) -> str:
    return f'<span class="error" data-error="{e(campo)}">{e(errores[campo])}</span>' if campo in errores else ""


def html_datos(valores: dict, errores: dict, nombres: dict) -> str:
    """Pantalla 1. `nombres` permite renombrar campos (fallo «campo incorrecto»)."""
    n = lambda c: nombres.get(c, c)  # noqa: E731
    etiquetas = {"identificador": "Identificador de registro previo", "fecha_nacimiento": "Fecha de nacimiento",
                 "telefono": "Teléfono (10 dígitos)", "telefono_confirmacion": "Confirma tu teléfono",
                 "correo": "Correo electrónico", "correo_confirmacion": "Confirma tu correo"}
    tipos = {"fecha_nacimiento": "date", "correo": "email", "correo_confirmacion": "email", "telefono": "tel",
             "telefono_confirmacion": "tel"}
    filas = [f'<label for="operacion">Operación</label>{opciones("operacion", OPERACIONES, valores.get("operacion"))}'
             f'{campo_err(errores, "operacion")}',
             f'<label for="tramite">Tipo de trámite</label>{opciones("tramite", TRAMITES, valores.get("tramite"))}'
             f'{campo_err(errores, "tramite")}']
    for c in CAMPOS_DATOS[2:]:
        filas.append(f'<label for="{e(n(c))}">{e(etiquetas[c])}</label><input id="{e(n(c))}" name="{e(n(c))}" '
                     f'type="{tipos.get(c, "text")}" value="{e(valores.get(c, ""))}" autocomplete="off">'
                     f'{campo_err(errores, c)}')
    return (f'<form method="post" action="/reservar/datos" data-formulario="datos" novalidate>{"".join(filas)}'
            f'<button type="submit" name="accion" value="continuar" id="continuar">Continuar</button></form>')


def html_disponibilidad(portal: Portal, s: dict, region: str | None, sucursal: str | None, error: dict) -> str:
    tramite = s["datos"]["tramite"]
    sucs = {k: v["nombre"] for k, v in portal.d["sucursales"].items() if not region or v["region"] == region}
    filtro = (f'<form method="get" action="/reservar/disponibilidad" data-formulario="filtro">'
              f'<label for="region">Región</label>{opciones("region", REGIONES, region, "Todas")}'
              f'<label for="sucursal">Sucursal</label>{opciones("sucursal", sucs, sucursal, "Todas")}'
              f'<button type="submit" id="filtrar">Ver calendario</button></form>')
    if not region and not sucursal:
        return filtro + '<section data-calendario="vacio"><p>Elige una región o sucursal.</p></section>'
    dispo = portal.disponibilidad(region=region, sucursal=sucursal, tramite=tramite)
    por_fecha: dict[str, list] = {}
    for h in dispo:
        por_fecha.setdefault(h["fecha"], []).append(h)
    filas = []
    for fecha in sorted(por_fecha):
        celdas = []
        for h in por_fecha[fecha]:
            if h["libres"] > 0:
                celdas.append(f'<label class="slot"><input type="radio" name="horario" value="{e(h["id"])}" '
                              f'data-fecha="{e(h["fecha"])}" data-hora="{e(h["hora"])}" data-sucursal="{e(h["sucursal"])}">'
                              f' {e(h["hora"])} · {e(h["sucursal"])}</label>')
            else:
                celdas.append(f'<span class="slot agotado" data-agotado="{e(h["id"])}">{e(h["hora"])} · '
                              f'{e(h["sucursal"])}</span>')
        filas.append(f'<tr data-fecha="{e(fecha)}"><th>{e(fecha)}</th><td>{"".join(celdas)}</td></tr>')
    tabla = (f'<table data-calendario="lleno"><tr><th>Fecha</th><th>Horarios</th></tr>{"".join(filas)}</table>'
             if filas else '<p data-calendario="sin-horarios">No hay horarios para este trámite.</p>')
    sel = (f'<form method="post" action="/reservar/horario" data-formulario="horario">'
           f'<input type="hidden" name="region" value="{e(region or "")}">'
           f'<input type="hidden" name="sucursal" value="{e(sucursal or "")}">{tabla}'
           f'{campo_err(error, "horario")}'
           f'<button type="submit" name="accion" value="seleccionar" id="seleccionar">Seleccionar horario</button></form>')
    return filtro + sel


def html_confirmacion(portal: Portal, s: dict) -> str:
    ret = portal.d["retenciones"].get(s.get("retencion") or "")
    if not ret:
        return '<section><p>No tienes un horario seleccionado.</p><a href="/reservar/disponibilidad">Volver</a></section>'
    h = portal._horario_publico(ret["horario"])
    datos = s["datos"]
    return (f'<section data-resumen="1"><h2>Resumen</h2><dl>'
            f'<dt>Trámite</dt><dd data-campo="tramite">{e(TRAMITES[datos["tramite"]])}</dd>'
            f'<dt>Identificador</dt><dd data-campo="identificador">{e(datos["identificador"])}</dd>'
            f'<dt>Sucursal</dt><dd data-campo="sucursal">{e(h["sucursal_nombre"])} ({e(h["sucursal"])})</dd>'
            f'<dt>Fecha</dt><dd data-campo="fecha">{e(h["fecha"])}</dd>'
            f'<dt>Horario</dt><dd data-campo="hora">{e(h["hora"])}</dd>'
            f'<dt>Reservado hasta</dt><dd data-campo="vence">{e(ret["vence"])}</dd></dl></section>'
            f'<form method="post" action="/reservar/confirmar" data-formulario="confirmacion">'
            f'<input type="hidden" name="clave" value="{e(s["clave"])}">'
            f'<button type="submit" name="accion" value="confirmar" id="confirmar">Confirmar reservación</button></form>')


def html_resultado(r: dict) -> str:
    hist = "".join(f'<li>{e(x["ts"])} — {e(x["estado"])}</li>' for x in r["historial"])
    return (f'<section data-resultado="{e(r["estado"])}"><h2>Reservación {e(r["estado"].lower())}</h2>'
            f'<p>Folio: <strong id="folio" data-folio="{e(r["folio"])}">{e(r["folio"])}</strong></p><dl>'
            f'<dt>Trámite</dt><dd>{e(TRAMITES.get(r["tramite"], r["tramite"]))}</dd>'
            f'<dt>Identificador</dt><dd>{e(r["identificador"])}</dd>'
            f'<dt>Sucursal</dt><dd>{e(r["sucursal_nombre"])}</dd><dt>Fecha</dt><dd>{e(r["fecha"])}</dd>'
            f'<dt>Horario</dt><dd>{e(r["hora"])}</dd></dl><h3>Historial</h3><ul data-historial="1">{hist}</ul>'
            f'<p><a href="/reservar">Nueva solicitud</a></p></section>')


# ---------------------------------------------------------------------- aplicación
def crear_app(archivo: Path, token: str, token_control: str, semilla: int = 5, dias: int = 10,
              retencion_s: float = 300, sesion_s: float = 900, zona: str | None = None,
              horizonte_dias: int = 30, dias_habilitados: int = 15, tic_s: float = 0.5) -> FastAPI:
    portal = Portal(archivo, semilla=semilla, dias=dias, retencion_s=retencion_s, sesion_s=sesion_s, zona=zona,
                    horizonte_dias=horizonte_dias, dias_habilitados=dias_habilitados)
    fallos = Fallos()

    @asynccontextmanager
    async def ciclo(_app):
        """v1.6: el tiempo pasa en el portal (vencimientos y ventana nocturna simulada)."""
        rng = random.Random(semilla)

        async def tictac():
            while True:
                await asyncio.sleep(tic_s)
                try:
                    with portal.lock:
                        if portal.dias.tic(rng):
                            portal._guardar()
                except Exception:  # noqa: BLE001 - un fallo del reloj no debe tumbar el portal
                    logging.getLogger("portal_reservas").exception("fallo en el reloj del laboratorio")
        tarea = asyncio.create_task(tictac())
        try:
            yield
        finally:
            tarea.cancel()

    app = FastAPI(title="Portal de reservaciones de PRUEBA", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=ciclo)
    app.state.portal, app.state.fallos = portal, fallos

    def auth(authorization: str | None) -> None:
        if authorization != f"Bearer {token}":
            raise HTTPException(401, "token inválido")

    def control(x: str | None) -> None:
        if x != token_control:
            raise HTTPException(403, "ruta de laboratorio: falta X-Control-Token válido")

    def resp(pantalla: str, cuerpo: str, status: int = 200, err: ErrorPortal | None = None) -> HTMLResponse:
        return HTMLResponse(pagina(pantalla, cuerpo, err.mensaje if err else None, err.codigo if err else None),
                            status_code=status)

    def sesion_o_reinicio(request: Request):
        """Devuelve (token, sesión) o una respuesta 440 que pide empezar de nuevo."""
        tok = request.cookies.get(COOKIE)
        try:
            return tok, portal.sesion(tok)
        except ErrorPortal as ex:
            r = resp("datos", '<section><a href="/reservar" id="reiniciar">Empezar de nuevo</a></section>',
                     ex.http, ex)
            r.delete_cookie(COOKIE)
            return tok, r

    @app.middleware("http")
    async def inyectar(request: Request, call_next):
        p = request.url.path
        if p.startswith(("/reservar", "/cita")):
            pantalla = p.split("/")[2] if p.count("/") >= 2 else "datos"
            if fallos.latencia_ms:
                await asyncio.sleep(fallos.latencia_ms / 1000)
            if fallos.activo(pantalla, {"limite"}):           # v2.0: el proveedor limita (cuota, demasiadas peticiones)
                return HTMLResponse(pagina("datos", "", "Demasiadas solicitudes: inténtalo más tarde.", "LIMITE"),
                                    status_code=429, headers={"Retry-After": "600"})
            if request.method == "POST":
                modo = fallos.activo(pantalla, {"error_interno", "cortar"})
                if modo == "error_interno":
                    return HTMLResponse(pagina("datos", "", "Error interno del portal de prueba.", "ERROR_INTERNO"),
                                        status_code=500)
                if modo == "cortar":
                    async def nada():
                        yield b"<!doctype html>"
                        raise ConnectionResetError("corte simulado antes de procesar")
                    return StreamingResponse(nada(), media_type="text/html", headers={"Content-Length": "500"})
        return await call_next(request)

    @app.get("/salud")
    def salud():
        return {"ok": True, "servicio": "portal_reservas", **portal.resumen()}

    @app.get("/")
    def raiz():
        return RedirectResponse("/reservar", 303)

    # ---------------------------------------------------------------- pantalla 1
    @app.get("/reservar", response_class=HTMLResponse)
    def pantalla_datos(request: Request):
        tok = request.cookies.get(COOKIE)
        try:
            s = portal.sesion(tok)
        except ErrorPortal:
            tok, s = portal.nueva_sesion(), None
        nombres = fallos.renombrar if fallos.activo("datos", {"renombrar_campo"}) else {}
        r = resp("datos", html_datos({}, {}, nombres))
        if s is None:
            r.set_cookie(COOKIE, tok, httponly=True, samesite="lax")
        return r

    @app.post("/reservar/datos", response_class=HTMLResponse)
    async def enviar_datos(request: Request):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        form = await leer_formulario(request)
        inverso = {v: k for k, v in fallos.renombrar.items()}
        f = {inverso.get(k, k): str(v) for k, v in form.items()}
        try:
            errores = portal.validar_datos(tok, f)
        except ErrorPortal as ex:
            return resp("datos", html_datos(f, {}, {}), ex.http, ex)
        if errores:
            valores = {k: v for k, v in f.items() if not k.endswith("_confirmacion")}
            return resp("datos", html_datos(valores, errores, {}), 422,
                        ErrorPortal("DATOS_INVALIDOS", "Revisa los campos marcados."))
        return RedirectResponse("/reservar/disponibilidad", 303)

    # ---------------------------------------------------------------- pantalla 2
    @app.get("/reservar/disponibilidad", response_class=HTMLResponse)
    def pantalla_disponibilidad(request: Request, region: str | None = None, sucursal: str | None = None):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        if s["paso"] < 2 or not s["datos"]:
            return RedirectResponse("/reservar", 303)
        return resp("disponibilidad", html_disponibilidad(portal, s, region or None, sucursal or None, {}))

    @app.post("/reservar/horario", response_class=HTMLResponse)
    async def elegir_horario(request: Request):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        form = await leer_formulario(request)
        region, sucursal = form.get("region") or None, form.get("sucursal") or None
        hid = str(form.get("horario") or "")
        try:
            if not hid:
                raise ErrorPortal("HORARIO_REQUERIDO", "Selecciona un horario.", campo="horario", http=422)
            portal.retener(tok, hid)
        except ErrorPortal as ex:
            return resp("disponibilidad",
                        html_disponibilidad(portal, s, region, sucursal, {"horario": ex.mensaje}) if s.get("datos")
                        else "", ex.http, ex)
        return RedirectResponse("/reservar/confirmacion", 303)

    # ---------------------------------------------------------------- pantalla 3
    @app.get("/reservar/confirmacion", response_class=HTMLResponse)
    def pantalla_confirmacion(request: Request):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        with portal.lock:
            portal._vencer_retenciones()
            if s["paso"] == 4 and s.get("folio"):
                return RedirectResponse(f"/reservar/resultado/{quote(s['folio'])}", 303)
            return resp("confirmacion", html_confirmacion(portal, s))

    @app.post("/reservar/confirmar", response_class=HTMLResponse)
    async def confirmar(request: Request):
        form = await leer_formulario(request)
        clave = str(form.get("clave") or "")
        tok = request.cookies.get(COOKIE)
        try:
            r, _repetida = portal.confirmar(tok, clave)
        except ErrorPortal as ex:
            try:
                s = portal.sesion(tok)
                cuerpo = html_confirmacion(portal, s) if ex.codigo != "RETENCION_VENCIDA" else \
                    '<section><a href="/reservar/disponibilidad" id="volver">Elegir otro horario</a></section>'
            except ErrorPortal:
                cuerpo = '<section><a href="/reservar" id="reiniciar">Empezar de nuevo</a></section>'
            return resp("confirmacion", cuerpo, ex.http, ex)
        if fallos.activo("confirmar", {"cortar_tras_confirmar"}):
            # La reservación YA quedó confirmada y guardada; el cliente pierde la respuesta.
            cuerpo = b"<!doctype html><html><body data-pantalla=\"resultado\">" + b" " * 200

            async def a_medias():
                yield cuerpo[:40]
                raise ConnectionResetError("corte simulado tras confirmar")
            # Content-Length mayor que lo enviado: el cliente detecta el corte (como en un corte real)
            return StreamingResponse(a_medias(), media_type="text/html", headers={"Content-Length": str(len(cuerpo))})
        return RedirectResponse(f"/reservar/resultado/{quote(r['folio'])}", 303)

    # ---------------------------------------------------------------- pantalla 4
    @app.get("/reservar/resultado/{folio}", response_class=HTMLResponse)
    def pantalla_resultado(folio: str, request: Request):
        tok, s = sesion_o_reinicio(request)
        if not isinstance(s, dict):
            return s
        r = portal.reservacion(folio)
        if r is None or s.get("folio") != folio:
            # solo la sesión que confirmó ve el detalle por pantalla; el motor verifica por la API
            raise HTTPException(404, "folio no encontrado en esta sesión")
        return resp("resultado", html_resultado(r))

    # ---------------------------------------------------------------- API JSON (motor)
    @app.get("/api/catalogo")
    def catalogo(authorization: str | None = Header(None)):
        auth(authorization)
        return {"regiones": REGIONES, "tramites": TRAMITES, "operaciones": OPERACIONES,
                "sucursales": list(portal.d["sucursales"].values())}

    @app.get("/api/disponibilidad")
    def api_disponibilidad(region: str | None = None, sucursal: str | None = None, tramite: str | None = None,
                           authorization: str | None = Header(None)):
        auth(authorization)
        items = portal.disponibilidad(region=region, sucursal=sucursal, tramite=tramite)
        return {"horarios": [{k: h[k] for k in ("id", "sucursal", "sucursal_nombre", "region", "fecha", "hora",
                                                 "capacidad", "libres", "tramites")} for h in items]}

    def _publica(r: dict) -> dict:
        return {k: r.get(k) for k in ("folio", "estado", "clave", "identificador", "tramite", "operacion", "sucursal",
                                      "sucursal_nombre", "region", "fecha", "hora", "horario", "confirmada_en",
                                      "historial", "modo", "dia", "reprograma")}

    @app.get("/api/reservaciones/{folio}")
    def api_reservacion(folio: str, authorization: str | None = Header(None)):
        auth(authorization)
        r = portal.reservacion(folio)
        if r is None:
            raise HTTPException(404, "folio inexistente")
        return _publica(r)

    @app.get("/api/reservaciones")
    def api_por_clave(clave: str, authorization: str | None = Header(None)):
        auth(authorization)
        r = portal.por_clave(clave)
        return {"reservacion": _publica(r) if r else None}

    @app.post("/api/reservaciones/{folio}/cancelar")
    def api_cancelar(folio: str, authorization: str | None = Header(None)):
        auth(authorization)
        try:
            return _publica(portal.cancelar(folio))
        except ErrorPortal as ex:
            raise HTTPException(ex.http, ex.mensaje)

    # ---------------------------------------------------------------- laboratorio
    @app.get("/control/estado")
    def ctl_estado(x_control_token: str | None = Header(None)):
        control(x_control_token)
        return {"fallos": fallos.estado(), **portal.resumen()}

    @app.post("/control/fallos")
    async def ctl_fallos(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        return fallos.configurar(await request.json())

    @app.post("/control/caducar-sesiones")
    def ctl_caducar(x_control_token: str | None = Header(None)):
        control(x_control_token)
        return {"caducadas": portal.caducar_sesiones()}

    @app.post("/control/agotar")
    async def ctl_agotar(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        ids = d.get("horarios") or [d["horario"]]
        for hid in ids:
            if hid not in portal.d["horarios"]:
                raise HTTPException(404, f"horario inexistente: {hid}")
            portal.agotar(hid)
        return {"agotados": ids}

    @app.post("/control/liberar")
    async def ctl_liberar(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        if d["horario"] not in portal.d["horarios"]:
            raise HTTPException(404, "horario inexistente")
        portal.liberar(d["horario"], int(d.get("unidades", 1)))
        return {"ok": True}

    @app.get("/control/registro-ejemplo")
    def ctl_registro(n: int = 0, x_control_token: str | None = Header(None)):
        control(x_control_token)
        ident, reg = portal.registro_ejemplo(n)
        return {"identificador": ident, **reg}

    @app.post("/control/registros")
    def ctl_registros(total: int = 300, x_control_token: str | None = Header(None)):
        control(x_control_token)
        if not 1 <= total <= 20000:
            raise HTTPException(422, "total entre 1 y 20000")
        return {"registros": portal.ampliar_registros(total)}

    @app.get("/control/historial")
    def ctl_historial(desde: int = 0, x_control_token: str | None = Header(None)):
        control(x_control_token)
        with portal.lock:
            return {"eventos": portal.d["historial"][desde:], "total": len(portal.d["historial"])}

    @app.get("/control/reservaciones")
    def ctl_reservaciones(x_control_token: str | None = Header(None)):
        control(x_control_token)
        with portal.lock:
            return {"reservaciones": [_publica(r) for r in portal.d["reservaciones"].values()],
                    "ocupacion": {h["id"]: h["ocupados"] for h in portal.d["horarios"].values() if h["ocupados"]},
                    "ocupacion_dias": {k: {"ocupados": x["ocupados"], "capacidad": x["capacidad"]}
                                       for k, x in portal.d["dias"].items() if x["ocupados"]}}

    # v1.6: modo POR DÍA (pantallas /cita, API /api/dias y /api/eventos, controles de días)
    from . import servidor_dias
    servidor_dias.montar(app, portal, fallos, auth, control, CSS)

    @app.exception_handler(ErrorPortal)
    async def _err(_req, ex: ErrorPortal):
        return JSONResponse({"codigo": ex.codigo, "detalle": ex.mensaje}, status_code=ex.http)

    return app


__all__ = ["crear_app", "COOKIE", "MODOS", "CAMPOS_DATOS"]

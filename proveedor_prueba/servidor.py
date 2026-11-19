"""Servidor HTTP del proveedor de prueba (proceso independiente del motor).

Todo es ficticio salvo la red: las peticiones, respuestas, códigos HTTP, latencias y cortes
de conexión son reales. El contrato está en CONTRATO.md.

Dos familias de rutas:
- /v1/...       API "del proveedor" (lo que consumiría el motor). Requiere `Authorization: Bearer`.
- /control/...  Laboratorio: inyectar fallos, liberar recursos, activar la simulación y leer el
                historial de cambios (la verdad con la que se audita al motor). Requiere
                `X-Control-Token`. Un proveedor real nunca ofrecería estas rutas.
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .estado import REGIONES, Proveedor, iso, ahora

MODOS = {"normal", "429", "500", "503", "401", "lento", "cortar", "colgar", "malformada"}


class Fallos:
    """Fallos inyectados. Se aplican solo a las rutas /v1 de disponibilidad."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.modo = "normal"
        self.retry_after = 2
        self.latencia_ms = [0, 0]
        self.prob_fallo = 1.0
        self.restantes: int | None = None      # None = sin límite
        self.regiones: list[str] | None = None
        self.colgar_s = 30.0
        self.repetir_antigua = 0               # cuántas respuestas antiguas servir
        self.cambiar_entre_paginas = 0         # cuántas veces cambiar datos entre página y página
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
                    self.restantes = None      # un modo nuevo sin límite explícito aplica siempre
            for k in ("retry_after", "prob_fallo", "restantes", "regiones", "colgar_s", "repetir_antigua",
                      "cambiar_entre_paginas"):
                if k in d:
                    setattr(self, k, d[k])
            if "latencia_ms" in d:
                v = d["latencia_ms"]
                self.latencia_ms = [v, v] if isinstance(v, (int, float)) else list(v)
            return self.estado()

    def estado(self) -> dict:
        return {k: getattr(self, k) for k in ("modo", "retry_after", "latencia_ms", "prob_fallo", "restantes",
                                               "regiones", "colgar_s", "repetir_antigua", "cambiar_entre_paginas",
                                               "aplicados")}

    def elegir(self, region: str, rng) -> str:
        """Decide el modo para ESTA petición y descuenta `restantes`."""
        with self.lock:
            if self.modo == "normal" or (self.regiones and region not in self.regiones):
                return "normal"
            if self.restantes is not None and self.restantes <= 0:
                return "normal"
            if rng.random() >= self.prob_fallo:
                return "normal"
            if self.restantes is not None:
                self.restantes -= 1
            self.aplicados[self.modo] = self.aplicados.get(self.modo, 0) + 1
            return self.modo

    def tomar(self, nombre: str) -> bool:
        with self.lock:
            if getattr(self, nombre) > 0:
                setattr(self, nombre, getattr(self, nombre) - 1)
                self.aplicados[nombre] = self.aplicados.get(nombre, 0) + 1
                return True
            return False


class Cuota:
    """Límite real de peticiones por credencial (ventana deslizante de 60 s)."""

    def __init__(self, por_min: int):
        self.por_min = por_min
        self.marcas: dict[str, deque] = {}
        self.lock = threading.Lock()

    def permitir(self, clave: str) -> float | None:
        """None si se permite; si no, segundos que hay que esperar."""
        if self.por_min <= 0:
            return None
        now = time.monotonic()
        with self.lock:
            q = self.marcas.setdefault(clave, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.por_min:
                return max(0.5, 60 - (now - q[0]))
            q.append(now)
            return None


def _cursor(version: int, offset: int) -> str:
    return base64.urlsafe_b64encode(f"{version}:{offset}".encode()).decode().rstrip("=")


def _leer_cursor(c: str) -> tuple[int, int]:
    try:
        v, o = base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)).decode().split(":")
        return int(v), int(o)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "cursor inválido") from e


def crear_app(archivo: Path | None, token: str, token_control: str, semilla: int = 7,
              recursos_por_region: int = 12, cuota_por_min: int = 1200) -> FastAPI:
    if not token or not token_control:
        raise ValueError("faltan las credenciales de prueba (PROVEEDOR_TOKEN y PROVEEDOR_TOKEN_CONTROL)")
    prov = Proveedor(archivo, semilla=semilla, recursos_por_region=recursos_por_region)
    fallos = Fallos()
    cuota = Cuota(cuota_por_min)
    sim = {"activa": False, "cada_ms": 500, "cfg": {}, "pasos": 0}
    antiguas: dict[str, deque] = {r: deque(maxlen=20) for r in REGIONES}   # respuestas ya servidas
    metricas = {"peticiones": 0, "por_codigo": {}, "por_region": {r: 0 for r in REGIONES}}
    parar = threading.Event()
    sucio = threading.Event()

    def bucle() -> None:
        """Hilo de fondo: vencimientos, liberaciones programadas, simulación y guardado."""
        ultimo_paso = ultimo_guardado = 0.0
        while not parar.wait(0.05):
            now = time.monotonic()
            if prov.tick():
                sucio.set()
            if sim["activa"] and (now - ultimo_paso) * 1000 >= sim["cada_ms"]:
                ultimo_paso = now
                if prov.paso_aleatorio(sim["cfg"]):
                    sucio.set()
                sim["pasos"] += 1
            if sucio.is_set() and now - ultimo_guardado >= 0.5:
                sucio.clear()
                ultimo_guardado = now
                prov.guardar()

    @asynccontextmanager
    async def lifespan(_app):
        hilo = threading.Thread(target=bucle, name="proveedor-reloj", daemon=True)
        hilo.start()
        yield
        parar.set()
        hilo.join(5)
        prov.guardar()

    app = FastAPI(title="Proveedor de disponibilidad de PRUEBA (datos ficticios)", version="1.3",
                  lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.proveedor, app.state.fallos = prov, fallos

    @app.middleware("http")
    async def contar(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        resp.headers["X-Tiempo-Servidor-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        if request.url.path.startswith("/v1/"):
            metricas["peticiones"] += 1
            k = str(resp.status_code)
            metricas["por_codigo"][k] = metricas["por_codigo"].get(k, 0) + 1
        return resp

    def etag(region: str, version: int) -> str:
        # Incluye el número de arranque: tras un corte brusco el proveedor puede perder sus
        # últimos cambios y volver a pasar por una versión ya usada con OTROS datos. Con la
        # versión sola, el motor recibiría un 304 falso y no vería esos cambios.
        return f'W/"{region}-{prov.arranques}-{version}"'

    def auth(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "falta la credencial", headers={"WWW-Authenticate": "Bearer"})
        if not secrets.compare_digest(authorization[7:].encode(), token.encode()):
            raise HTTPException(401, "credencial no válida", headers={"WWW-Authenticate": "Bearer"})
        espera = cuota.permitir(authorization[7:])
        if espera is not None:
            raise HTTPException(429, "cuota de peticiones superada", headers={"Retry-After": f"{espera:.0f}"})
        return authorization[7:]

    def control(x_control_token: str | None) -> None:
        if not x_control_token or not secrets.compare_digest(x_control_token.encode(), token_control.encode()):
            raise HTTPException(403, "ruta de laboratorio: falta X-Control-Token válido")

    # ---------------------------------------------------------------- API del proveedor
    @app.get("/salud")
    def salud():
        return {"ok": True, "arranques": prov.arranques, "regiones": list(REGIONES), "datos": "ficticios"}

    @app.get("/v1/regiones")
    def regiones(authorization: str | None = Header(None)):
        auth(authorization)
        with prov.lock:
            return {"regiones": [{"id": r, "nombre": n, "version": prov.versiones[r],
                                  "recursos": sum(1 for x in prov.recursos.values() if x.region == r)}
                                 for r, n in REGIONES.items()]}

    @app.get("/v1/regiones/{region}/disponibilidad")
    async def disponibilidad(region: str, request: Request, limite: int = 50, cursor: str | None = None,
                             authorization: str | None = Header(None), if_none_match: str | None = Header(None)):
        auth(authorization)
        if region not in REGIONES:
            raise HTTPException(404, "región desconocida")
        limite = max(1, min(limite, 200))
        metricas["por_region"][region] += 1
        lo, hi = fallos.latencia_ms
        modo = fallos.elegir(region, prov.rng)
        if hi > 0:
            await asyncio.sleep(prov.rng.uniform(lo, hi) / 1000)
        if modo == "lento":
            await asyncio.sleep(prov.rng.uniform(1.0, 3.0))
        elif modo == "colgar":
            await asyncio.sleep(fallos.colgar_s)
        elif modo in ("429", "503"):
            return JSONResponse({"error": "demasiadas peticiones" if modo == "429" else "servicio no disponible"},
                                int(modo), headers={"Retry-After": str(fallos.retry_after)})
        elif modo == "500":
            return JSONResponse({"error": "fallo interno simulado"}, 500)
        elif modo == "401":
            return JSONResponse({"error": "credencial revocada"}, 401)

        # Respuesta antigua repetida (p. ej. caché intermedia mal configurada)
        if cursor is None and antiguas[region] and fallos.tomar("repetir_antigua"):
            cuerpo = antiguas[region][0]
            return Response(cuerpo, media_type="application/json", headers={"X-Repetida": "1"})

        version, items = prov.vista(region)
        offset = 0
        if cursor:
            v_cursor, offset = _leer_cursor(cursor)
            if v_cursor != version:
                # Los datos cambiaron entre páginas: la paginación ya no es coherente.
                return JSONResponse({"error": "cursor_obsoleto", "version_actual": version}, 409)
        elif if_none_match and if_none_match == etag(region, version):
            return Response(status_code=304, headers={"ETag": etag(region, version)})
        pagina = items[offset:offset + limite]
        siguiente = _cursor(version, offset + limite) if offset + limite < len(items) else None
        if modo == "malformada" and pagina:
            pagina = [dict(x) for x in pagina]
            pagina[0].pop("disponibles", None)
        datos = {"region": region, "version": version, "generado_en": iso(ahora()), "total": len(items),
                 "recursos": pagina, "siguiente": siguiente}
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        if cursor is None and siguiente is None and modo == "normal":
            antiguas[region].append(cuerpo)
        if siguiente and fallos.tomar("cambiar_entre_paginas"):
            # Cambia un recurso de la región justo después de servir esta página.
            cand = [r for r in prov.recursos.values() if r.region == region and r.estado == "ABIERTO"]
            if cand:
                r = cand[-1]
                if r.disponibles(ahora()) < r.capacidad:
                    prov.liberar(r.id, 1, None, "cambio durante la consulta")
                else:
                    prov.reservar(r.id, 1, "Titular Ficticio 000")
                sucio.set()
        if modo == "cortar":
            async def a_medias():
                yield cuerpo[: max(1, len(cuerpo) // 2)]
                raise ConnectionResetError("corte simulado")
            return StreamingResponse(a_medias(), media_type="application/json",
                                     headers={"Content-Length": str(len(cuerpo))})
        return Response(cuerpo, media_type="application/json", headers={"ETag": etag(region, version)})

    @app.post("/v1/reservas", status_code=201)
    async def crear_reserva(request: Request, authorization: str | None = Header(None)):
        auth(authorization)
        d = await request.json()
        try:
            res = prov.reservar(str(d["recurso"]), int(d.get("unidades", 1)), str(d.get("titular", "Ficticio")))
        except KeyError as e:
            raise HTTPException(404, "recurso desconocido") from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        sucio.set()
        return res

    @app.delete("/v1/reservas/{res_id}")
    def borrar_reserva(res_id: str, authorization: str | None = Header(None)):
        auth(authorization)
        try:
            res = prov.cancelar_reserva(res_id)
        except KeyError as e:
            raise HTTPException(404, "reserva desconocida") from e
        sucio.set()
        return res

    # ---------------------------------------------------------------- laboratorio
    @app.get("/control/estado")
    def estado(x_control_token: str | None = Header(None)):
        control(x_control_token)
        with prov.lock:
            return {"arranques": prov.arranques, "versiones": prov.versiones, "secuencia": prov.secuencia,
                    "cambios": len(prov.historial), "reservas_activas": sum(
                        1 for r in prov.reservas.values() if r["estado"] == "ACTIVA"),
                    "programadas": len(prov.programadas), "fallos": fallos.estado(),
                    "simulacion": {k: sim[k] for k in ("activa", "cada_ms", "pasos", "cfg")}, "metricas": metricas}

    @app.post("/control/fallos")
    async def configurar_fallos(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        return fallos.configurar(await request.json())

    @app.post("/control/liberar")
    async def liberar(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        try:
            r = prov.liberar(d["recurso"], int(d.get("unidades", 1)), d.get("ventana_s"), d.get("motivo", "liberación"))
        except KeyError as e:
            raise HTTPException(404, "recurso desconocido") from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        sucio.set()
        return r

    @app.post("/control/programar")
    async def programar(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        if d["recurso"] not in prov.recursos:
            raise HTTPException(404, "recurso desconocido")
        p = prov.programar(d["recurso"], int(d.get("unidades", 1)), float(d.get("en_s", 1)), d.get("ventana_s"))
        sucio.set()
        return p

    @app.post("/control/cancelar-recurso")
    async def cancelar_recurso(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        try:
            prov.cancelar_recurso(d["recurso"])
        except KeyError as e:
            raise HTTPException(404, "recurso desconocido") from e
        sucio.set()
        return {"recurso": d["recurso"], "estado": "CANCELADO"}

    @app.post("/control/simulacion")
    async def simulacion(request: Request, x_control_token: str | None = Header(None)):
        control(x_control_token)
        d = await request.json()
        sim["activa"] = bool(d.get("activa", True))
        sim["cada_ms"] = float(d.get("cada_ms", sim["cada_ms"]))
        sim["cfg"] = {k: d[k] for k in ("p_rafaga", "ventana_s", "p_cancelar_recurso") if k in d}
        return {k: sim[k] for k in ("activa", "cada_ms", "cfg")}

    @app.get("/control/historial")
    def historial(desde: int = 0, x_control_token: str | None = Header(None)):
        control(x_control_token)
        with prov.lock:
            return {"cambios": [h for h in prov.historial if h["n"] > desde], "secuencia": prov.secuencia}

    @app.post("/control/reiniciar")
    def reiniciar(x_control_token: str | None = Header(None)):
        """Inventario inicial, sin fallos ni simulación (aísla pruebas que comparten el proceso)."""
        control(x_control_token)
        sim["activa"] = False
        fallos.configurar({"reset": True})
        for q in antiguas.values():
            q.clear()
        prov.reiniciar_datos(recursos_por_region)
        return {"ok": True}

    @app.post("/control/guardar")
    def guardar(x_control_token: str | None = Header(None)):
        control(x_control_token)
        prov.guardar()
        return {"ok": True}

    return app

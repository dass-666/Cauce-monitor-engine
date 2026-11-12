"""Aplicación FastAPI: API + panel web + motor en segundo plano."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import settings
from .db import Database
from .engine.scheduler import Scheduler
from .models import User
from .security import hash_password

log = logging.getLogger("monitoreo")
WEB_DIR = Path(__file__).parent / "web"


def bootstrap(db: Database, seed_demo: bool) -> str | None:
    """Crea tablas, datos base y el administrador inicial. Devuelve la contraseña generada si la hubo."""
    from .simulator.seed import seed_base, seed_demo as do_seed_demo
    report = db.migrate(make_backup=True)  # respaldo automático si cambia el esquema de una base con datos
    if report.steps:
        log.warning("Migración de esquema: %s", "; ".join(report.steps))
    seed_base(db)
    if seed_demo:
        import os
        do_seed_demo(db, port=int(os.environ.get("MONITOREO_PUERTO", "8000")))
    generated = None
    with db.session() as s:
        if not s.scalar(select(User.id).where(User.role == "admin").limit(1)):
            pwd = settings.admin_password
            if not pwd:
                pwd = generated = secrets.token_urlsafe(12)
            s.add(User(username=settings.admin_user, password_hash=hash_password(pwd), role="admin"))
    # v1.1: la recuperación (entregas a medias, simulaciones interrumpidas...) la ejecuta el
    # planificador cuando obtiene el liderazgo, para no interferir con otra instancia activa.
    return generated


def create_app(db_url: str | None = None, start_engine: bool | None = None, seed_demo: bool | None = None) -> FastAPI:
    db = Database(db_url or settings.db_url)
    start = settings.start_engine if start_engine is None else start_engine
    seed = settings.seed_demo if seed_demo is None else seed_demo

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        generated = bootstrap(db, seed)
        if generated:
            msg = (f"\n{'=' * 64}\n  Usuario administrador creado: '{settings.admin_user}'\n"
                   f"  Contraseña generada (cámbiala en el panel): {generated}\n"
                   f"  Para fijarla tú, define MONITOREO_ADMIN_PASSWORD en .env\n{'=' * 64}\n")
            print(msg, flush=True)
            (settings.data_dir / "CREDENCIAL_INICIAL.txt").write_text(
                msg + "\nBorra este archivo después de iniciar sesión.\n", encoding="utf-8")
        if start:
            app.state.scheduler = Scheduler(db)
            app.state.scheduler.start()
        yield
        if start and app.state.scheduler:
            await app.state.scheduler.stop()
        db.engine.dispose()

    app = FastAPI(title="Motor de Monitoreo de Disponibilidad", version="1.1.0", lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.db = db
    app.state.scheduler = None

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Nunca devolver trazas internas al cliente (pueden revelar rutas o datos).
        log.exception("error no controlado en %s", request.url.path)
        return JSONResponse({"detail": "Error interno"}, status_code=500)

    from .api.routes import router
    from .api.test_api import router as test_router
    from .api.observability import router as obs_router
    from .emuladores.telegram import router as telegram_emulator
    app.include_router(router)
    app.include_router(obs_router)
    app.include_router(test_router)
    app.include_router(telegram_emulator)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app

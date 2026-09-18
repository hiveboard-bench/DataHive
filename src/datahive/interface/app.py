"""FastAPI app factory for `datahive serve`. Binds to localhost only --
the CLI's `serve` command never exposes a --host flag, so there is no way
to bind this publicly by accident."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from datahive.interface.api import build_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(samples_root: Path) -> FastAPI:
    app = FastAPI(title="DataHive", docs_url="/api/docs")
    app.include_router(build_router(samples_root))
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app

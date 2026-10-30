from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router
from .core.config import settings
from .db.session import init_db

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Tynode", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
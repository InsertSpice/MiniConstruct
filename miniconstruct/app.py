from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from miniconstruct.api.routes import router
from miniconstruct.h3.guide_acquisition import require_guides


APP_ROOT = Path(__file__).resolve().parent.parent
PACKAGED_WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ROOT = PACKAGED_WEB_ROOT if PACKAGED_WEB_ROOT.exists() else APP_ROOT / "web"

@asynccontextmanager
async def lifespan(_: FastAPI):
    require_guides()
    yield


app = FastAPI(
    title="MiniConstruct",
    description="Local MiniMax H3 structured-prompt workbench",
    version="0.3.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.middleware("http")
async def revalidate_frontend_assets(request, call_next):
    """Keep unhashed browser modules from different application versions mixing."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")

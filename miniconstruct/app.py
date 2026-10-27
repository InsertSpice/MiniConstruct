from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from miniconstruct.h3.guide_acquisition import require_guides

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from miniconstruct.api.routes import router
from miniconstruct.api.execution_routes import router as execution_router
from miniconstruct.api.take_routes import router as take_router
from miniconstruct.api.observation_routes import router as observation_router


APP_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
PACKAGED_WEB_ROOT = PACKAGE_ROOT / "web"


def resolve_web_root(app_root: Path, package_root: Path) -> Path:
    """Use checkout assets during development and bundled assets after installation."""
    source_web_root = app_root / "web"
    is_source_checkout = (
        (app_root / "pyproject.toml").is_file()
        and (package_root / "app.py").is_file()
        and (source_web_root / "index.html").is_file()
    )
    if is_source_checkout:
        return source_web_root
    packaged_web_root = package_root / "web"
    return packaged_web_root if packaged_web_root.is_dir() else source_web_root


WEB_ROOT = resolve_web_root(APP_ROOT, PACKAGE_ROOT)

@asynccontextmanager
async def lifespan(_: FastAPI):
    require_guides()
    yield


app = FastAPI(
    title="MiniConstruct",
    description="Local MiniMax H3 structured-prompt workbench",
    version="0.5.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(execution_router)
app.include_router(take_router)
app.include_router(observation_router)
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.exception_handler(RequestValidationError)
async def bounded_generation_validation_detail(request: Request, exc: RequestValidationError):
    """Expose useful field failures without echoing request bodies or data URLs."""
    if request.url.path != "/api/generate-stream":
        return JSONResponse(status_code=422, content={"detail": [{
            "loc": list(error.get("loc", ())), "msg": str(error.get("msg", "Invalid request"))[:240],
            "type": str(error.get("type", "validation_error"))[:120],
        } for error in exc.errors()[:8]]})
    fields = []
    for error in exc.errors()[:8]:
        location = [str(part)[:80] for part in error.get("loc", ()) if part != "body"]
        fields.append({"path": ".".join(location) or "request", "message": str(error.get("msg", "Invalid value"))[:240],
                       "type": str(error.get("type", "validation_error"))[:120]})
    return JSONResponse(status_code=422, content={"detail": {
        "code": "generation_request_invalid", "message": "Generation request validation failed.", "fields": fields,
    }})


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

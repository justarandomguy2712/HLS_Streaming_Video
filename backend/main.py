"""Điểm vào ASGI của ứng dụng."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.health import router as health_router
from backend.api.uploads import router as uploads_router
from backend.api.videos import router as videos_router
from backend.config import PROJECT_ROOT, load_settings
from backend.database import initialize_database
from backend.services.upload_service import UploadService
from backend.storage.r2_storage import R2Storage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    if not settings.enable_legacy_lab_http or settings.worker_endpoint:
        raise RuntimeError("Deployment dùng companion: python -m backend.local_processing. HTTP cũ chỉ dành cho lab.")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.processing_dir.mkdir(parents=True, exist_ok=True)
    initialize_database(settings.database_path)
    app.state.settings = settings
    storage = R2Storage(settings.r2) if settings.r2 else None
    upload_service = UploadService(settings, storage)
    app.state.r2_storage = storage
    app.state.upload_service = upload_service
    upload_service.start()
    try:
        yield
    finally:
        await run_in_threadpool(upload_service.close)
        if storage:
            storage.client.s3.close()


app = FastAPI(
    title="HLS Binary Cloud Streaming",
    description="API thư viện video, upload MP4, tạo HLS và phát từ R2.",
    version="0.8.1",
    lifespan=lifespan,
    redoc_url=None,
)
@app.middleware("http")
async def lab_loopback_only(request: Request, call_next):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        return JSONResponse({"detail": "HTTP lab chỉ nhận kết nối loopback."}, status_code=403)
    return await call_next(request)


app.include_router(health_router)
app.include_router(uploads_router)
app.include_router(videos_router)
app.mount("/admin-js", StaticFiles(directory=PROJECT_ROOT / "admin" / "js"), name="admin-js")
app.mount("/js", StaticFiles(directory=PROJECT_ROOT / "frontend" / "js"), name="js")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "admin" / "index.html", media_type="text/html")

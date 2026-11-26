"""Companion HTTP cho Admin, chỉ lắng nghe loopback trên Windows."""

import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from urllib.parse import unquote
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.uploads import accept_mp4
from backend.config import PROJECT_ROOT, Settings, load_settings
from backend.database import initialize_database, recover_processing
from backend.local_processing import processing_lock
from backend.processing.system_metrics import SystemMetrics
from backend.services.browser_jobs import BrowserJobs
from backend.services.upload_service import UploadService
from backend.storage.r2_storage import R2Storage
from backend.worker_client import WorkerClient, WorkerError

HOST = "127.0.0.1"
PORT = 8765


def create_app(settings: Settings, *, port: int = PORT) -> FastAPI:
    if not settings.worker_endpoint or not settings.processing_service_token or not settings.r2:
        raise ValueError("Cần cấu hình R2, WORKER_ENDPOINT và PROCESSING_SERVICE_TOKEN cho companion.")
    host = f"{HOST}:{port}"
    local_origin = f"http://{host}"
    origins = [local_origin]
    if settings.browser_admin_origin:
        origins.append(settings.browser_admin_origin)
    frontend = PROJECT_ROOT / "frontend"
    csp = (f"default-src 'self'; connect-src 'self' {settings.worker_endpoint}; media-src blob:; "
           "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")

    @asynccontextmanager
    async def lifespan(app):
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        settings.processing_dir.mkdir(parents=True, exist_ok=True)
        initialize_database(settings.database_path)
        with processing_lock(settings.database_path):
            recover_processing(settings.database_path)
            client = WorkerClient(settings.worker_endpoint, settings.processing_service_token)
            storage = R2Storage(settings.r2)
            service = UploadService(settings, storage)
            jobs = BrowserJobs(service, client)
            try:
                service.remove_abandoned_uploads()
                app.state.upload_service = service
                app.state.jobs = jobs
                app.state.metrics = SystemMetrics(settings.enable_gpu_monitoring, settings.progress_update_interval_ms / 1000)
                app.state.client = client
                jobs.start()
                try:
                    yield
                finally:
                    await run_in_threadpool(jobs.close)
            finally:
                storage.client.s3.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type", "X-Filename", "X-Video-Title"])

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        if (request.client is None or request.client.host != HOST or request.headers.get("host") != host
                or request.headers.get("origin", local_origin) not in origins):
            return JSONResponse({"detail": "Kết nối hoặc origin không được companion cấp quyền."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = csp
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def admin(request: Request, authorization: str = Header(default="")) -> str:
        match = re.fullmatch(r"Bearer ([0-9a-f]{64})", authorization)
        if not match:
            raise HTTPException(401, "Cần phiên đăng nhập Admin.")
        try:
            who = request.app.state.client.request("GET", "/api/auth/me", token=match[1])
        except WorkerError as exc:
            status = exc.status if exc.status in {401, 403} else 503
            raise HTTPException(status, "Worker chưa xác nhận phiên Admin.") from exc
        if who.get("role") != "ADMIN" or not isinstance(who.get("id"), str):
            raise HTTPException(403, "Chức năng chỉ dành cho Admin.")
        return who["id"]

    @app.get("/api/companion/jobs")
    def list_jobs(request: Request, owner: str = Depends(admin)):
        jobs = request.app.state.jobs.list(owner)
        metrics = request.app.state.metrics.snapshot() if any(
            job["phase"] in {"PROCESSING", "UPLOADING", "VERIFYING"} for job in jobs
        ) else {}
        return {"jobs": jobs, "system": metrics, "interval_ms": settings.progress_update_interval_ms}

    @app.post("/api/companion/jobs", status_code=202)
    async def upload(
        request: Request, owner: str = Depends(admin), x_filename: str = Header(default=""),
        x_video_title: str = Header(default=""),
    ):
        try:
            title = unquote(x_video_title, errors="strict").strip()
            if not title:
                title = unquote(x_filename, errors="strict")[:-4].strip()
            if not title or len(title.encode("utf-16-le")) > 400 or any(ord(char) < 32 for char in title):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Tiêu đề cần từ 1 đến 200 ký tự hợp lệ.") from exc
        return await accept_mp4(request, x_filename, owner=owner, title=title)

    @app.post("/api/companion/jobs/{video_id}/retry", status_code=202)
    def retry(request: Request, video_id: UUID, owner: str = Depends(admin)):
        if not request.app.state.jobs.retry(owner, str(video_id)):
            raise HTTPException(409, "Tác vụ này chưa thể thử lại.")
        return {"id": video_id, "status": "queued"}

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html", media_type="text/html")

    @app.get("/js/config.js")
    def config():
        return Response(f"export const WORKER_ENDPOINT = {json.dumps(settings.worker_endpoint)};",
                        media_type="text/javascript")

    app.mount("/js", StaticFiles(directory=frontend / "js"), name="frontend-js")
    return app


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(load_settings()), host=HOST, port=PORT, proxy_headers=False)

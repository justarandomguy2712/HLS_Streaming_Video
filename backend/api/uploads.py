"""API nhận MP4 dạng binary và tra cứu tiến trình xử lý."""

from collections.abc import Iterator
from contextlib import closing
from typing import Annotated, Literal
from urllib.parse import unquote
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from backend.binary.integrity import CHUNK_BYTES
from backend.database import get_video
from backend.processing.ffmpeg import InsufficientDiskSpace
from backend.services.upload_service import InvalidUpload, UploadTooLarge
from backend.storage.r2_client import is_missing_object
from backend.storage.r2_storage import is_media_name, media_key

router = APIRouter(prefix="/api/uploads", tags=["Upload MP4"])
UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "video/mp4": {"schema": {"type": "string", "format": "binary"}},
        },
    },
}
UPLOAD_ERRORS = {
    400: {"description": "Tên tệp hoặc body không hợp lệ."},
    413: {"description": "Vượt giới hạn dung lượng upload."},
    415: {"description": "Request phải là binary MP4."},
    507: {"description": "Ổ đĩa không đủ dung lượng."},
}


class AcceptedUpload(BaseModel):
    id: UUID
    status: Literal["uploaded"] = "uploaded"


class UploadStatus(BaseModel):
    id: UUID
    original_filename: str
    created_at: str
    status: Literal["uploaded", "processing", "ready", "failed"]
    playlist_key: str | None
    codecs: str | None
    error: str | None
    processing_note: str | None = None
    source_sha256: str | None = None
    online_status: Literal["disabled", "pending", "published"] = "disabled"
    storage: Literal["local", "r2"]
    playback_url: str | None = None


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload một tệp MP4 dưới dạng binary",
    openapi_extra=UPLOAD_BODY,
    responses=UPLOAD_ERRORS,
)
async def upload_mp4(
    request: Request,
    response: Response,
    filename: Annotated[str, Header(alias="X-Filename")] = "video.mp4",
) -> AcceptedUpload:
    accepted = await accept_mp4(request, filename)
    response.headers["Location"] = f"/api/uploads/{accepted.id}"
    return accepted


async def accept_mp4(request: Request, filename: str) -> AcceptedUpload:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    content_encoding = request.headers.get("content-encoding", "identity").lower()
    if content_type not in {"video/mp4", "application/octet-stream"} or content_encoding != "identity":
        raise HTTPException(415, "Gửi byte MP4 trực tiếp với Content-Type video/mp4 hoặc application/octet-stream.")

    try:
        filename = unquote(filename, errors="strict")
        raw_length = request.headers.get("content-length")
        length = int(raw_length) if raw_length is not None else None
    except ValueError as exc:
        raise HTTPException(400, "Tên tệp hoặc Content-Length không hợp lệ.") from exc
    if (len(filename) > 255 or not filename.lower().endswith(".mp4")
            or not filename[:-4].strip() or any(char in '/\\:' or ord(char) < 32 for char in filename)):
        raise HTTPException(400, "X-Filename phải là tên tệp .mp4 hợp lệ.")

    try:
        video_id = await request.app.state.upload_service.receive(request.stream(), filename, length)
    except (InvalidUpload, ClientDisconnect) as exc:
        raise HTTPException(400, "Dữ liệu MP4 rỗng hoặc upload bị gián đoạn.") from exc
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except InsufficientDiskSpace as exc:
        raise HTTPException(507, str(exc)) from exc

    return AcceptedUpload(id=UUID(video_id))


def find_video(request: Request, video_id: UUID) -> dict:
    video = get_video(request.app.state.settings.database_path, str(video_id))
    if video is None:
        raise HTTPException(404, "Không tìm thấy video.")
    return video


@router.get("/{video_id}", summary="Xem trạng thái xử lý của video")
def upload_status(video_id: UUID, request: Request, response: Response) -> UploadStatus:
    video = find_video(request, video_id)
    response.headers["Cache-Control"] = "no-store"
    video["storage"] = "r2" if video["storage_bucket"] else "local"
    storage = request.app.state.r2_storage
    if video["status"] == "ready" and storage and storage.matches(video):
        video["playback_url"] = storage.playlist_url(video_id, video["playlist_key"])
        if video["online_synced"]:
            video["online_status"] = "published"
        elif request.app.state.settings.online_library_enabled:
            video["online_status"] = "pending"
    return UploadStatus.model_validate(video)


@router.get("/{video_id}/media/{filename}", summary="Đọc tệp HLS từ bucket R2 riêng")
def stored_media(video_id: UUID, filename: str, request: Request) -> StreamingResponse:
    if not is_media_name(filename):
        raise HTTPException(404, "Không tìm thấy tệp HLS.")
    video = get_video(request.app.state.settings.database_path, str(video_id))
    if not video or video["status"] != "ready" or not video["storage_bucket"]:
        raise HTTPException(404, "Video chưa có trên R2.")
    storage = request.app.state.r2_storage
    if not storage or not storage.matches(video):
        raise HTTPException(503, "Cấu hình R2 hiện tại chưa khớp nơi lưu video.")
    try:
        prefix = video["r2_prefix"]
        remote = storage.client.open(prefix + filename if prefix else media_key(video_id, filename))
    except ClientError as exc:
        if is_missing_object(exc):
            raise HTTPException(404, "Không tìm thấy object trên R2.") from exc
        raise HTTPException(502, "R2 không trả được object.") from exc
    except BotoCoreError as exc:
        raise HTTPException(502, "Không thể kết nối R2.") from exc

    body = remote["Body"]

    def chunks() -> Iterator[bytes]:
        with closing(body):
            yield from body.iter_chunks(chunk_size=CHUNK_BYTES)

    return StreamingResponse(
        chunks(), media_type=remote["ContentType"],
        headers={"Content-Length": str(remote["ContentLength"]), "Cache-Control": "private, no-store"},
        background=BackgroundTask(body.close),
    )

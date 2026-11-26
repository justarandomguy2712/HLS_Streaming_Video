"""API danh mục video và địa chỉ phát được tạo từ khóa lưu trữ."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from backend.api.uploads import (
    UPLOAD_BODY, UPLOAD_ERRORS, AcceptedUpload, UploadStatus, accept_mp4, find_video, upload_status,
)
from backend.config import MAX_VIDEO_PAGE_SIZE
from backend.database import list_videos

router = APIRouter(prefix="/api/videos", tags=["Thư viện video"])
CURSOR = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z)"
    r"~([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
)


class VideoItem(BaseModel):
    id: str
    title: str
    original_filename: str
    original_size: int | None
    duration: float | None
    segment_count: int | None
    processed_size: int | None
    init_key: str | None
    playlist_key: str | None
    r2_prefix: str | None
    status: str
    created_at: str
    playable: bool


class VideoPage(BaseModel):
    items: list[VideoItem]
    next_offset: int | None
    next_cursor: str | None


class Playback(BaseModel):
    id: UUID
    playlist_key: str
    playback_url: str
    codecs: str
    forward_buffer_seconds: int
    back_buffer_seconds: int


def video_item(request: Request, row: dict) -> VideoItem:
    storage = request.app.state.r2_storage
    return VideoItem.model_validate({
        **row,
        "title": row["title"] or row["original_filename"],
        "playable": bool(
            row["status"] == "ready" and row["playlist_key"] and row["codecs"]
            and storage and storage.matches(row)
        ),
    })


@router.post("/upload", status_code=202, openapi_extra=UPLOAD_BODY, responses=UPLOAD_ERRORS)
async def upload_video(
    request: Request, response: Response,
    filename: Annotated[str, Header(alias="X-Filename")] = "video.mp4",
) -> AcceptedUpload:
    accepted = await accept_mp4(request, filename)
    response.headers["Location"] = f"/api/videos/{accepted.id}/status"
    return accepted


@router.get("")
def video_library(
    request: Request, response: Response,
    limit: Annotated[int | None, Query(ge=1, le=MAX_VIDEO_PAGE_SIZE)] = None,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    cursor: Annotated[str | None, Query(max_length=80)] = None,
) -> VideoPage:
    if cursor is not None and offset:
        raise HTTPException(400, "Chọn cursor hoặc offset cho một lượt phân trang.")
    match = CURSOR.fullmatch(cursor) if cursor is not None else None
    if cursor is not None and match is None:
        raise HTTPException(400, "Cursor phân trang không hợp lệ.")
    settings = request.app.state.settings
    page_size = limit if limit is not None else settings.video_list_page_size
    before = (match[1], match[2]) if match else None
    rows = list_videos(settings.database_path, page_size + 1, offset, before=before)
    items = [video_item(request, row) for row in rows[:page_size]]
    has_more = len(rows) > page_size
    response.headers["Cache-Control"] = "no-store"
    return VideoPage(
        items=items,
        next_offset=offset + page_size if has_more and before is None else None,
        next_cursor=f"{items[-1].created_at}~{items[-1].id}" if has_more else None,
    )


@router.get("/{video_id}")
def video_detail(video_id: UUID, request: Request, response: Response) -> VideoItem:
    response.headers["Cache-Control"] = "no-store"
    return video_item(request, find_video(request, video_id))


@router.get("/{video_id}/playback")
def video_playback(video_id: UUID, request: Request, response: Response) -> Playback:
    video = find_video(request, video_id)
    if video["status"] != "ready" or not video["storage_bucket"]:
        raise HTTPException(409, video["error"] or "Video chưa sẵn sàng phát từ R2.")
    storage = request.app.state.r2_storage
    if not storage or not storage.matches(video):
        raise HTTPException(503, "Cấu hình R2 hiện tại chưa khớp nơi lưu video.")
    response.headers["Cache-Control"] = "no-store"
    return Playback(
        id=video_id, playlist_key=video["playlist_key"],
        playback_url=storage.playlist_url(video_id, video["playlist_key"]), codecs=video["codecs"],
        forward_buffer_seconds=request.app.state.settings.player_forward_seconds,
        back_buffer_seconds=request.app.state.settings.player_back_seconds,
    )


@router.get("/{video_id}/status")
def video_status(video_id: UUID, request: Request, response: Response) -> UploadStatus:
    return upload_status(video_id, request, response)

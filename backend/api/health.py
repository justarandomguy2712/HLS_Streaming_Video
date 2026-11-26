"""Kiểm tra backend có thể truy cập danh mục SQLite."""

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from backend.database import check_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Vận hành"])


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


@router.get(
    "/health",
    summary="Kiểm tra trạng thái ứng dụng",
    responses={
        503: {"model": HealthStatus, "description": "Không thể đọc danh mục SQLite."},
    },
)
def health(request: Request, response: Response) -> HealthStatus:
    try:
        check_database(request.app.state.settings.database_path)
    except sqlite3.Error:
        logger.exception("Không thể đọc danh mục video SQLite.")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(status="unavailable", database="unavailable")
    return HealthStatus(status="ok", database="ok")

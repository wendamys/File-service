"""Pydantic-схемы тел запросов и ответов веб-слоя."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Sort = Literal["asc", "desc"]


class SelectionRequest(BaseModel):
    """Тело запроса `POST /api/stats` — какие файлы выбрал пользователь.

    - "ids" — точечный выбор, имена перечислены в `names`.
    - "page" — все файлы текущей страницы (резолвятся на бэкенде).
    - "all" — вообще все скачанные файлы.

    Для режима "page" нужна не только страница, но и сортировка: без неё
    бэкенд собрал бы страницу в другом порядке, чем видел пользователь.
    """

    mode: Literal["ids", "page", "all"]
    names: list[str] = []
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
    sort: Sort = "desc"


class FileItem(BaseModel):
    """Одна строка списка скачанных файлов."""

    name: str
    downloaded_at: datetime
    downloaded_at_nsk: str
    size_bytes: int


class FileListResponse(BaseModel):
    """Ответ `GET /api/files`."""

    items: list[FileItem]
    total: int
    page: int
    per_page: int


class JobStatusResponse(BaseModel):
    """Снимок состояния job'а скачивания для `GET /api/download/status`."""

    status: Literal["idle", "running", "blocked", "done", "failed", "cancelled"]
    started_at: datetime | None
    started_at_nsk: str
    names_received: int
    downloaded: int
    total_downloaded: int
    unblock_at: datetime | None
    unblock_at_nsk: str
    last_error: str | None
    log: list[str]

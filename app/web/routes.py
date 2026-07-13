"""HTTP-роуты: две HTML-страницы и JSON API под ними.

Зависимости лежат в `request.app.state` — их кладёт туда `create_app()`.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.schemas import FileListResponse, JobStatusResponse, SelectionRequest, Sort
from app.stats import StatsResult, calculate
from app.timeutils import format_nsk

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/files", response_class=HTMLResponse)
def files_page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "files.html", {})


@router.post("/api/download/start")
def download_start(request: Request) -> JSONResponse:
    manager = request.app.state.manager
    started = manager.start()
    if not started:
        raise HTTPException(status_code=409, detail="Скачивание уже запущено.")
    return JSONResponse(status_code=202, content={"detail": "Скачивание запущено."})


@router.post("/api/download/stop")
def download_stop(request: Request) -> JSONResponse:
    manager = request.app.state.manager
    manager.stop()
    return JSONResponse(status_code=202, content={"detail": "Остановка запрошена."})


@router.get("/api/download/status", response_model=JobStatusResponse)
def download_status(request: Request) -> JobStatusResponse:
    state = request.app.state.manager.status()
    return JobStatusResponse(
        status=state.status,
        started_at=state.started_at,
        started_at_nsk=format_nsk(state.started_at),
        names_received=state.names_received,
        downloaded=state.downloaded,
        total_downloaded=state.total_downloaded,
        unblock_at=state.unblock_at,
        unblock_at_nsk=format_nsk(state.unblock_at),
        last_error=state.last_error,
        log=state.log,
    )


@router.get("/api/files", response_model=FileListResponse)
def list_files(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sort: Sort = "desc",
) -> FileListResponse:
    storage = request.app.state.storage
    rows, total = storage.list_files(page=page, per_page=per_page, sort=sort)
    return FileListResponse(
        items=[
            {
                "name": row.name,
                "downloaded_at": row.downloaded_at,
                "downloaded_at_nsk": format_nsk(row.downloaded_at),
                "size_bytes": row.size_bytes,
            }
            for row in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/api/stats")
def compute_stats(request: Request, selection: SelectionRequest) -> StatsResult:
    storage = request.app.state.storage
    settings = request.app.state.settings

    if selection.mode == "all":
        names = storage.all_names()
    elif selection.mode == "page":
        rows, _ = storage.list_files(
            page=selection.page, per_page=selection.per_page, sort=selection.sort
        )
        names = [row.name for row in rows]
    else:
        names = selection.names

    if not names:
        raise HTTPException(status_code=422, detail="Не выбрано ни одного файла для расчёта.")

    return calculate(names, settings.downloads_dir)

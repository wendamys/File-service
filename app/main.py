"""Фабрика FastAPI-приложения. Запуск: `uvicorn app.main:app`."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api_client import FileServiceClient
from app.config import get_settings
from app.downloader import Downloader
from app.jobs import JobManager
from app.rate_limiter import RateLimiter
from app.storage import Storage
from app.web.routes import router
from app.zip_extractor import ZipExtractor

_WEB_DIR = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()

    storage = Storage(settings.db_url)
    storage.init_db()
    # Диск — источник истины: выкидываем записи об исчезнувших файлах и
    # подхватываем те, что лежат на диске, но в БД не попали.
    storage.prune_missing(settings.downloads_dir)
    storage.backfill_from_disk(settings.downloads_dir)

    # Клиент живёт всё время работы приложения: его RateLimiter должен помнить
    # состояние троттлинга и между запусками job'а.
    client = FileServiceClient(
        base_url=settings.base_url,
        candidate_id=settings.candidate_id,
        rate_limiter=RateLimiter(
            interval=settings.request_interval,
            max_interval=settings.max_interval,
            backoff_factor=settings.backoff_factor,
        ),
        max_retries=settings.max_retries,
        timeout=settings.timeout,
    )
    extractor = ZipExtractor(output_dir=settings.downloads_dir)

    manager = JobManager(
        storage=storage,
        downloader_factory=lambda stop_event, on_progress: Downloader(
            client,
            extractor,
            storage,
            on_progress=on_progress,
            stop_event=stop_event,
        ),
    )

    app = FastAPI(title="File Service")
    app.state.storage = storage
    app.state.manager = manager
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
    app.include_router(router)

    return app


app = create_app()

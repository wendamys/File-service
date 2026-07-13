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
    """Собрать готовое к работе FastAPI-приложение со всеми зависимостями."""
    settings = get_settings()

    storage = Storage(settings.db_url)
    storage.init_db()
    # Диск — источник истины: сначала выкидываем записи об исчезнувших файлах,
    # затем подхватываем те, что лежат на диске, но ещё не попали в БД.
    storage.prune_missing(settings.downloads_dir)
    storage.backfill_from_disk(settings.downloads_dir)

    # Клиент можно переиспользовать между запусками job'а (у него свой
    # RateLimiter, который должен помнить состояние троттлинга даже между
    # запусками), а вот Downloader и Extractor создаются заново каждый раз.
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

    # Фабрика должна читать manager.stop_event в момент вызова, а не при объявлении:
    # на каждый запуск job'а создаётся новое событие остановки.
    manager = JobManager(storage=storage, downloader_factory=lambda: None)
    manager.downloader_factory = lambda: Downloader(
        client,
        extractor,
        storage,
        on_progress=manager._on_progress,
        stop_event=manager.stop_event,
    )

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

    app = FastAPI(title="File Service")
    app.state.storage = storage
    app.state.manager = manager
    app.state.settings = settings
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
    app.include_router(router)

    return app


app = create_app()

"""Оркестрация скачивания каталога: порядок шагов и поведение при блокировке."""

import threading
import time
from datetime import datetime
from typing import Callable

from app.api_client import FileServiceClient
from app.exceptions import ClientBlockedError
from app.logger import get_logger
from app.storage import Storage
from app.timeutils import utcnow
from app.zip_extractor import ZipExtractor

logger = get_logger(__name__)

# Жёсткий лимит API на количество имён в одном запросе на скачивание.
_DOWNLOAD_CHUNK_SIZE = 3

# Ждём разблокировки короткими тиками, чтобы не проспать stop_event.
_MAX_WAIT_TICK = 5.0


class Downloader:
    """Скачивает весь каталог файлов, переживая блокировку сервера.

    События, которые получает `on_progress`:

      {"event": "names_received", "count": int}           — получена порция имён
      {"event": "downloaded", "count": int, "total": int} — прогресс внутри порции
      {"event": "blocked", "unblock_at": datetime}        — 403, ждём разблокировки
      {"event": "resumed"}                                — дождались, продолжаем
      {"event": "done"}                                   — каталог скачан целиком
      {"event": "error", "message": str}                  — исключение, будет проброшено

    `stop_event` прерывает скачивание между порциями и во время ожидания
    разблокировки; `download_all()` тогда завершается без исключения.
    """

    def __init__(
        self,
        client: FileServiceClient,
        extractor: ZipExtractor,
        storage: Storage,
        on_progress: Callable[[dict], None] | None = None,
        stop_event: threading.Event | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.extractor = extractor
        self.storage = storage
        self.on_progress = on_progress
        self.stop_event = stop_event
        self.sleep = sleep

    def download_all(self) -> None:
        """Скачать весь каталог, переживая бан и продолжая с места остановки."""
        self._reconcile_unmarked()

        while True:
            try:
                self._drain_catalog()
            except ClientBlockedError as e:
                self._emit({"event": "blocked", "unblock_at": e.unblock_at})
                if self._wait_for_unblock(e.unblock_at):
                    logger.info("Ожидание разблокировки прервано по stop_event")
                    return
                self._emit({"event": "resumed"})
                continue
            except Exception as e:
                logger.error("Скачивание прервано ошибкой: %s", e)
                self._emit({"event": "error", "message": str(e)})
                raise
            return

    def _emit(self, event: dict) -> None:
        if self.on_progress is not None:
            self.on_progress(event)

    def _is_stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _reconcile_unmarked(self) -> None:
        """До-отметить файлы, скачанные на прошлом запуске, но не отмеченные на сервере."""
        pending = self.storage.unmarked_names()
        if not pending:
            return
        logger.info("Реконсиляция: до-отмечаем %s ранее скачанных файлов", len(pending))
        self.client.mark_downloaded(pending)
        self.storage.mark_files(pending)

    def _drain_catalog(self) -> None:
        """Качать порции, пока сервер не вернёт пустой список или не попросят остановиться."""
        while not self._is_stopped():
            names = self.client.get_file_names()
            if not names:
                logger.info("Каталог полностью скачан")
                self._emit({"event": "done"})
                return

            logger.info("Получена порция из %s имён", len(names))
            self._emit({"event": "names_received", "count": len(names)})

            known = self.storage.known_names()
            to_download = [name for name in names if name not in known]

            downloaded = 0
            for i in range(0, len(to_download), _DOWNLOAD_CHUNK_SIZE):
                chunk = to_download[i:i + _DOWNLOAD_CHUNK_SIZE]
                zip_bytes = self.client.download_files(chunk)
                for name, size in self.extractor.extract(zip_bytes):
                    self.storage.add_file(name, size, marked=False)
                    downloaded += 1
                self._emit({"event": "downloaded", "count": downloaded, "total": len(to_download)})

            # Лимит в 3 файла есть только у скачивания, у отметки его нет,
            # поэтому вся порция отмечается одним запросом.
            self.client.mark_downloaded(names)
            self.storage.mark_files(names)

    def _wait_for_unblock(self, unblock_at: datetime) -> bool:
        """Прерываемо ждать разблокировки. True — если прервали через stop_event."""
        while True:
            remaining = (unblock_at - utcnow()).total_seconds()
            if remaining <= 0:
                return self._is_stopped()

            tick = min(remaining, _MAX_WAIT_TICK)
            if self.stop_event is None:
                self.sleep(tick)
            elif self.stop_event.wait(timeout=tick):
                return True

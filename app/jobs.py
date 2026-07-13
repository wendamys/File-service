"""Фоновый job скачивания: мост между `Downloader` и веб-слоем."""

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Literal

from app.downloader import Downloader
from app.logger import get_logger
from app.storage import Storage
from app.timeutils import format_nsk, utcnow

logger = get_logger(__name__)

_LOG_TAIL = 50

JobStatus = Literal["idle", "running", "blocked", "done", "failed", "cancelled"]

# Собирает Downloader, привязанный к переданным stop_event и колбэку прогресса.
DownloaderFactory = Callable[[threading.Event, Callable[[dict], None]], Downloader]


@dataclass
class JobState:
    """Снимок состояния job'а скачивания для отображения в вебе."""

    status: JobStatus = "idle"
    started_at: datetime | None = None
    names_received: int = 0
    # Сколько имён из последней порции реально надо скачать: остальные уже
    # лежат на диске и будут только отмечены на сервере.
    to_download: int = 0
    # Скачано в рамках последней порции, а не накопительно по всем.
    downloaded: int = 0
    # Всего файлов в БД: накопительно, по всем порциям и запускам.
    total_downloaded: int = 0
    unblock_at: datetime | None = None
    last_error: str | None = None
    log: list[str] = field(default_factory=list)


class JobManager:
    """Запускает `Downloader.download_all()` в фоновом потоке и следит за его статусом."""

    def __init__(self, downloader_factory: DownloaderFactory, storage: Storage):
        self.downloader_factory = downloader_factory
        self.storage = storage
        self.stop_event: threading.Event | None = None

        self._lock = threading.Lock()
        self._state = JobState()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Запустить job. Возвращает False, если он уже идёт."""
        with self._lock:
            if self._state.status in ("running", "blocked"):
                return False
            self.stop_event = threading.Event()
            self._state = JobState(status="running", started_at=utcnow())

        downloader = self.downloader_factory(self.stop_event, self._on_progress)
        self._thread = threading.Thread(target=self._run, args=(downloader,), daemon=True)
        self._thread.start()
        logger.info("Job запущен")
        return True

    def stop(self) -> None:
        """Попросить job остановиться. Если он не запущен — ничего не делает."""
        if self.stop_event is not None:
            logger.info("Запрошена остановка job'а")
            self.stop_event.set()

    def status(self) -> JobState:
        """Вернуть независимый снимок текущего состояния job'а."""
        with self._lock:
            return replace(self._state, log=list(self._state.log))

    def _run(self, downloader: Downloader) -> None:
        try:
            downloader.download_all()
        except Exception as e:
            logger.exception("Job упал с ошибкой")
            with self._lock:
                self._state.status = "failed"
                self._state.last_error = str(e)
                self._append_log(f"Скачивание прервано ошибкой: {e}")
            return

        with self._lock:
            # download_all() выходит без исключения и когда каталог скачан целиком
            # (событие "done" уже перевело статус), и когда сработал stop_event —
            # во втором случае статус так и остался "running"/"blocked".
            if self._state.status != "done" and self.stop_event is not None and self.stop_event.is_set():
                self._state.status = "cancelled"
                self._append_log("Job остановлен пользователем")

    def _on_progress(self, event: dict) -> None:
        """Колбэк для `Downloader`: обновляет `JobState` под локом."""
        kind = event.get("event")
        with self._lock:
            if kind == "names_received":
                count = event["count"]
                to_download = event.get("to_download", count)
                self._state.names_received = count
                self._state.to_download = to_download
                self._state.downloaded = 0
                already_have = count - to_download
                message = f"Получена порция из {count} имён"
                if already_have:
                    message += f", из них {already_have} уже скачано ранее"
                self._append_log(message)
            elif kind == "downloaded":
                self._state.downloaded = event["count"]
                self._state.total_downloaded = self.storage.count()
                self._append_log(f"Скачано {event['count']} из {event['total']}")
            elif kind == "blocked":
                self._state.status = "blocked"
                self._state.unblock_at = event.get("unblock_at")
                self._append_log(
                    f"Заблокирован сервером до {format_nsk(self._state.unblock_at)} (НСК)"
                )
            elif kind == "resumed":
                self._state.status = "running"
                self._state.unblock_at = None
                self._append_log("Блокировка снята, скачивание продолжено")
            elif kind == "done":
                self._state.status = "done"
                self._state.total_downloaded = self.storage.count()
                self._append_log("Скачивание завершено")
            elif kind == "error":
                self._state.last_error = event.get("message")
                self._append_log(f"Ошибка: {event.get('message')}")
            else:
                logger.warning("Неизвестное событие прогресса: %s", kind)

    def _append_log(self, message: str) -> None:
        """Вызывать под self._lock."""
        self._state.log.append(message)
        if len(self._state.log) > _LOG_TAIL:
            self._state.log = self._state.log[-_LOG_TAIL:]

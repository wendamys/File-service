"""Управление фоновым job'ом скачивания каталога — мост между `Downloader` и веб-слоем.

`JobManager` держит единственный активный `Downloader` в фоновом потоке,
пробрасывает его прогресс (`on_progress`) в потокобезопасный `JobState`
и даёт HTTP-роутам (FastAPI) дёшево читать снимок состояния и просить
об остановке (`stop()`), не блокируясь на фоновом потоке.

Про связывание `stop_event`/`on_progress` с `Downloader`: по контракту
`downloader_factory` — это `Callable[[], Downloader]` без аргументов,
поэтому конкретный `Downloader` должен сам знать, с каким `stop_event`
и `on_progress` его создавать. Решение: `JobManager` перед каждым запуском
кладёт новый `threading.Event()` в публичный атрибут `self.stop_event`,
а вызывающий код собирает фабрику как замыкание над уже существующим
менеджером, например:

    manager = JobManager(storage=storage, downloader_factory=lambda: None)
    manager.downloader_factory = lambda: Downloader(
        client, extractor, storage,
        on_progress=manager._on_progress,
        stop_event=manager.stop_event,
    )

(или эквивалентно — определить `manager` заранее через `variable`,
захватываемую по ссылке в `lambda`, что работает, так как тело `lambda`
выполняется лишь в момент вызова `start()`, когда `manager` уже создан).
К моменту вызова `downloader_factory()` внутри `start()` атрибут
`self.stop_event` уже переприсвоен свежим `Event`, так что фабрика
всегда видит актуальный `stop_event` текущего запуска.
"""

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Literal

from app.downloader import Downloader
from app.logger import get_logger
from app.storage import Storage
from app.timeutils import utcnow

logger = get_logger(__name__)

# Сколько последних строк лога хранить в JobState.
_LOG_TAIL = 50

JobStatus = Literal["idle", "running", "blocked", "done", "failed", "cancelled"]


@dataclass
class JobState:
    """Снимок состояния job'а скачивания для отображения в вебе."""

    status: JobStatus = "idle"
    started_at: datetime | None = None
    names_received: int = 0
    # Скачано в рамках последней обработанной порции (не накопительно
    # по всем порциям) — см. событие "downloaded" в `Downloader`.
    downloaded: int = 0
    # Всего файлов в БД (накопительно, по всем порциям и запускам).
    total_downloaded: int = 0
    unblock_at: datetime | None = None
    last_error: str | None = None
    log: list[str] = field(default_factory=list)


class JobManager:
    """Запускает `Downloader.download_all()` в фоновом потоке и следит за его статусом."""

    def __init__(self, downloader_factory: Callable[[], Downloader], storage: Storage):
        self.downloader_factory = downloader_factory
        self.storage = storage
        self.stop_event: threading.Event | None = None

        self._lock = threading.Lock()
        self._state = JobState()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Запустить job, если он ещё не идёт. Возвращает False, если уже running/blocked."""
        with self._lock:
            if self._state.status in ("running", "blocked"):
                return False
            self.stop_event = threading.Event()
            self._state = JobState(status="running", started_at=utcnow())

        downloader = self.downloader_factory()
        thread = threading.Thread(target=self._run, args=(downloader,), daemon=True)
        self._thread = thread
        thread.start()
        logger.info("Job запущен")
        return True

    def stop(self) -> None:
        """Попросить job остановиться. Если job не запущен — не делает ничего."""
        if self.stop_event is not None:
            logger.info("Запрошена остановка job'а")
            self.stop_event.set()

    def status(self) -> JobState:
        """Вернуть независимый снимок текущего состояния job'а."""
        with self._lock:
            return replace(self._state, log=list(self._state.log))

    def _run(self, downloader: Downloader) -> None:
        """Тело фонового потока: гоняет `download_all()` и фиксирует итог."""
        try:
            downloader.download_all()
        except Exception as e:
            logger.exception("Job упал с ошибкой")
            with self._lock:
                self._state.status = "failed"
                self._state.last_error = str(e)
                self._append_log(f"Job failed: {e}")
            return

        with self._lock:
            # download_all() завершается без исключения и в случае штатного
            # окончания (событие "done" уже перевело статус в "done"),
            # и в случае остановки по stop_event (событие "done" не пришло) —
            # тогда статус ещё "running"/"blocked", и это отличаем по флагу.
            if self._state.status != "done" and self.stop_event is not None and self.stop_event.is_set():
                self._state.status = "cancelled"
                self._append_log("Job остановлен пользователем")

    def _on_progress(self, event: dict) -> None:
        """Callback для `Downloader`: обновляет `JobState` под локом по типу события."""
        kind = event.get("event")
        with self._lock:
            if kind == "names_received":
                self._state.names_received = event["count"]
                self._append_log(f"Получена порция из {event['count']} имён")
            elif kind == "downloaded":
                self._state.downloaded = event["count"]
                self._state.total_downloaded = self.storage.count()
                self._append_log(f"Скачано {event['count']} из {event['total']}")
            elif kind == "blocked":
                self._state.status = "blocked"
                self._state.unblock_at = event.get("unblock_at")
                self._append_log(f"Заблокирован сервером до {event.get('unblock_at')}")
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
        """Добавить строку в лог, храня не более `_LOG_TAIL` последних записей. Вызывать под self._lock."""
        self._state.log.append(message)
        if len(self._state.log) > _LOG_TAIL:
            self._state.log = self._state.log[-_LOG_TAIL:]

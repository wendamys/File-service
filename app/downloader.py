"""Оркестрация скачивания каталога файлов, переживающая ban (403) без потери прогресса.

Основные решения (см. README/тестовое задание):
  - Скачивание файлов ограничено API тройками (`download_files` принимает
    максимум 3 имени), но отметка `mark_downloaded` лимита не имеет —
    поэтому одна порция имён скачивается несколькими запросами по 3,
    а отмечается ОДНИМ запросом на всю порцию сразу (меньше запросов —
    меньше поводов словить 429/бан).
  - `ClientBlockedError` (403) не убивает процесс: `Downloader` переходит
    в ожидание до `unblock_at`, ждёт прерываемо (короткими интервалами,
    проверяя `stop_event`, чтобы кнопка "Стоп" в вебе срабатывала быстро),
    и затем продолжает с точки останова — ничего не перекачивая заново,
    т.к. `Storage.known_names()` и `Storage.unmarked_names()` сами
    восстанавливают, что уже сделано.
"""

import threading
import time
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

# Максимальный интервал одного "тика" ожидания разблокировки — чтобы
# stop_event проверялся часто, а не раз в час.
_MAX_WAIT_TICK = 5.0


class Downloader:
    """Скачивает весь каталог файлов, переживая rate-limit бан сервера.

    `on_progress` — необязательный callback, вызываемый на ключевых событиях
    жизненного цикла. Каждый вызов передаёт один словарь (`dict`) со
    строковым полем `event`:

      - {"event": "names_received", "count": int}
            Получена очередная порция имён файлов от сервера
            (`count` — размер порции, до фильтрации по known_names()).
      - {"event": "downloaded", "count": int, "total": int}
            Обработан(ы) очередной(е) чанк(и) скачивания в рамках текущей
            порции. `count` — сколько файлов из порции уже скачано и
            распаковано в этом проходе, `total` — сколько всего файлов
            нужно было скачать в этой порции (без учёта уже лежавших на
            диске — они не скачиваются повторно).
      - {"event": "done"}
            Сервер вернул пустой список имён — весь каталог скачан,
            `download_all()` вот-вот нормально завершится.
      - {"event": "blocked", "unblock_at": datetime}
            Получен 403, клиент заблокирован сервером до `unblock_at`
            (aware UTC datetime). Downloader переходит в прерываемое
            ожидание.
      - {"event": "resumed"}
            Ожидание разблокировки завершилось (дождались `unblock_at`),
            скачивание продолжается с того же места.
      - {"event": "error", "message": str}
            Необработанное исключение, отличное от `ClientBlockedError`
            (например, `RateLimitedError`, `FileNotFoundInCatalogError`,
            сетевая ошибка после исчерпания ретраев). Исключение будет
            проброшено вызывающему коду сразу после этого вызова.

    `stop_event`, если передан, — сигнал для аккуратного прерывания:
    проверяется перед каждым новым запросом `get_file_names()` и во время
    ожидания разблокировки. Если он установлен, `download_all()` завершается
    без исключения (это не ошибка, а штатная остановка по запросу извне).

    `sleep` — инжектируемая функция ожидания (по умолчанию `time.sleep`),
    используется в цикле ожидания разблокировки только если `stop_event`
    не передан (иначе используется `stop_event.wait`, что позволяет
    прервать ожидание немедленно).
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

    def _emit(self, event: dict) -> None:
        if self.on_progress is not None:
            self.on_progress(event)

    def _is_stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def download_all(self) -> None:
        """Скачать весь каталог, переживая бан и продолжая с места остановки."""
        self._reconcile_unmarked()

        while True:
            try:
                self._drain_catalog()
            except ClientBlockedError as e:
                self._emit({"event": "blocked", "unblock_at": e.unblock_at})
                interrupted = self._wait_for_unblock(e.unblock_at)
                if interrupted:
                    logger.info("Ожидание разблокировки прервано по stop_event")
                    return
                self._emit({"event": "resumed"})
                continue
            except Exception as e:
                logger.error("Скачивание прервано ошибкой: %s", e)
                self._emit({"event": "error", "message": str(e)})
                raise
            return

    def _reconcile_unmarked(self) -> None:
        """До-отметить файлы, скачанные на прошлом запуске, но не отмеченные на сервере."""
        pending = self.storage.unmarked_names()
        if not pending:
            return
        logger.info("Реконсиляция: до-отмечаем %s ранее скачанных файлов", len(pending))
        self.client.mark_downloaded(pending)
        self.storage.mark_files(pending)

    def _drain_catalog(self) -> None:
        """Скачивать порции файлов, пока сервер не вернёт пустой список или не попросят остановиться."""
        while True:
            if self._is_stopped():
                return

            names = self.client.get_file_names()
            if not names:
                logger.info("Каталог полностью скачан")
                self._emit({"event": "done"})
                return

            logger.info("Получена порция из %s имён", len(names))
            self._emit({"event": "names_received", "count": len(names)})

            known = self.storage.known_names()
            to_download = [n for n in names if n not in known]

            downloaded = 0
            for i in range(0, len(to_download), _DOWNLOAD_CHUNK_SIZE):
                chunk = to_download[i:i + _DOWNLOAD_CHUNK_SIZE]
                zip_bytes = self.client.download_files(chunk)
                extracted = self.extractor.extract(zip_bytes)
                for name, size in extracted:
                    self.storage.add_file(name, size, marked=False)
                downloaded += len(extracted)
                self._emit({"event": "downloaded", "count": downloaded, "total": len(to_download)})

            # Отмечаем ОДНИМ запросом всю порцию (включая то, что уже лежало
            # на диске) — MarkDownloadedRequest не ограничен тройкой в отличие
            # от DownloadRequest.
            self.client.mark_downloaded(names)
            self.storage.mark_files(names)

    def _wait_for_unblock(self, unblock_at) -> bool:
        """Ждать разблокировки прерываемо. Возвращает True, если прервано stop_event."""
        while utcnow() < unblock_at:
            if self._is_stopped():
                return True
            remaining = (unblock_at - utcnow()).total_seconds()
            wait_time = min(remaining, _MAX_WAIT_TICK)
            if wait_time <= 0:
                break
            if self.stop_event is not None:
                if self.stop_event.wait(timeout=wait_time):
                    return True
            else:
                self.sleep(wait_time)
        return self._is_stopped()

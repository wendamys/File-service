"""HTTP-клиент API сервиса файлов: троттлинг, ретраи и разбор ошибок 429/403."""

import random
import time
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import requests
from requests import Response

from app.exceptions import ClientBlockedError, FileNotFoundInCatalogError, RateLimitedError
from app.logger import get_logger
from app.rate_limiter import RateLimiter
from app.timeutils import utcnow

logger = get_logger(__name__)

# Потолок для экспоненциального backoff при сетевых ошибках и 5xx.
_MAX_BACKOFF_DELAY = 30.0

# Пути, для которых 404 означает "часть файлов отсутствует в каталоге",
# а не "эндпоинт не найден".
_CATALOG_PATHS = ("/api/files/download", "/api/files/downloaded")


def parse_retry_after(value: str | None, default: float = 1.0) -> float:
    """Разобрать заголовок `Retry-After` в число секунд ожидания.

    Поддерживает целые и дробные секунды ("30", "1.5") и HTTP-дату
    (RFC 7231, например "Wed, 21 Oct 2026 07:28:00 GMT") — тогда возвращается
    разница в секундах до этой даты от текущего момента (не меньше 0).
    Никогда не бросает исключение: любое невалидное значение или его
    отсутствие даёт `default`.
    """
    if value is None:
        return default

    value = value.strip()
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return default

    if target is None:
        return default
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)

    seconds_left = (target - utcnow()).total_seconds()
    return max(0.0, seconds_left)


class FileServiceClient:
    """Клиент API скачивания файлов с троттлингом и ретраями."""

    def __init__(
        self,
        base_url: str,
        candidate_id: str | None = None,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 5,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url
        self.candidate_id = candidate_id
        self.rate_limiter = rate_limiter or RateLimiter(
            interval=1.5,
            max_interval=15.0,
            backoff_factor=1.5,
        )
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep

        self.session = requests.Session()
        if candidate_id is not None:
            self.session.headers["X-Candidate-Id"] = candidate_id

    def _backoff_delay(self, attempt: int) -> float:
        """Экспоненциальный backoff с full jitter, не более `_MAX_BACKOFF_DELAY`."""
        base_delay = min(2 ** attempt, _MAX_BACKOFF_DELAY)
        return random.uniform(0, base_delay)

    @staticmethod
    def _is_catalog_path(url: str) -> bool:
        return url.endswith(_CATALOG_PATHS)

    @staticmethod
    def _extract_detail(response: Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text
        if isinstance(data, dict):
            detail = data.get("detail")
            if detail is not None:
                return detail
        return response.text

    def _request(self, method: str, url: str, **kwargs) -> Response:
        attempt = 0
        while True:
            attempt += 1
            # Пауза выдерживается перед каждой попыткой, включая повторные:
            # иначе ретраи сами по себе разгоняют частоту запросов до бана.
            self.rate_limiter.acquire()

            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.RequestException as e:
                if attempt > self.max_retries:
                    logger.error(
                        "%s %s: сетевая ошибка, попытки исчерпаны (%s): %s",
                        method, url, attempt, e,
                    )
                    raise
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "%s %s: сетевая ошибка (попытка %s/%s): %s. Повтор через %.2f с",
                    method, url, attempt, self.max_retries, e, delay,
                )
                self.sleep(delay)
                continue

            if response.status_code == 429:
                self.rate_limiter.penalize()
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                if attempt > self.max_retries:
                    logger.error(
                        "%s %s: лимит частоты, попытки исчерпаны (%s)",
                        method, url, attempt,
                    )
                    raise RateLimitedError(retry_after)
                wait = retry_after + random.uniform(0, 1)
                logger.warning(
                    "%s %s: превышена частота запросов (попытка %s/%s). Ожидание %.2f с",
                    method, url, attempt, self.max_retries, wait,
                )
                self.sleep(wait)
                continue

            if response.status_code == 403:
                retry_after = parse_retry_after(
                    response.headers.get("Retry-After"), default=1800.0
                )
                unblock_at = utcnow() + timedelta(seconds=retry_after)
                logger.error(
                    "%s %s: клиент заблокирован на %s с, до %s",
                    method, url, retry_after, unblock_at,
                )
                # Не ретраим — решение, ждать ли разблокировки, принимает вызывающий код.
                raise ClientBlockedError(retry_after, unblock_at)

            if response.status_code == 404 and self._is_catalog_path(url):
                detail = self._extract_detail(response)
                logger.error("%s %s: файлы отсутствуют в каталоге: %s", method, url, detail)
                raise FileNotFoundInCatalogError(detail)

            if response.status_code >= 500:
                if attempt > self.max_retries:
                    logger.error(
                        "%s %s: ошибка сервера %s, попытки исчерпаны (%s)",
                        method, url, response.status_code, attempt,
                    )
                    response.raise_for_status()
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "%s %s: ошибка сервера %s (попытка %s/%s). Повтор через %.2f с",
                    method, url, response.status_code, attempt, self.max_retries, delay,
                )
                self.sleep(delay)
                continue

            response.raise_for_status()
            self.rate_limiter.reward()
            logger.info("%s %s -> %s", method, url, response.status_code)
            return response

    def get_file_names(self) -> list[str]:
        """Получить список файлов, ещё не скачанных кандидатом."""
        response = self._request("GET", self.base_url + "/api/files/names")
        return response.json()["file_names"]

    def download_files(self, file_names: list[str]) -> bytes:
        """Скачать до трёх файлов одним ZIP-архивом."""
        if len(file_names) > 3:
            raise ValueError("Можно скачать не более 3 файлов за один запрос.")
        response = self._request(
            "POST",
            self.base_url + "/api/files/download",
            json={"file_names": file_names},
        )
        return response.content

    def mark_downloaded(self, file_names: list[str]) -> dict[str, int]:
        """Отметить файлы как скачанные на сервере."""
        response = self._request(
            "POST",
            self.base_url + "/api/files/downloaded",
            json={"file_names": file_names},
        )
        return response.json()

import threading
import time
from typing import Callable

from app.logger import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Проактивный троттлинг запросов: не даёт превысить допустимую частоту.

    Помимо равномерной паузы между вызовами (`acquire`), умеет адаптивно
    увеличивать интервал при 429 (`penalize`) и постепенно уменьшать его
    обратно после серии успешных ответов (`reward`), но не ниже базового
    значения, заданного при создании.
    """

    def __init__(
        self,
        interval: float,
        max_interval: float,
        backoff_factor: float,
        decrease_step: float = 0.1,
        successes_before_decrease: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._base_interval = interval
        self._interval = interval
        self._max_interval = max_interval
        self._backoff_factor = backoff_factor
        self._decrease_step = decrease_step
        self._successes_before_decrease = successes_before_decrease
        self._sleep = sleep
        self._monotonic = monotonic

        self._lock = threading.Lock()
        self._last_request_at: float | None = None
        self._consecutive_successes = 0

    @property
    def interval(self) -> float:
        with self._lock:
            return self._interval

    def acquire(self) -> None:
        """Дождаться, пока с прошлого запроса пройдёт не меньше `interval` секунд."""
        with self._lock:
            now = self._monotonic()
            if self._last_request_at is None:
                wait_for = 0.0
            else:
                elapsed = now - self._last_request_at
                wait_for = max(0.0, self._interval - elapsed)

        if wait_for > 0:
            self._sleep(wait_for)

        with self._lock:
            self._last_request_at = self._monotonic()

    def penalize(self) -> None:
        """Увеличить интервал после 429, сбросив накопленный прогресс `reward`."""
        with self._lock:
            new_interval = min(
                self._interval * self._backoff_factor,
                self._max_interval,
            )
            self._consecutive_successes = 0
            if new_interval != self._interval:
                logger.info(
                    "Интервал между запросами увеличен: %.2f -> %.2f с",
                    self._interval,
                    new_interval,
                )
            self._interval = new_interval

    def reward(self) -> None:
        """Учесть успешный ответ, после N подряд — уменьшить интервал."""
        with self._lock:
            self._consecutive_successes += 1
            if self._consecutive_successes < self._successes_before_decrease:
                return

            self._consecutive_successes = 0
            new_interval = max(
                self._base_interval,
                self._interval - self._decrease_step,
            )
            if new_interval != self._interval:
                logger.info(
                    "Интервал между запросами снижен: %.2f -> %.2f с",
                    self._interval,
                    new_interval,
                )
            self._interval = new_interval

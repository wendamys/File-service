from datetime import datetime


class FileServiceError(Exception):
    """Базовая ошибка при работе с API сервиса файлов."""


class RateLimitedError(FileServiceError):
    """Превышена допустимая частота запросов, попытки ретрая исчерпаны."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(
            f"Превышена частота запросов, повторите через {retry_after} сек."
        )


class ClientBlockedError(FileServiceError):
    """Клиент временно заблокирован сервером (403) за злоупотребление запросами."""

    def __init__(self, retry_after: float, unblock_at: datetime):
        self.retry_after = retry_after
        self.unblock_at = unblock_at
        super().__init__(
            f"Клиент заблокирован до {unblock_at.isoformat()} "
            f"(через {retry_after} сек.)"
        )


class FileNotFoundInCatalogError(FileServiceError):
    """Часть запрошенных файлов отсутствует в каталоге сервера."""

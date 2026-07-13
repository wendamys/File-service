from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NSK = ZoneInfo("Asia/Novosibirsk")


def utcnow() -> datetime:
    """Текущее время как aware datetime в UTC."""
    return datetime.now(timezone.utc)


def to_nsk(dt: datetime) -> datetime:
    """Перевести aware datetime (любая TZ) в часовой пояс Новосибирска."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NSK)


def format_nsk(dt: datetime | None) -> str:
    """Отформатировать время в НСК как 'дд.мм.гггг чч:мм:сс', либо пустую строку."""
    if dt is None:
        return ""
    return to_nsk(dt).strftime("%d.%m.%Y %H:%M:%S")

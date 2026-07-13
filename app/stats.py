from collections import Counter
from pathlib import Path
from typing import TypedDict

from app.logger import get_logger

logger = get_logger(__name__)

DIGITS = "0123456789"


class FileStats(TypedDict):
    name: str
    counts: dict[str, int]
    total: int


class StatsResult(TypedDict):
    total_counts: dict[str, int]
    total_chars: int
    files: list[FileStats]
    skipped: list[dict[str, str]]


def count_digits(text: str) -> dict[str, int]:
    """Посчитать встречаемость цифр в строке, нецифровые символы игнорируются."""
    counter = Counter(ch for ch in text if ch in DIGITS)
    return {digit: counter.get(digit, 0) for digit in DIGITS}


def calculate(names: list[str], downloads_dir: Path) -> StatsResult:
    """Посчитать статистику цифр по списку файлов, пропуская нечитаемые."""
    total_counts: dict[str, int] = {digit: 0 for digit in DIGITS}
    total_chars = 0
    files: list[FileStats] = []
    skipped: list[dict[str, str]] = []

    for name in names:
        path = downloads_dir / name
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("Файл не найден при подсчёте статистики: %s", name)
            skipped.append({"name": name, "reason": "файл не найден"})
            continue
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Не удалось прочитать файл %s: %s", name, e)
            skipped.append({"name": name, "reason": str(e)})
            continue

        counts = count_digits(content)
        for digit in DIGITS:
            total_counts[digit] += counts[digit]
        total_chars += len(content)
        files.append(FileStats(name=name, counts=counts, total=sum(counts.values())))

    return StatsResult(
        total_counts=total_counts,
        total_chars=total_chars,
        files=files,
        skipped=skipped,
    )

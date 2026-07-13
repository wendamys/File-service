import io
import zipfile
from pathlib import Path, PurePosixPath

from app.logger import get_logger

logger = get_logger(__name__)


class ZipExtractor:
    """Распаковывает ZIP-архив из байтов на диск, защищаясь от zip-slip."""

    def __init__(self, output_dir: str | Path = "downloads"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, zip_bytes: bytes) -> list[tuple[str, int]]:
        """Распаковать архив и вернуть список (имя файла, размер в байтах).

        Записи с небезопасными именами пропускаются с предупреждением.
        """
        extracted: list[tuple[str, int]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                # extractall() пишет файлы по сырым именам из архива и потому
                # уязвим к zip-slip — распаковываем сами, с проверкой имён.
                for info in archive.infolist():
                    if info.is_dir():
                        continue

                    safe_name = self._safe_name(info.filename)
                    if safe_name is None:
                        logger.warning("Пропущена небезопасная запись в архиве: %s", info.filename)
                        continue

                    with archive.open(info) as source:
                        data = source.read()
                    (self.output_dir / safe_name).write_bytes(data)
                    extracted.append((safe_name, len(data)))
        except zipfile.BadZipFile as e:
            logger.error("Повреждённый ZIP-архив: %s", e)
            raise
        return extracted

    @staticmethod
    def _safe_name(name: str) -> str | None:
        """Базовое имя файла или None, если запись выходит за пределы output_dir."""
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            return None
        return path.name or None

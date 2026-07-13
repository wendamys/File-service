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
        """Распаковывает архив, возвращает список (имя_файла, размер_в_байтах).

        Записи с подозрительными именами (абсолютные пути, выход за пределы
        output_dir через "..", директории) пропускаются с предупреждением
        и не попадают в результат.
        """
        extracted: list[tuple[str, int]] = []
        try:
            stream = io.BytesIO(zip_bytes)
            with zipfile.ZipFile(stream) as archive:
                # archive.extractall() пишет файлы по сырым именам из архива,
                # поэтому уязвим к zip-slip — распаковываем вручную с проверкой имён.
                for info in archive.infolist():
                    name = info.filename
                    if info.is_dir():
                        continue

                    safe_name = self._sanitize_name(name)
                    if safe_name is None:
                        logger.warning("Пропущена небезопасная запись в архиве: %s", name)
                        continue

                    target_path = self.output_dir / safe_name
                    with archive.open(info) as source, open(target_path, "wb") as target:
                        data = source.read()
                        target.write(data)

                    extracted.append((safe_name, len(data)))
        except zipfile.BadZipFile as e:
            logger.error("Corrupted zip archive: %s", e)
            raise
        return extracted

    @staticmethod
    def _sanitize_name(name: str) -> str | None:
        """Проверяет имя записи архива и возвращает безопасное базовое имя файла.

        Возвращает None, если запись небезопасна (абсолютный путь, "..",
        либо это директория, а не файл).
        """
        if name.startswith("/") or name.startswith("\\"):
            return None
        if PurePosixPath(name).is_absolute():
            return None
        # Путь вида "C:\..." или "C:/..." содержит букву диска Windows.
        if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
            return None
        normalized = name.replace("\\", "/")
        if ".." in PurePosixPath(normalized).parts:
            return None

        safe_name = Path(name).name
        if not safe_name:
            return None
        return safe_name

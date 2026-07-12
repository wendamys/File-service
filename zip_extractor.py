import io
import zipfile
from pathlib import Path

from logger import get_logger

logger = get_logger(__name__)


class ZipExtractor:
    def __init__(self, output_dir: str = "downloads"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def extract(self, zip_bytes: bytes) -> list[str]:
        try:
            stream = io.BytesIO(zip_bytes)
            with zipfile.ZipFile(stream) as archive:
                archive.extractall(self.output_dir)
                return archive.namelist()
        except zipfile.BadZipFile as e:
            logger.error("Corrupted zip archive: %s", e)
            raise

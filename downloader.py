from client import FileServiceClient, logger
from zip_extractor import ZipExtractor


class Downloader:

    def __init__(
        self,
        client: FileServiceClient,
        extractor: ZipExtractor
    ):
        self.client = client
        self.extractor = extractor

    def download_all(self):
        chunk_size = 3
        while True:
            files = self.client.get_file_names()
            if not files:
                print("Все файлы скачаны.")
                break
            logger.info("Received %s file names", len(files))
            for i in range(0, len(files), chunk_size):
                batch = files[i:i + chunk_size]
                logger.info("Downloading %s", batch)
                zip_bytes = self.client.download_files(batch)
                extracted = self.extractor.extract(zip_bytes)
                logger.info("Extracted: %s", extracted)
                result = self.client.mark_downloaded(batch)
                logger.info("Marked: %s", result)

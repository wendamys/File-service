from client import FileServiceClient
from downloader import Downloader
from zip_extractor import ZipExtractor

def main():
    client = FileServiceClient(
        base_url="http://91.199.149.128:18001",
        candidate_id="2"
    )

    extractor = ZipExtractor()

    downloader = Downloader(
        client,
        extractor
    )

    downloader.download_all()

if __name__ == "__main__":
    main()
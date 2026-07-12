import time
from typing import Optional
import requests
from requests import Response
from logger import get_logger

logger = get_logger(__name__)


class FileServiceClient:
    def __init__(
            self,
            base_url:str,
            candidate_id: Optional[str] = None):
        self.base_url = base_url
        self.candidate_id = candidate_id
        self.session = requests.Session()
        if candidate_id is not None:
            self.session.headers["X-Candidate-Id"] = candidate_id


    def _request(self, method: str, url: str, **kwargs) -> Response:
        while True:
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=30,
                    **kwargs
                )
            except requests.RequestException as e:
                logger.error("Request failed: %s", e)
                raise

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 1))
                logger.warning(
                    "Rate limit exceeded. Waiting %s seconds.",
                    retry_after
                )
                time.sleep(retry_after)
                continue

            if response.status_code == 403:
                retry_after = response.headers.get("Retry-After")
                logger.error(
                    "Client blocked. Retry after %s seconds.",
                    retry_after
                )
                raise RuntimeError(
                    f"Клиент заблокирован. Повторите через {retry_after} секунд."
                )

            response.raise_for_status()
            logger.info(
                "%s %s -> %s",
                method,
                url,
                response.status_code
            )
            return response

    def get_file_names(self) -> list[str]:
        """Получить список файлов, ещё не скачанных кандидатом."""
        response = self._request(
            "GET",
            self.base_url + "/api/files/names")
        return response.json()["file_names"]


    def download_files(self, file_names: list[str]) -> bytes:
        """Скачать до трёх файлов одним ZIP-архивом."""
        if len(file_names) > 3:
            raise ValueError("Можно скачать не более 3 файлов за один запрос.")
        response = self._request(
            "POST",
            self.base_url + "/api/files/download",
            json={
                "file_names": file_names
            }
        )
        return response.content


    def mark_downloaded(self, file_names: list[str]) -> dict[str, int]:
        response = self._request(
            "POST",
            self.base_url + "/api/files/downloaded",
            json={
                "file_names": file_names
            }
        )
        data = response.json()
        return data


if __name__ == '__main__':
    client = FileServiceClient(
        base_url = "http://91.199.149.128:18001",
        candidate_id = "1")
    files = client.get_file_names()
    zip_data = client.download_files(files[:3])

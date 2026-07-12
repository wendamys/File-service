import time
from typing import Optional
import requests
from requests import Response


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
            response = self.session.request(
                method=method,
                url=url,
                **kwargs
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 1))
                print(f"Превышен лимит запросов. Ждем {retry_after} сек...")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            return response

    def get_file_names(self) -> list[str]:
        response = self._request(
            "GET",
            self.base_url + "/api/files/names")
        data = response.json()
        return data["file_names"]


    def download_files(self, file_names: list[str]) -> bytes:
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

    print(type(zip_data))
    print(len(zip_data))
    print(zip_data[:4])
    print(client.session.headers)
    result = client.mark_downloaded(files[:3])
    print(result)

    # print(files)


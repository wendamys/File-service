from typing import Optional
import requests


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



    def get_file_names(self) -> list[str]:
        response = self.session.get(self.base_url + "/api/files/names")
        response.raise_for_status()
        data = response.json()
        return data["file_names"]


    def download_files(self, file_names: list[str]) -> bytes:
        response = self.session.post(
            self.base_url + "/api/files/download",
            json={
                "file_names": file_names
            }
        )
        response.raise_for_status()
        return response.content


    def mark_downloaded(self, file_names: list[str]) -> dict[str, int]:
        response = self.session.post(
            self.base_url + "/api/files/downloaded",
            json={
                "file_names": file_names
            }
        )
        response.raise_for_status()
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


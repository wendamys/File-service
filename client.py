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


    def get_file_names(self):
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
        print(len(files))
        print(files)
        print(response.request.body)
        print(response.status_code)
        print(response.text)
        response.raise_for_status()
        return response.content


    def mark_downloaded(self):
        pass


if __name__ == '__main__':
    client = FileServiceClient(
        base_url = "http://91.199.149.128:18001",
        candidate_id = "1")
    files = client.get_file_names()
    client.download_files(files)

    # print(files)


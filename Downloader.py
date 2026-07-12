from client import FileServiceClient
class Downloader:
    def __init__(self, client):
        self.client = client

    def download_all(self):
        chunk_size = 3
        while True:
            files_list = self.client.get_file_names()
            if not files_list:
                print("Все файлы скачаны")
                return
            for i in range(0, len(files_list), chunk_size):
                batch = files_list[i:i + chunk_size]
                zip_bytes = self.client.download_files(batch)
                print(zip_bytes[:4])



if __name__ == '__main__':
    client = FileServiceClient(
        base_url = "http://91.199.149.128:18001",
        candidate_id = "1")
    downloader = Downloader(client)
    downloader.download_all()



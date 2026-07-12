from unittest.mock import Mock, call

from downloader import Downloader


def make_downloader(file_batches):
    """client.get_file_names() returns each list in file_batches in turn, then []."""
    client = Mock()
    client.get_file_names.side_effect = list(file_batches) + [[]]
    client.download_files.return_value = b"ZIPBYTES"
    client.mark_downloaded.return_value = {"marked": True}

    extractor = Mock()
    extractor.extract.return_value = ["a.txt"]

    return Downloader(client, extractor), client, extractor


def test_stops_immediately_when_no_files():
    downloader, client, extractor = make_downloader([[]])
    downloader.download_all()

    client.get_file_names.assert_called_once()
    extractor.extract.assert_not_called()
    client.download_files.assert_not_called()
    client.mark_downloaded.assert_not_called()


def test_splits_batch_into_chunks_of_three():
    files = ["f1", "f2", "f3", "f4", "f5"]
    downloader, client, extractor = make_downloader([files])
    downloader.download_all()

    client.download_files.assert_has_calls([
        call(["f1", "f2", "f3"]),
        call(["f4", "f5"]),
    ])
    client.mark_downloaded.assert_has_calls([
        call(["f1", "f2", "f3"]),
        call(["f4", "f5"]),
    ])


def test_extracts_each_downloaded_zip():
    downloader, client, extractor = make_downloader([["f1", "f2"]])
    downloader.download_all()

    extractor.extract.assert_called_once_with(b"ZIPBYTES")


def test_loops_until_server_returns_empty_list():
    downloader, client, extractor = make_downloader([["f1"], ["f2"]])
    downloader.download_all()

    assert client.get_file_names.call_count == 3
    assert client.download_files.call_count == 2

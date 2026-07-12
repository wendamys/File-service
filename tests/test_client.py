from unittest.mock import Mock

import pytest
import requests

from client import FileServiceClient
from tests.conftest import make_response


BASE_URL = "http://example.test"


@pytest.fixture
def client():
    return FileServiceClient(base_url=BASE_URL, candidate_id="42")


def test_candidate_id_sets_header():
    client = FileServiceClient(base_url=BASE_URL, candidate_id="42")
    assert client.session.headers["X-Candidate-Id"] == "42"


def test_no_candidate_id_no_header():
    client = FileServiceClient(base_url=BASE_URL)
    assert "X-Candidate-Id" not in client.session.headers


def test_get_file_names_returns_list(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, json_data={"file_names": ["a.txt", "b.txt"]})),
    )
    assert client.get_file_names() == ["a.txt", "b.txt"]


def test_download_files_rejects_more_than_three(client):
    with pytest.raises(ValueError):
        client.download_files(["a", "b", "c", "d"])


def test_download_files_returns_content(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, content=b"ZIPBYTES")),
    )
    assert client.download_files(["a.txt"]) == b"ZIPBYTES"


def test_mark_downloaded_returns_dict(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, json_data={"marked": 2})),
    )
    assert client.mark_downloaded(["a.txt", "b.txt"]) == {"marked": 2}


def test_request_retries_on_429_then_succeeds(client, monkeypatch):
    responses = [
        make_response(429, headers={"Retry-After": "0"}),
        make_response(200, json_data={"file_names": []}),
    ]
    mock_request = Mock(side_effect=responses)
    monkeypatch.setattr(client.session, "request", mock_request)
    monkeypatch.setattr("client.time.sleep", Mock())

    assert client.get_file_names() == []
    assert mock_request.call_count == 2


def test_request_raises_runtime_error_on_403(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(403, headers={"Retry-After": "60"})),
    )
    with pytest.raises(RuntimeError):
        client.get_file_names()


def test_request_raises_for_server_error(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(500)),
    )
    with pytest.raises(requests.HTTPError):
        client.get_file_names()


def test_request_propagates_connection_errors(client, monkeypatch):
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(side_effect=requests.ConnectionError("boom")),
    )
    with pytest.raises(requests.ConnectionError):
        client.get_file_names()

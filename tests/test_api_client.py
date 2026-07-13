from datetime import timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import pytest
import requests

from app.api_client import FileServiceClient, parse_retry_after
from app.exceptions import ClientBlockedError, FileNotFoundInCatalogError, RateLimitedError
from app.timeutils import utcnow
from tests.conftest import make_response


BASE_URL = "http://example.test"


# --- parse_retry_after ------------------------------------------------------

def test_parse_retry_after_integer_seconds():
    assert parse_retry_after("30") == 30.0


def test_parse_retry_after_fractional_seconds():
    assert parse_retry_after("1.5") == 1.5


def test_parse_retry_after_http_date_in_future():
    future = utcnow() + timedelta(seconds=120)
    value = format_datetime(future, usegmt=True)
    result = parse_retry_after(value)
    assert result == pytest.approx(120, abs=2)


def test_parse_retry_after_http_date_in_past_clamped_to_zero():
    past = utcnow() - timedelta(seconds=120)
    value = format_datetime(past, usegmt=True)
    assert parse_retry_after(value) == 0.0


def test_parse_retry_after_none_returns_default():
    assert parse_retry_after(None, default=2.5) == 2.5
    assert parse_retry_after(None) == 1.0


def test_parse_retry_after_garbage_returns_default():
    assert parse_retry_after("not-a-number", default=3.0) == 3.0


def test_parse_retry_after_never_raises():
    for garbage in ["", "   ", "abc", "1,5", "Mon, 32 Foo 2026", None, "NaN", "-"]:
        parse_retry_after(garbage)


# --- клиент ------------------------------------------------------------------

def make_client(**overrides):
    params = dict(
        base_url=BASE_URL,
        candidate_id="42",
        rate_limiter=Mock(),
        max_retries=5,
        sleep=Mock(),
    )
    params.update(overrides)
    return FileServiceClient(**params)


# --- candidate id --------------------------------------------------------

def test_candidate_id_sets_header():
    client = make_client(candidate_id="42")
    assert client.session.headers["X-Candidate-Id"] == "42"


def test_no_candidate_id_no_header():
    client = make_client(candidate_id=None)
    assert "X-Candidate-Id" not in client.session.headers


# --- успешные сценарии -------------------------------------------------------

def test_get_file_names_returns_list(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, json_data={"file_names": ["a.txt", "b.txt"]})),
    )
    assert client.get_file_names() == ["a.txt", "b.txt"]
    client.rate_limiter.acquire.assert_called_once()
    client.rate_limiter.reward.assert_called_once()


def test_download_files_rejects_more_than_three():
    client = make_client()
    with pytest.raises(ValueError):
        client.download_files(["a", "b", "c", "d"])


def test_download_files_returns_content(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, content=b"ZIPBYTES")),
    )
    assert client.download_files(["a.txt"]) == b"ZIPBYTES"
    client.rate_limiter.reward.assert_called_once()


def test_mark_downloaded_returns_dict(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client.session,
        "request",
        Mock(return_value=make_response(200, json_data={"marked_now": 2, "already_marked": 0})),
    )
    assert client.mark_downloaded(["a.txt", "b.txt"]) == {"marked_now": 2, "already_marked": 0}


# --- 429 ---------------------------------------------------------------------

def test_request_retries_on_429_then_succeeds():
    client = make_client(max_retries=5)
    responses = [
        make_response(429, headers={"Retry-After": "0"}),
        make_response(200, json_data={"file_names": []}),
    ]
    mock_request = Mock(side_effect=responses)
    client.session.request = mock_request

    assert client.get_file_names() == []
    assert mock_request.call_count == 2
    client.rate_limiter.penalize.assert_called_once()
    client.rate_limiter.reward.assert_called_once()

    # Джиттер обязателен даже при Retry-After=0.
    (waited,), _ = client.sleep.call_args
    assert waited > 0


def test_request_429_without_retry_after_exhausts_retries_raises_rate_limited():
    client = make_client(max_retries=2)
    mock_request = Mock(return_value=make_response(429, headers={}))
    client.session.request = mock_request

    with pytest.raises(RateLimitedError):
        client.get_file_names()

    assert mock_request.call_count == 3  # первая попытка + 2 ретрая
    assert client.sleep.call_count == 2
    for (waited,), _ in client.sleep.call_args_list:
        assert waited > 0


# --- 403 ---------------------------------------------------------------------

def test_request_raises_client_blocked_error_on_403_without_retrying():
    client = make_client(max_retries=5)
    mock_request = Mock(return_value=make_response(403, headers={"Retry-After": "60"}))
    client.session.request = mock_request

    with pytest.raises(ClientBlockedError) as exc_info:
        client.get_file_names()

    assert mock_request.call_count == 1  # никаких ретраев на 403
    error = exc_info.value
    assert error.retry_after == 60.0
    assert error.unblock_at > utcnow()
    assert error.unblock_at <= utcnow() + timedelta(seconds=61)


def test_request_403_without_retry_after_uses_thirty_minute_default():
    client = make_client()
    mock_request = Mock(return_value=make_response(403, headers={}))
    client.session.request = mock_request

    with pytest.raises(ClientBlockedError) as exc_info:
        client.get_file_names()

    assert exc_info.value.retry_after == 1800.0


# --- 404 на ручках каталога ---------------------------------------------------

def test_download_files_404_raises_file_not_found_in_catalog():
    client = make_client()
    mock_request = Mock(
        return_value=make_response(404, json_data={"detail": "file missing: a.txt"})
    )
    client.session.request = mock_request

    with pytest.raises(FileNotFoundInCatalogError) as exc_info:
        client.download_files(["a.txt"])

    assert "a.txt" in str(exc_info.value)
    assert mock_request.call_count == 1  # 404 не ретраится


# --- 5xx ----------------------------------------------------------------------

def test_request_raises_http_error_for_server_error_after_retries():
    client = make_client(max_retries=2)
    mock_request = Mock(return_value=make_response(500))
    client.session.request = mock_request

    with pytest.raises(requests.HTTPError):
        client.get_file_names()

    assert mock_request.call_count == 3  # первая попытка + 2 ретрая
    assert client.sleep.call_count == 2


def test_request_recovers_after_transient_server_error():
    client = make_client(max_retries=5)
    responses = [
        make_response(500),
        make_response(200, json_data={"file_names": ["a.txt"]}),
    ]
    mock_request = Mock(side_effect=responses)
    client.session.request = mock_request

    assert client.get_file_names() == ["a.txt"]
    assert mock_request.call_count == 2


# --- сетевые ошибки -----------------------------------------------------------

def test_request_propagates_connection_error_after_retries_exhausted():
    client = make_client(max_retries=2)
    mock_request = Mock(side_effect=requests.ConnectionError("boom"))
    client.session.request = mock_request

    with pytest.raises(requests.ConnectionError):
        client.get_file_names()

    assert mock_request.call_count == 3  # первая попытка + 2 ретрая


def test_request_recovers_after_transient_connection_error():
    client = make_client(max_retries=5)
    mock_request = Mock(
        side_effect=[
            requests.ConnectionError("boom"),
            make_response(200, json_data={"file_names": []}),
        ]
    )
    client.session.request = mock_request

    assert client.get_file_names() == []
    assert mock_request.call_count == 2


# --- связка с троттлингом -----------------------------------------------------

def test_acquire_called_before_every_attempt_including_retries():
    client = make_client(max_retries=5)
    responses = [
        make_response(429, headers={"Retry-After": "0"}),
        make_response(429, headers={"Retry-After": "0"}),
        make_response(200, json_data={"file_names": []}),
    ]
    client.session.request = Mock(side_effect=responses)

    client.get_file_names()

    assert client.rate_limiter.acquire.call_count == 3
    assert client.rate_limiter.penalize.call_count == 2

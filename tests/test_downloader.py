import threading
from datetime import timedelta
from unittest.mock import Mock, call

import pytest

from app.downloader import Downloader
from app.exceptions import ClientBlockedError
from app.timeutils import utcnow


def make_downloader(file_batches, known_names=frozenset(), unmarked_names=(), **kwargs):
    """client.get_file_names() возвращает по очереди списки из file_batches, затем []."""
    client = Mock()
    client.get_file_names.side_effect = list(file_batches) + [[]]
    client.download_files.return_value = b"ZIPBYTES"
    client.mark_downloaded.return_value = {"marked": True}

    extractor = Mock()
    # По умолчанию содержимое распаковки не важно для большинства тестов.
    extractor.extract.return_value = []

    storage = Mock()
    storage.known_names.return_value = set(known_names)
    storage.unmarked_names.return_value = list(unmarked_names)

    downloader = Downloader(client, extractor, storage, **kwargs)
    return downloader, client, extractor, storage


def fake_zip(names):
    """Заглушка вместо реального zip: просто передаём список имён как "содержимое"."""
    return list(names)


# --- пустой каталог ------------------------------------------------------

def test_stops_immediately_when_no_files():
    downloader, client, extractor, storage = make_downloader([[]])
    downloader.download_all()

    client.get_file_names.assert_called_once()
    extractor.extract.assert_not_called()
    client.download_files.assert_not_called()
    client.mark_downloaded.assert_not_called()


# --- чанки по 3 на скачивание, но ОДНА отметка на порцию -----------------

def test_downloads_in_chunks_of_three_but_marks_once_per_batch():
    files = ["f1", "f2", "f3", "f4", "f5", "f6", "f7"]
    downloader, client, extractor, storage = make_downloader([files])
    client.download_files.side_effect = lambda names: fake_zip(names)

    downloader.download_all()

    client.download_files.assert_has_calls([
        call(["f1", "f2", "f3"]),
        call(["f4", "f5", "f6"]),
        call(["f7"]),
    ])
    assert client.download_files.call_count == 3
    client.mark_downloaded.assert_called_once_with(files)


def test_extracts_each_downloaded_chunk_and_stores_it():
    downloader, client, extractor, storage = make_downloader([["f1", "f2"]])
    client.download_files.return_value = b"ZIPBYTES"
    extractor.extract.return_value = [("f1", 10), ("f2", 20)]

    downloader.download_all()

    extractor.extract.assert_called_once_with(b"ZIPBYTES")
    storage.add_file.assert_has_calls([
        call("f1", 10, marked=False),
        call("f2", 20, marked=False),
    ])


def test_loops_until_server_returns_empty_list():
    downloader, client, extractor, storage = make_downloader([["f1"], ["f2"]])
    downloader.download_all()

    assert client.get_file_names.call_count == 3
    assert client.download_files.call_count == 2
    assert client.mark_downloaded.call_count == 2


# --- фильтрация уже скачанного -------------------------------------------

def test_already_known_files_are_not_downloaded_but_are_marked():
    files = ["f1", "f2", "f3", "f4"]
    downloader, client, extractor, storage = make_downloader(
        [files], known_names={"f2", "f3"}
    )
    client.download_files.side_effect = lambda names: fake_zip(names)

    downloader.download_all()

    # f2 и f3 уже на диске - не должны попасть ни в один запрос на скачивание.
    for call_args in client.download_files.call_args_list:
        downloaded_names = call_args.args[0]
        assert "f2" not in downloaded_names
        assert "f3" not in downloaded_names

    # Но отметка должна включать всю порцию целиком, включая f2/f3.
    client.mark_downloaded.assert_called_once_with(files)
    storage.mark_files.assert_called_once_with(files)


def test_names_received_event_reports_how_many_need_downloading():
    files = ["f1", "f2", "f3", "f4"]
    downloader, client, extractor, storage = make_downloader(
        [files], known_names={"f2", "f3"}, on_progress=Mock()
    )
    client.download_files.side_effect = lambda names: fake_zip(names)

    downloader.download_all()

    events = [c.args[0] for c in downloader.on_progress.call_args_list]
    names_received = next(e for e in events if e["event"] == "names_received")
    assert names_received["count"] == 4
    assert names_received["to_download"] == 2


def test_all_known_batch_skips_download_but_still_marks():
    files = ["f1", "f2"]
    downloader, client, extractor, storage = make_downloader([files], known_names=set(files))

    downloader.download_all()

    client.download_files.assert_not_called()
    client.mark_downloaded.assert_called_once_with(files)


# --- реконсиляция при старте ----------------------------------------------

def test_reconciles_unmarked_files_before_main_loop():
    downloader, client, extractor, storage = make_downloader(
        [[]], unmarked_names=["old1", "old2"]
    )

    downloader.download_all()

    client.mark_downloaded.assert_called_once_with(["old1", "old2"])
    storage.mark_files.assert_called_once_with(["old1", "old2"])
    # Реконсиляция должна произойти ДО первого get_file_names.
    assert client.mock_calls[0] == call.mark_downloaded(["old1", "old2"])


def test_no_reconciliation_when_nothing_unmarked():
    downloader, client, extractor, storage = make_downloader([[]], unmarked_names=[])

    downloader.download_all()

    client.mark_downloaded.assert_not_called()
    storage.mark_files.assert_not_called()


# --- ClientBlockedError: ожидание и продолжение ----------------------------

def test_blocked_error_waits_then_resumes_download(monkeypatch):
    unblock_at = utcnow() + timedelta(seconds=12)

    client = Mock()
    client.get_file_names.side_effect = [
        ClientBlockedError(retry_after=12.0, unblock_at=unblock_at),
        ["f1"],
        [],
    ]
    client.download_files.return_value = b"ZIP"
    client.mark_downloaded.return_value = {}

    extractor = Mock()
    extractor.extract.return_value = [("f1", 5)]

    storage = Mock()
    storage.known_names.return_value = set()
    storage.unmarked_names.return_value = []

    # Симулируем течение времени: каждый sleep() продвигает "часы" вперёд.
    current_time = {"now": utcnow()}

    def fake_utcnow():
        return current_time["now"]

    def fake_sleep(seconds):
        current_time["now"] = current_time["now"] + timedelta(seconds=seconds)

    monkeypatch.setattr("app.downloader.utcnow", fake_utcnow)

    on_progress = Mock()
    downloader = Downloader(client, extractor, storage, on_progress=on_progress, sleep=fake_sleep)

    downloader.download_all()

    events = [c.args[0]["event"] for c in on_progress.call_args_list]
    assert "blocked" in events
    assert "resumed" in events
    assert events.index("resumed") > events.index("blocked")

    # После разблокировки скачивание должно было продолжиться.
    client.download_files.assert_called_once_with(["f1"])
    client.mark_downloaded.assert_called_once_with(["f1"])


def test_blocked_error_wait_uses_injected_sleep_not_real_time(monkeypatch):
    """Ожидание должно идти короткими тиками через sleep(), не реальным time.sleep."""
    unblock_at = utcnow() + timedelta(seconds=11)

    client = Mock()
    client.get_file_names.side_effect = [
        ClientBlockedError(retry_after=11.0, unblock_at=unblock_at),
        [],
    ]

    extractor = Mock()
    storage = Mock()
    storage.known_names.return_value = set()
    storage.unmarked_names.return_value = []

    current_time = {"now": utcnow()}

    def fake_utcnow():
        return current_time["now"]

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        current_time["now"] = current_time["now"] + timedelta(seconds=seconds)

    monkeypatch.setattr("app.downloader.utcnow", fake_utcnow)

    downloader = Downloader(client, extractor, storage, sleep=fake_sleep)
    downloader.download_all()

    # 11 секунд ожидания тиками не более 5с -> минимум 3 вызова sleep.
    assert len(sleep_calls) >= 3
    assert all(s <= 5.0 for s in sleep_calls)


# --- stop_event -------------------------------------------------------------

def test_stop_event_set_from_start_prevents_any_requests():
    stop_event = threading.Event()
    stop_event.set()

    downloader, client, extractor, storage = make_downloader([["f1"]], stop_event=stop_event)

    downloader.download_all()

    client.get_file_names.assert_not_called()
    client.download_files.assert_not_called()
    client.mark_downloaded.assert_not_called()


def test_stop_event_interrupts_wait_for_unblock(monkeypatch):
    unblock_at = utcnow() + timedelta(seconds=1800)
    stop_event = threading.Event()

    client = Mock()
    client.get_file_names.side_effect = [
        ClientBlockedError(retry_after=1800.0, unblock_at=unblock_at),
    ]

    extractor = Mock()
    storage = Mock()
    storage.known_names.return_value = set()
    storage.unmarked_names.return_value = []

    # Флаг остановки взводится на первом же "тике" ожидания.
    def fake_wait(timeout=None):
        stop_event.set()
        return True

    monkeypatch.setattr(stop_event, "wait", fake_wait)

    on_progress = Mock()
    downloader = Downloader(
        client, extractor, storage, on_progress=on_progress, stop_event=stop_event
    )
    downloader.download_all()

    events = [c.args[0]["event"] for c in on_progress.call_args_list]
    assert "blocked" in events
    assert "resumed" not in events
    # Не должно быть повторного вызова get_file_names после прерывания ожидания.
    assert client.get_file_names.call_count == 1


# --- прочие ошибки не глотаются --------------------------------------------

def test_other_errors_are_reported_and_reraised():
    client = Mock()
    client.get_file_names.side_effect = RuntimeError("boom")

    extractor = Mock()
    storage = Mock()
    storage.known_names.return_value = set()
    storage.unmarked_names.return_value = []

    on_progress = Mock()
    downloader = Downloader(client, extractor, storage, on_progress=on_progress)

    with pytest.raises(RuntimeError):
        downloader.download_all()

    events = [c.args[0]["event"] for c in on_progress.call_args_list]
    assert "error" in events

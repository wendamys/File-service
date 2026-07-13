import threading
import time
from datetime import timedelta
from unittest.mock import Mock

from app.jobs import JobManager
from app.timeutils import utcnow


def make_manager(download_all=None):
    """downloader_factory возвращает Mock-Downloader с заданным поведением download_all()."""
    storage = Mock()
    storage.count.return_value = 0

    downloader = Mock()
    if download_all is not None:
        downloader.download_all.side_effect = download_all
    manager = JobManager(downloader_factory=lambda: downloader, storage=storage)
    return manager, downloader, storage


def wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- start()/повторный запуск -----------------------------------------------

def test_start_returns_true_then_false_while_running():
    ready = threading.Event()
    release = threading.Event()
    manager_holder = {}

    def download_all():
        ready.set()
        release.wait(timeout=2.0)
        manager_holder["manager"]._on_progress({"event": "done"})

    manager, downloader, storage = make_manager(download_all)
    manager_holder["manager"] = manager

    assert manager.start() is True
    ready.wait(timeout=2.0)
    assert manager.start() is False  # уже running

    release.set()
    assert wait_until(lambda: manager.status().status in ("done", "failed", "cancelled"))


def test_start_returns_true_again_after_job_finishes():
    # Реальный Downloader сам шлёт событие "done" перед нормальным завершением
    # download_all() — имитируем это же поведение у мока.
    storage = Mock()
    storage.count.return_value = 0
    downloader = Mock()
    manager = JobManager(downloader_factory=lambda: downloader, storage=storage)
    downloader.download_all.side_effect = lambda: manager._on_progress({"event": "done"})

    assert manager.start() is True
    assert wait_until(lambda: manager.status().status == "done")
    assert manager.start() is True


def test_start_returns_true_again_after_job_fails():
    manager, downloader, storage = make_manager(download_all=lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert manager.start() is True
    assert wait_until(lambda: manager.status().status == "failed")
    assert manager.status().last_error == "boom"
    assert manager.start() is True


# --- status() возвращает независимый снимок ---------------------------------

def test_status_returns_snapshot_not_live_reference():
    manager, downloader, storage = make_manager()

    snapshot = manager.status()
    assert snapshot.status == "idle"

    manager._on_progress({"event": "names_received", "count": 7})

    # Ранее взятый снимок не должен был измениться.
    assert snapshot.names_received == 0
    assert manager.status().names_received == 7


def test_status_log_list_is_independent_copy():
    manager, downloader, storage = make_manager()

    snapshot = manager.status()
    snapshot.log.append("mutated by caller")

    assert manager.status().log == []


# --- _on_progress: обновление состояния по типам событий --------------------

def test_on_progress_names_received():
    manager, downloader, storage = make_manager()
    manager._on_progress({"event": "names_received", "count": 5})
    state = manager.status()
    assert state.names_received == 5


def test_on_progress_downloaded_updates_counters():
    manager, downloader, storage = make_manager()
    storage.count.return_value = 42
    manager._on_progress({"event": "downloaded", "count": 3, "total": 9})
    state = manager.status()
    assert state.downloaded == 3
    assert state.total_downloaded == 42


def test_on_progress_blocked_sets_status_and_unblock_at():
    manager, downloader, storage = make_manager()
    unblock_at = utcnow() + timedelta(minutes=30)
    manager._on_progress({"event": "blocked", "unblock_at": unblock_at})
    state = manager.status()
    assert state.status == "blocked"
    assert state.unblock_at == unblock_at


def test_on_progress_resumed_sets_status_running():
    manager, downloader, storage = make_manager()
    manager._on_progress({"event": "blocked", "unblock_at": utcnow()})
    manager._on_progress({"event": "resumed"})
    state = manager.status()
    assert state.status == "running"
    assert state.unblock_at is None


def test_on_progress_done_sets_status_done():
    manager, downloader, storage = make_manager()
    storage.count.return_value = 10
    manager._on_progress({"event": "done"})
    state = manager.status()
    assert state.status == "done"
    assert state.total_downloaded == 10


def test_on_progress_error_sets_last_error():
    manager, downloader, storage = make_manager()
    manager._on_progress({"event": "error", "message": "kaboom"})
    state = manager.status()
    assert state.last_error == "kaboom"


def test_on_progress_appends_to_log():
    manager, downloader, storage = make_manager()
    manager._on_progress({"event": "names_received", "count": 1})
    manager._on_progress({"event": "done"})
    state = manager.status()
    assert len(state.log) == 2


# --- stop() -------------------------------------------------------------

def test_stop_is_noop_when_not_running():
    manager, downloader, storage = make_manager()
    manager.stop()  # не должно бросать исключений


def test_stop_sets_stop_event_used_by_downloader_factory():
    seen_events = []

    def factory():
        downloader = Mock()

        def download_all():
            seen_events.append(manager.stop_event)

        downloader.download_all.side_effect = download_all
        return downloader

    storage = Mock()
    storage.count.return_value = 0
    manager = JobManager(downloader_factory=factory, storage=storage)

    manager.start()
    wait_until(lambda: manager.status().status in ("done", "failed", "cancelled"))

    assert seen_events[0] is not None
    manager.stop()
    assert seen_events[0].is_set()

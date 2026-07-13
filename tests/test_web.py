"""Тесты веб-слоя: HTML-страницы и JSON API поверх FastAPI TestClient.

Настройки/БД/директория загрузок подменяются на временные (`tmp_path`) через
переменные окружения перед `create_app()`, чтобы не трогать реальные
`downloads/` и `file_service.db` в корне репозитория.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    db_path = tmp_path / "test.db"

    monkeypatch.setenv("downloads_dir", str(downloads_dir))
    monkeypatch.setenv("db_url", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)
    yield client, app

    get_settings.cache_clear()


class FakeDownloader:
    """Имитация `Downloader`, не бьющая по реальному внешнему API.

    Блокируется на `release` (сигнализируя о старте через `ready`), затем
    эмулирует штатное завершение job'а событием "done" — так же, как это
    делают моки в `tests/test_jobs.py`.
    """

    def __init__(self, manager, ready, release):
        self.manager = manager
        self.ready = ready
        self.release = release

    def download_all(self):
        self.ready.set()
        self.release.wait(timeout=2.0)
        self.manager._on_progress({"event": "done"})


def wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- страницы -----------------------------------------------------------

def test_index_page_has_download_button(app_client):
    client, _ = app_client
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Скачать данные" in resp.text


def test_files_page_ok(app_client):
    client, _ = app_client
    resp = client.get("/files")
    assert resp.status_code == 200


# --- download start/stop/status -----------------------------------------

def test_download_start_conflict_then_stop(app_client):
    client, app = app_client
    manager = app.state.manager
    ready = threading.Event()
    release = threading.Event()
    manager.downloader_factory = lambda: FakeDownloader(manager, ready, release)

    resp = client.post("/api/download/start")
    assert resp.status_code == 202
    assert ready.wait(timeout=2.0)

    # Пока job "running" — повторный запуск запрещён.
    resp2 = client.post("/api/download/start")
    assert resp2.status_code == 409
    assert "detail" in resp2.json()

    resp3 = client.post("/api/download/stop")
    assert resp3.status_code == 202

    release.set()
    assert wait_until(lambda: manager.status().status in ("done", "failed", "cancelled"))


def test_download_status_fields(app_client):
    client, _ = app_client
    resp = client.get("/api/download/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "status", "names_received", "downloaded", "total_downloaded",
        "started_at", "started_at_nsk", "unblock_at", "unblock_at_nsk", "log",
    ):
        assert key in data
    assert data["status"] == "idle"


# --- /api/files -----------------------------------------------------------

def _seed_files(storage, count=5):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(count):
        storage.add_file(f"file{i}.txt", 10 + i, marked=True, downloaded_at=base + timedelta(minutes=i))


def test_files_list_pagination(app_client):
    client, app = app_client
    _seed_files(app.state.storage, count=5)

    resp = client.get("/api/files?page=1&per_page=2&sort=asc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["items"][0]["name"] == "file0.txt"

    resp2 = client.get("/api/files?page=3&per_page=2&sort=asc")
    data2 = resp2.json()
    assert len(data2["items"]) == 1  # последняя, неполная страница


def test_files_list_sort_asc_vs_desc(app_client):
    client, app = app_client
    _seed_files(app.state.storage, count=5)

    asc = client.get("/api/files?page=1&per_page=10&sort=asc").json()
    desc = client.get("/api/files?page=1&per_page=10&sort=desc").json()

    assert [i["name"] for i in asc["items"]] == list(reversed([i["name"] for i in desc["items"]]))
    assert asc["items"][0]["name"] == "file0.txt"
    assert desc["items"][0]["name"] == "file4.txt"


def test_files_list_invalid_sort_422(app_client):
    client, _ = app_client
    resp = client.get("/api/files?sort=garbage")
    assert resp.status_code == 422


# --- /api/stats -----------------------------------------------------------

def test_stats_mode_ids(app_client):
    client, app = app_client
    downloads_dir = app.state.settings.downloads_dir
    (downloads_dir / "a.txt").write_text("11223344559999", encoding="utf-8")

    resp = client.post("/api/stats", json={"mode": "ids", "names": ["a.txt"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_counts"]["1"] == 2
    assert data["total_counts"]["9"] == 4
    assert data["files"][0]["name"] == "a.txt"


def test_stats_mode_all(app_client):
    client, app = app_client
    storage = app.state.storage
    downloads_dir = app.state.settings.downloads_dir
    (downloads_dir / "b.txt").write_text("000111", encoding="utf-8")
    storage.add_file("b.txt", 6, marked=True)

    resp = client.post("/api/stats", json={"mode": "all"})
    assert resp.status_code == 200
    data = resp.json()
    names = [f["name"] for f in data["files"]]
    assert names == ["b.txt"]
    assert data["total_counts"]["0"] == 3
    assert data["total_counts"]["1"] == 3


def test_stats_mode_page(app_client):
    client, app = app_client
    storage = app.state.storage
    downloads_dir = app.state.settings.downloads_dir
    _seed_files(storage, count=3)
    for i in range(3):
        (downloads_dir / f"file{i}.txt").write_text("5" * (i + 1), encoding="utf-8")

    resp = client.post("/api/stats", json={"mode": "page", "page": 1, "per_page": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 2


def test_stats_mode_page_respects_sort(app_client):
    """Страница для расчёта должна собираться в том же порядке, что видит пользователь."""
    client, app = app_client
    storage = app.state.storage
    downloads_dir = app.state.settings.downloads_dir
    _seed_files(storage, count=3)
    for i in range(3):
        (downloads_dir / f"file{i}.txt").write_text("5", encoding="utf-8")

    asc = client.post("/api/stats", json={"mode": "page", "page": 1, "per_page": 2, "sort": "asc"})
    desc = client.post("/api/stats", json={"mode": "page", "page": 1, "per_page": 2, "sort": "desc"})

    assert [f["name"] for f in asc.json()["files"]] == ["file0.txt", "file1.txt"]
    assert [f["name"] for f in desc.json()["files"]] == ["file2.txt", "file1.txt"]


def test_stats_empty_ids_returns_422(app_client):
    client, _ = app_client
    resp = client.post("/api/stats", json={"mode": "ids", "names": []})
    assert resp.status_code == 422

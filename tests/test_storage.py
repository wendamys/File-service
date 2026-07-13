from datetime import timedelta

import pytest

from app.storage import Storage
from app.timeutils import utcnow


@pytest.fixture
def storage(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    store = Storage(db_url)
    store.init_db()
    return store


def test_init_db_and_add_file_and_count(storage):
    storage.add_file("a.txt", size_bytes=500)
    assert storage.count() == 1


def test_add_file_is_idempotent(storage):
    storage.add_file("a.txt", size_bytes=500)
    storage.add_file("a.txt", size_bytes=500)
    storage.add_file("a.txt", size_bytes=500)

    assert storage.count() == 1


def test_add_file_does_not_overwrite_downloaded_at(storage):
    first_time = utcnow() - timedelta(days=1)
    storage.add_file("a.txt", size_bytes=500, downloaded_at=first_time)

    second_time = utcnow()
    storage.add_file("a.txt", size_bytes=600, downloaded_at=second_time)

    rows, _ = storage.list_files(page=1, per_page=10, sort="asc")
    assert len(rows) == 1
    assert rows[0].size_bytes == 600
    # downloaded_at не должен был замениться на second_time.
    assert abs((rows[0].downloaded_at.replace(tzinfo=None) - first_time.replace(tzinfo=None)).total_seconds()) < 1


def test_known_names_unmarked_names_mark_files(storage):
    storage.add_file("a.txt", size_bytes=500, marked=False)

    assert storage.known_names() == {"a.txt"}
    assert storage.unmarked_names() == ["a.txt"]

    storage.mark_files(["a.txt"])

    assert storage.unmarked_names() == []


def test_list_files_pagination_and_sort(storage):
    base = utcnow() - timedelta(days=10)
    for i in range(5):
        storage.add_file(f"file{i}.txt", size_bytes=500, downloaded_at=base + timedelta(hours=i))

    page1, total = storage.list_files(page=1, per_page=2, sort="asc")
    assert total == 5
    assert [f.name for f in page1] == ["file0.txt", "file1.txt"]

    page2, total = storage.list_files(page=2, per_page=2, sort="asc")
    assert total == 5
    assert [f.name for f in page2] == ["file2.txt", "file3.txt"]

    page3, total = storage.list_files(page=3, per_page=2, sort="asc")
    assert total == 5
    assert [f.name for f in page3] == ["file4.txt"]

    desc_page1, total = storage.list_files(page=1, per_page=2, sort="desc")
    assert total == 5
    assert [f.name for f in desc_page1] == ["file4.txt", "file3.txt"]


def test_list_files_invalid_sort_raises(storage):
    storage.add_file("a.txt", size_bytes=500)
    with pytest.raises(ValueError):
        storage.list_files(page=1, per_page=10, sort="invalid")


def test_all_names(storage):
    storage.add_file("a.txt", size_bytes=500)
    storage.add_file("b.txt", size_bytes=500)

    assert sorted(storage.all_names()) == ["a.txt", "b.txt"]


def test_backfill_from_disk(storage, tmp_path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "a.txt").write_text("1" * 500)
    (downloads_dir / "b.txt").write_text("2" * 500)
    (downloads_dir / "not_a_txt.dat").write_text("ignored")

    added = storage.backfill_from_disk(downloads_dir)

    assert added == 2
    assert storage.count() == 2
    assert storage.known_names() == {"a.txt", "b.txt"}
    assert storage.unmarked_names() == []

    # Повторный вызов не должен дублировать записи.
    added_again = storage.backfill_from_disk(downloads_dir)
    assert added_again == 0
    assert storage.count() == 2


def test_prune_missing_removes_records_without_files(storage, tmp_path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "alive.txt").write_text("1" * 500)
    storage.add_file("alive.txt", size_bytes=500, marked=True)
    storage.add_file("deleted.txt", size_bytes=500, marked=True)

    removed = storage.prune_missing(downloads_dir)

    assert removed == 1
    assert storage.known_names() == {"alive.txt"}


def test_prune_missing_is_idempotent(storage, tmp_path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "alive.txt").write_text("1" * 500)
    storage.add_file("alive.txt", size_bytes=500, marked=True)

    assert storage.prune_missing(downloads_dir) == 0
    assert storage.prune_missing(downloads_dir) == 0
    assert storage.count() == 1


def test_prune_missing_skips_when_dir_is_absent(storage, tmp_path):
    """Недоступная директория не должна приводить к очистке всей БД."""
    storage.add_file("a.txt", size_bytes=500, marked=True)

    removed = storage.prune_missing(tmp_path / "does_not_exist")

    assert removed == 0
    assert storage.count() == 1

import io
import zipfile

import pytest

from app.zip_extractor import ZipExtractor


def build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_creates_output_dir(tmp_path):
    output_dir = tmp_path / "a" / "b"
    ZipExtractor(output_dir=str(output_dir))
    assert output_dir.exists()


def test_extract_writes_files_and_returns_names_with_sizes(tmp_path):
    extractor = ZipExtractor(output_dir=str(tmp_path / "downloads"))
    zip_bytes = build_zip({"a.txt": b"hello", "b.txt": b"world!"})

    result = extractor.extract(zip_bytes)

    assert sorted(result) == [("a.txt", 5), ("b.txt", 6)]
    assert (tmp_path / "downloads" / "a.txt").read_bytes() == b"hello"
    assert (tmp_path / "downloads" / "b.txt").read_bytes() == b"world!"


def test_extract_raises_on_bad_zip(tmp_path):
    extractor = ZipExtractor(output_dir=str(tmp_path / "downloads"))
    with pytest.raises(zipfile.BadZipFile):
        extractor.extract(b"not a zip file")


def test_extract_blocks_zip_slip_with_relative_traversal(tmp_path):
    output_dir = tmp_path / "downloads"
    extractor = ZipExtractor(output_dir=str(output_dir))
    zip_bytes = build_zip(
        {
            "good.txt": b"legit",
            "../../evil.txt": b"malicious",
        }
    )

    result = extractor.extract(zip_bytes)

    # Вредоносное имя не попало в результат.
    assert result == [("good.txt", 5)]
    assert (output_dir / "good.txt").read_bytes() == b"legit"
    # Файл не появился за пределами output_dir.
    assert not (tmp_path / "evil.txt").exists()
    assert list(tmp_path.rglob("evil.txt")) == []


def test_extract_blocks_zip_slip_with_absolute_path(tmp_path):
    output_dir = tmp_path / "downloads"
    extractor = ZipExtractor(output_dir=str(output_dir))
    zip_bytes = build_zip(
        {
            "good.txt": b"legit",
            "/etc/evil.txt": b"malicious",
        }
    )

    result = extractor.extract(zip_bytes)

    assert result == [("good.txt", 5)]
    assert (output_dir / "good.txt").read_bytes() == b"legit"
    assert not (tmp_path / "etc" / "evil.txt").exists()


def test_extract_skips_directory_entries(tmp_path):
    output_dir = tmp_path / "downloads"
    extractor = ZipExtractor(output_dir=str(output_dir))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dir/", b"")
        archive.writestr("dir/file.txt", b"content")
    zip_bytes = buffer.getvalue()

    result = extractor.extract(zip_bytes)

    assert result == [("file.txt", 7)]

import io
import zipfile

import pytest

from zip_extractor import ZipExtractor


def build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_creates_output_dir(tmp_path):
    output_dir = tmp_path / "downloads"
    ZipExtractor(output_dir=str(output_dir))
    assert output_dir.exists()


def test_extract_writes_files_and_returns_namelist(tmp_path):
    extractor = ZipExtractor(output_dir=str(tmp_path / "downloads"))
    zip_bytes = build_zip({"a.txt": b"hello", "b.txt": b"world"})

    names = extractor.extract(zip_bytes)

    assert sorted(names) == ["a.txt", "b.txt"]
    assert (tmp_path / "downloads" / "a.txt").read_bytes() == b"hello"
    assert (tmp_path / "downloads" / "b.txt").read_bytes() == b"world"


def test_extract_raises_on_bad_zip(tmp_path):
    extractor = ZipExtractor(output_dir=str(tmp_path / "downloads"))
    with pytest.raises(zipfile.BadZipFile):
        extractor.extract(b"not a zip file")

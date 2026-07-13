from app.stats import calculate, count_digits


def test_count_digits_basic():
    assert count_digits("235") == {
        "0": 0, "1": 0, "2": 1, "3": 1, "4": 0,
        "5": 1, "6": 0, "7": 0, "8": 0, "9": 0,
    }


def test_count_digits_ignores_non_digits():
    result = count_digits("12\n3 ")
    assert result["1"] == 1
    assert result["2"] == 1
    assert result["3"] == 1
    assert sum(result.values()) == 3


def test_calculate_multiple_files(tmp_path):
    content_a = "1" * 500
    content_b = "23" * 250
    (tmp_path / "a.txt").write_text(content_a)
    (tmp_path / "b.txt").write_text(content_b)

    result = calculate(["a.txt", "b.txt"], tmp_path)

    assert result["skipped"] == []
    assert len(result["files"]) == 2

    summed_counts = {digit: 0 for digit in "0123456789"}
    for file_stats in result["files"]:
        for digit, cnt in file_stats["counts"].items():
            summed_counts[digit] += cnt

    assert summed_counts == result["total_counts"]
    assert result["total_chars"] == len(content_a) + len(content_b)


def test_calculate_skips_missing_file(tmp_path):
    (tmp_path / "a.txt").write_text("1" * 500)

    result = calculate(["a.txt", "missing.txt"], tmp_path)

    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["name"] == "missing.txt"
    assert len(result["files"]) == 1
    assert result["files"][0]["name"] == "a.txt"
    assert result["total_chars"] == 500

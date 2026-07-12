import logging

from logger import get_logger


def test_returns_named_logger_with_info_level():
    logger = get_logger("test.module.a")
    assert logger.name == "test.module.a"
    assert logger.level == logging.INFO


def test_adds_exactly_one_handler():
    logger = get_logger("test.module.b")
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_repeated_calls_do_not_duplicate_handlers():
    first = get_logger("test.module.c")
    second = get_logger("test.module.c")
    assert first is second
    assert len(first.handlers) == 1

from unittest.mock import Mock

import pytest

from app.rate_limiter import RateLimiter


class FakeClock:
    """Ручные, полностью детерминированные "часы" для тестов."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def sleep_mock():
    return Mock()


def make_limiter(clock, sleep_mock, **overrides):
    params = dict(
        interval=1.0,
        max_interval=10.0,
        backoff_factor=2.0,
        decrease_step=0.1,
        successes_before_decrease=3,
    )
    params.update(overrides)
    return RateLimiter(
        **params,
        sleep=sleep_mock,
        monotonic=clock.monotonic,
    )


def test_acquire_sleeps_for_remaining_time(clock, sleep_mock):
    limiter = make_limiter(clock, sleep_mock, interval=1.0)
    limiter.acquire()
    sleep_mock.reset_mock()

    clock.advance(0.3)
    limiter.acquire()

    sleep_mock.assert_called_once()
    (waited,), _ = sleep_mock.call_args
    assert waited == pytest.approx(0.7)


def test_acquire_does_not_sleep_when_enough_time_passed(clock, sleep_mock):
    limiter = make_limiter(clock, sleep_mock, interval=1.0)
    limiter.acquire()
    sleep_mock.reset_mock()

    clock.advance(1.5)
    limiter.acquire()

    sleep_mock.assert_not_called()


def test_penalize_increases_interval_up_to_max(clock, sleep_mock):
    limiter = make_limiter(
        clock, sleep_mock,
        interval=1.0, max_interval=3.0, backoff_factor=2.0,
    )

    limiter.penalize()
    assert limiter.interval == pytest.approx(2.0)

    limiter.penalize()
    assert limiter.interval == pytest.approx(3.0)

    limiter.penalize()
    assert limiter.interval == pytest.approx(3.0)


def test_reward_decreases_interval_after_n_successes_in_a_row(clock, sleep_mock):
    limiter = make_limiter(
        clock, sleep_mock,
        interval=1.0, max_interval=5.0, backoff_factor=2.0,
        decrease_step=0.2, successes_before_decrease=3,
    )
    limiter.penalize()
    assert limiter.interval == pytest.approx(2.0)

    limiter.reward()
    limiter.reward()
    assert limiter.interval == pytest.approx(2.0)

    limiter.reward()
    assert limiter.interval == pytest.approx(1.8)


def test_reward_does_not_go_below_base_interval(clock, sleep_mock):
    limiter = make_limiter(
        clock, sleep_mock,
        interval=1.0, max_interval=5.0, backoff_factor=2.0,
        decrease_step=0.7, successes_before_decrease=1,
    )
    limiter.penalize()

    limiter.reward()
    assert limiter.interval == pytest.approx(1.3)

    limiter.reward()
    assert limiter.interval == pytest.approx(1.0)

    limiter.reward()
    assert limiter.interval == pytest.approx(1.0)


def test_penalize_resets_reward_progress(clock, sleep_mock):
    limiter = make_limiter(
        clock, sleep_mock,
        interval=1.0, max_interval=5.0, backoff_factor=2.0,
        decrease_step=0.1, successes_before_decrease=3,
    )
    limiter.penalize()
    assert limiter.interval == pytest.approx(2.0)

    limiter.reward()
    limiter.reward()
    limiter.penalize()
    assert limiter.interval == pytest.approx(4.0)

    limiter.reward()
    assert limiter.interval == pytest.approx(4.0)

    limiter.reward()
    limiter.reward()
    assert limiter.interval == pytest.approx(3.9)


def test_no_real_sleep_is_used(clock, sleep_mock, monkeypatch):
    import time

    monkeypatch.setattr(
        time, "sleep", Mock(side_effect=AssertionError("real time.sleep was called"))
    )
    limiter = make_limiter(clock, sleep_mock, interval=1.0)
    limiter.acquire()
    clock.advance(0.1)
    limiter.acquire()

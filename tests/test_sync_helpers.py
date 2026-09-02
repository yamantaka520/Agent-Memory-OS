from datetime import UTC, datetime, timedelta

from agent_memory_os.sync import _norm_ts, _ts_too_future


def test_sync_normalizes_z_and_explicit_utc_to_the_same_instant() -> None:
    assert _norm_ts("2026-01-01T00:00:00Z") == _norm_ts(
        "2026-01-01T00:00:00+00:00"
    )


def test_sync_preserves_unparseable_embedded_z_separator() -> None:
    value = "2026-01-01Z00:00:00"

    assert _norm_ts(value) == value


def test_sync_naive_future_skew_uses_a_naive_utc_clock() -> None:
    utc_now = datetime.now(UTC)
    near = (utc_now + timedelta(seconds=60)).replace(tzinfo=None).isoformat()
    too_far = (utc_now + timedelta(seconds=600)).replace(tzinfo=None).isoformat()

    assert _ts_too_future(near) is False
    assert _ts_too_future(too_far) is True

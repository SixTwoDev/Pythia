import asyncio
import os
import time
from pathlib import Path

import pytest

from pythia.health import (
    HEARTBEAT_STALE_MULTIPLIER,
    heartbeat_loop,
    heartbeat_status,
)


def test_heartbeat_status_reports_stale_when_file_missing(tmp_path: Path) -> None:
    code, message = heartbeat_status(str(tmp_path / "absent"), interval_seconds=30)
    assert code == 1
    assert "does not exist" in message


def test_heartbeat_status_reports_fresh_for_just_touched_file(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    target.touch()
    code, message = heartbeat_status(str(target), interval_seconds=30)
    assert code == 0
    assert "fresh" in message


def test_heartbeat_status_reports_stale_when_mtime_exceeds_threshold(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    target.touch()
    # Push the mtime well past 3 x interval into the past.
    old = time.time() - (HEARTBEAT_STALE_MULTIPLIER * 30 + 60)
    os.utime(target, (old, old))
    code, message = heartbeat_status(str(target), interval_seconds=30)
    assert code == 1
    assert "stale" in message


@pytest.mark.asyncio
async def test_heartbeat_loop_returns_immediately_when_disabled(tmp_path: Path) -> None:
    # interval <= 0 → loop returns without ever creating the file.
    await asyncio.wait_for(heartbeat_loop(str(tmp_path / "x"), 0), timeout=1.0)
    assert not (tmp_path / "x").exists()


@pytest.mark.asyncio
async def test_heartbeat_loop_creates_parent_dir_and_touches_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "nested" / "dir" / "heartbeat"
    iterations = 0

    async def _short_sleep(_: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("pythia.health.asyncio.sleep", _short_sleep)
    with pytest.raises(asyncio.CancelledError):
        await heartbeat_loop(str(target), 1)

    assert target.exists(), "heartbeat file should have been created"
    assert target.parent.is_dir(), "parent directory should have been created"


@pytest.mark.asyncio
async def test_heartbeat_loop_keeps_running_when_a_touch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Simulate a transient OSError on the first touch; loop must log it and
    # keep going, eventually succeeding.
    iterations = 0

    async def _short_sleep(_: float) -> None:
        nonlocal iterations
        iterations += 1
        if iterations >= 3:
            raise asyncio.CancelledError

    target = tmp_path / "heartbeat"
    real_touch = Path.touch
    call_count = {"n": 0}

    def _flaky_touch(self: Path, mode: int = 0o666, exist_ok: bool = True) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("disk full")
        real_touch(self, mode=mode, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "touch", _flaky_touch)
    monkeypatch.setattr("pythia.health.asyncio.sleep", _short_sleep)

    with caplog.at_level("ERROR", logger="pythia.health"), pytest.raises(asyncio.CancelledError):
        await heartbeat_loop(str(target), 1)

    assert "could not touch" in caplog.text
    assert target.exists(), "second iteration's touch should have succeeded"

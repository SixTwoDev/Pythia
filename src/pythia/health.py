import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Heartbeat file the running bot touches periodically. The companion
# `pythia-healthcheck` CLI checks its mtime; K8s wires that CLI into a
# livenessProbe via `exec`. We don't add an HTTP server purely for probes —
# the "no inbound HTTP" first principle still holds in Socket Mode.
DEFAULT_HEARTBEAT_PATH = "/tmp/pythia/heartbeat"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30

# How many heartbeat intervals can pass before we declare the bot wedged.
# 3 means "miss two beats and you're dead" — tolerates one transient hiccup.
HEARTBEAT_STALE_MULTIPLIER = 3


async def heartbeat_loop(path: str, interval_seconds: int) -> None:
    """Touch `path` every `interval_seconds` so the liveness probe sees a
    fresh mtime. Returns immediately when interval_seconds <= 0 — useful
    for tests and operators who want to disable the heartbeat entirely.
    """
    if interval_seconds <= 0:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("starting heartbeat loop: touching %s every %ds", path, interval_seconds)
    while True:
        try:
            target.touch()
        except OSError:
            logger.exception("could not touch heartbeat file %s", path)
        await asyncio.sleep(interval_seconds)


def heartbeat_status(path: str, interval_seconds: int) -> tuple[int, str]:
    """Pure function used by the CLI. Returns (exit_code, message).

    Stale = mtime older than HEARTBEAT_STALE_MULTIPLIER * interval. Missing
    file is also stale. Anything else is fresh, exit 0.
    """
    max_age = max(interval_seconds, 1) * HEARTBEAT_STALE_MULTIPLIER
    target = Path(path)
    if not target.exists():
        return 1, f"heartbeat file {path} does not exist"
    age = time.time() - target.stat().st_mtime
    if age > max_age:
        return 1, f"heartbeat file {path} is stale ({age:.0f}s > {max_age}s)"
    return 0, f"heartbeat file {path} fresh ({age:.0f}s old)"


def healthcheck_cli() -> None:
    """Entry point for the `pythia-healthcheck` console script. Used by the
    Helm chart's livenessProbe — exit 0 when the bot is healthy, 1 otherwise.
    """
    path = os.environ.get("PYTHIA_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH)
    interval = int(
        os.environ.get("PYTHIA_HEARTBEAT_INTERVAL_SECONDS", str(DEFAULT_HEARTBEAT_INTERVAL_SECONDS))
    )
    code, message = heartbeat_status(path, interval)
    stream = sys.stderr if code else sys.stdout
    print(f"pythia-healthcheck: {message}", file=stream)
    sys.exit(code)

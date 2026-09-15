"""Shared waiting helpers for long-running operations."""

from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

T = TypeVar("T")


def wait_with_spinner(
    operation: Callable[[], T],
    description: str,
    delay_seconds: float = 1.5,
) -> T:
    """Run an operation and show a spinner with elapsed time when it takes long.

    The spinner is only shown after ``delay_seconds`` to avoid flicker for fast calls.
    """

    result: dict[str, T | BaseException | None] = {"value": None, "error": None}

    def _target() -> None:
        try:
            result["value"] = operation()
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            result["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()

    start = time.monotonic()
    progress: Progress | None = None

    while worker.is_alive():
        elapsed = time.monotonic() - start
        if progress is None and elapsed >= delay_seconds:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
                transient=True,
            )
            progress.start()
            progress.add_task(description, total=None)
        worker.join(timeout=0.1)

    if progress is not None:
        progress.stop()

    if result["error"] is not None:
        raise result["error"]  # type: ignore[misc]

    return result["value"]  # type: ignore[return-value]

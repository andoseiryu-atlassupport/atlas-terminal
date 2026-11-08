"""Request-local elapsed-time budgets for bounded multi-document source reads."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from atlas_terminal.errors import AtlasError

_DEADLINE: ContextVar[float | None] = ContextVar("source_deadline", default=None)


def remaining_timeout(default: float = 20) -> float:
    deadline = _DEADLINE.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AtlasError(
            "SOURCE_UNAVAILABLE",
            "Source read reached its elapsed-time budget.",
            details={"reason": "source_deadline_exceeded"},
        )
    return min(default, remaining)


def bounded_pause(seconds: float, sleep: Callable[[float], None]) -> None:
    sleep(min(seconds, remaining_timeout(seconds)))
    remaining_timeout()


def timeout_options() -> dict[str, Any]:
    """Leave an injected client's normal timeout unchanged outside a bounded read."""
    return {} if _DEADLINE.get() is None else {"timeout": remaining_timeout()}


@contextmanager
def source_deadline(seconds: float) -> Iterator[None]:
    previous = _DEADLINE.get()
    deadline = time.monotonic() + seconds
    token = _DEADLINE.set(min(previous, deadline) if previous is not None else deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)

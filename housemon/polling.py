"""Polling a cloud account gently: every interval while it answers, backing off while it fails, and not at all while
its sign-in has lapsed, until it's signed in again, so a lapsed sign-in isn't tried over and over around the clock."""

import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

BACKOFF = (120.0, 300.0, 600.0)  # seconds to wait after one, two, and three or more failed polls in a row
SIGN_IN_CHECK = 10.0  # seconds between looks at the sign-in's file while it's lapsed, for a new one


def _stamp(path: Path) -> tuple[int, int] | None:
    """What changes when the file is written again, as signing in again does, or None while there's none."""
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_mtime_ns


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:g} min"


def poll_gently(
    poll: Callable[[float], Iterable[T]],
    publish: Callable[[T], None],
    stop: threading.Event,
    interval: float,
    *,
    sign_in: Path,
    lapsed: type[Exception],
    failed: tuple[type[Exception], ...],
    sign_in_again: str,
    log: Callable[[str], None],
    describe: Callable[[Exception], str] = str,
    on_failure: Callable[[Exception], None] = lambda e: None,
) -> None:
    """Call publish for each thing poll(now) gives, every interval seconds, until stop is set.

    A poll that raises one of `failed` is logged, as describe says, and polling backs off, waiting BACKOFF's steps,
    or the interval if that's longer, until a poll works again. One that raises `lapsed`, the sign-in no longer good,
    is logged once, saying to sign in again with the command `sign_in_again`, and polling waits until the file at
    `sign_in` is written again, or made, as signing in does. on_failure(e) is called after each failure of either
    kind, as for a source to forget what it knew of the account."""
    failures = 0
    while not stop.is_set():
        try:
            for item in poll(time.time()):
                publish(item)
        except lapsed as e:
            on_failure(e)
            log(f"{e}; sign in again with {sign_in_again}, and until then it's left alone")
            seen = _stamp(sign_in)
            while not stop.wait(SIGN_IN_CHECK):
                if _stamp(sign_in) not in (seen, None):
                    log("signed in again; polling again")
                    break
            failures = 0
            continue
        except failed as e:
            on_failure(e)
            failures += 1
            wait = max(interval, BACKOFF[min(failures, len(BACKOFF)) - 1])
            log(f"{describe(e)}; trying again in {_minutes(wait)}")
            stop.wait(wait)
            continue
        if failures:
            log("polling again as usual")
        failures = 0
        stop.wait(interval)

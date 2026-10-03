"""Observer lists and futures that never keep GUI objects alive (SW_design §3.3 rules 4–5, TS SWD-PM3-07).

* ``ObserverList`` stores bound methods **weakly** (``weakref.WeakMethod``); plain functions strongly. Dead
  entries are pruned lazily. Observers run on backend threads and must return in < 1 ms.
* ``ReleasingFuture`` drops its done-callbacks once they have run.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/observers.py @37c87471 (as-is for ObserverList and
ReleasingFuture; AsyncCall replaced by the generator jobs of ``core.jobs``; helpers ``done_future``).

Implements: NFR-004 (no leaks), SW-PLT-002
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
import types
import weakref
from collections.abc import Callable
from typing import Any, Generic, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_Ref = Callable[[], Any]


class _Strong:
    __slots__ = ("fn",)

    def __init__(self, fn: Callable[..., Any]) -> None:
        self.fn = fn

    def __call__(self) -> Callable[..., Any]:
        return self.fn


def is_weakly_held(fn: Callable[..., Any]) -> bool:
    """True if ``ObserverList`` stores ``fn`` weakly by default (a bound method)."""
    return isinstance(fn, types.MethodType)


class ObserverList(Generic[F]):
    """Thread-safe token-based observer list. ``weak=None`` → weak for bound methods, strong otherwise."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[int, _Ref] = {}
        self._next = 1

    def add(self, fn: F, *, weak: bool | None = None) -> int:
        if weak is None:
            weak = is_weakly_held(fn)
        if weak and not is_weakly_held(fn):
            raise TypeError("weak=True needs a bound method")
        ref: _Ref = weakref.WeakMethod(fn) if weak else _Strong(fn)  # type: ignore[arg-type]
        with self._lock:
            token = self._next
            self._next += 1
            self._items[token] = ref
        return token

    def remove_token(self, token: int) -> None:
        with self._lock:
            self._items.pop(token, None)

    def snapshot(self) -> list[F]:
        with self._lock:
            refs = list(self._items.items())
        out: list[F] = []
        dead: list[int] = []
        for token, ref in refs:
            fn = ref()
            if fn is None:
                dead.append(token)
            else:
                out.append(fn)
        if dead:
            with self._lock:
                for token in dead:
                    self._items.pop(token, None)
        return out

    def call_all(self, *args: Any, on_error: Callable[[BaseException], None] | None = None) -> int:
        """Call every live observer; exceptions go to ``on_error`` (never propagate)."""
        fns = self.snapshot()
        n = len(fns)
        while fns:
            fn = fns.pop(0)
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001 — an observer must never break the publisher
                if on_error is not None:
                    on_error(exc)
            finally:
                del fn
        return n

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self.snapshot())


class ReleasingFuture(cf.Future):
    """``concurrent.futures.Future`` that releases its done-callbacks once invoked."""

    def _invoke_callbacks(self) -> None:
        with self._condition:
            callbacks, self._done_callbacks = self._done_callbacks, []
        while callbacks:
            cb = callbacks.pop(0)
            try:
                cb(self)
            except Exception:  # noqa: BLE001 — stdlib policy (logged, not raised)
                cf._base.LOGGER.exception("exception calling callback for %r", self)
            finally:
                del cb


def done_future(value: Any = None) -> ReleasingFuture:
    f = ReleasingFuture()
    f.set_result(value)
    return f


def failed_future(exc: BaseException) -> ReleasingFuture:
    f = ReleasingFuture()
    f.set_exception(exc)
    return f


__all__ = ["ObserverList", "ReleasingFuture", "is_weakly_held", "done_future", "failed_future"]

"""EventBus: topics of SW_design §15.3, payloads are frozen dataclasses; bounded history of 10 000 records.

Observers run on backend threads and must return in < 1 ms; bound methods are held weakly (the GUI bridge is
never kept alive by the backend, SW_design §3.3 rule 4). ``"*"`` subscribes to every topic. Events are
published after locks are released and after the action they report (act first, publish after).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/events.py @37c87471 (adapted: topic routing, clock-based
stamps, EventRecord from core.model).

Implements: SW-PLT-002, SAF-SW-005 (indicator change events), NFR-004 (bounded history)
"""
from __future__ import annotations

import collections
import logging
import threading
from collections.abc import Callable
from typing import Any

from bend_stand.core.clock import MONOTONIC, Clock
from bend_stand.core.model import EventRecord, Token
from bend_stand.core.observers import ObserverList, is_weakly_held

log = logging.getLogger("bend_stand.events")
MAX_EVENTS = 10_000

Observer = Callable[[EventRecord], None]


def _observer_failed(exc: BaseException) -> None:
    log.error("event observer failed", exc_info=(type(exc), exc, exc.__traceback__))


class EventBus:
    def __init__(self, clock: Clock = MONOTONIC, maxlen: int = MAX_EVENTS) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._subs: dict[str, ObserverList[Observer]] = {}
        self._topic_of: dict[Token, str] = {}
        self._next = 1
        self._history: collections.deque[EventRecord] = collections.deque(maxlen=maxlen)
        self.published = 0

    def subscribe(self, topic: str, cb: Observer, *, weak: bool = True) -> Token:
        with self._lock:
            lst = self._subs.setdefault(topic, ObserverList())
        inner = lst.add(cb, weak=bool(weak and is_weakly_held(cb)))   # functions/lambdas: strong
        with self._lock:
            token = self._next
            self._next += 1
            self._topic_of[token] = f"{topic}\x00{inner}"
        return token

    def unsubscribe(self, token: Token) -> None:
        with self._lock:
            key = self._topic_of.pop(token, None)
            if key is None:
                return
            topic, inner = key.split("\x00")
            lst = self._subs.get(topic)
        if lst is not None:
            lst.remove_token(int(inner))

    def publish(self, topic: str, payload: Any = None) -> EventRecord:
        rec = EventRecord(topic, self.clock.monotonic_ns(), payload)
        with self._lock:
            self._history.append(rec)
            self.published += 1
            lists = [self._subs.get(topic), self._subs.get("*")]
        for lst in lists:
            if lst is not None:
                lst.call_all(rec, on_error=_observer_failed)
        return rec

    def history(self, topic: str | None = None, limit: int | None = None) -> list[EventRecord]:
        with self._lock:
            items = list(self._history)
        if topic is not None:
            items = [e for e in items if e.topic == topic]
        return items[-limit:] if limit else items

    def log(self, text: str, level: int = logging.INFO, **data: Any) -> EventRecord:
        """Publish a ``log`` topic record (SW event text)."""
        log.log(level, "%s", text)
        return self.publish("log", {"text": text, "level": level, **data})

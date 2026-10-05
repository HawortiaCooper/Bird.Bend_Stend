"""Engine base for the Qt-free wizard state machines (SW_design §9.1, KD-08).

An engine holds one immutable ``EngineState``; ``_set(**changes)`` replaces it and publishes it to the engine's
subscribers (weakly held, backend threads) and to its EventBus topic (``tare.state``, ``cal.load.state``,
``cal.travel.state``) — after the change (act first, publish after). Methods never block: board work goes to the
Worker as generator jobs, captures to the ``CaptureHub``, motion to the ``MotionController``.

Implements: SW-CAL-001 (engine pattern), SW-TARE-001 (non-modal progress)
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from bend_stand.core.model import EngineState, GateCode, GateItem, GateResult, Severity, Token
from bend_stand.core.observers import ObserverList, is_weakly_held

log = logging.getLogger("bend_stand.core.engine")


def refuse(code: str, text: str, hint: str | None = None) -> GateResult:
    return GateResult((GateItem(code, Severity.REFUSE, text, hint),))


class EngineBase:
    KIND = "engine"
    TOPIC = "engine.state"
    PHASES: tuple[str, ...] = ()
    TERMINAL = ("IDLE", "DONE", "ABORTED", "REFUSED")

    def __init__(self, backend: Any) -> None:
        self._be = backend
        self._lock = threading.RLock()
        self._obs: ObserverList[Callable[[EngineState], None]] = ObserverList()
        self._tokens: dict[Token, int] = {}
        self._next_token = 1
        self._state = EngineState(self.KIND, "IDLE")

    # ---- protocol ---------------------------------------------------------------------------------------
    def state(self) -> EngineState:
        return self._state

    def subscribe(self, cb: Callable[[EngineState], None]) -> Token:
        inner = self._obs.add(cb, weak=is_weakly_held(cb))
        with self._lock:
            tok = self._next_token
            self._next_token += 1
            self._tokens[tok] = inner
        return tok

    def unsubscribe(self, token: Token) -> None:
        with self._lock:
            inner = self._tokens.pop(token, None)
        if inner is not None:
            self._obs.remove_token(inner)

    @property
    def active(self) -> bool:
        return self._state.phase not in self.TERMINAL

    # ---- helpers ----------------------------------------------------------------------------------------
    def _set(self, **changes: Any) -> EngineState:
        with self._lock:
            st = replace(self._state, **changes)
            self._state = st
        self._obs.call_all(st, on_error=lambda exc: log.error("engine observer failed: %s", exc))
        self._be.events.publish(self.TOPIC, st)
        return st

    def _reset(self, **changes: Any) -> EngineState:
        with self._lock:
            self._state = EngineState(self.KIND, "IDLE")
        return self._set(**changes)

    def _busy(self) -> GateResult:
        return refuse(GateCode.OPERATION_RUNNING, f"{self.KIND} already running")

    @staticmethod
    def _needs_confirmation(g: GateResult, confirmed: bool) -> GateResult | None:
        """§15 contract (GRQ-B-23, §15.4 rule 6): a start gate with CONFIRM items starts nothing without
        ``confirmed=True``; the gate result itself is returned (``ok`` and ``needs_confirmation`` True) so the GUI shows
        the confirmation and repeats the start with ``confirmed=True``."""
        if g.confirm_items and not confirmed:
            return g
        return None


__all__ = ["EngineBase", "refuse"]

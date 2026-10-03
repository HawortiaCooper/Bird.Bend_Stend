"""The ``sim`` / ``sim:<scenario.json>`` endpoint: a ``VirtualTransportPair`` with a ``SimBoard`` on its board end
and a ``SimControl`` (SW_design §4.1, §12.1). On the real clock the board runs its own 1 ms thread; on the
lockstep clock the backend calls ``step(now)`` first in every advance step (§12.4 hook a).

Implements: SYS-008, D-07
"""
from __future__ import annotations

from collections.abc import Callable

from bend_stand.core.clock import Clock
from bend_stand.io.sim.board import SimBoard, SimConfig
from bend_stand.io.sim.control import SimControl, SimScenario, default_scenario
from bend_stand.io.sim.models import Hx711Model
from bend_stand.io.transport import LINK_BYTES_PER_S, VirtualTransportPair


class SimEndpoint:
    def __init__(self, clock: Clock, scenario: str | SimScenario | None = None, *,
                 bytes_per_s: float | None = LINK_BYTES_PER_S, latency_ns: int = 0,
                 advance: Callable[[float], None] | None = None, nvm_path: str | None = None) -> None:
        self.clock = clock
        sc = (SimScenario.load(scenario) if isinstance(scenario, str) and scenario
              else scenario if isinstance(scenario, SimScenario) else default_scenario())
        self.scenario = sc
        self.pair = VirtualTransportPair(clock, bytes_per_s=bytes_per_s, latency_ns=latency_ns)
        self.board = SimBoard(clock, self.pair.board, config=SimConfig(seed=sc.seed, nvm_path=nvm_path),
                              afe=Hx711Model(seed=sc.seed))
        self.control = SimControl(self.board, advance=advance)
        sc.apply(self.board, self.control)

    @property
    def transport(self):  # noqa: ANN201 - VirtualEnd
        return self.pair.pc

    def start(self) -> None:
        self.board.start()

    def stop(self) -> None:
        self.board.stop()

    def step(self, now_ns: int | None = None) -> None:
        self.board.step(now_ns)

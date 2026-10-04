"""SimControl — world control of the in-process simulator with the **frozen shared vocabulary v2** of
``00_System/tools/README.md`` (same action names, arguments and reply shape as the FW twin's control port):
``act(action, **args) -> {"ok": True, ...} | {"ok": False, "error": ...}``. Twin-only actions answer
``{"ok": False, "error": "twin only"}``. Test-only extras (never used by behavioural-equality tests):
``override_status``, ``emit_event``, ``inject_nack``, ``inject_store_mismatch`` (SW_design §12.4).

Also the scenario file ``bird.bend.simscenario`` v1 (world, params, schedule).

Implements: SYS-008, D-07 (simulator control for validation hooks a–h of §12.4)
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.io.sim.board import SimBoard
from bend_stand.io.sim.models import Specimen

#: vocabulary v2 (tools/README): action → side ("both" | "T")
VOCABULARY: Mapping[str, str] = {
    "estop": "both", "drv_power": "both", "button": "both", "limit": "both", "wire": "both", "chatter": "T",
    "alm": "both", "pend": "both", "specimen": "both", "load_offset": "both", "afe": "both",
    "world_shift": "both", "inject": "both", "rx_bytes": "both", "on_frame": "both", "on_event": "both",
    "flash": "T", "iwdg": "T", "clk": "T", "clock": "both", "reset": "both", "query": "both",
    "driver": "T",                       # v0.6: S variant (DIR wiring inversion, automatic PEND)
}
TWIN_ONLY_INJECT = {"isr_storm"}
#: twin actions the simulator answers in its own variant (README: ``flash`` "T (S: record-level cut)")
SIM_VARIANTS = frozenset({"flash", "driver"})
RESET_CAUSES = {"pin": pg.ResetCause.PIN, "power": pg.ResetCause.POWER_ON, "iwdg": pg.ResetCause.IWDG,
                "software": pg.ResetCause.SOFTWARE}


def _err(text: str) -> dict[str, Any]:
    return {"ok": False, "error": text}


def _cmd_code(name: str | int | None) -> int | None:
    if name is None:
        return None
    if isinstance(name, int):
        return name
    return int(pg.Cmd[name])


class SimControl:
    """Thread-safe control of one ``SimBoard`` (see module doc)."""

    def __init__(self, board: SimBoard, advance: Callable[[float], None] | None = None) -> None:
        self.board = board
        self.advance = advance              # lockstep: backend/test advance (ms); None on the real clock
        self._lock = threading.RLock()
        self.log: list[dict[str, Any]] = []
        board.act_hook = lambda a: self.act(a.pop("action"), **a)

    # ---------------------------------------------------------------------------------------- dispatch
    def act(self, action: str, **args: Any) -> dict[str, Any]:
        side = VOCABULARY.get(action)
        if side is None:
            return _err(f"unknown action {action!r}")
        if side == "T" and action not in SIM_VARIANTS:
            return _err("twin only")
        fn = getattr(self, f"_a_{action}")
        with self._lock:
            self.log.append({"action": action, **args})
            try:
                res = fn(**args)
            except (TypeError, KeyError, ValueError) as exc:
                return _err(f"{action}: {exc}")
        return {"ok": True, **(res or {})}

    # ---------------------------------------------------------------------------------------- inputs
    def _a_estop(self, open: bool, drv_power_follows: bool = True, k1_delay_ms: int = 20,  # noqa: A002
                 bounce_ms: list[int] | None = None) -> None:
        b = self.board
        with b._lock:  # noqa: SLF001
            self._set_with_bounce(lambda v: setattr(b.world, "estop_open", v), bool(open), bounce_ms)
            if open and drv_power_follows:          # vocabulary v2: supply drops k1_delay_ms later (no contactor
                                                    # since D-41; only seen with the optional presence sense)
                _delayed(b, k1_delay_ms, lambda: setattr(b.world, "drv_power", False))

    def _a_drv_power(self, on: bool, bounce_ms: list[float] | None = None) -> None:
        self._set_with_bounce(lambda v: setattr(self.board.world, "drv_power", v), bool(on), bounce_ms)

    def _set_with_bounce(self, setter: Callable[[bool], None], level: bool, bounce_ms: list[float] | None) -> None:
        """Set an input now; ``bounce_ms`` = durations (ms) of the contact bounce: the level toggles back and forth
        with these interval lengths and settles at ``level`` (vocabulary v2 ``bounce_ms``)."""
        setter(level)
        seq = list(bounce_ms or ())
        t = 0.0
        for i, dt in enumerate(seq):
            t += float(dt)
            cur = (not level) if i % 2 == 0 else level
            if i == len(seq) - 1:
                cur = level
            _delayed(self.board, t, lambda v=cur: setter(v))

    def _a_button(self, name: str, pressed: bool, bounce_ms: list[float] | None = None) -> None:
        if name == "stop":
            raise ValueError("button 'stop' retired (ICD v0.5, D-36: the red button is the E-stop)")
        if name != "pause":
            raise ValueError(f"button {name!r}")
        self._set_with_bounce(lambda v: setattr(self.board.world, "pause_btn", v), bool(pressed), bounce_ms)

    def _a_limit(self, name: str, active: bool | None = None, position_um: int | None = None,
                 bounce_ms: list[float] | None = None) -> None:
        w = self.board.world
        if name not in ("start", "end"):
            raise ValueError(f"limit {name!r}")
        attr = "limit_start_forced" if name == "start" else "limit_end_forced"
        if position_um is not None:
            setattr(w, "start_switch_um" if name == "start" else "end_switch_um", int(position_um))
        if active is None or not bounce_ms:
            setattr(w, attr, active)
        else:
            self._set_with_bounce(lambda v: setattr(w, attr, v), bool(active), bounce_ms)

    def _a_wire(self, input: str, broken: bool) -> None:  # noqa: A002
        if input == "stop":
            raise ValueError("wire 'stop' retired (ICD v0.5, D-36)")
        if input not in ("estop", "start", "end", "pause", "alm", "pend", "drv_power"):
            raise ValueError(f"wire input {input!r}")
        w = self.board.world
        if broken:
            w.broken.add(input)
        else:
            w.broken.discard(input)

    def _a_alm(self, active: bool) -> None:
        self.board.world.alm = bool(active)

    def _a_pend(self, active: bool) -> None:
        self.board.world.pend = bool(active)

    def _a_specimen(self, kind: str = "none", **kw: Any) -> None:
        self.board.world.specimen = Specimen(kind=kind, **kw)

    def _a_load_offset(self, counts: int) -> None:
        self.board.afe.offset_counts = int(counts)

    def _a_afe(self, rate_error: float | None = None, noise_counts: float | None = None, stall: bool | None = None,
               saturate: str | None = "unset", drop_every: int | None = None, miss_next: int | None = None,
               sck_overrun: bool | None = None, raw_script: list[int] | None = None) -> None:
        a = self.board.afe
        if sck_overrun is not None:
            raise ValueError("sck_overrun: twin only")
        if rate_error is not None:
            a.rate_error = float(rate_error)
        if noise_counts is not None:
            a.noise_counts = float(noise_counts)
        if stall is not None:
            if a.stall and not stall:
                a.schedule_from(self.board.now_us())
            a.stall = bool(stall)
        if saturate != "unset":
            a.saturate = saturate
        if drop_every is not None:
            a.drop_every = int(drop_every)
        if miss_next is not None:
            a.miss_next = int(miss_next)
        if raw_script is not None:
            a.raw_script.extend(int(r) for r in raw_script)

    def _a_driver(self, dir_wiring_inverted: bool | None = None, pend_auto: bool | None = None,
                  pend_lag_ms: float | None = None) -> dict[str, Any]:
        """S variant of the twin's ``driver``: DIR wiring / SW5 inverted in the world (the carriage follows the
        inverted DIR pin, x stays continuous); PEND is modelled automatically (inactive while pulsing)."""
        b = self.board
        if dir_wiring_inverted is not None:
            b.set_dir_inverted(bool(dir_wiring_inverted))
        return {"variant": "sim", "pend_auto": True}

    def _a_world_shift(self, um: int) -> None:
        self.board.world.x_um_true_offset += int(um)

    # ---------------------------------------------------------------------------------------- faults
    def _a_inject(self, fault: str, duration_ms: int | None = None, where: str | None = None,
                  cmd: str | int | None = None, what: str = "response", n: int = 1, ms: int = 0) -> None:
        b = self.board
        if fault in TWIN_ONLY_INJECT or where is not None:
            raise ValueError("twin only")
        now = b.now_us()
        dur = int(duration_ms or 0) * 1000
        if fault in ("drop_next", "duplicate_next", "delay_next", "corrupt_next"):
            if what not in ("request", "response"):
                raise ValueError(f"what {what!r}")
            b.add_fault(fault, _cmd_code(cmd), what="response" if fault == "corrupt_next" else what, n=int(n),
                        ms=int(ms))
        elif fault == "link_silence":
            b.link_silence_until_us = now + dur
        elif fault == "tx_congestion":
            b.tx_congestion_until_us = now + dur
        elif fault == "hang":
            b.hang_until_us = now + dur
        elif fault == "rx_corrupt":
            b.link_silence_until_us = now + dur         # model: corrupted bytes = lost frames
        elif fault == "step_fault":
            b.inject_step_fault()
        else:
            raise ValueError(f"fault {fault!r}")

    def _a_rx_bytes(self, hex: str, at_us: int | None = None) -> None:  # noqa: A002
        b = self.board
        data = bytes.fromhex(hex)

        def feed() -> None:
            for fr in b.decoder.feed(data, b.clock.monotonic_ns()):
                b._on_frame(fr)  # noqa: SLF001
        if at_us is None or at_us <= b.now_us():
            with b._lock:  # noqa: SLF001
                feed()
        else:
            _delayed(b, (int(at_us) - b.now_us()) / 1000, feed)

    def _a_on_frame(self, cmd: str | int, delay_us: int = 0, then: dict[str, Any] | None = None,
                    nth: int = 1) -> None:
        self.board.on_frame_actions.append({"cmd": _cmd_code(cmd), "nth": int(nth), "delay_us": int(delay_us),
                                            "then": dict(then or {}), "count": 0})

    def _a_on_event(self, code: str | int, delay_us: int = 0, then: dict[str, Any] | None = None,
                    nth: int = 1) -> None:
        c = int(pg.Event[code]) if isinstance(code, str) else int(code)
        self.board.on_event_actions.append({"code": c, "nth": int(nth), "delay_us": int(delay_us),
                                            "then": dict(then or {}), "count": 0})

    def _a_flash(self, cut_after_word: int | None = None, cut_in_erase: int | None = None,
                 reset: str | None = None) -> dict[str, Any]:
        self.board.nvm.cut_next_save = True          # S variant: record-level cut on the next SAVE
        return {"variant": "record"}

    # ---------------------------------------------------------------------------------------- time / reset
    def _a_clock(self, advance_ms: float | None = None, advance_us: int | None = None) -> dict[str, Any]:
        if self.advance is None:
            raise ValueError("clock: lock-step only")
        ms = float(advance_ms or 0) + float(advance_us or 0) / 1000.0
        self.advance(ms)
        return {"t_us": self.board.now_us() & 0xFFFFFFFF}

    def _a_reset(self, cause: str = "pin", pc: int | None = None, cfsr: int | None = None) -> None:
        if cause == "hardfault":
            raise ValueError("reset cause 'hardfault': twin only")
        self.board.boot(int(RESET_CAUSES[cause]))

    # ---------------------------------------------------------------------------------------- query
    def _a_query(self, what: str, since_us: int | None = None) -> dict[str, Any]:
        b = self.board
        with b._lock:  # noqa: SLF001
            if what == "world":
                w = b.world
                xt = b._x_true()  # noqa: SLF001
                return {"x_um_true": xt, "x_um": b.x_um, "estop_open": w.estop_input_open(),
                        "drv_power": w.power_present(), "limit_start": w.limit_start(xt),
                        "limit_end": w.limit_end(xt), "pause": w.pause_btn,
                        "alm": w.alm, "pend": w.pend, "load_n": b._force_n(),  # noqa: SLF001
                        "raw_last": b.last_raw, "steps": b.steps}
            if what == "pulses":
                return {"pul_count": b.pulses, "pos_steps": b.pos_steps}
            if what == "outputs":
                return {"ena": not b.ena_disabled, "driver_energised": b.driver_energised(),
                        "rate": int(b.p("afe.rate_sps")) == 1, "led": False, "trip_relay": False}
            if what == "wire_log":
                return {"frames": [r for r in b.wire_log if since_us is None or r["first_us"] >= since_us]}
            if what == "sent":
                return {"frames": [r for r in b.sent_log if since_us is None or (r["t_us"] or 0) >= since_us]}
            if what == "flash":
                return b.nvm.summary()
            if what == "edges":
                return {"edges": [], "model": "position integrator (no PUL edges in the simulator)"}
            if what == "seam_log":
                raise ValueError("seam_log: twin only")
        raise ValueError(f"query {what!r}")

    # ---------------------------------------------------------------------------------------- test-only extras
    def override_status(self, set_bits: int = 0, clear_bits: int = 0, duration_ms: int = 0) -> None:
        b = self.board
        b.status_override = (int(set_bits), int(clear_bits), b.now_us() + int(duration_ms) * 1000)

    def emit_event(self, code: int, arg: int = 0, value: int = 0, value2: int = 0) -> None:
        with self.board._lock:  # noqa: SLF001
            self.board.emit(int(code), arg, value, value2)

    def inject_nack(self, cmd: str | int, status: int | str, detail: int = 0, count: int = 1) -> None:
        st = int(pg.Status[status]) if isinstance(status, str) else int(status)
        self.board.add_fault("nack", _cmd_code(cmd), what="request", n=int(count), status=st, detail=int(detail))

    def inject_store_mismatch(self, key: str, stored_value: Any) -> None:
        if key not in pgen.BY_KEY:
            raise KeyError(key)
        self.board.store_mismatch[key] = stored_value

    # ---------------------------------------------------------------------------------------- typed wrappers
    def set_estop(self, open: bool, **kw: Any) -> dict[str, Any]:  # noqa: A002
        return self.act("estop", open=open, **kw)

    def press(self, name: str) -> dict[str, Any]:
        return self.act("button", name=name, pressed=True)

    def release(self, name: str) -> dict[str, Any]:
        return self.act("button", name=name, pressed=False)

    def set_specimen(self, kind: str, **kw: Any) -> dict[str, Any]:
        return self.act("specimen", kind=kind, **kw)


def _delayed(board: SimBoard, delay_ms: float, fn: Callable[[], None]) -> None:
    from bend_stand.io.sim.board import _Delayed  # noqa: PLC0415

    board.delayed.append(_Delayed(board.now_us() + int(delay_ms * 1000), fn))


# ============================================================================================ scenario

@dataclass
class SimScenario:
    """``bird.bend.simscenario`` v1 (tools/README): world, params, schedule (``t_ms`` + vocabulary action)."""

    world: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    schedule: list[dict[str, Any]] = field(default_factory=list)
    seed: int = 1
    features: int | None = None          # INFO feature mask (names or int in the file); None = simulator default

    @classmethod
    def load(cls, path: str | Path) -> SimScenario:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SimScenario:
        if d.get("schema") != "bird.bend.simscenario":
            raise ValueError("not a bird.bend.simscenario file")
        if int(d.get("version", 1)) > 1:
            raise ValueError("scenario made by a newer version")
        return cls(dict(d.get("world", {})), dict(d.get("params", {})), list(d.get("schedule", [])),
                   int(d.get("seed", 1)), parse_features(d["features"]) if "features" in d else None)

    def apply(self, board: SimBoard, control: SimControl) -> None:
        """World and AFE before the test; ``params`` written into the board RAM (the in-process simulator has no
        separate harness); ``schedule`` armed relative to now."""
        if self.features is not None:            # e.g. the M1 FW mask (SW-C-M1-01); bits of absent features → 0
            board.features = self.features
        w = self.world
        bw = board.world
        for k in ("stroke_um", "start_switch_um", "end_switch_um"):
            if k in w:
                setattr(bw, k, int(w[k]))
        if "estop_open" in w:
            bw.estop_open = bool(w["estop_open"])
        if "cell_counts_per_n" in w:
            board.afe.counts_per_n = float(w["cell_counts_per_n"])
        if "load_offset_counts" in w:
            board.afe.offset_counts = int(w["load_offset_counts"])
        afe = w.get("afe", {})
        if "rate_error" in afe:
            board.afe.rate_error = float(afe["rate_error"])
        if "noise_counts" in afe:
            board.afe.noise_counts = float(afe["noise_counts"])
        if "rate_sps" in afe:
            board.afe.rate_sps = float(afe["rate_sps"])
        drv = w.get("driver", {})
        if "drv_power" in drv:
            bw.drv_power = bool(drv["drv_power"])
        if "specimen" in w:
            control.act("specimen", **w["specimen"])
        for k, v in self.params.items():
            meta = pgen.BY_KEY[k]
            board.params[k] = meta.enum_value(v) if isinstance(v, str) and meta.enum else v
        board.relatch_boot_params()
        if "nvm_stall_ms" in w:
            board.cfg.nvm_stall_ms.update({str(k): int(v) for k, v in w["nvm_stall_ms"].items()})
        if "ena_hardwired_cut" in w:
            bw.ena_hardwired_cut = bool(w["ena_hardwired_cut"])
        for entry in self.schedule:
            e = dict(entry)
            t_ms = float(e.pop("t_ms"))
            action = e
            _delayed(board, t_ms, lambda a=action: control.act(a["action"], **{k: v for k, v in a.items()
                                                                             if k != "action"}))


def parse_features(spec: Any) -> int:
    """Feature mask from an int, a list of names or a comma-separated string of ``FEATURES_BITS`` names."""
    if isinstance(spec, int):
        return spec
    names = spec.split(",") if isinstance(spec, str) else list(spec)
    mask = 0
    for n in names:
        n = str(n).strip().upper()
        if n:
            mask |= int(pg.Features[n])
    return mask


def default_scenario() -> SimScenario:
    """Default world: offset 50 000 counts, steps/mm 800 (SWD-P1-15, B31-03)."""
    return SimScenario(world={"load_offset_counts": 50_000}, params={"motion.steps_per_mm": 800.0})

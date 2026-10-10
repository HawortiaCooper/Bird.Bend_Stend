"""Pure display table: generated status names → indicator chips (SW_design_GUI §2.4, P5, P8).

* :data:`NAME_TABLE` has one entry for **every** name of ``DATA_FLAGS_BITS``, ``DATA_STATUS_BITS``,
  ``FAULTS_BITS`` and ``SYS_FLAGS_BITS`` (generated, ``core.protocol_gen``): the chip(s) it belongs to and its
  polarity, or "no chip" with a reason (G-39). Items are read as ``indicators[name.lower()]`` (B3-18).
* :func:`evaluate_chips` maps a ``BackendStatus`` to a list of :class:`ChipView` (level, text, tooltip). It is a
  display mapping only (P1) and unit-tested.
* P5: a chip with an ``UNKNOWN`` member is grey "?" — never green. Only an alarm member outranks it.

D-36: there is no physical holding STOP button any more; ``STOP_BTN`` stays in the generated tables until CR-01
(ICD v0.5) removes it and has no chip.

Implements: SAF-SW-005 (indicator set, UNKNOWN grey, clear procedure per latch), SW-STOP-002 (KEY chip, KL-01)
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bend_stand.core import protocol_gen as pg

# --------------------------------------------------------------------------------------------- name table

#: Polarity of an item: how ON / OFF map to a level.
POLARITY_LEVELS: Mapping[str, tuple[str, str]] = {   # (level when ON, level when OFF)
    "bad_on": ("alarm", "ok"),
    "warn_on": ("warn", "ok"),
    "good_on": ("ok", "alarm"),
    "good_on_warn": ("ok", "warn"),
    "info_on": ("info", "ok"),
    "valid": ("ok", "neutral"),
}


@dataclass(frozen=True)
class NameEntry:
    chips: tuple[str, ...]          # chip ids ( () = no chip)
    polarity: str = "bad_on"
    reason: str = ""                 # why no chip


def _e(*chips: str, polarity: str = "bad_on") -> NameEntry:
    return NameEntry(tuple(chips), polarity)


def _no(reason: str) -> NameEntry:
    return NameEntry((), "info_on", reason)


#: Every generated status name (G-39). Names that appear in two tables (LOAD_LIMIT) have one entry.
NAME_TABLE: Mapping[str, NameEntry] = {
    # DATA flags
    "VALID": _e("VALID", polarity="valid"),
    "MOVING": _e("MOV", polarity="info_on"),
    "HOMED": _e("HOMED", polarity="good_on"),
    "ENABLED": _e("ENA", polarity="good_on"),
    "ESTOP": _e("ESTOP"),
    "HALT": _e("HALT"),
    "FAULT": _e("FAULT"),
    "OVERRUN": _no("counted in the link statistics (LINK tooltip, Connection tab) and plottable as a channel"),
    # DATA status
    "PAUSED": _e("PAUSED", polarity="warn_on"),
    "LIMIT_START": _e("LIM_S"),
    "LIMIT_END": _e("LIM_E"),
    "LOAD_LIMIT": _e("LOAD"),
    "AFE_STALE": _e("AFE"),
    "AFE_SATURATED": _e("AFE"),
    "AFE_SETTLING": _e("AFE", polarity="warn_on"),
    "AFE_RATE_MISMATCH": _e("AFE", polarity="warn_on"),
    "LINK_WDG": _e("WDG"),
    "STOP_BTN": _no("retired by D-36: no physical holding STOP button (the red button is the E-stop); "
                    "removed from the ICD by CR-01"),
    "PAUSE_BTN": _e("BTN", polarity="warn_on"),
    "ALM": _e("ALM"),
    "PEND": _e("PEND", polarity="good_on_warn"),
    "POS_UNCERTAIN": _e("HOMED", polarity="warn_on"),
    "NO_AFE_DATA": _e("AFE"),
    "DRV_PWR": _e("DRV", polarity="good_on"),
    # FAULTS
    "AFE_FAULT": _e("FAULT"),
    "STEP_FAULT": _e("FAULT"),
    "LIMIT_WIRING": _e("LIM_S", "LIM_E"),
    "HOME_NOT_FOUND": _e("FAULT"),
    "HOME_WIRING": _e("FAULT"),
    "K1_WELDED": _e("K1"),
    "HOME_DRIFT": _e("FAULT"),
    # sys_flags (GET_STATUS / EVENT only)
    "CLK_FALLBACK": _e("CLK", polarity="warn_on"),
    "CFG_DIRTY": _e("CFG", polarity="warn_on"),
    "STREAM_ON": _no("shown by the toolbar Stream button (checked = stream on)"),
    "REBOOT_PENDING": _e("CFG", polarity="warn_on"),
    "NVM_DEFAULTED": _e("CFG", polarity="warn_on"),
}

#: Names that may stay in the table after the Integrator removed them from the generated tables (CR-01).
RETIRED_NAMES: frozenset[str] = frozenset({"STOP_BTN"})

GENERATED_TABLES: tuple[tuple[str, tuple[str, ...], Mapping[str, str]], ...] = (
    ("DATA_FLAGS", pg.DATA_FLAGS_BITS, pg.DATA_FLAGS_DESC),
    ("DATA_STATUS", pg.DATA_STATUS_BITS, pg.DATA_STATUS_DESC),
    ("FAULTS", pg.FAULTS_BITS, pg.FAULTS_DESC),
    ("SYS_FLAGS", pg.SYS_FLAGS_BITS, pg.SYS_FLAGS_DESC),
)


def generated_names() -> tuple[str, ...]:
    """All generated status names (table order, unique, reserved '' skipped)."""
    seen: dict[str, None] = {}
    for _t, names, _d in GENERATED_TABLES:
        for n in names:
            if n:
                seen.setdefault(n, None)
    return tuple(seen)


def describe(name: str) -> str:
    """``*_DESC`` text of a generated name (first table that defines it)."""
    for _t, _names, desc in GENERATED_TABLES:
        if name in desc:
            return desc[name]
    return ""


#: SW-only items (``core.model.SW_INDICATOR_KEYS``) → chip that shows them (G-39 SW part)
SW_ITEMS: Mapping[str, str] = {
    "link_state": "LINK", "sw_trip": "LOAD", "thresholds_state": "THR", "no_specimen_mode": "NOSPEC",
    "travel_cal_differs": "TCAL", "afe_synthetic": "AFE", "recording_failed": "REC", "hotkey": "KEY",
}

# --------------------------------------------------------------------------------------------- chips

#: Chip id → label, in display order; ``None`` separates the two rows.
CHIP_ORDER: tuple[tuple[str, str] | None, ...] = (
    ("LINK", "LINK"), ("ESTOP", "ESTOP"), ("HALT", "HALT"), ("PAUSED", "PAUSED"), ("LIM_S", "LIM S"),
    ("LIM_E", "LIM E"), ("LOAD", "LOAD"), ("FAULT", "FAULT"), ("DRV", "DRV"), ("K1", "K1"), ("AFE", "AFE"),
    ("WDG", "WDG"), ("NOSPEC", "NOSPEC"),
    None,
    ("HOMED", "HOMED"), ("ENA", "ENA"), ("MOV", "MOV"), ("ALM", "ALM"), ("PEND", "PEND"), ("BTN", "BTN"),
    ("THR", "THR"), ("CAL", "CAL"), ("TCAL", "TCAL"), ("TARE", "TARE"), ("VALID", "VALID"), ("CFG", "CFG"),
    ("CLK", "CLK"), ("REC", "REC"), ("KEY", "KEY"), ("RO", "RO"),
)
CHIP_IDS: tuple[str, ...] = tuple(c[0] for c in CHIP_ORDER if c is not None)
CHIP_LABEL: Mapping[str, str] = {c[0]: c[1] for c in CHIP_ORDER if c is not None}

LEVEL_RANK = {"alarm": 5, "unknown": 4, "warn": 3, "info": 2, "ok": 1, "neutral": 0}

KL01_TEXT = ("KL-01: the Pause/Break key is not delivered while an elevated (Administrator) window has focus. "
             "The on-screen STOP and the red E-stop button are the paths of record.")


@dataclass(frozen=True)
class ChipView:
    key: str
    label: str
    level: str                       # unknown / ok / warn / alarm / info / neutral
    text: str
    tooltip: str
    visible: bool = True


def chip_members(chip: str) -> tuple[str, ...]:
    """Generated names shown by ``chip`` (table order)."""
    return tuple(n for n, e in NAME_TABLE.items() if chip in e.chips)


def _ind(indicators: Any, name: str) -> Any:
    try:
        return indicators[name.lower()]
    except (KeyError, TypeError):
        return None


def _state(item: Any) -> str:
    return getattr(item, "state", "UNKNOWN") if item is not None else "UNKNOWN"


def member_level(name: str, item: Any) -> str:
    st = _state(item)
    if st == "UNKNOWN":
        return "unknown"
    on, off = POLARITY_LEVELS[NAME_TABLE[name].polarity]
    return on if st == "ON" else off


def combine(levels: list[str]) -> str:
    """alarm > unknown > warn > info > ok > neutral (P5: unknown is never hidden behind ok)."""
    return max(levels, key=lambda lv: LEVEL_RANK[lv]) if levels else "neutral"


def member_tooltip(name: str, item: Any) -> str:
    parts = [f"{name}: {describe(name)}", f"  state {_state(item)}"]
    if item is not None:
        if getattr(item, "source", None):
            parts[-1] += f", source {item.source}"
        if getattr(item, "value", None) is not None:
            parts[-1] += f", value {item.value:g}"
        if getattr(item, "since_t_us", None) is not None:
            parts[-1] += f", since t = {item.since_t_us / 1e6:.3f} s"
        if getattr(item, "clear_hint", None):
            parts.append(f"  clear: {item.clear_hint}")
    return "\n".join(parts)


def _generic(chip: str, ind: Any, *, ok_text: str = "ok", on_text: Callable[[list[str]], str] | None = None
             ) -> tuple[str, str, str]:
    names = chip_members(chip)
    items = [(n, _ind(ind, n)) for n in names]
    levels = [member_level(n, it) for n, it in items]
    level = combine(levels)
    on = [n for n, it in items if _state(it) == "ON"]
    if level == "unknown":
        text = "?"
    elif level in ("ok", "neutral") and not on:
        text = ok_text
    else:
        text = on_text(on) if on_text is not None else (" ".join(on) if on else "OFF")
    tip = "\n".join(member_tooltip(n, it) for n, it in items)
    return level, text, tip


def source_text(item: Any) -> str:
    """Display source of a latch; the generated SOURCE name NONE (not latched / not yet known) is not shown."""
    src = getattr(item, "source", None) if item is not None else None
    return "" if not src or src == pg.SOURCE_NAMES[0] else str(src)


def _src(item: Any) -> str:
    s = source_text(item)
    return f" {s}" if s else ""


# --------------------------------------------------------------------------------------------- evaluation

def evaluate_chips(status: Any, params: Mapping[str, Any] | None = None) -> list[ChipView]:
    """All chips for one ``BackendStatus`` (pure; called once per refresh tick). ``params`` = the board values
    (``config.values()``) for chips whose meaning depends on a board parameter (K1: ``drv.k1_check_enable``)."""
    ind = getattr(status, "indicators", None)
    out: list[ChipView] = []
    for chip in CHIP_IDS:
        try:
            if chip == "K1":
                out.append(_eval_k1(chip, status, ind, params))
                continue
            out.append(_EVAL.get(chip, _eval_generic)(chip, status, ind))
        except Exception as exc:  # noqa: BLE001 - a display defect must not hide the other chips
            out.append(ChipView(chip, CHIP_LABEL[chip], "unknown", "?", f"display error: {exc}"))
    return out


def _view(chip: str, level: str, text: str, tip: str, visible: bool = True) -> ChipView:
    return ChipView(chip, CHIP_LABEL[chip], level, text, tip, visible)


def _eval_generic(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind)
    return _view(chip, level, text, tip)


def _eval_link(chip: str, status: Any, ind: Any) -> ChipView:
    link = getattr(status, "link", None)
    state = str(getattr(getattr(link, "state", None), "value", getattr(link, "state", "DISCONNECTED")))
    level = {"CONNECTED": "ok", "CONNECTING": "warn", "DEGRADED": "warn", "LOST": "alarm"}.get(state, "neutral")
    stats = getattr(link, "stats", None)
    tip = [f"link {state}" + (f" ({link.why})" if getattr(link, "why", "") else "")]
    if stats is not None:
        tip.append(f"frames {stats.frames_ok}, lost FW {stats.frames_lost_fw} / link {stats.frames_lost_link}, "
                   f"dup {stats.dup_frames}, seq anomalies {stats.seq_anomalies}, CRC {stats.crc_errors}, "
                   f"timeouts {stats.command_timeouts}")
    tip.append("OVERRUN: " + describe("OVERRUN") + f" — state {_state(_ind(ind, 'OVERRUN'))}")
    return _view(chip, level, state, "\n".join(tip))


def _eval_halt(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="-", on_text=lambda on: "HALT" + _src(_ind(ind, "HALT")))
    return _view(chip, level, text, tip)


def _eval_paused(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="-", on_text=lambda on: "PAUSED" + _src(_ind(ind, "PAUSED")))
    return _view(chip, level, text, tip + "\nmotion blocked — Resume or Clear stop")


def _eval_fault(chip: str, status: Any, ind: Any) -> ChipView:
    def text(on: list[str]) -> str:
        parts = []
        for n in on:
            if n == "FAULT":
                continue
            it = _ind(ind, n)
            if n == "HOME_DRIFT" and getattr(it, "value", None) is not None:
                parts.append(f"HOME_DRIFT {it.value:.0f} µm")
            else:
                parts.append(n)
        return " ".join(parts) or "FAULT"
    level, t, tip = _generic(chip, ind, ok_text="-", on_text=text)
    return _view(chip, level, t, tip)


def _eval_load(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind)
    safety = getattr(status, "safety", None)
    trip = getattr(safety, "sw_trip", None)
    warns = tuple(getattr(safety, "warnings", ()) or ())
    if trip:
        level, text = "alarm", "SW trip"
        tip += f"\nSW limit trip: {trip}"
    elif warns and level in ("ok", "neutral"):
        level, text = "warn", "warning"
        tip += "\n" + "\n".join(warns)
    return _view(chip, level, text, tip)


def _eval_drv(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="on")
    if level == "alarm":
        text = "OFF – position lost"            # optional 48 V presence sense (D-41)
    elif level == "ok":
        text = "on"                             # good state ON: never echo the bit name ("DRV DRV_PWR")
    return _view(chip, level, text, tip)


def _eval_k1(chip: str, status: Any, ind: Any, params: Mapping[str, Any] | None) -> ChipView:
    """K1_WELDED is only checked with ``drv.k1_check_enable`` (and pwr sense, D-43 e; default off since CR-03 /
    D-41: no contactor). While the board reports the check disabled the chip is hidden (M2 gate condition, cosmetic);
    without board values (not connected) it shows the indicator state (UNKNOWN grey)."""
    level, text, tip = _generic(chip, ind)
    check = None if params is None else params.get("drv.k1_check_enable")
    if check is not None and not bool(check) and level != "alarm":
        return _view(chip, "neutral", "check off", tip + "\nK1 weld check disabled (drv.k1_check_enable = false, "
                     "CR-03 / D-41: no power-removal contactor)", visible=False)
    return _view(chip, level, text, tip)


def _eval_afe(chip: str, status: Any, ind: Any) -> ChipView:
    rate = getattr(getattr(status, "stream", None), "rate_sps", None)
    level, text, tip = _generic(chip, ind, ok_text=f"{rate:.1f}" if rate is not None else "ok")
    syn = _ind(ind, "afe_synthetic")
    if _state(syn) == "ON" and level in ("ok", "neutral", "info"):
        level, text = "warn", "synthetic"
    tip += "\nafe_synthetic (SW): M1 placeholder samples (feature AFE_SYNTHETIC)"
    return _view(chip, level, text, tip)


def _eval_nospec(chip: str, status: Any, ind: Any) -> ChipView:
    on = bool(getattr(getattr(status, "safety", None), "no_specimen_mode", False))
    return _view(chip, "warn" if on else "neutral", "NO-SPECIMEN" if on else "off",
                 "No-specimen mode: PC load limits OFF for this session; board load limit and travel limits "
                 "active (SW-LIM-004).", visible=on)


def _eval_homed(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="homed")
    h = _state(_ind(ind, "HOMED"))
    if level == "alarm":
        text = "NOT homed"
    elif h == "ON" and _state(_ind(ind, "POS_UNCERTAIN")) == "ON":
        text = "homed ±1 step"
    elif level == "ok":
        text = "homed"
    return _view(chip, level, text, tip)


def _eval_ena(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="enabled")
    left = int(getattr(getattr(status, "motion", None), "enabling_left_ms", 0) or 0)
    if left > 0 and level != "unknown":
        level, text = "warn", f"ENABLING {left} ms"
    elif level == "alarm":
        text = "disabled"
    elif level == "ok":
        text = "enabled"
    return _view(chip, level, text, tip)


def _eval_mov(chip: str, status: Any, ind: Any) -> ChipView:
    motion = getattr(status, "motion", None)
    phase = getattr(motion, "home_phase", None)
    homing = bool(phase) and phase not in ("NONE", "DONE")

    def moving_text(_on: list[str]) -> str:
        if homing:
            return f"homing {phase}"
        return "jogging" if getattr(motion, "jogging", False) else "moving"
    level, text, tip = _generic(chip, ind, ok_text="standing", on_text=moving_text)
    ms = getattr(motion, "motion_state", None)
    if ms:
        tip += f"\nmotion state {ms}: {pg.MOTION_STATE_DESC.get(ms, '')}"
    return _view(chip, level, text, tip)


def _eval_alm(chip: str, status: Any, ind: Any) -> ChipView:
    alm = _ind(ind, "ALM")
    st = _state(alm)
    tip = member_tooltip("ALM", alm) + "\n" + member_tooltip("DRV_PWR", _ind(ind, "DRV_PWR"))
    tip += "\ncheck the driver; closed loop 4000 p/rev (D-27): ALM also reports a position-following error"
    if st == "UNKNOWN":
        return _view(chip, "unknown", "?", tip)
    if st == "OFF":
        return _view(chip, "ok", "OFF", tip)
    if _state(_ind(ind, "DRV_PWR")) == "OFF":
        return _view(chip, "warn", "ALM – driver unpowered", tip)
    return _view(chip, "alarm", "ALM – new motion blocked", tip)


def _eval_pend(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="in position")
    if level == "warn":
        text = "not in position"
    elif level == "ok":
        text = "in position"
    return _view(chip, level, text, tip)


def _eval_btn(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="released", on_text=lambda on: "PAUSE pressed")
    return _view(chip, level, text, tip + "\nSTOP_BTN: " + NAME_TABLE["STOP_BTN"].reason)


def _eval_thr(chip: str, status: Any, ind: Any) -> ChipView:
    thr = getattr(getattr(status, "safety", None), "thresholds", None)
    st = getattr(thr, "state", "UNVERIFIED")
    level = {"VERIFIED": "ok", "DEFAULT_ONLY": "warn", "FAILED": "alarm", "INVALID": "alarm"}.get(st, "neutral")
    text = st
    if st == "VERIFIED" and getattr(thr, "clamped", False):
        level, text = "warn", "VERIFIED clamped"
    tip = "FW load thresholds (SAF-SW-002): " + st + (f" — {thr.text}" if getattr(thr, "text", "") else "")
    return _view(chip, level, text, tip)


def _eval_cal(chip: str, status: Any, ind: Any) -> ChipView:
    cal = getattr(status, "calibration", None)
    st = getattr(cal, "load_status", None)
    if not st:
        return _view(chip, "neutral", "none", "no active load calibration")
    level = "ok" if st == "PASS" else "warn"
    text = st
    if getattr(cal, "low_span", False):
        level, text = "warn", f"{st} LOW_SPAN"
    if not getattr(cal, "load_valid_for_limits", True):
        level, text = "warn", f"{text} (invalid for limits)"
    return _view(chip, level, text, f"load calibration {st}")


def _eval_tcal(chip: str, status: Any, ind: Any) -> ChipView:
    cal = getattr(status, "calibration", None)
    on = bool(getattr(cal, "travel_cal_differs", False))
    text = "restoring…" if getattr(cal, "restore_pending", False) else "board spm ≠ active"
    return _view(chip, "warn" if on else "neutral", text if on else "off",
                 "board steps/mm differs from the active travel calibration (SW-CAL-001)", visible=on)


def _eval_tare(chip: str, status: Any, ind: Any) -> ChipView:
    tare = getattr(status, "tare", None)
    st = getattr(tare, "state", "NONE")
    age = getattr(tare, "age_s", None)
    if st in (None, "NONE"):
        return _view(chip, "neutral", "none", "no tare (session-only, D-29 j)")
    level = "ok" if age is not None and age < 1800 else "warn"
    text = f"{age / 60:.0f} min" if age is not None else st
    return _view(chip, level, text, f"tare {st}")


def _eval_valid(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="0", on_text=lambda on: "1")
    return _view(chip, level, text, tip)


def _eval_cfg(chip: str, status: Any, ind: Any) -> ChipView:
    level, text, tip = _generic(chip, ind, ok_text="clean", on_text=lambda on: " ".join(
        {"CFG_DIRTY": "dirty", "REBOOT_PENDING": "reboot pending", "NVM_DEFAULTED": "NVM defaulted"}[n]
        for n in on))
    if getattr(status, "config_read_only", False):
        level, text = "alarm", "read-only"
        tip += "\nconfiguration read-only (dictionary hash mismatch, IF-008)"
    return _view(chip, level, text, tip)


def _eval_rec(chip: str, status: Any, ind: Any) -> ChipView:
    rec = getattr(status, "recording", None)
    st = getattr(rec, "state", "IDLE")
    if st == "FAILED" or _state(_ind(ind, "recording_failed")) == "ON":
        return _view(chip, "alarm", "FAILED", f"recording failed: {getattr(rec, 'failure', '')}")
    if st == "RECORDING":
        level = "warn" if getattr(rec, "queue_fill_pct", 0.0) >= 50.0 else "alarm"
        return _view(chip, level, f"{rec.rows} rows", f"recording to {rec.folder}")
    return _view(chip, "neutral", "off", "not recording")


def _eval_key(chip: str, status: Any, ind: Any) -> ChipView:
    hk = getattr(status, "hotkey", None)
    mode = getattr(hk, "mode", "UNAVAILABLE")
    level = {"REGISTERED": "ok", "LL_HOOK": "warn"}.get(mode, "alarm")
    text = mode
    if mode == "UNAVAILABLE" and "not responding" in str(getattr(hk, "reason", "")):
        text = "NOT RESPONDING"                  # B6-33 (5), SWR-09: hotkey thread silent > 750 ms (SW-STOP-002)
    if getattr(hk, "test_running", False):
        level, text = "warn", "test running"
    tip = f"Pause/Break hotkey: {mode} ({getattr(hk, 'reason', '')})"
    if mode == "UNAVAILABLE":
        tip += "\nPause/Break works only while the application is focused (app shortcut)."
    return _view(chip, level, text, tip + "\n" + KL01_TEXT)


def _eval_ro(chip: str, status: Any, ind: Any) -> ChipView:
    link = getattr(status, "link", None)
    ro = bool(getattr(link, "read_only", False))
    return _view(chip, "alarm" if ro else "neutral", "read-only" if ro else "-",
                 "protocol / payload version mismatch: read-only, motion disabled (IF-008)", visible=ro)


_EVAL: Mapping[str, Callable[[str, Any, Any], ChipView]] = {
    "LINK": _eval_link, "HALT": _eval_halt, "PAUSED": _eval_paused, "FAULT": _eval_fault, "LOAD": _eval_load,
    "DRV": _eval_drv, "AFE": _eval_afe, "NOSPEC": _eval_nospec, "HOMED": _eval_homed, "ENA": _eval_ena,
    "MOV": _eval_mov, "ALM": _eval_alm, "PEND": _eval_pend, "BTN": _eval_btn, "THR": _eval_thr, "CAL": _eval_cal,
    "TCAL": _eval_tcal, "TARE": _eval_tare, "VALID": _eval_valid, "CFG": _eval_cfg, "REC": _eval_rec,
    "KEY": _eval_key, "RO": _eval_ro,
}


def chip_by_key(views: list[ChipView]) -> dict[str, ChipView]:
    return {v.key: v for v in views}

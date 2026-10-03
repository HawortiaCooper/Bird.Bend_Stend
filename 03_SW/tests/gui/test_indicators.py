"""G-39 (generated-name completeness), G-44 (UNKNOWN part), indicator-map unit tests for every name, event-log
decoding of every generated EVENT name (SW_design_GUI §2.4, §4.4, P5, P8).

Verifies: SAF-SW-005, SW-RT-002, SW-STOP-002, SW-STOP-004, SW-PLT-002
"""
from __future__ import annotations

import pytest
from fakes import default_channels, ind, refuse

from bend_stand.core import protocol_gen as pg
from bend_stand.core.api import (
    INDICATOR_KEYS, SW_INDICATOR_KEYS, BackendStatus, FwEvent, GateId, HotkeyStatus, Indicators,
)
from bend_stand.gui import gating
from bend_stand.gui import indicator_map as imap
from bend_stand.gui.widgets.channel_tree import bit_name
from bend_stand.gui.widgets.event_log import fw_event_text


def views(st: BackendStatus) -> dict[str, imap.ChipView]:
    return imap.chip_by_key(imap.evaluate_chips(st))


def st_with(**items) -> BackendStatus:
    return BackendStatus(indicators=Indicators().replace(**items))


def all_state(state: str) -> BackendStatus:
    from bend_stand.core.api import Indicator
    return BackendStatus(indicators=Indicators({k: Indicator(state) for k in INDICATOR_KEYS}))


# --------------------------------------------------------------------------------------------- G-39

@pytest.mark.req("SAF-SW-005", "SW-PLT-002")
def test_every_generated_name_has_a_decision() -> None:
    """Verifies: SAF-SW-005 (G-39) — every generated name is a chip member or "no chip" with a reason."""
    gen = set(imap.generated_names())
    assert gen <= set(imap.NAME_TABLE), sorted(gen - set(imap.NAME_TABLE))
    assert set(imap.NAME_TABLE) - gen <= imap.RETIRED_NAMES
    for name, e in imap.NAME_TABLE.items():
        if e.chips:
            assert set(e.chips) <= set(imap.CHIP_IDS), name
            assert e.polarity in imap.POLARITY_LEVELS, name
        else:
            assert e.reason, name


@pytest.mark.req("SAF-SW-005")
def test_generated_names_resolve_as_lowercase_indicator_keys() -> None:
    """Verifies: SAF-SW-005 (B3-18) — every generated name resolves as indicators[name.lower()]."""
    i = Indicators()
    for n in imap.generated_names():
        assert getattr(i, n.lower()).state == "UNKNOWN"
    assert set(SW_INDICATOR_KEYS) == set(imap.SW_ITEMS)
    assert set(imap.SW_ITEMS.values()) <= set(imap.CHIP_IDS)


@pytest.mark.req("SAF-SW-005")
def test_chip_tooltips_carry_generated_descriptions() -> None:
    """Verifies: SAF-SW-005 (P8) — the chip tooltip of every member contains its generated *_DESC text."""
    v = views(BackendStatus())
    for name, e in imap.NAME_TABLE.items():
        for chip in e.chips:
            if name in imap.generated_names():
                assert imap.describe(name) in v[chip].tooltip, (chip, name)


@pytest.mark.req("SW-STOP-004")
def test_clear_first_codes_are_block_names() -> None:
    """Verifies: SW-STOP-004 — "Clear stop first" codes ⊆ generated BLOCK_BITS."""
    assert gating.CLEAR_FIRST_CODES <= set(pg.BLOCK_BITS)


@pytest.mark.req("SW-RT-002")
def test_registry_bit_channels_equal_generated_names() -> None:
    """Verifies: SW-RT-002 (G-39) — status-bit channels map onto the generated bit names, in generated order."""
    bits = [bit_name(s) for s in default_channels() if bit_name(s)]
    assert bits == [n for n in (*pg.DATA_FLAGS_BITS, *pg.DATA_STATUS_BITS) if n]


@pytest.mark.req("SAF-SW-005")
def test_event_log_decodes_every_generated_event() -> None:
    """Verifies: SAF-SW-005 (G-39) — every EVENT_NAMES entry is decoded with its description; unknown → EVENT n."""
    for code, name in enumerate(pg.EVENT_NAMES):
        ev = FwEvent(1, 0, code, name, 0, None, 0, 0)
        c, text = fw_event_text(ev)
        assert c == name and pg.EVENT_DESC[name] in text
    c, text = fw_event_text(FwEvent(1, 0, 200, "", 0, None, 0, 0))
    assert c == "EVENT 200"


# --------------------------------------------------------------------------------------------- G-44 UNKNOWN / P5

@pytest.mark.req("SAF-SW-005")
def test_before_first_frame_nothing_is_green() -> None:
    """Verifies: SAF-SW-005 (G-44, P5) — default status: every chip with a generated member is grey "?"."""
    v = views(BackendStatus())
    for chip in imap.CHIP_IDS:
        assert v[chip].level != "ok", chip
        if imap.chip_members(chip) and chip not in ("ALM",):
            assert v[chip].level == "unknown" and v[chip].text == "?", chip
    assert v["ALM"].level == "unknown"


@pytest.mark.req("SAF-SW-005")
@pytest.mark.parametrize("name", [n for n in imap.NAME_TABLE if imap.NAME_TABLE[n].chips])
def test_every_name_on_off_unknown(name) -> None:
    """Verifies: SAF-SW-005 — per generated name: UNKNOWN → its chips grey "?" (never green); ON/OFF → the
    polarity level, all other members OFF-good."""
    e = imap.NAME_TABLE[name]
    base = all_state("OFF")
    good = {}
    for n, ent in imap.NAME_TABLE.items():                 # every other member in its "good" state
        if ent.chips and ent.polarity in ("good_on", "good_on_warn", "valid"):
            good[n.lower()] = ind("ON")
    base = BackendStatus(indicators=base.indicators.replace(**good))
    unk = BackendStatus(indicators=base.indicators.replace(**{name.lower(): ind("UNKNOWN")}))
    for chip in e.chips:
        assert views(unk)[chip].level in ("unknown", "alarm"), (name, chip)
        assert views(unk)[chip].level != "ok"
    on_level, off_level = imap.POLARITY_LEVELS[e.polarity]
    for state, want in (("ON", on_level), ("OFF", off_level)):
        st = BackendStatus(indicators=base.indicators.replace(**{name.lower(): ind(state)}))
        for chip in e.chips:
            got = views(st)[chip].level
            if name == "ALM" and state == "ON":
                want = "alarm"                         # with driver power present
            assert got == want or (want in ("ok", "neutral") and got in ("ok", "neutral")), (name, state, chip, got)


@pytest.mark.req("SAF-SW-005")
def test_alarm_outranks_unknown_and_unknown_outranks_ok() -> None:
    """Verifies: SAF-SW-005 (P5) — alarm > unknown > warn > ok."""
    assert imap.combine(["ok", "unknown"]) == "unknown"
    assert imap.combine(["alarm", "unknown"]) == "alarm"
    assert imap.combine(["warn", "ok"]) == "warn"
    assert imap.combine(["neutral", "ok"]) == "ok"


@pytest.mark.req("SAF-SW-005")
def test_special_chip_texts() -> None:
    """Verifies: SAF-SW-005 — HALT/PAUSED with source, FAULT with HOME_DRIFT µm, ALM "new motion blocked" vs
    "driver unpowered", DRV off = position lost, K1 welded red."""
    v = views(st_with(halt=ind("ON", "KEY"), paused=ind("ON", "BUTTON"), fault=ind("ON"),
                      home_drift=ind("ON", value=350.0), alm=ind("ON"), drv_pwr=ind("ON"), k1_welded=ind("ON")))
    assert v["HALT"].text == "HALT KEY" and v["HALT"].level == "alarm"
    assert v["PAUSED"].text == "PAUSED BUTTON" and v["PAUSED"].level == "warn"
    assert "HOME_DRIFT 350 µm" in v["FAULT"].text and v["FAULT"].level == "alarm"
    assert v["ALM"].text == "ALM – new motion blocked" and v["ALM"].level == "alarm"
    assert v["K1"].level == "alarm"
    assert views(st_with(halt=ind("ON", "NONE")))["HALT"].text == "HALT"       # unknown source not shown
    v = views(st_with(alm=ind("ON"), drv_pwr=ind("OFF")))
    assert v["ALM"].level == "warn" and "unpowered" in v["ALM"].text
    assert v["DRV"].level == "alarm" and "position lost" in v["DRV"].text


@pytest.mark.req("SW-STOP-002")
def test_key_chip_modes_and_kl01() -> None:
    """Verifies: SW-STOP-002 — KEY chip REGISTERED green, LL_HOOK amber, UNAVAILABLE red; KL-01 always stated."""
    for mode, level in (("REGISTERED", "ok"), ("LL_HOOK", "warn"), ("UNAVAILABLE", "alarm")):
        v = views(BackendStatus(hotkey=HotkeyStatus(mode, "r")))["KEY"]
        assert v.level == level and "KL-01" in v.tooltip


@pytest.mark.req("SAF-SW-005")
def test_hidden_chips_only_when_active() -> None:
    """Verifies: SAF-SW-005 — NOSPEC / TCAL / RO are shown only while their condition holds."""
    import dataclasses

    from bend_stand.core.api import SafetyStatus
    v = views(BackendStatus())
    assert not v["NOSPEC"].visible and not v["TCAL"].visible and not v["RO"].visible
    st = BackendStatus(safety=SafetyStatus(no_specimen_mode=True))
    assert views(st)["NOSPEC"].visible and views(st)["NOSPEC"].level == "warn"
    st = dataclasses.replace(st, config_read_only=True)
    assert views(st)["CFG"].text == "read-only"


@pytest.mark.req("SAF-SW-005")
def test_indicator_bar_widget(qtbot) -> None:
    """Verifies: SAF-SW-005 — the bar paints the chip views (grey "?" before data, colours after)."""
    from bend_stand.gui.widgets.indicator_bar import IndicatorBar
    bar = IndicatorBar()
    qtbot.addWidget(bar)
    bar.update_status(BackendStatus())
    assert bar.chip("ESTOP").value.text() == "?" and bar.chip("ESTOP").led.color == "off"
    bar.update_status(st_with(estop=ind("ON", hint="release the E-stop, RESET K1, Clear stop")))
    assert bar.chip("ESTOP").led.color == "red"
    assert "release the E-stop" in bar.chip("ESTOP").toolTip()
    clicked = []
    bar.chipClicked.connect(clicked.append)
    qtbot.mouseClick(bar.chip("ESTOP"), __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.MouseButton.LeftButton)
    assert clicked == ["ESTOP"]


@pytest.mark.req("SAF-SW-005")
def test_status_help_lists_clear_hints_and_no_chip_reasons() -> None:
    """Verifies: SAF-SW-005 — the help text contains each latch's clear procedure and the no-chip reasons."""
    from bend_stand.gui.dialogs.status_help import help_text
    t = help_text(st_with(halt=ind("ON", "PC", hint="Clear stop (HALT_CLEAR)")))
    assert "Clear stop (HALT_CLEAR)" in t and "OVERRUN" in t and "D-36" in t


@pytest.mark.req("SW-STOP-004")
def test_gating_display_mapping() -> None:
    """Verifies: SW-STOP-004 — REFUSE disables with texts, WARN keeps enabled, missing motion gate disabled,
    HALT code → clear-first item."""
    g = refuse("HALT", "HALT latched", "Clear stop")
    cs = gating.control_state(g, motion=True)
    assert not cs.enabled and "HALT latched — Clear stop" in cs.tooltip and cs.clear_first[0].code == "HALT"
    assert not gating.control_state(None, motion=True).enabled
    assert gating.control_state(None, motion=False).enabled
    from fakes import warn
    cs = gating.control_state(warn("X", "careful"), motion=True)
    assert cs.enabled and cs.warning == "careful"
    assert GateId.PAUSE in gating.MOTION_GATES and GateId.STREAM_START not in gating.MOTION_GATES

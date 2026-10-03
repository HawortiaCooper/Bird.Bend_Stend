"""``core.api`` contract surface (SW_design §15): generated names, gate codes, immutable indicator set.

Verifies: SW-PLT-002, SAF-SW-005 (indicator keys / UNKNOWN), SW-RT-002
"""
from __future__ import annotations

import dataclasses

import pytest

from bend_stand.core import api
from bend_stand.core import protocol_gen as pg


@pytest.mark.req("SW-PLT-002")
def test_all_exports_resolve() -> None:
    for name in api.__all__:
        assert hasattr(api, name), name
    for proto in api.PROTOCOLS:
        assert getattr(proto, "_is_runtime_protocol", False), proto


@pytest.mark.req("SAF-SW-005")
def test_indicator_keys_are_generated_names_lowercased() -> None:
    gen = {n.lower() for t in (pg.DATA_FLAGS_BITS, pg.DATA_STATUS_BITS, pg.FAULTS_BITS, pg.SYS_FLAGS_BITS)
           for n in t if n}
    assert gen <= set(api.INDICATOR_KEYS)
    assert set(api.INDICATOR_KEYS) - gen == set(api.SW_INDICATOR_KEYS)
    ind = api.Indicators()
    for k in api.INDICATOR_KEYS:
        assert getattr(ind, k).state == "UNKNOWN"          # never OK before data (GRQ-B-02)
        assert ind[k] is getattr(ind, k)


@pytest.mark.req("SAF-SW-005")
def test_indicators_immutable_and_replace() -> None:
    ind = api.Indicators()
    ind2 = ind.replace(paused=api.Indicator("ON", 5, "PC"))
    assert ind.paused.state == "UNKNOWN" and ind2.paused.state == "ON" and ind2.paused.source == "PC"
    with pytest.raises(AttributeError):
        ind.paused = api.Indicator("OFF")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        _ = ind.no_such_item


@pytest.mark.req("SW-PLT-002")
def test_gate_codes_do_not_collide_with_generated_names() -> None:
    generated = set(pg.BLOCK_BITS) | set(pg.DATA_STATUS_BITS) | set(pg.DATA_FLAGS_BITS) | set(pg.FAULTS_BITS)
    assert not ({c.value for c in api.GateCode} & generated)


@pytest.mark.req("SW-PLT-002")
def test_gate_result_semantics() -> None:
    S = api.Severity
    r = api.GateResult((api.GateItem("PAUSED", S.REFUSE, "paused"), api.GateItem("X", S.WARN, "w")))
    assert not r.ok and r.refused[0].code == "PAUSED" and r.warnings[0].code == "X"
    c = api.GateResult((api.GateItem("C", S.CONFIRM, "sure?"),))
    assert c.ok and c.needs_confirmation
    assert api.GATE_OK.ok and not api.GATE_OK.needs_confirmation
    st = api.BackendStatus()
    assert set(st.gates) == set(api.GateId)


@pytest.mark.req("SW-PLT-002")
def test_status_types_are_frozen() -> None:
    st = api.BackendStatus()
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.cfg_dirty = True  # type: ignore[misc]
    assert st.link.state is api.LinkState.DISCONNECTED


@pytest.mark.req("IF-008")
def test_compat_flags() -> None:
    C = api.Compat
    assert not C.OK.read_only and not C.OK.config_read_only
    assert (C.MAJOR_MISMATCH | C.MINOR_DIFF).read_only
    assert C.PARAM_HASH_MISMATCH.config_read_only and not C.PARAM_HASH_MISMATCH.read_only


@pytest.mark.req("SW-RT-002")
def test_bit_names_and_device_features() -> None:
    assert api.bit_names(pg.BLOCK_BITS, int(pg.Block.PAUSED | pg.Block.HALT)) == ("HALT", "PAUSED")
    assert api.bit_names(pg.BLOCK_BITS, 1 << 15) == ("BIT15",)
    info = api.DeviceInfo(1, 0, 1, (0, 1, 0), 0, "00" * 12, "b", 48,
                          int(pg.Features.AFE | pg.Features.NVM))
    assert info.features == frozenset({"AFE", "NVM"})


@pytest.mark.req("SW-RT-002")
def test_bit_channel_prefix_and_stop_confirmation_exported() -> None:
    """GRQ-B-20: the status-bit channel key prefix is part of the API; SWD-M1-06 payload type."""
    assert api.BIT_PREFIX == "bit." and api.bit_key("PAUSED") == "bit.paused"
    c = api.StopConfirmation("HALT", "gui", 2, 5, False)
    assert c.cmd == "HALT" and not c.confirmed

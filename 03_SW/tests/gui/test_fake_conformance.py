"""The GUI-test FakeBackend conforms to B's ``core.api`` Protocols (SW_design_GUI §10.1), so the GUI tests cannot
drift from the real API: runtime ``isinstance`` per Protocol and identical parameter names / kinds / defaults of
every public method.

Verifies: SW-PLT-002
"""
from __future__ import annotations

import inspect

import pytest

from bend_stand.core import api

ATTR = {
    api.BackendAPI: "", api.ConfigAPI: "config", api.MotionAPI: "motion", api.LimitsAPI: "limits",
    api.DataViewAPI: "data", api.ChannelRegistryAPI: "channels", api.MarksAPI: "marks", api.SessionAPI: "session",
    api.EngineAPI: "travel_cal", api.TareEngineAPI: "tare_engine", api.LoadCalEngineAPI: "load_cal",
    api.CalibrationStoreAPI: "calibrations", api.SequencerAPI: "sequencer", api.ReportAPI: "reports",
    api.EventBusAPI: "events",
}
OPTIONAL = {api.SimControlAPI, api.TestHooksAPI}       # None in the fake (present only with the real simulator)


def _target(fake, proto):
    attr = ATTR[proto]
    return fake if not attr else getattr(fake, attr)


def _methods(proto) -> list[str]:
    return [n for n, v in inspect.getmembers(proto, inspect.isfunction) if not n.startswith("_")]


@pytest.mark.req("SW-PLT-002")
def test_every_protocol_covered() -> None:
    """Verifies: SW-PLT-002 — every Protocol of core.api is either faked or optional."""
    assert set(api.PROTOCOLS) == set(ATTR) | OPTIONAL


@pytest.mark.req("SW-PLT-002")
@pytest.mark.parametrize("proto", list(ATTR), ids=lambda p: p.__name__)
def test_fake_satisfies_protocol(proto, fake) -> None:
    """Verifies: SW-PLT-002 — isinstance + same signatures as B's Protocol."""
    obj = _target(fake, proto)
    assert isinstance(obj, proto)
    for name in _methods(proto):
        want = inspect.signature(getattr(proto, name))
        got = inspect.signature(getattr(obj, name))
        wp = [(p.name, p.kind, p.default) for p in want.parameters.values() if p.name != "self"]
        gp = [(p.name, p.kind, p.default) for p in got.parameters.values()]
        assert gp == wp, f"{proto.__name__}.{name}: {gp} != {wp}"


@pytest.mark.req("SW-PLT-002")
def test_fake_optional_members_are_none(fake) -> None:
    """Verifies: SW-PLT-002 — ``sim`` / ``test_hooks`` absent like a real Backend without simulator / hooks."""
    assert fake.sim is None and fake.test_hooks is None

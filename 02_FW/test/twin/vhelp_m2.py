"""Validator E - M2 helpers for the twin suites (motion, homing, inputs, latches).

Pre-written for M2 (FW_test_plan v0.3 §2.1): every M2 test asks `need(v, ...)` first, which SKIPS with
reason "M2 pending" while GET_INFO reports the feature bit 0 (Implementer A's M2 code not landed). When
the bits are 1 the tests run; a skip is then never accepted as evidence (report rule §5.2).

Observation sources (tools/README vocabulary v2): `query edges` (PUL/DIR/ENA, world µs with ns
resolution - each edge is rounded to 1 ns, so periods are reconstructed per interval as
round(Δt_ns · f_tick / 1e9) ticks, never from accumulated time), `query seam_log`, `query world`,
`query pulses`, EVENT/DATA frames decoded with ref_codec.

Verifies: (harness for) FW-MOT-*, FW-HOM-*, SAF-FW-* M2 twin suites
"""
from __future__ import annotations

import pytest

import ref_codec as rc
from vhelp import PBYKEY, V

F_TICK = 90_000_000
M2_PENDING = "M2 pending: GET_INFO feature bit {} = 0 (Implementer A's M2 code not landed)"


# ------------------------------------------------------------------------------ gating
def features(v: V) -> set[str]:
    return set(v.info()["features"])


def need(v: V, *feats: str) -> None:
    have = features(v)
    for f in feats:
        if f not in have:
            pytest.skip(M2_PENDING.format(f))


# ------------------------------------------------------------------------------ waits
KEEPALIVE_MS = 200.0     # PC heartbeat while waiting (SAF-FW-015 link watchdog trips after 1 s of silence)


def wait_until(v: V, pred, timeout_ms: float, step_ms: float = 1.0, keepalive: bool = True) -> bool:
    """Advance virtual time until pred() is true. With `keepalive` a PING frame is sent every 200 ms like
    the SW heartbeat (SAF-SW-003), so long waits do not trip the link watchdog unintentionally."""
    t, last_ping = 0.0, 0.0
    while t <= timeout_ms:
        if pred():
            return True
        if keepalive and t - last_ping >= KEEPALIVE_MS:
            v.link.send("PING")
            last_ping = t
        v.advance(step_ms)
        t += step_ms
    return pred()


def run(v: V, ms: float, step_ms: float = 1.0) -> None:
    """Let virtual time pass for `ms` with the PC heartbeat (no link-watchdog trip while moving)."""
    wait_until(v, lambda: False, ms, step_ms)


def events_since(v: V, n0: int, code: str | None = None) -> list[dict]:
    v.link.poll()
    ev = v.link.events()[n0:]
    return [e for e in ev if code is None or e["code"] == code]


def n_events(v: V) -> int:
    v.link.poll()
    return len(v.link.events())


def wait_event(v: V, code: str, n0: int, timeout_ms: float, step_ms: float = 2.0) -> dict:
    ok = wait_until(v, lambda: bool(events_since(v, n0, code)), timeout_ms, step_ms)
    assert ok, f"EVENT {code} not received within {timeout_ms} ms (virtual); got {[e['code'] for e in events_since(v, n0)]}"
    return events_since(v, n0, code)[0]


def st(v: V) -> dict:
    return v.status()


def state(v: V) -> str:
    return st(v)["motion_state"]


# ------------------------------------------------------------------------------ set-up
def set_ok(v: V, key: str, value) -> None:
    r = v.set(key, value)
    assert r["status"] == "OK", (key, value, r)


def enable(v: V) -> None:
    r = v.ok("ENABLE")
    v.advance(r["settle_ms"] + 5)
    assert state(v) == "IDLE", st(v)


def home(v: V, flags: int = 0, timeout_ms: float = 120_000) -> dict:
    n0 = n_events(v)
    v.ok("HOME", {"flags": flags})
    md = wait_event(v, "MOVE_DONE", n0, timeout_ms, step_ms=10.0)
    return md


def ready(v: V, x_um: int | None = 100_000, v_um_s: int = 10_000) -> None:
    """Precondition R (plan §1.4): enabled + settled, homed, at x (None = stay at 0)."""
    enable(v)
    md = home(v)
    assert md["arg"] == rc.MOVE_DONE_REASON.index("TARGET"), md
    if x_um is not None:
        move_abs(v, x_um, v_um_s)


def move_abs(v: V, target_um: int, v_um_s: int, a_um_s2: int = 0, wait: bool = True,
             timeout_ms: float = 600_000) -> dict | None:
    n0 = n_events(v)
    v.ok("MOVE_ABS", {"target_um": target_um, "v_um_s": v_um_s, "a_um_s2": a_um_s2})
    if not wait:
        return None
    return wait_event(v, "MOVE_DONE", n0, timeout_ms, step_ms=5.0)


def jog(v: V, v_um_s: int, a_um_s2: int = 0, bound_um: int = rc.JOG_NO_BOUND) -> dict:
    return v.cmd("JOG", {"v_um_s": v_um_s, "a_um_s2": a_um_s2, "bound_um": bound_um})


# ------------------------------------------------------------------------------ world / logs
def world(v: V) -> dict:
    return v.tw.act("query", what="world")


def edges(v: V, pin: str, since_us: float = 0.0) -> list[dict]:
    return [e for e in v.tw.act("query", what="edges", since_us=since_us)["edges"] if e["pin"] == pin]


def seam(v: V, call: str | None = None, since_us: float = 0.0) -> list[dict]:
    log = v.tw.act("query", what="seam_log", since_us=since_us)["seam_log"]
    return [s for s in log if call is None or s["call"].startswith(call)]


def ticks(dt_us: float) -> int:
    return round(dt_us * F_TICK / 1e6)


def periods_from_edges(pul: list[dict], start_us: float) -> list[int]:
    """Step periods (ticks) from PUL falling edges (= timer update, PWM mode 2) after a start at start_us."""
    fall = [e["t_us"] for e in pul if e["level"] == 0 and e["t_us"] >= start_us]
    out, prev = [], start_us
    for t in fall:
        out.append(ticks(t - prev))
        prev = t
    return out


def pulse_widths(pul: list[dict]) -> tuple[list[int], list[int]]:
    """(high widths, low widths) in ticks from consecutive PUL edges."""
    hi, lo = [], []
    for a, b in zip(pul, pul[1:]):
        (hi if a["level"] == 1 else lo).append(ticks(b["t_us"] - a["t_us"]))
    return hi, lo


def rising_count(v: V, since_us: float = 0.0) -> int:
    return sum(1 for e in edges(v, "PUL", since_us) if e["level"] == 1)


def param(key: str):
    return PBYKEY[key]

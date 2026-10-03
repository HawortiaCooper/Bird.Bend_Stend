"""Cross-parameter hard rules H1–H5 and the SET order that keeps them true (ICD §11.4). Pure; used by
``core.params`` (check / write plan) and by the simulator's command acceptance.

Implements: SW-CFG-003, IF-010, FW-CFG-003 (simulator side: E_CONFIG detail = partner id)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bend_stand.core import params_gen as pgen

#: enum code of ``afe.rate_sps`` → conversions per second (H5)
SPS_OF_CODE: Mapping[int, int] = {0: 10, 1: 80}

K_SOFT_MIN, K_SOFT_MAX = "limits.soft_min_um", "limits.soft_max_um"
K_RAW_MIN, K_RAW_MAX = "safety.load_raw_min", "safety.load_raw_max"
K_RATE, K_HIGH, K_LOW = "motion.max_step_rate_hz", "motion.pulse_high_ns", "motion.pulse_low_min_ns"
K_VLOAD, K_VTRAVEL = "motion.v_max_load_um_s", "motion.v_max_travel_um_s"
K_TIMEOUT, K_SPS = "afe.timeout_ms", "afe.rate_sps"

#: key → (rule, partner key named in the E_CONFIG detail) (ICD §11.4 column "Detail for a SET of …")
PARTNER: Mapping[str, tuple[str, str]] = {
    K_SOFT_MIN: ("H1", K_SOFT_MAX), K_SOFT_MAX: ("H1", K_SOFT_MIN),
    K_RAW_MIN: ("H2", K_RAW_MAX), K_RAW_MAX: ("H2", K_RAW_MIN),
    K_RATE: ("H3", K_HIGH), K_HIGH: ("H3", K_RATE), K_LOW: ("H3", K_RATE),
    K_VLOAD: ("H4", K_VTRAVEL), K_VTRAVEL: ("H4", K_VLOAD),
    K_TIMEOUT: ("H5", K_SPS), K_SPS: ("H5", K_TIMEOUT),
}
RULE_KEYS: Mapping[str, tuple[str, ...]] = {
    "H1": (K_SOFT_MIN, K_SOFT_MAX), "H2": (K_RAW_MIN, K_RAW_MAX), "H3": (K_RATE, K_HIGH, K_LOW),
    "H4": (K_VLOAD, K_VTRAVEL), "H5": (K_TIMEOUT, K_SPS),
}
RULE_TEXT: Mapping[str, str] = {
    "H1": "limits.soft_min_um < limits.soft_max_um",
    "H2": "safety.load_raw_min < safety.load_raw_max",
    "H3": "motion.max_step_rate_hz · (pulse_high_ns + pulse_low_min_ns) ≤ 1e9",
    "H4": "motion.v_max_load_um_s ≤ motion.v_max_travel_um_s",
    "H5": "afe.timeout_ms ≥ 2 × the conversion period of afe.rate_sps",
}


@dataclass(frozen=True)
class RuleViolation:
    rule: str            # "H1" … "H5"
    key: str             # the parameter being judged (the SET key, or the first key of the rule)
    other_key: str       # partner named in the E_CONFIG detail

    @property
    def other_id(self) -> int:
        return pgen.BY_KEY[self.other_key].id

    @property
    def text(self) -> str:
        return f"conflicts with {self.other_key} (rule {self.rule}: {RULE_TEXT[self.rule]})"


def _v(values: Mapping[str, Any], key: str) -> Any:
    if key in values:
        return values[key]
    return pgen.BY_KEY[key].default


def rule_holds(rule: str, values: Mapping[str, Any]) -> bool:
    def g(k: str) -> int:
        return int(_v(values, k))

    if rule == "H1":
        return g(K_SOFT_MIN) < g(K_SOFT_MAX)
    if rule == "H2":
        return g(K_RAW_MIN) < g(K_RAW_MAX)
    if rule == "H3":
        return g(K_RATE) * (g(K_HIGH) + g(K_LOW)) <= 1_000_000_000
    if rule == "H4":
        return g(K_VLOAD) <= g(K_VTRAVEL)
    if rule == "H5":
        sps = SPS_OF_CODE.get(g(K_SPS))
        return sps is not None and g(K_TIMEOUT) * sps >= 2000
    raise KeyError(rule)


def check_hard_rules(values: Mapping[str, Any]) -> list[RuleViolation]:
    """Every violated rule for a complete value set (missing keys = dictionary defaults)."""
    out = []
    for rule, keys in RULE_KEYS.items():
        if not rule_holds(rule, values):
            out.append(RuleViolation(rule, keys[0], PARTNER[keys[0]][1]))
    return out


def set_violation(values: Mapping[str, Any], key: str, value: Any) -> RuleViolation | None:
    """Rule violated by SET ``key = value`` against the current ``values`` (ICD §11.4) or ``None``."""
    if key not in PARTNER:
        return None
    rule, partner = PARTNER[key]
    trial = dict(values)
    trial[key] = value
    return None if rule_holds(rule, trial) else RuleViolation(rule, key, partner)


def write_order(current: Mapping[str, Any], target: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Order of single SETs from ``current`` to ``target`` keeping H1–H5 true after each SET where possible
    (ICD §11.4: outward bound first; H3 lower the rate before widening pulses; H5 raise the timeout before
    SPS10 …). The rules are monotone, so a greedy choice of the first key (dictionary order) whose SET keeps
    its rule true reaches every valid target. Keys that cannot be placed come last in dictionary order (the
    writer retries ``E_CONFIG`` items in further passes). Only keys whose value differs are returned."""
    order_idx = {p.key: i for i, p in enumerate(pgen.PARAMS)}
    state: dict[str, Any] = {k: _v(current, k) for k in pgen.BY_KEY}
    todo = sorted((k for k in target if k not in current or current[k] != target[k]),
                  key=lambda k: order_idx.get(k, 1 << 30))
    out: list[tuple[str, Any]] = []
    while todo:
        pick = next((k for k in todo if set_violation(state, k, target[k]) is None), None)
        if pick is None:
            out += [(k, target[k]) for k in todo]
            break
        state[pick] = target[pick]
        out.append((pick, target[pick]))
        todo.remove(pick)
    return out

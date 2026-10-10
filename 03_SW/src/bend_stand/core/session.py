"""Session settings file ``*.bbsession.json`` (``bird.bend.session`` v1, SW_design §13.5, SRS §5.2).

Contains the SW limits (travel / pull / push with enable + warning level, FW load-limit level — SW-LIM-003), pull
direction, k_est, windows (tare, calibration pre-settle / capture, take-sample), display unit, g_local, expected
steps/mm, travel-calibration speed, manual speed / acceleration, recordings root, raw dump, 3-point-bend geometry and
machine compliance. **Never** a tare or the no-specimen mode (session-only, D-29 h/j).

``validate(settings)`` returns ERROR issues (pure); ``to_dict`` / ``from_dict`` (unknown keys reported as WARN,
missing keys take the defaults); ``load`` / ``save`` (atomic).

Implements: SW-LIM-003, SW-TARE-002 (window setting), SW-CAL-005 (pre-settle / capture setting)
"""
from __future__ import annotations

import math
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

from bend_stand.calc.limits import check_limit_config
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import Bend3pGeometry, Issue, IssueSeverity, LimitConfig, SessionSettings
from bend_stand.core.schema import atomic_write_json, read_json, unknown_keys

KIND = "session"
VERSION = 1
E, W = IssueSeverity.ERROR, IssueSeverity.WARN


def _range(out: list[Issue], key: str, v: float | None, lo: float, hi: float, unit: str = "") -> None:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        out.append(Issue(key, E, "RANGE", f"{key} = {v!r} outside {lo:g}…{hi:g} {unit}".rstrip()))


def _finite_pair(a: object, b: object) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in (a, b))


def validate_limits(cfg: LimitConfig) -> list[Issue]:
    """SW-LIM-002 (load side) as Issues + the travel values that need no board (finite, enabled → present, min <
    max); the soft-limit containment needs the board's parameters (``LimitsAPI.check``)."""
    out = [Issue(f, E, c, t) for f, c, t in check_limit_config(
        pull_trip_n=cfg.pull_trip_n, pull_enabled=cfg.pull_enabled, push_trip_n=cfg.push_trip_n,
        push_enabled=cfg.push_enabled, warn_pct=cfg.warn_pct, fw_level_n=cfg.fw_level_n)]
    for name, en, v in (("travel_min_mm", cfg.travel_min_enabled, cfg.travel_min_mm),
                        ("travel_max_mm", cfg.travel_max_enabled, cfg.travel_max_mm)):
        if en and v is None:
            out.append(Issue(name, E, "MISSING", f"{name}: enabled without a value"))
    if cfg.travel_min_enabled and cfg.travel_max_enabled and _finite_pair(cfg.travel_min_mm, cfg.travel_max_mm) \
            and cfg.travel_min_mm >= cfg.travel_max_mm:                          # type: ignore[operator]
        out.append(Issue("travel_min_mm", E, "ORDER", "travel min must be < travel max"))
    return out


def restore_load_limits(s: SessionSettings) -> tuple[SessionSettings, list[Issue]]:
    """D-53 a (SW-LIM-003 v0.6.6, SWR-03): a session with both PC load limits off is restored with both on + a
    warning (switching both off is allowed only through the no-specimen mode, which is never stored)."""
    # Implements: SW-LIM-003 (D-53 a)
    lim = s.limits
    if lim.pull_enabled or lim.push_enabled:
        return s, []
    return replace(s, limits=replace(lim, pull_enabled=True, push_enabled=True)), [Issue(
        "limits", W, "LOAD_LIMITS_RESTORED", "the session file had both PC load limits off: restored ON (D-53 a) — "
                                             "use the no-specimen mode to move without a specimen")]


def _typed(where: str, ann: str, default: Any, v: Any) -> Any:
    """SWR-06: strict type of a session value from the dataclass default / annotation → ``FileFormatError``."""
    if v is None:
        if "None" in str(ann):
            return None
        raise FileFormatError(f"{where}: null not allowed")
    if isinstance(default, bool) or str(ann) == "bool":
        if not isinstance(v, bool):
            raise FileFormatError(f"{where}: true / false expected, got {v!r}")
        return v
    if isinstance(default, int) and "float" not in str(ann):
        if isinstance(v, bool) or not isinstance(v, int):
            raise FileFormatError(f"{where}: integer expected, got {v!r}")
        return v
    if isinstance(default, float) or (default is None and "float" in str(ann)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise FileFormatError(f"{where}: number expected, got {v!r}")
        return float(v)
    if isinstance(default, str) or (default is None and "str" in str(ann)):
        if not isinstance(v, str):
            raise FileFormatError(f"{where}: text expected, got {v!r}")
        return v
    return v


def validate(s: SessionSettings) -> list[Issue]:
    out: list[Issue] = []
    if s.display_unit not in ("N", "kgf"):
        out.append(Issue("display_unit", E, "ENUM", "display unit must be N or kgf"))
    if s.pull_dir not in (1, -1):
        out.append(Issue("pull_dir", E, "ENUM", "pull direction must be +1 or −1"))
    _range(out, "expected_spm", s.expected_spm, 100.0, 10_000.0, "steps/mm")
    _range(out, "sample_window_s", s.sample_window_s, 0.1, 10.0, "s")
    _range(out, "tare_window_s", s.tare_window_s, 2.0, 60.0, "s")
    _range(out, "cal_presettle_s", s.cal_presettle_s, 0.0, 60.0, "s")
    _range(out, "cal_capture_s", s.cal_capture_s, 1.0, 60.0, "s")
    _range(out, "cal_v_mm_s", s.cal_v_mm_s, 0.01, 30.0, "mm/s")
    _range(out, "manual_speed_mm_s", s.manual_speed_mm_s, 0.001, 30.0, "mm/s")
    _range(out, "g_local", s.g_local, 9.7, 9.9, "m/s²")
    _range(out, "compliance_mm_per_n", s.compliance_mm_per_n, 0.0, 1.0, "mm/N")
    if s.manual_accel_mm_s2 is not None:
        _range(out, "manual_accel_mm_s2", s.manual_accel_mm_s2, 0.001, 10_000.0, "mm/s²")
    if s.k_est_n_mm is not None:
        _range(out, "k_est_n_mm", s.k_est_n_mm, 1e-3, 1e6, "N/mm")
    _range(out, "k_min_n_mm", s.k_min_n_mm, 1e-3, 1e6, "N/mm")
    _range(out, "k_max_n_mm", s.k_max_n_mm, 1e-3, 1e7, "N/mm")
    if _finite_pair(s.k_min_n_mm, s.k_max_n_mm) and s.k_min_n_mm >= s.k_max_n_mm:
        out.append(Issue("k_min_n_mm", E, "ORDER", "k_min must be < k_max"))
    _range(out, "trim_kp", s.trim_kp, 0.01, 1.99, "")
    _range(out, "trim_max_step_mm", s.trim_max_step_mm, 0.001, 10.0, "mm")
    _range(out, "trim_v_mm_s", s.trim_v_mm_s, 0.001, 20.0, "mm/s")
    if not isinstance(s.trim_max_iter, int) or isinstance(s.trim_max_iter, bool) or not 1 <= s.trim_max_iter <= 100:
        out.append(Issue("trim_max_iter", E, "RANGE", "trim_max_iter must be 1…100"))
    if s.seq_travel_ref not in ("test", "machine"):
        out.append(Issue("seq_travel_ref", E, "ENUM", "travel reference must be test or machine"))
    g = s.bend3p
    if g is not None:
        for k in ("span_mm", "width_mm", "thickness_mm"):
            _range(out, f"bend3p.{k}", getattr(g, k), 1e-3, 1e4, "mm")
    out += validate_limits(s.limits)
    return out


def to_dict(s: SessionSettings) -> dict[str, Any]:
    d = asdict(s)
    return {"schema": f"bird.bend.{KIND}", "schema_version": VERSION, **d}


def from_dict(d: dict[str, Any]) -> tuple[SessionSettings, list[Issue]]:
    issues = [Issue(k, W, "UNKNOWN_KEY", f"unknown key {k!r} ignored") for k in
              unknown_keys(d, [f.name for f in fields(SessionSettings)] + ["file"])]
    base = SessionSettings()
    kw: dict[str, Any] = {}
    for f in fields(SessionSettings):
        if f.name not in d:
            continue
        v = d[f.name]
        if f.name == "limits":
            lim = LimitConfig()
            if not isinstance(v, dict):
                raise FileFormatError("limits must be an object")
            known = {x.name for x in fields(LimitConfig)}
            issues += [Issue(f"limits.{k}", W, "UNKNOWN_KEY", f"unknown key limits.{k!r} ignored")
                       for k in v if k not in known]
            lf = {x.name: x for x in fields(LimitConfig)}
            v = replace(lim, **{k: _typed(f"limits.{k}", lf[k].type, getattr(lim, k), x) for k, x in v.items()
                               if k in known})
        elif f.name == "bend3p" and v is not None:
            if not isinstance(v, dict):
                raise FileFormatError("bend3p must be an object or null")
            try:
                v = Bend3pGeometry(float(v["span_mm"]), float(v["width_mm"]), float(v["thickness_mm"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise FileFormatError(f"bend3p: {exc}") from exc
        elif f.name != "bend3p":
            v = _typed(f.name, f.type, getattr(base, f.name), v)
        kw[f.name] = v
    try:
        s = replace(base, **kw)
    except TypeError as exc:  # pragma: no cover - defensive
        raise FileFormatError(str(exc)) from exc
    return s, issues


def load(path: str | Path) -> tuple[SessionSettings, list[Issue]]:
    """Read and validate a session file (``FileFormatError`` on a broken / newer / invalid file)."""
    d = read_json(path, KIND, VERSION)
    s, issues = from_dict(d)
    s, restored = restore_load_limits(s)
    issues = issues + restored
    errors = validate(s)
    if errors:
        raise FileFormatError(f"{path}: " + "; ".join(i.text for i in errors))
    return s, issues


def save(path: str | Path, s: SessionSettings) -> None:
    atomic_write_json(path, to_dict(s))


__all__ = ["validate", "validate_limits", "to_dict", "from_dict", "load", "save"]

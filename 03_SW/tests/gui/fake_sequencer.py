"""Fake sequencer + report API for the GUI tests (SW_design_GUI §10.1; B §15.5f B6-01…B6-14).

The *pure* part is B's real code (``core.sequencer.model`` validation, ``plan.expand`` / ``planned_path``,
``generators``, ``seqfile``), so the editor, generator wizard and chart are tested against the real model, plan and
schemas. The *execution* part is scripted: ``check_start`` / ``start`` results, ``set_status(**fields)`` for the
run line, ``emit_result(...)`` for ``seq.step_result``. Every call is recorded on the owner (``sequencer.<name>``).
"""
from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from concurrent.futures import Future
from typing import Any

from bend_stand.core import api
from bend_stand.core import model as cm
from bend_stand.core.api import GATE_OK, GateItem, GateResult, Severity, StopResult
from bend_stand.core.sequencer import generators as G
from bend_stand.core.sequencer import model as M
from bend_stand.core.sequencer import plan as P
from bend_stand.core.sequencer import seqfile as F

SeqStatus = cm.SeqStatus
StepResult = getattr(cm, "StepResult", None)
RecordingInfo = getattr(cm, "RecordingInfo", None)
ReportResult = getattr(cm, "ReportResult", None)
ReportPaths = getattr(cm, "ReportPaths", None)

_ABORT_HAS_REASON = "reason" in inspect.signature(api.SequencerAPI.abort).parameters


def _done(v: Any) -> Future:
    f: Future = Future()
    f.set_result(v)
    return f


def _failed(exc: BaseException) -> Future:
    f: Future = Future()
    f.set_exception(exc)
    return f


class FakeSequencer:
    def __init__(self, owner: Any = None) -> None:
        self._o = owner
        self._status = SeqStatus()
        self.check_result: GateResult | None = None       # None = static sequence_start gate of the owner
        self.start_result: GateResult | None = None       # None = start succeeds (state RUNNING)
        self.extra_issues: list[Any] = []
        self.run_results: list[Any] = []
        self.loaded_warnings: list[str] = []

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        if self._o is not None:
            self._o._rec(f"sequencer.{name}", *args, **kwargs)

    # ------------------------------------------------------------------ model / files (B's pure code)
    def new(self) -> Any:
        self._rec("new")
        return M.Sequence(name="")

    def load(self, path: str) -> Any:
        self._rec("load", path)
        seq, self.loaded_warnings = F.load(path)
        return seq

    def save(self, seq: Any, path: str) -> None:
        self._rec("save", path)
        F.save(path, seq)

    def validate(self, seq: Any) -> list[Any]:
        return list(M.validate(seq)) + list(self.extra_issues)

    def expand(self, seq: Any) -> Any:
        return P.expand(seq)

    def planned_path(self, seq_or_plan: Any) -> Any:
        return P.planned_path(seq_or_plan if isinstance(seq_or_plan, P.Plan) else P.expand(seq_or_plan))

    def generator_schemas(self) -> Mapping[str, Any]:
        return G.generator_schemas()

    def generate(self, name: str, params: Mapping[str, Any] | None = None, **kw: Any) -> Any:
        self._rec("generate", name, dict(params or {}), **kw)
        return G.generate(name, params, **kw)

    # ------------------------------------------------------------------ execution (scripted)
    def check_start(self, seq: Any) -> GateResult:
        self._rec("check_start")
        if self.check_result is not None:
            return self.check_result
        st = self._o.status() if self._o is not None else None
        g = st.gates.get(api.GateId.SEQUENCE_START) if st is not None else None
        return g if g is not None else GATE_OK

    def start(self, seq: Any, *, confirmed: bool = False) -> GateResult:
        self._rec("start", seq, confirmed=confirmed)
        g = self.start_result if self.start_result is not None else self.check_start(seq)
        if g.refused:
            return g
        if g.confirm_items and not confirmed:
            return GateResult(g.items + (GateItem("CONFIRMATION_REQUIRED", Severity.REFUSE, "confirm: "
                                                  + g.confirm_items[0].text),))
        plan = P.expand(seq)
        self.set_status(state="RUNNING", exec_idx=0, step_idx=0, step_uid=plan.steps[0].uid if plan.steps else None,
                        plan_len=None if plan.infinite else len(plan), phase="COMMAND", end_reason=None,
                        plan_total_s=plan.total_s, remaining_s=plan.total_s, windows_total=plan.windows_total,
                        windows_done=0, travel_ref=seq.travel_ref, sequence_name=seq.name)
        return g

    def pause(self) -> StopResult:
        self._rec("pause")
        return self._o.pause("sequence")

    def resume(self) -> GateResult:
        self._rec("resume")
        return self._o.resume("sequence")

    def stop(self) -> StopResult:
        self._rec("stop")
        self.set_status(state="STOPPED", end_reason="OPERATOR_STOP")
        return StopResult("STOP", "sequence", True, 1)

    if _ABORT_HAS_REASON:
        def abort(self, reason: str = "operator") -> StopResult:
            self._rec("abort", reason)
            self.set_status(state="ABORTED", end_reason="OPERATOR_ABORT")
            return StopResult("HALT", "sequence", True, 1)
    else:
        def abort(self) -> StopResult:
            self._rec("abort", "operator")
            self.set_status(state="ABORTED", end_reason="OPERATOR_ABORT")
            return StopResult("HALT", "sequence", True, 1)

    def continue_(self) -> None:
        self._rec("continue_")

    def status(self) -> Any:
        return self._status

    def results(self) -> tuple[Any, ...]:
        return tuple(self.run_results)

    # ------------------------------------------------------------------ scripting
    def set_status(self, **fields: Any) -> Any:
        self._status = dataclasses.replace(self._status, **fields)
        if self._o is not None:
            self._o.events.emit("seq.status", self._status)
        return self._status

    def emit_result(self, **fields: Any) -> Any:
        base = dict(exec_idx=0, step_idx=0, uid="u", label="", kind="load")
        base.update(fields)
        r = StepResult(**base) if StepResult is not None else type("R", (), base)()
        self.run_results.append(r)
        if self._o is not None:
            self._o.events.emit("seq.step_result", r)
        return r


class FakeReports:
    """``list_recordings`` / ``load_result`` / ``build_async`` scripted from ``recordings`` / ``results``."""

    def __init__(self, owner: Any = None) -> None:
        self._o = owner
        self.recordings: list[Any] = []
        self.results: dict[str, Any] = {}
        self.build_error: BaseException | None = None
        self.pending: Future | None = None

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        if self._o is not None:
            self._o._rec(f"reports.{name}", *args, **kwargs)

    def build_async(self, rec_dir: str, cal: Any = None, tare: Any = None, bend3p: Any = None) -> Future:
        self._rec("build_async", rec_dir, cal=cal, tare=tare, bend3p=bend3p)
        if self.build_error is not None:
            return _failed(self.build_error)
        if self.pending is not None:
            return self.pending
        paths = ReportPaths(rec_dir, rec_dir + "/report.json", rec_dir + "/report.html") \
            if ReportPaths is not None else {"html": rec_dir + "/report.html"}
        if self._o is not None:
            self._o.events.emit("report.ready", paths)
        return _done(paths)

    def list_recordings(self, root: str | None = None) -> list[Any]:
        self._rec("list_recordings", root)
        return list(self.recordings)

    def load_result(self, rec_dir: str) -> Any:
        self._rec("load_result", rec_dir)
        if rec_dir not in self.results:
            raise api.FileFormatError(f"no report.json in {rec_dir}")
        return self.results[rec_dir]

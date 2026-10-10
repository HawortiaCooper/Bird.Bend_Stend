"""D-50 a / SRS v0.6.5 SW-CAL-007 in the load-calibration wizard (B's note, forwarded by the Orchestrator):

* **weight not detected** — after a weight point the engine returns to AWAIT_OPERATOR with the same step_index,
  ``can_repeat`` and the error text; shown like the other point rejections (red ✗), [Repeat] → ``repeat()``,
  Continue with another mass → ``continue_({"mass_kg": m})``; it can also follow the last point / finish_early;
* **K implausible** — FIT ``needs_confirmation.code == "K_IMPLAUSIBLE"`` → own confirmation C-14 (not the WARN
  linearity C-06) with the engine's text and an assertion; confirm → ``continue_(confirmed=True)``; a cancelled
  dialog is re-opened by Continue (never a plain ``continue_()``); retake / cancel unchanged;
* ``nominal unknown: K plausibility not checked`` is shown as information "(i)", not as a warning.

Verifies: SW-CAL-007, SAF-SW-004
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from bend_stand.core.api import GATE_OK, ConfirmRequest, InputSpec

from test_calibration_tare import load  # noqa: F401  (fixture re-used)

WEIGHT_ERR = ("weight not detected: raw change 12 counts from the zero point < 440 counts (10 × std_zero, min 50) "
              "— hang the weight and re-take the point")
K_TEXT = ("K implausible: |K| = 3.0e-04 N/count is 9.9 × the nominal 3.04e-05 N/count of the configured cell / AFE "
          "(expected 0.5…2 ×) — check the weights, the cell and the AFE gain. Accept anyway?")
MASS = InputSpec("mass_kg", "Mass", "kg", 0.001, 1000.0, 1.0)


def calls(fake, name):
    return fake.calls_of(name)


def _started(wizard, eng, qtbot) -> None:
    eng.start_result = GATE_OK
    qtbot.mouseClick(wizard.start_button, Qt.MouseButton.LeftButton)
    assert wizard.started


@pytest.mark.req("SW-CAL-007")
def test_weight_not_detected_shown_as_point_rejection(load, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-007 — the error is shown verbatim in red on the point page; Repeat → repeat(); Continue
    with a corrected mass → continue_({"mass_kg": m}); same after finish_early (re-check before the fit)."""
    eng = connected_fake.load_cal
    _started(load, eng, qtbot)
    eng.set_state(phase="AWAIT_OPERATOR", step_index=1, title="Load calibration — point 1 of 2", inputs=(MASS,),
                  continue_label="Capture point 1 ▶", can_continue=True, can_repeat=True, errors=(WEIGHT_ERR,))
    load.refresh()
    txt = load.messages.text()
    assert "✗ weight not detected" in txt and "#a00000" in txt and load.messages.isVisible()
    assert load.repeat_button.isEnabled() and load.continue_button.isEnabled()
    qtbot.mouseClick(load.repeat_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.repeat")
    load._inputs["mass_kg"].setValue(1.5)                                     # noqa: SLF001 - generated editor
    qtbot.mouseClick(load.continue_button, Qt.MouseButton.LeftButton)
    c = calls(connected_fake, "load_cal.continue_")[-1]
    assert c.args == ({"mass_kg": 1.5},) and c.kwargs == {"confirmed": False}
    # after finish_early the engine re-checks the points and may come back with the same rejection
    eng.set_state(step_index=2, errors=(WEIGHT_ERR.replace("from the zero point", "from the previous point"),))
    qtbot.mouseClick(load.finish_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.finish_early")
    load.refresh()
    assert "from the previous point" in load.messages.text() and load.repeat_button.isEnabled()


@pytest.mark.req("SW-CAL-007", "SAF-SW-004")
def test_k_implausible_needs_its_own_confirmation(load, connected_fake, qtbot) -> None:
    """Verifies: SW-CAL-007, SAF-SW-004 — FIT with K_IMPLAUSIBLE opens C-14 with the engine text (assertion
    required, Enter does not confirm); a cancel leaves the page, Continue re-opens C-14 instead of a plain
    continue_(); confirm → continue_(confirmed=True); re-take still works; "nominal unknown" is info."""
    eng = connected_fake.load_cal
    _started(load, eng, qtbot)
    pts = [{"mass_kg": m, "f_ref_n": m * 9.80665, "raw_mean": m * 3221.0 + 5.0} for m in (0.0, 1.0, 10.0)]
    eng.set_state(phase="FIT", step_index=2, step_count=3, inputs=(), errors=(),
                  result={"K": 3.0e-4, "B": 0.0, "status": "PASS", "points": pts},
                  warnings=("LOW_SPAN: largest reference force 98.1 N",), can_continue=True,
                  continue_label="Accept calibration ▶",
                  needs_confirmation=ConfirmRequest("K_IMPLAUSIBLE", K_TEXT))
    load.refresh()
    dlg = load.confirm_dialog
    assert dlg is not None and dlg.cid == "C-14" and "K implausible" in dlg.text_label.text()
    assert dlg.assertion_box is not None and not dlg.confirm_button.isEnabled()
    qtbot.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.outcome == "pending"
    dlg.reject()                                                              # operator cancels the question
    n = len(calls(connected_fake, "load_cal.continue_"))
    qtbot.mouseClick(load.continue_button, Qt.MouseButton.LeftButton)        # Continue → the question again
    assert len(calls(connected_fake, "load_cal.continue_")) == n
    dlg2 = load.confirm_dialog
    assert dlg2 is not dlg and dlg2.cid == "C-14" and dlg2.outcome == "pending"
    dlg2.assertion_box.setChecked(True)
    qtbot.mouseClick(dlg2.confirm_button, Qt.MouseButton.LeftButton)
    assert calls(connected_fake, "load_cal.continue_")[-1].kwargs == {"confirmed": True}
    load.retake_button.menu().actions()[1].trigger()
    assert calls(connected_fake, "load_cal.retake")[-1].args == (1,)
    # a WARN fit keeps C-06; the info warning is not shown as "⚠"
    eng.set_state(needs_confirmation=ConfirmRequest("WARN_FIT", "NL_span 0.3 % (WARN): accept?"),
                  warnings=("nominal unknown: K plausibility not checked",))
    load.refresh()
    assert load.confirm_dialog.cid == "C-06"
    load.confirm_dialog.reject()
    txt = load.messages.text()
    assert "(i) nominal unknown" in txt and "⚠ nominal unknown" not in txt
    # a plain continue_() leaves this error; it is shown verbatim
    eng.set_state(needs_confirmation=None, errors=("K implausible: confirm to accept",), warnings=())
    load.refresh()
    assert "✗ K implausible: confirm to accept" in load.messages.text()

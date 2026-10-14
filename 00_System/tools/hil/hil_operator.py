"""HIL operator interaction: prompts for manual steps (instrument readings, jumper changes, button presses, PSU
switching, flashing by the PO) with every answer recorded.

Owner: Validator E (00_System/tools/hil). Three implementations with one interface:
  ConsoleOperator  the real session: prints the instruction, waits for the operator's answer (input())
  TwinOperator     the twin dry run: a physical action that the twin world models is EMULATED through the
                   world-control vocabulary (estop, limit, wire, alm, drv_power, reset, specimen); an observation
                   the world can answer (caliper / dial = true world position, direction) is TWIN-OBSERVED;
                   anything else (DMM volts, photos, inspection) gets a scripted DRY-RUN DEFAULT that is flagged
                   and never counts as evidence (the check becomes MANUAL).
Every interaction is appended to `log` with its source: operator | twin-emulated | twin-observed | dry-run default.
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

OPERATOR, EMULATED, OBSERVED, DEFAULT, NOT_EMULATED = (
    "operator", "twin-emulated", "twin-observed", "dry-run default (not evidence)", "dry-run: manual step not emulated")


class OperatorAbort(RuntimeError):
    """The operator typed 'abort' (bench procedure §6.8 P-7 or any doubt)."""


@dataclass
class Answer:
    value: Any
    source: str

    @property
    def evidence(self) -> bool:
        return self.source in (OPERATOR, OBSERVED)


class Operator:
    mode = "base"

    def __init__(self):
        self.log: list[dict] = []
        self.item = ""
        self.journal: Path | None = None        # D-57 c: <out>/prompts.jsonl, one line per answer, written at once

    def _rec(self, key: str, kind: str, prompt: str, value: Any, source: str) -> Answer:
        r = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "item": self.item, "key": key, "kind": kind,
             "prompt": prompt, "value": value, "source": source}
        self.log.append(r)
        if self.journal is not None:
            line = dict(r, utc=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"))
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        return Answer(value, source)

    def take_log(self, item: str) -> list[dict]:
        return [r for r in self.log if r["item"] == item]


class ConsoleOperator(Operator):
    mode = "console"

    def __init__(self, input_fn: Callable[[str], str] = input, print_fn: Callable[..., None] = print,
                 pictures: dict[str, list[Path]] | None = None, plain: dict[str, str] | None = None):
        super().__init__()
        self._in, self._out = input_fn, print_fn
        self.pictures = pictures or {}          # D-57 a: {"ITEM:KEY" | "*:KEY" | "ITEM:*": [pictures]} drawn by A
        self.plain = plain or {}                # GR-2: {"ITEM:KEY" | "*:KEY": plain-language text}

    def _pic(self, key: str) -> None:
        import gate_res  # noqa: PLC0415
        for p in gate_res.pictures_for(self.pictures, self.item, key):
            self._out(f"    picture: {p}")

    def _say(self, key: str, text: str, prefix: str = "") -> None:
        """GR-2: the plain-language text first (if any), the exact technical instruction below it."""
        import gate_res  # noqa: PLC0415
        plain = gate_res.plain_for(self.plain, self.item, key)
        if plain:
            self._out(f"\n{prefix}[{self.item} {key}] {plain}")
            self._out(f"    details: {text}")
        else:
            self._out(f"\n{prefix}[{self.item} {key}] {text}")

    def _ask(self, prompt: str) -> str:
        s = self._in(prompt).strip()
        if s.lower() == "abort":
            raise OperatorAbort(prompt)
        return s

    def instruct(self, key: str, text: str, twin: Callable | None = None) -> Answer:
        self._say(key, text)
        self._pic(key)
        self._ask("    -> press Enter when done (or type 'abort'): ")
        return self._rec(key, "instruct", text, "done", OPERATOR)

    def cue(self, key: str, text: str, twin: Callable | None = None, delay_ms: tuple[float, float] = (200, 800)) -> Answer:
        self._say(key, text, prefix="*** NOW ")
        self._pic(key)
        return self._rec(key, "cue", text, "cued", OPERATOR)

    def ask_number(self, key: str, text: str, unit: str, twin: Callable | None = None,
                   nominal: float | None = None) -> Answer:
        self._say(key, text)
        self._pic(key)
        while True:
            s = self._ask(f"    -> type the value in {unit} (or 'abort'): ")
            try:
                return self._rec(key, "number", f"{text} [{unit}]", float(s.replace(",", ".")), OPERATOR)
            except ValueError:
                self._out("    not a number — again (or 'abort')")

    def ask_yes_no(self, key: str, text: str, twin: Callable | None = None, nominal: bool = True) -> Answer:
        self._say(key, text)
        self._pic(key)
        while True:
            s = self._ask("    -> type y (yes) or n (no), or 'abort': ").lower()
            if s in ("y", "yes", "n", "no"):
                return self._rec(key, "yes_no", text, s.startswith("y"), OPERATOR)

    def ask_text(self, key: str, text: str, nominal: str = "") -> Answer:
        self._say(key, text)
        self._pic(key)
        return self._rec(key, "text", text, self._ask("    -> type your answer (or 'abort'): "), OPERATOR)

    def confirm_port(self, port: str) -> bool:
        s = self._in(f"D-06: type the COM port named by the PO to confirm ({port}): ").strip()
        ok = s.upper() == port.upper()
        self._rec("D-06", "confirm_port", port, s, OPERATOR)
        return ok


class TwinOperator(Operator):
    mode = "twin"

    def __init__(self, link, seed: int = 1):
        super().__init__()
        self.L = link
        self.rng = random.Random(seed)

    @property
    def tw(self):
        return self.L.twin

    def instruct(self, key: str, text: str, twin: Callable | None = None) -> Answer:
        if twin is None:
            return self._rec(key, "instruct", text, "skipped", NOT_EMULATED)
        r = twin(self.tw)
        return self._rec(key, "instruct", text, r if isinstance(r, (int, float, str, bool)) else "done", EMULATED)

    def cue(self, key: str, text: str, twin: Callable | None = None, delay_ms: tuple[float, float] = (200, 800)) -> Answer:
        if twin is None:
            return self._rec(key, "cue", text, "skipped", NOT_EMULATED)
        d = self.rng.uniform(*delay_ms)
        tw = self.tw
        tw.at(tw.now + int(d * 1e6), lambda: twin(tw), f"cue {key}")
        return self._rec(key, "cue", text, f"scheduled +{d:.1f} ms", EMULATED)

    def ask_number(self, key: str, text: str, unit: str, twin: Callable | None = None,
                   nominal: float | None = None) -> Answer:
        if twin is not None:
            return self._rec(key, "number", f"{text} [{unit}]", float(twin(self.tw)), OBSERVED)
        return self._rec(key, "number", f"{text} [{unit}]", nominal, DEFAULT)

    def ask_yes_no(self, key: str, text: str, twin: Callable | None = None, nominal: bool = True) -> Answer:
        if twin is not None:
            return self._rec(key, "yes_no", text, bool(twin(self.tw)), OBSERVED)
        return self._rec(key, "yes_no", text, nominal, DEFAULT)

    def ask_text(self, key: str, text: str, nominal: str = "") -> Answer:
        return self._rec(key, "text", text, nominal or "(dry run)", DEFAULT)

    def confirm_port(self, port: str) -> bool:      # never used: twin mode opens no port
        return False

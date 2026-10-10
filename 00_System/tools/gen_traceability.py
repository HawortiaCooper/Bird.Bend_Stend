"""Generate 00_System/specs/TRACEABILITY.md from the SRS and the design/test documents.

Implements: SYS-010 (traceability requirement -> design -> code -> test)

Owner: Orchestrator. Run: .venv\\Scripts\\python 00_System\\tools\\gen_traceability.py [--check]
A requirement counts as "designed" when its ID appears in a design doc, and as "planned"
when a test plan has a TC-<ID>-nn case (or mentions the ID). Code/test tags are added in P2.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRS = ROOT / "00_System/specs/SRS.md"
OUT = ROOT / "00_System/specs/TRACEABILITY.md"
DESIGN = {
    "ICD": ROOT / "00_System/specs/ICD_protocol.md",
    "FW": ROOT / "02_FW/docs/FW_design.md",
    "SW": ROOT / "03_SW/docs/SW_design.md",
    "GUI": ROOT / "03_SW/docs/SW_design_GUI.md",
    "HW": ROOT / "01_HW/wiring.md",
}
TESTS = {
    "FW-TP": ROOT / "02_FW/docs/FW_test_plan.md",
    "SW-TP": ROOT / "03_SW/docs/SW_test_plan.md",
}
CODE_DIRS = [ROOT / "02_FW/src", ROOT / "03_SW/src", ROOT / "00_System/tools"]
TEST_DIRS = [ROOT / "02_FW/test", ROOT / "03_SW/tests", ROOT / "00_System/tools/tests", ROOT / "00_System/tools/hil/tests"]
CODE_EXT = {".c", ".h", ".cpp", ".py"}
SKIP_PARTS = {"gen", ".pio", "__pycache__", "build"}
SKIP_CODE_EXTRA = {"tests", "vectors"}  # tools/tests are tests, not code
# Requirements verified by inspection / hardware only: no code tag (and possibly no test file) expected.
NO_CODE_EXPECTED = {
    "SYS-001": "system composition (01_HW inspection)", "SYS-004": "mechanical envelope (HW demo)",
    "SYS-005": "drive-train setup sheet (inspection)", "SYS-006": "hardwired E-stop circuit (HW)",
    "SYS-007": "01_HW documentation (inspection)", "SYS-011": "buffer board (HW)",
    "FW-HOM-003": "home repeatability on the stand (HW)", "SAF-FW-022": "withdrawn (CR-01)",
}
NO_TEST_EXPECTED = {
    "SYS-001": "inspection", "SYS-005": "inspection", "SYS-011": "HW gate", "NFR-006": "target loop time, HW gate",
    "SAF-FW-022": "withdrawn",
}
TAG_CODE = re.compile(r"Implements:(.*)")
TAG_TEST = re.compile(r"(?:Verifies:(.*)|req\((.*))")


LABEL = re.compile(r"^\s*(?:[*#/]+\s*)?[A-Z][A-Za-z -]{2,30}:\s")


def tag_texts(dirs: list[Path], pat: re.Pattern | None, skip: set[str] = SKIP_PARTS) -> dict[str, str]:
    """Per file: text that cites requirements.

    pat given (code): each `Implements:` line plus its comment continuation lines (up to 8,
    until a blank line or the next `Label:` line). pat None (tests): the whole file, because
    validators cite IDs in `Verifies:` lines, `@pytest.mark.req(...)` and trailing comments.
    """
    out: dict[str, str] = {}
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.suffix not in CODE_EXT or skip & set(f.relative_to(ROOT).parts):
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pat is None:
                hits = [txt]
            else:
                lines = txt.splitlines()
                hits = []
                for i, line in enumerate(lines):
                    m = pat.search(line)
                    if not m:
                        continue
                    block = [m.group(1)]
                    for nxt in lines[i + 1:i + 9]:
                        body = nxt.strip().lstrip("*#/").strip()
                        if not body or LABEL.match(nxt) or pat.search(nxt):
                            break
                        block.append(body)
                    hits.append(" ".join(block))
            if hits:
                out[str(f.relative_to(ROOT)).replace("\\", "/")] = "\n".join(hits)
    return out


ROW = re.compile(r"^\| ((?:SYS|SAF-FW|SAF-SW|FW-[A-Z]+|IF|SW-[A-Z]+|NFR)-\d{3}) \|(.*)$")


def srs_rows() -> list[tuple[str, str, str]]:
    rows = []
    for line in SRS.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # ID | Requirement | Source | Acceptance | V | MS | Pri
            rows.append((m.group(1), cells[5] if len(cells) > 5 else "", cells[6] if len(cells) > 6 else ""))
    return rows


def count_refs(text: str, rid: str) -> int:
    """Exact references plus range forms 'X-001…006', 'X-001...006', 'X-013/014/015'."""
    n = len(re.findall(rf"(?<![A-Z0-9-]){re.escape(rid)}(?!\d)", text))
    prefix, num = rid.rsplit("-", 1)
    k = int(num)
    for m in re.finditer(rf"(?<![A-Z0-9-]){re.escape(prefix)}-(\d{{3}})(?:…|\.\.\.|–)(\d{{3}})", text):
        if int(m.group(1)) <= k <= int(m.group(2)):
            n += 1
    for m in re.finditer(rf"(?<![A-Z0-9-]){re.escape(prefix)}-(\d{{3}}(?:/\d{{3}})+)", text):
        if num in m.group(1).split("/")[1:]:
            n += 1
    return n


def build() -> str:
    rows = srs_rows()
    design = {k: p.read_text(encoding="utf-8") for k, p in DESIGN.items() if p.exists()}
    tests = {k: p.read_text(encoding="utf-8") for k, p in TESTS.items() if p.exists()}
    out = [
        "# Traceability matrix (generated — do not edit)",
        "",
        "Generated by `00_System/tools/gen_traceability.py` from `SRS.md`, the design documents and the test plans.",
        "Columns: number of references to the requirement ID per document; **TC** = number of `TC-<ID>-nn` test cases.",
        "Code files = files whose `Implements:` lines cite the ID; Test files = test files citing the ID (`Verifies:`, `@pytest.mark.req(...)` or test comments) (ranges like X-001…006 and X-013/014 count).",
        "",
    ]
    code_tags = tag_texts(CODE_DIRS, TAG_CODE, SKIP_PARTS | SKIP_CODE_EXTRA)
    test_tags = tag_texts(TEST_DIRS, None)
    no_design, no_tc, no_code, no_test = [], [], [], []
    body = ["| Req | MS | Pri | " + " | ".join(design) + " | TC (FW+SW) | Code files | Test files | Status |",
            "|---|---|---|" + "---|" * len(design) + "---|---|---|---|"]
    for rid, ms, pri in rows:
        drefs = [count_refs(t, rid) for t in design.values()]
        tcs = sum(len(re.findall(rf"TC-{re.escape(rid)}-\d+", t)) for t in tests.values())
        tc_ids = set()
        for t in tests.values():
            tc_ids |= set(re.findall(rf"TC-{re.escape(rid)}-\d+", t))
        if sum(drefs) == 0:
            no_design.append(rid)
        if not tc_ids:
            no_tc.append(rid)
        n_code = sum(1 for t in code_tags.values() if count_refs(t, rid))
        n_test = sum(1 for t in test_tags.values() if count_refs(t, rid))
        if not n_code and rid not in NO_CODE_EXPECTED:
            no_code.append(rid)
        if not n_test and rid not in NO_TEST_EXPECTED:
            no_test.append(rid)
        parts = []
        if not sum(drefs):
            parts.append("NO DESIGN")
        if not tc_ids:
            parts.append("NO TC")
        if not n_code:
            parts.append(f"no code ({NO_CODE_EXPECTED[rid]})" if rid in NO_CODE_EXPECTED else "NO CODE TAG")
        if not n_test:
            parts.append(f"no test file ({NO_TEST_EXPECTED[rid]})" if rid in NO_TEST_EXPECTED else "NO TEST")
        status = "complete" if not parts else ", ".join(parts)
        body.append(f"| {rid} | {ms} | {pri} | " + " | ".join(str(d) for d in drefs)
                    + f" | {len(tc_ids)} | {n_code} | {n_test} | {status} |")
    out += [
        "## Summary",
        "",
        f"- Requirements: **{len(rows)}**",
        f"- Without any design reference: **{len(no_design)}** {', '.join(no_design)}",
        f"- Without a test case: **{len(no_tc)}** {', '.join(no_tc)}",
        f"- Without an `Implements:` tag in code (excluding HW/inspection-only, see status column): **{len(no_code)}** {', '.join(no_code)}",
        f"- Not cited by any test file (excluding HW/inspection-only): **{len(no_test)}** {', '.join(no_test)}",
        f"- Tagged files scanned: code {len(code_tags)}, tests {len(test_tags)}",
        f"- Distinct test-case IDs: FW {len(set(re.findall(r'TC-[A-Z-]+-\d{3}-\d+', tests.get('FW-TP', ''))))}, "
        f"SW {len(set(re.findall(r'TC-[A-Z-]+-\d{3}-\d+', tests.get('SW-TP', ''))))}",
        "",
        "## Matrix",
        "",
    ] + body + [""]
    return "\n".join(out)


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("TRACEABILITY.md up to date" if ok else "TRACEABILITY.md stale")
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(text.split("## Matrix")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

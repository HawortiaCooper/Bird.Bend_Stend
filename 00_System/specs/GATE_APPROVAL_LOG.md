# Gate approval issuance log (Orchestrator)

Record of every gate-runner approval file issued by `00_System/tools/hil/make_approval.py` (D-57, GR-5).
The approval itself is the dedicated `D-06-GATE-YYYYMMDD` row in `DECISIONS.md`; this log only records which
approval files were issued from it, so Validator E can check the copy inside a results zip offline
(`hil_analyze.py --approval-sha`).

Rules:
- One row per issued file; never edit a row — a re-issue gets a new row.
- The first cell is the issue time, never the D-06-GATE reference (the approval-row parser treats a table row whose
  first cell is the reference as an approval record, C-R1).
- A revoked approval is revoked in `DECISIONS.md` (new non-approving row), not here.

| Issued (UTC) | Approval body SHA-256 | D-06-GATE ref | Board UID | Kit commit | Runner hash | Issued for |
|---|---|---|---|---|---|---|

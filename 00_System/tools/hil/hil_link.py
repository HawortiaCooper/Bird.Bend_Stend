"""HIL link layer: D-06 safety interlock, transports (FW twin / serial), PC-side command link, DIAG_MEAS wrapper.

Owner: Validator E (00_System/tools/hil, Integrator reviews). Built on the Integrator's reference codec
(`00_System/tools/ref_codec.py`, ICD v0.7.1) — not the production codec of the SW.

**D-06 interlock (the only way this package opens a serial port):** `open_serial()` refuses unless
  1. an explicit PO approval reference `D-06-GATE-YYYYMMDD[-TAG]` is passed (format, real calendar date, not in
     the future, not older than `max_age_days`), and
  2. that exact reference is recorded by the Orchestrator in `00_System/specs/DECISIONS.md` or
     `00_System/STATUS.md` (a typed-in reference alone is not enough), and
  3. a port name is given explicitly (never auto-detected), and
  4. the operator confirms by typing the port name again (`confirm` callback; console session).
`pyserial` is imported only after all checks passed. Twin mode never imports it.

Verifies: (harness for) SYS-009 HW gate, D-06, D-35 G5 / CR-02 (DIAG_MEAS evidence path, ICD App. C)
"""
from __future__ import annotations

import datetime as _dt
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = TOOLS.parents[1]
for _p in (TOOLS, TOOLS / "fw_twin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import gen_params  # noqa: E402
import ref_codec as rc  # noqa: E402

DICT = gen_params.load()
PBYKEY = {p.key: p for p in DICT.params}

# DIAG_MEAS tables (ICD v0.7.1 Appendix B.18–B.25; tests/test_hil_unit.py proves they match protocol.yaml)
MEAS_OP = {n: i for i, n in enumerate(rc.MEAS_OP)}
MEAS_SRC = {"ESTOP": 0, "LIMIT_START": 1, "LIMIT_END": 2, "PAUSE": 3, "DOUT": 4, "DRV_PWR": 5, "RX": 6, "DIR": 7,
            "STIM": 8}
MEAS_MODE = {"TRIGGER": 0, "RESET": 1, "PWM_INPUT": 2}
MEAS_PF = {"ARMED": 0, "TRIGGERED": 1, "OVERCAPTURE": 2, "WINDOW_OVERFLOW": 3, "EDGE_BEFORE_EVENT": 4}
MEAS_CHAN = {"EVT": 0, "PUL": 1, "DIR": 2, "AUX": 3}
MEAS_HANG = {"MAIN": 0, "TICK": 1, "ISR1": 2}
MEAS_PIN = {"PUL": 0, "DIR": 1}
MEAS_VAR = {"MEAS": 0, "DWT": 1, "TWIN_MODEL": 2}
MEAS_MAGIC = 0x4D454153
STAMPS_PER_PAGE = 14
NOT_IN_BUILD = 1                     # E_INTERNAL detail (ICD §4.4 step 2a)


# ============================================================================================ D-06 interlock
class InterlockError(RuntimeError):
    """Raised when the D-06 conditions for opening a serial port are not met."""


APPROVAL_RE = re.compile(r"^D-06-GATE-(\d{4})(\d{2})(\d{2})(?:-[A-Z0-9]{1,16})?$")
APPROVAL_FILES = ("00_System/specs/DECISIONS.md", "00_System/STATUS.md")


def check_approval(ref: str | None, *, today: _dt.date | None = None, max_age_days: int = 7,
                   repo: Path = REPO) -> _dt.date:
    """Validate a PO approval reference for hardware access (D-06). Returns its date; raises InterlockError."""
    if not ref:
        raise InterlockError("D-06: no PO approval reference given (--approved D-06-GATE-YYYYMMDD) — "
                             "serial port NOT opened")
    m = APPROVAL_RE.match(ref.strip())
    if not m:
        raise InterlockError(f"D-06: approval reference {ref!r} does not match D-06-GATE-YYYYMMDD[-TAG]")
    try:
        d = _dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError as e:
        raise InterlockError(f"D-06: approval reference {ref!r} has no valid date ({e})") from None
    today = today or _dt.date.today()
    if d > today:
        raise InterlockError(f"D-06: approval {ref} is dated in the future ({d} > {today})")
    if (today - d).days > max_age_days:
        raise InterlockError(f"D-06: approval {ref} is older than {max_age_days} days — ask the PO to renew it")
    recorded = False
    for rel in APPROVAL_FILES:
        f = repo / rel
        try:
            if ref.strip() in f.read_text(encoding="utf-8"):
                recorded = True
                break
        except OSError:
            continue
    if not recorded:
        raise InterlockError(f"D-06: approval {ref} is not recorded in {' or '.join(APPROVAL_FILES)} "
                             "(the Orchestrator records the PO's approval there before any session)")
    return d


def open_serial(port: str | None, approval: str | None, *, baud: int = 921600, today: _dt.date | None = None,
                max_age_days: int = 7, confirm: Callable[[str], bool] | None = None,
                opener: Callable[..., Any] | None = None, repo: Path = REPO):
    """The ONLY place that opens a serial port. All D-06 checks first; pyserial imported last."""
    check_approval(approval, today=today, max_age_days=max_age_days, repo=repo)
    if not port or not str(port).strip():
        raise InterlockError("D-06: no port named — the PO names board and port; auto-detection is not allowed")
    if confirm is None:
        raise InterlockError("D-06: operator confirmation of the port is required")
    if not confirm(str(port)):
        raise InterlockError(f"D-06: operator did not confirm port {port!r}")
    if opener is None:
        import serial  # noqa: PLC0415  (lazy: only after every check passed)
        opener = serial.Serial
    return opener(port=str(port), baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=0,
                  write_timeout=1.0, rtscts=False, dsrdtr=False, xonxoff=False)


# ============================================================================================ transports
class TwinTransport:
    """In-process FW twin (lock-step virtual time) with the Integrator's DIAG_MEAS model (`hw_meas=True`)."""

    kind = "twin"

    def __init__(self, run_dir: Path, image: str = "meas", exe: Path | None = None, seed: int = 1):
        import build as twin_build  # noqa: PLC0415
        from twin import Twin  # noqa: PLC0415
        self._Twin = Twin
        # private per-run binary (OBS-M2-09; $BEND_TWIN_BUILD_DIR), never the shared fw_twin.exe of other roles
        self.exe = Path(exe) if exe else twin_build.ensure_built_private("fw")
        if "probe" in self.exe.name:
            raise RuntimeError("HIL dry run needs A's FW core in the twin, not the harness probe")
        self.run_dir = Path(run_dir)
        self.seed = seed
        self.image = image
        self.tw = self._new(fresh=True)

    def _new(self, fresh: bool):
        return self._Twin("lockstep", exe=self.exe, run_dir=self.run_dir, fresh_flash=fresh,
                          hw_meas=self.image in ("meas", "meas_dwt"), seed=self.seed)

    def reflash(self, image: str) -> None:
        """Twin stand-in for 'the PO flashes image X': a new engine (power-on) with/without the HW_MEAS model;
        flash.bin (NVM) is kept like a flash tool that does not touch the parameter sectors. The twin world
        restarts at x = 0 (the launcher has no hook to carry x into a new engine); sessions re-home after a
        re-flash anyway."""
        self.tw.close()
        self.image = image
        self.tw = self._new(fresh=False)

    def write(self, data: bytes) -> None:
        self.tw.feed_rx(data)

    def read(self) -> bytes:
        return self.tw.read_client()

    def now_ns(self) -> int:
        return self.tw.now

    def advance_ms(self, ms: float) -> None:
        self.tw.advance_ms(ms)

    def close(self) -> None:
        self.tw.close()


class SerialTransport:
    """ST-LINK VCP (921 600 Bd 8N1). Constructed only through open_serial() (D-06 interlock)."""

    kind = "target"

    def __init__(self, port: str, approval: str, confirm: Callable[[str], bool], **kw):
        self.port, self.approval, self._confirm, self._kw = port, approval, confirm, kw
        self.ser = open_serial(port, approval, confirm=confirm, **kw)

    def reopen(self) -> None:
        """After the PO re-flashed the board: re-open with the same, already validated approval."""
        self.ser = open_serial(self.port, self.approval, confirm=lambda p: True, **self._kw)

    def close_port(self) -> None:
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def read(self) -> bytes:
        n = self.ser.in_waiting
        return self.ser.read(n) if n else b""

    def now_ns(self) -> int:
        return time.perf_counter_ns()

    def advance_ms(self, ms: float) -> None:
        time.sleep(max(ms, 0.0) / 1000.0)

    def close(self) -> None:
        self.close_port()


# ============================================================================================ link
@dataclass
class RxFrame:
    pc_ns: int
    frame: Any                       # rc.Frame


class LinkError(RuntimeError):
    pass


@dataclass
class Link:
    """PC side of the session (ref_codec). Times: `now_ns()` = PC clock (twin: virtual world time)."""

    tr: Any
    seq: int = 0x20
    parser: rc.FrameParser = field(default_factory=rc.FrameParser)
    frames: list[RxFrame] = field(default_factory=list)
    rtt_log: list[tuple[str, float]] = field(default_factory=list)
    step_ms: float = 0.25
    stop_guard_ms: float = 25.0      # DEF-M3-01 workaround (0 = off): no motion start ≤ 20 ms after a stop frame
    _last_stop_ns: int = -(10**18)
    _ev_seen: int = 0
    _ev_cache: list[dict] = field(default_factory=list)

    @property
    def twin(self):
        return getattr(self.tr, "tw", None)

    @property
    def is_twin(self) -> bool:
        return self.tr.kind == "twin"

    def now_ns(self) -> int:
        return self.tr.now_ns()

    def poll(self) -> list[RxFrame]:
        d = self.tr.read()
        if not d:
            return []
        t = self.now_ns()
        new = [RxFrame(t, f) for f in self.parser.feed(d)]
        self.frames += new
        return new

    def advance(self, ms: float) -> None:
        """Let time pass (twin: virtual; target: wall clock), receiving frames."""
        if self.is_twin:
            self.tr.advance_ms(ms)
            self.poll()
            return
        end = time.perf_counter() + ms / 1000.0
        while True:
            self.poll()
            left = end - time.perf_counter()
            if left <= 0:
                return
            time.sleep(min(left, 0.0005))

    # ------------------------------------------------------------------ commands
    def send(self, name: str, fields: dict | None = None) -> int:
        if name in ("STOP", "HALT", "PAUSE"):
            self._last_stop_ns = self.now_ns()
        elif self.stop_guard_ms and (name in ("MOVE_ABS", "HOME", "MOVE_UNTIL_LOAD") or
                                     (name == "JOG" and (fields or {}).get("v_um_s", 0) != 0)):
            # DEF-M3-01: a stop frame dispatched before the 1 kHz sniffer scanned it leaves a stale sniffed-stop
            # hold; a motion start within SNIFF_HOLD_MAX_MS (20 ms) is then discarded with STOPPED(old cause)
            left = min(self.stop_guard_ms - (self.now_ns() - self._last_stop_ns) / 1e6, self.stop_guard_ms)  # clock may restart (twin re-flash)
            if left > 0:
                self.advance(left)
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFF
        self.tr.write(rc.make_frame(name, seq, fields or {}))
        return seq

    def cmd(self, name: str, fields: dict | None = None, timeout_ms: float = 3000) -> dict:
        t0 = self.now_ns()
        seq = self.send(name, fields)
        want = rc.CMD[name] | rc.RESP_BIT
        k = len(self.frames)
        while (self.now_ns() - t0) / 1e6 <= timeout_ms:
            self.advance(self.step_ms)
            for rf in self.frames[k:]:
                if rf.frame.type == want and rf.frame.seq == seq:
                    d = rc.decode_frame(rf.frame)
                    d["_rtt_ms"] = (rf.pc_ns - t0) / 1e6
                    d["_pc_ns"] = rf.pc_ns
                    self.rtt_log.append((name, d["_rtt_ms"]))
                    return d
            k = len(self.frames)
        raise LinkError(f"{name}: no response within {timeout_ms} ms")

    def ok(self, name: str, fields: dict | None = None, **kw) -> dict:
        r = self.cmd(name, fields, **kw)
        if r["status"] != "OK":
            raise LinkError(f"{name} {fields or ''}: {r['status']} detail {r.get('detail')}")
        return r

    def status(self) -> dict:
        return self.ok("GET_STATUS")["board_status"]

    def info(self) -> dict:
        return self.ok("GET_INFO")["info"]

    def get(self, key: str):
        return self.ok("GET_PARAM", {"id": PBYKEY[key].id})["entry"]["value"]

    def set(self, key: str, value) -> dict:
        p = PBYKEY[key]
        if p.type == "f32":
            value = struct.unpack("<f", struct.pack("<f", float(value)))[0]
        elif p.type == "bool":
            value = 1 if value else 0
        return self.cmd("SET_PARAM", {"id": p.id, "type": p.type, "value": value})

    def set_ok(self, key: str, value) -> None:
        r = self.set(key, value)
        if r["status"] != "OK":
            raise LinkError(f"SET_PARAM {key}={value}: {r['status']} detail {r.get('detail')}")

    # ------------------------------------------------------------------ async frames
    def events(self) -> list[dict]:
        """Decoded EVENTs received so far (incremental)."""
        self.poll()
        fr = self.frames
        for rf in fr[self._ev_seen:]:
            if rf.frame.type == rc.ASYNC["EVENT"]:
                e = rc.decode_event(rf.frame.payload)
                e["_pc_ns"] = rf.pc_ns
                self._ev_cache.append(e)
        self._ev_seen = len(fr)
        return self._ev_cache

    def n_events(self) -> int:
        return len(self.events())

    def events_since(self, n0: int, code: str | None = None) -> list[dict]:
        return [e for e in self.events()[n0:] if code is None or e["code"] == code]

    def data_since(self, k0: int) -> list[dict]:
        out = []
        for rf in self.frames[k0:]:
            if rf.frame.type == rc.ASYNC["DATA"]:
                d = rc.decode_data(rf.frame.payload)
                d["_pc_ns"] = rf.pc_ns
                out.append(d)
        return out

    # ------------------------------------------------------------------ waiting
    def wait(self, ms: float, keepalive: bool = True, ping_ms: float = 200.0, step_ms: float = 1.0) -> None:
        """Let `ms` pass; with keepalive a PING every `ping_ms` (SAF-SW-003 heartbeat, link watchdog 1 s)."""
        t_end = self.now_ns() + int(ms * 1e6)
        last = self.now_ns()
        while self.now_ns() < t_end:
            if keepalive and (self.now_ns() - last) / 1e6 >= ping_ms:
                self.send("PING")
                last = self.now_ns()
            self.advance(min(step_ms, max((t_end - self.now_ns()) / 1e6, 0.001)))

    def wait_event(self, code: str, n0: int, timeout_ms: float, keepalive: bool = True,
                   jog_v: int | None = None, step_ms: float = 1.0) -> dict | None:
        """Wait for EVENT `code` after index n0; while a jog runs pass `jog_v` to refresh its dead-man."""
        t_end = self.now_ns() + int(timeout_ms * 1e6)
        last = self.now_ns()
        while True:
            ev = self.events_since(n0, code)
            if ev:
                return ev[0]
            if self.now_ns() >= t_end:
                return None
            if (self.now_ns() - last) / 1e6 >= (100.0 if jog_v is not None else 200.0):
                if jog_v is not None:
                    self.send("JOG", {"v_um_s": jog_v, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
                elif keepalive:
                    self.send("PING")
                last = self.now_ns()
            self.advance(step_ms)

    def jog_hold(self, v_um_s: int, ms: float, refresh_ms: float = 100.0, step_ms: float = 1.0) -> None:
        """Keep a JOG running for `ms` (refresh < motion.jog_timeout_ms, SAF-FW-016)."""
        t_end = self.now_ns() + int(ms * 1e6)
        last = self.now_ns()
        while self.now_ns() < t_end:
            if (self.now_ns() - last) / 1e6 >= refresh_ms:
                self.send("JOG", {"v_um_s": v_um_s, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
                last = self.now_ns()
            self.advance(min(step_ms, max((t_end - self.now_ns()) / 1e6, 0.001)))

    # ------------------------------------------------------------------ DIAG_MEAS
    @property
    def meas(self) -> "Meas":
        return Meas(self)


class Meas:
    """DIAG_MEAS 0x3D (ICD §5.6, Appendix C). Every method returns the 16 words or raises LinkError."""

    def __init__(self, link: Link):
        self.L = link

    def raw(self, op: str, sel: int = 0, a: int = 0, b: int = 0) -> dict:
        return self.L.cmd("DIAG_MEAS", {"op": MEAS_OP[op], "sel": sel, "a": a, "b": b})

    def w(self, op: str, sel: int = 0, a: int = 0, b: int = 0) -> list[int]:
        r = self.raw(op, sel, a, b)
        if r["status"] != "OK":
            raise LinkError(f"DIAG_MEAS {op} sel={sel} a={a} b={b}: {r['status']} detail {r.get('detail')}")
        return r["w"]

    def info(self) -> dict:
        w = self.w("INFO")
        return {"variant": [n for n, b in MEAS_VAR.items() if w[0] >> b & 1], "variant_raw": w[0],
                "probe_hz": w[1], "counter_bits": w[2], "stamp_hz": w[3], "ring": w[4], "dma_latency_ns": w[5],
                "stamp_overhead_cyc": w[6], "stim_hz": w[7]}

    def probe_arm(self, src: str, mode: str = "TRIGGER", falling: bool = False, psc: int = 1) -> None:
        self.w("PROBE_ARM", MEAS_SRC[src], MEAS_MODE[mode] | (0x100 if falling else 0), psc)

    def probe_read(self) -> dict:
        w = self.w("PROBE_READ")
        return {"flags": [n for n, b in MEAS_PF.items() if w[0] >> b & 1], "ccr2": w[2], "ccr3": w[3], "ccr4": w[4],
                "pul_since_arm": w[5], "cnt": w[6], "psc": w[7], "pwm_min_period": w[8], "pwm_max_period": w[9],
                "pwm_min_high": w[10], "pwm_max_high": w[11], "pwm_n": w[12]}

    def counter(self, reset: bool = False) -> tuple[int, int]:
        w = self.w("COUNTER", 1 if reset else 0)
        return w[0], w[1]

    def stamps_page(self, chan: str, page: int = 0) -> tuple[int, int, list[int]]:
        w = self.w("STAMPS", MEAS_CHAN[chan], page)
        return w[0], w[1], w[2:16]

    def stamp_count(self, chan: str) -> int:
        return self.stamps_page(chan, 0)[0]

    def stamp_entries(self, chan: str, first: int, last: int) -> dict[int, int | None]:
        """Stamps by entry index (0 = first stamp since boot / clear), first…last inclusive. Entries already
        overwritten in the ring are None. Robust against new stamps arriving between page reads."""
        want = set(range(first, last + 1))
        got: dict[int, int | None] = {}
        n, ring, _ = self.stamps_page(chan, 0)
        drift = 0            # stamps added per request (J-EVT on the RX line: every request frame adds its own edges)
        for _ in range(4 * (len(want) // STAMPS_PER_PAGE + 2) + 8):
            todo = sorted(want - got.keys())
            if not todo:
                break
            pred = n + drift
            e = todo[-1]                                    # newest still missing
            back = pred - 1 - e
            if back < 0:
                for x in todo:
                    if pred - 1 - x < 0:
                        got[x] = None                       # not written (yet)
                continue
            if back >= ring:
                for x in todo:
                    if pred - 1 - x >= ring:
                        got[x] = None                       # overwritten
                continue
            page = back // STAMPS_PER_PAGE
            n2, ring, vals = self.stamps_page(chan, page)
            drift, n = max(n2 - n, 0), n2
            for k, v in enumerate(vals):
                idx = n2 - 1 - (STAMPS_PER_PAGE * page + k)
                if idx in want and idx not in got:
                    got[idx] = v if (n2 - 1 - idx) < ring else None
        for x in want - got.keys():
            got[x] = None
        return got

    def newest(self, chan: str, k: int = 1) -> list[int]:
        """The k newest stamps (newest first)."""
        out: list[int] = []
        page = 0
        while len(out) < k:
            n, ring, vals = self.stamps_page(chan, page)
            avail = min(n, ring) - page * STAMPS_PER_PAGE
            if avail <= 0:
                break
            out += vals[:min(STAMPS_PER_PAGE, avail)]
            page += 1
        return out[:k]

    def noinit(self, clear: bool = False) -> dict:
        w = self.w("NOINIT", 1 if clear else 0)
        return {"magic": w[0], "last_pul": w[1], "heartbeat": w[2], "hang_start": w[3], "prev_valid": w[4],
                "prev_pul": w[5], "prev_hb": w[6], "prev_hang": w[7], "boots": w[8]}

    def stim_run(self, pulses: int, hold_ms: int, low_pulse: bool = False, seed: int = 1) -> dict:
        return self.raw("STIM_RUN", ((hold_ms & 0x7F) << 1) | (1 if low_pulse else 0), pulses, seed)

    def hang(self, where: str, ms: int = 0) -> dict:
        return self.raw("HANG", MEAS_HANG[where], ms)

    def static_level(self, pin: str, level: int) -> dict:
        return self.raw("STATIC_LEVEL", MEAS_PIN[pin], level)

    def dwt(self, section: int, reset: bool = False) -> list[int]:
        return self.w("DWT", 1 if reset else 0, section)

"""SIM-vs-twin differential subset (SW_test_plan TC-SYS-008-02 M1 part, condition C6; SW_design §12.5 item 4).

The same scripted M1 scenario runs against Implementer B's simulator (out-of-process server,
``python -m bend_stand.io.sim.server``, SW_design §12.4) and against the FW host twin (A's firmware); both are
driven through the same means — B's io layer on the data port and vocabulary-v2 JSON lines on the control
port — and the normalised transcripts (responses / NACK codes, EVENT sequences, DATA flags and status bits,
STATUS latches, parameter values, AFE raw at a constant load) must be identical.

Selection ``BEND_SIM_VS``: ``auto`` (default = ``inproc``), ``server`` (B's out-of-process server, CLI ``--port --ctl [--scenario]``, real clock), ``inproc``
(B's ``SimBoard`` + ``SimControl`` on the real clock, hosted by this test behind TCP through a
``VirtualTransportPair`` bridge — B's code unchanged) or ``twin`` (a second twin as the "simulator": self-check
of this differential harness only).

Verifies: SYS-008, IF-010 (behavioural equality), FW-CMD-003, D-30, D-31, FW-STR-005, FW-CFG-003
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import build as twin_build

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as proto

from io_client import IoClient


SIM_VS = os.environ.get("BEND_SIM_VS", "auto")
if SIM_VS == "auto":
    SIM_VS = "inproc"      # server mode cannot select the M1 feature mask yet (finding SW-C-M1-01 to B)
pytestmark = [pytest.mark.twin, pytest.mark.rt]
if SIM_VS == "server":
    pytestmark.append(pytest.mark.needs_b("bend_stand.io.sim.server"))
if SIM_VS == "inproc":
    pytestmark.append(pytest.mark.needs_b("bend_stand.io.sim.control", "SimControl"))
M1_FEATURES = int(pg.Features.AFE_SYNTHETIC | pg.Features.NVM)      # fallback: the M1 FW feature mask (§12.1)


def twin_features(twin_exe) -> int:
    """Feature mask of A's current build (without TWIN): the simulator mirrors it (ICD v0.5 §7.6, D-37 b)."""
    import tempfile  # noqa: PLC0415

    from twin import Twin, TwinLink  # noqa: PLC0415

    with Twin("lockstep", exe=twin_exe, run_dir=Path(tempfile.mkdtemp())) as t:
        t.advance_ms(5)
        names = TwinLink(t).cmd("GET_INFO")["info"]["features"]
    return sum(int(pg.Features[n]) for n in names if n != "TWIN")


class InprocSim:
    """B's SimBoard (real clock) behind tcp:// + a JSON-lines control port mapped to SimControl.act."""

    def __init__(self, features: int = M1_FEATURES) -> None:
        import threading  # noqa: PLC0415

        from bend_stand.core.clock import MONOTONIC  # noqa: PLC0415
        from bend_stand.io.sim.board import SimBoard, SimConfig  # noqa: PLC0415
        from bend_stand.io.sim.control import SimControl  # noqa: PLC0415
        from bend_stand.io.transport import VirtualTransportPair  # noqa: PLC0415

        self.pair = VirtualTransportPair(MONOTONIC, bytes_per_s=None)
        self.pair.pc.open()
        self.board = SimBoard(MONOTONIC, self.pair.board, config=SimConfig(features=features))
        self.ctl = SimControl(self.board)
        self.board.start()
        self.stop = threading.Event()
        self.ds = socket.create_server(("127.0.0.1", 0))
        self.cs = socket.create_server(("127.0.0.1", 0))
        self.port, self.ctl_port = self.ds.getsockname()[1], self.cs.getsockname()[1]
        for fn in (self._data, self._control):
            threading.Thread(target=fn, daemon=True).start()

    def _data(self) -> None:
        import threading  # noqa: PLC0415

        c, _ = self.ds.accept()
        c.settimeout(0.002)

        def down() -> None:                 # board -> PC
            while not self.stop.is_set():
                d = self.pair.pc.read(4096, 0.002)
                if d:
                    try:
                        c.sendall(d)
                    except OSError:
                        return
        threading.Thread(target=down, daemon=True).start()
        while not self.stop.is_set():       # PC -> board
            try:
                d = c.recv(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if not d:
                return
            self.pair.pc.write(d)

    def _control(self) -> None:
        import threading  # noqa: PLC0415

        while not self.stop.is_set():
            try:
                c, _ = self.cs.accept()
            except OSError:
                return

            def serve(c=c) -> None:
                f = c.makefile("rwb")
                for raw in f:
                    req = json.loads(raw)
                    a = req.pop("action")
                    f.write(json.dumps(self.ctl.act(a, **req), default=str).encode() + b"\n")
                    f.flush()
            threading.Thread(target=serve, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.board.stop()
        self.ds.close()
        self.cs.close()

SW_SRC = Path(__file__).resolve().parents[2] / "src"


class Ctl:
    def __init__(self, port: int):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.f = self.s.makefile("rwb")

    def act(self, action: str, **args) -> dict:
        self.f.write((json.dumps(dict(args, action=action)) + "\n").encode())
        self.f.flush()
        return json.loads(self.f.readline())

    def close(self) -> None:
        self.s.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def sim_host(twin_exe, tmp_path, features: int = M1_FEATURES):
    """(endpoint, ctl_port) of the simulator side."""
    if SIM_VS == "inproc":
        h = InprocSim(features)
        yield f"tcp://127.0.0.1:{h.port}", h.ctl_port
        h.close()
        return
    if SIM_VS == "twin":
        from twin import Twin

        t = Twin("realtime", exe=twin_exe, run_dir=tmp_path / "sim")
        p, c = t.serve(0, 0)
        yield f"tcp://127.0.0.1:{p}", c
        t.close()
        return
    p, c = _free_port(), _free_port()
    env = dict(os.environ, PYTHONPATH=str(SW_SRC))
    proc = subprocess.Popen([sys.executable, "-m", "bend_stand.io.sim.server", "--port", str(p), "--ctl", str(c)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", c), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                pytest.fail("sim server exited: " + (proc.stdout.read() if proc.stdout else ""))
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("sim server did not open its control port within 10 s")
    yield f"tcp://127.0.0.1:{p}", c
    proc.terminate()
    proc.wait(timeout=5)


def _bits(v: int, names) -> tuple[str, ...]:
    return tuple(n for i, n in enumerate(names) if n and v >> i & 1)


def _ev(cli: IoClient, n0: int) -> list[tuple[str, int]]:
    return [(pg.Event(e.code).name if e.code in pg.Event._value2member_map_ else str(e.code), e.arg)
            for e in cli.events[n0:] if e.code != pg.Event.BOOT]


def _resp(r: proto.Response) -> tuple:
    return (r.status_name, r.detail)


def run_script(endpoint: str, ctl_port: int) -> dict:
    """The M1 differential scenario; returns a normalised transcript."""
    T: dict = {}
    ctl = Ctl(ctl_port)
    for a, kw in (("load_offset", {"counts": 50000}), ("afe", {"noise_counts": 0, "rate_error": 0.0}),
                  ("specimen", {"kind": "none"})):
        assert ctl.act(a, **kw)["ok"], a
    with IoClient(endpoint) as c:
        c.pump(0.1)
        info = c.info()
        T["info"] = (info.proto_major, info.proto_minor, info.payload_version, info.param_dict_hash, info.param_count)
        T["features_m1"] = tuple(sorted(info.features & {"AFE", "AFE_SYNTHETIC", "NVM"}))
        st = c.status()
        T["status0"] = (pg.MotionState(st.motion_state).name, st.halt_src, st.pause_src,
                        _bits(st.flags, pg.DATA_FLAGS_BITS), _bits(st.status, pg.DATA_STATUS_BITS),
                        _bits(st.sys_flags & ~int(pg.SysFlags.STREAM_ON), pg.SYS_FLAGS_BITS))
        T["params"] = tuple((e.key, e.value()) for e in c.all_params())
        # SET_PARAM: OK as stored / E_RANGE never clamped / E_CONFIG H5 with the partner id
        r = c.set_param("stream.fallback_hz", 20)
        T["set_ok"] = (_resp(r), proto.decode_param_entry(r.body).value() if r.ok else None)
        m = pgen.BY_KEY["io.release_ms"]
        T["set_range"] = _resp(c.request(pg.Cmd.SET_PARAM, proto.encode_param_entry_unchecked(m.id, int(m.type), m.max + 1)))
        T["set_h5"] = (_resp(c.set_param("afe.timeout_ms", 26)), _resp(c.set_param("afe.rate_sps", "SPS10")),
                       _resp(c.set_param("afe.timeout_ms", 250)), _resp(c.set_param("afe.rate_sps", "SPS80")))
        # stream + VALID
        c.cmd("STREAM_START")
        c.pump(1.0)
        d = c.data[-60:]
        T["stream"] = (len(c.data) >= 70, all(b.frame_seq == a.frame_seq + 1 for a, b in zip(d, d[1:])),
                       tuple(sorted({(_bits(x.flags, pg.DATA_FLAGS_BITS), _bits(x.status, pg.DATA_STATUS_BITS))
                                     for x in d})), tuple(sorted({x.afe_raw for x in d[-20:]})))
        T["set_valid"] = _resp(c.cmd("SET_VALID", valid=1))
        c.pump(0.3)
        T["valid_flag"] = "VALID" in _bits(c.data[-1].flags, pg.DATA_FLAGS_BITS)
        # latches (D-30 / D-31) with EVENT sequences
        for step, name, kw in (("pause", "PAUSE", {}), ("halt", "HALT", {}), ("resume_refused", "RESUME", {}),
                               ("halt_clear", "HALT_CLEAR", {}), ("pause2", "PAUSE", {}), ("resume", "RESUME", {}),
                               ("stop_bad", "STOP", {"mode": 2}), ("stop0", "STOP", {"mode": 0}),
                               ("stop1", "STOP", {"mode": 1}), ("estop_clear", "ESTOP_CLEAR", {}),
                               ("fault_clear", "FAULT_CLEAR", {})):
            n0 = len(c.events)
            r = c.cmd(name, **kw)
            c.pump(0.15)
            last = c.data[-1]
            T[step] = (_resp(r), r.body.hex(), _ev(c, n0), _bits(last.flags, pg.DATA_FLAGS_BITS),
                       _bits(last.status, pg.DATA_STATUS_BITS))
        # AFE stall -> stale + fallback frames (FW-STR-005), recovery
        n0 = len(c.events)
        assert ctl.act("afe", stall=True)["ok"]
        c.pump(0.6)
        fb = c.data[-3:]
        T["stall"] = (_ev(c, n0), tuple(sorted({(_bits(x.flags, pg.DATA_FLAGS_BITS), _bits(x.status, pg.DATA_STATUS_BITS),
                                                 x.afe_raw) for x in fb})))
        n0 = len(c.events)
        assert ctl.act("afe", stall=False)["ok"]
        c.pump(1.0)
        T["recover"] = (_ev(c, n0), _bits(c.data[-1].status, pg.DATA_STATUS_BITS))
        st = c.status()
        T["status_end"] = (pg.MotionState(st.motion_state).name, st.halt_src, st.pause_src,
                           _bits(st.status & ~int(pg.DataStatus.AFE_SETTLING), pg.DATA_STATUS_BITS))
    ctl.close()
    return T


# ICD v0.5 §7.6 (D-37 b, IF-C-M1-02 closed): while FEAT_DRV_SIGNALS = 0, ALM/PEND/DRV_PWR are sent as 0 and are
# invalid. A's M1 FW sends 0; B's simulator still reports the world (PEND = 1, DRV_PWR = 1) -> follow-up for B
# (mask by protocol_gen.DATA_STATUS_FEATURE / IO_FEATURE) - aligned by B in M2; the subset still masks the
# bits, test_sim_vs_twin_drv_signal_bits compares them in full (xfail removed at ICD v0.5).
UNDEFINED_M1_BITS = {"ALM", "PEND", "DRV_PWR"}


def _mask(x):
    if isinstance(x, (tuple, list)):
        if x and all(isinstance(i, str) for i in x):
            return tuple(i for i in x if i not in UNDEFINED_M1_BITS)
        return tuple(_mask(i) for i in x)
    return x


def _diffs(a: dict, b: dict) -> dict:
    return {k: {"twin": a.get(k), "sim": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


@pytest.fixture(scope="module")
def transcripts(tmp_path_factory):
    """Run the scenario once on each side (module scope: ~10 s per side)."""
    from conftest import twin_unavailable
    from twin import Twin

    if twin_unavailable():
        pytest.xfail(twin_unavailable())
    exe = twin_build.ensure_built(os.environ.get("BEND_TWIN_CORE", "fw"))
    tw = Twin("realtime", exe=exe, run_dir=tmp_path_factory.mktemp("twin"))
    try:
        p, c = tw.serve(0, 0)
        twin_t = run_script(f"tcp://127.0.0.1:{p}", c)
    finally:
        tw.close()
    with sim_host(exe, tmp_path_factory.mktemp("sim"), twin_features(exe)) as (ep, ctl):
        sim_t = run_script(ep, ctl)
    out = os.environ.get("BEND_DIFF_OUT")
    if out:
        Path(out).write_text(json.dumps({"twin": twin_t, "sim": sim_t, "diffs": _diffs(twin_t, sim_t)}, indent=1,
                                        default=str))
    return twin_t, sim_t


@pytest.mark.req("SYS-008", "IF-010", "D-30", "D-31", "FW-STR-005", "FW-CFG-003")
def test_sim_vs_twin_m1_subset(transcripts):
    twin_t, sim_t = transcripts
    d = _diffs({k: _mask(v) for k, v in twin_t.items()}, {k: _mask(v) for k, v in sim_t.items()})
    lines = [f"  {k}: {v}" for k, v in d.items()]
    assert not d, "SIM vs twin differences:\n" + "\n".join(lines)


@pytest.mark.req("SYS-008", "FW-SW-004", "FW-SW-005")
def test_sim_vs_twin_drv_signal_bits(transcripts):
    twin_t, sim_t = transcripts
    assert not _diffs(twin_t, sim_t)

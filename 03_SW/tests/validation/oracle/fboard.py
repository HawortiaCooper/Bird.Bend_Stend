"""F-board: Validator F's own minimal FW stand-in over TCP, built only on the Integrator's oracle ``ref_codec`` and
the dictionary loaded from ``params.yaml`` (never on B's simulator or codec). Used where the simulator cannot
present a condition (IF-008: other PROTO major / PAYLOAD version / dictionary hash) — SW_test_plan TC-IF-008-01.

Behaviour (ICD v0.4.1, deliberately minimal): answers every request it understands with OK (+ body), keeps a
RAM parameter table (SET_PARAM stores, GET_PARAM / GET_ALL_PARAMS read it), streams DATA at 80 Hz while the
stream is on (``payload_version`` configurable), records every received request for assertions. No motion.

Verifies: (oracle) IF-008
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any

import ref_codec as rc


class FBoard:
    def __init__(self, pdict: Any, *, proto_major: int = 1, proto_minor: int = 0, payload_version: int = 1,
                 dict_hash: int | None = None, data_payload_version: int | None = None,
                 flags: tuple[str, ...] = (), status_bits: tuple[str, ...] = ("DRV_PWR",), seq_start: int = 0,
                 skip_every: int = 0, dup_every: int = 0, max_frames: int | None = None) -> None:
        self.pdict = pdict
        self.params = {p.key: p.default for p in pdict.params}
        self.by_id = {p.id: p for p in pdict.params}
        self.info = {"proto_major": proto_major, "proto_minor": proto_minor, "payload_version": payload_version,
                     "fw_version": [9, 9, 9],
                     "param_dict_hash": f"0x{(pdict.hash if dict_hash is None else dict_hash):08X}",
                     "uid": "46424F4152442D303030303031"[:24], "build": "F-BOARD", "param_count": len(pdict.params),
                     "features": ["AFE", "MOTION", "HOMING", "MOVE_UNTIL_LOAD", "NVM"]}
        self.data_pv = payload_version if data_payload_version is None else data_payload_version
        self.flags = list(flags)
        self.status_bits = list(status_bits)
        self.received: list[tuple[str, dict]] = []
        self.stream_on = False
        self.frame_seq = seq_start
        self.skip_every, self.dup_every, self.max_frames = skip_every, dup_every, max_frames
        self.frames_sent = 0             # DATA frames written (duplicates included)
        self.unique_sent = 0             # distinct DATA frames written
        self.skipped = 0                 # frame_seq values skipped without OVERRUN (= link-attributed loss)
        self.dups = 0
        self.sent_seqs: list[int] = []
        self._raw_out = bytearray()
        self._hold_s = 0.0
        self._hold_until = 0.0
        self._held = b""
        self.t0 = time.monotonic()
        self._srv = socket.create_server(("127.0.0.1", 0))
        self.port = self._srv.getsockname()[1]
        self.endpoint = f"tcp://127.0.0.1:{self.port}"
        self._stop = threading.Event()
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._serve, name="fboard", daemon=True)]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------------------------------------ helpers
    def _t_us(self) -> int:
        return int((time.monotonic() - self.t0) * 1e6) & 0xFFFFFFFF

    def inject_raw(self, data: bytes, hold_s: float = 0.0) -> None:
        """Queue raw bytes for the PC (corrupt / truncated / over-long frames); afterwards keep the line silent
        for ``hold_s`` (outgoing frames are held back) so a truncated frame meets the inter-byte timeout."""
        with self._lock:
            self._raw_out += data
            self._hold_s = hold_s

    def names(self) -> list[str]:
        with self._lock:
            return [n for n, _ in self.received]

    def _entry(self, p: Any) -> dict:
        v = self.params[p.key]
        return {"id": p.id, "type": p.type, "value": int(v) if p.type != "f32" else float(v)}

    def _status(self) -> dict:
        d = {k: 0 for k in rc.STATUS_FIELDS}
        d.update(flags=list(self.flags), status=list(self.status_bits), faults=[], io=[], sys_flags=(
            ["STREAM_ON"] if self.stream_on else []), motion_state="IDLE", home_phase="NONE", halt_src="NONE",
            reset_cause="POWER_ON", pause_src="NONE", t_us=self._t_us(), uptime_ms=int((time.monotonic() - self.t0)
                                                                                        * 1000))
        return d

    def _answer(self, name: str, req: dict) -> dict:
        if name == "GET_INFO":
            return {"status": "OK", "info": self.info}
        if name == "GET_STATUS":
            return {"status": "OK", "board_status": self._status()}
        if name == "GET_ALL_PARAMS":
            ps = sorted(self.pdict.params, key=lambda p: p.id)
            count = -(-len(ps) // rc.PARAMS_PER_PAGE)
            page = req["page"]
            sl = ps[page * rc.PARAMS_PER_PAGE:(page + 1) * rc.PARAMS_PER_PAGE]
            return {"status": "OK", "page": page, "page_count": count, "entries": [self._entry(p) for p in sl]}
        if name == "GET_PARAM":
            return {"status": "OK", "entry": self._entry(self.by_id[req["id"]])}
        if name == "SET_PARAM":
            p = self.by_id[req["id"]]
            self.params[p.key] = req["value"]
            return {"status": "OK", "entry": self._entry(p)}
        if name == "STREAM_START":
            self.stream_on = True
        elif name == "STREAM_STOP":
            self.stream_on = False
        elif name == "SET_VALID":
            return {"status": "OK", "t_us": self._t_us()}
        elif name == "ENABLE":
            return {"status": "OK", "settle_ms": 0}
        elif name == "FAULT_CLEAR":
            return {"status": "OK", "cleared": []}
        return {"status": "OK"}

    # ------------------------------------------------------------------------------------------ server
    def _serve(self) -> None:
        self._srv.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            conn.setblocking(False)
            self._conn = conn
            self._client(conn)

    def _client(self, conn: socket.socket) -> None:
        parser = rc.FrameParser()
        next_data = time.monotonic()
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
                if not data:
                    return
            except BlockingIOError:
                data = b""
            except OSError:
                return
            out = b""
            for fr in parser.feed(data):
                kind, name = rc.classify(fr.type)
                if kind != "request":
                    continue
                try:
                    req = rc.decode_request(name, fr.payload)
                except ValueError:
                    req = {}
                with self._lock:
                    self.received.append((name, req))
                resp = self._answer(name, req)
                out += rc.encode_frame(fr.type | rc.RESP_BIT, fr.seq, rc.encode_response(name, resp))
            now = time.monotonic()
            with self._lock:
                if self._raw_out:
                    raw = bytes(self._raw_out)
                    self._raw_out.clear()
                    try:
                        conn.sendall(self._held + out + raw)
                    except OSError:
                        return
                    self._held, out = b"", b""
                    self._hold_until = now + self._hold_s
            if now < self._hold_until:
                self._held += out
                out = b""
                time.sleep(0.001)
                continue
            if self._held:
                out, self._held = self._held + out, b""
            if self.stream_on and now >= next_data and (self.max_frames is None or self.unique_sent < self.max_frames):
                next_data = now + 0.0125
                n = self.unique_sent + 1
                if self.skip_every and n % self.skip_every == 0:
                    self.frame_seq += 1                                  # one frame_seq lost on the "link"
                    self.skipped += 1
                d = {"t_us": self._t_us(), "payload_version": self.data_pv, "flags": self.flags, "afe_raw": 50_000,
                     "setpoint_um": 0, "frame_seq": self.frame_seq & 0xFFFF, "status": self.status_bits}
                fr = rc.encode_frame(rc.ASYNC["DATA"], self.frame_seq & 0xFF, rc.encode_data(d))
                out += fr
                self.sent_seqs.append(self.frame_seq & 0xFFFF)
                self.frames_sent += 1
                self.unique_sent += 1
                if self.dup_every and n % self.dup_every == 0:
                    out += fr
                    self.frames_sent += 1
                    self.dups += 1
                self.frame_seq += 1
            if out:
                try:
                    conn.sendall(out)
                except OSError:
                    return
            if not data:
                time.sleep(0.001)

    def close(self) -> None:
        self._stop.set()
        for s in (self._conn, self._srv):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        for t in self._threads:
            t.join(2)

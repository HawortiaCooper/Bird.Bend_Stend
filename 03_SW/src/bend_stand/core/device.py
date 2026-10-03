"""Device — the wire-level API (SW_design §5.1–§5.3, §5.5, §4.4.1, §4.6), M1 subset.

Owns the link of one connection: transport, Reader, FrameWriter, CommandChannel, StopConfirmer. Everything that
waits for the board is a **generator job** (``*_job``) driven by the Worker (``core.jobs``): the same code runs
threaded on the real clock and deterministically on the lockstep clock. ``tick(now)`` is the Supervisor body
(5 ms): channel timeouts/retries, stop confirmation, heartbeat PING after 150 ms of TX idle (gated by
liveness), link states, STATUS poll (1 Hz streaming / 4 Hz stream off), DATA-loss-while-moving STOP and
reconnect attempts (never re-enabling, re-homing or re-moving, §4.6).

Priority actions (``stop``/``halt``/``pause``) never raise and return a ``StopResult``; the clears and RESUME
are VERIFY class (one frame, never re-sent; a timeout is resolved by GET_STATUS, D-31/D-34).

Implements: SW-PLT-003, SW-CFG-001…004 (device side), IF-005, IF-008, IF-011, SAF-SW-003 (heartbeat, link
loss), SW-STOP-001/002/003 (wire part), SW-ACQ-001 (stream toggle)
"""
from __future__ import annotations

import logging
import struct
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock
from bend_stand.core.errors import (
    CommandNotExecuted, CommandOutcomeUnknown, CommandTimeout, ConfigReadOnly, IncompatibleFirmware, LinkError,
    NackError, NotConnected, TransportError,
)
from bend_stand.core.events import EventBus
from bend_stand.core.jobs import Job, Poll, Sleep
from bend_stand.core.link import (
    CONNECT_INFO_TIMEOUT_NS, HEARTBEAT_IDLE_NS, CommandChannel, FrameWriter, Lane, StopConfirmer, link_state_for,
)
from bend_stand.core.liveness import LivenessMonitor
from bend_stand.core.model import (
    BoardStatus, ClearResult, Compat, DeviceInfo, LinkState, LinkStateChange, LinkStats, StopConfirmation,
    StopResult, ThresholdState,
    VerifyReport, WriteItem, WriteStatus, bit_names,
)
from bend_stand.core.observers import ReleasingFuture
from bend_stand.core.params import ParamStore, check_edits, normalise, write_plan
from bend_stand.io import protocol as P
from bend_stand.io.framing import Frame
from bend_stand.io.reader import Reader
from bend_stand.io.transport import Transport

log = logging.getLogger("bend_stand.core.device")
MS = 1_000_000
Cmd = pg.Cmd
CONNECT_INFO_WINDOW_NS = 3000 * MS
DATA_LOSS_MOVING_NS = 500 * MS
RECONNECT_PERIOD_NS = 1000 * MS
STATUS_POLL_STREAM_NS = 1000 * MS
STATUS_POLL_IDLE_NS = 250 * MS
SESSION_KEYS = ("safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw")


@dataclass(frozen=True)
class DeviceSettings:
    wire_log: bool = False
    stream_on_connect: bool = True
    auto_reconnect: bool = True
    heartbeat: bool = True
    seq_seed: int | None = None


class Device:
    def __init__(self, clock: Clock, events: EventBus, liveness: LivenessMonitor,
                 transport_for: Callable[[str], Transport], settings: DeviceSettings | None = None) -> None:
        self.clock = clock
        self.events = events
        self.liveness = liveness
        self.transport_for = transport_for
        self.settings = settings or DeviceSettings()
        self.params = ParamStore()
        self._lock = threading.RLock()
        self.endpoint: str | None = None
        self.state = LinkState.DISCONNECTED
        self.why = ""
        self.compat = Compat.OK
        self.info: DeviceInfo | None = None
        self.board: BoardStatus | None = None
        self.board_ns = 0
        self.thresholds = ThresholdState()
        self.stream_on = False
        self.transport: Transport | None = None
        self.reader: Reader | None = None
        self.writer: FrameWriter | None = None
        self.channel: CommandChannel | None = None
        self.confirmer: StopConfirmer | None = None
        self.on_async: Callable[[Frame], None] | None = None          # → pipeline queue (backend)
        self.on_link_change: Callable[[LinkState], None] | None = None
        self.submit_job: Callable[[Callable[..., Job]], Any] | None = None   # Worker.submit (backend)
        self.threaded_reader = not clock.is_lockstep
        # latest FW indications (DATA, then STATUS)
        self.last_flags = 0
        self.last_status = 0
        self.last_flags_ns = 0                       # receive stamp of the frame/STATUS that set last_flags
        self.last_data_ns: int | None = None
        self.halt_src_ev: int | None = None          # latch sources from EVENT HALT_SET / PAUSED (SWD-M1-05)
        self.pause_src_ev: int | None = None
        self.event_counts: dict[int, int] = {}
        self._user_disconnected = True
        self._poll_pending: Any = None
        self._next_poll_ns = 0
        self._next_reconnect_ns = 0
        self._reconnecting = False
        self._data_loss_stop_sent = False
        self._hb_pending: Any = None
        self.uid_seen: str | None = None
        self.board_changed = False
        self._sync_active = False
        self._valid_wanted = 0

    # ============================================================================== link assembly
    @property
    def connected(self) -> bool:
        return self.state in (LinkState.CONNECTED, LinkState.DEGRADED)

    def _set_state(self, state: LinkState, why: str = "") -> None:
        with self._lock:
            if state == self.state and why == self.why:
                return
            self.state, self.why = state, why
        self.events.publish("link.state", LinkStateChange(state, why, self.endpoint))
        cb = self.on_link_change
        if cb is not None:
            cb(state)

    def _open_link(self, endpoint: str) -> None:
        self._close_link()
        tr = self.transport_for(endpoint)
        if self.settings.wire_log:
            tr.enable_wire_log()
        tr.open()                                        # the operator selected this endpoint (D-06)
        writer = FrameWriter(tr, self.clock)
        channel = CommandChannel(writer, self.clock, seed=self.settings.seq_seed)
        channel.on_tx_error = self._on_transport_error
        reader = Reader(tr, self.clock, on_response=channel.on_response, on_async=self._on_async,
                        on_error=self._on_transport_error, beat=lambda t: self.liveness.beat("reader", t),
                        thread_init=_raise_priority)
        confirmer = StopConfirmer(channel, self.clock, on_confirmed=self._stop_confirmed,
                                  on_unconfirmed=self._stop_unconfirmed, poll_status=self._poll_now,
                                  stream_on=lambda: self.stream_on)
        with self._lock:
            self.transport, self.writer, self.channel, self.reader, self.confirmer = tr, writer, channel, reader, confirmer
        pending = tr.drain_input()                       # fed to the parser, not flushed blindly (R3 P13)
        if pending:
            reader.feed_pending(pending)
        if self.threaded_reader:
            reader.start()

    def _close_link(self, exc: Exception | None = None) -> None:
        with self._lock:
            tr, reader, channel = self.transport, self.reader, self.channel
            self.transport = self.reader = self.writer = self.channel = self.confirmer = None
        if reader is not None:
            reader.stop()
        if channel is not None:
            channel.close(exc or NotConnected())
        if tr is not None:
            try:
                tr.close()
            except Exception:  # noqa: BLE001 pragma: no cover
                pass
        self.liveness.forget("reader")
        self.stream_on = False
        self.last_data_ns = None

    def _on_transport_error(self, exc: Exception) -> None:
        log.warning("transport error: %s", exc)
        self._next_reconnect_ns = self.clock.monotonic_ns() + RECONNECT_PERIOD_NS
        self._set_state(LinkState.LOST, f"transport: {exc}")

    def _on_async(self, fr: Frame) -> None:
        if fr.type == pg.AsyncType.DATA and len(fr.payload) >= pg.DATA_LEN:
            self.last_flags = fr.payload[5]
            self.last_status = struct.unpack_from("<H", fr.payload, 16)[0]
            self.last_data_ns = fr.t_ns
            self.last_flags_ns = fr.t_ns
            self._data_loss_stop_sent = False
        cb = self.on_async
        if cb is not None:
            cb(fr)

    # ============================================================================== requests
    def _ch(self) -> CommandChannel:
        ch = self.channel
        if ch is None:
            raise NotConnected()
        return ch

    def request(self, cmd: int, payload: bytes | Callable[[], bytes | None] = b"", **kw: Any) -> Any:
        try:
            return self._ch().submit(cmd, payload, **kw)
        except NotConnected as exc:
            from bend_stand.core.observers import failed_future  # noqa: PLC0415

            return failed_future(exc)

    # ============================================================================== connect (§5.2)
    def connect_job(self, endpoint: str, *, stream: bool | None = None) -> Job:
        stream = self.settings.stream_on_connect if stream is None else stream
        self.endpoint = endpoint
        self._user_disconnected = False
        self._set_state(LinkState.CONNECTING, "")
        try:
            self._open_link(endpoint)
        except (TransportError, OSError, ValueError) as exc:
            self._set_state(LinkState.DISCONNECTED, str(exc))
            raise TransportError(str(exc)) from exc
        self._sync_active = True
        try:
            info = yield from self._get_info_job()
            yield from self._after_info_job(info, stream)
        finally:
            self._sync_active = False
        return info

    def _get_info_job(self) -> Job:
        t0 = self.clock.monotonic_ns()
        while True:
            try:
                resp = yield self.request(Cmd.GET_INFO, timeout_ns=CONNECT_INFO_TIMEOUT_NS)
                return P.decode_info(resp.body)
            except CommandTimeout:
                if self.clock.monotonic_ns() - t0 >= CONNECT_INFO_WINDOW_NS:
                    self._close_link()
                    self._set_state(LinkState.DISCONNECTED, "no board answered GET_INFO")
                    raise LinkError("no board: GET_INFO not answered within 3 s") from None

    def _after_info_job(self, info: DeviceInfo, stream: bool) -> Job:
        compat = Compat.OK
        if info.proto_major != pg.PROTO_MAJOR:
            compat |= Compat.MAJOR_MISMATCH
        elif info.proto_minor < pg.PROTO_MINOR:
            compat |= Compat.MINOR_DIFF
        if info.payload_version != pg.PAYLOAD_VERSION:
            compat |= Compat.PAYLOAD_MISMATCH
        if info.param_dict_hash != pgen.PARAM_DICT_HASH:
            compat |= Compat.PARAM_HASH_MISMATCH
        self.info, self.compat = info, compat
        self.board_changed = self.uid_seen is not None and self.uid_seen != info.uid
        self.uid_seen = info.uid
        self.events.publish("device.info", info)
        self.events.publish("link.compat", compat)
        self._set_state(LinkState.CONNECTED, "")
        yield from self._sync_job(stream)

    def _sync_job(self, stream: bool) -> Job:
        """Connect steps 4–7 (also after EVENT BOOT): STATUS, all parameters, session values, stream."""
        yield from self.get_status_job()
        yield from self.read_all_params_job()
        if not self.compat.read_only:
            yield from self.session_values_job()
        if stream and not self.compat.read_only:
            yield from self.stream_job(True)
        elif stream:                                     # read-only state: monitoring still allowed
            yield from self.stream_job(True)

    def disconnect_job(self) -> Job:
        self._user_disconnected = True
        if self.channel is not None and self.connected:
            if self.last_flags & pg.DataFlags.MOVING:
                self.stop(pg.StopMode.IMMEDIATE, "disconnect")
            try:
                yield self.request(Cmd.STREAM_STOP)
            except (LinkError, CommandTimeout):
                pass
        self._close_link()
        self.info = None
        self.board = None
        self.params.clear()
        self.thresholds = ThresholdState()
        self._set_state(LinkState.DISCONNECTED, "disconnected")
        return None

    # ============================================================================== status / params
    def get_status_job(self) -> Job:
        resp = yield self.request(Cmd.GET_STATUS)
        st = P.decode_status(resp.body)
        self._apply_status(st, resp.t_host_ns)
        return st

    def _apply_status(self, st: BoardStatus, t_ns: int) -> None:
        self.board, self.board_ns = st, t_ns
        if self.last_data_ns is None or t_ns >= self.last_data_ns:
            self.last_flags, self.last_status = st.flags, st.status
            self.last_flags_ns = t_ns
        self.stream_on = bool(st.sys_flags & pg.SysFlags.STREAM_ON)
        self.events.publish("device.status", st)

    def read_all_params_job(self) -> Job:
        values: dict[str, Any] = {}
        page, count = 0, 1
        while page < count:
            resp = yield self.request(Cmd.GET_ALL_PARAMS, P.build_request(Cmd.GET_ALL_PARAMS, page=page))
            pp = P.decode_param_page(resp.body)
            count = pp.page_count
            for e in pp.entries:
                meta = e.meta
                if meta is None:
                    continue
                try:
                    values[meta.key] = e.value()
                except ValueError:
                    log.warning("bad PARAM_ENTRY for %s", meta.key)
            page += 1
        self.params.set_all(values)
        self.events.publish("device.params", dict(values))
        return values

    def get_param_job(self, key: str) -> Job:
        meta = pgen.BY_KEY[key]
        resp = yield self.request(Cmd.GET_PARAM, P.build_request(Cmd.GET_PARAM, id=meta.id))
        v = P.decode_param_entry(resp.body[:7]).value()
        self.params.update(key, v)
        return v

    def set_param_job(self, key: str, value: Any) -> Job:
        """SET_PARAM → value as stored (RETRY class; ``safety.*`` on the CONTROL lane)."""
        if self.compat.config_read_only and key not in SESSION_KEYS:
            raise ConfigReadOnly()
        meta = pgen.BY_KEY[key]
        v = normalise(meta, value)
        lane = Lane.CONTROL if key.startswith("safety.") else Lane.GENERAL
        resp = yield self.request(Cmd.SET_PARAM, P.build_set_param(meta.id, v), lane=lane, key=f"param:{key}")
        stored = P.decode_param_entry(resp.body[:7]).value()
        self.params.update(key, stored)
        return stored

    def session_values_job(self) -> Job:
        """M1 ThresholdManager subset: no calibration → write and verify the dictionary defaults of the three
        session values (state DEFAULT_ONLY, SAF-SW-002; calibrated thresholds are M3)."""
        target = {k: pgen.BY_KEY[k].default for k in SESSION_KEYS}
        cur = {k: self.params.get(k) for k in SESSION_KEYS}
        try:
            for k, v in write_plan(cur, target):
                yield from self.set_param_job(k, v)
            for k in SESSION_KEYS:
                got = yield from self.get_param_job(k)
                if got != target[k]:
                    raise LinkError(f"{k}: read-back {got} != {target[k]}")
        except (LinkError, CommandTimeout) as exc:
            self.thresholds = ThresholdState("FAILED", text=str(exc))
        else:
            self.thresholds = ThresholdState("DEFAULT_ONLY", raw_min=target["safety.load_raw_min"],
                                             raw_max=target["safety.load_raw_max"],
                                             zero_raw=target["safety.zero_raw"],
                                             text="no calibration: board load limit at its nominal default")
        self.events.publish("safety.thresholds", self.thresholds)
        return self.thresholds

    # ============================================================================== write and verify (§5.3)
    def write_and_verify_job(self, edits: Mapping[str, Any]) -> Job:
        if self.compat.config_read_only:
            raise ConfigReadOnly()
        if self.channel is None:
            raise NotConnected()
        values = self.params.values()
        moving = bool(self.last_flags & pg.DataFlags.MOVING)
        issues = check_edits(values, edits, moving=moving)
        errors = [i for i in issues if i.severity == "ERROR"]
        if errors:
            items = tuple(WriteItem(k, v, None, WriteStatus.NOT_ATTEMPTED, text="check failed") for k, v in edits.items())
            return VerifyReport(items, issues=tuple(issues))
        busy = {i.key for i in issues if i.code == "MOVING" and i.key is not None}
        plan = write_plan(values, {k: v for k, v in edits.items() if k not in busy})
        results: dict[str, WriteItem] = {k: WriteItem(k, edits[k], values.get(k), WriteStatus.UNCHANGED)
                                         for k in edits}
        for k in busy:                               # not moving_ok while moving: BUSY without sending (§5.3)
            results[k] = WriteItem(k, edits[k], values.get(k), WriteStatus.BUSY,
                                   text="cannot be changed while the axis moves")
        todo = list(plan)
        while todo:
            again: list[tuple[str, Any]] = []
            progress = False
            for key, v in todo:
                meta = pgen.BY_KEY[key]
                try:
                    stored = yield from self.set_param_job(key, v)
                except NackError as exc:
                    if exc.name == "E_CONFIG":
                        again.append((key, v))
                        results[key] = WriteItem(key, v, None, WriteStatus.REJECTED, exc.status, exc.detail,
                                                 exc.detail_text)
                        continue
                    status = WriteStatus.BUSY if exc.name == "E_BUSY" else WriteStatus.REJECTED
                    results[key] = WriteItem(key, v, None, status, exc.status, exc.detail, exc.detail_text)
                    continue
                except CommandTimeout as exc:
                    results[key] = WriteItem(key, v, None, WriteStatus.TIMEOUT, text=str(exc))
                    continue
                progress = True
                if stored != v:
                    results[key] = WriteItem(key, v, stored, WriteStatus.MISMATCH, text=f"stored {stored!r}")
                elif meta.reboot_required:
                    results[key] = WriteItem(key, v, stored, WriteStatus.REBOOT_REQUIRED,
                                             text="effective after Save + Reboot")
                else:
                    results[key] = WriteItem(key, v, stored, WriteStatus.OK)
            if not again or not progress:
                break
            todo = again
        # (3) read-back
        try:
            back = yield from self.read_all_params_job()
            for key, item in list(results.items()):
                if item.status in (WriteStatus.OK, WriteStatus.REBOOT_REQUIRED) and back.get(key) != item.stored:
                    results[key] = replace(item, status=WriteStatus.MISMATCH, stored=back.get(key),
                                           text=f"read-back {back.get(key)!r}")
        except (LinkError, CommandTimeout) as exc:
            log.warning("read-back failed: %s", exc)
        # (4) STATUS → CFG_DIRTY, REBOOT_PENDING
        cfg_dirty = reboot = None
        try:
            st = yield from self.get_status_job()
            cfg_dirty = bool(st.sys_flags & pg.SysFlags.CFG_DIRTY)
            reboot = bool(st.sys_flags & pg.SysFlags.REBOOT_PENDING)
        except (LinkError, CommandTimeout):
            pass
        return VerifyReport(tuple(results[k] for k in edits), cfg_dirty, reboot, tuple(issues))

    # ============================================================================== NVM (VERIFY class)
    def _verify_job(self, cmd: int, executed: Callable[[BoardStatus, BoardStatus | None], bool]) -> Job:
        """Send a VERIFY command once; on a timeout decide by GET_STATUS (§4.4.1). Returns the response or None
        when GET_STATUS proved execution."""
        before = self.board
        try:
            resp = yield self.request(cmd)
            return resp
        except CommandTimeout:
            try:
                st = yield from self.get_status_job()
            except (LinkError, CommandTimeout) as exc:
                raise CommandOutcomeUnknown(f"{Cmd(cmd).name}: outcome unknown ({exc})") from None
            if executed(st, before):
                return None
            raise CommandNotExecuted(f"{Cmd(cmd).name} was not executed (no automatic re-send)") from None

    def save_job(self) -> Job:
        if self.compat.config_read_only:
            raise ConfigReadOnly()
        seq0 = self.board.nvm_record_seq if self.board else 0
        yield from self._verify_job(Cmd.SAVE_PARAMS, lambda st, _b: st.nvm_record_seq > seq0
                                    and not st.sys_flags & pg.SysFlags.CFG_DIRTY)
        st = yield from self.get_status_job()
        if st.sys_flags & pg.SysFlags.CFG_DIRTY:
            raise LinkError("SAVE_PARAMS: CFG_DIRTY still set after the save")
        return None

    def load_job(self) -> Job:
        if self.compat.config_read_only:
            raise ConfigReadOnly()
        n0 = self.event_counts.get(int(pg.Event.PARAMS_LOADED), 0) + self.event_counts.get(
            int(pg.Event.PARAMS_DEFAULTED), 0)
        yield from self._verify_job(Cmd.LOAD_PARAMS, lambda _st, _b: self.event_counts.get(
            int(pg.Event.PARAMS_LOADED), 0) + self.event_counts.get(int(pg.Event.PARAMS_DEFAULTED), 0) > n0)
        values = yield from self.read_all_params_job()
        yield from self.session_values_job()
        yield from self.get_status_job()
        return values

    def default_job(self) -> Job:
        if self.compat.config_read_only:
            raise ConfigReadOnly()
        n0 = self.event_counts.get(int(pg.Event.PARAMS_DEFAULTED), 0)
        yield from self._verify_job(Cmd.DEFAULT_PARAMS,
                                    lambda _st, _b: self.event_counts.get(int(pg.Event.PARAMS_DEFAULTED), 0) > n0)
        values = yield from self.read_all_params_job()
        yield from self.session_values_job()                 # DEFAULT resets the session values (ICD §11.5)
        yield from self.get_status_job()
        return values

    def reboot_job(self) -> Job:
        if self.last_flags & pg.DataFlags.MOVING:
            raise LinkError("REBOOT refused while moving")
        boots0 = self.event_counts.get(int(pg.Event.BOOT), 0)
        try:
            yield self.request(Cmd.REBOOT, P.build_request(Cmd.REBOOT, magic=pg.REBOOT_MAGIC))
        except CommandTimeout:
            pass
        ok = yield Poll(lambda: self.event_counts.get(int(pg.Event.BOOT), 0) > boots0, 3000 * MS)
        if not ok:
            raise CommandOutcomeUnknown("REBOOT: no EVENT BOOT within 3 s")
        return None

    # ============================================================================== stream / valid
    def stream_job(self, on: bool) -> Job:
        cmd = Cmd.STREAM_START if on else Cmd.STREAM_STOP
        yield self.request(cmd, key="stream")
        self.stream_on = on
        return None

    def set_valid_job(self, flag: bool) -> Job:
        """SET_VALID (RETRY, newest value); returns the device ``t_us`` from which it applies (FW-CMD-002)."""
        self._valid_wanted = 1 if flag else 0
        lane = Lane.SAFETY if not flag else Lane.CONTROL
        resp = yield self.request(Cmd.SET_VALID, lambda: bytes([self._valid_wanted]), lane=lane, key="valid")
        return P.decode_u32(resp.body)

    # ============================================================================== stops (priority, never raise)
    def latest(self) -> tuple[int, int]:
        """Newest (flags, status) from DATA or GET_STATUS."""
        return self.last_flags, self.last_status

    def _priority_stop(self, cmd: Cmd, payload: bytes, source: str,
                       indication: Callable[[int, int], bool]) -> StopResult:
        """Write STOP / HALT / PAUSE on the priority path whenever a link is open — in every link state incl.
        DEGRADED, LOST and CONNECTING (SWD-M1-01, IF-011; the FW→PC direction may be the broken one). Arms the
        CONFIRM repetition: confirmed only by the ACK or by the FW indication in a DATA / STATUS **received after
        the write** (SWD-M1-02, ICD §9.3)."""
        ch, conf, tr = self.channel, self.confirmer, self.transport
        if ch is None or conf is None or tr is None or not tr.is_open:
            res = StopResult(cmd.name, source, False, None, "not connected")
            self.events.publish("stop.issued", res)
            return res
        ch.bump_epoch()
        fut, t, err = ch.send_priority(cmd, payload)
        if err is not None or t is None:
            res = StopResult(cmd.name, source, False, None, err or "not written")
        else:
            t_sent = t

            def confirmed() -> bool:
                return self.last_flags_ns > t_sent and indication(self.last_flags, self.last_status)
            conf.arm(int(cmd), payload, fut, confirmed, source)
            res = StopResult(cmd.name, source, True, t)
        self.events.publish("stop.issued", res)
        return res

    def stop(self, mode: pg.StopMode = pg.StopMode.IMMEDIATE, source: str = "user") -> StopResult:
        return self._priority_stop(Cmd.STOP, bytes([int(mode)]), source,
                                   lambda f, s: not f & pg.DataFlags.MOVING)

    def halt(self, source: str = "user") -> StopResult:
        return self._priority_stop(Cmd.HALT, b"", source, lambda f, s: bool(f & pg.DataFlags.HALT))

    def pause(self, source: str = "user") -> StopResult:
        return self._priority_stop(Cmd.PAUSE, b"", source, lambda f, s: bool(s & pg.DataStatus.PAUSED))

    def _stop_confirmed(self, cmd: int, name: str, attempts: int, source: str) -> None:
        self.events.publish("stop.confirmed", StopConfirmation(name, source, attempts,
                                                               self.clock.monotonic_ns(), True))

    def _stop_unconfirmed(self, cmd: int, name: str, attempts: int, source: str) -> None:
        self.events.publish("stop.unconfirmed", StopConfirmation(name, source, attempts,
                                                                 self.clock.monotonic_ns(), False))
        self.events.log(f"{name} not confirmed within 1 s — use the physical STOP / E-stop", logging.ERROR)

    # ============================================================================== clears / resume (VERIFY)
    _CLEAR_EVENTS = {Cmd.HALT_CLEAR: (pg.Event.HALT_SET, pg.Event.PAUSED), Cmd.ESTOP_CLEAR: (pg.Event.ESTOP_SET,),
                     Cmd.FAULT_CLEAR: (pg.Event.FAULT_SET,), Cmd.RESUME: (pg.Event.PAUSED,)}

    def clear_async(self, cmd: Cmd) -> ReleasingFuture:
        """HALT_CLEAR / ESTOP_CLEAR / FAULT_CLEAR / RESUME: **one** frame, never re-sent (VERIFY class, D-31, D-34).

        The clears are written at once on the priority path from the caller's thread (D-34, SWD-M1-04) — never
        behind the Worker queue, lanes or the token bucket; RESUME is submitted at once on the CONTROL lane
        (Orchestrator decision, ICD §2.4). The outcome is resolved by callbacks on the Reader / Supervisor tick: ACK
        → OK, NACK → REFUSED, timeout → one GET_STATUS decides (latch gone and no new latch event since →
        confirmed). ``ClearResult.cleared`` names the latches that were set before (GF-21)."""
        out = ReleasingFuture()
        name = cmd.name
        ch = self.channel
        if ch is None or self.transport is None or not self.connected:
            out.set_result(ClearResult(name, False, False, "NOT_CONFIRMED", text="not connected"))
            return out
        watch = self._CLEAR_EVENTS[cmd]
        ev0 = {int(e): self.event_counts.get(int(e), 0) for e in watch}
        flags0, status0 = self.latest()
        faults0 = self.board.faults if self.board else 0
        before = self._latched_names(cmd, flags0, status0, faults0)
        try:
            fut = ch.submit(cmd)
        except Exception as exc:  # noqa: BLE001 — never raises to the GUI
            out.set_result(ClearResult(name, False, False, "NOT_CONFIRMED", text=str(exc)))
            return out

        def finish(res: ClearResult) -> None:
            if not out.done():
                out.set_result(res)
            self._poll_now()                     # refresh latches / sys_flags for the indicators

        def on_status(f: Any) -> None:
            if f.exception() is not None or f.result() is None:
                finish(ClearResult(name, True, False, "NOT_CONFIRMED", text=f"{name} not confirmed — click again"))
                return
            st = P.decode_status(f.result().body)
            self._apply_status(st, f.result().t_host_ns)
            new_latch = any(self.event_counts.get(e, 0) > n for e, n in ev0.items())
            if cmd == Cmd.HALT_CLEAR:
                done = not st.flags & pg.DataFlags.HALT and not st.status & pg.DataStatus.PAUSED
            elif cmd == Cmd.ESTOP_CLEAR:
                done = not st.flags & pg.DataFlags.ESTOP
            elif cmd == Cmd.RESUME:
                done = not st.status & pg.DataStatus.PAUSED
            else:
                done = (faults0 & st.faults) != faults0 or faults0 == 0
            if done and not new_latch:
                cleared = (bit_names(pg.FAULTS_BITS, faults0 & ~st.faults) if cmd == Cmd.FAULT_CLEAR else before)
                finish(ClearResult(name, True, True, "OK", cleared, "confirmed by GET_STATUS"))
                return
            text = ("Resume not confirmed — press Resume again" if cmd == Cmd.RESUME
                    else "Clear not confirmed — click again")
            finish(ClearResult(name, True, False, "NOT_CONFIRMED", text=text))

        def on_resp(f: Any) -> None:
            exc = f.exception()
            if exc is None:
                r = f.result()
                cleared = (bit_names(pg.FAULTS_BITS, P.decode_u16(r.body)) if cmd == Cmd.FAULT_CLEAR and r is not None
                           else before)
                finish(ClearResult(name, True, True, "OK", cleared, ""))
            elif isinstance(exc, NackError):
                finish(ClearResult(name, True, False, "REFUSED", text=exc.detail_text, nack_status=exc.status,
                                   nack_detail=exc.detail))
            elif isinstance(exc, CommandTimeout) and self.channel is not None:
                self.channel.submit(Cmd.GET_STATUS).add_done_callback(on_status)
            else:
                finish(ClearResult(name, bool(fut.done() and not isinstance(exc, TransportError)), False,
                                   "NOT_CONFIRMED", text=f"{name}: {exc}"))
        fut.add_done_callback(on_resp)
        return out

    @staticmethod
    def _latched_names(cmd: Cmd, flags: int, status: int, faults: int) -> tuple[str, ...]:
        if cmd == Cmd.HALT_CLEAR:
            return tuple(n for n, on in (("HALT", flags & pg.DataFlags.HALT), ("PAUSED", status & pg.DataStatus.PAUSED))
                         if on)
        if cmd == Cmd.ESTOP_CLEAR:
            return ("ESTOP",) if flags & pg.DataFlags.ESTOP else ()
        if cmd == Cmd.RESUME:
            return ("PAUSED",) if status & pg.DataStatus.PAUSED else ()
        return bit_names(pg.FAULTS_BITS, faults)

    # ============================================================================== FW events (pipeline thread)
    def handle_fw_event(self, ev: P.EventPayload) -> None:
        """Link-level reactions to FW EVENTs (in arrival order with DATA; called by the pipeline)."""
        self.event_counts[ev.code] = self.event_counts.get(ev.code, 0) + 1
        if ev.code == pg.Event.HALT_SET:
            self.halt_src_ev = ev.arg
        elif ev.code == pg.Event.HALT_CLEARED:
            self.halt_src_ev = None
        elif ev.code == pg.Event.PAUSED:
            self.pause_src_ev = ev.arg
        elif ev.code == pg.Event.PAUSE_CLEARED:
            self.pause_src_ev = None
        elif ev.code == pg.Event.BOOT:
            self.halt_src_ev = self.pause_src_ev = None
        ch = self.channel
        if ev.code in (pg.Event.STOPPED, pg.Event.ESTOP_SET, pg.Event.HALT_SET, pg.Event.PAUSED,
                       pg.Event.FAULT_SET, pg.Event.LIMIT_SET, pg.Event.LINK_WDG, pg.Event.HOME_FAILED,
                       pg.Event.DRIVER_DISABLED) or (ev.code == pg.Event.DRIVER_POWER and ev.arg == 0):
            if ch is not None:
                ch.bump_epoch()
        if self._sync_active:            # a (re)connect / resync is reading everything anyway
            return
        if ev.code == pg.Event.BOOT and self.connected and self.submit_job is not None:
            self.events.log(f"board reset ({P.event_arg_name(ev.code, ev.arg)})", logging.WARNING)
            self.submit_job(self._resync_after_boot_job)
        if ev.code in (pg.Event.PARAMS_LOADED, pg.Event.PARAMS_DEFAULTED) and self.submit_job is not None \
                and self.connected:
            self.submit_job(self.read_all_params_job)

    def _resync_after_boot_job(self) -> Job:
        """EVENT BOOT: connect steps 4–8 again; no motion, no re-enable (§5.5.1)."""
        self.stream_on = False
        self._sync_active = True
        try:
            yield from self._sync_job(self.settings.stream_on_connect)
        finally:
            self._sync_active = False
        return None

    # ============================================================================== supervisor tick (§4.6)
    def tick(self, now: int) -> None:
        ch, conf, writer = self.channel, self.confirmer, self.writer
        if ch is not None:
            ch.tick(now)
        if conf is not None:
            conf.tick(now)
        if self.state == LinkState.LOST:
            self._maybe_reconnect(now)
            return
        if ch is None or writer is None or not self.connected:
            return
        # heartbeat (SAF-SW-003): PING after 150 ms TX idle, only while the backend pipeline is alive
        if self.settings.heartbeat and now - writer.last_tx_ns >= HEARTBEAT_IDLE_NS and self.liveness.gate_open(now) \
                and (self._hb_pending is None or self._hb_pending.done()):
            self._hb_pending = ch.submit(Cmd.PING, key="heartbeat")
        # link state
        rd = self.reader
        rx_age = None if rd is None or rd.last_rx_ns is None else max(0, now - rd.last_rx_ns)
        new = link_state_for(ch.consecutive_timeouts, rx_age, self.stream_on)
        if new == LinkState.LOST:
            self._next_reconnect_ns = now + RECONNECT_PERIOD_NS
        if new != self.state:
            self._set_state(new, {LinkState.DEGRADED: "timeouts / no frames", LinkState.LOST: "no frames for 1 s",
                                  LinkState.CONNECTED: ""}[new])
        # DATA loss while moving → STOP (SAF-SW-003)
        if self.stream_on and self.last_flags & pg.DataFlags.MOVING and self.last_data_ns is not None and \
                now - self.last_data_ns > DATA_LOSS_MOVING_NS and not self._data_loss_stop_sent:
            self._data_loss_stop_sent = True
            self.stop(pg.StopMode.IMMEDIATE, "LINK_LOST")
            self.events.log("LINK LOST while moving — STOP sent", logging.ERROR)
        # STATUS poll (1 Hz streaming / 4 Hz stream off)
        if now >= self._next_poll_ns and (self._poll_pending is None or self._poll_pending.done()):
            self._next_poll_ns = now + (STATUS_POLL_STREAM_NS if self.stream_on else STATUS_POLL_IDLE_NS)
            self._poll_now()

    def _poll_now(self) -> None:
        ch = self.channel
        if ch is None:
            return
        fut = ch.submit(Cmd.GET_STATUS, key="status_poll")
        self._poll_pending = fut

        def done(f: Any) -> None:
            if f.exception() is None and f.result() is not None:
                r = f.result()
                try:
                    self._apply_status(P.decode_status(r.body), r.t_host_ns)
                except ValueError:
                    pass
        fut.add_done_callback(done)

    def _maybe_reconnect(self, now: int) -> None:
        if not self.settings.auto_reconnect or self._user_disconnected or self.endpoint is None \
                or self.submit_job is None or self._reconnecting or now < self._next_reconnect_ns:
            return
        self._next_reconnect_ns = now + RECONNECT_PERIOD_NS
        self._reconnecting = True
        ep = self.endpoint
        fut = self.submit_job(lambda: self.connect_job(ep))

        def done(_f: Any) -> None:
            self._reconnecting = False
        fut.add_done_callback(done)

    # ============================================================================== stats
    def link_stats(self, base: LinkStats) -> LinkStats:
        """Merge decoder / reader / channel / FW counters into the pipeline's counters."""
        rd, ch, st = self.reader, self.channel, self.board
        kw: dict[str, Any] = {}
        if rd is not None:
            c = rd.decoder.counters
            kw.update(frames_ok=c.frames_ok, crc_errors=c.crc_errors, len_errors=c.len_errors,
                      timeout_drops=c.timeout_drops, unknown_type=rd.stats.unknown_type,
                      response_frames=rd.stats.responses)
        if ch is not None:
            s = ch.stats
            kw.update(late_responses=s.late_responses, command_timeouts=s.timeouts, nacks=s.nacks,
                      retries=s.retries_sent, tx_frames=s.sent)
        if st is not None:
            kw.update(fw_rx_frames_ok=st.rx_frames_ok, fw_rx_crc_errors=st.rx_crc_errors,
                      fw_rx_frame_errors=st.rx_frame_errors, fw_rx_overruns=st.rx_overruns, fw_tx_drops=st.tx_drops,
                      fw_event_overflows=st.event_overflows)
        return replace(base, **kw)


def _raise_priority() -> None:
    from bend_stand.core.timing import raise_thread_priority  # noqa: PLC0415

    raise_thread_priority(1)


__all__ = ["Device", "DeviceSettings", "IncompatibleFirmware", "Sleep"]

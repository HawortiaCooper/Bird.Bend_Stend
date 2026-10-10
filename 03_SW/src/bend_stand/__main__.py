"""Entry point: ``python -m bend_stand [--sim[=scenario.json]] [--port COM7 | --port tcp://host:port]
[--session file] [--recordings DIR] [--headless [--duration s] [--record]]``, the subcommand
``python -m bend_stand report <recording> [...]`` and the script ``bend-stand`` (also the frozen
``BirdBendStand.exe`` / ``BirdBendStand-cli.exe``, SW_design §24).

Owner: Implementer B (D-29 n). Without ``--headless`` the Backend is built **unstarted** and handed to
``bend_stand.gui.app.run(backend, args)`` (owner D, B3-20), which starts it, connects to ``args.endpoint``
if given and shuts it down on exit. ``--headless`` connects, streams and prints link statistics (M1 smoke
test, no Qt import); with ``--record`` it also records the stream for ``--duration`` and prints the folder.
``report`` rebuilds a test report offline from a recording folder: it hands the remaining arguments to the public
entry ``bend_stand.core.report.main`` (same options as ``python -m bend_stand.core.report``), so the installed
application can rebuild reports without a Python installation (OI-UM-03).

D-06: no COM port is ever opened unless the operator selected it with ``--port COMx`` (or in the GUI).
Without ``--port``/``--sim`` the GUI starts disconnected and ``--headless`` refuses to run.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/__main__.py @37c87471 (adapted: endpoint argument,
unstarted Backend hand-off to gui.app.run, headless smoke mode).

Implements: SW-PLT-001, SW-PLT-003 (headless connect), SYS-008 (--sim), SW-REP-003 (offline report rebuild),
SW-ACQ-001 (--headless --record), SW-PLT-001 / SWR-14 (log file + incident log for every run, ``core.logfile``)
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from typing import TYPE_CHECKING

from bend_stand import ICD_VERSION, PROTO_VERSION, __version__

if TYPE_CHECKING:
    from bend_stand.core.backend import BackendSettings


#: subcommands dispatched before the option parser (first argument)
REPORT_SUBCOMMAND = "report"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m bend_stand", description="Bird Bend Stand PC application",
        epilog=f"subcommand: {REPORT_SUBCOMMAND} <recording folder> [--cal FILE] [--tare-raw N] [--bend3p L b h] "
               f"[--out DIR] - rebuild a test report offline (see '{REPORT_SUBCOMMAND} --help')")
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    ap.add_argument("--sim", nargs="?", const="", default=None, metavar="SCENARIO",
                    help="use the in-process FW simulator (optional scenario file *.simscn.json)")
    ap.add_argument("--port", default=None,
                    help="COM port (e.g. COM7) or tcp://host:port (FW host twin / out-of-process simulator)")
    ap.add_argument("--session", default=None, help="session file to open (*.bbsession.json)")
    ap.add_argument("--recordings", default=None, metavar="DIR",
                    help="folder for recordings and reports (default: recordings_root of the session, "
                         "else Documents\\BirdBendStand\\recordings)")
    ap.add_argument("--headless", action="store_true",
                    help="no GUI: connect, stream and print link statistics (smoke test)")
    ap.add_argument("--duration", type=float, default=5.0, help="--headless run time in seconds (default 5)")
    ap.add_argument("--record", action="store_true",
                    help="--headless: record the stream for --duration and print the recording folder")
    ap.add_argument("--log-level", default="WARNING", help="logging level (DEBUG, INFO, WARNING, ERROR)")
    return ap


def endpoint_from_args(args: argparse.Namespace) -> str | None:
    """``"sim"``, ``"sim:<scenario>"``, ``"COM7"``, ``"tcp://host:port"`` or ``None`` (start disconnected)."""
    if args.sim is not None and args.port:
        raise SystemExit("--sim and --port are mutually exclusive")
    if args.sim is not None:
        return "sim" if args.sim == "" else f"sim:{args.sim}"
    return args.port or None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_arg_parser().parse_args(argv)
    args.endpoint = endpoint_from_args(args)
    return args


def backend_settings(args: argparse.Namespace) -> BackendSettings:
    from bend_stand.core.backend import BackendSettings  # noqa: PLC0415

    return BackendSettings(session_path=args.session, recordings_root=getattr(args, "recordings", None))


def run_report(argv: list[str]) -> int:
    """``report <recording> [...]``: offline report rebuild through the public ``core.report.main`` (SW-REP-003)."""
    from bend_stand.core import report  # noqa: PLC0415

    return int(report.main(argv) or 0)


def run_headless(args: argparse.Namespace) -> int:
    """Connect, stream for ``args.duration`` s (recording it with ``--record``), print link statistics; exit 0
    when DATA arrived (and, with ``--record``, the recording was written)."""
    if args.endpoint is None:
        print("bend_stand: --headless requires --sim or --port", file=sys.stderr)
        return 2
    from bend_stand.core.backend import Backend  # noqa: PLC0415
    from bend_stand.core.logfile import attach_incident_log  # noqa: PLC0415

    backend = Backend(backend_settings(args))
    attach_incident_log(backend.events)          # SWR-14
    backend.start()
    rec_ok = True
    try:
        info = backend.connect_async(args.endpoint).result(timeout=10.0)
        print(f"connected: {args.endpoint}  fw {'.'.join(map(str, info.fw_version))}  build {info.build!r}  "
              f"uid {info.uid}  proto {info.proto_major}.{info.proto_minor}")
        folder = None
        if getattr(args, "record", False):
            g = backend.record_start()
            if not g.ok:
                print("recording refused: " + "; ".join(i.text for i in g.refused), file=sys.stderr)
                rec_ok = False
            folder = backend.status().recording.folder
        t_end = time.monotonic() + max(0.0, float(args.duration))
        while time.monotonic() < t_end:
            backend.gui_beat()
            time.sleep(0.05)
        if folder is not None:
            rows = backend.status().recording.rows
            backend.record_stop()
            print(f"recording: {folder}  rows {rows}")
            rec_ok = rows > 0
        st = backend.status()
        s = st.link.stats
        print(f"link state {st.link.state.value}  stream {'on' if st.stream.on else 'off'}  "
              f"rate {st.stream.rate_sps if st.stream.rate_sps is not None else float('nan'):.2f} SPS")
        print(f"frames ok {s.frames_ok}  data {s.data_frames}  events {s.event_frames}  "
              f"lost fw {s.frames_lost_fw}  lost link {s.frames_lost_link}  dup {s.dup_frames}  "
              f"crc {s.crc_errors}  len {s.len_errors}  timeout {s.timeout_drops}  "
              f"cmd timeouts {s.command_timeouts}  late {s.late_responses}")
        return 0 if s.data_frames > 0 and rec_ok else 1
    finally:
        backend.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == REPORT_SUBCOMMAND:
        return run_report(argv[1:])
    args = parse_args(argv)
    if args.version:
        print(f"bend_stand {__version__} (ICD {ICD_VERSION}, PROTO {PROTO_VERSION[0]}.{PROTO_VERSION[1]})")
        return 0
    from bend_stand.core import logfile  # noqa: PLC0415

    try:
        try:                                     # SWR-31: a log set-up failure of any kind never blocks the start
            logfile.setup_logging(args.log_level)    # SWR-14: console + rotating <data>/logs/bend_stand.log
        except Exception as exc:  # noqa: BLE001
            print(f"bend_stand: log file set-up failed ({exc}); console logging only", file=sys.stderr)
        if args.headless:
            return run_headless(args)
        return run_gui(args)
    finally:
        logfile.shutdown_logging()


def run_gui(args: argparse.Namespace) -> int:
    try:
        app = importlib.import_module("bend_stand.gui.app")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("bend_stand.gui"):
            print("bend_stand: GUI not available yet (bend_stand.gui.app missing); use --headless.",
                  file=sys.stderr)
            return 2
        raise
    from bend_stand.core.backend import Backend  # noqa: PLC0415
    from bend_stand.core.logfile import attach_incident_log  # noqa: PLC0415

    backend = Backend(backend_settings(args))   # unstarted (B3-20)
    attach_incident_log(backend.events)          # SWR-14: stops, latches, link loss, faults → log file
    return int(app.run(backend, args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Build metadata and post-build steps of the Windows distribution (SW_design §24, packaging/README.md).

Used by ``BirdBendStand.spec`` (PyInstaller) and ``build_dist.ps1``; stdlib only, no ``bend_stand`` import, so it
runs in the build environment before anything is frozen.

- **Version**: ``[project].version`` of ``03_SW/pyproject.toml`` + the short git hash of HEAD (PEP 440 local label)
  -> ``0.1.0+ge600169`` (``.dirty`` appended when the packaged sources differ from HEAD). The frozen app reads it back
  through ``importlib.metadata`` from a generated ``bend_stand-<version>.dist-info`` (``bend_stand.__version__``),
  so the source package needs no build-time file.
- **Windows version resource** (``VSVersionInfo`` text for ``EXE(version=...)``), **dist folder / zip name**
  ``BirdBendStand-0.1.0-ge600169[-dirty]``, ``BUILD_INFO.txt`` and a deterministic zip of the dist folder.

CLI (``.venv\\Scripts\\python 03_SW\\packaging\\buildinfo.py ...``)::

    info [--out build_info.json]          collect and print (or write) the build info as JSON
    check-env --req 03_SW/requirements-build.lock.txt   installed versions == pins (lock or ``-r`` files), else exit 1
    finalize --info build_info.json --dist <dist folder>   docs (manuals + HTML, docs_html.py) and BUILD_INFO.txt
                                                           (incl. the frozen exe's ``--version``)
    zip --info build_info.json --dist <dist folder>        deterministic ``<dist folder>.zip``

Implements: SW-PLT-001 (installable Windows distribution, version identification)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
SW_ROOT = PACKAGING.parent                      # 03_SW
REPO_ROOT = SW_ROOT.parent                      # Bird.Bend_Stend
APP_NAME = "BirdBendStand"
PRODUCT_NAME = "Bird Bend Stand"
COMPANY = "Bird Bend Stand project"
#: paths whose state decides the ``dirty`` flag (everything that ends up in the bundle)
PACKAGED_PATHS = ("03_SW/src", "03_SW/packaging", "03_SW/pyproject.toml", "03_SW/requirements.txt",
                  "03_SW/requirements-build.txt", "03_SW/requirements.lock.txt", "03_SW/requirements-build.lock.txt",
                  "03_SW/docs/USER_MANUAL.md", "03_SW/docs/QUICK_REFERENCE.md", "03_SW/docs/img")

_BASE_RE = re.compile(r"^\d+(\.\d+){0,3}$")
_HASH_RE = re.compile(r"^[0-9a-f]{4,40}$|^nogit$")


@dataclass(frozen=True)
class BuildInfo:
    base_version: str          # pyproject [project].version, e.g. "0.1.0"
    git_hash: str              # short hash of HEAD, or "nogit"
    dirty: bool                # packaged sources differ from HEAD (or untracked files in them)
    commit_time: int           # HEAD commit time (unix s); used as SOURCE_DATE_EPOCH, 0 if unknown
    build_time: str            # ISO-8601 UTC of the build
    python: str                # build interpreter version (= the frozen runtime)

    def __post_init__(self) -> None:
        if not _BASE_RE.match(self.base_version):
            raise ValueError(f"base version {self.base_version!r} is not N[.N[.N[.N]]]")
        if not _HASH_RE.match(self.git_hash):
            raise ValueError(f"git hash {self.git_hash!r} is not a hex hash")

    @property
    def version(self) -> str:
        """PEP 440 version with a local label: ``0.1.0+ge600169`` / ``0.1.0+ge600169.dirty``."""
        local = "nogit" if self.git_hash == "nogit" else f"g{self.git_hash}"
        return f"{self.base_version}+{local}{'.dirty' if self.dirty else ''}"

    @property
    def dist_name(self) -> str:
        """Folder / zip stem: ``BirdBendStand-0.1.0-ge600169`` (``-dirty`` appended)."""
        return f"{APP_NAME}-{self.version.replace('+', '-').replace('.dirty', '-dirty')}"

    @property
    def file_version(self) -> tuple[int, int, int, int]:
        parts = [int(p) for p in self.base_version.split(".")]
        return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]

    def to_json(self) -> str:
        d = asdict(self)
        d.update(version=self.version, dist_name=self.dist_name, file_version=".".join(map(str, self.file_version)))
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "BuildInfo":
        d = json.loads(text)
        return cls(**{k: d[k] for k in ("base_version", "git_hash", "dirty", "commit_time", "build_time", "python")})


# ------------------------------------------------------------------------------------------------ collect

def read_base_version(pyproject: Path = SW_ROOT / "pyproject.toml") -> str:
    return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])


def _git(*args: str, cwd: Path = REPO_ROOT) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_state(repo: Path = REPO_ROOT) -> tuple[str, bool, int]:
    """(short hash | "nogit", dirty, commit time)."""
    h = _git("rev-parse", "--short=7", "HEAD", cwd=repo)
    if not h:
        return "nogit", True, 0
    status = _git("status", "--porcelain", "--", *PACKAGED_PATHS, cwd=repo)
    ct = _git("log", "-1", "--format=%ct", cwd=repo)
    return h.lower(), bool(status), int(ct) if ct and ct.isdigit() else 0


def collect(repo: Path = REPO_ROOT, pyproject: Path = SW_ROOT / "pyproject.toml") -> BuildInfo:
    h, dirty, ct = git_state(repo)
    return BuildInfo(base_version=read_base_version(pyproject), git_hash=h, dirty=dirty, commit_time=ct,
                     build_time=_dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
                     python=platform.python_version())


def load_or_collect(path: str | os.PathLike[str] | None) -> BuildInfo:
    """The JSON written by ``info --out`` (one consistent version per build run), else a fresh collect."""
    if path and Path(path).is_file():
        return BuildInfo.from_json(Path(path).read_text(encoding="utf-8"))
    return collect()


# ------------------------------------------------------------------------------------------------ PyInstaller inputs

def version_file_text(info: BuildInfo, exe_name: str, description: str) -> str:
    """``VSVersionInfo`` source as accepted by PyInstaller ``EXE(version=<file>)``."""
    fv = info.file_version
    strings = {
        "CompanyName": COMPANY,
        "FileDescription": description,
        "FileVersion": info.version,
        "InternalName": APP_NAME,
        "LegalCopyright": "Proprietary",
        "OriginalFilename": exe_name,
        "ProductName": PRODUCT_NAME,
        "ProductVersion": info.version,
        "Comments": f"git {info.git_hash}{' (dirty)' if info.dirty else ''}, built {info.build_time}",
    }
    items = ",\n            ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    return f"""# UTF-8 — generated by 03_SW/packaging/buildinfo.py, do not edit
VSVersionInfo(
  ffi=FixedFileInfo(filevers={fv}, prodvers={fv}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
            {items}])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_file(path: Path, info: BuildInfo, exe_name: str, description: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version_file_text(info, exe_name, description), encoding="utf-8")
    return path


def write_dist_info(parent: Path, info: BuildInfo) -> Path:
    """Minimal ``bend_stand-<version>.dist-info`` so ``importlib.metadata.version("bend_stand")`` works frozen."""
    d = parent / f"bend_stand-{info.version}.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: bend_stand\nVersion: {info.version}\n"
        f"Summary: {PRODUCT_NAME} PC application (frozen Windows distribution)\n", encoding="utf-8")
    (d / "INSTALLER").write_text("pyinstaller\n", encoding="utf-8")
    return d


# ------------------------------------------------------------------------------------------------ environment

def read_pins(req: Path) -> dict[str, str]:
    """``name==version`` pins of a requirements file, following ``-r other.txt`` (comments / blanks ignored); also
    reads the hash-pinned lock files (``name==version \\`` + ``--hash=…`` continuation lines, SWR-27)."""
    pins: dict[str, str] = {}
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line or line.startswith("--hash"):
            continue
        if line.startswith("-r"):
            pins.update(read_pins((req.parent / line[2:].strip()).resolve()))
            continue
        if "==" not in line:
            raise ValueError(f"{req.name}: {line!r} is not pinned (name==version)")
        name, ver = line.split("==", 1)
        pins[_norm(name)] = ver.split()[0].strip() if ver.strip() else ver
    return pins


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def check_env(req: Path) -> list[str]:
    """Mismatches between the pins of ``req`` and the running interpreter's installed distributions."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    out = []
    for name, want in sorted(read_pins(req).items()):
        try:
            have = version(name)
        except PackageNotFoundError:
            out.append(f"{name}: not installed (pinned {want})")
            continue
        if have != want:
            out.append(f"{name}: installed {have}, pinned {want}")
    return out


# ------------------------------------------------------------------------------------------------ finalize

def write_build_info_txt(dist: Path, info: BuildInfo, extra: dict[str, str] | None = None) -> Path:
    lines = [f"{PRODUCT_NAME} — Windows distribution", "",
             f"version        {info.version}", f"git            {info.git_hash}{' (dirty)' if info.dirty else ''}",
             f"built (UTC)    {info.build_time}", f"python         {info.python} (frozen, PyInstaller one-folder)"]
    for k, v in (extra or {}).items():
        lines.append(f"{k:<14} {v}")
    lines += ["", "Start:   BirdBendStand.exe            (GUI, starts disconnected — no COM port is opened until you",
              "                                       select one and press Connect)",
              "         BirdBendStand.exe --sim      (GUI with the built-in FW simulator, no hardware)",
              "         BirdBendStand-cli.exe --headless --sim   (console smoke test, prints link statistics)",
              "Data:    %APPDATA%\\BirdBendStand  (calibration, sessions, presets, gui.ini, logs)",
              "Records: Documents\\BirdBendStand\\recordings by default; another folder: \"recordings_root\" in",
              "         %APPDATA%\\BirdBendStand\\sessions\\default.bbsession.json (or a --session file)",
              "Log:     %APPDATA%\\BirdBendStand\\logs\\BirdBendStand.log (output and crashes of BirdBendStand.exe)",
              "Docs:    docs\\USER_MANUAL.html (operator manual), docs\\QUICK_REFERENCE.html (one-page card)",
              "Report:  BirdBendStand-cli.exe report <recording folder> [--cal F] [--tare-raw N] [--bend3p L b h]",
              "         [--out DIR]   (rebuild a test report offline, no board needed)",
              ""]
    p = dist / "BUILD_INFO.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def zip_dist(dist: Path, info: BuildInfo, out: Path | None = None) -> Path:
    """Deterministic zip (sorted entries, fixed timestamps = HEAD commit time) with the folder as top level."""
    out = out or dist.parent / f"{dist.name}.zip"   # not with_suffix: the name contains dots
    if out.exists():
        out.unlink()
    epoch = max(info.commit_time, 315532800)  # zip cannot store dates before 1980
    stamp = _dt.datetime.fromtimestamp(epoch, _dt.UTC).timetuple()[:6]
    files = sorted((p for p in dist.rglob("*") if p.is_file()), key=lambda p: p.relative_to(dist).as_posix())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in files:
            zi = zipfile.ZipInfo(f"{dist.name}/{f.relative_to(dist).as_posix()}",
                                 date_time=stamp)  # type: ignore[arg-type]
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with f.open("rb") as src, zf.open(zi, "w") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
    return out


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def frozen_version_line(dist: Path) -> str:
    """``BirdBendStand-cli.exe --version`` of the built folder (D-06 guard on, private data folder)."""
    env = dict(os.environ, BEND_STAND_D06_GUARD="1", BEND_STAND_HOTKEY="off")
    try:
        r = subprocess.run([str(dist / f"{APP_NAME}-cli.exe"), "--version"], capture_output=True, text=True,
                           timeout=60, env=env, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(cannot run: {exc})"
    return (r.stdout.strip() or r.stderr.strip() or f"(rc {r.returncode}, no output)").splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="buildinfo.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("info")
    a.add_argument("--out", default=None)
    c = sub.add_parser("check-env")
    c.add_argument("--req", default=str(SW_ROOT / "requirements-build.lock.txt"))
    for name in ("finalize", "zip"):
        f = sub.add_parser(name)
        f.add_argument("--info", required=True)
        f.add_argument("--dist", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "info":
        info = collect()
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(info.to_json(), encoding="utf-8")
        print(info.to_json())
        return 0
    if args.cmd == "check-env":
        bad = check_env(Path(args.req))
        for line in bad:
            print(f"check-env: {line}", file=sys.stderr)
        print(f"check-env: {len(read_pins(Path(args.req)))} pins, {len(bad)} mismatch(es)")
        return 1 if bad else 0
    info = load_or_collect(args.info)
    dist = Path(args.dist)
    if not (dist / f"{APP_NAME}.exe").is_file():
        print(f"buildinfo: {dist} has no {APP_NAME}.exe", file=sys.stderr)
        return 2
    if args.cmd == "finalize":
        from importlib.metadata import version  # noqa: PLC0415

        if str(PACKAGING) not in sys.path:
            sys.path.insert(0, str(PACKAGING))
        import docs_html  # noqa: PLC0415  (03_SW/packaging/docs_html.py)

        docs = docs_html.install_docs(dist)
        print(f"docs   {len(docs)} files -> {dist / 'docs'}")
        extra = {"frozen exe": frozen_version_line(dist), "pyinstaller": version("pyinstaller"),
                 "requirements": "03_SW/requirements-build.lock.txt (hash-pinned; venv checked by build_dist.ps1)"}
        write_build_info_txt(dist, info, extra)
        files = [p for p in dist.rglob("*") if p.is_file()]
        print(f"dist   {dist}  {sum(p.stat().st_size for p in files) / 2**20:.1f} MiB  {len(files)} files")
        print(f"exe    {extra['frozen exe']}")
        return 0
    z = zip_dist(dist, info)
    print(f"zip    {z}  {z.stat().st_size / 2**20:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

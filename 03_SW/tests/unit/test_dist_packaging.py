"""Windows distribution helpers (``03_SW/packaging``, SW_design §24): version stamping, requirement pins, deterministic
zip, frozen-app metadata, icon, the frozen-exe runtime hook (windowed log redirect, D-06 guard) and git-ignore.

The built exe itself is exercised by ``packaging/smoke_dist.py`` (run by ``build_dist.ps1``), not here.

Verifies: SW-PLT-001
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import types
import zipfile
from pathlib import Path

import pytest

SW_ROOT = Path(__file__).resolve().parents[2]
PACK = SW_ROOT / "packaging"


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"bbs_packaging_{name}", PACK / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                      # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


bi = _load("buildinfo")


def _info(**kw: object) -> object:
    base = dict(base_version="0.1.0", git_hash="e600169", dirty=False, commit_time=1791201257,
                build_time="2026-10-08T00:00:00+00:00", python="3.14.7")
    base.update(kw)
    return bi.BuildInfo(**base)


@pytest.mark.req("SW-PLT-001")
@pytest.mark.parametrize("kw, version, dist", [
    ({}, "0.1.0+ge600169", "BirdBendStand-0.1.0-ge600169"),
    ({"dirty": True}, "0.1.0+ge600169.dirty", "BirdBendStand-0.1.0-ge600169-dirty"),
    ({"git_hash": "nogit", "dirty": True}, "0.1.0+nogit.dirty", "BirdBendStand-0.1.0-nogit-dirty"),
    ({"base_version": "1.2"}, "1.2+ge600169", "BirdBendStand-1.2-ge600169"),
])
def test_version_and_dist_name(kw: dict, version: str, dist: str) -> None:
    info = _info(**kw)
    assert info.version == version and info.dist_name == dist
    assert re.fullmatch(r"\d+(\.\d+)*\+[a-z0-9]+(\.[a-z0-9]+)*", info.version)      # PEP 440 local version
    assert len(info.file_version) == 4 and all(isinstance(x, int) for x in info.file_version)
    assert bi.BuildInfo.from_json(info.to_json()) == info


@pytest.mark.req("SW-PLT-001")
@pytest.mark.parametrize("kw", [{"base_version": "0.1.0rc1"}, {"base_version": ""}, {"git_hash": "XYZ"}])
def test_invalid_build_info_rejected(kw: dict) -> None:
    with pytest.raises(ValueError):
        _info(**kw)


@pytest.mark.req("SW-PLT-001")
def test_base_version_is_pyproject_version() -> None:
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert bi.read_base_version(SW_ROOT / "pyproject.toml") == py["project"]["version"]


@pytest.mark.req("SW-PLT-001")
def test_version_resource_text_is_python_and_stamped() -> None:
    info = _info(dirty=True)
    text = bi.version_file_text(info, "BirdBendStand.exe", "desc")
    compile(text, "version.txt", "eval")                     # PyInstaller evaluates it as one expression
    assert "'FileVersion', '0.1.0+ge600169.dirty'" in text and "filevers=(0, 1, 0, 0)" in text
    assert "'OriginalFilename', 'BirdBendStand.exe'" in text


@pytest.mark.req("SW-PLT-001")
def test_dist_info_makes_version_readable(tmp_path: Path) -> None:
    d = bi.write_dist_info(tmp_path, _info())
    dist = importlib.metadata.PathDistribution(d)
    assert dist.metadata["Name"] == "bend_stand" and dist.version == "0.1.0+ge600169"
    assert d.name == "bend_stand-0.1.0+ge600169.dist-info"


@pytest.mark.req("SW-PLT-001")
def test_build_requirements_pinned_and_consistent() -> None:
    build = bi.read_pins(SW_ROOT / "requirements-build.txt")
    runtime = bi.read_pins(SW_ROOT / "requirements.txt")
    assert runtime.items() <= build.items()                  # -r requirements.txt followed, same pins
    assert {"pyinstaller", "pyinstaller-hooks-contrib"} <= set(build)
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = {re.split(r"[<>=!~ ]", d, maxsplit=1)[0].lower(): d
             for d in py["project"]["optional-dependencies"]["build"]}
    pin = tuple(int(x) for x in build["pyinstaller"].split("."))
    lo = re.search(r">=\s*([0-9.]+)", extra["pyinstaller"])
    hi = re.search(r"<\s*([0-9.]+)", extra["pyinstaller"])
    assert lo and pin >= tuple(int(x) for x in lo.group(1).split("."))
    assert hi and pin < tuple(int(x) for x in hi.group(1).split("."))


@pytest.mark.req("SW-PLT-001")
def test_read_pins_rejects_unpinned(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("numpy==2.5.3\n", encoding="utf-8")
    (tmp_path / "r.txt").write_text("# c\n-r base.txt\nPyInstaller == 6.22.3  # x\n", encoding="utf-8")
    assert bi.read_pins(tmp_path / "r.txt") == {"numpy": "2.5.3", "pyinstaller": "6.22.3"}
    (tmp_path / "bad.txt").write_text("pyinstaller>=6\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bi.read_pins(tmp_path / "bad.txt")


@pytest.mark.req("SW-PLT-001")
def test_zip_is_deterministic_and_named_after_folder(tmp_path: Path) -> None:
    dist = tmp_path / "BirdBendStand-0.1.0-ge600169"
    (dist / "_internal" / "sub").mkdir(parents=True)
    (dist / "BirdBendStand.exe").write_bytes(b"MZ" + bytes(100))
    (dist / "_internal" / "sub" / "a.txt").write_text("a" * 5000, encoding="utf-8")
    z1 = bi.zip_dist(dist, _info())
    assert z1 == tmp_path / "BirdBendStand-0.1.0-ge600169.zip"       # not with_suffix (dots in the name)
    b1 = z1.read_bytes()
    os.utime(dist / "BirdBendStand.exe", (1, 1))                     # file times do not matter
    assert bi.zip_dist(dist, _info()).read_bytes() == b1
    with zipfile.ZipFile(z1) as zf:
        assert zf.namelist() == ["BirdBendStand-0.1.0-ge600169/BirdBendStand.exe",
                                 "BirdBendStand-0.1.0-ge600169/_internal/sub/a.txt"]
        assert zf.testzip() is None


@pytest.mark.req("SW-PLT-001")
def test_build_info_txt(tmp_path: Path) -> None:
    p = bi.write_build_info_txt(tmp_path, _info(dirty=True), {"pyinstaller": "6.22.3"})
    text = p.read_text(encoding="utf-8")
    assert "0.1.0+ge600169.dirty" in text and "e600169 (dirty)" in text and "6.22.3" in text
    assert "%APPDATA%\\BirdBendStand" in text


# ------------------------------------------------------------------------------------------------ icon

def _ico_sizes(data: bytes) -> list[int]:
    reserved, kind, n = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(n):
        w, _h, _c, _r, planes, bpp, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert planes == 1 and bpp == 32 and data[off:off + 8] == b"\x89PNG\r\n\x1a\n" and off + size <= len(data)
        sizes.append(w or 256)
    return sizes


@pytest.mark.req("SW-PLT-001")
def test_committed_icon_is_valid() -> None:
    assert _ico_sizes((PACK / "assets" / "BirdBendStand.ico").read_bytes()) == [16, 24, 32, 48, 64, 128, 256]


@pytest.mark.req("SW-PLT-001")
@pytest.mark.slow
def test_icon_generator_reproduces_committed_icon() -> None:
    mi = _load("make_icon")
    ico, png = mi.build()
    assert ico == (PACK / "assets" / "BirdBendStand.ico").read_bytes()
    assert png.startswith(b"\x89PNG")


# ------------------------------------------------------------------------------------------------ runtime hook

RTHOOK = PACK / "rthook_bend_stand.py"


@pytest.mark.req("SW-PLT-001")
def test_rthook_d06_guard_blocks_ports_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``BEND_STAND_D06_GUARD`` the frozen exe refuses every port open (checked with pyserial's virtual
    ``loop://`` and an unconfigured ``Serial`` — never a real COM name)."""
    import runpy

    import serial

    from bend_stand.core.errors import HardwareAccessForbidden

    monkeypatch.setattr(serial.Serial, "open", serial.Serial.open)        # restored after the test
    monkeypatch.setattr(serial, "serial_for_url", serial.serial_for_url)
    monkeypatch.setenv("BEND_STAND_D06_GUARD", "1")
    runpy.run_path(str(RTHOOK), run_name="rthook")
    with pytest.raises(HardwareAccessForbidden, match="BEND_STAND_D06_GUARD"):
        serial.serial_for_url("loop://")
    with pytest.raises(HardwareAccessForbidden, match="BEND_STAND_D06_GUARD"):
        serial.Serial().open()


@pytest.mark.req("SW-PLT-001")
@pytest.mark.parametrize("value", ["", "0"])
def test_rthook_without_guard_leaves_serial_alone(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    import runpy

    import serial

    before = (serial.Serial.open, serial.serial_for_url)
    monkeypatch.setenv("BEND_STAND_D06_GUARD", value)
    runpy.run_path(str(RTHOOK), run_name="rthook")
    assert (serial.Serial.open, serial.serial_for_url) == before


@pytest.mark.req("SW-PLT-001")
def test_rthook_redirects_windowed_output_to_log(tmp_path: Path) -> None:
    """A windowed exe has ``sys.stdout``/``sys.stderr`` = None: the hook sends them to ``<data>/logs``."""
    code = ("import runpy, sys\nsys.stdout = None\nsys.stderr = None\n"
            f"runpy.run_path({str(RTHOOK)!r}, run_name='rthook')\n"
            "print('hello-out')\nsys.stderr.write('hello-err\\n')\n")
    env = dict(os.environ, BEND_STAND_DATA_DIR=str(tmp_path / "data"))
    env.pop("BEND_STAND_D06_GUARD", None)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "data" / "logs" / "BirdBendStand.log").read_text(encoding="utf-8")
    assert "===== start" in log and "hello-out" in log and "hello-err" in log


@pytest.mark.req("SW-PLT-001")
def test_rthook_rotates_large_log(tmp_path: Path) -> None:
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "BirdBendStand.log").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    code = ("import runpy, sys\nsys.stdout = None\nsys.stderr = None\n"
            f"runpy.run_path({str(RTHOOK)!r}, run_name='rthook')\nprint('fresh')\n")
    env = dict(os.environ, BEND_STAND_DATA_DIR=str(tmp_path / "data"))
    env.pop("BEND_STAND_D06_GUARD", None)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (logs / "BirdBendStand.log.1").stat().st_size == 2 * 1024 * 1024 + 1
    assert "fresh" in (logs / "BirdBendStand.log").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ spec / repo

@pytest.mark.req("SW-PLT-001", "SYS-008")
def test_spec_bundles_simulator_hook_icon_and_excludes_no_app_module() -> None:
    spec = (PACK / "BirdBendStand.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("bend_stand")' in spec and "bend_stand.io.sim" in spec
    assert "rthook_bend_stand.py" in spec and "BirdBendStand.ico" in spec
    block = re.search(r"EXCLUDES = \[(.*?)\n\]", spec, re.S)
    assert block
    excluded = set(re.findall(r'"([^"]+)"', block.group(1)))
    needed = {"bend_stand", "bend_stand.io.sim", "bend_stand.gui", "serial", "numpy", "pyqtgraph", "PySide6",
              "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtSvg", "PySide6.QtOpenGL",
              "PySide6.QtOpenGLWidgets", "shiboken6"}
    for name in needed:                       # neither the module nor one of its parent packages is excluded
        parts = name.split(".")
        assert not {".".join(parts[:i]) for i in range(1, len(parts) + 1)} & excluded, name


@pytest.mark.req("SW-PLT-001")
def test_dist_and_build_are_git_ignored() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    for path in ("03_SW/dist/BirdBendStand-x/BirdBendStand.exe", "03_SW/dist/BirdBendStand-x.zip",
                 "03_SW/build/pyinstaller/x.toc", "03_SW/build/build_info.json"):
        r = subprocess.run([git, "check-ignore", "-q", path], cwd=SW_ROOT.parent, capture_output=True, timeout=30)
        assert r.returncode == 0, path


# ------------------------------------------------------------------------------------------------ operator docs

dh = _load("docs_html")


@pytest.mark.req("SW-PLT-001")
@pytest.mark.parametrize("text, sid", [
    ("1. Safety first", "1-safety-first"),
    ("9. Load calibration with 1 kg + 10 kg", "9-load-calibration-with-1-kg--10-kg"),
    ("19. Appendix A — indicator chips", "19-appendix-a--indicator-chips"),
    ("**Bold** and `code` [link](x.md)", "bold-and-code-link"),
])
def test_heading_slug_is_github_style(text: str, sid: str) -> None:
    assert dh.slug(text) == sid


@pytest.mark.req("SW-PLT-001")
def test_markdown_subset_renders() -> None:
    md = "\n".join([
        "# Title", r"Intro **bold**, *em*, `a_b*c`, \*star\*, [card](QUICK_REFERENCE.md#x), [out](../x/README.md).",
        "", "## 2. Section", "- item one", "  continued", "", "  ![Pic](img/p.png)", "",
        "  | A | B |", "  |---|---|", r"  | 1 \| 2 | **3** |", "- item two", "  1. nested", "",
        "3. third", "4. fourth", "", "```", "<code> & *raw*", "```", "> quoted", "", "---", "tail_text_here"])
    page = dh.render(md, {"QUICK_REFERENCE.md": "QUICK_REFERENCE.html"})
    assert '<h1 id="title">Title</h1>' in page and '<h2 id="2-section">' in page
    assert "<strong>bold</strong>" in page and "<em>em</em>" in page and "<code>a_b*c</code>" in page
    assert "*star*" in page and '<a href="QUICK_REFERENCE.html#x">card</a>' in page
    assert "out" in page and "../x/README.md" not in page                 # link leaving docs/ → plain text
    assert "<li>item one continued" in page and '<img src="img/p.png" alt="Pic">' in page
    assert "<td>1 | 2</td><td><strong>3</strong></td>" in page
    assert "<ol><li>nested</li></ol>" in page and '<ol start="3"><li>third</li><li>fourth</li></ol>' in page
    assert "<pre><code>&lt;code&gt; &amp; *raw*</code></pre>" in page
    assert "<blockquote><p>quoted</p></blockquote>" in page and "<hr>" in page and "tail_text_here" in page


@pytest.mark.req("SW-PLT-001")
def test_install_docs_renders_the_real_manuals(tmp_path: Path) -> None:
    """The installed manuals: every table-of-contents anchor resolves, every screenshot is copied and shown."""
    files = dh.install_docs(tmp_path)
    docs = tmp_path / "docs"
    assert {p.name for p in files} >= {"USER_MANUAL.md", "USER_MANUAL.html", "QUICK_REFERENCE.md",
                                       "QUICK_REFERENCE.html"}
    for name in ("USER_MANUAL.html", "QUICK_REFERENCE.html"):
        page = (docs / name).read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', page))
        assert not [h for h in re.findall(r'href="#([^"]+)"', page) if h not in ids], name
        assert not [s for s in re.findall(r'<img src="([^"]+)"', page) if not (docs / s).is_file()], name
        assert "**" not in page and "](" not in page, name                 # no unrendered Markdown left
    manual = (docs / "USER_MANUAL.html").read_text(encoding="utf-8")
    assert len(re.findall(r'<img src="img/', manual)) == len(dh.image_refs((docs / "USER_MANUAL.md").read_text(
        encoding="utf-8"))) > 0
    assert 'href="QUICK_REFERENCE.html"' in manual
    assert "BirdBendStand-cli.exe report" in manual                     # offline rebuild paragraph (OI-UM-03)


@pytest.mark.req("SW-PLT-001")
def test_install_docs_reports_missing_image(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "img").mkdir(parents=True)
    (src / "USER_MANUAL.md").write_text("# M\n![x](img/missing.png)\n", encoding="utf-8")
    (src / "QUICK_REFERENCE.md").write_text("# Q\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing.png"):
        dh.install_docs(tmp_path / "dist", src)


@pytest.mark.req("SW-PLT-001")
def test_installer_and_smoke_cover_docs() -> None:
    iss = (PACK / "BirdBendStand.iss").read_text(encoding="utf-8")
    assert r"{app}\docs\USER_MANUAL.html" in iss and r"{app}\docs\QUICK_REFERENCE.html" in iss
    smoke = (PACK / "smoke_dist.py").read_text(encoding="utf-8")
    assert "def docs_problems" in smoke and "def check_report" in smoke


@pytest.mark.req("SW-PLT-001")
def test_gui_icon_resource_is_packaged() -> None:
    """GRQ-B-31 c: ``bend_stand.gui.resources/BirdBendStand.ico`` goes into the wheel (package-data) and the frozen
    build (spec datas, same relative path) and is the packaging icon."""
    src_icon = SW_ROOT / "src" / "bend_stand" / "gui" / "resources" / "BirdBendStand.ico"
    assert src_icon.read_bytes() == (PACK / "assets" / "BirdBendStand.ico").read_bytes()
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "*.ico" in py["tool"]["setuptools"]["package-data"]["bend_stand.gui.resources"]
    spec = (PACK / "BirdBendStand.spec").read_text(encoding="utf-8")
    assert '(str(GUI_ICON), "bend_stand/gui/resources")' in spec
    assert '"bend_stand" / "gui" / "resources" / "BirdBendStand.ico"' in spec
    assert "bend_stand/gui/resources/BirdBendStand.ico" in (PACK / "smoke_dist.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ SWR-27 lock files

lk = _load("lock_requirements")


@pytest.mark.req("SW-PLT-001")
def test_swr27_lock_files_consistent_with_pins() -> None:
    """Every requirements file has a hash-pinned lock: all direct pins locked at their version, every entry with a
    SHA-256, the dev / build locks contain the runtime lock unchanged (``lock_requirements.py check``)."""
    assert lk.check(SW_ROOT) == []
    for _src, dst in lk.PAIRS:
        entries = lk.parse_lock((SW_ROOT / dst).read_text(encoding="utf-8"))
        assert entries and all(len(h) == 64 for e in entries for h in e.hashes) and all(e.hashes for e in entries)
        assert len({e.name for e in entries}) == len(entries)
    runtime = {e.name for e in lk.parse_lock((SW_ROOT / "requirements.lock.txt").read_text(encoding="utf-8"))}
    assert {"pyside6-essentials", "shiboken6", "numpy", "pyqtgraph", "pyserial"} <= runtime   # transitive included


@pytest.mark.req("SW-PLT-001")
def test_swr27_lock_parser_and_check_detect_problems(tmp_path: Path) -> None:
    text = "# c\nfoo==1.0 \\n    --hash=sha256:" + "a" * 64 + " \\n    --hash=sha256:" + "b" * 64 + "\nbar_baz==2\n"
    assert lk.parse_lock(text) == [lk.LockEntry("foo", "1.0", ("a" * 64, "b" * 64)), lk.LockEntry("bar-baz", "2", ())]
    for src, dst in lk.PAIRS:
        (tmp_path / src).write_text("foo==1.0\n", encoding="utf-8")
        (tmp_path / dst).write_text(text, encoding="utf-8")
    probs = lk.check(tmp_path)
    assert any("bar-baz has no sha256" in p for p in probs)
    (tmp_path / "requirements-dev.txt").write_text("-r requirements.txt\nqux==3\n", encoding="utf-8")
    assert any("direct requirement qux not locked" in p for p in lk.check(tmp_path))
    (tmp_path / "requirements-build.lock.txt").unlink()
    assert "requirements-build.lock.txt missing" in lk.check(tmp_path)


@pytest.mark.req("SW-PLT-001")
def test_swr27_build_uses_the_lock() -> None:
    ps1 = (PACK / "build_dist.ps1").read_text(encoding="utf-8")
    assert "--require-hashes -r (Join-Path $SW \"requirements-build.lock.txt\")" in ps1
    assert "check-env --req (Join-Path $SW \"requirements-build.lock.txt\")" in ps1
    pins = bi.read_pins(SW_ROOT / "requirements-build.lock.txt")                 # lock format readable by check-env
    assert bi.read_pins(SW_ROOT / "requirements-build.txt").items() <= pins.items() and len(pins) > 11
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = {re.split(r"[<>=!~ ]", d, maxsplit=1)[0].lower(): d for d in py["project"]["dependencies"]}
    assert all("<" in spec for spec in deps.values()), deps                      # every runtime range has a ceiling

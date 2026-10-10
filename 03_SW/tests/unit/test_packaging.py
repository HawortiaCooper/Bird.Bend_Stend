"""Packaging: import, versions, entry point, ``--help``, requirements files vs ``pyproject.toml``.

Verifies: SW-PLT-001, IF-001
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import bend_stand
from bend_stand import __main__ as entry
from bend_stand.core import params_gen, protocol_gen

SW_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.req("SW-PLT-001", "IF-001")
def test_versions_come_from_generated_modules() -> None:
    assert bend_stand.PROTO_VERSION == (protocol_gen.PROTO_MAJOR, protocol_gen.PROTO_MINOR) == (1, 0)
    assert bend_stand.PAYLOAD_VERSION == protocol_gen.PAYLOAD_VERSION == 1
    assert bend_stand.ICD_VERSION == protocol_gen.ICD_VERSION
    assert bend_stand.PARAM_DICT_HASH == params_gen.PARAM_DICT_HASH
    assert isinstance(bend_stand.__version__, str) and bend_stand.__version__


@pytest.mark.req("SW-PLT-001")
def test_entry_point_declared() -> None:
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert py["project"]["scripts"]["bend-stand"] == "bend_stand.__main__:main"
    assert callable(entry.main)


@pytest.mark.req("SW-PLT-001")
def test_help_and_version_run_as_module() -> None:
    env_src = str(SW_ROOT / "src")
    for args in (["--help"], ["--version"]):
        r = subprocess.run([sys.executable, "-m", "bend_stand", *args], capture_output=True, text=True,
                           timeout=60, cwd=env_src)
        assert r.returncode == 0, r.stderr
        assert ("bend_stand" in r.stdout) or ("usage" in r.stdout)


@pytest.mark.req("SW-PLT-001", "SYS-008")
@pytest.mark.parametrize("argv, endpoint", [
    ([], None), (["--sim"], "sim"), (["--sim", "x.simscn.json"], "sim:x.simscn.json"),
    (["--port", "COM7"], "COM7"), (["--port", "tcp://127.0.0.1:5760"], "tcp://127.0.0.1:5760"),
])
def test_endpoint_argument(argv: list[str], endpoint: str | None) -> None:
    assert entry.parse_args(argv).endpoint == endpoint


@pytest.mark.req("SW-PLT-001")
def test_sim_and_port_exclusive() -> None:
    with pytest.raises(SystemExit):
        entry.parse_args(["--sim", "--port", "COM7"])


@pytest.mark.req("SW-PLT-001")
def test_headless_requires_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--headless"]) == 2


def _pins(path: Path) -> dict[str, str]:
    """Direct pins; a line whose comment starts with ``transitive`` pins a transitive package to the verified venv
    (SWR-34) and is not a direct dependency of pyproject."""
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "#" in raw and raw.split("#", 1)[1].strip().lower().startswith("transitive"):
            continue
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-r"):
            continue
        name, ver = line.split("==")
        out[name.strip().lower()] = ver.strip()
    return out


@pytest.mark.req("SW-PLT-001")
def test_requirements_agree_with_pyproject() -> None:
    py = tomllib.loads((SW_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ranges = {re.split(r"[<>=!~ ]", d, maxsplit=1)[0].lower(): d
              for d in py["project"]["dependencies"] + py["project"]["optional-dependencies"]["test"]}
    pins = {**_pins(SW_ROOT / "requirements.txt"), **_pins(SW_ROOT / "requirements-dev.txt")}
    assert set(pins) == set(ranges), (set(pins) ^ set(ranges))
    for name, ver in pins.items():
        spec = ranges[name][len(name):]
        lower = re.search(r">=\s*([0-9.]+)", spec)
        upper = re.search(r"<\s*([0-9.]+)", spec)
        v = tuple(int(x) for x in ver.split("."))
        if lower:
            assert v >= tuple(int(x) for x in lower.group(1).split(".")), (name, ver, spec)
        if upper:
            assert v < tuple(int(x) for x in upper.group(1).split(".")), (name, ver, spec)


@pytest.mark.req("SW-PLT-001", "SW-PLT-003", "SYS-008")
@pytest.mark.rt
def test_headless_in_process(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--headless", "--sim", "--duration", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "connected: sim" in out and "link state CONNECTED" in out
    assert entry.main(["--version"]) == 0

"""Bird Bend Stand — PC application (backend ``core``/``io``/``calc`` + PySide6 ``gui``).

Owner: Implementer B (D-29 n). The implemented interface versions come from the generated
``core.protocol_gen`` / ``core.params_gen`` (never hand-written, ICD §0.3).

Implements: SW-PLT-001, SW-PLT-002, IF-001
"""
from __future__ import annotations

from bend_stand.core.params_gen import PARAM_DICT_HASH, PARAM_DICT_VERSION
from bend_stand.core.protocol_gen import ICD_VERSION, PAYLOAD_VERSION, PROTO_MAJOR, PROTO_MINOR

try:  # installed (pip install -e 03_SW)
    from importlib.metadata import PackageNotFoundError, version as _version

    try:
        __version__ = _version("bend_stand")
    except PackageNotFoundError:  # running from the source tree (pythonpath = 03_SW/src)
        __version__ = "0.1.0+src"
except ImportError:  # pragma: no cover
    __version__ = "0.1.0+src"

#: (major, minor) of the protocol this SW implements (ICD §0.2)
PROTO_VERSION: tuple[int, int] = (PROTO_MAJOR, PROTO_MINOR)

__all__ = [
    "__version__", "PROTO_VERSION", "PROTO_MAJOR", "PROTO_MINOR", "PAYLOAD_VERSION", "ICD_VERSION",
    "PARAM_DICT_HASH", "PARAM_DICT_VERSION",
]

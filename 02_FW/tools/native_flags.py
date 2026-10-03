"""PlatformIO pre-script of env:native (FW_design §2.2): strict warnings for our own C sources.

src/pure, src/gen and src/core (and the A-owned fake seams in test/common_impl) are compiled with
-Werror -Wconversion -Wshadow on top of the env flags; Unity and the generated test runner are not
ours and keep the plain flags.

Origin: the build middleware of Thrust_Stand_HAW/02_FW/tools/gen_test_vectors.py @37c8747.
Implements: NFR-005 (host build hygiene), FW-PLT-001
"""
Import("env")  # noqa: F821

STRICT = ["-Werror", "-Wconversion", "-Wshadow", "-Wstrict-prototypes"]
OURS = ("/src/pure/", "/src/gen/", "/src/core/", "/test/common_impl/")


def _strict_middleware(node):
    p = node.srcnode().get_abspath().replace("\\", "/")
    if any(o in p for o in OURS) and p.endswith(".c"):
        return env.Object(node, CCFLAGS=env["CCFLAGS"] + STRICT)  # noqa: F821
    return node


env.AddBuildMiddleware(_strict_middleware)  # noqa: F821

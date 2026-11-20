"""Removed namespace-scope ``using std::X;`` re-exports score through the typed API.

The bug class: a public header declares ``namespace lib { using std::X; }``, so
consumers may spell ``lib::X``. When a release drops that using-declaration the
consumer's source breaks, and abicheck must report it (``STD_REEXPORT_REMOVED``,
``API_BREAK``). Before the fix the typed dump's header graph left the re-export
out of the public surface, so the contract stage stamped the finding
``PROVEN_OUT_OF_CONTRACT`` and the verdict fell to ``NO_CHANGE``.

Oracle, written from the C++ rule and not from the implementation: after the
using-declaration is removed, ``lib::X`` no longer names anything, so the pair
must be an API break whose only finding is ``STD_REEXPORT_REMOVED``. The
library's exported symbols are the same on both sides, so nothing else may fire.

One case per kind of re-exported entity: a function, a type and a variable.
Each runs through ``abiforge.checkers.abicheck``, which dumps through the typed
``dumper.dump()`` with the public headers given, so the default header graph is
attached exactly as it is for a typed API user. The test needs castxml and a
C++ compiler; it does not skip, so it belongs to the ``integration`` lane.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abiforge.checkers import abicheck as checker

pytestmark = pytest.mark.integration

BREAK = "API_BREAK"
EXPECTED_KINDS = frozenset({"STD_REEXPORT_REMOVED"})


def _require_toolchain() -> None:
    """Skip only when a tool is absent; the integration lane installs both."""
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH; this integration test needs it")


def _identity_args(name: str) -> list[str]:
    """Pin the library's identity so the old and new builds compare equal.

    Without this, macOS records the output path as the install name, and the
    two builds (written to different directories) read as ``SONAME_CHANGED``,
    a finding unrelated to the re-export under test. Linux records no soname
    unless one is given; giving the same one to both sides changes nothing.
    """
    if sys.platform == "darwin":
        return [f"-Wl,-install_name,@rpath/{name}"]
    return [f"-Wl,-soname,{name}"]


def _build(tmp: Path, stem: str, header: str, source: str) -> tuple[Path, Path]:
    """Compile one shared library from *source* with *header* as its public header."""
    tmp.mkdir(parents=True, exist_ok=True)
    hdr = tmp / f"{stem}.h"
    src = tmp / f"{stem}.cpp"
    so = tmp / f"lib{stem}.so"
    hdr.write_text(header + "\n", encoding="utf-8")
    src.write_text(f'#include "{hdr.name}"\n' + source + "\n", encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-std=c++17",
            *_identity_args(so.name),
            "-I",
            str(tmp),
            str(src),
            "-o",
            str(so),
        ],
        check=True,
        cwd=tmp,
    )
    return so, hdr


# (case id, old header, new header, library source). The old header re-exports
# the std entity; the new one drops only that using-declaration.
CASES = {
    "function": (
        "#include <cstdlib>\nnamespace krx { using std::abs; int krx_f(int a); }",
        "#include <cstdlib>\nnamespace krx { int krx_f(int a); }",
        "#include <cstdlib>\nnamespace krx { int krx_f(int a) { return std::abs(a); } }",
    ),
    "type": (
        "#include <string>\nnamespace krx { using std::string; int krx_t(int a); }",
        "#include <string>\nnamespace krx { int krx_t(int a); }",
        "#include <string>\nnamespace krx { int krx_t(int a) { return a; } }",
    ),
    "variable": (
        "#include <iostream>\nnamespace krx { using std::cout; int krx_v(int a); }",
        "#include <iostream>\nnamespace krx { int krx_v(int a); }",
        "#include <iostream>\nnamespace krx { int krx_v(int a) { std::cout << a; return a; } }",
    ),
}


@pytest.mark.parametrize("kind", sorted(CASES))
def test_removed_using_reexport_is_an_api_break_through_the_typed_api(
    kind: str, tmp_path: Path
) -> None:
    _require_toolchain()
    old_header, new_header, source = CASES[kind]
    old_so, old_hdr = _build(tmp_path / "old", kind, old_header, source)
    new_so, new_hdr = _build(tmp_path / "new", kind, new_header, source)

    old_snap = checker.snapshot(
        old_so, [old_hdr], version="1", is_cxx=True, backend="castxml", gcc_options=None
    )
    new_snap = checker.snapshot(
        new_so, [new_hdr], version="2", is_cxx=True, backend="castxml", gcc_options=None
    )
    verdict, kinds = checker.answer(old_snap, new_snap)

    assert verdict == BREAK, (
        f"removed using-declaration of a {kind} scored {verdict} with kinds "
        f"{sorted(kinds)}; the re-export is declared public, so it must be an API break"
    )
    assert kinds == EXPECTED_KINDS, (
        f"removed using-declaration of a {kind}: kinds {sorted(kinds)}, "
        f"expected exactly {sorted(EXPECTED_KINDS)}"
    )


def test_private_bare_name_collision_keeps_the_reexport_finding(tmp_path: Path) -> None:
    """Known gap: a private ``detail::abs`` shares the bare tail of ``std::abs``.

    The origin is keyed by bare name, so the collision could keep the finding in
    surface scope. Either way the removed ``krx::abs`` must still be reported as
    an API break: this is the correct answer, asserted as an ordinary regression.
    """
    _require_toolchain()
    priv = tmp_path / "priv.h"
    priv.write_text("namespace detail { struct abs { int v; }; }\n", encoding="utf-8")

    def build(stem: str, public: str) -> tuple[Path, Path]:
        d = tmp_path / stem
        d.mkdir(parents=True, exist_ok=True)
        hdr = d / "pub.h"
        hdr.write_text('#include "priv.h"\n' + public + "\n", encoding="utf-8")
        src = d / "lib.cpp"
        src.write_text(
            '#include "pub.h"\n#include <cstdlib>\n'
            "namespace krx { int krx_f(int a) { return std::abs(a) + detail::abs{a}.v; } }\n",
            encoding="utf-8",
        )
        so = d / "libx.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-std=c++17",
                *_identity_args(so.name),
                "-I",
                str(tmp_path),
                "-I",
                str(d),
                str(src),
                "-o",
                str(so),
            ],
            check=True,
        )
        return so, hdr

    old_so, old_hdr = build(
        "old", "#include <cstdlib>\nnamespace krx { using std::abs; int krx_f(int a); }"
    )
    new_so, new_hdr = build(
        "new", "#include <cstdlib>\nnamespace krx { int krx_f(int a); }"
    )
    opts = f"-I{tmp_path}"
    old_snap = checker.snapshot(
        old_so, [old_hdr], version="1", is_cxx=True, backend="castxml", gcc_options=opts
    )
    new_snap = checker.snapshot(
        new_so, [new_hdr], version="2", is_cxx=True, backend="castxml", gcc_options=opts
    )
    verdict, kinds = checker.answer(old_snap, new_snap)
    assert verdict == BREAK
    assert kinds == EXPECTED_KINDS

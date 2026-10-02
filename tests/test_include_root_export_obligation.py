"""An `-I` root's headers carry a narrowed, not a dropped, export obligation.

Known gap "An `-I` include root makes another library's public headers this
component's export obligations" (2026-09-16), the PVXS case: `-H
include/pvxs/iochooks.h -I include` charged `libpvxsIoc` with
`public_not_exported` for `version.h`'s symbols, which the sibling `libpvxs`
exports. Maintainer ruling (2026-10-01): report such a declaration as an
*unresolved* obligation -- LOW confidence, worded as not established --
never drop it, and never touch a declaration the run's own `-H` set covers.

The oracle for "which symbols are reported" is the set of declarations the
binary does not export, computed here from the source fixture, independently
of the check's own predicate.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from abicheck.buildsource.export_obligation_ownership import obligation_standing
from abicheck.cli import main
from abicheck.model.extraction_scope import EntityOwnership
from abicheck.model.fact import Fact

# ── the standing predicate, exhaustively ────────────────────────────────────


def _snap(roots: tuple[str, ...] | None):  # type: ignore[no-untyped-def]
    if roots is None:
        return SimpleNamespace(extraction_scope=None)
    return SimpleNamespace(
        extraction_scope=SimpleNamespace(
            ownership_rules=SimpleNamespace(target_roots=roots)
        )
    )


def _decl(contract: str | None, rule_id: str = "no_root"):  # type: ignore[no-untyped-def]
    if contract is None:
        return SimpleNamespace()
    return SimpleNamespace(
        ownership_fact=Fact.present(EntityOwnership("x", contract, rule_id))
    )


@pytest.mark.parametrize("roots", [None, (), ("/inc/a.h",)])
@pytest.mark.parametrize(
    "contract", [None, "public", "private", "external", "unresolved"]
)
@pytest.mark.parametrize("rule_id", ["no_root", "no_file", "target_root:/inc/a.h"])
def test_standing_truth_table(
    roots: tuple[str, ...] | None, contract: str | None, rule_id: str
) -> None:
    """Oracle: narrowed iff the run declared a surface AND this declaration
    is unresolved because no root covers its file."""
    expected = bool(roots) and contract == "unresolved" and rule_id == "no_root"
    got = obligation_standing(_snap(roots), _decl(contract, rule_id))
    assert got.outside_declared_surface is expected


# ── end to end through the CLI ──────────────────────────────────────────────

_E2E = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux"),
    pytest.mark.skipif(
        shutil.which("g++") is None or shutil.which("castxml") is None,
        reason="needs g++ + castxml",
    ),
]


def _e2e(fn):  # type: ignore[no-untyped-def]
    for mark in reversed(_E2E):
        fn = mark(fn)
    return fn


#: `iochooks.h` is this library's; `version.h` is a sibling library's, reached
#: only through `#include`. `ioc_missing` is declared in the library's own
#: header and deliberately not defined -- the positive control.
_VERSION_H = """#pragma once
namespace pvxs { unsigned long version_int(); const char* version_str(); }
"""
_IOCHOOKS_H = """#pragma once
#include <pvxs/version.h>
namespace pvxs { namespace ioc { void testPrepare(); void ioc_missing(); } }
"""
_IOC_CPP = (
    "#include <pvxs/iochooks.h>\nnamespace pvxs{namespace ioc{void testPrepare(){}}}\n"
)

_SIBLING = {"_ZN4pvxs11version_intEv", "_ZN4pvxs11version_strEv"}
_OWN_MISSING = {"_ZN4pvxs3ioc11ioc_missingEv"}


def _build(tmp_path: Path) -> Path:
    inc = tmp_path / "include" / "pvxs"
    inc.mkdir(parents=True)
    (inc / "version.h").write_text(_VERSION_H)
    (inc / "iochooks.h").write_text(_IOCHOOKS_H)
    (tmp_path / "ioc.cpp").write_text(_IOC_CPP)
    lib = tmp_path / "libpvxsIoc.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            f"-I{tmp_path / 'include'}",
            str(tmp_path / "ioc.cpp"),
            "-o",
            str(lib),
        ],
        check=True,
    )
    return lib


def _public_not_exported(tmp_path: Path, lib: Path, header: Path) -> dict[str, str]:
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(lib),
            str(lib),
            "-H",
            str(header),
            "-I",
            str(tmp_path / "include"),
            "-o",
            "json=-",
        ],
    )
    doc = json.loads(result.output[result.output.index("{") :])
    return {
        c["symbol"]: c["description"]
        for c in doc["changes"]
        if c["kind"] == "public_not_exported"
    }


@_e2e
def test_file_h_narrows_only_the_included_sibling_header(tmp_path: Path) -> None:
    lib = _build(tmp_path)
    found = _public_not_exported(tmp_path, lib, tmp_path / "include/pvxs/iochooks.h")
    # Nothing is dropped: exactly the undefined declarations are reported.
    assert set(found) == _SIBLING | _OWN_MISSING
    # The sibling's are narrowed and say so ...
    for sym in _SIBLING:
        assert "not established" in found[sym], found[sym]
        assert "version.h" in found[sym]
    # ... the library's own declared header keeps its full obligation.
    for sym in _OWN_MISSING:
        assert "not established" not in found[sym]
        assert "undefined-symbol" in found[sym]


@_e2e
def test_directory_h_keeps_every_obligation_full(tmp_path: Path) -> None:
    """A -H *directory* declares everything under it, siblings included."""
    lib = _build(tmp_path)
    found = _public_not_exported(tmp_path, lib, tmp_path / "include/pvxs")
    assert set(found) == _SIBLING | _OWN_MISSING
    assert not any("not established" in text for text in found.values())

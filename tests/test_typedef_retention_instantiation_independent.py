"""Dependency typedef retention must not depend on which template instances
an inline body happens to request (field report H7).

Bug class ``scoping.declaration_kind_forwarded_unfiltered`` (retention by a
stated reference rule): dump-time scoping kept a system typedef only when a
*kept declaration's signature* named it. A member template's implicit
instance (``Table::setArrays<double>(double*, size_t)``) is such a
declaration only while some inline body instantiates it, so ``size_t`` was
kept in the old release and dropped in the new one -- although the public
header declares ``setArrays(T*, std::size_t)`` in both -- and ``compare``
reported ``typedef_removed size_t``.

The oracle is computed from the generated header identifiers and declaring
headers, never from the implementation's helpers. The metamorphic invariant:
removing every instance signature from the retention haystack leaves the
kept typedef set unchanged whenever the header still spells the names.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.extract.flat_map_dependency_scope import scope_flat_maps
from abicheck.extract.public_header_identifiers import identifiers_in_kept_headers
from abicheck.model import AbiSnapshot
from abicheck.model.declaration_headers import attributed
from abicheck.model.identity import ScopePath, entity_id_for_typedef

SYS = "/usr/include/stddef.h"
OWN = "/proj/t.h"

# (qualified key, leaf, declaring header) -- several independent system
# typedefs plus a std::string alias, each reached only via an instance.
_SYSTEM_TYPEDEFS = (
    ("size_t", "size_t", SYS),
    ("std::size_t", "size_t", "/usr/include/c++/bits/c++config.h"),
    ("ptrdiff_t", "ptrdiff_t", SYS),
    ("uint32_t", "uint32_t", "/usr/include/stdint.h"),
    ("std::string", "string", "/usr/include/c++/string"),
)
_OWN_TYPEDEF = ("Handle", "Handle", OWN)


def _is_dep(header: str | None) -> bool:
    return bool(header) and header.startswith("/usr/")


def _snapshot(entries) -> AbiSnapshot:
    return AbiSnapshot(
        library="t",
        version="1",
        typedefs=attributed((k, "x", h) for k, _, h in entries),
        typedefs_qualified=attributed((k, "x", h) for k, _, h in entries),
        typedef_entity_ids={
            k: entity_id_for_typedef(ScopePath(()), leaf) for k, leaf, _ in entries
        },
    )


_ALL = (*_SYSTEM_TYPEDEFS, _OWN_TYPEDEF)
_HEADER_IDS = frozenset(
    {"Table", "setArrays", "T", "std", "size_t", "ptrdiff_t", "uint32_t", "string"}
)
_INSTANCE_SIG = (
    "int Table::setArrays<double>(double*, size_t, ptrdiff_t, uint32_t, std::string)"
)


@pytest.mark.parametrize("entry", _SYSTEM_TYPEDEFS, ids=lambda e: e[0])
def test_system_typedef_kept_with_and_without_the_instance(entry) -> None:
    snap = _snapshot(_ALL)
    with_instance = scope_flat_maps(snap, _is_dep, _INSTANCE_SIG, None, {}, _HEADER_IDS)
    without_instance = scope_flat_maps(snap, _is_dep, "", None, {}, _HEADER_IDS)
    key = entry[0]
    assert key in with_instance.typedefs_qualified
    assert key in without_instance.typedefs_qualified
    assert set(with_instance.typedefs) == set(without_instance.typedefs)


@pytest.mark.parametrize("entry", _SYSTEM_TYPEDEFS, ids=lambda e: e[0])
def test_system_typedef_header_does_not_spell_is_still_dropped(entry) -> None:
    """Control: the new root is the header text, not 'keep everything'."""
    key, leaf, _ = entry
    ids = _HEADER_IDS - {leaf}
    out = scope_flat_maps(_snapshot(_ALL), _is_dep, "", None, {}, ids)
    assert key not in out.typedefs_qualified
    assert "Handle" in out.typedefs_qualified  # own header: always kept


def test_unscanned_header_set_falls_back_to_signature_roots() -> None:
    out = scope_flat_maps(_snapshot(_ALL), _is_dep, "f(size_t)", None, {}, None)
    want = {k for k, leaf, h in _ALL if not _is_dep(h) or leaf == "size_t"}
    assert set(out.typedefs_qualified) == want


def test_kept_header_scan_skips_dependency_files(tmp_path: Path) -> None:
    """An excluded header under a ``-H`` directory never roots its own
    typedefs; the library header's spelling does."""
    (tmp_path / "api.h").write_text("int f(std::size_t n); // ptrdiff_t\n")
    (tmp_path / "dep.h").write_text("typedef int dep_unused_t;\n")
    ids = identifiers_in_kept_headers([tmp_path], lambda h: h.endswith("dep.h"))
    assert ids is not None
    assert {"f", "std", "size_t", "n"} <= ids
    assert "dep_unused_t" not in ids
    assert "ptrdiff_t" not in ids  # comments are not spellings
    assert identifiers_in_kept_headers([tmp_path / "missing.h"], _is_dep) is None


# ---------------------------------------------------------------- compiled

_HEADER = """#include <cstddef>
%(own)s
class Table {
public:
  template <class T> int setArrays(T* p, std::size_t n) { return (int)n + (p ? 1 : 0); }
  template <class T> int setImpl(T* p) { return p ? 1 : 0; }
  int fill() { double d = 0; %(fill)s }
  int other(%(handle)s h);
};
"""


def _build(root: Path, *, uses_set_arrays: bool, own_typedef: bool) -> Path:
    root.mkdir(parents=True)
    fill = (
        "return setArrays<double>(&d, 1);"
        if uses_set_arrays
        else "return setImpl<double>(&d);"
    )
    own = "typedef int Handle;" if own_typedef else ""
    handle = "Handle" if own_typedef else "int"
    (root / "t.h").write_text(_HEADER % {"own": own, "fill": fill, "handle": handle})
    (root / "t.cpp").write_text(
        f'#include "t.h"\nint Table::other({handle} h) {{ return fill() + h; }}\n'
    )
    cmd = [
        "g++",
        "-shared",
        "-fPIC",
        "-fvisibility-inlines-hidden",
        f"-I{root}",
        str(root / "t.cpp"),
        "-o",
        str(root / "libt.so"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return root


def _typedef_kinds(old: Path, new: Path, depth: str) -> set[tuple[str, str]]:
    snap = old / "old.abicheck.json"
    dump = [
        sys.executable, "-m", "abicheck", "dump", str(old / "libt.so"),
        "-H", str(old / "t.h"), "-I", str(old), "-o", str(snap),
    ]  # fmt: skip
    proc = subprocess.run(dump, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-2000:]
    cmd = [
        sys.executable, "-m", "abicheck", "compare", str(snap), str(new / "libt.so"),
        "--header", f"new={new / 't.h'}", "--include", f"new={new}",
        "--depth", depth, "-o", "json=-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    report = json.loads(proc.stdout)
    return {
        (c["kind"], c.get("symbol") or "")
        for c in report.get("changes", [])
        if "typedef" in c["kind"] or c["kind"] == "public_surface_shrank"
    }


@pytest.mark.integration
@pytest.mark.parametrize("depth", ("headers", "source"))
def test_compiled_vanished_instance_reports_no_typedef_removal(
    tmp_path: Path, depth: str
) -> None:
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    old = _build(tmp_path / "old", uses_set_arrays=True, own_typedef=False)
    new = _build(tmp_path / "new", uses_set_arrays=False, own_typedef=False)
    assert _typedef_kinds(old, new, depth) == set()


@pytest.mark.integration
@pytest.mark.parametrize("depth", ("headers", "source"))
def test_compiled_library_typedef_removal_still_reported(
    tmp_path: Path, depth: str
) -> None:
    """Control: a typedef the library's own header declared is removed."""
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    old = _build(tmp_path / "old", uses_set_arrays=True, own_typedef=True)
    new = _build(tmp_path / "new", uses_set_arrays=False, own_typedef=False)
    kinds = _typedef_kinds(old, new, depth)
    assert ("typedef_removed", "Handle") in kinds
    assert not any(sym == "size_t" for _, sym in kinds)

# SPDX-License-Identifier: Apache-2.0
"""A default install, with every optional tool hidden, still produces the facts its depth promises.

The clang companion layout tool (``abicheck-clang-layout-tool``) was for months
the only producer of clang record layout, and no test ran a dump without it on
a binary without DWARF, so clang snapshots silently lacked sizes and offsets
(``docs/contribute/archive/known-gaps-closed.md``, 2026-11-04).

Every test here runs the real CLI in an environment that is a *default
install*: ``PATH`` holds only the frontend a depth is defined by (castxml,
clang, the C/C++ compiler they drive, and that compiler's own linker and
assembler), every :attr:`ToolLookup.binaries` and :attr:`ToolLookup.modules`
of ``tests/optional_tool_inventory.py`` is unreachable, every ``ABICHECK_*``
variable is unset (``ABICHECK_AST_FRONTEND`` aside, which names the frontend
under test), and the dump cache is empty. The library is built without ``-g``,
so no DWARF can backfill what the header frontend failed to produce.

The oracle is independent of abicheck: a program compiled from the same header
prints ``sizeof``/``alignof``/``offsetof`` and the enumerator values. A fact a
frontend legitimately cannot produce must read as an explicit gap
(``NOT_COLLECTED``/``UNSUPPORTED``/``FAILED`` with no value, or ``None`` for a
plain unknown), never as an empty or zero value (ADR-028/063).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from abicheck.model.abi_fundamental import FundamentalEncoding
from abicheck.model.availability import GAP_STATUSES, FactStatus
from abicheck.model.explicit_instantiation import ExplicitInstantiation
from abicheck.model.fact import Fact
from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import REPO_ROOT, abicheck_command
from tests.optional_tool_inventory import hidden_binaries, hidden_modules

pytestmark = [
    pytest.mark.integration,
    # Linux/ELF only. The default-install PATH is built from symlinked tool
    # shims (a privilege Windows runners do not grant), and the oracles assume
    # an ELF library and glibc spellings: on macOS the same build is Mach-O,
    # `int64_t` is `long long`, and no typedef ABI facts are recorded. Mach-O
    # and PE default-install facts are not covered by this module.
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="default-install oracles are ELF/Linux-specific",
    ),
]

_NEEDS_CASTXML = pytest.mark.skipif(
    not shutil.which("castxml"), reason="castxml is not installed"
)
_NEEDS_CLANG = pytest.mark.skipif(
    not (shutil.which("clang") and shutil.which("clang++")),
    reason="clang and clang++ are not installed",
)
#: Each frontend arm skips on its own tool; the binary arm needs neither.
FRONTENDS_ONLY = ("castxml", "clang")
FRONTENDS = (
    pytest.param("castxml", marks=_NEEDS_CASTXML, id="castxml"),
    pytest.param("clang", marks=_NEEDS_CLANG, id="clang"),
)

#: What a default install of a header frontend puts on PATH.
_FRONTEND_TOOLS = ("castxml", "clang", "clang++", "gcc", "g++", "cc", "c++")
#: The compiler's own linker and assembler: part of any compiler install. The
#: linker-less arm drops them.
_LINKER_TOOLS = ("ld", "as")

CXX_HEADER = """\
#pragma once
#include <cstdint>
namespace lib {
enum Color { Red = 1, Green = 5, Blue = 9 };
enum class Mode : std::uint16_t { Off = 0, On = 3 };
typedef std::int64_t handle_t;
using count_t = unsigned int;
struct Point { char tag; double x; int y; };
class Shape {
public:
  virtual ~Shape();
  virtual double area() const;
  virtual void scale(double f);
  int id;
};
struct Derived : Shape { short extra; double area() const override; };
template <typename T> class Buffer { public: T* data; T first; int size() const; };
extern template class Buffer<int>;
handle_t make(Point p, Color c, Mode m, count_t n);
Buffer<int>* buf();
Derived* derived();
}
"""

CXX_SOURCE = """\
#include "api.h"
namespace lib {
Shape::~Shape() {}
double Shape::area() const { return 0; }
void Shape::scale(double) {}
double Derived::area() const { return 1; }
template <typename T> int Buffer<T>::size() const { return 1; }
template class Buffer<int>;
handle_t make(Point p, Color, Mode, count_t n) { return p.y + n; }
Buffer<int>* buf() { static Buffer<int> b; return &b; }
Derived* derived() { static Derived d; return &d; }
}
"""

#: Prints the oracle: ``R <record> <sizeof> <alignof>``, ``F <record> <field>
#: <offsetof>``, ``E <enum> <name> <value>``, ``T <typedef> <sizeof>``.
CXX_ORACLE = """\
#include "api.h"
#include <cstddef>
#include <cstdio>
using namespace lib;
#define R(T) std::printf("R %s %zu %zu\\n", #T, sizeof(T), alignof(T));
#define F(T, m) std::printf("F %s %s %zu\\n", #T, #m, offsetof(T, m));
#define E(T, v) std::printf("E %s %s %lld\\n", #T, #v, (long long)T::v);
#define TD(T) std::printf("T %s %zu\\n", #T, sizeof(T));
int main() {
  R(Point) F(Point, tag) F(Point, x) F(Point, y)
  R(Shape) F(Shape, id)
  R(Derived) F(Derived, extra)
  R(Buffer<int>) F(Buffer<int>, data) F(Buffer<int>, first)
  E(Color, Red) E(Color, Green) E(Color, Blue)
  E(Mode, Off) E(Mode, On)
  TD(handle_t) TD(count_t)
  return 0;
}
"""

C_HEADER = """\
#include <stdint.h>
typedef int64_t c_handle_t;
enum c_color { C_RED = 1, C_GREEN = 5 };
struct c_point { char tag; double x; int y; };
c_handle_t c_make(struct c_point p, enum c_color c);
"""

C_SOURCE = """\
#include "c_api.h"
c_handle_t c_make(struct c_point p, enum c_color c) { return p.y + c; }
"""

C_ORACLE = """\
#include "c_api.h"
#include <stddef.h>
#include <stdio.h>
int main(void) {
  printf("R c_point %zu %zu\\n", sizeof(struct c_point), _Alignof(struct c_point));
  printf("F c_point tag %zu\\n", offsetof(struct c_point, tag));
  printf("F c_point x %zu\\n", offsetof(struct c_point, x));
  printf("F c_point y %zu\\n", offsetof(struct c_point, y));
  printf("E c_color C_RED %d\\n", C_RED);
  printf("E c_color C_GREEN %d\\n", C_GREEN);
  printf("T c_handle_t %zu\\n", sizeof(c_handle_t));
  return 0;
}
"""


@dataclass(frozen=True)
class Oracle:
    records: dict[str, tuple[int, int]]  # name -> (size, align) in bytes
    offsets: dict[tuple[str, str], int]  # (record, field) -> offset in bytes
    enums: dict[str, dict[str, int]]
    typedef_sizes: dict[str, int]


def _parse_oracle(text: str) -> Oracle:
    records: dict[str, tuple[int, int]] = {}
    offsets: dict[tuple[str, str], int] = {}
    enums: dict[str, dict[str, int]] = {}
    typedefs: dict[str, int] = {}
    for line in text.splitlines():
        tag, *rest = line.split(" ")
        if tag == "R":
            records[rest[0]] = (int(rest[1]), int(rest[2]))
        elif tag == "F":
            offsets[(rest[0], rest[1])] = int(rest[2])
        elif tag == "E":
            enums.setdefault(rest[0], {})[rest[1]] = int(rest[2])
        elif tag == "T":
            typedefs[rest[0]] = int(rest[1])
    return Oracle(records, offsets, enums, typedefs)


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    # A configured compiler that ran and failed is a failure, never a skip
    # (tests/CLAUDE.md, guard.absent_capability_vs_real_failure).
    assert proc.returncode == 0, (argv, proc.stdout, proc.stderr)
    return proc


@dataclass(frozen=True)
class Fixture:
    root: Path
    lib: Path
    debug_lib: Path
    header: Path
    oracle: Oracle
    c_lib: Path
    c_header: Path
    c_oracle: Oracle


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    missing = [t for t in ("gcc", "g++", "ld") if not shutil.which(t)]
    if missing:
        pytest.skip(f"the fixture build needs gcc, g++ and ld: {missing}")
    root = tmp_path_factory.mktemp("default-install")
    (root / "api.h").write_text(CXX_HEADER, encoding="utf-8")
    (root / "api.cpp").write_text(CXX_SOURCE, encoding="utf-8")
    (root / "oracle.cpp").write_text(CXX_ORACLE, encoding="utf-8")
    (root / "c_api.h").write_text(C_HEADER, encoding="utf-8")
    (root / "c_api.c").write_text(C_SOURCE, encoding="utf-8")
    (root / "c_oracle.c").write_text(C_ORACLE, encoding="utf-8")
    common = ["-shared", "-fPIC", "-O1", f"-I{root}"]
    # No -g: no DWARF can backfill what the header frontend did not produce.
    _run(
        [
            "g++",
            "-std=c++17",
            *common,
            str(root / "api.cpp"),
            "-o",
            str(root / "libapi.so"),
        ]
    )
    _run(
        [
            "g++",
            "-std=c++17",
            "-g",
            *common,
            str(root / "api.cpp"),
            "-o",
            str(root / "libapi_g.so"),
        ]
    )
    _run(["gcc", *common, str(root / "c_api.c"), "-o", str(root / "libc_api.so")])
    _run(
        [
            "g++",
            "-std=c++17",
            "-Wno-invalid-offsetof",
            f"-I{root}",
            str(root / "oracle.cpp"),
            "-o",
            str(root / "oracle"),
        ]
    )
    _run(["gcc", f"-I{root}", str(root / "c_oracle.c"), "-o", str(root / "c_oracle")])
    return Fixture(
        root=root,
        lib=root / "libapi.so",
        debug_lib=root / "libapi_g.so",
        header=root / "api.h",
        oracle=_parse_oracle(_run([str(root / "oracle")]).stdout),
        c_lib=root / "libc_api.so",
        c_header=root / "c_api.h",
        c_oracle=_parse_oracle(_run([str(root / "c_oracle")]).stdout),
    )


def _default_install_env(
    tmp: Path, frontend: str | None, *, with_linker: bool = True
) -> dict[str, str]:
    """A default-install environment: frontends on PATH, nothing optional."""
    shim = tmp / "bin"
    shim.mkdir(parents=True, exist_ok=True)
    tools = _FRONTEND_TOOLS + (_LINKER_TOOLS if with_linker else ())
    for name in tools:
        found = shutil.which(name)
        if found and not (shim / name).exists():
            # Linked by name, so a driver sees the argv[0] it was asked for
            # (clang++ -> clang must still run in C++ mode).
            (shim / name).symlink_to(os.path.realpath(found))
    blocker = tmp / "blocked-modules"
    blocker.mkdir(exist_ok=True)
    for module in hidden_modules():
        (blocker / f"{module}.py").write_text(
            f"raise ImportError('{module} is hidden by the default-install suite')\n",
            encoding="utf-8",
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ABICHECK_") and k not in ("PYTHONPATH", "PATH")
    }
    env["PATH"] = str(shim)
    env["PYTHONPATH"] = os.pathsep.join([str(blocker), str(REPO_ROOT)])
    cache = tmp / "xdg-cache"
    cache.mkdir(exist_ok=True)
    env["XDG_CACHE_HOME"] = str(cache)
    if frontend:
        env["ABICHECK_AST_FRONTEND"] = frontend
    for name in hidden_binaries():
        assert shutil.which(name, path=env["PATH"]) is None, name
    if not with_linker:
        assert shutil.which("ld", path=env["PATH"]) is None
    return env


def _full_env(tmp: Path, frontend: str | None) -> dict[str, str]:
    """The developer's own environment (optional tools reachable if installed)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ABICHECK_")}
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
    )
    cache = tmp / "xdg-cache-full"
    cache.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = str(cache)
    if frontend:
        env["ABICHECK_AST_FRONTEND"] = frontend
    return env


def _dump(env: dict[str, str], out: Path, lib: Path, *args: str | Path) -> Any:
    proc = subprocess.run(
        abicheck_command("dump", lib, *args, "-o", out),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return load_snapshot(out)


# --- snapshot readers --------------------------------------------------------


def _records(snap: Any) -> dict[str, Any]:
    return {
        (t.qualified_name or t.name).removeprefix("lib::"): t
        for t in snap.declarations.types
        if not t.is_template_pattern
    }


def _iter_facts(obj: Any, path: str = "") -> Iterator[tuple[str, Fact[Any]]]:
    """Every ``Fact`` reachable from a declaration, with its attribute path."""
    if isinstance(obj, Fact):
        yield path, obj
        return
    if isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            yield from _iter_facts(item, f"{path}[{i}]")
        return
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            if f.name.endswith("_fact") or f.name in ("fields", "params", "members"):
                yield from _iter_facts(getattr(obj, f.name, None), f"{path}.{f.name}")


def _assert_no_empty_as_value(snap: Any) -> None:
    """A gap carries no value; PRESENT carries one where the family is scalar."""
    decls = snap.declarations
    for group in (decls.types, decls.functions, decls.enums, decls.variables):
        for entity in group:
            for path, fact in _iter_facts(entity, getattr(entity, "name", "?")):
                # `.value`, not `value_or`: value_or returns the default for
                # every gap status, so it would hide a stored value.
                if fact.status in GAP_STATUSES:
                    assert fact.value is None, (path, fact)
    # ``None`` is the model's "the producer captured none": an explicit unknown.
    for name, fact in (snap.typedef_abi_facts or {}).items():
        if fact.status in GAP_STATUSES:
            assert fact.value is None, (name, fact)
        if fact.status is FactStatus.PRESENT:
            assert fact.value is not None, (name, fact)


def _assert_layout_matches(
    snap: Any, oracle: Oracle, *, alignment_known: bool = True
) -> None:
    """Sizes and offsets equal the oracle. *alignment_known* False admits a
    ``None`` alignment (DWARF records none without ``DW_AT_alignment``) but
    never a wrong or zero one."""
    records = _records(snap)
    for name, (size, align) in oracle.records.items():
        assert name in records, (name, sorted(records))
        rec = records[name]
        assert rec.size_bits == size * 8, (name, rec.size_bits)
        if alignment_known or rec.alignment_bits is not None:
            assert rec.alignment_bits == align * 8, (name, rec.alignment_bits)
    for (name, field_name), offset in oracle.offsets.items():
        by_name = {f.name: f for f in records[name].fields}
        assert by_name[field_name].offset_bits == offset * 8, (name, field_name)


def _assert_enums_match(snap: Any, oracle: Oracle) -> None:
    enums = {
        (e.qualified_name or e.name).removeprefix("lib::"): e
        for e in snap.declarations.enums
    }
    for name, members in oracle.enums.items():
        assert name in enums, (name, sorted(enums))
        assert {m.name: m.value for m in enums[name].members} == members


def _assert_typedef_targets(
    snap: Any, oracle: Oracle, expected: dict[str, set[str]]
) -> None:
    targets = {
        k.removeprefix("lib::"): v
        for k, v in dict(snap.declarations.typedefs_qualified).items()
    }
    targets.update(snap.declarations.typedefs)
    for name, spellings in expected.items():
        assert targets.get(name) in spellings, (name, targets.get(name))
    facts = {k.removeprefix("lib::"): v for k, v in snap.typedef_abi_facts.items()}
    for name, size in oracle.typedef_sizes.items():
        fact = facts[name]
        assert fact.status is FactStatus.PRESENT, (name, fact)
        assert fact.value_or(None).size == size, (name, fact)


# --- the invariants ----------------------------------------------------------


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_header_facts_survive_without_optional_tools(
    fixture: Fixture, frontend: str, tmp_path: Path
) -> None:
    env = _default_install_env(tmp_path, frontend)
    snap = _dump(env, tmp_path / "s.json", fixture.lib, "-H", fixture.header)
    assert snap.ast_producer == frontend
    oracle = fixture.oracle

    # sizes, alignment, offsets -- including the explicitly instantiated
    # Buffer<int>, which is a record of its own under both frontends.
    _assert_layout_matches(snap, oracle)
    _assert_enums_match(snap, oracle)
    _assert_typedef_targets(
        snap,
        oracle,
        {"handle_t": {"int64_t", "std::int64_t"}, "count_t": {"unsigned int"}},
    )

    records = _records(snap)
    # vtable: three slots in declaration order, the vptr at offset 0.
    shape = records["Shape"]
    assert shape.vtable_fact.status is FactStatus.PRESENT
    slots = shape.vtable_fact.value_or(None)
    assert slots[1:] == ["_ZNK3lib5Shape4areaEv", "_ZN3lib5Shape5scaleEd"], slots
    assert slots[0] in ("~Shape", "_ZN3lib5ShapeD1Ev", "_ZN3lib5ShapeD0Ev"), slots
    assert shape.vptr_offset_bits == 0
    derived = records["Derived"]
    assert "Shape" in [b.removeprefix("lib::") for b in derived.bases]
    assert "_ZNK3lib7Derived4areaEv" in derived.vtable_fact.value_or(None)
    # A record with no virtuals has a PRESENT, empty vtable: established, not unknown.
    assert records["Point"].vtable_fact.status is FactStatus.PRESENT
    assert records["Point"].vtable_fact.value_or(None) == []

    # data size: clang computes it; castxml does not export it, so it is a gap.
    for name in ("Point", "Shape", "Buffer<int>"):
        dsize = records[name].data_size_bits_fact
        if frontend == "clang":
            assert dsize.status is FactStatus.PRESENT, (name, dsize)
            assert 0 < dsize.value_or(None) <= records[name].size_bits
        else:
            assert dsize.status is FactStatus.NOT_COLLECTED, (name, dsize)

    # explicit instantiation: clang reads `extern template class Buffer<int>;`;
    # castxml cannot, and says so instead of claiming the instance is implicit.
    size_fn = next(
        f
        for f in snap.declarations.functions
        if f.mangled == "_ZNK3lib6BufferIiE4sizeEv"
    )
    fact = size_fn.explicit_instantiation_fact
    if frontend == "clang":
        assert fact.status is FactStatus.PRESENT
        assert fact.value_or(None) is ExplicitInstantiation.DECLARATION
    else:
        assert fact.status is FactStatus.NOT_COLLECTED, fact

    # builtin ABI facts on a builtin slot (needs only the frontend's compiler).
    scale = next(
        f for f in snap.declarations.functions if f.mangled == "_ZN3lib5Shape5scaleEd"
    )
    abi = scale.params[0].type_abi_fact
    assert abi.status is FactStatus.PRESENT, abi
    assert abi.value_or(None).encoding is FundamentalEncoding.FLOAT
    assert abi.value_or(None).size == 8

    _assert_no_empty_as_value(snap)


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_c_header_facts_survive_without_optional_tools(
    fixture: Fixture, frontend: str, tmp_path: Path
) -> None:
    """The built-in clang layout pass once probed C++ only; a C header must be sized too."""
    env = _default_install_env(tmp_path, frontend)
    snap = _dump(env, tmp_path / "c.json", fixture.c_lib, "-H", fixture.c_header)
    assert snap.ast_producer == frontend
    _assert_layout_matches(snap, fixture.c_oracle)
    _assert_enums_match(snap, fixture.c_oracle)
    _assert_typedef_targets(
        snap, fixture.c_oracle, {"c_handle_t": {"int64_t", "long int", "long"}}
    )
    _assert_no_empty_as_value(snap)


def _assert_builtin_slot_unknown(
    kind: str, owner: str, fact: Fact[Any] | None, slots: dict[str, int]
) -> None:
    """A builtin slot's ABI fact is absent or a NOT_COLLECTED gap with no value."""
    if fact is None:
        return
    slots[kind] += 1
    assert fact.status is FactStatus.NOT_COLLECTED, (kind, owner, fact)
    assert fact.value is None, (kind, owner, fact)


@pytest.mark.known_gap_canary
@_NEEDS_CLANG
def test_clang_builtin_abi_facts_are_marked_without_a_linker(
    fixture: Fixture, tmp_path: Path
) -> None:
    """The clang builtin-ABI probe links and runs a program. Without a linker
    (or for a target the host cannot run) every builtin slot must read
    NOT_COLLECTED -- never a fabricated or empty fact -- and every other header
    family must stay present."""
    env = _default_install_env(tmp_path, "clang", with_linker=False)
    # Clang also looks for `ld` in its GCC installation's target bin directory,
    # so hiding it from PATH is not always enough. Ask clang itself.
    probe = tmp_path / "probe.c"
    probe.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    linked = subprocess.run(
        ["clang", "-x", "c", str(probe), "-o", str(tmp_path / "probe")],
        env=env,
        capture_output=True,
        text=True,
    )
    if linked.returncode == 0:
        pytest.skip("clang finds a linker outside PATH on this host")
    snap = _dump(env, tmp_path / "s.json", fixture.lib, "-H", fixture.header)
    _assert_layout_matches(snap, fixture.oracle)
    _assert_enums_match(snap, fixture.oracle)
    # Every builtin slot -- parameter, return and record field -- reads as a
    # gap with no value. Count the slots that carry a fact so the sweep cannot
    # pass vacuously on a snapshot that recorded none.
    slots: dict[str, int] = {"param": 0, "return": 0, "field": 0}
    for fn in snap.declarations.functions:
        facts = [("param", p.type_abi_fact) for p in fn.params]
        facts.append(("return", fn.return_type_abi_fact))
        for kind, fact in facts:
            _assert_builtin_slot_unknown(kind, fn.mangled, fact, slots)
    for name, rec in _records(snap).items():
        for f in rec.fields:
            _assert_builtin_slot_unknown(
                "field", f"{name}::{f.name}", f.type_abi_fact, slots
            )
    assert all(slots.values()), slots
    # Typedef ABI facts come from the same probe: the whole map is unknown.
    assert snap.typedef_abi_facts is None
    _assert_no_empty_as_value(snap)


@pytest.mark.known_gap_canary
@_NEEDS_CASTXML
@_NEEDS_CLANG
def test_clang_leaves_enum_slots_without_an_abi_fact(
    fixture: Fixture, tmp_path: Path
) -> None:
    """Known gap (docs/contribute/known-gaps.md): for ``make(Point, Color, Mode,
    count_t) -> handle_t`` castxml records the ABI fact of the enum parameters,
    and clang records NOT_COLLECTED for exactly those two slots. Clang records
    ``count_t``, plain builtins and, since extractor generation 54, the
    typedef'd return with castxml's value. Fails when clang starts recording
    the enum slots, or when the gap widens."""
    mangled = "_ZN3lib4makeENS_5PointENS_5ColorENS_4ModeEj"
    slots = {}
    for frontend in FRONTENDS_ONLY:
        env = _default_install_env(tmp_path / frontend, frontend)
        snap = _dump(
            env, tmp_path / f"{frontend}.json", fixture.lib, "-H", fixture.header
        )
        make = next(f for f in snap.declarations.functions if f.mangled == mangled)
        slots[frontend] = {
            "Color": make.params[1].type_abi_fact,
            "Mode": make.params[2].type_abi_fact,
            "count_t": make.params[3].type_abi_fact,
            "return": make.return_type_abi_fact,
        }
    expected = {  # (encoding, size) the oracle-backed castxml side records
        "Color": (FundamentalEncoding.UNSIGNED, 4),
        "Mode": (FundamentalEncoding.UNSIGNED, 2),
        "count_t": (FundamentalEncoding.UNSIGNED, 4),
        "return": (FundamentalEncoding.SIGNED, 8),
    }
    for slot, (encoding, size) in expected.items():
        fact = slots["castxml"][slot]
        assert fact.status is FactStatus.PRESENT, (slot, fact)
        assert (fact.value.encoding, fact.value.size) == (encoding, size), slot
    # The gap: clang marks the enum slots, never a wrong value.
    for slot in ("Color", "Mode"):
        fact = slots["clang"][slot]
        assert fact.status is FactStatus.NOT_COLLECTED, (slot, fact)
        assert fact.value is None
    for slot in ("count_t", "return"):
        fact = slots["clang"][slot]
        assert fact.status is FactStatus.PRESENT, (slot, fact)
        assert fact.value == slots["castxml"][slot].value, slot


def _fact_projection(snap: Any) -> dict[str, Any]:
    """The recorded facts of a header snapshot, without provenance or tool paths."""

    def fact(f: Fact[Any] | None) -> Any:
        return None if f is None else (f.status.value, repr(f.value_or(None)))

    return {
        "types": sorted(
            (
                t.qualified_name or t.name,
                t.size_bits,
                t.alignment_bits,
                tuple(
                    (f.name, f.offset_bits, f.type, fact(f.type_abi_fact))
                    for f in t.fields
                ),
                fact(t.vtable_fact),
                fact(t.data_size_bits_fact),
                fact(t.vptr_offset_bits_fact),
                tuple(t.bases),
            )
            for t in snap.declarations.types
        ),
        "functions": sorted(
            (
                f.mangled,
                f.return_type,
                fact(f.return_type_abi_fact),
                tuple((p.type, fact(p.type_abi_fact)) for p in f.params),
                fact(f.explicit_instantiation_fact),
            )
            for f in snap.declarations.functions
        ),
        "enums": sorted(
            (e.qualified_name or e.name, tuple((m.name, m.value) for m in e.members))
            for e in snap.declarations.enums
        ),
        "typedefs": sorted(dict(snap.declarations.typedefs).items()),
        "typedef_abi": sorted((k, fact(v)) for k, v in snap.typedef_abi_facts.items()),
    }


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_optional_tools_do_not_change_header_facts(
    fixture: Fixture, frontend: str, tmp_path: Path
) -> None:
    """Metamorphic: hiding every optional tool changes no recorded header fact."""
    bare = _dump(
        _default_install_env(tmp_path / "bare", frontend),
        tmp_path / "bare.json",
        fixture.lib,
        "-H",
        fixture.header,
    )
    full = _dump(
        _full_env(tmp_path / "full", frontend),
        tmp_path / "full.json",
        fixture.lib,
        "-H",
        fixture.header,
    )
    assert _fact_projection(bare) == _fact_projection(full)


def _without_provenance(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sections"].pop("provenance", None)
    return doc


def test_binary_facts_do_not_depend_on_optional_tools(
    fixture: Fixture, tmp_path: Path
) -> None:
    """--depth binary on a DWARF build: with or without demanglers and every
    other optional tool, the snapshot's facts are byte-identical."""
    bare_out, full_out = tmp_path / "bare.json", tmp_path / "full.json"
    bare = _dump(
        _default_install_env(tmp_path / "bare", None),
        bare_out,
        fixture.debug_lib,
        "--depth",
        "binary",
    )
    _dump(
        _full_env(tmp_path / "full", None),
        full_out,
        fixture.debug_lib,
        "--depth",
        "binary",
    )
    exported = {s.name for s in bare.elf.symbols}
    assert {"_ZN3lib4makeENS_5PointENS_5ColorENS_4ModeEj", "_ZN3lib3bufEv"} <= exported
    # DWARF layout is present at binary depth and matches the oracle.
    _assert_layout_matches(bare, fixture.oracle, alignment_known=False)
    assert _without_provenance(bare_out) == _without_provenance(full_out)

# SPDX-License-Identifier: Apache-2.0
"""Template-parameter default changes read the same on both header backends.

castxml 0.7 emits no element for an uninstantiated template, so its header
lane read every default change ``NO_CHANGE`` -- a false clean next to clang
(edit-corpus ``template_default_arg_change``). clang itself modelled neither
alias nor variable templates. Both backends now read the defaults of every
class, function, alias and variable template from the clang AST of the same
header (``extract/headers/clang/template_default_entries.py``; castxml through
``castxml/clang_semantics_join.py``). When castxml's join cannot run, the
defaults read as not observed (assurance ``partial``), never as unchanged.

Oracle: the C++ compiler, not abicheck. Each shape carries a consumer
translation unit written against the *old* header that pins what the old
default meant (``static_assert`` on the type or value it produced, or a use
that omits the argument). Whether it still compiles against the *new* header
decides whether the edit is a source break; abicheck's verdict must reach
``API_BREAK`` exactly when it does not.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.model.template_default_table import (
    added_defaults_only,
    merge_template_defaults,
    split_template_default_key,
    template_default_key,
)

_GAP = "template-parameter defaults"
_STD = "-std=c++20"
_KIND = "TEMPLATE_PARAM_DEFAULT_CHANGED"

_needs_toolchain = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("g++", "castxml", "clang++")),
    reason="needs g++, castxml and clang++",
)


@dataclass(frozen=True)
class Shape:
    name: str
    old: str
    new: str
    consumer: str  # written against the old header


_P2 = "template <class T, class U> struct P2 { T a; U b; };\n"
_SHAPES = (
    Shape(
        "class_nontype_changed",
        "template <typename T, int N = 4> struct Buf { T data[N]; };",
        "template <typename T, int N = 8> struct Buf { T data[N]; };",
        "static_assert(sizeof(Buf<int>) == 4 * sizeof(int));",
    ),
    Shape(
        "class_type_changed",
        "template <class T, class U = T> struct Box { T t; U u; };",
        "template <class T, class U = long> struct Box { T t; U u; };",
        "static_assert(same<decltype(Box<char>{}.u), char>::value);",
    ),
    Shape(
        "class_type_removed",
        "template <class T, class U = int> struct Box { T t; U u; };",
        "template <class T, class U> struct Box { T t; U u; };",
        "Box<char> b{};",
    ),
    Shape(
        # Negative control: no previously valid spelling changes meaning.
        "class_type_added",
        "template <class T, class U> struct Box { T t; U u; };",
        "template <class T, class U = int> struct Box { T t; U u; };",
        "Box<char, long> b{}; static_assert(sizeof(b.u) == sizeof(long));",
    ),
    Shape(
        "class_template_template_changed",
        _P2 + "template <class T, class U> struct P3 { T a; U b; char c; };\n"
        "template <template <class, class> class C = P2> struct Holder "
        "{ using type = C<int, int>; };",
        _P2 + "template <class T, class U> struct P3 { T a; U b; char c; };\n"
        "template <template <class, class> class C = P3> struct Holder "
        "{ using type = C<int, int>; };",
        "static_assert(same<Holder<>::type, P2<int, int>>::value);",
    ),
    Shape(
        "function_type_changed",
        "template <class T = int> inline T make() { return T(); }",
        "template <class T = long> inline T make() { return T(); }",
        "static_assert(same<decltype(make()), int>::value);",
    ),
    Shape(
        "member_function_template_changed",
        "struct S { template <class U = int> static U get() { return U(); } };",
        "struct S { template <class U = short> static U get() { return U(); } };",
        "static_assert(same<decltype(S::get()), int>::value);",
    ),
    Shape(
        # A member template whose default names the enclosing template's
        # parameter: the outer rename alone is no change (see the rename
        # shape), retargeting the default is.
        "nested_member_default_retargeted",
        "template <class T> struct W { template <class U = T> static U get() "
        "{ return U(); } };",
        "template <class T> struct W { template <class U = long> static U get() "
        "{ return U(); } };",
        "static_assert(same<decltype(W<char>::get()), char>::value);",
    ),
    Shape(
        "alias_type_changed",
        _P2 + "template <class T, class U = T> using Pair = P2<T, U>;",
        _P2 + "template <class T, class U = long> using Pair = P2<T, U>;",
        "static_assert(same<Pair<char>, P2<char, char>>::value);",
    ),
    Shape(
        "alias_type_added",
        _P2 + "template <class T, class U> using Pair = P2<T, U>;",
        _P2 + "template <class T, class U = long> using Pair = P2<T, U>;",
        "static_assert(same<Pair<char, int>, P2<char, int>>::value);",
    ),
    Shape(
        # A macro-spelled default: the AST reads the expansion, which a
        # text scan cannot.
        "alias_macro_default_changed",
        "#define DFLT(x) = x\n" + _P2 + "template <class T, class U DFLT(int)> "
        "using Pair = P2<T, U>;",
        "#define DFLT(x) = x\n" + _P2 + "template <class T, class U DFLT(long)> "
        "using Pair = P2<T, U>;",
        "static_assert(same<Pair<char>, P2<char, int>>::value);",
    ),
    Shape(
        "variable_nontype_changed",
        "template <class T, int N = 1> constexpr int scaled = sizeof(T) * N;",
        "template <class T, int N = 2> constexpr int scaled = sizeof(T) * N;",
        "static_assert(scaled<int> == sizeof(int));",
    ),
    Shape(
        "variable_type_removed",
        "template <class T, class U = int> constexpr int width = sizeof(U);",
        "template <class T, class U> constexpr int width = sizeof(U);",
        "int w = width<char>;",
    ),
    Shape(
        # Negative control: a pure parameter rename keeps every default.
        "dependent_default_renamed",
        "template <class T, class U = T> struct Box { T t; U u; };",
        "template <class X, class Y = X> struct Box { X t; Y u; };",
        "static_assert(same<decltype(Box<char>{}.u), char>::value);",
    ),
    Shape(
        # Negative control: nothing changed.
        "unchanged",
        _P2 + "template <class T, class U = int> using Pair = P2<T, U>;\n"
        "template <class T, int N = 2> constexpr int scaled = sizeof(T) * N;",
        _P2 + "template <class T, class U = int> using Pair = P2<T, U>;\n"
        "template <class T, int N = 2> constexpr int scaled = sizeof(T) * N;",
        "static_assert(scaled<int> == 2 * sizeof(int));",
    ),
)

# A trait of its own, not <type_traits>: the standard library's class
# templates carry defaults the AST walk cannot spell, which would name an
# unrelated gap on every shape.
_PRELUDE = (
    "#pragma once\nint api(int);\n"
    "template <class A, class B> struct same { static constexpr bool value = false; };\n"
    "template <class A> struct same<A, A> { static constexpr bool value = true; };\n"
)

# Mach-O records the output path as the install name; give both builds one.
_INSTALL_NAME = (
    ["-Wl,-install_name,@rpath/libapi.so"] if sys.platform == "darwin" else []
)


def _write(d: Path, header_body: str) -> tuple[Path, Path]:
    d.mkdir(parents=True, exist_ok=True)
    header = d / "api.h"
    header.write_text(_PRELUDE + header_body + "\n")
    src = d / "api.cpp"
    src.write_text('#include "api.h"\nint api(int x) { return x; }\n')
    so = d / "libapi.so"
    proc = subprocess.run(
        ["g++", _STD, "-shared", "-fPIC", *_INSTALL_NAME, "-o", str(so), str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"library did not build:\n{proc.stderr}"
    return so, header


def _consumer_compiles(header: Path, consumer: str, out: Path) -> bool:
    tu = out / "consumer.cpp"
    tu.write_text(f'#include "{header}"\n{consumer}\n')
    proc = subprocess.run(
        ["g++", _STD, "-fsyntax-only", str(tu)], capture_output=True, text=True
    )
    return proc.returncode == 0


def _dumps(
    tmp: Path, shape: Shape, backend: str
) -> tuple[AbiSnapshot, AbiSnapshot, Path, Path]:
    old_so, old_h = _write(tmp / "old", shape.old)
    new_so, new_h = _write(tmp / "new", shape.new)
    old, new = (
        dump(
            so,
            [h],
            version=v,
            compiler="c++",
            lang="c++",
            header_backend=backend,
            gcc_options=_STD,
        )
        for v, so, h in (("1", old_so, old_h), ("2", new_so, new_h))
    )
    return old, new, old_h, new_h


def _gap_notes(result: object) -> list[str]:
    notes = result.analysis_assurance.notes  # type: ignore[attr-defined]
    return [n for n in notes if n.startswith(_GAP)]


@pytest.mark.integration
@_needs_toolchain
@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize("shape", _SHAPES, ids=lambda s: s.name)
def test_verdict_matches_the_compiler_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, backend: str
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    old, new, old_h, new_h = _dumps(tmp_path, shape, backend)
    result = compare(old, new)
    assert _consumer_compiles(old_h, shape.consumer, tmp_path / "old")
    source_break = not _consumer_compiles(new_h, shape.consumer, tmp_path / "new")
    kinds = {c.kind.name for c in result.changes}
    verdict = result.verdict.name
    assert (_KIND in kinds) is source_break, (backend, verdict, sorted(kinds))
    assert (verdict in ("API_BREAK", "BREAKING")) is source_break, (
        backend,
        verdict,
        sorted(kinds),
    )
    if not source_break:
        assert verdict == "NO_CHANGE", (backend, sorted(kinds))
    assert _gap_notes(result) == []
    assert result.analysis_assurance.status == "complete", (
        backend,
        result.analysis_assurance.notes,
    )


def _no_clang(*_a: object, **_k: object) -> object:
    raise SnapshotError("clang not available (simulated)")


@pytest.mark.integration
@_needs_toolchain
@pytest.mark.parametrize(
    "shape",
    # A macro-spelled default is seen only by the AST: without clang the
    # header text scan misses it (known-gaps.md, the residual of the castxml
    # template-default entry).
    [
        s
        for s in _SHAPES
        if s.name.endswith(("_changed", "_removed")) and "macro" not in s.name
    ],
    ids=lambda s: s.name,
)
def test_castxml_without_its_clang_join_reports_the_defaults_unobserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape
) -> None:
    """Weaker evidence narrows the conclusion: no clang for the join means the
    defaults are *not observed* -- no finding and a ``partial`` rollup naming
    the dimension -- never a clean pass."""
    import abicheck.extract.headers.castxml.clang_semantics_join as join

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(join, "clang_header_dump", _no_clang)
    old, new, _old_h, _new_h = _dumps(tmp_path, shape, "castxml")
    assert (old.cxx_template_defaults, new.cxx_template_defaults) == (None, None)
    result = compare(old, new)
    assert result.analysis_assurance.status != "complete"
    assert _gap_notes(result), result.analysis_assurance.notes
    assert _KIND not in {c.kind.name for c in result.changes}


@pytest.mark.integration
@_needs_toolchain
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_a_stored_baseline_compares_like_the_live_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Live and stored operands agree: the table survives a save and load
    (the ``abicheck.cxx_template_defaults`` extension fact)."""
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    by_name = {s.name: s for s in _SHAPES}
    old, new, _old_h, _new_h = _dumps(tmp_path, by_name["alias_type_changed"], backend)
    stored = tmp_path / "old.json"
    save_snapshot(old, stored)
    loaded = load_snapshot(stored)
    assert loaded.cxx_template_defaults == old.cxx_template_defaults
    assert old.cxx_template_defaults is not None
    live, from_disk = compare(old, new), compare(loaded, new)
    assert [(c.kind, c.symbol) for c in from_disk.changes] == [
        (c.kind, c.symbol) for c in live.changes
    ]
    assert from_disk.verdict == live.verdict
    assert _KIND in {c.kind.name for c in live.changes}


# --------------------------------------------------------------------------
# Fast: the table's merge rule, the detector and the codec, on hand-built input.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "merged"),
    [
        ([("int", None)], ("int", None)),
        # A default stated on the declaration, not repeated on the definition.
        ([(None, "int"), (None, None)], (None, "int")),
        ([(None, None), ("long", None)], ("long", None)),
        # Two different spellings of one default: unknown.
        ([("int",), ("long",)], None),
        # A declaration whose defaults are unknown makes the template unknown.
        ([("int",), None], None),
        # A differing parameter count is not one template.
        ([("int",), ("int", None)], None),
        ([], None),
    ],
)
def test_merge_takes_each_default_from_any_declaration(
    rows: list[tuple[str | None, ...] | None], merged: tuple[str | None, ...] | None
) -> None:
    assert merge_template_defaults(rows) == merged


@pytest.mark.parametrize(
    ("before", "after", "added_only"),
    [
        ((None, None), (None, "int"), True),
        ((None, "int"), (None, "long"), False),
        ((None, "int"), (None, None), False),
        ((None, None), ("int", "int"), True),
        (("int", None), ("long", "int"), False),
    ],
)
def test_added_defaults_only(
    before: tuple[str | None, ...], after: tuple[str | None, ...], added_only: bool
) -> None:
    assert added_defaults_only(before, after) is added_only


@pytest.mark.parametrize("family", ["class", "function", "alias", "variable"])
def test_keys_round_trip(family: str) -> None:
    key = template_default_key(family, "ns::T(void ())")
    assert split_template_default_key(key) == (family, "ns::T(void ())")
    assert split_template_default_key("concept ns::C") == (None, "concept ns::C")
    with pytest.raises(ValueError):
        template_default_key("struct", "x")


def _table_snap(table: dict[str, tuple[str | None, ...] | None] | None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.ast_producer = "castxml"
    snap.cxx_template_defaults = table
    return snap


# (old row, new row, family, reported) -- by hand from the C++ rules.
_DETECTOR_CASES = [
    ((None, "int"), (None, "long"), "class", True),
    ((None, "int"), (None, None), "alias", True),
    ((None, None), (None, "int"), "class", False),  # added only
    ((None, None), (None, "int"), "alias", False),
    ((None, None), (None, "int"), "variable", False),
    ((None, None), (None, "int"), "function", True),  # can rebind a call
    ((None, "int"), (None, "int"), "variable", False),
    ((None, "int"), None, "class", False),  # unknown on a side
    ((None, "int"), ("int",), "class", False),  # parameter count changed
]


@pytest.mark.parametrize(("before", "after", "family", "reported"), _DETECTOR_CASES)
def test_table_detector(
    before: tuple[str | None, ...],
    after: tuple[str | None, ...] | None,
    family: str,
    reported: bool,
) -> None:
    from abicheck.compare.template_default_table import (
        detect_template_default_table_changes,
    )

    name = "ns::f(void ())" if family == "function" else "ns::T"
    key = template_default_key(family, name)
    findings, suppressed = detect_template_default_table_changes(
        _table_snap({key: before}), _table_snap({key: after})
    )
    assert suppressed == []
    assert [(c.kind.name, c.symbol) for c in findings] == (
        [(_KIND, name)] if reported else []
    )


def test_table_detector_needs_both_tables() -> None:
    from abicheck.compare.template_default_table import (
        detect_template_default_table_changes,
    )

    key = template_default_key("class", "T")
    assert detect_template_default_table_changes(
        _table_snap(None), _table_snap({key: ("int",)})
    ) == ([], [])


def test_table_finding_covered_by_an_instantiation_is_deduplicated() -> None:
    from abicheck.compare.template_default_table import (
        detect_template_default_table_changes,
    )

    key = template_default_key("class", "ns::Box")
    findings, suppressed = detect_template_default_table_changes(
        _table_snap({key: (None, "int")}),
        _table_snap({key: (None, "long")}),
        instantiation_stems={"ns::Box::get"},
    )
    assert findings == []
    assert [c.symbol for c in suppressed] == ["ns::Box"]


def test_a_template_the_pattern_facts_compare_is_not_reported_twice() -> None:
    """The clang backend also records a class template as a pattern record
    with a defaults fact; that detector reports it, the table does not."""
    from abicheck.compare.template_default_table import (
        detect_template_default_table_changes,
    )
    from abicheck.model import Fact, RecordType

    key = template_default_key("class", "ns::Box")
    old, new = _table_snap({key: (None, "int")}), _table_snap({key: (None, "long")})
    for snap, row in ((old, (None, "int")), (new, (None, "long"))):
        snap.declarations.types.append(
            RecordType(
                name="Box",
                kind="struct",
                qualified_name="ns::Box",
                is_template_pattern=True,
                template_param_defaults_fact=Fact.present(row),
            )
        )
    assert detect_template_default_table_changes(old, new) == ([], [])


@pytest.mark.parametrize(
    "raw",
    [
        {"class T": [None, "int"], "alias P": None, "variable v": []},
        {},
    ],
)
def test_extension_fact_round_trips(raw: dict[str, object]) -> None:
    from abicheck.storage.extension_fact_catalog import CXX_TEMPLATE_DEFAULTS

    assert CXX_TEMPLATE_DEFAULTS.decode(CXX_TEMPLATE_DEFAULTS.encode(raw)) == raw


@pytest.mark.parametrize(
    "bad",
    [["class T"], {"class T": "int"}, {"class T": [1]}, {1: ["int"]}],
)
def test_extension_fact_refuses_a_malformed_table(bad: object) -> None:
    from abicheck.storage.extension_fact_catalog import CXX_TEMPLATE_DEFAULTS

    with pytest.raises(TypeError):
        CXX_TEMPLATE_DEFAULTS.decode(bad)


def test_assurance_names_only_unknown_templates() -> None:
    from abicheck.policy.analysis_assurance._template_defaults import (
        template_default_findings,
    )

    known = _table_snap({"class T": (None, "int"), "alias P": (None,)})
    # The join's table overrides the header text scan: nothing is unassessed.
    known.template_default_header_scan = True
    unknown = _table_snap({"class T": (None, "int"), "variable v": None})
    unknown.template_default_header_scan = True
    assert template_default_findings(known, known) == []
    (finding,) = template_default_findings(known, unknown)
    assert finding.side == "new"
    assert "1 template(s)" in finding.reason
    # castxml whose join did not run: the header scan's gap.
    blind = _table_snap(None)
    blind.template_default_header_scan = True
    (finding,) = template_default_findings(None, blind)
    assert "clang join did not run" in finding.reason


@pytest.mark.integration
@pytest.mark.known_gap_canary
@_needs_toolchain
def test_castxml_without_clang_misses_a_macro_spelled_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Known gap (residual of the castxml template-default entry): without a
    clang for the join, castxml falls back to the header text scan, which does
    not expand macros, so a default spelled by one is neither reported nor
    named as a gap. With clang the AST reads it (the oracle test above)."""
    import abicheck.extract.headers.castxml.clang_semantics_join as join

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(join, "clang_header_dump", _no_clang)
    by_name = {s.name: s for s in _SHAPES}
    old, new, _old_h, _new_h = _dumps(
        tmp_path, by_name["alias_macro_default_changed"], "castxml"
    )
    result = compare(old, new)
    assert result.verdict.name == "NO_CHANGE"
    assert _KIND not in {c.kind.name for c in result.changes}
    assert _gap_notes(result) == []


_BOX = "template <class T> struct Box { T t; };\n"
_SPEC = "template <> struct Box<char> {{ template <class U = {d}> static U get() {{ return U(); }} }};"


@pytest.mark.integration
@_needs_toolchain
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_a_specialization_member_default_is_never_a_silent_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """A member template of an explicit specialization: clang records it as a
    function pattern and reports the change; the table cannot key it (the
    specialization has no name of its own), so castxml names the gap. Neither
    reads a complete clean ``NO_CHANGE``."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    shape = Shape(
        "specialization_member_changed",
        _BOX + _SPEC.format(d="int"),
        _BOX + _SPEC.format(d="long"),
        "static_assert(same<decltype(Box<char>::get()), int>::value);",
    )
    old, new, _old_h, _new_h = _dumps(tmp_path, shape, backend)
    result = compare(old, new)
    reported = _KIND in {c.kind.name for c in result.changes}
    if backend == "clang":
        assert reported and result.verdict.name == "API_BREAK"
    else:
        assert not reported
        assert result.analysis_assurance.status == "partial"
        assert any("could not be spelled" in n for n in _gap_notes(result))


@pytest.mark.integration
@_needs_toolchain
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_an_explicit_instantiation_names_no_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Negative control: ``template struct Box<double>;`` repeats the primary
    template's members; it is not a specialization whose members go unkeyed."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    header = (
        "template <class T> struct Box { template <class U = int> U get() const "
        "{ return U(); } };\ntemplate struct Box<double>;"
    )
    shape = Shape("explicit_instantiation", header, header, "Box<double> b{};")
    old, new, _old_h, _new_h = _dumps(tmp_path, shape, backend)
    result = compare(old, new)
    assert result.verdict.name == "NO_CHANGE"
    assert _gap_notes(result) == []
    assert result.analysis_assurance.status == "complete"

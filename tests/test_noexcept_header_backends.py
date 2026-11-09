# SPDX-License-Identifier: Apache-2.0
"""``noexcept`` and C++20 constraint edits read the same on both header backends.

castxml writes neither a ``noexcept`` specification nor an uninstantiated
template, so its header lane read both edits ``NO_CHANGE`` -- a false clean
next to clang, which reads both (edit-corpus ``method_noexcept_add`` and
``concept_constraint_added``). castxml now reads them from the clang AST of
the same header (``extract/headers/castxml/clang_semantics_join.py``), and
when that join cannot run the facts are reported as not observed (assurance
``partial``), never as unchanged.

Oracle: the C++ compiler, not abicheck. Each shape carries a consumer
translation unit written against the *old* header (an overrider of every
virtual with the old signature, a call with the argument type the old
template accepted). Whether it still compiles against the *new* header
decides whether the edit is a source break; abicheck's verdict must reach
``API_BREAK`` exactly when it does not.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.errors import SnapshotError
from abicheck.policy.analysis_assurance._concepts import CONCEPT_GAP_DIMENSION
from abicheck.policy.analysis_assurance._noexcept import NOEXCEPT_GAP_DIMENSION

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(shutil.which(t) for t in ("g++", "castxml", "clang++")),
        reason="needs g++, castxml and clang++",
    ),
]

_STD = "-std=c++20"


@dataclass(frozen=True)
class Shape:
    name: str
    old: str
    new: str
    source: str  # the library's out-of-line definitions (old and new alike)
    consumer: str  # written against the old header
    kinds: frozenset[str]  # kinds both backends must report


_SHAPE_VIRTUAL = (
    "struct Shape { virtual ~Shape(); virtual int area() const{q}; int w; };"
)
_SHAPES = (
    Shape(
        "virtual_method_noexcept_added",
        _SHAPE_VIRTUAL.replace("{q}", ""),
        _SHAPE_VIRTUAL.replace("{q}", " noexcept"),
        "Shape::~Shape() {} int Shape::area() const {q} { return w; }",
        "struct Sq : Shape { int area() const override { return 1; } };",
        frozenset({"FUNC_NOEXCEPT_ADDED", "FUNC_VIRTUAL_NOEXCEPT_ADDED"}),
    ),
    Shape(
        # A macro spelling: the join reads the expanded specification.
        "virtual_method_noexcept_macro",
        "#define API_NOTHROW\n" + _SHAPE_VIRTUAL.replace("{q}", " API_NOTHROW"),
        "#define API_NOTHROW noexcept\n"
        + _SHAPE_VIRTUAL.replace("{q}", " API_NOTHROW"),
        "Shape::~Shape() {} int Shape::area() const {q} { return w; }",
        "struct Sq : Shape { int area() const override { return 1; } };",
        frozenset({"FUNC_NOEXCEPT_ADDED", "FUNC_VIRTUAL_NOEXCEPT_ADDED"}),
    ),
    Shape(
        "virtual_method_noexcept_removed",
        _SHAPE_VIRTUAL.replace("{q}", " noexcept"),
        _SHAPE_VIRTUAL.replace("{q}", ""),
        "Shape::~Shape() {} int Shape::area() const {q} { return w; }",
        "struct Sq : Shape { int area() const noexcept override { return 1; } };",
        frozenset({"FUNC_NOEXCEPT_REMOVED"}),
    ),
    Shape(
        "free_function_noexcept_added",
        "int count(int x);",
        "int count(int x) noexcept;",
        "int count(int x) {q} { return x; }",
        "int use() { return count(1); }",
        frozenset({"FUNC_NOEXCEPT_ADDED"}),
    ),
    Shape(
        "method_noexcept_added",
        "struct Box { int get() const; int v; };",
        "struct Box { int get() const noexcept; int v; };",
        "int Box::get() const {q} { return v; }",
        "int use(const Box& b) { return b.get(); }",
        frozenset({"FUNC_NOEXCEPT_ADDED"}),
    ),
    Shape(
        "constructor_noexcept_added",
        "struct Res { Res(int n); int n; };",
        "struct Res { Res(int n) noexcept; int n; };",
        "Res::Res(int v) {q} : n(v) {}",
        "Res make() { return Res(3); }",
        frozenset({"FUNC_NOEXCEPT_ADDED"}),
    ),
    Shape(
        # noexcept(false) is a throwing specification: nothing changed.
        "noexcept_false_is_not_noexcept",
        "int count(int x);",
        "int count(int x) noexcept(false);",
        "int count(int x) { return x; }",
        "int use() { return count(1); }",
        frozenset(),
    ),
    Shape(
        "requires_clause_added",
        "template <typename T> T clampv(T v) { return v < 0 ? T(0) : v; }\nint api(int);",
        "template <typename T> requires (sizeof(T) >= 4) T clampv(T v) "
        "{ return v < 0 ? T(0) : v; }\nint api(int);",
        "int api(int x) { return x; }",
        "short use() { return clampv((short)-3); }",
        frozenset({"CONCEPT_TIGHTENED"}),
    ),
    Shape(
        "concept_on_class_template",
        "#include <concepts>\ntemplate <typename T> struct Cell { T v; };\nint api(int);",
        "#include <concepts>\ntemplate <std::integral T> struct Cell { T v; };\nint api(int);",
        "int api(int x) { return x; }",
        "Cell<double> c{1.5};",
        frozenset({"CONCEPT_TIGHTENED"}),
    ),
    Shape(
        "abbreviated_template_constrained",
        "#include <concepts>\ninline int twice(auto v) { return int(v) * 2; }\nint api(int);",
        "#include <concepts>\ninline int twice(std::integral auto v) "
        "{ return int(v) * 2; }\nint api(int);",
        "int api(int x) { return x; }",
        "int use() { return twice(1.5); }",
        frozenset({"CONCEPT_TIGHTENED"}),
    ),
    Shape(
        # Unchanged constrained template: no finding, nothing unobserved.
        "unchanged_constraint",
        "#include <concepts>\ntemplate <std::integral T> T id(T v) { return v; }\nint api(int);",
        "#include <concepts>\ntemplate <std::integral T> T id(T v) { return v; }\nint api(int);",
        "int api(int x) { return x; }",
        "int use() { return id(1); }",
        frozenset(),
    ),
)


def _write(d: Path, side: str, header: str, source: str) -> tuple[Path, Path]:
    d.mkdir(parents=True, exist_ok=True)
    h = d / "api.h"
    h.write_text(header + "\n")
    src = d / "api.cpp"
    spec = (
        " noexcept" if "noexcept;" in header or "API_NOTHROW noexcept" in header else ""
    )
    src.write_text(f'#include "api.h"\n{source.replace("{q}", spec)}\n')
    so = d / "libapi.so"
    proc = subprocess.run(
        ["g++", _STD, "-shared", "-fPIC", "-g", "-o", str(so), str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{side} library did not build:\n{proc.stderr}"
    return so, h


def _consumer_compiles(header: Path, consumer: str, out: Path) -> bool:
    tu = out / "consumer.cpp"
    tu.write_text(f'#include "{header}"\n{consumer}\n')
    proc = subprocess.run(
        ["g++", _STD, "-fsyntax-only", str(tu)], capture_output=True, text=True
    )
    return proc.returncode == 0


def _compare(tmp_path: Path, shape: Shape, backend: str) -> tuple[object, Path, Path]:
    old_so, old_h = _write(tmp_path / "old", "old", shape.old, shape.source)
    new_so, new_h = _write(tmp_path / "new", "new", shape.new, shape.source)
    snaps = [
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
    ]
    return compare(snaps[0], snaps[1]), old_h, new_h


@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize("shape", _SHAPES, ids=lambda s: s.name)
def test_verdict_matches_the_compiler_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, backend: str
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    result, old_h, new_h = _compare(tmp_path, shape, backend)
    assert _consumer_compiles(old_h, shape.consumer, tmp_path / "old")
    source_break = not _consumer_compiles(new_h, shape.consumer, tmp_path / "new")
    kinds = {c.kind.name for c in result.changes}  # type: ignore[attr-defined]
    verdict = result.verdict.name  # type: ignore[attr-defined]
    assert shape.kinds <= kinds, (backend, verdict, sorted(kinds))
    assert (verdict in ("API_BREAK", "BREAKING")) is source_break, (
        backend,
        verdict,
        sorted(kinds),
        source_break,
    )
    if not shape.kinds:
        assert verdict == "NO_CHANGE", (backend, sorted(kinds))
    notes = result.analysis_assurance.notes  # type: ignore[attr-defined]
    assert not [
        n
        for n in notes
        if n.startswith((NOEXCEPT_GAP_DIMENSION, CONCEPT_GAP_DIMENSION))
    ]


def _no_clang(*_a: object, **_k: object) -> object:
    raise SnapshotError("clang not available (simulated)")


@pytest.mark.parametrize(
    "shape",
    [s for s in _SHAPES if s.kinds],
    ids=lambda s: s.name,
)
def test_castxml_without_its_clang_join_reports_the_fact_unobserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape
) -> None:
    """Weaker evidence narrows the conclusion: no clang for the join means the
    specification or constraint is *not observed* -- a declined comparison
    and a ``partial`` rollup naming the dimension -- never a clean pass."""
    import abicheck.extract.headers.castxml.clang_semantics_join as join

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(join, "clang_header_dump", _no_clang)
    result, _old_h, _new_h = _compare(tmp_path, shape, "castxml")
    assurance = result.analysis_assurance  # type: ignore[attr-defined]
    assert assurance.status != "complete"
    dimension = (
        CONCEPT_GAP_DIMENSION
        if "CONCEPT_TIGHTENED" in shape.kinds
        else NOEXCEPT_GAP_DIMENSION
    )
    assert [n for n in assurance.notes if n.startswith(dimension)], assurance.notes
    kinds = {c.kind.name for c in result.changes}  # type: ignore[attr-defined]
    assert not (kinds & {"FUNC_NOEXCEPT_REMOVED", "FUNC_NOEXCEPT_ADDED"} - shape.kinds)


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_only_a_virtual_gaining_noexcept_reports_the_overrider_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """``func_virtual_noexcept_added`` is the virtual case alone: a plain
    method gaining ``noexcept`` and a virtual losing it never report it."""
    from abicheck.model.change_catalog.kinds import ChangeKind

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    by_name = {s.name: s for s in _SHAPES}
    reported = {}
    for name in (
        "virtual_method_noexcept_added",
        "method_noexcept_added",
        "virtual_method_noexcept_removed",
    ):
        result, _old_h, _new_h = _compare(tmp_path / name, by_name[name], backend)
        reported[name] = {c.kind for c in result.changes}  # type: ignore[attr-defined]
    assert (
        ChangeKind.FUNC_VIRTUAL_NOEXCEPT_ADDED
        in reported["virtual_method_noexcept_added"]
    )
    assert (
        ChangeKind.FUNC_VIRTUAL_NOEXCEPT_ADDED not in reported["method_noexcept_added"]
    )
    assert (
        ChangeKind.FUNC_VIRTUAL_NOEXCEPT_ADDED
        not in reported["virtual_method_noexcept_removed"]
    )


@pytest.mark.parametrize(
    "shape",
    [s for s in _SHAPES if "noexcept" in s.name],
    ids=lambda s: s.name,
)
def test_castxml_join_does_not_depend_on_matching_mangling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape
) -> None:
    """castxml and its clang join can mangle for different targets (clang's
    Windows default is MSVC, castxml emulates g++): the join still reads
    each specification, by qualified name and arity. Seen on the Windows
    integration lane; simulated here by targeting the join at MSVC."""
    import abicheck.extract.headers.castxml.clang_semantics_join as join

    real = join.clang_header_dump

    def msvc_join(
        headers: object, extra: object, compiler: object, **kw: object
    ) -> object:
        tokens = (
            *tuple(kw.pop("gcc_option_tokens", ()) or ()),
            "--target=x86_64-pc-windows-msvc",
        )  # type: ignore[arg-type]
        return real(headers, extra, compiler, gcc_option_tokens=tokens, **kw)  # type: ignore[arg-type]

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(join, "clang_header_dump", msvc_join)
    result, old_h, new_h = _compare(tmp_path, shape, "castxml")
    assert _consumer_compiles(old_h, shape.consumer, tmp_path / "old")
    source_break = not _consumer_compiles(new_h, shape.consumer, tmp_path / "new")
    kinds = {c.kind.name for c in result.changes}  # type: ignore[attr-defined]
    verdict = result.verdict.name  # type: ignore[attr-defined]
    assert shape.kinds <= kinds, (verdict, sorted(kinds))
    assert (verdict in ("API_BREAK", "BREAKING")) is source_break, (
        verdict,
        sorted(kinds),
    )
    assert result.analysis_assurance.status == "complete"  # type: ignore[attr-defined]

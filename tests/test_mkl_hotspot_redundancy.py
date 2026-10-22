"""Equivalence and call-count bounds for the MKL-profile hot-spot fixes.

Four per-symbol redundancies removed from one large comparison (MKL
``libmkl_intel_lp64``: 26k exports, 600k signature-spelling scans):

* ``has_unresolved_component`` walked every spelling char by char even when it
  contains no ``"?"`` sentinel at all;
* ``textual_declaration_hint`` compiled one regex per export before checking
  whether any header even names it (overflowing the ``re`` cache);
* ``_unresolved_type_edges`` re-judged the same spelling once per occurrence;
* ``detect_internal_template_leaks`` resolved each function's identity twice.

Each oracle below is written independently of the optimized code (a direct
re-implementation of the pre-change algorithm), never by calling the new
fast path or its cache.
"""

from __future__ import annotations

import itertools
import re

import pytest

import abicheck.export_surface as export_surface
from abicheck.buildsource.export_declaration_evidence import (
    DeclarationHint,
    ExportDeclarationEvidence,
    _HeaderText,
    textual_declaration_hint,
)
from abicheck.diff_templates import (
    _callable_identity_name,
    _functions_by_stem,
    _looks_like_template_instantiation,
    _strip_template_args,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, RecordType, TypeField
from abicheck.model.castxml_spelling_artifacts import has_unresolved_component

# --------------------------------------------------------------------------
# has_unresolved_component
# --------------------------------------------------------------------------


def _reference_unresolved(raw: str) -> bool:
    """The bracket-stack walk, re-stated without any fast path."""
    stack: list[str] = []
    i = 0
    while i < len(raw):
        if raw.startswith("_Atomic(", i):
            i += len("_Atomic(")
            continue
        ch = raw[i]
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if stack:
                stack.pop()
        elif ch == "<":
            if not stack or stack[-1] not in "([":
                stack.append(ch)
        elif ch == ">":
            if stack and stack[-1] == "<":
                stack.pop()
        elif ch == "?" and not stack:
            return True
        i += 1
    return False


_ALPHABET = "?<>()[]a "


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4])
def test_has_unresolved_component_matches_reference_exhaustively(length: int) -> None:
    for chars in itertools.product(_ALPHABET, repeat=length):
        raw = "".join(chars)
        assert has_unresolved_component(raw) is _reference_unresolved(raw), raw


@pytest.mark.parametrize(
    "raw",
    [
        "_Atomic(?)",
        "_Atomic(int)",
        "const ?*",
        "S<decltype(f ? A{} : B{})>",
        "S<(N >> 1 ? 1 : 2)>",
        "std::vector<int>",
        "int",
    ],
)
def test_has_unresolved_component_named_shapes(raw: str) -> None:
    assert has_unresolved_component(raw) is _reference_unresolved(raw)


# --------------------------------------------------------------------------
# textual_declaration_hint
# --------------------------------------------------------------------------


def _reference_hint(
    leaf: str, scope: str | None, evidence: ExportDeclarationEvidence
) -> DeclarationHint | None:
    """Pre-change body: eager compile, regex on every candidate line."""
    leaf_re = re.compile(rf"\b{re.escape(leaf)}\s*(\(|\[|;|=|,)")
    conditional = None
    for h in evidence.headers:
        if leaf not in h.identifiers or (scope and scope not in h.identifiers):
            continue
        for line, cond in h.lines:
            if not leaf_re.search(line):
                continue
            if h.excluded:
                return DeclarationHint("excluded_header", h.path, cond)
            if cond is not None and conditional is None:
                conditional = DeclarationHint("conditional", h.path, cond)
    return conditional


def _header(path, excluded, lines):
    idents = frozenset(
        tok for text, _ in lines for tok in re.findall(r"[A-Za-z_]\w*", text)
    )
    return _HeaderText(path=path, excluded=excluded, lines=lines, identifiers=idents)


_EVIDENCES = [
    ExportDeclarationEvidence(),
    ExportDeclarationEvidence(
        headers=(
            _header("a.h", False, [("int foo(int);", None), ("int bar;", "X")]),
            _header("b.h", True, [("void baz (void);", None), ("foobar(1);", None)]),
        )
    ),
    ExportDeclarationEvidence(
        headers=(
            _header("c.h", False, [("namespace ns {", None), ("int foo[3];", "Y")]),
            _header("d.h", False, [("ns::foo = 1, foo2;", "Z"), ("// foo", None)]),
        )
    ),
]

_SYMBOLS = ["foo", "bar", "baz", "foobar", "foo2", "absent", "_ZN2ns3fooEv"]


@pytest.mark.parametrize("evidence", _EVIDENCES)
@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_textual_hint_matches_reference(symbol, evidence) -> None:
    from abicheck.buildsource.export_declaration_evidence import _leaf_and_scope

    names = _leaf_and_scope(symbol)
    expected = (
        None
        if names is None or not evidence.headers
        else _reference_hint(names[0], names[1], evidence)
    )
    assert textual_declaration_hint(symbol, evidence) == expected


def test_textual_hint_compiles_nothing_when_no_header_names_the_symbol(
    monkeypatch,
) -> None:
    import abicheck.buildsource.export_declaration_evidence as mod

    compiled: list[str] = []
    real = re.compile

    class _Re:
        def __getattr__(self, name):
            return getattr(re, name)

        @staticmethod
        def compile(pattern, *a):
            compiled.append(pattern)
            return real(pattern, *a)

    mod._leaf_declaration_re.cache_clear()
    monkeypatch.setattr(mod, "re", _Re())
    for i in range(50):
        assert textual_declaration_hint(f"absent_{i}", _EVIDENCES[1]) is None
    assert compiled == []
    assert textual_declaration_hint("baz", _EVIDENCES[1]) == DeclarationHint(
        "excluded_header", "b.h", None
    )
    assert len(compiled) == 1


# --------------------------------------------------------------------------
# _unresolved_type_edges
# --------------------------------------------------------------------------


def _mkl_like_snapshot(n: int) -> AbiSnapshot:
    spellings = ["const double *", "Missing *", "int", "Holder *", "Alias", "float"]
    funcs = [
        Function(
            name=f"f{i}",
            mangled=f"f{i}",
            return_type=spellings[i % len(spellings)],
            params=[
                Param(name="a", type=spellings[(i + 1) % len(spellings)]),
                Param(name="b", type=spellings[(i + 3) % len(spellings)]),
            ],
        )
        for i in range(n)
    ]
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=funcs,
        types=[
            RecordType(
                name="Holder",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="g", type="Gone")],
            )
        ],
        typedefs={"Alias": "AliasTarget"},
        elf=ElfMetadata(symbols=[ElfSymbol(name=f"f{i}") for i in range(n)]),
    )
    return snap


@pytest.mark.parametrize("n", [1, 7, 60])
def test_unresolved_edges_judges_each_spelling_once(monkeypatch, n) -> None:
    calls: list[object] = []
    indexes: list[object] = []
    real = export_surface._edge_is_unresolved

    def counting(type_str, index):
        calls.append(type_str)
        indexes.append(index)
        return real(type_str, index)

    monkeypatch.setattr(export_surface, "_edge_is_unresolved", counting)
    surf = export_surface.compute_export_surface(_mkl_like_snapshot(n))
    assert len(calls) == len(set(calls))
    # Oracle: the union of each distinct scanned spelling's own judgment,
    # recomputed from scratch with the unwrapped helper.
    expected = set().union(*(real(t, indexes[0]) for t in set(calls)))
    assert surf.unresolved_type_edges == frozenset(expected)
    assert {"Missing", "Gone"} <= surf.unresolved_type_edges


# --------------------------------------------------------------------------
# _functions_by_stem
# --------------------------------------------------------------------------

_FUNCS = [
    Function(name="lib::detail::sort<int>", mangled="", return_type="void"),
    Function(name="lib::detail::sort<long>", mangled="", return_type="void"),
    Function(name="lib::detail::foo", mangled="", return_type="void"),
    Function(name="lib::detail::bar", mangled="", return_type="void"),
    Function(name="api::make<T>", mangled="", return_type="void"),
    Function(name="api::run", mangled="", return_type="void"),
]


@pytest.mark.parametrize(
    "old,new",
    [(_FUNCS[:3], _FUNCS[1:]), (_FUNCS, []), ([], _FUNCS[2:4]), (_FUNCS, _FUNCS)],
)
def test_functions_by_stem_collects_reference_template_stems(old, new) -> None:
    def ref_stems(funcs):
        out = set()
        for f in funcs:
            q = _callable_identity_name(f.name, f.mangled, {})
            if _looks_like_template_instantiation(q):
                out.add(_strip_template_args(q))
        return out

    stems: set[str] = set()
    by_old = _functions_by_stem(old, {}, stems)
    by_new = _functions_by_stem(new, {}, stems)
    assert stems == ref_stems(old) | ref_stems(new)
    assert sum(map(len, by_old.values())) == len(old)
    assert sum(map(len, by_new.values())) == len(new)

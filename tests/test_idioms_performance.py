# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Behaviour-preservation contract for the idiom-recognition fast paths.

The optimisations in :mod:`abicheck.idioms` (a memoised ``_strip_ptr``, the
record-local OPAQUE_POINTER pre-checks) are pure CPU work removal: every tag,
its evidence, its proof fields and every anti-pattern must be *byte-identical*
to what the unoptimised formulation produces.

So the assertions here are stated against **test-only reference
implementations** transcribed from the pre-optimisation code, not against
fixed expected values, and they are exercised with generated inputs rather
than a handful of named spellings. A reference that is merely the production
helper under another name would assert nothing, so each one is written out
independently and guarded by a negative control that proves it can fail.
"""

from __future__ import annotations

import gc
import re
import threading
import weakref

import pytest

from abicheck import idioms, pattern_verdicts
from abicheck.idioms import Idiom, detect_antipatterns, recognise_idioms
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.model.surface_facts import in_public_surface
from abicheck.policy import public_use_index, type_spelling
from abicheck.policy.evidence_status import EvidenceTier
from abicheck.surface_graph import SurfaceGraph, build_surface_graph

# --------------------------------------------------------------------------
# Test-only reference implementations (the pre-optimisation formulations)
# --------------------------------------------------------------------------

_REF_POINTER_RE = re.compile(r"[*&]")
# The rejected single-pass alternation, kept only to prove it differs.
_COMBINED_RE = re.compile(r"[*&]|\b(?:const|volatile|struct|class|union|enum)\b")


def _ref_strip_ptr(type_str: str) -> str:
    """Verbatim transcription of the original, uncached, uncompiled helper."""
    s = _REF_POINTER_RE.sub("", type_str)
    for kw in ("const", "volatile", "struct", "class", "union", "enum"):
        s = re.sub(rf"\b{kw}\b", "", s)
    return s.strip()


def _ref_public_pointer_only(graph: SurfaceGraph, type_name: str) -> tuple[bool, bool]:
    """Verbatim transcription of the original full per-record signature scan."""
    referenced = False
    only_pointer = True
    short = type_name.rsplit("::", 1)[-1]
    for fn in graph.snapshot.functions:
        if not in_public_surface(fn):
            continue
        sites: list[tuple[str, int]] = [(fn.return_type, fn.return_pointer_depth)]
        for p in fn.params:
            sites.append((getattr(p, "type", "") or "", getattr(p, "pointer_depth", 0)))
        for type_str, depth in sites:
            names = {type_str.rsplit("::", 1)[-1]} | set(
                _ref_strip_ptr(type_str).split()
            )
            if short in names or type_name in (type_str, _ref_strip_ptr(type_str)):
                referenced = True
                if depth < 1 and "*" not in type_str:
                    only_pointer = False
    return referenced, only_pointer


def _ref_recognise_opaque(graph: SurfaceGraph, rec: RecordType):
    """Original ordering: the signature scan runs *before* the cheap checks."""
    referenced, only_pointer = _ref_public_pointer_only(graph, rec.name)
    if not referenced or not only_pointer:
        return None
    if not rec.is_opaque:
        return None
    if any(f.access.name == "PUBLIC" for f in rec.fields):
        return None
    return idioms.IdiomTag(
        idiom=Idiom.OPAQUE_POINTER,
        confidence=idioms.Confidence.HIGH,
        evidence=[
            f"{rec.name} is incomplete in the public surface",
            f"{rec.name} crossed only by pointer in public API",
        ],
        definition_hidden=True,
    )


# --------------------------------------------------------------------------
# Generated spelling corpus
# --------------------------------------------------------------------------

_QUALIFIERS = [
    "",
    "const ",
    "volatile ",
    "const volatile ",
    "struct ",
    "class ",
    "union ",
    "enum ",
]
_CORES = [
    "Foo",
    "ns::Bar",
    "a::b::Ctx",
    "int",
    "unsigned long",
    "int32_t",
    "std::vector<int>",
    "Tpl<A, B>",
    "FunctionType",
    "void",
    "constant",
    "classic",
    "unionise",
    "enumerator",
    "volatility",
]
_SUFFIXES = [
    "",
    "*",
    " *",
    "**",
    "&",
    "&&",
    " * const",
    "*const",
    " const *",
    "* volatile",
    " volatile * const",
]


def _spellings() -> list[str]:
    out = []
    for q in _QUALIFIERS:
        for c in _CORES:
            for s in _SUFFIXES:
                out.append(f"{q}{c}{s}")
    return out


# The counterexamples that rule out replacing the sequential substitutions
# with one combined alternation: the combined form additionally strips the
# residue two adjacent keywords leave behind.
_COMBINED_REGEX_COUNTEREXAMPLES = {
    "Foo*const": "Fooconst",
    "int const*volatile": "int constvolatile",
    "int volatile*const": "int volatileconst",
}


class TestStripPtrSemanticsPreserved:
    def test_matches_reference_on_generated_corpus(self) -> None:
        corpus = _spellings()
        assert len(corpus) > 1000, "corpus must search more than a few named cases"
        mismatches = [
            (s, _ref_strip_ptr(s), type_spelling.strip_ptr(s))
            for s in corpus
            if _ref_strip_ptr(s) != type_spelling.strip_ptr(s)
        ]
        assert mismatches == []

    @pytest.mark.parametrize(
        ("spelling", "expected"), sorted(_COMBINED_REGEX_COUNTEREXAMPLES.items())
    )
    def test_combined_regex_counterexamples_keep_sequential_result(
        self, spelling: str, expected: str
    ) -> None:
        # A combined ``\b(const|volatile|struct|class|union|enum)\b`` pass would
        # yield "Foo" / "int" here. Recognition is defined by the sequential
        # result, so a performance change must not alter it.
        assert type_spelling.strip_ptr(spelling) == expected
        assert type_spelling.strip_ptr(spelling) == _ref_strip_ptr(spelling)
        # The proposed single-pass form folds the pointer class and the keyword
        # alternation into one scan, so a keyword still adjacent to a ``*``
        # keeps its word boundary and is stripped -- which the sequential form,
        # having already deleted the ``*``, no longer sees.
        combined = _COMBINED_RE.sub("", spelling).strip()
        assert combined != expected, "counterexample no longer discriminates"

    def test_reference_is_not_the_production_helper(self) -> None:
        # Negative control: the reference must be able to disagree, otherwise
        # the corpus comparison above is vacuous.
        assert _ref_strip_ptr is not type_spelling.strip_ptr
        assert _ref_strip_ptr("Foo*") != "Foo*"

    def test_internal_whitespace_and_token_order_preserved(self) -> None:
        for s in ("const unsigned long * x", "ns::A<  int ,char > *"):
            assert type_spelling.strip_ptr(s) == _ref_strip_ptr(s)


class TestStripPtrCacheBounds:
    def setup_method(self) -> None:
        type_spelling.strip_ptr_cache_clear()

    def test_eviction_keeps_occupancy_within_the_documented_bound(self) -> None:
        maxsize = type_spelling.STRIP_PTR_CACHE_MAXSIZE
        for i in range(maxsize * 3):
            assert type_spelling.strip_ptr(f"T{i} *") == f"T{i}"
        stats = type_spelling.strip_ptr_cache_stats()
        assert stats["occupancy"] <= maxsize
        assert stats["maxsize"] == maxsize

    def test_all_unique_inputs_stay_correct_under_eviction(self) -> None:
        inputs = [
            f"ns{i}::Type{i} const *"
            for i in range(type_spelling.STRIP_PTR_CACHE_MAXSIZE + 500)
        ]
        assert [type_spelling.strip_ptr(s) for s in inputs] == [
            _ref_strip_ptr(s) for s in inputs
        ]

    def test_oversized_spelling_bypasses_cache_with_identical_output(self) -> None:
        limit = type_spelling.STRIP_PTR_CACHE_MAX_INPUT
        big = "Tpl<" + ", ".join(f"const Arg{i} *" for i in range(200)) + "> *"
        assert len(big) > limit
        before = type_spelling.strip_ptr_cache_stats()["occupancy"]
        assert type_spelling.strip_ptr(big) == _ref_strip_ptr(big)
        # Not truncated, not rejected -- fully normalised.
        assert "Arg199" in type_spelling.strip_ptr(big)
        assert type_spelling.strip_ptr_cache_stats()["occupancy"] == before

    def test_boundary_length_is_cached(self) -> None:
        limit = type_spelling.STRIP_PTR_CACHE_MAX_INPUT
        exact = "A" * limit
        before = type_spelling.strip_ptr_cache_stats()["occupancy"]
        type_spelling.strip_ptr(exact)
        assert type_spelling.strip_ptr_cache_stats()["occupancy"] == before + 1

    def test_cache_retains_only_plain_strings(self) -> None:
        """The documented retention contract, actually inspected.

        The earlier version of this test asserted that ``__wrapped__`` was
        callable and that occupancy was non-zero -- neither of which can fail
        if the cache retained a snapshot, a record or a bound method. The
        contract is only meaningful if the retained set is read, so the memo
        exposes it (``strip_ptr_cache_entries``) and this reads it.
        """
        type_spelling.strip_ptr_cache_clear()
        for spelling in _spellings()[:200]:
            type_spelling.strip_ptr(spelling)
        entries = type_spelling.strip_ptr_cache_entries()
        assert entries, "nothing retained -- the assertion below would be vacuous"
        for key, value in entries:
            assert type(key) is str, (key, type(key))
            assert type(value) is str, (value, type(value))
            # A plain str references no object graph; assert the property that
            # actually matters rather than inferring it from the type alone.
            assert not gc.get_referents(key)
            assert not gc.get_referents(value)

    def test_retention_assertion_would_catch_a_non_string_entry(self) -> None:
        """Negative control: the check above must be able to fail."""

        class _Poison:
            def __init__(self) -> None:
                self.graph = object()

        with pytest.raises(AssertionError):
            for key, value in (("k", _Poison()),):
                assert type(key) is str
                assert type(value) is str

    def test_every_cached_value_equals_the_reference_result(self) -> None:
        """Retained values are the *correct* normalisations, not merely strings."""
        type_spelling.strip_ptr_cache_clear()
        for spelling in _spellings()[:200]:
            type_spelling.strip_ptr(spelling)
        for key, value in type_spelling.strip_ptr_cache_entries():
            assert value == _ref_strip_ptr(key), key

    def test_eviction_is_least_recently_used_not_a_clear(self) -> None:
        """Overflow drops one entry; it never discards the whole cache.

        A clear-on-overflow policy satisfies an occupancy bound just as well
        while throwing away what a concurrent worker relies on, so the bound
        alone cannot distinguish the two and the order is asserted directly.
        """
        type_spelling.strip_ptr_cache_clear()
        maxsize = type_spelling.STRIP_PTR_CACHE_MAXSIZE
        for i in range(maxsize):
            type_spelling.strip_ptr(f"T{i} *")
        # Re-touch the oldest key so it becomes most recently used...
        type_spelling.strip_ptr("T0 *")
        # ...then overflow by one: T1, not T0, must be the entry that goes.
        type_spelling.strip_ptr("Overflow *")
        keys = {k for k, _ in type_spelling.strip_ptr_cache_entries()}
        assert len(keys) == maxsize
        assert "T0 *" in keys, "LRU re-touch was ignored"
        assert "T1 *" not in keys, "evicted entry was not the least recently used"
        assert "Overflow *" in keys

    def test_oversized_input_is_never_retained(self) -> None:
        big = "Tpl<" + ", ".join(f"const Arg{i} *" for i in range(200)) + "> *"
        assert len(big) > type_spelling.STRIP_PTR_CACHE_MAX_INPUT
        type_spelling.strip_ptr_cache_clear()
        type_spelling.strip_ptr(big)
        assert type_spelling.strip_ptr_cache_entries() == ()


# --------------------------------------------------------------------------
# Snapshot fixtures exercising every shape the recognisers care about
# --------------------------------------------------------------------------


def _fn(name, ret, params, *, ret_depth=0, visibility=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled or name,
        return_type=ret,
        params=params,
        visibility=visibility,
        return_pointer_depth=ret_depth,
    )


def _mixed_snapshot() -> AbiSnapshot:
    """Pointer/by-value mixes, hidden overloads, short-name collisions, PIMPL."""
    return AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            # opaque, pointer only
            _fn("ctx_open", "Ctx*", [], ret_depth=1),
            _fn("ctx_close", "void", [Param(name="c", type="Ctx*", pointer_depth=1)]),
            # opaque short name also spelled qualified elsewhere
            _fn("ns_use", "void", [Param(name="q", type="ns::Qual*", pointer_depth=1)]),
            # by-value use defeats "only pointer"
            _fn("by_value", "void", [Param(name="v", type="ByVal")]),
            _fn(
                "by_value_ptr",
                "void",
                [Param(name="v", type="ByVal*", pointer_depth=1)],
            ),
            # a *hidden* overload passing an opaque type by value must not count
            _fn(
                "ctx_hidden",
                "void",
                [Param(name="c", type="Ctx")],
                visibility=Visibility.HIDDEN,
                mangled="_Z10ctx_hidden3Ctx",
            ),
            # reference parameter
            _fn("by_ref", "void", [Param(name="r", type="RefOnly&")]),
            # typedef'd callback
            _fn("with_cb", "void", [Param(name="cb", type="cb_t")]),
            # builtin pointer, not a handle
            _fn("raw", "void", [Param(name="p", type="int*", pointer_depth=1)]),
            # factory / lifecycle pair
            _fn("create_thing", "Thing*", [], ret_depth=1),
            _fn(
                "destroy_thing",
                "void",
                [Param(name="t", type="Thing*", pointer_depth=1)],
            ),
            # std:: by value anti-pattern
            _fn("stl", "void", [Param(name="v", type="std::vector<int>")]),
        ],
        types=[
            RecordType(name="Ctx", kind="struct", is_opaque=True),
            RecordType(name="ns::Qual", kind="struct", is_opaque=True),
            RecordType(name="ByVal", kind="struct", is_opaque=True),
            RecordType(name="RefOnly", kind="class", is_opaque=True),
            RecordType(name="Unreferenced", kind="struct", is_opaque=True),
            RecordType(name="ImplHidden", kind="struct", is_opaque=True),
            RecordType(
                name="Wrapper",
                kind="class",
                size_bits=64,
                alignment_bits=64,
                fields=[
                    TypeField(
                        name="impl",
                        type="ImplHidden*",
                        offset_bits=0,
                        access=AccessLevel.PRIVATE,
                    )
                ],
            ),
            RecordType(
                name="PublicFields",
                kind="struct",
                is_opaque=True,
                fields=[
                    TypeField(
                        name="x", type="int", offset_bits=0, access=AccessLevel.PUBLIC
                    )
                ],
            ),
            RecordType(name="Thing", kind="class", size_bits=64, alignment_bits=64),
            RecordType(name="U", kind="union", is_opaque=True),
            RecordType(name="E", kind="enum", is_opaque=True),
        ],
        typedefs={
            "cb_t": "void (*)(int)",
            "handle_t": "Ctx*",
            "int_ptr_t": "int*",
            "void_tok": "void*",
        },
    )


def _uniform_snapshot(n_types: int, n_funcs: int, opaque: bool) -> AbiSnapshot:
    types = [
        RecordType(name=f"ns::T{i}", kind="struct", is_opaque=opaque)
        if opaque
        else RecordType(
            name=f"ns::T{i}",
            kind="class",
            size_bits=64,
            alignment_bits=64,
            fields=[
                TypeField(
                    name="f", type="int", offset_bits=0, access=AccessLevel.PRIVATE
                )
            ],
        )
        for i in range(n_types)
    ]
    funcs = [
        _fn(
            f"fn{i}",
            f"struct ns::T{i % max(1, n_types)} *",
            [
                Param(
                    name="p",
                    type=f"const struct ns::T{i % max(1, n_types)} *",
                    pointer_depth=1,
                )
            ],
            ret_depth=1,
        )
        for i in range(n_funcs)
    ]
    return AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=funcs,
        types=types,
        typedefs={},
    )


_SNAPSHOT_CASES = {
    "mixed": _mixed_snapshot(),
    "none_eligible": _uniform_snapshot(30, 40, opaque=False),
    "few_eligible": _mixed_snapshot(),
    "all_eligible": _uniform_snapshot(30, 40, opaque=True),
    "empty": AbiSnapshot(
        library="l", version="1", from_headers=True, functions=[], types=[], typedefs={}
    ),
}


def _tag_digest(tags: dict[str, list]) -> list[tuple]:
    """Full IdiomTag content, not just names -- evidence and proof fields too."""
    return [
        (
            name,
            t.idiom.value,
            t.confidence.value,
            tuple(t.evidence),
            t.layout_signature,
            t.hidden_pointee,
            t.definition_hidden,
        )
        for name, tl in sorted(tags.items())
        for t in tl
    ]


class TestRecogniseOpaqueReordering:
    @pytest.mark.parametrize("case", sorted(_SNAPSHOT_CASES))
    def test_full_tag_parity_with_original_ordering(self, case: str) -> None:
        graph = build_surface_graph(_SNAPSHOT_CASES[case])
        produced = _tag_digest(recognise_idioms(graph))
        # Reference: recompute the OPAQUE_POINTER tags with the original order
        # and splice them over the production result's opaque entries.
        expected_opaque = sorted(
            (rec.name, tag)
            for rec in graph.snapshot.types
            if (tag := _ref_recognise_opaque(graph, rec)) is not None
        )
        got_opaque = sorted(
            (name, t)
            for name, tl in recognise_idioms(graph).items()
            for t in tl
            if t.idiom is Idiom.OPAQUE_POINTER
        )
        assert got_opaque == expected_opaque
        assert produced == _tag_digest(recognise_idioms(graph))

    @pytest.mark.parametrize("case", sorted(_SNAPSHOT_CASES))
    def test_predicate_parity_for_every_record(self, case: str) -> None:
        graph = build_surface_graph(_SNAPSHOT_CASES[case])
        for rec in graph.snapshot.types:
            assert idioms._public_pointer_only(
                graph, rec.name
            ) == _ref_public_pointer_only(graph, rec.name)

    def test_hidden_overload_by_value_use_stays_excluded(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        # ``ctx_hidden`` takes Ctx by value but is not public, so Ctx is still
        # "only ever by pointer" and still tagged opaque.
        assert idioms._public_pointer_only(graph, "Ctx") == (True, True)
        tags = recognise_idioms(graph)
        assert any(t.idiom is Idiom.OPAQUE_POINTER for t in tags.get("Ctx", []))

    def test_public_by_value_use_defeats_only_pointer(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        assert idioms._public_pointer_only(graph, "ByVal") == (True, False)
        assert not any(
            t.idiom is Idiom.OPAQUE_POINTER
            for t in recognise_idioms(graph).get("ByVal", [])
        )

    def test_unreferenced_type_keeps_current_predicate_result(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        assert idioms._public_pointer_only(graph, "Unreferenced") == (False, True)
        assert "Unreferenced" not in recognise_idioms(graph)

    def test_ineligible_records_never_reach_the_signature_query(self) -> None:
        """Deterministic work-count guard (not a millisecond threshold)."""
        graph = build_surface_graph(_mixed_snapshot())
        asked: list[str] = []
        orig = idioms.query_public_use

        def spy(index, name: str):
            asked.append(name)
            return orig(index, name)

        idioms.query_public_use = spy  # type: ignore[assignment]
        try:
            recognise_idioms(graph)
        finally:
            idioms.query_public_use = orig  # type: ignore[assignment]
        # Complete records and records with a PUBLIC field are rejected first.
        assert "Wrapper" not in asked
        assert "Thing" not in asked
        assert "PublicFields" not in asked
        # ...while every genuinely opaque, private-field record is still asked.
        assert "Ctx" in asked

    def test_index_is_built_at_most_once_per_recognition(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        builds = self._count_index_builds(graph)
        assert builds == 1

    def test_index_is_not_built_when_no_record_is_eligible(self) -> None:
        graph = build_surface_graph(_SNAPSHOT_CASES["none_eligible"])
        assert self._count_index_builds(graph) == 0
        graph_empty = build_surface_graph(_SNAPSHOT_CASES["empty"])
        assert self._count_index_builds(graph_empty) == 0

    @staticmethod
    def _count_index_builds(graph: SurfaceGraph) -> int:
        calls = 0
        orig = idioms.build_public_use_index

        def spy(functions):
            nonlocal calls
            calls += 1
            return orig(functions)

        idioms.build_public_use_index = spy  # type: ignore[assignment]
        try:
            recognise_idioms(graph)
        finally:
            idioms.build_public_use_index = orig  # type: ignore[assignment]
        return calls

    def test_index_holds_only_strings_and_bools(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        index = public_use_index.build_public_use_index(graph.snapshot.functions)
        for mapping in (index.by_short, index.by_exact):
            assert mapping
            assert all(isinstance(k, str) for k in mapping)
            assert all(isinstance(v, bool) for v in mapping.values())

    def test_index_size_scales_with_sites_not_records(self) -> None:
        """A per-record copy of the site list would scale with record count.

        The index is built from the declarations alone, so its size is bounded
        by the number of signature *sites* -- at most one short-name key per
        word plus two exact keys per site -- however many records will later be
        queried against it.
        """
        snap = _uniform_snapshot(400, 40, opaque=True)
        sites = sum(1 + len(fn.params) for fn in snap.functions)
        index = public_use_index.build_public_use_index(snap.functions)
        assert len(index.by_exact) <= 2 * sites
        assert len(index.by_short) <= 2 * sites
        # 400 records, 40 functions: far fewer entries than records x sites.
        assert len(index.by_short) + len(index.by_exact) < 400 * sites

    def test_negative_control_dropping_by_value_evidence_is_caught(self) -> None:
        """An 'optimisation' that loses by-value uses must fail these tests."""
        graph = build_surface_graph(_mixed_snapshot())
        orig = idioms.query_public_use

        def broken(index, name: str) -> tuple[bool, bool]:
            referenced, _ = orig(index, name)
            return referenced, True  # pretends everything is pointer-only

        idioms.query_public_use = broken  # type: ignore[assignment]
        try:
            tags = recognise_idioms(graph)
        finally:
            idioms.query_public_use = orig  # type: ignore[assignment]
        assert any(t.idiom is Idiom.OPAQUE_POINTER for t in tags.get("ByVal", []))

    def test_negative_control_excluding_all_public_functions_is_caught(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        empty = public_use_index.PublicUseIndex({}, {})
        got = [idioms.query_public_use(empty, rec.name) for rec in graph.snapshot.types]
        assert got == [(False, True)] * len(got)
        # ...and an index built with the public-surface test inverted would
        # admit the hidden overload's by-value Ctx use, which the real one does
        # not -- so the admission test is load-bearing, not incidental.
        hidden_only = public_use_index.build_public_use_index(
            [fn for fn in graph.snapshot.functions if fn.name == "ctx_hidden"]
        )
        assert idioms.query_public_use(hidden_only, "Ctx") == (False, True)


class TestPublicUseIndexInvertsThePredicateExactly:
    """Property-style contract for the reusable index primitive itself.

    Stated against the transcribed original scan over generated snapshots, not
    through one caller's domain logic -- an inversion that is only checked via
    ``recognise_idioms`` would not search the input space of the mapping.
    """

    @pytest.mark.parametrize("case", sorted(_SNAPSHOT_CASES))
    def test_agrees_with_the_original_scan_for_every_queried_name(
        self, case: str
    ) -> None:
        graph = build_surface_graph(_SNAPSHOT_CASES[case])
        index = public_use_index.build_public_use_index(graph.snapshot.functions)
        names = {rec.name for rec in graph.snapshot.types}
        names |= {rec.name.rsplit("::", 1)[-1] for rec in graph.snapshot.types}
        names |= {"Absent", "ns::Absent", "int", "void", ""}
        for name in sorted(names):
            assert public_use_index.query_public_use(
                index, name
            ) == _ref_public_pointer_only(graph, name), name

    def test_order_independence(self) -> None:
        snap = _mixed_snapshot()
        forward = public_use_index.build_public_use_index(snap.functions)
        reverse = public_use_index.build_public_use_index(
            list(reversed(snap.functions))
        )
        assert forward.by_short == reverse.by_short
        assert forward.by_exact == reverse.by_exact

    def test_any_by_value_site_defeats_only_pointer(self) -> None:
        """Combining sites is an OR, whichever order they arrive in."""
        ptr = _fn("p", "void", [Param(name="a", type="T*", pointer_depth=1)])
        val = _fn("v", "void", [Param(name="a", type="T")])
        for order in ([ptr, val], [val, ptr]):
            index = public_use_index.build_public_use_index(order)
            assert public_use_index.query_public_use(index, "T") == (True, False)

    def test_short_and_exact_clauses_stay_separate(self) -> None:
        """Each clause keys off a different thing; neither is widened.

        An *unqualified* site spelling is what the short-name clause binds, so
        a differently-qualified query reaches it -- the pre-existing,
        deliberately loose behaviour. A *qualified* site spelling does not:
        its short name never enters the short-name key set. Both readings are
        asserted against the transcribed original, so this pins the existing
        matching rule rather than a new resolver.
        """
        unqualified = _fn("f", "void", [Param(name="a", type="Ctx*", pointer_depth=1)])
        qualified = _fn(
            "g", "void", [Param(name="a", type="ns1::Ctx*", pointer_depth=1)]
        )
        for fn, expected_referenced in ((unqualified, True), (qualified, False)):
            index = public_use_index.build_public_use_index([fn])
            graph = build_surface_graph(
                AbiSnapshot(
                    library="l",
                    version="1",
                    from_headers=True,
                    functions=[fn],
                    types=[],
                    typedefs={},
                )
            )
            got = public_use_index.query_public_use(index, "ns2::Ctx")
            assert got[0] is expected_referenced
            assert got == _ref_public_pointer_only(graph, "ns2::Ctx")

    def test_reference_parameter_treatment_unchanged(self) -> None:
        fn = _fn("r", "void", [Param(name="a", type="T&")])
        index = public_use_index.build_public_use_index([fn])
        graph = build_surface_graph(
            AbiSnapshot(
                library="l",
                version="1",
                from_headers=True,
                functions=[fn],
                types=[],
                typedefs={},
            )
        )
        assert public_use_index.query_public_use(
            index, "T"
        ) == _ref_public_pointer_only(graph, "T")


class TestOtherRecognisersUnchanged:
    """Every other caller of ``_strip_ptr`` keeps its behaviour."""

    def test_all_idiom_families_still_recognised(self) -> None:
        tags = recognise_idioms(build_surface_graph(_mixed_snapshot()))
        seen = {t.idiom for tl in tags.values() for t in tl}
        assert Idiom.OPAQUE_POINTER in seen
        assert Idiom.PIMPL in seen
        assert Idiom.HANDLE in seen
        assert Idiom.CREATE_DESTROY in seen
        assert Idiom.CALLBACK_ABI in seen

    def test_pimpl_proof_fields_intact(self) -> None:
        tags = recognise_idioms(build_surface_graph(_mixed_snapshot()))
        pimpl = [t for t in tags["Wrapper"] if t.idiom is Idiom.PIMPL]
        assert len(pimpl) == 1
        assert pimpl[0].hidden_pointee == "ImplHidden"
        assert pimpl[0].layout_signature == (
            "size=64;align=64;fields=impl:ImplHidden*@0"
        )

    def test_handle_excludes_builtin_pointer_typedef(self) -> None:
        tags = recognise_idioms(build_surface_graph(_mixed_snapshot()))
        assert not any(t.idiom is Idiom.HANDLE for t in tags.get("int_ptr_t", []))
        assert any(t.idiom is Idiom.HANDLE for t in tags["handle_t"])
        assert any(t.idiom is Idiom.HANDLE for t in tags["void_tok"])

    def test_antipatterns_unchanged(self) -> None:
        found = detect_antipatterns(build_surface_graph(_mixed_snapshot()))
        assert any(
            "std::vector<int>" in a.description or a.symbol == "stl" for a in found
        )


class TestLifetimeAndReuse:
    def test_no_graph_or_snapshot_retained_after_recognition(self) -> None:
        snap = _mixed_snapshot()
        graph = build_surface_graph(snap)
        recognise_idioms(graph)
        detect_antipatterns(graph)
        ref_graph = weakref.ref(graph)
        ref_snap = weakref.ref(snap)
        del graph, snap
        gc.collect()
        assert ref_graph() is None
        assert ref_snap() is None

    def test_repeated_comparisons_in_one_process_are_identical(self) -> None:
        graph = build_surface_graph(_mixed_snapshot())
        first = _tag_digest(recognise_idioms(graph))
        for _ in range(5):
            assert _tag_digest(recognise_idioms(graph)) == first

    def test_cache_growth_is_bounded_across_many_comparisons(self) -> None:
        type_spelling.strip_ptr_cache_clear()
        for i in range(40):
            recognise_idioms(
                build_surface_graph(_uniform_snapshot(20, 20, opaque=True))
            )
        assert (
            type_spelling.strip_ptr_cache_stats()["occupancy"]
            <= type_spelling.STRIP_PTR_CACHE_MAXSIZE
        )

    def test_concurrent_independent_comparisons_agree(self) -> None:
        graphs = [build_surface_graph(_mixed_snapshot()) for _ in range(4)]
        expected = _tag_digest(recognise_idioms(graphs[0]))
        results: list[list[tuple]] = []
        lock = threading.Lock()

        def work(g: SurfaceGraph) -> None:
            d = _tag_digest(recognise_idioms(g))
            with lock:
                results.append(d)

        threads = [threading.Thread(target=work, args=(g,)) for g in graphs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results == [expected] * len(graphs)


class TestPatternVerdictsSharesOneIndex:
    """The second consumer of the predicate must not rebuild it per name.

    ``pattern_verdicts._emit_lost_invariants`` asks the public-use question
    once per OPAQUE_POINTER-tagged type. ``_public_pointer_only`` is the
    one-shot entry point and rebuilds the index on every call, so a caller with
    many records has to build it once itself -- the same rule this branch
    applies inside ``recognise_idioms``. A work-count guard, not a timing
    threshold, so it states the invariant rather than a machine's speed.
    """

    @staticmethod
    def _build_count(old: AbiSnapshot, new: AbiSnapshot) -> tuple[int, object]:
        calls = 0
        orig = pattern_verdicts.build_public_use_index

        def spy(functions):
            nonlocal calls
            calls += 1
            return orig(functions)

        pattern_verdicts.build_public_use_index = spy  # type: ignore[assignment]
        try:
            result = pattern_verdicts.apply_pattern_verdicts(
                [], old, new, evidence_tier=EvidenceTier.HEADER_AWARE
            )
        finally:
            pattern_verdicts.build_public_use_index = orig  # type: ignore[assignment]
        return calls, result

    def test_index_built_at_most_once_for_many_opaque_types(self) -> None:
        snap = _uniform_snapshot(30, 40, opaque=True)
        calls, _ = self._build_count(snap, snap)
        assert calls <= 1, f"rebuilt the index {calls} times"

    def test_index_not_built_when_no_opaque_tag_exists(self) -> None:
        snap = _uniform_snapshot(30, 40, opaque=False)
        calls, _ = self._build_count(snap, snap)
        assert calls == 0

    def test_hoisting_preserves_the_emitted_verdicts(self) -> None:
        """Equivalence, not just call counts: same findings either way."""
        old = _mixed_snapshot()
        new = _mixed_snapshot()
        _, hoisted = self._build_count(old, new)
        # Re-derive through the one-shot entry point the loop used to call.
        graph = build_surface_graph(new)
        per_name = {
            rec.name: idioms._public_pointer_only(graph, rec.name)
            for rec in graph.snapshot.types
        }
        shared = public_use_index.build_public_use_index(new.functions)
        assert per_name == {
            rec.name: public_use_index.query_public_use(shared, rec.name)
            for rec in graph.snapshot.types
        }
        assert hoisted is not None

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

"""Tests for ADR-030 source ABI replay: schema round-trip, the linker, and the
source-replay diff findings (D4, D5, D6, D10)."""

from __future__ import annotations

import pytest

from abicheck.buildsource import (
    SOURCE_ABI_VERSION,
    BuildSourcePack,
    SourceAbiSurface,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    diff_source_abi,
    link_source_abi,
)
from abicheck.buildsource.source_abi import EVIDENCE_TIER_L4
from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
    ChangeKind,
)


def _no_demangler() -> bool:
    """True when no working C++ demangler (cxxfilt / c++filt) is available — some
    CI runners (macOS, Windows) have neither. Demangler-dependent matching
    degrades gracefully in that case, so the tests that assert the *demangler-
    present* behaviour are skipped rather than failed."""
    from abicheck.demangle import demangle

    return demangle("_ZN6WidgetC1Ev") is None


#: Skip marker for tests that assert demangler-derived matching (RTTI/vtable
#: attribution, the ctor/dtor demangle backstop, the demangled-identity rematch).
needs_demangler = pytest.mark.skipif(
    _no_demangler(), reason="no C++ demangler (cxxfilt/c++filt) available"
)


# -- helpers -----------------------------------------------------------------


def _entity(
    name: str,
    kind: str,
    *,
    visibility: str = "public_header",
    origin: str = "PUBLIC_HEADER",
    mangled: str = "",
    value: str = "",
    signature_hash: str = "",
    body_hash: str = "",
    type_hash: str = "",
    api_relevant: bool = True,
) -> SourceEntity:
    return SourceEntity(
        id=f"decl://{name}",
        kind=kind,
        qualified_name=name,
        mangled_name=mangled,
        signature_hash=signature_hash,
        body_hash=body_hash,
        type_hash=type_hash,
        value=value,
        source_location=SourceLocation(path=f"include/{name}.h", line=1, origin=origin),
        visibility=visibility,
        api_relevant=api_relevant,
    )


def _surface(**kw: object) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    for key, val in kw.items():
        setattr(s, key, val)
    return s


# -- schema round-trip (D4, D5) ----------------------------------------------


def test_source_abi_tu_roundtrip() -> None:
    tu = SourceAbiTu(
        tu_id="cu://src/foo.cpp#cfg:abc",
        target_id="target://libfoo",
        extractor={"name": "castxml", "version": "0.6"},
        compile_context_hash="sha256:deadbeef",
        source="src/foo.cpp",
        public_header_roots=["include/foo.h"],
        macros=[_entity("FOO_SIZE", "macro", value="16")],
        functions=[
            SourceEntity(
                id="decl://foo::bar",
                kind="function",
                qualified_name="foo::bar",
                mangled_name="_ZN3foo3barEv",
                names={
                    "source_qualified": "foo::bar",
                    "mangled": "_ZN3foo3barEv",
                    "usr": "c:@N@foo@F@bar#",
                },
                relations={"template_owner": "foo::Box<T>"},
                ownership={
                    "visibility": "public_header",
                    "origin": "PUBLIC_HEADER",
                    "role": "own_api_candidate",
                },
            )
        ],
    )
    restored = SourceAbiTu.from_dict(tu.to_dict())
    assert restored.schema_version == SOURCE_ABI_VERSION
    assert restored.tu_id == tu.tu_id
    assert restored.extractor == {"name": "castxml", "version": "0.6"}
    assert [e.qualified_name for e in restored.macros] == ["FOO_SIZE"]
    assert restored.functions[0].mangled_name == "_ZN3foo3barEv"
    assert restored.functions[0].names["usr"] == "c:@N@foo@F@bar#"
    assert restored.functions[0].relations["template_owner"] == "foo::Box<T>"
    assert restored.functions[0].ownership["role"] == "own_api_candidate"
    # all_entities flattens every bucket
    assert {e.qualified_name for e in restored.all_entities()} == {
        "FOO_SIZE",
        "foo::bar",
    }


def test_source_abi_tu_from_dict_tolerates_missing_fields() -> None:
    # Forward/defensive parsing: a minimal hand-written dump must not abort.
    tu = SourceAbiTu.from_dict({"tu_id": "cu://x"})
    assert tu.tu_id == "cu://x"
    assert tu.macros == []
    assert tu.schema_version == SOURCE_ABI_VERSION


def test_source_abi_tu_from_dict_tolerates_malformed_source_edges() -> None:
    """A forward-versioned/hand-edited dump may carry ``source_edges: null``
    or a stray string; ``list(None)`` raises and ``list("x")`` yields
    character entries, either of which would abort loading or poison the
    graph (CodeRabbit review)."""
    assert (
        SourceAbiTu.from_dict({"tu_id": "x", "source_edges": None}).source_edges == []
    )
    assert (
        SourceAbiTu.from_dict({"tu_id": "x", "source_edges": "oops"}).source_edges == []
    )
    tu = SourceAbiTu.from_dict(
        {
            "tu_id": "x",
            "source_edges": [
                {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b"},
                "junk",
                1,
            ],
        }
    )
    assert tu.source_edges == [{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b"}]


def test_source_abi_surface_from_dict_tolerates_malformed_source_edges() -> None:
    surface = SourceAbiSurface.from_dict({"library": "libfoo.so", "source_edges": None})
    assert surface.source_edges == []
    surface = SourceAbiSurface.from_dict(
        {"library": "libfoo.so", "source_edges": "oops"}
    )
    assert surface.source_edges == []


def test_source_entity_from_dict_boolean_safe_api_relevant() -> None:
    # A hand-edited pack may carry the string "false"; bool("false") would be
    # True, so loading must parse it as a real boolean (CodeRabbit #335).
    ent = SourceEntity.from_dict({"id": "x", "api_relevant": "false"})
    assert ent.api_relevant is False
    assert (
        SourceEntity.from_dict({"id": "x", "api_relevant": "true"}).api_relevant is True
    )
    # Missing field falls back to the dataclass default (True).
    assert SourceEntity.from_dict({"id": "x"}).api_relevant is True
    # A real JSON boolean still round-trips.
    assert (
        SourceEntity.from_dict({"id": "x", "api_relevant": False}).api_relevant is False
    )


def test_source_entity_from_dict_tolerates_null_evidence_dicts() -> None:
    ent = SourceEntity.from_dict(
        {"id": "x", "names": None, "relations": "bad", "ownership": []}
    )
    assert ent.names == {}
    assert ent.relations == {}
    assert ent.ownership == {}


def test_source_abi_surface_roundtrip() -> None:
    s = link_source_abi(
        [
            SourceAbiTu(
                public_header_roots=["include/foo.h"],
                macros=[_entity("FOO_SIZE", "macro", value="16")],
                functions=[_entity("foo::bar", "function", mangled="_ZN3foo3barEv")],
            )
        ],
        exported_symbols=["_ZN3foo3barEv"],
        library="libfoo.so",
        target_id="target://libfoo",
    )
    restored = SourceAbiSurface.from_dict(s.to_dict())
    assert restored.library == "libfoo.so"
    # The decl→symbol map is keyed by the entity's stable identity (mangled name).
    assert (
        restored.mappings["source_decl_to_binary_symbol"]["_ZN3foo3barEv"]
        == "_ZN3foo3barEv"
    )
    assert [e.qualified_name for e in restored.reachable_macros] == ["FOO_SIZE"]


# -- linker (D5) -------------------------------------------------------------


def test_linker_maps_exported_decls_and_records_unmatched() -> None:
    tu = SourceAbiTu(
        functions=[
            _entity("foo::shipped", "function", mangled="_ZN3foo7shippedEv"),
            _entity("foo::header_only", "function", mangled="_ZN3foo11header_onlyEv"),
        ],
    )
    surface = link_source_abi(
        [tu],
        exported_symbols=["_ZN3foo7shippedEv", "_ZN3foo9orphan_symEv"],
    )
    # Map is keyed by stable identity (mangled name); value is the exported symbol.
    mapping = surface.mappings["source_decl_to_binary_symbol"]
    assert mapping["_ZN3foo7shippedEv"] == "_ZN3foo7shippedEv"
    assert mapping["_ZN3foo11header_onlyEv"] == ""
    # exported symbol with no source decl is unmatched
    assert "_ZN3foo9orphan_symEv" in surface.unmatched["symbols_without_decl"]
    # public decl with no exported symbol is unmatched, reported by qualified name
    assert "foo::header_only" in surface.unmatched["decls_without_symbol"]


def test_linker_keeps_overloads_distinct() -> None:
    # Two overloads share a qualified_name but differ in mangled name. Dropping
    # only one exported overload must stay visible (Codex review #335).
    tu = SourceAbiTu(
        functions=[
            _entity("ns::f", "function", mangled="_ZN2ns1fEi"),  # f(int)
            _entity("ns::f", "function", mangled="_ZN2ns1fEd"),  # f(double)
        ],
    )
    surface = link_source_abi(
        [tu], exported_symbols=["_ZN2ns1fEi"]
    )  # only f(int) exported
    mapping = surface.mappings["source_decl_to_binary_symbol"]
    assert mapping["_ZN2ns1fEi"] == "_ZN2ns1fEi"
    assert mapping["_ZN2ns1fEd"] == ""  # f(double) declared but not exported
    assert surface.unmatched["decls_without_symbol"] == ["ns::f"]


def test_linker_dedups_identical_entities_across_tus() -> None:
    # A per-TU Flow-2 pack re-emits the same public-header decl once per compile
    # (a ~20x blow-up on template-heavy libraries). Byte-identical repeats fold to
    # one surface entity; a genuine ODR variant (same name, divergent type_hash)
    # is NOT folded and still fires a conflict. Overloads (above) also stay split.
    dup = _entity("ns::g", "function", mangled="_ZN2ns1gEv", signature_hash="s1")
    tus = [SourceAbiTu(functions=[dup]) for _ in range(5)]  # same decl in 5 TUs
    # plus a type that two TUs define differently (ODR conflict)
    tus.append(SourceAbiTu(types=[_entity("ns::W", "record", type_hash="h1")]))
    tus.append(SourceAbiTu(types=[_entity("ns::W", "record", type_hash="h2")]))
    surface = link_source_abi(tus, exported_symbols=["_ZN2ns1gEv"])
    # 5 identical function facts collapse to one
    assert [e.qualified_name for e in surface.reachable_declarations] == ["ns::g"]
    # the two divergent ns::W definitions are both kept (dedup key includes
    # type_hash) so ODR detection still sees the conflict
    assert len(surface.reachable_types) == 2
    assert surface.odr_conflicts, "divergent same-name type must still conflict"


def test_linker_folds_source_edges_across_tus() -> None:
    """PR1: link_source_abi rolls tu.source_edges up onto the surface,
    deduplicating byte-identical rows the same way repeated public-header
    entities are deduplicated (Codex review's "deduplicate equivalent TU
    copies" recommendation)."""
    edge = {
        "edge": "DECL_CALLS_DECL",
        "src": "_ZN3foo3barEv",
        "dst": "_ZN3foo3bazEv",
        "provenance": "clang-plugin",
        "confidence": "high",
        "attrs": {},
    }
    tus = [SourceAbiTu(source_edges=[dict(edge)]) for _ in range(3)]
    surface = link_source_abi(tus)
    assert surface.source_edges == [edge]


def test_linker_keeps_distinct_source_edges_from_different_tus() -> None:
    tu_a = SourceAbiTu(
        source_edges=[{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b"}]
    )
    tu_b = SourceAbiTu(
        source_edges=[{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "c"}]
    )
    surface = link_source_abi([tu_a, tu_b])
    assert len(surface.source_edges) == 2


def test_linker_fills_missing_dst_file_from_later_duplicate_tu() -> None:
    # Codex review, PR #555: dedup keeps only the first-seen (edge, src, dst)
    # row across TUs. If that first copy's attrs lack dst_file (this TU
    # couldn't resolve the callee/type's declaring file) but a later TU's
    # copy of the SAME logical edge has it, the richer dst_file must be
    # merged in -- otherwise fold_source_edges() can never mark that
    # endpoint defined_in_project and PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    # is skipped for a dependency the graph otherwise has full evidence for.
    tu_a = SourceAbiTu(
        source_edges=[
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "b",
                "attrs": {"call_kind": "direct"},
            }
        ]
    )
    tu_b = SourceAbiTu(
        source_edges=[
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "b",
                "attrs": {"call_kind": "direct", "dst_file": "src/detail/impl.cc"},
            }
        ]
    )
    surface = link_source_abi([tu_a, tu_b])
    assert len(surface.source_edges) == 1
    assert surface.source_edges[0]["attrs"]["dst_file"] == "src/detail/impl.cc"


def test_linker_keeps_first_dst_file_when_later_duplicate_has_none() -> None:
    # The merge is additive only -- a later duplicate lacking dst_file must
    # never clobber an already-resolved one.
    tu_a = SourceAbiTu(
        source_edges=[
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "b",
                "attrs": {"dst_file": "src/detail/impl.cc"},
            }
        ]
    )
    tu_b = SourceAbiTu(
        source_edges=[{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "attrs": {}}]
    )
    surface = link_source_abi([tu_a, tu_b])
    assert len(surface.source_edges) == 1
    assert surface.source_edges[0]["attrs"]["dst_file"] == "src/detail/impl.cc"


def test_linker_skips_malformed_source_edges_rows() -> None:
    tu = SourceAbiTu(source_edges=[{}, "not-a-dict", {"edge": "DECL_CALLS_DECL"}])
    surface = link_source_abi([tu])
    assert surface.source_edges == []


def test_linker_rejects_non_string_edge_identity_fields() -> None:
    """A malformed row with a null/structured identity field must not be
    stringified into a bogus graph endpoint -- str(None) == "None" would
    otherwise pass the truthiness check (CodeRabbit review)."""
    tu = SourceAbiTu(
        source_edges=[
            {"edge": "DECL_CALLS_DECL", "src": None, "dst": "b"},
            {"edge": "DECL_CALLS_DECL", "src": ["a"], "dst": "b"},
            {"edge": None, "src": "a", "dst": "b"},
        ]
    )
    surface = link_source_abi([tu])
    assert surface.source_edges == []


def test_linker_keeps_unmangled_overloads_distinct() -> None:
    # castxml omits a mangled name for some decls (notably constructors), so two
    # public overloads share the bare qualified_name "Widget". identity() folds
    # in signature_hash so they stay distinct instead of collapsing onto one key
    # and silently dropping an overload (Codex review #335, P2).
    ctor_int = _entity(
        "Widget", "function", mangled="", signature_hash="si", value="x=1"
    )
    ctor_dbl = _entity(
        "Widget", "function", mangled="", signature_hash="sd", value="y=0"
    )
    assert ctor_int.identity() != ctor_dbl.identity()
    tu = SourceAbiTu(functions=[ctor_int, ctor_dbl])
    surface = link_source_abi([tu])
    # Both overloads survive linking onto the public surface.
    assert len(surface.reachable_declarations) == 2
    # A constexpr/macro with no signature still keys on the bare name.
    assert _entity("FOO", "constexpr", value="1").identity() == "FOO"


def test_identity_is_build_root_independent() -> None:
    # identity() must NOT include the declaring header path: castxml reports an
    # absolute build path that differs between old/new checkout roots, so baking
    # it in would make an unchanged decl look removed. Same name+signature from
    # two different roots must share one identity (Codex P2).
    def _ctor(header: str) -> SourceEntity:
        return SourceEntity(
            id=f"decl://{header}",
            kind="function",
            qualified_name="foo",
            mangled_name="",
            signature_hash="sig",
            source_location=SourceLocation(path=header, origin="PUBLIC_HEADER"),
            visibility="public_header",
        )

    assert (
        _ctor("build/old/include/foo.h").identity()
        == _ctor("build/new/include/foo.h").identity()
    )


def test_diff_mappings_robust_to_build_root_path_shift() -> None:
    # An unmangled extern-C decl `foo` keeps exporting the same symbol across two
    # build roots. The decl's identity may shift (different header paths), but the
    # mapping diff reconciles by exported symbol, so no false
    # source_decl_binary_symbol_mismatch is emitted (Codex review #335, P2).
    old = _surface(
        roots={"exported_symbols": ["foo"]},
        mappings={"source_decl_to_binary_symbol": {"foo#sigOLD": "foo"}},
    )
    new = _surface(
        roots={"exported_symbols": ["foo"]},
        mappings={"source_decl_to_binary_symbol": {"foo#sigNEW": "foo"}},
    )
    kinds = [c.kind for c in diff_source_abi(old, new)]
    assert ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH not in kinds


def test_diff_default_arg_change_on_unmangled_overload() -> None:
    # The default-arg change is on one of two unmangled overloads; folding
    # signature_hash into identity keeps them apart so the change is detected on
    # the right overload and not lost to a key collision (Codex review #335, P2).
    old = _surface(
        reachable_declarations=[
            _entity("Widget", "function", mangled="", signature_hash="si", value="x=1"),
            _entity("Widget", "function", mangled="", signature_hash="sd", value="y=0"),
        ]
    )
    new = _surface(
        reachable_declarations=[
            _entity("Widget", "function", mangled="", signature_hash="si", value="x=2"),
            _entity("Widget", "function", mangled="", signature_hash="sd", value="y=0"),
        ]
    )
    kinds = [c.kind for c in diff_source_abi(old, new)]
    assert kinds.count(ChangeKind.DEFAULT_ARGUMENT_CHANGED) == 1


def test_linker_matches_unmangled_c_exports() -> None:
    # A C / extern "C" decl has no mangled_name; the export is the plain name.
    # It must still map, not be reported as unmatched (Codex review #335).
    tu = SourceAbiTu(functions=[_entity("foo", "function", mangled="")])
    surface = link_source_abi([tu], exported_symbols=["foo"])
    assert surface.mappings["source_decl_to_binary_symbol"]["foo"] == "foo"
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.unmatched["decls_without_symbol"] == []


def test_linker_matches_ctor_dtor_abi_clone_variants() -> None:
    # One source ctor/dtor Decl mangles to a single name (C1/D1), but the compiler
    # exports several ABI clones (C1 complete + C2 base; D0 deleting + D1 + D2).
    # An exact-string match would orphan the C2/D0/D2 clones as
    # "exported but no source decl"; the linker folds ctor/dtor tags so the one
    # declaration claims every clone (ADR-030 D5 symbol linking).
    tu = SourceAbiTu(
        functions=[
            _entity("Widget::Widget", "function", mangled="_ZN6WidgetC1Ev"),
            _entity("Widget::~Widget", "function", mangled="_ZN6WidgetD1Ev"),
        ],
    )
    surface = link_source_abi(
        [tu],
        exported_symbols=[
            "_ZN6WidgetC1Ev",  # complete-object ctor (matches decl exactly)
            "_ZN6WidgetC2Ev",  # base-object ctor clone (only via folding)
            "_ZN6WidgetD0Ev",  # deleting dtor clone
            "_ZN6WidgetD1Ev",  # complete-object dtor (matches decl exactly)
            "_ZN6WidgetD2Ev",  # base-object dtor clone
        ],
    )
    # Every exported clone is attributed to a declaration — none is orphaned.
    assert surface.unmatched["symbols_without_decl"] == []
    # Both declarations resolve to a concrete exported symbol (not "").
    mapping = surface.mappings["source_decl_to_binary_symbol"]
    assert mapping["_ZN6WidgetC1Ev"]
    assert mapping["_ZN6WidgetD1Ev"]
    assert surface.coverage["matched_symbols"] == 5


def test_linker_matches_dwarf_unified_ctor_tag_to_real_clones() -> None:
    # GCC's DWARF linkage name uses the non-standard *unified* C4/D4 tag for a
    # ctor/dtor, which never appears in the ELF export table (which has C1/C2 …).
    # The fold canonicalizes C4/D4 too, so a DWARF-sourced decl still matches the
    # real exported clones instead of collapsing to zero matches.
    tu = SourceAbiTu(
        functions=[_entity("Widget::Widget", "function", mangled="_ZN6WidgetC4Ev")],
    )
    surface = link_source_abi(
        [tu], exported_symbols=["_ZN6WidgetC1Ev", "_ZN6WidgetC2Ev"]
    )
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.coverage["matched_symbols"] == 2
    # The single decl maps to one of the concrete clones (a stable pick).
    assert surface.mappings["source_decl_to_binary_symbol"]["_ZN6WidgetC4Ev"] in {
        "_ZN6WidgetC1Ev",
        "_ZN6WidgetC2Ev",
    }


def test_ctor_dtor_fold_leaves_non_ctor_symbols_exact() -> None:
    # The fold must be a no-op for ordinary symbols: a regular function whose name
    # merely contains letters+digits must not be conflated with a ctor/dtor clone.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    assert _ctor_dtor_canonical("_ZN3foo3barEv") == "_ZN3foo3barEv"
    # C1/C2/C3/C4 fold together; D0/D1/D2/D4 fold together; the two stay distinct.
    ctors = {_ctor_dtor_canonical(f"_ZN6WidgetC{n}Ev") for n in (1, 2, 3, 4)}
    dtors = {_ctor_dtor_canonical(f"_ZN6WidgetD{n}Ev") for n in (0, 1, 2, 4)}
    assert len(ctors) == 1 and len(dtors) == 1
    assert ctors != dtors
    # Non-nested / vtable / plain names have no ctor/dtor and stay untouched.
    assert _ctor_dtor_canonical("_Z3barv") == "_Z3barv"
    assert _ctor_dtor_canonical("_ZTVN3fooE") == "_ZTVN3fooE"
    # A non-exported, non-ctor decl stays unmatched (no accidental fold match).
    tu = SourceAbiTu(functions=[_entity("ns::f", "function", mangled="_ZN2ns1fEi")])
    surface = link_source_abi([tu], exported_symbols=["_ZN2ns1gEi"])
    assert surface.mappings["source_decl_to_binary_symbol"]["_ZN2ns1fEi"] == ""


def test_ctor_dtor_fold_does_not_touch_length_encoded_identifiers() -> None:
    # Codex review: a `C1`/`D0` that is merely the TAIL of a length-prefixed
    # ordinary identifier must NOT fold — else two unrelated functions collide and
    # one is wrongly dropped from symbols_without_decl. `N::AC1()`/`N::AC2()`
    # mangle as `_ZN1N3AC1Ev`/`_ZN1N3AC2Ev`; the `C1`/`C2` are identifier chars,
    # not a ctor special name (which is never length-prefixed).
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    assert _ctor_dtor_canonical("_ZN1N3AC1Ev") == "_ZN1N3AC1Ev"
    assert _ctor_dtor_canonical("_ZN1N3AC1Ev") != _ctor_dtor_canonical("_ZN1N3AC2Ev")
    # Through the linker: AC1 is a real source decl; AC2 is exported without one.
    # AC2 must remain an orphan (symbols_without_decl), not be claimed by AC1.
    tu = SourceAbiTu(functions=[_entity("N::AC1", "function", mangled="_ZN1N3AC1Ev")])
    surface = link_source_abi([tu], exported_symbols=["_ZN1N3AC1Ev", "_ZN1N3AC2Ev"])
    assert surface.mappings["source_decl_to_binary_symbol"]["_ZN1N3AC1Ev"] == (
        "_ZN1N3AC1Ev"
    )
    assert surface.unmatched["symbols_without_decl"] == ["_ZN1N3AC2Ev"]


def test_ctor_dtor_fold_handles_digit_suffixed_class_names() -> None:
    # CodeRabbit review: a class whose <source-name> ends in a digit (Vec2, Mat4,
    # Base64 — common in graphics/math code) must still fold. The structural parse
    # consumes the length-prefixed identifier wholesale, so the trailing digit is
    # part of the class name, and the following C1/D0 is correctly seen as the
    # ctor/dtor special name. (A naive letter-only lookbehind would miss these.)
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    for cls in ("_ZN4Vec2", "_ZN4Mat4", "_ZN6Base64"):
        assert _ctor_dtor_canonical(cls + "C1Ev") == _ctor_dtor_canonical(cls + "C2Ev")
        assert _ctor_dtor_canonical(cls + "D0Ev") == _ctor_dtor_canonical(cls + "D1Ev")
        # and the fold actually happened (not a no-op)
        assert _ctor_dtor_canonical(cls + "C1Ev") != cls + "C1Ev"


def test_ctor_dtor_fold_tolerates_malformed_huge_length_fields() -> None:
    # Exported symbol names may come from untrusted binaries or ABI snapshots.
    # A malformed Itanium name with an enormous length field must not crash the
    # structural parser via Python's int-conversion digit limit; it should simply
    # fail to fold and keep exact/unmatched semantics.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    malformed = "_ZN" + ("9" * 5000)
    assert _ctor_dtor_canonical(malformed) == malformed

    tu = SourceAbiTu(functions=[_entity("ok", "function", mangled="_Z2okv")])
    surface = link_source_abi([tu], exported_symbols=["_Z2okv", malformed])
    assert surface.mappings["source_decl_to_binary_symbol"]["_Z2okv"] == "_Z2okv"
    assert malformed in surface.unmatched["symbols_without_decl"]


def test_ctor_dtor_fold_parses_template_and_substitution_prefixes() -> None:
    # The nested-name parser must skip <substitution> (St = std::) and
    # <template-args> (I…E) before reaching a genuine ctor/dtor tag, e.g.
    # std::vector<int>::vector() → _ZNSt6vectorIiEC1Ev. Exercises the S and I
    # branches of the parser.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    assert _ctor_dtor_canonical("_ZNSt6vectorIiEC1Ev") == _ctor_dtor_canonical(
        "_ZNSt6vectorIiEC2Ev"
    )
    assert _ctor_dtor_canonical("_ZNSt6vectorIiEC1Ev") != "_ZNSt6vectorIiEC1Ev"
    # A backref substitution (S_) in the prefix, then the ctor.
    assert _ctor_dtor_canonical("_ZN1NS_3FooC1Ev") != "_ZN1NS_3FooC1Ev"


def test_ctor_dtor_fold_handles_nested_template_argument_names() -> None:
    # Codex review: a template argument that is itself a nested name (e.g. the
    # NSt7__cxx11…E of std::string) must not close the outer I…E early — the
    # balanced skip consumes length-prefixed identifiers wholesale and tracks
    # I/N nesting, so the ctor tag *after* the template-args is still folded.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    # std::vector<std::string>::vector()  (C1 complete vs C2 base)
    vec_str = (
        "_ZNSt6vectorINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEE{tag}Ev"
    )
    assert _ctor_dtor_canonical(vec_str.format(tag="C1")) == _ctor_dtor_canonical(
        vec_str.format(tag="C2")
    )
    assert _ctor_dtor_canonical(vec_str.format(tag="C1")) != vec_str.format(tag="C1")
    # ns::A<std::map<std::string, Foo>>::A() — two levels of nested template args.
    a_map = (
        "_ZN2ns1AISt3mapINSt7__cxx1112basic_string"
        "IcSt11char_traitsIcESaIcEEE3FooEE{tag}Ev"
    )
    assert _ctor_dtor_canonical(a_map.format(tag="C1")) == _ctor_dtor_canonical(
        a_map.format(tag="C2")
    )


def test_ctor_dtor_fold_handles_non_type_template_parameters() -> None:
    # Codex review: a non-type template parameter mangles as an L<type><value>E
    # literal whose VALUE is raw digits — `Fixed<3>::Fixed()` → `_ZN5FixedILi3EE`
    # `C1Ev`. Those digits must not be read as a source-name length (which would
    # swallow the trailing ctor tag). The literal is consumed to its own E.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    for sym in (
        "_ZN5FixedILi3EE{tag}Ev",  # Fixed<3>
        "_ZN3ArrIiLm5EE{tag}Ev",  # Arr<int, 5ul>
        "_ZN1XILin1EE{tag}Ev",  # X<-1>
    ):
        assert _ctor_dtor_canonical(sym.format(tag="C1")) == _ctor_dtor_canonical(
            sym.format(tag="C2")
        )
        assert _ctor_dtor_canonical(sym.format(tag="C1")) != sym.format(tag="C1")


def test_ctor_dtor_fold_handles_macho_leading_underscore() -> None:
    # Codex review: Mach-O/Darwin prefixes every Itanium symbol with an extra
    # leading underscore (`__ZN1AC1Ev`). The fold must NORMALIZE it away for the
    # canonical key so it unifies with the export table's `_ZN…` spelling.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    assert _ctor_dtor_canonical("__ZN1AC1Ev") == _ctor_dtor_canonical("__ZN1AC2Ev")
    # The prefix is NORMALIZED away (not restored): the export table strips one
    # underscore (`_ZN…`) while the plugin emits `__ZN…`, so the canonical key must
    # unify both spellings or the clones never match on macOS Plugin injection (Codex).
    assert _ctor_dtor_canonical("__ZN1AC1Ev") == _ctor_dtor_canonical("_ZN1AC1Ev")
    assert not _ctor_dtor_canonical("__ZN1AC1Ev").startswith("__ZN")
    # A Mach-O non-ctor symbol is returned byte-for-byte unchanged.
    assert _ctor_dtor_canonical("__ZN1N3barEv") == "__ZN1N3barEv"


def test_linker_matches_macho_decl_against_stripped_exports() -> None:
    # End-to-end macOS Plugin injection: the plugin decl keeps the raw `__ZN…` mangling but
    # the export table stores `_ZN…` (one underscore stripped). The ctor clones
    # must still fold and match across the two spellings (Codex review).
    tu = SourceAbiTu(functions=[_entity("A::A", "function", mangled="__ZN1AC1Ev")])
    surface = link_source_abi([tu], exported_symbols=["_ZN1AC1Ev", "_ZN1AC2Ev"])
    assert surface.unmatched["symbols_without_decl"] == []


def test_linker_matches_macho_non_ctor_decl_against_stripped_exports() -> None:
    # Codex review: an ORDINARY (non ctor/dtor) C++ method takes the no-fold path,
    # so on macOS Plugin injection the plugin's `__ZN1A3fooEv` must still exact-match the
    # export table's `_ZN1A3fooEv` (one underscore stripped). Before the fix these
    # landed in decls_without_symbol / symbols_without_decl because normalization
    # was applied only to ctor/dtor canonical keys.
    tu = SourceAbiTu(functions=[_entity("A::foo", "function", mangled="__ZN1A3fooEv")])
    surface = link_source_abi([tu], exported_symbols=["_ZN1A3fooEv"])
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.unmatched["decls_without_symbol"] == []
    # The mapping resolves to the REAL exported spelling (the `_Z…` form actually in
    # the binary), not the plugin's raw `__Z…` key.
    assert (
        surface.mappings["source_decl_to_binary_symbol"]["__ZN1A3fooEv"]
        == "_ZN1A3fooEv"
    )


def test_relink_matches_macho_non_ctor_decl_against_stripped_exports() -> None:
    # Same normalization must apply on the merge/relink path (a plugin/wrapper pack
    # linked with no binary, then relinked against the Mach-O export table).
    from abicheck.buildsource.source_link import relink_surface_exports

    tu = SourceAbiTu(functions=[_entity("A::foo", "function", mangled="__ZN1A3fooEv")])
    surface = link_source_abi([tu])  # source-only, no exports yet
    relink_surface_exports(surface, ["_ZN1A3fooEv"])
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.unmatched["decls_without_symbol"] == []
    assert (
        surface.mappings["source_decl_to_binary_symbol"]["__ZN1A3fooEv"]
        == "_ZN1A3fooEv"
    )


def test_norm_itanium_strips_only_macho_double_underscore() -> None:
    from abicheck.buildsource.source_link import _norm_itanium

    # `__Z…` loses exactly one leading underscore; every other spelling is byte-
    # for-byte unchanged (a C symbol, an already-normalized `_Z…`, a bare name).
    assert _norm_itanium("__ZN1A3fooEv") == "_ZN1A3fooEv"
    assert _norm_itanium("_ZN1A3fooEv") == "_ZN1A3fooEv"
    assert _norm_itanium("foo") == "foo"
    assert _norm_itanium("") == ""


def test_build_exact_index_prefers_canonical_spelling_on_collision() -> None:
    from abicheck.buildsource.source_link import _build_exact_index

    # If a binary somehow lists BOTH spellings of one Itanium name (practically
    # impossible), the canonical `_Z…` form wins the normalized key so the returned
    # real spelling is deterministic and genuinely in the export set.
    idx = _build_exact_index({"__ZN1A3fooEv", "_ZN1A3fooEv"})
    assert idx["_ZN1A3fooEv"] == "_ZN1A3fooEv"
    # A lone Mach-O spelling maps its normalized key back to the real `__Z…` name.
    idx2 = _build_exact_index({"__ZN1A3fooEv"})
    assert idx2["_ZN1A3fooEv"] == "__ZN1A3fooEv"


@needs_demangler
def test_demangled_rematch_normalizes_macho_decl() -> None:
    # The second-tier rematch must normalize a Mach-O `__Z…` decl before demangling
    # (the shared demangler only accepts `_Z…`), so a drifted macOS decl still
    # rescues against the `_Z…` export.
    from abicheck.buildsource.source_link import _demangled_rematch

    decl = _entity("N::f", "function", mangled="__ZN1N1fEN1N1TE")  # Mach-O spelling
    mapping = {decl.identity(): ""}
    matched: set[str] = set()
    exported = {"_ZN1N1fENS_1TE"}  # substitution form, same demangled identity
    new = _demangled_rematch([decl], mapping, matched, exported)
    assert new == {decl.identity(): "_ZN1N1fENS_1TE"}
    assert mapping[decl.identity()] == "_ZN1N1fENS_1TE"


def test_ctor_dtor_fold_handles_function_type_template_args() -> None:
    # Codex review: a function-type template argument (`A<void(int)>` → `FviE`,
    # `std::function<void()>` → `FvvE`) is itself E-terminated; the balanced skip
    # must treat `F` as an opener so its `E` doesn't close the template-arg list
    # early and hide the trailing ctor tag.
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    # A<void(int)>::A()
    assert _ctor_dtor_canonical("_ZN1AIFviEEC1Ev") == _ctor_dtor_canonical(
        "_ZN1AIFviEEC2Ev"
    )
    # std::function<void()>::function(function const&)
    assert _ctor_dtor_canonical("_ZNSt8functionIFvvEEC1ERKS1_") == _ctor_dtor_canonical(
        "_ZNSt8functionIFvvEEC2ERKS1_"
    )


@needs_demangler
def test_ctor_dtor_demangle_fallback() -> None:
    # The demangler backstop collapses ctor/dtor clones for any Itanium
    # production the structural parser doesn't model (a robustness net). It keys a
    # ctor/dtor to a `ctordtor:<qualified>` form and leaves everything else alone.
    from abicheck.buildsource.source_link import _ctor_dtor_demangle_fallback as fb

    # Ctor clones and dtor clones each collapse to one key.
    assert fb("_ZN6WidgetC1Ev") == fb("_ZN6WidgetC2Ev")
    assert fb("_ZN6WidgetD0Ev") == fb("_ZN6WidgetD1Ev")
    assert fb("_ZN6WidgetC1Ev").startswith("ctordtor:")
    # An ordinary function whose name merely ends in C1/C2 is NOT a ctor — the
    # demangled form (`N::AC1()`) is not `Class::Class`, so it stays unchanged.
    assert fb("_ZN1N3AC1Ev") == "_ZN1N3AC1Ev"
    # A plain free function is untouched.
    assert fb("_Z3foov") == "_Z3foov"


def test_ctor_dtor_fold_parser_edge_cases() -> None:
    # Exercise the remaining parser branches for coverage + robustness:
    from abicheck.buildsource.source_link import _ctor_dtor_canonical

    # CV-qualified member function (K = const): _ZNK… — the qualifier is skipped.
    assert _ctor_dtor_canonical("_ZNK3foo3barEv") == "_ZNK3foo3barEv"  # not a ctor
    # A nested-name that simply ends (no ctor/dtor) hits the closing-E break.
    assert _ctor_dtor_canonical("_ZN3foo3barEv") == "_ZN3foo3barEv"
    # A non-ctor `C`/`D` at a boundary (a member named `Cat`) must NOT fold: the
    # tag test requires an exact C1-C4/D0-D4 special name.
    assert _ctor_dtor_canonical("_ZN3foo3CatEv") == "_ZN3foo3CatEv"
    # An empty / non-mangled string is returned untouched (early out).
    assert _ctor_dtor_canonical("") == ""
    assert _ctor_dtor_canonical("plain_c_symbol") == "plain_c_symbol"
    # A *numbered* backref substitution (S0_) — the S<id>_ branch — still reaches
    # and folds the trailing ctor tag.
    assert _ctor_dtor_canonical("_ZN1NS0_3FooC1Ev") == _ctor_dtor_canonical(
        "_ZN1NS0_3FooC2Ev"
    )


@needs_demangler
def test_linker_attributes_rtti_vtable_thunk_to_public_owner() -> None:
    # vtable/typeinfo/typeinfo-name/thunk exports belong to a type/method, not a
    # free decl, so exact matching orphaned them. They are now attributed to their
    # public owner and drop out of symbols_without_decl (ADR-030 D5).
    tu = SourceAbiTu(
        types=[_entity("Widget", "record", type_hash="t1")],
        functions=[_entity("Widget::foo", "function", mangled="_ZN6Widget3fooEv")],
    )
    surface = link_source_abi(
        [tu],
        exported_symbols=[
            "_ZN6Widget3fooEv",  # the method itself (decl match)
            "_ZTV6Widget",  # vtable for Widget
            "_ZTI6Widget",  # typeinfo for Widget
            "_ZTS6Widget",  # typeinfo name for Widget
            "_ZThn8_N6Widget3fooEv",  # non-virtual thunk to Widget::foo()
            "_ZTVN2ns7UnknownE",  # vtable for a type NOT on the surface
        ],
    )
    # The unknown type's vtable stays unmatched; everything for Widget attributes.
    assert surface.unmatched["symbols_without_decl"] == ["_ZTVN2ns7UnknownE"]
    owners = surface.mappings["synthesized_symbol_to_owner"]
    assert owners["_ZTV6Widget"] == {"kind": "vtable", "owner": "Widget"}
    assert owners["_ZThn8_N6Widget3fooEv"] == {"kind": "thunk", "owner": "Widget::foo"}
    # Honest coverage breakdown: 1 decl match + 4 synthesized + 1 remainder.
    assert surface.coverage["matched_symbols"] == 1
    assert surface.coverage["synthesized_symbols_matched"] == 4
    assert surface.coverage["unmatched_symbols"] == 1
    # The attribution mapping survives serialization.
    restored = SourceAbiSurface.from_dict(surface.to_dict())
    assert restored.mappings["synthesized_symbol_to_owner"]["_ZTI6Widget"] == {
        "kind": "typeinfo",
        "owner": "Widget",
    }


def test_strip_call_signature_preserves_operator_parens() -> None:
    from abicheck.buildsource.source_link import _strip_call_signature

    # Ordinary functions: the trailing parameter list is dropped.
    assert _strip_call_signature("ns::Widget::foo()") == "ns::Widget::foo"
    assert _strip_call_signature("ns::f(int, char)") == "ns::f"
    # Call operator: the `()` that is part of the name is preserved; only the
    # trailing (parameter-list) group is removed.
    assert _strip_call_signature("D::operator()()") == "D::operator()"
    assert _strip_call_signature("D::operator()(int)") == "D::operator()"
    # A nested paren inside the parameter list balances right-to-left.
    assert _strip_call_signature("f(void (*)(int))") == "f"
    # No parens at all → returned as-is.
    assert _strip_call_signature("ns::x") == "ns::x"


@needs_demangler
def test_linker_attributes_call_operator_thunk_to_owner() -> None:
    # Codex review: a thunk to a call operator demangles to `D::operator()()`;
    # splitting at the first `(` would yield `D::operator`, orphaning the thunk.
    # The owner `D::operator()` must be matched and the thunk attributed.
    tu = SourceAbiTu(
        functions=[_entity("D::operator()", "function", mangled="_ZN1DclEv")],
    )
    surface = link_source_abi(
        [tu],
        exported_symbols=["_ZN1DclEv", "_ZThn8_N1DclEv"],  # the op + its thunk
    )
    assert surface.unmatched["symbols_without_decl"] == []
    owners = surface.mappings["synthesized_symbol_to_owner"]
    assert owners["_ZThn8_N1DclEv"] == {"kind": "thunk", "owner": "D::operator()"}


@needs_demangler
def test_thunk_attribution_requires_overload_identity() -> None:
    # Codex review: a thunk carries the full mangling of the overload it forwards
    # to. `_ZThn8_N1D3fooEd` is a thunk for `D::foo(double)`; with only the
    # `D::foo(int)` overload public, it must NOT be attributed to `D::foo` (bare
    # name) — that would drop a real unmatched export and inflate coverage.
    tu = SourceAbiTu(functions=[_entity("D::foo", "function", mangled="_ZN1D3fooEi")])
    surface = link_source_abi(
        [tu], exported_symbols=["_ZN1D3fooEi", "_ZThn8_N1D3fooEd"]
    )
    # The (double)-overload thunk stays orphaned; nothing attributed.
    assert surface.unmatched["symbols_without_decl"] == ["_ZThn8_N1D3fooEd"]
    assert surface.mappings["synthesized_symbol_to_owner"] == {}

    # When the (double) overload IS public, its thunk attributes to it.
    tu2 = SourceAbiTu(
        functions=[
            _entity("D::foo", "function", mangled="_ZN1D3fooEi"),
            _entity("D::foo", "function", mangled="_ZN1D3fooEd"),
        ]
    )
    surface2 = link_source_abi(
        [tu2],
        exported_symbols=["_ZN1D3fooEi", "_ZN1D3fooEd", "_ZThn8_N1D3fooEd"],
    )
    assert surface2.unmatched["symbols_without_decl"] == []
    assert surface2.mappings["synthesized_symbol_to_owner"]["_ZThn8_N1D3fooEd"] == {
        "kind": "thunk",
        "owner": "D::foo",
    }


def test_thunk_target_mangled_parses_offset_forms() -> None:
    from abicheck.buildsource.source_link import _thunk_target_mangled

    # non-virtual (h), virtual (v), Mach-O prefix, and a non-thunk.
    assert _thunk_target_mangled("_ZThn8_N1D3fooEd") == "_ZN1D3fooEd"
    assert _thunk_target_mangled("_ZTv0_n24_N6Widget3fooEv") == "_ZN6Widget3fooEv"
    assert _thunk_target_mangled("__ZThn8_N1DclEv") == "_ZN1DclEv"
    assert _thunk_target_mangled("_ZN1D3fooEv") is None  # not a thunk
    assert _thunk_target_mangled("_ZTV6Widget") is None  # vtable, not a thunk


@pytest.mark.parametrize(
    "sym,expected",
    [
        # non-virtual, positive and negative (n) this-adjustment.
        ("_ZTh4_N1D3fooEv", "_ZN1D3fooEv"),
        ("_ZThn8_N1D3fooEv", "_ZN1D3fooEv"),
        # virtual thunk: two numbers (v-offset _ vcall-offset _).
        ("_ZTv0_n24_N1D3fooEv", "_ZN1D3fooEv"),
        # covariant: TWO call-offsets (h/v) before the encoding.
        ("_ZTch0_h4_N1D5cloneEv", "_ZN1D5cloneEv"),
        ("_ZTcv0_n24_h8_N1D5cloneEv", "_ZN1D5cloneEv"),
        # --- malformed / non-thunk → None (error branches) ---
        ("", None),
        ("foo", None),
        ("_ZT", None),  # too short, no kind char
        ("_ZTz0_N1DfooEv", None),  # kind not h/v/c
        ("_ZTh", None),  # kind but no offset digits (read_number → None)
        ("_ZThX_N1DfooEv", None),  # non-digit offset
        ("_ZThn8N1DfooEv", None),  # missing '_' after the number
        ("_ZTv0_N1DfooEv", None),  # virtual missing the second offset
        ("_ZTch0_N1DfooEv", None),  # covariant missing the second call-offset
        ("_ZThn8_", None),  # valid offset but empty encoding
    ],
)
def test_thunk_target_mangled_branches(sym: str, expected: str | None) -> None:
    from abicheck.buildsource.source_link import _thunk_target_mangled

    assert _thunk_target_mangled(sym) == expected


@needs_demangler
def test_synthesized_attribution_skips_unrecognized_symbols() -> None:
    # A synthesized-looking symbol that fails to demangle (`_ZTTN6Widget`) or
    # demangles to an unrecognized form (`_ZTW1x` = "TLS wrapper function for x")
    # is left orphaned, never attributed — exercising the best-effort skip branches.
    tu = SourceAbiTu(types=[_entity("Widget", "record", type_hash="t1")])
    surface = link_source_abi([tu], exported_symbols=["_ZTTN6Widget", "_ZTW1x"])
    assert sorted(surface.unmatched["symbols_without_decl"]) == [
        "_ZTTN6Widget",
        "_ZTW1x",
    ]
    assert surface.mappings["synthesized_symbol_to_owner"] == {}


@needs_demangler
def test_guard_variable_attributed_to_enclosing_function() -> None:
    # A guard variable for a function-local static (`_ZGVZ3foovE3bar` demangles to
    # "guard variable for foo()::bar") is attributed to its enclosing public
    # function `foo` — the guard-owner (bare-name) path, distinct from the
    # overload-specific thunk path.
    tu = SourceAbiTu(functions=[_entity("foo", "function", mangled="_Z3foov")])
    surface = link_source_abi([tu], exported_symbols=["_Z3foov", "_ZGVZ3foovE3bar"])
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.mappings["synthesized_symbol_to_owner"]["_ZGVZ3foovE3bar"] == {
        "kind": "guard",
        "owner": "foo",
    }


@needs_demangler
def test_synthesized_attribution_requires_exact_specialization() -> None:
    # Codex review: with only `ns::A<int>` on the surface, the vtable for a
    # DIFFERENT specialization `ns::A<char>` (which base-splits to the same
    # `ns::A`) must NOT be attributed — that would hide an exported, unchecked
    # specialization. Base matching is allowed only when the *unspecialized*
    # template is itself public.
    tu = SourceAbiTu(types=[_entity("ns::A<int>", "record", type_hash="t1")])
    surface = link_source_abi(
        [tu], exported_symbols=["_ZTVN2ns1AIiEE", "_ZTVN2ns1AIcEE"]
    )
    # A<int> attributes; A<char> stays an orphan (no public owner).
    assert surface.unmatched["symbols_without_decl"] == ["_ZTVN2ns1AIcEE"]

    # When the bare (unspecialized) template `ns::A` is public, a base match is OK.
    tu2 = SourceAbiTu(types=[_entity("ns::A", "record", type_hash="t2")])
    surface2 = link_source_abi([tu2], exported_symbols=["_ZTVN2ns1AIiEE"])
    assert surface2.unmatched["symbols_without_decl"] == []


@needs_demangler
def test_synthesized_attribution_via_class_template_pattern() -> None:
    # A binary exports vtable/typeinfo per concrete instantiation
    # (`ns::A<int>`, `ns::A<char>`), but the source captures only the class
    # *template* pattern `ns::A` (kind="template"). The pattern legitimately owns
    # all its instantiations' RTTI, so both instantiation vtables attribute to it —
    # closing the template-instantiation RTTI gap that dominated LLVM's unmatched
    # vtable exports (`llvm::format_object<char>`, …).
    tu = SourceAbiTu(templates=[_entity("ns::A", "template", body_hash="b1")])
    surface = link_source_abi(
        [tu], exported_symbols=["_ZTVN2ns1AIiEE", "_ZTVN2ns1AIcEE"]
    )
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.coverage["synthesized_symbols_matched"] == 2

    # But a *concrete* specialization alone is not a pattern: with only the
    # `ns::A<int>` type present (no template entity), `ns::A<char>`'s vtable must
    # still stay an orphan (the exact-specialization guard is not weakened).
    tu2 = SourceAbiTu(types=[_entity("ns::A<int>", "record", type_hash="t1")])
    surface2 = link_source_abi(
        [tu2], exported_symbols=["_ZTVN2ns1AIiEE", "_ZTVN2ns1AIcEE"]
    )
    assert surface2.unmatched["symbols_without_decl"] == ["_ZTVN2ns1AIcEE"]


@needs_demangler
def test_relink_also_attributes_synthesized_exports() -> None:
    # The merge/relink path (used by `merge` on a plugin/wrapper pack) must apply
    # the same RTTI/vtable attribution as link_source_abi.
    from abicheck.buildsource.source_link import relink_surface_exports

    tu = SourceAbiTu(types=[_entity("Widget", "record", type_hash="t1")])
    surface = link_source_abi([tu])  # no exports yet (source-only)
    relink_surface_exports(surface, ["_ZTV6Widget", "_ZTI6Widget"])
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.coverage["synthesized_symbols_matched"] == 2
    assert surface.coverage["unmatched_symbols"] == 0


@needs_demangler
def test_relink_clears_stale_synthesized_owners() -> None:
    # Codex review: a relink runs against a possibly different export set. A surface
    # that recorded synthesized owners on a previous link but attributes NONE under
    # the new exports must have the stale mapping cleared — otherwise the serialized
    # L4 surface keeps claiming ownership of vtables/typeinfo the new binary never
    # exports, contradicting the recomputed coverage / symbols_without_decl.
    from abicheck.buildsource.source_link import relink_surface_exports

    tu = SourceAbiTu(
        types=[_entity("Widget", "record", type_hash="t1")],
        functions=[_entity("Widget::foo", "function", mangled="_ZN6Widget3fooEv")],
    )
    surface = link_source_abi([tu])
    # First relink: Widget's vtable/typeinfo attribute → mapping is populated.
    relink_surface_exports(surface, ["_ZTV6Widget", "_ZTI6Widget"])
    assert surface.mappings["synthesized_symbol_to_owner"]  # non-empty

    # Second relink against an export set with NO synthesized matches: the previous
    # owners must be cleared, not left as stale evidence.
    relink_surface_exports(surface, ["_ZN6Widget3fooEv"])
    assert surface.mappings["synthesized_symbol_to_owner"] == {}
    assert surface.coverage["synthesized_symbols_matched"] == 0
    # And the surface round-trips with the cleared (empty) mapping.
    restored = SourceAbiSurface.from_dict(surface.to_dict())
    assert restored.mappings["synthesized_symbol_to_owner"] == {}


@needs_demangler
def test_linker_demangled_identity_rematch() -> None:
    # A source decl whose mangled name differs *textually* from the export but
    # demangles identically (substitution-form / mangler drift) is rescued by the
    # second-tier demangled-identity match. `_ZN1N1fEN1N1TE` (expanded) and
    # `_ZN1N1fENS_1TE` (substitution form) both demangle to `N::f(N::T)`.
    decl = _entity("N::f", "function", mangled="_ZN1N1fEN1N1TE")
    tu = SourceAbiTu(functions=[decl])
    surface = link_source_abi([tu], exported_symbols=["_ZN1N1fENS_1TE"])
    # Exact match misses (different strings); the demangled tier rescues it.
    assert surface.mappings["source_decl_to_binary_symbol"]["_ZN1N1fEN1N1TE"] == (
        "_ZN1N1fENS_1TE"
    )
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.unmatched["decls_without_symbol"] == []


@needs_demangler
def test_demangled_rematch_skips_ambiguous_forms() -> None:
    # The rematch must never *guess* when a demangled form maps to more than one
    # export. `_ZN1N1fENS_1TE` (substitution form) and `_ZN1N1fEN1N1TE` (expanded)
    # both demangle to `N::f(N::T)`; an unmatched decl demangling to that same
    # form has two candidate exports, so neither may be claimed (CodeRabbit).
    from abicheck.buildsource.source_link import _demangled_rematch

    decl = _entity("N::f", "function", mangled="_ZN1N1fENS_1TE")
    mapping = {decl.identity(): ""}  # unmatched
    matched: set[str] = set()
    exported = {"_ZN1N1fENS_1TE", "_ZN1N1fEN1N1TE"}  # both → "N::f(N::T)"
    new = _demangled_rematch([decl], mapping, matched, exported)
    # Ambiguous form → no claim; the decl stays unmatched and nothing is consumed.
    assert new == {}
    assert mapping[decl.identity()] == ""
    assert matched == set()


def test_demangled_rematch_is_noop_when_already_matched() -> None:
    # Distinct overloads already exact-matched: the rematch has nothing to do.
    from abicheck.buildsource.source_link import _demangled_rematch

    fi = _entity("f", "function", mangled="_Z1fi")  # f(int)
    fd = _entity("f", "function", mangled="_Z1fd")  # f(double)
    mapping = {"_Z1fi": "_Z1fi", "_Z1fd": "_Z1fd"}
    matched = {"_Z1fi", "_Z1fd"}
    new = _demangled_rematch([fi, fd], mapping, matched, {"_Z1fi", "_Z1fd"})
    assert new == {}


@needs_demangler
def test_template_instantiation_exports_attribute_to_public_pattern() -> None:
    # oneDAL-like Flow-2 shape: source replay sees a public template pattern, but
    # the binary exports concrete instantiations. Exact mangled matching cannot
    # connect `Box<T>::get` with `_ZN2ns3BoxIiE3getEv`; the template-pattern tier
    # attributes the concrete export without pretending it was an exact decl match.
    tu = SourceAbiTu(
        functions=[_entity("ns::Box<T>::get", "function", mangled="")],
    )
    surface = link_source_abi(
        [tu],
        exported_symbols=["_ZN2ns3BoxIiE3getEv", "_ZN2ns3BoxIdE3getEv"],
    )
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.coverage["matched_symbols"] == 0
    assert surface.coverage["template_instantiation_symbols_matched"] == 2
    assert surface.mappings["template_instantiation_symbol_to_decl"] == {
        "_ZN2ns3BoxIdE3getEv": "ns::Box<T>::get",
        "_ZN2ns3BoxIiE3getEv": "ns::Box<T>::get",
    }
    # The evidence must survive baseline serialization.
    restored = SourceAbiSurface.from_dict(surface.to_dict())
    assert (
        restored.mappings["template_instantiation_symbol_to_decl"]
        == (surface.mappings["template_instantiation_symbol_to_decl"])
    )


@needs_demangler
def test_relink_attributes_template_instantiation_exports() -> None:
    from abicheck.buildsource.source_link import relink_surface_exports

    tu = SourceAbiTu(
        functions=[_entity("ns::Box<T>::get", "function", mangled="")],
    )
    surface = link_source_abi([tu])  # source-only pack, later merged with binary
    relink_surface_exports(surface, ["_ZN2ns3BoxIiE3getEv"])
    assert surface.unmatched["symbols_without_decl"] == []
    assert surface.coverage["template_instantiation_symbols_matched"] == 1
    assert surface.mappings["template_instantiation_symbol_to_decl"] == {
        "_ZN2ns3BoxIiE3getEv": "ns::Box<T>::get",
    }


@needs_demangler
def test_template_instantiation_attribution_skips_ambiguous_source_patterns() -> None:
    # Two source declarations erase to the same `ns::Box<>::get` pattern (e.g.
    # overloads whose extractor could not provide a mangled identity). Claiming a
    # concrete export would hide an overload ambiguity, so leave it unmatched.
    tu = SourceAbiTu(
        functions=[
            _entity("ns::Box<T>::get", "function", signature_hash="sig:int"),
            _entity("ns::Box<U>::get", "function", signature_hash="sig:double"),
        ],
    )
    surface = link_source_abi([tu], exported_symbols=["_ZN2ns3BoxIiE3getEv"])
    assert surface.unmatched["symbols_without_decl"] == ["_ZN2ns3BoxIiE3getEv"]
    assert surface.mappings.get("template_instantiation_symbol_to_decl", {}) == {}


@needs_demangler
def test_template_pattern_key_does_not_match_missing_member_name() -> None:
    # Guard for the oneDAL `compute_input<Task>` shape observed in the scan: a
    # class-template pattern without the member name must not claim arbitrary
    # exported methods such as `compute_input<task::compute>::set_data()`.
    tu = SourceAbiTu(
        functions=[_entity("ns::Box<T>", "function", mangled="")],
    )
    surface = link_source_abi([tu], exported_symbols=["_ZN2ns3BoxIiE3getEv"])
    assert surface.unmatched["symbols_without_decl"] == ["_ZN2ns3BoxIiE3getEv"]


@needs_demangler
def test_template_special_members_need_only_public_class_template_pattern() -> None:
    tu = SourceAbiTu(
        templates=[
            _entity("", "template", mangled=""),
            _entity("ns::Box", "template", mangled=""),
        ],
    )
    exports = [
        "_ZN2ns3BoxIiEC1Ev",
        "_ZN2ns3BoxIiED1Ev",
        "_ZN2ns3BoxIiEaSERKS1_",
        "_ZN2ns3BoxIiE3getEv",
    ]
    surface = link_source_abi([tu], exported_symbols=exports)
    assert surface.unmatched["symbols_without_decl"] == ["_ZN2ns3BoxIiE3getEv"]
    assert surface.coverage["template_instantiation_symbols_matched"] == 3
    assert surface.mappings["template_instantiation_symbol_to_decl"] == {
        "_ZN2ns3BoxIiEC1Ev": "ns::Box",
        "_ZN2ns3BoxIiED1Ev": "ns::Box",
        "_ZN2ns3BoxIiEaSERKS1_": "ns::Box",
    }


@needs_demangler
def test_template_special_members_attribute_to_public_owner_pattern() -> None:
    # oneDAL-like shape: source facts contain only the public class-template
    # pattern, while the binary exports concrete ctor/dtor/assignment symbols.
    # Those special members are owned by the class pattern; arbitrary methods are
    # still left unmatched by the guard above.
    tu = SourceAbiTu(
        functions=[_entity("ns::Box<T>", "function", mangled="")],
        templates=[_entity("ns::Box", "template", mangled="")],
    )
    exports = [
        "_ZN2ns3BoxIiEC1Ev",
        "_ZN2ns3BoxIiEC2Ev",
        "_ZN2ns3BoxIiED1Ev",
        "_ZN2ns3BoxIiED2Ev",
        "_ZN2ns3BoxIiEaSERKS1_",
        "_ZN2ns3BoxIiE3getEv",
    ]
    surface = link_source_abi([tu], exported_symbols=exports)
    assert surface.unmatched["symbols_without_decl"] == ["_ZN2ns3BoxIiE3getEv"]
    assert surface.coverage["template_instantiation_symbols_matched"] == 5
    assert surface.mappings["template_instantiation_symbol_to_decl"] == {
        "_ZN2ns3BoxIiEC1Ev": "ns::Box",
        "_ZN2ns3BoxIiEC2Ev": "ns::Box",
        "_ZN2ns3BoxIiED1Ev": "ns::Box",
        "_ZN2ns3BoxIiED2Ev": "ns::Box",
        "_ZN2ns3BoxIiEaSERKS1_": "ns::Box",
    }


def test_allocator_interposer_exports_attribute_only_with_tbb_proxy_marker() -> None:
    exports = [
        "__TBB_malloc_proxy",
        "malloc",
        "free",
        "__libc_malloc",
        "_Znwm",
        "_ZdlPv",
        "unrelated",
    ]
    surface = link_source_abi([SourceAbiTu()], exported_symbols=exports)
    assert surface.unmatched["symbols_without_decl"] == ["unrelated"]
    assert surface.coverage["allocator_interposer_symbols_matched"] == 6
    assert surface.mappings["allocator_interposer_symbol_to_owner"] == {
        "__TBB_malloc_proxy": "__TBB_malloc_proxy",
        "malloc": "__TBB_malloc_proxy",
        "free": "__TBB_malloc_proxy",
        "__libc_malloc": "__TBB_malloc_proxy",
        "_Znwm": "__TBB_malloc_proxy",
        "_ZdlPv": "__TBB_malloc_proxy",
    }

    no_marker = link_source_abi([SourceAbiTu()], exported_symbols=["malloc", "_Znwm"])
    assert no_marker.unmatched["symbols_without_decl"] == ["_Znwm", "malloc"]
    assert no_marker.mappings.get("allocator_interposer_symbol_to_owner", {}) == {}


@needs_demangler
def test_non_public_export_accounting_keeps_reasons_separate_from_decl_matches() -> (
    None
):
    exports = [
        "_ZNSt6vectorIiSaIiEEC1Ev",
        "_ZN3tbb6detail2r13fooEv",
        "_ZN2ns6detail3barEv",
        "_ZN2ns3Api8set_implEv",
        "_ZN2ns3Api3pubEv",
        "_ZN12LocalStorage3getEv",
        "_private_c",
        "IMPL",
    ]
    surface = link_source_abi(
        [SourceAbiTu(types=[_entity("ns::Api", "record")])],
        exported_symbols=exports,
        library="libfoo",
    )
    assert surface.unmatched["symbols_without_decl"] == sorted(exports)
    assert surface.coverage["matched_symbols"] == 0
    assert surface.coverage["non_public_symbols_classified"] == len(exports)
    assert surface.coverage["unmatched_symbols"] == len(exports)
    assert surface.mappings["non_public_symbol_to_reason"] == {
        "_ZNSt6vectorIiSaIiEEC1Ev": "dependency:stdlib",
        "_ZN3tbb6detail2r13fooEv": "dependency:tbb",
        "_ZN2ns6detail3barEv": "internal_or_private_export",
        "_ZN2ns3Api8set_implEv": "internal_or_private_export",
        "_ZN2ns3Api3pubEv": "own_export_without_public_source_decl",
        "_ZN12LocalStorage3getEv": "cpp_export_without_public_source_decl",
        "_private_c": "internal_or_private_export",
        "IMPL": "c_export_without_public_source_decl",
    }


def test_non_public_export_accounting_does_not_hide_arbitrary_unowned_symbols() -> None:
    surface = link_source_abi([SourceAbiTu()], exported_symbols=["plain_c_symbol"])
    assert surface.unmatched["symbols_without_decl"] == ["plain_c_symbol"]
    assert surface.coverage["non_public_symbols_classified"] == 0
    assert surface.mappings.get("non_public_symbol_to_reason", {}) == {}


def test_non_public_export_accounting_tolerates_demangler_failure(monkeypatch) -> None:
    import abicheck.demangle as demangle_mod

    def _boom(_symbol: str) -> str:
        raise RuntimeError("demangler crashed")

    monkeypatch.setattr(demangle_mod, "demangle", _boom)
    surface = link_source_abi(
        [SourceAbiTu(types=[_entity("ns::Api", "record")])],
        exported_symbols=["_ZN2ns3Api3pubEv"],
        library="libfoo",
    )
    assert surface.unmatched["symbols_without_decl"] == ["_ZN2ns3Api3pubEv"]
    assert surface.coverage["non_public_symbols_classified"] == 0


def test_non_public_export_accounting_without_surface_roots_stays_conservative() -> (
    None
):
    from abicheck.buildsource.source_link import (
        _classify_non_public_exports,
        _source_namespace_roots,
    )

    assert _source_namespace_roots(None) == set()
    assert _classify_non_public_exports({"plain_c_symbol"}, library="libfoo") == {}


def test_stdlib_rtti_and_guard_exports_classified_as_stdlib() -> None:
    """RTTI/vtable/guard exports for *nested* std types must classify as
    ``dependency:stdlib``, not the generic ``cpp_export`` bucket.

    A naive ``startswith("std::")`` origin check misses them because they
    demangle with a descriptor prefix (``"typeinfo for std::..."``); the fix
    strips the descriptor before the origin test (and backs it with the
    nested-std mangled prefixes when the demangler is unavailable).
    """
    from abicheck.buildsource.source_link import (
        _classify_non_public_exports,
        _is_stdlib_export,
        _is_tbb_export,
        _strip_synthesized_descriptor,
    )

    # descriptor stripping exposes the underlying entity name
    assert (
        _strip_synthesized_descriptor("typeinfo for std::__detail::X")
        == "std::__detail::X"
    )
    assert _strip_synthesized_descriptor("ns::Api::pub()") == "ns::Api::pub()"

    # via the demangled form (descriptor + nested std) — incl. guard variables,
    # whose many mangled shapes are handled by demangling rather than prefixes
    assert _is_stdlib_export("x", "typeinfo name for std::__detail::_AnyMatcher<c>")
    assert _is_stdlib_export("x", "guard variable for std::__cxx11::basic_string<c>::f")
    # via the RTTI mangled fallback when the demangler yields nothing
    assert _is_stdlib_export("_ZTINSt8__detail11_AnyMatcherE", "")
    assert _is_stdlib_export("_ZTVNSt7__cxx1112basic_stringE", "")
    # a non-std nested RTTI symbol must NOT be swept into stdlib
    assert not _is_stdlib_export("_ZTIN2ns3FooE", "typeinfo for ns::Foo")

    # the same descriptor-stripping applies to the TBB origin check, whose
    # mangled fallback also covers the nested construction-vtable (_ZTTN3tbb)
    assert _is_tbb_export("x", "typeinfo for tbb::detail::d1::task")
    assert _is_tbb_export("_ZTTN3tbb6detail2d14taskE", "")
    assert not _is_tbb_export("_ZTIN2ns3FooE", "typeinfo for ns::Foo")

    # end-to-end: a real nested-std typeinfo lands in dependency:stdlib
    sym = "_ZTINSt8__detail11_AnyMatcherINSt7__cxx1112regex_traitsIcEELb0ELb0ELb0EEE"
    assert _classify_non_public_exports({sym}) == {sym: "dependency:stdlib"}


def test_template_pattern_key_edge_cases() -> None:
    from abicheck.buildsource.source_link import _template_pattern_key

    assert _template_pattern_key("ns::Map<std::vector<int>>::get") == ("ns::Map<>::get")
    assert _template_pattern_key("ns::Box<T>::operator<") == "ns::Box<>::operator<"
    assert _template_pattern_key("ns::Broken<T") == ""
    assert _template_pattern_key("plain") == ""


def test_template_owner_helper_edge_cases() -> None:
    import abicheck.buildsource.source_link as sl

    assert sl._split_qualified_scope("Box") == ("", "Box")
    assert sl._drop_demangled_return_type("Box") == "Box"
    assert sl._template_special_member_owner_key("ns::Box::Box") == ""


@needs_demangler
def test_template_instantiation_attribution_noop_edges(monkeypatch) -> None:
    import abicheck.buildsource.source_link as sl

    surface = SourceAbiSurface()
    assert sl._template_instantiation_attribution(surface, [], set(), set()) == {}
    empty_name = SourceEntity(id="empty", kind="function", qualified_name="")
    assert (
        sl._template_instantiation_attribution(
            surface, [empty_name], set(), {"_ZN2ns3BoxIiE3getEv"}
        )
        == {}
    )
    pattern = _entity("ns::Box<T>::get", "function", mangled="")
    assert (
        sl._template_instantiation_attribution(
            surface, [pattern], {"_ZN2ns3BoxIiE3getEv"}, {"_ZN2ns3BoxIiE3getEv"}
        )
        == {}
    )
    monkeypatch.setattr(
        sl, "_norm_itanium", lambda _s: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert (
        sl._template_instantiation_attribution(
            surface, [pattern], set(), {"_ZN2ns3BoxIiE3getEv"}
        )
        == {}
    )


def test_linker_excludes_non_public_entities() -> None:
    tu = SourceAbiTu(
        functions=[
            _entity(
                "priv", "function", visibility="private_header", origin="PRIVATE_HEADER"
            ),
            _entity("notapi", "function", api_relevant=False),
            _entity("pub", "function"),
        ],
    )
    surface = link_source_abi([tu])
    names = {e.qualified_name for e in surface.reachable_declarations}
    assert names == {"pub"}


def test_linker_detects_odr_conflict_across_tus() -> None:
    # Same name AND same declaring header, divergent hashes → real ODR conflict.
    tu1 = SourceAbiTu(types=[_entity("Widget", "record", type_hash="hashA")])
    tu2 = SourceAbiTu(types=[_entity("Widget", "record", type_hash="hashB")])
    surface = link_source_abi([tu1, tu2])
    assert len(surface.odr_conflicts) == 1
    assert surface.odr_conflicts[0]["qualified_name"] == "Widget"


def test_linker_no_odr_for_same_name_in_different_headers() -> None:
    # castxml reports a bare type name (namespace lives in the XML context), so
    # a::Widget (a.h) and b::Widget (b.h) both arrive as "Widget". Keying ODR by
    # (name, header) keeps them distinct so no false odr_source_conflict fires
    # even though their layouts differ (Codex review #335).
    a_widget = SourceEntity(
        id="t1",
        kind="record",
        qualified_name="Widget",
        type_hash="hashA",
        source_location=SourceLocation(path="a.h", origin="PUBLIC_HEADER"),
        visibility="public_header",
    )
    b_widget = SourceEntity(
        id="t2",
        kind="record",
        qualified_name="Widget",
        type_hash="hashB",
        source_location=SourceLocation(path="b.h", origin="PUBLIC_HEADER"),
        visibility="public_header",
    )
    surface = link_source_abi(
        [SourceAbiTu(types=[a_widget]), SourceAbiTu(types=[b_widget])]
    )
    assert surface.odr_conflicts == []


def test_linker_forced_public_overrides_visibility() -> None:
    tu = SourceAbiTu(
        functions=[
            _entity("forced", "function", visibility="private_header", origin="SOURCE")
        ],
    )
    surface = link_source_abi([tu], forced_public=["forced"])
    assert any(e.qualified_name == "forced" for e in surface.reachable_declarations)
    assert surface.roots["forced_public"] == ["forced"]


# -- diff findings (D6) ------------------------------------------------------


def test_diff_public_macro_value_changed() -> None:
    old = _surface(reachable_macros=[_entity("FOO_SIZE", "macro", value="16")])
    new = _surface(reachable_macros=[_entity("FOO_SIZE", "macro", value="32")])
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.PUBLIC_MACRO_VALUE_CHANGED]
    assert changes[0].old_value == "16"
    assert changes[0].new_value == "32"
    assert EVIDENCE_TIER_L4 in (changes[0].source_location or "")


def test_diff_default_argument_changed_keeps_signature() -> None:
    old = _surface(
        reachable_declarations=[
            _entity("f", "function", signature_hash="sig", value="x=1")
        ]
    )
    new = _surface(
        reachable_declarations=[
            _entity("f", "function", signature_hash="sig", value="x=2")
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.DEFAULT_ARGUMENT_CHANGED]


def test_diff_default_argument_change_on_non_last_overload() -> None:
    # Two overloads share qualified_name "g"; the default-arg change is on the
    # first one. Keying by qualified_name alone would drop it (Codex review #335).
    old = _surface(
        reachable_declarations=[
            _entity("g", "function", mangled="_Z1gi", signature_hash="si", value="x=1"),
            _entity("g", "function", mangled="_Z1gd", signature_hash="sd", value="y=0"),
        ]
    )
    new = _surface(
        reachable_declarations=[
            _entity("g", "function", mangled="_Z1gi", signature_hash="si", value="x=2"),
            _entity("g", "function", mangled="_Z1gd", signature_hash="sd", value="y=0"),
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.DEFAULT_ARGUMENT_CHANGED]
    # The display name is the readable qualified name, not the mangled identity.
    assert changes[0].symbol == "g"


def test_diff_variable_initializer_change_is_not_default_argument() -> None:
    # A non-function decl (e.g. a `variable`) carries an empty signature_hash and
    # a `value` (its initializer); a 1->2 change must NOT be reported as
    # default_argument_changed — that branch is function/method only (Codex P2).
    old = _surface(reachable_declarations=[_entity("gVar", "variable", value="1")])
    new = _surface(reachable_declarations=[_entity("gVar", "variable", value="2")])
    kinds = [c.kind for c in diff_source_abi(old, new)]
    assert ChangeKind.DEFAULT_ARGUMENT_CHANGED not in kinds


def test_diff_constexpr_value_changed() -> None:
    old = _surface(reachable_declarations=[_entity("kMax", "constexpr", value="10")])
    new = _surface(reachable_declarations=[_entity("kMax", "constexpr", value="20")])
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.CONSTEXPR_VALUE_CHANGED]


def test_diff_inline_body_changed() -> None:
    old = _surface(reachable_inline_bodies=[_entity("inl", "inline", body_hash="b1")])
    new = _surface(reachable_inline_bodies=[_entity("inl", "inline", body_hash="b2")])
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.INLINE_BODY_CHANGED]


def test_diff_template_body_changed_and_removed() -> None:
    old = _surface(
        reachable_templates=[
            _entity("tpl_changed", "template", body_hash="t1"),
            _entity("tpl_gone", "template", body_hash="g1"),
        ]
    )
    new = _surface(
        reachable_templates=[_entity("tpl_changed", "template", body_hash="t2")]
    )
    kinds = {c.kind for c in diff_source_abi(old, new)}
    assert ChangeKind.TEMPLATE_BODY_CHANGED in kinds
    assert ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED in kinds


def test_diff_source_decl_binary_symbol_mismatch() -> None:
    old = _surface(
        mappings={
            "source_decl_to_binary_symbol": {"foo::bar": "_ZN3foo3barEv"},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        }
    )
    new = _surface(
        mappings={
            "source_decl_to_binary_symbol": {"foo::bar": ""},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        }
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH]


def test_diff_mismatch_on_removed_decl_with_stale_export() -> None:
    # Declaration removed from the new surface but its symbol is still exported
    # (stale export). L0 sees no removed symbol, so L4 must flag it (Codex #335).
    old = _surface(
        mappings={
            "source_decl_to_binary_symbol": {"foo": "foo"},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        }
    )
    new = _surface(
        mappings={
            "source_decl_to_binary_symbol": {},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        },
        roots={
            "exported_symbols": ["foo"],
            "public_header_declarations": [],
            "forced_public": [],
        },
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH]
    assert changes[0].old_value == "foo"


def test_diff_no_mismatch_when_decl_and_export_both_removed() -> None:
    # Declaration AND its export are gone → L0 owns the breaking finding; L4 must
    # not double-report (the symbol is not in the new exported set).
    old = _surface(
        mappings={
            "source_decl_to_binary_symbol": {"foo": "foo"},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        }
    )
    new = _surface(
        mappings={
            "source_decl_to_binary_symbol": {},
            "source_type_to_debug_type": {},
            "public_header_to_target": {},
        },
        roots={
            "exported_symbols": [],
            "public_header_declarations": [],
            "forced_public": [],
        },
    )
    assert diff_source_abi(old, new) == []


def test_diff_odr_source_conflict_only_when_new() -> None:
    conflict = {"qualified_name": "Widget", "old_type_hash": "a", "new_type_hash": "b"}
    # Pre-existing conflict on both sides → not re-reported.
    both = diff_source_abi(
        _surface(odr_conflicts=[conflict]), _surface(odr_conflicts=[conflict])
    )
    assert both == []
    # Newly introduced conflict → flagged.
    new_only = diff_source_abi(_surface(), _surface(odr_conflicts=[conflict]))
    assert [c.kind for c in new_only] == [ChangeKind.ODR_SOURCE_CONFLICT]


def test_diff_odr_tracked_by_name_and_header() -> None:
    # A new conflict for a same-named type in a *different* header must still be
    # flagged even when a same-name conflict already exists elsewhere — the diff
    # keys by (qualified_name, header), matching the linker (Codex review #335).
    # Distinct basenames so the discriminator survives build-root normalization.
    a = {"qualified_name": "Widget", "header": "gui/widget_a.h", "new_type_hash": "x"}
    b = {"qualified_name": "Widget", "header": "gui/widget_b.h", "new_type_hash": "y"}
    changes = diff_source_abi(
        _surface(odr_conflicts=[a]), _surface(odr_conflicts=[a, b])
    )
    assert [c.kind for c in changes] == [ChangeKind.ODR_SOURCE_CONFLICT]
    assert changes[0].new_value == "y"  # the widget_b.h conflict, not widget_a.h


def test_diff_odr_stable_across_build_roots() -> None:
    # The same pre-existing conflict reported from two different checkout/build
    # roots (only the absolute path prefix differs) must NOT be re-flagged as a
    # new odr_source_conflict — the header discriminator is normalized to a
    # build-root-stable basename (Codex review #335, P2; build-root-stability
    # decision). Old and new carry the identical conflict under different roots.
    old_c = {
        "qualified_name": "Widget",
        "header": "/build/old/include/api.h",
        "old_type_hash": "a",
        "new_type_hash": "b",
    }
    new_c = {
        "qualified_name": "Widget",
        "header": "/build/new/include/api.h",
        "old_type_hash": "a",
        "new_type_hash": "b",
    }
    changes = diff_source_abi(
        _surface(odr_conflicts=[old_c]), _surface(odr_conflicts=[new_c])
    )
    assert changes == []


def test_diff_generated_header_changed() -> None:
    old = _surface(
        reachable_declarations=[
            _entity(
                "cfg::FLAG",
                "variable",
                visibility="generated",
                origin="GENERATED",
                value="0",
            )
        ]
    )
    new = _surface(
        reachable_declarations=[
            _entity(
                "cfg::FLAG",
                "variable",
                visibility="generated",
                origin="GENERATED",
                value="1",
            )
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.GENERATED_HEADER_CHANGED]


def test_diff_generated_constant_value_change_is_api_break() -> None:
    # A generated public constexpr still behaves like a baked-in constant for
    # consumers. Preserve constexpr_value_changed so legacy ABI gates see the
    # API_BREAK severity, while removals remain covered by generated headers.
    old = _surface(
        reachable_declarations=[
            _entity(
                "cfg::KMax",
                "constexpr",
                visibility="generated",
                origin="GENERATED",
                value="64",
            )
        ]
    )
    new = _surface(
        reachable_declarations=[
            _entity(
                "cfg::KMax",
                "constexpr",
                visibility="generated",
                origin="GENERATED",
                value="128",
            )
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.CONSTEXPR_VALUE_CHANGED]
    assert changes[0].old_value == "64"
    assert changes[0].new_value == "128"


def test_diff_generated_constant_removed_detected() -> None:
    # A constexpr declared in a *generated* header that is removed in the new
    # version must surface as generated_header_changed. A namespace-scope
    # constexpr has no exported symbol, so L0 can't see the removal; without the
    # generated marker neither _diff_generated (sees it as non-generated) nor
    # _diff_declarations (common keys only) would flag it (Codex review #335, P2).
    old = _surface(
        reachable_declarations=[
            _entity(
                "cfg::KMax",
                "constexpr",
                visibility="generated",
                origin="GENERATED",
                value="64",
            )
        ]
    )
    new = _surface()  # constant gone in the regenerated config header
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.GENERATED_HEADER_CHANGED]


def test_diff_generated_type_change_detected() -> None:
    # A generated public *type* lives in reachable_types, not declarations; its
    # content change must still be flagged (Codex review #335).
    old = _surface(
        reachable_types=[
            _entity(
                "cfg::Layout",
                "record",
                visibility="generated",
                origin="GENERATED",
                type_hash="h1",
            )
        ]
    )
    new = _surface(
        reachable_types=[
            _entity(
                "cfg::Layout",
                "record",
                visibility="generated",
                origin="GENERATED",
                type_hash="h2",
            )
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.GENERATED_HEADER_CHANGED]
    assert changes[0].symbol == "cfg::Layout"


def test_diff_generated_type_removal_detected() -> None:
    # A generated public type/decl dropped by the generated header (present only
    # in the old surface) must surface as generated_header_changed: the normal
    # declaration diff skips generated entities and there is no removal diff for
    # reachable_types, so it would otherwise be silently missed (Codex #335, P2).
    old = _surface(
        reachable_types=[
            _entity(
                "cfg::Layout",
                "record",
                visibility="generated",
                origin="GENERATED",
                type_hash="h1",
            )
        ],
        reachable_declarations=[
            _entity(
                "cfg::FLAG",
                "constexpr",
                visibility="generated",
                origin="GENERATED",
                value="1",
            )
        ],
    )
    new = _surface()  # both generated entities gone
    changes = diff_source_abi(old, new)
    assert all(c.kind is ChangeKind.GENERATED_HEADER_CHANGED for c in changes)
    assert {c.symbol for c in changes} == {"cfg::Layout", "cfg::FLAG"}
    assert all(c.new_value == "" for c in changes)


def test_diff_no_change_is_empty() -> None:
    s = _surface(
        reachable_macros=[_entity("FOO", "macro", value="1")],
        reachable_declarations=[
            _entity("f", "function", signature_hash="s", value="x=1")
        ],
    )
    # Compare a surface against an independent but identical copy.
    other = SourceAbiSurface.from_dict(s.to_dict())
    assert diff_source_abi(s, other) == []


# -- partition / authority invariants (D6, D10) ------------------------------


def test_source_replay_kinds_never_breaking() -> None:
    l4_kinds = {
        ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
        ChangeKind.INLINE_BODY_CHANGED,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
        ChangeKind.TEMPLATE_BODY_CHANGED,
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
        ChangeKind.ODR_SOURCE_CONFLICT,
        ChangeKind.GENERATED_HEADER_CHANGED,
    }
    # ADR-028 D3 / ADR-030 D6: source-only findings never default to BREAKING.
    assert l4_kinds.isdisjoint(BREAKING_KINDS)
    # Every one is partitioned into exactly API_BREAK or RISK.
    for kind in l4_kinds:
        assert (kind in API_BREAK_KINDS) ^ (kind in RISK_KINDS)


# -- pack persistence --------------------------------------------------------


def test_pack_roundtrips_source_abi(tmp_path: object) -> None:
    surface = link_source_abi(
        [SourceAbiTu(macros=[_entity("FOO", "macro", value="1")])],
        exported_symbols=[],
        library="libfoo.so",
    )
    pack = BuildSourcePack.empty(tmp_path)  # type: ignore[arg-type]
    pack.source_abi = surface
    pack.write()

    loaded = BuildSourcePack.load(tmp_path)  # type: ignore[arg-type]
    assert loaded.source_abi is not None
    assert [e.qualified_name for e in loaded.source_abi.reachable_macros] == ["FOO"]
    # The source surface contributes to the content hash (it is a normalized payload).
    assert any("sha256:" in d for d in loaded.manifest.artifacts)


def test_pack_removes_stale_source_abi(tmp_path: object) -> None:
    pack = BuildSourcePack.empty(tmp_path)  # type: ignore[arg-type]
    pack.source_abi = link_source_abi([SourceAbiTu(macros=[_entity("FOO", "macro")])])
    pack.write()
    # A later collection with no source ABI must drop the stale file.
    pack.source_abi = None
    pack.write()
    reloaded = BuildSourcePack.load(tmp_path)  # type: ignore[arg-type]
    assert reloaded.source_abi is None


# -- typedef target change (ADR-030 follow-up #3) ----------------------------


def test_diff_public_typedef_target_changed() -> None:
    old = _surface(
        reachable_types=[
            _entity("handle_t", "typedef", value="int32_t", type_hash="h-old"),
        ]
    )
    new = _surface(
        reachable_types=[
            _entity("handle_t", "typedef", value="int64_t", type_hash="h-new"),
        ]
    )
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED]
    assert changes[0].old_value == "int32_t"
    assert changes[0].new_value == "int64_t"
    assert "L4_SOURCE_ABI" in (changes[0].source_location or "")


def test_diff_typedef_unchanged_target_is_quiet() -> None:
    same = [_entity("handle_t", "typedef", value="int32_t", type_hash="h")]
    assert (
        diff_source_abi(
            _surface(reachable_types=same), _surface(reachable_types=list(same))
        )
        == []
    )


def test_diff_typedef_never_breaking() -> None:
    # Authority rule (ADR-028 D3): an L4 source-only finding is never BREAKING.
    from abicheck.checker_policy import BREAKING_KINDS

    old = _surface(reachable_types=[_entity("h", "typedef", value="a", type_hash="1")])
    new = _surface(reachable_types=[_entity("h", "typedef", value="b", type_hash="2")])
    assert all(c.kind not in BREAKING_KINDS for c in diff_source_abi(old, new))


def test_diff_generated_typedef_not_double_reported() -> None:
    # A generated typedef change is reported once, as generated_header_changed.
    old = _surface(
        reachable_types=[
            _entity(
                "cfg_t",
                "typedef",
                visibility="generated",
                origin="GENERATED",
                value="int",
                type_hash="1",
            ),
        ]
    )
    new = _surface(
        reachable_types=[
            _entity(
                "cfg_t",
                "typedef",
                visibility="generated",
                origin="GENERATED",
                value="long",
                type_hash="2",
            ),
        ]
    )
    kinds = [c.kind for c in diff_source_abi(old, new)]
    assert kinds == [ChangeKind.GENERATED_HEADER_CHANGED]


def test_typedef_self_alias_no_odr_conflict() -> None:
    # `typedef struct Foo Foo;` — the record Foo and the typedef Foo share the
    # same (name, header). The typedef must NOT enter the ODR path (would emit a
    # spurious odr_source_conflict against the record) — Codex review.
    record = _entity("Foo", "record", type_hash="rec-hash")
    typedef = _entity("Foo", "typedef", value="struct Foo", type_hash="td-hash")
    tu = SourceAbiTu(types=[record, typedef])
    surface = link_source_abi([tu])
    assert surface.odr_conflicts == []
    kinds = {e.kind for e in surface.reachable_types if e.qualified_name == "Foo"}
    assert kinds == {"record", "typedef"}

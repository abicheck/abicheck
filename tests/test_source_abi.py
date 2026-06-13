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
        functions=[_entity("foo::bar", "function", mangled="_ZN3foo3barEv")],
    )
    restored = SourceAbiTu.from_dict(tu.to_dict())
    assert restored.schema_version == SOURCE_ABI_VERSION
    assert restored.tu_id == tu.tu_id
    assert restored.extractor == {"name": "castxml", "version": "0.6"}
    assert [e.qualified_name for e in restored.macros] == ["FOO_SIZE"]
    assert restored.functions[0].mangled_name == "_ZN3foo3barEv"
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


def test_linker_keeps_unmangled_overloads_distinct() -> None:
    # castxml omits a mangled name for some decls (notably constructors), so two
    # public overloads share the bare qualified_name "Widget". identity() folds
    # in signature_hash so they stay distinct instead of collapsing onto one key
    # and silently dropping an overload (Codex review #335, P2).
    ctor_int = _entity("Widget", "function", mangled="", signature_hash="si", value="x=1")
    ctor_dbl = _entity("Widget", "function", mangled="", signature_hash="sd", value="y=0")
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

    assert _ctor("build/old/include/foo.h").identity() == _ctor(
        "build/new/include/foo.h"
    ).identity()


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
    old = _surface(reachable_types=[
        _entity("handle_t", "typedef", value="int32_t", type_hash="h-old"),
    ])
    new = _surface(reachable_types=[
        _entity("handle_t", "typedef", value="int64_t", type_hash="h-new"),
    ])
    changes = diff_source_abi(old, new)
    assert [c.kind for c in changes] == [ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED]
    assert changes[0].old_value == "int32_t"
    assert changes[0].new_value == "int64_t"
    assert "L4_SOURCE_ABI" in (changes[0].source_location or "")


def test_diff_typedef_unchanged_target_is_quiet() -> None:
    same = [_entity("handle_t", "typedef", value="int32_t", type_hash="h")]
    assert diff_source_abi(_surface(reachable_types=same),
                           _surface(reachable_types=list(same))) == []


def test_diff_typedef_never_breaking() -> None:
    # Authority rule (ADR-028 D3): an L4 source-only finding is never BREAKING.
    from abicheck.checker_policy import BREAKING_KINDS
    old = _surface(reachable_types=[_entity("h", "typedef", value="a", type_hash="1")])
    new = _surface(reachable_types=[_entity("h", "typedef", value="b", type_hash="2")])
    assert all(c.kind not in BREAKING_KINDS for c in diff_source_abi(old, new))


def test_diff_generated_typedef_not_double_reported() -> None:
    # A generated typedef change is reported once, as generated_header_changed.
    old = _surface(reachable_types=[
        _entity("cfg_t", "typedef", visibility="generated", origin="GENERATED",
                value="int", type_hash="1"),
    ])
    new = _surface(reachable_types=[
        _entity("cfg_t", "typedef", visibility="generated", origin="GENERATED",
                value="long", type_hash="2"),
    ])
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

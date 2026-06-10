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

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
    ChangeKind,
)
from abicheck.evidence import (
    SOURCE_ABI_VERSION,
    EvidencePack,
    SourceAbiSurface,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    diff_source_abi,
    link_source_abi,
)
from abicheck.evidence.source_abi import EVIDENCE_TIER_L4

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
    assert (
        restored.mappings["source_decl_to_binary_symbol"]["foo::bar"] == "_ZN3foo3barEv"
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
    mapping = surface.mappings["source_decl_to_binary_symbol"]
    assert mapping["foo::shipped"] == "_ZN3foo7shippedEv"
    assert mapping["foo::header_only"] == ""
    # exported symbol with no source decl is unmatched
    assert "_ZN3foo9orphan_symEv" in surface.unmatched["symbols_without_decl"]
    # public decl with no exported symbol is unmatched
    assert "foo::header_only" in surface.unmatched["decls_without_symbol"]


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
    tu1 = SourceAbiTu(types=[_entity("Widget", "record", type_hash="hashA")])
    tu2 = SourceAbiTu(types=[_entity("Widget", "record", type_hash="hashB")])
    surface = link_source_abi([tu1, tu2])
    assert len(surface.odr_conflicts) == 1
    assert surface.odr_conflicts[0]["qualified_name"] == "Widget"


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
    pack = EvidencePack.empty(tmp_path)  # type: ignore[arg-type]
    pack.source_abi = surface
    pack.write()

    loaded = EvidencePack.load(tmp_path)  # type: ignore[arg-type]
    assert loaded.source_abi is not None
    assert [e.qualified_name for e in loaded.source_abi.reachable_macros] == ["FOO"]
    # The source surface contributes to the content hash (it is a normalized payload).
    assert any("sha256:" in d for d in loaded.manifest.artifacts)


def test_pack_removes_stale_source_abi(tmp_path: object) -> None:
    pack = EvidencePack.empty(tmp_path)  # type: ignore[arg-type]
    pack.source_abi = link_source_abi([SourceAbiTu(macros=[_entity("FOO", "macro")])])
    pack.write()
    # A later collection with no source ABI must drop the stale file.
    pack.source_abi = None
    pack.write()
    reloaded = EvidencePack.load(tmp_path)  # type: ignore[arg-type]
    assert reloaded.source_abi is None

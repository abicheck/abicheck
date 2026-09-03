# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The legacy-flat-snapshot adapter and the typedef cutover's index selector
(ADR-063 Phase 6B).

Two properties carry the whole cutover's safety and are therefore stated
here as invariants over generated input rather than as fixed examples:

1. :func:`render_display_name` round-trips a synthetic identity exactly, and
   refuses (``None``) any identity carrying a parse-order ordinal — the
   property :func:`typedef_index_pair`'s fidelity gate rests on.
2. The gate is *both-or-neither*: it never pairs an IR-backed index with an
   adapted one, whatever the two sides' evidence looks like.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.compare.typedefs import typedef_index_pair
from abicheck.model import AbiSnapshot
from abicheck.model.fact import Fact
from abicheck.model.identity import (
    Anonymous,
    EntityId,
    EntityKind,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    Record,
    entity_id_for_typedef,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex
from abicheck.model.semantic_ir_legacy_adapter import (
    SYNTHETIC_IDENTITY_EXTRA,
    legacy_typedef_ir,
    producer_entity_id,
    render_display_name,
)

_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=6)


def _snap(**kw) -> AbiSnapshot:
    base = dict(library="libfoo.so.1", version="1.0.0")
    base.update(kw)
    return AbiSnapshot(**base)


def _typedef_ir(entries: dict[EntityId, str]) -> SemanticIR:
    return SemanticIR(
        occurrences={
            OccurrenceId(eid): CanonicalEntity(canonical_spelling=Fact.present(v))
            for eid, v in entries.items()
        }
    )


# -- render_display_name ---------------------------------------------------


class TestRenderDisplayName:
    @given(scope=st.lists(_names, max_size=4), leaf=_names)
    def test_renders_a_namespace_chain_as_the_qualified_spelling(
        self, scope: list[str], leaf: str
    ) -> None:
        eid = entity_id_for_typedef(tuple(Namespace(n) for n in scope), leaf)
        assert render_display_name(eid) == "::".join([*scope, leaf])

    @given(outer=_names, leaf=_names)
    def test_a_record_scope_renders_by_name_alone(self, outer: str, leaf: str) -> None:
        """``Record.access`` is payload, not identity, and never appears in
        a qualified spelling -- so two access levels must render alike."""
        public = entity_id_for_typedef((Record(outer, access="public"),), leaf)
        private = entity_id_for_typedef((Record(outer, access="private"),), leaf)
        assert render_display_name(public) == f"{outer}::{leaf}"
        assert render_display_name(public) == render_display_name(private)

    @given(name=_names, tag=st.text(max_size=3), leaf=_names)
    def test_an_inline_namespace_renders_by_name(
        self, name: str, tag: str, leaf: str
    ) -> None:
        eid = entity_id_for_typedef((InlineNamespace(name, tag),), leaf)
        assert render_display_name(eid) == f"{name}::{leaf}"

    @given(
        prefix=st.lists(_names, max_size=2),
        suffix=st.lists(_names, max_size=2),
        ordinal=st.integers(min_value=0, max_value=8),
        leaf=_names,
    )
    def test_any_anonymous_segment_anywhere_refuses_to_render(
        self, prefix: list[str], suffix: list[str], ordinal: int, leaf: str
    ) -> None:
        """Position-independent: one parse-order ordinal anywhere in the
        chain makes the whole identity unrenderable. A best-effort string
        here would silently map two distinct anonymous siblings onto one
        display key, which is exactly what the fidelity gate exists to
        catch."""
        scope = (
            *(Namespace(n) for n in prefix),
            Anonymous("struct", ordinal),
            *(Namespace(n) for n in suffix),
        )
        assert render_display_name(entity_id_for_typedef(scope, leaf)) is None

    @given(owner=_names, block=st.integers(min_value=0, max_value=8), leaf=_names)
    def test_a_function_local_segment_refuses_to_render(
        self, owner: str, block: int, leaf: str
    ) -> None:
        owner_id = EntityId(scope=(), kind=EntityKind.FUNCTION, leaf_name=owner)
        scope = (LocalToFunction(owner=owner_id, block_ordinal=block),)
        assert render_display_name(entity_id_for_typedef(scope, leaf)) is None


# -- synthetic identity ----------------------------------------------------


class TestSyntheticIdentity:
    @given(alias=st.text(min_size=0, max_size=20))
    def test_a_synthetic_identity_round_trips_its_display_name(
        self, alias: str
    ) -> None:
        """Load-bearing for behavior preservation: a detector reading the
        adapter must emit the identical ``Change.symbol`` text it emitted
        before its migration, for *any* alias spelling -- including one with
        ``::`` in it, which is why the whole flat spelling goes in
        ``leaf_name`` rather than being split into scope segments."""
        ir = legacy_typedef_ir(_snap(), {alias: "int"})
        (occ,) = ir.occurrences
        assert render_display_name(occ.entity_id) == alias

    @given(alias=_names)
    def test_a_synthetic_identity_is_never_offered_as_producer_evidence(
        self, alias: str
    ) -> None:
        ir = legacy_typedef_ir(_snap(), {alias: "int"})
        (occ,) = ir.occurrences
        assert tuple(occ.entity_id.extra) == SYNTHETIC_IDENTITY_EXTRA
        assert producer_entity_id(occ.entity_id) is None

    @given(scope=st.lists(_names, max_size=3), leaf=_names)
    def test_a_real_backend_identity_is_kept_and_reported_as_producer_evidence(
        self, scope: list[str], leaf: str
    ) -> None:
        real = entity_id_for_typedef(tuple(Namespace(n) for n in scope), leaf)
        alias = "::".join([*scope, leaf])
        snap = _snap(typedef_entity_ids={alias: real})
        ir = legacy_typedef_ir(snap, {alias: "int"})
        (occ,) = ir.occurrences
        assert occ.entity_id == real
        assert producer_entity_id(occ.entity_id) == real

    def test_a_sidecar_id_that_does_not_render_to_its_own_key_is_refused(
        self,
    ) -> None:
        """A sidecar whose rendering disagrees with its map key would key the
        occurrence under a name the detector cannot then find, turning a
        projection mismatch into a phantom removal. The adapter falls back
        to a synthetic identity instead."""
        mismatched = entity_id_for_typedef((Namespace("other"),), "Alias")
        snap = _snap(typedef_entity_ids={"ns::Alias": mismatched})
        ir = legacy_typedef_ir(snap, {"ns::Alias": "int"})
        (occ,) = ir.occurrences
        assert producer_entity_id(occ.entity_id) is None
        assert render_display_name(occ.entity_id) == "ns::Alias"


# -- the fidelity gate -----------------------------------------------------


class TestTypedefIndexPair:
    def test_a_faithful_ir_on_both_sides_is_used(self) -> None:
        """A real producer populates ``semantic_ir`` and the legacy
        ``typedef_entity_ids`` sidecar from the same pass (see
        ``dumper.py``'s own ``ast_result.typedef_entity_ids``), so a
        genuinely faithful snapshot carries both, agreeing -- the identity
        half of the fidelity gate (Codex review on PR #1041, follow-up
        round) requires exactly that agreement."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        maps = {"ns::Alias": "int"}
        old = _snap(
            semantic_ir=_typedef_ir({eid: "int"}),
            typedef_entity_ids={"ns::Alias": eid},
        )
        new = _snap(
            semantic_ir=_typedef_ir({eid: "long"}),
            typedef_entity_ids={"ns::Alias": eid},
        )
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs={"ns::Alias": "long"}
        )
        # The real IR was used: its own payload, not the adapter's.
        assert old_index.fact(eid, "canonical_spelling").value == "int"
        assert new_index.fact(eid, "canonical_spelling").value == "long"

    def test_a_sidecar_identity_disagreeing_with_the_ir_forces_the_fallback(
        self,
    ) -> None:
        """Regression for the Codex review on PR #1041, follow-up round:
        names and values matching is not enough -- the real IR's own
        ``EntityId`` for an alias must also agree with what the legacy
        adapter would independently assign that same alias via the
        ``typedef_entity_ids`` sidecar. A loaded or Python-constructed
        snapshot can carry a real IR resolving ``ns::Alias`` under one
        scope while its sidecar names a *different*, differently-scoped
        identity that happens to render back to the identical alias text
        (e.g. two same-named typedefs the sidecar and the IR each
        associate with a different one of two structurally distinct
        anonymous scopes). Picking the IR path there would stamp the
        wrong ``entity_id`` on the emitted finding relative to the
        pre-cutover detector, silently changing which stored
        ``entity:``-alias suppression rules match -- so this must fall
        back to the adapter instead, even though every name and value
        still matches exactly."""
        ir_eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        sidecar_eid = entity_id_for_typedef((Namespace("other"),), "Alias")
        maps = {"ns::Alias": "int"}
        old = _snap(
            semantic_ir=_typedef_ir({ir_eid: "int"}),
            typedef_entity_ids={"ns::Alias": sidecar_eid},
        )
        new = _snap(
            semantic_ir=_typedef_ir({ir_eid: "int"}),
            typedef_entity_ids={"ns::Alias": sidecar_eid},
        )
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        # The adapter ran, not the real IR: its identities are synthetic
        # (the sidecar's own EntityId doesn't render back to "ns::Alias",
        # since it names a different scope, so `legacy_typedef_ir` falls
        # back to a synthetic identity for it too).
        for index in (old_index, new_index):
            for eid_ in index.entities_of_kind(EntityKind.TYPEDEF):
                assert producer_entity_id(eid_) is None

    def test_an_ir_missing_one_alias_falls_back_on_both_sides(self) -> None:
        """Set equality, not a count: the IR here has the right *number* of
        typedefs on the old side and still disagrees about which."""
        present = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(semantic_ir=_typedef_ir({present: "int"}))
        new = _snap(semantic_ir=_typedef_ir({present: "int"}))
        maps = {"ns::Alias": "int", "ns::Other": "char"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        for index in (old_index, new_index):
            names = {
                render_display_name(e)
                for e in index.entities_of_kind(EntityKind.TYPEDEF)
            }
            assert names == {"ns::Alias", "ns::Other"}

    def test_one_faithful_side_and_one_unfaithful_side_never_mix(self) -> None:
        """Both-or-neither. Pairing an IR-backed old side with an adapted new
        side compares two differently-derived key spaces, which fabricates a
        removal out of a projection difference."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(semantic_ir=_typedef_ir({eid: "int"}))
        new = _snap()  # no SemanticIR at all
        maps = {"ns::Alias": "int"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        # Both adapted: the adapter marks its identities synthetic, the real
        # IR path does not -- so this is a direct check of which side ran.
        for index in (old_index, new_index):
            for eid_ in index.entities_of_kind(EntityKind.TYPEDEF):
                assert producer_entity_id(eid_) is None

    def test_an_unrenderable_anonymous_scope_forces_the_fallback(self) -> None:
        """The concrete case ``render_display_name``'s ``None`` exists for:
        the IR carries the typedef, but under an identity with no faithful
        flat spelling, so it cannot be matched to the alias map."""
        anon = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        ir = _typedef_ir({anon: "int"})
        old = _snap(semantic_ir=ir)
        new = _snap(semantic_ir=ir)
        maps = {"Alias": "int"}
        old_index, _ = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        assert {
            render_display_name(e)
            for e in old_index.entities_of_kind(EntityKind.TYPEDEF)
        } == {"Alias"}

    def test_two_empty_sides_still_agree_and_use_the_ir_path(self) -> None:
        old_index, new_index = typedef_index_pair(
            _snap(), _snap(), old_typedefs={}, new_typedefs={}
        )
        assert isinstance(old_index, SemanticIRIndex)
        assert old_index.entities_of_kind(EntityKind.TYPEDEF) == {}
        assert new_index.entities_of_kind(EntityKind.TYPEDEF) == {}

    def test_matching_names_with_a_stale_underlying_spelling_still_falls_back(
        self,
    ) -> None:
        """The name-only reading of the gate this regression test pins: a
        snapshot's ``semantic_ir`` can carry the *right* typedef identity
        while its ``canonical_spelling`` disagrees with what the legacy
        alias map (independently resolved from the same snapshot's flat
        typedef collection) says the same alias resolves to -- e.g. a
        hand-built or loaded snapshot where the two representations were
        never cross-validated. Comparing display names alone would accept
        this IR and either silently drop or silently fabricate a
        ``TYPEDEF_BASE_CHANGED`` finding depending on which side is stale.
        The gate must also compare the resolved underlying spelling, so this
        must still fall back to the legacy adapter on both sides."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        # The IR's own value ("long") disagrees with the alias map's value
        # ("int") for the identical, correctly-identified alias.
        old = _snap(semantic_ir=_typedef_ir({eid: "long"}))
        new = _snap(semantic_ir=_typedef_ir({eid: "long"}))
        maps = {"ns::Alias": "int"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        # Both adapted (fell back to the legacy projection), not the real IR
        # whose stale "long" would otherwise have leaked through.
        for index in (old_index, new_index):
            for eid_ in index.entities_of_kind(EntityKind.TYPEDEF):
                assert producer_entity_id(eid_) is None
                assert index.fact(eid_, "canonical_spelling").value == "int"


def test_typedef_identities_by_alias_skips_an_unrenderable_entity() -> None:
    """Defensive-floor coverage for `_typedef_identities_by_alias`: an
    entity whose identity has no faithful flat rendering (an anonymous
    scope) is skipped rather than keyed under `None` -- direct unit test
    since `typedef_index_pair`'s own callers never reach this branch (an
    unrenderable real-IR entity already fails the name-sequence check
    first, short-circuiting before the identity comparison runs)."""
    from abicheck.compare.typedefs import _typedef_identities_by_alias

    anon = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
    named = entity_id_for_typedef((Namespace("ns"),), "Alias")
    index = SemanticIRIndex(_typedef_ir({anon: "int", named: "int"}))
    assert _typedef_identities_by_alias(index) == {"ns::Alias": named}

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

from abicheck.compare.typedefs import diff_typedefs, typedef_index_pair
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
    entity_id_for_constant,
    entity_id_for_typedef,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex
from abicheck.model.semantic_ir_legacy_adapter import (
    SYNTHETIC_IDENTITY_EXTRA,
    legacy_constant_ir,
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


# -- the constant cohort's adapter (ADR-063 Phase 6B, cohort 2) ------------


class TestLegacyConstantIr:
    """``legacy_constant_ir`` is ``legacy_typedef_ir`` with a value-literal
    payload instead of a resolved-type-spelling one -- the same round-trip,
    fallback, and sidecar-mismatch properties above hold identically, so
    this is a compact confirmation rather than a full re-derivation. See
    ``tests/test_constant_cutover.py`` for the family's own detector-level
    and fidelity-gate coverage."""

    @given(name=st.text(min_size=0, max_size=20), value=_names)
    def test_a_synthetic_identity_round_trips_its_display_name(
        self, name: str, value: str
    ) -> None:
        ir = legacy_constant_ir(_snap(), {name: value})
        (occ,) = ir.occurrences
        assert render_display_name(occ.entity_id) == name

    def test_a_synthetic_identity_is_never_offered_as_producer_evidence(self) -> None:
        ir = legacy_constant_ir(_snap(), {"X": "1"})
        (occ,) = ir.occurrences
        assert tuple(occ.entity_id.extra) == SYNTHETIC_IDENTITY_EXTRA
        assert producer_entity_id(occ.entity_id) is None

    def test_a_real_backend_identity_is_kept_and_reported_as_producer_evidence(
        self,
    ) -> None:
        real = entity_id_for_constant((Namespace("ns"),), "X")
        snap = _snap(constant_entity_ids={"ns::X": real})
        ir = legacy_constant_ir(snap, {"ns::X": "1"})
        (occ,) = ir.occurrences
        assert occ.entity_id == real
        assert producer_entity_id(occ.entity_id) == real

    def test_a_sidecar_id_that_does_not_render_to_its_own_key_is_refused(self) -> None:
        mismatched = entity_id_for_constant((Namespace("other"),), "X")
        snap = _snap(constant_entity_ids={"ns::X": mismatched})
        ir = legacy_constant_ir(snap, {"ns::X": "1"})
        (occ,) = ir.occurrences
        assert producer_entity_id(occ.entity_id) is None
        assert render_display_name(occ.entity_id) == "ns::X"

    def test_the_value_payload_is_the_raw_text_unchanged(self) -> None:
        """No canonicalization is applied -- matches ``extract/
        semantic_normalizer.py``'s "Scope of the fourth slice"."""
        ir = legacy_constant_ir(_snap(), {"X": "0x2A"})
        (occ,) = ir.occurrences
        spelling = ir.occurrences[occ].canonical_spelling
        assert spelling.is_present
        assert spelling.value == "0x2A"


# -- the fidelity gate -----------------------------------------------------


class TestTypedefIndexPair:
    """ADR-063 Track T3: ``typedef_index_pair`` is no longer a fidelity gate
    -- when both sides carry a real ``SemanticIR`` it is used directly, with
    no comparison against (and no fallback to) the legacy projection. A
    disagreement between the real IR and its own ``typedef_entity_ids``
    sidecar is caught earlier, at snapshot construction, as a hard
    :class:`~abicheck.errors.SemanticIrAuthorityError` -- see
    ``TestConstructionTimeConsistency`` below."""

    def test_a_faithful_ir_on_both_sides_is_used(self) -> None:
        """A real producer populates ``semantic_ir`` and the legacy
        ``typedef_entity_ids`` sidecar from the same pass (see
        ``dumper.py``'s own ``ast_result.typedef_entity_ids``), so a
        genuinely faithful snapshot carries both, agreeing."""
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

    def test_the_real_ir_is_trusted_even_when_the_legacy_map_disagrees_on_membership(
        self,
    ) -> None:
        """Before T3, an IR whose alias *set* didn't reproduce the legacy
        map exactly (a count match with a different member) fell back to
        the adapter on both sides. After T3 there is nothing left to fall
        back to when both sides carry a real IR: the IR's own alias set is
        authoritative, whatever a *stale* legacy map says -- e.g. a legacy
        ``typedefs_qualified``/``typedef_entity_ids`` sidecar this
        comparison was handed that predates a later real parse."""
        present = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(semantic_ir=_typedef_ir({present: "int"}))
        new = _snap(semantic_ir=_typedef_ir({present: "int"}))
        # The legacy map this comparison was handed names a typedef the
        # real IR does not -- no longer enough to force a fallback.
        maps = {"ns::Alias": "int", "ns::Other": "char"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        for index in (old_index, new_index):
            names = {
                render_display_name(e)
                for e in index.entities_of_kind(EntityKind.TYPEDEF)
            }
            assert names == {"ns::Alias"}
            # The real IR ran, not the adapter: its identity is a genuine
            # producer one, never synthetic.
            for eid_ in index.entities_of_kind(EntityKind.TYPEDEF):
                assert producer_entity_id(eid_) is not None

    def test_each_side_is_decided_independently_not_both_or_neither(self) -> None:
        """ADR-063 Track T3 (Codex review, PR #1078): a side with a real IR
        uses it directly even when the *other* side has none at all -- the
        old both-or-neither rule would have discarded the old side's real
        IR in favor of a legacy reconstruction of it, purely because the new
        side had nothing to be authoritative over. See
        ``compare.typedefs.typedef_index_pair``'s own docstring for why
        mixing is safe: both index shapes are matched by rendered alias
        name, not by ``EntityId``."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(semantic_ir=_typedef_ir({eid: "int"}))
        new = _snap()  # no SemanticIR at all
        maps = {"ns::Alias": "int"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        # The real IR ran for old (a genuine producer identity survives);
        # the adapter ran for new (no IR to prefer, so its identity is
        # synthetic) -- each side decided on its own merits.
        for eid_ in old_index.entities_of_kind(EntityKind.TYPEDEF):
            assert producer_entity_id(eid_) is not None
        for eid_ in new_index.entities_of_kind(EntityKind.TYPEDEF):
            assert producer_entity_id(eid_) is None

    def test_the_ir_side_is_not_starved_when_both_sides_trust_qualified_naming(
        self,
    ) -> None:
        """The concrete regression per-side independence closes (Codex
        review, PR #1078, first round): a stored baseline with no
        ``SemanticIR`` but real, *qualified-trusted* flat typedefs, compared
        against a live snapshot that carries a real IR -- and, as every real
        producer does, the identical content in its own flat
        ``typedefs_qualified`` too. Under the old both-or-neither rule the
        live side's real IR would have been discarded in favor of a legacy
        reconstruction of it purely because the old side lacked IR; deciding
        per side means it never is."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(typedefs_qualified={"ns::Alias": "int"}, ast_producer="")
        new = _snap(
            typedefs_qualified={"ns::Alias": "int"},
            semantic_ir=_typedef_ir({eid: "int"}),
        )
        old_index, new_index = typedef_index_pair(
            old,
            new,
            old_typedefs={"ns::Alias": "int"},
            new_typedefs={"ns::Alias": "int"},
        )
        changes = diff_typedefs(
            old_index,
            new_index,
            exclude_stdlib_namespaces=False,
            suppress_removed=False,
            is_non_abi_surface_type=lambda name, *, exclude_stdlib_namespaces: False,
        )
        assert changes == []
        # The real IR ran for `new`: its identity is a genuine producer one.
        for eid_ in new_index.entities_of_kind(EntityKind.TYPEDEF):
            assert producer_entity_id(eid_) is not None

    def test_bare_mode_uses_the_legacy_adapter_for_both_sides_even_with_real_ir(
        self,
    ) -> None:
        """Codex review, PR #1078, second round: when the OLD side does not
        trust qualified naming (a genuinely pre-v25/pre-v38 baseline, which
        also means it carries no ``SemanticIR`` at all), the whole
        comparison operates in *bare*-keyed mode -- and a real ``SemanticIR``
        on the other side, which always renders under its own fully
        *qualified* name, must not be used directly there either: doing so
        would key that side under ``"ns::Alias"`` while the bare-mode old
        side is keyed under ``"Alias"``, fabricating a removal out of a pure
        naming-granularity mismatch rather than a real one. Both sides must
        render through the legacy adapter over the comparison's own
        bare-keyed maps instead, exactly as every pre-T3 comparison already
        did in this narrower case."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(typedefs={"Alias": "int"}, ast_producer="")
        # `new` carries a real, qualified-named IR *and* the matching
        # `typedefs_qualified` a real producer would also populate -- but
        # `_typedef_diff_maps` still resolves the *bare* maps for both
        # sides here, since `old` cannot express qualified names at all.
        new = _snap(
            typedefs_qualified={"ns::Alias": "int"},
            typedefs={"Alias": "int"},
            semantic_ir=_typedef_ir({eid: "int"}),
        )
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs={"Alias": "int"}, new_typedefs={"Alias": "int"}
        )
        changes = diff_typedefs(
            old_index,
            new_index,
            exclude_stdlib_namespaces=False,
            suppress_removed=False,
            is_non_abi_surface_type=lambda name, *, exclude_stdlib_namespaces: False,
        )
        assert changes == []
        # The adapter ran for `new`, not its real IR: its identity is
        # synthetic, and it is keyed bare ("Alias"), not qualified.
        for eid_ in new_index.entities_of_kind(EntityKind.TYPEDEF):
            assert producer_entity_id(eid_) is None
            assert render_display_name(eid_) == "Alias"

    def test_an_unrenderable_anonymous_scope_is_used_but_invisible_to_aliasing(
        self,
    ) -> None:
        """The real IR is used directly (both sides carry one) -- an
        anonymous-scoped identity is still present in the raw index, but
        renders no alias, exactly the same as it would on the legacy
        adapter path: it genuinely has no flat spelling a ``Change.symbol``
        could name, on either backing."""
        anon = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        ir = _typedef_ir({anon: "int"})
        old = _snap(semantic_ir=ir)
        new = _snap(semantic_ir=ir)
        old_index, _ = typedef_index_pair(old, new, old_typedefs={}, new_typedefs={})
        assert anon in old_index.entities_of_kind(EntityKind.TYPEDEF)
        assert render_display_name(anon) is None

    def test_two_empty_sides_still_agree_and_use_the_ir_path(self) -> None:
        old_index, new_index = typedef_index_pair(
            _snap(), _snap(), old_typedefs={}, new_typedefs={}
        )
        assert isinstance(old_index, SemanticIRIndex)
        assert old_index.entities_of_kind(EntityKind.TYPEDEF) == {}
        assert new_index.entities_of_kind(EntityKind.TYPEDEF) == {}

    def test_a_stale_legacy_value_no_longer_matters_once_ir_is_authoritative(
        self,
    ) -> None:
        """Before T3, an IR whose ``canonical_spelling`` disagreed with the
        legacy alias map's own value forced a fallback. After T3 the real
        IR's value is trusted directly -- the whole point of authority
        transfer is that a real IR need not agree with a legacy projection
        derived independently from the same snapshot's flat collection."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        old = _snap(semantic_ir=_typedef_ir({eid: "long"}))
        new = _snap(semantic_ir=_typedef_ir({eid: "long"}))
        maps = {"ns::Alias": "int"}
        old_index, new_index = typedef_index_pair(
            old, new, old_typedefs=maps, new_typedefs=maps
        )
        for index in (old_index, new_index):
            for eid_ in index.entities_of_kind(EntityKind.TYPEDEF):
                assert producer_entity_id(eid_) is not None
                assert index.fact(eid_, "canonical_spelling").value == "long"


class TestConstructionTimeConsistency:
    """ADR-063 Track T3: the one piece of the old fidelity gate still worth
    checking (a sidecar identity disagreeing with the real IR for the same
    rendered name) now fires at snapshot construction, as a hard
    :class:`~abicheck.errors.SemanticIrAuthorityError`, instead of being
    silently absorbed by a per-comparison fallback."""

    def test_a_sidecar_identity_disagreeing_with_the_ir_is_a_hard_failure(
        self,
    ) -> None:
        """The same scenario the old fidelity gate used to route around
        silently: a real IR resolving ``ns::Alias`` under one scope while
        the ``typedef_entity_ids`` sidecar names a *different* identity for
        the identical rendered alias. This is now a producer bug that must
        fail loudly at construction, not a difference to adjudicate away at
        comparison time."""
        import pytest

        from abicheck.errors import SemanticIrAuthorityError

        ir_eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        sidecar_eid = entity_id_for_typedef((Namespace("other"),), "Alias")
        with pytest.raises(SemanticIrAuthorityError):
            _snap(
                semantic_ir=_typedef_ir({ir_eid: "int"}),
                typedef_entity_ids={"ns::Alias": sidecar_eid},
            )

    def test_an_agreeing_sidecar_is_not_a_failure(self) -> None:
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        snap = _snap(
            semantic_ir=_typedef_ir({eid: "int"}),
            typedef_entity_ids={"ns::Alias": eid},
        )
        assert snap.semantic_ir is not None

    def test_no_sidecar_at_all_is_not_a_failure(self) -> None:
        """The common, forward-looking case: a snapshot carrying only a
        real ``SemanticIR`` with no legacy sidecar populated at all. There
        is nothing to disagree with, so this must construct cleanly --
        requiring a populated sidecar would make the legacy dict an
        accidental prerequisite of the very representation meant to replace
        it."""
        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        snap = _snap(semantic_ir=_typedef_ir({eid: "int"}))
        assert snap.semantic_ir is not None

    def test_an_unrenderable_sidecar_entity_id_cannot_collide(self) -> None:
        """A sidecar id that does not itself render back to its own map key
        (e.g. an anonymous-scoped identity kept only for a legacy consumer)
        has nothing to be compared against -- the check only ever looks up
        the sidecar by the *IR's own* rendered name, and an anonymous IR
        entity renders no name to look up in the first place."""
        anon = entity_id_for_typedef((Anonymous("namespace", 0),), "Alias")
        snap = _snap(
            semantic_ir=_typedef_ir({anon: "int"}),
            typedef_entity_ids={"Alias": anon},
        )
        assert snap.semantic_ir is not None

    def test_the_constant_family_gets_the_identical_check(self) -> None:
        import pytest

        from abicheck.errors import SemanticIrAuthorityError

        ir_eid = entity_id_for_constant((Namespace("ns"),), "K")
        sidecar_eid = entity_id_for_constant((Namespace("other"),), "K")
        with pytest.raises(SemanticIrAuthorityError):
            _snap(
                semantic_ir=SemanticIR(
                    occurrences={
                        OccurrenceId(ir_eid): CanonicalEntity(
                            canonical_spelling=Fact.present("1")
                        )
                    }
                ),
                constant_entity_ids={"ns::K": sidecar_eid},
            )


class TestConsistencyCheckedOnDeserialize:
    """Codex review, PR #1078: ``AbiSnapshot.__post_init__`` alone cannot
    catch a disagreement in a *loaded* snapshot -- ``serialization.
    snapshot_from_dict`` constructs the ``AbiSnapshot`` (running
    ``__post_init__`` with ``semantic_ir`` still ``None``) and only
    afterward calls ``storage.semantic_ir_codec.decode_semantic_ir``, which
    mutates ``snap.semantic_ir`` directly -- bypassing ``__post_init__``
    entirely. ``snapshot_from_dict`` must re-run the Track T3 consistency
    check itself after that decode."""

    def test_a_disagreeing_stored_sidecar_is_caught_on_load(self) -> None:
        from abicheck.errors import SemanticIrAuthorityError
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
        from abicheck.storage.entity_ids import domain_entity_id_to_dto

        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        other = entity_id_for_typedef((Namespace("other"),), "Alias")
        snap = _snap(
            typedefs_qualified={"ns::Alias": "int"},
            typedef_entity_ids={"ns::Alias": eid},
            semantic_ir=_typedef_ir({eid: "int"}),
        )
        d = snapshot_to_dict(snap)
        # Corrupt the stored sidecar so it disagrees with the stored IR for
        # the identical rendered alias -- the same disagreement
        # `test_a_sidecar_identity_disagreeing_with_the_ir_is_a_hard_failure`
        # catches at construction time, reproduced here as it would arrive
        # from disk instead.
        d["typedef_entity_ids"]["ns::Alias"] = domain_entity_id_to_dto(other)
        import pytest

        with pytest.raises(SemanticIrAuthorityError):
            snapshot_from_dict(d)

    def test_an_agreeing_stored_snapshot_loads_cleanly(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        eid = entity_id_for_typedef((Namespace("ns"),), "Alias")
        snap = _snap(
            typedefs_qualified={"ns::Alias": "int"},
            typedef_entity_ids={"ns::Alias": eid},
            semantic_ir=_typedef_ir({eid: "int"}),
        )
        loaded = snapshot_from_dict(snapshot_to_dict(snap))
        assert loaded.semantic_ir is not None

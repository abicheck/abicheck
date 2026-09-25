"""Ownership in the graph (evidence-entity-model Phase 3, ADR-075 D5/D7).

``compare.ownership_relations``: ``owned_by``/``in_contract`` projected from
each entity's recorded ``ownership_fact`` onto its invariant-I1 node id,
``provided_by`` over the release model's ``BundleExportIndex``, the one
obligation predicate both ``public_not_exported`` readers share, and the
contract inputs recorded with D7 provenance.
"""

from __future__ import annotations

import random

from hypothesis import given, settings, strategies as st

from abicheck.buildsource.cross_source_checks import (
    CrosscheckConfig,
    _check_public_not_exported,
)
from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.compare.ownership_relations import (
    EDGE_KIND_IN_CONTRACT,
    EDGE_KIND_OWNED_BY,
    EDGE_KIND_PROVIDED_BY,
    OWNERSHIP_RELATION_SPECS,
    contract_relations,
    ownership_relations,
    provider_relations,
)
from abicheck.compatibility_evaluation_frontend import (
    ExplicitCompatibilityInputs,
    FrontEnd,
    ProjectCompatibilityInputs,
    resolve_compatibility_evaluation_config,
)
from abicheck.contract_relevance_types import SelectorLayer
from abicheck.model import AbiSnapshot, Function, RecordType, Variable
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.extraction_scope import EntityOwnership, ExtractionScope
from abicheck.model.fact import Fact
from abicheck.model.graph_entity_identity import snapshot_identities
from abicheck.model.graph_evidence_class import EdgeEvidenceClass
from abicheck.model.graph_join import binary_symbol_node_id
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules
from abicheck.model.vocabulary import ScopeOrigin

_OWNERS = {
    "target": ("target", "public"),
    "private": ("target", "private"),
    "dep": ("dependency:other", "external"),
    "tool": ("toolchain", "external"),
    "unresolved": ("unresolved", "unresolved"),
}


def _fn(name: str, owner: str | None) -> Function:
    fn = Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        source_location="/p/inc/a.h:1",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    if owner is not None:
        o, c = _OWNERS[owner]
        fn.ownership_fact = Fact.present(EntityOwnership(o, c, "r"))
    return fn


def _snap(fns: list[Function], *, exports: tuple[str, ...] = ()) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="lib",
        version="1",
        functions=fns,
        from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name=e) for e in exports]),
    )
    snap.extraction_scope = ExtractionScope(OwnershipRules(target_roots=("inc",)))
    return snap


class TestRelationSpecs:
    def test_each_relation_declares_its_evidence_class(self) -> None:
        assert OWNERSHIP_RELATION_SPECS[EDGE_KIND_OWNED_BY].evidence_class is EdgeEvidenceClass.DERIVED
        assert OWNERSHIP_RELATION_SPECS[EDGE_KIND_IN_CONTRACT].evidence_class is EdgeEvidenceClass.DERIVED
        assert (
            OWNERSHIP_RELATION_SPECS[EDGE_KIND_PROVIDED_BY].evidence_class
            is EdgeEvidenceClass.RESOLVED_JOIN
        )
        for spec in OWNERSHIP_RELATION_SPECS.values():
            assert spec.producer and spec.inputs and "never persisted" in spec.recompute_rule


class TestOwnershipRelations:
    def test_edges_are_keyed_on_the_i1_node_ids(self) -> None:
        snap = _snap([_fn("a", "target"), _fn("b", "dep")])
        ids = snapshot_identities(snap)
        edges = set(ownership_relations(snap).edges())
        assert edges == {
            (ids.functions[0].node_id, "owner://target", EDGE_KIND_OWNED_BY),
            (ids.functions[0].node_id, "contract://public", EDGE_KIND_IN_CONTRACT),
            (ids.functions[1].node_id, "owner://dependency:other", EDGE_KIND_OWNED_BY),
            (ids.functions[1].node_id, "contract://external", EDGE_KIND_IN_CONTRACT),
        }

    def test_unclassified_entity_gets_no_edge_and_answers_unknown(self) -> None:
        snap = _snap([_fn("a", None)])
        rel = ownership_relations(snap)
        assert list(rel.edges()) == []
        node = snapshot_identities(snap).functions[0].node_id
        assert rel.owner(node) is None and rel.contract(node) is None
        assert not rel.function_owes_no_export(0)

    def test_two_entities_disagreeing_on_one_node_answer_unknown(self) -> None:
        # Same linker name, different recorded decisions: neither is picked.
        a, b = _fn("a", "target"), _fn("a", "dep")
        snap = _snap([a, b])
        rel = ownership_relations(snap)
        node = snapshot_identities(snap).functions[0].node_id
        assert rel.contract(node) is None

    def test_types_are_related_too(self) -> None:
        rec = RecordType(name="ns::S", kind="struct")
        rec.ownership_fact = Fact.present(EntityOwnership("toolchain", "external", "system_path"))
        snap = _snap([])
        snap.types.append(rec)
        rel = ownership_relations(snap)
        assert rel.owner(snapshot_identities(snap).records[0].node_id) == "toolchain"

    def test_unrecorded_snapshot_takes_the_cheap_all_unknown_path(self) -> None:
        snap = _snap([_fn("a", "dep")])
        snap.extraction_scope = None
        rel = contract_relations(snap)
        assert rel.identities is None
        assert not rel.function_owes_no_export(0)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from([*_OWNERS, None]), min_size=1, max_size=10), st.integers(0, 999))
def test_relations_do_not_depend_on_declaration_order(owners, seed) -> None:
    fns = [_fn(f"f{i}", o) for i, o in enumerate(owners)]
    shuffled = list(fns)
    random.Random(seed).shuffle(shuffled)
    assert set(ownership_relations(_snap(fns)).edges()) == set(
        ownership_relations(_snap(shuffled)).edges()
    )


class TestObligationPredicate:
    """``public_not_exported`` asks the contract relation, and only
    ``private``/``external`` lift an obligation."""

    def _reported(self, owner: str | None) -> set[str]:
        snap = _snap([_fn("present", "target"), _fn("missing", owner)], exports=("_Z7presentv",))
        out = _check_public_not_exported(snap, CrosscheckConfig())  # type: ignore[arg-type]
        return {c.symbol for c in out.findings}

    def test_target_public_declaration_still_owes_its_export(self) -> None:
        assert self._reported("target") == {"_Z7missingv"}

    def test_unresolved_and_unknown_fall_back_to_the_public_header_rule(self) -> None:
        assert self._reported("unresolved") == {"_Z7missingv"}
        assert self._reported(None) == {"_Z7missingv"}

    def test_external_and_private_owe_nothing(self) -> None:
        for owner in ("dep", "tool", "private"):
            assert self._reported(owner) == set(), owner


class TestProviderRelations:
    def test_provided_by_is_the_release_index_on_phase2_export_ids(self) -> None:
        a = _snap([], exports=("x", "shared"))
        b = _snap([], exports=("y", "shared"))
        index = build_bundle_export_index("new", {"liba.so": a, "libb.so": b})
        rel = provider_relations(index)
        assert index.platform == "elf"
        edges = set(rel.edges())
        assert (binary_symbol_node_id("elf", "shared"), "release_member://liba.so", EDGE_KIND_PROVIDED_BY) in edges
        assert (binary_symbol_node_id("elf", "shared"), "release_member://libb.so", EDGE_KIND_PROVIDED_BY) in edges
        assert rel.providers("x") == ("liba.so",)
        assert rel.complete

    def test_a_member_with_no_table_makes_it_incomplete(self) -> None:
        bare = AbiSnapshot(library="c", version="1")
        index = build_bundle_export_index("new", {"libc.so": bare})
        assert not provider_relations(index).complete


class TestContractInputsProvenance:
    """ADR-075 D7: the contract's inputs are recorded fields with a layer."""

    def test_nothing_stated_leaves_the_field_unset(self) -> None:
        cfg = resolve_compatibility_evaluation_config()
        assert cfg.surface.ownership is None
        assert cfg.provenance["surface.ownership.dependencies"].layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_project_rules_are_recorded_as_project_config(self) -> None:
        project = ProjectCompatibilityInputs(
            path=".abicheck.yml",
            ownership=OwnershipRules(
                target_roots=("include",),
                dependencies=(DependencyRoots("fmt", ("third/fmt",)),),
                private_namespaces=("lib::detail",),
            ),
        )
        cfg = resolve_compatibility_evaluation_config(project=project)
        assert cfg.surface.ownership is not None
        assert cfg.surface.ownership.target_roots == ("include",)
        assert cfg.surface.ownership.dependencies == (DependencyRoots("fmt", ("third/fmt",)),)
        for key in ("public_header_dirs", "dependencies", "private_namespaces"):
            assert cfg.provenance[f"surface.ownership.{key}"].layer is SelectorLayer.PROJECT_CONFIG

    def test_header_dirs_and_config_roots_are_a_union_with_two_receipts(self, tmp_path) -> None:
        hdir = tmp_path / "inc"
        hdir.mkdir()
        cfg = resolve_compatibility_evaluation_config(
            explicit=ExplicitCompatibilityInputs(header_dirs=(str(hdir),)),
            project=ProjectCompatibilityInputs(ownership=OwnershipRules(target_roots=("pub",))),
        )
        assert cfg.surface.ownership is not None
        assert set(cfg.surface.ownership.target_roots) == {str(hdir), "pub"}
        assert cfg.provenance["surface.ownership.header_dirs"].layer is SelectorLayer.EXPLICIT_CLI
        assert cfg.provenance["surface.ownership.public_header_dirs"].layer is SelectorLayer.PROJECT_CONFIG

    def test_a_typed_request_outranks_the_project(self) -> None:
        cfg = resolve_compatibility_evaluation_config(
            front_end=FrontEnd.API,
            explicit=ExplicitCompatibilityInputs(
                ownership=OwnershipRules(private_namespaces=("api::detail",))
            ),
            project=ProjectCompatibilityInputs(
                ownership=OwnershipRules(private_namespaces=("cfg::detail",))
            ),
        )
        assert cfg.surface.ownership is not None
        assert cfg.surface.ownership.private_namespaces == ("api::detail",)
        assert cfg.provenance["surface.ownership.private_namespaces"].layer is SelectorLayer.API_REQUEST

    def test_the_receipt_round_trips(self) -> None:
        from abicheck.contract_context_io import (
            resolved_config_from_dict,
            resolved_config_to_dict,
        )

        cfg = resolve_compatibility_evaluation_config(
            project=ProjectCompatibilityInputs(
                ownership=OwnershipRules(
                    target_roots=("include",),
                    dependencies=(DependencyRoots("fmt", ("third",)),),
                )
            )
        )
        back = resolved_config_from_dict(resolved_config_to_dict(cfg))
        assert back.surface.ownership == cfg.surface.ownership

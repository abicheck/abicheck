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

"""ADR-049 Phase 3: the observed provider ledger and its raw type graph."""

from __future__ import annotations

import pytest

from abicheck.contract_evidence_collect import (
    EXPLICIT_SCOPE_EVIDENCE_REF,
    PROVIDER_EXPORT_TABLE,
    PROVIDER_FORCED_PUBLIC,
    PROVIDER_POST_MANIFEST,
    PROVIDER_PUBLIC_HEADER,
    build_type_graph,
    closure_from_graph,
    collect_contract_evidence,
    evidence_record_id,
    evidence_refs_for_reason,
    resolve_graph_node,
    validate_decision_evidence,
)
from abicheck.contract_relevance_types import (
    ContractMode,
    EvidenceCompleteness,
    EvidenceProviderStatus,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.surface import compute_public_surface


def _snap(
    version: str = "1",
    *,
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    typedefs: dict[str, str] | None = None,
    elf: ElfMetadata | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        typedefs=typedefs or {},
        elf=elf,
    )


def _public_fn(
    name: str, *, ret: str = "void", params: list[str] | None = None
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params or [])],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


class TestTypeGraph:
    def test_declaration_reaches_its_signature_types(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", params=["Widget *"])],
            types=[RecordType(name="Widget", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert "decl:api" in graph.nodes
        assert "record:Widget" in graph.nodes
        assert ("decl:api", "record:Widget") in graph.edges

    def test_field_and_base_edges_are_followed_transitively(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Outer *")],
            types=[
                RecordType(
                    name="Outer",
                    kind="struct",
                    fields=[TypeField(name="inner", type="Inner")],
                ),
                RecordType(
                    name="Inner",
                    kind="struct",
                    bases=["Root"],
                    fields=[],
                ),
                RecordType(name="Root", kind="struct", fields=[]),
            ],
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api"])
        assert {"record:Outer", "record:Inner", "record:Root"} <= closure

    def test_typedef_target_is_an_edge(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Alias")],
            types=[RecordType(name="Real", kind="struct", fields=[])],
            typedefs={"Alias": "Real"},
        )
        closure = closure_from_graph(build_type_graph(snap), ["decl:api"])
        assert "typedef:Alias" in closure
        assert "record:Real" in closure

    def test_unreferenced_type_is_outside_the_closure(self) -> None:
        snap = _snap(
            functions=[_public_fn("api", ret="Kept *")],
            types=[
                RecordType(name="Kept", kind="struct", fields=[]),
                RecordType(name="Orphan", kind="struct", fields=[]),
            ],
        )
        graph = build_type_graph(snap)
        closure = closure_from_graph(graph, ["decl:api"])
        assert "record:Orphan" in graph.nodes  # observed...
        assert "record:Orphan" not in closure  # ...but not reachable

    def test_graph_is_policy_independent(self) -> None:
        """A private declaration is still observed -- the graph is raw facts.

        This is what makes one collected block valid input to a later
        re-evaluation under a different contract mode (plan Section 5.1).
        """
        snap = _snap(
            functions=[
                Function(
                    name="internal",
                    mangled="internal",
                    return_type="Secret *",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                )
            ],
            types=[RecordType(name="Secret", kind="struct", fields=[])],
        )
        graph = build_type_graph(snap)
        assert ("decl:internal", "record:Secret") in graph.edges

    def test_graph_is_order_independent(self) -> None:
        types = [
            RecordType(name="A", kind="struct", fields=[TypeField(name="b", type="B")]),
            RecordType(name="B", kind="struct", fields=[]),
        ]
        forward = build_type_graph(_snap(types=types))
        reversed_ = build_type_graph(_snap(types=list(reversed(types))))
        assert forward == reversed_

    def test_alias_spellings_resolve_to_the_canonical_node(self) -> None:
        snap = _snap(functions=[_public_fn("ns::api")])
        graph = build_type_graph(snap)
        assert resolve_graph_node(graph, "ns::api") == {"decl:ns::api"}
        # The bare tail is an alias of the same declaration.
        assert resolve_graph_node(graph, "api") == {"decl:ns::api"}

    def test_unknown_spelling_resolves_to_nothing(self) -> None:
        graph = build_type_graph(_snap(functions=[_public_fn("api")]))
        assert resolve_graph_node(graph, "nope") == set()


class TestProviderLedger:
    def test_header_provider_records_public_roots(self) -> None:
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        entry = next(
            e
            for e in block.providers
            if e.record.provider == PROVIDER_PUBLIC_HEADER and e.record.side == "old"
        )
        assert entry.declarations == ("decl:api",)
        assert entry.record.status is EvidenceProviderStatus.AVAILABLE
        assert entry.record.id == evidence_record_id(PROVIDER_PUBLIC_HEADER, "old")

    def test_unresolvable_header_surface_is_unavailable_and_not_started(self) -> None:
        """A snapshot with no public declarations resolves no surface.

        ``searched_scope`` must then differ from ``requested_scope`` -- plan
        Section 4.2's "requested scope equals searched scope" clause is only
        checkable if an incomplete search actually records a smaller one.
        """
        snap = _snap()
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        entry = next(e for e in block.providers if e.record.side == "old")
        assert entry.record.status is EvidenceProviderStatus.UNAVAILABLE
        assert entry.record.completeness is EvidenceCompleteness.NOT_STARTED
        assert entry.record.reason_code == "header_surface_unresolvable"
        assert entry.record.searched_scope == ()
        assert entry.record.requested_scope != entry.record.searched_scope

    def test_export_provider_absent_when_not_consulted(self) -> None:
        """ "Not consulted" is a different fact from "consulted and failed"."""
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap, snap, compute_public_surface(snap), compute_public_surface(snap)
        )
        assert not [
            e for e in block.providers if e.record.provider == PROVIDER_EXPORT_TABLE
        ]

    def test_export_provider_records_observed_roots(self) -> None:
        elf = ElfMetadata(symbols=[ElfSymbol(name="api")])
        snap = _snap(functions=[_public_fn("api", params=["int"])], elf=elf)
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            exports_old=exports,
            exports_new=exports,
        )
        entry = next(
            e
            for e in block.providers
            if e.record.provider == PROVIDER_EXPORT_TABLE and e.record.side == "new"
        )
        assert entry.declarations == ("decl:api",)
        assert entry.manifests == ("elf",)
        assert entry.record.domain_kind == "exports"

    def test_export_provider_reports_which_guard_failed(self) -> None:
        """An export no declaration accounts for is reported by name.

        ``ExportSurface.exclusion_is_provable`` folds five conditions into one
        boolean; the ledger exists to say which of them was not met.
        """
        elf = ElfMetadata(
            symbols=[
                ElfSymbol(name="api"),
                ElfSymbol(name="mystery"),
            ]
        )
        snap = _snap(functions=[_public_fn("api", params=["int"])], elf=elf)
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            exports_old=exports,
            exports_new=exports,
        )
        entry = next(
            e for e in block.providers if e.record.provider == PROVIDER_EXPORT_TABLE
        )
        assert entry.record.completeness is EvidenceCompleteness.PARTIAL
        assert entry.record.reason_code == "unmatched_exports"

    def test_overlays_are_recorded_for_both_sides(self) -> None:
        snap = _snap(functions=[_public_fn("api")])
        block = collect_contract_evidence(
            snap,
            snap,
            compute_public_surface(snap),
            compute_public_surface(snap),
            public_surface_allowlist=["api"],
            force_public_symbols=["other"],
        )
        for provider in (PROVIDER_POST_MANIFEST, PROVIDER_FORCED_PUBLIC):
            sides = {
                e.record.side for e in block.providers if e.record.provider == provider
            }
            assert sides == {"old", "new"}

    def test_collection_is_order_independent(self) -> None:
        snap_a = _snap(functions=[_public_fn("a"), _public_fn("b")])
        snap_b = _snap(functions=[_public_fn("b"), _public_fn("a")])
        block_a = collect_contract_evidence(
            snap_a,
            snap_a,
            compute_public_surface(snap_a),
            compute_public_surface(snap_a),
        )
        block_b = collect_contract_evidence(
            snap_b,
            snap_b,
            compute_public_surface(snap_b),
            compute_public_surface(snap_b),
        )
        assert block_a == block_b

    def test_input_identity_changes_with_the_observation(self) -> None:
        one = _snap(functions=[_public_fn("api")])
        two = _snap(functions=[_public_fn("api"), _public_fn("extra")])
        block_one = collect_contract_evidence(
            one, one, compute_public_surface(one), compute_public_surface(one)
        )
        block_two = collect_contract_evidence(
            two, two, compute_public_surface(two), compute_public_surface(two)
        )
        digests = [
            next(e for e in b.providers if e.record.side == "old").record.input_identity
            for b in (block_one, block_two)
        ]
        assert digests[0] is not None and digests[1] is not None
        assert digests[0].sha256 != digests[1].sha256


class TestEvidenceReferences:
    def _block(self, *, exports: bool = False):
        snap = _snap(
            functions=[_public_fn("api")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        surf = compute_public_surface(snap)
        export_surface = compute_export_surface(snap) if exports else None
        return collect_contract_evidence(
            snap,
            snap,
            surf,
            surf,
            exports_old=export_surface,
            exports_new=export_surface,
        )

    def test_non_entity_finding_cites_no_provider(self) -> None:
        refs = evidence_refs_for_reason(
            "non_entity_finding",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == ()

    def test_public_membership_cites_both_sides(self) -> None:
        """The one genuinely two-sided decision: ``classify_change_surface``
        runs against ``surface_unions(surf_old, surf_new)``."""
        refs = evidence_refs_for_reason(
            "public_root_membership",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == ("public_header:old", "public_header:new")

    def test_one_sided_reason_cites_only_the_authoritative_side(self) -> None:
        refs = evidence_refs_for_reason(
            "required_evidence_incomplete",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="new",
        )
        assert refs == ("public_header:new",)

    def test_exports_domain_cites_only_the_authoritative_side(self) -> None:
        """``_exports_mode_decision`` reads one surface, so it cites one.

        Citing the other side would claim the decision rests on evidence it
        never read -- actively misleading when that side's provider is
        unavailable (an exported removal is conclusive from the old table
        even if the new one failed to parse; Codex review, fresh evidence).
        """
        refs = evidence_refs_for_reason(
            "export_root_membership",
            mode=ContractMode.EXPORTS,
            block=self._block(exports=True),
            authoritative_side="old",
        )
        assert refs == ("export_table:old",)

    def test_reference_is_dropped_when_the_record_is_absent(self) -> None:
        """A reference must never dangle -- see ``validate_decision_evidence``."""
        refs = evidence_refs_for_reason(
            "export_root_membership",
            mode=ContractMode.EXPORTS,
            block=self._block(exports=False),
            authoritative_side="old",
        )
        assert refs == ()

    def test_explicit_scope_cites_the_run_level_reference(self) -> None:
        refs = evidence_refs_for_reason(
            "explicit_consumer_or_required_symbol_evidence",
            mode=ContractMode.PUBLIC,
            block=self._block(),
            authoritative_side="old",
        )
        assert refs == (EXPLICIT_SCOPE_EVIDENCE_REF,)

    def test_validate_accepts_run_level_and_known_records(self) -> None:
        block = self._block()
        validate_decision_evidence(
            [EXPLICIT_SCOPE_EVIDENCE_REF, "public_header:old"], block
        )

    def test_validate_rejects_a_dangling_reference(self) -> None:
        with pytest.raises(ValueError, match="no known provider record"):
            validate_decision_evidence(["export_table:old"], self._block())


class TestOverlayAttribution:
    """A decision an explicit overlay produced must cite *that* overlay.

    ``--public-symbol`` and ``--post-manifest`` short-circuit the evaluator
    before it ever looks at the header surface, but both carry the same
    reason code a genuine header-derived root membership does -- so a
    reason-code-only mapping would cite the header provider for evidence
    that never decided anything here. ``evidence_refs_for_change`` is the
    wrapper that distinguishes them (it lives in ``contract_evaluation``
    so the matching mirrors that module's own overlay rules).
    """

    def _pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = _snap(
            functions=[
                _public_fn("pub"),
                Function(
                    name="priv",
                    mangled="priv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ]
        )
        new = _snap("2")
        return old, new

    def _refs(self, **compare_kwargs) -> dict[str, tuple[str, ...]]:
        from abicheck.checker import compare

        old, new = self._pair()
        result = compare(old, new, contract_evaluation=True, **compare_kwargs)
        return {
            c.symbol: tuple(c.contract_evidence_refs or ())
            for c in result.changes + result.out_of_surface_changes
        }

    def test_forced_public_symbol_cites_its_own_overlay(self) -> None:
        refs = self._refs(force_public_symbols={"priv"})
        assert refs["priv"] == ("forced_public_symbols:old",)
        # An ordinary header-derived membership is unaffected.
        assert refs["pub"] == ("public_header:old", "public_header:new")

    def test_post_manifest_cites_the_manifest_in_both_directions(self) -> None:
        """A commitment *and* an exclusion both rest on the manifest."""
        refs = self._refs(public_surface_allowlist={"pub"})
        assert refs["pub"] == ("post_manifest:old",)
        assert refs["priv"] == ("post_manifest:old",)

    def test_private_header_declaration_is_still_a_persisted_root(self) -> None:
        """Root selection is by *visibility*, and the receipt says so.

        ``_public_header_declarations`` seeds from ``Visibility.PUBLIC``
        exactly as ``surface._seed_public_roots`` does, so a public-visibility
        declaration whose header origin is private is recorded as a root here
        even though header-origin scoping would demote a finding about it.
        That is the intended policy-independence of the evidence block, not
        an oversight -- pinning it means a later widening (or narrowing) of
        the root rule fails this test instead of silently changing what a
        replay computes (CodeRabbit review).
        """
        from abicheck.contract_context import build_persisted_context
        from abicheck.contract_relevance_types import ContractMode

        old, _new = self._pair()
        surf = compute_public_surface(old)
        receipt = build_persisted_context(
            collect_contract_evidence(old, old, surf, surf),
            mode=ContractMode.PUBLIC,
        ).decision_receipt
        assert set(receipt.evaluated_contract_roots) == {"decl:pub", "decl:priv"}

    def test_no_overlay_configured_cites_the_domain_provider(self) -> None:
        refs = self._refs()
        assert all(
            r and all(ref.startswith("public_header:") for ref in r)
            for r in refs.values()
        )

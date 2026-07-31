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

"""ADR-049 ``contract=exports``: the shadow evaluator's third mode.

Split out of ``test_contract_evaluation.py`` (which sits at the AI-readiness
file-size hard cap -- see ``AGENTS.md`` for the canonical limit) the same way
``test_contract_evaluation_not_applicable.py`` already is -- one sibling file
per mode/concern, not one growing module.
``test_export_surface.py`` covers the evidence *provider* in isolation; this
file covers the *decisions* the evaluator makes from it.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import (
    ContractEvaluationDecision,
    evaluate_change_contract_relevance,
    evaluate_snapshot_pair_contract_relevance,
)
from abicheck.contract_relevance_types import (
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.surface import REASON_PRIVATE_HEADER, PublicSurface


def _fn(
    name,
    ret="void",
    params=(),
    vis=Visibility.PUBLIC,
    origin=ScopeOrigin.UNKNOWN,
    mangled=None,
):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, size=64, origin=ScopeOrigin.UNKNOWN):
    return RecordType(name=name, kind="struct", size_bits=size, origin=origin)


_UNRESOLVABLE = PublicSurface()  # resolvable defaults to False


class TestExportsMode:
    """ADR-049 plan Section 7's ``exports`` row.

    Its domain is only exported function/variable roots and the closure
    computed from the raw type graph -- so these tests deliberately pin the
    *differences* from ``PUBLIC`` (header origin is advisory here; the export
    table is authoritative), not a re-verification of the shared
    identity/candidate machinery ``TestPublicMode`` already covers.
    """

    @staticmethod
    def _snap(**kw):
        return AbiSnapshot(library="libfoo.so", version="1", **kw)

    @staticmethod
    def _exports(snap):
        return compute_export_surface(snap)

    def _evaluate(self, change, exp_old, exp_new=None, **kw):
        return evaluate_change_contract_relevance(
            change,
            _UNRESOLVABLE,
            _UNRESOLVABLE,
            mode=ContractMode.EXPORTS,
            exports_old=exp_old,
            exports_new=exp_new if exp_new is not None else exp_old,
            **kw,
        )

    def test_export_root_is_in_contract(self) -> None:
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3apiv", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="export_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_type_inside_the_export_closure_is_in_contract(self) -> None:
        snap = self._snap(
            functions=[_fn("api", params=("Cfg *",), mangled="_Z3apiP3Cfg")],
            types=[_rec("Cfg")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiP3Cfg")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Cfg", description="")
        decision = self._evaluate(c, self._exports(snap))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

    def test_known_but_unexported_symbol_is_proven_out_of_contract(self) -> None:
        snap = self._snap(
            functions=[
                _fn("api", mangled="_Z3apiv"),
                _fn("helper", mangled="_Z6helperv"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z6helperv", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_known_type_outside_the_closure_is_proven_out_of_contract(self) -> None:
        snap = self._snap(
            functions=[_fn("api", params=("Cfg *",), mangled="_Z3apiP3Cfg")],
            types=[_rec("Cfg"), _rec("Internal")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiP3Cfg")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Internal", description="")
        decision = self._evaluate(c, self._exports(snap))
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT

    def test_unknown_entity_is_unresolved_not_proven_out(self) -> None:
        # A macro has no linker symbol and no type-universe entry, so it
        # cannot be placed in this domain at all -- "incomplete root/graph
        # evidence", never proof of exclusion.
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(
            kind=ChangeKind.PUBLIC_MACRO_REMOVED, symbol="MAX_LEN", description=""
        )
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_no_export_table_is_unresolved_with_unavailable_assurance(self) -> None:
        snap = self._snap(functions=[_fn("api", mangled="_Z3apiv")])
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3apiv", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.UNAVAILABLE,
        )

    def test_untyped_roots_cannot_prove_a_known_type_out(self) -> None:
        # An export-table-only dump ("?" return type, no params) gives the
        # closure no seeds, so an absence from it is not evidence (ADR-024
        # D5.2's rule applied to this domain).
        snap = self._snap(
            functions=[_fn("opaque", ret="?", mangled="opaque")],
            types=[_rec("Cfg")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="opaque")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Cfg", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.UNKNOWN_UNRESOLVED
        )

    def test_private_header_origin_does_not_demote_a_real_export(self) -> None:
        # The defining difference from PUBLIC mode: header-origin evidence is
        # "unrelated and advisory" here, so an earlier pipeline step's own
        # private-header exclusion reason must not close this domain's
        # question -- the export table already answered it.
        snap = self._snap(
            functions=[
                _fn("api", mangled="_Z3apiv", origin=ScopeOrigin.PRIVATE_HEADER)
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3apiv",
            description="",
            surface_exclusion_reason=REASON_PRIVATE_HEADER,
        )
        decision = self._evaluate(c, self._exports(snap))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

    def test_manifest_omission_does_not_exclude_a_real_export(self) -> None:
        # Section 7's `exports` row is unconditional in both directions:
        # "Roots/closure are IN_CONTRACT ... Public-header/manifest/consumer
        # failures are unrelated and advisory". A symbol the binary
        # demonstrably exports therefore stays in this contract even when a
        # POST manifest omits it -- so this mode dispatches ahead of the
        # manifest-exclusion shortcut every other mode honors (Codex review).
        import abicheck.contract_evaluation as mod

        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3apiv",
            description="",
            surface_exclusion_reason=mod._REASON_POST_MANIFEST_NOT_COMMITTED,
        )
        decision = self._evaluate(c, self._exports(snap))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

    @pytest.mark.parametrize("mode", [ContractMode.PUBLIC, ContractMode.ALL])
    def test_manifest_exclusion_is_still_terminal_under_other_modes(
        self, mode: ContractMode
    ) -> None:
        # The mode dispatch moved, so pin that the *other* modes' behaviour is
        # unchanged: the manifest exclusion is still authoritative there.
        import abicheck.contract_evaluation as mod

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3apiv",
            description="",
            surface_exclusion_reason=mod._REASON_POST_MANIFEST_NOT_COMMITTED,
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=mode
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"

    def test_not_applicable_kind_still_wins_under_exports(self) -> None:
        snap = self._snap(elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]))
        c = Change(kind=ChangeKind.PIE_DISABLED, symbol="", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.NOT_APPLICABLE
        )

    @pytest.mark.parametrize(
        "overlay", ["force_public_symbols", "public_surface_allowlist"]
    )
    def test_declared_public_overlays_cannot_widen_the_export_domain(
        self, overlay: str
    ) -> None:
        # `--public-symbol` and `--post-manifest` both assert something about
        # the *declared-public* surface; neither observes an export, and a
        # user assertion cannot make an unexported declaration exported. This
        # domain is defined as "only exported roots and their closure", so an
        # overlay-named-but-unexported declaration stays out of it (Codex
        # review) -- unlike the `public` path, where these overlays *are* the
        # domain's own evidence.
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv"), _fn("declared", mangled="d")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="d", description="")
        decision = self._evaluate(c, self._exports(snap), **{overlay: frozenset({"d"})})
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT

    def test_an_overlay_named_symbol_that_is_exported_is_still_a_root(self) -> None:
        # The overlays are ignored, not inverted: membership still comes from
        # the export table, so an overlay-named declaration that *is* exported
        # resolves through the ordinary root path.
        snap = self._snap(
            functions=[_fn("declared", mangled="d")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="d")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="d", description="")
        decision = self._evaluate(
            c, self._exports(snap), public_surface_allowlist=frozenset({"d"})
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

    @pytest.mark.parametrize(
        ("symbol", "kind"),
        [
            ("Maybe", ChangeKind.TYPE_SIZE_CHANGED),
            ("unexported_helper", ChangeKind.FUNC_REMOVED),
        ],
    )
    def test_an_unmatched_export_blocks_every_exclusion(
        self, symbol: str, kind: ChangeKind
    ) -> None:
        # An export no declaration accounted for is a real entry point whose
        # own signature -- and type closure -- this snapshot knows nothing
        # about, so an absence from the root/closure sets could just mean the
        # missing export is what reaches the entity (Codex review).
        snap = self._snap(
            functions=[
                _fn("known", mangled="known", ret="Known *"),
                _fn("unexported_helper", mangled="unexported_helper"),
            ],
            types=[_rec("Known"), _rec("Maybe")],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="known"), ElfSymbol(name="mystery")]
            ),
        )
        c = Change(kind=kind, symbol=symbol, description="")
        assert self._evaluate(c, self._exports(snap)).relevance is not (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )

    def test_linker_artifacts_do_not_count_as_unmatched_exports(self) -> None:
        # Otherwise every real ELF binary (which always exports _init/_fini/
        # thunks) would permanently block exclusion.
        snap = self._snap(
            functions=[_fn("known", mangled="known", ret="Known *")],
            types=[_rec("Known"), _rec("Maybe")],
            elf=ElfMetadata(
                symbols=[
                    ElfSymbol(name="known"),
                    ElfSymbol(name="_init"),
                    ElfSymbol(name="_fini"),
                ]
            ),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Maybe", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )

    def test_addition_uses_new_side_evidence(self) -> None:
        # ADR-049 D4: an addition is judged by the new side, which is the
        # only side where a newly-exported symbol can exist at all.
        old = self._snap(elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3oldv")]))
        new = self._snap(
            functions=[_fn("added", mangled="_Z5addedv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z5addedv")]),
        )
        c = Change(kind=ChangeKind.FUNC_ADDED, symbol="_Z5addedv", description="")
        decision = self._evaluate(c, self._exports(old), self._exports(new))
        assert decision.relevance is ContractRelevance.IN_CONTRACT

    def test_removal_uses_old_side_evidence(self) -> None:
        # The mirror image: new-side export evidence cannot manufacture
        # confidence about an old obligation, and vice versa -- the removed
        # symbol only exists on the old side.
        old = self._snap(
            functions=[_fn("gone", mangled="_Z4gonev")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z4gonev")]),
        )
        new = self._snap(elf=ElfMetadata(symbols=[ElfSymbol(name="_Z5otherv")]))
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z4gonev", description="")
        decision = self._evaluate(c, self._exports(old), self._exports(new))
        assert decision.relevance is ContractRelevance.IN_CONTRACT

    def test_ambiguous_closure_match_is_identity_ambiguous(self) -> None:
        # Two records sharing a bare name both land in the closure (the
        # anti-hiding rule); which one the finding is about is exactly what
        # decides membership, so neither answer is provable -- and in
        # particular it must not fall through to PROVEN_OUT_OF_CONTRACT just
        # because no *unambiguous* match confirmed.
        snap = self._snap(
            functions=[_fn("api", params=("Point *",), mangled="_Z3api5Point")],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    qualified_name="two::Point",
                ),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3api5Point")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Point", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="identity_ambiguous",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_reduced_tier_identity_downgrades_under_exports_too(self) -> None:
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="", description="")
        assert (
            self._evaluate(c, self._exports(snap)).reason_code == "identity_ambiguous"
        )

    def test_batch_helper_threads_the_export_surfaces(self) -> None:
        snap = self._snap(
            functions=[
                _fn("api", mangled="_Z3apiv"),
                _fn("helper", mangled="_Z6helperv"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        exp = self._exports(snap)
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3apiv", description=""),
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z6helperv", description=""),
        ]
        decisions = evaluate_snapshot_pair_contract_relevance(
            changes,
            _UNRESOLVABLE,
            _UNRESOLVABLE,
            mode=ContractMode.EXPORTS,
            exports_old=exp,
            exports_new=exp,
        )
        assert [d.relevance for d in decisions] == [
            ContractRelevance.IN_CONTRACT,
            ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        ]

    def test_partial_typed_root_coverage_cannot_prove_a_type_out(self) -> None:
        # Regression (Codex review): with one typed root and one untyped one,
        # `has_typed_roots` is true but the untyped root's own closure is
        # unknown and could contain this very type -- a partial closure
        # proves unreachability from the roots it covers, not from the ones
        # it doesn't.
        snap = self._snap(
            functions=[
                _fn("typed", ret="Known *", mangled="typed"),
                _fn("opaque", ret="?", mangled="opaque"),
            ],
            types=[_rec("Known"), _rec("Maybe")],
            elf=ElfMetadata(
                symbols=[ElfSymbol(name="typed"), ElfSymbol(name="opaque")]
            ),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Maybe", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_fully_typed_roots_still_prove_a_type_out(self) -> None:
        # The mirror image: complete root coverage keeps the exclusion.
        snap = self._snap(
            functions=[_fn("typed", ret="Known *", mangled="typed")],
            types=[_rec("Known"), _rec("Maybe")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="typed")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Maybe", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )

    def test_observed_table_with_no_resolved_roots_proves_nothing(self) -> None:
        # A real export table whose names no declaration matched (a
        # mangling-scheme gap): the exports exist, so an absence from the
        # empty root/closure sets is not evidence about any entity.
        snap = self._snap(
            functions=[_fn("local", mangled="_Z5localv")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="?unmatched@@YAXXZ")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z5localv", description="")
        assert self._evaluate(c, self._exports(snap)) == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_a_symbol_level_finding_does_not_ride_the_type_closure(self) -> None:
        # An unexported helper whose `caused_by_type` some *other* exported
        # signature reaches must not become IN_CONTRACT on that basis --
        # closure membership answers a type-level question, and gating an
        # internal function merely for using a public type is exactly the
        # over-inclusion this domain exists to avoid (Codex review).
        snap = self._snap(
            functions=[
                _fn("api", ret="Foo *", mangled="api"),
                _fn("internal", mangled="internal"),
            ],
            types=[_rec("Foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="internal",
            description="",
            caused_by_type="Foo",
        )
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )

    def test_a_type_level_finding_still_uses_the_closure(self) -> None:
        snap = self._snap(
            functions=[_fn("api", ret="Foo *", mangled="api")],
            types=[_rec("Foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Foo", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.IN_CONTRACT
        )

    @pytest.mark.parametrize(
        ("kind", "symbol"),
        [
            # `diff_types`' field family: `symbol` is the owning type alone.
            (ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "Foo"),
            # `diff_platform`'s struct family: `symbol` is owner + member.
            (ChangeKind.STRUCT_FIELD_OFFSET_CHANGED, "Foo::x"),
        ],
    )
    def test_a_member_level_finding_still_uses_its_owner_type(
        self, kind: ChangeKind, symbol: str
    ) -> None:
        snap = self._snap(
            functions=[_fn("api", ret="Foo *", mangled="api")],
            types=[_rec("Foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        c = Change(kind=kind, symbol=symbol, description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.IN_CONTRACT
        )

    def test_a_nested_type_does_not_ride_its_parents_membership(self) -> None:
        # Offering both owner readings would confirm a finding about the
        # nested `Outer::Helper` on `Outer`'s membership (Codex review).
        snap = self._snap(
            functions=[_fn("api", ret="Outer *", mangled="api")],
            types=[_rec("Outer"), _rec("Outer::Helper")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        c = Change(
            kind=ChangeKind.TYPE_FIELD_REMOVED, symbol="Outer::Helper", description=""
        )
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )

    def test_a_qualified_finding_does_not_match_a_bare_export_root_tail(self) -> None:
        # The binary exports a real C `foo`, so "foo" is legitimately in
        # `export_symbols`; deriving the tail of an unrelated qualified
        # finding must not match it (Codex review).
        snap = self._snap(
            functions=[
                _fn("foo", mangled="foo"),
                _fn("ns::foo", mangled="_ZN2ns3fooEv"),
            ],
            elf=ElfMetadata(symbols=[ElfSymbol(name="foo")]),
        )
        exp = self._exports(snap)
        qualified = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="ns::foo", description=""
        )
        assert self._evaluate(qualified, exp).relevance is (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        )
        # ... while the real C export still resolves exactly.
        exact = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="")
        assert self._evaluate(exact, exp).relevance is ContractRelevance.IN_CONTRACT

    @pytest.mark.parametrize(
        ("kind", "symbol"),
        [
            # `diff_types` records the owning *type* alone for this family,
            # with the field name in `detail` -- stripping the last component
            # would yield the namespace fragment "ns", losing the owner.
            (ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "ns::Foo"),
            (ChangeKind.TYPE_FIELD_TYPE_CHANGED, "ns::Foo"),
            # ... while the enum-member family records owner *and* member, so
            # the owner really is the stripped prefix.
            (ChangeKind.ENUM_MEMBER_VALUE_CHANGED, "ns::Foo::X"),
        ],
    )
    def test_member_level_findings_resolve_their_owner_in_both_shapes(
        self, kind: ChangeKind, symbol: str
    ) -> None:
        # Regression (Codex review): the two producers in one kind set spell
        # `symbol` differently, so both readings must be offered as candidates.
        snap = self._snap(
            functions=[_fn("api", ret="ns::Foo *", mangled="api")],
            types=[_rec("ns::Foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="api")]),
        )
        c = Change(kind=kind, symbol=symbol, description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.IN_CONTRACT
        )

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            (
                "_ZN3Foo3barEv",
                ContractEvaluationDecision(
                    relevance=ContractRelevance.IN_CONTRACT,
                    reason_code="export_root_membership",
                    assurance=ContractAssurance.COMPLETE,
                ),
            ),
            # The same producer shape on an *unexported* method resolves the
            # other way -- the exception is an exact linker-root match, not a
            # blanket "any mangled symbol counts".
            (
                "_ZN3Foo6secretEv",
                ContractEvaluationDecision(
                    relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
                    reason_code="terminal_authoritative_exclusion",
                    assurance=ContractAssurance.COMPLETE,
                ),
            ),
        ],
    )
    def test_vtable_slot_change_resolves_by_exact_linker_root(
        self, symbol: str, expected: ContractEvaluationDecision
    ) -> None:
        # `diff_symbols._check_vtable_index_change` reuses the *type-level*
        # `TYPE_VTABLE_CHANGED` for a moved virtual slot but sets
        # `symbol=mangled` -- the method's own linker name. The blanket
        # type-level symbol rejection left that `UNKNOWN_UNRESOLVED` even
        # though the exact mangled name is an observed root (Codex review).
        snap = self._snap(
            functions=[
                _fn("Foo::bar", mangled="_ZN3Foo3barEv"),
                _fn("Foo::secret", mangled="_ZN3Foo6secretEv"),
            ],
            types=[_rec("Foo")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_ZN3Foo3barEv")]),
        )
        c = Change(kind=ChangeKind.TYPE_VTABLE_CHANGED, symbol=symbol, description="")
        assert self._evaluate(c, self._exports(snap)) == expected

    def test_a_source_spelled_type_level_symbol_still_never_matches(self) -> None:
        # The narrowing must not reopen the collision the rejection exists
        # for: a type "Foo" sharing a bare name with an unrelated exported
        # function "Foo" is not evidence about the type.
        snap = self._snap(
            functions=[_fn("Foo", mangled="Foo")],
            types=[_rec("Unrelated")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="Foo")]),
        )
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Foo", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is not (
            ContractRelevance.IN_CONTRACT
        )

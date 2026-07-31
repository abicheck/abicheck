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
2000-line hard cap) the same way ``test_contract_evaluation_not_applicable.py``
already is -- one sibling file per mode/concern, not one growing module.
``test_export_surface.py`` covers the evidence *provider* in isolation; this
file covers the *decisions* the evaluator makes from it.
"""

from __future__ import annotations

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

    def test_post_manifest_exclusion_stays_terminal_under_exports(self) -> None:
        # The one cross-domain exclusion: an exact committed-*export*
        # manifest is direct evidence for this domain, so it still closes
        # membership even for a symbol present in the export table.
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
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"

    def test_not_applicable_kind_still_wins_under_exports(self) -> None:
        snap = self._snap(elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]))
        c = Change(kind=ChangeKind.PIE_DISABLED, symbol="", description="")
        assert self._evaluate(c, self._exports(snap)).relevance is (
            ContractRelevance.NOT_APPLICABLE
        )

    def test_force_public_symbols_uses_the_explicit_evidence_tier(self) -> None:
        # `--public-symbol` is a user-declared required symbol, not an
        # observed export -- honored (the pipeline keeps such a finding
        # unconditionally) but never labeled as an observed export root.
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv"), _fn("forced", mangled="forced")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="forced", description="")
        decision = self._evaluate(
            c, self._exports(snap), force_public_symbols=frozenset({"forced"})
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "explicit_consumer_or_required_symbol_evidence"

    def test_post_manifest_allowlist_membership_is_an_export_root(self) -> None:
        snap = self._snap(
            functions=[_fn("api", mangled="_Z3apiv"), _fn("committed", mangled="c")],
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3apiv")]),
        )
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="c", description="")
        decision = self._evaluate(
            c, self._exports(snap), public_surface_allowlist=frozenset({"c"})
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

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

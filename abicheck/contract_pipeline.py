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

"""ADR-049 Phase 7: contract relevance as an authoritative pipeline stage.

ADR-049 D9 fixes the order in which a run must decide things::

    detect rich + L0 facts
    → normalize and reconcile canonical finding identity
    → apply explicit consumer/manifest scope
    → classify contract relevance      <- this module
    → apply compatibility policy
    → apply change suppressions
    → compute gate severity
    → aggregate command exit

Until this module existed, relevance was classified *last* -- after
``checker.compare`` had already computed the verdict over the final ``kept``
list -- which is what made the evaluator a shadow: a finding it labelled
``PROVEN_OUT_OF_CONTRACT`` still scored, so a report could state that a
finding is outside the promised contract next to a process that exited ``4``
because of it. Phase 3 built the decision; this stage is what puts it in
front of the policy that consumes it.

The stage is deliberately split into *build once* and *classify many times*,
because "before the verdict" is not one point in ``compare()``:

- the bulk of the findings exist before the first ``_compute_verdict_for``;
- the opt-in ``--surface-metrics`` and ``--pattern-verdicts`` steps append
  more findings and then *recompute* the verdict;
- the audit ledgers (out-of-surface, redundant, suppressed, reconciled) are
  filled at several points, some of them after the verdict.

So the expensive half -- resolving the mode, computing both sides' public
surfaces and export surfaces, and collecting the provider-evidence ledger --
happens once in :func:`build_contract_stage`, and
:meth:`ContractEvaluationStage.classify` is then called at each of those
points. It is idempotent per finding (a finding already stamped is skipped),
which is what lets ``_compute_verdict_for`` call it unconditionally without
the caller having to track which findings are new. Per-finding decisions are
independent by construction
(:func:`~abicheck.contract_evaluation.evaluate_snapshot_pair_contract_relevance`
maps over its input), so classifying in several batches gives the same answer
as classifying in one -- the only thing the batching preserves is *order*,
which the decision receipt is keyed by.

Nothing here decides an exit code. It stamps relevance, the resulting
:class:`~abicheck.contract_relevance_types.CompatibilityEvaluationStatus`,
and (once the policy has run) the per-finding compatibility decision;
``severity.py`` and ``checker.py`` read those through the shared predicate in
:mod:`abicheck.contract_gating`. Not a resolver either: the contract *mode*
arrives already resolved from the front end's
``CompatibilityEvaluationConfig``, and the legacy-alias fallback is Phase 1's
own wiring rather than a second copy of D7's precedence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .contract_relevance_types import ContractMode, evaluation_status_for

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .checker_types import Change
    from .compatibility_evaluation_config import ValueProvenance
    from .contract_evidence import ContractEvidenceBlock, PersistedContractContext
    from .export_surface import ExportSurface
    from .model import AbiSnapshot
    from .policy_file import PolicyFile
    from .post_processing import PipelineContext
    from .severity import KindSets
    from .suppression import SuppressionList
    from .surface import PublicSurface


@dataclass
class ContractEvaluationStage:
    """One comparison's resolved contract-evaluation inputs, plus the running
    ledger of every finding classified against them.

    Built by :func:`build_contract_stage`; never constructed directly by a
    front end. ``changes`` accumulates in classification order and is what
    the decision receipt is built from, so every stamped finding -- kept,
    demoted, redundant, suppressed or reconciled -- appears in the persisted
    context exactly once.
    """

    mode: ContractMode
    mode_provenance: ValueProvenance | None
    surf_old: PublicSurface
    surf_new: PublicSurface
    exports_old: ExportSurface
    exports_new: ExportSurface
    evidence: ContractEvidenceBlock
    forced_public: frozenset[str] | None
    committed_exports: frozenset[str] | None
    changes: list[Change] = field(default_factory=list)
    #: ``id()`` of every already-classified finding. Identity, not equality:
    #: two findings can compare equal (``Change`` is a plain dataclass) while
    #: being distinct facts about distinct entities, and stamping one of them
    #: twice would put a duplicate row in the decision receipt.
    _classified: set[int] = field(default_factory=set, repr=False)

    def classify(self, changes: Iterable[Change]) -> None:
        """Stamp relevance, reason, assurance, evidence refs and evaluation
        status on every not-yet-classified finding in *changes*, in place.

        Idempotent per finding, so a caller may pass a list that mixes
        already-stamped and fresh findings -- which is exactly what
        ``_compute_verdict_for`` does on every recomputation.
        """
        from .contract_evaluation import (
            evaluate_snapshot_pair_contract_relevance,
            evidence_refs_for_change,
        )

        fresh = [c for c in changes if id(c) not in self._classified]
        if not fresh:
            return
        decisions = evaluate_snapshot_pair_contract_relevance(
            fresh,
            self.surf_old,
            self.surf_new,
            mode=self.mode,
            force_public_symbols=self.forced_public,
            public_surface_allowlist=self.committed_exports,
            exports_old=self.exports_old,
            exports_new=self.exports_new,
        )
        for change, decision in zip(fresh, decisions, strict=True):
            change.contract_relevance = decision.relevance
            change.contract_reason_code = decision.reason_code
            change.contract_assurance = decision.assurance
            change.contract_evidence_refs = evidence_refs_for_change(
                change,
                decision,
                mode=self.mode,
                block=self.evidence,
                force_public_symbols=self.forced_public,
                public_surface_allowlist=self.committed_exports,
            )
            # ADR-049 D1: the status follows from the relevance and from
            # nothing else. Stamped here rather than derived at read time so
            # every consumer -- the verdict, the gate, each renderer -- reads
            # one value instead of three independent re-derivations of it.
            change.compatibility_evaluation_status = evaluation_status_for(
                decision.relevance
            )
            self._classified.add(id(change))
            self.changes.append(change)

    def record_compatibility_decisions(
        self,
        changes: Iterable[Change],
        *,
        policy: str,
        policy_file: PolicyFile | None,
        kind_sets: KindSets | None = None,
    ) -> None:
        """Stamp each EVALUATED finding's own compatibility decision.

        The per-finding half of ADR-049 D1's canonical shape: the ``Verdict``
        compatibility policy reached for this finding, or ``None`` (JSON
        ``null``) when the finding was ``NOT_EVALUATED``. ``None`` is not a
        sixth verdict meaning "compatible" -- it records that policy did not
        run, which is why a renderer must not fill it in.

        Uses ``severity.effective_verdict_for_change``, the same per-finding
        classification the gate itself uses (including per-finding
        ``effective_verdict`` overrides and frozen-namespace guards), so the
        recorded decision cannot disagree with the category the gate put the
        finding in.
        """
        from .contract_gating import is_evaluated
        from .severity import effective_verdict_for_change

        for change in changes:
            if not is_evaluated(change):
                change.compatibility_decision = None
                continue
            change.compatibility_decision = effective_verdict_for_change(
                change,
                policy=policy,
                kind_sets=kind_sets,
                policy_file=policy_file,
            )

    def build_context(
        self,
        *,
        policy: str,
        policy_file: PolicyFile | None,
        suppression: SuppressionList | None,
        internal_namespaces: tuple[str, ...],
    ) -> PersistedContractContext:
        """Assemble ADR-049 Phase 4's persisted context for this comparison.

        Called once, at the end of ``compare()``, over every finding this
        stage classified -- so the receipt covers the late opt-in steps'
        findings too, which is the property the old "classify last" ordering
        got for free and this one has to keep deliberately.
        """
        from .contract_context import build_persisted_context, suppression_config_for

        # The report's own per-finding id, taken from the dependency-free
        # `finding_identity` leaf module rather than from `reporter_markdown`
        # (which re-exports it): importing the renderer here would close a
        # `checker -> reporter_markdown -> checker` cycle the
        # `import-cycle-growth` gate rejects. Keying the receipt any other way
        # loses findings: a coarse `kind:symbol` key is shared by two findings
        # of one kind on one symbol (two parameters of the same function),
        # silently dropping one recorded decision and making the receipt
        # uncorrelatable with the report (Codex review, fresh evidence).
        from .finding_identity import report_finding_id

        return build_persisted_context(
            self.evidence,
            mode=self.mode,
            mode_provenance=self.mode_provenance,
            policy=policy,
            # `policy` here is already `effective_policy` -- the policy file's
            # own `base_policy` when one was supplied, which *replaces* the
            # `policy` argument rather than merging with it. Say which input
            # it came from.
            policy_from_file=policy_file is not None,
            internal_namespaces=internal_namespaces,
            # `PolicyFile` is the one place that can tell "no policy file"
            # from "a policy file that wrote `internal_namespaces: []`" -- the
            # tuple above collapses both to `()`. Forwarded so the provenance
            # receipt keeps that distinction (CodeRabbit review).
            internal_namespaces_stated=bool(
                policy_file is not None and policy_file.internal_namespaces_stated
            ),
            # Keyed by the ChangeKind *slug*, which is what the typed config's
            # `overrides` field takes (and what a persisted receipt must carry
            # -- a `ChangeKind` member is not JSON).
            policy_overrides=(
                {kind.value: verdict for kind, verdict in policy_file.overrides.items()}
                if policy_file is not None and policy_file.overrides
                else None
            ),
            suppressions=suppression_config_for(suppression),
            changes=self.changes,
            finding_id=report_finding_id,
        )


def build_contract_stage(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    scope_to_public_surface: bool,
    force_public_symbols: set[str] | None,
    pp_ctx: PipelineContext,
    contract_mode: str | None = None,
) -> ContractEvaluationStage:
    """Resolve the contract mode and collect both sides' evidence once.

    This is the expensive half of contract evaluation -- two public-surface
    resolutions (when post-processing did not already compute them), two
    export-surface resolutions, and the provider-evidence ledger -- and it
    runs only when the caller opted into ``contract_evaluation=True``, which
    is off by default.
    """
    from .compatibility_evaluation_wiring import resolve_legacy_contract_mode
    from .contract_evidence_collect import collect_contract_evidence
    from .contract_relevance_types import coerce_contract_mode
    from .export_surface import compute_export_surface
    from .surface import compute_public_surface

    surf_old = pp_ctx.surf_old
    surf_new = pp_ctx.surf_new
    if surf_old is None or surf_new is None:
        # FilterNonPublicSurface only populates pp_ctx.surf_old/surf_new on
        # the header-scoping path (_run_scope) -- a POST-manifest-only run
        # (public_surface_allowlist set) or scope_to_public_surface=False
        # never computes them, so compute independently here (mirroring
        # that step's own call) rather than leave contract evaluation
        # entirely unresolvable for those runs.
        surf_old = compute_public_surface(old)
        surf_new = compute_public_surface(new)

    # ADR-049 D7 precedence: an explicit `--contract` (EXPLICIT_CLI) outranks
    # the legacy `--scope-public-headers`/`--no-scope-public-headers` alias
    # (LEGACY_ALIAS), which is itself the exact alias for contract=all /
    # contract=public per D2's table. Resolved through Phase 1's own
    # `resolve_legacy_contract_mode` rather than re-deriving the mapping
    # here, so the two cannot drift.
    mode_provenance = None
    if contract_mode is not None:
        mode = coerce_contract_mode(contract_mode)
    else:
        # `scope_public_headers_is_explicit=True` is unconditional on
        # purpose (CodeRabbit review): this core verb receives only the
        # resolved boolean, never whether a user typed the flag, and the
        # pre-Phase-6 behaviour for an absent `contract_mode` is exactly the
        # alias mapping. Passing `False` would contribute no candidate at
        # all, fall through to the built-in default, and silently change the
        # evidence domain of every legacy caller.
        mode, mode_provenance = resolve_legacy_contract_mode(
            scope_public_headers=scope_to_public_surface,
            scope_public_headers_is_explicit=True,
        )

    # `exports` is rooted in the binary's own export table, so it needs its
    # own evidence provider rather than the header-derived surfaces above.
    # Computed for *every* opted-in comparison, not only when `exports` is the
    # selected mode: the persisted `contract_evidence` block is
    # policy-independent by contract, and collecting it conditionally on the
    # original run's mode would make a later `exports` re-evaluation
    # (`contract_replay.reevaluate_from_evidence`) answer
    # `UNKNOWN_UNRESOLVED` for a snapshot pair whose export tables were fully
    # observable -- the exact "re-evaluate under a different contract without
    # re-reading the binaries" guarantee Phase 4 advertises (Codex review,
    # fresh evidence). The cost is one export-table match per side, paid only
    # under `--contract-evaluation`, which is off by default.
    exports_old = compute_export_surface(old)
    exports_new = compute_export_surface(new)

    # ADR-049 Phase 3's observed provider ledger (plan Section 4.1).
    # Collected for *both* providers whenever the export surfaces were
    # computed at all, and always for the header provider: the block is
    # policy-independent by contract, so a later re-evaluation under a
    # different mode can read it without the original run having had to
    # anticipate that mode (plan Section 5.1).
    evidence = collect_contract_evidence(
        old,
        new,
        surf_old,
        surf_new,
        exports_old=exports_old,
        exports_new=exports_new,
        public_surface_allowlist=pp_ctx.public_surface_allowlist,
        force_public_symbols=force_public_symbols,
    )

    return ContractEvaluationStage(
        mode=mode,
        mode_provenance=mode_provenance,
        surf_old=surf_old,
        surf_new=surf_new,
        exports_old=exports_old,
        exports_new=exports_new,
        evidence=evidence,
        forced_public=(
            frozenset(force_public_symbols) if force_public_symbols else None
        ),
        # `is not None`, not truthiness: `post_manifest.parse_manifest()`
        # explicitly supports a manifest committing to *no* exports, and that
        # empty allowlist actively scopes every concrete export out --
        # `post_processing` itself branches on `is not None` for exactly that
        # reason. Collapsing it to `None` here made the persisted ledger and
        # configuration claim no manifest was selected at all, so the
        # resulting exclusions cited header evidence instead of the manifest
        # that caused them (Codex review, fresh evidence).
        committed_exports=(
            frozenset(pp_ctx.public_surface_allowlist)
            if pp_ctx.public_surface_allowlist is not None
            else None
        ),
    )


def evaluated_for_policy(changes: Sequence[Change]) -> list[Change]:
    """The findings compatibility policy scores, per ADR-049 D1.

    A thin re-export of :func:`abicheck.contract_gating.evaluated_changes`
    typed for ``Change``, so ``checker.py`` states the rule in the vocabulary
    of this stage rather than reaching into the leaf module for a predicate
    whose default (unstamped == evaluated) matters to read correctly.
    """
    from .contract_gating import is_evaluated

    return [c for c in changes if is_evaluated(c)]


__all__ = [
    "ContractEvaluationStage",
    "ContractMode",
    "build_contract_stage",
    "evaluated_for_policy",
]

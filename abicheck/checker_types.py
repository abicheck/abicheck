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


"""The comparison result: :class:`DiffResult`.

``DiffResult`` aggregates the :class:`~abicheck.model.change.Change` records
one comparison produced. It is pure model data: bucketing findings by
effective verdict under the active policy is a policy question, answered by
``abicheck.policy.evaluate.evaluate(diff)`` -- never by the result itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .detectors import DetectorResult
from .model.change import (
    Change,
    LibraryMetadata,
)
from .model.change_catalog.registry import Verdict
from .model.contract_finding_relevance import is_evaluated
from .model.evidence_status import (
    Confidence,
    EvidenceTier,
)
from .model.policy_file_protocol import PolicyFileProtocol
from .report_side_facts import ReportSideFacts


@dataclass
class DiffResult(ReportSideFacts):
    old_version: str
    new_version: str
    library: str
    changes: list[Change] = field(default_factory=list)
    verdict: Verdict = Verdict.NO_CHANGE
    suppressed_count: int = 0
    suppressed_changes: list[Change] = field(default_factory=list)  # full audit trail
    suppression_file_provided: bool = (
        False  # True when --suppress was passed, even if 0 matched
    )
    detector_results: list[DetectorResult] = field(default_factory=list)
    policy: str = (
        "strict_abi"  # active policy profile; drives breaking/source_breaks/compatible
    )
    policy_file: PolicyFileProtocol | None = None  # custom policy w/ overrides (Bug 4)
    old_metadata: LibraryMetadata | None = None
    new_metadata: LibraryMetadata | None = None
    redundant_changes: list[Change] = field(
        default_factory=list
    )  # hidden by redundancy filter
    redundant_count: int = 0
    old_symbol_count: int | None = None  # public exported symbol count in old library
    # Evidence tier and confidence — helps users assess how much trust to
    # place in the verdict.  "high" means multiple evidence sources agree;
    # "low" means key detectors were disabled (e.g., DWARF stripped).
    confidence: Confidence = Confidence.HIGH
    evidence_tiers: list[str] = field(
        default_factory=list
    )  # e.g. ["elf", "dwarf", "header"]
    coverage_warnings: list[str] = field(
        default_factory=list
    )  # human-readable coverage gaps
    # ADR-024: findings excluded because they are not on the public-header
    # ABI surface (only populated when scope_to_public_surface is enabled).
    # Recorded for audit — surfaced under --show-filtered — never dropped.
    out_of_surface_changes: list[Change] = field(default_factory=list)
    out_of_surface_count: int = 0
    # ADR-039 — findings suppressed as context-free header-parse artifacts once
    # build context (active ``-D`` defines + per-field ``guard`` annotations)
    # proved the field's real presence is identical across both builds. Recorded
    # for audit — surfaced under --show-filtered — never silently dropped. Only
    # populated when ``reconcile_build_context`` was enabled and build evidence
    # was present; empty otherwise. Excluded from the verdict.
    reconciled_changes: list[Change] = field(default_factory=list)
    reconciled_count: int = 0
    scope_to_public_surface: bool = False
    # False only when --scope-public-headers was requested but the public
    # surface could not be resolved, so scoping fell back to the full export
    # table. A False value means compatibility is *unconfirmed* and the result
    # needs manual review — it must never read as a confidently-clean public
    # surface (issue #235).
    scope_resolved: bool = True
    # ADR-024 §D5.3 — structured confidence in the surface resolution itself
    # (distinct from ``confidence`` above, which is the overall verdict trust).
    # "high" with no notes = clean header-scoped run; "reduced" with one or more
    # structured note codes (e.g. "mangling-fallback", "no-provenance") when the
    # surface had to be resolved less reliably. Disclosed in the JSON/SARIF
    # surface ledger so the "demote + disclose" promise stays auditable.
    surface_scope_confidence: str = "high"
    surface_scope_notes: list[str] = field(default_factory=list)
    # Canonical analysis depth (ordered): ELF_ONLY < DWARF_AWARE < HEADER_AWARE.
    # Distinct from the raw ``evidence_tiers`` list above — this is the single
    # scalar consumers should key trust decisions off of. See EvidenceTier.
    evidence_tier: EvidenceTier = EvidenceTier.ELF_ONLY
    # ADR-027 A4 — pattern-aware verdict modulation ledger. Each entry records a
    # demotion/raise the pattern pass made (symbol, original→new category, the
    # rule id, the disclosed reason, the evidence tier, and the graph edges that
    # matched). Empty unless --pattern-verdicts was enabled. Findings themselves
    # carry the override on Change.effective_verdict; this is the audit trail.
    pattern_modulations: list[dict[str, object]] = field(default_factory=list)
    # ADR-028 D7 — evidence-coverage rows (L0–L5) for the compare, when an
    # BuildSourcePack was supplied. Each entry is a serialized LayerCoverage
    # ({layer, status, confidence, detail}). Surfaced in the JSON report so
    # machine consumers can tell artifact-proven from build-context-only
    # findings; empty when no evidence was involved.
    layer_coverage: list[dict[str, object]] = field(default_factory=list)
    # I4 per-side edge coverage (compare.edge_query.edge_coverage_report).
    edge_coverage: dict[str, object] = field(default_factory=dict)
    # ADR-033 D6/D9 — evidence-collection timing and observability metrics for
    # the compare. Populated only when build-info/source facts were involved
    # (mirrors ``layer_coverage``). Keys follow the D9 metric names (e.g.
    # ``extractor.duration_seconds``, ``findings.source_only.count``); surfaced
    # in the JSON report so CI can tune mode selection. Empty otherwise.
    evidence_metrics: dict[str, object] = field(default_factory=dict)
    # ADR-047 §7 report-identity envelope (G30 P0.3) — optional, additive.
    # Nothing in the CLI/service layer populates these yet; they exist so the
    # GitHub Actions integration-model primitives planned in G30 P1
    # (``resolve-baseline``, ``check-target``) have a report-level place to
    # record a check's identity once they're built. None means "not set by
    # this caller" and the field is omitted from the JSON report entirely —
    # never emitted as a null/empty placeholder.
    check_id: str | None = None  # "target@profile#baseline_channel@requested_depth"
    profile_id: str | None = None  # e.g. "linux-x86_64-gcc13-release"
    requested_depth: str | None = None  # one of EVIDENCE_DEPTH_VALUES
    effective_depth: str | None = None  # one of EVIDENCE_DEPTH_VALUES
    baseline_channel: str | None = None  # e.g. "accepted-main", a release tag
    # ADR-050 D2 — set when exactly one side of the compare carried an
    # ExtractionContract (a genuinely mixed pair: e.g. a fresh header-AST
    # dump compared against a pre-ADR-050 stored baseline, or a symbols-only
    # side against a full L2 side). None on the ordinary case (both or
    # neither side carries a contract) — this is report-level metadata, not
    # a ChangeKind/Change finding, so it is structurally unreachable by any
    # severity promotion. "partial" is currently the only recognized
    # value.
    contract_coverage: Literal["partial"] | None = None
    # ADR-050 D2 — set to "none" only when --diagnostic-comparison forced a
    # tentative diff through past a genuine contract mismatch that would
    # otherwise have raised ProfileMismatchError/ScopeMismatchError. Applies
    # to the whole DiffResult (the gate failed for the pair as a whole
    # before any diff ran), not per-Change.
    assurance: Literal["none"] | None = None
    # E-S2 (cli-cleanup-phase-two.md Block 5) — `assurance`'s per-dimension
    # breakdown: one entry per `comparability.COMPARABILITY_DIMENSIONS` name,
    # each "unverified" or "trusted". Same population/omission rule as
    # `assurance` above; see `comparability.dimension_assurance`'s own doc.
    comparability_assurance: dict[str, str] | None = None
    # ADR-049 Phase 4 — the three persisted blocks (``contract_evidence`` /
    # ``evaluation_context`` / ``decision_receipt``) for this comparison,
    # assembled by ``contract_context.build_persisted_context``. Populated
    # only under ``compare(..., contract_evaluation=True)``; ``None``
    # otherwise, and omitted from the JSON report entirely rather than
    # emitted as a null placeholder. Typed as ``object`` for the same
    # circular-import reason as ``Change.contract_relevance``'s flat fields:
    # ``contract_evidence.py`` reaches ``compatibility_evaluation_config.py``
    # -> ``checker_policy.py``, which this module also imports, and a real
    # annotation here would pull that whole chain into every consumer of
    # ``DiffResult``. Audit data as far as *compatibility* policy and the
    # change gate are concerned -- they read the per-finding fields above,
    # never this block -- but not inert: ``contract_coverage_exit.
    # coverage_exit_floor`` derives the orthogonal coverage contribution from
    # it, so a run carrying one can exit ``1`` on that axis (ADR-049 §7).
    contract_context: object | None = None
    # E-S3 — multi-source contract conflicts (exported-but-undeclared,
    # manifest narrowing since baseline), each a serialized
    # ``model.contract_conflicts.ContractSourceConflict``. Same gate/``None``/
    # omission/``object``-typing rules as ``contract_context`` above.
    # Advisory only — never changes a verdict, severity, or exit code.
    contract_conflicts: object | None = None
    # P0.4 — the orthogonal "how complete/trustworthy was the evidence"
    # answer (analysis_assurance.py), sitting beside `verdict` (what changed)
    # and the severity/gate exit code (whether to fail the build) as the
    # third leg of a three-way split. Always populated by `checker.compare()`
    # (never `None` for a `DiffResult` it returned) -- typed as `object` for
    # the same reason `contract_context` above is: `analysis_assurance.py`
    # imports `DiffResult` from this module to build one, so a real
    # annotation here would be circular. Narrow with `isinstance` at any
    # consumption site (see `reporter._add_analysis_assurance`).
    analysis_assurance: object | None = None
    # ``compare --use-cases MANIFEST``'s attribution of this comparison's own
    # findings to the declared use cases whose entrypoints reach them
    # (``impact.use_case_impact.UseCaseImpact``). ``None`` for every run that
    # did not pass the flag. Typed ``object`` for the same circular-import
    # reason as the two blocks above -- the builder imports ``Change`` from
    # this module. Read-only report data: it never reaches the verdict, the
    # gate, or an exit code, because an unattributed finding is an absence of
    # proof, not proof the finding is harmless.
    use_case_impact: object | None = None
    # CLI cleanup phase two, PR B (Codex review, PR #803): the resolved
    # ``CompatibilityEvaluationConfig`` this comparison was configured with,
    # when one was resolved at all -- unlike ``contract_context`` above,
    # this is populated whenever ``--pack`` selected a pack, *not only*
    # under ``--contract`` (``resolve_and_apply``/``resolve_cli_config``
    # already resolve one for a pack-only run; it was previously only
    # attached to the report through ``contract_context``, which stays
    # unset without ``--contract``, so a pack-only run's real pack
    # identities were unreachable at report time). ``field(kw_only=True)``,
    # appended at the true end, per this file's own established
    # positional-field-safety convention (see ``vtable_covers_unverifiable_
    # layout_gap``/``symbol_binding`` above) -- inserting an ordinary
    # positional field here would silently reinterpret every existing
    # positional caller's later arguments. Typed ``object`` for the same
    # circular-import reason as ``contract_context``/``analysis_assurance``
    # above. Read by ``effective_config_digest.effective_config_fields``,
    # which prefers ``contract_context``'s own nested resolved config over
    # this one whenever a ``PersistedContractContext`` exists (Codex
    # review, PR #803, fresh evidence: ``contract_context.with_resolved_
    # config`` merges *observed* overlay evidence -- e.g. a
    # ``--post-manifest`` overlay no front-end input model describes --
    # into a *new* config object, so the two are not always the same
    # object even though both are stamped from the same D7 resolution),
    # falling back to this field only when no context exists at all (the
    # ``--pack``-only case this field exists for).
    evaluation_config: object | None = field(default=None, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803): the resolved
    # --suppress file's own content digest (``SuppressionList.source_
    # sha256``), when one was given -- for an ordinary comparison with
    # neither ``--contract`` nor ``--pack``, no ``CompatibilityEvaluation
    # Config`` is ever resolved, so ``effective_config_digest``'s baseline
    # tier had no way to detect that two runs differing only in which
    # suppression file they loaded (each removing different findings)
    # resolved genuinely different configuration. ``None`` when no
    # suppression file was given, distinct from ``suppression_file_
    # provided``'s bool (this field is the content, not just presence).
    suppression_source_sha256: str | None = field(default=None, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # a canonical content digest of every resolved explicit-scope input:
    # ``compare(..., force_public_symbols=...)`` (resolved from
    # ``--public-symbols-list``/``.abicheck.yml``'s ``scope.public_
    # symbols``) and ``compare(..., public_surface_allowlist=...)`` (the
    # resolved ``--post-manifest`` allowlist, where an *empty* allowlist is
    # a distinct active scope, not the absence of one), for the same reason
    # ``suppression_source_sha256`` above exists: an ordinary comparison
    # with neither ``--contract`` nor ``--pack`` never resolves a
    # ``CompatibilityEvaluationConfig``, so the baseline tier had no way to
    # detect that two runs selecting different forced-public/manifest
    # scopes (which can retain different findings) resolved genuinely
    # different configuration. ``None`` when neither source was active at
    # all.
    explicit_scope_source_sha256: str | None = field(default=None, kw_only=True)
    # The ``--exclude-header`` rules (``surface.exclude_headers``) and the
    # ADR-075 ownership-rule fingerprint (``surface.ownership``) this
    # comparison ran under, each canonicalized by its model helper
    # (``header_exclusion_record.comparison_exclusion_identity``,
    # ``extraction_scope.extraction_scope_identity``). Both narrow or relabel
    # the compared surface, so two runs differing only in them must not
    # collide on ``effective_config_digest``.
    excluded_header_patterns: str = field(default="", kw_only=True)
    extraction_scope_identity: str = field(default="", kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # whether ADR-027 A4 pattern-aware verdict modulation
    # (``compare(..., pattern_verdicts=...)``, opt-in via
    # ``--pattern-verdicts``/``--explain-patterns``) was requested for this
    # comparison. ``pattern_modulations`` (elsewhere on this dataclass)
    # records the *ledger of applied* modulations, which is empty whenever
    # no idiom/antipattern happened to match -- indistinguishable from the
    # flag never having been set at all, even though the *configuration*
    # genuinely differed
    # (a policy demotion/raise can change ``kept``/``verdict``/the exit
    # code the moment a matching idiom appears, per ``checker.py``'s own
    # ``_apply_pattern_verdicts_step``). Recorded directly on ``result``
    # rather than nested under any D7 namespace, since it isn't a D7
    # ``CompatibilityEvaluationConfig`` concept at all -- there is no
    # external, user-authored "patterns" config to content-digest, only
    # abicheck's own built-in idiom/antipattern detection gated on this one
    # boolean, the same shape as ``scope_to_public_surface`` above.
    pattern_verdicts_enabled: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # whether ``compare(..., collapse_versioned_symbols=...)`` was
    # requested for this comparison -- when enabled, post-processing can
    # remove a versioned symbol-version remove/add pair entirely, turning
    # an otherwise ``BREAKING`` verdict non-breaking, with no other trace
    # of the setting on ``result``. Same shape as ``pattern_verdicts_
    # enabled`` above: a checker-level boolean with no D7 namespace of its
    # own, recorded directly rather than nested under any D7 field.
    collapse_versioned_symbols_enabled: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # whether ``compare(..., surface_metrics=...)`` (``--surface-metrics``)
    # was requested for this comparison -- when enabled,
    # ``_apply_surface_metrics`` appends suppressible ADR-027 A1/D1.2
    # aggregate-drift findings and can flip ``NO_CHANGE`` to ``COMPATIBLE``.
    # Same shape as ``pattern_verdicts_enabled`` above.
    surface_metrics_enabled: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence): a
    # canonical content digest of the resolved deployment matrix (ADR-020b
    # declared deployment constraints -- `.abicheck.yml`'s `deployment:`
    # config key, ADR-068 D5, former `--env-matrix FILE`) this comparison
    # ran with. ``_env_matrix_contract_changes`` can reclassify a version-
    # requirement finding against ``env_matrix.runtime_floors``, so two runs
    # against a differently-configured ``deployment:`` block must not
    # collide on the digest. ``None`` when none was declared -- distinct
    # from one resolving to every constraint unspecified, the same
    # "selected vs. absent" distinction ``explicit_scope_source_sha256``
    # already draws.
    env_matrix_source_sha256: str | None = field(default=None, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # whether ``compare(..., reconcile_build_context=...)``
    # (``--reconcile-build-context``) was requested for this comparison --
    # when enabled, ``reconcile_build_context_findings`` can move a phantom
    # breaking finding from ``kept`` into the reconciliation audit bucket,
    # changing the verdict and exit code. Same shape as
    # ``pattern_verdicts_enabled`` above.
    reconcile_build_context_enabled: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B (Codex review, PR #803, fresh evidence):
    # the *raw* ``compare(..., scope_to_public_surface=...)`` value the
    # caller passed in -- distinct from ``scope_to_public_surface`` above,
    # which ``checker.compare()`` stamps with the *derived* ``scope_active
    # = scope_to_public_surface or public_surface_allowlist is not None``
    # (so it reads ``True`` whenever a ``--post-manifest`` allowlist is
    # active, regardless of the raw flag). That collapsing matters:
    # ``post_processing.FilterNonPublicSurface._run_allowlist`` only honors
    # the ``force_public_symbols`` widening overlay when the *raw* flag is
    # true (deliberately -- the CLI already warns that overlay is ignored
    # under ``--no-scope-public-headers``), so two runs sharing the same
    # POST manifest and forced-public symbols but opposite raw
    # ``--scope-public-headers`` settings can retain genuinely different
    # findings while ``scope_to_public_surface`` reads identically
    # ``True`` for both. Default ``True``, matching ``compare()``'s own
    # parameter default.
    scope_to_public_surface_requested: bool = field(default=True, kw_only=True)
    # ADR-067 C-S1 -- the conserved policy-disposition ledger
    # (``policy.disposition_ledger.DispositionLedger``): every atomically
    # detected change with the single terminal disposition it received, plus
    # the suppression rule's provenance for each finding a ``--suppress`` rule
    # hid. Always populated by ``checker.compare()``; ``None`` for a
    # ``DiffResult`` some other caller assembled, in which case
    # ``policy.disposition_ledger.ledger_for`` finalizes an equivalent one
    # from this object's own buckets -- so every consumer can state D3's
    # counts unconditionally. Typed ``object`` for the same circular-import
    # reason as ``contract_context``/``analysis_assurance`` above (the ledger
    # module type-checks against ``DiffResult``). Audit data: it decides no
    # verdict and no exit code, but it is not inert -- ``semver.
    # recommend_release`` reads the *conserved* delta from it, so a suppressed
    # major-class break can no longer read as "no bump needed".
    disposition_ledger: object | None = field(default=None, kw_only=True)
    # ADR-067 D5/C-S3: the loaded acknowledgment records this comparison was
    # given, if any (`checker.compare(acknowledgments=...)`). Read generically
    # (duck-typed `.evaluate`) by `policy.disposition_close.finalize_ledger`/
    # `close_consumer_scope` to resolve each finding's `acknowledged_by`
    # overlay -- typed `object` for the same circular-import reason as
    # `disposition_ledger` above (`policy.acknowledgment` is a `policy`-layer
    # module; `checker_types` is `model`-layer and may not import it).
    # `None` for every pre-existing caller, which is a no-op (AGENTS.md's
    # "optional inputs stay optional").
    acknowledgments: object | None = field(default=None, kw_only=True)
    # ADR-067 D6/C-S3: the additions-review gate's own evaluated result
    # (`policy.acknowledgment_gate.AdditionsReviewResult`), computed by
    # `checker.compare()` only when `acknowledgments` above was supplied.
    # `None` otherwise -- the same "capability never exercised" state
    # `report.disposition_audit`'s `not_evaluated_detectors` already uses,
    # so a run that never asked the question reads as "never evaluated",
    # not as "zero unacknowledged additions found".
    unacknowledged_additions_review: object | None = field(default=None, kw_only=True)
    # one-comparison-product.md P3 / ADR-064: `scan`'s two exit axes
    # (`_EvidenceContractError`/`_BudgetOverflow`, exit 7/5), reserved so
    # native `compare` can carry the identical signal via `policy.exit_
    # decision_precedence.resolve_compare_exit_decision_with_abort_axes`,
    # reusing ADR-064's precedence rule rather than a second copy. `False`
    # for every `DiffResult` any current caller builds -- `compare` has no
    # CLI-level source for either condition yet (no `--budget` flag, ADR-037
    # D5's auto-strict `--depth`/`--source-method` enforcement is scan-only);
    # that CLI wiring is deferred to the plan's Phase 2/7. Appended at the
    # true end (positional-field-safety convention, see `effective_config`
    # above).
    evidence_contract_error: bool = field(default=False, kw_only=True)
    budget_overflow: bool = field(default=False, kw_only=True)
    # ADR-068 Phase 1 item 2: findings from a *previous* comparison in a
    # chain that no longer appear in `changes` (`FindingEvolution.RESOLVED`'s
    # home -- never a `changes` member, as no current-side `Change` exists
    # for policy to score), and whether a previous comparison was supplied at
    # all -- the recorded fact `finding_evolution.chain_evaluated` reports,
    # never inferred from counts (two empty results compared still evaluated
    # a chain). Both set only by `policy.finding_evolution`.
    resolved_findings: list[Change] = field(default_factory=list, kw_only=True)
    finding_evolution_evaluated: bool = field(default=False, kw_only=True)
    # Phase 2b (one-comparison-product.md plan §3 #6/#8, ADR-068 D3/D4/D5):
    # the folded, per-side lexical pattern pre-scan + preprocessor pre-scan
    # result (``workflows.pattern_preprocessor_scan.
    # PatternPreprocessorScanResult``) -- the same "run independently on OLD
    # and NEW, fold via ``CrossSourceEvolution``" shape ``cross_source_
    # evolution.py`` already established for the cross-source checks, reused
    # here for the two other scan-only primitives ADR-068 §1 named
    # (``buildsource.pattern_facts.find_pattern_facts`` /
    # ``buildsource.preprocessor_facts.collect_preprocessor_facts``). Always
    # populated by `checker.compare()` (`pattern_preprocessor_scan` defaults
    # to `True`, no front end exposes a way to disable it -- D5 rejects "a
    # flag that merely enables useful analysis"). Typed ``object`` for the
    # same circular-import reason as ``contract_context``/
    # ``analysis_assurance`` above: the workflow module that builds one
    # imports ``AbiSnapshot`` from ``model``, not this module, so the
    # annotation itself is not what forces this, but keeping the same
    # convention as every other late-appended block here avoids a special
    # case. Advisory only -- pattern/preprocessor facts are never a verdict
    # on their own (``pattern_facts.py``/``preprocessor_facts.py``'s own
    # docstrings), so this field never reaches the verdict, severity, or
    # exit code. Appended at the true end, same positional-field-safety
    # convention as every other block above.
    pattern_preprocessor_scan: object | None = field(default=None, kw_only=True)

    @property
    def not_evaluated(self) -> list[Change]:
        """Findings compatibility policy did not score (ADR-049 D1).

        ``PROVEN_OUT_OF_CONTRACT``, ``UNKNOWN_UNPROVEN`` and
        ``UNKNOWN_UNRESOLVED`` findings, in ``changes`` order. Empty for
        every run that did not opt into ``--contract``.
        """
        return [c for c in self.changes if not is_evaluated(c)]

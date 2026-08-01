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

"""Versioned JSON Schemas for abicheck machine-readable output.

The schemas in this package describe the stable JSON contract that
automated consumers (CI gates, dashboards, other tooling) can rely on.

Stability policy
----------------
The compare-report schema is versioned with a SemVer-style
``MAJOR.MINOR`` string exposed as :data:`REPORT_SCHEMA_VERSION` and emitted
in every JSON report as ``report_schema_version``:

- **Additive** changes — new optional keys, new enum members, relaxing a
  constraint — bump the **MINOR** component. Existing consumers keep working.
- **Breaking** changes — removing/renaming a key, tightening a type,
  removing an enum member — bump the **MAJOR** component.

Consumers should accept any report whose ``report_schema_version`` shares
their expected MAJOR component and ignore unknown keys.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

#: SemVer-style (MAJOR.MINOR) version of the compare-report JSON schema.
#: 1.1 — added the optional ``release_recommendation`` object (additive).
#: 1.2 — added the optional source/build evidence coverage array (additive).
#: 2.0 — renamed that coverage array's key ``evidence_coverage`` →
#:       ``layer_coverage`` (ADR-028 D7) during the evidence→buildsource
#:       rename. Renaming a key is breaking per the policy above, so the MAJOR
#:       component bumps; consumers pinned to 1.x must update.
#: 2.1 — added the optional ``evidence_metrics`` object (ADR-033 D6/D9):
#:       evidence-collection timing + finding split. Additive optional key.
#: 2.2 — added the optional per-finding ``evidence_status`` key (one of
#:       "artifact_proven"/"source_contract"/"contextual_risk"/
#:       "consumer_proven"/"not_checkable"): the finding's policy-independent
#:       epistemic status (derived from its kind's intrinsic category, not
#:       the policy-resolved verdict), or set explicitly for appcompat/
#:       plugin-check consumer-proven findings. Additive optional key.
#: 2.3 — three additive optional keys: per-finding ``finding_id`` (a stable,
#:       deterministic fingerprint hashed from kind/symbol/old_value/
#:       new_value/source_location — lets a consumer correlate the same
#:       finding across two report runs without relying on array order) and
#:       ``operation`` ("added"/"removed"/"modified", derived from the
#:       kind's own suffix classification — the same one --show-only's
#:       added/removed/changed tokens already use); and, on the top-level
#:       ``severity`` object (only present when severity_config is active),
#:       ``blocking`` (bool) and ``blocking_categories`` (list of category
#:       names actually gating the exit code) — a typed gate summary
#:       mirroring SARIF's ``severityGate`` block, so a consumer no longer
#:       has to independently recompute "what's actually failing the build"
#:       from ``config``/``categories``.
#: 2.4 — added the optional per-finding ``recommended_action`` key: a
#:       structured, machine-readable next step derived from the same
#:       effective verdict/category resolution ``severity``/``operation``
#:       already use — ``recompile_and_relink_required`` (BREAKING),
#:       ``recompile_required`` (API_BREAK), ``verify_deployment_compatibility``
#:       (COMPATIBLE_WITH_RISK), ``review_recommended`` (COMPATIBLE quality
#:       issue), or ``no_action_required`` (COMPATIBLE addition). Additive
#:       optional key.
#: 2.5 — added the optional per-finding ``correlated_change_kind`` key
#:       (ADR-041 P0 roadmap item 2): for a ``public_api_internal_dependency_added``
#:       finding correlated with the same public entry's own body/type-hash
#:       change this version, the correlated finding's ``ChangeKind`` value
#:       (e.g. ``"inline_body_changed"``) — the structured sibling to the
#:       correlation ``description`` already carried in prose. Additive
#:       optional key.
#: 2.6 — added the optional per-finding ``reviewer_action`` key, present only
#:       on a COMPATIBLE addition (``recommended_action == "no_action_required"``):
#:       finer-grained reviewer guidance that field alone couldn't carry, since
#:       it only answers "does the old binary consumer need to do anything?"
#:       (no) and not "does a human reviewing this PR have anything to check?"
#:       (usually yes — was the export intentional, do exhaustive switches
#:       need the new case, does a stable-API doc need updating). One of
#:       ``review_exhaustive_switches`` (``enum_member_added``),
#:       ``document_stable_replacement`` (``experimental_graduated``), or the
#:       default ``confirm_public_api_intent`` for every other addition kind.
#:       Additive optional key; does not change ``recommended_action``'s
#:       existing values or meaning.
#: 2.7 — added three additive optional per-finding keys (ADR-044 P1 item 4):
#:       ``public_reachable`` (bool), ``reachability_kind`` (one of
#:       "direct_public_symbol"/"value_embedding"/"pointer_or_signature"/
#:       "symbol_availability"), and ``reachability_proof_path`` (string) —
#:       previously surfaced only as prose inside the
#:       ``suppression_would_hide_public_break`` diagnostic's description.
#: 2.8 — added ``"consumer_proven"`` to ``reachability_kind``'s enum
#:       (ADR-044 P2): set on the ``consumer_required_symbol_removed``/
#:       ``consumer_runtime_load_failed`` overlays ``compare --used-by``
#:       synthesizes, which are always consumer-verified real (a real
#:       consumer binary's own requirement, or an actual dynamic-linker
#:       failure) rather than established by the L0-L5 public-surface walk
#:       the other four values describe. Additive enum member.
#: 2.9 — added three additive optional top-level keys documenting the
#:       existing scoped-vs-full-library split for a ``--used-by``/
#:       ``--required-symbol(s)`` compare: ``full_verdict`` (mirrors
#:       ``verdict``'s enum), ``full_severity`` (mirrors ``severity``'s
#:       shape), and ``full_summary`` (mirrors ``summary``'s shape).
#:       ``full_verdict``/``full_severity`` were already emitted (unversioned)
#:       since the pre-1.0 CLI reset (#566); ``full_summary`` is new this
#:       version, fixing the audit finding that a scoped run gated only by a
#:       scoped-only synthetic finding could report a non-zero ``verdict``
#:       next to a stale, contradictory ``summary.total_changes: 0`` --
#:       ``summary`` is now always recomputed from the complete (post-scoping)
#:       ``changes`` array, and ``full_summary`` preserves the original
#:       pre-scoping counts. Additive optional keys (external review).
#:   2.10: ``reachability_kind`` gained a new enum member,
#:       ``"public_source_abi_surface"`` -- set by ``MarkReachability`` for
#:       an L4/L5 source-graph finding (e.g. ``public_typedef_removed``)
#:       whose kind is public by construction, not established by the
#:       public-surface layout/call-graph walk the other values describe
#:       (Codex review). Additive.
#:   2.11: added three additive optional per-finding keys (G31 Phase B3,
#:       ADR-048): ``affected_public_roots`` (list of public entry labels an
#:       L5 graph walk proved reach this finding's internal target),
#:       ``impact_proof_path`` (the structured node/edge-list counterpart of
#:       the prose "Proof path(s)" text ``graph explain`` already produces),
#:       and ``impact_is_direct`` (bool — whether the shortest proof path is
#:       a single hop). Enrichment on an existing finding, never a
#:       standalone new finding; present only when the embedded L5 graph
#:       has relevant reachability data for that finding (Codex review).
#:   2.12: added five additive optional top-level keys -- ``check_id``,
#:       ``profile_id``, ``requested_depth``, ``effective_depth``,
#:       ``baseline_channel`` -- the report-identity envelope subset of
#:       ADR-047 §7 (G30 P0.3). Nothing in the CLI/service layer populates
#:       them yet; they exist so the GitHub Actions integration-model
#:       primitives G30 P1 will add (``resolve-baseline``, ``check-target``)
#:       have a report-level place to record a check's identity. Omitted
#:       entirely (never emitted as null) when unset.
#:   2.13: added the remaining ADR-047 §7 report-envelope keys --
#:       ``compatibility_verdict`` (``Verdict``-cased mirror of the legacy
#:       ``verdict`` field), ``policy_gate_decision`` (``"pass"``/``"fail"``),
#:       ``check_evidence_coverage`` (``{state, reasons}``, not
#:       ``layer_coverage`` -- deliberately a different name/shape than that
#:       existing per-layer array, see §7's field-naming corrections),
#:       ``operational_errors`` (list of ``{kind, message}``), ``publication``
#:       (``{state, channels}``), ``project``, ``head_sha``, ``base_ref``,
#:       ``action_version``, and ``tool_version``. Populated by
#:       ``actions/check-target`` (G30 P1.3, ``abicheck.buildsource.
#:       check_report``), which reads/rewrites a report file after the CLI
#:       has already produced it -- nothing in the CLI/service layer sets
#:       these directly either, same as 2.12's five keys. All additive/
#:       optional; omitted entirely (never emitted as null) when unset.
#:   2.14: ``release_recommendation`` gained a new optional ``state`` key
#:       (one of "actionable"/"review"/"unavailable" -- how much weight the
#:       recommendation itself can bear, independent of what it recommends)
#:       and ``soname_action`` gained a new enum member, ``"not_determined"``
#:       -- set on a BREAKING verdict whose comparison carried no
#:       ELF/PE/Mach-O/DWARF evidence at all, so abicheck no longer asserts a
#:       confident SONAME action it cannot back with a real binary artifact
#:       (Codex review). Both additive.
#:   2.15: added two additive optional per-change keys (G29 Phase 3 slice 1,
#:       ADR-052) -- ``reachability_state`` (the tri-state signal from PR
#:       #607's ``Change.reachability_state``, always present, never
#:       serialized before this) and ``impact_assessment`` (a unified read
#:       view over the scattered reachability/impact fields above --
#:       ``reachability_state``/``public_reachable``/``reachability_kind``/
#:       the proof path/decision state/``evidence_category``/
#:       ``correlated_change_kind`` -- present only when it carries
#:       information beyond the all-defaults case).
#:   2.16: added two additive optional top-level keys, present only under
#:       ``--report-mode root-cause`` (G29 Phase 3 slices 3-4, ADR-052) --
#:       ``root_causes`` (groups ``changes`` by ``Change.caused_by_type``,
#:       falling back to the change's own symbol for an ungrouped finding)
#:       and ``root_cause_count``. This schema field is JSON-specific by
#:       nature, but the same grouping also renders for ``--format
#:       markdown``/text (slice 4) and as additive SARIF ``properties``
#:       (slice 5, SARIF has no ``report_schema_version`` of its own) --
#:       ``root_cause_id`` is a stable hash of the grouping key, not the
#:       eventual G29 Phase 6 ``RootCauseCorrelator``'s own identifier
#:       scheme.
#:   2.17: ADR-050 D2 -- ``verdict`` gains a third class, ``null``, for a
#:       comparability-gate hard-fail (old/new were not extracted under a
#:       comparable ``ExtractionContract``; see ADR-050 D1). A ``null``-verdict
#:       report carries a new ``reason`` object (``{kind, message}``,
#:       ``kind`` one of ``"profile_mismatch"``/``"scope_mismatch"``) instead
#:       of the full compare-report shape (``changes``/``summary``/etc. are
#:       never populated, since the gate raises before any diff runs). Two
#:       more additive optional top-level keys, present on an ordinary
#:       completed comparison: ``contract_coverage`` (``"partial"`` when
#:       only one side of the pair carried a given fingerprint) and
#:       ``assurance`` (``"none"`` when the comparison only completed via
#:       ``--diagnostic-comparison`` after a genuine mismatch).
#:   2.18: ``evidence_status`` gained a new enum member, ``"unattributed"``
#:       (P0 evidence-provider audit) -- a finding whose kind is intrinsically
#:       a BREAKING_KINDS member (would otherwise read ``"artifact_proven"``)
#:       but whose comparison's ``evidence_tiers`` positively show no real
#:       binary (ELF/PE/Mach-O/DWARF) was ever examined, e.g. a Python-API
#:       caller comparing hand-built/loaded snapshots. Previously such a
#:       finding always read ``"artifact_proven"`` purely from its kind,
#:       regardless of what the comparison actually examined -- mirrors the
#:       2.14 SONAME ``"not_determined"`` fix, applied to the per-finding
#:       evidence label instead of the release recommendation. Additive.
#:   2.19: ``impact_assessment`` gained three additive optional keys --
#:       ``root_cause_id``, ``root_cause_display``, ``impact_group_id``
#:       (G29 Phase 3 follow-up, ADR-052) -- the same 2.16 root-cause
#:       grouping surfaced per-finding, independent of ``report_mode``
#:       (unlike 2.16's ``root_causes`` array, which is root-cause-mode
#:       only). Deliberately absent for a singleton finding with no real
#:       correlation signal, so ``impact_assessment`` doesn't balloon with a
#:       root cause that names nothing but itself. ``impact_group_id`` is
#:       currently always identical to ``root_cause_id`` -- a placeholder
#:       alias until Phase 6's ``RootCauseCorrelator`` gives it independent
#:       meaning. Also documents, retroactively, three additive optional
#:       keys on ``impact_assessment.proof_path`` that shipped in 2.16
#:       (ADR-046 D6/D1, ADR-052's ``occurrence_id`` follow-up) but were left
#:       undocumented here until this audit caught the gap:
#:       ``alternative_paths`` (up to 3 runner-up candidate paths not
#:       selected as primary, each shaped like ``proof_path`` itself minus
#:       these three keys), ``discarded_path_count`` (candidates beyond that
#:       cap), and ``occurrence_id`` (a hash over the path's edges' own graph
#:       occurrences, independent of ``description`` text). All absent for
#:       the common single-candidate/no-occurrence-data case.
#:   2.20: ``release_recommendation.version_bump`` gains ``null`` as a valid
#:       value (P0 evidence-recommendation-honesty audit): previously the
#:       field always serialized a plausible-looking ``"major"`` literal even
#:       when ``state`` was ``"unavailable"`` (no real evidence backs the
#:       bump), which let automation reading ``version_bump`` alone act on a
#:       release action abicheck explicitly could not confirm. Now
#:       ``version_bump`` is ``null`` whenever ``state == "unavailable"``;
#:       the still-plausible bump, if any, remains readable from
#:       ``rationale`` prose. A consumer that only reads ``state`` (as the
#:       2.14 addition intended) is unaffected; a consumer that reads
#:       ``version_bump`` without checking ``state`` first must now handle
#:       ``null``, which is why this is a relaxed-constraint MINOR bump, not
#:       a MAJOR one (the enum widened, no existing value changed meaning).
#:   2.21: ``release_recommendation`` gains an ``allOf``/``if``/``then`` pair
#:       enforcing ``version_bump == null`` iff ``state == "unavailable"``
#:       (CodeRabbit review, PR #639). 2.20 widened ``version_bump``'s type
#:       to accept ``null`` but did not, at the schema level, tie that to
#:       ``state`` — a producer bug could in principle emit
#:       ``version_bump: null`` with ``state: "actionable"``, or a concrete
#:       bump with ``state: "unavailable"``, and still validate. Every real
#:       producer (``ReleaseRecommendation.to_dict()``) already only emits
#:       the paired combination, so this tightens validation without
#:       changing what a conformant report looks like — a MINOR bump, not
#:       MAJOR, same reasoning as 2.20 itself.
#:   2.22: ``release_recommendation`` gains ``possible_impact`` (status-review
#:       follow-up): a new, always-non-null string field carrying the bump
#:       abicheck would recommend if its evidence were sufficient to confirm
#:       one. 2.20 made ``version_bump`` itself ``null`` for ``state ==
#:       "unavailable"``/withheld the value for automation — correctly, but
#:       it left the still-plausible bump readable only from free-text
#:       ``rationale`` prose, which no machine consumer should have to parse.
#:       ``possible_impact`` is additive (new optional key, existing fields
#:       unchanged) and equals ``version_bump`` whenever ``state ==
#:       "actionable"`` — a MINOR bump.
#:   2.23: added three additive optional per-finding keys, present only when
#:       the caller opts into ``compare(..., contract_evaluation=True)``
#:       (ADR-049 Phase 3's still-non-authoritative shadow evaluator) --
#:       ``contract_relevance`` (one of "IN_CONTRACT"/"UNKNOWN_UNRESOLVED"/
#:       "UNKNOWN_UNPROVEN"/"PROVEN_OUT_OF_CONTRACT"/"NOT_APPLICABLE" --
#:       matching ``ContractRelevance``'s own uppercase ``.value``, unlike
#:       ``contract_assurance``'s lowercase one below),
#:       ``contract_reason_code`` (a stable slug, e.g.
#:       "public_root_membership"/"required_evidence_incomplete"/
#:       "non_entity_finding"), and ``contract_assurance`` (one of
#:       "complete"/"partial"/"unavailable"). Purely additive and
#:       shadow: absent for every existing caller that doesn't pass
#:       ``contract_evaluation=True`` (the default), and never affects
#:       ``verdict``, ``severity``, or any exit code.
#:   2.24: added the optional top-level ``suppression_audit`` object, present
#:       only when the caller opts into ``compare --audit-suppressions``
#:       (``--suppress`` also required). ``total_rules`` (int),
#:       ``stale_rules``/``expired_rules``/``near_expiry_rules`` (arrays of
#:       rule-identifier strings), and ``high_risk_matches`` (array of
#:       ``{"rule": str, "kind": str, "symbol": str | None}``). Purely
#:       additive: absent for every existing caller, and never affects
#:       ``verdict``, ``severity``, or any exit code.
#:   2.25: ADR-049 Phase 3's provider-evidence ledger and Phase 4's persisted
#:       decision context, both present only under ``compare(...,
#:       contract_evaluation=True)`` alongside the 2.23 fields. Per finding:
#:       ``contract_evidence_refs`` (array of strings), the ids of the
#:       ``contract_evidence`` provider records that finding's contract
#:       decision rests on — ``[]`` is a real value, meaning the decision
#:       consulted no provider (a non-entity finding, whose relevance
#:       follows from its ``ChangeKind`` alone). Top-level:
#:       ``contract_context``, the three sibling blocks ADR-049 plan
#:       Section 5.1 persists together — ``contract_evidence`` (observed,
#:       policy-independent provider records plus the raw type graph),
#:       ``evaluation_context`` (the resolved effective configuration and
#:       its field provenance), and ``decision_receipt`` (the evaluated
#:       roots/closure and per-finding relevance). Each block carries its
#:       own version counters, checked independently on read
#:       (``contract_replay.load_replayable_context``). Purely additive and
#:       shadow, same as 2.23: absent by default, and never affects
#:       ``verdict``, ``severity``, or any exit code.
REPORT_SCHEMA_VERSION = "2.25"

#: SemVer-style (MAJOR.MINOR) version of the ``scan`` JSON output, emitted as
#: ``scan_schema_version`` at the top level of both public scan dict shapes:
#: :meth:`abicheck.scan_engine.ScanOutcome.to_dict` (the CLI's
#: ``scan --format json`` contract — mode/level/risk/coverage/diff/verdict/
#: exit_code) and :meth:`abicheck.service_scan.ScanResult.to_dict` (the typed
#: Python/MCP service envelope — verdict/exit_code/findings/layers/confidence/
#: estimate/report, where ``report`` nests the former). Same additive/breaking
#: bump policy as :data:`REPORT_SCHEMA_VERSION` above; independent of it (scan
#: and compare are separate contracts that evolve on their own schedules).
#: 1.0 — initial versioned envelope.
#: 1.1 — added five additive optional top-level keys — ``check_id``,
#:       ``profile_id``, ``requested_depth``, ``effective_depth``,
#:       ``baseline_channel`` — mirroring compare's 2.12 report-identity
#:       envelope (ADR-047 §7, G30 P0.3). Reserved for G30 P1; not yet
#:       populated. Omitted entirely (never emitted as null) when unset.
#: 1.2 — mirrors compare's 2.13 bump: the remaining ADR-047 §7 keys
#:       (``compatibility_verdict``, ``policy_gate_decision``,
#:       ``check_evidence_coverage``, ``operational_errors``,
#:       ``publication``, ``project``, ``head_sha``, ``base_ref``,
#:       ``action_version``, ``tool_version``), populated the same way by
#:       ``actions/check-target`` (G30 P1.3) for a ``scan``-mode audit
#:       check (ADR-047 S5).
#: 1.3 — added the ``NOT_COMPARABLE`` ``verdict``/exit code ``6`` (ADR-050
#:       D2): ``scan --against`` (via ``run_scan_core``'s
#:       ``_run_baseline_compare`` call) now catches a genuine
#:       ``ProfileMismatchError``/``ScopeMismatchError`` instead of letting
#:       it propagate unhandled, setting ``diff.reason`` to the exception
#:       message. Mirrors compare's 2.17 ``verdict: null``/``reason`` bump —
#:       a new possible value a pre-1.3 ``scan`` consumer could never have
#:       received before.
#: 1.4 — added three additive optional keys to the ``diff`` block (ADR-049
#:       Phase 5 §6.4): ``suppressed_count`` and ``suppressed`` (present
#:       only when ``--suppress`` actually silenced at least one finding —
#:       mirrors compare's own ``suppressed_changes`` audit trail, projected
#:       through the same ``_baseline_finding_dicts`` shape the existing
#:       ``findings`` key uses) and ``suppressed_truncated`` (present only
#:       when the suppressed-change count exceeds the same cap
#:       ``findings_truncated`` already uses). Omitted entirely when no
#:       suppression is in effect, matching every other additive scan key.
#: 1.5 — ``scan --artifact-set`` (ADR-056): ``ScanSetResult.to_dict()``
#:       carries this same ``scan_schema_version`` marker but is a
#:       structurally distinct payload shape from ``ScanResult.to_dict()``
#:       (``per_artifact``/``bundle_findings``/``bundle_verdict``/
#:       ``bundle_incomplete`` instead of ``findings``/``layers``/
#:       ``confidence``/``estimate``/``report``) — a pre-1.5 consumer
#:       parsing a fixed ``ScanResult`` shape from this marker could not
#:       have expected the aggregate form. A consumer distinguishes the two
#:       by the presence of ``per_artifact``, not the version number alone.
#:       Bumped to 1.5, not 1.4, because 1.4 was independently claimed by
#:       the ADR-049 Phase 5 suppression-block addition above (a rebase-time
#:       version collision between two structurally distinct additive
#:       changes — reusing 1.4 here would leave a consumer unable to detect
#:       which shape it actually received).
#: 1.6 — the ``diff`` block became comparable field-for-field with
#:       ``compare``'s own report (ADR-049 Phase 5 §6.4's Gate). Every
#:       ``findings``/``suppressed`` entry gained ``finding_id`` — the same
#:       canonical identity ``compare``'s ``changes`` entries carry
#:       (``finding_identity.report_finding_id``), so a consumer can join a
#:       scan finding to its ``compare`` counterpart, or to the same
#:       finding across two runs, instead of relying on array order. The
#:       ``diff`` block also gained an optional ``detectors`` list (same
#:       ``name``/``changes_count``/``enabled``/``coverage_gap`` shape and
#:       same "findings or a coverage gap" filter as ``compare``'s
#:       top-level ``detectors``; omitted when empty). And, only under the
#:       new ``scan --against --contract-evaluation``, each finding
#:       additionally carries ``contract_relevance``/
#:       ``contract_reason_code``/``contract_assurance``/
#:       ``contract_evidence_refs``, under the same "absent means unstamped"
#:       rule ``compare``'s report already follows — so a run without that
#:       flag is byte-identical to 1.5 apart from ``finding_id``.
SCAN_SCHEMA_VERSION = "1.6"

_SCHEMA_DIR = Path(__file__).resolve().parent
COMPARE_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "compare_report.schema.json"
AGGREGATE_REPORT_SCHEMA_PATH = _SCHEMA_DIR / "aggregate_report.schema.json"


@cache
def load_compare_report_schema() -> dict[str, Any]:
    """Return the parsed compare-report JSON Schema as a dict."""
    with COMPARE_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


@cache
def load_aggregate_report_schema() -> dict[str, Any]:
    """Return the parsed aggregate-report JSON Schema as a dict."""
    with AGGREGATE_REPORT_SCHEMA_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


#: Artifact names :func:`current` accepts, each mapped to the module-level
#: constant that already owns its version (ADR-055 D3). Read-only lookup
#: facade -- adding an entry here never changes that constant's own value or
#: bump policy. This frozenset itself is built once at import time; it is
#: :func:`current`'s per-artifact lookups that use function-local imports
#: (not this set), since some of those modules transitively import this
#: package themselves (e.g. ``buildsource.run_plan`` -> ``buildsource.
#: check_report`` -> ``schemas``) and a module-level import here would turn
#: that into a real import cycle.
_ARTIFACT_NAMES = frozenset(
    {"snapshot", "compare", "scan", "aggregate", "build-output", "run-plan"}
)


def current(name: str) -> str | int:
    """Return the current version abicheck emits for persisted artifact *name*.

    One read-only lookup facade over the version constants each artifact's
    own module already owns (ADR-055 D3) -- current-version discovery only;
    this is not a new versioning scheme, and does not add compatibility
    metadata or cross-version lookup. *name* is one of ``"snapshot"``,
    ``"compare"``, ``"scan"``, ``"aggregate"``, ``"build-output"``, or
    ``"run-plan"``.

    A doc generator (or an external integrator) can pull every current
    version number from here instead of a human hand-copying one -- the
    exact failure mode that let ``docs/use/python-api.md`` claim snapshots
    carried ``schema_version 8`` long after the real value reached 17.
    """
    if name not in _ARTIFACT_NAMES:
        raise ValueError(
            f"Unknown schema artifact {name!r}; expected one of "
            f"{sorted(_ARTIFACT_NAMES)}"
        )
    if name == "snapshot":
        from ..serialization import SCHEMA_VERSION

        return SCHEMA_VERSION
    if name == "compare":
        return REPORT_SCHEMA_VERSION
    if name == "scan":
        return SCAN_SCHEMA_VERSION
    if name == "aggregate":
        from ..aggregate import AGGREGATE_SCHEMA_VERSION

        return AGGREGATE_SCHEMA_VERSION
    if name == "build-output":
        from ..buildsource.build_output import BUILD_OUTPUT_SCHEMA

        return BUILD_OUTPUT_SCHEMA
    from ..buildsource.run_plan import RUN_PLAN_SCHEMA

    return RUN_PLAN_SCHEMA


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "SCAN_SCHEMA_VERSION",
    "COMPARE_REPORT_SCHEMA_PATH",
    "AGGREGATE_REPORT_SCHEMA_PATH",
    "load_compare_report_schema",
    "load_aggregate_report_schema",
    "current",
]

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
#:       "complete"/"partial"/"unavailable"). Absent for every existing
#:       caller that doesn't pass ``contract_evaluation=True`` (the
#:       default). These per-finding decisions never affect ``verdict`` or
#:       ``severity``; the sibling contract-coverage ledger added in 2.26
#:       does contribute an orthogonal exit floor (ADR-049 Phase 7).
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
#: 2.26 — added two additive top-level keys, present only under
#:       ``--contract-evaluation`` alongside 2.25's ``contract_context``:
#:       ``contract_coverage_failures`` (ADR-049 plan Section 6.1's sibling
#:       ledger — one entry per provider/domain coverage failure *for the
#:       selected contract domain*, each with ``provider``/``side``/
#:       ``record_id``/``reason``/``status``/``completeness``/``mode`` and a
#:       ``suppressible: false`` marker) and
#:       ``contract_coverage_exit_contribution`` (``0`` or ``1``). ``[]`` is a
#:       real value meaning "this domain closed", so the key is emitted
#:       rather than omitted. The ledger is **derived** from
#:       ``contract_context``, not a second observation: the same provider
#:       record is a failure under one domain and advisory under another
#:       (Section 7), so it is answered per selected mode. Unlike 2.23/2.25,
#:       this one is not purely advisory: ADR-049 Phase 7 applies the
#:       contribution, so ``contract_coverage_exit_contribution`` is the
#:       number that actually gated the run (folded with ``max``, and ``0``
#:       when ``contract.unresolved=warn`` accepted the failures below it).
#: 2.27 — ADR-049 D1's canonical per-finding shape completes, alongside the
#:       contract decision becoming *authoritative* (Phase 7): every entry
#:       that already carries ``contract_relevance`` now also carries
#:       ``compatibility_evaluation_status`` (``EVALUATED``/``NOT_EVALUATED``),
#:       ``compatibility_decision`` (the finding's own ``Verdict``, or JSON
#:       ``null`` when policy did not run — ``null`` is not a sixth verdict)
#:       and ``gate_contribution`` (what the finding actually contributed to
#:       the exit code; ``0`` for a ``NOT_EVALUATED`` finding and for every
#:       audit-ledger entry, none of which reach a gate). Additive in shape,
#:       but unlike 2.23/2.25 not advisory in effect: under
#:       ``--contract-evaluation``, a ``NOT_EVALUATED`` finding no longer
#:       contributes to ``verdict``, ``severity``, or the exit code, and the
#:       four ``summary`` counts are over the evaluated findings only. Absent,
#:       and bit-for-bit unchanged, for a run that did not opt in.
#: 2.28 — ``correlated_change_kind`` (already-published since ADR-041, one
#:       producer: ``public_api_internal_dependency_added``) gains a second,
#:       independent producer: a ``layout_unverifiable`` finding that shares
#:       the identical asymmetric-layout-evidence gap as a co-reported
#:       ``type_vtable_changed`` finding on the same type is now also
#:       annotated with it (always the literal string
#:       ``"type_vtable_changed"`` for this producer). Purely additive --
#:       the field's shape and existing ``public_api_internal_dependency_added``
#:       meaning are unchanged, and a consumer already reading it as an
#:       opaque ``ChangeKind`` slug (rather than assuming which kind pairing
#:       produced it) needs no change. Bumped rather than left silent because
#:       the field's schema description named one specific producer/kind
#:       pairing, which a strict consumer could have relied on.
#: 2.29 — G29 Phase 6 follow-up: wires the already-implemented
#:       ``RootCauseCorrelator`` (``abicheck.impact.correlation.
#:       correlate_root_causes``) into the JSON/SARIF ``impact_assessment``
#:       surface. Adds ``change.impact_assessment.root_cause_evidence``
#:       (this finding's own evidence rank plus the whole correlator
#:       group's ``strongest_evidence_level``/``evidence_levels``), present
#:       only for a finding that is a member of one of the correlator's
#:       multi-piece groups -- the four load-failure kinds named in that
#:       module's docstring -- and, under ``--report-mode root-cause``,
#:       ``root_causes[].strongest_evidence_level``/``evidence_levels`` for
#:       the same groups. Purely additive: absent for every other finding
#:       and group, unconditional on report_mode (mirroring root_cause_id/
#:       impact_group_id's own precedent), and never affects ``verdict``,
#:       ``severity``, or any exit code.
#: 2.30 — new optional ``policy_reclassify`` array (A: selector-scoped
#:       reclassification, ``abicheck/reclassify.py``): when the active
#:       policy file carries one or more ``reclassify:`` rules, each is
#:       listed (``kind``, whichever selector fields it set, ``to``,
#:       ``reason``, ``label``, ``expires``) alongside the existing
#:       ``policy_overrides``/
#:       ``policy_file`` keys (Codex review: an ordinary comparison
#:       reclassifying a finding had no trace of the active rule anywhere in
#:       the standard report). Lists the *active rule set*, matching the
#:       level of detail ``policy_overrides`` already provides for kind-
#:       global overrides -- not a per-finding "which rule fired" attribution
#:       (that needs a new per-``Change`` field and is tracked separately as
#:       a deliberately deferred follow-up, same as the ``--contract-
#:       evaluation`` receipt gap). Absent, and the report byte-identical,
#:       when no ``reclassify:`` rule is configured.
#: 2.31 — the per-``Change`` ``reclassified_by`` field 2.30's own note
#:       tracked as a deferred follow-up (Codex review, PR #733): a
#:       ``change`` entry now carries ``reclassified_by`` (the deciding
#:       ``reclassify:`` rule's ``label``/``reason``/``to`` spelling, first
#:       one set) when a selector-scoped rule -- not a kind-global
#:       ``overrides:`` entry -- actually decided its effective verdict
#:       (:func:`abicheck.severity.reclassify_rule_for_change`). Motivated by
#:       ``cli_pr_comment``: its ``_reclassified_count()`` only recognized
#:       ``policy_overrides``' kind-keyed map, so a finding downgraded by a
#:       selector-scoped rule silently bypassed the PR comment's "🔀 N
#:       findings reclassified by --policy-file" disclosure and read as an
#:       unremarked safe change. Purely additive and per-finding-opt-in:
#:       absent for every change not actually decided by a ``reclassify:``
#:       rule, which is every report without one, and never affects
#:       ``verdict``, ``severity``, or any exit code.
#: 2.32 — added the optional per-finding ``symbol_binding`` key: the removed
#:       symbol's ELF linkage (``"global"``/``"weak"``/``"local"``/
#:       ``"unique"``/``"other"``), present only on ``func_removed``/
#:       ``func_removed_elf_only``/``var_removed``/``func_deleted_elf_fallback``
#:       findings whose binding was captured. Lets a JSON/SARIF consumer tell
#:       a WEAK-COMDAT removal apart from a GLOBAL/strong export's removal —
#:       the structured counterpart to the new ``binding:`` suppression
#:       selector (see ``suppression.py``'s ``Suppression.binding``).
#:       Additive optional key (Codex review; renumbered from a conflicting
#:       2.29/2.30 when the PR #735/#733 rebase claimed those versions
#:       first).
#: 2.33 — the same optional ``symbol_binding`` key (see 2.32) is now also
#:       propagated onto ``suppression.suppressed_changes[]`` entries, not
#:       just the top-level ``changes[]``/``root_causes[]`` array -- a
#:       separate projector (``reporter._suppressed_change_entry``) that
#:       the 2.32 fix didn't reach, so a binding-scoped suppression's audit
#:       trail couldn't show why it matched. Additive optional key (Codex
#:       review, fresh evidence).
#: 2.34 — ``policy_reclassify[]`` items gain the optional ``binding`` key,
#:       matching ``reclassify.ReclassifyRule.to_report_dict()``'s new
#:       ``binding`` selector output (the ``reclassify:`` rule form's own
#:       ``binding`` selector, wired through in this same change). The
#:       schema's ``policy_reclassify`` item previously set
#:       ``additionalProperties: false`` without listing this key, so valid
#:       output using the feature failed validation against the bundled
#:       schema. Additive optional key (Codex review, fresh evidence).
#: 2.35 — ``verdict`` gains a third sentinel value, ``"NEW_TARGET"``
#:       (``check_report.NEW_TARGET_VERDICT``), and
#:       ``check_evidence_coverage.state`` gains ``"new_target"`` --
#:       ``actions/check-target``'s new ``allow-new-target``-opted-in
#:       report shape for a target genuinely absent from an otherwise-
#:       resolved baseline-set (e.g. a new library's first release). A new
#:       optional ``baseline_new_target`` boolean, mirroring the existing
#:       ``baseline_bootstrap``, is set ``true`` only on that report.
#:       Additive: new enum members plus one new optional key, same shape
#:       as ``NO_BASELINE``/``baseline_bootstrap``'s own 2.13 addition
#:       (Codex review, fresh evidence).
#: 2.36 — every ``changes[]``/``root_causes[]``/audit-ledger finding entry
#:       (``out_of_surface_changes``, ``suppression.suppressed_changes``,
#:       reconciled/filtered-internal entries) gains ``canonical_finding_id``
#:       alongside the existing ``finding_id``. Unlike ``finding_id`` (which
#:       folds in ``source_location``/``description`` to disambiguate two
#:       same-kind findings on one symbol, fields two header backends are
#:       not guaranteed to spell identically), this sibling is
#:       ``finding_identity.resolve_change_identity()``'s producer-agnostic
#:       ``primary_id`` — stable across a ``--ast-frontend castxml`` vs.
#:       ``--ast-frontend clang`` switch on the same underlying change, so a
#:       ``--suppress`` rule written against one header backend's report can
#:       reliably match the equivalent finding in the other's (the new
#:       ``finding_id:`` suppression selector — see ``suppression.py``'s
#:       ``Suppression.finding_id``). Additive optional key. Renumbered from
#:       a conflicting 2.35 when the PR #755 rebase claimed that version
#:       first (same "renumber, don't reuse" convention as the 2.32 entry
#:       above).
#: 2.37 — each ``layer_coverage[]`` row (the L3_build row in particular)
#:       gains five optional keys: ``requested_roots``/``resolved_roots``
#:       (the P0.2 Bazel root-target(s) declared via ``dump --build-target``/
#:       ``.abicheck.yml``'s ``build.targets``, and the subset a query
#:       actually resolved), ``transitive_targets`` (the scoped dependency
#:       closure size), and ``compile_units``/``link_units`` (machine-
#:       readable counterparts to what ``detail`` already states in prose).
#:       All five are present-but-empty/``null`` on every row unaffected by
#:       root-target scoping (every report before this feature, and every
#:       non-L3_build row), so no pre-existing consumer's parsing breaks.
#:       Additive optional keys.
#: 2.38 — new top-level ``analysis_assurance`` object (P0.4,
#:       ``analysis_assurance.py``): the third leg of the
#:       compatibility-verdict / analysis-assurance / policy-gate split,
#:       answering "how complete and trustworthy was the evidence" as its
#:       own axis, orthogonal to ``verdict``. Unconditionally present (every
#:       ``checker.compare()`` call populates it) rather than gated behind a
#:       flag, unlike ``contract_context`` -- see that module's own
#:       docstring. Additive top-level key, versioned internally via its own
#:       ``analysis_assurance.schema_version`` so a consumer can version-check
#:       the sub-object without caring about this report schema's own
#:       MAJOR.MINOR. Renumbered from a conflicting 2.37 when the origin/main
#:       rebase claimed that version first for P0.2's ``layer_coverage``
#:       root-target keys (same "renumber, don't reuse" convention as the
#:       2.32/2.36 entries above).
#: 2.39 — new top-level ``use_case_impact`` object, present only under
#:       ``compare --use-cases MANIFEST`` (``impact/use_case_impact.py``):
#:       the manifest's declared use cases, each one's resolved and
#:       unresolved entrypoints, and this comparison's own findings
#:       attributed to the use cases whose entrypoints can be *shown* to
#:       reach them, plus an ``unattributed_changes`` count for the
#:       remainder. Read-only evidence -- it never reaches a verdict, a
#:       gate, or an exit code, because an unattributed finding is an
#:       absence of proof rather than proof the finding is harmless. Omitted
#:       entirely without the flag rather than emitted empty (an empty block
#:       would read as "no use case is affected" for a run that never
#:       resolved a manifest). Replaces the ``project validate-use-cases
#:       --against/--against-new`` report, which diffed two snapshots inside
#:       a manifest validator. Each attributed row carries the report's own
#:       ``finding_id`` alongside ``symbol``/``kind``, since those two do not
#:       identify a finding (one symbol can emit one ``ChangeKind`` more than
#:       once), so a row joins back to ``changes``/``findings``. Under
#:       ``--show-only`` the block is projected onto the displayed findings,
#:       so it never names a change the report itself omits. Additive
#:       optional top-level key.
#: 2.40 -- new top-level ``analysis_assurance_exit_contribution`` (``0``/``1``),
#:       the exact sibling of the pre-existing ``contract_coverage_exit_
#:       contribution`` for P0.4's own orthogonal analysis-assurance axis.
#:       Persisted alongside ``analysis_assurance`` (present whenever a real
#:       ``AnalysisAssurance`` is attached to the result -- in practice every
#:       real ``compare()`` call) rather than unconditionally: read directly
#:       by ``aggregate.py``'s ``_analysis_assurance_exit`` the same way the
#:       coverage sibling already is, since without it a report whose
#:       severity/compatibility gate read a clean 0 while this axis
#:       independently floored the *real* exit to 1 fed ``abicheck aggregate``
#:       a green result for it (Codex review). Additive key.
#: 2.41 -- new top-level ``exit`` object (CLI cleanup phase two, PR G1 --
#:       ``exit_decision.ExitDecision``): ``code``, ``reasons`` (which
#:       orthogonal axis or axes actually determined ``code`` -- a
#:       lower, non-winning contribution is excluded, since it explains
#:       nothing about why the exit is what it is),
#:       ``compatibility_contribution``, ``contract_coverage_contribution``,
#:       and ``analysis_assurance_contribution``. The exact number
#:       ``cli._exit_with_severity_or_verdict`` computes for the real
#:       process exit, persisted so a report reader does not have to
#:       re-derive it from ``severity.exit_code``/
#:       ``contract_coverage_exit_contribution``/
#:       ``analysis_assurance_exit_contribution`` independently and risk
#:       disagreeing with the number that actually gated the run.
#:       Emitted by every native ``compare`` JSON report -- every comparison
#:       has a compatibility contribution, so there is always a decision to
#:       report, unlike ``contract_context`` which stays opt-in -- but is
#:       schema-optional, not required: an
#:       ``include_exit_decision=False`` caller (``compat check``'s own
#:       ABICC 0/1/2 exit scheme differs from native ``compare``'s, so its
#:       report omits this block rather than emit a ``code`` that would
#:       disagree with the real process exit for the same run -- Codex
#:       review) still validates against schema 2.41 with the key absent.
#:       ``scan --against`` emits the identical block too, since schema 1.18
#:       (CLI cleanup phase two, PR E) -- nested at ``diff.exit``, not this
#:       top-level key, matching where its own constituent contribution
#:       fields already live; see ``SCAN_SCHEMA_VERSION``'s own 1.18 entry.
#:       Deliberately does not yet cover ``not_comparable``/scan-budget/
#:       release-removed-library exits, which are raised through different
#:       code paths today; see ``exit_decision.py``'s own module docstring.
#:       Additive key.
#: 2.42 -- the ``exit`` object gains ``crosscheck_promotion_contribution``
#:       (CLI cleanup phase two, PR E follow-up, Codex review): a fourth
#:       axis alongside the three schema 2.41 introduced, always ``0`` for
#:       a native ``compare`` report (this axis has no meaning outside
#:       ``scan --against``'s own maintainer-promoted ``--crosscheck
#:       KEY=error`` finding -- see ``SCAN_SCHEMA_VERSION``'s own 1.18
#:       entry). Added so ``code == max(`` the four contributions ``)``
#:       stays true for every ``exit`` block this package emits, on both
#:       commands, rather than holding only for ``compare``'s three-axis
#:       case. ``reasons`` may now also contain ``"promoted_crosscheck"``.
#:       Additive key.
#: 2.43 -- new top-level ``annotations`` array (CLI cleanup phase two, PR
#:       E's persistence prerequisite -- ``reporter_contract_blocks.
#:       add_annotations``/``annotations.annotation_report_entries``): one
#:       ``{"level": "error"|"warning"|"notice", "annotation": <the exact
#:       GitHub workflow-command line ``compare --annotate`` would emit>}``
#:       entry per finding a full annotation pass over this comparison
#:       found -- always the *superset* (as if ``--annotate-additions`` had
#:       also been given), regardless of whether this run itself was given
#:       ``--annotate``. Exists so a rendering front end (the composite
#:       Action) can read an already-classified, already-formatted answer
#:       instead of inferring one from stderr or re-running the comparison
#:       -- the plan's own "New invariant" for this PR, already true for
#:       ``exit``/``analysis_assurance`` and now true for annotations too.
#:       A consumer filters out ``"notice"``-level entries itself when its
#:       own ``annotate-additions`` input is off, rather than this package
#:       persisting two differently-scoped arrays for one comparison.
#:       Unconditionally present (mirroring ``exit``'s own presence rule),
#:       including as ``[]`` on a clean comparison. Additive key.
#: 2.44 -- each ``annotations`` entry gains ``always_visible`` (bool)
#:       (Codex review on PR E's own persistence prerequisite): 2.43's own
#:       "filter out notice-level entries when annotate-additions is off"
#:       guidance was incomplete -- one ``"notice"`` kind (a ``--contract``
#:       finding compatibility policy never evaluated,
#:       ``annotations._collect_annotations_detailed``'s not-evaluated
#:       demotion) is surfaced by plain ``--annotate`` alone, with no
#:       ``--annotate-additions`` needed, so a renderer dropping every
#:       ``"notice"`` by default would silently hide it even though the
#:       CLI's own stderr rendering never does. ``always_visible`` is what a
#:       renderer must actually gate a ``"notice"`` entry on instead of the
#:       level alone; it is always ``True`` for ``"error"``/``"warning"``.
#:       Additive key on an existing array item.
#: 2.45 -- CLI cleanup phase two, PR B: two new top-level keys,
#:       ``effective_config_digest`` (a ``sha256:...`` fingerprint) and
#:       ``effective_config_fields`` (the field dict it was hashed from, so
#:       a mismatch can be attributed to a specific field rather than read
#:       as an opaque hash -- mirrors the existing ``profile_fingerprint``/
#:       ``scope_fingerprint`` precedent in ``comparability.py``). Computed
#:       by ``effective_config_digest.effective_config_fields`` from
#:       whichever tier of resolved configuration this comparison actually
#:       has (a full ``CompatibilityEvaluationConfig`` under ``--contract``/
#:       ``--pack``, else the policy/gate fields every comparison resolves
#:       regardless -- see that module's own docstring). Identical
#:       computation for `compare` and the directory/package release
#:       fan-out (both funnel through ``reporter_contract_blocks.
#:       add_contract_context``) and for `scan --against` (schema 1.19,
#:       below) -- "one effective configuration ... with the same
#:       effective-config digest recorded in every report" (the plan's own
#:       still-open PR B goal). Unconditional, like ``exit`` conceptually
#:       is: every comparison has a resolved configuration to fingerprint.
#:       Additive keys.
#: 2.46 -- ``effective_config_fields`` (2.45, above) gains
#:       ``gate.require_complete_analysis`` (Codex review, PR #803, fresh
#:       evidence): ``--require-complete-analysis`` genuinely changes gating
#:       behavior for an otherwise-identical incomplete-evidence result (its
#:       ``analysis_assurance_exit_contribution`` floors to 1 vs. 0) but is
#:       not a D7 ``CompatibilityEvaluationConfig`` namespace field, so two
#:       reports differing only in this flag previously collided on the
#:       digest. Threaded through the same way ``severity_config``/
#:       ``exit_code_scheme`` already are. Also: the directory/package
#:       release fan-out's ``--output-dir`` sibling document
#:       (``summary.json``, written by ``cli_compare_release.
#:       _write_release_summary_file``) now carries both effective-config
#:       fields too -- previously only the primary release JSON and the
#:       optional per-library sidecar files did, so this write path alone
#:       was silently missing the "same digest in every report" invariant.
#:       Additive key/keys, on an existing report shape shared by every
#:       front end. Two more fixes landed under this same still-unreleased
#:       version: the baseline tier's ``policy.base`` now carries a
#:       recognized built-in policy's full ``id@version:sha256`` identity
#:       (matching the rich tier's own fix, one review round earlier) rather
#:       than the bare name; and ``effective_config_digest``/
#:       ``effective_config_fields`` are now schema-optional (removed from
#:       the real-verdict branch's ``then.required``) and omitted from
#:       ``compat check --report-format json`` output (``include_exit_
#:       decision=False``), mirroring the pre-existing ``exit`` block's own
#:       optional status -- this digest's gate axes describe only the
#:       native legacy/severity scheme and don't represent compat's own
#:       transform options (``-strict``, ``-source``/``-binary``, ...), so
#:       emitting it there would let two behaviorally different compat
#:       reports claim an identical effective configuration. Two more
#:       fixes landed in a further review round: ``effective_config_
#:       fields`` gains ``gate.scope`` (ADR-043 ``--used-by``/
#:       ``--required-symbol(s)`` scoped-gate selection, which can replace
#:       the reported verdict/findings/exit code but was previously
#:       unrepresented -- two runs selecting different consumers/
#:       entrypoints against the identical pair collided on the digest);
#:       and the rich tier's ``gate.exit_code_scheme``/``gate.severity.*``
#:       now always come from the caller's own already-resolved severity/
#:       exit-code-scheme (the same pair used for the sibling ``exit``
#:       block) rather than from the resolved
#:       ``CompatibilityEvaluationConfig`` directly -- closing a real bug
#:       where a ``--pack``-only ``scan --against`` recorded its digest
#:       from ``resolve_scan_config``'s deliberately gate-blanked config
#:       instead of the run's real ``--severity-preset``/
#:       ``--exit-code-scheme``. Three more fixes landed in further review
#:       rounds under this same still-unreleased version: ``surface.
#:       explicit_scope`` now folds in ``--post-manifest``'s resolved
#:       ``public_surface_allowlist`` (a second, independent explicit-scope
#:       axis alongside ``--public-symbols-list``, keyed separately so
#:       neither can collide with the other), gated on ``is not None``
#:       rather than truthiness (an empty ``--post-manifest`` allowlist is
#:       a real, distinct, active scope, not the absence of one); the rich
#:       tier *merges* that same result-level scope digest with
#:       ``resolved_config.surface.explicit_scope`` rather than falling
#:       back to only one, since ``force_public_symbols`` is threaded into
#:       ``compare()`` unconditionally and a single ``--pack``-only run
#:       combining ``--public-symbols-list`` and ``--post-manifest`` can
#:       populate both sources at once; and ``effective_config_fields``
#:       gains ``policy.pattern_verdicts`` (ADR-027 A4's ``--pattern-
#:       verdicts``/``--explain-patterns``, which can modulate a finding's
#:       verdict and the process exit but was previously unrepresented --
#:       two otherwise-identical runs differing only in this flag collided
#:       on the digest whenever no idiom/antipattern happened to match,
#:       since the applied-modulation ledger alone is indistinguishable
#:       from the flag never having been set). One more fix landed in the
#:       same further review round: ``effective_config_fields`` gains
#:       ``policy.collapse_versioned_symbols`` (``compare(...,
#:       collapse_versioned_symbols=...)``, which can remove a versioned
#:       symbol-version remove/add pair entirely, turning an otherwise
#:       ``BREAKING`` verdict non-breaking), ``policy.surface_metrics``
#:       (``--surface-metrics``, which appends suppressible aggregate-drift
#:       findings and can flip ``NO_CHANGE`` to ``COMPATIBLE``), and
#:       ``policy.env_matrix`` (a content digest of the resolved
#:       ``--env-matrix``, ADR-020b: ``_env_matrix_contract_changes`` can
#:       reclassify a version-requirement finding against declared runtime
#:       floors and add deployment findings) -- three more checker-level
#:       axes that were previously unrepresented in the digest, all
#:       following the identical shape ``policy.pattern_verdicts`` already
#:       established. Additive keys. One more fix, same further review
#:       round: ``policy.reconcile_build_context`` (``compare(...,
#:       reconcile_build_context=...)``, ``--reconcile-build-context``,
#:       which can move a phantom breaking finding from ``kept`` into the
#:       reconciliation audit bucket, changing the verdict and exit code)
#:       -- same shape again. One more fix, same round: ``effective_config_
#:       fields`` gains ``surface.scope_to_public_surface_requested`` --
#:       distinct from the existing ``surface.scope_to_public_surface``,
#:       which ``checker.compare()`` stamps with the *derived*
#:       ``scope_active = scope_to_public_surface or public_surface_
#:       allowlist is not None`` and therefore reads ``True`` whenever a
#:       ``--post-manifest`` allowlist is active regardless of the raw
#:       flag. ``post_processing.FilterNonPublicSurface._run_allowlist``
#:       only honors the ``force_public_symbols`` widening overlay when the
#:       *raw* flag is true, so two runs sharing the same POST manifest and
#:       forced-public symbols but opposite raw ``--scope-public-headers``
#:       settings previously published an identical
#:       ``surface.scope_to_public_surface`` value despite retaining
#:       genuinely different findings.
#: 2.48 -- ADR-063 Phase 7: every JSON report gains an additive top-level
#:       ``run_outcome`` block (``policy.outcome.RunOutcome.to_dict()`` --
#:       ``compatibility``/``assurance``/``gate``/``operational``/
#:       ``lifecycle``), alongside the unchanged ``verdict``/``exit_code``/
#:       ``severity`` fields.
#: 2.49 -- G42 phase 1: ``check_id`` gains two optional tail segments,
#:       ``!<environment_id>`` and ``~<explicit_id>``. Renumbered from a
#:       conflicting 2.48 (origin/main claimed it for the ``run_outcome`` block).
REPORT_SCHEMA_VERSION = "2.50"  #: ADR-063 Track T3 (Codex review, PR #1078, twenty-fourth round): ``finding_id``'s documented algorithm gains a seventh, conditional input -- ``Change.disambiguator`` (never itself a reported field) -- appended only for a typedef/constant occurrence-level finding needing collision disambiguation. Every other finding's id is unchanged; the schema's own description was updated to match (previously undocumented since the seventeenth round introduced the conditional append).

#: SemVer-style (MAJOR.MINOR) version of the ``scan`` JSON output, emitted as
#: ``scan_schema_version`` at the top level of both public scan dict shapes:
#: :meth:`abicheck.scan_engine.ScanOutcome.to_dict` (the CLI's
#: ``scan --format json`` contract — mode/level/risk/coverage/diff/verdict/
#: exit_code) and :meth:`abicheck.service_scan.ScanResult.to_dict` (the typed
#: Python service envelope — verdict/exit_code/findings/layers/confidence/
#: estimate/report, where ``report`` nests the former). Same additive/breaking
#: bump policy as :data:`REPORT_SCHEMA_VERSION` above; independent of it (scan
#: and compare are separate contracts that evolve on their own schedules).
#: Adding a top-level ``diff``-block key: update this version's history entry
#: below AND ``cli_scan_baseline.py``'s ``_baseline_summary`` docstring AND
#: ``tests/test_cli_scan_baseline.py``'s ``TestBaselineSummaryKeysArePinned``
#: (whose own failure is what makes an unversioned new key discoverable at
#: all — see PR #724, where the two truncation keys shipped one commit ahead
#: of this bump).
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
#:       rule ``compare``'s report already follows. So a run *without*
#:       ``--contract-evaluation`` differs from a 1.5 one in exactly three
#:       places: this version marker itself, the new ``finding_id`` on each
#:       finding, and the ``detectors`` list — which, unlike the contract
#:       keys, does not depend on that flag (CodeRabbit review: the earlier
#:       wording claimed byte-identity apart from ``finding_id``, which
#:       understated both).
#: 1.7 — the ``diff`` block gained three keys under ``scan --against
#:       --contract-evaluation`` (ADR-049 Phase 5): ``contract_context``
#:       (the same three-block sibling group ``compare``'s report carries at
#:       its top level, serialized by the same encoder, so a scan receipt
#:       replays identically), plus the sibling
#:       ``contract_coverage_failures``/``contract_coverage_exit_contribution``
#:       ledger (report schema 2.26's equivalents). Before this the scan
#:       *computed* a contract context — its findings were stamped from one —
#:       and then dropped it, so the evidence those decisions rest on was
#:       unobservable from a scan. All three are absent without the flag, so
#:       an ordinary scan is unchanged from 1.6.
#: 1.8 — the ``diff`` block gained a ``not_evaluated`` count and a matching
#:       ``not_evaluated`` bucket in ``findings``, under ``scan --against
#:       --contract-evaluation`` (ADR-049 Phase 7). The four compatibility
#:       counts became the *evaluated* findings only when relevance became
#:       authoritative, and this summary itemizes findings from those buckets
#:       alone — so without this key an excluded fact vanished from the scan
#:       report entirely rather than merely stopping gating, which is the one
#:       outcome D9's "exactly one visible outcome" forbids. Each entry
#:       carries the same relevance/reason fields 1.6 added, which is what
#:       says why it did not gate. Both are absent when nothing was excluded,
#:       so an ordinary scan is unchanged from 1.7.
#:       Every ``findings`` row under ``--contract-evaluation`` also gains
#:       ADR-049 D1's ``compatibility_evaluation_status`` and
#:       ``compatibility_decision`` (``null`` for a ``NOT_EVALUATED`` row),
#:       matching what the equivalent ``compare`` finding already carries --
#:       section 6.4 requires the two commands to be comparable field by
#:       field, and a row stating only the relevance was not (Codex review).
#:       Not ``gate_contribution``: that is a property of a severity gate
#:       ``scan --against`` did not run at the time. Absent without the
#:       opt-in, so an ordinary scan is still unchanged. (1.9 gave ``scan
#:       --against`` a real severity gate; ``gate_contribution`` is still
#:       not emitted per finding, which stays a deliberate follow-up.)
#: 1.9 — the ``diff`` block gained a ``severity`` gate object (the same shape
#:       ``compare``'s own report carries: ``config``/``categories``/
#:       ``exit_code``/``blocking``/``blocking_categories``, built by the one
#:       shared ``reporter._build_severity_json``), emitted when ``scan
#:       --against`` resolves the ``severity`` exit-code scheme. Under that
#:       scheme a *compatible* diff can exit non-zero (``severity.addition: error`` on an additions-only diff exits 1), so without this block a
#:       report read ``COMPATIBLE`` with exit 1 and no stated cause --
#:       indistinguishable from ADR-049 Phase 7's orthogonal
#:       contract-coverage 1 (Codex review). Absent under the default legacy
#:       scheme, where there is no gate to report, so an ordinary scan is
#:       unchanged from 1.8.
#:       The block is the scan's *compatibility* gate, not the baseline diff's
#:       alone: a ``--crosscheck KEY=error`` promotion raises it (with a
#:       ``promoted_crosscheck`` entry in ``blocking_categories``, deliberately
#:       outside ``IssueCategory``'s vocabulary since no severity level
#:       produced it -- the same shape as ``aggregate``'s own
#:       ``operational_error``/``not_comparable``). Without that the block
#:       claimed a passing gate while the process exited 2, and `aggregate`
#:       -- which prefers this block -- dropped the gated target from
#:       ``blocking_targets`` (Codex review). It never lowers the gate: the
#:       promotion is a floor.
#: 1.10 -- the ``diff`` block's ``findings``/``suppressed`` truncation now
#:        carries a per-kind breakdown: ``findings_truncated_kinds``/
#:        ``suppressed_truncated_kinds`` (``ChangeKind -> count`` of what was
#:        cut), alongside the existing ``findings_truncated``/
#:        ``suppressed_truncated`` booleans. Both are new, additive keys,
#:        present only when the corresponding list was actually truncated --
#:        an ordinary (untruncated) scan is unchanged from 1.9. The
#:        ``findings``/``suppressed`` truncation cap itself also became
#:        configurable (``scan --max-findings`` / ``ABICHECK_MAX_BASELINE_
#:        FINDINGS``, default still 20), which does not change the schema,
#:        only how much of a diff a given run itemizes.
#: 1.11 -- ``_baseline_finding_dicts`` entries gain the optional
#:        ``symbol_binding`` key (the removed symbol's ELF linkage), mirroring
#:        the compare-report schema's 2.32 addition -- ``scan --against``
#:        shares the same ``--suppress`` surface as ``compare``, so a
#:        binding-scoped suppression's match/no-match needs to be auditable
#:        here too. Additive optional key, present only when captured.
#: 1.12 -- the top-level ``preprocessor_scan`` block gained an additive
#:        ``probes_truncated`` int (``PreprocessorScanResult``'s own
#:        ``PREPROCESSOR_SCAN_VERSION`` bumped 1 -> 2 in lockstep — see that
#:        module for the fact-shape rationale): compile units / public
#:        headers skipped by the new ``ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES``
#:        cap (perf fix for ``scan --depth build``'s S2 preprocessor
#:        pre-scan — parallelized, but deliberately NOT deduped by compile
#:        context, since flag-based dedup could silently hide a real
#:        per-TU macro divergence; see that module's own docstring). ``0``
#:        when nothing was truncated, so an ordinary scan is unchanged from
#:        1.11.
#: 1.13 -- the ``diff`` block gains an always-on ``additions`` array (plus
#:        optional ``additions_truncated``/``additions_total`` fields): the
#:        addition-shaped subset of ``diff.compatible`` (new public-API
#:        surface -- ``ChangeKind``'s ``ADDITION_KINDS``), itemized the same
#:        shape as ``findings`` (``bucket: "compatible"``) regardless of
#:        whether severity policy made any of them blocking. Previously
#:        ``diff.compatible`` contributed only its bare count unless
#:        promoted to blocking by ``--severity-addition error`` (see 1.9's
#:        ``severity`` block) -- with no way to render *what* was added, a
#:        ``scan --against`` PR comment (``pr_comment_scan.from_scan``) had no
#:        source for a green "public API additions" section the way
#:        ``compare``'s own JSON report already supports via its full
#:        ``changes`` list. Capped independently of ``findings``' own budget
#:        (same ``--max-findings``/``ABICHECK_MAX_BASELINE_FINDINGS`` cap),
#:        so a large addition set can never crowd out a real gating finding.
#:        ``additions_total`` is the exact, untruncated addition count
#:        (Codex review) -- emitted alongside ``additions_truncated`` only
#:        when ``additions`` was itself capped, since ``diff.compatible``'s
#:        own scalar mixes additions and quality findings and so cannot
#:        answer "how many additions, exactly" on its own; the PR comment's
#:        header count needs this to stay exact under truncation. All three
#:        keys are additive and omitted when ``diff.compatible`` has no
#:        addition-shaped entry, so an ordinary scan with no new public API
#:        is unchanged from 1.12. The ``diff`` block also gains ``policy``
#:        (Codex review): the resolved compatibility policy name
#:        (``DiffResult.policy``, e.g. ``"strict_abi"``) that classified
#:        these buckets, the same fact ``compare``'s own JSON report has
#:        always carried at its top level -- without it, ``scan
#:        --against``'s sticky PR comment (``pr_comment_scan.from_scan``)
#:        had no way to report which policy actually gated a non-default
#:        ``--policy`` run and silently rendered the ``"strict_abi"``
#:        fallback regardless. Additive and always present alongside the
#:        other ``diff`` fields on a real comparison (only absent for the
#:        ``NOT_COMPARABLE``/audit-only ``diff`` shapes, which never reach
#:        policy classification at all). The ``diff`` block also gains
#:        ``quality`` (plus optional ``quality_truncated``/``quality_total``
#:        fields), ``additions``'s exact complement: the compatible-but-
#:        non-addition subset of ``diff.compatible`` (a quality-category
#:        change like ``func_noexcept_added``, or a policy-demoted removal
#:        reclassified compatible), itemized the same shape and under the
#:        same cap as ``additions`` (Codex review, follow-up). Without it, a
#:        scan whose only compatible findings were quality-shaped had an
#:        exact "N safe" header count (once the count itself was corrected
#:        to read the full ``diff.compatible`` scalar) with nothing
#:        itemized underneath it to explain what those N findings were.
#: 1.14 -- upstream ask #2 (Codex review): ``scan --format json`` had no
#:        policy-audit disclosure at all -- a reviewer saw a downgraded
#:        verdict with no way to tell which rule produced it, unlike the
#:        ``compare``/report path (``reporter._add_policy_overrides``,
#:        report_schema_version 2.30/2.31). The ``diff`` block gains the
#:        identical two top-level keys, byte-for-byte the same shape
#:        (``ReclassifyRule.to_report_dict()`` encoding, shared with
#:        ``reporter.py``/``sarif.py`` so the three can't drift): optional
#:        ``policy_overrides`` (kind -> verdict, from ``PolicyFile.overrides``)
#:        and ``policy_reclassify`` (the active, non-expired
#:        ``PolicyFile.reclassify`` rule set), plus ``policy_file`` (the
#:        source path) when either is present. Each finding dict
#:        (``_baseline_finding_dicts``, ``findings``/``additions``/
#:        ``quality``/``suppressed``) also gains the optional
#:        ``reclassified_by`` key -- the same per-finding audit value
#:        ``compare``'s ``change.reclassified_by`` (report_schema_version
#:        2.31) already stamps, computed by the identical
#:        ``reporter._reclassified_by_for_change`` helper so the two commands
#:        can't disagree about which rule decided a shared finding. All keys
#:        are additive and omitted when no policy file (or no active rule)
#:        applies, so an ordinary scan is unchanged from 1.13.
#: 1.15 -- ``_baseline_finding_dicts`` entries gain ``canonical_finding_id``
#:        alongside the existing ``finding_id`` (see ``REPORT_SCHEMA_VERSION``
#:        2.36's identical addition to ``compare``'s ``changes[]`` -- the two
#:        bump together since ``finding_id``/``canonical_finding_id`` are
#:        joinable across both report shapes by design). Additive key.
#: 1.16 -- P0.4: the ``diff`` block gains ``analysis_assurance`` (the same
#:        shape ``compare``'s own top-level ``analysis_assurance`` key
#:        already carries, via the identical ``analysis_assurance_report_
#:        dict`` narrowing helper), always present on a real ``--against``
#:        comparison regardless of whether ``--require-complete-analysis``
#:        was passed (Codex review: a consumer dispatching on
#:        ``scan_schema_version`` had no way to detect this additive
#:        contract change). Reports how complete/trustworthy the run's own
#:        evidence was -- depth, TU/export accounting, fact-set
#:        comparability, header-context drift, source-graph completeness --
#:        independently of the compatibility verdict; the flag only
#:        controls whether an incomplete status additionally floors the
#:        exit code. Additive key, absent only for the ``NOT_COMPARABLE``/
#:        audit-only ``diff`` shapes that never reach a real comparison.
#: 1.17 -- P0.4: the ``diff`` block gains ``analysis_assurance_exit_
#:        contribution`` (``0``/``1``), the exact sibling of the pre-existing
#:        ``contract_coverage_exit_contribution`` for the analysis-assurance
#:        axis -- read directly rather than recomputed by
#:        ``aggregate.GateInfo.from_scan_report``/``_analysis_assurance_exit``,
#:        since the aggregate holds none of the evidence needed to answer it
#:        again (Codex review: without this field, a target whose severity
#:        gate read ``0`` but whose analysis-assurance axis independently
#:        floored the real exit to ``1`` fed the aggregate a green result for
#:        that target). Always present on a real ``--against`` comparison,
#:        regardless of whether ``--require-complete-analysis`` was passed
#:        (``0`` either way when the flag wasn't given or the status was
#:        already ``complete``). Additive key, absent only for the same
#:        NOT_COMPARABLE/audit-only shapes ``analysis_assurance`` itself is.
#: 1.18 -- CLI cleanup phase two, PR E: the ``diff`` block gains ``exit``,
#:        the same canonical ``ExitDecision`` (``exit_decision.
#:        resolve_compare_exit_decision``) ``compare``'s own top-level
#:        ``exit`` key already carries since report_schema_version 2.41 (PR
#:        G1, #789) -- ``{code, reasons, compatibility_contribution,
#:        contract_coverage_contribution, analysis_assurance_contribution,
#:        crosscheck_promotion_contribution}`` (the last one added by
#:        report_schema_version 2.42 -- see that entry), so a reader
#:        doesn't have to re-derive "why is this exit N" from the
#:        separately-emitted ``severity``/``analysis_assurance_exit_
#:        contribution``/``contract_coverage_exit_contribution`` fields.
#:        Nested under ``diff`` (not ``ScanOutcome``'s own top-level
#:        ``verdict``/``exit_code``, which additionally folds the scan-only
#:        budget/``NOT_COMPARABLE`` axes this key does not model -- see
#:        ``exit_decision.resolve_compare_exit_decision``'s own docstring).
#:        A maintainer-promoted ``--crosscheck KEY=error`` finding raises
#:        this block's ``code`` after the fact
#:        (``scan_engine._promote_published_gate``), the same way it
#:        already raises the persisted ``severity`` block -- by
#:        reconstructing the whole decision through ``exit_decision.
#:        resolve_exit_decision`` with ``crosscheck_promotion_contribution``
#:        set to the promoted finding's own exit contribution, rather than
#:        hand-patching ``code``/``reasons`` in place (an earlier revision
#:        did exactly that and broke this class's own ``code == max(the
#:        contributions)`` invariant -- Codex review). Additive key, absent
#:        only for the same NOT_COMPARABLE/audit-only shapes
#:        ``analysis_assurance`` itself is.
#: 1.19 -- CLI cleanup phase two, PR B: the ``diff`` block gains
#:        ``effective_config_digest``/``effective_config_fields`` --
#:        `compare`'s report_schema_version 2.45 counterpart, computed by
#:        the identical ``effective_config_digest.effective_config_fields``
#:        call with this comparison's own ``sev_config``/``exit_scheme`` (the
#:        same pair the ``exit`` block immediately above was resolved from).
#:        Additive keys, absent only for the same NOT_COMPARABLE/audit-only shapes ``exit``/``analysis_assurance`` themselves are.
#: 1.20 -- ``effective_config_fields`` (1.19, above) gains ``gate.require_complete_analysis`` and ``gate.scope`` -- `compare`'s
#:        report_schema_version 2.46 counterpart, same fields, same reasons
#:        (see that version's own entry). Also: the rich tier's gate axes
#:        now come from the caller's own resolved severity/exit-code-scheme
#:        rather than the resolved ``CompatibilityEvaluationConfig``
#:        directly -- this is *scan*'s own bug being fixed here, since
#:        ``resolve_scan_config`` deliberately blanks that config's own
#:        gate fields, and a ``--pack``-only ``scan --against`` previously
#:        recorded the digest from that blanked copy instead of the run's
#:        real gate. Additive keys. Three more fixes landed under this same
#:        still-unreleased version, sharing the identical
#:        ``effective_config_digest`` call `compare`'s report_schema_version
#:        2.46 entry describes in full: ``surface.explicit_scope`` now also
#:        covers ``--post-manifest``'s ``public_surface_allowlist`` and
#:        merges rather than falls back between the two explicit-scope
#:        sources; ``policy.pattern_verdicts`` was added. One more fix,
#:        same round as `compare`'s report_schema_version 2.46 entry:
#:        ``policy.collapse_versioned_symbols``, ``policy.surface_metrics``,
#:        and ``policy.env_matrix`` were added, sharing the identical
#:        computation (see that entry for the full reasoning). Two more,
#:        same round as `compare`'s report_schema_version 2.46 entry: ``policy.reconcile_build_context`` and ``surface.scope_to_public_surface_requested`` were both added.
#: 1.21 -- ``scan --against``'s baseline summary gains ``coverage_warnings`` (Codex review), mirroring `compare`'s own top-level field of the same name/shape -- e.g. a byte-identical-binaries warning. Additive key, omitted when empty.
#: 1.22 -- ADR-064 stage 1b, mirrors `compare`'s 2.47 entry: ``ExitDecision.to_dict``'s five new keys reach ``diff.exit`` too, and a ``NOT_COMPARABLE`` baseline outcome now persists a real ``diff.exit`` block (previously ``diff`` was only ``{"reason": <message>}``).
#: 1.23 -- ADR-064 stage 1b (Codex review, PR #967): the typed ``ScanResult`` envelope's own ``report`` key -- ``ScanResult.to_dict()``'s ``report``, not the CLI's ``diff`` block above -- now nests a real ``exit`` block for a ``BUDGET_OVERFLOW``/``EVIDENCE_CONTRACT_ERROR`` abort (`abicheck.workflows.scan_abort_result.scan_abort_result_fields`), where it used to be `{}`.
#: 1.24 -- ADR-063 Phase 7, mirrors `compare`'s 2.48 entry: every scan JSON producer (``ScanOutcome.to_dict()``, the typed ``ScanResult.to_dict()``/``ScanSetResult.to_dict()``) gains an additive top-level ``run_outcome`` block, alongside the unchanged ``verdict``/``exit_code`` fields.
#: 1.25 -- G42 phase 1 (Codex review, fresh evidence): mirrors `compare`'s 2.49 entry -- ``check_id`` (already carried by a scan-shaped ``check-target`` audit report, ADR-047 §7's report-identity envelope subset) gains the identical two optional, composable tail segments, since ``buildsource.check_report.build_check_id()`` is the single shared constructor both a compare-shaped and a scan-shaped ``check-target`` report call. A pre-1.25 scan consumer pinned to this version could not feature-detect the widened ``~<explicit_id>``/``!<environment_id>`` lexical contract.
#: 1.26 -- ADR-037 D5's evidence-contract-error abort (Codex review, PR #1032, fresh evidence): its process exit code moved from the generic ClickException code 1 to a dedicated ``cli_scan.py`` ``_EXIT_EVIDENCE_CONTRACT_ERROR = 7`` -- three earlier stderr/marker-file classification signals were each shown forgeable by a PR-controlled build script running as part of the scan's own evidence collection (see ADR-064's own "fourth round" account). This changes the *value*, not the shape, of two already-published fields for this one abort axis: the top-level ``exit_code`` and ``diff.exit``/``report.exit``'s ``evidence_contract_error_contribution`` (previously both ``1``, now both ``7``). A pre-1.26 consumer pinned to this version and hard-coding ``1`` for this axis (rather than reading the published value, or comparing against the ``verdict``/``reasons`` strings, which are unchanged) would misread the new value.
SCAN_SCHEMA_VERSION = "1.27"  #: Codex review, PR #1062, fresh evidence: an ``EVIDENCE_CONTRACT_ERROR`` abort's typed ``ScanResult.report`` (both the single-binary path and each ``--artifact-set`` member's own ``per_artifact[].report``) gains an additive ``evidence_contract_error_message`` string -- ``_EvidenceContractError.message``, previously discarded at the exception boundary (`abicheck.workflows.scan_abort_result.scan_abort_result_fields`). Absent, not empty, for the ``budget_overflow`` axis and for every non-abort report, which carry no such message at all. A pre-1.27 consumer feature-detects the field's presence; nothing it already reads changed shape or value.

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
        from ..workflows.aggregate import AGGREGATE_SCHEMA_VERSION

        return AGGREGATE_SCHEMA_VERSION
    if name == "build-output":
        from ..buildsource.build_output import BUILD_OUTPUT_SCHEMA

        return BUILD_OUTPUT_SCHEMA
    # RUN_PLAN_SCHEMA_GATE, not the base RUN_PLAN_SCHEMA -- a run-plan.json
    # is stamped one of two schema strings depending on whether it carries a
    # `gate` block (CLI cleanup phase two, PR 2 continuation), so there is
    # no single fixed "the" version this artifact always emits. This
    # registry promises "the current version abicheck emits" (this
    # function's own docstring); the highest version abicheck can produce
    # is the truthful answer an external integrator/doc generator needs to
    # be prepared to parse, not the lower, conditionally-emitted one (Codex
    # review, fresh evidence -- reporting the base RUN_PLAN_SCHEMA here made
    # this registry silently unaware of the newly emitted gate-bearing
    # format).
    from ..buildsource.run_plan import RUN_PLAN_SCHEMA_GATE

    return RUN_PLAN_SCHEMA_GATE


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "SCAN_SCHEMA_VERSION",
    "COMPARE_REPORT_SCHEMA_PATH",
    "AGGREGATE_REPORT_SCHEMA_PATH",
    "load_compare_report_schema",
    "load_aggregate_report_schema",
    "current",
]

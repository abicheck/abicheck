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
#:   2.13: added two additive optional per-change keys (G29 Phase 3 slice 1,
#:       ADR-050) -- ``reachability_state`` (the tri-state signal from PR
#:       #607's ``Change.reachability_state``, always present, never
#:       serialized before this) and ``impact_assessment`` (a unified read
#:       view over the scattered reachability/impact fields above --
#:       ``reachability_state``/``public_reachable``/``reachability_kind``/
#:       the proof path/decision state/``evidence_category``/
#:       ``correlated_change_kind`` -- present only when it carries
#:       information beyond the all-defaults case).
#:   2.14: added two additive optional top-level keys, present only under
#:       ``--report-mode root-cause`` (G29 Phase 3 slice 3, ADR-050) --
#:       ``root_causes`` (groups ``changes`` by ``Change.caused_by_type``,
#:       falling back to the change's own symbol for an ungrouped finding)
#:       and ``root_cause_count``. A first, JSON-only slice of the plan's
#:       root-cause grouping -- ``root_cause_id`` is a stable hash of the
#:       grouping key, not the eventual G29 Phase 6 ``RootCauseCorrelator``'s
#:       own identifier scheme.
REPORT_SCHEMA_VERSION = "2.14"

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
SCAN_SCHEMA_VERSION = "1.1"

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


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "SCAN_SCHEMA_VERSION",
    "COMPARE_REPORT_SCHEMA_PATH",
    "AGGREGATE_REPORT_SCHEMA_PATH",
    "load_compare_report_schema",
    "load_aggregate_report_schema",
]

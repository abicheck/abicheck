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
"""P0.4: ``analysis_assurance`` — a first-class, orthogonal answer to "how
complete and trustworthy was the evidence behind this verdict", independent
of what the verdict itself says.

This is the third leg of a three-way split this codebase already has two
thirds of:

    compatibility_verdict   (``DiffResult.verdict`` / ``Verdict``)
    analysis_assurance      (this module -- NEW)
    policy_gate_decision    (``severity.compute_gate_decision`` / exit codes)

ADR-049's contract-relevance/evidence/coverage machinery
(``contract_evaluation.py``, ``policy/coverage_ledger.py``,
``contract_coverage_exit.py``, ...) already answers a *narrower* version of
this question -- "was there enough evidence to trust a *contract-relevance*
decision" -- and only when a caller opts into ``--contract``. This
module answers the broader question for *every* comparison, opt-in or not:
did the analysis itself (depth, translation-unit accounting, export
accounting, header/build/source-graph evidence) come back complete, whatever
the contract-relevance question turns out to be. The two are complementary,
not competing -- ``policy.coverage_ledger`` remains the authority for its
own narrower question, and this module does not duplicate it (it reads the
same underlying evidence rollups where they overlap: ``fact_set``
comparability, header-parse-context drift, source-graph completeness).

**Deliberately a rollup, not a new probe.** Every field below is derived
from data the pipeline already computed by the time ``checker.compare()``
returns (or, for translation-unit/export/graph-completeness detail, by the
time a ``BuildSourcePack`` was embedded on the snapshot ahead of
``compare()`` -- see ``service_input_resolution.embed_side_build_source``).
Nothing here shells out, re-parses a binary, or re-runs an extractor.

**What's included in this first slice, and what's deferred:**

- Requested-vs-effective *depth* reuses ``checker_types.EVIDENCE_DEPTH_VALUES``
  (``binary``/``headers``/``build``/``source``) rather than inventing a
  parallel vocabulary. ``requested_depth`` mirrors
  ``DiffResult.requested_depth`` -- the G30 report-identity field. Finding
  (review, P1, round 9): this field was documented as unpopulated by any
  front end and this module's own ``depth_satisfied`` gate reduced to
  reporting ``effective_depth`` alone as a result -- so an explicit
  ``compare --depth source`` that never actually reached source evidence
  (both sides lacking a compile database, say) silently read
  ``requested_depth=None``/``depth_satisfied=None`` and could still report
  ``status="complete"``. Fixed first at the one front end that knows the
  user's real, explicit ``--depth`` request: ``cli_compare_helpers.
  _report_compare_result`` copies its own Click-validated ``--depth``
  string onto ``DiffResult.requested_depth`` before recomputing this block
  (never an *inferred* depth from bare ``--sources``/``--build-info`` or
  ``.abicheck.yml``'s ``source.method`` -- only an explicit flag is this
  comparison's stated request in the same on-the-record way). That fix was
  CLI-only, though the underlying gap was not (P2 review,
  ``discussion_r3787839902``): ``service.run_compare_request()`` -- the
  shared, typed Python-API/MCP entry point ``service_compare_pipeline.
  classify_compare_pair`` implements -- never gets this treatment, since
  the CLI's own ``compare`` command resolves its evidence through
  ``resolve_compare_request`` but classifies through a hand-rolled
  ``compare_snapshots`` call of its own (its ``--depth`` flag never even
  reaches ``CompareRequest.depth``; that field only carries a real value
  for a *direct* typed-API caller). Fixed there too:
  ``classify_compare_pair`` now applies the identical ``if request.depth
  is not None`` stamp-and-recompute, so a direct
  ``service.run_compare_request(CompareRequest(..., depth="headers"))``
  call gets the same `requested_depth`/`depth_satisfied` receipt the CLI
  already did, without disturbing a caller that never sets
  ``CompareRequest.depth`` (unaffected, same as before). The two call
  sites are not one shared helper because each recomputes from genuinely
  different inputs the pipeline made available at a different point: the
  CLI recomputes with a possibly out-of-band ``--old/new-build-info``/
  ``--old/new-sources`` pack (`_resolve_side_pack`) that never touches
  ``old``/``new`` directly, while ``classify_compare_pair`` recomputes from
  each snapshot's own already-embedded ``build_source`` (mirroring
  ``checker.compare()``'s own call) since a typed request's
  ``InputSpec.sources``/``build_info`` are embedded before resolution ever
  returns -- there is no separate out-of-band pack to fold in on that path.
  ``effective_depth`` is always computed here, independent of that field,
  from what each side's snapshot actually carries, via the shared
  ``evidence_depth.depth_label_for`` (removed). That rule used to be reimplemented
  here rather than imported, because importing it meant importing a
  CLI-layer module and reaching back through ``cli.py`` into ``checker.py``;
  ADR-061 Phase 3 moved it to a leaf, so this is a real delegation now.
- ``target_accounting`` (expected vs. resolved Bazel targets) rolls up P0.2's
  root-target resolution (``BuildEvidence.target_scope``) now that it has
  landed -- ``requested``/``resolved`` stay ``None`` only when neither side
  requested root-target scoping at all, never silently omitted, so a
  consumer can tell "not requested" from "requested and empty".
- Translation-unit and export accounting are rollups of the L3/L4
  ``BuildSourcePack`` coverage a snapshot already carries (when one was
  embedded); both are ``None``-filled when no such pack is present, which is
  the ordinary case for a plain ELF/DWARF/header comparison.
- ``fact_set_comparability``/``l0_context_status``/``header_context_status``/
  ``dwarf_context_status``/``l3_context_status``/``graph_completeness``
  surface existing signals (``fact_set_inconsistent`` plus a cross-side
  ``fact_set`` name/version comparison, each side's own binary export-table
  presence, the ``header_parse_context_drift`` finding, each side's own
  ``dwarf.has_dwarf``/``dwarf_advanced.has_dwarf``, each side's own
  ``BuildSourcePack.build_evidence`` presence, ``SourceGraphSummary.
  degraded_passes``) as structured status fields rather than leaving them as
  prose-only findings/coverage keys.
"""

from __future__ import annotations

from ._compute import compute_analysis_assurance
from ._outputs import (
    INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION,
    analysis_assurance_exit_contribution,
    analysis_assurance_report_dict,
    assurance_floor_diagnostic,
)
from ._result import (
    ANALYSIS_ASSURANCE_SCHEMA_VERSION,
    ASSURANCE_STATUS_VALUES,
    AnalysisAssurance,
    ExportAccounting,
    TargetAccounting,
    TranslationUnitAccounting,
)

__all__ = [
    "ANALYSIS_ASSURANCE_SCHEMA_VERSION",
    "ASSURANCE_STATUS_VALUES",
    "INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION",
    "AnalysisAssurance",
    "ExportAccounting",
    "TargetAccounting",
    "TranslationUnitAccounting",
    "analysis_assurance_exit_contribution",
    "analysis_assurance_report_dict",
    "assurance_floor_diagnostic",
    "compute_analysis_assurance",
]

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

"""The directory/package release JSON envelope's own schema version.

Split out of :mod:`abicheck.schemas` (PR #1195), which sits at its
`architecture/debt.yaml` ``no_growth`` baseline: adding this envelope's 1.1
note would have grown it, and the sanctioned remedy is to move a
responsibility out rather than trim the note to fit. This envelope is a
real one -- `compare`'s directory/package fan-out document, versioned
independently of the per-comparison report and of `scan` -- so its constant
and its version history are a responsibility, not a stray constant.

Re-exported from :mod:`abicheck.schemas`, so every existing importer and
``schemas.current("release")`` are unaffected. A dependency-free leaf: it
imports nothing, which is what lets the package re-export it without a
cycle.
"""

from __future__ import annotations

__all__ = ["RELEASE_SCHEMA_VERSION"]


#: SemVer-style (MAJOR.MINOR) version of the directory/package release JSON
#: envelope (`compare-release`'s per-run document,
#: ``cli_compare_release_helpers._format_release_json``).
#:
#: 1.0 -- introduced (Codex review, findings-fixes round 11): this document
#:       had no schema-version field at all before this constant, so a
#:       consumer could not tell a release document predating the
#:       ``compatible_additions`` correction (report schema 4.0: additions
#:       only, excluding ``quality_issues``) apart from one written after
#:       it -- both used the identical field name under two different
#:       meanings. A document with no ``release_schema_version`` key
#:       predates this constant and may carry either meaning depending on
#:       which abicheck version produced it, with no reliable way to tell
#:       from the payload alone; 1.0+ always carries the corrected value.
#: 1.1 -- PR #1195 (Codex review, P2): every ``libraries[]`` entry gains an
#:       always-present ``evidence_contract_error_contribution`` integer --
#:       ADR-064's exit-7 axis for the member (``0``, or ``7`` when an
#:       explicit ``--depth build``/``--depth source`` pin was not reached
#:       by a live side's own evidence). Additive and unconditional: it is
#:       written for every member of every release document, ``0`` for the
#:       runs that never pinned a rung, which is why the shape change needs
#:       a version signal even though no existing field moved. The same
#:       release's ``exit`` block carries the aggregated value under the
#:       identical key, and ``run_outcome.operational`` reads
#:       ``evidence_contract_error`` when it is nonzero. A pre-1.1 consumer
#:       reads nothing differently; one that wants the axis feature-detects
#:       the key or requires >= 1.1.
#: 1.2 -- Codex review, P2 (PR #1221 follow-up): the declared-deployment-
#:       floor contract's content digest, ``env_matrix_source_sha256`` --
#:       the same field the scalar ``compare`` report (schema 4.2) and the
#:       ``--no-baseline`` audit report already carry under this identical
#:       name, for the identical ``EnvironmentMatrix``/``.abicheck.yml``
#:       ``deployment:`` contract. Present on both a ``libraries[]`` entry
#:       (mirroring what a standalone `compare` of that pair would report)
#:       and, once, on the release envelope itself (since ``env_matrix`` is
#:       threaded identically to every library in one release fan-out, so
#:       every entry that carries the key carries the identical digest) --
#:       omitted entirely, never ``null``, from both when this release's
#:       candidate declared no ``deployment:`` contract at all. A pre-1.2
#:       consumer reads nothing differently; one that wants the field
#:       feature-detects the key or requires >= 1.2.
#: 1.3 -- ADR-071 (release analysis-assurance fold): a top-level
#:       ``analysis_assurance`` block (the fold over every compared member:
#:       its own ``schema_version``, the aggregate ``status``, the member and
#:       incomplete-member counts, the ``0``/``1`` ``exit_contribution``, and
#:       the named ``incomplete_members`` rows), plus
#:       ``analysis_assurance_status``/``analysis_assurance_notes``/
#:       ``analysis_assurance_exit_contribution`` on each ``libraries[]``
#:       entry, and the canonical **top-level**
#:       ``analysis_assurance_exit_contribution`` (the exact sibling of
#:       ``contract_coverage_exit_contribution``, and the key report schema
#:       2.40 defined for this axis) -- emitted on the release document and on
#:       ``--output-dir``'s ``summary.json`` alike, because that top-level key
#:       is what ``workflows.aggregate.gate._analysis_assurance_exit`` and the
#:       composite Action's ``gate_mode: deferred`` path read. Emitting the
#:       floor only inside ``exit``/``analysis_assurance`` left both reading
#:       ``0`` for a run whose real exit was ``1`` (Codex security review,
#:       P1). The fold block carries ``notes`` -- one flat, member-attributed
#:       list in the same shape a scalar ``analysis_assurance.notes`` has --
#:       because that is the key the composite Action's ``assurance_notes``
#:       query reads to name what fell short; without it a release run reported
#:       ``ANALYSIS_INCOMPLETE`` with no reasons where a scalar one named them. Unlike 1.1's unconditional field, all of these are present
#:       ONLY when ``assurance.require_complete`` was in effect -- the same
#:       "present only when active" convention the ``severity`` and
#:       ``contract_coverage_*`` blocks already follow, and what keeps every
#:       release document produced without the setting byte-identical. The
#:       ``exit`` block's own ``analysis_assurance_contribution`` is not new
#:       (ADR-064 stage 1b already wrote it, always ``0`` on a release); it
#:       can now be nonzero. A pre-1.3 consumer reads nothing differently;
#:       one that wants the block feature-detects the key or requires >= 1.3.
#: - ``1.4`` adds the two ADR-067 disposition ledgers to each ``libraries[]``
#:       entry: ``suppression`` (``file_provided``/``suppressed_count``/
#:       ``suppressed_changes[]``, each naming the rule that hid it) and
#:       ``surface_scope`` (``out_of_surface_count``/``out_of_surface_
#:       changes[]``, each naming its exclusion reason). Both are the
#:       *identical* blocks a single-pair ``compare`` report already carries,
#:       produced by the same two builders (``reporter.disposition_ledger_
#:       blocks``), so a consumer reads one shape at either cardinality.
#:       Before this, a release that suppressed or scoped out its entire
#:       breaking set emitted only counts and echoed the detail to stderr, so
#:       the report artifact itself could not say what had been disposed of
#:       or by which rule (Codex review, PR #1284). Present ONLY when the
#:       setting was in effect -- ``surface_scope`` when scoping ran,
#:       ``suppression`` when a suppression document was supplied or
#:       something was actually suppressed -- the same "present only when
#:       active" convention 1.3's own blocks follow, which keeps every
#:       release document produced without them byte-identical. A pre-1.4
#:       consumer reads nothing differently.
#: - ``1.5`` adds the remaining two ADR-067 dispositions to each
#:       ``libraries[]`` entry, so the release carries the same four a
#:       single-pair ``compare`` report does rather than two of them:
#:       ``build_context_reconciled`` (``count``/``changes[]``, each naming
#:       its ADR-039 reconciliation reason) and ``pattern_modulations`` (the
#:       ADR-027 ledger, each entry naming the ``rule_id`` that reclassified
#:       the finding and why). Both are the *identical* blocks the scalar
#:       report already carries, from the same builders
#:       (``reporter.disposition_ledger_blocks``), so a consumer reads one
#:       shape at either cardinality. Before this, a release whose entire
#:       breaking set was cleared by reconciliation, or demoted by a pattern
#:       rule, rendered a passing artifact naming neither the findings nor
#:       the reasons -- reconciliation is the sharper case, because it needs
#:       no settings at all (no suppression document, no
#:       ``--scope-public-headers``), so the section was absent rather than
#:       merely incomplete (Codex review, PR #1284). Both follow the same
#:       "present only when active" convention as 1.3 and 1.4:
#:       ``build_context_reconciled`` only when reconciliation cleared
#:       something, ``pattern_modulations`` only when a rule fired (ADR-027
#:       is opt-in and off by default), so every release document produced
#:       without them is byte-identical. A pre-1.5 consumer reads nothing
#:       differently; one that wants either block feature-detects the key or
#:       requires >= 1.5.
#: ``1.6`` adds each member's scalar-parity ``review_groups`` and
#: ``result_counts`` blocks; both are additive and presentation-only.
#: - ``1.7`` adds the change-versus-inventory split at both cardinalities:
#:       ``libraries[].change_inventory`` (the *identical* block, from the
#:       identical computation, that a scalar ``compare`` report carries as
#:       ``summary.change_inventory``) and the release fold
#:       ``change_inventory`` (``report/release_change_inventory.py``).
#:       Additive: every existing counter keeps its documented meaning, so a
#:       pre-1.7 consumer reads nothing differently. The member block closes
#:       a real discrepancy -- the fan-out already computed
#:       ``build_summary(result)`` and copied two of its fields, leaving the
#:       inventory behind, so a release whose displayed "risk" was entirely
#:       standing hygiene could not say so the way the scalar report could.
#:       The release fold is members-only and states its own scope
#:       (``members_contributing``/``members_no_comparison_completed``/
#:       ``members_without_inventory``): bundle-coherence and probe-matrix
#:       findings are a different unit over a different operand and are
#:       never folded in, and a member with no completed comparison is
#:       counted rather than summed as zero findings. Absent when no member
#:       carries a block, rather than an all-zero aggregate.
#: - ``1.8`` adds the top-level ``public_surface_reconciliation`` object: the
#:       release's one public contract reconciled against the union of its
#:       members' exports (``report.release_public_surface``). It states, per
#:       side, how many public declarations carry an export obligation, how
#:       many the bundle satisfies, which are missing, which are *unresolved*
#:       because a member was unread, the export totals and the
#:       documented/undocumented split; plus the release-level
#:       ``public_not_exported`` findings, the ``shared_findings`` folded out
#:       of the per-library tables (each naming every affected library), the
#:       per-member undocumented-export counts, and the header-acquisition
#:       instrumentation (``acquisitions``/``reuses`` -- one acquisition per
#:       side for an ordinary directory comparison). Two related, additive
#:       per-member changes come with it: a ``libraries[]`` entry gains
#:       ``product_level_findings`` (how many of its findings were folded
#:       into the release section, so an entry whose ``findings`` list is
#:       shorter than its counts is never silently so), and the
#:       whole-product ``public_not_exported`` check no longer runs per
#:       member at all -- a multi-member release reports it once, at release
#:       level, instead of once per member per declaration a sibling
#:       provides. That last part is the one non-additive consequence: a
#:       consumer counting per-member ``public_not_exported`` findings on a
#:       directory comparison will now find them under
#:       ``public_surface_reconciliation.missing_exports`` instead, and far
#:       fewer of them, because the per-member answer was a Cartesian
#:       product (787,833 on a 28-library Intel MKL release). Every other
#:       block is "present only when it states something", following 1.3-1.5.
#:       This entry independently claimed ``1.7`` on the same base
#:       version as the ``change_inventory`` entry above -- a genuine
#:       same-line collision, resolved the way this file's own history
#:       already resolves several (``2.32``/``2.36``/``2.38``/``3.2``
#:       in the per-comparison report's constant): renumber, don't
#:       reuse, rather than discarding either side's change.
RELEASE_SCHEMA_VERSION = "1.8"

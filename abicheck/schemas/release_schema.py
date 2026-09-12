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
#: 1.3 -- ADR-070 (release analysis-assurance fold): a top-level
#:       ``analysis_assurance`` block (the fold over every compared member:
#:       its own ``schema_version``, the aggregate ``status``, the member and
#:       incomplete-member counts, the ``0``/``1`` ``exit_contribution``, and
#:       the named ``incomplete_members`` rows), plus
#:       ``analysis_assurance_status``/``analysis_assurance_notes``/
#:       ``analysis_assurance_exit_contribution`` on each ``libraries[]``
#:       entry. Unlike 1.1's unconditional field, all of these are present
#:       ONLY when ``assurance.require_complete`` was in effect -- the same
#:       "present only when active" convention the ``severity`` and
#:       ``contract_coverage_*`` blocks already follow, and what keeps every
#:       release document produced without the setting byte-identical. The
#:       ``exit`` block's own ``analysis_assurance_contribution`` is not new
#:       (ADR-064 stage 1b already wrote it, always ``0`` on a release); it
#:       can now be nonzero. A pre-1.3 consumer reads nothing differently;
#:       one that wants the block feature-detects the key or requires >= 1.3.
RELEASE_SCHEMA_VERSION = "1.3"

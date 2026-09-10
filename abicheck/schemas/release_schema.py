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
RELEASE_SCHEMA_VERSION = "1.1"

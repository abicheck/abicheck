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

"""The extraction contract and dependency ledger a snapshot was produced under.

``ExtractionContract`` records the scope and profile fingerprints ADR-050's
comparability gate compares two snapshots on; ``DependencyInfo`` records the
resolved dependency graph a scan observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DependencyInfo:
    """Resolved transitive dependency graph and symbol bindings.

    Populated when a snapshot is created with ``--follow-deps``.
    """

    nodes: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    bindings_summary: dict[str, int] = field(default_factory=dict)
    missing_symbols: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExtractionContract:
    """ADR-050 D1 — profile/scope fingerprints proving two snapshots were
    extracted under a comparable contract, plus the resolved per-field
    inputs each fingerprint was computed from (so a mismatch report can show
    *what* differs, not just that the hashes don't match).

    Built by ``abicheck.comparability.compute_extraction_contract`` — never
    constructed by hand outside tests. Both fingerprints are independently
    optional: a symbols-only dump with no header-AST inputs but a real
    a public-header set still attaches a
    ``scope_fingerprint`` with ``profile_fingerprint=None`` (see that
    module's docstring for the full rationale).
    """

    profile_fingerprint: str | None = None
    scope_fingerprint: str | None = None
    # Named resolved sub-inputs, one string per component, keyed the same way
    # on both sides of a compare so a mismatch can be attributed to a specific
    # field instead of an opaque hash. See ``comparability.PROFILE_FIELD_KEYS``
    # / ``comparability.SCOPE_FIELD_KEYS`` for the recognized keys.
    profile_fields: dict[str, str] = field(default_factory=dict)
    scope_fields: dict[str, str] = field(default_factory=dict)

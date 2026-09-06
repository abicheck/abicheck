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
    # E-S1 (docs/contribute/plans/vision-api-abi-evolution.md, "E. Evidence
    # adequacy, contract-source conflicts, cross-profile comparison" /
    # cli-cleanup-phase-two.md Block 5): the toolchain-identity probe's own
    # ``model.availability.FactStatus.value`` behind this side's
    # ``profile_fields["compiler_family"]`` -- ``"present"`` when a host
    # compiler identity was actually resolved, ``"failed"`` when resolution
    # was attempted and errored (``dumper_toolchain._stamp_ast_parser``'s own
    # ``compiler_error``), or ``None`` when this contract predates the field
    # or no L2 frontend ran at all (nothing was ever attempted -- not a gap,
    # since nothing here asserts one). A plain ``str``, like every other
    # field on this dataclass, not a ``FactStatus`` instance itself: this
    # keeps the whole dataclass serializing through the ordinary
    # ``dataclasses.asdict()``/``json.dumps()`` round-trip
    # ``serialization.py`` already uses for it, with no new codec --
    # callers translate to/from ``FactStatus`` at the two edges
    # (``dumper_toolchain.py`` writes it, ``comparability_profile.py`` reads
    # it back via ``FactStatus(value)``). Deliberately never silently
    # collapsed into an absent/``None`` ``compiler_family`` on a probe
    # failure (see ``dumper_toolchain._compiler_family_from_toolchain``) --
    # that indistinguishability from "never attempted" is exactly what
    # previously let a mismatched GCC/Clang pair compare silently.
    compiler_identity_status: str | None = None

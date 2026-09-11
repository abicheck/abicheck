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

"""Declared-runtime-floor / wheel-packaging checks as a candidate-side
audit (ADR-068 D3), for ``compare --no-baseline``.

``checker._env_matrix_contract_changes`` already runs two different kinds
of ``env_matrix.runtime_floors`` check inside every two-sided ``compare()``
call: ``apply_runtime_floor_contract`` *reclassifies* an existing
version-requirement **delta** finding between the two diffed snapshots
(there is none on a lone, no-baseline candidate -- there is no OLD side to
diff against), and six standalone checks that each read only the
**candidate**'s own evidence (``new_elf``/``new_macho``) plus the declared
floors, firing regardless of whether anything moved relative to an old
snapshot -- exactly the manylinux-tag-violation case a floor exists to
catch (a binary that has *always* required a newer glibc than its wheel tag
promises). Those six are candidate-only by construction, so
``compare --no-baseline`` can and should run them even with no baseline at
all -- previously it had no channel to them, so a real declared
``deployment.runtime_floors`` config was silently ignored for a no-baseline
audit of a candidate that violates it (Codex review): an ordinary two-sided
``compare`` of the identical candidate would fail BREAKING, while the
no-baseline audit of it passed clean.

:func:`env_matrix_candidate_findings` mirrors
``workflows.abi3_audit.abi3_candidate_findings``'s own pattern for exactly
this shape of gap: compute the candidate-only findings directly (not by
passing *env_matrix* into ``checker.compare()``'s own ``env_matrix``
parameter, which would also try to run ``apply_runtime_floor_contract``
against a self-diff's ``kept``/``verdict_redundant`` -- harmless since a
self-diff never carries a version-requirement delta to reclassify, but the
findings ``checker.compare()`` would themselves produce carry neither of
the two ADR-068 D3 one-sided markers, so ``policy.no_baseline_findings``
would read them as an *identity*-half finding and
``check_no_baseline_partition`` would raise ``NoBaselineInvariantError`` --
a snapshot compared against itself is expected to be identical, and these
would not be), mark each one ``candidate_side_enrichment`` (ADR-068 D3's
"marked as such"), and fold them into a run's *extra_changes* the same way
the abi3 audit does -- entering the change set *before* classification, so
compatibility policy, suppression, the disposition ledger, and the exit
code all see them exactly like every other externally-produced finding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..environment_matrix import EnvironmentMatrix

__all__ = ["env_matrix_candidate_findings", "fold"]


def env_matrix_candidate_findings(
    candidate: Any, env_matrix: EnvironmentMatrix | None
) -> list[Change]:
    """The candidate-only declared-runtime-floor/wheel-packaging findings for
    *candidate* under *env_matrix*.

    ``[]`` when *env_matrix* is ``None`` or declares no ``runtime_floors`` at
    all -- the identical cheap pre-filter
    ``checker._env_matrix_contract_changes`` applies, so an audit that
    declares no floors is bit-for-bit unchanged from before this function
    existed. Deliberately does **not** call
    ``diff_versioning.apply_runtime_floor_contract`` -- see this module's
    own docstring for why that half has no candidate-only meaning. It *does*
    call ``diff_versioning.promote_baseline_violation_findings`` (Codex
    review), the same shared promotion
    ``checker._env_matrix_contract_changes`` applies to its own standalone
    checks' output: three of these six checks only ever fire on an actual
    floor violation, so any finding of one of those three kinds is
    unconditionally BREAKING here too, not left at its RISK catalog default.
    """
    if env_matrix is None or not env_matrix.runtime_floors:
        return []
    floors = env_matrix.runtime_floors

    from ..diff_versioning import (
        check_musllinux_glibc_dependency,
        check_platform_baseline_floor,
        promote_baseline_violation_findings,
    )
    from ..diff_wheel_deployment import (
        check_macos_deployment_target_floor,
        check_wheel_closure_dependency_violation,
        check_wheel_rpath_not_portable,
        check_wheel_tag_architecture_mismatch,
    )
    from ..elf_metadata import ElfMetadata

    # Same substitution `checker._env_matrix_contract_changes` applies: the
    # floor checks take an empty `ElfMetadata()` stand-in for a missing one
    # (answering "does this binary's own declared requirement violate the
    # floor", which an absent ELF answers "no"), while the wheel checks take
    # the raw, possibly-`None` value, since a non-ELF/non-Mach-O candidate
    # has no wheel-portability claim to check at all.
    new_elf = getattr(candidate, "elf", None)
    new_macho = getattr(candidate, "macho", None)

    findings: list[Change] = []
    for check_changes in (
        check_platform_baseline_floor(new_elf or ElfMetadata(), floors),
        check_musllinux_glibc_dependency(new_elf or ElfMetadata(), floors),
        check_macos_deployment_target_floor(new_macho, floors),
        check_wheel_tag_architecture_mismatch(new_elf, new_macho, floors),
        check_wheel_rpath_not_portable(new_elf, floors),
        check_wheel_closure_dependency_violation(new_elf, floors),
    ):
        findings.extend(check_changes)
    promote_baseline_violation_findings(findings)
    for finding in findings:
        finding.candidate_side_enrichment = True
    return findings


def fold(
    extra_changes: list[Change] | None,
    candidate: Any,
    env_matrix: EnvironmentMatrix | None,
) -> list[Change] | None:
    """:func:`env_matrix_candidate_findings`, folded into a run's *extra_changes*.

    Mirrors ``abi3_audit.fold``'s own shape exactly, for the identical
    reason: the one place the fold happens is what lets every caller agree
    on *when* these findings enter the change set (before classification,
    not after).
    """
    findings = env_matrix_candidate_findings(candidate, env_matrix)
    if findings:
        extra_changes = [*(extra_changes or []), *findings]
    return extra_changes

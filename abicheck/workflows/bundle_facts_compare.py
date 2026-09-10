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

"""Bundle-level comparison orchestration from a stored
:class:`~abicheck.model.bundle_facts.BundleFacts` (G38 Phase 2, ADR-061 gap
E). The sibling of ``workflows/bundle_facts_capture.py``: that module
builds/reconstructs a ``BundleFacts``-backed value; this one runs an actual
comparison from one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..bundle_manifest import InstantiationManifest
from .bundle_facts_capture import bundle_snapshot_from_facts

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult, BundleSnapshot
    from ..checker_types import DiffResult
    from ..model.bundle_facts import BundleFacts


def compare_bundle_from_facts(
    old_facts: BundleFacts,
    new_snapshot: BundleSnapshot,
    per_library_results: list[DiffResult],
    *,
    manifest: InstantiationManifest | None = None,
    system_providers: Any = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: Any = None,
    new_signature_evidence: dict[str, Any] | None = None,
    old_signature_evidence: dict[str, Any] | None = None,
) -> BundleDiffResult:
    """Bundle-level comparison with the *old* side loaded from a stored
    :class:`~abicheck.model.bundle_facts.BundleFacts` instead of live
    ``.so`` files (G38 Phase 2).

    A thin wrapper, deliberately: it reconstructs the old-side
    :class:`~abicheck.bundle_models.BundleSnapshot` via
    :func:`~abicheck.workflows.bundle_facts_capture.bundle_snapshot_from_facts`
    and then delegates to :func:`abicheck.bundle_analysis.analyze_bundle` --
    the same orchestrator a live-directory-vs-live-directory
    ``compare --release`` uses, so the two entry points share one detection
    implementation and can never independently drift, per the mandatory
    dump/live parity test.

    *manifest*, given explicitly, overrides *old_facts.manifest* (mirroring
    ``compare_bundle()``'s own ``manifest=`` parameter); otherwise the
    manifest captured in *old_facts* is reused.

    *new_signature_evidence* (G38 Phase 12) is the NEW side's bundle-
    canonical-key -> ``AbiSnapshot`` map for ``find_unverified_signature_
    findings``. *old_signature_evidence* (Codex review, PR #1060, round 6)
    overrides the OLD side's map, falling back to *old_facts.per_library_
    snapshots* -- needed by a depth-projecting caller. Omitted: no gate.
    """
    from ..bundle_analysis import analyze_bundle

    old_snapshot = bundle_snapshot_from_facts(old_facts)
    effective_manifest = manifest if manifest is not None else old_facts.manifest
    effective_old_evidence = old_facts.per_library_snapshots if old_signature_evidence is None else old_signature_evidence
    return analyze_bundle(
        old_snapshot,
        new_snapshot,
        per_library_results,
        manifest=effective_manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
        policy_file=policy_file,
        old_signature_evidence=effective_old_evidence,
        new_signature_evidence=new_signature_evidence,
    )

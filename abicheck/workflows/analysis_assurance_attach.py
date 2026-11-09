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

"""Attach one comparison's ``AnalysisAssurance`` to its ``DiffResult``.

The two pairwise entry points -- ``checker.compare`` (Tier 1) and
``workflows.compare_classify.classify_resolved_pair`` (Tier 2, the one
classification every scalar ``compare`` route runs) -- had byte-identical
copies of this call. That was survivable while it was three
arguments; it stopped being so once the call had to carry the content-
identity projection too (Codex review, PR #1229): ``analysis_assurance``
sits in ``policy``, which may not import ``storage``
(``architecture/modules.yaml``), so "are these two snapshots the same
persisted content" has to be supplied by a caller that may -- and a caller
that forgets silently falls back to a narrower object-identity answer, with
nothing failing anywhere. One owner in ``workflows`` is where that cannot
happen; ``tests/test_analysis_assurance_content_identity.py`` holds every
remaining call site to the same wiring.
"""

from __future__ import annotations

import dataclasses
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot

__all__ = [
    "attach_analysis_assurance",
    "attach_evidence_depths",
    "attach_type_evidence",
]


def attach_analysis_assurance(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    """Compute *result*'s assurance rollup from *old*/*new* and store it.

    The packs are read off the snapshots rather than taken as parameters:
    an out-of-band ``--build-info``/``--sources`` pack is attached onto the
    snapshot before classification, so the depth-capped view carries it.
    """
    from ..evidence_depth import embedded_evidence_pack
    from ..policy.analysis_assurance import compute_analysis_assurance
    from ..policy.analysis_assurance._unobserved import (
        COVERAGE_WARNING_DIMENSIONS,
        collect_unobserved,
    )
    from ..storage.snapshot_encode import same_persisted_content

    attach_type_evidence(result, old, new)
    # One registry pass feeds both the rollup and the coverage rows below.
    unobserved = collect_unobserved(old, new)
    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        unobserved=unobserved,
        # A header-only side's graph stands in as its pack (ADR-063 Phase 10).
        old_pack=embedded_evidence_pack(old) if old is not None else None,
        new_pack=embedded_evidence_pack(new),
        same_content=(
            partial(same_persisted_content, old, new) if old is not None else None
        ),
    )
    # ADR-085 D3 / ADR-079: the unobserved dimensions registered with
    # ``coverage_warning=True`` (template defaults, concepts, export-table
    # reads) are coverage gaps, so every report's coverage-gap rows show the
    # same text the rollup noted -- one registry, not per-gap wiring. Scalar
    # compare can attach twice to one result, so a warning already present is
    # skipped.
    for finding in unobserved:
        if finding.dimension not in COVERAGE_WARNING_DIMENSIONS:
            continue
        warning = finding.reason
        if warning not in result.coverage_warnings:
            result.coverage_warnings.append(warning)


def attach_evidence_depths(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> None:
    """Stamp ``old_evidence_depth``/``new_evidence_depth`` onto *result*.

    The evidence depth each side *actually* carries (the gate's
    :func:`~abicheck.evidence_depth.reported_depth_label` rule), read off the
    snapshots' own -- already depth-capped -- ``build_source`` packs.

    Owned here rather than by a front end: the native ``compare`` CLI used to
    be the only route that set these two report fields, so a typed-API
    report and every release member's report carried ``null`` for the
    identical comparison (F2 route-parity harness). Every scalar route now
    reaches it through the one classification core.
    """
    from ..evidence_depth import reported_depth_label

    result.old_evidence_depth = reported_depth_label(old, old.build_source)
    result.new_evidence_depth = reported_depth_label(new, new.build_source)


def attach_type_evidence(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    """Stamp ADR-090's per-side type-evidence fact onto *result*.

    Runs on both pairwise entry points (this module's docstring), so a
    typed-API, scalar-CLI and release-member result all carry it. The
    identical-input exemption (D1(c)) matters only when *both* sides lack
    type-level evidence: a one-sided gap means the two inputs differ by
    construction. It is proven by byte-identical binaries
    (``LibraryMetadata.sha256``, set by ``compare_classify`` before its own
    call) or, with no binary hashes on both sides, by the snapshots' evidence
    identity digest -- the same identity ``note_if_same_binary_compared``
    uses.
    """
    from ..model.type_evidence import type_evidence_facts
    from ..storage.snapshot_evidence import evidence_identity_digests

    # Recomputed on every attach, from the snapshots this call was given:
    # a cached answer from an earlier attach could describe other inputs.
    facts = type_evidence_facts(old, new)
    if old is not None and facts.old is False and not facts.new:
        old_meta, new_meta = result.old_metadata, result.new_metadata
        if old_meta is not None and new_meta is not None:
            identical = bool(old_meta.sha256) and old_meta.sha256 == new_meta.sha256
        else:
            # ADR-062 D16's *evidence* identity: two dumps of one binary that
            # differ only in `created_at`, dump path or producer order are one
            # input -- the identity `note_if_same_binary_compared` uses too.
            old_digest, new_digest = evidence_identity_digests(old, new)
            identical = old_digest is not None and old_digest == new_digest
        facts = dataclasses.replace(facts, identical_inputs=identical)
    result.type_evidence = facts

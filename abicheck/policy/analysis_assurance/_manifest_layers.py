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
"""Manifest-layer incompleteness axis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...buildsource.model import CoverageStatus, DataLayer

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _manifest_layer_incompleteness(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[bool, list[str]]:
    """Whether either side's pack *manifest* itself records an L3/L4 layer as
    incomplete -- ``partial`` coverage, or a ``failed``/``partial`` extractor
    -- for a layer this run's rollup otherwise treats as present (Finding, P1
    review). Deliberately does not flag a plain ``not_collected`` coverage
    row: that's the ordinary "this layer was never attempted" state every
    other helper in this module already handles correctly (``sa is None`` /
    ``pack is None`` short-circuits) -- it is specifically a layer the model
    objects say IS present that the manifest says is only ``partial`` that
    every other check here was blind to.

    This is the one place the manifest's own, authoritative D7/D8 vocabulary
    (``BuildSourceManifest.coverage``'s ``LayerCoverage.status``,
    ``BuildSourceManifest.extractors``'s ``ExtractorRecord.status``) is
    consulted -- every other helper in this module reads the pack's *model*
    objects (``source_abi``/``source_graph``/``build_evidence``) directly,
    which is exactly what missed the ``inline._run_inline_source_abi()``
    clang-unavailable case: a bare ``SourceAbiSurface()`` reads as "L4
    present" to every model-level check, while the manifest ``build_inline_
    coverage()`` already built for this same pack correctly recorded that L4
    row as ``partial`` (see that function's own ``any_entities`` check).
    Reading the manifest here closes that gap without duplicating its logic.

    Finding (review, P1, round 7): the extractor-status scan above only ever
    recognized a ``failed``/``partial`` record whose ``name`` started with
    one of a fixed ``source_abi``/``compile_``/``source_graph`` prefixes --
    but real extractor records outside that allowlist can also record a hard
    ``"failed"`` that invalidates the layer they cover, and this scan was
    silently blind to every one of them. The concrete case that surfaced
    this (Finding 3): ``inline._check_build_info_source_mismatch``
    appends an ``ExtractorRecord(name="build_info_source_tree_mismatch",
    status="failed", ...)`` when most of a compile database's own source
    files are absent from the ``--sources`` tree -- i.e. the build metadata
    and the source checkout may not even be the same codebase -- and that
    name matches none of the three prefixes, so the record was invisible
    here: if the resulting L4 surface still carried ordinary TU accounting
    and no other partial signal tripped, ``analysis_assurance.status`` read
    ``"complete"`` and ``--require-complete-analysis`` exited 0 despite the
    source facts possibly coming from a different checkout entirely. The
    same blind spot also silently missed every other non-prefixed extractor
    family that can legitimately record a hard failure --
    ``build_query``/``build_query_auto``/``bazel`` (a query subprocess
    timing out or failing to run, ``build_query.py``) and
    ``merge_layer_conflict`` (two inputs supplying irreconcilably different
    facts for one layer, ``merge_support.py``) chief among them -- none of
    which start with the old prefixes either. Fixed per the root-cause
    principle (fix the class of failure, not the one instance): a
    ``"failed"`` record ALWAYS invalidates the layer it names, regardless of
    which family produced it, so the prefix allowlist is dropped entirely
    for that status. ``"partial"`` keeps the original, narrower
    ``source_abi``/``compile_``/``source_graph`` prefix scope deliberately
    -- a real, confirmed-complete-but-edge-free L5 graph pass records its own
    ``status="partial"`` for a fully legitimate, non-incomplete reason (see
    :func:`_graph_completeness`'s own ``confirmed`` exemption for that exact
    shape), and broadening ``"partial"`` the same way ``"failed"`` was
    broadened would resurrect that already-fixed false positive here instead
    of in :func:`_graph_completeness`, which is the one place with the
    context (``extractor_passes``/``narrowed_passes``) to tell the two
    apart.
    """
    notes: list[str] = []
    incomplete = False
    watched_layers = (DataLayer.L3_BUILD.value, DataLayer.L4_SOURCE_ABI.value)
    for side, pack in (("old", old_pack), ("new", new_pack)):
        manifest = getattr(pack, "manifest", None)
        if manifest is None:
            continue
        for layer in watched_layers:
            row = manifest.coverage_for(layer)
            if row is None or row.status != CoverageStatus.PARTIAL:
                continue
            incomplete = True
            detail = f": {row.detail}" if row.detail else ""
            notes.append(
                f"{side} side's build-source pack manifest records "
                f"{layer} coverage as 'partial'{detail}"
            )
        for rec in manifest.extractors:
            if rec.status == "failed":
                incomplete = True
                detail = f": {rec.detail}" if rec.detail else ""
                notes.append(
                    f"{side} side's {rec.name!r} extractor status is 'failed'{detail}"
                )
                continue
            if rec.status != "partial":
                continue
            if not rec.source_side:
                continue
            incomplete = True
            detail = f": {rec.detail}" if rec.detail else ""
            notes.append(
                f"{side} side's {rec.name!r} extractor status is 'partial'{detail}"
            )
    return incomplete, notes

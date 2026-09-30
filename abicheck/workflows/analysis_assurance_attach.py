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
``service_compare_pipeline.classify_compare_pair`` (Tier 2) -- had
byte-identical copies of this call. That was survivable while it was three
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

from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot

__all__ = ["attach_analysis_assurance", "attach_evidence_depths"]


def attach_analysis_assurance(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    """Compute *result*'s assurance rollup from *old*/*new* and store it.

    The packs are read off the snapshots rather than taken as parameters:
    both call sites passed exactly this, and a caller holding a *different*
    pack (the native ``compare`` CLI, which reloads an uncapped one) calls
    ``compute_analysis_assurance`` directly and passes it explicitly.
    """
    from ..analysis_assurance import compute_analysis_assurance
    from ..evidence_depth import embedded_evidence_pack
    from ..storage.snapshot_encode import same_persisted_content

    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        # A header-only side's graph stands in as its pack (ADR-063 Phase 10).
        old_pack=embedded_evidence_pack(old) if old is not None else None,
        new_pack=embedded_evidence_pack(new),
        same_content=(
            partial(same_persisted_content, old, new) if old is not None else None
        ),
    )


def attach_evidence_depths(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> None:
    """Stamp ``old_evidence_depth``/``new_evidence_depth`` onto *result*.

    The evidence depth each side *actually* carries (the gate's
    :func:`~abicheck.evidence_depth.reported_depth_label` rule), read off the
    snapshots' own -- already depth-capped -- ``build_source`` packs.

    Owned here rather than by a front end: the native ``compare`` CLI used to
    be the only route that set these two report fields, so a typed-API
    (``run_compare_request``) report and every release member's report
    carried ``null`` for the identical comparison (F2 route-parity harness).
    Both pairwise Tier-2 paths now call this one function.
    """
    from ..evidence_depth import reported_depth_label

    result.old_evidence_depth = reported_depth_label(old, old.build_source)
    result.new_evidence_depth = reported_depth_label(new, new.build_source)

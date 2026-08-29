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

"""Workflow-layer surface for ``scan``'s cost estimation (ADR-061 Phase 4).

``abicheck/frontends`` may import only ``abicheck.model``,
``abicheck.workflows``, and ``abicheck.report`` (see
``abicheck/frontends/AGENTS.md``'s "Permitted imports") -- it must not reach
into the flat, not-yet-migrated ``abicheck.service_scan`` implementation
module directly. This module is that seam for the one operation
``frontends.cli.artifact_set_dry_run`` needs: projecting a per-member cost
for an ``--artifact-set`` preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Coarse per-member cost anchor (seconds) for the cross-library bundle-audit
#: pass (``bundle.audit_bundle`` -> ``build_bundle_snapshot`` + SONAME/export
#: resolution): the same order of magnitude as ``service_scan``'s own
#: ``L0_binary`` "binary export table parse" row (0.1s/binary), since both
#: are ELF/dynsym-only, no-compiler-invocation passes over the same file. Not
#: read from ``service_scan`` itself (that module is ``no_growth``-debt-
#: tracked, at its own line-count baseline) -- a duplicated *magnitude*, not
#: duplicated *logic*, so it carries none of the drift risk a second resolved
#: -level computation would (Codex review: the projected total previously
#: excluded this pass entirely, understating --budget planning for large sets).
_COST_PER_MEMBER_BUNDLE_AUDIT = 0.1


def estimate_artifact_set(
    req: Any, member_paths: list[Path]
) -> tuple[dict[str, tuple[int, float]], list[str]]:
    """Project a per-layer cost total across every member of an
    ``--artifact-set`` request.

    *req* is the already-assembled, set-wide ``ScanRequest`` the real run
    would submit to ``service_scan.run_scan_set``; one single-binary
    ``ScanRequest`` is built per entry in *member_paths* from *req*'s shared
    fields, and each is estimated independently via
    ``service_scan.estimate_scan`` (its own L1-L5 rows don't scale with
    ``len(binaries)``, so a single shared-request estimate would understate
    the total by roughly N×).

    The risk-driven ``(SourceMethod, EvidenceDepth)`` level is resolved
    **once**, via ``service_scan._resolve_member_scan_level`` -- the same
    function ``_run_scan_one_member`` (the real per-member execution path)
    calls, per ``workflows/AGENTS.md``'s "Dry-run and execution must consume
    the same resolved plan" rule -- rather than an independent copy of that
    precedence that could silently drift from it. This also means a
    malformed ``--risk-rules`` profile raises the identical ``ValueError``
    the real run raises, so the preview fails the same way rather than
    silently reporting success. ``changed_paths`` (and so the risk score) is
    shared across the whole set, not per-member, so resolving it once here
    is correct, not just cheaper.

    Returns ``(totals, notes)``: *totals* maps each touched layer name to
    its summed ``(tus, est_seconds)`` across every member, plus one
    ``"bundle_audit"`` entry pricing the cross-library bundle-audit pass
    ``run_scan_set`` performs once over the whole set; *notes* is the
    deduplicated set of every per-estimate caveat (e.g. an ``--build-target``
    TU-count scoping warning) any member's estimate carried, so an aggregated
    preview doesn't silently drop a caveat a maintainer needs to see.
    """
    from ..service_scan import ScanRequest, _resolve_member_scan_level, estimate_scan

    _sm, _dp, _changed, _seeded, _risk, resolved, eff_depth, _collect_mode = (
        _resolve_member_scan_level(req)
    )
    resolved_level = (resolved, eff_depth)
    totals: dict[str, tuple[int, float]] = {}
    notes: list[str] = []
    seen_notes: set[str] = set()
    for member_path in member_paths:
        member_req = ScanRequest(
            binaries=[member_path],
            headers=list(req.headers),
            includes=list(req.includes),
            sources=req.sources,
            build_info=req.build_info,
            mode="audit",
            source_method=req.source_method,
            depth=req.depth,
            changed_paths=list(req.changed_paths),
            seeded=req.seeded,
            budget=req.budget,
            lang=req.lang,
            build_targets=req.build_targets,
        )
        for e in estimate_scan(member_req, resolved_level=resolved_level):
            tus, seconds = totals.get(e.layer, (0, 0.0))
            totals[e.layer] = (tus + e.tus, seconds + e.est_seconds)
            if e.note and e.note not in seen_notes:
                seen_notes.add(e.note)
                notes.append(e.note)
    # The cross-library bundle-audit pass (run once over the whole set, not
    # per member) is real, budgeted work `run_scan_set` performs -- price it
    # too, rather than silently excluding it from the projected total.
    totals["bundle_audit"] = (
        len(member_paths), _COST_PER_MEMBER_BUNDLE_AUDIT * len(member_paths),
    )
    return totals, notes

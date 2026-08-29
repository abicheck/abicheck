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


def _resolve_level(req: Any) -> tuple[Any, Any]:
    """Resolve the ``(SourceMethod, EvidenceDepth)`` level *req* would use
    for a real run, honoring ``req.risk_rules_path`` (``ValueError`` on a
    malformed profile) the same way ``service_scan.run_scan``/
    ``_run_scan_one_member`` do -- inlined here rather than factored into
    ``service_scan.py`` itself (also ``no_growth``-debt-tracked, at its own
    line-count baseline) since this is the one caller needing the level
    resolved *ahead of* :func:`~abicheck.service_scan.estimate_scan`,
    instead of that function's own ``RiskRules.default()`` fallback.
    """
    from ..buildsource.risk import RiskRules, score_changed_paths
    from ..buildsource.scan_levels import (
        ScanMode,
        SourceMethod,
        parse_user_depth,
        resolve_level,
    )
    from ..service_scan import _load_risk_rules_for_service

    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)
    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = (
        _load_risk_rules_for_service(req.risk_rules_path)
        if req.risk_rules_path is not None
        else RiskRules.default()
    )
    risk = score_changed_paths(changed, risk_rules)
    auto_method = risk.recommended_method if (sm is SourceMethod.AUTO and seeded) else None
    return resolve_level(
        mode=ScanMode(req.mode), source_method=sm, depth=dp, auto_method=auto_method
    )


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
    **once**, via :func:`_resolve_level` -- honoring *req*'s own
    ``risk_rules_path`` -- rather than left to each per-member
    ``estimate_scan()`` call to re-derive it from ``RiskRules.default()``,
    which would silently ignore a caller's ``--risk-rules`` profile and can
    project a different layer/cost than the real run. ``changed_paths`` (and
    so the risk score) is shared across the whole set, not per-member, so
    resolving it once is correct, not just cheaper. Raises ``ValueError`` on
    a malformed ``--risk-rules`` profile -- the same failure the real run
    surfaces -- so the preview fails the same way the run would, rather than
    silently reporting success.

    Returns ``(totals, notes)``: *totals* maps each touched layer name to
    its summed ``(tus, est_seconds)`` across every member; *notes* is the
    deduplicated set of every per-estimate caveat (e.g. an ``--build-target``
    TU-count scoping warning) any member's estimate carried, so an aggregated
    preview doesn't silently drop a caveat a maintainer needs to see.
    """
    from ..service_scan import ScanRequest, estimate_scan

    resolved_level = _resolve_level(req)
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
    return totals, notes

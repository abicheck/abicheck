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

"""``compare --dry-run``'s "Cost preview" section -- the *compute* half
(one-comparison-product.md #3, plan item #35, Phase 2f).

``scan --dry-run`` already projects L0-L5 evidence-collection cost via
:func:`abicheck.service_scan.estimate_scan` (see ``cli_scan.py``'s own
``--dry-run`` branch); ``compare --dry-run`` had no equivalent preview. This
module reuses :func:`~abicheck.service_scan.estimate_scan` directly -- no
new cost model -- and adds only the compare-specific glue: resolving
compare's own ``--depth``/``.abicheck.yml`` ``source.method``/
``--sources``/``--build-info`` precedence into the
``(SourceMethod, EvidenceDepth)`` pair ``estimate_scan``'s ``resolved_level``
takes, building one :class:`~abicheck.api_types.InputSpec` per side, and
summing both sides' rows layer-by-layer (:func:`_merge_layer_estimates`) --
a real ``compare`` run with live source/build evidence extracts *both*
operands, so the preview sums each side's own projection.

A new, dedicated leaf module rather than an addition to
:mod:`abicheck.cli_compare_helpers` or :mod:`abicheck.service_scan`: both
already sit at their own ``architecture/debt.yaml`` ``no_growth`` baseline
with no room for a new function -- the same "prefer extending a split-out
module over growing the parent toward the cap" guidance in the root
``CLAUDE.md`` that :mod:`abicheck.frontends.cli.release_dry_run` already
follows. Deliberately compute-only (no ``click``, no ``DryRunResult``):
:mod:`abicheck.frontends.cli.compare_dry_run` owns turning this module's
output into report lines, mirroring ``frontends/cli/scan_dry_run.py``'s own
split between a workflow's computed result and its CLI rendering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.evidence_depth_levels import EvidenceDepth, SourceMethod
    from ..service_scan import CostEstimate


def _resolve_compare_estimate_level(
    depth: str | None,
    source_method: str | None,
    old_sources: Path | None, new_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
) -> tuple[SourceMethod, EvidenceDepth]:
    """The (S-method, L-depth) pair a compare's cost-preview probes are priced
    at -- mirrors ``cli_compare_helpers._resolve_compare_collect_mode``'s own
    precedence (explicit ``--depth`` > ``.abicheck.yml`` ``source.method`` >
    inferred from ``--sources``/``--build-info`` > off), but returns the
    resolved ``(SourceMethod, EvidenceDepth)`` pair rather than a
    collect-mode string -- the shape
    :func:`~abicheck.service_scan.estimate_scan`'s ``resolved_level`` takes,
    mirroring how ``cli_scan.py`` pre-resolves its own ``(resolved,
    eff_depth_enum)`` before calling it rather than letting the callee
    re-derive a level from ``estimate_scan``'s own mode-preset default, which
    has no notion of compare's --sources/--build-info inference rule (or of
    compare having no PR change seed for ``auto`` to escalate against)."""
    from ..model.evidence_depth_levels import (
        EvidenceDepth,
        SourceMethod,
        depth_to_method,
        method_to_depth,
    )

    if depth is not None:
        evidence_depth = EvidenceDepth(depth.lower())
        return (depth_to_method(evidence_depth) or SourceMethod.S0), evidence_depth
    if source_method:
        method = SourceMethod(source_method)
        if method is SourceMethod.AUTO:
            # compare has no PR change seed for `auto` to score a risk-driven
            # escalation against (unlike `scan --mode auto`) -- price the
            # S0/headers-only floor rather than guessing a deeper level.
            return SourceMethod.S0, EvidenceDepth.HEADERS
        return method, method_to_depth(method)
    if old_sources is not None or new_sources is not None:
        return SourceMethod.S5, EvidenceDepth.SOURCE
    if old_build_info is not None or new_build_info is not None:
        return SourceMethod.S1, EvidenceDepth.BUILD
    return SourceMethod.S0, EvidenceDepth.HEADERS


def _merge_layer_estimates(
    estimate_lists: tuple[list[CostEstimate], list[CostEstimate]],
) -> list[CostEstimate]:
    """Sum two :func:`~abicheck.service_scan.estimate_scan` results layer-by-
    layer -- a real ``compare`` run with live source/build evidence extracts
    *both* operands, so the projected cost is each side's own row summed,
    the same per-operand aggregation
    :func:`~abicheck.service_scan.estimate_scan` already produces per
    operand, applied here across a compare's two operands instead. Introduces no separate cost model -- every row still
    comes straight out of :func:`~abicheck.service_scan.estimate_scan`."""
    from ..service_scan import CostEstimate

    totals: dict[str, tuple[str | None, int, float]] = {}
    order: list[str] = []
    notes: dict[str, list[str]] = {}
    for estimates in estimate_lists:
        for e in estimates:
            if e.layer not in totals:
                order.append(e.layer)
                totals[e.layer] = (e.method, 0, 0.0)
                notes[e.layer] = []
            method, tus, seconds = totals[e.layer]
            totals[e.layer] = (method or e.method, tus + e.tus, seconds + e.est_seconds)
            if e.note and e.note not in notes[e.layer]:
                notes[e.layer].append(e.note)
    return [
        CostEstimate(
            totals[layer][0],
            layer,
            totals[layer][1],
            totals[layer][2],
            0.0,
            "; ".join(notes[layer]),
        )
        for layer in order
    ]


def estimate_compare_dry_run_cost(
    *,
    old_input: Path, new_input: Path,
    depth: str | None, source_method: str | None,
    headers: tuple[Path, ...], includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_sources: Path | None, new_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
) -> tuple[list[CostEstimate] | None, str | None]:
    """Combined old+new per-layer cost preview for ``compare --dry-run``.

    Returns ``(estimates, None)`` on success or ``(None, error)`` when the
    probe itself raised -- mirroring ``cli_scan.py``'s own best-effort
    ``estimate_scan`` call, which the dry run must never let a probe failure
    turn into a hard crash."""
    from ..api_types import InputSpec
    from ..service_scan import estimate_scan

    try:
        resolved_level = _resolve_compare_estimate_level(
            depth, source_method, old_sources, new_sources, old_build_info, new_build_info,
        )
        common_headers = list(headers) + list(includes)
        old_side = InputSpec.of(
            old_input,
            headers=common_headers + list(old_headers_only),
            includes=list(includes),
            sources=old_sources,
            build_info=old_build_info,
        )
        new_side = InputSpec.of(
            new_input,
            headers=common_headers + list(new_headers_only),
            includes=list(includes),
            sources=new_sources,
            build_info=new_build_info,
        )
        old_estimates = estimate_scan(old_side, resolved_level=resolved_level)
        new_estimates = estimate_scan(new_side, resolved_level=resolved_level)
        return _merge_layer_estimates((old_estimates, new_estimates)), None
    except Exception as exc:  # noqa: BLE001 - best-effort dry-run probe
        return None, str(exc)

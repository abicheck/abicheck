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

"""``scan --artifact-set --dry-run`` report builder (CLI cleanup phase two,
PR 5 / G35's own "dry-run/estimator" gap).

Lives under :mod:`abicheck.frontends.cli` (ADR-061: CLI-owned rendering is
``frontends/`` responsibility) rather than in :mod:`abicheck.cli_scan` (the
``no_growth``-debt-tracked, near-2000-line-cap module its single-binary
sibling ``render_scan_dry_run`` lives in).

Takes the per-layer cost ``totals``/``notes`` as already-computed data
(``cli_scan._run_artifact_set`` calls ``service_scan.estimate_artifact_set``
and passes the result in) rather than computing them itself: a module in
between ``cli_scan`` and ``service_scan`` that imported the latter directly
or transitively (via a ``workflows`` seam) would join the large, already-
accepted CLI-registration import cycle those two modules both already sit
in (``cli -> cli_scan -> ... -> service_scan -> scan_engine ->
cli_scan_baseline -> cli_buildsource -> cli``) -- growing that cycle's
membership, which the AI-readiness ``import-cycle-growth`` gate rejects.
Taking already-computed data instead means this module needs no import of
``service_scan``/``workflows`` at all, which also happens to be the purest
reading of ``frontends/AGENTS.md``'s "Permitted imports" (it translates a
result into a process response; it does not compute the result itself).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_artifact_set_dry_run(
    req: Any,
    *,
    discovered: dict[str, Path],
    explicit: bool,
    header_backend: str,
    fmt: str,
    totals: dict[str, tuple[int, float]],
    notes: list[str],
) -> Any:
    """Build the report from *req* (the set-wide ``ScanRequest``) and the
    already-computed ``(totals, notes)`` (``service_scan.
    estimate_artifact_set``'s return value). ``discovered``/``explicit`` are
    already resolved and ELF-validated by the time this runs
    (``cli_scan._resolve_artifact_set_paths`` + ``bundle.
    discover_artifact_set``), the same as the real run.
    """
    from ...dry_run import DryRunResult, tool_status

    result = DryRunResult(command="scan")
    members = sorted(discovered.items())
    result.add(
        "Inputs",
        f"--artifact-set form: {'explicit path list' if explicit else 'directory'}",
        f"members ({len(members)}):",
        *(f"  - {name}: {path}" for name, path in members),
        f"--bundle-system-providers: {', '.join(req.bundle_system_providers)}"
        if req.bundle_system_providers
        else None,
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {req.depth or '(auto per member)'}",
        f"changed paths ({req.changed_src}): {len(req.changed_paths)}",
        *(
            f"{layer}: {tus} TU(s) total, ~{seconds:.2f}s -- summed over "
            f"{len(members)} member(s)"
            for layer, (tus, seconds) in totals.items()
        ),
        f"projected total: {sum(seconds for _tus, seconds in totals.values()):.2f}s",
        "note: each member is estimated independently and summed; "
        "'bundle_audit' prices the one cross-library pass run once over "
        "the whole set (ELF/dynsym-only, no compiler invocation)",
        *notes,
    )
    result.add("Headers and compile context", f"ast-frontend: {header_backend}")
    result.add(
        "Build/source inputs",
        f"--sources: {req.sources}" if req.sources else None,
        f"--build-info: {req.build_info}" if req.build_info else None,
        f"--build-target: {', '.join(req.build_targets)}" if req.build_targets else None,
        "note: the same declared header/build/source inputs are given to "
        "every member's own scan (cross-member header-obligation "
        "attribution) -- they are not per-member-scoped inputs.",
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Consumer/contract scoping",
        "audit checks: always run per member (pattern pre-scan + "
        "intra-version cross-source)",
        "cross-library bundle audit: will run over the whole set "
        "(resolution graph, unresolved intra-dependency detection)",
        "compatibility comparison: will NOT run (--artifact-set is "
        "audit-only, no old side)",
    )
    result.add("Output and exit-code behavior", f"format: {fmt}")
    return result

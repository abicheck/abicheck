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
sibling ``render_scan_dry_run`` lives in) or :mod:`abicheck.cli_scan_helpers`
(which deliberately never imports :mod:`abicheck.service` -- that would
close an import cycle back through ``service -> service_scan -> scan_engine
-> cli_scan_helpers``, see that module's own docstring). Nothing imports
this module back, so it can depend on :mod:`abicheck.service` directly the
same way ``cli_scan.py`` itself does.
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
) -> Any:
    """Build the report. Takes the already-assembled set-wide
    ``ScanRequest`` (``req``) the real run would submit to
    :func:`~abicheck.service.run_scan_set`, rather than its fields spelled
    out one by one, to keep the call site short; ``discovered``/
    ``explicit``/``header_backend``/``fmt`` aren't ``ScanRequest`` fields,
    so those four stay explicit. ``discovered``/``explicit`` are already
    resolved and ELF-validated by the time this runs
    (``cli_scan._resolve_artifact_set_paths`` +
    ``bundle.discover_artifact_set``), the same as the real run -- a
    malformed member fails loud before any dry-run text is printed.

    The cost projection is summed **per member**, not read off one shared
    :func:`~abicheck.service.estimate_scan` call the way a naive port of
    the single-binary preview would: only its L0_binary row scales by
    ``len(binaries)`` (a known, still-open gap in the shared estimator for
    other callers). Building one single-binary ``ScanRequest`` per
    discovered member (reusing ``req``'s own shared fields) and summing
    each member's own :func:`~abicheck.service.estimate_scan` result gives
    a genuinely per-member-scaled total for this preview specifically,
    without changing ``estimate_scan``'s own single-request contract.
    """
    from ...dry_run import DryRunResult, tool_status
    from ...service import ScanRequest, estimate_scan

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
    try:
        from ...buildsource.scan_levels import SourceMethod

        totals: dict[str, tuple[int, float]] = {}
        for _name, member_path in members:
            member_req = ScanRequest(
                binaries=[member_path],
                headers=list(req.headers),
                includes=list(req.includes),
                sources=req.sources,
                build_info=req.build_info,
                mode="audit",
                source_method=SourceMethod.AUTO.value if req.depth is None else None,
                depth=req.depth,
                changed_paths=list(req.changed_paths),
                seeded=req.seeded,
                budget=req.budget,
                lang=req.lang,
                build_targets=req.build_targets,
            )
            for e in estimate_scan(member_req):
                tus, seconds = totals.get(e.layer, (0, 0.0))
                totals[e.layer] = (tus + e.tus, seconds + e.est_seconds)
        total_seconds = sum(seconds for _tus, seconds in totals.values())
        result.add(
            "Resolved depth and source scope",
            *(
                f"{layer}: {tus} TU(s) total, ~{seconds:.2f}s -- summed over "
                f"{len(members)} member(s)"
                for layer, (tus, seconds) in totals.items()
            ),
            f"projected total: {total_seconds:.2f}s",
            "note: each member is estimated independently and summed; this "
            "does not price the cross-library bundle-audit pass itself "
            "(cheap ELF/dynsym-only, no compiler invocation)",
        )
    except Exception as exc:  # pragma: no cover - best-effort probe
        result.warn(f"could not project per-layer cost: {exc}")
    return result

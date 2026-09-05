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

Takes the per-layer cost ``totals``/``notes``/``blocker``/``unknown_layers``
as already-computed data (``cli_scan._run_artifact_set`` calls
``service_scan.estimate_artifact_set`` and passes the result in) rather than
computing them itself: a module in between ``cli_scan`` and ``service_scan``
that imported the latter directly or transitively would join the large,
already-accepted CLI-registration import cycle those two modules both
already sit in (``cli -> cli_scan -> ... -> service_scan -> scan_engine ->
cli_scan_baseline -> cli_buildsource -> cli``) -- growing that cycle's
membership, which the AI-readiness ``import-cycle-growth`` gate rejects. It
does import one leaf ``workflows`` module, :mod:`abicheck.workflows.
scan_abi3_dry_run` (the ``--abi3`` precondition check, CLI cleanup phase two
PR 5 follow-up) -- that module (via :mod:`abicheck.scan_abi3_resolve`)
depends only on :mod:`abicheck.python_ext` and the still-unclassified
:mod:`abicheck.serialization`, neither of which sits in the CLI-registration
cycle, so it stays outside that cycle entirely (``frontends -> extract`` is
otherwise forbidden by ADR-061, which is why this renderer cannot call
``python_ext`` directly).
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
    blocker: str | None = None,
    unknown_layers: frozenset[str] = frozenset(),
) -> Any:
    """Build the report from *req* (the set-wide ``ScanRequest``) and the
    already-computed ``(totals, notes, blocker, unknown_layers)``
    (``service_scan.estimate_artifact_set``'s return value).
    ``discovered``/``explicit`` are already resolved and ELF-validated by
    the time this runs (``cli_scan._resolve_artifact_set_paths`` + ``bundle.
    discover_artifact_set``), the same as the real run. *blocker*, when set,
    is routed through :meth:`DryRunResult.block` so the preview's own exit
    code (1) matches the real run's ``EVIDENCE_CONTRACT_ERROR`` exit code,
    the same convention every other command's dry-run blocker follows.

    *unknown_layers* names which ``totals`` keys sum at least one member's
    genuinely-unknown TU count (Codex review, fresh evidence): an earlier
    revision hardcoded this treatment to ``L3_build`` only, so a query-only
    build config's L4/L5 rows still showed a confident-looking numeric zero
    even once L3's own row was fixed to say "unknown" -- ``totals`` sums
    numbers across members and layers, so it cannot itself tell "zero" from
    "never counted" apart, and neither can a single project-wide
    ``any_unknown`` flag distinguish *which* layer that applies to.
    """
    from ...dry_run import DryRunResult, tool_status

    result = DryRunResult(command="scan")
    members = sorted(discovered.items())
    result.add(
        "Inputs",
        f"--artifact-set form: {'explicit path list' if explicit else 'directory'}",
        f"members ({len(members)}):",
        *(f"  - {name}: {path}" for name, path in members),
        # CLI cleanup phase two, PR J: sourced from .abicheck.yml's
        # `bundle.system_providers:`, not a CLI flag any more.
        f"system providers (.abicheck.yml): {', '.join(req.bundle_system_providers)}"
        if req.bundle_system_providers
        else None,
    )
    # A member whose own L3 (or, since Codex review, L4/L5) estimate is
    # genuinely unknown (a query-only build.query, service_scan.
    # _estimate_total_tus) still contributes a numeric (0, 0.0) into
    # `totals`'s sum -- rendering it as a confident "0 TU(s) total, ~0.00s"
    # would read as a real near-zero cost rather than "not counted", the
    # same defect the single-binary renderer fixes for its own per-layer
    # row. Checked per *layer* (`unknown_layers`), not via a single
    # project-wide flag hardcoded to L3_build: L4/L5 derive their own counts
    # from L3's and go unknown right along with it, and a global flag with a
    # hardcoded layer name can't tell them apart.
    any_unknown = bool(unknown_layers)
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {req.depth or '(auto per member)'}",
        f"changed paths ({req.changed_src}): {len(req.changed_paths)}",
        *(
            f"{layer}: TU count/cost unknown for at least one member (see notes below)"
            if layer in unknown_layers
            else f"{layer}: {tus} TU(s) total, ~{seconds:.2f}s -- summed over "
            f"{len(members)} member(s)"
            for layer, (tus, seconds) in totals.items()
        ),
        f"projected total: {sum(seconds for _tus, seconds in totals.values()):.2f}s",
        "note: each member is estimated independently and summed; "
        "'bundle_audit' prices the one cross-library pass run once over "
        "the whole set (ELF/dynsym-only, no compiler invocation)",
        "note: at least one member's TU count/cost is unknown (see above) "
        "-- it contributes 0.0s to the projected total, understating it"
        if any_unknown
        else None,
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
    manifest = getattr(req, "bundle_manifest", None)
    result.add(
        "Consumer/contract scoping",
        "audit checks: always run per member (pattern pre-scan + "
        "intra-version cross-source)",
        "cross-library bundle audit: will run over the whole set "
        "(resolution graph, unresolved intra-dependency detection, "
        "duplicate-provider ownership ambiguity)",
        (
            f"--manifest: {len(manifest.entries)} expected-provider "
            f"{'entry' if len(manifest.entries) == 1 else 'entries'} will "
            "be checked against this set"
        )
        if manifest is not None
        else "--manifest: not given -- no expected-provider ownership check",
        "compatibility comparison: will NOT run (--artifact-set is "
        "audit-only, no old side)",
    )
    result.add("Output and exit-code behavior", f"format: {fmt}")
    from ...workflows.scan_abi3_dry_run import apply_abi3_dry_run_check_set

    apply_abi3_dry_run_check_set(result, members, req.abi3_floor)
    if blocker:
        result.block(blocker)
    return result

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

"""``compare --no-baseline --dry-run``\'s report (ADR-068 D2, ADR-043 D4).

A leaf sibling of :mod:`abicheck.frontends.cli.compare_dry_run`, deliberately
kept out of that module for two independent reasons:

* **Shape.** The two-sided builder\'s whole structure is two operands --
  ``old:``/``new:`` input lines, a two-sided cost preview, an old-vs-new
  build/source pair -- and rendering a single-build audit through it would
  print the candidate twice under two labels, exactly the "a baseline was
  consulted" misreading ADR-068 D2 forbids.
* **Imports.** ``compare_dry_run`` reaches ``workflows.compare_cost_preview``
  and ``dry_run_estimate``, which close the ``cli_options -> dry_run_estimate ->
  scan_engine -> cli_scan_baseline -> cli_compare_helpers`` CLI-registration
  SCC. A ``--no-baseline`` dispatch importing that module -- even
  function-locally, which the AI-readiness scan counts too -- would join that
  cluster, which the ``import-cycle-growth`` gate rejects for a new member
  (``AGENTS.md`` "What NOT to do": move the shared logic to a leaf module,
  never extend ``IMPORT_CYCLE_ALLOWLIST``). This module imports only
  :mod:`abicheck.dry_run`, itself a leaf.

The section *titles* are the shared canonical ones
(:data:`abicheck.dry_run.SECTION_ORDER`), so the two reports still read the
same way despite being built separately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["build_no_baseline_dry_run_result"]


def build_no_baseline_dry_run_result(
    *,
    candidate: Path,
    depth: str | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    public_header_dirs: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    fmt: str,
    contract_mode: str | None,
    candidate_is_live: bool = True,
) -> Any:
    """Build the ``compare --no-baseline --dry-run`` report (ADR-068 D2).

    A sibling of :func:`build_compare_dry_run_result` rather than a call
    into it: that function's whole shape is two operands (``old:``/``new:``
    input lines, a two-sided cost preview, an old-vs-new build/source pair),
    and rendering a single-build audit through it would print the candidate
    twice under two labels -- exactly the "a baseline was consulted"
    misreading ADR-068 D2 forbids. The section *titles* are the shared
    canonical ones (``dry_run.SECTION_ORDER``), so the two reports still read
    the same way.

    Reports, rather than silently accepting, the two things this invocation
    can get wrong before any analysis runs: a pinned ``--depth build``/
    ``--depth source`` with no ``--sources``/``--build-info`` to satisfy it
    (a blocker -- the real run exits 7 on the same condition, so a dry run
    that called it fine would be lying), and ``--contract`` naming a domain
    whose evidence this candidate may not carry (a note, since only the real
    evidence collection can answer it).

    *candidate_is_live* carries the real run's own live/stored carve-out
    (``policy.depth_evidence_contract``'s "Live extraction only" note): a
    stored ``.abi.json`` candidate was never extracted by this run, so it
    cannot fall short of a pinned depth and the real run exempts it. Without
    the same exemption here the preview claimed a blocker (exit 1) for an
    invocation that really exits 0 -- a dry run that disagrees with the run
    it previews is worse than no dry run (Codex review, P2).
    """
    from ...dry_run import DryRunResult, tool_status

    result = DryRunResult(command="compare --no-baseline")
    result.add(
        "Inputs",
        f"candidate: {candidate} ({'directory' if candidate.is_dir() else 'file'})",
        "baseline: (none -- OLD declared absent via --no-baseline)",
    )
    collects_source = (
        depth is not None and depth.lower() in ("build", "source") and candidate_is_live
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(not given)'}",
        "candidate is a stored snapshot: the depth floor does not apply "
        "(this run performs no extraction)"
        if not candidate_is_live
        else None,
        "source scope: candidate only (an audit has no second side to scope against)",
    )
    result.add(
        "Headers and compile context",
        f"headers: {', '.join(str(h) for h in headers)}" if headers else None,
        f"includes: {', '.join(str(i) for i in includes)}" if includes else None,
        f"public header dirs: {', '.join(str(d) for d in public_header_dirs)}"
        if public_header_dirs
        else None,
    )
    result.add(
        "Build/source inputs",
        f"candidate sources/build-info: {sources or build_info or '(none given)'}",
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Consumer/contract scoping",
        f"--contract: {contract_mode or '(not given -- no contract evaluation)'}",
        "contract coverage of the selected domain contributes an orthogonal exit 1"
        if contract_mode
        else None,
    )
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        "no compatibility verdict is reported (ADR-068 D2); exit code folds the "
        "coverage, analysis-assurance and evidence-contract axes only",
    )
    if collects_source and sources is None and build_info is None:
        result.block(
            f"--depth {depth} pins evidence this run has no input to collect: pass "
            "--sources or --build-info. Without one, the real run records an "
            "evidence-contract error and exits 7 rather than degrading silently."
        )
    elif collects_source:
        # Deliberately a warning, not a second blocker: an input is present,
        # but only the real collection can say whether it reaches the pinned
        # depth -- predicting that here would be guessing, and a dry run that
        # guessed wrong in either direction is worse than one that says so.
        result.warn(
            f"--depth {depth} is satisfied only if evidence collection from the "
            "input above succeeds; a dry run cannot confirm it, and the real run "
            "exits 7 if the floor is not reached."
        )
    return result

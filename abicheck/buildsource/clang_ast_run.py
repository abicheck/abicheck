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

"""One bounded ``clang -Xclang -ast-dump=json`` run, shared by the L5 passes.

A **leaf** module: the call-graph (``call_graph.py``) and type-graph
(``type_graph.py``) extractors run the *identical* procedure — same argv shape,
same 120s local cap folded against the active ``--budget`` deadline, same
degrade-to-``diagnostic``-and-give-up contract at every failure point — and
differ only in which pure parser they hand the resulting AST to. Both used to
carry their own copy, kept in step by hand through comments pointing at each
other ("mirrors ``call_graph.ClangCallGraphExtractor._extract_from_safe_args``")
across several review rounds of deadline fixes; stated once here instead, so a
fix to the bounding or the diagnostics cannot land on one pass only (CodeFactor:
duplicate code).

Imports nothing from either caller, so neither has to import the other's
internals for this.
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 - AST extraction shells out to clang (never shell=True)
from typing import Any

from .. import deadline

#: Per-TU wall-clock ceiling for one AST dump. Folded against the scan's own
#: remaining ``--budget`` so one hung TU can never eat the whole scan.
LOCAL_CAP_S = 120.0


def run_clang_ast_dump(
    clang_bin: str,
    argv: list[str],
    *,
    cwd: str | None,
    diagnostics: list[str],
) -> dict[str, Any] | None:
    """Dump one TU's clang AST as JSON, or ``None`` with a recorded diagnostic.

    *argv* must already be the caller's sanitized, allowlisted flag subset —
    this function only prepends the fixed dump flags and never consults a
    shell. Every failure path (a failed invocation, empty output, an exhausted
    budget, unparseable JSON) appends to *diagnostics* and answers ``None``:
    these passes are advisory (ADR-028 D3), never authoritative, so a probe
    failure must degrade rather than abort the scan. Whether ``clang`` is
    present at all stays the caller's own ``available()`` check — that is an
    overridable method on each extractor, not a property of this run.

    A non-zero clang exit is *not* a failure path — clang still prints the
    partial, error-recovered tree it built from the necessarily approximate
    replayed flags, and salvaging edges from it is deliberate. It does record a
    diagnostic, so ``extractor_pass_fully_covered`` (ADR-041 P0 slice 3) never
    counts that TU as cleanly, fully parsed.
    """
    cmd = [clang_bin, "-Xclang", "-ast-dump=json", "-fsyntax-only", *argv]
    scan_remaining = deadline.remaining()
    effective_timeout = (
        LOCAL_CAP_S if scan_remaining is None else min(LOCAL_CAP_S, scan_remaining)
    )
    try:
        # Bound by min(LOCAL_CAP_S, active --budget deadline) — run_bounded()
        # alone would honor a generous outer deadline verbatim instead of this
        # pass's own cap, letting one hung TU eat the whole remaining scan
        # budget — and process-group-safe on timeout, same as the L2/L4 clang
        # calls (Codex review, PR #591, round 8).
        with deadline.deadline_scope(effective_timeout):
            proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=LOCAL_CAP_S,
            )
    except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded) as exc:
        diagnostics.append(f"clang invocation failed: {exc}")
        return None
    if not proc.stdout.strip():
        diagnostics.append(f"clang produced no AST (stderr: {proc.stderr[:200]})")
        return None
    if proc.returncode != 0:
        diagnostics.append(
            f"clang exited {proc.returncode} (stderr: {proc.stderr[:200]})"
        )
    try:
        # clang can exit successfully right as the budget expires; recheck
        # before the CPU/RSS-heavy parse+walk, same as the L2/L4 post-run
        # checks (Codex review, PR #591).
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        diagnostics.append(f"scan deadline exceeded before parsing clang AST: {exc}")
        return None
    try:
        # Both json.loads and the caller's recursive AST walk can hit Python's
        # recursion limit on a pathologically deep TU; guard so a degenerate AST
        # degrades to "no edges" rather than aborting collection.
        ast: dict[str, Any] = json.loads(proc.stdout)
    except (ValueError, RecursionError) as exc:
        diagnostics.append(f"could not parse clang AST JSON: {exc}")
        return None
    try:
        # The JSON load itself can consume the rest of the budget on a huge AST;
        # re-check before the recursive walk (Codex review, PR #591, round 4).
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        diagnostics.append(f"scan deadline exceeded before walking clang AST: {exc}")
        return None
    return ast

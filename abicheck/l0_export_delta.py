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

"""ADR-049 Phase 5 (§6.3): the one L0 hard-removal fold shared by ``compare``
and ``scan --against``'s baseline comparison.

Before this module, the same "re-resolve both sides symbols-only, diff them
unscoped, and keep only the ``func_removed_elf_only`` fact" logic was
hand-copied in two places -- ``cli_helpers_compare.fold_l0_hard_removals``
(direct ``compare``) and ``cli_scan_baseline._run_baseline_compare`` (``scan
--against``) -- each docstring explicitly cross-referencing the other as its
twin. :func:`collect_l0_export_delta` is the single implementation both now
call; this is the first concrete step of Phase 5's "route both direct compare
and scan baseline compare through the same core" goal, not the goal itself --
the two call sites still differ in every other stage (policy, suppression,
public-surface scoping, exit-code derivation), which is real, tracked,
still-open Phase 5 work (see the plan doc's Phase 5 progress note).

A function present in the ELF/DWARF exports can be entirely absent from the
header AST -- most commonly because it is declared behind a
consumer-controlled macro the header pass parses without knowing the real
build's ``-D`` set (``examples/case97_api_depends_on_consumer_env``). When
that happens the function never enters the header-scoped model on either
side, so the diff has nothing to compare it against and a real ``BREAKING``
removal is missed. Re-resolving both sides symbols-only (bypassing the
header AST entirely) and diffing them unscoped recovers that one hard fact
without manufacturing anything the ELF layer doesn't already assert
(ADR-028 D3: artifact-backed evidence stays authoritative).

Deliberately narrow: this module owns only the "resolve two paths
symbols-only and extract the hard fact" core. It does NOT own staleness
checking against an already-resolved snapshot's own ``source_path`` (that
stays in ``cli_helpers_compare.fold_l0_hard_removals``, since only the
direct-``compare`` call site re-derives paths from snapshots that could have
been read from a stale pre-dumped JSON file; ``scan --against`` already holds
the real, freshly-resolved ``Path`` objects and has nothing to go stale
against).
"""

from __future__ import annotations

from pathlib import Path

from .checker_types import Change


def collect_l0_export_delta(
    old_path: Path, new_path: Path, lang: str
) -> tuple[Change, ...]:
    """Re-resolve *old_path*/*new_path* symbols-only and return the hard
    ``func_removed_elf_only`` facts between them.

    Best-effort: if either path cannot be resolved (e.g. it no longer
    exists, or isn't a real binary/snapshot), returns an empty tuple rather
    than raising -- this is an enrichment on top of an already-succeeded
    compare, not something that should ever abort it.
    """
    from .errors import AbicheckError
    from .service import compare_snapshots, resolve_input

    # This deliberately re-resolves both sides with no headers -- the point
    # is to see what ELF/DWARF alone exports -- so the "no headers provided"
    # note `resolve_input` would otherwise log is expected, not a real input
    # problem; swallow it rather than confuse the user with a warning about
    # an internal probe they didn't ask for.
    try:
        l0_old = resolve_input(
            old_path,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
            notify=lambda _msg: None,
        )
        l0_new = resolve_input(
            new_path,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
            notify=lambda _msg: None,
        )
        # compare_snapshots is a thin wrapper over checker.compare -- a
        # failure there is just as much a "this best-effort probe didn't
        # pan out" case as a resolve_input failure, so it must not escape
        # this guard (Codex/CodeRabbit review, carried over from the
        # original compare-side implementation).
        l0_diff = compare_snapshots(
            l0_old, l0_new, extra_changes=[], scope_to_public_surface=False
        )
    except AbicheckError:
        return ()
    return tuple(
        change
        for change in getattr(l0_diff, "breaking", ())
        if getattr(getattr(change, "kind", None), "value", None)
        == "func_removed_elf_only"
    )

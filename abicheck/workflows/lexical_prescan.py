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

"""``compare``'s per-side lexical/preprocessor pre-scan enrichment
(``docs/contribute/plans/one-comparison-product.md`` §3 rows 6/8, §6 Phase
2b; ADR-068 D3/D5).

:mod:`abicheck.buildsource.pattern_scan` (the compiler-free lexical ABI-risk
pre-scan, ADR-035 D2) and :mod:`abicheck.buildsource.preprocessor_scan` (the
S2 macro-value/header-leak pre-scan) were, before this module, reachable only
from ``scan_engine.py`` -- unlike the eleven :mod:`abicheck.buildsource.
crosscheck` checks migrated in Phase 2a, neither ever ran against ``compare``.
``checker.compare()`` runs this stage automatically, on both sides, whenever
either side supplies the evidence each pre-scan needs -- no CLI/API/Action
opt-in flag (ADR-068 D4/D5: "a flag that merely enables useful analysis" is
REMOVE).

**The design decision this module embodies (read before extending it):**
both pre-scans are *structurally different* from the eleven cross-source
checks Phase 2a migrated, and that difference rules out reusing
:mod:`abicheck.workflows.cross_source_evolution`'s
``PERSISTENT``/``INTRODUCED``/``RESOLVED``/``NOT_EVALUATED`` axis here. Two
options were weighed:

1. **Reuse the ``CrossSourceEvolution`` axis** -- synthesize ``Change``-like
   records per pattern/macro/leak fact so "this escalation trigger fires on
   NEW but not OLD" folds through the same evolution bookkeeping the eleven
   checks use.
2. **A simpler mechanism**: compute each side's scan result as *evidence*
   attached to the comparison (the same "coverage row" shape L3/L4/L5
   evidence already gets), with no per-fact identity, no ``ChangeKind``, and
   no evolution axis at all.

**Option 2 is what this module implements**, for reasons specific to these
two pre-scans (not a precedent against evolution-stating a future check):

* Neither pre-scan has ever produced a ``ChangeKind``/``Change``. The eleven
  crosscheck migrations each moved a check that already independently
  diffed evidence and emitted a real, policy-scored finding -- evolution-
  stating *that* finding's OLD-vs-NEW presence was a natural extension of
  something already meaningful per side. A ``PatternFact``
  ("this line says `virtual`") or a ``MacroDivergence``/``HeaderLeak`` is not
  independently a hygiene violation the way e.g. ``unversioned_exported_
  symbol`` is; it is a *hint* feeding a deeper mechanism. Inventing a
  ``ChangeKind`` whose only evidence is a lexical regex match, purely so it
  can ride the evolution axis, would manufacture policy-scored findings out
  of a source that ADR-028 D3/ADR-035 D1 deliberately keep advisory-only --
  the opposite of what that authority rule protects.
* ``scan`` itself never computed either pre-scan on a *baseline* -- both ran
  exactly once, against the scan's own candidate binary
  (``scan_engine.py``'s single ``scan_files(...)``/``run_preprocessor_scan(
  ...)`` call, never a second one for ``--against``'s baseline). A two-sided
  evolution axis over these facts would not be *preserving* a scan
  capability; it would be inventing new product behavior the migration's own
  parity mandate does not require and ADR-068 D9 does not ask for. (Compare
  is naturally two-sided -- both ``old``/``new`` can independently supply
  ``--old-sources``/``--new-sources`` -- so nothing stops a *future* PR from
  deciding a genuine per-side comparison is worth it; this module simply
  does not claim that decision was already made by ``scan``'s own behavior,
  the way the eleven-check migration's per-side crosscheck genuinely was an
  extension of already-real evidence-gated logic.)
* The lexical/preprocessor hints exist to approximate signals ``compare``'s
  own detectors already compute far more precisely whenever the richer
  evidence (headers/build/L4) is present -- a real ``virtual`` addition is
  already a real vtable finding from ``diff_types``, a real macro-driven
  layout split is already visible to ``diff_build_evidence``/L4 replay,
  and a real header leak is already ``private_header_leak`` (Phase 2a,
  header-AST-based). Promoting the lexical *guess* to a second, independent
  policy-scored finding in a context where the precise detector already ran
  would inflate noise exactly where the parity requirement least needs it.
  The genuinely new value these two pre-scans add on ``compare`` is the
  *advisory* one they always had: "these lexical risk constructs exist in
  the code under review" / "this build disagrees with itself on an
  ABI-affecting macro" -- exactly the coverage/evidence shape option 2
  gives them, not a comparison verdict.

Both facts are still evidence-gated the identical "skip cleanly, report why,
never silently clean" way every other tier in this codebase is:
:func:`compute_pattern_prescan_side` and :func:`compute_preprocessor_
prescan_side` degrade to an honest ``NOT_COLLECTED``/``skipped_reason`` row
(via each result's own ``coverage()``) exactly as ``scan`` does, rather than
omitting the block. :func:`fold_lexical_prescan` is the one place both the
native ``compare`` CLI (:mod:`abicheck.frontends.cli.compare_enrichment`) and
the typed API (:func:`abicheck.service_compare_pipeline.classify_compare_pair`)
call it, so the two front ends cannot disagree about when/whether it runs --
mirroring :mod:`abicheck.workflows.abi3_audit`'s own "one fold point, two
callers" shape.

Escalation-trigger-driven POI focusing (the *other* half of ``pattern_scan``'s
role in ``scan``, feeding the S5 replay work-list) is deliberately **not**
reproduced here: Phase 2c already gave ``compare`` its own, simpler
changed-path localization/POI mechanism
(:mod:`abicheck.workflows.changed_paths`), which is not gated on a lexical
pre-scan's escalation triggers at all. Reproducing the old escalation-driven
focus alongside the new, already-landed one would be two competing
localization mechanisms for the identical purpose -- exactly the drift
ADR-054/ADR-068 exist to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..buildsource.pattern_scan import PatternScanResult
    from ..buildsource.preprocessor_scan import PreprocessorScanResult
    from ..checker_types import DiffResult
    from ..compile_context import CompileContext
    from ..model import AbiSnapshot

    # `buildsource.build_evidence` is not an ADR-061-classified module, and
    # this `workflows`-classified module may only import classified/model
    # first-party modules (`scripts/check_architecture.py`'s
    # `unclassified-import` gate applies to a `TYPE_CHECKING`-guarded import
    # too, same as its import-cycle-growth sibling) -- so `BuildEvidence`
    # stays `Any` here rather than a real import purely for a type hint.
    BuildEvidence = Any

#: Depths at which the compiler-free pattern pre-scan does not walk a
#: ``--sources`` tree even when one is given -- mirrors ``scan``'s own
#: ``cli_scan_helpers.scan_pattern_roots`` guard verbatim (a bare
#: ``binary``/``headers`` depth has no source-evidence commitment to walk a
#: whole tree for). Duplicated rather than imported: ``cli_scan_helpers`` is
#: a ``frontends``-adjacent legacy module and ``workflows`` may not import it
#: (ADR-061 dependency direction) -- see ``buildsource/poi.py``'s own note
#: on the identical constraint.
_PATTERN_SCAN_SOURCE_EXCLUDED_DEPTHS: frozenset[str] = frozenset({"binary", "headers"})


def _pattern_scan_roots(
    headers: list[Path], sources: Path | None, depth: str | None
) -> list[Path]:
    """Roots :func:`compute_pattern_prescan_side` walks, mirroring ``scan``'s
    own ``scan_pattern_roots``: headers are always in scope; ``sources`` is
    added only when the depth actually reaches source evidence."""
    roots: list[Path] = list(headers)
    if sources is not None and (
        depth is None or depth.lower() not in _PATTERN_SCAN_SOURCE_EXCLUDED_DEPTHS
    ):
        roots.append(sources)
    return roots


def compute_pattern_prescan_side(
    headers: list[Path],
    sources: Path | None,
    depth: str | None,
    changed_paths: tuple[str, ...] | None,
) -> PatternScanResult:
    """One side's lexical pre-scan result (never raises; degrades to an empty,
    ``NOT_COLLECTED`` result when *headers* is empty and *sources* is
    ``None`` -- the honest "nothing to scan" case, not a failure)."""
    from ..buildsource.pattern_scan import scan_files

    roots = _pattern_scan_roots(headers, sources, depth)
    return scan_files(roots, changed_paths or None)


def _resolve_prescan_clang_bin(compile_context: CompileContext | None) -> str:
    """The ``clang -E``/``clang -M`` binary for the S2 pre-scan, from a side's
    resolved compile context -- mirrors ``scan_engine._preprocessor_scan_
    clang_bin`` exactly (same shared resolver, same fallback rule) so the two
    commands pick the identical toolchain for an equivalent invocation."""
    if compile_context is None:
        return "clang++"
    from ..dumper_clang import resolve_source_frontend_clang_bin

    return resolve_source_frontend_clang_bin(
        compile_context.gcc_path, compile_context.gcc_prefix
    )


def compute_preprocessor_prescan_side(
    build_evidence: BuildEvidence | None,
    public_headers: list[Path],
    compile_context: CompileContext | None,
) -> PreprocessorScanResult:
    """One side's S2 preprocessor pre-scan result (never raises; degrades to
    an honest ``ran=False``/``skipped_reason`` result with no ``clang``
    invocation at all when *build_evidence* carries no compile units)."""
    from ..buildsource.preprocessor_scan import run_preprocessor_scan
    from ..service_scan import expand_public_header_inputs

    expanded = expand_public_header_inputs(public_headers)
    return run_preprocessor_scan(
        build_evidence,
        expanded,
        clang_bin=_resolve_prescan_clang_bin(compile_context),
    )


def fold_lexical_prescan(
    result: DiffResult,
    *,
    old_headers: list[Path],
    new_headers: list[Path],
    old_sources: Path | None,
    new_sources: Path | None,
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    depth: str | None,
    changed_paths: tuple[str, ...] | None = None,
    old_compile_context: CompileContext | None = None,
    new_compile_context: CompileContext | None = None,
) -> None:
    """Compute and attach both pre-scans' per-side results onto *result*.

    The one shared fold point (mirrors :func:`abicheck.workflows.abi3_audit
    .fold`'s "one rule, two callers" shape): both the native ``compare`` CLI
    and the typed API call this identically, so front ends cannot disagree
    about when the stage runs. Always runs -- no flag gates it (ADR-068
    D4/D5) -- and each half degrades to an honest, empty/skipped result
    rather than being conditionally skipped by this function itself; see the
    module docstring for why no ``Change``/``ChangeKind``/evolution axis is
    involved.
    """
    old_pattern = compute_pattern_prescan_side(
        old_headers, old_sources, depth, changed_paths
    )
    new_pattern = compute_pattern_prescan_side(
        new_headers, new_sources, depth, changed_paths
    )
    result.pattern_prescan = {
        "old": old_pattern.to_dict(),
        "new": new_pattern.to_dict(),
    }

    old_pack = old_snapshot.build_source
    new_pack = new_snapshot.build_source
    old_build = old_pack.build_evidence if old_pack is not None else None
    new_build = new_pack.build_evidence if new_pack is not None else None
    old_preproc = compute_preprocessor_prescan_side(
        old_build, old_headers, old_compile_context
    )
    new_preproc = compute_preprocessor_prescan_side(
        new_build, new_headers, new_compile_context
    )
    result.preprocessor_prescan = {
        "old": old_preproc.to_dict(),
        "new": new_preproc.to_dict(),
    }


__all__ = [
    "compute_pattern_prescan_side",
    "compute_preprocessor_prescan_side",
    "fold_lexical_prescan",
]

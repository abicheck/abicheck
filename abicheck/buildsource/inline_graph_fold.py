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

"""Clang call/type-graph folding for :mod:`inline`'s ``_build_inline_graph``.

Split out of ``inline.py`` (which sits at its 2000-line hard cap) to keep
adding scoping/coverage fields — ``narrowed_scope``, ``degraded_passes`` —
from pushing that file over the limit (ADR-041 P0). ``inline.py`` imports
:func:`fold_call_graph`/:func:`fold_type_graph` and calls them exactly as it
called the former same-module ``_fold_call_graph``/``_fold_type_graph``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence
from .model import ExtractorRecord

if TYPE_CHECKING:
    from .source_graph import SourceGraphSummary


#: Header / non-compilable changed paths fan the call-graph pass out to all TUs
#: (mirroring the L4 selector). A *real* header suffix — not merely "not a source
#: TU" — so a docs/config-only change (README.md, ci.yml) does NOT trigger a
#: whole-compile-DB clang pass (Codex review). Reuses ``source_replay._is_header``.
def _is_header_path(path: str) -> bool:
    """Whether *path* is a C/C++ header (real header suffix), per source_replay."""
    from .source_replay import _looks_like_header

    return bool(_looks_like_header(path))


def _cu_matches_changed(cu: Any, changed: tuple[str, ...]) -> bool:
    """Whether a compile unit's source is one of the *changed* paths (suffix match).

    Build-evidence sources are often absolute (``/work/src/foo.cpp``) while the
    changed set is repo-relative (``src/foo.cpp``); match when either is a
    path-component suffix of the other (mirrors ``source_replay._path_matches``).
    """
    src = (cu.source or "").replace("\\", "/")
    if not src:
        return False
    while src.startswith("./"):
        src = src[2:]
    for ch in changed:
        n = ch.replace("\\", "/")
        while n.startswith("./"):
            n = n[2:]
        if src == n or src.endswith("/" + n) or n.endswith("/" + src):
            return True
    return False


def _scope_narrowed_target(
    merged: BuildEvidence,
    changed_paths: tuple[str, ...],
    scoped_units: list[Any] | None,
) -> tuple[BuildEvidence, str, bool, frozenset[str]]:
    """Shared scoping decision for :func:`fold_call_graph`/``fold_type_graph``.

    Returns ``(target, scoped_note, narrowed, scope_key)`` — ``scope_key`` is
    the actual scope a narrowed run examined (``changed_paths``, or the
    ``scoped_units`` source paths), letting a comparison tell "narrowed to the
    same TUs" from "narrowed but disjoint" (fourteenth Codex review).
    """
    if changed_paths and not any(_is_header_path(p) for p in changed_paths):
        scoped = [
            cu for cu in merged.compile_units if _cu_matches_changed(cu, changed_paths)
        ]
        target = replace(merged, compile_units=scoped)
        return target, " (changed-scoped)", True, frozenset(changed_paths)
    if changed_paths:
        return merged, " (header change → all TUs)", False, frozenset()
    if scoped_units is not None:
        target = replace(merged, compile_units=list(scoped_units))
        scope = frozenset(cu.source for cu in scoped_units if cu.source)
        return target, " (headers-only scope, matching L4)", True, scope
    return merged, "", False, frozenset()


def fold_call_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang call-graph augmentation of *graph* (ADR-031 D4).

    Mirrors ``cli_buildsource._collect_call_graph`` for the inline path: a missing
    ``clang++`` or parse failure is recorded as a partial/failed extractor row and
    leaves the graph without call edges — it never raises (ADR-028 D3 authority
    rule: source evidence never aborts collection).

    Scope selection, in precedence order:

    - *changed_paths* (a PR/``--since`` scan) → the changed compile units only —
      parsing every TU of a large compile DB would defeat the targeted PR cost
      model (ADR-035 D7 / Codex review). A changed *header* still fans out to all
      TUs (we cannot tell which it affects without an include graph).
    - *scoped_units* (an **unseeded** run) → the exact compile-unit set the L4
      replay used (``headers-only``). Without this the unseeded call-graph pass
      re-parsed the *whole* compile DB even though L4 was scoped to one TU — the
      Gap-1 asymmetry (``validation/scan-level-scalability-2026-06.md``): the pass
      scaled with the whole tree while its reported L4 coverage stayed at a
      fraction. Aligning the two makes the L5 call-graph consistent with the L4
      surface (no phantom edges from TUs L4 never examined) and removes the
      seedless ``--depth source`` cost blow-up.
    - neither → the broad pass over all TUs (the ``full``/``s6`` contract).
    """
    from .call_graph import (
        ClangCallGraphExtractor,
        augment_graph_with_calls,
        extractor_pass_fully_covered,
        narrowed_pass_confirmed,
        project_source_files,
    )

    rows = extractors if extractors is not None else []
    # The L4 extractor's clang_bin may be a plain "clang"; the call extractor
    # needs a C++ driver, so prefer clang++ unless the user pinned a specific one.
    extractor = ClangCallGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="call_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no call edges",
            )
        )
        return
    # Scope to the changed TUs for a focused PR scan; parse all when unseeded.
    # A changed *header* fans out to all TUs — it has no compile unit of its own,
    # and (like the L4 selector without an include graph) we cannot tell which TUs
    # it affects, so restricting to ``cu.source`` matches would drop every unit and
    # silently skip header-only API changes (Codex review). Source-only changes
    # stay narrowed to the matching TUs.
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    edges = extractor.extract_from_build(target)
    # The project's own compile-unit sources — used to mark call-graph decls
    # ``defined_in_project`` from source-location provenance, so the cross-checks
    # can flag a public→impl-helper dependency the built-in call graph produced
    # without L4 ``SOURCE_DECLARES`` evidence, while still excluding third-party
    # header-inline callees (ADR-035 D4 / Codex review).
    project_files = project_source_files(merged)
    added = augment_graph_with_calls(graph, edges, project_files or None)
    # Recorded regardless of `added` — a pass that ran and found zero edges is
    # still "covered" (ADR-041 P0 slice 2 follow-up): edge presence alone
    # cannot tell a version diff "ran, zero output" from "never ran". But only
    # when extractor_pass_fully_covered() confirms the run examined the
    # whole compile DB (not narrowed), had units to examine, and hit no
    # per-TU parse failures (sixth/seventh Codex review) — otherwise fall back
    # to edge-presence inference rather than claim confirmed coverage.
    if extractor_pass_fully_covered(target, extractor, narrowed):
        graph.extractor_passes["call_graph"] = True
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        graph.narrowed_passes["call_graph"] = True
        graph.narrowed_scope["call_graph"] = scope_key
    elif extractor.diagnostics:
        graph.degraded_passes["call_graph"] = True  # ran unnarrowed, some TU failed
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"call_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="call_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{added} call edges from {len(target.compile_units)} compile "
                f"unit(s){scoped_note}{timing}"
            ),
        )
    )


def fold_type_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang type/reference-graph augmentation of *graph* (ADR-041 P0).

    Mirrors :func:`fold_call_graph` exactly (same scoping precedence, same
    graceful degradation on a missing ``clang++``) but folds
    ``TYPE_INHERITS``/``TYPE_HAS_FIELD_TYPE``/``DECL_HAS_TYPE``/
    ``DECL_REFERENCES_DECL`` edges instead of ``DECL_CALLS_DECL`` — the
    dependency kinds ``crosscheck.py``'s ``public_to_internal_dependency``
    already reads but that, before this module, no extractor populated. Run
    only when the caller also runs the call graph (``with_call_graph``), so
    the two passes share one scoping decision and one clang-availability
    diagnostic story.
    """
    from .call_graph import (
        extractor_pass_fully_covered,
        narrowed_pass_confirmed,
        project_source_files,
    )
    from .type_graph import ClangTypeGraphExtractor, augment_graph_with_types

    rows = extractors if extractors is not None else []
    extractor = ClangTypeGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="type_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no type edges",
            )
        )
        return
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    edges = extractor.extract_from_build(target)
    project_files = project_source_files(merged)
    added = augment_graph_with_types(graph, edges, project_files or None)
    # Recorded regardless of `added` — mirrors fold_call_graph's coverage gate.
    if extractor_pass_fully_covered(target, extractor, narrowed):
        graph.extractor_passes["type_graph"] = True
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        graph.narrowed_passes["type_graph"] = True
        graph.narrowed_scope["type_graph"] = scope_key
    elif extractor.diagnostics:
        graph.degraded_passes["type_graph"] = True
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"type_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="type_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{added} type edges from {len(target.compile_units)} compile "
                f"unit(s){scoped_note}{timing}"
            ),
        )
    )

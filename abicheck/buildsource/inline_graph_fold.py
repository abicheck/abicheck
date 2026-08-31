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

"""Clang call/type/include-graph folding for :mod:`inline`'s ``_build_inline_graph``.

Split out of ``inline.py`` (which sits at its 2000-line hard cap) to keep
adding scoping/coverage fields — ``narrowed_scope``, ``degraded_passes`` —
from pushing that file over the limit (ADR-041 P0). ``inline.py`` imports
:func:`fold_call_graph`/:func:`fold_type_graph`/:func:`fold_include_graph`/
:func:`fold_archive_graph`/:func:`fold_macro_graph` and calls them exactly as
it called the former same-module ``_fold_call_graph``/``_fold_type_graph``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence
from .model import ExtractorRecord
from .virtual_dispatch_graph import augment_graph_with_virtual_dispatch

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary


#: ADR-046 D3: the per-(kind, role) coverage matrix. ``type_graph.py``'s
#: ``_walk_types`` (the pure-AST full walk this module's ``fold_type_graph``
#: drives) emits exactly these role values, each from its own unconditional
#: code path — unlike the ADR-038 C.8 clang-plugin producer (a *different*,
#: filtered walk ingested via ``inputs_pack.py``, out of scope here), this
#: pass has no known per-role gap, so a confirmed family-level pass may
#: honestly claim every role below too. Kept here (not in ``type_graph.py``)
#: since this module is the one call site that turns "family confirmed" into
#: role-level claims.
ROLE_COVERAGE_MATRIX: dict[str, tuple[str, ...]] = {
    "TYPE_INHERITS": ("base",),
    "TYPE_HAS_FIELD_TYPE": (
        "field",
        "alias",
        "enum_underlying",
        "template_param",
        "default_template_arg",
    ),
    "DECL_HAS_TYPE": (
        "var",
        "return",
        "param",
        "template_param",
        "default_template_arg",
    ),
    "DECL_REFERENCES_DECL": ("ref",),
}


def _role_coverage_key(pass_name: str, edge_kind: str, role: str) -> str:
    """The finer ``extractor_passes``/``narrowed_passes`` key for one
    (pass, edge kind, role) triple (ADR-046 D3) — e.g.
    ``"type_graph:DECL_HAS_TYPE:param"``.
    """
    return f"{pass_name}:{edge_kind}:{role}"


def role_pass_covered(
    graph: SourceGraphSummary, pass_name: str, edge_kind: str, role: str
) -> bool:
    """Whether *graph* confirms ``(pass_name, edge_kind, role)`` was examined
    (ADR-046 D3), falling back to the coarser family-level
    ``extractor_passes[pass_name]`` flag when no finer key is recorded (a
    hand-built or pre-D3 graph) — the family key stays the honest fallback
    for any role this matrix doesn't (yet) break out.
    """
    key = _role_coverage_key(pass_name, edge_kind, role)
    if key in graph.extractor_passes:
        return graph.extractor_passes[key]
    return graph.extractor_passes.get(pass_name, False)


def _mark_role_coverage(dest: dict[str, bool], pass_name: str) -> None:
    """Set every ``ROLE_COVERAGE_MATRIX`` key for *pass_name* in *dest*
    (``graph.extractor_passes`` or ``graph.narrowed_passes``), alongside the
    family-level key the caller already set there.
    """
    for edge_kind, roles in ROLE_COVERAGE_MATRIX.items():
        for role in roles:
            dest[_role_coverage_key(pass_name, edge_kind, role)] = True


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
    """Shared scoping decision for :func:`fold_call_graph`/``fold_type_graph``/
    ``fold_template_graph``/``fold_include_graph``.

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
      Gap-1 asymmetry: the pass scaled with the whole tree while its reported
      L4 coverage stayed at a
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
    # ADR-046 D3: a confirmed full/narrowed pass also earns the finer
    # per-(kind, role) keys — see ROLE_COVERAGE_MATRIX's docstring for why
    # this producer (unlike the clang-plugin one) has no known role gap.
    if extractor_pass_fully_covered(target, extractor, narrowed):
        graph.extractor_passes["type_graph"] = True
        _mark_role_coverage(graph.extractor_passes, "type_graph")
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        graph.narrowed_passes["type_graph"] = True
        graph.narrowed_scope["type_graph"] = scope_key
        _mark_role_coverage(graph.narrowed_passes, "type_graph")
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


def fold_override_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang virtual-dispatch/class-hierarchy augmentation of
    *graph* (ADR-041 P2 item 1).

    Mirrors :func:`fold_type_graph` exactly (same scoping precedence, same
    graceful degradation on a missing ``clang++``) but folds
    ``METHOD_POSSIBLE_OVERRIDE`` edges instead — the possible-override
    candidate set a virtual call's ``CALL_KIND_VIRTUAL``/
    ``RESOLUTION_OVERAPPROX`` (``call_graph.py``) can be resolved against.
    Run only when the caller also runs the call/type graph
    (``with_call_graph``), same as the other Clang passes.
    """
    from .call_graph import extractor_pass_fully_covered, narrowed_pass_confirmed
    from .override_graph import (
        ClangOverrideGraphExtractor,
        augment_graph_with_overrides,
    )

    rows = extractors if extractors is not None else []
    extractor = ClangOverrideGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="override_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no override edges",
            )
        )
        return
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    edges = extractor.extract_from_build(target)
    # `virtual_methods` closes a real gap (Codex review, fresh evidence):
    # a virtual method with no override anywhere in the scanned codebase
    # never appears as either endpoint of an OverrideEdge at all, so
    # augment_graph_with_overrides also needs the extractor's own
    # per-TU-aggregated virtual-method identity set to stamp an
    # `is_virtual` fact for `virtual_dispatch_graph.py`'s vtable-presence
    # seeding to read (see that function's own docstring).
    added = augment_graph_with_overrides(
        graph,
        edges,
        virtual_methods=frozenset(extractor.last_virtual_methods),
        virtual_destructor_owners=frozenset(extractor.last_virtual_destructor_owners),
    )
    # Recorded regardless of `added` — mirrors fold_call_graph/fold_type_graph's
    # coverage gate. project_source_files() isn't consulted here: unlike a call
    # or type/field edge, an override edge's endpoints are always declarations
    # that already carry their own identity (no third-party-vs-project
    # ambiguity this pass itself introduces) — augment_graph_with_overrides
    # merges onto whatever provenance an earlier call/type-graph pass already
    # attached to the same decl:// node.
    if extractor_pass_fully_covered(target, extractor, narrowed):
        graph.extractor_passes["override_graph"] = True
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        graph.narrowed_passes["override_graph"] = True
        graph.narrowed_scope["override_graph"] = scope_key
    elif extractor.diagnostics:
        graph.degraded_passes["override_graph"] = True
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"override_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="override_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{added} override edges from {len(target.compile_units)} compile "
                f"unit(s){scoped_note}{timing}"
            ),
        )
    )


def fold_virtual_dispatch_graph(
    graph: SourceGraphSummary,
) -> None:
    """Best-effort virtual-dispatch augmentation of *graph* (G29 Phase 5 item
    3): ``VIRTUAL_CALL_MAY_DISPATCH_TO`` (Part A) + ``TYPE_HAS_VTABLE``
    (Part B) — see ``virtual_dispatch_graph.py``'s own module docstring for
    the full derivation.

    Unlike every other ``fold_*`` function in this module, this one takes no
    ``merged``/``clang_bin``/``extractors``/scoping arguments and never shells
    out to ``clang`` at all: both parts are pure transformations over graph
    state ``fold_call_graph``/``fold_type_graph``/``fold_override_graph``
    already folded (called immediately after ``fold_override_graph`` in
    :func:`fold_semantic_graphs`, so all three have already run). There is
    nothing to scope narrowly and no per-TU clang diagnostics to degrade
    on — a run over an empty/no-override graph simply adds zero edges, which
    is not a failure to report as a ``degraded_passes`` entry.

    Coverage stamping is correspondingly different from the clang-backed
    passes: ``extractor_passes["virtual_dispatch_graph"]`` is set whenever
    this function runs at all (it always completes — there is no subprocess
    step that can fail), but it is only meaningful evidence when the passes
    it *reads* — ``call_graph``/``type_graph``/``override_graph`` —
    themselves ran (``graph_impact.py``'s derived, no-subprocess passes
    follow the identical "ran iff its own read succeeded" shape rather than
    a fresh coverage story of their own). A reader checking whether this
    pass's silence ("zero edges") means anything should check those three
    passes' own coverage, not invent a fourth clang-availability check for a
    pass that never touches a compiler.

    **``narrowed_passes``/``degraded_passes`` are propagated from the three
    prerequisite passes, not left unstamped** (Codex review, fresh
    evidence): an earlier version set only ``extractor_passes`` unconditionally,
    so a run scoped to ``changed_paths``/``scoped_units`` — which correctly
    stamps ``narrowed_passes["call_graph"]``/``["type_graph"]``/
    ``["override_graph"]`` — let a reader see ``extractor_passes
    ["virtual_dispatch_graph"] = True`` with no accompanying narrowed flag,
    misreading a scoped, partial virtual-dispatch surface (candidates and
    vtables from only the scanned TUs) as complete coverage. Since all three
    prerequisite passes share one ``changed_paths``/``scoped_units`` call
    from :func:`fold_semantic_graphs`, "any of the three narrowed" implies
    the same scope, so this pass is narrowed to that same scope; "any of the
    three degraded" (a per-TU clang failure with no explicit scope) makes
    this pass degraded too, since a class/override missed by a failed TU
    silently narrows what this pass can see exactly the same way an explicit
    scope would.

    **``extractor_passes``/``narrowed_passes``/``degraded_passes`` are
    mutually exclusive for this pass, matching every clang-backed pass's own
    if/elif stamping chain** (Codex review, fresh evidence, second round):
    the first fix above added the narrowed/degraded propagation but left the
    unconditional ``extractor_passes["virtual_dispatch_graph"] = True``
    assignment in place alongside it — the persisted coverage contract
    treats a full-coverage stamp as mutually exclusive with a narrowed one
    (every sibling pass's own ``if fully_covered: ... elif narrowed: ...
    elif degraded: ...`` chain, e.g. :func:`fold_call_graph`), so a reader
    that only checks ``extractor_passes`` could still trust a scoped run as
    complete. Fixed by only stamping ``extractor_passes`` when neither
    condition holds.

    **Full coverage requires every prerequisite to have its OWN full-coverage
    stamp, not merely "neither narrowed nor degraded" (Codex review, fresh
    evidence, third round).** When ``clang``/``clang++`` isn't on ``PATH`` at
    all, each of :func:`fold_call_graph`/:func:`fold_type_graph`/
    :func:`fold_override_graph` returns early after recording a `"failed"`
    :class:`ExtractorRecord` — setting **none** of ``extractor_passes``/
    ``narrowed_passes``/``degraded_passes`` for its own pass at all (there is
    no per-TU diagnostic to attach a degraded stamp to; the extractor never
    ran). A naive ``narrowed``/``degraded`` check both reads as ``False`` in
    exactly this case — nothing is *narrowed*, nothing recorded a
    *diagnostic* — which would fall through to claiming full virtual-
    dispatch coverage from zero prerequisite facts.

    **A degraded prerequisite outranks a narrowed one (Codex review, fresh
    evidence, fourth round):** the three prerequisites stamp independently,
    so one can land in ``narrowed_passes`` (a clean, explicitly-scoped run)
    while another lands in ``degraded_passes`` (a per-TU clang failure) —
    e.g. ``call_graph`` narrows cleanly while ``override_graph`` degrades. A
    version that checked ``narrowed`` before ``degraded`` stamped only the
    narrowed key for that mix, silently dropping the untracked gap from the
    failed TUs — contradicting ``SourceGraphSummary.degraded_passes``'s own
    documented precedence ("a narrowed run with diagnostics lands here too
    ... since it is even less trustworthy than either"). Fixed by folding
    both the "some prerequisite recorded a real diagnostic" case and the
    "some prerequisite never ran at all" case (the third-round finding
    above) into one ``degraded`` check, and checking it *first*: only when
    no prerequisite is missing or degraded, and at least one is narrowed,
    does this pass count as narrowed; only when every prerequisite has its
    own confirmed full-coverage stamp does it count as fully covered.
    """
    augment_graph_with_virtual_dispatch(graph)
    _PREREQ_PASSES = ("call_graph", "type_graph", "override_graph")
    degraded = any(graph.degraded_passes.get(p) for p in _PREREQ_PASSES)
    narrowed = any(graph.narrowed_passes.get(p) for p in _PREREQ_PASSES)
    # A prerequisite that never ran at all (clang unavailable) carries none
    # of the three stamps -- as untrustworthy as a real per-TU diagnostic.
    missing = any(
        p not in graph.extractor_passes
        and p not in graph.narrowed_passes
        and p not in graph.degraded_passes
        for p in _PREREQ_PASSES
    )
    if degraded or missing:
        graph.degraded_passes["virtual_dispatch_graph"] = True
    elif narrowed:
        graph.narrowed_passes["virtual_dispatch_graph"] = True
        for p in _PREREQ_PASSES:
            scope = graph.narrowed_scope.get(p)
            if scope:
                graph.narrowed_scope["virtual_dispatch_graph"] = scope
                break
    else:
        graph.extractor_passes["virtual_dispatch_graph"] = True


def fold_template_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang template-instantiation augmentation of *graph*
    (G29 Phase 5 item 1).

    Mirrors :func:`fold_type_graph` exactly (same scoping precedence, same
    graceful degradation on a missing ``clang++``) but folds
    ``DECL_INSTANTIATES_TEMPLATE``/``TEMPLATE_USES_TYPE``/
    ``INSTANTIATION_EMITS_SYMBOL`` edges — see
    ``template_graph.py``'s own module docstring for the empirically-
    verified AST shapes this closes over. Run only when the caller also
    runs the call/type graph (``with_call_graph``), sharing the same
    scoping decision and clang-availability diagnostic story.
    """
    from . import template_graph
    from .call_graph import (
        extractor_pass_fully_covered,
        narrowed_pass_confirmed,
        project_source_files,
    )
    from .template_graph_fold import augment_graph_with_templates

    # Deliberately `template_graph.ClangTemplateGraphExtractor` (through the
    # module, not a direct `from .template_graph_extractor import ...`) --
    # this is what makes `monkeypatch.setattr(template_graph,
    # "ClangTemplateGraphExtractor", ...)` still redirect this call site, the
    # exact compatibility guarantee `template_graph_extractor.py`'s own
    # module docstring documents (Codex review, fresh evidence: an earlier
    # revision imported the class directly from the new module here, which
    # silently broke that guarantee for this one caller since a monkeypatch
    # on `template_graph`'s own attribute no longer had anywhere to be read
    # from). `template_graph`'s lazy `__getattr__` shim returns `Any`, so
    # this attribute access itself is untyped -- annotated explicitly here
    # rather than fixed by bypassing the shim.
    ClangTemplateGraphExtractor: type[Any] = template_graph.ClangTemplateGraphExtractor

    rows = extractors if extractors is not None else []
    extractor = ClangTemplateGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="template_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no template edges",
            )
        )
        return
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    instantiations = extractor.extract_from_build(target)
    project_files = project_source_files(merged)
    added = augment_graph_with_templates(graph, instantiations, project_files or None)
    if extractor_pass_fully_covered(target, extractor, narrowed):
        graph.extractor_passes["template_graph"] = True
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        graph.narrowed_passes["template_graph"] = True
        graph.narrowed_scope["template_graph"] = scope_key
    elif extractor.diagnostics:
        graph.degraded_passes["template_graph"] = True
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"template_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="template_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{len(instantiations)} instantiation(s), {added} edges from "
                f"{len(target.compile_units)} compile unit(s){scoped_note}{timing}"
            ),
        )
    )


def fold_macro_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang macro/config-dependency augmentation of *graph*
    (G29 Phase 5 item 2).

    Mirrors :func:`fold_template_graph` exactly (same scoping precedence,
    same graceful degradation on a missing ``clang++``) but folds
    ``MACRO_CONTROLS_DECL``/``DECL_USES_MACRO`` edges — see
    ``macro_graph.py``'s own module docstring for the full reasoning,
    including a load-bearing empirical clang AST-dump finding. Run only when
    the caller also runs the call/type graph (``with_call_graph``), sharing
    the same scoping decision and clang-availability diagnostic story.

    Unlike the AST-only passes above, this one also needs each named file's
    *raw source text* (``macro_graph.py``'s Pass B is a text scan, not an
    AST walk) — supplied here as a plain ``Path.read_text`` callable, whose
    own read failures are turned into per-file diagnostics by
    :func:`~abicheck.buildsource.macro_graph.augment_graph_with_macro_dependencies`
    itself (never raised here).
    """
    from .call_graph import (
        extractor_pass_fully_covered,
        narrowed_pass_confirmed,
    )
    from .macro_graph import (
        ClangMacroGraphExtractor,
        augment_graph_with_macro_dependencies,
    )

    rows = extractors if extractors is not None else []
    extractor = ClangMacroGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="macro_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no macro edges",
            )
        )
        return
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    decl_ranges = extractor.extract_from_build(target)

    def _read_source(path: str) -> str | None:
        return Path(path).read_text(encoding="utf-8")

    result = augment_graph_with_macro_dependencies(
        graph, decl_ranges, source_lookup=_read_source
    )
    added = result.controls_edges + result.uses_edges
    # Coverage honesty mirrors fold_template_graph's clang-extractor gate,
    # additionally requiring Pass B's own text-read diagnostics to be clean
    # (result.complete) — a confirmed clang pass whose source-text scan hit
    # a read failure on some file must not read as fully covered either.
    if extractor_pass_fully_covered(target, extractor, narrowed) and result.complete:
        graph.extractor_passes["macro_graph"] = True
    elif narrowed and narrowed_pass_confirmed(target, extractor) and result.complete:
        graph.narrowed_passes["macro_graph"] = True
        graph.narrowed_scope["macro_graph"] = scope_key
    elif extractor.diagnostics or result.diagnostics:
        graph.degraded_passes["macro_graph"] = True
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"macro_graph: {diag}")
    for diag in result.diagnostics:
        merged.diagnostics.append(f"macro_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="macro_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{result.controls_edges} MACRO_CONTROLS_DECL, "
                f"{result.uses_edges} DECL_USES_MACRO edge(s) from "
                f"{len(target.compile_units)} compile unit(s){scoped_note}{timing}"
            ),
        )
    )


def fold_callback_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort Clang callback/function-pointer augmentation of *graph*
    (G29 Phase 5 item 4).

    Mirrors :func:`fold_macro_graph`'s scoping precedence and graceful
    degradation, but folds ``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF``
    (Part B — a new Clang AST pass, :mod:`callback_graph`) then
    ``CALLBACK_MAY_INVOKE`` (Part A — a pure join over the edges Part B just
    folded plus ``call_graph.py``'s already-folded function-pointer-kind
    ``DECL_CALLS_DECL`` edges) in the same call, unlike
    :func:`fold_virtual_dispatch_graph`'s split shape: this family's Part A
    needs Part B's edges to have *already* landed in *graph* before it can
    join against them, and both parts share one clang-availability/scoping
    story, so there is no benefit to a separate function the way
    ``fold_virtual_dispatch_graph`` (which needs no clang/scoping arguments
    at all) earned one. Run only when the caller also runs the call graph
    (``with_call_graph``), sharing the same scoping decision and
    clang-availability diagnostic story as every other Clang-backed pass —
    see ``callback_graph.py``'s own module docstring for the full reasoning
    (including the identity design Part A's join depends on, and the
    empirical AST-shape findings this is grounded in).
    """
    from .call_graph import extractor_pass_fully_covered, narrowed_pass_confirmed
    from .callback_graph import (
        ClangCallbackGraphExtractor,
        augment_graph_with_callback_invocations,
        augment_graph_with_callback_registrations,
    )

    rows = extractors if extractors is not None else []
    extractor = ClangCallbackGraphExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        rows.append(
            ExtractorRecord(
                name="callback_graph:clang",
                status="failed",
                detail=f"{extractor.clang_bin} not found; graph has no callback edges",
            )
        )
        return
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    callback_edges = extractor.extract_from_build(target)
    registration_result = augment_graph_with_callback_registrations(
        graph, callback_edges
    )
    dispatch_result = augment_graph_with_callback_invocations(graph)
    added = registration_result.edges_joined + dispatch_result.dispatch_edges_added
    # This pass's own extractor run (Part B, DECL_REGISTERS_CALLBACK/
    # DECL_TAKES_ADDRESS_OF) gives the *ceiling* on the coverage claim below;
    # Part A's CALLBACK_MAY_INVOKE join separately depends on `call_graph`'s
    # already-folded function-pointer-kind DECL_CALLS_DECL edges (Codex
    # review, fresh evidence) -- a degraded or missing call_graph pass means
    # a real CALLBACK_MAY_INVOKE dispatch target can be silently absent even
    # when this pass's own clang run examined the whole compile DB cleanly,
    # mirroring the same prerequisite-propagation `fold_virtual_dispatch_graph`
    # already applies for its own three prerequisites.
    if extractor_pass_fully_covered(target, extractor, narrowed):
        own_state = "full"
    elif narrowed and narrowed_pass_confirmed(target, extractor):
        own_state = "narrowed"
    elif extractor.diagnostics:
        own_state = "degraded"
    else:
        own_state = "none"
    if graph.degraded_passes.get("call_graph"):
        call_graph_state = "degraded"
    elif graph.narrowed_passes.get("call_graph"):
        call_graph_state = "narrowed"
    elif graph.extractor_passes.get("call_graph"):
        call_graph_state = "full"
    else:
        # Never ran at all (e.g. clang unavailable) -- as untrustworthy as a
        # real per-TU diagnostic.
        call_graph_state = "degraded"
    # "degraded" and "none" share the worst rank on purpose: both land in the
    # same `degraded_passes` branch below, so which of the two `max()` picks
    # on a tie (Python keeps the first-seen argument, i.e. `own_state`) never
    # changes the outcome -- the branch below re-checks both states directly
    # rather than trusting `worst_state`'s exact spelling in that case.
    _STATE_RANK = {"full": 0, "narrowed": 1, "degraded": 2, "none": 2}
    worst_state = max(own_state, call_graph_state, key=_STATE_RANK.__getitem__)
    if worst_state == "full":
        graph.extractor_passes["callback_graph"] = True
    elif worst_state == "narrowed":
        graph.narrowed_passes["callback_graph"] = True
        graph.narrowed_scope["callback_graph"] = (
            scope_key
            if own_state == "narrowed"
            # A real fold_call_graph run always sets narrowed_scope
            # alongside narrowed_passes, but a hand-built/deserialized
            # graph is not guaranteed to (CodeRabbit review, fresh
            # evidence) -- .get() degrades to an empty scope rather than
            # raising and aborting this whole fold.
            else graph.narrowed_scope.get("call_graph", frozenset())
        )
    elif own_state != "none" or call_graph_state != "none":
        graph.degraded_passes["callback_graph"] = True
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"callback_graph: {diag}")
    timing = (
        f", {extractor.last_elapsed_s:.2f}s, jobs={extractor.last_jobs}"
        if getattr(extractor, "last_jobs", 0)
        else ""
    )
    rows.append(
        ExtractorRecord(
            name="callback_graph:clang",
            status="ok" if added else "partial",
            detail=(
                f"{registration_result.edges_joined} registration/address-of edge(s), "
                f"{dispatch_result.dispatch_edges_added} CALLBACK_MAY_INVOKE edge(s) "
                f"from {len(target.compile_units)} compile unit(s){scoped_note}{timing}"
            ),
        )
    )


def fold_include_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Best-effort compile-unit include-closure augmentation of *graph* (ADR-031 D3).

    Mirrors :func:`fold_call_graph`'s scoping precedence and graceful
    degradation, but folds ``COMPILE_UNIT_INCLUDES_FILE`` edges. Prefers
    already-recorded build-tool inputs (``include_map_from_recorded_inputs`` —
    no clang invocation needed at all, e.g. a CMake/Ninja/Bazel adapter that
    captured them) and only shells out to ``clang -M`` when none were
    recorded. Run alongside the call/type graph passes (``with_call_graph``),
    sharing the same scoping decision — this used to be a separate opt-in CLI
    flag (``collect --include-graph``) with no equivalent in the inline
    ``dump --sources`` path at all; folding it in here closes that gap so
    every path that runs the semantic graph gets the same edge kinds.
    """
    from .call_graph import extractor_pass_fully_covered, narrowed_pass_confirmed
    from .include_graph import (
        ClangIncludeExtractor,
        augment_graph_with_includes,
        include_map_from_recorded_inputs,
    )

    rows = extractors if extractors is not None else []
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    includes = include_map_from_recorded_inputs(target)
    extractor_name = "include_graph:recorded_inputs"
    extractor = None
    if not includes:
        extractor = ClangIncludeExtractor(
            clang_bin=clang_bin if clang_bin != "clang" else "clang++"
        )
        extractor_name = "include_graph:clang"
        if not extractor.available():
            rows.append(
                ExtractorRecord(
                    name=extractor_name,
                    status="failed",
                    detail=f"{extractor.clang_bin} not found; graph has no include edges",
                )
            )
            return
        includes = extractor.extract_from_build(target)
        for diag in extractor.diagnostics:
            merged.diagnostics.append(f"include_graph: {diag}")
    added = augment_graph_with_includes(graph, includes)
    # Coverage honesty mirrors fold_call_graph/fold_type_graph — but only when
    # a live clang extractor ran at all: recorded-inputs mode has no
    # ExtractorCapabilities-shaped object to check fully-covered/diagnostics
    # against, so it stays unmarked (edge-presence inference downstream) the
    # same way a pre-slice-2/externally-ingested pack does.
    if extractor is not None:
        if extractor_pass_fully_covered(target, extractor, narrowed):
            graph.extractor_passes["include_graph"] = True
        elif narrowed and narrowed_pass_confirmed(target, extractor):
            graph.narrowed_passes["include_graph"] = True
            graph.narrowed_scope["include_graph"] = scope_key
        elif extractor.diagnostics:
            graph.degraded_passes["include_graph"] = True
    rows.append(
        ExtractorRecord(
            name=extractor_name,
            status="ok" if added else "partial",
            detail=(
                f"{added} include edges from {len(includes)} compile "
                f"unit(s){scoped_note}"
            ),
        )
    )


def fold_semantic_graphs(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Run every Clang-derived L5 graph pass over *graph* in one call:
    :func:`fold_call_graph`, :func:`fold_type_graph`,
    :func:`fold_override_graph` (ADR-041 P2 item 1),
    :func:`fold_virtual_dispatch_graph` (G29 Phase 5 item 3),
    :func:`fold_template_graph` (G29 Phase 5 item 1),
    :func:`fold_macro_graph` (G29 Phase 5 item 2),
    :func:`fold_callback_graph` (G29 Phase 5 item 4), then
    :func:`fold_include_graph` — the exact sequence ``inline._build_inline_graph``
    ran inline before this wrapper existed, kept together here (rather than
    one call site per pass in ``inline.py``, which sits at its own
    line-count cap) so a future pass adds one call here instead of
    growing that file too. ``fold_macro_graph`` sits right after
    ``fold_template_graph`` — both are Clang-backed passes needing the same
    scoping decision, and both close over one of G29 Phase 5's originally
    open graph families. ``fold_virtual_dispatch_graph`` sits right after
    ``fold_override_graph`` and takes no clang/scoping arguments of its own
    (see its own docstring) — it is a pure transformation over the call/type/
    override graph state the three preceding passes just folded, so it must
    run after all three, not merely "somewhere in this sequence".
    ``fold_callback_graph`` sits after ``fold_macro_graph`` for the identical
    reason ``fold_virtual_dispatch_graph`` sits after ``fold_override_graph``:
    its own internal Part A join reads ``call_graph.py``'s already-folded
    function-pointer-kind ``DECL_CALLS_DECL`` edges, so ``fold_call_graph``
    must have already run — any position after it works, and grouping it with
    the other G29 Phase 5 passes keeps this family together. Each
    clang-backed pass shares the same *changed_paths*/*scoped_units* scoping
    precedence and clang-binary resolution, and each degrades independently
    (a missing ``clang++`` or a per-TU parse failure never aborts a later
    pass — ADR-028 D3).
    """
    fold_call_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_type_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_override_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_virtual_dispatch_graph(graph)
    fold_template_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_macro_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_callback_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
    fold_include_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )


def _default_archive_search_roots(merged: BuildEvidence) -> tuple[Path, ...]:
    """Every distinct compile-unit ``directory``, plus every distinct
    link-unit's own ``directory`` when recorded — the default base a
    relative archive link-input path is tried against (G29 Phase 5 item 6):
    for most generators it's the same directory the link step itself ran
    from. An absolute link-input path ignores this entirely (see
    :func:`~abicheck.buildsource.archive_graph._resolve_archive_path`).

    The link-unit directories matter for **link-only** evidence (no
    ``compile_units`` at all, e.g. a Make transcript linking prebuilt
    objects against a static archive) — without them this function returned
    no roots whatsoever for exactly that input, so a relative archive input
    could never be found even though the fixed `has_build` gate now reaches
    this pass for it (Codex review, fresh evidence). Only ``adapters/
    make.py`` populates ``LinkUnit.directory`` today (the one build system
    with no absolute-path-carrying target graph to lean on instead); every
    other adapter's own ``LinkUnit.inputs``/``output`` are already absolute.
    """
    roots: list[Path] = []
    for cu in merged.compile_units:
        if cu.directory and Path(cu.directory) not in roots:
            roots.append(Path(cu.directory))
    for link in merged.link_units:
        if link.directory and Path(link.directory) not in roots:
            roots.append(Path(link.directory))
    return tuple(roots)


def fold_archive_graph(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord] | None,
    *,
    search_roots: tuple[Path, ...] = (),
) -> None:
    """``ar``-index introspection over *graph*'s ``static_library`` nodes
    (G29 Phase 5 item 6): populates ``ARCHIVE_CONTAINS_OBJECT``/
    ``OBJECT_DEFINES_SYMBOL``, closing the last of the five graph families
    G29.6 named.

    Unlike :func:`fold_call_graph`/:func:`fold_type_graph`/
    :func:`fold_include_graph`, this pass needs no compiler at all — it reads
    the archive's own symbol index off disk — so it always runs whenever the
    graph has at least one ``static_library`` node, independent of
    ``with_call_graph``/clang availability. *search_roots* are tried, in
    order, for a link-input path that is not already absolute; when omitted
    (the ``inline.py`` caller's only use today), they default to
    :func:`_default_archive_search_roots`.

    Coverage honesty mirrors the clang-backed passes: ``archive_graph`` in
    ``extractor_passes`` means every ``static_library`` node the graph named
    was found, opened, and carried a linker-readable symbol index —
    :attr:`~abicheck.buildsource.archive_graph.ArchiveGraphResult.complete`.
    Anything less (some archives missing, unreadable, or built without an
    index) is a ``degraded_passes`` entry with the per-archive reasons on the
    extractor row's ``detail`` — never silently upgraded to "confirmed", and
    never downgraded to an error (ADR-028 D3): the nodes/edges this pass did
    manage to extract stay in the graph either way.
    """
    from .archive_graph import augment_graph_with_archives

    if not any(n.kind == "static_library" for n in graph.nodes):
        return  # no archive link inputs recorded — nothing to introspect
    rows = extractors if extractors is not None else []
    result = augment_graph_with_archives(
        graph, search_roots=search_roots or _default_archive_search_roots(merged)
    )
    if result.complete:
        graph.extractor_passes["archive_graph"] = True
    elif result.archives_read > 0 or result.diagnostics:
        graph.degraded_passes["archive_graph"] = True
    rows.append(
        ExtractorRecord(
            name="archive_graph:ar_index",
            status="ok"
            if result.complete
            else ("partial" if result.archives_read else "failed"),
            detail=(
                f"{result.archives_read}/{result.archives_seen} archive(s) read, "
                f"{result.members} member(s), {result.symbol_edges} symbol edge(s)"
                + (
                    f", {result.unjoined_symbols} unjoined"
                    if result.unjoined_symbols
                    else ""
                )
                + ("; " + "; ".join(result.diagnostics) if result.diagnostics else "")
            ),
        )
    )

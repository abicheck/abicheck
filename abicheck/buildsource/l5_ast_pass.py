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

"""The one clang AST pass every clang-backed L5 graph family reads.

The call, type, override, template, macro-range and callback graphs are all
pure parsers over a TU's ``clang -ast-dump=json`` tree, replayed with the same
argv L4 replays (``clang_command.build_clang_command``). Each
used to own a ``Clang*GraphExtractor`` that selected the compile units, sized
its own worker pool, dumped every TU, and merged the results -- six copies of
one loop, dumping every TU six times. :func:`run_ast_passes` does that once:
one unit selection, one pool, one dump per TU, then every :class:`AstPass`'s
parser applied to that tree. What stays with each family is only what is
genuinely its own: the per-TU parser, the cross-TU merge, and the graph fold
(``inline_graph_fold.fold_*``).

:func:`fold_semantic_graphs` is the one entry point that runs the pass and
folds every family, used by both the inline ``dump --sources`` path and the
out-of-band ``collect`` path, so the two cannot drift apart again.

See ``docs/contribute/plans/l4-l2-extraction-convergence.md`` (Phase 1).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ..extract.headers.clang.locations import materialize_locations
from ..parallel_probe import run_parallel_probes
from .call_graph import (
    _call_graph_jobs,
    merge_call_edges,
    parse_clang_ast_calls,
)
from .callback_graph import merge_callback_edges, parse_clang_ast_callbacks
from .clang_ast_run import (
    LOCAL_CAP_S,
    STAGE_DEADLINE_AFTER_PARSE,
    STAGE_DEADLINE_BEFORE_PARSE,
    STAGE_EMPTY,
    STAGE_JSON,
    STAGE_RUN,
    TuAstDump,
    dump_compile_unit,
)
from .header_graph_ast_projection import MalformedClangAstError, validate_ast_shape
from .inline_graph_fold import (
    _scope_narrowed_target,
    fold_call_graph,
    fold_callback_graph,
    fold_include_graph,
    fold_macro_graph,
    fold_override_graph,
    fold_template_graph,
    fold_type_graph,
    fold_virtual_dispatch_graph,
)
from .l5_ast_run import AstPassOutcome, L5AstRun
from .macro_graph import merge_decl_ranges, parse_tu_decl_ranges
from .override_graph import merge_override_facts, parse_clang_ast_override_facts
from .source_extractors.base import SourceExtractionError
from .source_extractors.clang_command import ast_clang_binary
from .template_graph import merge_template_instantiations, parse_clang_ast_templates
from .type_graph import merge_type_edges
from .type_graph_index import ast_derived_scope
from .type_graph_walk import parse_clang_ast_types

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence, CompileUnit
    from .model import ExtractorRecord

#: Progress label for the shared pass (it used to be the call-graph pass's).
PROGRESS_LABEL = "source graph (L5)"


@dataclass(frozen=True)
class AstPass:
    """One graph family's share of the L5 AST pass.

    *parse* runs on every TU's tree (the TU is passed for a parser that needs
    its replay context); an exception of a type in *parse_errors* degrades
    that TU to a ``could not parse clang AST JSON`` diagnostic for this family
    only. *merge* folds the per-TU results -- in TU input order, skipping a TU
    that produced none -- into the family's result.
    """

    name: str
    parse: Callable[[dict[str, Any], CompileUnit], Any]
    merge: Callable[[list[Any]], Any]
    parse_errors: tuple[type[Exception], ...] = (ValueError, RecursionError)


def _unparsed(ast: dict[str, Any], cu: CompileUnit, parse: Callable[..., Any]) -> Any:
    return parse(ast)


def _unit_parser(parse: Callable[[dict[str, Any]], Any]) -> Callable[..., Any]:
    return partial(_unparsed, parse=parse)


#: Every clang-backed L5 family, in the order their folds run.
L5_AST_PASSES: tuple[AstPass, ...] = (
    AstPass("call_graph", _unit_parser(parse_clang_ast_calls), merge_call_edges),
    AstPass("type_graph", _unit_parser(parse_clang_ast_types), merge_type_edges),
    AstPass(
        "override_graph",
        _unit_parser(parse_clang_ast_override_facts),
        merge_override_facts,
    ),
    AstPass(
        "template_graph",
        _unit_parser(parse_clang_ast_templates),
        merge_template_instantiations,
    ),
    AstPass(
        "macro_graph",
        parse_tu_decl_ranges,
        merge_decl_ranges,
        # _LocationCursor.advance() int()s a line value with no type check;
        # a malformed one must degrade like any other malformed AST.
        parse_errors=(TypeError, ValueError, RecursionError),
    ),
    AstPass(
        "callback_graph",
        _unit_parser(parse_clang_ast_callbacks),
        merge_callback_edges,
    ),
)


def dump_diagnostics(dump: TuAstDump) -> list[str]:
    """The L5 diagnostics one TU's dump failure (or non-zero exit) records.

    Shared by every family, since they all read the one dump. A non-zero exit
    with a tree is recorded too: clang still printed a partial tree, and the
    family's coverage must not count that TU as cleanly parsed.
    """
    diagnostics: list[str] = []
    if dump.stage not in (STAGE_RUN, STAGE_EMPTY) and dump.returncode:
        diagnostics.append(
            f"clang exited {dump.returncode} (stderr: {dump.stderr[:200]})"
        )
    if dump.stage == STAGE_RUN:
        diagnostics.append(f"clang invocation failed: {dump.error}")
    elif dump.stage == STAGE_EMPTY:
        diagnostics.append(f"clang produced no AST (stderr: {dump.stderr[:200]})")
    elif dump.stage == STAGE_JSON:
        diagnostics.append(f"could not parse clang AST JSON: {dump.error}")
    elif dump.stage == STAGE_DEADLINE_BEFORE_PARSE:
        diagnostics.append(
            f"scan deadline exceeded before parsing clang AST: {dump.error}"
        )
    elif dump.stage == STAGE_DEADLINE_AFTER_PARSE:
        diagnostics.append(
            f"scan deadline exceeded before walking clang AST: {dump.error}"
        )
    return diagnostics


def consume_tu_dump(
    dump: TuAstDump, cu: CompileUnit, passes: Sequence[AstPass]
) -> tuple[list[Any], list[list[str]]]:
    """Every pass's result (``None`` = none) and diagnostics for one TU's dump.

    The passes read the dump's tree as-is and never mutate it; a wrong-typed
    tree is refused here, the way a dump that will not parse is refused (no pass
    gets a result, and each family's TU degrades). It is not the L4 reader's
    concern: the L4 surface reads the same tree without this gate.
    """
    dump_diags = dump_diagnostics(dump)
    ast = dump.ast
    if ast is not None:
        # Explicit locations, as every L2 header tree has them (`dumper_clang_
        # errors`): a reader tracking clang's sticky file from the nodes it
        # visits drifts, and the L5 passes must read the same tree the L2 pass
        # reads to derive the same facts (`semantic_fact_folds`). In place: the
        # L4 reader reads this tree before the L5 passes do, from the raw form.
        ast = materialize_locations(ast)
        # The passes read every node's `inner` as a list of objects, and a
        # wrong-typed one reads as an empty TU rather than failing: the pass
        # would then report "ran, found nothing" for a tree it could not read.
        try:
            validate_ast_shape(ast)
        except MalformedClangAstError as exc:
            dump_diags.append(f"clang AST is malformed: {exc}")
            ast = None
    results: list[Any] = []
    diagnostics: list[list[str]] = []
    # One TU's passes share its type graph: the type_graph pass and both
    # override_graph parsers read the same tree (type_graph.ast_derived_scope).
    with ast_derived_scope():
        for p in passes:
            own = list(dump_diags)
            result = None
            if ast is not None:
                try:
                    result = p.parse(ast, cu)
                except p.parse_errors as exc:
                    own.append(f"could not parse clang AST JSON: {exc}")
            results.append(result)
            diagnostics.append(own)
    return results, diagnostics


def outcomes_from_unit_results(
    passes: Sequence[AstPass],
    per_unit: Sequence[tuple[list[Any], list[list[str]]]],
    *,
    jobs: int,
    elapsed: float,
) -> dict[str, AstPassOutcome]:
    """Fold per-TU ``(results, diagnostics)`` -- in TU input order -- per family.

    *per_unit* carries one entry per TU that was dumped, in input order; a
    result of ``None`` is a TU that produced none for that family.
    """
    collected: list[list[Any]] = [[] for _ in passes]
    diagnostics: list[list[str]] = [[] for _ in passes]
    for results, unit_diagnostics in per_unit:
        for i, result in enumerate(results):
            if result is not None:
                collected[i].append(result)
            diagnostics[i].extend(unit_diagnostics[i])
    return {
        p.name: AstPassOutcome(
            result=p.merge(collected[i]),
            diagnostics=diagnostics[i],
            last_jobs=jobs,
            last_elapsed_s=elapsed,
        )
        for i, p in enumerate(passes)
    }


def _run_unit(
    clang_bin: str, passes: Sequence[AstPass], cu: CompileUnit
) -> tuple[CompileUnit, tuple[list[Any], list[list[str]]]]:
    """Dump *cu* once; every pass's result (``None`` = none) and diagnostics."""
    try:
        dump = dump_compile_unit(cu, clang_bin=clang_bin, timeout=LOCAL_CAP_S)
    except SourceExtractionError as exc:
        # No command for this TU: degrade it alone, as the shared run does, so
        # one unbuildable unit does not abort the parallel run.
        dump = TuAstDump(None, None, "", stage=STAGE_RUN, error=str(exc), raised=exc)
    unit = consume_tu_dump(dump, cu, passes)
    dump.release()
    return cu, unit


def run_ast_passes(
    target: BuildEvidence, clang_bin: str, passes: Sequence[AstPass] = L5_AST_PASSES
) -> dict[str, AstPassOutcome]:
    """Dump every TU of *target* once and apply every pass to it.

    TUs run on :func:`~abicheck.parallel_probe.run_parallel_probes` (bounded
    pool, the active ``--budget`` deadline carried into each worker) and are
    folded in input order, never worker-completion order, so a pinned input
    yields a deterministic result. The caller checks that *clang_bin* exists
    first; the fold records a missing one as a failed row.
    """
    start = time.monotonic()
    units = [cu for cu in target.compile_units if cu.source]
    jobs = _call_graph_jobs(len(units))
    probed = run_parallel_probes(
        units, partial(_run_unit, clang_bin, passes), jobs=jobs, progress=PROGRESS_LABEL
    )
    elapsed = time.monotonic() - start if units else 0.0
    return outcomes_from_unit_results(
        passes, [unit for _cu, unit in probed], jobs=jobs, elapsed=elapsed
    )


def _clang_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def plan_l5_run(
    merged: BuildEvidence,
    clang_bin: str,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> L5AstRun:
    """Scope *merged* once for the L5 AST pass, without dumping any TU.

    The shared L4/L5 run (``source_replay.run_source_replay``) dumps the TUs of
    this scope itself, so the scope and the dump are separate steps.

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
    binary = ast_clang_binary(clang_bin)
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    return L5AstRun(
        binary,
        target,
        scoped_note,
        narrowed,
        scope_key,
        consume=_consume_family_passes,
        fold=_fold_family_outcomes,
    )


def plan_shared_l5_run(
    extractor: object,
    wanted: bool,
    merged: BuildEvidence,
    clang_bin: str,
    changed_paths: tuple[str, ...],
    selected_units: list[Any],
    scope: str,
) -> L5AstRun | None:
    """The L5 scope for an L4 replay that will carry it, or ``None``.

    ``None`` when the L5 pass is not *wanted*, or *extractor* cannot share a
    dump (castxml), so the L5 passes run standalone or not at all. Otherwise an unseeded headers-only replay narrows the L5
    pass to the TUs L4 selected (Gap-1); a seeded run scopes by *changed_paths*,
    and any other scope runs the broad pass over every TU.
    """
    from .source_replay_shared import shares_ast_dump

    if not wanted or not shares_ast_dump(extractor):
        return None
    scoped = (
        selected_units
        if scope == "headers-only" and not changed_paths and selected_units
        else None
    )
    return plan_l5_run(merged, clang_bin, changed_paths, scoped_units=scoped)


def _consume_family_passes(
    dump: TuAstDump, cu: CompileUnit
) -> tuple[list[Any], list[list[str]]]:
    """``consume_tu_dump`` over every L5 family (the shared run's hook)."""
    return consume_tu_dump(dump, cu, L5_AST_PASSES)


def _fold_family_outcomes(
    per_unit: Sequence[tuple[list[Any], list[list[str]]]],
    *,
    jobs: int,
    elapsed: float,
) -> dict[str, AstPassOutcome]:
    """``outcomes_from_unit_results`` over every L5 family (the shared run's hook)."""
    return outcomes_from_unit_results(
        L5_AST_PASSES, per_unit, jobs=jobs, elapsed=elapsed
    )


def run_l5_ast_pass(
    merged: BuildEvidence,
    clang_bin: str,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> L5AstRun:
    """Scope *merged* once and run every L5 AST pass over the result.

    Standalone form (no L4 replay in the run): each TU is dumped here. The
    L4 extractor's ``clang_bin`` may be a plain ``clang``; the AST dump runs
    under :func:`~.source_extractors.clang_command.ast_clang_binary`.
    """
    run = plan_l5_run(merged, clang_bin, changed_paths, scoped_units)
    if _clang_available(run.clang_bin):
        run.outcomes = run_ast_passes(run.target, run.clang_bin)
    return run


def fold_semantic_graphs(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
    run: L5AstRun | None = None,
) -> None:
    """Run the L5 AST pass once and fold every clang-derived graph family.

    Fold order is load-bearing: ``fold_virtual_dispatch_graph`` is a pure
    transform over the call/type/override state the three folds before it
    produced, and ``fold_callback_graph``'s invocation join reads the call
    fold's function-pointer ``DECL_CALLS_DECL`` edges. ``fold_include_graph``
    is a different invocation (``clang -MM``) and runs its own pass. Each
    family degrades independently: a missing ``clang++`` or a per-TU parse
    failure never aborts a later fold (ADR-028 D3).

    The replay runs even when the L4 surface's source_edges are already
    confirmed complete: those edges carry bare endpoint identities, not the
    dst_file/project-file provenance the folds attach (``project_files`` ->
    ``defined_in_project``). Without it ``crosscheck.public_to_internal_dependency``
    cannot classify an unannotated callee as internal, so a public-to-internal
    dependency would go undetected (Codex review). It stays unconditional until
    source_edges carries that provenance end to end.
    """
    if run is None:
        run = run_l5_ast_pass(merged, clang_bin, changed_paths, scoped_units)
    fold_call_graph(graph, merged, run, extractors)
    fold_type_graph(graph, merged, run, extractors)
    fold_override_graph(graph, merged, run, extractors)
    fold_virtual_dispatch_graph(graph)
    fold_template_graph(graph, merged, run, extractors)
    fold_macro_graph(graph, merged, run, extractors)
    fold_callback_graph(graph, merged, run, extractors)
    fold_include_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )

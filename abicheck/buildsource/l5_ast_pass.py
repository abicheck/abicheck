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
allowlisted argv (``call_graph._safe_clang_args_from_compile_unit``). Each
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

from ..parallel_probe import run_parallel_probes
from .call_graph import (
    _call_graph_jobs,
    _replay_cwd,
    _safe_clang_args_from_compile_unit,
    merge_call_edges,
    parse_clang_ast_calls,
)
from .callback_graph import merge_callback_edges, parse_clang_ast_callbacks
from .clang_ast_run import run_clang_ast_dump
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
from .template_graph import merge_template_instantiations, parse_clang_ast_templates
from .type_graph import merge_type_edges, parse_clang_ast_types

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


def _run_unit(
    clang_bin: str, passes: Sequence[AstPass], cu: CompileUnit
) -> tuple[CompileUnit, tuple[list[Any], list[list[str]]]]:
    """Dump *cu* once; every pass's result (``None`` = none) and diagnostics."""
    dump_diagnostics: list[str] = []
    ast = run_clang_ast_dump(
        clang_bin,
        _safe_clang_args_from_compile_unit(cu),
        cwd=_replay_cwd(cu),
        diagnostics=dump_diagnostics,
    )
    results: list[Any] = []
    diagnostics: list[list[str]] = []
    for p in passes:
        own = list(dump_diagnostics)
        result = None
        if ast is not None:
            try:
                result = p.parse(ast, cu)
            except p.parse_errors as exc:
                own.append(f"could not parse clang AST JSON: {exc}")
        results.append(result)
        diagnostics.append(own)
    return cu, (results, diagnostics)


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
    per_unit: list[list[Any]] = [[] for _ in passes]
    diagnostics: list[list[str]] = [[] for _ in passes]
    probed = run_parallel_probes(
        units, partial(_run_unit, clang_bin, passes), jobs=jobs, progress=PROGRESS_LABEL
    )
    for _cu, (results, unit_diagnostics) in probed:
        for i, result in enumerate(results):
            if result is not None:
                per_unit[i].append(result)
            diagnostics[i].extend(unit_diagnostics[i])
    elapsed = time.monotonic() - start if units else 0.0
    return {
        p.name: AstPassOutcome(
            result=p.merge(per_unit[i]),
            diagnostics=diagnostics[i],
            last_jobs=jobs,
            last_elapsed_s=elapsed,
        )
        for i, p in enumerate(passes)
    }


def _clang_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def run_l5_ast_pass(
    merged: BuildEvidence,
    clang_bin: str,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> L5AstRun:
    """Scope *merged* once and run every L5 AST pass over the result.

    The L4 extractor's ``clang_bin`` may be a plain ``clang``; these passes
    need a C++ driver, so it becomes ``clang++`` unless the user pinned a
    specific binary.

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
    binary = clang_bin if clang_bin != "clang" else "clang++"
    target, scoped_note, narrowed, scope_key = _scope_narrowed_target(
        merged, changed_paths, scoped_units
    )
    run = L5AstRun(binary, target, scoped_note, narrowed, scope_key)
    if _clang_available(binary):
        run.outcomes = run_ast_passes(target, binary)
    return run


def fold_semantic_graphs(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Run the L5 AST pass once and fold every clang-derived graph family.

    Fold order is load-bearing: ``fold_virtual_dispatch_graph`` is a pure
    transform over the call/type/override state the three folds before it
    produced, and ``fold_callback_graph``'s invocation join reads the call
    fold's function-pointer ``DECL_CALLS_DECL`` edges. ``fold_include_graph``
    is a different invocation (``clang -MM``) and runs its own pass. Each
    family degrades independently: a missing ``clang++`` or a per-TU parse
    failure never aborts a later fold (ADR-028 D3).
    """
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

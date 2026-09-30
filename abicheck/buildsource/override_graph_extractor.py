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

"""``ClangOverrideGraphExtractor``: the live-clang half of the override pass.

Split out of ``override_graph.py`` (which keeps the pure parsers and the
graph fold) the same way ``template_graph_extractor.py`` was split out of
``template_graph.py``. It also owns :func:`parse_clang_ast_override_facts`,
the one parser this pass hands to
:func:`~abicheck.buildsource.clang_ast_run.parse_clang_ast`, so the pass
shares the single per-TU dump the other L5 graph passes read
(``l5_shared_ast``) and still records that dump's diagnostics once.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from .. import deadline
from ..process_resources import BudgetedExecutor
from .clang_ast_run import parse_clang_ast
from .override_graph import (
    OverrideEdge,
    parse_clang_ast_overrides,
    parse_clang_ast_virtual_destructor_owners,
    parse_clang_ast_virtual_methods,
)

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit


def parse_clang_ast_override_facts(
    ast: dict[str, Any],
) -> tuple[list[OverrideEdge], frozenset[str], frozenset[str]]:
    """The override pass's three facts from one AST, as one shareable parser."""
    return (
        parse_clang_ast_overrides(ast),
        parse_clang_ast_virtual_methods(ast),
        parse_clang_ast_virtual_destructor_owners(ast),
    )


@dataclass
class ClangOverrideGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its override edges.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder (same ABI-relevant
    flag allowlist) so every AST-replay pass stays in lockstep on what is
    safe to replay.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0
    #: Every virtual-method identity found across the most recent
    #: :meth:`extract_from_build` call (:func:`parse_clang_ast_virtual_methods`,
    #: unioned across every TU) — a side-effecting sibling to ``diagnostics``/
    #: ``last_jobs``, not a second return value, following the same pattern
    #: those already use. Consumed by ``inline_graph_fold.fold_override_graph``
    #: to close the "leaf virtual method has no override edge at all" gap in
    #: :func:`augment_graph_with_overrides`'s ``virtual_methods`` parameter
    #: (Codex review, fresh evidence — see that function's own docstring).
    #: Mutated from :meth:`_extract_from_safe_args`, which may run inside a
    #: thread-pool worker — ``set.update`` from multiple threads is safe
    #: under CPython's GIL, and unlike ``diagnostics`` (a list, order-
    #: sensitive — see :meth:`extract_from_build`'s own docstring) a *set*
    #: has no order to get wrong in the first place, so no further fix is
    #: needed here.
    last_virtual_methods: set[str] = field(default_factory=set)
    #: Every class identity found to directly declare its own virtual
    #: destructor across the most recent :meth:`extract_from_build` call
    #: (:func:`parse_clang_ast_virtual_destructor_owners`, unioned across
    #: every TU) — same side-effecting-sibling pattern as
    #: ``last_virtual_methods``, closing the sibling gap that field's own
    #: docstring cross-references (a class whose only virtual member is its
    #: destructor was invisible to every existing vtable-presence seed).
    last_virtual_destructor_owners: set[str] = field(default_factory=set)

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self,
        argv: list[str],
        cwd: str | None = None,
        *,
        diagnostics: list[str] | None = None,
    ) -> list[OverrideEdge]:
        diag = self.diagnostics if diagnostics is None else diagnostics
        if not self.available():
            diag.append(f"{self.clang_bin} not found in PATH")
            return []
        try:
            edges, methods, owners = parse_clang_ast(
                self.clang_bin,
                argv,
                cwd,
                diag,
                parse_clang_ast_override_facts,
                ([], (), ()),
            )
            self.last_virtual_methods.update(methods)
            self.last_virtual_destructor_owners.update(owners)
            return edges
        except (ValueError, RecursionError) as exc:
            diag.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit, *, diagnostics: list[str] | None = None
    ) -> list[OverrideEdge]:
        from .call_graph import _replay_cwd, _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(
            argv, cwd=_replay_cwd(cu), diagnostics=diagnostics
        )

    def extract_from_build(self, build: BuildEvidence) -> list[OverrideEdge]:
        """Extract override edges across every compile unit in *build*
        (best effort). Mirrors ``ClangTypeGraphExtractor.extract_from_build``
        exactly, including its cross-TU dedup-by-(src,dst) merge, and
        ``ClangCallGraphExtractor.extract_from_build``'s deterministic-
        diagnostics-ordering fix — each unit's diagnostics are collected into
        a fresh per-call list and only folded into ``self.diagnostics`` on
        the single driving thread, in ``pool.map``'s own input-ordered
        iteration."""
        from .call_graph import _call_graph_jobs, _deadline_bound_worker

        self.last_virtual_methods = set()
        self.last_virtual_destructor_owners = set()
        start = time.monotonic()
        units = [cu for cu in build.compile_units if cu.source]
        self.last_jobs = _call_graph_jobs(len(units))
        if not units:
            self.last_elapsed_s = 0.0
            return []
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            self.last_elapsed_s = time.monotonic() - start
            return []

        all_edges: list[OverrideEdge] = []
        seen: set[tuple[str, str]] = set()

        def add_edges(edges: list[OverrideEdge]) -> None:
            for e in edges:
                key = (e.src, e.dst)
                if key in seen:
                    continue
                seen.add(key)
                all_edges.append(e)

        def _probe(
            cu: BuildEvidenceCompileUnit,
        ) -> tuple[list[OverrideEdge], list[str]]:
            local_diagnostics: list[str] = []
            edges = self._extract_from_compile_unit(cu, diagnostics=local_diagnostics)
            return edges, local_diagnostics

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    _probe,
                )
                with BudgetedExecutor(self.last_jobs) as pool:
                    for edges, local_diagnostics in pool.map(pool_worker, units):
                        add_edges(edges)
                        self.diagnostics.extend(local_diagnostics)
            else:
                for cu in units:
                    edges, local_diagnostics = _probe(cu)
                    add_edges(edges)
                    self.diagnostics.extend(local_diagnostics)
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_edges

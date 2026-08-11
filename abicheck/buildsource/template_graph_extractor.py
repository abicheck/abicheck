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

"""``ClangTemplateGraphExtractor`` — split out of ``template_graph.py`` (at
its own 2000-line hard cap, and this class's own live-clang/cross-TU-merge
concerns are self-contained) to make room for the TEMPLATE_USES_DECL
follow-up (G29 Phase 5 item 1). ``template_graph.py`` re-exports the name
via a lazy module-level ``__getattr__`` (PEP 562), the same "preserve the
historical import path without a static back-reference" shim
``cli_buildsource.py`` already uses for its own moved names — so
``from .template_graph import ClangTemplateGraphExtractor`` (every existing
call site, including ``inline_graph_fold.fold_template_graph``'s own lazy
import) keeps working unchanged, and ``monkeypatch.setattr(template_graph,
"ClangTemplateGraphExtractor", ...)`` still works too (a real attribute
`monkeypatch.setattr` writes onto the module's own ``__dict__`` is found by
ordinary attribute lookup before ``__getattr__`` is ever consulted).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from .. import deadline
from .clang_ast_run import run_clang_ast_dump
from .template_graph import (
    _FUNCTION_KIND,
    TemplateInstantiation,
    _merge_template_instantiations,
    parse_clang_ast_templates,
)

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence, CompileUnit as BuildEvidenceCompileUnit


@dataclass
class ClangTemplateGraphExtractor:
    """Shell out to ``clang`` to emit a TU's AST and parse its template
    instantiations.

    Side-effecting and compiler-dependent: only exercised on the
    ``integration`` lane. A missing ``clang`` (or a parse failure) degrades
    gracefully — extraction returns ``[]`` and records nothing (ADR-028 D3).
    Reuses ``call_graph``'s vetted parse-only argv builder (same ABI-relevant
    flag allowlist) so all three AST passes stay in lockstep on what is safe
    to replay.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def _extract_from_safe_args(
        self, argv: list[str], cwd: str | None = None
    ) -> list[TemplateInstantiation]:
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            return []
        ast = run_clang_ast_dump(
            self.clang_bin, argv, cwd=cwd, diagnostics=self.diagnostics
        )
        if ast is None:
            return []
        try:
            return parse_clang_ast_templates(ast)
        except (ValueError, RecursionError) as exc:
            self.diagnostics.append(f"could not parse clang AST JSON: {exc}")
            return []

    def _extract_from_compile_unit(
        self, cu: BuildEvidenceCompileUnit
    ) -> list[TemplateInstantiation]:
        from .call_graph import _replay_cwd, _safe_clang_args_from_compile_unit

        argv = _safe_clang_args_from_compile_unit(cu)
        return self._extract_from_safe_args(argv, cwd=_replay_cwd(cu))

    def extract_from_build(self, build: BuildEvidence) -> list[TemplateInstantiation]:
        """Extract template instantiations across every compile unit in
        *build* (best effort)."""
        from .call_graph import _call_graph_jobs, _deadline_bound_worker

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

        all_instantiations: list[TemplateInstantiation] = []

        # Dedup by (kind, template_qname, label) -- two TUs instantiating the
        # identical template with the identical arguments (a shared public
        # header) must not double the graph's edge count. A later TU seeing
        # the same instantiation is merged in (_merge_template_instantiations),
        # not dropped -- one TU may resolve an argument's target_qname or
        # reach more of the instantiated members than another (Codex review,
        # mirrors type_graph.py's own cross-TU merge for the identical
        # richness gap).
        # For a function-kind instantiation, disambiguate by its own mangled
        # name (falling back to label only when unavailable) rather than
        # (kind, template_qname, label) alone -- two distinct overloads of
        # the same function template (`f<T>(T)` vs `f<T>(T,T)`) instantiated
        # with identical template arguments produce the identical label
        # (arity isn't a template argument), so the plain 3-tuple key would
        # merge them into one instantiation here too, the same collision
        # template_instantiation_node_id's own fix addresses (Codex review).
        # A class-kind instantiation has no such ambiguity (a class template
        # can't be overloaded), so it keeps the plain key.
        def dedup_key(inst: TemplateInstantiation) -> tuple[str, str, str]:
            if inst.kind == _FUNCTION_KIND and inst.emitted_symbols:
                return (inst.kind, inst.template_qname, inst.emitted_symbols[0])
            return (inst.kind, inst.template_qname, inst.label)

        seen: dict[tuple[str, str, str], int] = {}

        def add(instantiations: Iterable[TemplateInstantiation]) -> None:
            for inst in instantiations:
                key = dedup_key(inst)
                idx = seen.get(key)
                if idx is None:
                    seen[key] = len(all_instantiations)
                    all_instantiations.append(inst)
                else:
                    all_instantiations[idx] = _merge_template_instantiations(
                        all_instantiations[idx], inst
                    )

        try:
            if self.last_jobs > 1 and len(units) > 1:
                pool_worker = partial(
                    _deadline_bound_worker,
                    deadline.current_deadline_ts(),
                    self._extract_from_compile_unit,
                )
                with ThreadPoolExecutor(max_workers=self.last_jobs) as pool:
                    for instantiations in pool.map(pool_worker, units):
                        add(instantiations)
            else:
                for cu in units:
                    add(self._extract_from_compile_unit(cu))
        finally:
            self.last_elapsed_s = time.monotonic() - start

        return all_instantiations

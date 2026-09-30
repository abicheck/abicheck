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

"""Which L5 graph passes share one ``clang -ast-dump=json`` per TU.

The clang-backed L5 passes (call, type, override, template, macro-range,
callback) all dump a TU with the identical argv
(``call_graph._safe_clang_args_from_compile_unit``) and differ only in the
pure parser they apply. :func:`shared_l5_ast` runs a function -- in practice
``inline_graph_fold.fold_semantic_graphs`` -- inside a
:func:`~abicheck.buildsource.clang_ast_run.shared_ast_scope` naming every
pass's parser, so the first pass's single dump per TU answers all six.

A new clang-backed pass adds its (module-level) parser to
:func:`l5_ast_parsers` and calls
:func:`~abicheck.buildsource.clang_ast_run.parse_clang_ast` with it; a parser
missing from this list still works, it just dumps the TU again for itself.
See ``docs/contribute/plans/l4-l2-extraction-convergence.md`` (Phase 1).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from .clang_ast_run import AstParser, shared_ast_scope

P = ParamSpec("P")
R = TypeVar("R")


def parse_clang_ast_override_facts(
    ast: dict[str, Any],
) -> tuple[list[Any], frozenset[str], frozenset[str]]:
    """The override pass's three facts from one AST, as one shareable parser.

    Bundled so the pass records its dump's diagnostics once, as it did when
    it parsed the tree itself; the three parsers stay owned by
    ``override_graph``.
    """
    from .override_graph import (
        parse_clang_ast_overrides,
        parse_clang_ast_virtual_destructor_owners,
        parse_clang_ast_virtual_methods,
    )

    return (
        parse_clang_ast_overrides(ast),
        parse_clang_ast_virtual_methods(ast),
        parse_clang_ast_virtual_destructor_owners(ast),
    )


def l5_ast_parsers() -> tuple[AstParser, ...]:
    """The parser each clang-backed L5 pass applies to a TU's AST dump."""
    from .call_graph import parse_clang_ast_calls
    from .callback_graph import parse_clang_ast_callbacks
    from .macro_graph import parse_clang_ast_decl_ranges
    from .template_graph import parse_clang_ast_templates
    from .type_graph import parse_clang_ast_types

    return (
        parse_clang_ast_calls,
        parse_clang_ast_types,
        parse_clang_ast_override_facts,
        parse_clang_ast_templates,
        parse_clang_ast_decl_ranges,
        parse_clang_ast_callbacks,
    )


def shared_l5_ast(fn: Callable[P, R]) -> Callable[P, R]:
    """Run *fn* with every L5 pass sharing one AST dump per TU."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with shared_ast_scope(l5_ast_parsers()):
            return fn(*args, **kwargs)

    return wrapper

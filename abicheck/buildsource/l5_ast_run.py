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

"""What one L5 AST pass produced -- the shapes the clang-backed folds read.

A leaf (no imports from the passes or the folds), so ``inline_graph_fold``
can name these types without importing ``l5_ast_pass``, which imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence


@dataclass
class AstPassOutcome:
    """One family's result of a run.

    ``diagnostics`` is the family's own list: each TU's dump diagnostics
    (shared by every family, since they read one dump) followed by any parse
    failure of this family's parser, in TU input order.
    ``extractor_pass_fully_covered`` reads it to decide coverage.
    ``last_jobs``/``last_elapsed_s`` describe the shared run.
    """

    result: Any
    diagnostics: list[str] = field(default_factory=list)
    last_jobs: int = 0
    last_elapsed_s: float = 0.0


@dataclass
class L5AstRun:
    """What every clang-backed fold reads: the run's scope and outcomes.

    ``outcomes`` is empty when ``clang_bin`` is not on ``PATH`` -- each fold
    then records its own failed extractor row, as before.
    """

    clang_bin: str
    target: BuildEvidence
    scoped_note: str
    narrowed: bool
    scope_key: frozenset[str]
    outcomes: dict[str, AstPassOutcome] = field(default_factory=dict)

    @property
    def clang_available(self) -> bool:
        return bool(self.outcomes)

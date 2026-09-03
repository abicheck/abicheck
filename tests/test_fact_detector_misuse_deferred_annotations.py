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

"""`_deferred_annotation_compare_ids()` (``scripts/fact_detector_
misuse.py``) -- ADR-063 Phase 0 (``docs/contribute/plans/
one-semantic-pipeline.md``).

Covers the finding that a comparison embedded inside a parameter,
return, or variable annotation's own subtree -- `def f(x: Annotated[int,
Fact.present(1) == sentinel]): ...` -- was still flagged as a real
misuse site even though `from __future__ import annotations` (PEP 563)
defers every annotation to source text, never actually evaluating it at
runtime (Codex review, fresh evidence). This repository's own
`AGENTS.md` mandates that future import throughout `abicheck/`, so this
gap would have applied to the check's own real scan target universally.

Deliberately gated on the future import's actual presence: *without*
it, the identical embedded comparison genuinely does execute at
def-time (as ordinary `Annotated[...]` metadata construction), so it
stays a real site -- this fix must not blanket-exempt every annotation
regardless of whether the module actually defers them.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestDeferredAnnotationsExcludeEmbeddedComparisons:
    def test_reported_annotated_metadata_in_a_parameter_annotation(self) -> None:
        src = (
            "from __future__ import annotations\n"
            "from typing import Annotated\n"
            "from abicheck.model.fact import Fact\n"
            "def f(x: Annotated[int, Fact.present(1) == sentinel]):\n"
            "    pass\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_annotated_metadata_in_a_return_annotation(self) -> None:
        src = (
            "from __future__ import annotations\n"
            "from typing import Annotated\n"
            "from abicheck.model.fact import Fact\n"
            "def f() -> Annotated[int, Fact.present(1) == sentinel]:\n"
            "    pass\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_annotated_metadata_in_a_variable_annotation(self) -> None:
        src = (
            "from __future__ import annotations\n"
            "from typing import Annotated\n"
            "from abicheck.model.fact import Fact\n"
            "x: Annotated[int, Fact.present(1) == sentinel] = 5\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_annotated_metadata_in_a_lambda_like_nested_function_annotation(
        self,
    ) -> None:
        """A nested function's own annotation is covered too -- the
        walk visits every FunctionDef, not just module-level ones."""
        src = (
            "from __future__ import annotations\n"
            "from typing import Annotated\n"
            "from abicheck.model.fact import Fact\n"
            "def outer():\n"
            "    def inner(x: Annotated[int, Fact.present(1) == sentinel]):\n"
            "        pass\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_without_the_future_import_the_same_comparison_is_a_real_site(
        self,
    ) -> None:
        """The identical embedded comparison genuinely executes at
        def-time without PEP 563 deferral, so it must stay flagged --
        this fix is conditional on the future import, not unconditional."""
        src = (
            "from typing import Annotated\n"
            "from abicheck.model.fact import Fact\n"
            "def f(x: Annotated[int, Fact.present(1) == sentinel]):\n"
            "    pass\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_body_comparison_still_flagged_with_deferred_annotations(
        self,
    ) -> None:
        src = (
            "from __future__ import annotations\n"
            "from abicheck.model.fact import Fact\n"
            "def f(rec, other):\n"
            "    return rec.bases_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_default_value_comparison_still_flagged(self) -> None:
        """A default *value* (as opposed to an annotation) is never
        deferred by PEP 563 -- it always evaluates eagerly at
        def-time, future import or not."""
        src = (
            "from __future__ import annotations\n"
            "from abicheck.model.fact import Fact\n"
            "def f(rec, flag=Fact.present(1) == 2):\n"
            "    return flag\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_ordinary_fact_typed_annotation_recognition_unaffected(
        self,
    ) -> None:
        """A parameter's own `Fact`-typed annotation is a completely
        separate recognition mechanism (`_is_fact_typed_annotation()`),
        unaffected by this fix -- it recognizes the annotation's own
        *type*, not a comparison embedded inside it."""
        src = (
            "from __future__ import annotations\n"
            "from abicheck.model.fact import Fact\n"
            "def f(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_a_body_level_annassign_value_is_not_deferred(self) -> None:
        """`x: int = Fact.present(1) == other`'s own *value* (as
        opposed to its annotation) is never deferred -- only
        `.annotation` is; `.value` always executes."""
        src = (
            "from __future__ import annotations\n"
            "from abicheck.model.fact import Fact\n"
            'def f() -> "whatever":\n'
            "    x: int = Fact.present(1) == 2\n"
            "    return x\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

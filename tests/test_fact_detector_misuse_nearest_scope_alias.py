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

"""`_resolve_effective_fact_names()` (``scripts/fact_detector_misuse_
scope.py``) -- ADR-063 Phase 0 (``docs/contribute/plans/
one-semantic-pipeline.md``).

Two Codex findings against the same commit both traced back to the
identical root cause: the constructor-alias-addition step (`F = Fact`)
was folded into the effective `fact_names` set via an *independent*
`_scope_chain_union()` walk from the shadow-subtraction step, rather
than one combined, nearest-scope-wins resolution -- so a nearer scope's
own shadow of an alias name could be silently overridden by a farther
ancestor's alias mention, and the annotation-recognition path
(`_fact_aliases()`) never consulted constructor aliases at all.

**(A) Annotation recognition never consulted constructor aliases.**
`F = Fact; def f(value: F, other): return value == other` -- `value`'s
annotation names `F`, a genuine constructor alias, but `_fact_aliases()`'s
own `_effective_fact_names()` closure only ever subtracted shadows from
the raw, whole-tree `fact_names` set -- it never added the constructor
aliases the constructor-*call* path (`is_fact_typed()`) already folds
in. Fixed by computing `_constructor_alias_names()` inside `_fact_
aliases()` too and routing both call sites through the new shared
`_resolve_effective_fact_names()` primitive.

**(B) An unconditional alias union re-added an alias a nearer scope
shadows.** `F = Fact; def f(F, other): return F(1) == other` -- `f`'s
own parameter `F` is an ordinary, unrelated local reusing the outer
alias's name, but the old `_scope_chain_union(qualname,
constructor_aliases, lexical_parents)` walk unioned in *every*
ancestor's own aliases unconditionally, re-adding `outer`'s `F` alias
regardless of `f`'s own nearer shadow -- a real false positive.
Verified as having an identical sibling for
`_constructor_method_alias_names()` (`make_fact = Fact.present; def
f(make_fact, other): return make_fact(1) == other`) before considering
this fix done -- not a reported finding, found via this repo's own
established "verify every sibling form" discipline. Fixed by routing
both the bare-alias union (folded into `effective_fact_names`) and the
classmethod-alias check (previously its own separate
`_scope_chain_union()` call, checked via membership rather than set
union since it needs no `fact_names` base) through the same
`_resolve_effective_fact_names()` primitive -- nearest-scope-wins per
name, with a constructor-alias mention checked before a shadow mention
within the same scope (so `F = Fact` still resolves as an alias at its
*own* defining scope, not merely as an ordinary local binding).
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestAnnotationRecognitionConsultsConstructorAliases:
    """Finding (A): a constructor alias used as a type annotation must
    resolve exactly like the real `Fact` name would."""

    def test_module_level_alias_used_as_a_bare_annotation(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "F = Fact\n"
            "def f(value: F, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_used_in_a_generic_specialized_annotation(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "F = Fact\n"
            "def f(value: F[int], other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_defined_in_an_enclosing_function_used_in_a_nested_annotation(
        self,
    ) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    def f(value: F):\n"
            "        return value == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_used_in_an_annassign_annotation(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    F = Fact\n"
            "    value: F = None\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_annotation_shadowed_by_a_sibling_nested_parameter(self) -> None:
        """A nested function's own parameter named the same as the
        outer alias suppresses the annotation recognition too, mirroring
        the constructor-call shadowing rule."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    def f(F, value: F):\n"
            "        return value == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_regression_real_fact_annotation_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_renaming_import_shadow_in_annotation_still_suppressed(
        self,
    ) -> None:
        src = (
            "from other_model import Value as Fact\n"
            "def f(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_regression_global_bypass_still_composes_with_annotations(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(value: Fact, other):\n"
            "        global Fact\n"
            "        return value == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1


class TestNearestScopeWinsForConstructorAliases:
    """Finding (B) and its verified sibling: a nested scope's own
    shadow of an alias name must not be overridden by a farther
    ancestor's own alias mention of the identical name."""

    def test_bare_alias_shadowed_by_a_nested_parameter(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    def f(F, other):\n"
            "        return F(1) == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_classmethod_alias_shadowed_by_a_nested_parameter(self) -> None:
        """Not a reported finding -- found via this repo's own
        established "verify every sibling form" discipline before
        considering the reported bare-alias fix done."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    make_fact = Fact.present\n"
            "    def f(make_fact, other):\n"
            "        return make_fact(1) == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_bare_alias_still_recognized_without_a_shadow(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    def f():\n"
            "        return F(1) == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_classmethod_alias_still_recognized_without_a_shadow(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    make_fact = Fact.present\n"
            "    def f():\n"
            "        return make_fact(1) == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_still_recognized_at_its_own_defining_scope(self) -> None:
        """`F = Fact` is itself an ordinary assignment target too (the
        shadow collector records every assignment target
        unconditionally), so the alias mention must win the same-scope
        tie against the shadow mention at the defining scope itself."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_bare_constructor_still_recognized(self) -> None:
        src = "from abicheck.model.fact import Fact\ndef f(other):\n    return Fact(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_parameter_shadow_of_fact_itself_still_suppresses(
        self,
    ) -> None:
        src = "def f(Fact, other):\n    return Fact(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

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

"""`fact_equality_misuse_sites()`'s new local-shadow awareness for the
`Fact` constructor name itself, via `_locally_bound_constructor_shadow_
names()` (``scripts/fact_detector_misuse.py``/``scripts/fact_detector_
misuse_scope.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Covers the follow-up finding that an ordinary parameter reusing the
`Fact` constructor's own name (`def f(Fact, other): return Fact(1) ==
other`) was still treated as the real constructor, since `_is_fact_
typed_expr()`'s constructor-call recognition is a pure, scope-blind
lookup against a single whole-tree name set (Codex review, fresh
evidence).

**A self-inflicted regression was caught and fixed before this landed:
the first attempt reused `_fact_aliases()`'s own broader `locally_bound`
set (which also records every `ast.ImportFrom` binding), which made a
genuine `from abicheck.model.fact import Fact` -- the ordinary way to
bring the real constructor into scope at all -- read as though it
*shadowed* `Fact`, silently disabling constructor recognition
everywhere. Reverted in favor of a narrower, parameter-only collector.
`TestGenuineImportStillRecognizedAfterTheFix` below is the regression
test for that self-inflicted bug specifically, not just for the
originally reported finding.**

**A follow-up round widened the collector past parameters alone**
(`TestNonParameterShadowsSuppressRecognition` below): an ordinary
assignment/annotated-assignment/walrus/for-loop/comprehension target
named `Fact`, and an import that *renames* an unrelated symbol to
`Fact`, all shadow the constructor exactly like a parameter does --
verified this time against the exact realistic sibling case (a genuine,
unrenamed `from abicheck.model.fact import Fact`) *before* considering
the widening done, precisely the check the first self-inflicted
regression above skipped.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestParameterShadowOfTheConstructorSuppressesRecognition:
    def test_direct_constructor_call_shadow_is_not_flagged(self) -> None:
        src = "def f(Fact, other):\n    return Fact(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_shadow_is_scoped_to_its_own_function_only(self) -> None:
        """A sibling function with no shadowing parameter still detects
        the real, unshadowed constructor."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(Fact, other):\n"
            "    return Fact(1) == other\n"
            "def g(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        assert sites[0][0] == 5

    def test_nested_function_inherits_the_outer_parameter_shadow(self) -> None:
        """A closure over the outer function's own shadowing parameter
        still sees the shadow, the ordinary Python closure rule."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_doubly_nested_function_inherits_the_grandparent_shadow(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def middle():\n"
            "        def inner(other):\n"
            "            return Fact(1) == other\n"
            "        return inner\n"
            "    return middle\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_class_nested_method_inherits_the_outer_function_shadow(self) -> None:
        """The lexical-function-parent chain skips class layers, the
        identical closure-scope rule `_fact_aliases()`'s own alias
        inheritance already applies."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    class C:\n"
            "        def method(self, other):\n"
            "            return Fact(1) == other\n"
            "    return C\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_shadow_of_an_imported_alias_also_suppresses_recognition(self) -> None:
        src = (
            "from abicheck.model.fact import Fact as F\n"
            "def f(F, other):\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestGenuineImportStillRecognizedAfterTheFix:
    """Regression coverage for the self-inflicted bug this fix's own
    first attempt introduced and reverted (see module docstring)."""

    def test_bare_import_of_fact_is_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_classmethod_constructor_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact.present(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_aliased_import_of_fact_is_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact as F\n"
            "def f(other):\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_attribute_field_access_is_unaffected_by_shadowing(self) -> None:
        """A `Fact` parameter shadows the *constructor* name only --
        `rec.bases_fact` is a different recognition path entirely
        (an attribute access, not a constructor call) and must stay
        detected regardless."""
        src = "def f(Fact, rec, other):\n    return rec.bases_fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_no_shadowing_parameter_at_all_is_unaffected(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(rec, other):\n"
            "    return Fact.present(rec.bases) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1


class TestNonParameterShadowsSuppressRecognition:
    """Every ordinary local-binding form -- not just a parameter --
    shadows the constructor's own name the identical way (Codex review,
    fresh evidence)."""

    def test_plain_assignment_shadow(self) -> None:
        src = "def f(other):\n    Fact = lambda x: x\n    return Fact(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_annotated_assignment_shadow(self) -> None:
        src = (
            "def f(other):\n"
            "    Fact: object = lambda x: x\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_walrus_shadow(self) -> None:
        src = "def f(other):\n    return (Fact := (lambda x: x))(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_for_loop_target_shadow(self) -> None:
        src = (
            "def f(factories, other):\n"
            "    for Fact in factories:\n"
            "        return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_comprehension_target_shadow(self) -> None:
        src = (
            "def f(factories, other):\n"
            "    return [Fact(1) == other for Fact in factories]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_nested_tuple_unpacking_target_shadow(self) -> None:
        src = (
            "def f(pairs, other):\n"
            "    for (a, Fact) in pairs:\n"
            "        return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_renaming_import_shadow(self) -> None:
        """`from unrelated import Whatever as Fact` binds `Fact` to
        something that is *not* the real constructor -- a genuine
        shadow, unlike `from abicheck.model.fact import Fact as F`
        (which imports the real thing under a different local name)."""
        src = (
            "from unrelated import Whatever as Fact\n"
            "def f(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_bare_import_shadow(self) -> None:
        src = (
            "import somewhere.Fact as Fact\n"
            "def f(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_sibling_function_unaffected_by_a_local_shadow(self) -> None:
        """Regression guard: a local shadow in one function must not
        suppress recognition in an unrelated sibling function."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    Fact = 1\n"
            "    return Fact == other\n"
            "def g(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        assert sites[0][0] == 6

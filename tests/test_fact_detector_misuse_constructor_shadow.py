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

**A later round tracked assignment aliases of the constructor itself**
(`TestConstructorAssignmentAliasesAreRecognized` below): `F = Fact` or
`make_fact = Fact.present` followed by `F(1) == other`/`make_fact(1) ==
other` went unrecognized, since `F`/`make_fact` is a genuine, if local,
name for the identical constructor, not merely a shadow of it (Codex
review, fresh evidence). Fixed via `_constructor_alias_names()`/
`_constructor_method_alias_names()` (`scripts/fact_detector_misuse_
scope.py`). **Proactive sibling verification for this round found its
own real false positive before it shipped, not a reported finding**:
`def f(Fact, other): F = Fact; return F(1) == other` has a parameter
named `Fact` shadowing the real constructor for the whole function, so
`F = Fact` binds `F` to that *parameter's* runtime value, not to the
real constructor -- registering `F` as a constructor alias
unconditionally would have fabricated a misuse site out of an unrelated
local rebinding. Fixed by guarding both new collectors on the identical
shadow-chain check `is_fact_typed()` already applies to a bare
constructor reference (`TestShadowedConstructorSuppressesItsOwnAlias`
below is the regression coverage for this specific composition).

**A later round found the enclosing-shadow walk itself was too blunt**
(`TestGlobalDeclarationBypassesEnclosingShadow` below): `def
outer(Fact): def inner(other): global Fact; return Fact.present(1) ==
other` -- `inner`'s own `global Fact` statement routes *every*
reference to `Fact` inside `inner` straight to module scope, completely
bypassing `outer`'s parameter, the ordinary Python rule that a
function's own `global` declaration overrides the closure chain for
that name. The unconditional `_scope_chain_union()` walk had no notion
of this and still unioned in `outer`'s shadow, suppressing a genuine
misuse site (Codex review, fresh evidence). Fixed via a new
`_global_declared_names()` collector (`scripts/fact_detector_misuse_
scope.py`) and an optional `global_names` parameter on
`_scope_chain_union()` that excludes a `global`-declared name from
every non-module ancestor's own contribution -- the name still resolves
through whatever module scope's own entry says, since the walk reaches
`"<module>"` as the chain's terminal scope regardless.

**A further round found the shadow collector only ever recorded a
`def`'s own *parameters*, never a nested `def`/`class` statement's own
*name*** (`TestNestedDefinitionsShadowTheConstructorName` below): `def
outer(other): def Fact(x): return x; return Fact(1) == other` was still
read as the real constructor, since nothing recorded the nested
function's own name as a binding in its containing scope -- the
identical `STORE_NAME`/`STORE_FAST` rule an ordinary assignment already
gets (Codex review, fresh evidence). Fixed by resolving each `def`/
`class` node's own containing qualname via the already-existing
`_def_containing_qualnames()` helper (used elsewhere in this module for
the identical "a def/class statement's name binds into whatever
namespace textually contains it" question) and recording the
definition's own `.name` there, entirely inside `fact_detector_misuse_
scope.py` -- no change to `fact_detector_misuse.py` itself was needed,
which mattered given its own tight remaining headroom under the
2000-line hard cap.

**A further round found `_fact_aliases()`'s own parameter/`AnnAssign`
annotation recognition had no shadow awareness at all**
(`TestAnnotationsResolveThroughTheSameShadowMachinery` below): `from
other_model import Value as Fact; def f(value: Fact, other): return
value == other` -- the identical import the constructor-*call* path
already recognizes as a shadow -- still marked `value` as Fact-typed,
since `_is_fact_typed_annotation()` was called with the raw, whole-tree
`fact_names` set directly, with no per-scope subtraction at all (Codex
review, fresh evidence). Fixed by reusing the exact same
`_locally_bound_constructor_shadow_names()`/`_global_declared_names()`/
`_scope_chain_union()` machinery the constructor-call path already
built, computed once inside `_fact_aliases()` itself and applied at
both annotation call sites (the parameter form and the `AnnAssign`
form). `fact_detector_misuse.py` had almost no headroom left by this
point (1974/2000 lines going in); the fix landed at 1993/2000 --
confirming the earlier round's own prediction that the file would need
a further split very soon.

**A further round found the constructor-call/alias recognition treated
*any* `Fact.<attr>(...)` call as a constructor call, regardless of
which attribute** (`TestOnlyRealConstructorMethodsAreRecognized`
below): `Fact.value_or(fact, 0) == expected` is an ordinary, correct
unwrap-then-compare -- `value_or` is an *instance* method that returns
the bare `T`, never a `Fact` -- but was still flagged as a misuse, and
`_constructor_method_alias_names()`'s own `get = Fact.value_or` alias
tracking repeated the identical mistake (Codex review, fresh evidence).
Fixed by adding `_FACT_CONSTRUCTOR_METHOD_NAMES`
(`fact_detector_misuse_scope.py`) -- the real `Fact` class's own six
`@classmethod`s that actually return `cls(...)` (`present`, `partial`,
`not_collected`, `unsupported`, `failed`, `not_applicable`), explicitly
excluding `value_or` (unwraps to `T`) and `is_present` (a `@property`
returning `bool`) -- and checking the called/aliased attribute's own
name against it at both call sites (`_is_fact_typed_expr()`'s Attribute
branch, `_constructor_method_alias_names()`'s own check), the identical
"no type inference, match by name alone" stance `FACT_FIELD_NAMES`
already takes.
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


class TestConstructorAssignmentAliasesAreRecognized:
    """`F = Fact`/`make_fact = Fact.present` bind a real, if local, name
    for the identical constructor -- a later direct call through that
    name is exactly as real a misuse as calling `Fact`/`Fact.present`
    directly (Codex review, fresh evidence)."""

    def test_bare_constructor_alias_used_directly(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    F = Fact\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_classmethod_bound_alias_used_directly(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    make_fact = Fact.present\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_classmethod_bound_alias_of_a_different_classmethod(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    make_fact = Fact.not_collected\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_bare_alias_of_an_already_aliased_import(self) -> None:
        src = (
            "from abicheck.model.fact import Fact as OrigFact\n"
            "def f(other):\n"
            "    F = OrigFact\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_classmethod_alias_of_an_already_aliased_import(self) -> None:
        src = (
            "from abicheck.model.fact import Fact as OrigFact\n"
            "def f(other):\n"
            "    make_fact = OrigFact.present\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_bare_alias_composes_with_a_further_attribute_call(self) -> None:
        """`F = Fact` makes `F` behave exactly like `Fact` itself, so a
        further classmethod call through it (`F.present(1)`) is also
        recognized -- not just a direct call of `F` itself."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    F = Fact\n"
            "    return F.present(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_nested_closure_inherits_a_bare_constructor_alias(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    F = Fact\n"
            "    def inner():\n"
            "        return F(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_nested_closure_inherits_a_classmethod_bound_alias(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    make_fact = Fact.present\n"
            "    def inner():\n"
            "        return make_fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_transitive_alias_is_not_chased_one_hop_only(self) -> None:
        """Deliberately not a false negative to fix -- documented,
        accepted "one hop only" limit matching this module's own
        established alias-tracking discipline elsewhere."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    F = Fact\n"
            "    F2 = F\n"
            "    return F2(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_bare_constructor_alias_via_tuple_unpack_is_not_recognized(self) -> None:
        """Deliberately narrow, no type inference: only a single-target
        assignment is recognized."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    F, G = Fact, Fact\n"
            "    return F(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_classmethod_alias_via_tuple_unpack_is_not_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    make_fact, other_thing = Fact.present, 1\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_aliasing_an_unrelated_name_is_ignored(self) -> None:
        src = "def f(other):\n    F = SomethingElse\n    return F(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_aliasing_a_non_fact_attribute_access_is_ignored(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    make_thing = rec.build\n"
            "    return make_thing(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestShadowedConstructorSuppressesItsOwnAlias:
    """A parameter named `Fact` shadows the real constructor for the
    whole function -- an alias assignment sourced from that shadowed
    name must not fabricate a misuse site out of it (found during this
    fix's own proactive sibling verification, not a reported finding)."""

    def test_bare_alias_of_a_shadowed_constructor_is_not_recognized(self) -> None:
        src = "def f(Fact, other):\n    F = Fact\n    return F(1) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_classmethod_alias_of_a_shadowed_constructor_is_not_recognized(
        self,
    ) -> None:
        src = (
            "def f(Fact, other):\n"
            "    make_fact = Fact.present\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_shadow_at_outer_scope_suppresses_an_alias_in_a_nested_closure(
        self,
    ) -> None:
        """The alias assignment happens in `inner`, but `Fact` is
        `outer`'s own shadowing parameter -- the closure-scope chain
        must still catch it, the same rule
        `TestParameterShadowOfTheConstructorSuppressesRecognition`'s own
        nested-closure cases already establish for a bare reference."""
        src = (
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        F = Fact\n"
            "        return F(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestGlobalDeclarationBypassesEnclosingShadow:
    """A nested function's own `global` statement routes resolution of
    that name straight to module scope, completely bypassing an
    enclosing function's own shadowing binding of the same name -- the
    ordinary Python closure-vs-global rule (Codex review, fresh
    evidence)."""

    def test_global_bypasses_enclosing_parameter_shadow_classmethod_form(
        self,
    ) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        return Fact.present(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_global_bypasses_enclosing_parameter_shadow_bare_call_form(
        self,
    ) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_global_bypasses_shadow_for_a_bare_constructor_alias(self) -> None:
        """`_constructor_alias_names()`'s own shadow check must apply the
        identical `global` bypass, not just `is_fact_typed()`'s."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        F = Fact\n"
            "        return F(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_global_bypasses_shadow_for_a_classmethod_bound_alias(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        make_fact = Fact.present\n"
            "        return make_fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_global_bypasses_both_shadow_levels_when_doubly_nested(self) -> None:
        """Only the innermost function declares `global`; both `outer`
        and `middle` have their own shadowing `Fact` parameter, and the
        bypass must still reach all the way to module scope."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def middle(Fact):\n"
            "        def inner(other):\n"
            "            global Fact\n"
            "            return Fact.present(1) == other\n"
            "        return inner\n"
            "    return middle\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_shadow_still_suppresses_without_global(self) -> None:
        """Regression guard: removing the `global` statement must restore
        the ordinary shadow-suppresses-recognition behavior."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_global_with_no_shadowing_parameter_is_unaffected(self) -> None:
        """A `global` declaration with nothing shadowing it changes
        nothing -- the real constructor is still recognized exactly as
        it would be without the declaration."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    def inner():\n"
            "        global Fact\n"
            "        return Fact(1) == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_sibling_function_unaffected_by_an_unrelated_global_declaration(
        self,
    ) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
            "def g(Fact, other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_global_of_an_unrelated_name_does_not_affect_the_fact_shadow(
        self,
    ) -> None:
        """`global x` (a name that has nothing to do with `Fact`) must
        not itself bypass a genuine `Fact` shadow -- the bypass is keyed
        by name, not merely by "a global statement exists here"."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(Fact):\n"
            "    def inner(other, x):\n"
            "        global x\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_global_routes_to_a_shadowed_module_level_binding_too(self) -> None:
        """`global` only says "look at module scope" -- it doesn't say
        the module-level binding is the real constructor. A module-level
        `Fact = 1` genuinely shadows the constructor there too, so
        recognition must correctly stay suppressed."""
        src = (
            "Fact = 1\n"
            "def outer(Fact):\n"
            "    def inner(other):\n"
            "        global Fact\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestNestedDefinitionsShadowTheConstructorName:
    """A nested `def Fact(...):`/`class Fact:` binds `Fact` as a local in
    whatever scope directly *contains* it -- the same `STORE_NAME`/
    `STORE_FAST` binding rule an ordinary assignment already gets, but
    the collector previously recorded only such a definition's own
    parameters, never the definition's own name (Codex review, fresh
    evidence)."""

    def test_nested_function_definition_shadows_the_constructor(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    def Fact(x):\n"
            "        return x\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_nested_class_definition_shadows_the_constructor(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    class Fact:\n"
            "        def __call__(self, x):\n"
            "            return x\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_nested_function_shadow_is_scoped_to_its_own_function_only(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    def Fact(x):\n"
            "        return x\n"
            "    return Fact(1) == other\n"
            "def g(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        assert sites[0][0] == 7

    def test_nested_class_shadow_is_scoped_to_its_own_function_only(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    class Fact:\n"
            "        def __call__(self, x):\n"
            "            return x\n"
            "    return Fact(1) == other\n"
            "def g(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        assert sites[0][0] == 8

    def test_further_nested_closure_inherits_a_def_shadowed_name(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    def Fact(x):\n"
            "        return x\n"
            "    def inner():\n"
            "        return Fact(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_module_level_function_definition_also_shadows(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def Fact(x):\n"
            "    return x\n"
            "def g(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_nested_definition_of_an_unrelated_name_is_unaffected(self) -> None:
        """Regression guard: an ordinary nested `def helper(...):` must
        not itself suppress recognition of the real constructor."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    def helper(x):\n"
            "        return x\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_composes_with_the_global_bypass(self) -> None:
        """A nested `global Fact` still routes straight to module scope
        even when a sibling nested `def Fact` in the same enclosing
        function would otherwise shadow it -- the two mechanisms don't
        interfere with each other."""
        src = (
            "from abicheck.model.fact import Fact\n"
            "def outer(other):\n"
            "    def Fact(x):\n"
            "        return x\n"
            "    def inner():\n"
            "        global Fact\n"
            "        return Fact.present(1) == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regressions_unaffected_bare_import_and_classmethod(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

        src2 = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact.present(1) == other\n"
        )
        tree2 = ast.parse(src2, filename="x.py")
        assert len(fact_equality_misuse_sites(tree2, "x.py")) == 1


class TestAnnotationsResolveThroughTheSameShadowMachinery:
    """`_fact_aliases()`'s own parameter/`AnnAssign` annotation
    recognition previously used the raw, whole-tree `fact_names` set
    directly, with no shadow awareness at all -- the same shadow concept
    the constructor-*call* path already resolves through
    `_locally_bound_constructor_shadow_names()` (Codex review, fresh
    evidence): `from other_model import Value as Fact; def f(value:
    Fact, other): return value == other` marked `value` as Fact-typed
    even though the identical import is correctly recognized as a
    shadow by the constructor-call path."""

    def test_parameter_annotation_with_a_renaming_import_shadow(self) -> None:
        src = (
            "from other_model import Value as Fact\n"
            "def f(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_annassign_annotation_with_a_renaming_import_shadow(self) -> None:
        src = (
            "from other_model import Value as Fact\n"
            "def f(other):\n"
            "    value: Fact = None\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_parameter_named_fact_suppresses_a_sibling_annotation_too(self) -> None:
        src = "def f(Fact, value: Fact, other):\n    return value == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_nested_closure_inherits_an_annotation_shadow(self) -> None:
        src = (
            "from other_model import Value as Fact\n"
            "def outer(other):\n"
            "    def inner(value: Fact):\n"
            "        return value == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_sibling_function_unaffected_by_an_unrelated_local_shadow(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def shadowed(value):\n"
            "    Fact = 1\n"
            "    return value\n"
            "def real(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        assert sites[0][0] == 6

    def test_global_bypasses_an_annotation_shadow_too(self) -> None:
        """The identical `global` bypass this module's own
        `TestGlobalDeclarationBypassesEnclosingShadow` covers for
        constructor calls composes correctly with annotation
        resolution too."""
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

    def test_regression_bare_annotation_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(value: Fact, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_subscripted_annotation_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(value: Fact[int], other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_optional_wrapped_annotation_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(value: Fact[int] | None, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_stringized_annassign_annotation_still_recognized(
        self,
    ) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            '    value: "Fact[list[str]]" = None\n'
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_negative_unrelated_annotation_is_unaffected(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(value: int, other):\n"
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestOnlyRealConstructorMethodsAreRecognized:
    """`Fact.<attr>(...)` is a real constructor call only when `<attr>`
    is one of the six classmethods that actually return `cls(...)` --
    `value_or` (an instance method unwrapping to the bare `T`) and
    `is_present` (a `@property` returning `bool`) are not (Codex
    review, fresh evidence)."""

    def test_value_or_direct_call_is_not_a_false_positive(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(fact, expected):\n"
            "    return Fact.value_or(fact, 0) == expected\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_value_or_aliased_call_is_not_a_false_positive(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(fact, expected):\n"
            "    get = Fact.value_or\n"
            "    return get(fact, 0) == expected\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_is_present_attribute_access_is_unaffected(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(fact, other):\n"
            "    return fact.is_present == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_an_unrecognized_future_method_is_not_a_false_positive(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(fact, expected):\n"
            "    return Fact.some_future_method(fact) == expected\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_all_six_real_constructor_classmethods_still_recognized(self) -> None:
        for method, args in [
            ("present", "1"),
            ("partial", "1"),
            ("not_collected", ""),
            ("unsupported", ""),
            ("failed", '"x"'),
            ("not_applicable", ""),
        ]:
            src = (
                "from abicheck.model.fact import Fact\n"
                "def f(other):\n"
                f"    return Fact.{method}({args}) == other\n"
            )
            tree = ast.parse(src, filename="x.py")
            assert len(fact_equality_misuse_sites(tree, "x.py")) == 1, method

    def test_a_real_constructor_classmethod_alias_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    make_fact = Fact.present\n"
            "    return make_fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_generic_specialized_real_constructor_still_recognized(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact[int].present(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_generic_specialized_value_or_still_not_a_false_positive(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(fact, expected):\n"
            "    return Fact[int].value_or(fact, 0) == expected\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

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

"""ADR-063 Phase 2: direct primitive-level tests for
``model.signature_normalization.canonicalize_function_signature_param_type``.

``tests/test_model_identity.py`` already exercises this primitive through
``entity_id_for_function``'s own overload-discrimination contract; this
file pins the primitive's own contract directly, per AGENTS.md's
"Primitive-level property tests" convention -- a new reusable string-
normalization primitive gets its own standalone tests, decoupled from any
one caller's domain logic.
"""

from __future__ import annotations

from abicheck.model import signature_normalization
from abicheck.model.signature_normalization import (
    canonicalize_function_signature_param_type as canon,
)


class TestByValueCvIsDropped:
    """A top-level BY-VALUE cv-qualifier plays no part in a function's own
    type for linkage purposes -- void f(int) and void f(const int) name
    the same function."""

    def test_plain_scalar_unchanged(self) -> None:
        assert canon("int") == "int"

    def test_leading_const_dropped(self) -> None:
        assert canon("const int") == canon("int")

    def test_leading_volatile_dropped(self) -> None:
        assert canon("volatile unsigned long long") == canon("unsigned long long")

    def test_const_volatile_class_type_dropped(self) -> None:
        assert canon("const std::string") == canon("std::string")


class TestCrossProducerSpellingNormalized:
    """CastXML and Clang spell an otherwise-identical type differently."""

    def test_castxml_vs_clang_pointer_spacing(self) -> None:
        assert canon("char const*") == canon("char const *")

    def test_leading_vs_trailing_const_spelling(self) -> None:
        assert canon("const char *") == canon("char const *")


class TestPointeeCvIsPreserved:
    """A pointee cv-qualifier on a pointer/reference parameter is a
    genuine, standard-mandated overload discriminator -- unlike the
    by-value case, it must never be dropped."""

    def test_const_pointer_differs_from_mutable(self) -> None:
        assert canon("char *") != canon("const char *")

    def test_intermediate_pointer_level_cv_differs(self) -> None:
        # "pointer to a const-qualified pointer to int" vs. "pointer to
        # pointer to int" -- genuinely different, non-interchangeable
        # types, even though the qualifier isn't on the outermost sigil.
        assert canon("int **") != canon("int * const *")

    def test_template_argument_cv_differs(self) -> None:
        # A cv-qualifier nested in a template argument names a genuinely
        # different type -- Box<const int> vs. Box<int>.
        assert canon("Box<const int>") != canon("Box<int>")


class TestPointersOwnTopLevelCvIsDropped:
    """A cv-qualifier trailing the pointer's own outermost sigil qualifies
    the pointer value itself, not what it points to -- dropped exactly
    like any other top-level by-value parameter qualifier."""

    def test_pointer_own_const_dropped(self) -> None:
        assert canon("int * const") == canon("int *")

    def test_pointer_own_const_does_not_erase_pointee_cv(self) -> None:
        # The pointer's own trailing qualifier is dropped, but a genuine
        # pointee qualifier earlier in the same string must survive.
        assert canon("const int * const") == canon("const int *")
        assert canon("const int * const") != canon("int *")


class TestArrayParameterDecay:
    """A function parameter's array type always decays to a pointer -- the
    bound plays no part in the adjusted type at all."""

    def test_bound_does_not_distinguish(self) -> None:
        assert canon("int []") == canon("int [3]") == canon("int [4]") == canon("int *")

    def test_element_cv_becomes_pointee_cv(self) -> None:
        assert canon("const int []") == canon("const int *")
        assert canon("const int [3]") != canon("int [3]")

    def test_multi_dimensional_array_left_unchanged(self) -> None:
        # Documented, accepted limitation: correctly re-spelling T[][N]'s
        # adjusted type (T(*)[N]) needs declarator-rewriting this
        # primitive does not implement.
        assert canon("int [3][4]") == "int [3][4]"

    def test_multi_dimensional_array_element_cv_not_wrongly_stripped(self) -> None:
        # The accepted limitation must not become an active regression:
        # a genuinely different element-cv must still compare different,
        # even though the bound itself isn't normalized away here.
        assert canon("const int [3][4]") != canon("int [3][4]")

    def test_pointer_to_array_left_unchanged(self) -> None:
        # int (*)[3] ("pointer to array of 3 ints") already has its own
        # outermost sigil -- the trailing [3] is the POINTEE's bound, not
        # the parameter's own top-level shape, and must not be decayed.
        assert "*" in canon("int (*)[3]")
        assert "[3]" in canon("int (*)[3]")


class TestParenthesizedDeclaratorOwnCvIsDropped:
    """A parenthesized declarator's own grouping parens (a function-pointer
    or pointer-to-array parameter) are transparent for by-value cv
    purposes -- the cv-qualifier on the declarator's own outermost pointer
    is by-value and dropped, exactly like an unparenthesized pointer."""

    def test_function_pointer_own_const_dropped(self) -> None:
        assert canon("void (* const)(int)") == canon("void (*)(int)")

    def test_function_pointer_param_list_untouched(self) -> None:
        # The callback's OWN parameter types are not this parameter's
        # by-value qualifiers -- they must survive verbatim either way.
        assert "(int)" in canon("void (*)(int)")
        assert "(int)" in canon("void (* const)(int)")

    def test_pointer_to_array_own_const_dropped(self) -> None:
        assert canon("int (* const)[3]") == canon("int (*)[3]")

    def test_pointer_to_array_bound_untouched(self) -> None:
        assert "[3]" in canon("int (* const)[3]")


class TestIdempotence:
    """Canonicalizing an already-canonical form is a no-op -- a basic
    sanity property any normalization function should hold."""

    def test_idempotent_on_plain_type(self) -> None:
        once = canon("const int")
        assert canon(once) == once

    def test_idempotent_on_pointer_type(self) -> None:
        once = canon("int * const")
        assert canon(once) == once

    def test_idempotent_on_array_type(self) -> None:
        once = canon("const int [3]")
        assert canon(once) == once

    def test_idempotent_on_function_pointer_type(self) -> None:
        once = canon("void (* const)(int)")
        assert canon(once) == once


def test_module_declares_no_dependency_above_model() -> None:
    """Leaf-module contract (ADR-063 D10): ``model.signature_normalization``
    imports nothing from ``checker_types``/``diff_*``/anything above
    ``model`` — the same contract ``model.identity`` (its only caller)
    states, checked the identical way -- against the module's real
    ``import``/``from ... import`` AST nodes, not a substring scan."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(signature_normalization))
    banned_prefixes = (
        "checker_types",
        "diff_",
        "checker",
        "compare",
        "finding_identity",
    )
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    for name in imported_names:
        bare = name.lstrip(".")
        assert not bare.startswith(banned_prefixes), name

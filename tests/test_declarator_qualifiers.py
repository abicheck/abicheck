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
``model.declarator_qualifiers``, the sibling leaf module
``model.signature_normalization`` was split into (fifteenth Codex/
CodeRabbit review round on PR #941) once it hit the AI-readiness gate's
800-line production maximum.

``tests/test_signature_normalization.py`` already exercises every function
here indirectly through ``canonicalize_function_signature_param_type``'s own
doctests and test classes; this file pins each moved primitive's own
contract directly, mirroring ``test_signature_normalization.py``'s own
"Primitive-level property tests" rationale -- these functions are reusable
declarator-scanning primitives in their own right, not merely private
implementation details of one caller.
"""

from __future__ import annotations

from abicheck.model import declarator_qualifiers
from abicheck.model.declarator_qualifiers import (
    _canonicalize_member_qualifiers,
    _find_member_pointer_qualifier,
    _is_declarator_group,
    _split_at_trailing_param_list,
)


class TestIsDeclaratorGroup:
    """A declarator-grouping paren's content -- an optional calling-
    convention keyword, then a qualified-name prefix, then a bare
    ``*``/``&`` sigil -- as opposed to an opaque parameter-list paren."""

    def test_bare_pointer_sigil_is_a_group(self) -> None:
        assert _is_declarator_group("*)(int)", 0) is True

    def test_bare_reference_sigil_is_a_group(self) -> None:
        assert _is_declarator_group("&)(int)", 0) is True

    def test_qualified_member_pointer_prefix_is_a_group(self) -> None:
        assert _is_declarator_group("C::*)(int)", 0) is True

    def test_chained_namespace_prefix_is_a_group(self) -> None:
        assert _is_declarator_group("ns::C::*)(int)", 0) is True

    def test_template_id_prefix_is_a_group(self) -> None:
        assert _is_declarator_group("C<int>::*)(int)", 0) is True

    def test_calling_convention_keyword_is_a_group(self) -> None:
        assert _is_declarator_group("__cdecl *)(int)", 0) is True

    def test_ordinary_parameter_list_is_not_a_group(self) -> None:
        # A real parameter list's first token is a type, never immediately
        # followed by "::" then only a bare sigil.
        assert _is_declarator_group("int, char)", 0) is False

    def test_unmatched_template_bracket_is_not_a_group(self) -> None:
        assert _is_declarator_group("C<int::*)(int)", 0) is False


class TestFindMemberPointerQualifier:
    """A bare, non-parenthesized data-member-pointer's own qualified-name
    prefix, detected via the ``":: "`` (colon-colon-space) marker that
    ``canonicalize_type_name`` uniquely produces for it."""

    def test_simple_class_qualifier_found(self) -> None:
        span = _find_member_pointer_qualifier("int C:: *")
        assert span is not None
        start, end = span
        assert "int C:: *"[start:end] == "C::"

    def test_chained_namespace_qualifier_found(self) -> None:
        span = _find_member_pointer_qualifier("int ns::C:: *")
        assert span is not None
        start, end = span
        assert "int ns::C:: *"[start:end] == "ns::C::"

    def test_ordinary_namespace_qualified_pointer_not_found(self) -> None:
        # No space follows "::" in ordinary namespace-qualified spelling
        # (e.g. "ns::Foo"), so this must not match.
        assert _find_member_pointer_qualifier("ns::Foo *") is None

    def test_non_sigil_suffix_not_found(self) -> None:
        assert _find_member_pointer_qualifier("int C::x") is None

    def test_empty_prefix_not_found(self) -> None:
        assert _find_member_pointer_qualifier("") is None


class TestSplitAtTrailingParamList:
    def test_splits_at_top_level_paren(self) -> None:
        assert _split_at_trailing_param_list("(int)") == ("", "(int)")

    def test_splits_with_head_text(self) -> None:
        assert _split_at_trailing_param_list(" const(int)") == (
            " const",
            "(int)",
        )

    def test_no_top_level_paren_returns_none(self) -> None:
        assert _split_at_trailing_param_list(" const") is None


class TestCanonicalizeMemberQualifiers:
    def test_empty_stays_empty(self) -> None:
        assert _canonicalize_member_qualifiers("") == ""

    def test_reorders_cv(self) -> None:
        assert _canonicalize_member_qualifiers(
            " volatile const"
        ) == _canonicalize_member_qualifiers(" const volatile")

    def test_noexcept_preserved(self) -> None:
        assert _canonicalize_member_qualifiers(" noexcept") == "noexcept"

    def test_noexcept_true_and_bare_unify(self) -> None:
        assert _canonicalize_member_qualifiers(
            " noexcept(true)"
        ) == _canonicalize_member_qualifiers(" noexcept")

    def test_noexcept_false_drops_to_nothing(self) -> None:
        assert _canonicalize_member_qualifiers(" noexcept(false)") == ""

    def test_non_literal_noexcept_expression_left_untouched(self) -> None:
        assert (
            _canonicalize_member_qualifiers(" noexcept(Foo<const int>)")
            == "noexcept(Foo<const int>)"
        )


def test_module_declares_no_dependency_above_model() -> None:
    """Leaf-module contract (ADR-063 D10): ``model.declarator_qualifiers``
    imports nothing from ``checker_types``/``diff_*``/anything above
    ``model`` -- the same contract ``model.identity`` and
    ``model.signature_normalization`` themselves state, checked the
    identical way -- against the module's real ``import``/
    ``from ... import`` AST nodes, not a substring scan."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(declarator_qualifiers))
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

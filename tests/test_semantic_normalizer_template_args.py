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

"""``extract.semantic_normalizer_template_args.split_template_arguments``
(ADR-063 Phase 6, sixth slice).

Split out of ``test_semantic_normalizer.py`` (mirroring
``test_semantic_normalizer_artifacts.py``'s own precedent) since this
module's own contents are a standalone, pure text primitive with a wide
edge-case surface of its own, independent of ``normalize_header_ast``'s
wiring into ``CanonicalEntity.template_arguments`` (covered by that
sibling file's own "sixth slice" test section).
"""

from __future__ import annotations

from abicheck.extract.semantic_normalizer_template_args import (
    split_template_arguments,
)


def test_plain_name_is_not_a_template() -> None:
    assert split_template_arguments("Widget") is None


def test_namespaced_plain_name_is_not_a_template() -> None:
    assert split_template_arguments("ns::deeply::Widget") is None


def test_simple_type_and_value_arguments() -> None:
    assert split_template_arguments("Box<int, 3>") == ("int", "3")


def test_namespaced_specialization() -> None:
    assert split_template_arguments("ns::Box<int, 3>") == ("int", "3")


def test_single_argument() -> None:
    assert split_template_arguments("Vector<double>") == ("double",)


def test_nested_template_argument_is_one_argument_not_split_on_its_own_comma() -> None:
    """``Box<pair<int, int>, 3>`` has exactly TWO top-level arguments -- a
    naive comma split (ignoring nesting) would wrongly produce four."""
    assert split_template_arguments("Box<std::pair<int, int>, 3>") == (
        "std::pair<int, int>",
        "3",
    )


def test_doubly_nested_template_argument() -> None:
    assert split_template_arguments("Outer<Inner<Deepest<int>>>") == (
        "Inner<Deepest<int>>",
    )


def test_function_pointer_argument_comma_is_not_a_top_level_separator() -> None:
    """A function-pointer-type argument's own parameter-list comma must not
    be mistaken for a template-argument separator."""
    assert split_template_arguments("Box<void (*)(int, int), 3>") == (
        "void (*)(int, int)",
        "3",
    )


def test_array_bound_bracket_does_not_confuse_the_split() -> None:
    assert split_template_arguments("Box<int[3], 4>") == ("int[3]", "4")


def test_only_the_leaf_segments_own_template_arguments_are_reported() -> None:
    """``Outer<int>::Inner<double>`` is a nested specialization's own
    qualified name -- ``Inner``'s own arguments (``double``), not
    ``Outer``'s enclosing ones (``int``), are what belongs to THIS
    declaration."""
    assert split_template_arguments("Outer<int>::Inner<double>") == ("double",)


def test_scope_separator_inside_an_argument_is_not_mistaken_for_the_leaf_separator() -> (
    None
):
    """``Box<std::string>``'s argument contains ``::`` -- this must not be
    misread as the boundary between an enclosing scope and the leaf
    segment."""
    assert split_template_arguments("Box<std::string>") == ("std::string",)


def test_closure_typed_argument_is_returned_verbatim_unrenumbered() -> None:
    """The raw closure marker is returned as-is -- canonicalizing it into a
    stable ordinal is ``qualified_name_segments.
    renumber_anonymous_closure_identities``'s job, applied post-hoc to the
    whole snapshot, not this pure text-splitting primitive's."""
    assert split_template_arguments("Wrapper<(lambda at /src/f.cpp:7:14)>") == (
        "(lambda at /src/f.cpp:7:14)",
    )


def test_explicit_empty_argument_list_is_a_real_specialization_not_none() -> None:
    """``"Box<>"`` -- every parameter used its own default -- is a REAL,
    explicit (if empty) argument list, distinct from ``Widget`` (not a
    template at all)."""
    assert split_template_arguments("Box<>") == ("",)


def test_unterminated_angle_bracket_degrades_to_not_a_template() -> None:
    """Malformed/truncated input degrades to "not a template" rather than
    guessing at a partial argument list."""
    assert split_template_arguments("Box<int, 3") is None


def test_whitespace_around_each_argument_is_trimmed() -> None:
    assert split_template_arguments("Box< int ,  3 >") == ("int", "3")


def test_empty_string_is_not_a_template() -> None:
    assert split_template_arguments("") is None

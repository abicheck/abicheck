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

"""A ``func_added``/``var_added`` description claims only what the declaration shows.

Bug class (oneCCL): every addition whose export the table confirmed absent
read "New public header-only function (no exported symbol)" -- including a
``private:`` member declared without an inline body, which is neither public
(callable by consumers) nor header-only (nothing about it is defined in a
header). The oracle below is the C++ meaning of each declaration attribute,
stated independently of ``addition_evidence``'s own branch order, and swept
over every combination of them.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.diff_symbols_variables import addition_evidence
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import AccessLevel, Function, ScopeOrigin, Variable

_ACCESS = (AccessLevel.PUBLIC, AccessLevel.PROTECTED, AccessLevel.PRIVATE)


def _facts(exported: bool) -> dict:
    return header_ast_surface_facts(
        exported=exported, judged_public=True, producer="castxml"
    )


def _expected_function(access, inline, pure, deleted, exported) -> tuple[bool, str]:
    """(may the text say header-only?, phrase that must appear)."""
    if exported:
        return False, "New "
    if pure:
        return False, "pure virtual"
    if deleted:
        return False, "deleted"
    if inline:  # emitted in every consumer's own object: genuinely header-only
        return True, "header-only"
    return False, "declared without an exported symbol"


@pytest.mark.parametrize(
    ("access", "inline", "pure", "deleted", "exported"),
    list(
        itertools.product(
            _ACCESS, (False, True), (False, True), (False, True), (False, True)
        )
    ),
)
def test_function_addition_wording_matrix(
    access, inline, pure, deleted, exported
) -> None:
    fn = Function(
        name="f",
        mangled="_ZN2ns1C1fEv",
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
        access=access,
        is_inline=inline,
        is_pure_virtual=pure,
        is_virtual=pure,
        is_deleted=deleted,
        **_facts(exported),
    )
    text = addition_evidence(fn, "function")["description"]
    may_say_header_only, phrase = _expected_function(
        access, inline, pure, deleted, exported
    )
    assert phrase in text, text
    assert ("header-only" in text) == may_say_header_only, text
    # "public" is said only of a public declaration; any other access is named.
    assert text.startswith("New public ") == (access == AccessLevel.PUBLIC), text
    if access != AccessLevel.PUBLIC:
        assert f"{access.value} member" in text, text
    assert ("no exported symbol" in text or "without an exported symbol" in text) == (
        not exported
    ), text


@pytest.mark.parametrize(
    ("mangled", "is_static", "header_only"),
    [
        ("_ZN2nsL14invalid_kvs_idE", False, True),  # namespace-scope internal linkage
        ("_ZL5kCount", False, True),  # file-scope internal linkage
        ("_ZN2ns6globalE", False, False),  # external linkage, just not exported
        # A class's static data member owes an out-of-line definition.
        ("_ZN2ns1C5countE", True, False),
        ("_ZN2ns1C5countE", False, False),
        ("c_static_const", True, True),  # C `static`: internal linkage
        ("plain_c_var", False, False),
    ],
)
def test_variable_addition_wording(
    mangled: str, is_static: bool, header_only: bool
) -> None:
    var = Variable(
        name="v",
        mangled=mangled,
        type="int",
        origin=ScopeOrigin.PUBLIC_HEADER,
        is_static=is_static,
        **_facts(False),
    )
    text = addition_evidence(var, "variable")["description"]
    assert ("header-only" in text) == header_only, text
    assert "exported symbol" in text


@pytest.mark.parametrize(
    ("mangled", "internal"),
    [
        ("_ZL1x", True),
        ("_ZN2nsL1xE", True),
        ("_ZN2ns3subL1xE", True),
        ("_ZNK2ns1xE", False),
        ("_ZN2ns1xE", False),
        ("_ZN3BoxIiEL1xE", True),  # template args skipped structurally
        ("_ZN3BoxIL1xEE1yE", False),  # an `L` inside the args is not the marker
        ("_ZN" + "9" * 40 + "x", False),  # hostile length: bounded, no claim
        ("x", False),
    ],
)
def test_internal_linkage_marker(mangled: str, internal: bool) -> None:
    from abicheck.buildsource.export_obligation_linkage import has_internal_linkage

    assert has_internal_linkage(mangled) is internal


# Linkage of each spelling, stated from the Itanium/C rules (oracle), not from
# `has_internal_linkage`/`is_static_member_symbol`: "internal" (an `L` marker),
# "nested" (inside a class or namespace, external linkage), "c" (unmangled).
_VAR_SPELLINGS = {
    "_ZN2nsL14invalid_kvs_idE": "internal",
    "_ZL5kCount": "internal",
    "_ZN3ccl14invalid_kvs_idE": "nested",
    "_ZN2ns1C5countE": "nested",
    "plain_c_var": "c",
}


def _expected_variable(linkage, is_static, is_const, value) -> str:
    if linkage == "internal" or (linkage == "c" and is_static):
        return "header-only"  # emitted in each consumer's own object
    if linkage == "nested" and is_const and value is not None:
        # `static constexpr` (inline since C++17) or `static const` integral:
        # used by value, defined only if odr-used -- never "not shipped".
        return "header-initialized constant"
    return "not inline, so its definition is not shipped"


@pytest.mark.parametrize(
    ("mangled", "is_static", "is_const", "value"),
    list(itertools.product(_VAR_SPELLINGS, (False, True), (False, True), (None, "-1"))),
)
def test_variable_addition_wording_matrix(mangled, is_static, is_const, value) -> None:
    var = Variable(
        name="v",
        mangled=mangled,
        type="const int" if is_const else "int",
        is_const=is_const,
        value=value,
        origin=ScopeOrigin.PUBLIC_HEADER,
        is_static=is_static,
        **_facts(False),
    )
    text = addition_evidence(var, "variable")["description"]
    phrase = _expected_variable(_VAR_SPELLINGS[mangled], is_static, is_const, value)
    assert phrase in text, text
    # The three explanations are mutually exclusive.
    others = {
        "header-only",
        "header-initialized constant",
        "not inline, so its definition is not shipped",
    } - {phrase}
    assert not any(o in text for o in others), text

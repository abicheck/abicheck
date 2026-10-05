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
        ("_ZN2nsS_1xE", False),  # substitution: not modelled, never guessed
        ("_ZN" + "9" * 40 + "x", False),  # hostile length: bounded, no claim
        ("_ZN5shortL", True),
        ("_ZN50short", False),  # a length past the end of the name
        ("x", False),
    ],
)
def test_internal_linkage_marker(mangled: str, internal: bool) -> None:
    from abicheck.diff_symbols_variables import _has_internal_linkage

    assert _has_internal_linkage(mangled) is internal

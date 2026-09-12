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

"""Property tests for symbol-origin *ownership* (bug class
``classification.symbol_origin_ownership``).

The defect this states as an invariant: ``symbol_origin`` classified a symbol
by scanning the **whole** mangled string for a length-prefixed internal
namespace component. A mangled name embeds its *parameter types*, so any
public function taking an argument from a ``detail``/``impl``/``internal``
namespace was reported as internal churn -- and the Markdown report then told
the user those breaking findings were "not public-API breaks".

Per AGENTS.md's bug-class rule these are generated/enumerated properties
against an **independent oracle**, not a fixed reproducer: the test *builds*
each mangled name from structured components with :func:`_mangle_function`, a
small Itanium encoder written here that shares no code with the
implementation, so the expected answer is known by construction from the
component list rather than re-derived by the parser under test.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model.symbol_ownership import (
    INTERNAL_NAMESPACE_NAMES,
    symbol_origin,
)
from abicheck.name_classification import INTERNAL_NAMESPACE_COMPONENTS

# Ordinary namespace/class identifiers that are *not* an internal-namespace
# convention, and the internal ones. Kept as literals so the oracle never
# consults the implementation's own sets for its expected answer.
PUBLIC_IDENTS = ("svs", "runtime", "lib", "Index", "core", "Token", "v0", "api")
INTERNAL_IDENTS = ("detail", "impl", "internal", "__detail", "_impl")


def _mangle_component(name: str) -> str:
    """Itanium ``<length><name>`` source-name encoding."""
    return f"{len(name)}{name}"


def _mangle_class_type(components: tuple[str, ...]) -> str:
    """Encode ``const A::B&`` for a class type named by *components*."""
    inner = "".join(_mangle_component(c) for c in components)
    return f"RKN{inner}E" if len(components) > 1 else f"RK{inner}"


def _mangle_function(
    scope: tuple[str, ...], leaf: str, param_types: tuple[tuple[str, ...], ...]
) -> str:
    """Encode ``scope::leaf(param_types...)`` as an Itanium mangled name."""
    if scope:
        nested = "".join(_mangle_component(c) for c in (*scope, leaf))
        head = f"_ZN{nested}E"
    else:
        head = f"_Z{_mangle_component(leaf)}"
    params = "".join(_mangle_class_type(p) for p in param_types) or "v"
    return head + params


def _mangle_rtti(prefix: str, components: tuple[str, ...]) -> str:
    inner = "".join(_mangle_component(c) for c in components)
    return f"{prefix}N{inner}E" if len(components) > 1 else f"{prefix}{inner}"


idents = st.sampled_from(PUBLIC_IDENTS + INTERNAL_IDENTS)
public_only = st.sampled_from(PUBLIC_IDENTS)
scopes = st.lists(idents, min_size=0, max_size=3).map(tuple)
public_scopes = st.lists(public_only, min_size=0, max_size=3).map(tuple)
param_lists = st.lists(
    st.lists(idents, min_size=1, max_size=3).map(tuple), min_size=0, max_size=3
).map(tuple)


class TestOwnershipDeterminesOrigin:
    """The core invariant: only the *enclosing* scope decides."""

    @settings(max_examples=400, deadline=None)
    @given(scope=scopes, leaf=idents, params=param_lists)
    def test_origin_follows_enclosing_scope_only(
        self, scope: tuple[str, ...], leaf: str, params: tuple[tuple[str, ...], ...]
    ) -> None:
        symbol = _mangle_function(scope, leaf, params)
        # Independent oracle: known by construction from the scope we generated.
        expected = "internal" if any(c in INTERNAL_IDENTS for c in scope) else "public"
        assert symbol_origin(symbol) == expected, symbol

    @settings(max_examples=300, deadline=None)
    @given(scope=public_scopes, leaf=public_only, params=param_lists)
    def test_parameter_types_never_change_the_answer(
        self, scope: tuple[str, ...], leaf: str, params: tuple[tuple[str, ...], ...]
    ) -> None:
        """Metamorphic: a public function stays public whatever it takes.

        This is the property the original whole-string scan violated.
        """
        with_params = _mangle_function(scope, leaf, params)
        without = _mangle_function(scope, leaf, ())
        assert symbol_origin(with_params) == symbol_origin(without) == "public"

    @settings(max_examples=200, deadline=None)
    @given(scope=public_scopes, leaf=st.sampled_from(INTERNAL_IDENTS))
    def test_entity_named_like_an_internal_namespace_is_not_internal(
        self, scope: tuple[str, ...], leaf: str
    ) -> None:
        """A function *called* ``detail`` is not a function *in* ``detail``."""
        assert symbol_origin(_mangle_function(scope, leaf, ())) == "public"

    def test_reported_svs_case(self) -> None:
        """The concrete case from the SVS report, spelled out."""
        # svs::consume(const svs::detail::Token&)
        assert symbol_origin("_ZN3svs7consumeERKNS_6detail5TokenE") == "public"


class TestRttiOwnership:
    """RTTI keeps its own bucket, and resolves its owner the same way."""

    @settings(max_examples=200, deadline=None)
    @given(
        prefix=st.sampled_from(("_ZTV", "_ZTI", "_ZTS", "_ZTT")),
        scope=scopes,
        leaf=public_only,
    )
    def test_rtti_classifies_as_rtti_regardless_of_scope(
        self, prefix: str, scope: tuple[str, ...], leaf: str
    ) -> None:
        assert symbol_origin(_mangle_rtti(prefix, (*scope, leaf))) == "rtti"


class TestConstructorFallbackPath:
    """Constructors are the shape the structural parser does not model, so
    they exercise the conservative ``_nested_name_region`` fallback. It must
    still exclude parameter types -- the whole point of the fix."""

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            # svs::Token::Token(const svs::detail::Tag&) -- param is internal.
            ("_ZN3svs5TokenC1ERKNS_6detail3TagE", "public"),
            # svs::detail::Token::Token() -- the owner itself is internal.
            ("_ZN3svs6detail5TokenC1Ev", "internal"),
            ("_ZN3svs4impl5TokenD1Ev", "internal"),
            ("_ZN3svs5TokenC2ERKNS_4impl3TagE", "public"),
        ],
    )
    def test_constructor_owner_vs_parameter(self, symbol: str, expected: str) -> None:
        assert symbol_origin(symbol) == expected


class TestInternalNamespaceSetsStayInLockstep:
    """``INTERNAL_NAMESPACE_NAMES`` is the parsed-component form of
    ``INTERNAL_NAMESPACE_COMPONENTS``; a name added to one must reach both,
    or the parser path and the textual fallback path disagree."""

    def test_sets_agree(self) -> None:
        from_prefixed = {c.lstrip("0123456789") for c in INTERNAL_NAMESPACE_COMPONENTS}
        assert from_prefixed == set(INTERNAL_NAMESPACE_NAMES)

    def test_length_prefixes_are_correct(self) -> None:
        for comp in INTERNAL_NAMESPACE_COMPONENTS:
            digits = comp[: len(comp) - len(comp.lstrip("0123456789"))]
            assert int(digits) == len(comp) - len(digits), comp

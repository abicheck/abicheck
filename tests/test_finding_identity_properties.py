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

"""ADR-049 Phase 2: fact-conservation property tests for ``finding_identity.py``.

``finding_identity.py`` is partially wired: ``diff_filtering.py``'s
cross-detector dedup key now uses ``resolve_change_identity()``, while
``diff_symbols.py``'s own old/new function and variable matching remains
unwired (see that module's own docstring and
``docs/contribute/plans/public-contract-default.md``'s Phase 2 section for
the remaining, deliberately deferred matching-engine work). This file is
the other piece Phase 2 calls out as remaining: a Hypothesis property suite
exercising the invariants a real reconciliation call site would need to hold
for ADR-049 D9's "every detector fact must be conserved in exactly one
visible outcome" to actually be checkable once wired in. A matching
algorithm keyed on an unstable or accidentally-colliding identity could
silently:

- manufacture a spurious removal+addition pair for one unchanged entity
  (identity instability -- the same input resolving differently run to run,
  or two independently-built-but-content-identical declarations resolving
  to two different primary ids), or
- mask a real removal by two genuinely *different* entities colliding onto
  the same primary id.

These properties test the identity primitive itself, in isolation, against
those two failure modes -- they do not exercise ``diff_symbols.py``,
``diff_filtering.py``, or ``checker.compare`` at all, matching this module's
existing "additive only" contract.
"""

from __future__ import annotations

import copy
import string

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.finding_identity import (
    resolve_change_identity,
    resolve_function_identity,
    resolve_variable_identity,
)
from abicheck.model import Function, Param, Variable

pytestmark = pytest.mark.slow

_HSETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Identifiers shaped so both f"_Z{len(ident)}{ident}v" (a zero-parameter free
# function returning int) and the bare f"_Z{len(ident)}{ident}" (a data name,
# e.g. a global variable) are real, structurally verifiable Itanium
# <encoding>s -- confirmed against normalize_mangled_name directly for both
# forms. Restricting to ASCII letters/underscore avoids incidentally
# generating a digit-leading identifier that would change the encoding's own
# byte-length arithmetic in a way unrelated to what these properties test.
_identifier = st.text(alphabet=string.ascii_letters + "_", min_size=1, max_size=12)
_type_name = st.sampled_from(
    ["int", "char", "double", "long", "unsigned int", "float", "bool", "void*"]
)


def _function_mangled_for(ident: str) -> str:
    """A zero-parameter function <encoding>: <source-name> + 'v' (void params)."""
    return f"_Z{len(ident)}{ident}v"


def _data_mangled_for(ident: str) -> str:
    """A data-name <encoding> (e.g. a global variable): bare <source-name>,
    with no parameter-list suffix -- unlike a function, a variable has no
    signature to encode (Codex review: reusing the function form for
    variable test fixtures doesn't model a real data-name mangling)."""
    return f"_Z{len(ident)}{ident}"


@st.composite
def _functions(draw: st.DrawFn) -> Function:
    ident = draw(_identifier)
    n_params = draw(st.integers(min_value=0, max_value=3))
    params = [Param(name=f"p{i}", type=draw(_type_name)) for i in range(n_params)]
    return Function(
        name=ident,
        mangled=_function_mangled_for(ident),
        return_type=draw(_type_name),
        params=params,
    )


@st.composite
def _variables(draw: st.DrawFn) -> Variable:
    ident = draw(_identifier)
    return Variable(
        name=ident, mangled=_data_mangled_for(ident), type=draw(_type_name)
    )


class TestIdentityIsDeterministic:
    """A reconciliation algorithm needs the same input to always resolve to
    the same identity -- otherwise even a genuinely unchanged entity could
    be matched inconsistently depending on evaluation order."""

    @_HSETTINGS
    @given(_functions())
    def test_function_identity_is_deterministic(self, func: Function) -> None:
        assert resolve_function_identity(func) == resolve_function_identity(func)

    @_HSETTINGS
    @given(_variables())
    def test_variable_identity_is_deterministic(self, var: Variable) -> None:
        assert resolve_variable_identity(var) == resolve_variable_identity(var)


class TestUnchangedEntityIsConserved:
    """D9: "Every detector fact must be conserved in exactly one visible
    outcome." The prerequisite for that at the matching layer is recognizing
    an unchanged entity as unchanged, not as a spurious removal+addition
    pair -- two independently-constructed but content-identical
    declarations (modeling one entity as it appears on the old and new side
    of a comparison where nothing actually changed) must resolve to the SAME
    primary id.
    """

    @_HSETTINGS
    @given(_functions())
    def test_identical_function_on_old_and_new_side_matches(
        self, func: Function
    ) -> None:
        old_side = copy.deepcopy(func)
        new_side = copy.deepcopy(func)
        assert resolve_function_identity(old_side) == resolve_function_identity(
            new_side
        )

    @_HSETTINGS
    @given(_variables())
    def test_identical_variable_on_old_and_new_side_matches(
        self, var: Variable
    ) -> None:
        old_side = copy.deepcopy(var)
        new_side = copy.deepcopy(var)
        assert resolve_variable_identity(old_side) == resolve_variable_identity(
            new_side
        )


class TestDistinctEntitiesDoNotCollide:
    """The flip side of conservation: a real removal must not be masked by
    an accidental identity collision between two DIFFERENT entities. Two
    functions/variables with distinct verified mangled names must never
    resolve to the same CANONICAL-tier primary id -- otherwise a matching
    algorithm could pair the wrong old/new declarations together and hide
    the actual removal of one of them.
    """

    @_HSETTINGS
    @given(_functions(), _functions())
    def test_distinct_mangled_functions_never_collide(
        self, func_a: Function, func_b: Function
    ) -> None:
        assume(func_a.mangled != func_b.mangled)
        identity_a = resolve_function_identity(func_a)
        identity_b = resolve_function_identity(func_b)
        assert identity_a.primary_id != identity_b.primary_id

    @_HSETTINGS
    @given(_variables(), _variables())
    def test_distinct_mangled_variables_never_collide(
        self, var_a: Variable, var_b: Variable
    ) -> None:
        assume(var_a.mangled != var_b.mangled)
        identity_a = resolve_variable_identity(var_a)
        identity_b = resolve_variable_identity(var_b)
        assert identity_a.primary_id != identity_b.primary_id


_FUNCTION_CHANGE_KINDS = (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED)
_VARIABLE_CHANGE_KINDS = (ChangeKind.VAR_REMOVED, ChangeKind.VAR_ADDED)


@st.composite
def _symbol_level_changes(draw: st.DrawFn) -> Change:
    kind = draw(st.sampled_from(_FUNCTION_CHANGE_KINDS + _VARIABLE_CHANGE_KINDS))
    ident = draw(_identifier)
    mangled = (
        _function_mangled_for(ident)
        if kind in _FUNCTION_CHANGE_KINDS
        else _data_mangled_for(ident)
    )
    return Change(
        kind=kind,
        symbol=mangled,
        description=draw(st.text(max_size=40)),
        old_value=draw(st.none() | st.text(max_size=10)),
        new_value=draw(st.none() | st.text(max_size=10)),
    )


class TestChangeIdentityIsDeterministic:
    @_HSETTINGS
    @given(_symbol_level_changes())
    def test_change_identity_is_deterministic(self, change: Change) -> None:
        assert resolve_change_identity(change) == resolve_change_identity(change)


class TestBatchShapedChangeIgnoresTheSample:
    """A batch/library-level finding (ADR-049 Phase 2's
    ``_ALWAYS_BATCH_SHAPED_KIND_SLUGS``) samples an arbitrary affected export
    into ``symbol``/``description`` as a "spokesperson" for the whole
    release-level event -- two ``Change`` instances differing only in WHICH
    export happened to be sampled describe the exact same logical event and
    must resolve to the SAME identity. Otherwise the same release-level
    event would fragment into a different finding every time a different
    export happened to sort first.
    """

    @_HSETTINGS
    @given(_identifier, _identifier)
    def test_allocator_replacement_identity_ignores_sampled_export(
        self, ident_a: str, ident_b: str
    ) -> None:
        assume(ident_a != ident_b)
        change_a = Change(
            kind=ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
            symbol=ident_a,
            description=f"Allocator replacement added: {ident_a}",
        )
        change_b = Change(
            kind=ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
            symbol=ident_b,
            description=f"Allocator replacement added: {ident_b}",
        )
        assert resolve_change_identity(change_a) == resolve_change_identity(change_b)

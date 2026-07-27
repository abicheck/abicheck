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

"""ADR-049 Phase 2: end-to-end fact-conservation property test over
``checker.compare``.

D9's rule -- "every detector fact must be conserved in exactly one visible
outcome" -- is checked here at the full pipeline level (matching, detection,
and the identity-based cross-detector dedup wired into
``diff_filtering._deduplicate_cross_detector`` this same Phase 2 session):
an old-side public function that disappears from the new side must always
surface as a removal finding referencing it (never silently vanish), and a
function that survives unchanged must never spuriously appear as a removal.

This complements, rather than duplicates,
``tests/test_finding_identity_properties.py`` (which tests the
``finding_identity`` primitive in isolation) and
``tests/test_diff_filtering_cross_detector_identity.py`` (which tests the
dedup stage in isolation) -- this file is the one that exercises the real,
wired pipeline end to end via ``checker.compare``.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Variable, Visibility

pytestmark = pytest.mark.slow

_HSETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_FUNC_REMOVAL_KINDS = frozenset(
    {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY}
)
_VAR_REMOVAL_KINDS = frozenset({ChangeKind.VAR_REMOVED})

_ident = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8)


def _func(ident: str) -> Function:
    # A zero-parameter function <encoding>, matching the same shape
    # test_finding_identity_properties.py uses -- a real, structurally
    # verifiable Itanium mangling.
    return Function(
        name=ident,
        mangled=f"_Z{len(ident)}{ident}v",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


def _var(ident: str) -> Variable:
    return Variable(
        name=ident, mangled=f"_Z{len(ident)}{ident}", type="int", visibility=Visibility.PUBLIC
    )


@st.composite
def _removal_scenario(draw: st.DrawFn):
    idents = draw(st.lists(_ident, min_size=1, max_size=10, unique=True))
    keep_mask = draw(
        st.lists(st.booleans(), min_size=len(idents), max_size=len(idents))
    )
    return [(ident, keep) for ident, keep in zip(idents, keep_mask)]


class TestFunctionRemovalFactConservation:
    """A function that disappears between snapshots must always be reported
    removed; a function that survives unchanged must never be. Exercises the
    full ``compare()`` pipeline, including the identity-based cross-detector
    dedup stage (ADR-049 Phase 2 wiring, this session)."""

    @_HSETTINGS
    @given(_removal_scenario())
    def test_removed_functions_reported_kept_functions_are_not(
        self, scenario: list[tuple[str, bool]]
    ) -> None:
        functions = [_func(ident) for ident, _ in scenario]
        kept = [_func(ident) for ident, keep in scenario if keep]
        removed_idents = [ident for ident, keep in scenario if not keep]

        old = AbiSnapshot(library="libfact.so", version="1.0", functions=functions)
        new = AbiSnapshot(library="libfact.so", version="2.0", functions=kept)
        result = compare(old, new)

        removal_symbols = {
            c.symbol for c in result.changes if c.kind in _FUNC_REMOVAL_KINDS
        }
        for ident in removed_idents:
            mangled = f"_Z{len(ident)}{ident}v"
            assert mangled in removal_symbols, f"{mangled} silently vanished"
        for ident, keep in scenario:
            if keep:
                mangled = f"_Z{len(ident)}{ident}v"
                assert mangled not in removal_symbols, (
                    f"{mangled} spuriously flagged removed"
                )


class TestVariableRemovalFactConservation:
    """Same conservation invariant for variables (VAR_REMOVED)."""

    @_HSETTINGS
    @given(_removal_scenario())
    def test_removed_variables_reported_kept_variables_are_not(
        self, scenario: list[tuple[str, bool]]
    ) -> None:
        variables = [_var(ident) for ident, _ in scenario]
        kept = [_var(ident) for ident, keep in scenario if keep]
        removed_idents = [ident for ident, keep in scenario if not keep]

        old = AbiSnapshot(library="libfact.so", version="1.0", variables=variables)
        new = AbiSnapshot(library="libfact.so", version="2.0", variables=kept)
        result = compare(old, new)

        removal_symbols = {
            c.symbol for c in result.changes if c.kind in _VAR_REMOVAL_KINDS
        }
        for ident in removed_idents:
            mangled = f"_Z{len(ident)}{ident}"
            assert mangled in removal_symbols, f"{mangled} silently vanished"
        for ident, keep in scenario:
            if keep:
                mangled = f"_Z{len(ident)}{ident}"
                assert mangled not in removal_symbols, (
                    f"{mangled} spuriously flagged removed"
                )

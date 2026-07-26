# Copyright 2026 Nikolay Petrov
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

"""Grammar-level property tests for name_classification.py's Itanium
<local-name> classifiers, added as a PR #641 follow-up.

tests/test_name_classification.py already pins the individual mangled-name
examples PR #641's nine-round review cycle found one at a time (a missing
restrict qualifier, missing Sa-Sd standard substitutions, missing
__gnu_debug, missing recursive lambda nesting, missing user-specializable
customization-point exclusion, ...). Every one of those was a *different*
axis of the same grammar, discovered only because a real compiler emitted
that exact shape on a real binary. This module instead generates the
grammar itself -- nesting depth, CV/ref-qualifier combinations, every
recognized stdlib marker, both stdlib- and library-owned roots -- so a
future gap in the same grammar is caught structurally instead of waiting
for the next real binary to expose it one shape at a time.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.name_classification import (
    is_local_name_symbol,
    is_stdlib_local_name_symbol,
)

pytestmark = pytest.mark.slow

_HSETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Grammar knobs mirroring what name_classification.py's _STDLIB_LOCAL_NAME_RE
# and _USER_SPECIALIZABLE_STD_TEMPLATE_RE actually accept -- see those
# constants' own docstrings for the grammar. Real GCC/clang output only
# pairs non-empty CV/ref qualifiers with a nested-name "N" wrapper (a bare
# `_ZZSt4sortI...` free-function local name never carries qualifiers), so
# _qualifier_and_n below enforces that pairing rather than drawing
# qualifiers and "N" independently of each other.
_QUALIFIER_SUBSETS = ("", "K", "V", "r", "KV", "Kr", "Vr", "KVr")
_REF_QUALIFIERS = ("", "R", "O")
_STDLIB_MARKERS = (
    "St",
    "Sa",
    "Sb",
    "Ss",
    "Si",
    "So",
    "Sd",
    "3std",
    "9__gnu_cxx",
    "11__gnu_debug",
    "10__cxxabiv1",
    "7__cxx11",
)
_CUSTOMIZATION_POINT_CODES = (
    "4hash",
    "4less",
    "7greater",
    "8equal_to",
    "12not_equal_to",
    "10less_equal",
    "13greater_equal",
    "11char_traits",
    "14numeric_limits",
    "15iterator_traits",
    "14default_delete",
    "9formatter",
    "4swap",
)
_LIBRARY_OWNED_ROOTS = ("4pvxs6client6ConfigEvE", "N6mylib5inner3fooEvE", "3fooEvE")
_ENTITY_SUFFIXES = ("5cache", "1x", "9__unused")


@st.composite
def _qualifier_and_n(draw: st.DrawFn) -> tuple[bool, str, str]:
    """A ``(has_n, cv_qualifiers, ref_qualifier)`` triple -- real manglings
    only carry non-empty qualifiers inside an ``N...E`` nested-name
    wrapper, never on a bare (non-nested) local-name production."""
    has_n = draw(st.booleans())
    if not has_n:
        return False, "", ""
    return (
        True,
        draw(st.sampled_from(_QUALIFIER_SUBSETS)),
        draw(st.sampled_from(_REF_QUALIFIERS)),
    )


@st.composite
def _stdlib_local_name(draw: st.DrawFn) -> tuple[str, bool]:
    """A synthetic, grammar-valid stdlib-rooted ``<local-name>`` string,
    paired with the boolean :func:`is_stdlib_local_name_symbol` must return
    for it."""
    extra_zs = draw(st.integers(min_value=0, max_value=3))
    has_n, quals, ref = draw(_qualifier_and_n())
    marker = draw(st.sampled_from(_STDLIB_MARKERS))
    suffix = draw(st.sampled_from(_ENTITY_SUFFIXES))

    # Only St/3std are covered by _USER_SPECIALIZABLE_STD_TEMPLATE_RE --
    # the standard-substitution codes (Sa-Sd) and the runtime-implementation
    # namespaces (__gnu_cxx/__gnu_debug/__cxxabiv1/__cxx11) have no
    # customization-point exclusion to draw here.
    is_customizable_root = marker in ("St", "3std")
    wants_customization = is_customizable_root and draw(st.booleans())

    prefix = "_Z" + "Z" * (1 + extra_zs)
    n_part = "N" if has_n else ""

    if wants_customization:
        cp = draw(st.sampled_from(_CUSTOMIZATION_POINT_CODES))
        mangled = f"{prefix}{n_part}{quals}{ref}{marker}{cp}I6MyTypeEclERKS0_E{suffix}"
        expected = False
    else:
        mangled = f"{prefix}{n_part}{quals}{ref}{marker}6vectorIiSaIiEE9push_backERKiE{suffix}"
        expected = True
    return mangled, expected


@st.composite
def _library_owned_local_name(draw: st.DrawFn) -> tuple[str, bool]:
    """A synthetic ``<local-name>`` string rooted in a non-stdlib
    (library-under-test) function -- must never classify as stdlib-owned,
    at any nesting depth or qualifier combination."""
    extra_zs = draw(st.integers(min_value=0, max_value=3))
    has_n, quals, ref = draw(_qualifier_and_n())
    root = draw(st.sampled_from(_LIBRARY_OWNED_ROOTS))
    suffix = draw(st.sampled_from(_ENTITY_SUFFIXES))

    prefix = "_Z" + "Z" * (1 + extra_zs)
    n_part = "N" if has_n else ""
    mangled = f"{prefix}{n_part}{quals}{ref}{root}{suffix}"
    return mangled, False


@given(case=_stdlib_local_name())
@_HSETTINGS
def test_stdlib_rooted_local_name_grammar_classified_correctly(
    case: tuple[str, bool],
) -> None:
    """Every stdlib-rooted ``<local-name>`` production this module's grammar
    covers -- any nesting depth, any CV/ref-qualifier combination, any
    recognized namespace/standard-substitution marker -- classifies as
    stdlib-owned, UNLESS it's also a user-specializable customization-point
    specialization (e.g. ``std::hash<MyType>``), which must not."""
    mangled, expected = case
    assert is_local_name_symbol(mangled)
    assert is_stdlib_local_name_symbol(mangled) == expected


@given(case=_library_owned_local_name())
@_HSETTINGS
def test_library_owned_local_name_grammar_never_classified_stdlib(
    case: tuple[str, bool],
) -> None:
    """A ``<local-name>`` rooted in a non-stdlib function must never
    classify as stdlib-owned, regardless of nesting depth or qualifier
    combination -- the library-under-test's own public inline-function
    local statics must keep firing a real alignment regression (Codex
    review, PR #641)."""
    mangled, expected = case
    assert expected is False
    assert is_local_name_symbol(mangled)
    assert is_stdlib_local_name_symbol(mangled) is False

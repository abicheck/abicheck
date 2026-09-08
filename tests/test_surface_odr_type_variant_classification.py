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

"""Regression test for ``odr_type_variant``'s public-surface classification
(PR #1147 Codex review, P2 finding 2).

Split into its own small file rather than growing ``test_surface.py``
(``TestChangeClassification`` already covers the identical collision shape
for other kinds -- see ``test_public_type_change_with_private_symbol_name_
collision_is_in_surface`` there) since that file sits close to the
AI-readiness hard cap and this is the one test this fix needs.

``crosscheck._check_odr_type_variant`` sets both ``Change.symbol`` and
``Change.caused_by_type`` to the ODR-conflicted *type*'s own qualified name
-- never a function/variable symbol. ``odr_type_variant`` was missing from
``surface._TYPE_LEVEL_KIND_NAMES``, so ``classify_change_surface`` ran it
through the symbol-level path (``_classify_symbol_level``) first. In C, a
struct tag can legitimately share its bare name with an unrelated,
non-public helper function of the same spelling -- when that happens, the
symbol-level lookup finds the name in ``all_symbols`` but not
``public_symbols`` and returns a confident ``False`` before type
reachability is ever consulted, silently demoting a real, public-type
ODR-conflict finding out of the scored set.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.surface import classify_change_surface, compute_public_surface


def _fn(name, ret="void", vis=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[],
        visibility=vis,
    )


def _rec(name, size=64):
    return RecordType(name=name, kind="struct", size_bits=size, fields=[])


def test_odr_type_variant_with_unrelated_symbol_name_collision_is_in_surface() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[
            _fn("api", ret="Widget *"),
            # An unrelated, non-public helper sharing the type's bare name --
            # a legal, common C shape (a struct tag and a private function
            # can share a spelling with no relationship to each other).
            _fn("Widget", vis=Visibility.HIDDEN, mangled="_ZL6Widgetv"),
        ],
        types=[_rec("Widget")],
    )
    s = compute_public_surface(snap)
    # Confirm the collision this test relies on actually exists in the fixture.
    assert "Widget" in s.public_types
    assert "Widget" in s.all_symbols
    assert "Widget" not in s.public_symbols

    c = Change(
        kind=ChangeKind.ODR_TYPE_VARIANT,
        symbol="Widget",
        caused_by_type="Widget",
        description="",
    )

    # Must be classified through type reachability (in-surface, since
    # "Widget" the *type* is publicly reachable via `api`'s return type),
    # never demoted via the unrelated hidden function of the same name.
    assert classify_change_surface(c, s, s) == (True, None)

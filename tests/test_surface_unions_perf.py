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

"""Round 10 perf fix: a real CI performance-gate failure (``union_churn``
scenario @ size=2000, +48% vs. base) traced to ``surface._classify_type_
level``'s bare-owner-tail guard (added for the round 9/10 Codex finding
that a bare candidate could inherit an unrelated type's reachability)
recomputing ``any("::" in t for t in all_types)`` on *every* single call
instead of once per surface pair -- turning the whole classification loop
(O(findings)) back into O(findings x len(all_types)), exactly the
quadratic blowup ``SurfaceUnions``'s own docstring exists to prevent (see
``surface.py``'s "avoid recomputing... quadratic" comment).

Fixed by computing the signal once in ``surface_unions()`` as a new
``SurfaceUnions.tracks_qualified_names`` field, threaded through to
``_classify_type_level`` instead of recomputed there. These tests pin the
field's own correctness directly, since the classification-level tests in
``tests/test_surface.py``/``tests/test_surface_vtable_demangle.py`` only
exercise it indirectly (through a single classification call, not through
repeated calls sharing one ``SurfaceUnions``, so they cannot by themselves
prove this stays O(1) per call).

Split into its own file (not added to ``tests/test_surface.py``, already at
its own ``architecture/debt.yaml`` ``no_growth`` line-count ceiling) per
this repo's own "move responsibility, don't shrink the file to fit" rule
(``AGENTS.md``, "Files that are large — edit carefully"; the same pattern
``tests/test_pack_application_provenance.py`` already established).
"""

from __future__ import annotations

from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.surface import compute_public_surface, surface_unions


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, mangled=None):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
    )


def _rec(name):
    return RecordType(name=name, kind="struct", size_bits=64)


def _surf(types):
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[_fn("api", ret="Result *")],
        types=[_rec("Result"), *[_rec(t) for t in types]],
    )
    return compute_public_surface(snap)


def test_true_when_a_qualified_type_is_present():
    s = _surf(["ns::Thing"])
    assert surface_unions(s, s).tracks_qualified_names is True


def test_false_when_every_type_is_bare():
    s = _surf(["Thing"])
    assert surface_unions(s, s).tracks_qualified_names is False


def test_true_when_only_the_new_side_has_a_qualified_type():
    old = _surf(["Thing"])
    new = _surf(["Thing", "ns::Other"])
    assert surface_unions(old, new).tracks_qualified_names is True

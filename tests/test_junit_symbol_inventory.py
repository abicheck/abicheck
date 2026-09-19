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

"""``model/symbol_inventory.py`` -- JUnit's compact OLD-side projection.

The governing claim is an *equivalence*: rendering from the inventory must
produce exactly what rendering from the snapshot produced, so the
regression test is a differential one against the full snapshot rather than
a fixed expected document. The retention claim (the snapshot is no longer
reachable from a JUnit-only run) is asserted structurally.
"""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.junit_report import to_junit_xml, to_junit_xml_multi
from abicheck.model import AbiSnapshot, EnumType, Function, RecordType, Variable
from abicheck.model.symbol_inventory import SymbolInventory, build_symbol_inventory
from abicheck.report.junit_inventory import coerce_junit_inventory


def _snapshot(n: int = 12) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(name=f"ns::f{i}", mangled=f"_ZN2ns2f{i}Ev", return_type="void")
            for i in range(n)
        ],
        variables=[
            Variable(name=f"ns::v{i}", mangled=f"_ZN2ns2v{i}E", type="int")
            for i in range(n // 2)
        ],
        types=[RecordType(name=f"ns::S{i}", kind="struct") for i in range(n // 3)],
        enums=[EnumType(name=f"ns::E{i}") for i in range(n // 4)],
    )


def _result(snap: AbiSnapshot) -> DiffResult:
    r = DiffResult(library="libfoo.so", old_version="1.0", new_version="1.1")
    r.changes = [
        Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=snap.functions[0].mangled,
            description="removed",
        ),
        Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="_ZN2ns5addedEv",
            description="added",
        ),
    ]
    return r


class TestEquivalenceWithTheFullSnapshot:
    """A JUnit document rendered from the inventory equals one rendered
    from the snapshot it was projected from -- the whole point."""

    @pytest.mark.parametrize("n", [1, 4, 12, 60])
    def test_documents_are_identical_at_several_sizes(self, n):
        snap = _snapshot(n)
        result = _result(snap)
        from_snapshot = to_junit_xml(result, snap)
        from_inventory = to_junit_xml(result, build_symbol_inventory(snap))
        assert from_inventory == from_snapshot

    def test_multi_pair_release_render_is_identical(self):
        snaps = [_snapshot(9), _snapshot(15)]
        pairs_snap = [(_result(s), s) for s in snaps]
        pairs_inv = [(r, build_symbol_inventory(s)) for (r, s) in pairs_snap]
        assert to_junit_xml_multi(pairs_inv) == to_junit_xml_multi(pairs_snap)

    def test_unchanged_cases_and_the_pass_rate_denominator_survive(self):
        snap = _snapshot(12)
        result = _result(snap)
        doc = ET.fromstring(to_junit_xml(result, build_symbol_inventory(snap)))
        cases = doc.findall(".//testcase")
        # 12 functions + 6 variables + 4 types + 3 enums, plus the one
        # addition that is not in the OLD inventory.
        assert len(cases) == 12 + 6 + 4 + 3 + 1
        failing = {c.get("name") for c in cases if c.find("failure") is not None}
        passing = [c for c in cases if c.find("failure") is None]
        assert snap.functions[0].mangled in failing
        assert len(passing) == len(cases) - len(failing)

    def test_show_only_still_drops_the_unchanged_set(self):
        snap = _snapshot(12)
        result = _result(snap)
        inv = build_symbol_inventory(snap)
        assert to_junit_xml(result, inv, show_only="breaking") == to_junit_xml(
            result, snap, show_only="breaking"
        )


class TestProjection:
    def test_categories_and_order_follow_the_snapshot(self):
        snap = _snapshot(12)
        inv = build_symbol_inventory(snap)
        assert inv.functions == tuple(f.mangled for f in snap.functions)
        assert inv.variables == tuple(v.mangled for v in snap.variables)
        assert inv.types == tuple(t.name for t in snap.types)
        assert inv.enums == tuple(e.name for e in snap.enums)
        mapping = inv.as_symbol_map()
        assert list(mapping) == (
            list(inv.functions)
            + list(inv.variables)
            + list(inv.types)
            + list(inv.enums)
        )
        assert set(mapping.values()) == {"functions", "variables", "types", "enums"}

    def test_a_name_in_two_categories_keeps_the_earlier_one(self):
        """Matches the four sequential ``all_symbols[...] =`` folds it replaces."""
        inv = SymbolInventory(functions=("dup",), types=("dup",))
        assert inv.as_symbol_map() == {"dup": "functions"}

    def test_len_counts_the_distinct_symbol_names(self):
        """``len`` is the pass-rate denominator's size, so it counts the
        *map*, not the sum of the four tuples: a name appearing in two
        categories is one test case, not two."""
        inv = build_symbol_inventory(_snapshot(12))
        assert len(inv) == len(inv.as_symbol_map()) == 12 + 6 + 4 + 3
        assert len(SymbolInventory()) == 0
        assert len(SymbolInventory(functions=("dup",), types=("dup",))) == 1

    def test_an_empty_snapshot_projects_to_an_empty_inventory(self):
        inv = build_symbol_inventory(AbiSnapshot(library="libempty.so", version="1.0"))
        assert inv == SymbolInventory()
        assert inv.as_symbol_map() == {}

    def test_the_public_boundary_accepts_either_shape(self):
        snap = _snapshot(5)
        inv = build_symbol_inventory(snap)
        assert coerce_junit_inventory(None) is None
        assert coerce_junit_inventory(inv) is inv
        assert coerce_junit_inventory(snap) == inv


class TestRetention:
    """The inventory must not reach back to anything it replaced."""

    def test_it_holds_only_strings(self):
        """Exactly ``str``, not merely ``isinstance`` of it.

        The point of the projection is that nothing which could reach back
        into a snapshot survives it, and a ``str`` *subclass* could carry
        arbitrary state and references. ``__class__ is str`` states that
        exactly; ``isinstance`` would accept the very thing this asserts
        against, and ``type(n) is str`` says the same but trips pylint's
        ``unidiomatic-typecheck`` (which cannot tell a deliberate
        exact-type check from an accidental one).
        """
        inv = build_symbol_inventory(_snapshot(12))
        for names in (inv.functions, inv.variables, inv.types, inv.enums):
            assert isinstance(names, tuple)
            assert all(n.__class__ is str for n in names)

    def test_no_snapshot_is_reachable_from_the_inventory(self):
        """The inventory's payload is strings and containers of strings.

        Walks the *payload* -- the four field values and any containers
        inside them -- and never the instance itself, because an earlier
        version of this test did the latter and was unsound. Transitive
        ``gc.get_referents`` from a dataclass instance escapes the object
        almost immediately: the instance refers to its class, a class
        refers to its methods, and a method refers to its ``__globals__``
        -- the whole module namespace, and from there effectively the
        entire heap. It passed locally and failed on CI against a
        ``RecordType`` named ``Payload`` that *no fixture here creates*,
        which is the tell: the assertion was reporting on whatever else
        happened to be resident in the interpreter, not on the inventory.
        A test whose result depends on unrelated tests is worse than no
        test, since it fails somewhere far from its cause.

        Bounding the walk to containers states the real claim exactly and
        makes it deterministic.
        """
        inv = build_symbol_inventory(_snapshot(12))
        pending: list[object] = [inv.functions, inv.variables, inv.types, inv.enums]
        seen: set[int] = set()
        leaves: list[object] = []
        while pending:
            obj = pending.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if isinstance(obj, (tuple, list, set, frozenset)):
                pending.extend(obj)
            elif isinstance(obj, dict):
                pending.extend(obj.keys())
                pending.extend(obj.values())
            else:
                leaves.append(obj)
        assert leaves, "nothing was walked -- the guard would be vacuous"
        offenders = [o for o in leaves if o.__class__ is not str]
        assert not offenders, f"non-str payload reachable: {offenders[:3]}"

    def test_it_is_immutable(self):
        """Frozen, and named exactly: a bare ``Exception`` here would pass
        against an attribute error, a typo in the field name, or any other
        incidental failure, which is not the claim.
        """
        inv = build_symbol_inventory(_snapshot(5))
        before = inv.functions
        with pytest.raises(dataclasses.FrozenInstanceError):
            inv.functions = ()  # type: ignore[misc]
        assert inv.functions is before

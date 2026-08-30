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

"""ADR-063 Phase 2: ``Change.entity_id`` carrier — the function-diff
producer wiring in ``diff_symbols.py`` (the "finding_identity.py algorithm
migration"'s second half, giving ``Change`` an ``EntityId`` to key on).

No consumer reads ``Change.entity_id`` yet (same "carrier lands before
consumer" discipline as ``Function.entity_id``/``Variable.entity_id``) --
these tests pin only that the field is populated correctly at emission.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.identity import entity_id_for_function


def _snap(functions: list[Function] | None = None) -> AbiSnapshot:
    return AbiSnapshot(library="libtest.so.1", version="1.0", functions=functions or [])


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="int", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _change(result: object, kind: ChangeKind) -> object:
    return next(c for c in result.changes if c.kind == kind)  # type: ignore[union-attr]


class TestChangeEntityIdCarrier:
    def test_modified_function_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old = _func("f", "_Z1fi", return_type="int", entity_id=eid)
        new = _func("f", "_Z1fi", return_type="long")
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_RETURN_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_removed_function_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old = _func("f", "_Z1fi", entity_id=eid)
        r = compare(_snap([old]), _snap([]))
        assert _change(r, ChangeKind.FUNC_REMOVED).entity_id == eid  # type: ignore[attr-defined]

    def test_added_function_carries_new_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        new = _func("f", "_Z1fi", entity_id=eid)
        r = compare(_snap([]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_ADDED).entity_id == eid  # type: ignore[attr-defined]

    def test_noexcept_transition_carries_old_side_entity_id(self) -> None:
        # bool_transition-backed check -- a different code path than the
        # ordinary make_change() call sites above.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func("f", "_Z1fv", is_noexcept=False, entity_id=eid)
        new = _func("f", "_Z1fv", is_noexcept=True)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_NOEXCEPT_ADDED).entity_id == eid  # type: ignore[attr-defined]

    def test_no_producer_entity_id_leaves_change_entity_id_none(self) -> None:
        # Never fabricates one: a Function with no entity_id (e.g. a
        # DWARF-only producer that hasn't wired this yet) produces a Change
        # with entity_id=None, not a guessed value.
        old = _func("f", "_Z1fi", return_type="int")
        new = _func("f", "_Z1fi", return_type="long")
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_RETURN_CHANGED).entity_id is None  # type: ignore[attr-defined]

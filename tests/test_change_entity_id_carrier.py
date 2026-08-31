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

    def test_matched_pair_falls_back_to_new_side_when_old_has_none(self) -> None:
        # Codex review: a pre-v28 baseline's Function carries no entity_id
        # at all (schema predates the carrier), while a freshly-dumped new
        # side does -- the documented "old side, else new side" rule must
        # actually fall back, not silently stay None just because old_val
        # exists but its own entity_id is unresolved.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old = _func("f", "_Z1fi", return_type="int")
        new = _func("f", "_Z1fi", return_type="long", entity_id=eid)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_RETURN_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_visibility_changed_carries_old_side_entity_id(self) -> None:
        # A function that goes public -> hidden (still present, not removed)
        # is a separate _check_removed_function construction site from the
        # true-removal one above -- its own entity_id=old-or-new fallback
        # line was untested (codecov flagged it as a patch-coverage gap).
        from abicheck.model import Visibility

        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old = _func("f", "_Z1fi", visibility=Visibility.PUBLIC, entity_id=eid)
        new = _func("f", "_Z1fi", visibility=Visibility.HIDDEN)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.FUNC_VISIBILITY_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_entity_id_excluded_from_change_equality(self) -> None:
        # Codex review: entity_id must be compare=False -- two otherwise-
        # identical Changes (e.g. a legacy baseline without an ID vs a
        # current snapshot with one) must still compare equal, or the
        # changelog's "excluded from equality" promise is false and
        # public-API callers comparing expected findings can break.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old_with_id = _func("f", "_Z1fi", return_type="int", entity_id=eid)
        old_without_id = _func("f", "_Z1fi", return_type="int")
        new = _func("f", "_Z1fi", return_type="long")
        r_with_id = compare(_snap([old_with_id]), _snap([new]))
        r_without_id = compare(_snap([old_without_id]), _snap([new]))
        c_with_id = _change(r_with_id, ChangeKind.FUNC_RETURN_CHANGED)
        c_without_id = _change(r_without_id, ChangeKind.FUNC_RETURN_CHANGED)
        assert c_with_id.entity_id == eid  # type: ignore[attr-defined]
        assert c_without_id.entity_id is None  # type: ignore[attr-defined]
        assert c_with_id == c_without_id

    def test_added_virtual_method_carries_new_side_entity_id(self) -> None:
        # Codex review: an added virtual method routes through
        # diff_cxx_rules.virtual_method_addition's own VIRTUAL_METHOD_ADDED
        # change instead of the ordinary FUNC_ADDED make_change() call --
        # a separate construction site that must carry entity_id too.
        from abicheck.model import RecordType

        owner = RecordType(name="C", kind="class", size_bits=64, vtable=[])
        old_owner = RecordType(name="C", kind="class", size_bits=64, vtable=[])
        eid = entity_id_for_function((), "f", mangled_name="_ZN1C1fEv")
        new_method = _func(
            "C::f",
            "_ZN1C1fEv",
            return_type="void",
            is_virtual=True,
            source_location="c.h:1",
            entity_id=eid,
        )
        old_snap = _snap([])
        old_snap.types = [old_owner]
        new_snap = _snap([new_method])
        new_snap.types = [owner]
        r = compare(old_snap, new_snap)
        assert _change(r, ChangeKind.VIRTUAL_METHOD_ADDED).entity_id == eid  # type: ignore[attr-defined]

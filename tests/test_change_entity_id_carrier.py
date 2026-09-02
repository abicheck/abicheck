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
migration"'s second half, giving ``Change`` an ``EntityId`` to key on) --
and, as of ``TestResolveChangeIdentityConsumesEntityId`` below, the first
real consumer read: ``finding_identity.resolve_change_identity`` folds it
in as an additional ``entity:`` alias (additive only, never promoted to
``primary_id``/tier). Every other test in this file still pins only that
the carrier is populated correctly at emission, the same "carrier lands
before consumer" discipline ``Function.entity_id``/``Variable.entity_id``
went through first.
"""

from __future__ import annotations

from dataclasses import replace

from abicheck.checker import ChangeKind, compare
from abicheck.checker_types import Change
from abicheck.finding_identity import IDENTITY_TIER_CANONICAL, resolve_change_identity
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.identity import (
    Namespace,
    entity_id_for_constant,
    entity_id_for_function,
    entity_id_for_typedef,
)


def _snap(functions: list[Function] | None = None, **kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions or [],
        **kwargs,  # type: ignore[arg-type]
    )


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

    def test_param_default_removed_carries_old_side_entity_id(self) -> None:
        # Codex review: the auxiliary param/deprecated/override detectors
        # below (separate @registry.detector functions, not _check_*
        # matched-pair sites) were fresh omissions from the same carrier
        # contract -- each gets its own construction-site test here.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fii")
        old = _func(
            "f",
            "_Z1fii",
            params=[Param(name="x", type="int", default="1")],
            entity_id=eid,
        )
        new = _func("f", "_Z1fii", params=[Param(name="x", type="int")])
        r = compare(_snap([old], from_headers=True), _snap([new], from_headers=True))
        assert _change(r, ChangeKind.PARAM_DEFAULT_VALUE_REMOVED).entity_id == eid  # type: ignore[attr-defined]

    def test_param_default_changed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fii")
        old = _func(
            "f",
            "_Z1fii",
            params=[Param(name="x", type="int", default="1")],
            entity_id=eid,
        )
        new = _func("f", "_Z1fii", params=[Param(name="x", type="int", default="2")])
        r = compare(_snap([old], from_headers=True), _snap([new], from_headers=True))
        assert _change(r, ChangeKind.PARAM_DEFAULT_VALUE_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_param_renamed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
        old = _func("f", "_Z1fi", params=[Param(name="x", type="int")], entity_id=eid)
        new = _func("f", "_Z1fi", params=[Param(name="y", type="int")])
        r = compare(_snap([old], from_headers=True), _snap([new], from_headers=True))
        assert _change(r, ChangeKind.PARAM_RENAMED).entity_id == eid  # type: ignore[attr-defined]

    def test_return_pointer_level_changed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func(
            "f", "_Z1fv", return_type="int", return_pointer_depth=1, entity_id=eid
        )
        new = _func("f", "_Z1fv", return_type="int", return_pointer_depth=2)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.RETURN_POINTER_LEVEL_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_param_pointer_level_changed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fPi")
        old = _func(
            "f",
            "_Z1fPi",
            params=[Param(name="x", type="int", pointer_depth=1)],
            entity_id=eid,
        )
        new = _func(
            "f", "_Z1fPi", params=[Param(name="x", type="int", pointer_depth=2)]
        )
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.PARAM_POINTER_LEVEL_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_method_access_changed_carries_old_side_entity_id(self) -> None:
        from abicheck.model import AccessLevel

        eid = entity_id_for_function((), "f", mangled_name="_ZN1C1fEv")
        old = _func(
            "C::f",
            "_ZN1C1fEv",
            return_type="void",
            access=AccessLevel.PUBLIC,
            entity_id=eid,
        )
        new = _func("C::f", "_ZN1C1fEv", return_type="void", access=AccessLevel.PRIVATE)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.METHOD_ACCESS_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_func_deprecated_added_carries_old_side_entity_id(self) -> None:
        # ast_producer="castxml" (not just from_headers) is what makes
        # fact_producer() report a positively-known backend -- see its
        # docstring in fact_provenance.py.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func("f", "_Z1fv", entity_id=eid)
        new = _func("f", "_Z1fv", deprecated="use g instead")
        r = compare(
            _snap([old], from_headers=True, ast_producer="castxml"),
            _snap([new], from_headers=True, ast_producer="castxml"),
        )
        assert _change(r, ChangeKind.FUNC_DEPRECATED_ADDED).entity_id == eid  # type: ignore[attr-defined]

    def test_func_deprecated_removed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func("f", "_Z1fv", deprecated="use g instead", entity_id=eid)
        new = _func("f", "_Z1fv")
        r = compare(
            _snap([old], from_headers=True, ast_producer="castxml"),
            _snap([new], from_headers=True, ast_producer="castxml"),
        )
        assert _change(r, ChangeKind.FUNC_DEPRECATED_REMOVED).entity_id == eid  # type: ignore[attr-defined]

    def test_func_override_specifier_added_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_ZN1C1fEv")
        old = _func(
            "C::f",
            "_ZN1C1fEv",
            return_type="void",
            is_virtual=True,
            is_override=False,
            entity_id=eid,
        )
        new = _func(
            "C::f", "_ZN1C1fEv", return_type="void", is_virtual=True, is_override=True
        )
        r = compare(
            _snap([old], from_headers=True, ast_producer="castxml"),
            _snap([new], from_headers=True, ast_producer="castxml"),
        )
        assert _change(r, ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED).entity_id == eid  # type: ignore[attr-defined]

    def test_func_override_specifier_removed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_ZN1C1fEv")
        old = _func(
            "C::f",
            "_ZN1C1fEv",
            return_type="void",
            is_virtual=True,
            is_override=True,
            entity_id=eid,
        )
        new = _func(
            "C::f", "_ZN1C1fEv", return_type="void", is_virtual=True, is_override=False
        )
        r = compare(
            _snap([old], from_headers=True, ast_producer="castxml"),
            _snap([new], from_headers=True, ast_producer="castxml"),
        )
        assert _change(r, ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED).entity_id == eid  # type: ignore[attr-defined]

    def test_hidden_friend_added_transition_carries_old_side_entity_id(self) -> None:
        # Codex review (2nd round): diff_hidden_friends.py/diff_param_qualifiers.py
        # are separate modules from diff_symbols.py's own detectors -- the
        # same carrier omission, in files split out for the file-size cap.
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func("f", "_Z1fv", is_hidden_friend=False, entity_id=eid)
        new = _func("f", "_Z1fv", is_hidden_friend=True)
        r = compare(_snap([old]), _snap([new]))
        assert _change(r, ChangeKind.HIDDEN_FRIEND_ADDED).entity_id == eid  # type: ignore[attr-defined]

    def test_hidden_friend_removed_with_symbol_carries_old_side_entity_id(self) -> None:
        # diff_inline_hidden_friends' own single-sided removal path (symbol
        # gone entirely, not just a flag flip on a matched pair).
        eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
        old = _func("f", "_Z1fv", is_hidden_friend=True, entity_id=eid)
        r = compare(_snap([old]), _snap([]))
        assert _change(r, ChangeKind.HIDDEN_FRIEND_REMOVED).entity_id == eid  # type: ignore[attr-defined]

    def test_param_restrict_changed_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fPi")
        old = _func(
            "f",
            "_Z1fPi",
            params=[Param(name="x", type="int*", is_restrict=False)],
            entity_id=eid,
        )
        new = _func(
            "f", "_Z1fPi", params=[Param(name="x", type="int*", is_restrict=True)]
        )
        r = compare(_snap([old], from_headers=True), _snap([new], from_headers=True))
        assert _change(r, ChangeKind.PARAM_RESTRICT_CHANGED).entity_id == eid  # type: ignore[attr-defined]

    def test_param_became_va_list_carries_old_side_entity_id(self) -> None:
        eid = entity_id_for_function((), "f", mangled_name="_Z1fz")
        old = _func(
            "f",
            "_Z1fz",
            params=[Param(name="x", type="...", is_va_list=False)],
            entity_id=eid,
        )
        new = _func(
            "f", "_Z1fz", params=[Param(name="x", type="va_list", is_va_list=True)]
        )
        r = compare(
            _snap([old], from_headers=True, ast_producer="clang"),
            _snap([new], from_headers=True, ast_producer="clang"),
        )
        assert _change(r, ChangeKind.PARAM_BECAME_VA_LIST).entity_id == eid  # type: ignore[attr-defined]

    def test_ctor_overload_ambiguity_risk_carries_entity_id(self) -> None:
        # Single-sided (new-side-only) construction site -- no old/new
        # fallback pair, just the new ctor's own entity_id.
        from abicheck.model import RecordType

        eid = entity_id_for_function((), "C::C", mangled_name="_ZN1CC1Ej")
        owner = RecordType(name="C", kind="class", size_bits=64)
        old_ctor = _func(
            "C::C", "_ZN1CC1Ei", params=[Param(name="x", type="int")], is_explicit=False
        )
        new_ctor1 = _func(
            "C::C", "_ZN1CC1Ei", params=[Param(name="x", type="int")], is_explicit=False
        )
        new_ctor2 = _func(
            "C::C",
            "_ZN1CC1Ej",
            params=[Param(name="x", type="unsigned int")],
            is_explicit=False,
            entity_id=eid,
        )
        old_snap = _snap([old_ctor])
        old_snap.types = [owner]
        new_snap = _snap([new_ctor1, new_ctor2])
        new_snap.types = [owner]
        r = compare(old_snap, new_snap, scope_to_public_surface=False)
        assert _change(r, ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK).entity_id == eid  # type: ignore[attr-defined]


class TestTypedefAndConstantSidecarsReachTheChange:
    """ADR-063 Phase 2's closing slice: the two detector families whose
    entities have no declaration object, so their identity is read off
    ``AbiSnapshot.typedef_entity_ids``/``constant_entity_ids`` instead."""

    _TYPEDEF_EID = entity_id_for_typedef((Namespace("ns"),), "Alias")
    _CONSTANT_EID = entity_id_for_constant((Namespace("ns"),), "kLimit")

    def _typedef_snap(self, underlying: str | None) -> AbiSnapshot:
        if underlying is None:
            return _snap(from_headers=True)
        return _snap(
            from_headers=True,
            typedefs={"Alias": underlying},
            typedefs_qualified={"ns::Alias": underlying},
            typedef_entity_ids={"ns::Alias": self._TYPEDEF_EID},
        )

    def _constant_snap(self, value: str | None) -> AbiSnapshot:
        if value is None:
            return _snap(from_headers=True)
        return _snap(
            from_headers=True,
            constants={"ns::kLimit": value},
            constant_entity_ids={"ns::kLimit": self._CONSTANT_EID},
        )

    def test_typedef_base_change_carries_the_sidecar_identity(self) -> None:
        r = compare(self._typedef_snap("int"), self._typedef_snap("long"))
        assert (
            _change(r, ChangeKind.TYPEDEF_BASE_CHANGED).entity_id == self._TYPEDEF_EID
        )  # type: ignore[attr-defined]

    def test_typedef_removal_carries_the_old_side_identity(self) -> None:
        r = compare(self._typedef_snap("int"), self._typedef_snap(None))
        assert _change(r, ChangeKind.TYPEDEF_REMOVED).entity_id == self._TYPEDEF_EID  # type: ignore[attr-defined]

    def test_constant_change_carries_the_sidecar_identity(self) -> None:
        r = compare(self._constant_snap("7"), self._constant_snap("8"))
        assert _change(r, ChangeKind.CONSTANT_CHANGED).entity_id == self._CONSTANT_EID  # type: ignore[attr-defined]

    def test_constant_removal_carries_the_old_side_identity(self) -> None:
        r = compare(self._constant_snap("7"), self._constant_snap(None))
        assert _change(r, ChangeKind.CONSTANT_REMOVED).entity_id == self._CONSTANT_EID  # type: ignore[attr-defined]

    def test_constant_addition_carries_the_new_side_identity(self) -> None:
        r = compare(self._constant_snap(None), self._constant_snap("7"))
        assert _change(r, ChangeKind.CONSTANT_ADDED).entity_id == self._CONSTANT_EID  # type: ignore[attr-defined]

    def test_absent_sidecar_leaves_the_change_identity_none(self) -> None:
        # A DWARF-only baseline resolves no typedef/constant scope, so the
        # sidecar is empty -- the detector must report the change with no
        # identity rather than fabricating one from the flat key.
        old = _snap(from_headers=True, typedefs_qualified={"ns::Alias": "int"})
        new = _snap(from_headers=True, typedefs_qualified={"ns::Alias": "long"})
        assert (
            _change(compare(old, new), ChangeKind.TYPEDEF_BASE_CHANGED).entity_id
            is None
        )  # type: ignore[attr-defined]


class TestResolveChangeIdentityConsumesEntityId:
    """ADR-063 Phase 2, the true completion of (c2): the first real
    consumer read of ``Change.entity_id``, via ``resolve_change_identity``'s
    new ``entity:`` alias -- additive only (qualified with the discriminator
    like every other alias there), never promoted to ``primary_id``/tier."""

    _eid = entity_id_for_function((), "f", mangled_name="_Z1fi")
    _ret = Change(ChangeKind.FUNC_RETURN_CHANGED, "_Z1fi", "c", "int", "long")
    _params = Change(ChangeKind.FUNC_PARAMS_CHANGED, "_Z1fi", "c", "(int)", "(long)")

    def test_entity_id_alias_present_iff_entity_id_set(self) -> None:
        a = resolve_change_identity(replace(self._ret, entity_id=self._eid))
        b = resolve_change_identity(self._ret)
        assert any(x.startswith(f"entity:{self._eid.key}\x1f") for x in a.aliases)
        assert not any(x.startswith("entity:") for x in b.aliases)
        assert a.primary_id == b.primary_id
        assert a.tier == b.tier == IDENTITY_TIER_CANONICAL

    def test_entity_id_alias_distinguishes_findings_on_same_entity(self) -> None:
        a = resolve_change_identity(replace(self._ret, entity_id=self._eid))
        b = resolve_change_identity(replace(self._params, entity_id=self._eid))
        ea = {x for x in a.aliases if x.startswith("entity:")}
        eb = {x for x in b.aliases if x.startswith("entity:")}
        assert ea and eb and ea.isdisjoint(eb)

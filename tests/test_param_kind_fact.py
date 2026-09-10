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

"""ADR-063 Phase 5 (eleventh batch) — ``Param.kind_fact`` (schema v45).

``Param.kind`` (value/pointer/reference/rvalue-reference) was never flagged
as an availability-ambiguous case-(a) field the way ``is_restrict``/
``is_va_list`` were: neither header-AST backend (castxml, clang) had ever
set it to anything but the dataclass's own resting ``ParamKind.VALUE``, so
nothing about a header-derived snapshot's blanket VALUE looked wrong by
inspection -- there was no visibly-broken value to notice, unlike
``is_restrict``'s pre-fix blanket ``False`` on clang. ``dwarf_snapshot.py``
has always been a real producer (``DW_TAG_reference_type``/
``DW_TAG_rvalue_reference_type``), so comparing a header-derived snapshot
against a DWARF-derived one of the IDENTICAL, unchanged library fabricated
``FUNC_PARAMS_CHANGED``/``Verdict.BREAKING`` for every pointer/reference
parameter, purely from which side happened to determine ``kind`` at all
(``diff_symbols._params_differ``'s bare ``p_old.kind != p_new.kind``, with
no producer gate — the same shape ``is_restrict``'s own pre-fix bug had).

This is fixed at two levels, both covered here:

1. **Real evidence, not just an evidence gate.** Both header-AST backends
   now genuinely determine ``kind`` (castxml structurally, from its own
   type graph; clang via a documented spelling heuristic, same status as
   its sibling ``_pointer_depth``) instead of leaving every parameter at
   the resting default.
2. **A safety net for evidence that still isn't there** (an older stored
   snapshot, or any future producer gap): ``Param.kind_fact`` bridges to
   ``Fact[ParamKind]`` the same way ``is_restrict``/``is_va_list`` do, and
   ``diff_symbols._params_differ`` gates its comparison through
   ``compare_facts`` instead of reading the raw ``kind`` values.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind
from abicheck.compare.fact_comparison import FactComparability, compare_facts
from abicheck.dwarf_utils import unwrap_cv_typedef
from abicheck.extract.headers.clang.context import qualtype_desugared
from abicheck.extract.headers.clang.functions import _param_kind
from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    Param,
    ParamKind,
    Visibility,
)


def _func(mangled: str, params: list[Param]) -> Function:
    return Function(
        name=mangled,
        mangled=mangled,
        return_type="void",
        params=params,
        visibility=Visibility.PUBLIC,
    )


def _snap(*funcs: Function) -> AbiSnapshot:
    return AbiSnapshot(library="lib", version="1", functions=list(funcs))


class TestParamKindLegacyBridge:
    """``Param.__post_init__``'s ``bridge_legacy_and_fact`` for ``kind``,
    mirroring ``is_restrict``'s identical shape."""

    def test_omitted_kind_bridges_to_not_collected(self) -> None:
        p = Param(name="p", type="int")
        assert (
            p.kind is ParamKind.VALUE
        )  # legacy field still resolves to the resting value
        assert p.kind_fact is not None
        assert p.kind_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_kind_bridges_to_present(self) -> None:
        p = Param(name="p", type="S*", kind=ParamKind.POINTER)
        assert p.kind is ParamKind.POINTER
        assert p.kind_fact is not None
        assert p.kind_fact.status is FactStatus.PRESENT
        assert p.kind_fact.value is ParamKind.POINTER

    def test_explicit_fact_wins_over_legacy_value(self) -> None:
        p = Param(
            name="p",
            type="int",
            kind=ParamKind.VALUE,
            kind_fact=Fact.not_collected(),
        )
        assert p.kind_fact is not None
        assert p.kind_fact.status is FactStatus.NOT_COLLECTED


class TestParamsDifferKindGate:
    """``diff_symbols._params_differ``'s ``compare_facts`` gate, exercised
    through the real detector via ``checker.compare``."""

    def test_header_derived_vs_dwarf_derived_pointer_param_is_no_change(self) -> None:
        """The exact false-positive class this fix closes: one side never
        determined ``kind`` at all (``kind_fact`` NOT_COLLECTED, the
        pre-fix header-AST shape), the other genuinely confirms POINTER
        (DWARF) -- the pair must decline, not manufacture a change, and the
        type spelling itself (which already encodes the pointer) is what
        still keeps the params identical."""
        old = _snap(
            _func(
                "_Z1fP1S",
                [Param(name="s", type="S*", pointer_depth=1)],  # kind never set
            )
        )
        new = _snap(
            _func(
                "_Z1fP1S",
                [Param(name="s", type="S *", kind=ParamKind.POINTER, pointer_depth=1)],
            )
        )
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE
        assert result.changes == []

    def test_both_sides_confirmed_and_actually_different_kind_still_fires(self) -> None:
        """The gate must not become a blanket suppression: two sides that
        both genuinely confirm a DIFFERENT kind still report the change."""
        old = _snap(
            _func(
                "_Z1fRi",
                [Param(name="s", type="int&", kind=ParamKind.REFERENCE)],
            )
        )
        new = _snap(
            _func(
                "_Z1fRi",
                [Param(name="s", type="int", kind=ParamKind.VALUE)],
            )
        )
        result = compare(old, new)
        assert any(c.kind == ChangeKind.FUNC_PARAMS_CHANGED for c in result.changes)

    def test_both_sides_unreliable_declines(self) -> None:
        """Both sides never determined kind (the pre-fix castxml-vs-clang
        shape) -- neither is wrong, so the pair declines rather than
        reading "confirmed same" from two placeholders that happen to
        match."""
        old = _snap(_func("_Z1fPi", [Param(name="p", type="int*", pointer_depth=1)]))
        new = _snap(_func("_Z1fPi", [Param(name="p", type="int *", pointer_depth=1)]))
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE


class TestCompareFactsKindPrimitive:
    """The ``compare_facts`` primitive itself, over ``Param.kind_fact``
    pairs -- the shared contract every consumer (not just
    ``_params_differ``) relies on."""

    def test_not_collected_vs_present_is_incomplete(self) -> None:
        cmp = compare_facts(
            Fact.not_collected(), Fact.present(ParamKind.POINTER), ParamKind.VALUE
        )
        assert cmp.comparability is FactComparability.INCOMPLETE

    def test_present_vs_present_same_value_is_comparable_and_equal(self) -> None:
        cmp = compare_facts(
            Fact.present(ParamKind.POINTER),
            Fact.present(ParamKind.POINTER),
            ParamKind.VALUE,
        )
        assert cmp.is_comparable
        assert cmp.old_value == cmp.new_value == ParamKind.POINTER


class TestClangParamKindSpelling:
    """``_param_kind``'s spelling heuristic (clang has no type-graph node
    kind the way castxml does -- see ``extract/headers/castxml/
    type_resolution.top_level_param_kind`` for the structural counterpart).
    """

    @pytest.mark.parametrize(
        ("qual_type", "expected"),
        [
            ("int", ParamKind.VALUE),
            ("S", ParamKind.VALUE),
            ("const S", ParamKind.VALUE),
            ("S *", ParamKind.POINTER),
            ("int **", ParamKind.POINTER),
            ("S &", ParamKind.REFERENCE),
            ("const S &", ParamKind.REFERENCE),
            ("S &&", ParamKind.RVALUE_REF),
            # Outermost (rightmost, bracket-depth-0) operator wins: a
            # reference TO a pointer is a reference, not a pointer.
            ("int *&", ParamKind.REFERENCE),
            ("int *&&", ParamKind.RVALUE_REF),
            # Template/bracket interior must not count.
            ("std::vector<int *>", ParamKind.VALUE),
            ("std::vector<int *> &", ParamKind.REFERENCE),
        ],
    )
    def test_spellings(self, qual_type: str, expected: ParamKind) -> None:
        assert _param_kind(qual_type) is expected


class TestQualtypeDesugared:
    """``context.qualtype_desugared`` -- clang's own typedef-unwrap signal
    (Codex review, PR #1200): a typedef'd parameter's ``qualType`` is the
    bare alias name, with no ``&``/``*`` token for ``_param_kind`` to find,
    so the caller must pass the desugared spelling instead."""

    def test_prefers_desugared_when_present(self) -> None:
        node = {"type": {"qualType": "Ref", "desugaredQualType": "int &"}}
        assert qualtype_desugared(node) == "int &"

    def test_falls_back_to_qualtype_when_no_typedef(self) -> None:
        node = {"type": {"qualType": "int *"}}
        assert qualtype_desugared(node) == "int *"

    def test_falls_back_when_no_type_at_all(self) -> None:
        assert qualtype_desugared({}) == ""

    def test_empty_desugared_falls_back_to_qualtype(self) -> None:
        # Defensive: clang's real output never emits an empty
        # `desugaredQualType`, but a falsy value must not silently win over
        # a real `qualType`.
        node = {"type": {"qualType": "int *", "desugaredQualType": ""}}
        assert qualtype_desugared(node) == "int *"


class TestUnwrapCvTypedef:
    """``dwarf_utils.unwrap_cv_typedef`` (Codex review, PR #1200): mirrors
    ``dwarf_snapshot.py``'s own pointer-depth unwrap set
    (const/volatile/typedef), used so a typedef'd reference/rvalue-reference
    is recognized the same way a typedef'd pointer already was."""

    class _FakeDie:
        def __init__(self, tag: str) -> None:
            self.tag = tag

    def test_unwraps_const_typedef_chain_to_reference(self, monkeypatch) -> None:
        import abicheck.dwarf_utils as dwarf_utils_module

        ref_die = self._FakeDie("DW_TAG_reference_type")
        const_die = self._FakeDie("DW_TAG_const_type")
        typedef_die = self._FakeDie("DW_TAG_typedef")
        chain = {id(typedef_die): const_die, id(const_die): ref_die}
        monkeypatch.setattr(
            dwarf_utils_module,
            "resolve_type_die",
            lambda die, CU: chain.get(id(die)),
        )
        assert unwrap_cv_typedef(typedef_die, CU=None) is ref_die

    def test_does_not_unwrap_through_pointer(self) -> None:
        pointer_die = self._FakeDie("DW_TAG_pointer_type")
        assert unwrap_cv_typedef(pointer_die, CU=None) is pointer_die

    def test_none_input_returns_none(self) -> None:
        assert unwrap_cv_typedef(None, CU=None) is None

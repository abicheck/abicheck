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

"""``closure_discriminator_free_identity`` and the closure-respelling filter
(:mod:`abicheck.compare.closure_respelling`).

Oracle: a hand-written expected spelling per case, never the implementation's
own regex. The end-to-end behaviour (castxml dumps, the real checker) is pinned
separately in ``test_closure_param_churn_pipeline.py``.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.closure_respelling import (
    RESPELLING_KINDS,
    closure_ordinal_map_is_uniform,
    drop_closure_respellings,
    is_closure_respelling,
)
from abicheck.model.change import Change
from abicheck.model.declarations import Function
from abicheck.model.fact import Fact
from abicheck.model.graph_identity import (
    closure_discriminator_free_identity as ident,
    closure_ordinals,
)
from abicheck.model.snapshot import AbiSnapshot

_KEY = "_Z8run_task10raii_guardIZN8tbb_like8arena_fnIiE4makeEvEUlvE_E"


def _snapshot(*keys: str, exported: bool = True) -> AbiSnapshot:
    fn_list = [
        Function(
            name=k,
            mangled=k,
            return_type="void",
            binary_exported_fact=Fact.present(exported),
        )
        for k in keys
    ]
    return AbiSnapshot(library="lib.so", version="1", functions=fn_list)


def _change(kind: ChangeKind, old: str, new: str, symbol: str = _KEY) -> Change:
    return Change(
        kind=kind, symbol=symbol, description="d", old_value=old, new_value=new
    )


class TestDiscriminatorFreeIdentity:
    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("raii_guard<(lambda:lib.h#1)>", "raii_guard<(lambda)>"),
            ("raii_guard<(lambda:lib.h#2)>", "raii_guard<(lambda)>"),
            ("raii_guard<(lambda:lib.h:522:26)>", "raii_guard<(lambda)>"),
            ("raii_guard<(lambda at /a/lib.h:4:37)>", "raii_guard<(lambda)>"),
            ("(unnamed struct:lib.h#3)", "(unnamed struct)"),
            (
                "raii_guard<(lambda:lib.h#1), (lambda:lib.h#2)>",
                "raii_guard<(lambda), (lambda)>",
            ),
            ("plain<int>", "plain<int>"),
            ("", ""),
        ],
    )
    def test_strips_every_closure_discriminator_to_the_bare_marker(
        self, spelling: str, expected: str
    ) -> None:
        assert ident(spelling) == expected

    @pytest.mark.parametrize(
        "spelling",
        [
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h:522:26)>",
            'Tag<"lambda:foo.h#1">, raii_guard<(lambda:lib.h#7)>',
        ],
    )
    def test_is_idempotent(self, spelling: str) -> None:
        once = ident(spelling)
        assert ident(once) == once

    @pytest.mark.parametrize("n", [1, 2, 9, 40])
    def test_ordinal_and_coordinate_spellings_of_one_closure_agree(
        self, n: int
    ) -> None:
        # Oracle: the marker kind, not the discriminator, decides identity.
        assert (
            ident(f"f<(lambda:lib.h#{n})>")
            == ident("f<(lambda:lib.h:5:27)>")
            == "f<(lambda)>"
        )

    def test_marker_kinds_are_not_collapsed(self) -> None:
        assert ident("f<(lambda:lib.h#1)>") != ident("f<(unnamed struct:lib.h#1)>")

    def test_quoted_literal_is_left_untouched(self) -> None:
        literal = 'Tag<"lambda:foo.h#1">'
        assert ident(literal) == literal


class TestRespellingFilter:
    def test_drops_ordinal_respelling_on_symbol_exported_under_same_key(self) -> None:
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
        )
        assert is_closure_respelling(c, {_KEY}, {_KEY})

    @pytest.mark.parametrize("kind", sorted(RESPELLING_KINDS, key=str))
    def test_every_signature_kind_is_covered(self, kind: ChangeKind) -> None:
        c = _change(
            kind, "raii_guard<(lambda:lib.h#1)>", "raii_guard<(lambda:lib.h#2)>"
        )
        assert is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kept_when_not_exported_on_one_side(self) -> None:
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
        )
        assert not is_closure_respelling(c, {_KEY}, set())
        assert not is_closure_respelling(c, set(), {_KEY})

    def test_kept_when_symbol_is_not_exported_at_all(self) -> None:
        # Inline / template-member / synthetic keys have no binary evidence.
        c = _change(
            ChangeKind.FUNC_RETURN_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
            symbol="_Z10make_guardv",
        )
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kept_when_the_non_closure_part_changes(self) -> None:
        # Same exported key, but the guard template itself changed: a real change.
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "other_guard<(lambda:lib.h#2)>",
        )
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kept_when_a_plain_type_changes(self) -> None:
        c = _change(ChangeKind.FUNC_PARAMS_CHANGED, "int", "long")
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kept_when_the_closure_marker_is_replaced_by_a_different_type(self) -> None:
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<int>",
        )
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kept_when_spelling_is_unchanged(self) -> None:
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#1)>",
        )
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_kind_outside_the_signature_set_is_kept(self) -> None:
        c = _change(
            ChangeKind.FUNC_REMOVED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
        )
        assert not is_closure_respelling(c, {_KEY}, {_KEY})

    def test_drop_keeps_order_and_only_the_respellings(self) -> None:
        keep_a = _change(ChangeKind.FUNC_PARAMS_CHANGED, "int", "long", symbol="_Za")
        drop = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
        )
        keep_b = _change(ChangeKind.FUNC_RETURN_CHANGED, "int", "void", symbol="_Zb")
        old = _snapshot(_KEY, "_Za", "_Zb")
        new = _snapshot(_KEY, "_Za", "_Zb")
        out = drop_closure_respellings([keep_a, drop, keep_b], old, new)
        assert out == [keep_a, keep_b]

    def test_export_evidence_comes_from_the_fact_not_the_symbol_name(self) -> None:
        # Same key, but the export fact is absent on the new side: no evidence.
        c = _change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            "raii_guard<(lambda:lib.h#1)>",
            "raii_guard<(lambda:lib.h#2)>",
        )
        out = drop_closure_respellings(
            [c], _snapshot(_KEY), _snapshot(_KEY, exported=False)
        )
        assert out == [c]


class TestOrdinalMapIsUniform:
    """Oracle: the order-preserving rule written out per case, not the
    implementation's own map."""

    @staticmethod
    def _c(old: str, new: str) -> Change:
        return _change(ChangeKind.FUNC_PARAMS_CHANGED, old, new)

    @pytest.mark.parametrize("shift", [1, 2, 7])
    def test_a_uniform_shift_is_accepted(self, shift: int) -> None:
        cands = [
            self._c("f<(lambda:h#1)>", f"f<(lambda:h#{1 + shift})>"),
            self._c("g<(lambda:h#1)>", f"g<(lambda:h#{1 + shift})>"),
        ]
        assert closure_ordinal_map_is_uniform(cands)

    def test_reordering_two_closures_across_functions_is_rejected(self) -> None:
        cands = [
            self._c("run_one<(lambda:h#1)>", "run_one<(lambda:h#2)>"),
            self._c("run_two<(lambda:h#2)>", "run_two<(lambda:h#1)>"),
        ]
        assert not closure_ordinal_map_is_uniform(cands)

    def test_swapping_two_closures_inside_one_signature_is_rejected(self) -> None:
        cands = [
            self._c("f<(lambda:h#1),(lambda:h#2)>", "f<(lambda:h#2),(lambda:h#1)>")
        ]
        assert not closure_ordinal_map_is_uniform(cands)

    def test_one_closure_mapped_to_two_different_ordinals_is_rejected(self) -> None:
        cands = [
            self._c("f<(lambda:h#1)>", "f<(lambda:h#2)>"),
            self._c("g<(lambda:h#1)>", "g<(lambda:h#3)>"),
        ]
        assert not closure_ordinal_map_is_uniform(cands)

    def test_mismatched_marker_counts_are_rejected(self) -> None:
        assert not closure_ordinal_map_is_uniform(
            [self._c("f<(lambda:h#1)>", "f<(lambda:h#1),(lambda:h#2)>")]
        )


class TestClosureOrdinals:
    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("f<(lambda:h#2),(lambda:h#1)>", (2, 1)),
            ('Tag<"lambda:x.h#9">', ()),
            ("plain<int>", ()),
            ("f<(lambda:h:5:27)>", ()),
        ],
    )
    def test_ordinals_in_order_outside_quoted_literals(
        self, spelling: str, expected: tuple[int, ...]
    ) -> None:
        assert closure_ordinals(spelling) == expected

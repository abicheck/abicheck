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

"""A variable is reported by its qualified name, and one value change is one finding.

A class-static ``static constexpr int K_V`` reached two header-tier detectors:
``compare.constants`` reported ``K_S::K_V`` (CONSTANT_CHANGED) and the ABICC
parity detector reported the variable as ``K_V`` (VAR_VALUE_CHANGED), which no
dedup key could pair. The oracles below are literal spellings, not the
implementation's own helper.

The end-to-end cell is ``test_one_change_one_finding_matrix.py``.
"""

from __future__ import annotations

import pytest

from abicheck.compare.finding_dedup import deduplicate_cross_detector
from abicheck.compare.variable_names import qualified_variable_name
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.identity import EntityId, EntityKind, Namespace


@pytest.mark.parametrize(
    ("mangled", "leaf", "expected"),
    [
        pytest.param("_ZN3K_S3K_VE", "K_V", "K_S::K_V", id="class-static-member"),
        pytest.param(
            "_ZN4demo6kLimitE", "kLimit", "demo::kLimit", id="namespace-variable"
        ),
        pytest.param("g_var", "g_var", "g_var", id="c-global-keeps-leaf"),
        pytest.param("_ZN3K_S3K_VE", "", "K_S::K_V", id="mangled-only-leaf-empty"),
    ],
)
def test_variable_name_is_its_qualified_spelling(
    mangled: str, leaf: str, expected: str
) -> None:
    assert qualified_variable_name(None, leaf, mangled=mangled) == expected


def test_scope_bearing_entity_id_names_the_variable() -> None:
    entity = EntityId(
        scope=(Namespace(name="demo"),), kind=EntityKind.VARIABLE, leaf_name="kLimit"
    )
    assert qualified_variable_name(entity, "kLimit") == "demo::kLimit"


def test_function_shaped_demangling_is_not_a_variable_name() -> None:
    # A demangled parameter list is a function's spelling, not a variable's.
    assert qualified_variable_name(None, "f", mangled="_Z1fi") == "f"


def _constant(symbol: str, old: str, new: str) -> Change:
    return Change(
        kind=ChangeKind.CONSTANT_CHANGED,
        symbol=symbol,
        description=f"Preprocessor constant value changed: {symbol}",
        old_value=old,
        new_value=new,
    )


def _variable(mangled: str, old: str, new: str) -> Change:
    entity = EntityId(
        scope=(),
        kind=EntityKind.VARIABLE,
        leaf_name="",
        extra=("mangled", mangled),
    )
    return Change(
        kind=ChangeKind.VAR_VALUE_CHANGED,
        symbol=mangled,
        description="Global data value changed",
        old_value=old,
        new_value=new,
        entity_id=entity,
    )


@pytest.mark.parametrize(
    ("changes", "survivors"),
    [
        pytest.param(
            [_constant("K_S::K_V", "1", "2"), _variable("_ZN3K_S3K_VE", "1", "2")],
            [ChangeKind.CONSTANT_CHANGED],
            id="static-member-one-finding",
        ),
        pytest.param(
            [_constant("K_S::K_V", "1", "2"), _variable("_ZN3K_S3K_VE", "1", "3")],
            [ChangeKind.CONSTANT_CHANGED, ChangeKind.VAR_VALUE_CHANGED],
            id="different-value-is-a-different-observation",
        ),
        pytest.param(
            [_constant("K_S::K_W", "1", "2"), _variable("_ZN3K_S3K_VE", "1", "2")],
            [ChangeKind.CONSTANT_CHANGED, ChangeKind.VAR_VALUE_CHANGED],
            id="different-entity-never-pairs",
        ),
        pytest.param(
            [_variable("_ZN3K_S3K_VE", "1", "2")],
            [ChangeKind.VAR_VALUE_CHANGED],
            id="variable-alone-is-kept",
        ),
    ],
)
def test_value_restatement_collapses_to_the_constant(
    changes: list[Change], survivors: list[ChangeKind]
) -> None:
    kept = deduplicate_cross_detector(changes)
    assert [c.kind for c in kept] == survivors


def _deletion(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description=f"{kind.value} {symbol}")


def test_deleted_function_is_one_finding() -> None:
    # `= delete` is reported as FUNC_DELETED; the export loss and the implied
    # inline are consequences of that one deletion (see finding_dedup).
    kept = deduplicate_cross_detector(
        [
            _deletion(ChangeKind.FUNC_VISIBILITY_CHANGED, "_Z5df_fnv"),
            _deletion(ChangeKind.FUNC_DELETED, "_Z5df_fnv"),
            _deletion(ChangeKind.FUNC_BECAME_INLINE, "_Z5df_fnv"),
        ]
    )
    assert [c.kind for c in kept] == [ChangeKind.FUNC_DELETED]


def test_deletion_folds_only_its_own_symbol() -> None:
    kept = deduplicate_cross_detector(
        [
            _deletion(ChangeKind.FUNC_DELETED, "_Z5df_fnv"),
            _deletion(ChangeKind.FUNC_BECAME_INLINE, "_Z5ot_fnv"),
            _deletion(ChangeKind.FUNC_VISIBILITY_CHANGED, "_Z5ot_fnv"),
        ]
    )
    assert [(c.kind, c.symbol) for c in kept] == [
        (ChangeKind.FUNC_DELETED, "_Z5df_fnv"),
        (ChangeKind.FUNC_BECAME_INLINE, "_Z5ot_fnv"),
        (ChangeKind.FUNC_VISIBILITY_CHANGED, "_Z5ot_fnv"),
    ]


def test_plain_removal_keeps_its_visibility_finding() -> None:
    # Without a deletion, a visibility loss is the finding; nothing folds it.
    kept = deduplicate_cross_detector(
        [_deletion(ChangeKind.FUNC_VISIBILITY_CHANGED, "_Z5f_fnv")]
    )
    assert [c.kind for c in kept] == [ChangeKind.FUNC_VISIBILITY_CHANGED]

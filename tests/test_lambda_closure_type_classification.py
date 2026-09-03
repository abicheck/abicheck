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

"""Clang-spelled lambda closure types are not the library's own ABI surface.

``_ANONYMOUS_TYPE_MARKERS`` recognized GCC/DWARF's ``{lambda(...)#1}`` and the
``<lambda`` spelling but not clang's ``(lambda at <path>:<line>:<col>)`` — nor
the ``(lambda:<file>:<line>:<col>)`` form
:func:`strip_anonymous_type_location` normalizes it to. A template
instantiated over a closure (``raii_guard<(lambda:task_group.h:522:26)>``)
therefore carried a source *line number* in its ABI identity: an unrelated
edit earlier in the header shifted it and the shifted spelling read as a whole
type removed plus a whole type added.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.name_classification import (
    is_abi_surface_type_name,
    is_non_abi_surface_type,
    strip_anonymous_type_location,
)

# Every spelling a supported backend can produce for the same closure-
# parameterized instantiation.
_CLOSURE_SPELLINGS = [
    # clang, raw
    "tbb::detail::d1::raii_guard<(lambda at /src/tbb/task_group.h:522:26)>",
    # clang, after strip_anonymous_type_location
    "tbb::detail::d1::raii_guard<(lambda:task_group.h:522:26)>",
    # GCC / DWARF
    "tbb::detail::d1::raii_guard<{lambda()#1}>",
    # castxml
    "tbb::detail::d1::raii_guard<<lambda_1>>",
]


@pytest.mark.parametrize("spelling", _CLOSURE_SPELLINGS)
def test_every_backend_spelling_is_off_surface(spelling: str) -> None:
    assert is_non_abi_surface_type(spelling)
    assert not is_abi_surface_type_name(spelling, exclude_stdlib=True)


def test_normalized_clang_spelling_round_trips_through_the_stripper() -> None:
    """The stripper and the classifier must agree: normalizing a raw clang
    spelling must not turn an off-surface type into an on-surface one."""
    raw = _CLOSURE_SPELLINGS[0]
    stripped = strip_anonymous_type_location(raw)
    assert stripped != raw
    assert is_non_abi_surface_type(stripped)


def test_an_ordinary_template_instantiation_stays_on_surface() -> None:
    assert is_abi_surface_type_name(
        "tbb::detail::d1::raii_guard<int>", exclude_stdlib=True
    )


def _snap(version: str, line: int) -> AbiSnapshot:
    ty = f"tbb::detail::d1::raii_guard<(lambda:task_group.h:{line}:26)>"
    return AbiSnapshot(
        library="libtbb.so",
        version=version,
        types=[
            RecordType(
                name=ty,
                kind="class",
                size_bits=64,
                fields=[TypeField(name="m_func", type="int", offset_bits=0)],
            )
        ],
    )


def test_lambda_location_churn_produces_no_type_level_findings() -> None:
    """An unrelated edit shifts the lambda's line; nothing about the library's
    own ABI surface changed, so no type-level finding may be emitted."""
    result = compare(_snap("2021", 522), _snap("2022", 530))
    type_kinds = {
        ChangeKind.TYPE_ADDED,
        ChangeKind.TYPE_REMOVED,
        ChangeKind.TYPE_SIZE_CHANGED,
    }
    assert not ({c.kind for c in result.changes} & type_kinds)

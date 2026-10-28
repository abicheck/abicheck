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

"""An empty-header warning names only a side with no declaration of any kind."""

from __future__ import annotations

import pytest

from abicheck.model.declaration_store import DECLARATION_KINDS
from abicheck.model.empty_header_surface import (
    EMPTY_HEADER_SURFACE_MARKER,
    empty_header_surface_warnings,
)
from abicheck.model.snapshot import AbiSnapshot

#: Kinds that only annotate an entry stored under another kind.
_SIDECARS = {"typedef_entity_ids", "constant_entity_ids"}


def _header_snapshot() -> AbiSnapshot:
    snap = AbiSnapshot(library="libx", version="1")
    snap.from_headers = True
    return snap


@pytest.mark.parametrize("kind", sorted(set(DECLARATION_KINDS) - _SIDECARS))
def test_one_declaration_of_any_kind_is_not_an_empty_surface(kind: str) -> None:
    snap = _header_snapshot()
    store = getattr(snap.declarations, kind)
    if isinstance(store, dict):
        store["x"] = "1"
    else:
        store.append(object())
    assert empty_header_surface_warnings(None, snap) == []


def test_a_header_side_with_nothing_is_named() -> None:
    warnings = empty_header_surface_warnings(None, _header_snapshot())
    assert len(warnings) == 1
    assert warnings[0].startswith("new:")
    assert EMPTY_HEADER_SURFACE_MARKER in warnings[0]


def test_a_binary_only_side_is_not_named() -> None:
    snap = AbiSnapshot(library="libx", version="1")
    snap.from_headers = False
    assert empty_header_surface_warnings(None, snap) == []

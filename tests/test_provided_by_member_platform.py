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

"""Evidence-entity-model gap B3: ``provided_by`` is keyed per member platform.

A release whose members come from different export-table kinds (an ELF
``.so`` beside a PE ``.dll`` or a Mach-O ``.dylib``) used to key every
``provided_by`` edge on one side-wide platform -- ``"mixed"`` -- whose
``binary_symbol://mixed/...`` ids join no Phase 2 export node. Each edge's
source must be the export node of the member that actually provides it.

Oracle: the member's own table kind, fixed per member by the generator,
never ``BundleExportIndex.platform`` (the value under test's former input).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.compare.ownership_relations import (
    EDGE_KIND_PROVIDED_BY,
    provider_relations,
)
from abicheck.model.graph_join import binary_symbol_node_id

_PLATFORMS = ("elf", "pe", "macho")


def _member(platform: str, names: set[str]) -> SimpleNamespace:
    return SimpleNamespace(export_names=frozenset(names), export_platform=platform)


def _expected(members: dict[str, tuple[str, set[str]]]) -> set[tuple[str, str, str]]:
    return {
        (
            binary_symbol_node_id(platform, sym),
            f"release_member://{name}",
            EDGE_KIND_PROVIDED_BY,
        )
        for name, (platform, names) in members.items()
        for sym in names
    }


def _edges(members: dict[str, tuple[str, set[str]]]) -> set[tuple[str, str, str]]:
    index = build_bundle_export_index(
        "new", {n: _member(p, s) for n, (p, s) in members.items()}
    )
    return set(provider_relations(index).edges())


@pytest.mark.xfail(strict=True, reason="gap B3")
def test_mixed_elf_and_pe_release_keys_each_edge_on_its_member() -> None:
    members = {
        "libfoo.so": ("elf", {"foo", "shared"}),
        "foo.dll": ("pe", {"foo_win", "shared"}),
    }
    edges = _edges(members)
    assert edges == _expected(members)
    assert not any("://mixed/" in src for src, _, _ in edges)


@pytest.mark.xfail(strict=True, reason="gap B3")
def test_mixed_elf_and_macho_release_keys_each_edge_on_its_member() -> None:
    members = {"libfoo.so": ("elf", {"a"}), "libfoo.dylib": ("macho", {"a"})}
    assert _edges(members) == _expected(members)


@pytest.mark.xfail(strict=True, reason="gap B3")
@settings(max_examples=60, deadline=None)
@given(
    st.dictionaries(
        st.from_regex(r"lib[a-z]{1,4}", fullmatch=True),
        st.tuples(
            st.sampled_from(_PLATFORMS),
            st.sets(st.sampled_from(["a", "b", "c", "d"]), max_size=4),
        ),
        min_size=1,
        max_size=5,
    )
)
def test_every_edge_names_its_own_members_platform(
    members: dict[str, tuple[str, set[str]]],
) -> None:
    assert _edges(members) == _expected(members)

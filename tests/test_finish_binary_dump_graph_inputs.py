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

"""``finish_binary_dump``'s graph branch: the typed tail attaches the
default header graph only when graph inputs are given and not suppressed."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model.snapshot import AbiSnapshot
from abicheck.workflows import snapshot_factory
from abicheck.workflows.dump import default_header_graph
from abicheck.workflows.dump.default_header_graph import graph_public_scope


@pytest.fixture
def attach_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _spy(snap: AbiSnapshot, *args: object, **kwargs: object) -> AbiSnapshot:
        calls.append({"graph": args[0], "includes": args[1], **kwargs})
        return snap

    monkeypatch.setattr(default_header_graph, "_attach_header_graph", _spy)
    return calls


def _finish(graph_inputs: tuple[list[Path], list[Path], str | None, bool] | None):
    return snapshot_factory.finish_binary_dump(
        AbiSnapshot(library="libx", version="1.0"),
        None,
        None,
        read_dwarf=False,
        graph_inputs=graph_inputs,
    )


def test_no_graph_inputs_attaches_no_graph(
    attach_calls: list[dict[str, object]],
) -> None:
    snap = _finish(None)
    assert snap.library == "libx"
    assert attach_calls == []


def test_suppressed_graph_inputs_attach_no_graph(
    attach_calls: list[dict[str, object]],
) -> None:
    _finish(([Path("api.h")], [], "c++", True))
    assert len(attach_calls) == 1
    assert attach_calls[0]["graph"] is False


def test_enabled_graph_inputs_attach_one_graph(
    attach_calls: list[dict[str, object]],
) -> None:
    _finish(([Path("api.h")], [], "c++", False))
    assert len(attach_calls) == 1
    assert attach_calls[0]["graph"] is True


def test_empty_public_lists_fall_back_to_the_header_operands() -> None:
    # An empty explicit list is no boundary: the -H operands define it.
    headers = [Path("include/api.h")]
    assert graph_public_scope(headers, None, None) == (headers, [])
    assert graph_public_scope(headers, [], []) == (headers, [])


def test_explicit_public_dirs_define_the_boundary() -> None:
    headers = [Path("include/api.h")]
    files, dirs = graph_public_scope(headers, None, [Path("include")])
    assert files == []
    assert dirs == [Path("include")]

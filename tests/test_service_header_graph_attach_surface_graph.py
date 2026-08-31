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

"""ADR-063 Phase 3 (D5), slice 12: ``_attach_header_graph`` shares exactly one
``SourceGraphSummary`` instance between ``AbiSnapshot.surface_graph`` and
``AbiSnapshot.build_source.source_graph`` -- never two independently
constructed summary objects that merely happen to agree, per the phase's own
shared-assembly design (see ``compare/surface_graph.py``'s module docstring
for the deliberately-not-yet-unified node-id-namespace gap this leaves
open)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import dumper_clang
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.service_header_graph_attach import _attach_header_graph


def _snap_with_public_function() -> AbiSnapshot:
    return AbiSnapshot(
        library="lib",
        version="1.0",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="void",
                params=[],
                visibility=Visibility.PUBLIC,
                source_header="api.h",
            )
        ],
    )


def test_surface_graph_is_the_same_object_as_build_source_source_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: False)
    header = tmp_path / "api.h"
    header.write_text("void f(void);\n")

    snap = _attach_header_graph(
        _snap_with_public_function(),
        header_graph=True,
        header_graph_includes=False,
        headers=[header],
        includes=[],
        lang="c",
        compile=None,
        public_headers=None,
        public_header_dirs=None,
    )

    assert snap.surface_graph is not None
    assert snap.build_source is not None
    assert snap.surface_graph is snap.build_source.source_graph


def test_surface_graph_carries_both_builders_own_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The L5 builder's own ``source_decl``/``header`` nodes (from
    ``build_header_only_graph``) and this phase's own ``declaration``/
    ``header`` nodes (from ``build_public_surface_facts``) both land in the
    one shared graph -- proving the assembly step actually threads the same
    instance into both builders, not just constructs one and discards it."""
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: False)
    header = tmp_path / "api.h"
    header.write_text("void f(void);\n")

    snap = _attach_header_graph(
        _snap_with_public_function(),
        header_graph=True,
        header_graph_includes=False,
        headers=[header],
        includes=[],
        lang="c",
        compile=None,
        public_headers=None,
        public_header_dirs=None,
    )
    graph = snap.surface_graph
    assert graph is not None

    kinds = {node.kind for node in graph.nodes}
    # header_graph.py's own L5 seeding (flat-snapshot fallback, clang unavailable).
    assert "source_decl" in kinds
    # compare/surface_graph.py's own Phase 3 facts.
    assert "declaration" in kinds
    assert any(node.label == "f" and node.kind == "declaration" for node in graph.nodes)


def test_no_op_when_header_graph_not_requested(tmp_path: Path) -> None:
    """The pre-existing early-return (``not header_graph or not headers``)
    is unaffected -- ``surface_graph`` stays unset, matching every
    pre-Phase-3 caller's behavior exactly."""
    snap = _attach_header_graph(
        _snap_with_public_function(),
        header_graph=False,
        header_graph_includes=False,
        headers=[],
        includes=[],
        lang="c",
        compile=None,
        public_headers=None,
        public_header_dirs=None,
    )
    assert snap.surface_graph is None
    assert snap.build_source is None

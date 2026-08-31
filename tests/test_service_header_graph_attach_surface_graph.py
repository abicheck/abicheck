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


def test_surface_graph_carries_the_l5_builders_own_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The L5 builder's own ``source_decl``/``header`` nodes (from
    ``build_header_only_graph``) land in the shared graph -- proving the
    assembly step actually threads a real, populated instance into
    ``AbiSnapshot.surface_graph``, not an empty placeholder."""
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


def test_compare_surface_graph_facts_are_not_populated_eagerly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``compare/surface_graph.py``'s own ``declaration``/``type``/
    ``header``/``symbol`` facts are deliberately NOT added by
    ``_attach_header_graph`` itself -- this step runs unconditionally on
    essentially every real dump (G31 Phase A), and nothing in this phase's
    own wiring reads those facts back yet (``PublicSurfaceQuery.resolve()``
    reads each declaration's ``.entity_id`` directly, never the graph).
    Paying that per-declaration walk on every dump for a feature with no
    current reader regressed the header-graph attach-cost perf gate by
    47-96% at realistic sizes; this test pins the fix. The shared instance
    is still there for a future consumer to populate explicitly -- see the
    companion test below."""
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
    assert "declaration" not in kinds
    assert "symbol" not in kinds


def test_compare_surface_graph_facts_can_still_be_populated_on_the_shared_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller that does need this phase's facts can still populate them
    onto the exact same shared graph instance ``_attach_header_graph``
    already produced -- the deferred-population fix does not orphan
    ``compare/surface_graph.py``'s builder, it only stops calling it from
    inside the always-on dump path."""
    from abicheck.compare.surface_graph import build_public_surface_facts

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

    build_public_surface_facts(snap, graph)

    assert any(node.label == "f" and node.kind == "declaration" for node in graph.nodes)
    # Still the same shared instance -- populating it explicitly doesn't
    # fork it away from AbiSnapshot.build_source.source_graph.
    assert snap.surface_graph is snap.build_source.source_graph


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

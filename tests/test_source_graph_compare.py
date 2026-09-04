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

"""Tests for ADR-061 Phase 5 item 2's comparison half: diff_source_graph()
and localize_symbol(), split out of test_source_graph.py along with the
production module split (source_graph_compare.py)."""

from __future__ import annotations

from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    Target,
    TargetKind,
)
from abicheck.buildsource.model import LayerConfidence
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph_build import build_source_graph
from abicheck.buildsource.source_graph_compare import diff_source_graph, localize_symbol


def _sample_build() -> BuildEvidence:
    b = BuildEvidence(generated_files=["gen/config.h"])
    b.targets.append(
        Target(
            id="target://libfoo",
            name="foo",
            kind=TargetKind.SHARED_LIBRARY,
            source_files=["src/foo.cpp", "gen/config.h"],
            public_headers=["include/foo.h"],
            dependencies=["target://libbar", "sys://pthread"],
            confidence=Confidence.HIGH,
        )
    )
    b.targets.append(Target(id="target://libbar", name="bar"))
    b.compile_units.append(
        CompileUnit(
            id="cu://foo",
            source="src/foo.cpp",
            output="foo.o",
            target_id="target://libfoo",
            abi_relevant_flags=["-fvisibility=hidden", "-std=c++20"],
        )
    )
    return b


def _entity(
    qn: str,
    kind: str,
    *,
    mangled: str = "",
    path: str = "include/foo.h",
    origin: str = "PUBLIC_HEADER",
    conf: LayerConfidence = LayerConfidence.HIGH,
) -> SourceEntity:
    return SourceEntity(
        id=qn,
        kind=kind,
        qualified_name=qn,
        mangled_name=mangled,
        source_location=SourceLocation(path=path, line=1, origin=origin),
        visibility="public_header",
        confidence=conf,
    )


def _sample_surface() -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    s.reachable_declarations.append(
        _entity("foo::bar", "function", mangled="_ZN3foo3barEv")
    )
    s.reachable_types.append(_entity("foo::Widget", "record"))
    s.reachable_types.append(_entity("foo::Color", "enum"))
    s.reachable_types.append(_entity("foo::Alias", "typedef"))
    s.reachable_macros.append(
        _entity("FOO_VERSION", "macro", conf=LayerConfidence.REDUCED)
    )
    # Keyed by entity identity (the mangled name for C++), exactly as
    # link_source_abi/relink_surface_exports persist it — not by qualified_name.
    s.mappings["source_decl_to_binary_symbol"] = {"_ZN3foo3barEv": "_ZN3foo3barEv"}
    s.mappings["source_type_to_debug_type"] = {"foo::Widget": "struct foo::Widget"}
    return s


def test_localize_symbol_walks_the_graph() -> None:
    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["include/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    g = build_source_graph(b, source_abi=_sample_surface())
    result = localize_symbol(g, "_ZN3foo3barEv")
    assert result["found"] is True
    assert "target://libfoo" in result["exported_by_targets"]
    assert "foo::bar" in result["source_declarations"]
    assert any("foo.h" in h for h in result["declared_in_headers"])


def test_localize_symbol_absent_returns_empty() -> None:
    result = localize_symbol(build_source_graph(BuildEvidence()), "_Zmissing")
    assert result["found"] is False
    assert result["exported_by_targets"] == []


def test_diff_detects_added_and_removed() -> None:
    old = build_source_graph(_sample_build())
    b2 = _sample_build()
    b2.targets.append(Target(id="target://libbaz", name="baz"))
    new = build_source_graph(b2)
    delta = diff_source_graph(old, new)
    assert delta.changed
    assert any(n.id == "target://libbaz" for n in delta.added_nodes)
    assert not delta.removed_nodes


def test_diff_identical_graphs_no_change() -> None:
    g = build_source_graph(_sample_build())
    delta = diff_source_graph(g, g)
    assert not delta.changed
    assert delta.to_dict()["counts"]["added_nodes"] == 0

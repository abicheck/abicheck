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

"""Inline-collection changed-path threading (ADR-035 D7 POI focusing, G19.3).

Verifies that an explicit changed-path set threaded by the `scan` orchestrator
keeps the inline L4 replay at `changed` scope (narrow, POI-focused) instead of
falling back to the full `target` replay — and that an empty set still falls back
(the inline-dump default). No compiler needed: the extractor and the replay
driver are stubbed so only the scope-selection decision is exercised.
"""

from __future__ import annotations

from pathlib import Path

import abicheck.buildsource.inline as inline
from abicheck.buildsource import source_replay
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_abi import SourceAbiSurface


class _FakeExtractor:
    def available(self) -> bool:
        return True


def _build_with_one_unit() -> BuildEvidence:
    return BuildEvidence(
        compile_units=[CompileUnit(id="cu://src/foo.cpp", source="src/foo.cpp")]
    )


def _capture_scope(monkeypatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _fake_replay(build, extractor, *, scope="target", changed_paths=(), **kw):
        captured["scope"] = scope
        captured["changed_paths"] = tuple(changed_paths)
        return SourceAbiSurface(), []

    monkeypatch.setattr(
        inline, "_make_source_extractor", lambda *a, **k: (_FakeExtractor(), "fake")
    )
    monkeypatch.setattr(source_replay, "run_source_replay", _fake_replay)
    return captured


def test_changed_paths_keep_changed_scope(monkeypatch, tmp_path: Path):
    captured = _capture_scope(monkeypatch)
    inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="changed",
        clang_bin="clang",
        changed_paths=("src/foo.cpp",),
    )
    # An explicit changed set is honoured: the replay stays narrow (D7 focusing).
    assert captured["scope"] == "changed"
    assert captured["changed_paths"] == ("src/foo.cpp",)


def test_changed_scope_in_collect_pack_falls_back_without_paths(monkeypatch, tmp_path):
    captured = _capture_scope(monkeypatch)
    inline.collect_inline_pack(
        sources=tmp_path,
        build_info=None,
        base_build=_build_with_one_unit(),
        scope="changed",
        layers=("L3", "L4"),
        changed_paths=(),
    )
    # No changed set → fall back to the non-empty target replay (inline default).
    assert captured["scope"] == "target"


def test_changed_scope_in_collect_pack_narrows_with_paths(monkeypatch, tmp_path):
    captured = _capture_scope(monkeypatch)
    inline.collect_inline_pack(
        sources=tmp_path,
        build_info=None,
        base_build=_build_with_one_unit(),
        scope="changed",
        layers=("L3", "L4"),
        changed_paths=("src/foo.cpp",),
    )
    # An explicit changed set narrows the inline replay to the affected TUs.
    assert captured["scope"] == "changed"
    assert captured["changed_paths"] == ("src/foo.cpp",)

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

"""`--build-info` format sniffing (ADR-037 D5 #5).

`--build-info` accepts a compile_commands.json, a Bazel `--output=jsonproto`
aquery/cquery, or a `collect` pack. These guard that the content sniffer
classifies each correctly and that a pre-captured Bazel query is routed to the
Bazel adapter (producing BuildEvidence) instead of being mis-parsed as a compile
database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.inline import (
    _maybe_collect_bazel_build_info,
    sniff_build_info_format,
)

# A minimal aquery action graph (one CppCompile) — the deduplicated fragment tree.
_AQUERY = json.dumps(
    {
        "artifacts": [
            {"id": "1", "pathFragmentId": "10"},
            {"id": "2", "pathFragmentId": "11"},
        ],
        "actions": [
            {
                "targetId": "100",
                "mnemonic": "CppCompile",
                "arguments": ["/usr/bin/gcc", "-std=c++17", "-c", "foo/foo.cc"],
                "primaryOutputId": "2",
            }
        ],
        "targets": [{"id": "100", "label": "//foo:foo"}],
        "pathFragments": [
            {"id": "10", "label": "foo.cc", "parentId": "20"},
            {"id": "11", "label": "foo.o", "parentId": "20"},
            {"id": "20", "label": "foo"},
        ],
    }
)
_CQUERY = json.dumps({"results": [{"target": {"rule": {"name": "//foo:foo"}}}]})
_COMPILE_DB = json.dumps(
    [{"directory": "/w", "file": "/w/a.c", "command": "cc -c a.c"}]
)


def _w(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "name,text,expected",
    [
        ("compile_commands.json", _COMPILE_DB, "compile_db"),
        ("aquery.json", _AQUERY, "bazel_aquery"),
        ("cquery.json", _CQUERY, "bazel_cquery"),
        ("weird.json", '{"hello": 1}', "unknown"),
        ("empty.json", "", "unknown"),
    ],
)
def test_sniff_classifies_files(tmp_path: Path, name, text, expected) -> None:
    assert sniff_build_info_format(_w(tmp_path, name, text)) == expected


def test_sniff_pack_vs_build_dir(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    assert sniff_build_info_format(build_dir) == "build_dir"
    pack = tmp_path / "pack"
    pack.mkdir()
    # A real pack carries the BuildSourcePack version marker; a bare {} manifest
    # is a stray file and sniffs as a build_dir, not a pack.
    (pack / "manifest.json").write_text(
        '{"build_source_pack_version": 1}', encoding="utf-8"
    )
    assert sniff_build_info_format(pack) == "pack"


def test_bazel_aquery_routed_to_adapter(tmp_path: Path) -> None:
    """A pre-captured aquery passed as --build-info is normalized by the Bazel
    adapter (compile units), not mis-read as a compile_commands.json."""
    merged = BuildEvidence()
    extractors: list = []
    routed = _maybe_collect_bazel_build_info(
        _w(tmp_path, "aquery.json", _AQUERY), merged, extractors
    )
    assert routed is True
    assert merged.compile_units  # the CppCompile action became a compile unit
    assert any(e.name == "bazel" for e in extractors)


def test_bazel_cquery_routed_to_adapter(tmp_path: Path) -> None:
    merged = BuildEvidence()
    extractors: list = []
    assert _maybe_collect_bazel_build_info(
        _w(tmp_path, "cquery.json", _CQUERY), merged, extractors
    ) is True
    assert any(e.name == "bazel" for e in extractors)


def test_compile_db_not_routed_to_bazel(tmp_path: Path) -> None:
    """A compile_commands.json must NOT be claimed by the Bazel router — it falls
    through to compile-DB resolution."""
    merged = BuildEvidence()
    assert _maybe_collect_bazel_build_info(
        _w(tmp_path, "compile_commands.json", _COMPILE_DB), merged, []
    ) is False
    assert not merged.compile_units


def test_collect_inline_pack_routes_bazel_build_info(tmp_path: Path) -> None:
    """End-to-end: collect_inline_pack with a Bazel aquery --build-info produces a
    pack from the adapter and never emits the 'no compile_commands.json' miss."""
    from abicheck.buildsource.inline import collect_inline_pack

    pack = collect_inline_pack(
        sources=None,
        build_info=_w(tmp_path, "aquery.json", _AQUERY),
        layers=("L3",),
    )
    assert pack is not None
    assert pack.build_evidence is not None
    assert pack.build_evidence.compile_units
    # The Bazel route was taken — no compile-DB miss diagnostic.
    assert not any(
        "no compile_commands.json" in d for d in pack.build_evidence.diagnostics
    )


def test_sniff_large_aquery_preamble_still_detected(tmp_path: Path) -> None:
    """A Bazel aquery whose `actions` key sits past the bounded sniff window (huge
    `artifacts`/`pathFragments` preamble) must still classify as bazel_aquery —
    the object is fully parsed, not prefix-scanned (Codex review)."""
    big_artifacts = [{"id": str(i), "pathFragmentId": str(i)} for i in range(20000)]
    payload = {"artifacts": big_artifacts, "actions": [{"mnemonic": "CppCompile"}]}
    p = _w(tmp_path, "big_aquery.json", json.dumps(payload))
    assert p.stat().st_size > 70000  # exceeds the 64 KiB sniff head
    assert sniff_build_info_format(p) == "bazel_aquery"


def test_sniff_object_wrapped_compile_db(tmp_path: Path) -> None:
    # An object that carries compile-DB keys (not actions/results) → compile_db.
    p = _w(tmp_path, "wrapped.json", json.dumps({"file": "a.c", "command": "cc"}))
    assert sniff_build_info_format(p) == "compile_db"


def test_sniff_unreadable_or_missing_path_is_unknown(tmp_path: Path) -> None:
    # A path that can't be opened (here: does not exist) → "unknown", never a crash
    # (the OSError guard around the bounded-head read).
    assert sniff_build_info_format(tmp_path / "nope.json") == "unknown"


@pytest.mark.parametrize(
    "text,expected",
    [
        ('"just a json string"', "unknown"),  # neither array nor object head
        ("garbage not json", "unknown"),  # not JSON at all
        ('{"actions": [{"mnemonic":', "bazel_aquery"),  # truncated → prefix fallback
        ('{"results": [{"target":', "bazel_cquery"),  # truncated → prefix fallback
        ('{"foo": [1, 2, 3', "unknown"),  # truncated, no discriminating key
    ],
)
def test_sniff_non_object_and_truncated_objects(
    tmp_path: Path, text, expected
) -> None:
    # Heads that aren't a parseable object exercise the non-`{` branch and the
    # truncated-JSON prefix fallback (json.load fails → scan the bounded head).
    assert sniff_build_info_format(_w(tmp_path, "x.json", text)) == expected


def test_maybe_collect_bazel_handles_none_and_nonfile(tmp_path: Path) -> None:
    from abicheck.buildsource.inline import _maybe_collect_bazel_build_info

    merged = BuildEvidence()
    assert _maybe_collect_bazel_build_info(None, merged, []) is False
    # A directory is not a file → not routed here (pack/build_dir handled upstream).
    assert _maybe_collect_bazel_build_info(tmp_path, merged, []) is False

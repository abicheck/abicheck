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

"""Unit tests for abicheck.dependency_info (ADR-055 D1's --follow-deps parity).

The implementation moved here from ``cli_resolve`` when the typed request
gained ``follow_dependencies``, so these cover it directly rather than only
through a CLI path that needs a real ELF and a real dependency graph.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, InputSpec
from abicheck.dependency_info import (
    _dependency_source,
    populate_dependency_info,
    populate_pair_dependency_info,
)
from abicheck.model import AbiSnapshot


class _FakeGraph:
    """Minimal stand-in for resolver.resolve_dependencies' return value."""

    def __init__(self):
        self.nodes = {
            "libb": types.SimpleNamespace(
                path=Path("/usr/lib/libb.so.1"),
                soname="libb.so.1",
                needed=["libc.so.6"],
                depth=1,
                resolution_reason="needed",
            ),
            "liba": types.SimpleNamespace(
                path=Path("/usr/lib/liba.so.1"),
                soname="liba.so.1",
                needed=[],
                depth=0,
                resolution_reason="root",
            ),
        }
        self.edges = [("liba.so.1", "libb.so.1")]
        self.unresolved = [("liba.so.1", "libmissing.so.2")]


def _stub_resolution(monkeypatch, *, missing_symbol: bool = True):
    from abicheck import binder, resolver

    monkeypatch.setattr(resolver, "resolve_dependencies", lambda *a, **k: _FakeGraph())
    bindings = [
        types.SimpleNamespace(
            consumer="liba.so.1",
            symbol="ok",
            version=None,
            status=binder.BindingStatus.RESOLVED_OK,
        )
    ]
    if missing_symbol:
        bindings.append(
            types.SimpleNamespace(
                consumer="liba.so.1",
                symbol="gone",
                version="V1",
                status=binder.BindingStatus.MISSING,
            )
        )
    monkeypatch.setattr(binder, "compute_bindings", lambda graph: bindings)


class TestPopulateDependencyInfo:
    def test_builds_dependency_info_on_the_snapshot(self, monkeypatch):
        _stub_resolution(monkeypatch)
        snap = AbiSnapshot(library="liba.so.1", version="1.0")
        populate_dependency_info(snap, Path("/usr/lib/liba.so.1"), [], None, "")

        info = snap.dependency_info
        assert info is not None
        # Nodes are sorted by (depth, soname) so the record is stable across runs.
        assert [n["soname"] for n in info.nodes] == ["liba.so.1", "libb.so.1"]
        assert info.nodes[0]["depth"] == 0
        assert info.edges == [{"consumer": "liba.so.1", "provider": "libb.so.1"}]
        assert info.unresolved == [
            {"consumer": "liba.so.1", "soname": "libmissing.so.2"}
        ]

    def test_missing_symbols_and_binding_summary(self, monkeypatch):
        _stub_resolution(monkeypatch)
        snap = AbiSnapshot(library="liba.so.1", version="1.0")
        populate_dependency_info(snap, Path("/usr/lib/liba.so.1"), [], None, "")

        info = snap.dependency_info
        assert info.missing_symbols == [
            {"consumer": "liba.so.1", "symbol": "gone", "version": "V1"}
        ]
        assert sum(info.bindings_summary.values()) == 2

    def test_no_missing_symbols_yields_an_empty_list(self, monkeypatch):
        _stub_resolution(monkeypatch, missing_symbol=False)
        snap = AbiSnapshot(library="liba.so.1", version="1.0")
        populate_dependency_info(snap, Path("/usr/lib/liba.so.1"), [], None, "")
        assert snap.dependency_info.missing_symbols == []

    def test_search_paths_and_ld_library_path_reach_the_resolver(self, monkeypatch):
        from abicheck import binder, resolver

        captured: dict[str, object] = {}

        def _spy(so_path, *, search_paths=None, sysroot=None, ld_library_path=""):
            captured.update(
                {
                    "search_paths": search_paths,
                    "sysroot": sysroot,
                    "ld_library_path": ld_library_path,
                }
            )
            return _FakeGraph()

        monkeypatch.setattr(resolver, "resolve_dependencies", _spy)
        monkeypatch.setattr(binder, "compute_bindings", lambda graph: [])
        populate_dependency_info(
            AbiSnapshot(library="l", version="1"),
            Path("/usr/lib/liba.so.1"),
            [Path("/opt/lib")],
            None,
            "/usr/local/lib",
        )
        assert captured["search_paths"] == [Path("/opt/lib")]
        assert captured["ld_library_path"] == "/usr/local/lib"

    def test_empty_search_paths_pass_none_not_an_empty_list(self, monkeypatch):
        # resolve_dependencies treats None as "use the default search order";
        # an empty list would be a different, wrong instruction.
        from abicheck import binder, resolver

        captured: dict[str, object] = {}

        def _spy(so_path, *, search_paths=None, **kwargs):
            captured["search_paths"] = search_paths
            return _FakeGraph()

        monkeypatch.setattr(resolver, "resolve_dependencies", _spy)
        monkeypatch.setattr(binder, "compute_bindings", lambda graph: [])
        populate_dependency_info(
            AbiSnapshot(library="l", version="1"), Path("/l.so"), [], None, ""
        )
        assert captured["search_paths"] is None


class TestDependencySource:
    """Which file the dependency graph is actually read from (Codex review)."""

    def test_plain_elf_uses_its_own_path(self):
        spec = InputSpec(path=Path("/usr/lib/liba.so.1"))
        assert _dependency_source(spec, "elf") == Path("/usr/lib/liba.so.1")

    def test_linker_script_resolves_to_its_elf_target(self, tmp_path):
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 100)
        script = tmp_path / "libfoo.so"
        script.write_text(f"/* GNU ld script */\nINPUT({target})\n", encoding="utf-8")

        # This is the case the format check alone got wrong: a text linker
        # script detects as no known format, so `fmt` is None even though
        # resolve_input followed it and produced an ELF snapshot.
        from abicheck.binary_utils import detect_binary_format

        assert detect_binary_format(script) is None
        assert _dependency_source(InputSpec(path=script), None) == target

    def test_linker_script_is_not_followed_when_the_side_opted_out(self, tmp_path):
        # The MCP server pins follow_linker_scripts=False to keep its size
        # guard authoritative; that must hold here too, not just in resolving.
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 100)
        script = tmp_path / "libfoo.so"
        script.write_text(f"INPUT({target})\n", encoding="utf-8")
        spec = InputSpec(path=script, follow_linker_scripts=False)
        assert _dependency_source(spec, None) is None

    def test_plain_non_elf_input_is_skipped(self, tmp_path):
        snapshot = tmp_path / "old.json"
        snapshot.write_text('{"library": "l"}', encoding="utf-8")
        assert _dependency_source(InputSpec(path=snapshot), None) is None

    def test_script_pointing_at_a_non_elf_target_is_skipped(self, tmp_path):
        target = tmp_path / "notelf.so.1"
        target.write_text("not a binary\n", encoding="utf-8")
        script = tmp_path / "libfoo.so"
        script.write_text(f"INPUT({target})\n", encoding="utf-8")
        assert _dependency_source(InputSpec(path=script), None) is None


class TestPopulatePairDependencyInfo:
    def _request(self, old_path, new_path, **kwargs):
        return CompareRequest(
            old=InputSpec(path=old_path), new=InputSpec(path=new_path), **kwargs
        )

    def test_opt_out_is_a_no_op(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            "abicheck.dependency_info.populate_dependency_info",
            lambda *a: calls.append(a),
        )
        populate_pair_dependency_info(
            self._request(Path("/a.so"), Path("/b.so")),
            AbiSnapshot(library="a", version="1"),
            AbiSnapshot(library="b", version="2"),
            old_fmt="elf",
            new_fmt="elf",
        )
        assert calls == []

    def test_a_linker_script_side_is_no_longer_skipped(self, tmp_path, monkeypatch):
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 100)
        script = tmp_path / "libfoo.so"
        script.write_text(f"INPUT({target})\n", encoding="utf-8")
        elf = tmp_path / "new.so"
        elf.write_bytes(b"\x7fELF" + b"\x00" * 100)

        seen: list[Path] = []
        monkeypatch.setattr(
            "abicheck.dependency_info.populate_dependency_info",
            lambda snap, path, *a: seen.append(path),
        )
        populate_pair_dependency_info(
            self._request(script, elf, follow_dependencies=True),
            AbiSnapshot(library="a", version="1"),
            AbiSnapshot(library="b", version="2"),
            old_fmt=None,  # what detect_binary_format answers for the script
            new_fmt="elf",
        )
        assert seen == [target, elf]


@pytest.mark.parametrize("fmt", ["pe", "macho", None])
def test_non_elf_formats_never_resolve_dependencies(fmt, tmp_path):
    # resolve_dependencies reads ELF DT_NEEDED entries — there is nothing for
    # it to do on a PE or Mach-O side.
    other = tmp_path / "lib.bin"
    other.write_bytes(b"MZ" + b"\x00" * 100)
    assert _dependency_source(InputSpec(path=other), fmt) is None

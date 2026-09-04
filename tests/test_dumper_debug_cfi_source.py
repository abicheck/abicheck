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

"""P1 review finding, split out of ``test_dumper_unit.py`` (which is at the
AI-readiness 2000-line hard cap -- ``_extra``-style sibling, matching e.g.
``test_btf_metadata_evidence.py``/``test_ctf_metadata_evidence.py``).

Finding: when ``_resolve_debug_metadata`` resolves a detached-debug sidecar
(``--debug-root``/``--debuginfod``, an ``objcopy --only-keep-debug`` file),
it previously called ``dwarf_unified.parse_dwarf`` with only the sidecar
path -- but a sidecar's own ``.eh_frame``/``.debug_frame`` are typically
``SHT_NOBITS`` (objcopy strips their content, keeping only the section
headers), so CFI extraction from the sidecar alone fails or finds no FDEs,
stamping the advanced channel ``partial`` even though the real unwind data
sits in the primary (stripped) binary right alongside it. Fixed by
forwarding the primary binary (``so_path``) to ``parse_dwarf`` as
``cfi_source_path`` whenever a detached debug artifact (``dwarf_source``)
was resolved -- see ``dwarf_unified.DwarfSession.cfi_elf``'s own docstring
for the full mechanism, and ``tests/test_dwarf_unified.py::
TestDetachedDebugCfiSource`` for the end-to-end (real ``objcopy``-produced
files) regression coverage of that mechanism itself. This file covers only
the one-line wiring decision in ``dumper_debug.py`` -- which path gets
passed as ``cfi_source_path``.
"""

from __future__ import annotations

from abicheck.dumper_debug import _resolve_debug_metadata


class TestResolveDebugMetadataForwardsCfiSource:
    def test_detached_debug_forwards_primary_as_cfi_source(self, tmp_path, monkeypatch):
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        expected = (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata())
        calls: list[dict] = []

        def _fake_parse_dwarf(path, **kwargs):
            calls.append({"path": path, **kwargs})
            return expected

        monkeypatch.setattr("abicheck.dwarf_unified.parse_dwarf", _fake_parse_dwarf)

        so_path = tmp_path / "lib.so"
        sidecar = tmp_path / "lib.debug"
        result = _resolve_debug_metadata(so_path, "dwarf", dwarf_source=sidecar)

        assert result is expected
        assert len(calls) == 1
        assert calls[0]["path"] == sidecar
        assert calls[0]["cfi_source_path"] == so_path

    def test_no_detached_debug_passes_no_cfi_source(self, tmp_path, monkeypatch):
        """Positive control: the ordinary (non-split-debug) case passes
        cfi_source_path=None -- unaffected by this fix."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        expected = (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata())
        calls: list[dict] = []

        def _fake_parse_dwarf(path, **kwargs):
            calls.append({"path": path, **kwargs})
            return expected

        monkeypatch.setattr("abicheck.dwarf_unified.parse_dwarf", _fake_parse_dwarf)

        so_path = tmp_path / "lib.so"
        result = _resolve_debug_metadata(so_path, "dwarf")

        assert result is expected
        assert calls[0]["path"] == so_path
        assert calls[0]["cfi_source_path"] is None

    def test_auto_detect_path_also_forwards_cfi_source(self, tmp_path, monkeypatch):
        """The auto-detect (debug_format=None) DWARF>BTF>CTF branch has its
        own separate parse_dwarf call site -- must forward the same way."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        expected = (DwarfMetadata(has_dwarf=True), AdvancedDwarfMetadata())
        calls: list[dict] = []

        def _fake_parse_dwarf(path, **kwargs):
            calls.append({"path": path, **kwargs})
            return expected

        monkeypatch.setattr("abicheck.dumper_debug._is_kernel_binary", lambda _p: False)
        monkeypatch.setattr("abicheck.dwarf_unified.parse_dwarf", _fake_parse_dwarf)

        so_path = tmp_path / "lib.so"
        sidecar = tmp_path / "lib.debug"
        result = _resolve_debug_metadata(so_path, None, dwarf_source=sidecar)

        # Unlike the forced-"dwarf" branch, the auto-detect branch unpacks
        # and re-returns the pair rather than the exact tuple object -- the
        # auto-detect flow's own separate parse_dwarf call site is what's
        # under test here, not object identity.
        assert result == expected
        assert len(calls) == 1
        assert calls[0]["path"] == sidecar
        assert calls[0]["cfi_source_path"] == so_path

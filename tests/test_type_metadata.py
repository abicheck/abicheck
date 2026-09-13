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

"""Tests for TypeMetadataSource protocol and resolution logic."""

from __future__ import annotations

from abicheck.btf_metadata import BtfMetadata
from abicheck.ctf_metadata import CtfMetadata
from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo, StructLayout
from abicheck.type_metadata import TypeMetadataSource, resolve_debug_metadata


class TestTypeMetadataSourceProtocol:
    """All three metadata classes implement TypeMetadataSource."""

    def test_dwarf_is_source(self) -> None:
        meta = DwarfMetadata(has_dwarf=True)
        assert isinstance(meta, TypeMetadataSource)
        assert meta.has_data is True

    def test_btf_is_source(self) -> None:
        meta = BtfMetadata(has_btf=True)
        assert isinstance(meta, TypeMetadataSource)
        assert meta.has_data is True

    def test_ctf_is_source(self) -> None:
        meta = CtfMetadata(has_ctf=True)
        assert isinstance(meta, TypeMetadataSource)
        assert meta.has_data is True

    def test_empty_sources(self) -> None:
        assert DwarfMetadata().has_data is False
        assert BtfMetadata().has_data is False
        assert CtfMetadata().has_data is False

    def test_dwarf_get_methods(self) -> None:
        meta = DwarfMetadata(
            has_dwarf=True,
            structs={"foo": StructLayout(name="foo", byte_size=8)},
            enums={
                "bar": EnumInfo(name="bar", underlying_byte_size=4, members={"X": 1})
            },
        )
        assert meta.get_struct_layout("foo") is not None
        assert meta.get_struct_layout("missing") is None
        assert meta.get_enum_info("bar") is not None
        assert meta.get_enum_info("missing") is None


class TestResolveDebugMetadata:
    def _make_dwarf(self) -> DwarfMetadata:
        return DwarfMetadata(has_dwarf=True)

    def _make_btf(self) -> BtfMetadata:
        return BtfMetadata(has_btf=True)

    def _make_ctf(self) -> CtfMetadata:
        return CtfMetadata(has_ctf=True)

    def test_userspace_prefers_dwarf(self) -> None:
        result = resolve_debug_metadata(
            dwarf=self._make_dwarf(),
            btf=self._make_btf(),
            ctf=self._make_ctf(),
        )
        assert isinstance(result, DwarfMetadata)

    def test_kernel_prefers_btf(self) -> None:
        result = resolve_debug_metadata(
            dwarf=self._make_dwarf(),
            btf=self._make_btf(),
            ctf=self._make_ctf(),
            prefer_btf=True,
        )
        assert isinstance(result, BtfMetadata)

    def test_fallback_to_btf_when_no_dwarf(self) -> None:
        result = resolve_debug_metadata(
            dwarf=DwarfMetadata(),
            btf=self._make_btf(),
            ctf=self._make_ctf(),
        )
        assert isinstance(result, BtfMetadata)

    def test_fallback_to_ctf_when_nothing_else(self) -> None:
        result = resolve_debug_metadata(
            dwarf=DwarfMetadata(),
            btf=BtfMetadata(),
            ctf=self._make_ctf(),
        )
        assert isinstance(result, CtfMetadata)

    def test_none_when_all_empty(self) -> None:
        result = resolve_debug_metadata(
            dwarf=DwarfMetadata(),
            btf=BtfMetadata(),
            ctf=CtfMetadata(),
        )
        assert result is None

    def test_none_when_no_sources(self) -> None:
        result = resolve_debug_metadata()
        assert result is None

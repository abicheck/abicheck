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

"""Tests for :mod:`abicheck.extract.library_export_facts` (ADR-005).

:func:`read_library_export_facts` replaces the old ``_lib_fmt``/
``_lib_*_meta``/``_get_new_lib_exports``/``_get_old_lib_exports_for_scoping``/
``_get_lib_soname`` helpers. Snapshots are real ``AbiSnapshot`` objects; for
raw ``Path`` inputs only the platform metadata parser is stubbed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.extract.library_export_facts import read_library_export_facts
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot
from abicheck.model.availability import FactStatus
from abicheck.model.name_decoration import elf_version
from abicheck.pe_metadata import PeExport, PeMetadata

_ELF = b"\x7fELF" + b"\x00" * 100
_PE = b"MZ" + b"\x00" * 100
_MACHO = b"\xfe\xed\xfa\xcf" + b"\x00" * 100


def _write(tmp_path, name, data):
    f = tmp_path / name
    f.write_bytes(data)
    return f


class TestBinaryPathExports:
    def test_elf_exports(self, tmp_path):
        f = _write(tmp_path, "lib.so", _ELF)
        meta = ElfMetadata(symbols=[ElfSymbol(name="foo"), ElfSymbol(name="bar")])
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            facts = read_library_export_facts(f)
        assert facts.binary_format == "elf"
        assert facts.status is FactStatus.PRESENT
        assert facts.export_names == {"foo", "bar"}
        assert facts.label == str(f)

    def test_elf_unversioned_exports_for_scoping(self, tmp_path):
        f = _write(tmp_path, "libold.so", _ELF)
        meta = ElfMetadata(
            symbols=[ElfSymbol(name="foo@@V_1"), ElfSymbol(name="bar")],
            versions_defined=["V_1"],
        )
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            facts = read_library_export_facts(f)
        assert facts.unversioned_exports == {"foo", "bar"}
        assert facts.versions_defined == {"V_1"}

    def test_non_elf_has_no_elf_only_facts(self, tmp_path):
        f = _write(tmp_path, "lib.dll", _PE)
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=PeMetadata()):
            facts = read_library_export_facts(f)
        assert facts.unversioned_exports is None
        assert facts.versions_defined is None

    def test_normalize_elf_symbol_name(self):
        assert elf_version.unversioned_name("inflate") == "inflate"
        assert elf_version.unversioned_name("inflate@ZLIB_1.2.0") == "inflate"
        assert elf_version.unversioned_name("inflate@@ZLIB_1.2.0") == "inflate"

    def test_pe_exports(self, tmp_path):
        f = _write(tmp_path, "lib.dll", _PE)
        meta = PeMetadata(exports=[PeExport(name="CreateFoo"), PeExport(name="")])
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=meta):
            facts = read_library_export_facts(f)
        assert facts.export_names == {"CreateFoo"}

    def test_macho_exports(self, tmp_path):
        f = _write(tmp_path, "lib.dylib", _MACHO)
        meta = MachoMetadata(
            exports=[MachoExport(name="foo_init"), MachoExport(name="")]
        )
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            facts = read_library_export_facts(f)
        assert facts.export_names == {"foo_init"}

    def test_unknown_format_is_failed_fact(self, tmp_path):
        f = _write(tmp_path, "lib.bin", b"\0" * 4)
        facts = read_library_export_facts(f)
        assert facts.status is FactStatus.FAILED
        assert facts.binary_format is None
        assert facts.export_names == frozenset()
        assert facts.failure_reason


class TestSoname:
    def test_elf_soname(self, tmp_path):
        f = _write(tmp_path, "libfoo.so.1.2.3", _ELF)
        meta = ElfMetadata(soname="libfoo.so.1")
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            assert read_library_export_facts(f).soname == "libfoo.so.1"

    def test_elf_no_soname(self, tmp_path):
        f = _write(tmp_path, "libfoo.so", _ELF)
        meta = ElfMetadata(soname="")
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            assert read_library_export_facts(f).soname == "libfoo.so"

    def test_pe_soname(self, tmp_path):
        f = _write(tmp_path, "foo.dll", _PE)
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=PeMetadata()):
            assert read_library_export_facts(f).soname == "foo.dll"

    def test_macho_install_name(self, tmp_path):
        f = _write(tmp_path, "libfoo.dylib", _MACHO)
        meta = MachoMetadata(install_name="/usr/lib/libfoo.1.dylib")
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            assert read_library_export_facts(f).soname == "/usr/lib/libfoo.1.dylib"

    def test_macho_no_install_name(self, tmp_path):
        f = _write(tmp_path, "libfoo.dylib", _MACHO)
        meta = MachoMetadata(install_name="")
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            assert read_library_export_facts(f).soname == "libfoo.dylib"

    def test_unknown_format(self, tmp_path):
        f = _write(tmp_path, "lib.bin", b"\0" * 4)
        assert read_library_export_facts(f).soname == "lib.bin"


class TestSnapshotInputs:
    """Was ``TestLibFmtAndMetaAccessors``: a snapshot's stored metadata is used
    as is, and no binary is parsed."""

    def test_elf_snapshot(self):
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            elf=ElfMetadata(soname="libfoo.so.1", symbols=[ElfSymbol(name="foo")]),
        )
        facts = read_library_export_facts(snap)
        assert facts.binary_format == "elf"
        assert facts.label == "libfoo.so.1"
        assert facts.soname == "libfoo.so.1"
        assert facts.export_names == {"foo"}

    def test_pe_snapshot_format_and_ordinals(self):
        pe = PeMetadata(
            exports=[PeExport(name="Foo", ordinal=1), PeExport(name="", ordinal=2)]
        )
        facts = read_library_export_facts(
            AbiSnapshot(library="foo.dll", version="1.0", pe=pe)
        )
        assert facts.binary_format == "pe"
        assert dict(facts.exports_by_ordinal) == {1: "Foo", 2: ""}
        assert facts.export_names == {"Foo"}

    def test_macho_snapshot_format_and_exports(self):
        macho = MachoMetadata(exports=[MachoExport(name="_foo")])
        facts = read_library_export_facts(
            AbiSnapshot(library="libfoo.dylib", version="1.0", macho=macho)
        )
        assert facts.binary_format == "macho"
        assert facts.exports_by_ordinal is None
        assert facts.export_names

    @pytest.mark.parametrize("data", [_ELF, _MACHO])
    def test_no_pe_ordinals_for_non_pe_path(self, tmp_path, data):
        f = _write(tmp_path, "lib.so", data)
        with (
            patch(
                "abicheck.elf_metadata.parse_elf_metadata", return_value=ElfMetadata()
            ),
            patch(
                "abicheck.macho_metadata.parse_macho_metadata",
                return_value=MachoMetadata(),
            ),
        ):
            facts = read_library_export_facts(f)
        assert facts.exports_by_ordinal is None

    def test_snapshot_without_platform_metadata_is_failed(self):
        """A headers-only snapshot carries no export table: FAILED, not empty."""
        facts = read_library_export_facts(
            AbiSnapshot(library="libfoo.so.1", version="1.0")
        )
        assert facts.status is FactStatus.FAILED
        assert facts.binary_format is None
        assert facts.failure_reason
        assert facts.export_names == frozenset()
        assert facts.soname == "libfoo.so.1"

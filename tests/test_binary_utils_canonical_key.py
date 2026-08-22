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

"""Direct unit tests for :func:`abicheck.binary_utils._canonical_library_key`'s
Mach-O version-in-filename handling (the ``libfoo.N.dylib`` ld64
compatibility-version convention, the Mach-O counterpart of the pre-existing
ELF ``libfoo.so.N`` handling this function already covered)."""

from __future__ import annotations

from pathlib import Path

from abicheck.binary_utils import _canonical_library_key


class TestCanonicalLibraryKeyMachOVersioning:
    def test_single_component_version_reduces_to_bare_dylib(self) -> None:
        assert _canonical_library_key(Path("libfoo.1.dylib")) == "libfoo.dylib"

    def test_multi_component_version_reduces_to_bare_dylib(self) -> None:
        assert _canonical_library_key(Path("libfoo.1.2.3.dylib")) == "libfoo.dylib"

    def test_two_different_major_versions_share_a_canonical_key(self) -> None:
        assert _canonical_library_key(Path("libfoo.1.dylib")) == _canonical_library_key(
            Path("libfoo.2.dylib")
        )

    def test_unversioned_dylib_is_left_unchanged(self) -> None:
        # No numeric segment before .dylib -- nothing to strip.
        assert _canonical_library_key(Path("libfoo.dylib")) == "libfoo.dylib"

    def test_a_version_looking_number_inside_the_stem_is_not_touched(self) -> None:
        # Only a version segment directly before ".dylib" is stripped --
        # not an arbitrary digit anywhere in the name.
        assert _canonical_library_key(Path("libfoo2.dylib")) == "libfoo2.dylib"

    def test_elf_versioning_is_unaffected(self) -> None:
        # The pre-existing ELF handling takes priority and is unchanged by
        # this addition.
        assert _canonical_library_key(Path("libfoo.so.1.2")) == "libfoo.so"


class TestCanonicalLibraryKeyCaseFolding:
    """Case-folding is restricted to the PE/.dll suffix, the one format
    whose loader identity is genuinely case-insensitive (Windows). ELF
    .so and Mach-O .dylib identity is case-sensitive -- a case-only
    rename breaks a case-sensitive consumer's DT_NEEDED/LC_LOAD_DYLIB, a
    real break the canonical fallback must not silently pair away (Codex
    review, fresh evidence)."""

    def test_dll_case_only_rename_still_shares_a_canonical_key(self) -> None:
        assert _canonical_library_key(Path("Foo.dll")) == _canonical_library_key(
            Path("foo.dll")
        )

    def test_elf_case_only_rename_does_not_share_a_canonical_key(self) -> None:
        assert _canonical_library_key(Path("libFoo.so")) != _canonical_library_key(
            Path("libfoo.so")
        )

    def test_dylib_case_only_rename_does_not_share_a_canonical_key(self) -> None:
        assert _canonical_library_key(Path("libFoo.dylib")) != _canonical_library_key(
            Path("libfoo.dylib")
        )

    def test_elf_stem_case_is_preserved_in_the_key(self) -> None:
        assert _canonical_library_key(Path("libFoo.so.1.2")) == "libFoo.so"

    def test_dylib_stem_case_is_preserved_in_the_key(self) -> None:
        assert _canonical_library_key(Path("libFoo.1.2.dylib")) == "libFoo.dylib"

    def test_pyd_case_only_rename_shares_a_canonical_key(self, tmp_path: Path) -> None:
        # A PE DLL shipped under a nonstandard extension (a Python .pyd
        # extension module) is just as case-insensitive on Windows as a
        # .dll's -- a suffix-only check misses it (Codex review, fresh
        # evidence). Needs real PE content on disk: unlike the .dll cases
        # above, there is no suffix to short-circuit on.
        import struct

        dos_header = bytearray(64)
        dos_header[0:2] = b"MZ"
        struct.pack_into("<I", dos_header, 0x3C, 64)
        coff_header = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x2000)
        pe_bytes = bytes(dos_header) + b"PE\x00\x00" + coff_header

        upper = tmp_path / "Foo.pyd"
        lower = tmp_path / "foo.pyd"
        upper.write_bytes(pe_bytes)
        lower.write_bytes(pe_bytes)

        assert _canonical_library_key(upper) == _canonical_library_key(lower)

    def test_versioned_uppercase_dylib_pairs_with_unversioned_uppercase(
        self,
    ) -> None:
        # libfoo.1.DYLIB -> libfoo.DYLIB is a real version-drop pair. A
        # fixed lowercase replacement on the versioned side would produce
        # "libfoo.dylib" while the untouched unversioned side stays
        # "libfoo.DYLIB" -- two different-cased keys for what should be
        # one canonical identity (Codex review, fresh evidence).
        assert _canonical_library_key(Path("libfoo.1.DYLIB")) == _canonical_library_key(
            Path("libfoo.DYLIB")
        )

    def test_versioned_uppercase_dylib_extension_case_is_preserved(self) -> None:
        assert _canonical_library_key(Path("libfoo.1.DYLIB")) == "libfoo.DYLIB"

    def test_stored_pe_snapshot_case_only_rename_shares_a_canonical_key(
        self,
    ) -> None:
        # A stored snapshot of a PE library (Foo.dll.abicheck.json) carries
        # its DLL identity in the filename, not in the represented binary's
        # own content -- the snapshot's bytes are JSON, so
        # _pe_is_dll_content (which reads the actual file) never recognizes
        # it as a PE image. Without matching the embedded ".dll" segment,
        # compare-release's _build_match_map reported the same release's
        # snapshot published under two case spellings as an unrelated
        # removal+addition (Codex review, fresh evidence).
        assert _canonical_library_key(
            Path("Foo.dll.abicheck.json")
        ) == _canonical_library_key(Path("foo.dll.abicheck.json"))

    def test_stored_pe_snapshot_compressed_case_only_rename_shares_a_key(
        self,
    ) -> None:
        # Same as above, but one side is a compressed snapshot (ADR-059) --
        # the compression-suffix stripping and the embedded ".dll" match
        # must compose correctly.
        assert _canonical_library_key(
            Path("Foo.dll.abicheck.json.gz")
        ) == _canonical_library_key(Path("foo.dll.abicheck.json"))

    def test_stored_dylib_snapshot_version_bump_still_pairs(self) -> None:
        # The Mach-O counterpart of the stored-PE-snapshot case above:
        # _DYLIB_VERSION_RE is anchored to the end of string, so it never
        # matched through a stored snapshot's own wrapper suffix
        # (Codex review, fresh evidence).
        assert _canonical_library_key(
            Path("libfoo.1.dylib.abicheck.json")
        ) == _canonical_library_key(Path("libfoo.2.dylib.abicheck.json"))

    def test_a_dll_segment_that_is_not_the_represented_extension_is_not_folded(
        self,
    ) -> None:
        # A genuinely case-sensitive ELF name that happens to contain the
        # literal substring ".dll." (e.g. a compatibility shim) must not be
        # case-folded just because ".dll" appears somewhere in the name --
        # only a *represented* library whose own (wrapper-suffix-stripped)
        # name genuinely ends in ".dll" is a PE identity (Codex review,
        # fresh evidence: an earlier fix matched any ".dll" segment
        # anywhere in the name, which wrongly case-folded this and hid a
        # real ELF case-only-rename break).
        assert _canonical_library_key(Path("libFoo.dll.so")) != _canonical_library_key(
            Path("libfoo.dll.so")
        )

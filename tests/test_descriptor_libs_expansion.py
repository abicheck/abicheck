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

"""Which entries a descriptor `<libs>` directory expands to.

A sibling of ``test_compat_multi_library.py`` rather than another class in
it: that file is at the architecture gate's 1200-line test-file cap, and the
rule for a file at its cap is to move responsibility out, not to trim it to
fit.
"""

from __future__ import annotations

from pathlib import Path


class TestSymlinkedLibrariesAreKept:
    """An SDK overlay directory of symlinks must not expand to nothing.

    Excluding symlinks was a shortcut for collapsing a SONAME chain
    (`libfoo.so` -> `libfoo.so.1` -> `libfoo.so.1.2`, three names for one
    file). It silently dropped the case an SDK actually ships: an overlay
    directory of links into a store elsewhere. There every selected library
    vanished -- a symlink-only directory raised "contains no shared
    libraries", and a mixed one produced a verdict over part of the selected
    set without saying so (Codex review).

    Bug class: a deduplication implemented by excluding a *kind* of entry
    rather than by identifying the duplicate.
    """

    @staticmethod
    def _elf(path: Path) -> None:
        import struct

        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4] = 2
        header[5] = 1
        header[6] = 1
        struct.pack_into("<H", header, 16, 3)
        struct.pack_into("<H", header, 18, 62)
        struct.pack_into("<I", header, 20, 1)
        struct.pack_into("<H", header, 52, 64)
        struct.pack_into("<H", header, 54, 56)
        path.write_bytes(bytes(header))

    def test_an_overlay_of_symlinks_resolves_to_its_targets(self, tmp_path):
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        store = tmp_path / "store"
        store.mkdir()
        overlay = tmp_path / "overlay"
        overlay.mkdir()
        for name in ("liba.so", "libb.so"):
            self._elf(store / name)
            (overlay / name).symlink_to(store / name)

        found = expand_descriptor_libs([overlay])
        assert sorted(p.name for p in found) == ["liba.so", "libb.so"]

    def test_a_soname_chain_is_still_one_library(self, tmp_path):
        """What the exclusion was really for, which the fix must preserve --
        otherwise one library is compared three times."""
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        d = tmp_path / "lib"
        d.mkdir()
        self._elf(d / "libfoo.so.1.2")
        (d / "libfoo.so.1").symlink_to("libfoo.so.1.2")
        (d / "libfoo.so").symlink_to("libfoo.so.1")

        found = expand_descriptor_libs([d])
        assert [p.name for p in found] == ["libfoo.so"]

    def test_a_mixed_directory_keeps_both_kinds(self, tmp_path):
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        store = tmp_path / "store"
        store.mkdir()
        d = tmp_path / "lib"
        d.mkdir()
        self._elf(d / "libreal.so")
        self._elf(store / "liblinked.so")
        (d / "liblinked.so").symlink_to(store / "liblinked.so")

        found = expand_descriptor_libs([d])
        assert sorted(p.name for p in found) == ["liblinked.so", "libreal.so"]

    def test_a_broken_symlink_is_skipped_not_fatal(self, tmp_path):
        from abicheck.compat.descriptor_expansion import expand_descriptor_libs

        d = tmp_path / "lib"
        d.mkdir()
        self._elf(d / "libreal.so")
        (d / "libgone.so").symlink_to(tmp_path / "nowhere.so")

        assert [p.name for p in expand_descriptor_libs([d])] == ["libreal.so"]

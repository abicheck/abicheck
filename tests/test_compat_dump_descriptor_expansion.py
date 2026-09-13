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

"""`compat dump` must read every descriptor `compat check` reads.

`compat check`'s loading path expands a `<headers>`/`<libs>` *directory*
operand at the boundary (`run_inputs._load_descriptor_or_dump`), which is
ABICC's ordinary usage. `compat_dump_cmd` called `parse_descriptor` alone,
so the same descriptor failed there -- the library directory reached the
binary parser as "Unrecognised binary format" and the header directory
reached the header parser as `#include "<dir>"` (Codex review).

Bug class: one documented capability wired into one of two public consumers.
The tests below are written against the *command*, not the helper, since
the helper was already correct and already tested -- what was missing was
the call.
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from abicheck.compat.cli import compat_group


def _et_dyn_elf_header() -> bytes:
    """A minimal, genuinely well-formed 64-bit ``ET_DYN`` ELF header.

    Enough for `package.discover_shared_libraries`, which is what
    `expand_descriptor_libs` delegates to, and which reads `e_type` rather
    than trusting the magic alone -- a bare `b"\x7fELF"` buffer is rejected
    (confirmed: the first version of this fixture was, which is the
    discovery check doing its job). The dump itself is stubbed, so no
    toolchain is needed and this module stays in the default fast lane.
    """
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    struct.pack_into("<H", header, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<H", header, 18, 62)  # e_machine = EM_X86_64
    struct.pack_into("<I", header, 20, 1)  # e_version
    struct.pack_into("<H", header, 52, 64)  # e_ehsize
    struct.pack_into("<H", header, 54, 56)  # e_phentsize
    return bytes(header)


def _descriptor(tmp_path: Path, headers: str, libs: str) -> Path:
    desc = tmp_path / "d.xml"
    desc.write_text(
        f"<version>1.0</version>\n<headers>\n  {headers}\n</headers>\n"
        f"<libs>\n  {libs}\n</libs>\n"
    )
    return desc


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "a.h").write_text("int keep_me(int x);\n")
    (tmp_path / "include" / "b.h").write_text("int also_me(int x);\n")
    libs = tmp_path / "libs"
    libs.mkdir()
    (libs / "libfoo.so").write_bytes(_et_dyn_elf_header())
    return tmp_path


class TestDirectoryOperandsReachTheDumper:
    def _run(self, tree: Path, captured: dict):
        def _fake_dump(path, **kwargs):
            captured["path"] = path
            captured["headers"] = list(kwargs.get("headers") or [])
            from abicheck.model import AbiSnapshot

            return AbiSnapshot(library="libfoo.so", version="1.0")

        desc = _descriptor(tree, "include", "libs")
        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            return CliRunner().invoke(
                compat_group,
                [
                    "dump",
                    "-lib",
                    "foo",
                    "-dump",
                    str(desc),
                    "-dump-path",
                    str(tree / "out.json"),
                ],
            )

    def test_a_libs_directory_resolves_to_the_library_inside_it(self, tree):
        captured: dict = {}
        result = self._run(tree, captured)
        assert result.exit_code == 0, result.output
        # Not the directory, which is what reached the binary parser before.
        assert captured["path"].name == "libfoo.so"
        assert captured["path"].is_file()

    def test_a_headers_directory_resolves_to_the_headers_inside_it(self, tree):
        captured: dict = {}
        result = self._run(tree, captured)
        assert result.exit_code == 0, result.output
        assert sorted(h.name for h in captured["headers"]) == ["a.h", "b.h"]
        assert all(h.is_file() for h in captured["headers"])

    def test_a_plain_file_descriptor_is_unchanged(self, tree):
        """The negative control: expansion must be a no-op for the form that
        already worked, or this fix trades one broken descriptor for another."""
        captured: dict = {}

        def _fake_dump(path, **kwargs):
            captured["path"] = path
            captured["headers"] = list(kwargs.get("headers") or [])
            from abicheck.model import AbiSnapshot

            return AbiSnapshot(library="libfoo.so", version="1.0")

        desc = _descriptor(
            tree, str(tree / "include" / "a.h"), str(tree / "libs" / "libfoo.so")
        )
        with mock.patch("abicheck.compat.cli.dump", side_effect=_fake_dump):
            result = CliRunner().invoke(
                compat_group,
                [
                    "dump",
                    "-lib",
                    "foo",
                    "-dump",
                    str(desc),
                    "-dump-path",
                    str(tree / "out.json"),
                ],
            )
        assert result.exit_code == 0, result.output
        assert captured["path"] == tree / "libs" / "libfoo.so"
        assert [h.name for h in captured["headers"]] == ["a.h"]

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

"""GNU symbol-version correlation, over a real versioned ELF.

``extract/elf_symbol_versions.py`` is the second full walk of a symbol
table a parse performs: it attaches each entry's ``.gnu.version`` label and
default/hidden binding. It was *moved* out of ``elf_metadata.py`` (to keep
that module under its no-growth baseline), and a move is exactly when a
module can quietly stop being exercised -- so this drives it through the
real ``parse_elf_metadata`` on a shared object built with a real linker
version script, rather than trusting that whatever covered it before the
move still does.

The oracle is the version script itself: it *states* which symbol belongs
to which version node, so the expected mapping is written independently of
anything the parser computes.

Skips when the host cannot produce a versioned ELF -- no compiler at all,
or a compiler that emits Mach-O/PE rather than ELF. It does **not** skip
when a configured compiler ran and *failed*: that is a broken fixture or
toolchain, and `_require_compile_success` turns it into a failure with the
command, source and stderr attached (bug class
`guard.absent_capability_vs_real_failure`, contract in
`test_compile_failure_contract.py`). A helper that skipped on any nonzero
exit would produce a green lane proving less than it claims, and the
`ABICHECK_MIN_EXECUTED` floor could not see it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from abicheck.elf_metadata import parse_elf_metadata
from tests.test_cross_platform_integration import _require_compile_success

_ELF_MAGIC = b"\x7fELF"

#: The version script's own statement of the mapping -- the oracle.
_EXPECTED_VERSIONS = {
    "alpha": "LIBV_1.0",
    "beta": "LIBV_2.0",
    "gamma_fn": "LIBV_2.0",
}


@pytest.fixture(scope="module")
def versioned_so(tmp_path_factory):
    """A real ELF DSO with two GNU version nodes, or ``None``."""
    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if cc is None:
        return None
    tmp_path = tmp_path_factory.mktemp("versioned")
    (tmp_path / "v.c").write_text(
        "int alpha(int a){return a;}\n"
        "int beta(int a){return a+1;}\n"
        "int gamma_fn(int a){return a+2;}\n"
        "int delta_hidden(int a){return a+3;}\n"
    )
    (tmp_path / "v.map").write_text(
        "LIBV_1.0 { global: alpha; local: *; };\n"
        "LIBV_2.0 { global: beta; gamma_fn; } LIBV_1.0;\n"
    )
    so = tmp_path / "libv.so"
    cmd = [
        cc,
        "-shared",
        "-fPIC",
        f"-Wl,--version-script={tmp_path / 'v.map'}",
        "-o",
        str(so),
        str(tmp_path / "v.c"),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 and not _emits_elf(cc, tmp_path):
        # This host's compiler does not produce ELF at all, so a GNU
        # version script is simply not a capability it has: an absence, not
        # a broken fixture.
        return None
    _require_compile_success(
        "cc", cmd, (tmp_path / "v.c").read_text(), proc, optional_feature=None
    )
    if not so.exists():
        return None
    with open(so, "rb") as fh:
        if fh.read(4) != _ELF_MAGIC:
            return None
    return so


def _emits_elf(cc: str, tmp_path) -> bool:
    """Whether *cc* produces ELF here, decided without a version script.

    Separates "this platform is not ELF" (an absent capability, skip) from
    "the ELF toolchain rejected our build" (a real failure). Probed with a
    plain `-shared` so a version-script problem cannot be misread as a
    platform difference.
    """
    probe_src = tmp_path / "probe.c"
    probe_src.write_text("int probe(void){return 0;}\n")
    probe_so = tmp_path / "libprobe.so"
    cmd = [cc, "-shared", "-fPIC", "-o", str(probe_so), str(probe_src)]
    probe = subprocess.run(cmd, capture_output=True)
    # A configured compiler that ran and rejected a two-line `-shared` build
    # is a broken toolchain, not a non-ELF platform: reporting `False` here
    # would launder it into this module's own skip.
    _require_compile_success(
        "cc", cmd, probe_src.read_text(), probe, optional_feature=None
    )
    if not probe_so.exists():
        pytest.fail(f"{cc} exited 0 but produced no {probe_so.name}")
    with open(probe_so, "rb") as fh:
        return fh.read(4) == _ELF_MAGIC


def _require(versioned_so):
    if versioned_so is None:
        pytest.skip("host cannot build a versioned ELF shared object")
    return versioned_so


class TestVersionCorrelation:
    def test_each_export_carries_the_version_its_script_assigned(
        self, versioned_so
    ) -> None:
        meta = parse_elf_metadata(str(_require(versioned_so)))
        by_name = {s.name: s for s in meta.symbols}
        # Vacuity guard: the parse must have found the exports at all,
        # otherwise an empty mapping would satisfy every assertion below.
        assert set(_EXPECTED_VERSIONS) <= set(by_name), sorted(by_name)
        for name, expected in _EXPECTED_VERSIONS.items():
            assert by_name[name].version == expected, name

    def test_a_local_symbol_is_not_exported_at_all(self, versioned_so) -> None:
        """``local: *`` in the script means ``delta_hidden`` never appears.

        Confirms the fixture really applied the version script, which is
        what makes the version assertions above meaningful rather than
        coincidental.
        """
        meta = parse_elf_metadata(str(_require(versioned_so)))
        assert "delta_hidden" not in {s.name for s in meta.symbols}

    def test_versions_are_default_bindings(self, versioned_so) -> None:
        """``@@`` (default) rather than ``@`` (hidden), as the script implies."""
        meta = parse_elf_metadata(str(_require(versioned_so)))
        for symbol in meta.symbols:
            if symbol.name in _EXPECTED_VERSIONS:
                assert symbol.is_default is True, symbol.name

    def test_the_two_version_nodes_are_both_observed(self, versioned_so) -> None:
        """Both nodes, not just whichever one the walk happened to reach.

        A correlation that mis-indexed ``.gnu.version`` by one ordinal
        would still produce *some* version for every symbol, so the
        distinguishing check is that the right symbols land in the right
        node.
        """
        meta = parse_elf_metadata(str(_require(versioned_so)))
        by_version: dict[str, set[str]] = {}
        for symbol in meta.symbols:
            if symbol.name in _EXPECTED_VERSIONS:
                by_version.setdefault(symbol.version, set()).add(symbol.name)
        assert by_version == {
            "LIBV_1.0": {"alpha"},
            "LIBV_2.0": {"beta", "gamma_fn"},
        }

    def test_string_table_buffering_does_not_change_the_versions(
        self, versioned_so, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The version walk is buffered too, so it needs the same equivalence.

        ``_parse_dynsym`` and the version correlation are two separate
        walks of one table, and both were wrapped in the string-table
        buffer. A buffer that served a wrong name would mis-key the
        version map, which this catches by comparing against an unbuffered
        parse of the same file.
        """
        import abicheck.extract.elf_string_table as mod

        so = str(_require(versioned_so))

        def shape():
            meta = parse_elf_metadata(so)
            return sorted(
                (s.name, s.version, s.is_default) for s in meta.symbols
            ), sorted((i.name, i.version, i.version_soname) for i in meta.imports)

        buffered = shape()
        monkeypatch.setattr(mod, "MAX_BUFFERED_TABLE_BYTES", 0)
        assert shape() == buffered
        assert any(v for _n, v, _d in buffered[0]), "no versions were parsed"

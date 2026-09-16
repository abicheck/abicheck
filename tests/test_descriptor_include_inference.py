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

"""A descriptor's ``<headers>`` roots are its automatic include path.

Bug class (``tests/regressions/manifest.py``: ``inferred-include-root-
bypassed-by-a-second-entry-point``): a resolution step every other entry
point performs, skipped by one that calls the low-level extractor
directly. ``resolve_inferred_header_roots`` already existed and already
returned the right root; the compat path expanded the descriptor's
directory into individual files and handed them to ``dumper.dump``, so
nothing ever inferred anything. The symptom was an umbrella header whose
``#include <sibling.h>`` did not resolve, reported as a compile failure
naming neither the descriptor nor the missing root -- Intel MKL's own
descriptor could not be compiled without hand-adding an
``<include_paths>`` element it should never have needed.

ABICC selects this automatic mode by the *absence* of ``<include_paths>``
(``Internals/Descriptor.pm``), which is why the descriptor model keeps
``<include_paths>`` and ``<add_include_paths>`` separate rather than
flattening them into one list.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from abicheck._compiler_options import split_gcc_options
from abicheck.compat.cli import _descriptor_compile_options
from abicheck.compat.descriptor import parse_descriptor


def _descriptor(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "d.xml"
    path.write_text(body, encoding="utf-8")
    return path


class TestAutomaticIncludePathMode:
    def test_absent_include_paths_selects_the_automatic_mode(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "include").mkdir()
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include</headers>",
            )
        )
        assert desc.auto_include_paths is True
        tokens = split_gcc_options(_descriptor_compile_options(desc))
        assert str(tmp_path / "include") in " ".join(tokens)

    def test_add_include_paths_alone_does_not_disable_it(self, tmp_path: Path) -> None:
        """``<add_include_paths>`` *adds* to what the automatic mode
        searches; only ``<include_paths>`` replaces it. Flattening the two
        made a descriptor using the additive element lose its own roots."""
        (tmp_path / "include").mkdir()
        (tmp_path / "extra").mkdir()
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include</headers>"
                "<add_include_paths>extra</add_include_paths>",
            )
        )
        assert desc.auto_include_paths is True
        rendered = " ".join(split_gcc_options(_descriptor_compile_options(desc)))
        assert str(tmp_path / "extra") in rendered
        assert str(tmp_path / "include") in rendered

    def test_explicit_include_paths_disables_it(self, tmp_path: Path) -> None:
        """The negative control: a descriptor that stated its search path in
        full gets exactly that path, and nothing inferred behind its back --
        inferring anyway can change which header an ambiguous ``#include``
        resolves to."""
        (tmp_path / "include").mkdir()
        (tmp_path / "stated").mkdir()
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include</headers>"
                "<include_paths>stated</include_paths>",
            )
        )
        assert desc.auto_include_paths is False
        rendered = " ".join(split_gcc_options(_descriptor_compile_options(desc)))
        assert str(tmp_path / "stated") in rendered
        assert str(tmp_path / "include") not in rendered

    def test_a_header_file_operand_contributes_its_own_root(
        self, tmp_path: Path
    ) -> None:
        """The reported reproduction shape: ``<headers>`` naming
        ``include/mkl.h`` rather than the directory."""
        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "mkl.h").write_text("#include <mkl_dfti.h>\n", encoding="utf-8")
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include/mkl.h</headers>",
            )
        )
        rendered = " ".join(split_gcc_options(_descriptor_compile_options(desc)))
        assert str(inc) in rendered

    def test_only_the_declared_root_is_added_not_every_walked_directory(
        self, tmp_path: Path
    ) -> None:
        """Adding each walked subdirectory would build a search path of
        hundreds of entries on a real release tree and silently change
        header shadowing. The descriptor root is the unit."""
        inc = tmp_path / "include"
        (inc / "deep" / "deeper").mkdir(parents=True)
        (inc / "deep" / "deeper" / "x.h").write_text("int x(void);\n")
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include</headers>",
            )
        )
        rendered = " ".join(split_gcc_options(_descriptor_compile_options(desc)))
        assert str(inc) in rendered
        assert str(inc / "deep") not in rendered


_UMBRELLA = "#include <dii_dfti.h>\nint dii_api(dii_dfti_t *h);\n"
_SIBLING = "#pragma once\ntypedef struct { int v; } dii_dfti_t;\n"
_SOURCE = '#include "dii.h"\nint dii_api(dii_dfti_t *h) { return h->v; }\n'


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="builds an ELF .so, linux/macos only"
)
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
class TestTheUnmodifiedDescriptorCompiles:
    """The acceptance criterion: no ``<include_paths>`` added by hand, no
    rewritten ``<headers>``, and the umbrella's angle-include resolves."""

    @staticmethod
    def _build(tmp_path: Path) -> tuple[Path, Path]:
        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "dii.h").write_text(_UMBRELLA, encoding="utf-8")
        (inc / "dii_dfti.h").write_text(_SIBLING, encoding="utf-8")
        src = tmp_path / "dii.c"
        src.write_text(_SOURCE, encoding="utf-8")
        so = tmp_path / "libdii.so"
        subprocess.run(
            ["gcc", f"-I{inc}", "-fPIC", "-shared", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
            text=True,
        )
        return inc, so

    def test_compat_dump_parses_the_umbrella_header(self, tmp_path: Path) -> None:
        inc, so = self._build(tmp_path)
        desc = _descriptor(
            tmp_path,
            f"<version>1.0</version>\n<headers>{inc}</headers>\n<libs>{so}</libs>\n",
        )
        out = tmp_path / "out.json"
        from abicheck.compat.cli import compat_group

        result = CliRunner().invoke(
            compat_group,
            ["dump", "-lib", "dii", "-dump", str(desc), "-dump-path", str(out)],
        )
        assert result.exit_code == 0, result.output
        from abicheck.serialization import load_snapshot

        snap = load_snapshot(out)
        # The declaration only parses when <dii_dfti.h> resolved, which only
        # happens when the descriptor's own <headers> root was searched.
        assert any(f.name == "dii_api" for f in snap.functions), [
            f.name for f in snap.functions
        ]


class TestTheInferenceGoesThroughTheSharedResolver:
    """Not a second, parallel implementation of root inference.

    A private re-derivation here is exactly how the compat path came to
    differ from every other entry point in the first place.
    """

    def test_resolve_inferred_header_roots_is_the_one_called(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "include").mkdir()
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<version>1</version><libs>l.so</libs><headers>include</headers>",
            )
        )
        sentinel = Path("/sentinel/root")
        with mock.patch(
            "abicheck.compat.multi_library_run.resolve_inferred_header_roots",
            return_value=([sentinel], []),
        ) as spy:
            rendered = _descriptor_compile_options(desc)
        assert spy.called
        # `str(sentinel)`, not the POSIX literal: the emitter renders a
        # `Path`, and a `Path` spells itself with the host's own separator
        # (`\sentinel\root` on Windows -- real Windows CI failure).
        assert str(sentinel) in rendered

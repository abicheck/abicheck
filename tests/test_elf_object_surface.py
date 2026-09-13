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

"""Relocatable-object (`.o`) symbol-surface capture — G2.

A relocatable object carries no `.dynsym`; `parse_elf_metadata` falls back to
`.symtab` so a probe-built `.o`'s defined global symbols are captured (which
unblocks `API_DEPENDS_ON_CONSUMER_ENV` end-to-end). This needs only a C
compiler (stock `cc`), so — like `test_probe_examples.py` — it runs in the
default lane and self-skips when no compiler is available, rather than using
the castxml-gated ``integration`` marker.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.elf_metadata import SymbolBinding, parse_elf_metadata

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="relocatable-object ELF capture is exercised on Linux (cc emits ELF)",
)


def _compile_object(src: str, out: Path) -> None:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("cc unavailable; cannot compile relocatable object")
    res = subprocess.run(
        [cc, "-c", "-x", "c", "-", "-o", str(out)],
        input=src.encode(),
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"cc failed: {res.stderr.decode()[:200]}")


def test_symtab_fallback_captures_object_global_symbols(tmp_path: Path) -> None:
    obj = tmp_path / "probe.o"
    _compile_object(
        """
        int public_api(int x) { return x + 1; }
        static int helper(int y) { return y; }
        int uses_helper(void) { return helper(0); }
        """,
        obj,
    )
    meta = parse_elf_metadata(obj)
    names = {s.name for s in meta.symbols}

    assert "public_api" in names
    assert "uses_helper" in names
    # static symbols are local → never part of the exported surface
    assert "helper" not in names
    # captured entries are GLOBAL-bound defined symbols
    assert all(s.binding is not SymbolBinding.LOCAL for s in meta.symbols)


def test_symtab_fallback_excludes_undefined_references(tmp_path: Path) -> None:
    """An undefined reference (e.g. a libc call) is an import, not an export."""
    obj = tmp_path / "ref.o"
    _compile_object(
        """
        extern int external_thing(int);
        int my_api(int x) { return external_thing(x); }
        """,
        obj,
    )
    meta = parse_elf_metadata(obj)
    exported = {s.name for s in meta.symbols}

    assert "my_api" in exported
    assert "external_thing" not in exported  # undefined → import, not export
    assert "external_thing" in {i.name for i in meta.imports}

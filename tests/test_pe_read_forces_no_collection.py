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

"""Reading a PE image forces no garbage collection, on every read path.

Bug class ``perf.optimization_wired_to_one_of_several_equivalent_paths``.
``pefile.PE.close()`` ends in an unconditional ``gc.collect()`` -- a full
collection of the whole host process on every DLL read. Each PE ``compare``
reads every DLL several times (exports, the CodeView PDB reference, consumer
imports), and on the Windows integration lane those collections were a
quarter of a live compare's wall time. ``abicheck.extract.pe_image.open_pe``
maps the image itself so pefile's ``close()`` is never needed.

Two halves, as the class requires:

* every PE reader, on a real DLL, triggers no collection and leaves no handle
  on the file (it can be replaced right after the read). The oracle is
  ``gc.callbacks`` with automatic collection disabled, so the only
  collections it can see are explicit ``gc.collect()`` calls;
* no module in ``abicheck/`` constructs ``pefile.PE`` anywhere but
  ``open_pe``, so a new read path cannot quietly bring the collection back.
"""

from __future__ import annotations

import ast
import gc
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from _pe_build import build_pe_dll

from abicheck.extract.consumer_imports import _parse_pe_app_requirements
from abicheck.pdb_utils import _extract_pdb_path_from_pe
from abicheck.pe_metadata import parse_pe_metadata

_PACKAGE = Path(__file__).resolve().parent.parent / "abicheck"
_OWNER = _PACKAGE / "extract" / "pe_image.py"

#: Every public way abicheck reads a PE image.
_READERS: dict[str, Callable[[Path], Any]] = {
    "exports_and_imports": parse_pe_metadata,
    "codeview_pdb_reference": _extract_pdb_path_from_pe,
    "consumer_imports": lambda p: _parse_pe_app_requirements(p, "kernel32.dll"),
}


@pytest.fixture(scope="module")
def dll(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("pe-read")
    src = root / "foo.c"
    src.write_text(
        "int foo(void){return 1;}\nint bar(int x){return x;}\n", encoding="utf-8"
    )
    out = root / "foo.dll"
    if not build_pe_dll(out, src):
        pytest.skip("no PE toolchain (MinGW gcc, or clang + ld.lld)")
    return out


@pytest.fixture
def explicit_collections() -> Iterator[list[int]]:
    """The generations of every collection started while the test runs, with
    automatic collection off so only explicit ``gc.collect()`` calls count."""
    seen: list[int] = []

    def record(phase: str, info: dict[str, int]) -> None:
        if phase == "start":
            seen.append(info["generation"])

    was_enabled = gc.isenabled()
    gc.disable()
    gc.callbacks.append(record)
    try:
        yield seen
    finally:
        gc.callbacks.remove(record)
        if was_enabled:
            gc.enable()


@pytest.mark.integration
@pytest.mark.parametrize("reader", sorted(_READERS))
def test_reading_a_pe_image_forces_no_collection(
    reader: str, dll: Path, explicit_collections: list[int]
) -> None:
    for _ in range(3):
        _READERS[reader](dll)
    assert explicit_collections == []


@pytest.mark.integration
@pytest.mark.parametrize("reader", sorted(_READERS))
def test_reading_a_pe_image_leaves_no_handle_on_it(
    reader: str, dll: Path, tmp_path: Path
) -> None:
    """The mapping is released when the read returns: on Windows a file with
    a live mapping cannot be replaced."""
    copy = tmp_path / "copy.dll"
    copy.write_bytes(dll.read_bytes())
    _READERS[reader](copy)
    replacement = tmp_path / "next.dll"
    replacement.write_bytes(dll.read_bytes())
    os.replace(replacement, copy)
    copy.unlink()
    assert not copy.exists()


@pytest.mark.integration
def test_the_counter_sees_a_direct_pefile_read(
    dll: Path, explicit_collections: list[int]
) -> None:
    """Negative control: pefile's own name-based open-and-close is visible to
    the oracle, so an empty count above is a real absence."""
    import pefile  # type: ignore[import-untyped]

    pe = pefile.PE(str(dll), fast_load=True)
    pe.close()
    assert explicit_collections, "the oracle missed pefile's own gc.collect()"


def _direct_pe_constructions(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "PE"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pefile"
    ]


@pytest.mark.repo_scan
def test_only_open_pe_constructs_a_pefile_image() -> None:
    offenders = [
        f"{path.relative_to(_PACKAGE.parent)}:{line}"
        for path in sorted(_PACKAGE.rglob("*.py"))
        if path != _OWNER
        for line in _direct_pe_constructions(
            ast.parse(path.read_text(encoding="utf-8"))
        )
    ]
    assert offenders == [], (
        f"construct PE images through abicheck.extract.pe_image.open_pe: {offenders}"
    )


def test_the_scan_recognises_a_direct_construction() -> None:
    """The scanner on known shapes: a bare ``pefile.PE(...)`` call is found,
    a same-named attribute on another object is not."""
    found = _direct_pe_constructions(
        ast.parse("import pefile\npe = pefile.PE('x.dll')\nother.PE('y')\n")
    )
    assert found == [2]
    # The owner itself holds exactly the one construction the scan exempts.
    owner = ast.parse(_OWNER.read_text(encoding="utf-8"))
    assert len(_direct_pe_constructions(owner)) == 1

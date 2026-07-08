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

"""Tests for CPython extension-module support (G14).

Covers: extension recognition (Cython/pybind11/C-ext, abi3 vs version-specific),
the stable-ABI classifier, the compare-time detector (stable-ABI violations and
interpreter-floor drift), snapshot serialization round-trip, and the
``abicheck stable-abi`` CLI.
"""

from __future__ import annotations

import json

import pytest

from abicheck import stable_abi
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
)
from abicheck.model import AbiSnapshot
from abicheck.python_ext import PythonExtMetadata, detect_python_extension
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.stable_abi import StableAbiStatus


def _ext_snapshot(
    version: str,
    imports: list[str],
    *,
    init: str | None = "PyInit_foo",
    source_path: str = "foo.abi3.so",
    library: str = "foo.abi3.so",
) -> AbiSnapshot:
    """Build an extension-module snapshot with the given CPython imports."""
    elf = ElfMetadata()
    if init is not None:
        elf.symbols = [
            ElfSymbol(name=init, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
        ]
    elf.imports = [
        ElfImport(name=i, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
        for i in imports
    ]
    snap = AbiSnapshot(
        library=library, version=version, elf=elf, source_path=source_path
    )
    snap.python_ext = detect_python_extension(snap)
    return snap


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


# ── Recognition ─────────────────────────────────────────────────────────────


def test_detect_extension_from_pyinit_export() -> None:
    snap = _ext_snapshot("1.0", ["PyList_New", "malloc"])
    assert snap.python_ext is not None
    assert snap.python_ext.is_extension
    assert snap.python_ext.module_name == "foo"
    assert snap.python_ext.init_symbol == "PyInit_foo"
    assert snap.python_ext.python_major == 3


def test_detect_only_captures_cpython_imports() -> None:
    snap = _ext_snapshot("1.0", ["PyList_New", "malloc", "memcpy", "_PyObject_New"])
    assert snap.python_ext is not None
    # libc symbols dropped; both public and private CPython symbols kept.
    assert snap.python_ext.cpython_imports == ["PyList_New", "_PyObject_New"]


def test_detect_abi3_from_suffix() -> None:
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path="foo.abi3.so")
    assert snap.python_ext is not None
    assert snap.python_ext.limited_api is True
    assert snap.python_ext.soabi_tag == "abi3"


def test_detect_version_specific_soabi_is_not_abi3() -> None:
    snap = _ext_snapshot(
        "1.0",
        ["PyList_New"],
        source_path="foo.cpython-311-x86_64-linux-gnu.so",
        library="foo.cpython-311-x86_64-linux-gnu.so",
    )
    assert snap.python_ext is not None
    assert snap.python_ext.limited_api is False
    assert snap.python_ext.declared_abi3 == (3, 11)


def test_detect_windows_pyd_tag() -> None:
    snap = _ext_snapshot(
        "1.0",
        ["PyList_New"],
        source_path="foo.cp312-win_amd64.pyd",
        library="foo.cp312-win_amd64.pyd",
    )
    assert snap.python_ext is not None
    assert snap.python_ext.declared_abi3 == (3, 12)


def test_plain_library_is_not_an_extension() -> None:
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    elf.imports = [
        ElfImport(name="malloc", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    snap = AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)
    assert detect_python_extension(snap) is None


def test_extension_detected_from_imports_without_init_export() -> None:
    # A stripped or statically-linked init still leaves the Py* import surface.
    snap = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"], init=None)
    assert snap.python_ext is not None
    assert snap.python_ext.is_extension
    assert snap.python_ext.init_symbol is None


# ── Stable-ABI classifier ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,floor,expected",
    [
        ("_PyObject_New", None, StableAbiStatus.PRIVATE),
        ("_PyRuntime", None, StableAbiStatus.PRIVATE),
        ("PyList_New", (3, 2), StableAbiStatus.STABLE),
        ("PyType_GetName", (3, 9), StableAbiStatus.ABOVE_FLOOR),
        ("PyType_GetName", (3, 12), StableAbiStatus.STABLE),
        ("PyTotallyMadeUp_Thing", None, StableAbiStatus.UNKNOWN),
        ("malloc", None, StableAbiStatus.NOT_CPYTHON),
    ],
)
def test_classify(
    name: str, floor: tuple[int, int] | None, expected: StableAbiStatus
) -> None:
    status, _ = stable_abi.classify(name, floor)
    assert status is expected


def test_min_required_abi3_ignores_private_and_unknown() -> None:
    floor = stable_abi.min_required_abi3(
        ["PyList_New", "PyType_GetName", "_PyPrivate", "PyMadeUp"]
    )
    assert floor == (3, 11)


def test_min_required_abi3_none_when_no_recognised_stable() -> None:
    assert stable_abi.min_required_abi3(["_PyPrivate", "PyMadeUp"]) is None


@pytest.mark.parametrize(
    "text,expected",
    [("3.9", (3, 9)), ("3", (3, 0)), ("3.12", (3, 12)), ("bogus", None), ("", None)],
)
def test_parse_abi3_version(text: str, expected: tuple[int, int] | None) -> None:
    assert stable_abi.parse_abi3_version(text) == expected


# ── Compare-time detector ────────────────────────────────────────────────────


def test_stable_abi_violation_on_new_private_import() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong", "_PyObject_GC_New"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_abi_floor_raised_detected() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong", "PyType_GetName"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI_FLOOR_RAISED in _kinds(result)


def test_no_finding_when_import_surface_unchanged() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)
    assert ChangeKind.PYTHON_ABI_FLOOR_RAISED not in _kinds(result)


def test_version_specific_module_does_not_flag_private_imports() -> None:
    # A cpython-311 (non-abi3) module legitimately uses private CPython API and
    # is rebuilt per interpreter, so the stable-ABI contract must not apply.
    src = "foo.cpython-311-x86_64-linux-gnu.so"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot(
        "2.0", ["PyList_New", "_PyObject_GC_New"], source_path=src, library=src
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


def test_detector_skipped_for_non_extension_pair() -> None:
    a = AbiSnapshot(library="libfoo.so", version="1.0", elf=ElfMetadata())
    b = AbiSnapshot(library="libfoo.so", version="2.0", elf=ElfMetadata())
    result = compare(a, b)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


# ── Serialization ────────────────────────────────────────────────────────────


def test_python_ext_survives_serialization_roundtrip() -> None:
    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName", "_PyObject_New"])
    back = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
    assert back.python_ext is not None
    assert back.python_ext.cpython_imports == snap.python_ext.cpython_imports
    assert back.python_ext.limited_api is True
    assert back.python_ext.module_name == "foo"


def test_python_ext_metadata_helpers() -> None:
    meta = PythonExtMetadata(
        module_name="foo",
        cpython_imports=["PyList_New", "PyType_GetName", "_PyPrivate"],
        limited_api=True,
    )
    assert meta.private_imports == ["_PyPrivate"]
    assert meta.min_required_abi3() == (3, 11)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _write_snapshot(tmp_path: object, snap: AbiSnapshot) -> str:
    path = f"{tmp_path}/ext.abi.json"
    with open(path, "w") as fh:
        fh.write(snapshot_to_json(snap))
    return path


def test_cli_stable_abi_flags_above_floor(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path, "--abi3", "3.9", "-f", "json"])
    assert result.exit_code == 1, result.output
    assert "python_stable_abi_violation" in result.output


def test_cli_stable_abi_clean_passes(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    # Floor 3.12 admits PyType_GetName (added 3.11) → no findings.
    result = runner.invoke(main, ["stable-abi", path, "--abi3", "3.12", "-f", "json"])
    assert result.exit_code == 0, result.output


def test_cli_stable_abi_rejects_non_extension(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    snap = AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path])
    assert result.exit_code == 2, result.output
    assert "not a recognisable CPython extension" in result.output

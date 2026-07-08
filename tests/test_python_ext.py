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


def test_detect_windows_abi3_tag_with_floor() -> None:
    # A Windows stable-ABI artifact whose name carries the `cpXY-abi3` wheel tag
    # is recognised as abi3 AND its floor recovered.
    snap = _ext_snapshot(
        "1.0",
        ["PyList_New"],
        source_path="foo.cp39-abi3-win_amd64.pyd",
        library="foo.cp39-abi3-win_amd64.pyd",
    )
    assert snap.python_ext is not None
    assert snap.python_ext.limited_api is True
    assert snap.python_ext.declared_abi3 == (3, 9)


def test_abi3_tag_variants_recognised() -> None:
    from abicheck.python_ext import _detect_soabi

    # bare `.abi3.` suffix (no floor)
    assert _detect_soabi("foo.abi3.so", None)[:2] == ("abi3", True)
    # `-abi3-` token embedded (no cp floor)
    assert _detect_soabi("foo-abi3-linux.so", None)[:2] == ("abi3", True)
    # version-specific tags stay non-abi3
    assert _detect_soabi("foo.cp312-win_amd64.pyd", None) == (
        "cpython-312",
        False,
        (3, 12),
    )


def test_windows_abi3_pyd_compare_flags_new_private_import() -> None:
    # Two cp39-abi3 Windows builds; the new one gains a private import → flagged.
    src = "foo.cp39-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot(
        "2.0", ["PyList_New", "_PyObject_New"], source_path=src, library=src
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


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


@pytest.mark.parametrize(
    "name", ["_Py_NoneStruct", "_Py_TrueStruct", "_Py_FalseStruct"]
)
def test_stable_abi_singleton_data_symbols_are_not_private(name: str) -> None:
    # The ABI-only structs behind Py_None/Py_True/Py_False are `_Py`-prefixed but
    # part of the Limited API — they must not be flagged as private violations.
    assert stable_abi.is_private_symbol(name) is False
    status, _ = stable_abi.classify(name, (3, 9))
    assert status is StableAbiStatus.STABLE


def test_min_required_abi3_ignores_private_and_unknown() -> None:
    floor = stable_abi.min_required_abi3(
        ["PyList_New", "PyType_GetName", "_PyPrivate", "PyMadeUp"]
    )
    assert floor == (3, 11)


def test_min_required_abi3_none_when_no_recognised_stable() -> None:
    assert stable_abi.min_required_abi3(["_PyPrivate", "PyMadeUp"]) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3.9", (3, 9)),
        ("3.12", (3, 12)),
        ("bogus", None),
        ("", None),
        # Py_LIMITED_API=3 → the 3.2 Stable-ABI baseline (not 3.0).
        ("3", (3, 2)),
        ("3.0", (3, 2)),
        ("3.1", (3, 2)),
    ],
)
def test_parse_abi3_version(text: str, expected: tuple[int, int] | None) -> None:
    assert stable_abi.parse_abi3_version(text) == expected


def test_stable_abi_since_differs_from_added_version() -> None:
    # PyType_GetModuleByDef was added in CPython 3.11 but only entered the Stable
    # ABI in 3.13: an abi3 module targeting 3.11 that imports it is above-floor.
    status, added = stable_abi.classify("PyType_GetModuleByDef", (3, 11))
    assert status is StableAbiStatus.ABOVE_FLOOR
    assert added == (3, 13)
    status, _ = stable_abi.classify("PyType_GetModuleByDef", (3, 13))
    assert status is StableAbiStatus.STABLE


def test_bare_major_floor_accepts_core_stable_symbols() -> None:
    # A cp3-abi3 (Py_LIMITED_API=3) module importing only 3.2-era stable symbols
    # must NOT be flagged as above-floor once `3` maps to (3, 2).
    floor = stable_abi.parse_abi3_version("3")
    status, _ = stable_abi.classify("PyList_New", floor)
    assert status is StableAbiStatus.STABLE


# ── Compare-time detector ────────────────────────────────────────────────────


def test_stable_abi_violation_on_new_private_import() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong", "_PyObject_GC_New"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_added_stable_import_is_not_flagged_as_floor_raise() -> None:
    # Adding a newer *stable* symbol (PyType_GetName, 3.11) is NOT a finding:
    # without the module's declared floor we cannot prove any supported
    # interpreter was dropped, so the compare-time detector stays silent
    # (floor conformance is the `stable-abi --abi3` command's job).
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong", "PyType_GetName"])
    result = compare(old, new)
    assert not (_kinds(result) & {ChangeKind.PYTHON_STABLE_ABI_VIOLATION})


def test_no_finding_when_import_surface_unchanged() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


def test_retag_to_abi3_flags_carried_over_private_import() -> None:
    # Retagging foo.cpython-311.so → foo.abi3.so makes a NEW cross-interpreter
    # promise; a private import carried over unchanged is now a violation even
    # though it is not newly gained.
    old = _ext_snapshot(
        "1.0",
        ["PyList_New", "_PyObject_GC_New"],
        source_path="foo.cpython-311-x86_64-linux-gnu.so",
        library="foo.cpython-311-x86_64-linux-gnu.so",
    )
    new = _ext_snapshot("2.0", ["PyList_New", "_PyObject_GC_New"])  # foo.abi3.so
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


def test_abi3_module_using_py_none_is_clean() -> None:
    # A clean Limited-API module that uses Py_None (imports _Py_NoneStruct) must
    # not be flagged as a stable-ABI violation.
    old = _ext_snapshot("1.0", ["PyList_New"])
    new = _ext_snapshot("2.0", ["PyList_New", "_Py_NoneStruct"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


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
    with open(path, "w", encoding="utf-8") as fh:
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


def test_cli_stable_abi_flags_private_import(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_New"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    # No --abi3: uses the module's own (abi3-tagged) floor; a private import is
    # always a violation regardless of floor.
    result = runner.invoke(main, ["stable-abi", path, "-f", "json"])
    assert result.exit_code == 1, result.output
    assert "python_stable_abi_violation" in result.output


@pytest.mark.parametrize("fmt", ["markdown", "json", "sarif", "junit"])
def test_cli_stable_abi_all_output_formats(tmp_path: object, fmt: str) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_New"])
    path = _write_snapshot(tmp_path, snap)
    out = f"{tmp_path}/report.{fmt}"

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path, "-f", fmt, "-o", out])
    assert result.exit_code == 1, result.output
    with open(out, encoding="utf-8") as fh:
        assert fh.read().strip()


def test_cli_stable_abi_invalid_abi3(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = _ext_snapshot("2.0", ["PyList_New"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path, "--abi3", "not-a-version"])
    assert result.exit_code != 0
    assert "invalid --abi3" in result.output


def test_cli_stable_abi_reports_unknown_advisory(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    # A public Py* symbol not in the curated allowlist is an advisory, not a
    # hard finding: exit 0, but the summary mentions it.
    snap = _ext_snapshot("2.0", ["PyList_New", "PyTotallyMadeUpSymbol"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path, "--abi3", "3.9"])
    assert result.exit_code == 0, result.output
    assert "advisory" in result.output
    assert "PyTotallyMadeUpSymbol" in result.output


def test_cli_stable_abi_warns_when_no_floor_on_abi3_module(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    # abi3 module, no --abi3: the stable-symbol floor check cannot run. A newer
    # stable import (PyType_GetName, 3.11) is not flagged, but the audit is
    # INCOMPLETE and must fail (exit 3), not pass silently — otherwise CI would
    # accept a cp39-abi3 artifact importing a 3.11 symbol (Codex review).
    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path])
    assert result.exit_code == 3, result.output
    assert "INCOMPLETE" in result.output
    assert "--abi3" in result.output

    # Supplying --abi3 lets the floor check run: PyType_GetName (3.11) > 3.9 →
    # a real violation (exit 1), and at 3.12 it is clean (exit 0).
    assert runner.invoke(main, ["stable-abi", path, "--abi3", "3.9"]).exit_code == 1
    assert runner.invoke(main, ["stable-abi", path, "--abi3", "3.12"]).exit_code == 0


def test_cli_stable_abi_private_import_flagged_without_floor(tmp_path: object) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main

    # Private imports are caught even without a floor.
    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_New"])
    path = _write_snapshot(tmp_path, snap)

    runner = CliRunner()
    result = runner.invoke(main, ["stable-abi", path, "-f", "json"])
    assert result.exit_code == 1, result.output
    assert "python_stable_abi_violation" in result.output


# ── Cross-platform detection (PE / Mach-O) ──────────────────────────────────


def test_detect_extension_from_pe_imports() -> None:
    from abicheck.pe_metadata import PeExport, PeMetadata

    pe = PeMetadata()
    pe.exports = [PeExport(name="PyInit_foo")]
    pe.imports = {
        "python312.dll": ["PyList_New", "_PyObject_New"],
        "kernel32.dll": ["Sleep"],
    }
    snap = AbiSnapshot(library="foo.cp312-win_amd64.pyd", version="1.0", pe=pe)
    meta = detect_python_extension(snap)
    assert meta is not None
    assert meta.module_name == "foo"
    assert meta.cpython_imports == ["PyList_New", "_PyObject_New"]
    assert meta.declared_abi3 == (3, 12)


def test_detect_extension_from_macho_imports() -> None:
    from abicheck.macho_metadata import MachoExport, MachoMetadata

    macho = MachoMetadata()
    macho.exports = [MachoExport(name="PyInit_foo")]
    macho.imported_symbols = ["PyList_New", "_PyObject_New", "malloc"]
    snap = AbiSnapshot(library="foo.abi3.so", version="1.0", macho=macho)
    meta = detect_python_extension(snap)
    assert meta is not None
    assert meta.cpython_imports == ["PyList_New", "_PyObject_New"]
    assert meta.limited_api is True


def test_detect_python2_init_export() -> None:
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name="initfoo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    elf.imports = [
        ElfImport(
            name="PyList_New", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    snap = AbiSnapshot(library="foo.so", version="1.0", elf=elf)
    meta = detect_python_extension(snap)
    assert meta is not None
    assert meta.python_major == 2
    assert meta.module_name == "foo"


def test_soabi_falls_back_to_library_name_when_no_source_path() -> None:
    snap = _ext_snapshot(
        "1.0", ["PyList_New"], source_path="/build/tmp.so", library="foo.abi3.so"
    )
    assert snap.python_ext is not None
    # source_path has no tag; library carries the abi3 tag.
    assert snap.python_ext.limited_api is True


# ── Detector helpers ────────────────────────────────────────────────────────


def test_module_symbol_fallbacks() -> None:
    from abicheck.diff_python import _module_symbol

    named = PythonExtMetadata(module_name="foo")
    assert _module_symbol(named, named) == "python:foo"
    # module_name absent but init_symbol present → identify by init symbol.
    init_only = PythonExtMetadata(init_symbol="PyInit_bar")
    assert _module_symbol(init_only, init_only) == "python:PyInit_bar"
    # neither → generic placeholder.
    empty = PythonExtMetadata()
    assert _module_symbol(empty, empty) == "python:<extension>"


def test_detector_uses_init_symbol_when_module_name_absent() -> None:
    # imports-only extension (no PyInit export) → module_name is None; the
    # finding falls back to a stable identifier without crashing.
    old = _ext_snapshot("1.0", ["PyList_New"], init=None)
    new = _ext_snapshot("2.0", ["PyList_New", "_PyObject_GC_New"], init=None)
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


# ── Service attach hook ──────────────────────────────────────────────────────


def test_service_attach_hook_sets_metadata() -> None:
    from abicheck.service import _try_attach_python_ext_metadata

    snap = AbiSnapshot(
        library="foo.abi3.so",
        version="1.0",
        elf=ElfMetadata(),
        source_path="foo.abi3.so",
    )
    snap.elf.imports = [
        ElfImport(
            name="PyList_New", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    _try_attach_python_ext_metadata(snap)
    assert snap.python_ext is not None
    assert snap.python_ext.limited_api is True


def test_service_attach_hook_noop_for_plain_library() -> None:
    from abicheck.service import _try_attach_python_ext_metadata

    snap = AbiSnapshot(library="libfoo.so", version="1.0", elf=ElfMetadata())
    _try_attach_python_ext_metadata(snap)
    assert snap.python_ext is None


# ── Serialization edge cases ─────────────────────────────────────────────────


def test_declared_abi3_tuple_survives_roundtrip() -> None:
    src = "foo.cpython-312-x86_64-linux-gnu.so"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    assert snap.python_ext.declared_abi3 == (3, 12)
    back = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
    assert back.python_ext is not None
    assert back.python_ext.declared_abi3 == (3, 12)


def test_derive_on_load_when_key_absent() -> None:
    # A snapshot dumped before G14 (no python_ext key) is re-derived on load so
    # a saved abi3 baseline is still checked at compare time.
    elf = {
        "symbols": [{"name": "PyInit_foo", "binding": "global", "sym_type": "func"}],
        "imports": [{"name": "_PyObject_New", "binding": "global", "sym_type": "func"}],
    }
    d = {
        "library": "foo.abi3.so",
        "version": "1.0",
        "source_path": "foo.abi3.so",
        "elf": elf,
    }
    snap = snapshot_from_dict(d)
    assert snap.python_ext is not None
    assert snap.python_ext.limited_api is True
    assert "_PyObject_New" in snap.python_ext.cpython_imports


def test_explicit_null_python_ext_not_rederived() -> None:
    # An explicit null means the dumper checked and found no extension; a bare
    # ELF library with a Py-free surface must stay None (not re-derived to a
    # false positive).
    elf = {
        "symbols": [{"name": "foo", "binding": "global", "sym_type": "func"}],
        "imports": [{"name": "malloc", "binding": "global", "sym_type": "func"}],
    }
    d = {"library": "libfoo.so", "version": "1.0", "elf": elf, "python_ext": None}
    snap = snapshot_from_dict(d)
    assert snap.python_ext is None

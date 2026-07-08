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

Covers: extension recognition (Cython/pybind11/C-ext, abi3 vs version-specific,
free-threaded), the stable-ABI classifier, the compare-time detector (stable-ABI
violations, abi3-dropped, GIL/free-threaded switch), snapshot serialization
round-trip, and the ``abicheck scan --abi3`` single-artifact audit.
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
    snap = _ext_snapshot(
        "1.0", ["PyList_New", "malloc", "memcpy", "_PyObject_LookupSpecial"]
    )
    assert snap.python_ext is not None
    # libc symbols dropped; both public and private CPython symbols kept.
    assert snap.python_ext.cpython_imports == ["PyList_New", "_PyObject_LookupSpecial"]


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
    # version-specific tags stay non-abi3 (and not free-threaded)
    assert _detect_soabi("foo.cp312-win_amd64.pyd", None) == (
        "cpython-312",
        False,
        (3, 12),
        False,
    )


def test_windows_abi3_pyd_compare_flags_new_private_import() -> None:
    # Two cp39-abi3 Windows builds; the new one gains a private import → flagged.
    src = "foo.cp39-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot(
        "2.0", ["PyList_New", "_PyObject_LookupSpecial"], source_path=src, library=src
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


def test_init_export_alone_is_not_an_extension() -> None:
    # A non-Python C library exporting `initialize` (matches the broad Py2
    # `init*` pattern) with NO Py* imports must NOT be treated as an extension.
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name="initialize", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    elf.imports = [
        ElfImport(name="malloc", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    snap = AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)
    assert detect_python_extension(snap) is None


def test_python2_init_requires_cpython_imports() -> None:
    # `initfoo` alone → not an extension; with a Py* import → a Py2 extension.
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name="initfoo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    snap = AbiSnapshot(library="foo.so", version="1.0", elf=elf)
    assert detect_python_extension(snap) is None

    elf.imports = [
        ElfImport(
            name="PyList_New", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]
    snap2 = AbiSnapshot(library="foo.so", version="1.0", elf=elf)
    meta = detect_python_extension(snap2)
    assert meta is not None
    assert meta.python_major == 2


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
        ("_PyObject_LookupSpecial", None, StableAbiStatus.PRIVATE),
        ("_PyRuntime", None, StableAbiStatus.PRIVATE),
        # PyUnstable_* (PEP 689) is public but never Limited-API → violation.
        ("PyUnstable_Code_New", None, StableAbiStatus.PRIVATE),
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
        # invalid floors — no Limited API outside the CPython 3 line.
        ("39", None),  # mistyped 3.9
        ("4", None),
        ("2.7", None),
        ("4.0", None),
        # a floor newer than the vendored data is a real/near-future
        # interpreter (e.g. --abi3 3.14 while data is 3.13) → accepted.
        ("3.14", (3, 14)),
        # implausible minors well beyond the future margin (typo like `3.99`)
        # would sort above every vendored symbol and silently suppress all
        # ABOVE_FLOOR violations → rejected.
        ("3.99", None),
        ("3.999", None),
        # trailing junk / extra version components are not valid floors.
        ("3.9.1", None),
    ],
)
def test_parse_abi3_version(text: str, expected: tuple[int, int] | None) -> None:
    assert stable_abi.parse_abi3_version(text) == expected


def test_parse_abi3_version_accepts_near_future_minor_rejects_typos() -> None:
    # A floor above the vendored data version but within the future margin is a
    # legitimate current/near-future interpreter; only an implausible minor past
    # the margin is rejected as a typo. Both bounds track the vendored data so a
    # refresh raises them automatically (typo rejection is decoupled from it).
    ceiling = stable_abi._MAX_ABI3_MINOR
    assert stable_abi._MAX_KNOWN_MINOR < ceiling  # there is real headroom
    assert stable_abi.parse_abi3_version(f"3.{ceiling}") == (3, ceiling)
    assert stable_abi.parse_abi3_version(f"3.{ceiling + 1}") is None


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
    new = _ext_snapshot(
        "2.0", ["PyList_New", "PyLong_FromLong", "_PyThreadState_UncheckedGet"]
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_added_stable_import_is_not_flagged_as_floor_raise() -> None:
    # Adding a newer *stable* symbol (PyType_GetName, 3.11) is NOT a finding:
    # without the module's declared floor we cannot prove any supported
    # interpreter was dropped, so the compare-time detector stays silent
    # (floor conformance is the `scan --abi3` audit's job).
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong", "PyType_GetName"])
    result = compare(old, new)
    assert not (_kinds(result) & {ChangeKind.PYTHON_STABLE_ABI_VIOLATION})


def test_unstable_api_import_flagged() -> None:
    # An abi3 module that gains a PyUnstable_* import (unstable API tier) is a
    # violation — the compare detector uses the same predicate as the audit.
    old = _ext_snapshot("1.0", ["PyList_New"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyUnstable_Code_New"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


def test_abi3_dropped_flagged_when_new_is_version_specific() -> None:
    # Old is cp39-abi3 (loads on all 3.9+); new is a version-specific
    # cpython-312 build → drops every other interpreter → risk flagged.
    old = _ext_snapshot(
        "1.0",
        ["PyList_New"],
        source_path="foo.cp39-abi3-win_amd64.pyd",
        library="foo.cp39-abi3-win_amd64.pyd",
    )
    new_src = "foo.cpython-312-x86_64-linux-gnu.so"
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI3_DROPPED in _kinds(result)


def test_abi3_dropped_not_flagged_when_old_was_not_abi3() -> None:
    # Two version-specific builds: no abi3 promise existed to drop.
    old_src = "foo.cpython-311-x86_64-linux-gnu.so"
    new_src = "foo.cpython-312-x86_64-linux-gnu.so"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=old_src, library=old_src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI3_DROPPED not in _kinds(result)


def test_abi3_floor_raised_flagged_from_declared_tags() -> None:
    # Both cpXY-abi3 with the same stable imports, but the declared floor rose
    # 3.9 → 3.10: CPython 3.9 users are dropped. Exact (declared tag on both).
    old_src = "foo.cp39-abi3-win_amd64.pyd"
    new_src = "foo.cp310-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=old_src, library=old_src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    assert old.python_ext.declared_abi3 == (3, 9)
    assert new.python_ext.declared_abi3 == (3, 10)
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI3_FLOOR_RAISED in _kinds(result)


def test_compare_flags_stable_import_above_declared_floor() -> None:
    # Both builds stay cp39-abi3, but the new one gains PyType_GetName (stable
    # since 3.11): it advertises floor 3.9 yet can no longer load on 3.9/3.10.
    # Exact — the floor is the declared cp39-abi3 tag, not inferred.
    src = "foo.cp39-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot(
        "2.0", ["PyList_New", "PyType_GetName"], source_path=src, library=src
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


def test_compare_stable_import_within_declared_floor_not_flagged() -> None:
    # Same symbol under a cp312-abi3 floor: PyType_GetName (3.11) ≤ 3.12 → fine.
    src = "foo.cp312-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot(
        "2.0", ["PyList_New", "PyType_GetName"], source_path=src, library=src
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


def test_compare_no_above_floor_check_without_declared_floor() -> None:
    # A bare `.abi3.so` carries no declared floor → no inference (avoids the
    # min-of-imports false positive); a stable import is not flagged.
    old = _ext_snapshot("1.0", ["PyList_New"], source_path="foo.abi3.so")
    new = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"], source_path="foo.abi3.so")
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


def test_abi3_floor_lowered_not_flagged() -> None:
    # Lowering the floor (3.10 → 3.9) supports *more* interpreters → no finding.
    old_src = "foo.cp310-abi3-win_amd64.pyd"
    new_src = "foo.cp39-abi3-win_amd64.pyd"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=old_src, library=old_src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI3_FLOOR_RAISED not in _kinds(result)


def test_abi3_floor_raise_skipped_without_declared_floor() -> None:
    # A bare `.abi3.so` carries no declared floor → nothing exact to compare, so
    # no floor-raise finding (avoids the min-of-imports false-positive trap).
    old = _ext_snapshot("1.0", ["PyList_New"], source_path="foo.abi3.so")
    new = _ext_snapshot("2.0", ["PyList_New"], source_path="foo.abi3.so")
    result = compare(old, new)
    assert ChangeKind.PYTHON_ABI3_FLOOR_RAISED not in _kinds(result)


def test_no_finding_when_import_surface_unchanged() -> None:
    old = _ext_snapshot("1.0", ["PyList_New", "PyLong_FromLong"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyLong_FromLong"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


# ── Free-threading (PEP 703, Py_GIL_DISABLED) ────────────────────────────────


def test_detect_free_threaded_soabi() -> None:
    # A `cpython-313t` tag marks a free-threaded (no-GIL) build; it is
    # version-specific (never abi3) and flagged free_threaded.
    src = "foo.cpython-313t-x86_64-linux-gnu.so"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    assert snap.python_ext is not None
    assert snap.python_ext.free_threaded is True
    assert snap.python_ext.limited_api is False
    assert snap.python_ext.soabi_tag == "cpython-313t"
    assert snap.python_ext.declared_abi3 == (3, 13)


def test_detect_free_threaded_windows_tag() -> None:
    src = "foo.cp314t-win_amd64.pyd"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    assert snap.python_ext is not None
    assert snap.python_ext.free_threaded is True
    assert snap.python_ext.declared_abi3 == (3, 14)


def test_regular_build_is_not_free_threaded() -> None:
    src = "foo.cpython-313-x86_64-linux-gnu.so"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    assert snap.python_ext is not None
    assert snap.python_ext.free_threaded is False


def test_gil_to_free_threaded_switch_flagged() -> None:
    # Regular (GIL) 3.13 build → free-threaded 3.13t build: the ABIs are not
    # interchangeable, so the switch is a deployment RISK.
    old_src = "foo.cpython-313-x86_64-linux-gnu.so"
    new_src = "foo.cpython-313t-x86_64-linux-gnu.so"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=old_src, library=old_src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_GIL_ABI_CHANGED in _kinds(result)


def test_free_threaded_to_gil_switch_flagged() -> None:
    # The reverse direction is equally a switch and equally flagged.
    old_src = "foo.cpython-313t-x86_64-linux-gnu.so"
    new_src = "foo.cpython-313-x86_64-linux-gnu.so"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=old_src, library=old_src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_GIL_ABI_CHANGED in _kinds(result)


def test_gil_abi_not_flagged_when_unchanged() -> None:
    # Two free-threaded builds: no GIL-ABI change.
    src = "foo.cpython-313t-x86_64-linux-gnu.so"
    old = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=src, library=src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_GIL_ABI_CHANGED not in _kinds(result)


def test_gil_abi_not_flagged_for_freshly_introduced_extension() -> None:
    # A module that was not an extension before gains no GIL-change finding.
    old = AbiSnapshot(library="foo.so", version="1.0", elf=ElfMetadata())
    new_src = "foo.cpython-313t-x86_64-linux-gnu.so"
    new = _ext_snapshot("2.0", ["PyList_New"], source_path=new_src, library=new_src)
    result = compare(old, new)
    assert ChangeKind.PYTHON_GIL_ABI_CHANGED not in _kinds(result)


def test_free_threaded_roundtrips_through_serialization() -> None:
    from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

    src = "foo.cpython-313t-x86_64-linux-gnu.so"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    restored = snapshot_from_dict(snapshot_to_dict(snap))
    assert restored.python_ext is not None
    assert restored.python_ext.free_threaded is True


def test_retag_to_abi3_flags_carried_over_private_import() -> None:
    # Retagging foo.cpython-311.so → foo.abi3.so makes a NEW cross-interpreter
    # promise; a private import carried over unchanged is now a violation even
    # though it is not newly gained.
    old = _ext_snapshot(
        "1.0",
        ["PyList_New", "_PyThreadState_UncheckedGet"],
        source_path="foo.cpython-311-x86_64-linux-gnu.so",
        library="foo.cpython-311-x86_64-linux-gnu.so",
    )
    new = _ext_snapshot(
        "2.0", ["PyList_New", "_PyThreadState_UncheckedGet"]
    )  # foo.abi3.so
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
        "2.0",
        ["PyList_New", "_PyThreadState_UncheckedGet"],
        source_path=src,
        library=src,
    )
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


def test_new_abi3_flagged_when_old_is_not_an_extension() -> None:
    # Baseline is a plain library (no python_ext); the new artifact is
    # introduced/retagged as abi3 and imports a private symbol → flagged, with
    # the missing old extension treated as an empty baseline.
    old = AbiSnapshot(library="libfoo.so", version="1.0", elf=ElfMetadata())
    new = _ext_snapshot("2.0", ["PyList_New", "_PyObject_LookupSpecial"])
    assert old.python_ext is None
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


def test_detector_skipped_for_non_extension_pair() -> None:
    a = AbiSnapshot(library="libfoo.so", version="1.0", elf=ElfMetadata())
    b = AbiSnapshot(library="libfoo.so", version="2.0", elf=ElfMetadata())
    result = compare(a, b)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)


# ── Serialization ────────────────────────────────────────────────────────────


def test_python_ext_survives_serialization_roundtrip() -> None:
    snap = _ext_snapshot(
        "2.0", ["PyList_New", "PyType_GetName", "_PyObject_LookupSpecial"]
    )
    back = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
    assert back.python_ext is not None
    assert back.python_ext.cpython_imports == snap.python_ext.cpython_imports
    assert back.python_ext.limited_api is True
    assert back.python_ext.module_name == "foo"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _write_snapshot(tmp_path: object, snap: AbiSnapshot) -> str:
    path = f"{tmp_path}/ext.abi.json"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(snapshot_to_json(snap))
    return path


def _scan_abi3(path: str, *args: str) -> object:
    """Invoke ``scan --binary <path> --depth binary`` with extra args."""
    from click.testing import CliRunner

    from abicheck.cli import main

    return CliRunner().invoke(
        main, ["scan", "--binary", path, "--depth", "binary", *args]
    )


#: Promote the audit finding to a hard gate (scan's advisory→error path).
_GATE = ("--crosscheck", "python_stable_abi_violation=error")


def test_scan_abi3_flags_above_floor(tmp_path: object) -> None:
    # PyType_GetName entered the Stable ABI in 3.11; under a 3.9 floor it is a
    # violation. Advisory by default (exit 0), and gated to exit 2 on promotion.
    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9")
    assert result.exit_code == 0, result.output
    assert "python_stable_abi_violation" in result.output

    gated = _scan_abi3(path, "--abi3", "3.9", *_GATE)
    assert gated.exit_code == 2, gated.output


def test_scan_crosscheck_rejects_compare_time_python_kinds(tmp_path: object) -> None:
    # Only the single-artifact audit finding is promotable via --crosscheck. The
    # compare-time kinds gate through compare's own verdict, so promoting them
    # here is rejected as an unknown cross-check (documented boundary).
    snap = _ext_snapshot("2.0", ["PyList_New"])
    path = _write_snapshot(tmp_path, snap)
    result = _scan_abi3(
        path, "--abi3", "3.9", "--crosscheck", "python_abi3_dropped=error"
    )
    assert result.exit_code != 0
    assert "unknown cross-check" in result.output


def test_scan_abi3_clean_passes(tmp_path: object) -> None:
    # Floor 3.12 admits PyType_GetName (added 3.11) → no findings, even gated.
    snap = _ext_snapshot("2.0", ["PyList_New", "PyType_GetName"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.12", *_GATE)
    assert result.exit_code == 0, result.output
    assert "python_stable_abi_violation" not in result.output


def test_scan_abi3_rejects_non_extension(tmp_path: object) -> None:
    # --abi3 on a plain C library is a usage error (nothing to audit).
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    snap = AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9")
    assert result.exit_code != 0
    assert "not a recognisable CPython extension" in result.output


def test_scan_abi3_flags_private_import(tmp_path: object) -> None:
    # A private _Py* import is a violation at any floor.
    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_LookupSpecial"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9", *_GATE)
    assert result.exit_code == 2, result.output
    assert "python_stable_abi_violation" in result.output


def test_scan_abi3_json_output_carries_finding(tmp_path: object) -> None:
    import json as _json

    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_LookupSpecial"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9", "--format", "json")
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    # The abi3 audit coverage row records it ran AND names the offending symbol,
    # so a CI artifact tells the user which import to fix (not just a count).
    rows = {row.get("layer"): row for row in data.get("coverage", [])}
    assert "abi3_audit" in rows
    assert "_PyObject_LookupSpecial" in rows["abi3_audit"]["detail"]


def test_scan_abi3_text_report_names_offending_symbol(tmp_path: object) -> None:
    snap = _ext_snapshot("2.0", ["PyList_New", "_PyObject_LookupSpecial"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9")
    assert result.exit_code == 0, result.output
    # The specific non-stable import is visible in the human report, not hidden
    # behind a bare `python_stable_abi_violation: 1` count.
    assert "_PyObject_LookupSpecial" in result.output


def test_scan_abi3_invalid_floor(tmp_path: object) -> None:
    snap = _ext_snapshot("2.0", ["PyList_New"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "not-a-version")
    assert result.exit_code != 0
    assert "invalid --abi3" in result.output


def test_scan_abi3_flags_version_specific_artifact(tmp_path: object) -> None:
    # `scan --abi3 3.9` on a version-specific `foo.cpython-311.so` must not
    # certify it clean: the SOABI tag itself pins it to 3.11, so it cannot
    # satisfy the abi3 floor no matter how stable its imports are.
    src = "foo.cpython-311-x86_64-linux-gnu.so"
    snap = _ext_snapshot("1.0", ["PyList_New"], source_path=src, library=src)
    assert snap.python_ext.is_version_specific is True
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9")
    assert "python_stable_abi_violation" in result.output
    assert "cpython-311" in result.output
    # And it gates when promoted.
    gated = _scan_abi3(path, "--abi3", "3.9", *_GATE)
    assert gated.exit_code == 2, gated.output


def test_audit_abi3_tagged_artifact_not_version_specific() -> None:
    # A proper `.abi3.` build is not version-specific and audits clean.
    from abicheck.diff_python import audit_stable_abi_imports

    snap = _ext_snapshot("1.0", ["PyList_New"], source_path="foo.abi3.so")
    assert snap.python_ext.is_version_specific is False
    assert audit_stable_abi_imports(snap.python_ext, (3, 9)) == []


def test_scan_abi3_flags_unknown_public_symbol(tmp_path: object) -> None:
    # A public Py* symbol absent from the authoritative Stable-ABI set
    # (PyUnicode_AsUTF8 — public but never Limited API) is a violation.
    snap = _ext_snapshot("2.0", ["PyList_New", "PyUnicode_AsUTF8"])
    path = _write_snapshot(tmp_path, snap)

    result = _scan_abi3(path, "--abi3", "3.9")
    assert result.exit_code == 0, result.output
    assert "python_stable_abi_violation" in result.output


def test_unknown_public_import_flagged_in_compare() -> None:
    # A newly-gained public non-Limited-API import in an abi3 module is flagged.
    old = _ext_snapshot("1.0", ["PyList_New"])
    new = _ext_snapshot("2.0", ["PyList_New", "PyUnicode_AsUTF8"])
    result = compare(old, new)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in _kinds(result)


def test_audit_stable_abi_imports_helper() -> None:
    # The shared single-artifact audit returns one aggregated finding per class
    # of violation (private + above-floor here).
    from abicheck.diff_python import audit_stable_abi_imports
    from abicheck.python_ext import PythonExtMetadata

    meta = PythonExtMetadata(
        module_name="foo",
        cpython_imports=["PyList_New", "_PyObject_LookupSpecial", "PyType_GetName"],
    )
    findings = audit_stable_abi_imports(meta, (3, 9))
    assert findings
    assert all(
        f.kind is ChangeKind.PYTHON_STABLE_ABI_VIOLATION for f in findings
    )
    joined = " ".join(str(f.new_value) for f in findings)
    assert "_PyObject_LookupSpecial" in joined
    assert "PyType_GetName" in joined
    # A clean floor (3.12) leaves only... nothing above floor, no private → empty.
    clean = audit_stable_abi_imports(
        PythonExtMetadata(module_name="foo", cpython_imports=["PyList_New"]), (3, 12)
    )
    assert clean == []


# ── Cross-platform detection (PE / Mach-O) ──────────────────────────────────


def test_detect_extension_from_pe_imports() -> None:
    from abicheck.pe_metadata import PeExport, PeMetadata

    pe = PeMetadata()
    pe.exports = [PeExport(name="PyInit_foo")]
    pe.imports = {
        "python312.dll": ["PyList_New", "_PyObject_LookupSpecial"],
        "kernel32.dll": ["Sleep"],
    }
    snap = AbiSnapshot(library="foo.cp312-win_amd64.pyd", version="1.0", pe=pe)
    meta = detect_python_extension(snap)
    assert meta is not None
    assert meta.module_name == "foo"
    assert meta.cpython_imports == ["PyList_New", "_PyObject_LookupSpecial"]
    assert meta.declared_abi3 == (3, 12)


def _pe_ext(dll: str, src: str = "foo.abi3.pyd") -> AbiSnapshot:
    from abicheck.pe_metadata import PeExport, PeMetadata

    pe = PeMetadata()
    pe.exports = [PeExport(name="PyInit_foo")]
    pe.imports = {dll: ["PyList_New", "PyLong_FromLong"], "kernel32.dll": ["Sleep"]}
    snap = AbiSnapshot(library=src, version="1.0", pe=pe, source_path=src)
    snap.python_ext = detect_python_extension(snap)
    return snap


def test_pe_captures_cpython_provider_dll() -> None:
    snap = _pe_ext("python311.dll")
    assert snap.python_ext is not None
    assert snap.python_ext.cpython_dlls == ["python311.dll"]
    assert snap.python_ext.version_specific_python_dlls == ["python311.dll"]


def test_pe_stable_python3_dll_is_not_version_specific() -> None:
    snap = _pe_ext("python3.dll")
    assert snap.python_ext is not None
    assert snap.python_ext.version_specific_python_dlls == []
    # Case-insensitive: Windows import names vary in case.
    assert _pe_ext("PYTHON3.DLL").python_ext.version_specific_python_dlls == []


@pytest.mark.parametrize(
    "dll",
    [
        "python311.dll",  # numbered
        "python313t.dll",  # free-threaded
        "python311_d.dll",  # debug
        "python315.dll",
    ],
)
def test_pe_suffixed_python_dll_is_version_specific(dll: str) -> None:
    # Anything but exactly python3.dll pins the module to one interpreter ABI.
    snap = _pe_ext(dll)
    assert snap.python_ext is not None
    assert snap.python_ext.version_specific_python_dlls == [dll]


def test_abi3_pyd_linking_versioned_dll_is_a_violation() -> None:
    # An abi3-tagged .pyd that links python311.dll cannot load on another minor,
    # even though PyList_New/PyLong_FromLong are stable symbol names.
    from abicheck.diff_python import audit_stable_abi_imports

    snap = _pe_ext("python311.dll")
    findings = audit_stable_abi_imports(snap.python_ext, (3, 9))
    assert findings
    assert any("python311.dll" in str(f.new_value) for f in findings)
    # The version-neutral python3.dll audits clean.
    clean = audit_stable_abi_imports(_pe_ext("python3.dll").python_ext, (3, 9))
    assert clean == []


def test_compare_flags_switch_to_versioned_python_dll() -> None:
    old = _pe_ext("python3.dll")
    new = _pe_ext("python311.dll")
    result = compare(old, new)
    kinds = _kinds(result)
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION in kinds


def test_pe_third_party_py_prefixed_dll_excluded() -> None:
    from abicheck.pe_metadata import PeExport, PeMetadata

    # numpy.dll exports PyArray_*/PyUFunc_* (the Py C-API convention) but is NOT
    # the CPython runtime — its symbols must not be judged against the Limited API.
    pe = PeMetadata()
    pe.exports = [PeExport(name="PyInit_foo")]
    pe.imports = {
        "python3.dll": ["PyList_New", "PyLong_FromLong"],
        "numpy.dll": ["PyArray_New", "PyUFunc_FromFuncAndData"],
        "kernel32.dll": ["Sleep"],
    }
    snap = AbiSnapshot(library="foo.abi3.pyd", version="1.0", pe=pe)
    meta = detect_python_extension(snap)
    assert meta is not None
    # Only the CPython-DLL symbols are captured; numpy's Py* symbols are excluded.
    assert meta.cpython_imports == ["PyList_New", "PyLong_FromLong"]
    assert "numpy.dll" not in meta.cpython_dlls
    assert meta.cpython_dlls == ["python3.dll"]


def test_pe_third_party_py_import_not_a_stable_abi_violation() -> None:
    from abicheck.pe_metadata import PeExport, PeMetadata

    pe = PeMetadata()
    pe.exports = [PeExport(name="PyInit_foo")]
    pe.imports = {
        "python3.dll": ["PyList_New"],
        "numpy.dll": ["PyArray_New"],
    }
    snap = AbiSnapshot(library="foo.abi3.pyd", version="1.0", pe=pe)
    snap.python_ext = detect_python_extension(snap)
    # Audit via the shared helper: numpy's PyArray_New must NOT be flagged.
    from abicheck.diff_python import audit_stable_abi_imports

    findings = audit_stable_abi_imports(snap.python_ext, (3, 9))
    assert findings == []


def test_versioned_dll_roundtrips_through_serialization() -> None:
    from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

    snap = _pe_ext("python311.dll")
    restored = snapshot_from_dict(snapshot_to_dict(snap))
    assert restored.python_ext is not None
    assert restored.python_ext.cpython_dlls == ["python311.dll"]


def test_detect_extension_from_macho_imports() -> None:
    from abicheck.macho_metadata import MachoExport, MachoMetadata

    macho = MachoMetadata()
    macho.exports = [MachoExport(name="PyInit_foo")]
    macho.imported_symbols = ["PyList_New", "_PyObject_LookupSpecial", "malloc"]
    snap = AbiSnapshot(library="foo.abi3.so", version="1.0", macho=macho)
    meta = detect_python_extension(snap)
    assert meta is not None
    assert meta.cpython_imports == ["PyList_New", "_PyObject_LookupSpecial"]
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
    new = _ext_snapshot("2.0", ["PyList_New", "_PyThreadState_UncheckedGet"], init=None)
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
        "imports": [
            {"name": "_PyObject_LookupSpecial", "binding": "global", "sym_type": "func"}
        ],
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
    assert "_PyObject_LookupSpecial" in snap.python_ext.cpython_imports


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

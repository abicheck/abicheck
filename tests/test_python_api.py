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

"""Tests for Python-level API diffing of extension modules (G23).

Covers: static ``.pyi`` extraction (parameter kinds, defaults, annotations,
``self``/``cls`` dropping, staticmethod handling, public-name filtering), stub
discovery next to a binary, ``detect_python_api`` snapshot attach, serialization
round-trip, the compare-time detector for every emitted ``ChangeKind``, and the
headline scenario — a Python-API break caught while the native C-ABI check
scores the pair compatible.
"""

from __future__ import annotations

import json

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
from abicheck.python_api import (
    KEYWORD_ONLY,
    POSITIONAL_ONLY,
    POSITIONAL_OR_KEYWORD,
    VAR_KEYWORD,
    VAR_POSITIONAL,
    PythonApiSurface,
    detect_python_api,
    surface_from_stub_file,
    surface_from_stub_source,
)
from abicheck.python_ext import detect_python_extension
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── Helpers ──────────────────────────────────────────────────────────────────


def _snap(version: str, stub: str, *, module: str = "foo") -> AbiSnapshot:
    """A snapshot carrying only a Python API surface parsed from *stub*."""
    snap = AbiSnapshot(library=f"{module}.abi3.so", version=version)
    snap.python_api = surface_from_stub_source(stub, module_name=module)
    return snap


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


def _diff_kinds(old_stub: str, new_stub: str) -> set[ChangeKind]:
    return _kinds(compare(_snap("1", old_stub), _snap("2", new_stub)))


def _ext_snapshot(so_path, module: str = "foo") -> AbiSnapshot:
    """A snapshot recognised as a CPython extension (has a ``PyInit_`` export)."""
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name=f"PyInit_{module}",
            binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.FUNC,
        )
    ]
    snap = AbiSnapshot(
        library=str(so_path.name), version="1", elf=elf, source_path=str(so_path)
    )
    snap.python_ext = detect_python_extension(snap)
    return snap


# ── Extraction ───────────────────────────────────────────────────────────────


def test_extract_top_level_functions_and_classes() -> None:
    surface = surface_from_stub_source(
        "def a(): ...\nclass B:\n    def m(self): ...\n",
        module_name="foo",
    )
    assert set(surface.functions) == {"a"}
    assert set(surface.classes) == {"B"}
    assert set(surface.classes["B"].methods) == {"m"}
    assert surface.module_name == "foo"
    assert surface.source == "stub"


def test_private_names_excluded_but_dunders_kept() -> None:
    surface = surface_from_stub_source(
        "def _hidden(): ...\n"
        "def public(): ...\n"
        "class _Priv: ...\n"
        "class Widget:\n"
        "    def __init__(self): ...\n"
        "    def _helper(self): ...\n"
        "    def api(self): ...\n",
        module_name="foo",
    )
    assert set(surface.functions) == {"public"}
    assert set(surface.classes) == {"Widget"}
    assert set(surface.classes["Widget"].methods) == {"__init__", "api"}


def test_parameter_kinds_defaults_and_annotations() -> None:
    surface = surface_from_stub_source(
        "def f(a, b, /, c, d=1, *args, e, g=2, **kw) -> int: ...\n",
        module_name="foo",
    )
    params = surface.functions["f"].parameters
    by_name = {p.name: p for p in params}
    assert by_name["a"].kind == POSITIONAL_ONLY
    assert by_name["b"].kind == POSITIONAL_ONLY
    assert by_name["c"].kind == POSITIONAL_OR_KEYWORD
    assert by_name["d"].kind == POSITIONAL_OR_KEYWORD and by_name["d"].has_default
    assert by_name["args"].kind == VAR_POSITIONAL
    assert by_name["e"].kind == KEYWORD_ONLY and not by_name["e"].has_default
    assert by_name["g"].kind == KEYWORD_ONLY and by_name["g"].has_default
    assert by_name["kw"].kind == VAR_KEYWORD
    assert surface.functions["f"].return_annotation == "int"


def test_self_dropped_but_staticmethod_keeps_all_params() -> None:
    surface = surface_from_stub_source(
        "class W:\n"
        "    def method(self, x): ...\n"
        "    @staticmethod\n"
        "    def make(x): ...\n"
        "    @classmethod\n"
        "    def build(cls, x): ...\n",
        module_name="foo",
    )
    methods = surface.classes["W"].methods
    assert [p.name for p in methods["method"].parameters] == ["x"]
    assert [p.name for p in methods["make"].parameters] == ["x"]
    assert [p.name for p in methods["build"].parameters] == ["x"]


def test_annotation_only_parameter_has_no_annotation() -> None:
    surface = surface_from_stub_source("def f(x): ...\n", module_name="foo")
    assert surface.functions["f"].parameters[0].annotation is None


def test_malformed_stub_marks_parse_failure() -> None:
    surface = surface_from_stub_source("def f(:\n", module_name="foo")
    assert surface.is_empty and surface.parse_ok is False


def test_clean_empty_stub_is_parse_ok() -> None:
    surface = surface_from_stub_source("def _priv(): ...\n", module_name="foo")
    assert surface.is_empty and surface.parse_ok is True


def test_detect_skips_malformed_stub(tmp_path) -> None:
    # A recognised extension whose stub has a syntax error → unrecoverable →
    # None (so the diff does not read every old name as removed).
    (tmp_path / "foo.pyi").write_text("def f(:\n", encoding="utf-8")
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    assert detect_python_api(_ext_snapshot(so)) is None


def test_overload_widened_with_optional_param_is_compatible() -> None:
    # One overload gains a compatible optional parameter while another keeps the
    # name overloaded — the old call shape still works, so no removal.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, y: int = ...) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in _diff_kinds(old, new)


def test_overload_dropping_optional_param_is_breaking() -> None:
    # Removing an optional parameter from a variant drops a supported call shape
    # (`f(int, int)`), so it is a removal — the flip side of allowing additions.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, y: int = ...) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED in _diff_kinds(old, new)


def test_overload_dropping_varargs_is_breaking() -> None:
    # One overload loses *args while another keeps the name overloaded — the
    # extra-positional call shape is gone, so it is a removal (not silent).
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, *args: str) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED in _diff_kinds(old, new)


def test_overload_positional_only_rename_is_not_a_removal() -> None:
    # A positional-only slot renamed on one variant (`a` → `b`) is invisible to
    # callers (they bind by position), so the variant is still matched — it must
    # not be mis-reported as a removed overload just because the source name of
    # the slot changed.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(a: int, /) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(b: int, /) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in _diff_kinds(old, new)


def test_overload_optional_inserted_before_existing_is_a_binding_shift() -> None:
    # An optional parameter inserted *before* an existing one on a variant keeps
    # the optional set a superset (so the variant still "covers" the old call),
    # but it rebinds positional arguments: `f(1, 2)` bound `x=1, y=2`, now binds
    # `x=1, z=2`. The matched-variant diff must surface that as a kind change.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, y: int = ...) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, z: int = ..., y: int = ...) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED in kinds
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in kinds


def test_overload_varargs_annotation_change_is_a_type_change() -> None:
    # A surviving `*args` collector whose element type changed on a variant is a
    # type-contract RISK, not a removed overload — the matched-variant diff must
    # report it rather than silently treating the variant as covered.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, *args: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int, *args: str) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED in kinds
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in kinds


# ── Stub discovery + attach ──────────────────────────────────────────────────


def test_find_stub_and_detect_from_snapshot(tmp_path) -> None:
    (tmp_path / "foo.pyi").write_text("def go(x): ...\n", encoding="utf-8")
    so = tmp_path / "foo.cpython-311-x86_64-linux-gnu.so"
    so.write_bytes(b"\x7fELF")
    surface = detect_python_api(_ext_snapshot(so))
    assert surface is not None
    assert set(surface.functions) == {"go"}
    assert surface.source_path == str(tmp_path / "foo.pyi")


def test_detect_returns_none_for_non_extension_with_sibling_pyi(tmp_path) -> None:
    # A plain native library that merely has an unrelated `.pyi` sibling must
    # NOT be handed a Python API surface (it exports no PyInit_ module).
    (tmp_path / "libfoo.pyi").write_text("def go(): ...\n", encoding="utf-8")
    so = tmp_path / "libfoo.so"
    so.write_bytes(b"\x7fELF")
    snap = AbiSnapshot(library=so.name, version="1", source_path=str(so))
    assert snap.python_ext is None
    assert detect_python_api(snap) is None


def test_detect_returns_none_for_embedding_host_with_sibling_pyi(tmp_path) -> None:
    # An *embedding host* imports Py* C-API symbols (so `is_extension` is true)
    # but exports NO `PyInit_*` — it is not importable as a module. Pairing it
    # with a sibling `.pyi` must NOT yield a Python API surface: the module-init
    # export, not merely CPython imports, is what makes the stub's surface real.
    (tmp_path / "libhost.pyi").write_text("def go(): ...\n", encoding="utf-8")
    so = tmp_path / "libhost.so"
    so.write_bytes(b"\x7fELF")
    elf = ElfMetadata()
    elf.imports = [
        ElfImport(
            name="Py_Initialize",
            binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.FUNC,
        )
    ]
    snap = AbiSnapshot(library=so.name, version="1", elf=elf, source_path=str(so))
    snap.python_ext = detect_python_extension(snap)
    # It reads as an extension (imports Py*), yet has no init export …
    assert snap.python_ext is not None
    assert snap.python_ext.is_extension
    assert snap.python_ext.init_symbol is None
    # … so no Python API surface is recovered.
    assert detect_python_api(snap) is None


def test_detect_uses_module_name_from_python_ext(tmp_path) -> None:
    (tmp_path / "mymod.pyi").write_text("def go(): ...\n", encoding="utf-8")
    so = tmp_path / "mymod.cpython-312-x86_64-linux-gnu.so"
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name="PyInit_mymod",
            binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.FUNC,
        )
    ]
    snap = AbiSnapshot(library=so.name, version="1", elf=elf, source_path=str(so))
    snap.python_ext = detect_python_extension(snap)
    surface = detect_python_api(snap)
    assert surface is not None and surface.module_name == "mymod"


def test_detect_returns_none_without_stub(tmp_path) -> None:
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    assert detect_python_api(_ext_snapshot(so)) is None


def test_detect_returns_none_without_source_path() -> None:
    assert detect_python_api(AbiSnapshot(library="foo.so", version="1")) is None


def test_present_but_empty_stub_yields_empty_surface(tmp_path) -> None:
    # A *present* stub with only private names yields an (empty) surface, NOT
    # None — None is reserved for "no stub". This keeps removals diffable when a
    # later version deletes its last public name.
    (tmp_path / "foo.pyi").write_text("def _hidden(): ...\n", encoding="utf-8")
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    surface = detect_python_api(_ext_snapshot(so))
    assert surface is not None and surface.is_empty


def test_deleting_last_public_function_is_reported(tmp_path) -> None:
    # old ships a public `go`; new's stub drops it (only a private helper left).
    # The removal must be reported, not swallowed as NO_CHANGE.
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    for d, body in ((old_dir, "def go(x): ...\n"), (new_dir, "def _priv(): ...\n")):
        d.mkdir()
        (d / "foo.pyi").write_text(body, encoding="utf-8")
        (d / "foo.abi3.so").write_bytes(b"\x7fELF")
    old = _ext_snapshot(old_dir / "foo.abi3.so")
    old.python_api = detect_python_api(old)
    new = _ext_snapshot(new_dir / "foo.abi3.so")
    new.python_api = detect_python_api(new)
    assert new.python_api is not None and new.python_api.is_empty
    assert ChangeKind.PYTHON_API_FUNCTION_REMOVED in _kinds(compare(old, new))


def test_find_stub_dash_stubs_package(tmp_path) -> None:
    stubs = tmp_path / "foo-stubs"
    stubs.mkdir()
    (stubs / "__init__.pyi").write_text("def go(): ...\n", encoding="utf-8")
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    surface = detect_python_api(_ext_snapshot(so))
    assert surface is not None and set(surface.functions) == {"go"}


def test_find_stub_package_init(tmp_path) -> None:
    pkg = tmp_path / "foo"
    pkg.mkdir()
    (pkg / "__init__.pyi").write_text("def go(): ...\n", encoding="utf-8")
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    surface = detect_python_api(_ext_snapshot(so))
    assert surface is not None and set(surface.functions) == {"go"}


def test_service_attach_python_api_surface(tmp_path) -> None:
    from abicheck.service import _try_attach_python_api_surface

    (tmp_path / "foo.pyi").write_text("def go(x): ...\n", encoding="utf-8")
    so = tmp_path / "foo.cpython-311-x86_64-linux-gnu.so"
    snap = _ext_snapshot(so)
    _try_attach_python_api_surface(snap)
    assert snap.python_api is not None
    assert set(snap.python_api.functions) == {"go"}


def test_service_attach_is_noop_without_stub(tmp_path) -> None:
    from abicheck.service import _try_attach_python_api_surface

    so = tmp_path / "foo.abi3.so"
    snap = _ext_snapshot(so)  # a real extension, but no sibling .pyi
    _try_attach_python_api_surface(snap)
    assert snap.python_api is None


def test_surface_from_stub_file(tmp_path) -> None:
    p = tmp_path / "foo.pyi"
    p.write_text("def go(): ...\n", encoding="utf-8")
    surface = surface_from_stub_file(p, module_name="foo")
    assert set(surface.functions) == {"go"}
    assert surface.source_path == str(p)


# ── Serialization round-trip ─────────────────────────────────────────────────


def test_serialization_round_trip() -> None:
    stub = (
        "def transform(data, *, encoding: str = 'utf-8') -> bytes: ...\n"
        "class Widget:\n"
        "    def __init__(self, name: str, size: int = 10) -> None: ...\n"
    )
    snap = _snap("1", stub)
    snap.python_api.source_path = "/x/foo.pyi"  # type: ignore[union-attr]
    restored = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
    pa = restored.python_api
    assert pa is not None
    assert pa.module_name == "foo" and pa.source == "stub"
    assert pa.source_path == "/x/foo.pyi"
    tf = pa.functions["transform"]
    assert tf.return_annotation == "bytes"
    enc = {p.name: p for p in tf.parameters}["encoding"]
    assert enc.kind == KEYWORD_ONLY and enc.has_default and enc.annotation == "str"
    init = pa.classes["Widget"].methods["__init__"]
    assert {p.name: p.has_default for p in init.parameters} == {
        "name": False,
        "size": True,
    }


# ── Detector: each ChangeKind ────────────────────────────────────────────────


def test_function_removed_and_added() -> None:
    kinds = _diff_kinds("def a(): ...\ndef b(): ...\n", "def a(): ...\ndef c(): ...\n")
    assert ChangeKind.PYTHON_API_FUNCTION_REMOVED in kinds
    assert ChangeKind.PYTHON_API_FUNCTION_ADDED in kinds


def test_class_removed_and_added() -> None:
    kinds = _diff_kinds("class A: ...\nclass B: ...\n", "class A: ...\nclass C: ...\n")
    assert ChangeKind.PYTHON_API_CLASS_REMOVED in kinds
    assert ChangeKind.PYTHON_API_CLASS_ADDED in kinds


def test_method_removed_and_added() -> None:
    old = "class A:\n    def keep(self): ...\n    def drop(self): ...\n"
    new = "class A:\n    def keep(self): ...\n    def fresh(self): ...\n"
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_METHOD_REMOVED in kinds
    assert ChangeKind.PYTHON_API_METHOD_ADDED in kinds


def test_parameter_removed() -> None:
    kinds = _diff_kinds("def f(a, b, c): ...\n", "def f(a, c): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED in kinds


def test_required_parameter_added_is_breaking() -> None:
    kinds = _diff_kinds("def f(a): ...\n", "def f(a, b, c): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_ADDED in kinds


def test_optional_parameter_added_is_not_flagged() -> None:
    kinds = _diff_kinds("def f(a): ...\n", "def f(a, b=1): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_ADDED not in kinds
    # Adding an optional argument is backward compatible — no findings at all.
    assert not kinds


def test_parameter_renamed() -> None:
    kinds = _diff_kinds(
        "def transform(data, *, encoding='utf-8'): ...\n",
        "def transform(data, codec): ...\n",
    )
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED in kinds
    # Reported as a rename, not a remove + add pair.
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED not in kinds
    assert ChangeKind.PYTHON_API_PARAMETER_ADDED not in kinds


def test_default_removed() -> None:
    kinds = _diff_kinds("def f(a, b=1): ...\n", "def f(a, b): ...\n")
    assert ChangeKind.PYTHON_API_DEFAULT_REMOVED in kinds


def test_default_added_is_not_flagged() -> None:
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(a, b=1): ...\n")
    assert ChangeKind.PYTHON_API_DEFAULT_REMOVED not in kinds


def test_parameter_type_changed() -> None:
    kinds = _diff_kinds("def f(a: int): ...\n", "def f(a: str): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED in kinds


def test_adding_annotation_is_not_a_type_change() -> None:
    kinds = _diff_kinds("def f(a): ...\n", "def f(a: int): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED not in kinds


def test_return_type_changed() -> None:
    kinds = _diff_kinds("def f() -> int: ...\n", "def f() -> str: ...\n")
    assert ChangeKind.PYTHON_API_RETURN_TYPE_CHANGED in kinds


def test_method_signature_changes_are_detected() -> None:
    old = "class A:\n    def m(self, x, y=1): ...\n"
    new = "class A:\n    def m(self, x, y): ...\n"
    kinds = _diff_kinds(old, new)
    # `self` is dropped on both sides, so `y` losing its default is the only
    # signature change — proving method signatures are diffed, not just presence.
    assert ChangeKind.PYTHON_API_DEFAULT_REMOVED in kinds


# ── Order- and kind-aware signature diff ────────────────────────────────────


def test_positional_made_keyword_only_is_breaking() -> None:
    # def f(a, b) -> def f(a, *, b): positional callers `f(1, 2)` break, even
    # though the parameter *names* are unchanged.
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(a, *, b): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED in kinds


def test_keyword_made_positional_only_is_breaking() -> None:
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(a, b, /): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED in kinds


def test_positional_reorder_is_breaking() -> None:
    result = compare(_snap("1", "def f(a, b): ...\n"), _snap("2", "def f(b, a): ...\n"))
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_optional_parameter_inserted_before_existing_is_breaking() -> None:
    # Inserting an optional `x` before `b` shifts `b`'s position, so `f(1, 2)`
    # now binds 2 -> x, not b. A positional break the name-set view misses.
    kinds = _diff_kinds("def f(a, b=1): ...\n", "def f(a, x=1, b=1): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED in kinds


def test_optional_parameter_appended_is_compatible() -> None:
    # Appending an optional parameter at the end keeps the positional prefix,
    # so it is backward compatible — no binding finding.
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(a, b, c=1): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED not in kinds


def test_positional_only_widened_is_compatible() -> None:
    # positional-only -> positional-or-keyword *gains* a calling mode; compatible.
    kinds = _diff_kinds("def f(a, /, b): ...\n", "def f(a, b): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED not in kinds


def test_trailing_positional_removal_is_not_double_reported() -> None:
    kinds = _diff_kinds("def f(a, b, c): ...\n", "def f(a, b): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED in kinds
    # The removal is not also reported as a positional-order change.
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED not in kinds


def test_positional_only_rename_is_compatible() -> None:
    # A positional-only parameter can't be passed by keyword, so renaming it
    # (same position) is invisible to callers — not a break.
    kinds = _diff_kinds("def f(a, /): ...\n", "def f(b, /): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED not in kinds
    assert not kinds


def test_positional_only_rename_dropping_default_is_breaking() -> None:
    # `def f(a=1, /)` → `def f(b, /)`: the positional-only name change is
    # invisible, but the slot lost its default, so a no-arg caller now breaks.
    kinds = _diff_kinds("def f(a=1, /): ...\n", "def f(b, /): ...\n")
    assert ChangeKind.PYTHON_API_DEFAULT_REMOVED in kinds
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED not in kinds


def test_positional_or_keyword_rename_is_still_breaking() -> None:
    kinds = _diff_kinds("def f(a): ...\n", "def f(b): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED in kinds


def test_keyword_only_rename_is_still_breaking() -> None:
    kinds = _diff_kinds("def f(*, a): ...\n", "def f(*, b): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED in kinds


def test_positionally_misaligned_drop_add_is_not_a_rename() -> None:
    # `f(a, b)` → `f(b, c)`: `a` is dropped and `c` is added at *different*
    # positions (b shifts), so it is a removal + addition + reorder, not a
    # phantom `a`→`c` rename.
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(b, c): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED not in kinds
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED in kinds


def test_same_position_rename_is_not_reported_as_reorder() -> None:
    # A single rename at the same position is a rename, not a reorder.
    kinds = _diff_kinds("def f(a, b): ...\n", "def f(a, c): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED in kinds
    assert ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED not in kinds


def test_variadic_params_do_not_confuse_the_diff() -> None:
    # *args / **kwargs are not named arguments; keeping them unchanged is not a
    # named-parameter finding.
    kinds = _diff_kinds("def f(a, *args, **kw): ...\n", "def f(a, *args, **kw): ...\n")
    assert not kinds


def test_dropping_var_positional_is_breaking() -> None:
    kinds = _diff_kinds("def f(a, *args): ...\n", "def f(a): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED in kinds


def test_dropping_var_keyword_is_breaking() -> None:
    kinds = _diff_kinds("def f(a, **kw): ...\n", "def f(a): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_REMOVED in kinds


def test_adding_var_positional_is_compatible() -> None:
    # Gaining *args is more permissive — callers are unaffected.
    kinds = _diff_kinds("def f(a): ...\n", "def f(a, *args): ...\n")
    assert not kinds


def test_var_positional_annotation_change_is_risk() -> None:
    kinds = _diff_kinds("def f(*args: int): ...\n", "def f(*args: str): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED in kinds


def test_var_keyword_annotation_change_is_risk() -> None:
    kinds = _diff_kinds("def f(**kw: int): ...\n", "def f(**kw: str): ...\n")
    assert ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED in kinds


# ── Callable protocol: async / descriptor kind ──────────────────────────────


def test_async_added_is_breaking() -> None:
    kinds = _diff_kinds("def f(x): ...\n", "async def f(x): ...\n")
    assert ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED in kinds


def test_async_removed_is_breaking() -> None:
    kinds = _diff_kinds("async def f(x): ...\n", "def f(x): ...\n")
    assert ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED in kinds


def test_property_to_method_is_breaking() -> None:
    old = "class A:\n    @property\n    def x(self) -> int: ...\n"
    new = "class A:\n    def x(self) -> int: ...\n"
    result = compare(_snap("1", old), _snap("2", new))
    assert ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_staticmethod_to_instance_is_breaking() -> None:
    old = "class A:\n    @staticmethod\n    def m(a): ...\n"
    new = "class A:\n    def m(self, a): ...\n"
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED in kinds


def test_classmethod_recorded_and_cls_dropped() -> None:
    surface = surface_from_stub_source(
        "class A:\n    @classmethod\n    def build(cls, x): ...\n", module_name="m"
    )
    build = surface.classes["A"].methods["build"]
    assert build.descriptor == "class"
    assert [p.name for p in build.parameters] == ["x"]


def test_async_recorded_on_extraction() -> None:
    surface = surface_from_stub_source("async def f(): ...\n", module_name="m")
    assert surface.functions["f"].is_async is True


# ── Overloads ────────────────────────────────────────────────────────────────

_OV_TWO = (
    "from typing import overload\n"
    "@overload\n"
    "def f(x: int) -> int: ...\n"
    "@overload\n"
    "def f(x: str) -> str: ...\n"
)
_OV_ONE = "from typing import overload\n@overload\ndef f(x: str) -> str: ...\n"


def test_overloads_are_all_retained() -> None:
    surface = surface_from_stub_source(_OV_TWO, module_name="m")
    assert len(surface.functions["f"].overloads) == 2


def test_overload_removed_is_breaking() -> None:
    result = compare(_snap("1", _OV_TWO), _snap("2", _OV_ONE))
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_overload_added_is_compatible() -> None:
    kinds = _diff_kinds(_OV_ONE, _OV_TWO)
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in kinds


def test_unchanged_overloads_report_nothing() -> None:
    kinds = _diff_kinds(_OV_TWO, _OV_TWO)
    assert not kinds


def test_overload_return_only_change_is_risk_not_removal() -> None:
    # A return-only change on a matched overload variant is a RISK (return-type
    # change), NOT an API_BREAK overload removal — the input call shape survives.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> bytes: ...\n"  # return int -> bytes
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_RETURN_TYPE_CHANGED in kinds
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED not in kinds


def test_overload_param_type_change_is_removal() -> None:
    # A *parameter*-type change drops a supported input call shape → removal.
    old = (
        "from typing import overload\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    new = (
        "from typing import overload\n"
        "@overload\ndef f(x: bytes) -> int: ...\n"  # int input shape dropped
        "@overload\ndef f(x: str) -> str: ...\n"
    )
    kinds = _diff_kinds(old, new)
    assert ChangeKind.PYTHON_API_OVERLOAD_REMOVED in kinds


def test_overloads_round_trip_serialization() -> None:
    snap = _snap("1", _OV_TWO)
    restored = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
    pa = restored.python_api
    assert pa is not None
    assert len(pa.functions["f"].overloads) == 2
    assert pa.functions["f"].overloads[0].return_annotation == "int"


# ── Verdict + interaction with the native-ABI check ─────────────────────────


def test_python_api_break_verdict_is_api_break() -> None:
    result = compare(_snap("1", "def f(a): ...\n"), _snap("2", "def g(a): ...\n"))
    assert ChangeKind.PYTHON_API_FUNCTION_REMOVED in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_break_invisible_to_c_abi_is_caught() -> None:
    """C-ABI-identical modules whose only change is a renamed kwarg.

    Both snapshots have the same ELF export/import table and the same
    ``python_ext`` surface, so the native-ABI (G14) detector is silent — the
    break lives purely in the Python signature and is caught only by G23.
    """
    elf = ElfMetadata()
    elf.symbols = [
        ElfSymbol(
            name="PyInit_foo", binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
        )
    ]

    def build(version: str, stub: str) -> AbiSnapshot:
        snap = AbiSnapshot(
            library="foo.abi3.so",
            version=version,
            elf=ElfMetadata(symbols=list(elf.symbols), imports=list(elf.imports)),
            source_path="foo.abi3.so",
        )
        snap.python_ext = detect_python_extension(snap)
        snap.python_api = surface_from_stub_source(stub, module_name="foo")
        return snap

    old = build("1", "def transform(data, *, encoding='utf-8'): ...\n")
    new = build("2", "def transform(data, codec): ...\n")
    result = compare(old, new)
    # G14 native-ABI check sees nothing (identical imports/exports)…
    assert ChangeKind.PYTHON_STABLE_ABI_VIOLATION not in _kinds(result)
    # …but the Python-API rename is caught.
    assert ChangeKind.PYTHON_API_PARAMETER_RENAMED in _kinds(result)
    assert result.verdict == Verdict.API_BREAK


def test_detector_skipped_when_new_has_no_surface() -> None:
    old = _snap("1", "def f(a): ...\n")
    new = AbiSnapshot(library="foo.abi3.so", version="2")  # no python_api
    result = compare(old, new)
    assert not (_kinds(result) & {ChangeKind.PYTHON_API_FUNCTION_REMOVED})


def test_missing_old_surface_treated_as_empty_baseline() -> None:
    old = AbiSnapshot(library="foo.abi3.so", version="1")  # no python_api
    new = _snap("2", "def f(a): ...\nclass C: ...\n")
    kinds = _kinds(compare(old, new))
    assert ChangeKind.PYTHON_API_FUNCTION_ADDED in kinds
    assert ChangeKind.PYTHON_API_CLASS_ADDED in kinds


def test_identical_surface_is_no_change() -> None:
    stub = "def f(a, b=1) -> int: ...\nclass C:\n    def m(self, x): ...\n"
    result = compare(_snap("1", stub), _snap("2", stub))
    assert not _kinds(result)


def test_empty_surface_helpers() -> None:
    assert PythonApiSurface().is_empty
    assert not surface_from_stub_source("def f(): ...\n").is_empty


# ── Public-contract oracle scoping (DemoteOffPythonSurface) ──────────────────


def _ext_with_api(tmp_path, module: str = "foo"):
    """A recognised extension snapshot carrying a recovered Python API surface."""
    (tmp_path / f"{module}.pyi").write_text("def go(x): ...\n", encoding="utf-8")
    so = tmp_path / f"{module}.cpython-311-x86_64-linux-gnu.so"
    so.write_bytes(b"\x7fELF")
    snap = _ext_snapshot(so, module)
    snap.python_api = detect_python_api(snap)
    assert snap.python_api is not None
    return snap


def _run_demote(old_snap, new_snap, changes):
    from abicheck.post_processing import DemoteOffPythonSurface, PipelineContext

    ctx = PipelineContext(old=old_snap, new=new_snap, scope_to_public_surface=True)
    kept = DemoteOffPythonSurface().run(list(changes), ctx)
    return kept, ctx.out_of_surface


def _c(kind: ChangeKind, symbol: str):
    from abicheck.checker_types import Change

    return Change(kind=kind, symbol=symbol, description="x")


def test_oracle_demotes_native_internal_findings(tmp_path) -> None:
    ext = _ext_with_api(tmp_path)
    internal = _c(ChangeKind.FUNC_REMOVED, "_Z8internalv")
    type_churn = _c(ChangeKind.TYPE_SIZE_CHANGED, "detail::Impl")
    kept, demoted = _run_demote(ext, ext, [internal, type_churn])
    assert kept == []
    assert {c.kind for c in demoted} == {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.TYPE_SIZE_CHANGED,
    }
    assert all(c.surface_exclusion_reason == "off-python-surface" for c in demoted)


def test_oracle_keeps_python_and_load_and_init_findings(tmp_path) -> None:
    ext = _ext_with_api(tmp_path)
    py = _c(ChangeKind.PYTHON_API_FUNCTION_REMOVED, "python:foo.go")
    load = _c(ChangeKind.NEEDED_REMOVED, "libc.so.6")
    init = _c(ChangeKind.FUNC_REMOVED, "PyInit_foo")
    stable = _c(ChangeKind.PYTHON_STABLE_ABI_VIOLATION, "python:foo")
    kept, demoted = _run_demote(ext, ext, [py, load, init, stable])
    assert demoted == []
    assert {c.kind for c in kept} == {
        ChangeKind.PYTHON_API_FUNCTION_REMOVED,
        ChangeKind.NEEDED_REMOVED,
        ChangeKind.FUNC_REMOVED,
        ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
    }


def test_oracle_noop_without_recovered_surface(tmp_path) -> None:
    # A recognised extension with NO recovered Python surface keeps everything
    # (honest degradation — no oracle to scope against).
    so = tmp_path / "foo.abi3.so"
    so.write_bytes(b"\x7fELF")
    ext = _ext_snapshot(so)  # python_api stays None
    internal = _c(ChangeKind.FUNC_REMOVED, "_Z8internalv")
    kept, demoted = _run_demote(ext, ext, [internal])
    assert demoted == []
    assert kept == [internal]


def test_oracle_noop_when_old_side_not_an_extension(tmp_path) -> None:
    # v1 is a normal native library (no python_ext); v2 is an extension. A
    # native removal is a REAL break for the old library's C/C++ consumers and
    # must NOT be demoted just because the new artifact is an extension.
    new_ext = _ext_with_api(tmp_path)
    old_plain = AbiSnapshot(library="libfoo.so", version="1")
    internal = _c(ChangeKind.FUNC_REMOVED, "foo")
    kept, demoted = _run_demote(old_plain, new_ext, [internal])
    assert demoted == []
    assert kept == [internal]


def test_python_api_field_is_keyword_only() -> None:
    # Guards against a positional-slot shift: a caller building AbiSnapshot
    # positionally must not land its `enums` argument in `python_api`.
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(AbiSnapshot)}
    assert fields["python_api"].kw_only is True


def test_oracle_noop_for_non_extension() -> None:
    plain = AbiSnapshot(library="libfoo.so", version="1")
    internal = _c(ChangeKind.FUNC_REMOVED, "_Z8internalv")
    kept, demoted = _run_demote(plain, plain, [internal])
    assert demoted == []
    assert kept == [internal]


def test_oracle_defers_to_resolved_old_header_surface(tmp_path) -> None:
    # A hybrid extension whose OLD side had a public C header (surf_old
    # resolvable) but whose NEW side no longer resolves must NOT have its native
    # func_removed demoted — the old header proved the symbol was public.
    from types import SimpleNamespace

    from abicheck.post_processing import DemoteOffPythonSurface, PipelineContext

    ext = _ext_with_api(tmp_path)
    ctx = PipelineContext(old=ext, new=ext, scope_to_public_surface=True)
    ctx.surf_old = SimpleNamespace(resolvable=True)  # type: ignore[assignment]
    ctx.surf_new = SimpleNamespace(resolvable=False)  # type: ignore[assignment]
    internal = _c(ChangeKind.FUNC_REMOVED, "_Z8internalv")
    kept = DemoteOffPythonSurface().run([internal], ctx)
    assert kept == [internal] and ctx.out_of_surface == []


def test_oracle_disabled_when_scoping_off(tmp_path) -> None:
    from abicheck.post_processing import DemoteOffPythonSurface, PipelineContext

    ext = _ext_with_api(tmp_path)
    internal = _c(ChangeKind.FUNC_REMOVED, "_Z8internalv")
    ctx = PipelineContext(old=ext, new=ext, scope_to_public_surface=False)
    kept = DemoteOffPythonSurface().run([internal], ctx)
    assert kept == [internal] and ctx.out_of_surface == []

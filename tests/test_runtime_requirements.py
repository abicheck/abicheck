"""Load-time requirements a new ELF build places on consumers.

Bug class: a library that no uninstrumented (or no non-host) process can load
read as COMPATIBLE_WITH_RISK. Found by the abiforge execution oracle: an
``-fsanitize=address`` library aborts every uninstrumented consumer ("ASan
runtime does not come first"), and a clang ``-shared -fsanitize=undefined``
library imports ``__ubsan_*`` with nothing to provide them (exit 127).
"""

from __future__ import annotations

import pytest

from abicheck.checker import Verdict, compare
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfImport, ElfMetadata, SymbolBinding
from abicheck.model.extraction_contract import DependencyInfo

_BASE_NEEDED = ["libc.so.6"]


def _snap(
    version: str,
    *,
    needed: list[str] | None = None,
    imports: list[ElfImport] | None = None,
    deps: DependencyInfo | None = None,
) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version=version)
    snap.elf = ElfMetadata(
        machine="EM_X86_64",
        needed=list(needed if needed is not None else _BASE_NEEDED),
        imports=list(imports or [ElfImport("malloc")]),
    )
    snap.dependency_info = deps
    return snap


def _kinds(old: AbiSnapshot, new: AbiSnapshot) -> tuple[Verdict, set[str]]:
    result = compare(old, new)
    return result.verdict, {c.kind.value for c in result.changes}


@pytest.mark.parametrize(
    "runtime",
    [
        "libasan.so.8",
        "libasan.so.6",
        "libtsan.so.2",
        "libhwasan.so.0",
        "libclang_rt.asan-x86_64.so",
        "libclang_rt.tsan-x86_64.so",
        "libclang_rt.hwasan-aarch64.so",
    ],
)
def test_a_preload_runtime_dependency_breaks_every_uninstrumented_consumer(
    runtime: str,
) -> None:
    old = _snap("1")
    new = _snap("2", needed=[runtime, *_BASE_NEEDED])
    verdict, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" in kinds
    assert verdict == Verdict.BREAKING


@pytest.mark.parametrize(
    "runtime",
    ["libubsan.so.1", "libclang_rt.ubsan_standalone-x86_64.so"],
)
def test_a_runtime_that_loads_anywhere_is_not_a_load_requirement(
    runtime: str,
) -> None:
    old = _snap("1")
    new = _snap(
        "2",
        needed=[runtime, *_BASE_NEEDED],
        imports=[ElfImport("malloc"), ElfImport("__ubsan_handle_add_overflow")],
    )
    verdict, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" not in kinds
    assert verdict != Verdict.BREAKING


@pytest.mark.parametrize(
    "symbol",
    [
        "__ubsan_handle_pointer_overflow",
        "__asan_report_load4",
        "__tsan_read8",
        "__msan_warning",
        "__hwasan_load4",
        "__sanitizer_cov_trace_pc",
    ],
)
def test_sanitizer_imports_with_no_runtime_dependency_break(symbol: str) -> None:
    old = _snap("1")
    new = _snap("2", imports=[ElfImport("malloc"), ElfImport(symbol)])
    verdict, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" in kinds
    assert verdict == Verdict.BREAKING


def test_weak_or_already_present_sanitizer_imports_are_not_new_requirements() -> None:
    weak = ElfImport("__asan_default_options", binding=SymbolBinding.WEAK)
    old = _snap("1", imports=[ElfImport("malloc"), ElfImport("__ubsan_handle_x")])
    new = _snap("2", imports=[ElfImport("malloc"), ElfImport("__ubsan_handle_x"), weak])
    _, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" not in kinds


def _deps(missing: list[str]) -> DependencyInfo:
    return DependencyInfo(
        nodes=[{"path": "/b/libx.so", "soname": "libx.so", "needed": [], "depth": 0}],
        missing_symbols=[
            {"consumer": "libx.so", "symbol": m, "version": ""} for m in missing
        ],
    )


def test_a_self_contained_library_gaining_an_unprovided_import_breaks() -> None:
    old = _snap("1", deps=_deps([]))
    new = _snap(
        "2",
        imports=[ElfImport("malloc"), ElfImport("helper_v2")],
        deps=_deps(["helper_v2"]),
    )
    verdict, kinds = _kinds(old, new)
    assert "imported_symbol_unprovided" in kinds
    assert verdict == Verdict.BREAKING


@pytest.mark.parametrize(
    ("old_deps", "new_deps", "new_import"),
    [
        # No --follow-deps evidence on one side: nothing is proven.
        (None, _deps(["helper_v2"]), ElfImport("helper_v2")),
        (_deps([]), None, ElfImport("helper_v2")),
        # A plugin already relying on its host: the host may provide it too.
        (_deps(["host_api"]), _deps(["host_api", "helper_v2"]), ElfImport("helper_v2")),
        # The new import resolves against a dependency.
        (_deps([]), _deps([]), ElfImport("helper_v2")),
    ],
)
def test_unprovided_imports_need_proof(old_deps, new_deps, new_import) -> None:
    old = _snap("1", deps=old_deps)
    new = _snap("2", imports=[ElfImport("malloc"), new_import], deps=new_deps)
    _, kinds = _kinds(old, new)
    assert "imported_symbol_unprovided" not in kinds

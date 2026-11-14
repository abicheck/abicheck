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
        "__sanitizer_print_stack_trace",
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


@pytest.mark.parametrize(
    ("runtime", "symbol"),
    [
        # Each runtime defines only its own families: UBSan's runtime does not
        # define ASan's, TSan's, MSan's, HWASan's or LSan's interface.
        ("libubsan.so.1", "__asan_init"),
        ("libubsan.so.1", "__tsan_read8"),
        ("libclang_rt.ubsan_standalone-x86_64.so", "__msan_warning"),
        ("libubsan.so.1", "__hwasan_load4"),
        ("libubsan.so.1", "__lsan_do_leak_check"),
    ],
)
def test_a_runtime_does_not_provide_another_sanitizers_interface(
    runtime: str, symbol: str
) -> None:
    old = _snap("1", needed=[runtime, *_BASE_NEEDED])
    new = _snap(
        "2",
        needed=[runtime, *_BASE_NEEDED],
        imports=[ElfImport("malloc"), ElfImport(symbol)],
    )
    verdict, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" in kinds
    assert verdict == Verdict.BREAKING


@pytest.mark.parametrize(
    ("runtime", "symbol"),
    [
        ("libubsan.so.1", "__ubsan_handle_add_overflow"),
        ("libubsan.so.1", "__sanitizer_print_stack_trace"),
        ("libclang_rt.ubsan_standalone-x86_64.so", "__ubsan_handle_x"),
        ("libclang_rt.ubsan_standalone-x86_64.so", "__sanitizer_set_report_path"),
    ],
)
def test_a_runtime_provides_its_own_and_the_common_interface(
    runtime: str, symbol: str
) -> None:
    old = _snap("1", needed=[runtime, *_BASE_NEEDED])
    new = _snap(
        "2",
        needed=[runtime, *_BASE_NEEDED],
        imports=[ElfImport("malloc"), ElfImport(symbol)],
    )
    _, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" not in kinds


@pytest.mark.parametrize(
    "callback",
    [
        "__sanitizer_cov_trace_pc",
        "__sanitizer_cov_trace_pc_guard",
        "__sanitizer_cov_trace_cmp4",
    ],
)
def test_coverage_callbacks_are_user_supplied_not_runtime_required(
    callback: str,
) -> None:
    """SanitizerCoverage callbacks are implemented by the host (a fuzzer), not
    reserved to a sanitizer runtime: not a runtime requirement, and a plugin
    whose host supplies them is not judged either."""
    old = _snap("1")
    new = _snap("2", imports=[ElfImport("malloc"), ElfImport(callback)])
    verdict, kinds = _kinds(old, new)
    assert "sanitizer_runtime_required" not in kinds
    assert verdict != Verdict.BREAKING
    host_old = _snap("1", deps=_deps(["host_api"]))
    host_new = _snap(
        "2",
        imports=[ElfImport("malloc"), ElfImport(callback)],
        deps=_deps(["host_api", callback]),
    )
    _, kinds = _kinds(host_old, host_new)
    assert not kinds & {"sanitizer_runtime_required", "imported_symbol_unprovided"}


def _assert_not_evaluated(result, reason: str) -> None:
    det = {d.name: d for d in result.detector_results}
    gated = det["imported_symbols_unprovided"]
    assert gated.not_evaluated and not gated.enabled
    assert reason in (gated.coverage_gap or "")
    # The sanitizer analysis keeps running without dependency evidence.
    assert det["runtime_requirements"].enabled
    assert not det["runtime_requirements"].not_evaluated


def test_an_incomplete_dependency_closure_proves_nothing() -> None:
    """A dependency the resolver could not open contributes no exports, so its
    symbols read as missing; the analysis is recorded as not evaluated."""
    old = _snap("1", deps=_deps([]))
    incomplete = _deps(["helper_v2"])
    incomplete.unresolved = [{"consumer": "libx.so", "soname": "libhelper.so.2"}]
    new = _snap(
        "2", imports=[ElfImport("malloc"), ElfImport("helper_v2")], deps=incomplete
    )
    result = compare(old, new)
    assert "imported_symbol_unprovided" not in {c.kind.value for c in result.changes}
    _assert_not_evaluated(result, "incomplete dependency closure")


@pytest.mark.parametrize("missing_side", ["old", "new"])
def test_missing_dependency_evidence_is_not_an_evaluated_zero(missing_side) -> None:
    old = _snap("1", deps=None if missing_side == "old" else _deps([]))
    new = _snap(
        "2",
        needed=["libubsan.so.1", *_BASE_NEEDED],
        imports=[ElfImport("malloc"), ElfImport("__asan_init")],
        deps=None if missing_side == "new" else _deps([]),
    )
    result = compare(old, new)
    _assert_not_evaluated(
        result, f"no --follow-deps dependency evidence on the {missing_side} side"
    )
    assert "sanitizer_runtime_required" in {c.kind.value for c in result.changes}

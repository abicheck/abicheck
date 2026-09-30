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

"""Harness H5: every optimization produces the reference answer (family F5).

Plan: ``docs/contribute/plans/defect-family-harnesses.md`` § H5. Historical
members of the family: #1336 (shared spelling caches under the release pool),
#1361 (gc census in workers), #1340/#1357 (projection-cache sidecar), #1331
(optimization wired to one of several equivalent paths), #1306 (warm cache
re-acquiring per member), #1245, #1371, #1359.

Each *cell* runs the real ``compare`` CLI twice -- optimized and reference
-- and requires the canonical JSON report (:func:`canonical_report`, which
removes only :data:`VOLATILE_FIELDS`) to be byte-equal. Every cell also
proves both of its configurations ran (AGENTS.md, "A differential test must
prove both of its configurations actually ran"): the bypass counts every
call through each bypassed site, the thread cell records every pool grant,
the disk-cache cell counts hits/misses/stores.

Reference mode is test-side (see ``_family_f5_support``'s docstring for
why no production ``ABICHECK_REFERENCE_MODE`` was added).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _family_f5_support import (
    VOLATILE_FIELDS,
    DiskCacheSpy,
    GrantSpy,
    ReferenceMode,
    Site,
    cache_info_totals,
    canonical_report,
    clear_functools,
    first_difference,
    functools_objects,
    scan_optimization_sites,
)
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_to_json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import example_catalog  # noqa: E402

# ── coverage table: every inventoried site -> the cell that bypasses it ─────
#
# A value naming a cell is a *checked* claim: that cell asserts the site's
# bypass was actually called during its reference run. ``UNCOVERED: ...``
# records why no cell reaches the site today.

R, B, H = "release.memo", "binary.memo", "headers.memo"
_NEEDS_L5 = "UNCOVERED: L3-L5 build-source graph extraction (needs a compile database + clang); no in-process fixture reaches it"
COVERAGE: dict[str, str] = {
    # ---- functools ----
    "abicheck.buildsource.header_compile_context::functools::_include_pattern": _NEEDS_L5,
    "abicheck.buildsource.source_extractors.castxml::functools::_castxml_tool_version": "UNCOVERED: L4 castxml source extractor tool probe; build-source path only",
    "abicheck.buildsource.source_extractors.clang::functools::_clang_compiler_family": "UNCOVERED: L4 clang source extractor tool probe; build-source path only",
    "abicheck.buildsource.source_extractors.clang::functools::_clang_compiler_version": "UNCOVERED: L4 clang source extractor tool probe; build-source path only",
    "abicheck.buildsource.toolchain_probe::functools::_clang_accepts_target": "UNCOVERED: clang cross-target probe; needs clang and a --target build",
    "abicheck.buildsource.type_graph::functools::_base_type_name": _NEEDS_L5,
    "abicheck.compatibility_evaluation_frontend::functools::builtin_policy_identity": R,
    "abicheck.compatibility_evaluation_frontend::functools::severity_preset_identity": "UNCOVERED: reached only under --severity-preset; pure function of a packaged preset name",
    "abicheck.demangle::functools::demangle": R,
    "abicheck.diff_helpers::functools::depth_aware_bare_name": B,
    "abicheck.diff_symbols_renames::functools::_rename_name_parse": "UNCOVERED: rename heuristics need a removed+added pair with matching fingerprints; no cell fixture has one",
    "abicheck.dumper_toolchain::functools::_executable_sha256": H,
    "abicheck.dumper_toolchain::functools::_probe_default_language_standard": H,
    "abicheck.dumper_toolchain::functools::_tool_target_triple": H,
    "abicheck.dumper_toolchain::functools::_tool_version_output": H,
    "abicheck.elf_symbol_filter::functools::is_abi_relevant_elf_symbol": B,
    "abicheck.extract.path_aliases::functools::_canonical_spelling": H,
    "abicheck.extract.path_aliases::functools::_source_header_alias_segments": H,
    "abicheck.model.signature_normalization::functools::_canonicalize_top_level_param_type": R,
    "abicheck.model.type_identifiers::functools::_type_identifiers_cached": R,
    "abicheck.name_classification::functools::canonicalize_type_name": R,
    "abicheck.name_classification::functools::strip_anonymous_type_location": H,
    "abicheck.schemas.documents::functools::load_aggregate_report_schema": "UNCOVERED: schema loader for `aggregate` only; returns packaged JSON",
    "abicheck.schemas.documents::functools::load_audit_report_schema": "UNCOVERED: schema loader for audit reports only; returns packaged JSON",
    "abicheck.schemas.documents::functools::load_compare_report_schema": "UNCOVERED: schema loader used by validation tooling, not by compare itself",
    "abicheck.storage.closure_identity::functools::_anon_type_ordinal_matches_cached": "UNCOVERED: stored-closure identity for anonymous types; needs a ProjectSnapshot fixture",
    # ---- cached_property ----
    "abicheck.compare.edge_query::cached_property::EdgeEvidence._ambiguous_spellings": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::cached_property::EdgeEvidence._entity_nodes": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::cached_property::EdgeEvidence._ownership": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::cached_property::EdgeEvidence._projection": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::cached_property::EdgeEvidence._refs": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::cached_property::EdgeEvidence.debug_types": R,
    "abicheck.compare.edge_query::cached_property::EdgeEvidence.export_records": R,
    "abicheck.compare.edge_query::cached_property::EdgeEvidence.exports": R,
    "abicheck.model.elf_facts::cached_property::ElfMetadata.symbol_map": B,
    "abicheck.model.macho_facts::cached_property::MachoMetadata.export_map": "UNCOVERED: Mach-O only; no Mach-O toolchain on the Linux lanes",
    "abicheck.model.pe_facts::cached_property::PeMetadata.export_map": "UNCOVERED: PE only; no PE toolchain on the Linux lanes",
    # ---- run-scoped digest memo (one shared scope variable) ----
    "abicheck.cli_compare_helpers::scoped_decorator::run_compare": R,
    "abicheck.service_compare_pipeline::scoped_decorator::classify_compare_pair": R,
    # ---- module-level memo dicts / ContextVars / memo objects ----
    "abicheck.buildsource.pattern_facts::memo_dict::_SCAN_MEMO": H,
    "abicheck.comparability_fields::memo_contextvar::_PATH_MEMO": H,
    "abicheck.compare.detection_memo::memo_contextvar::_MEMO": R,
    "abicheck.compare.spelling_match_cache::memo_object::MATCH_CACHE": "UNCOVERED: spelling-pattern matching runs only with an L5 type-reachability graph (#1336's cache); its thread-safety is owned by test_spelling_match_cache_concurrency.py",
    "abicheck.compare.spelling_match_cache::memo_object::VOCABULARY_CACHE": "UNCOVERED: as MATCH_CACHE",
    "abicheck.demangle::memo_dict::_BATCH_CACHE_FAIL": R,
    "abicheck.demangle::memo_dict::_BATCH_CACHE_OK": R,
    "abicheck.dumper_ast_config_cpp20::memo_object::_SCAN_MEMO": "UNCOVERED: C++20 module/dialect header scan; only for headers using C++20 features",
    "abicheck.dumper_ast_config_cpp20::memo_object::_SHADOW_MEMO": "UNCOVERED: as dumper_ast_config_cpp20._SCAN_MEMO",
    "abicheck.dumper_cache::memo_contextvar::_ast_memo_slot": H,
    "abicheck.dumper_cache::memo_contextvar::_ast_memoize_scope": "UNCOVERED: only opened by the clang AST frontend's release reuse scope (--ast-frontend clang)",
    "abicheck.elf_metadata::memo_dict::_PARSE_MEMO": B,
    "abicheck.model.graph_identity::memo_object::_NORMALIZE_MEMO_STATE": H,
    "abicheck.policy.type_spelling::memo_dict::_strip_ptr_memo": R,
    # ---- pools (reference = ABICHECK_MAX_THREADS=1 / ABICHECK_PARALLEL_EXTRACTION=0) ----
    "abicheck.process_resources::pool::BudgetedExecutor.__init__->ThreadPoolExecutor": "release.threads",
    "abicheck.workflows.keyed_thread_pool::pool::run_keyed_in_threads->BudgetedExecutor": "release.threads",
    "abicheck.service_compare_pipeline::pool::resolve_compare_request->BudgetedExecutor": "binary.sides",
    "abicheck.buildsource.include_graph_workers::pool::_shared_pool->BudgetedExecutor": _NEEDS_L5,
    "abicheck.buildsource.pattern_facts::pool::find_pattern_facts->ProcessPoolExecutor": "UNCOVERED: process pool for large L4 pattern scans (threshold-gated); build-source path only",
    "abicheck.dumper_manifest::pool::_run_tu_fragments->BudgetedExecutor": "UNCOVERED: per-TU pool of a --dump-manifest dump; needs a manifest + castxml",
    "abicheck.parallel_probe::pool::run_parallel_probes->BudgetedExecutor": "UNCOVERED: environment-matrix probes (--probe-matrix); needs a probe matrix",
    "abicheck.service_header_graph_attach::pool::prefetch_header_graph_ast->BudgetedExecutor": _NEEDS_L5,
}

_SITES = scan_optimization_sites()


def _cell_sites(cell: str) -> list[str]:
    return sorted(k for k, v in COVERAGE.items() if v == cell)


# ── inventory completeness ──────────────────────────────────────────────────


@pytest.mark.repo_scan
def test_inventory_has_no_unlisted_site() -> None:
    """A new cache/memo/pool under ``abicheck/`` must be placed in COVERAGE."""
    missing = sorted({s.key for s in _SITES} - COVERAGE.keys())
    assert not missing, (
        "new optimization site(s) with no H5 coverage entry:\n" + "\n".join(missing)
    )


@pytest.mark.repo_scan
def test_inventory_has_no_stale_entry() -> None:
    stale = sorted(COVERAGE.keys() - {s.key for s in _SITES})
    assert not stale, "COVERAGE names site(s) the scan no longer finds:\n" + "\n".join(
        stale
    )


def test_every_coverage_value_is_a_known_cell_or_a_reasoned_gap() -> None:
    cells = {R, B, H, "release.threads", "binary.sides"}
    bad = {
        k: v
        for k, v in COVERAGE.items()
        if v not in cells and not (v.startswith("UNCOVERED: ") and len(v) > 20)
    }
    assert not bad, bad
    # Most memo sites must be covered, or the harness is decorative.
    memo = [k for k in COVERAGE if "::pool::" not in k]
    covered = [k for k in memo if not COVERAGE[k].startswith("UNCOVERED")]
    assert len(covered) >= 29, (
        len(covered),
        len(memo),
    )  # floor: today's count; raise, never lower


def test_scanner_finds_each_site_kind_on_a_synthetic_module(tmp_path: Path) -> None:
    """The scanner's own contract, independent of today's tree (a scanner
    that found nothing would make both completeness tests vacuous)."""
    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "import functools, contextvars\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "_FOO_CACHE = {}\n_bar_memo: dict = dict()\n_NOT_A_CACHE = {'a': 1}\n_plain = {}\n"
        "_SCOPE_MEMO = contextvars.ContextVar('x', default=None)\n_OBJ = LRUCache()\n"
        "@functools.lru_cache(maxsize=4)\ndef f(x): return x\n"
        "class C:\n    @functools.cached_property\n    def p(self): return 1\n"
        "    def run(self):\n        with ThreadPoolExecutor(2) as a, ThreadPoolExecutor(3) as b: pass\n"
    )
    keys = {s.key for s in scan_optimization_sites(pkg)}
    assert keys == {
        "abicheck.m::memo_dict::_FOO_CACHE",
        "abicheck.m::memo_dict::_bar_memo",
        "abicheck.m::memo_contextvar::_SCOPE_MEMO",
        "abicheck.m::memo_object::_OBJ",
        "abicheck.m::functools::f",
        "abicheck.m::cached_property::C.p",
        "abicheck.m::pool::C.run->ThreadPoolExecutor",
        "abicheck.m::pool::C.run->ThreadPoolExecutor#2",
    }


def test_volatile_table_states_a_reason_for_every_field() -> None:
    assert VOLATILE_FIELDS
    assert all(len(reason) > 20 for reason in VOLATILE_FIELDS.values())
    # Only volatile keys are removed; everything else survives canonicalisation.
    doc = '{"verdict": "BREAKING", "elapsed_s": 1.5, "nested": [{"created_at": "x", "k": 1}]}'
    assert canonical_report(doc) == canonical_report(
        '{"verdict": "BREAKING", "nested": [{"k": 1}]}'
    )
    assert canonical_report(doc) != canonical_report(
        '{"verdict": "COMPATIBLE", "nested": [{"k": 1}]}'
    )


# ── fixtures ────────────────────────────────────────────────────────────────


def _release_dirs(root: Path, members: int = 4) -> tuple[Path, Path]:
    """A multi-member release whose members share type spellings (the
    contention #1336 needed), with a removal, a layout change and no-change
    members, so the report carries findings of several kinds."""
    old, new = root / "old", root / "new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)

    def dense(extra: bool) -> RecordType:
        fields = [
            TypeField(name="r", type="int", offset_bits=0),
            TypeField(name="c", type="int", offset_bits=32),
        ]
        if extra:
            fields.append(TypeField(name="z", type="int", offset_bits=64))
        return RecordType(
            name="onemock::dense",
            kind="struct",
            size_bits=96 if extra else 64,
            fields=fields,
        )

    for i in range(members):
        lib = f"libm{i}.so"
        funcs = [
            Function(
                name=f"onemock::f{i}",
                mangled=f"_ZN7onemock2f{i}EPNS_5denseE",
                return_type="int",
                params=[Param(name="d", type="onemock::dense *")],
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name=f"onemock::g{i}",
                mangled=f"_ZN7onemock2g{i}ERKNS_5denseE",
                return_type="const onemock::dense &",
                params=[Param(name="d", type="const onemock::dense &")],
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name=f"h{i}",
                mangled=f"h{i}",
                return_type="unsigned long",
                params=[Param(name="n", type="unsigned long int")],
                visibility=Visibility.PUBLIC,
            ),
        ]
        new_funcs = funcs[:2] if i % 2 else list(funcs)
        if i == 0:
            # A parameter-type change whose two spellings share a prefix: a
            # type-spelling cache keyed on less than the whole spelling
            # (the #1331/#1336 shape) answers the second from the first.
            new_funcs.append(
                Function(
                    name="k0",
                    mangled="k0",
                    return_type="int",
                    params=[Param(name="n", type="unsigned long")],
                    visibility=Visibility.PUBLIC,
                )
            )
            funcs = [
                *funcs,
                Function(
                    name="k0",
                    mangled="k0",
                    return_type="int",
                    params=[Param(name="n", type="unsigned int")],
                    visibility=Visibility.PUBLIC,
                ),
            ]
        (old / f"{lib}.json").write_text(
            snapshot_to_json(
                AbiSnapshot(
                    library=lib,
                    version="1",
                    functions=funcs,
                    types=[dense(False)],
                    from_headers=True,
                )
            )
        )
        (new / f"{lib}.json").write_text(
            snapshot_to_json(
                AbiSnapshot(
                    library=lib,
                    version="2",
                    functions=new_funcs,
                    types=[dense(i == 2)],
                    from_headers=True,
                )
            )
        )
    return old, new


def _run(*args: str) -> str:
    result = CliRunner().invoke(main, ["compare", *args, "-o", "json=-"])
    assert result.exit_code in (0, 1, 2, 4), (
        result.exit_code,
        result.stdout[-2000:],
        result.stderr[-2000:] if result.stderr_bytes else "",
    )
    assert result.stdout.strip(), "compare wrote no JSON report"
    return canonical_report(result.stdout)


def _assert_same(optimized: str, reference: str, cell: str) -> None:
    assert optimized == reference, (
        f"H5 violation in cell {cell}: {first_difference(optimized, reference)}"
    )


def _memo_sites() -> list[Site]:
    return [s for s in _SITES if s.kind != "pool"]


def _import_all() -> dict[str, Any]:
    import importlib

    for s in _SITES:
        importlib.import_module(s.module)
    return functools_objects(_SITES)


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Path]:
    return _release_dirs(tmp_path / "rel")


def _memo_cell(
    monkeypatch: pytest.MonkeyPatch,
    cell: str,
    args: tuple[str, ...],
    cache_root: Callable[[str], Path] | None = None,
) -> None:
    """Optimized vs every memo bypassed. When the operands are binaries,
    *cache_root* gives each configuration its own disk-cache root: sharing
    one would serve the reference run the optimized run's snapshot, so the
    reference would never parse anything (the AGENTS.md vacuous-differential
    hazard)."""
    objs = _import_all()
    if cache_root is not None:
        DiskCacheSpy().install(monkeypatch, cache_root("optimized"))
    clear_functools(objs)
    optimized = _run(*args)
    optimized_hits = sum(h for h, _ in cache_info_totals(objs).values())
    ref = ReferenceMode()
    ref.install(monkeypatch, _memo_sites())
    assert not ref.unpatchable, ref.unpatchable
    clear_functools(objs)
    if cache_root is not None:
        DiskCacheSpy().install(monkeypatch, cache_root("reference"))
    reference = _run(*args)
    # Engagement: optimized run hit real caches; reference run never touched
    # one, and called through every bypass this cell claims in COVERAGE.
    assert optimized_hits > 0
    assert all(h + m == 0 for h, m in cache_info_totals(objs).values()), (
        "a real functools cache was consulted in reference mode"
    )
    unreached = [k for k in _cell_sites(cell) if not ref.calls.get(k)]
    assert not unreached, (
        f"COVERAGE claims cell {cell} reaches these sites, but their bypass was never called: {unreached}"
    )
    _assert_same(optimized, reference, cell)


# ── (a)/(b) memo caches bypassed ────────────────────────────────────────────


def test_release_memo_bypass_matches_default(
    monkeypatch: pytest.MonkeyPatch, release: tuple[Path, Path]
) -> None:
    old, new = release
    _memo_cell(monkeypatch, R, (str(old), str(new)))


def test_single_pair_memo_bypass_matches_default(
    monkeypatch: pytest.MonkeyPatch, release: tuple[Path, Path]
) -> None:
    old, new = release
    objs = _import_all()
    clear_functools(objs)
    args = (str(old / "libm2.so.json"), str(new / "libm2.so.json"))
    optimized = _run(*args)
    ref = ReferenceMode()
    ref.install(monkeypatch, _memo_sites())
    reference = _run(*args)
    assert ref.calls.get(
        "abicheck.service_compare_pipeline::scoped_decorator::classify_compare_pair"
    )
    _assert_same(optimized, reference, "single_pair.memo")


# ── (e) warm in-process memo vs fresh ───────────────────────────────────────


def test_warm_repeat_matches_fresh(release: tuple[Path, Path]) -> None:
    old, new = release
    objs = _import_all()
    clear_functools(objs)
    fresh = _run(str(old), str(new))
    before = sum(h for h, _ in cache_info_totals(objs).values())
    warm = _run(str(old), str(new))
    after = sum(h for h, _ in cache_info_totals(objs).values())
    assert after > before, "the second run was not served from a warm memo"
    _assert_same(warm, fresh, "release.warm")


# ── (c) threads=1 vs threads=8 ──────────────────────────────────────────────


def _threads_run(
    monkeypatch: pytest.MonkeyPatch, old: Path, new: Path, threads: str
) -> tuple[str, list[int]]:
    spy = GrantSpy()
    with monkeypatch.context() as mp:
        mp.setenv("ABICHECK_MAX_THREADS", threads)
        mp.setattr(os, "cpu_count", lambda: 8)
        spy.install(mp)
        return _run(str(old), str(new)), spy.grants


def test_release_threads_1_matches_threads_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old, new = _release_dirs(tmp_path / "rel", members=8)
    serial, serial_grants = _threads_run(monkeypatch, old, new, "1")
    parallel, parallel_grants = _threads_run(monkeypatch, old, new, "8")
    assert serial_grants and max(serial_grants) <= 1, serial_grants
    assert parallel_grants and max(parallel_grants) > 1, parallel_grants
    _assert_same(parallel, serial, "release.threads")


# ── binary cells: need a C compiler (no castxml) ────────────────────────────


def _cc() -> str:
    comp = shutil.which("cc")
    if comp is None or sys.platform != "linux":
        pytest.skip("binary cells build ELF shared objects with a C compiler on Linux")
    return comp


def _build_case(case: str, out: Path) -> tuple[Path, Path]:
    case_dir = example_catalog.case_dir(case)
    libs = []
    for v in ("v1", "v2"):
        src = next(case_dir.glob(f"{v}.c*"))
        comp = shutil.which("c++") if src.suffix == ".cpp" else _cc()
        if comp is None:
            pytest.skip("no C++ compiler for a .cpp example")
        lib = out / f"lib{v}.so"
        res = subprocess.run(
            [str(comp), "-shared", "-fPIC", "-g", "-Og", "-o", str(lib), str(src)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, res.stderr[-800:]
        libs.append(lib)
    return libs[0], libs[1]


@pytest.fixture
def binaries(tmp_path: Path) -> tuple[Path, Path]:
    _cc()
    out = tmp_path / "bin"
    out.mkdir()
    return _build_case("case40_field_layout", out)


def test_binary_memo_bypass_matches_default(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    _memo_cell(
        monkeypatch,
        B,
        tuple(str(p) for p in binaries),
        lambda tag: tmp_path / f"cache-{tag}",
    )


def test_binary_parallel_sides_match_sequential(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    """The typed API resolves old/new on a two-thread pool (the native CLI
    always resolves sequentially, so this cell goes through
    ``run_compare_request``); ``ABICHECK_PARALLEL_EXTRACTION=0`` is the
    reference."""
    from abicheck.reporter import to_json
    from abicheck.service import CompareRequest, InputSpec
    from abicheck.service_compare_pipeline import run_compare_request

    results = {}
    for flag in ("1", "0"):
        spy = GrantSpy()
        with monkeypatch.context() as mp:
            mp.setenv("ABICHECK_PARALLEL_EXTRACTION", flag)
            DiskCacheSpy().install(mp, tmp_path / f"cache{flag}")
            spy.install(mp)
            res = run_compare_request(
                CompareRequest(
                    old=InputSpec(path=binaries[0]), new=InputSpec(path=binaries[1])
                )
            )
            results[flag] = (
                canonical_report(
                    to_json(res.diff, severity_config=res.severity_config)
                ),
                spy.grants,
            )
    assert 2 in results["1"][1], (
        f"sides were not resolved on a two-thread pool: {results['1'][1]}"
    )
    assert 2 not in results["0"][1], (
        "ABICHECK_PARALLEL_EXTRACTION=0 still used the side pool"
    )
    _assert_same(results["1"][0], results["0"][0], "binary.sides")


# ── (d) cold vs warm disk cache, distinct roots ─────────────────────────────


def test_disk_cache_cold_warm_and_fresh_root_agree(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    args = tuple(str(p) for p in binaries)
    outs, spies = [], []
    for root in ("A", "A", "B"):
        spy = DiskCacheSpy()
        with monkeypatch.context() as mp:
            spy.install(mp, tmp_path / f"cache{root}")
            outs.append(_run(*args))
        spies.append(spy)
    cold, warm, fresh = spies
    assert cold.hits == 0 and cold.misses >= 2 and cold.stores >= 2, cold
    assert warm.hits >= 2 and warm.stores == 0, warm
    assert fresh.hits == 0 and fresh.misses >= 2, fresh
    _assert_same(outs[1], outs[0], "disk.warm")
    _assert_same(outs[2], outs[0], "disk.fresh_root")


# ── seeded mutants: the oracle must catch each historical bug shape ─────────


def _stale_key_memo(fn: Callable[[str], Any]) -> Callable[..., Any]:
    """A memo keyed on a truncated input (#1331/#1336 shape): a warm call on
    a different spelling sharing the key's prefix returns a stale answer."""
    memo: dict[object, Any] = {}

    def wrapper(arg: Any, *a: Any, **k: Any) -> Any:
        key = arg[:5] if isinstance(arg, str) else id(arg)
        if key not in memo:
            memo[key] = fn(arg, *a, **k)
        return memo[key]

    return wrapper


def test_mutant_stale_cache_key_is_caught(
    monkeypatch: pytest.MonkeyPatch, release: tuple[Path, Path]
) -> None:
    old, new = release
    objs = _import_all()
    clear_functools(objs)
    reference = _run(str(old), str(new))
    target = objs["abicheck.name_classification::functools::canonicalize_type_name"]
    mutant = _stale_key_memo(target.__wrapped__)
    n = ReferenceMode._swap_everywhere(monkeypatch, target, mutant)
    assert n > 0
    assert _run(str(old), str(new)) != reference, "oracle missed a stale-key memo"


def test_mutant_parallel_path_dropping_a_member_is_caught(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A narrowed fast path returning a subset (#1336's lost members): only
    the parallel configuration drops the last member."""
    from abicheck import cli_compare_release_pairwise as pairwise, process_resources

    old, new = _release_dirs(tmp_path / "rel", members=8)
    orig = pairwise.run_keyed_in_threads

    def narrowed(*a: Any, **k: Any) -> Any:
        out = orig(*a, **k)
        return out if process_resources.max_threads() == 1 else out[:-1]

    monkeypatch.setattr(pairwise, "run_keyed_in_threads", narrowed)
    serial, _ = _threads_run(monkeypatch, old, new, "1")
    parallel, grants = _threads_run(monkeypatch, old, new, "8")
    assert max(grants) > 1
    assert serial != parallel, "oracle missed a parallel path that drops a member"


def test_mutant_stale_disk_cache_entry_is_caught(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    """A warm disk-cache hit serving a stale/partial snapshot (#1340/#1306 shape)."""
    from abicheck import snapshot_cache

    args = tuple(str(p) for p in binaries)
    spy = DiskCacheSpy()
    spy.install(monkeypatch, tmp_path / "cacheM")
    cold = _run(*args)
    real_lookup = snapshot_cache.lookup_key

    def stale(key: str, path: Path) -> Any:
        snap = real_lookup(key, path)
        if snap is not None and snap.functions:
            snap.functions = snap.functions[:-1]
        return snap

    monkeypatch.setattr(snapshot_cache, "lookup_key", stale)
    warm = _run(*args)
    assert spy.hits >= 2
    assert warm != cold, "oracle missed a stale disk-cache entry"


# ── example catalog (castxml headers): integration ──────────────────────────

_HEADER_CASES = ("case40_field_layout", "case31_enum_rename", "case12_function_removed")


@pytest.mark.integration
@pytest.mark.parametrize("case", _HEADER_CASES)
def test_catalog_with_headers_memo_bypass_matches_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
) -> None:
    if shutil.which("castxml") is None:
        pytest.skip("castxml not available")
    _cc()
    out = tmp_path / "bin"
    out.mkdir()
    v1, v2 = _build_case(case, out)
    case_dir = example_catalog.case_dir(case)
    h1, h2 = case_dir / "v1.h", case_dir / "v2.h"
    if not h1.exists():
        pytest.skip(f"{case} has no v1.h/v2.h pair")
    args = (str(v1), str(v2), "--header", f"old={h1}", "--header", f"new={h2}")
    if case == _HEADER_CASES[0]:
        _memo_cell(monkeypatch, H, args, lambda tag: tmp_path / f"cache-{tag}")
        return
    DiskCacheSpy().install(monkeypatch, tmp_path / "cacheH")
    objs = _import_all()
    clear_functools(objs)
    optimized = _run(*args)
    ref = ReferenceMode()
    ref.install(monkeypatch, _memo_sites())
    DiskCacheSpy().install(monkeypatch, tmp_path / "cacheH2")
    reference = _run(*args)
    assert ref.calls, "reference mode bypassed nothing"
    _assert_same(optimized, reference, f"headers.memo[{case}]")


@pytest.mark.slow
@pytest.mark.parametrize(
    "case",
    (
        "case01_symbol_removal",
        "case33_pointer_level",
        "case38_virtual_methods",
        "case71_inline_namespace_moved",
        "case86_tag_struct_renamed",
    ),
)
def test_catalog_dwarf_memo_bypass_and_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str
) -> None:
    _cc()
    out = tmp_path / "bin"
    out.mkdir()
    try:
        v1, v2 = _build_case(case, out)
    except StopIteration:
        pytest.skip(f"{case} has no v1/v2 source pair")
    args = (str(v1), str(v2))
    DiskCacheSpy().install(monkeypatch, tmp_path / "c1")
    optimized = _run(*args)
    with monkeypatch.context() as mp:
        ref = ReferenceMode()
        ref.install(mp, _memo_sites())
        mp.setenv("ABICHECK_MAX_THREADS", "1")
        mp.setenv("ABICHECK_PARALLEL_EXTRACTION", "0")
        DiskCacheSpy().install(mp, tmp_path / "c2")
        reference = _run(*args)
    assert ref.calls
    _assert_same(optimized, reference, f"catalog.dwarf[{case}]")

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

Reference mode is the production ``ABICHECK_REFERENCE_MODE=1`` switch
(design-hardening plan, Phase 4): every cache goes through
``abicheck.model.execution_cache``, which bypasses and counts in reference
mode, and every pool runs inline.
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
    memoized_callable,
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
#: A stored snapshot compared with a copy of itself: the one shape whose
#: front end asks for ``snapshot_content_digest`` (no binary metadata, and the
#: verbatim-field prefilter cannot tell the two apart).
D = "same_content.memo"
_NEEDS_L5 = "UNCOVERED: L3-L5 build-source graph extraction (needs a compile database + clang); no in-process fixture reaches it"
COVERAGE: dict[str, str] = {
    # ---- memoized functions ----
    "abicheck.storage.code_identity::memoized::abicheck_code_fingerprint": B,
    "abicheck.model.semantic_ir_function_signature::memoized::_shared_present": B,
    "abicheck.storage.code_identity::memoized::abicheck_modules_fingerprint": H,
    "abicheck.buildsource.header_compile_context::memoized::_include_pattern": _NEEDS_L5,
    "abicheck.buildsource.source_extractors.castxml::memoized::_castxml_tool_version": "UNCOVERED: L4 castxml source extractor tool probe; build-source path only",
    "abicheck.buildsource.source_extractors.clang::memoized::_clang_compiler_family": "UNCOVERED: L4 clang source extractor tool probe; build-source path only",
    "abicheck.buildsource.source_extractors.clang::memoized::_clang_compiler_version": "UNCOVERED: L4 clang source extractor tool probe; build-source path only",
    "abicheck.buildsource.toolchain_probe::memoized::_clang_accepts_target": "UNCOVERED: clang cross-target probe; needs clang and a --target build",
    "abicheck.buildsource.type_graph::memoized::_base_type_name": _NEEDS_L5,
    "abicheck.compare.overload_ambiguity::memoized::_callable_key": H,
    "abicheck.compare.parameter_facts::memoized::_pointee_qualifier_delta": H,
    "abicheck.compatibility_evaluation_frontend::memoized::builtin_policy_identity": R,
    "abicheck.compatibility_evaluation_frontend::memoized::severity_preset_identity": "UNCOVERED: reached only under --severity-preset; pure function of a packaged preset name",
    "abicheck.demangle::memoized::demangle": R,
    "abicheck.diff_helpers::memoized::depth_aware_bare_name": B,
    "abicheck.diff_symbols_renames::memoized::_rename_name_parse": "UNCOVERED: rename heuristics need a removed+added pair with matching fingerprints; no cell fixture has one",
    "abicheck.dumper_toolchain::memoized::_executable_sha256": H,
    "abicheck.dumper_toolchain::memoized::_probe_default_language_standard": H,
    "abicheck.dumper_toolchain::memoized::_tool_target_triple": H,
    "abicheck.dumper_toolchain::memoized::_tool_version_output": H,
    "abicheck.elf_symbol_filter::memoized::is_abi_relevant_elf_symbol": B,
    "abicheck.extract.path_aliases::memoized::_canonical_spelling": H,
    "abicheck.extract.path_aliases::memoized::_source_header_alias_segments": H,
    "abicheck.model.signature_normalization::memoized::_canonicalize_top_level_param_type": R,
    "abicheck.model.type_identifiers::memoized::_type_identifiers_cached": R,
    "abicheck.compare.template_surface::memoized::mask_operator_symbols": B,
    "abicheck.compare.template_surface::memoized::strip_template_args": B,
    "abicheck.buildsource.dpcpp_jobs::memoized::_host_only_single_pass": "UNCOVERED: DPC++ driver pass probe; needs an icx/icpx compiler",
    "abicheck.compare.qualified_name_normalization::memoized::_segments_cached": B,
    "abicheck.diff_namespaces::memoized::_scope_path_of": "UNCOVERED: reached only for a removed experimental-namespace declaration with a signature; no cell fixture has one",
    "abicheck.policy.classification::memoized::policy_kind_sets": R,
    "abicheck.compare.template_surface::memoized::template_angle_depth": H,
    "abicheck.diff_templates::memoized::_cpo_function_stem": B,
    "abicheck.diff_templates::memoized::_strip_param_signature": B,
    "abicheck.name_classification::memoized::canonicalize_type_name": R,
    "abicheck.name_classification::memoized::strip_anonymous_type_location": H,
    "abicheck.schemas.documents::memoized::load_aggregate_report_schema": "UNCOVERED: schema loader for `aggregate` only; returns packaged JSON",
    "abicheck.schemas.documents::memoized::load_audit_report_schema": "UNCOVERED: schema loader for audit reports only; returns packaged JSON",
    "abicheck.schemas.documents::memoized::load_audit_set_report_schema": "UNCOVERED: schema loader for directory audit-set reports only; returns packaged JSON",
    "abicheck.schemas.documents::memoized::load_compare_report_schema": "UNCOVERED: schema loader used by validation tooling, not by compare itself",
    "abicheck.storage.closure_identity::memoized::_anon_type_ordinal_matches_cached": "UNCOVERED: stored-closure identity for anonymous types; needs a ProjectSnapshot fixture",
    # ---- memoized properties ----
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence._ambiguous_spellings": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence._entity_nodes": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence._ownership": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence._projection": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence._refs": "UNCOVERED: edge queries over a header-graph projection; needs L5 source graph",
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence.debug_types": R,
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence.export_records": R,
    "abicheck.compare.edge_query::memoized_property::EdgeEvidence.exports": R,
    "abicheck.model.elf_facts::memoized_property::ElfMetadata.symbol_map": B,
    "abicheck.model.macho_facts::memoized_property::MachoMetadata.export_map": "UNCOVERED: Mach-O only; no Mach-O toolchain on the Linux lanes",
    "abicheck.model.pe_facts::memoized_property::PeMetadata.export_map": "UNCOVERED: PE only; no PE toolchain on the Linux lanes",
    # ---- explicitly keyed memory caches ----
    "abicheck.buildsource.header_include_memo::memory_cache::_MEMO": _NEEDS_L5,
    "abicheck.buildsource.pattern_facts::memory_cache::_SCAN_MEMO": H,
    "abicheck.demangle::memory_cache::_BATCH_CACHE_FAIL": R,
    "abicheck.demangle::memory_cache::_BATCH_CACHE_OK": R,
    "abicheck.dumper_ast_config_cpp20::memory_cache::_SCAN_MEMO": "UNCOVERED: C++20 module/dialect header scan; only for headers using C++20 features",
    "abicheck.dumper_ast_config_cpp20::memory_cache::_SHADOW_MEMO": "UNCOVERED: as dumper_ast_config_cpp20._SCAN_MEMO",
    "abicheck.dumper_ast_config_cpp20::header_scan::_find_cpp20_requirements": H,
    "abicheck.extract.header_scan_memo::memory_cache::memoize_header_scan.decorate->MemoryCache": "UNCOVERED: the factory itself; each decorated use is its own header_scan site",
    "abicheck.elf_metadata::memory_cache::_PARSE_MEMO": B,
    "abicheck.policy.reclassify::memory_cache::_KIND_BUCKETS": "UNCOVERED: reached only when a policy carries two or more reclassify rules",
    "abicheck.policy.type_spelling::memory_cache::_strip_ptr_memo": R,
    # ---- request-scoped and instance memos ----
    "abicheck.buildsource.type_graph::scoped_cache::_AST_DERIVED": H,
    "abicheck.comparability_fields::scoped_cache::_PATH_MEMO": H,
    "abicheck.buildsource.export_account_decision::scoped_cache::_CONTEXTS": H,
    "abicheck.compare.detection_memo::scoped_cache::_MEMO": R,
    "abicheck.model.comparison_memo::scoped_cache::_MEMO": R,
    "abicheck.surface_graph::scoped_cache::_GRAPHS": "UNCOVERED: shared only under --pattern-verdicts plus --surface-metrics; tests/test_surface_graph_sharing.py states its contract",
    "abicheck.storage.snapshot_digest_cache::scoped_cache::_SCOPE": D,
    "abicheck.model.graph_identity::shared_scoped_cache::_NORMALIZE_MEMO": "UNCOVERED: opened only while loading a stored L5 source graph; no cell carries one",
    "abicheck.compare.surface_reconcile::instance_memo::PAIR_MEMO": R,
    "abicheck.elf_symbol_filter::instance_memo::_EXPORTED_NAMES_MEMO": B,
    "abicheck.model.export_index::instance_memo::_ELF_INDEX_MEMO": B,
    "abicheck.extract.dwarf_subtree_index::instance_memo::_INDEX_MEMO": B,
    # ---- registered engines (own storage, central switch) ----
    "abicheck.compare.spelling_match_cache::registered::_MATCH_STATS": "UNCOVERED: spelling-pattern matching runs only with an L5 type-reachability graph (#1336's cache); its thread-safety is owned by test_spelling_match_cache_concurrency.py",
    "abicheck.compare.spelling_match_cache::registered::_VOCABULARY_STATS": "UNCOVERED: as _MATCH_STATS",
    "abicheck.storage.header_ast_cache::registered::_AST_SLOT_STATS": "UNCOVERED: the per-thread AST handoff is written only by the clang header backend (--ast-frontend clang); the header cell uses castxml",
    "abicheck.storage.header_ast_cache::registered::_AST_ACQUISITION_STATS": "UNCOVERED: the request-wide AST table is opened by the release fan-out over binaries with headers; no cell builds one",
    # ---- disk caches ----
    "abicheck.snapshot_cache::disk_cache::SNAPSHOT_DISK_CACHE": B,
    "abicheck.storage.ast_cache_location::disk_cache::AST_DISK_CACHE": H,
    "abicheck.buildsource.build_cache::disk_cache::BUILD_EVIDENCE_DISK_CACHE": _NEEDS_L5,
    "abicheck.buildsource.build_cache::disk_cache::SOURCE_ABI_DISK_CACHE": _NEEDS_L5,
    # ---- pools (reference = ABICHECK_REFERENCE_MODE=1: every pool runs inline) ----
    "abicheck.process_resources::pool::BudgetedExecutor.__init__->ThreadPoolExecutor": "release.threads",
    "abicheck.workflows.keyed_thread_pool::pool::run_keyed_in_threads->BudgetedExecutor": "release.threads",
    "abicheck.service_compare_pipeline::pool::resolve_compare_request->BudgetedExecutor": "binary.sides",
    "abicheck.buildsource.include_graph_workers::pool::_shared_pool->BudgetedExecutor": _NEEDS_L5,
    "abicheck.buildsource.include_graph_workers::pool::_shared_pool->BudgetedExecutor#2": _NEEDS_L5,
    "abicheck.buildsource.pattern_facts::pool::find_pattern_facts->ProcessPoolExecutor": "UNCOVERED: process pool for large L4 pattern scans (threshold-gated); build-source path only",
    "abicheck.dumper_manifest::pool::_run_tu_fragments->BudgetedExecutor": "UNCOVERED: per-TU pool of a --dump-manifest dump; needs a manifest + castxml",
    # The one pool behind the environment-matrix probes and, since #1425,
    # every L5 clang graph pass (buildsource/l5_ast_pass.run_ast_passes) --
    # it replaced the six per-extractor ``extract_from_build`` pools.
    "abicheck.parallel_probe::pool::run_parallel_probes->BudgetedExecutor": "UNCOVERED: environment-matrix probes (--probe-matrix) and the shared L5 clang AST passes; needs a probe matrix or a compile database + clang",
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
    cells = {R, B, H, D, "release.threads", "binary.sides"}
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
        "from abicheck.model.execution_cache import *\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "_MEM = MemoryCache('m.mem')\n_SC: ScopedCache = ScopedCache('m.sc')\n"
        "_SH = SharedScopedCache('m.sh')\n_IM = InstanceMemo('m.im', '_a')\n"
        "_DC = DiskCache('m.dc')\n_RS = register_cache('m.rs', 'registered')\n"
        "_NOT_A_CACHE = {'a': 1}\n"
        "@memoized(maxsize=4)\ndef f(x): return x\n"
        "@memoize_header_scan(len)\ndef g(x): return x\n"
        "def factory():\n    return MemoryCache('m.inner')\n"
        "class C:\n    @memoized_property\n    def p(self): return 1\n"
        "    def run(self):\n        with ThreadPoolExecutor(2) as a, ThreadPoolExecutor(3) as b: pass\n"
    )
    keys = {s.key for s in scan_optimization_sites(pkg)}
    assert keys == {
        "abicheck.m::memory_cache::_MEM",
        "abicheck.m::scoped_cache::_SC",
        "abicheck.m::shared_scoped_cache::_SH",
        "abicheck.m::instance_memo::_IM",
        "abicheck.m::disk_cache::_DC",
        "abicheck.m::registered::_RS",
        "abicheck.m::memoized::f",
        "abicheck.m::header_scan::g",
        "abicheck.m::memory_cache::factory->MemoryCache",
        "abicheck.m::memoized_property::C.p",
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
    # The single-pair path shares the release cell's memos; reference mode
    # must have bypassed at least the comparison-scoped ones.
    assert ref.calls.get("abicheck.model.comparison_memo::scoped_cache::_MEMO")
    _assert_same(optimized, reference, "single_pair.memo")


def test_same_content_digest_memo_bypass_matches_default(
    monkeypatch: pytest.MonkeyPatch, release: tuple[Path, Path]
) -> None:
    """The run-scoped digest memo (``storage.snapshot_digest_cache``),
    consulted inside the scope the front end opens: a stored snapshot
    compared with a byte-identical copy reaches ``snapshot_content_digest``
    for both sides, and reference mode must route those lookups through the
    bypass and still produce the same report."""
    import shutil

    old, _new = release
    twin = old.parent / "twin.json"
    shutil.copyfile(old / "libm2.so.json", twin)
    args = (str(old / "libm2.so.json"), str(twin))
    optimized = _run(*args)
    ref = ReferenceMode()
    ref.install(monkeypatch, _memo_sites())
    reference = _run(*args)
    unreached = [k for k in _cell_sites(D) if not ref.calls.get(k)]
    assert not unreached, (
        f"COVERAGE claims cell {D} reaches these sites, but their bypass was never called: {unreached}"
    )
    _assert_same(optimized, reference, D)


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


def test_reference_mode_runs_every_pool_inline_and_matches_threads_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ABICHECK_REFERENCE_MODE=1`` alone -- no ``ABICHECK_MAX_THREADS`` --
    takes the sequential path: every pool is granted no worker thread (runs
    inline) and the release report equals the pooled one."""
    from abicheck.model.execution_cache import REFERENCE_MODE_ENV_VAR

    old, new = _release_dirs(tmp_path / "rel", members=8)
    parallel, parallel_grants = _threads_run(monkeypatch, old, new, "8")
    spy = GrantSpy()
    with monkeypatch.context() as mp:
        mp.delenv("ABICHECK_MAX_THREADS", raising=False)
        mp.setenv(REFERENCE_MODE_ENV_VAR, "1")
        mp.setattr(os, "cpu_count", lambda: 8)
        spy.install(mp)
        reference = _run(str(old), str(new))
    assert parallel_grants and max(parallel_grants) > 1, parallel_grants
    # The pooled run borrowed >1 thread; the reference run borrowed none --
    # no pool at all on the sequential member path, and any pool created on
    # the way (a nested one) was granted zero threads and ran inline.
    assert all(g == 0 for g in spy.grants), spy.grants
    _assert_same(parallel, reference, "release.reference_mode")


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


def test_reference_mode_never_serves_a_warm_disk_cache(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    """A warm whole-snapshot cache root is ignored in reference mode: the
    run misses every lookup, stores nothing, and reports what a cold run on
    a fresh root reports."""
    from abicheck.model.execution_cache import REFERENCE_MODE_ENV_VAR

    args = tuple(str(p) for p in binaries)
    warm_spy = DiskCacheSpy()
    with monkeypatch.context() as mp:
        warm_spy.install(mp, tmp_path / "warm")
        _run(*args)
        _run(*args)
    assert warm_spy.hits >= 2, warm_spy  # the root really is warm
    ref_spy = DiskCacheSpy()
    with monkeypatch.context() as mp:
        ref_spy.install(mp, tmp_path / "warm")
        mp.setenv(REFERENCE_MODE_ENV_VAR, "1")
        reference = _run(*args)
    assert ref_spy.hits == 0 and ref_spy.misses >= 2, ref_spy
    assert not any((tmp_path / "warm").glob("*.json.zst.tmp*"))
    with monkeypatch.context() as mp:
        DiskCacheSpy().install(mp, tmp_path / "fresh")
        fresh = _run(*args)
    _assert_same(reference, fresh, "disk.reference_mode")


# ── seeded mutants: the oracle must catch each historical bug shape ─────────


def _swap_everywhere(mp: pytest.MonkeyPatch, old: object, new: object) -> int:
    """Rebind every ``abicheck`` module global that is *old* to *new*."""
    n = 0
    for mod in list(sys.modules.values()):
        d = getattr(mod, "__dict__", None)
        if not d or not str(getattr(mod, "__name__", "")).startswith("abicheck"):
            continue
        for attr, val in list(d.items()):
            if val is old:
                mp.setattr(mod, attr, new)
                n += 1
    return n


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
    target = memoized_callable(
        "abicheck.name_classification::memoized::canonicalize_type_name"
    )
    mutant = _stale_key_memo(target.__wrapped__)
    n = _swap_everywhere(monkeypatch, target, mutant)
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
        if snap is not None and snap.declarations.functions:
            snap.declarations.functions = snap.declarations.functions[:-1]
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


# ── the production switch from the environment, end to end ──────────────────

_SUBPROCESS_CASES = (
    "case01_symbol_removal",
    "case33_pointer_level",
    "case40_field_layout",
    "case71_inline_namespace_moved",
)


def _cli_report(args: tuple[str, ...], env: dict[str, str]) -> str:
    res = subprocess.run(
        [sys.executable, "-m", "abicheck", "compare", *args, "-o", "json=-"],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    assert res.returncode in (0, 1, 2, 4), (res.returncode, res.stderr[-2000:])
    assert res.stdout.strip(), res.stderr[-2000:]
    return canonical_report(res.stdout)


def _entry_stamps(root: Path) -> dict[str, int]:
    return {
        str(p.relative_to(root)): p.stat().st_mtime_ns
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.slow
@pytest.mark.parametrize("case", _SUBPROCESS_CASES)
def test_catalog_reference_mode_from_the_environment_matches_default(
    tmp_path: Path, case: str
) -> None:
    """``ABICHECK_REFERENCE_MODE=1`` set in a child's environment -- no
    in-process patching at all -- produces the default report, and so does
    ``ABICHECK_MAX_THREADS=8``. Engagement is observed on disk: the reference
    run shares the default run's (warm) cache root and must neither add an
    entry nor touch one (a hit refreshes an entry's mtime for LRU)."""
    _cc()
    out = tmp_path / "bin"
    out.mkdir()
    try:
        v1, v2 = _build_case(case, out)
    except StopIteration:
        pytest.skip(f"{case} has no v1/v2 source pair")
    args = (str(v1), str(v2))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ABICHECK_")}
    warm_root = tmp_path / "cache-warm"
    default = _cli_report(args, {**base, "XDG_CACHE_HOME": str(warm_root)})
    warm = _cli_report(args, {**base, "XDG_CACHE_HOME": str(warm_root)})
    before = _entry_stamps(warm_root)
    assert before, "the default run stored no cache entry: nothing to bypass"
    reference = _cli_report(
        args,
        {**base, "XDG_CACHE_HOME": str(warm_root), "ABICHECK_REFERENCE_MODE": "1"},
    )
    assert _entry_stamps(warm_root) == before, (
        "a reference-mode run read or wrote the shared cache root"
    )
    threaded = _cli_report(
        args,
        {
            **base,
            "XDG_CACHE_HOME": str(tmp_path / "cache-threads"),
            "ABICHECK_MAX_THREADS": "8",
        },
    )
    _assert_same(warm, default, f"subprocess.warm[{case}]")
    _assert_same(reference, default, f"subprocess.reference[{case}]")
    _assert_same(threaded, default, f"subprocess.threads8[{case}]")


# ── (d') disk cache: vary exactly one key input ─────────────────────────────
#
# The cold/warm cell above runs the same operands twice, and its two
# operands differ in *several* key inputs at once, so a key that drops any
# single input still keys them apart (the H7 survivors
# cache_key_drops_binary_content / cache_key_drops_version). Each cell here
# changes exactly one input between a cold run and a warm run in the *same*
# root, and requires the warm run to equal a run on a fresh root. Engagement:
# the unchanged side must hit and the changed side must miss.


def _warm_vs_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prime: tuple[str, ...],
    probe: tuple[str, ...],
    before_probe: Callable[[], None] = lambda: None,
) -> tuple[str, str, DiskCacheSpy]:
    with monkeypatch.context() as mp:
        DiskCacheSpy().install(mp, tmp_path / "shared")
        _run(*prime)
    before_probe()
    warm_spy = DiskCacheSpy()
    with monkeypatch.context() as mp:
        warm_spy.install(mp, tmp_path / "shared")
        warm = _run(*probe)
    with monkeypatch.context() as mp:
        DiskCacheSpy().install(mp, tmp_path / "fresh")
        fresh = _run(*probe)
    return warm, fresh, warm_spy


def test_disk_cache_rebuilt_binary_in_place_is_not_served_stale(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    v1, v2 = binaries
    slot = tmp_path / "slot"
    slot.mkdir()
    old = slot / "libold.so"
    shutil.copyfile(v1, old)
    args = (str(old), str(v2))
    warm, fresh, spy = _warm_vs_fresh(
        monkeypatch,
        tmp_path,
        args,
        args,
        before_probe=lambda: shutil.copyfile(v2, old),  # same path, same label
    )
    assert spy.hits >= 1 and spy.misses >= 1, spy
    _assert_same(warm, fresh, "disk.rebuilt_in_place")


def test_disk_cache_version_label_alone_keys_apart(
    monkeypatch: pytest.MonkeyPatch, binaries: tuple[Path, Path], tmp_path: Path
) -> None:
    v1, v2 = binaries
    base = (str(v1), str(v2), "--version", "new=2.0")
    warm, fresh, spy = _warm_vs_fresh(
        monkeypatch,
        tmp_path,
        (*base, "--version", "old=1.0"),
        (*base, "--version", "old=9.0"),
    )
    assert spy.hits >= 1 and spy.misses >= 1, spy
    assert "9.0" in fresh, "the version label does not reach the report"
    _assert_same(warm, fresh, "disk.version_label")

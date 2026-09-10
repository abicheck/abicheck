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

"""Tests for the ADR-035 D10 typed scan API + dry-run cost estimate (G19.7).

Covers ``service.estimate_scan`` (the project cost probe) and the ``scan
--dry-run`` CLI surface (which reuses it) / the default one-build-audit
``scan`` flow (no ``--against``). Default lane — no compiler.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from scan_estimate_helpers import EstimateOperand, estimate

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def snap_path(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        elf=ElfMetadata(
            symbols=[ElfSymbol(name="_Z3foov"), ElfSymbol(name="_Z6secretv")]
        ),
    )
    p = tmp_path / "new.abi.json"
    p.write_text(snapshot_to_json(snap), encoding="utf-8")
    return p


@pytest.fixture
def header(tmp_path: Path) -> Path:
    h = tmp_path / "foo.h"
    h.write_text("#pragma pack(1)\nstruct X { virtual void v(); };\n", encoding="utf-8")
    return h


# ── service.estimate_scan ────────────────────────────────────────────────────


def test_estimate_pr_mode_layers(snap_path: Path) -> None:
    req = EstimateOperand(binaries=[snap_path], mode="pr")
    layers = {e.layer for e in estimate(req)}
    # pr = source-changed → intrinsic L0-L2 + L3 build + L4 replay + the L5 graph
    # fold and call-graph clang pass (both run for source-changed, so the estimate
    # must price them — Codex review).
    assert {
        "L0_binary",
        "L1_debug",
        "L2_header",
        "L3_build",
        "L4_source_abi",
        "L5_source_graph",
    } <= layers


def test_estimate_baseline_mode_includes_graph(snap_path: Path) -> None:
    req = EstimateOperand(binaries=[snap_path], mode="baseline")
    layers = {e.layer for e in estimate(req)}
    assert "L5_source_graph" in layers  # graph-full


def test_estimate_headers_depth_has_no_source_layers(snap_path: Path) -> None:
    req = EstimateOperand(binaries=[snap_path], depth="headers")
    layers = {e.layer for e in estimate(req)}
    assert layers == {"L0_binary", "L1_debug", "L2_header"}


def test_estimate_counts_compile_db_tus(snap_path: Path, tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": "a.cpp", "command": "c++ a.cpp", "directory": "."},
                {"file": "b.cpp", "command": "c++ b.cpp", "directory": "."},
                {"file": "a.cpp", "command": "c++ a.cpp -DX", "directory": "."},
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(binaries=[snap_path], compile_db=cdb, mode="baseline")
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert l3.tus == 2  # unique files
    assert l3.method == "s1"


def test_estimate_focused_replay_smaller_than_full(
    snap_path: Path, tmp_path: Path
) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(10)
            ]
        ),
        encoding="utf-8",
    )
    full = next(
        e
        for e in estimate(
            EstimateOperand(binaries=[snap_path], compile_db=cdb, mode="baseline")
        )
        if e.layer == "L4_source_abi"
    )
    focused = next(
        e
        for e in estimate(
            EstimateOperand(
                binaries=[snap_path],
                compile_db=cdb,
                mode="pr",
                changed_paths=["f1.cpp"],
            )
        )
        if e.layer == "L4_source_abi"
    )
    assert focused.tus < full.tus


def test_estimate_resolves_build_info_directory(
    snap_path: Path, tmp_path: Path
) -> None:
    # --build-info given as a build *directory* must resolve to its compile DB,
    # not report 0 TUs from an unreadable directory (Codex review).
    build = tmp_path / "build"
    build.mkdir()
    (build / "compile_commands.json").write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(7)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(binaries=[snap_path], build_info=build, mode="baseline")
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert l3.tus == 7


def test_estimate_finds_compile_db_in_nonhint_subdir(
    snap_path: Path, tmp_path: Path
) -> None:
    # A compile DB in a non-hint immediate subdirectory (cmake-build-debug-gcc/)
    # is found by the estimate via the same depth-1 fallback the real scan uses,
    # so --estimate mirrors execution instead of pricing L3 as absent (Codex).
    tree = tmp_path / "src"
    sub = tree / "cmake-build-debug-gcc"
    sub.mkdir(parents=True)
    (sub / "compile_commands.json").write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(5)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(binaries=[snap_path], sources=tree, mode="baseline")
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert l3.tus == 5


def test_estimate_header_change_fans_out_to_all_tus(
    snap_path: Path, tmp_path: Path
) -> None:
    # A changed header with no include graph fails open to all TUs in the real
    # scan, so the estimate must charge total_tus, not 1 (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(8)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(
        binaries=[snap_path],
        compile_db=cdb,
        mode="pr",
        changed_paths=["include/foo.h"],
    )
    l4 = next(e for e in estimate(req) if e.layer == "L4_source_abi")
    assert l4.tus == 8


def test_estimate_counts_collect_pack_tus(snap_path: Path, tmp_path: Path) -> None:
    # A --build-info pointed at an `abicheck collect` pack dir must count the
    # pack's build_evidence compile units, not report 0 (Codex review).
    from abicheck.buildsource import pack_io
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack

    pack_dir = tmp_path / "pack"
    be = BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://f{i}", source=f"f{i}.cpp", language="CXX")
            for i in range(4)
        ]
    )
    pack_io.write(BuildSourcePack(root=pack_dir, build_evidence=be))

    req = EstimateOperand(binaries=[snap_path], build_info=pack_dir, mode="baseline")
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert l3.tus == 4


def test_the_unpinned_level_the_dry_run_prices_is_the_one_the_run_executes(
    snap_path: Path, tmp_path: Path
) -> None:
    """`scan --dry-run` and `scan` resolve an omitted `--depth` identically.

    ADR-068's second 2026-09-09 amendment rules risk-driven `auto` depth
    selection (b) and names the fixed `headers` rung as the replacement --
    *not* the `--mode` preset, which is `(S5, SOURCE)` and would price (and
    run) a full source replay on every unpinned scan.

    `estimate_scan`'s own `mode` argument is a caller's explicit "price this
    preset" request, so the rule deliberately does not live there; the CLI
    pre-resolves its level and hands it over as `resolved_level`. That makes
    `resolve_unpinned_level` the single point the two share, which is what
    this pins: the projection taken at that level touches only the intrinsic
    L0-L2 layers, matching what `level_to_collect_mode` gives the real run.

    The seed-independence half is asserted end-to-end on the CLI, in
    `tests/test_cli_scan.py::test_unpinned_depth_resolves_to_headers_whatever_the_seed`.
    """
    from abicheck.model.evidence_depth_levels import (
        ScanMode,
        SourceScope,
        level_to_collect_mode,
        resolve_unpinned_level,
    )

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps([{"file": "a.cpp", "command": "c++", "directory": "."}]),
        encoding="utf-8",
    )
    for mode in (ScanMode.PR, ScanMode.AUDIT):
        method, depth = resolve_unpinned_level(mode)
        # What the real run would collect at that level: nothing beyond L2.
        for scope in (SourceScope.CHANGED, SourceScope.TARGET):
            assert level_to_collect_mode(method, depth, source_scope=scope) == "off"
        # ... and what the dry run prices for it, through the same pair.
        priced = {
            e.layer
            for e in estimate(
                EstimateOperand(binaries=[snap_path], compile_db=cdb, mode=mode.value),
                resolved_level=(method, depth),
            )
        }
        assert priced == {"L0_binary", "L1_debug", "L2_header"}


def test_estimate_inline_header_change_fans_out(
    snap_path: Path, tmp_path: Path
) -> None:
    # A changed .inl/.tcc inline header fans out to all TUs in the real replay
    # selector, so the estimate must charge total_tus (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(6)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(
        binaries=[snap_path],
        compile_db=cdb,
        mode="pr",
        changed_paths=["include/foo.inl"],
    )
    l4 = next(e for e in estimate(req) if e.layer == "L4_source_abi")
    assert l4.tus == 6


def test_estimate_compile_db_dedup_by_resolved_path(
    snap_path: Path, tmp_path: Path
) -> None:
    # Two TUs with the same relative `file` under different `directory` entries are
    # distinct and must not collapse on the bare basename (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": "main.cpp", "command": "c++", "directory": "/proj/a"},
                {"file": "main.cpp", "command": "c++", "directory": "/proj/b"},
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(binaries=[snap_path], compile_db=cdb, mode="baseline")
    l3 = next(e for e in estimate(req) if e.layer == "L3_build")
    assert l3.tus == 2


def test_estimate_budget_max_tus_caps_replay(snap_path: Path, tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(20)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(
        binaries=[snap_path], compile_db=cdb, mode="baseline", max_tus=5
    )
    l4 = next(e for e in estimate(req) if e.layer == "L4_source_abi")
    assert l4.tus == 5


def test_estimate_l4_uses_cold_realworld_anchor(
    snap_path: Path, tmp_path: Path
) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(4)
            ]
        ),
        encoding="utf-8",
    )
    req = EstimateOperand(binaries=[snap_path], compile_db=cdb, mode="baseline")
    l4 = next(e for e in estimate(req) if e.layer == "L4_source_abi")
    assert l4.tus == 4
    assert l4.est_seconds == pytest.approx(30.0)


# ── CLI: scan --dry-run / (default) audit ────────────────────────────────────


def test_cli_dry_run_scans_nothing(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    # --estimate was folded into the general --dry-run report (CLI
    # simplification); it still reuses service.estimate_scan under the hood, so
    # the per-layer TU/cost projection is still printed. An *unpinned* run
    # prices only the intrinsic L0-L2 rows now (ADR-068's second 2026-09-09
    # amendment: an omitted --depth resolves to the fixed `headers` rung, not
    # the `--mode` preset) -- pinning `--depth source` is what asks for the L4
    # replay row, and is asserted below so this still proves the dry run
    # projects real source cost when the run would incur it.
    res = runner.invoke(main, ["scan", str(snap_path), "-H", str(header), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "Dry run only" in res.output
    assert "L2_header" in res.output
    assert "L4_source_abi" not in res.output
    deep = runner.invoke(
        main,
        ["scan", str(snap_path), "-H", str(header), "--depth", "source", "--dry-run"],
    )
    assert deep.exit_code == 0, deep.output
    assert "L4_source_abi" in deep.output


def test_cli_dry_run_reports_projected_cost(runner: CliRunner, snap_path: Path) -> None:
    # scan --dry-run always renders as text (fmt only affects the informational
    # "format: ..." line), never the old --estimate JSON payload shape.
    res = runner.invoke(main, ["scan", str(snap_path), "--dry-run", "--format", "json"])
    assert res.exit_code == 0, res.output
    assert "format: json" in res.output
    assert "projected total:" in res.output
    assert "against: (none -- one-build audit only)" in res.output


# NOTE: test_estimate_pr_deep_preserves_graph_full_depth (--estimate --mode
# pr-deep) is deleted — --estimate/--mode are both gone, and pr-deep's (s5,
# graph) preset is unreachable from the CLI at all (the public --depth ladder
# stops at "source"; see test_pr_deep_is_distinct_from_pr's deletion note in
# test_cli_scan.py). The "resolved level is honored verbatim, not re-resolved"
# concern it guarded stays covered by test_estimate_scan_honors_resolved_level
# below, which drives service.estimate_scan directly.


def test_count_bazel_build_info_tus_branches(monkeypatch, tmp_path: Path) -> None:
    # Cover the helper's branches directly: non-file, non-Bazel (compile DB array),
    # the cquery route, and the best-effort guard that swallows any adapter/sniff
    # failure into None so the estimate never raises (Codex review).
    from abicheck.service_scan import _count_bazel_build_info_tus

    assert _count_bazel_build_info_tus(tmp_path / "nope.json") is None

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"file": "a.c", "command": "cc -c a.c", "directory": str(tmp_path)}]
        ),
        encoding="utf-8",
    )
    assert _count_bazel_build_info_tus(cdb) is None  # not Bazel → compile-DB path

    cq = tmp_path / "cq.json"
    cq.write_text(
        json.dumps({"results": [{"target": {"rule": {"name": "//foo:foo"}}}]}),
        encoding="utf-8",
    )
    assert _count_bazel_build_info_tus(cq) == 0  # cquery route, no compile actions

    def _boom(_p):
        raise RuntimeError("adapter blew up")

    monkeypatch.setattr("abicheck.buildsource.inline.sniff_build_info_format", _boom)
    assert _count_bazel_build_info_tus(cq) is None  # guard swallows the failure


def test_estimate_counts_bazel_build_info_tus(snap_path: Path, tmp_path: Path) -> None:
    # A Bazel aquery --build-info is replayed via BazelAdapter by the real scan, so
    # the estimate must count its compile actions instead of routing the JSON object
    # through the compile-DB counter and reporting 0 TUs (Codex review).

    aquery = tmp_path / "aq.json"
    aquery.write_text(
        json.dumps(
            {
                "artifacts": [
                    {"id": "1", "pathFragmentId": "10"},
                    {"id": "2", "pathFragmentId": "11"},
                ],
                "actions": [
                    {
                        "targetId": "100",
                        "mnemonic": "CppCompile",
                        "arguments": ["/usr/bin/gcc", "-std=c++17", "-c", "foo/foo.cc"],
                        "primaryOutputId": "2",
                    }
                ],
                "targets": [{"id": "100", "label": "//foo:foo"}],
                "pathFragments": [
                    {"id": "10", "label": "foo.cc", "parentId": "20"},
                    {"id": "11", "label": "foo.o", "parentId": "20"},
                    {"id": "20", "label": "foo"},
                ],
            }
        ),
        encoding="utf-8",
    )
    est = estimate(
        EstimateOperand(
            binaries=[snap_path], build_info=aquery, depth="source", mode="audit"
        )
    )
    l3 = next(e for e in est if e.layer == "L3_build")
    assert l3.tus >= 1  # the CppCompile action counted, not 0
    assert any("Bazel" in e.note for e in est)


def test_estimate_compile_db_overrides_bazel_build_info(
    snap_path: Path, tmp_path: Path
) -> None:
    # When both --compile-db and a Bazel --build-info are given, the real scan uses
    # `req.compile_db or req.build_info` (compile DB wins); the estimate must mirror
    # that and count the compile DB's TUs, not the Bazel action graph (Codex review).

    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": "a.c", "command": "cc -c a.c", "directory": str(tmp_path)},
                {"file": "b.c", "command": "cc -c b.c", "directory": str(tmp_path)},
            ]
        ),
        encoding="utf-8",
    )
    aq = tmp_path / "aq.json"  # a Bazel aquery with a single CppCompile (1 TU)
    aq.write_text(
        json.dumps(
            {
                "artifacts": [{"id": "2", "pathFragmentId": "11"}],
                "actions": [
                    {
                        "targetId": "100",
                        "mnemonic": "CppCompile",
                        "arguments": ["/usr/bin/gcc", "-c", "foo/foo.cc"],
                        "primaryOutputId": "2",
                    }
                ],
                "targets": [{"id": "100", "label": "//foo:foo"}],
                "pathFragments": [
                    {"id": "11", "label": "foo.o", "parentId": "20"},
                    {"id": "20", "label": "foo"},
                ],
            }
        ),
        encoding="utf-8",
    )
    est = estimate(
        EstimateOperand(
            binaries=[snap_path],
            compile_db=cdb,
            build_info=aq,
            depth="source",
            mode="audit",
        )
    )
    l3 = next(e for e in est if e.layer == "L3_build")
    assert l3.tus == 2  # the 2-TU compile DB, not the 1-action Bazel graph
    assert not any("Bazel" in e.note for e in est)


def test_estimate_binary_depth_suppresses_header_cost(
    snap_path: Path, header: Path
) -> None:
    # The estimate must mirror the real scan: --depth binary suppresses the L2
    # header AST, so the embedded ScanResult.estimate (and any direct caller) must
    # not price an L2_header layer for suppressed headers (Codex review).

    binary = estimate(
        EstimateOperand(
            binaries=[snap_path], depth="binary", headers=[header], mode="audit"
        )
    )
    l2 = next(e for e in binary if e.layer == "L2_header")
    assert l2.tus == 0
    assert l2.est_seconds == 0.0
    # Control: a --depth headers scan with the same header DOES price the L2 layer.
    headers_depth = estimate(
        EstimateOperand(
            binaries=[snap_path], depth="headers", headers=[header], mode="audit"
        )
    )
    l2b = next(e for e in headers_depth if e.layer == "L2_header")
    assert l2b.tus >= 1


def test_estimate_scan_honors_resolved_level(snap_path: Path) -> None:
    # estimate_scan honors a caller-supplied resolved (method, depth) verbatim: the
    # (s5, graph) pr-deep pair stays graph-full, whereas re-resolving the same req
    # applies precedence and collapses to plain S5 — source-target here since the
    # request carries no diff seed (ADR-043 D2/D3: unseeded S5 scopes to TARGET,
    # never silently to a zero-TU "source-changed" default) — Codex review.
    from abicheck.model.evidence_depth_levels import EvidenceDepth, SourceMethod

    req = EstimateOperand(
        binaries=[snap_path], mode="pr-deep", source_method="s5", depth="graph"
    )
    reresolved = " ".join(e.note for e in estimate(req))
    pinned = " ".join(
        e.note
        for e in estimate(
            req, resolved_level=(SourceMethod.S5, EvidenceDepth.GRAPH)
        )
    )
    assert "source-target" in reresolved  # the round-trip hazard this guards
    assert "graph-full" in pinned
    assert "source-target" not in pinned


def test_estimate_l2_cost_is_size_aware(snap_path: Path, tmp_path: Path) -> None:
    # A flat per-header anchor priced a one-line shim and a heavy templated
    # umbrella identically, so `scan --estimate` understated the cost of a large
    # public surface (field-eval P1: ICU/HDF5). The L2 estimate must scale with
    # header size: a big header costs strictly more than a tiny one.

    tiny = tmp_path / "tiny.h"
    tiny.write_text("void f(void);\n", encoding="utf-8")
    big = tmp_path / "big.h"
    big.write_text("void g(void);\n" * 20000, encoding="utf-8")  # ~260 KB

    def l2(header: Path) -> float:
        est = estimate(
            EstimateOperand(
                binaries=[snap_path], depth="headers", headers=[header], mode="audit"
            )
        )
        return next(e.est_seconds for e in est if e.layer == "L2_header")

    tiny_s, big_s = l2(tiny), l2(big)
    assert big_s > tiny_s
    # The size term should dominate for a large header (well above the per-header
    # base anchor), so the ranking is meaningful, not a rounding artefact.
    assert big_s > 0.5


def test_estimate_l2_two_headers_sum_their_sizes(
    snap_path: Path, tmp_path: Path
) -> None:

    d = tmp_path / "inc"
    d.mkdir()
    (d / "a.h").write_text("void a(void);\n" * 4000, encoding="utf-8")
    (d / "b.h").write_text("void b(void);\n" * 4000, encoding="utf-8")
    est = estimate(
        EstimateOperand(binaries=[snap_path], depth="headers", headers=[d], mode="audit")
    )
    l2 = next(e for e in est if e.layer == "L2_header")
    assert l2.tus == 2  # both headers counted
    assert (
        l2.est_seconds > 0.1
    )  # two non-trivial headers cost more than the base anchors


def test_estimate_header_seconds_falls_back_when_unstattable(tmp_path: Path) -> None:
    # A path that can't be stat'd (e.g. a dangling symlink) must not raise
    # mid-dry-run: the size term is skipped and only the per-header base counts.
    from abicheck.service_scan import _COST_PER_HEADER_PARSE, _estimate_header_seconds

    missing = tmp_path / "gone.h"  # never created
    seconds, high_risk = _estimate_header_seconds([missing])
    assert seconds == _COST_PER_HEADER_PARSE
    assert high_risk is False
    real = tmp_path / "real.h"
    real.write_text("void f(void);\n" * 1000, encoding="utf-8")
    # A real, sizeable header costs strictly more than the bare base anchor.
    seconds, high_risk = _estimate_header_seconds([real])
    assert seconds > _COST_PER_HEADER_PARSE
    assert high_risk is False


def test_estimate_header_seconds_flags_include_heavy_header(tmp_path: Path) -> None:
    """Many local #includes signal fan-out a flat size estimate can't see —
    P0 SVS field report: a small, pathological 3-header set dry-ran at 0.51s
    then took over 15,000s for real. The estimate must flag, not just size,
    such a header, and price it well above the plain size-based cost."""
    from abicheck.service_scan import _estimate_header_seconds

    heavy = tmp_path / "heavy.h"
    heavy.write_text(
        "".join(f'#include "dep{i}.h"\n' for i in range(20)), encoding="utf-8"
    )
    plain = tmp_path / "plain.h"
    plain.write_text("void f(void);\n", encoding="utf-8")

    heavy_seconds, heavy_risk = _estimate_header_seconds([heavy])
    plain_seconds, plain_risk = _estimate_header_seconds([plain])
    assert heavy_risk is True
    assert plain_risk is False
    assert heavy_seconds > plain_seconds * 10


def test_estimate_header_seconds_flags_template_heavy_header(tmp_path: Path) -> None:
    """Heavy template/concept usage is flagged even in a tiny header (no size
    signal at all) — the SVS pathological headers were small on disk."""
    from abicheck.service_scan import _estimate_header_seconds

    heavy = tmp_path / "heavy.h"
    heavy.write_text(
        "template <class T> struct A { template <class U> requires true "
        "constexpr U f(); };\n"
        "template <class T> concept C = requires(T t) { t.f(); };\n"
        "template <class T> constexpr bool enable_if_v = true;\n",
        encoding="utf-8",
    )
    _seconds, high_risk = _estimate_header_seconds([heavy])
    assert high_risk is True


def test_estimate_scan_l2_note_flags_high_risk_headers(
    snap_path: Path, tmp_path: Path
) -> None:
    """The dry-run's L2_header note must say so — not just a bare number —
    when a header trips the complexity signal (P0 acceptance: no falsely
    precise ETA for a pathological header)."""
    d = tmp_path / "inc"
    d.mkdir()
    (d / "heavy.h").write_text(
        "".join(f'#include "dep{i}.h"\n' for i in range(20)), encoding="utf-8"
    )
    est = estimate(
        EstimateOperand(binaries=[snap_path], depth="headers", headers=[d], mode="audit")
    )
    l2 = next(e for e in est if e.layer == "L2_header")
    assert "conservative" in l2.note
    assert "--budget" in l2.note


def _minimal_compile_db(tmp_path: Path) -> Path:
    """A minimal compile_commands.json (L3 build metadata; pure parsing).

    Supplies source evidence so a pinned deep --source-method does not trip
    auto-strict (ADR-037 D5: a pinned depth with no source input errors).
    """
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    return cdb


def test_replay_seed_empty_without_diff_seed(
    monkeypatch, runner: CliRunner, snap_path: Path, header: Path, tmp_path: Path
) -> None:
    # No --since/--changed-path → broad scope. Pattern-trigger POIs must NOT
    # narrow the replay seed (would skip source-only checks in other TUs) — the
    # seed stays empty so collect_inline_pack keeps the broad fallback (Codex).
    # _build_new_snapshot lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs

    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["changed_paths"] = kwargs.get("changed_paths")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(snap_path),
            "-H",
            str(header),
            "--depth",
            "source",
            "--build-info",
            str(_minimal_compile_db(tmp_path)),
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["changed_paths"] == ()


def test_replay_seed_used_when_changed_path_given(
    monkeypatch, runner: CliRunner, snap_path: Path, header: Path, tmp_path: Path
) -> None:
    # An explicit --changed-path is a real diff seed → the POI floor feeds the
    # replay scope.
    # _build_new_snapshot lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs

    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["changed_paths"] = kwargs.get("changed_paths")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(snap_path),
            "-H",
            str(header),
            "--depth",
            "source",
            "--build-info",
            str(_minimal_compile_db(tmp_path)),
            "--changed-path",
            "src/a.cpp",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "src/a.cpp" in (captured["changed_paths"] or ())


def test_seeded_empty_diff_scans_nothing(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    # --since HEAD is a *seeded* but empty diff (no-op PR). The pattern pre-scan
    # must honour the empty scope (scan nothing) rather than fall back to a
    # whole-tree scan that would surface unrelated pattern triggers (Codex).
    res = runner.invoke(
        main,
        [
            "scan",
            str(snap_path),
            "-H",
            str(header),
            "--since",
            "HEAD",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["pattern_scan"]["files_scanned"] == 0
    # No pattern triggers → no pattern-trigger POIs from a no-op PR.
    assert payload["poi"]["counts_by_reason"].get("pattern_trigger", 0) == 0


def test_cli_audit_emits_hygiene_catalog(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    # Absence of --against already means a one-build audit (no separate flag).
    res = runner.invoke(main, ["scan", str(snap_path), "-H", str(header)])
    assert res.exit_code == 0
    assert "ABI-hygiene catalog" in res.output
    # The accidental export _Z6secretv is flagged.
    assert "exported_not_public" in res.output


def test_cli_audit_json_carries_poi(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    res = runner.invoke(
        main,
        [
            "scan",
            str(snap_path),
            "-H",
            str(header),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert "poi" in payload
    assert payload["poi"]["version"] == 1


def test_cli_scan_json_carries_scan_schema_version(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    """The CLI's ``scan --format json`` contract (ScanOutcome.to_dict) must
    carry a schema-version marker, same as compare's ``report_schema_version``,
    so external consumers can pin/validate the envelope (P1.5)."""
    from abicheck.schemas import SCAN_SCHEMA_VERSION

    res = runner.invoke(
        main,
        ["scan", str(snap_path), "-H", str(header), "--format", "json"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION

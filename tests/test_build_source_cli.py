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

"""CLI tests for `collect`, `dump --evidence`, and
`compare --old/--new-build-info` (ADR-028 D6 / ADR-029).

ADR-043 CLI reset (commit 308880c) deleted the `collect`/`merge`/
`recommend-collect-mode` Click commands from `abicheck/cli_buildsource.py`.
Every *other* function those commands drove is still present and used by
`dump`/`compare`'s inline collection, so most of this file's tests were
rewritten to call those surviving library functions directly instead of
invoking a Click command that no longer exists.

The `collect` command's own orchestration body (`collect_cmd`) and its
`--source-abi`/`--source-abi-extractor android` helpers (`_collect_source_abi`,
`_collect_source_abi_android`, `_source_abi_scope_needs_include_map`) were
*not* preserved anywhere in `abicheck/` — they were deleted outright, not just
unwired from Click (unlike what the commit message implies). The lower-level
primitives they called (`select_source_backend`, `AndroidHeaderAbiAdapter`,
`run_source_replay`, `ClangIncludeExtractor`, ...) are all still present and
functional, so `_collect`/`_collect_source_abi`/`_collect_source_abi_android`/
`_source_abi_scope_needs_include_map` below are test-local reimplementations of
the deleted orchestration (verbatim logic ported from `git show 308880c --
abicheck/cli_buildsource.py`), so the tests that exercise them keep exercising
real, still-shipped code paths. Note this file is the *only* remaining caller
of that orchestration shape (see the task report) — worth flagging upstream as
a capability that is no longer reachable from any command."""
from __future__ import annotations

import datetime as _dt
import json
import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from abicheck import __version__ as _abicheck_version
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.merge_support import _detect_merge_layer_conflicts
from abicheck.buildsource.model import ExtractorRecord
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.redaction import DEFAULT_REDACTION
from abicheck.cli import main
from abicheck.cli_buildsource import (
    _build_coverage,
    _collect_source_graph,
    _echo_collection_summary,
    _enforce_strict_mode,
    _merge_attach_combined,
    _merge_fold_packs,
    _merge_handle_conflicts,
    _merge_load_snapshots,
    _merge_pick_base,
    _merge_print_summary,
    _run_adapters,
    parse_from_specs,
)
from abicheck.model import AbiSnapshot
from abicheck.schemas import REPORT_SCHEMA_VERSION
from abicheck.serialization import load_snapshot, save_snapshot, snapshot_to_json


def _include_map_for_replay(merged, clang_bin, scope):
    """Test-local port of the deleted `collect`-flow `_include_map_for_replay`
    (per-TU include graph for L4 replay scoping over `BuildEvidence`, distinct
    from the source-tree-oriented helper of the same name now living in
    `abicheck.buildsource.inline`, used by `dump --sources`)."""
    from abicheck.buildsource.include_graph import (
        ClangIncludeExtractor,
        include_map_from_recorded_inputs,
    )

    recorded = include_map_from_recorded_inputs(merged)
    if recorded and scope != "headers-only":
        return recorded
    extractor = ClangIncludeExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    if not extractor.available():
        return None
    includes = extractor.extract_from_build(merged)
    for diag in extractor.diagnostics:
        merged.diagnostics.append(f"source_abi_include_graph: {diag}")
    return includes or None


_SOURCE_ABI_DIRECT_SOURCE_EXTS = (
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".cu", ".m", ".mm",
)


def _source_abi_scope_needs_include_map(scope, changed_paths):
    """Test-local port of the deleted `collect`-flow scope->include-map gate."""
    if scope == "headers-only":
        return True
    if scope != "changed":
        return False
    return not all(
        path.lower().replace("\\", "/").endswith(_SOURCE_ABI_DIRECT_SOURCE_EXTS)
        for path in changed_paths
    )


def _collect_source_abi_android(
    android_dump, extractors, *, target_id, exported, library, roots
):
    """Test-local port of the deleted `_collect_source_abi_android`."""
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_extractors import (
        AndroidHeaderAbiAdapter,
        SourceExtractionError,
    )
    from abicheck.buildsource.source_link import link_source_abi

    if android_dump is None:
        raise click.UsageError(
            "--source-abi-extractor android requires --android-dump <file.lsdump|.sdump>."
        )
    adapter = AndroidHeaderAbiAdapter()
    try:
        tu = adapter.load(android_dump, target_id=target_id, public_header_roots=roots)
    except SourceExtractionError as exc:
        extractors.append(
            ExtractorRecord(
                name="source_abi:android",
                status="failed",
                inputs=[DEFAULT_REDACTION.path(str(android_dump))],
                detail=str(exc),
            )
        )
        return SourceAbiSurface(library=library, target_id=target_id), f"failed: {exc}"
    surface = link_source_abi(
        [tu], exported_symbols=exported, library=library, target_id=target_id,
    )
    extractors.append(
        ExtractorRecord(
            name="source_abi:android",
            status="ok",
            inputs=[DEFAULT_REDACTION.path(str(android_dump))],
            detail=f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types",
        )
    )
    return surface, (
        f"android dump: {len(surface.reachable_declarations)} decls, "
        f"{len(surface.reachable_types)} types"
    )


def _collect_source_abi(
    merged, extractors, *, extractor, scope, target_id, changed_paths,
    android_dump, cache_dir, clang_bin, headers, binary, verbose,
):
    """Test-local port of the deleted `_collect_source_abi` (collect-flow L4)."""
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_replay import (
        SourceAbiCache,
        public_header_roots_for,
        run_source_replay,
    )
    from abicheck.cli_buildsource import _exported_symbols_from_binary

    exported = _exported_symbols_from_binary(binary)
    library = str(binary) if binary else ""

    if scope == "off" or (scope == "changed" and not changed_paths):
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="partial",
                detail=f"scope {scope!r} selects no translation units; nothing to replay",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            f"no-op: scope {scope!r} selects no translation units",
        )

    roots = [str(h) for h in headers] or public_header_roots_for(merged, target_id)

    if extractor == "android":
        return _collect_source_abi_android(
            android_dump, extractors, target_id=target_id, exported=exported,
            library=library, roots=roots,
        )

    from abicheck.buildsource.source_extractors import select_source_backend

    choice, impl = select_source_backend(extractor, clang_bin=clang_bin)
    if impl is None or choice.selected is None:
        detail = "; ".join(f"{n}: {why}" for n, why in choice.skipped) or choice.reason
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"no usable source-ABI backend ({detail}); source-only checks disabled",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            "unavailable: no source-ABI front-end on PATH (clang/castxml) — "
            "source-only checks disabled. Install clang or castxml.",
        )

    extractor = choice.selected
    tool_name = clang_bin if choice.selected == "clang" else "castxml"
    merged.diagnostics.append(f"source_abi: {choice.reason}")
    if choice.capability_gaps:
        merged.diagnostics.append(f"source_abi: {choice.gap_note()}")

    if not merged.compile_units:
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="skipped",
                detail="no compile units in build evidence; collect L3 first (e.g. --compile-db)",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            "skipped: no L3 build context (need compile units to replay)",
        )
    if not impl.available():
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"{tool_name} not found in PATH; source-only checks disabled",
            )
        )
        return (
            SourceAbiSurface(library=library, target_id=target_id),
            f"unavailable: {tool_name} not on PATH — source-only checks disabled "
            "(macros, default args, inline/template/constexpr bodies). Install "
            f"{tool_name} or omit --source-abi.",
        )

    include_map = (
        _include_map_for_replay(merged, clang_bin, scope)
        if _source_abi_scope_needs_include_map(scope, changed_paths)
        else None
    )
    cache = SourceAbiCache(cache_dir) if cache_dir else None
    surface, diagnostics = run_source_replay(
        merged, impl, scope=scope, changed_paths=changed_paths, target_id=target_id,
        library=library, exported_symbols=exported, public_header_roots=roots,
        cache=cache, include_map=include_map,
    )
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    detail = (
        f"scope={scope}, {parsed}/{selected} TUs parsed, {len(diagnostics)} failures"
    )
    if cache is not None and cache.hit_rate is not None:
        detail += f", cache_hit_rate={cache.hit_rate:.0%} ({cache.hits}/{cache.hits + cache.misses})"
    extractors.append(
        ExtractorRecord(
            name=f"source_abi:{extractor}",
            status="ok" if parsed else "partial",
            detail=detail,
        )
    )
    return surface, (
        f"{extractor} extractor, scope={scope}: parsed {parsed}/{selected} TUs, "
        f"{len(surface.reachable_declarations)} decls, {len(surface.reachable_types)} types, "
        f"{len(surface.reachable_inline_bodies)} inline bodies, "
        f"{len(surface.reachable_templates)} templates"
        + (f", {len(diagnostics)} TU(s) failed (partial coverage)" if diagnostics else "")
    )


def _collect(
    output,
    *,
    binary=None,
    headers=(),
    build_dir=None,
    compile_db=None,
    compile_db_p=None,
    from_adapters=(),
    read_compiler_record=False,
    build_system="generic",
    source_abi=False,
    source_abi_extractor="auto",
    source_abi_scope="target",
    source_abi_target="",
    changed_paths=(),
    android_dump=None,
    source_abi_cache=None,
    clang_bin="clang",
    source_graph="off",
    kythe_entries=None,
    codeql_results=None,
    codeql_extends_results=None,
    source_root=None,
    allow_build_query=False,
    collection_mode="permissive",
    verbose=False,
):
    """Test-local re-implementation of the deleted `collect` Click command's
    body (ADR-043 CLI reset), calling the surviving library functions directly.
    Mirrors `collect_cmd`'s exact call sequence (see `git show 308880c --
    abicheck/cli_buildsource.py`) but as a plain function instead of a Click
    command; `output` is the previously-required `-o/--output`. Returns the
    written `BuildSourcePack`."""
    effective_compile_db = compile_db or compile_db_p
    extractors: list[ExtractorRecord] = []
    merged = BuildEvidence()
    record_bazel_inputs = source_abi and (
        source_graph == "summary"
        or bool(kythe_entries)
        or bool(codeql_results)
        or bool(codeql_extends_results)
        or _source_abi_scope_needs_include_map(source_abi_scope, list(changed_paths))
    )
    adapters = parse_from_specs(from_adapters)

    _run_adapters(
        merged,
        extractors,
        compile_db=effective_compile_db,
        build_dir=build_dir,
        cmake=bool(adapters["cmake"]),
        ninja=bool(adapters["ninja"]),
        ninja_compdb=adapters["ninja_compdb"],
        bazel_cquery=adapters["bazel_cquery"],
        bazel_aquery=adapters["bazel_aquery"],
        make_dry_run=adapters["make_dry_run"],
        binary=binary,
        read_compiler_record=read_compiler_record,
        build_system=build_system,
        record_bazel_inputs=record_bazel_inputs,
        verbose=verbose,
    )

    surface = None
    source_detail = ""
    if source_abi:
        surface, source_detail = _collect_source_abi(
            merged,
            extractors,
            extractor=source_abi_extractor,
            scope=source_abi_scope,
            target_id=source_abi_target,
            changed_paths=list(changed_paths),
            android_dump=android_dump,
            cache_dir=source_abi_cache,
            clang_bin=clang_bin,
            headers=headers,
            binary=binary,
            verbose=verbose,
        )

    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph=source_graph,
        changed_paths=changed_paths,
        kythe_entries=kythe_entries,
        codeql_results=codeql_results,
        codeql_extends_results=codeql_extends_results,
        surface=surface,
        clang_bin=clang_bin,
    )

    pack = BuildSourcePack.empty(
        output,
        abicheck_version=_abicheck_version,
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    red = DEFAULT_REDACTION
    pack.manifest.extractors = extractors
    pack.manifest.inputs = {
        "binary": red.path(str(binary)) if binary else None,
        "headers": [red.path(str(h)) for h in headers],
        "build_dir": red.path(str(build_dir)) if build_dir else None,
        "collection_mode": collection_mode,
    }
    has_build = bool(
        merged.compile_units
        or merged.targets
        or merged.toolchains
        or merged.link_units
        or merged.build_options
    )
    if has_build:
        pack.build_evidence = merged
    if surface is not None:
        pack.source_abi = surface
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = _build_coverage(
        merged, has_build, surface, source_detail, graph, graph_detail
    )
    pack.write()

    _enforce_strict_mode(extractors, merged, collection_mode)
    _echo_collection_summary(
        pack,
        merged,
        output,
        has_build=has_build,
        source_abi=source_abi,
        source_detail=source_detail,
        graph=graph,
        graph_detail=graph_detail,
    )
    return pack


def _merge(inputs, output, on_conflict="warn"):
    """Test-local re-implementation of the deleted `merge` Click command's body
    (ADR-043 CLI reset). `inputs` is a tuple of `Path`s (matching the old
    positional args); `output` is the previously-required `-o/--output`.
    Returns the written base `AbiSnapshot`."""
    snaps = _merge_load_snapshots(inputs)
    base_path, base = _merge_pick_base(snaps)

    conflicts = _detect_merge_layer_conflicts(snaps)
    combined, contributors = _merge_fold_packs(snaps)

    _merge_handle_conflicts(conflicts, combined, on_conflict)

    if combined is None:
        click.echo(
            "Note: no input carried embedded build_source facts; the merged "
            "baseline is the base snapshot's ABI surface only.",
            err=True,
        )
    else:
        _merge_attach_combined(combined, base, output)

    output.write_text(snapshot_to_json(base), encoding="utf-8")
    _merge_print_summary(base_path, contributors, len(snaps), combined, output)
    return base


def _write_cdb(tmp_path, std):
    cdb = [{
        "directory": str(tmp_path),
        "file": "src/foo.cpp",
        "arguments": ["c++", f"-std={std}", "-Iinclude", "-c", "src/foo.cpp"],
    }]
    p = tmp_path / f"cc_{std}.json"
    p.write_text(json.dumps(cdb))
    return p


def test_collect_evidence_creates_pack(tmp_path, capsys):
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "libfoo.evidence"
    pack = _collect(out, compile_db=cdb)
    assert "Evidence pack written" in capsys.readouterr().out
    pack = BuildSourcePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.compile_units) == 1
    cov = pack.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_redacts_manifest_paths(tmp_path, monkeypatch):
    """Codex: provenance paths in manifest.json are home-redacted before write."""
    # Pretend tmp_path is under the user's home so redaction rewrites it.
    monkeypatch.setenv("HOME", str(tmp_path))
    from abicheck.buildsource.redaction import RedactionPolicy
    policy = RedactionPolicy(home_replacements={str(tmp_path): "~"})
    # `_collect` (this test file's port of the deleted `collect` command body)
    # redacts the binary/input paths using its own imported `DEFAULT_REDACTION`
    # binding, but the extractor-manifest rows are built by helpers living in
    # abicheck.cli_buildsource_helpers that read that module's own binding.
    # Patch both so every manifest path is redacted before write.
    monkeypatch.setattr(sys.modules[__name__], "DEFAULT_REDACTION", policy)
    monkeypatch.setattr(
        "abicheck.cli_buildsource_helpers.DEFAULT_REDACTION", policy
    )
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "e"
    _collect(out, compile_db=cdb, binary=tmp_path / "libfoo.so")
    manifest = json.loads((out / "manifest.json").read_text())
    # No absolute tmp_path leaks into the manifest provenance.
    blob = json.dumps(manifest)
    assert str(tmp_path) not in blob
    assert manifest["inputs"]["binary"].startswith("~")
    assert any(e["inputs"] and e["inputs"][0].startswith("~") for e in manifest["extractors"])


# NOTE: `test_collect_evidence_requires_output` was deleted here (ADR-043 CLI
# reset). It asserted that Click's `-o/--output required=True` rejected an
# invocation with no `-o` — a pure Click-declaration artifact with no
# library-level equivalent now that `collect` is not a Click command (there is
# no `-o` flag to omit; `_collect`'s `output` is a plain required positional
# parameter, and calling it with a missing argument is a `TypeError`, not a
# meaningful behavior to test).


def test_collect_evidence_cmake_requires_build_dir(tmp_path):
    with pytest.raises(click.UsageError, match="build-dir"):
        _collect(tmp_path / "e", from_adapters=("cmake",))


def test_parse_from_specs_maps_adapters(tmp_path):
    """The unified `--from adapter[=path]` parses into the per-adapter kwargs."""
    from abicheck.cli_buildsource_helpers import parse_from_specs

    got = parse_from_specs((
        "cmake", "ninja",
        f"ninja-compdb={tmp_path / 'c.json'}",
        f"bazel-cquery={tmp_path / 'cq.json'}",
        f"bazel-aquery={tmp_path / 'aq.json'}",
        f"make={tmp_path / 'dry.txt'}",
    ))
    assert got["cmake"] is True and got["ninja"] is True
    assert got["ninja_compdb"] == tmp_path / "c.json"
    assert got["bazel_cquery"] == tmp_path / "cq.json"
    assert got["bazel_aquery"] == tmp_path / "aq.json"
    assert got["make_dry_run"] == tmp_path / "dry.txt"
    # Empty specs → all defaults (no adapter requested).
    empty = parse_from_specs(())
    assert empty["cmake"] is False and empty["ninja_compdb"] is None


@pytest.mark.parametrize(
    "spec, needle",
    [
        ("cmake=foo", "takes no '=path'"),       # live adapter rejects a path
        ("ninja=foo", "takes no '=path'"),
        ("make", "requires a pre-captured path"),  # pre-captured needs a path
        ("bazel-cquery", "requires a pre-captured path"),
        ("bogus", "unknown adapter"),
    ],
)
def test_parse_from_specs_rejects_bad_specs(spec, needle):
    import click as _click

    from abicheck.cli_buildsource_helpers import parse_from_specs

    with pytest.raises(_click.UsageError) as exc:
        parse_from_specs((spec,))
    assert needle in str(exc.value)


def test_parse_from_specs_rejects_duplicate_adapter():
    """A repeated `--from` adapter is rejected, not silently last-wins."""
    import click as _click

    from abicheck.cli_buildsource_helpers import parse_from_specs

    with pytest.raises(_click.UsageError) as exc:
        parse_from_specs(("bazel-aquery=a.json", "bazel-aquery=b.json"))
    assert "more than once" in str(exc.value)


def test_collect_from_bogus_adapter_is_usage_error(tmp_path):
    """The bad-spec error surfaces through the live `collect` flow too."""
    with pytest.raises(click.UsageError, match="unknown adapter"):
        _collect(tmp_path / "e", from_adapters=("nope",))


def test_dump_attach_evidence_ref(tmp_path):
    # Build an evidence pack first.
    cdb = _write_cdb(tmp_path, "c++20")
    ev_dir = tmp_path / "e"
    _collect(ev_dir, compile_db=cdb)

    # Attach it to an existing snapshot via dump on a JSON snapshot is not
    # supported (dump takes a binary), so attach directly through the helper
    # path exercised by `dump --evidence`: load pack and to_ref.
    pack = BuildSourcePack.load(ev_dir)
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.build_source_pack = pack.to_ref(path_hint=str(ev_dir))
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)

    reloaded = load_snapshot(out)
    assert reloaded.build_source_pack is not None
    assert reloaded.build_source_pack.content_hash == pack.content_hash()


def test_dump_empty_build_info_dir_is_noop(tmp_path):
    # Source-tree-centric model: a plain directory with no manifest and no
    # compile DB is a build dir that yields no L3 facts — graceful, not an error
    # (ADR-028 D3). Nothing is embedded.
    bad = tmp_path / "bad"
    bad.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    save_snapshot(snap, tmp_path / "s.json")

    from abicheck.cli_buildsource import embed_build_source

    embed_build_source(snap, bad, None)
    assert snap.build_source is None


def test_dump_malformed_pack_dir_errors(tmp_path):
    # A directory *with* a manifest.json is treated as a pack; a malformed one
    # is still a hard error so a corrupt collect output is not silently ignored.
    import click
    import pytest

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{ this is not json", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")

    from abicheck.cli_buildsource import embed_build_source

    with pytest.raises(click.ClickException):
        embed_build_source(snap, bad, None)


def _make_snap(tmp_path, name, version):
    snap = AbiSnapshot(library="libfoo.so", version=version, from_headers=True)
    p = tmp_path / name
    save_snapshot(snap, p)
    return p


def test_compare_with_source_graph_packs_runs_graph_diff(tmp_path):
    """ADR-031: two --source-graph packs drive the graph-diff wiring in
    diff_embedded_build_source (folded into the verdict pipeline). Build-only
    graphs yield no graph findings, but the L5 coverage must read present and
    the comparison must still succeed."""
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    _collect(ev_old, compile_db=old_cdb, source_graph="summary")
    _collect(ev_new, compile_db=new_cdb, source_graph="summary")
    assert BuildSourcePack.load(ev_old).source_graph is not None
    assert BuildSourcePack.load(ev_new).source_graph is not None

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 1, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L5_source_graph"]["status"] == "present"


def test_compare_with_evidence_emits_coverage_and_findings(tmp_path):
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    _collect(ev_old, compile_db=old_cdb)
    _collect(ev_new, compile_db=new_cdb)

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--format", "markdown",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    # D7 coverage table is emitted to stderr.
    assert "Evidence coverage:" in result.stderr
    assert "Evidence coverage by side:" in result.stderr
    assert "old=present" in result.stderr
    assert "new=present" in result.stderr
    assert "L3 build context" in result.stderr
    # The -std drift surfaces as an ABI-relevant build-flag finding (RISK).
    assert "COMPATIBLE_WITH_RISK" in result.stdout or "Deployment Risk" in result.stdout


def test_compare_json_carries_layer_coverage_block(tmp_path):
    """ADR-028 D7: the JSON report carries a structured layer_coverage block."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    _collect(ev_new, compile_db=cdb)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--build-info", "new=" + str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert set(cov) >= {"L0", "L1", "L2", "L3_build", "L4_source_abi", "L5_source_graph"}
    assert cov["L3_build"]["status"] == "present"


def test_compare_asymmetric_old_only_reports_target_not_collected(tmp_path):
    """Only --old-build-info: the target (new) side has no build facts, so the
    coverage table must report L3 not_collected — not reuse the old pack and
    claim source/build checks ran for this scan (Codex review)."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    runner = CliRunner()
    _collect(ev_old, compile_db=cdb)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--build-info", "old=" + str(ev_old),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L3_build"]["status"] == "not_collected"
    assert "Evidence coverage by side:" in result.stderr
    assert "L3 build context" in result.stderr
    assert "old=present" in result.stderr
    assert "new=not_collected" in result.stderr
    assert "(asymmetric)" in result.stderr


def test_compare_json_without_evidence_omits_coverage(tmp_path):
    """No evidence → no layer_coverage key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "layer_coverage" not in json.loads(result.stdout)


def test_compare_json_carries_evidence_metrics_block(tmp_path):
    """ADR-033 D6/D9: the JSON report carries an evidence_metrics block with
    collection timing and the artifact-backed vs source-only finding split."""
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    _collect(ev_old, compile_db=old_cdb)
    _collect(ev_new, compile_db=new_cdb)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    metrics = json.loads(result.stdout)["evidence_metrics"]
    # Timing is measured and non-negative; coverage flags reflect the run.
    assert isinstance(metrics["extractor.duration_seconds"], (int, float))
    assert metrics["extractor.duration_seconds"] >= 0
    assert metrics["coverage.build_context.present"] is True
    # The -std drift is a build-context-drift finding, not a source-only one.
    assert metrics["findings.build_context_drift.count"] >= 1
    assert metrics["findings.source_only.count"] == 0
    # And the D6 timing summary is echoed to stderr alongside the coverage table.
    assert "Evidence metrics:" in result.stderr


def test_evidence_metrics_bucket_counts_are_post_suppression(tmp_path):
    """ADR-033 D9 (Codex review): a suppressed build-drift finding must drop out
    of findings.build_context_drift.count so the buckets partition the *reported*
    findings, not the pre-suppression set."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    supp = tmp_path / "supp.yaml"
    supp.write_text(
        "version: 1\n"
        "suppressions:\n"
        "  - change_kind: abi_relevant_build_flag_changed\n"
        "    symbol_pattern: '.*'\n"
        "    reason: known std bump\n"
        "  - change_kind: header_parse_context_drift\n"
        "    symbol_pattern: '.*'\n"
        "    reason: known parse-context drift\n"
    )
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--suppress", str(supp), "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    metrics = json.loads(result.stdout)["evidence_metrics"]
    # The only build finding was suppressed → it must not be counted.
    assert metrics["findings.build_context_drift.count"] == 0


def test_compare_json_without_evidence_omits_metrics(tmp_path):
    """No evidence → no evidence_metrics key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "evidence_metrics" not in json.loads(result.stdout)


def test_evidence_metrics_helpers_edge_branches(capsys):
    """ADR-033 D6/D9 helper edge cases: empty-metrics no-ops, the
    missing-duration echo path, and the _layer_status fallback."""
    from abicheck.buildsource.evidence_policy import (
        _layer_status,
        echo_evidence_metrics,
    )
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.checker_types import DiffResult, Verdict
    from abicheck.cli_buildsource import attach_evidence_metrics

    # Unknown layer → not_collected fallback (no rows for L5).
    rows = [LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT)]
    assert _layer_status(rows, DataLayer.L5_SOURCE_GRAPH) == "not_collected"

    # Empty metrics: attach is a no-op, nothing is echoed.
    result = DiffResult(old_version="1", new_version="2", library="l", verdict=Verdict.NO_CHANGE)
    attach_evidence_metrics(result, {}, [])
    assert result.evidence_metrics == {}
    echo_evidence_metrics({})
    assert capsys.readouterr().err == ""

    # Metrics without a measured duration still echo the findings line.
    echo_evidence_metrics({"findings.source_only.count": 2})
    err = capsys.readouterr().err
    assert "Evidence metrics:" in err
    assert "collection time" not in err
    assert "source-only=2" in err


def test_evidence_metrics_excludes_probe_matrix_from_artifact_backed(tmp_path):
    """ADR-033 D9 (Codex review): probe-matrix findings are injected via
    extra_changes but are build-config/source-level, not L0-L2 artifact-backed,
    so they must not inflate findings.artifact_backed.count on a mixed run."""
    # Probe matrices whose only delta is a raised C++ standard floor (17 -> 20),
    # which surfaces as a probe-matrix finding (cxx_standard_floor_raised).
    def _matrix(path, version, stds):
        path.write_text(json.dumps({
            "library": "libfoo", "version": version, "spec_name": "libfoo",
            "cxx_stds": stds, "defaults": {"backend": "tbb"}, "results": [],
        }))

    pm_old = tmp_path / "pm_old.json"
    pm_new = tmp_path / "pm_new.json"
    _matrix(pm_old, "1.0", {"a": 17, "b": 20})
    _matrix(pm_new, "2.0", {"b": 20, "c": 23})

    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    _collect(ev_new, compile_db=new_cdb)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--build-info", "new=" + str(ev_new),
        "--probe-matrix", "old=" + str(pm_old), "--probe-matrix", "new=" + str(pm_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 1, 2, 4), result.output
    payload = json.loads(result.stdout)
    metrics = payload["evidence_metrics"]
    # The probe-matrix finding is reported (it is in result.changes) ...
    kinds = {c["kind"] for c in payload["changes"]}
    assert "cxx_standard_floor_raised" in kinds
    # ... but it is not counted as artifact-backed. These ELF-less snapshots have
    # no L0-L2 diff, so the only artifact-backed count here must be zero.
    assert metrics["findings.artifact_backed.count"] == 0


def _two_build_packs(tmp_path, runner=None):
    """Two build-info packs whose only delta is a C++ std bump (17 -> 20),
    yielding an abi_relevant_build_flag_changed finding (RISK by default)."""
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    _collect(ev_old, compile_db=_write_cdb(tmp_path, "c++17"))
    _collect(ev_new, compile_db=_write_cdb(tmp_path, "c++20"))
    return ev_old, ev_new


def test_evidence_policy_build_drift_fail_on_abi_relevant_escalates(tmp_path):
    """ADR-033 D7: build_context_drift: fail-on-abi-relevant escalates the
    ABI-relevant std-flag drift from RISK (exit 0) to API_BREAK (exit 2)."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  build_context_drift: fail-on-abi-relevant\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 2, result.output  # API_BREAK
    payload = json.loads(result.stdout)
    assert payload["verdict"] in ("API_BREAK", "source_break")


def test_evidence_policy_build_drift_default_is_risk(tmp_path):
    """Without the knob the same std drift stays a non-failing risk (exit 0)."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
    ])
    assert result.exit_code == 0, result.output


def test_require_evidence_fails_when_layer_absent(tmp_path):
    """ADR-033 D7 require_evidence: a mandatory-but-absent layer fails the run
    with an evidence_required_missing (API_BREAK) finding, even with no packs."""
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  require_evidence:\n    build_context: true\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 2, result.output  # API_BREAK
    payload = json.loads(result.stdout)
    kinds = {c["kind"] for c in payload["changes"]}
    assert "evidence_required_missing" in kinds
    # D9: the failure is counted on its own metric, not lost (Codex review).
    assert payload["evidence_metrics"]["findings.evidence_required_missing.count"] == 1


def test_require_evidence_fails_when_layer_only_on_target_side(tmp_path):
    """A required evidence layer must be comparable on both sides."""
    runner = CliRunner()
    ev_new = tmp_path / "new.evidence"
    _collect(ev_new, compile_db=_write_cdb(tmp_path, "c++20"))
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  require_evidence:\n    build_context: true\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--build-info", "new=" + str(ev_new),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    changes = [c for c in payload["changes"] if c["kind"] == "evidence_required_missing"]
    assert len(changes) == 1
    assert "baseline side" in changes[0]["description"]


def test_require_evidence_satisfied_when_layer_comparable(tmp_path):
    """When the required layer is present on both sides, no finding."""
    runner = CliRunner()
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    _collect(ev_old, compile_db=_write_cdb(tmp_path, "c++20"))
    _collect(ev_new, compile_db=_write_cdb(tmp_path, "c++20"))
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  require_evidence:\n    build_context: true\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    kinds = {c["kind"] for c in json.loads(result.stdout)["changes"]}
    assert "evidence_required_missing" not in kinds


def test_evidence_policy_invalid_action_rejected(tmp_path):
    """An out-of-range evidence_policy action is a clear policy-file error."""
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  graph_risk_findings: maybe\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--policy-file", str(pol),
    ])
    assert result.exit_code != 0
    assert "graph_risk_findings" in result.output


def _source_tree(tmp_path):
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "foo.cpp").write_text("int f(){return 0;}\n")
    (tree / "compile_commands.json").write_text(json.dumps([{
        "directory": str(tree), "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]))
    return tree


def test_dump_collect_mode_build_collects_l3_only(tmp_path):
    """ADR-033 D2/Phase-1: `dump --depth build` captures L3 build context
    only — no L4 source replay or L5 graph."""
    tree = _source_tree(tmp_path)
    out = tmp_path / "s.json"
    result = CliRunner().invoke(main, [
        "dump", "--sources", str(tree), "--depth", "build", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    bs = load_snapshot(out).build_source
    assert bs is not None and bs.build_evidence is not None
    assert bs.source_abi is None and bs.source_graph is None
    cov = {(c.layer if isinstance(c.layer, str) else c.layer.value): c.status.value
           for c in bs.manifest.coverage}
    assert cov["L3_build"] == "present"
    assert cov["L4_source_abi"] == "not_collected"
    assert cov["L5_source_graph"] == "not_collected"


def test_dump_collect_mode_build_filters_pre_captured_pack(tmp_path):
    """ADR-033 D2 (Codex review): `--depth build` must strip L4/L5 from a
    pre-captured pack too, so an L3-only run can't smuggle in source evidence."""
    runner = CliRunner()
    cdb = _write_cdb(tmp_path, "c++17")
    ev = tmp_path / "full.ev"
    _collect(ev, compile_db=cdb, source_graph="summary")
    assert BuildSourcePack.load(ev).source_graph is not None  # full pack
    out = tmp_path / "s.json"
    result = runner.invoke(main, [
        "dump", "--build-info", str(ev), "--depth", "build", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    bs = load_snapshot(out).build_source
    assert bs.build_evidence is not None       # L3 kept
    assert bs.source_abi is None               # L4 stripped
    assert bs.source_graph is None             # L5 stripped


def test_source_abi_cache_hit_rate_instrumented(tmp_path):
    """ADR-033 D9: the per-TU SourceAbiCache tracks hits/misses → hit_rate."""
    from abicheck.buildsource.source_abi import SourceAbiTu
    from abicheck.buildsource.source_replay import SourceAbiCache

    cache = SourceAbiCache(tmp_path / "cache")
    assert cache.hit_rate is None              # no lookups yet
    assert cache.get("missing-key") is None    # miss
    cache.put("k1", SourceAbiTu(tu_id="cu://x", source="f.cpp"))
    assert cache.get("k1") is not None         # hit
    assert cache.get(None) is None             # uncacheable, not counted
    assert cache.hits == 1 and cache.misses == 1
    assert cache.hit_rate == 0.5


def test_recommend_collect_mode_cli():
    """ADR-033 D3: recommend_collect_mode maps changed paths to a collection mode.

    The `recommend-collect-mode` Click command was deleted in the ADR-043 CLI
    reset (its behavior folded into command-aware source-scope resolution +
    `scan --dry-run`); the underlying library function is unchanged, so this
    calls it directly instead of via `CliRunner`."""
    from abicheck.buildsource.source_replay import recommend_collect_mode

    assert recommend_collect_mode(["CMakeLists.txt"]) == "build"
    assert recommend_collect_mode(["src/a.cpp"]) == "source-changed"
    assert recommend_collect_mode(["README.md"]) == "off"
    assert recommend_collect_mode([]) == "off"


def test_dump_collect_mode_off_embeds_nothing(tmp_path):
    """A depth rung that resolves to collect mode "off" collects no source
    evidence even with a source tree.

    CLI-audit P1: previously exercised via ``dump --sources tree --depth
    binary`` (both "headers" and "binary" resolve to the "off" collect
    mode, ADR-037 D5) -- but a source-only dump (no SO_PATH) structurally
    has no binary at all, so an *explicit* ``--depth binary`` there is now
    itself a usage error (external review: it used to let an empty,
    fact-less snapshot silently "satisfy" the floor rung), and ``--depth
    headers`` fails the pre-existing strict depth gate the same way (a
    source-only dump never reaches 'headers' either). Neither CLI spelling
    can reach this collect_mode="off" suppression path for a source-only
    dump anymore -- which is the correct, tighter invariant -- so this
    now calls ``dump_source_only`` directly with collect_mode="off" and
    depth=None (bypassing the CLI depth-string validation, which is a
    separate concern from the "off" embedding-suppression behavior this
    test actually cares about)."""
    from abicheck.cli_buildsource import dump_source_only

    tree = _source_tree(tmp_path)
    out = tmp_path / "s.json"
    dump_source_only(
        sources=tree, build_info=None, version="1.0", output=out,
        build_config=None, allow_build_query=False, git_tag=None,
        build_id=None, no_git=True, collect_mode="off", depth=None,
    )
    assert load_snapshot(out).build_source is None


def test_compare_collect_mode_without_packs_is_noted(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--depth", "build",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "'build'" in result.stderr


def test_compare_without_evidence_is_unchanged(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap)])
    assert result.exit_code == 0, result.output
    assert "Evidence coverage:" not in result.stderr


# -- L4 source ABI replay (ADR-030 phases 5-7 + CLI wiring) ------------------


def test_collect_evidence_source_abi_graceful_without_tool(tmp_path, monkeypatch, capsys):
    """Source ABI replay degrades gracefully when the tool is missing.

    The user message must be explicit that clang is required and that source-only
    checks are disabled (never abort the collection).
    """
    from abicheck.buildsource.source_extractors.resolver import SourceExtractorChoice

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.select_source_backend",
        lambda extractor, *, clang_bin: (
            SourceExtractorChoice(
                selected=None,
                skipped=[("clang", "not on PATH"), ("castxml", "not on PATH")],
                reason="no backend available",
            ),
            None,
        ),
    )
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    _collect(
        out, compile_db=cdb, source_abi=True, source_abi_scope="full",
        clang_bin="clang-definitely-not-installed-xyz",
    )
    assert "source-only checks disabled" in capsys.readouterr().out
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    # Replay ran but the tool was absent → partial, not present (and not silent).
    assert cov is not None and cov.status.value == "partial"


def test_collect_evidence_source_abi_android_dump(tmp_path):
    """The Android backend normalizes a pre-captured dump into the pack (D9)."""
    dump = tmp_path / "libfoo.lsdump"
    dump.write_text(json.dumps({
        "source_file": "include/foo.h",
        "functions": [{"function_name": "foo", "linker_set_key": "_Z3foov", "return_type": "void"}],
        "record_types": [{"name": "Foo", "size": 8, "source_file": "include/foo.h"}],
    }))
    out = tmp_path / "ev"
    _collect(
        out, source_abi=True, source_abi_extractor="android", android_dump=dump,
    )
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "Foo" for e in pack.source_abi.reachable_types)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_android_requires_dump(tmp_path):
    with pytest.raises(click.UsageError, match="requires --android-dump"):
        _collect(
            tmp_path / "ev", source_abi=True, source_abi_extractor="android",
        )


def _ev_with_default_arg(tmp_path, name, default):
    """Write an evidence pack whose L4 surface has one function with a default arg."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )
    from abicheck.buildsource.source_link import link_source_abi

    ent = SourceEntity(
        id="id", kind="function", qualified_name="add", mangled_name="_Z3addii",
        signature_hash="sig", value=default,
        source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
        visibility="public_header", api_relevant=True,
    )
    tu = SourceAbiTu(tu_id="cu://a", functions=[ent], public_header_roots=["include/foo.h"])
    pack = BuildSourcePack.empty(tmp_path / name)
    pack.source_abi = link_source_abi([tu], library="libfoo.so")
    pack.write()
    return tmp_path / name


def test_compare_source_abi_findings_and_capabilities(tmp_path):
    """An L4 default-argument change surfaces as a finding, and the capability
    report explains which checks ran and which did not (the user's ask)."""
    ev_old = _ev_with_default_arg(tmp_path, "old.evidence", "x=1")
    ev_new = _ev_with_default_arg(tmp_path, "new.evidence", "x=2")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--build-info", "old=" + str(ev_old), "--build-info", "new=" + str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    # The source-replay finding is folded into the verdict pipeline.
    assert "default_argument_changed" in result.stdout.lower()
    # Authority rule (ADR-028 D3): a source-only L4 finding with no artifact-backed
    # break must NOT escalate to a breaking verdict — it stays API/source-level.
    assert payload["verdict"] != "breaking"
    kinds = {f.get("kind") for f in payload.get("changes", [])}
    assert "default_argument_changed" in kinds
    # And the L4 finding is partitioned as an API break, never a BREAKING kind.
    from abicheck.checker_policy import BREAKING_KINDS, ChangeKind
    assert ChangeKind.DEFAULT_ARGUMENT_CHANGED not in BREAKING_KINDS
    # The capability report names what is on/off and why.
    assert "Checks enabled for this scan" in result.stderr
    assert "[off]" in result.stderr
    # Macros/default-args/bodies row references its source/clang requirement.
    assert "inline/template/constexpr" in result.stderr


def _fake_clang_extractor():
    """A drop-in ClangSourceExtractor replacement that needs no real clang."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )

    class _Fake:
        name = "clang-source"
        version = "0.1"

        def __init__(self, **kw):
            pass

        def available(self):
            return True

        def extract(self, cu, *, public_header_roots, target_id=""):
            ent = SourceEntity(
                id="e", kind="function", qualified_name="add",
                mangled_name="_Z3addi", signature_hash="sig", value="p0=1",
                source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
                visibility="public_header", api_relevant=True,
            )
            return SourceAbiTu(
                tu_id=cu.id, source=cu.source,
                public_header_roots=list(public_header_roots), functions=[ent],
            )

    return _Fake


def test_collect_evidence_source_abi_success(tmp_path, monkeypatch, capsys):
    """The clang collection path writes a populated L4 surface and PRESENT row."""
    import abicheck.buildsource.source_extractors as se
    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())

    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    _collect(
        out, compile_db=cdb, source_abi=True, source_abi_scope="full",
        source_abi_cache=tmp_path / "cache",
    )
    assert "L4 source ABI replay: clang extractor" in capsys.readouterr().out
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "add" for e in pack.source_abi.reachable_declarations)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_include_map_for_replay_helper(monkeypatch):
    """_include_map_for_replay returns the depfile map, or None when clang is absent.

    `_include_map_for_replay` is this test file's own port of the deleted
    `collect`-flow helper (no longer importable from `abicheck.cli_buildsource`
    — see the module docstring)."""
    import abicheck.buildsource.include_graph as ig
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    class _Avail:
        clang_bin = "clang++"

        def __init__(self, **kw):
            self.diagnostics = []

        def available(self):
            return True

        def extract_from_build(self, build):
            return {"cu://a": ["include/foo.h"]}

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Avail)
    assert _include_map_for_replay(BuildEvidence(), "clang", "headers-only") == {
        "cu://a": ["include/foo.h"]
    }

    recorded = BuildEvidence(compile_units=[
        CompileUnit(id="cu://recorded", source="foo.cc", input_files=["foo.cc", "foo.h"]),
    ])
    assert _include_map_for_replay(recorded, "clang", "changed") == {
        "cu://recorded": ["foo.cc", "foo.h"]
    }
    assert _include_map_for_replay(recorded, "clang", "headers-only") == {
        "cu://a": ["include/foo.h"]
    }

    class _Unavail(_Avail):
        def available(self):
            return False

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Unavail)
    assert _include_map_for_replay(BuildEvidence(), "clang", "headers-only") is None


def test_collect_evidence_source_abi_uses_include_graph(tmp_path, monkeypatch):
    """headers-only/changed scopes feed the depfile include map into replay."""
    import abicheck.buildsource.source_extractors as se

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())
    monkeypatch.setattr(
        sys.modules[__name__], "_include_map_for_replay",
        lambda merged, clang_bin, scope: {"cu://x": ["include/foo.h"]},
    )
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    pack = _collect(
        out, compile_db=cdb, source_abi=True, source_abi_scope="headers-only",
    )
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert pack.source_abi.coverage.get("include_graph_used") is True


def test_collect_evidence_source_abi_changed_source_skips_include_graph(
    tmp_path, monkeypatch
):
    """A changed source is selected directly; depfile fan-out is wasted work."""
    import abicheck.buildsource.source_extractors as se

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())

    def _boom(merged, clang_bin, scope):
        raise AssertionError("include graph should not run for source-only changes")

    monkeypatch.setattr(sys.modules[__name__], "_include_map_for_replay", _boom)
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    pack = _collect(
        out, compile_db=cdb, source_abi=True, source_abi_scope="changed",
        changed_paths=("src/foo.cpp",),
    )
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert pack.source_abi.coverage.get("include_graph_used") is False


def test_collect_evidence_source_abi_changed_header_uses_include_graph(
    tmp_path, monkeypatch
):
    """Header changes still need the depfile map for precise affected-TU replay."""
    import abicheck.buildsource.source_extractors as se

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())
    monkeypatch.setattr(
        sys.modules[__name__],
        "_include_map_for_replay",
        lambda merged, clang_bin, scope: {"cu://src/foo.cpp#cfg": ["include/foo.h"]},
    )
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    pack = _collect(
        out, compile_db=cdb, source_abi=True, source_abi_scope="changed",
        changed_paths=("include/foo.h",),
    )
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert pack.source_abi.coverage.get("include_graph_used") is True


def test_collect_evidence_source_abi_changed_non_source_paths_use_include_graph(
    tmp_path, monkeypatch
):
    """Unknown/header-like changed paths need depfiles to find affected TUs."""
    import abicheck.buildsource.source_extractors as se

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())
    cdb = _write_cdb(tmp_path, "c++17")
    for changed_path in ("include/foo.cuh", "include/public"):
        out = tmp_path / f"ev-{changed_path.rsplit('/', 1)[-1]}"
        monkeypatch.setattr(
            sys.modules[__name__],
            "_include_map_for_replay",
            lambda merged, clang_bin, scope, p=changed_path: {merged.compile_units[0].id: [p]},
        )
        pack = _collect(
            out, compile_db=cdb, source_abi=True, source_abi_scope="changed",
            changed_paths=(changed_path,),
        )
        pack = BuildSourcePack.load(out)
        assert pack.source_abi is not None
        assert pack.source_abi.coverage.get("include_graph_used") is True
        assert pack.source_abi.coverage.get("compile_units_selected") == 1


def test_collect_evidence_source_abi_castxml_unavailable(tmp_path):
    """The castxml backend degrades gracefully when castxml is absent."""
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    _collect(out, compile_db=cdb, source_abi=True, source_abi_extractor="castxml")
    # Either castxml ran (present) or it was unavailable (graceful) — both fine,
    # but the run must not crash and must record an L4 row.
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value in ("present", "partial")


def test_collect_evidence_source_abi_without_compile_units(tmp_path, capsys):
    """--source-abi with no L3 build context reports the missing prerequisite."""
    out = tmp_path / "ev"
    _collect(out, source_abi=True, source_abi_extractor="clang")
    assert "no L3 build context" in capsys.readouterr().out


def test_collect_evidence_source_abi_without_compile_units_strict_fails(tmp_path):
    """Strict mode fails loud when an explicitly-requested L4 layer is empty.

    Permissive mode (above) still exits 0; under --collection-mode strict the
    empty source-ABI layer is a "skipped" extractor and must fail the command
    rather than silently passing on an empty requested layer.
    """
    out = tmp_path / "ev"
    with pytest.raises(click.ClickException, match="strict collection mode"):
        _collect(
            out, source_abi=True, source_abi_extractor="clang",
            collection_mode="strict",
        )


def test_collect_evidence_source_abi_noop_scope_strict_passes(tmp_path):
    """A no-op replay scope must not false-fail strict mode on absent L3.

    `--source-abi-scope off` selects zero translation units by design, so a
    missing compile DB is not a missing prerequisite — strict mode must still
    exit 0 (the skipped-on-empty rule applies only to scopes that consume units).
    """
    out = tmp_path / "ev"
    pack = _collect(
        out, source_abi=True, source_abi_extractor="clang",
        source_abi_scope="off", collection_mode="strict",
    )
    # Didn't raise (strict mode passed) and the no-op scope is recorded.
    assert any(e.status != "failed" for e in pack.manifest.extractors)


def test_collect_evidence_source_abi_noop_scope_android_no_dump(tmp_path):
    """A no-op scope short-circuits before the Android branch and its dump check.

    `--source-abi-extractor android --source-abi-scope off` must not raise the
    missing-`--android-dump` usage error, since the off scope selects zero TUs
    and needs neither a dump nor a frontend; strict mode still exits 0.
    """
    out = tmp_path / "ev"
    pack = _collect(
        out, source_abi=True, source_abi_extractor="android",
        source_abi_scope="off", collection_mode="strict",
    )
    # Didn't raise the missing --android-dump usage error, and strict mode
    # (which fails loud on a genuinely skipped requested layer) still passed.
    assert any(e.status != "failed" for e in pack.manifest.extractors)


def test_exported_symbols_from_binary_edge_cases(tmp_path):
    from pathlib import Path

    from abicheck.cli_buildsource import _exported_symbols_from_binary
    assert _exported_symbols_from_binary(None) == []
    assert _exported_symbols_from_binary(Path(tmp_path / "missing")) == []
    junk = tmp_path / "x.txt"
    junk.write_text("not a binary")
    assert _exported_symbols_from_binary(junk) == []


# ── Source-tree-centric inline collection (ADR-028..033 amendment) ────────────


def test_embed_build_info_compile_db_inline(tmp_path):
    """`--build-info compile_commands.json` collects L3 inline (no pack dir)."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, cdb, None)

    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1
    cov = snap.build_source.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_embed_build_info_preserves_preexisting_header_only_graph(tmp_path):
    """A `dump --header-graph` pass attaches a header-only L5 pack to
    `snap.build_source` before `embed_build_source` runs (mirrors
    `service._attach_header_graph`, called ahead of `write_snapshot_output`).
    Combining `--header-graph` with `--build-info` under the L3-only
    `collect_mode="build"` (no L4/L5 attempted at all) must not silently drop
    that graph just because this embed step's own merged pack carries no L5 of
    its own (Codex review)."""
    from pathlib import Path

    from abicheck.buildsource.model import (
        CoverageStatus,
        DataLayer,
        LayerConfidence,
        LayerCoverage,
    )
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    snap = AbiSnapshot(library="libfoo.so", version="1")

    graph = SourceGraphSummary(nodes=[GraphNode(id="d:foo", kind="function")])
    header_pack = BuildSourcePack(root=Path(""), source_graph=graph)
    header_pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PARTIAL,
            confidence=LayerConfidence.UNKNOWN,
        ),
    ]
    snap.build_source = header_pack

    embed_build_source(snap, cdb, None, collect_mode="build")

    # L3 facts from --build-info landed as normal ...
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1
    # ... and the pre-existing header-only graph survived instead of being
    # silently overwritten by the merged (graph-less) pack.
    assert snap.build_source.source_graph is graph
    cov = snap.build_source.manifest.coverage_for("L5_source_graph")
    assert cov is not None and cov.status == CoverageStatus.PARTIAL


def test_embed_build_info_backfilled_graph_changes_content_hash(tmp_path):
    """The backfilled source_graph must actually be reflected in
    build_source.content_hash() (via snap.build_source_pack), not silently
    excluded because merged.manifest.artifacts still lists only the
    pre-backfill (graph-less) digests. BuildSourcePack.content_hash() prefers
    a non-empty manifest.artifacts over recomputing it, so two otherwise-
    identical embeds that backfill genuinely different graphs must not
    collide on the same content hash (Codex review)."""
    from pathlib import Path

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_buildsource import embed_build_source

    def _embed_with_graph(node_id: str) -> str:
        cdb = _write_cdb(tmp_path, "c++17")
        snap = AbiSnapshot(library="libfoo.so", version="1")
        graph = SourceGraphSummary(nodes=[GraphNode(id=node_id, kind="function")])
        snap.build_source = BuildSourcePack(root=Path(""), source_graph=graph)
        embed_build_source(snap, cdb, None, collect_mode="build")
        assert snap.build_source is not None
        assert snap.build_source.source_graph is graph
        return snap.build_source.content_hash()

    hash_a = _embed_with_graph("d:foo")
    hash_b = _embed_with_graph("d:bar")
    assert hash_a != hash_b


def test_embed_build_info_backfills_graph_with_no_preexisting_coverage_row(tmp_path):
    """Same backfill as test_embed_build_info_preserves_preexisting_header_only_graph,
    but the pre-existing pack carries source_graph with no matching L5 coverage
    row in its manifest at all (a degenerate/hand-built pack, unlike the normal
    service._attach_header_graph output which always sets one). The backfill
    must still adopt the graph itself and fall back to leaving no L5 row behind
    (rather than raising) when there is no row to carry over."""
    from pathlib import Path

    from abicheck.buildsource.model import DataLayer
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    snap = AbiSnapshot(library="libfoo.so", version="1")

    graph = SourceGraphSummary(nodes=[GraphNode(id="d:foo", kind="function")])
    header_pack = BuildSourcePack(root=Path(""), source_graph=graph)
    # No manifest.coverage rows at all -- graph_row lookup finds nothing.
    assert header_pack.manifest.coverage == []
    snap.build_source = header_pack

    embed_build_source(snap, cdb, None, collect_mode="build")

    assert snap.build_source is not None
    assert snap.build_source.source_graph is graph
    cov = snap.build_source.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH.value)
    assert cov is None
    # L3 facts from --build-info still landed normally.
    assert snap.build_source.build_evidence is not None


def test_embed_build_info_autodiscovers_compile_db_in_tree(tmp_path):
    """A compile DB inside the --sources tree is auto-discovered for L3."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # No --build-info: the tree's compile_commands.json is found automatically.
    embed_build_source(snap, None, tree)
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_embed_sources_without_tool_is_graceful(tmp_path):
    """`--sources` with a compile DB but no clang yields partial L4, not abort."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # clang is almost certainly absent under the fast unit lane; replay degrades
    # to partial coverage and the dump still succeeds (ADR-028 D3).
    embed_build_source(snap, None, tree, clang_bin="definitely-not-a-real-clang")
    assert snap.build_source is not None
    l4 = snap.build_source.manifest.coverage_for("L4_source_abi")
    assert l4 is not None and l4.status.value in ("partial", "present")


def test_build_query_skipped_when_config_untrusted(tmp_path):
    """An arbitrary build.query from an untrusted (auto-discovered) config is not
    executed (ADR-032 amended): the --allow-build-query flag is gone, but the
    trust gate remains — only an explicit --config / --build-query runs a query."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    cfg = BuildConfig(query="this-tool-should-never-run --emit", compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg,
        build_config_trusted_for_query=False,
    )
    # The untrusted query is not executed; no facts are collected. The pack
    # survives only to carry the skipped-query diagnostic (A3).
    assert pack is not None
    assert pack.build_evidence is None  # no L3 facts
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "skipped"]


def test_auto_discovered_build_query_is_not_executed(tmp_path):
    """Source-tree .abicheck.yml may be untrusted, so queries need --config."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    marker = tree / "query-ran.txt"
    (tree / "payload.py").write_text(
        "from pathlib import Path\nPath('query-ran.txt').write_text('ran')\n",
        encoding="utf-8",
    )
    (tree / ".abicheck.yml").write_text(
        "build:\n"
        f"  query: {json.dumps(f'{sys.executable} payload.py')}\n"
        "  compile_db: compile_commands.json\n",
        encoding="utf-8",
    )
    (tree / "compile_commands.json").write_text(
        json.dumps([{
            "directory": str(tree),
            "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
        }]),
        encoding="utf-8",
    )

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(
        snap, None, tree, allow_build_query=True,
        clang_bin="definitely-not-a-real-clang",
    )

    assert not marker.exists()
    assert snap.build_source is not None
    assert any(
        record.name == "build_query"
        and record.status == "skipped"
        and "auto-discovered" in (record.detail or "")
        for record in snap.build_source.manifest.extractors
    )


def test_merge_combines_binary_and_source_snapshots(tmp_path):
    """`merge` keeps the binary base and folds in the source side's L3 facts."""
    from abicheck.cli_buildsource import embed_build_source

    # Source/build side: a snapshot carrying only L3 build facts.
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(src_snap, _write_cdb(tmp_path, "c++17"), None)
    src_path = tmp_path / "libfoo.src.json"
    save_snapshot(src_snap, src_path)

    # Binary side: a snapshot with an ABI surface (faked ELF marker) and no pack.
    from abicheck.elf_metadata import ElfMetadata

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    _merge((bin_path, src_path), out)

    merged = load_snapshot(out)
    assert merged.elf is not None          # base ABI surface preserved
    assert merged.build_source is not None  # source-side facts folded in
    assert merged.build_source.build_evidence is not None


# ── inline.py pure-logic coverage (no external tools) ─────────────────────────


def test_build_config_from_dict_and_load(tmp_path):
    from abicheck.buildsource.inline import (
        BuildConfig,
        discover_build_config,
        load_build_config,
    )

    cfg = BuildConfig.from_dict({
        "build": {"system": "bazel", "query": "bazel cquery //x", "compile_db": "out/cc.json"},
        "sources": {"public_headers": ["a/**.hpp"], "exclude": "**/test/**"},
    })
    assert cfg.system == "bazel"
    assert cfg.query.startswith("bazel cquery")
    assert cfg.compile_db == "out/cc.json"
    assert cfg.public_headers == ["a/**.hpp"]
    assert cfg.exclude == ["**/test/**"]

    # Empty input falls back to all-defaults.
    assert BuildConfig.from_dict({}).system == "auto"
    # ADR-043 CLI reset: a block key given a scalar (not a mapping) is a hard
    # error now, not a silent coercion to `{}` (this used to be exactly the gap
    # the now-removed `abicheck config validate` command existed to catch).
    with pytest.raises(ValueError, match="build must be a mapping"):
        BuildConfig.from_dict({"build": "nope"})

    # load_build_config: missing file → defaults; present file → parsed.
    assert load_build_config(tmp_path / "nope.yml").system == "auto"
    p = tmp_path / ".abicheck.yml"
    p.write_text("build:\n  system: cmake\n", encoding="utf-8")
    assert load_build_config(p).system == "cmake"
    # A YAML scalar (not a mapping) is tolerated.
    p.write_text("just a string\n", encoding="utf-8")
    assert load_build_config(p).system == "auto"

    # discover_build_config finds .abicheck.yml at the tree root.
    tree = tmp_path / "src"
    tree.mkdir()
    assert discover_build_config(tree) is None
    (tree / ".abicheck.yml").write_text("build: {}\n", encoding="utf-8")
    assert discover_build_config(tree) == tree / ".abicheck.yml"
    assert discover_build_config(None) is None


def test_is_pack_dir_and_compile_db_resolution(tmp_path):
    from abicheck.buildsource.inline import (
        _autodiscover_compile_db,
        _compile_db_at,
        is_pack_dir,
    )

    assert is_pack_dir(None) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_pack_dir(plain) is False
    # A valid manifest.json WITHOUT the BuildSourcePack marker is a stray file,
    # not a pack — collect from the tree instead of mis-loading an empty pack.
    (plain / "manifest.json").write_text("{}", encoding="utf-8")
    assert is_pack_dir(plain) is False
    # The version marker makes it a real pack.
    (plain / "manifest.json").write_text(
        '{"build_source_pack_version": 1}', encoding="utf-8"
    )
    assert is_pack_dir(plain) is True
    # A present-but-corrupt manifest stays a (corrupt) pack so the load errors loudly.
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert is_pack_dir(corrupt) is True

    # _compile_db_at: a build dir with build/compile_commands.json is found.
    bd = tmp_path / "bd"
    (bd / "build").mkdir(parents=True)
    cdb = bd / "build" / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")
    assert _compile_db_at(bd) == cdb
    assert _compile_db_at(tmp_path / "empty-missing") is None

    # auto-discovery inside a source tree (top-level).
    tree = tmp_path / "src"
    tree.mkdir()
    assert _autodiscover_compile_db(tree) is None
    top = tree / "compile_commands.json"
    top.write_text("[]", encoding="utf-8")
    assert _autodiscover_compile_db(tree) == top
    assert _autodiscover_compile_db(None) is None


def test_build_inline_coverage_rows():
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage

    rows = build_inline_coverage(BuildEvidence(), has_build=False, surface=None, graph=None)
    by = {r.layer: r for r in rows}
    assert by["L3_build"].status.value == "not_collected"
    assert by["L4_source_abi"].status.value == "not_collected"
    assert by["L5_source_graph"].status.value == "not_collected"


def test_l4_coverage_row_reports_tu_and_cache_counts():
    # ADR-035 P5: the live L4 coverage row must carry the TU/symbol/cache counts,
    # not print a bare "partial" with an empty detail.
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

    surface = SourceAbiSurface(
        reachable_types=[SourceEntity(id="t1", kind="record", qualified_name="W")],
        coverage={
            "replay_scope": "headers-only",
            "compile_units_selected": 4,
            "compile_units_parsed": 3,
            "matched_symbols": 7,
            "exported_symbols": 10,
            "cache_hits": 2,
            "cache_misses": 1,
            "extractor_failures": 1,
        },
    )
    rows = {
        r.layer: r
        for r in build_inline_coverage(
            BuildEvidence(), has_build=False, surface=surface, graph=None
        )
    }
    detail = rows["L4_source_abi"].detail
    assert "scope=headers-only" in detail
    assert "3/4 TUs parsed" in detail
    assert "7/10 symbols matched" in detail
    assert "cache 2/3 hit" in detail
    assert "1 extractor failures" in detail


def test_l4_coverage_detail_partial_and_empty():
    # The detail helper degrades gracefully: an empty coverage dict yields an
    # empty string (no spurious segments), and absent optional counts are simply
    # omitted (ADR-035 P5 — branch coverage of _l4_coverage_detail).
    from abicheck.buildsource.inline import _l4_coverage_detail
    from abicheck.buildsource.source_abi import SourceAbiSurface

    assert _l4_coverage_detail(SourceAbiSurface(coverage={})) == ""
    only_scope = SourceAbiSurface(
        coverage={
            "replay_scope": "changed",
            "compile_units_selected": 2,
            "compile_units_parsed": 2,
        }
    )
    detail = _l4_coverage_detail(only_scope)
    assert detail == "scope=changed, 2/2 TUs parsed"
    assert "symbols matched" not in detail and "cache" not in detail


def test_l4_coverage_detail_reports_full_accounting():
    # When exports are attributed (synthesized/template) or classified
    # (non-public) rather than directly matched, the detail must surface the
    # accounted/unmatched totals so a low "matched" ratio doesn't read as a gap.
    from abicheck.buildsource.inline import _l4_coverage_detail
    from abicheck.buildsource.source_abi import SourceAbiSurface

    surface = SourceAbiSurface(
        coverage={
            "matched_symbols": 4,
            "exported_symbols": 10,
            "synthesized_symbols_matched": 3,
            "non_public_symbols_classified": 3,
            "unmatched_symbols": 0,
        }
    )
    detail = _l4_coverage_detail(surface)
    assert "4/10 symbols matched" in detail
    assert "10/10 accounted, 0 unmatched" in detail

    # unmatched absent → derive it from exported minus accounted (no crash)
    derived = _l4_coverage_detail(
        SourceAbiSurface(
            coverage={
                "matched_symbols": 4,
                "exported_symbols": 10,
                "synthesized_symbols_matched": 2,
            }
        )
    )
    assert "6/10 accounted, 4 unmatched" in derived


def test_l4_include_map_uses_depfile_not_recorded_inputs_for_headers_only(
    monkeypatch,
) -> None:
    import abicheck.buildsource.include_graph as ig
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import _include_map_for_replay

    class _FakeIncludeExtractor:
        clang_bin = "clang++"

        def __init__(self, **kw):
            self.diagnostics = []

        def extract_from_build(self, build):
            return {"cu://b": ["include/api.h"]}

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _FakeIncludeExtractor)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp", input_files=["include/api.h"]),
            CompileUnit(id="cu://b", source="b.cpp", input_files=["src/impl.h"]),
        ]
    )
    extractors = []
    include_map = _include_map_for_replay(
        build,
        scope="headers-only",
        roots=("include/api.h",),
        clang_bin="definitely-not-needed",
        extractors=extractors,
    )
    assert include_map == {"cu://b": ["include/api.h"]}
    assert extractors[0].name == "include_graph:clang"


def test_build_query_failure_is_recorded(tmp_path, monkeypatch):
    """A failing build.query command degrades to a failed extractor, no abort."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    # An unparseable command string is handled gracefully.
    cfg = BuildConfig(query='unterminated "quote', compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg, allow_build_query=True,
    )
    # The command produced no DB; the pack survives only to carry the failed-query
    # diagnostic (A3) so a later compare can surface it, never aborting.
    assert pack is not None
    assert pack.build_evidence is None
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "failed"]


def test_merge_requires_two_inputs(tmp_path):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    p = tmp_path / "a.json"
    save_snapshot(snap, p)
    with pytest.raises(click.UsageError, match="at least two"):
        _merge_load_snapshots((p,))


def _src_snapshot_with_l3(tmp_path, std, name):
    """A source-only snapshot whose embedded pack carries an L3 build_evidence
    folded from a compile DB built with -std=<std>."""
    from abicheck.cli_buildsource import embed_build_source

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, _write_cdb(tmp_path, std), None)
    path = tmp_path / name
    save_snapshot(snap, path)
    return path


def test_merge_layer_conflict_warns_and_records(tmp_path, capsys):
    """A2: two inputs supplying L3 with DIFFERING facts → warn + persisted record,
    first-wins kept (exit 0 in the default warn mode)."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++20", "b.json")
    out = tmp_path / "baseline.json"
    _merge((a, b), out)
    err = capsys.readouterr().err
    assert "merge conflict" in err
    assert "L3_build" in err

    # L3 is first-wins in _combine_packs, so the reported survivor is a.json —
    # the message and record must name the ACTUAL winner (Codex), not a guess.
    assert "kept a.json" in err

    merged = load_snapshot(out)
    assert merged.build_source is not None
    recs = [e for e in merged.build_source.manifest.extractors
            if e.name == "merge_layer_conflict"]
    assert recs, "conflict must be persisted in the extractor ledger"
    assert recs[0].status == "failed"
    assert recs[0].diagnostics  # carries a forward-looking note
    assert "kept a.json" in recs[0].diagnostics[0]


def test_resolve_conflict_winner_latest_wins_on_digest_tie():
    """A2 (Codex): for latest-wins layers (L4/L5), when two inputs share the
    winning digest the recorded survivor must be the LAST contributor (the one
    _combine_packs actually keeps), not the first same-digest sibling."""
    from abicheck.buildsource.merge_support import (
        _MERGE_LAYER_ATTRS,
        _resolve_conflict_winners,
    )
    from abicheck.buildsource.model import DataLayer

    l4 = DataLayer.L4_SOURCE_ABI.value
    l3 = DataLayer.L3_BUILD.value

    class _Payload:
        def __init__(self, digest_src):
            self._d = digest_src

        def to_dict(self):
            return self._d

    # combined L4 facts == {"v": "x"}; inputs A=x, B=y, C=x → C is the survivor.
    combined = SimpleNamespace(**{
        _MERGE_LAYER_ATTRS[l4]: _Payload({"v": "x"}),
        _MERGE_LAYER_ATTRS[l3]: _Payload({"v": "x"}),
    })
    from abicheck.buildsource.merge_support import _canonical_layer_digest
    dx = _canonical_layer_digest({"v": "x"})
    dy = _canonical_layer_digest({"v": "y"})
    conflicts = {
        l4: [("A", dx), ("B", dy), ("C", dx)],
        l3: [("A", dx), ("B", dy), ("C", dx)],
    }
    winners = _resolve_conflict_winners(combined, conflicts)
    assert winners[l4] == "C"   # latest-wins → last same-digest contributor
    assert winners[l3] == "A"   # accumulator-wins → first same-digest contributor


def test_merge_layer_conflict_error_mode_exits_nonzero(tmp_path):
    """A2: --on-conflict=error aborts non-zero and writes no baseline."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++20", "b.json")
    out = tmp_path / "baseline.json"
    with pytest.raises(click.ClickException, match="merge aborted"):
        _merge((a, b), out, on_conflict="error")
    assert not out.exists()


def test_merge_identical_layer_is_not_a_conflict(tmp_path, capsys):
    """A2: two inputs supplying L3 with the SAME facts must NOT flag a conflict."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++17", "b.json")
    out = tmp_path / "baseline.json"
    _merge((a, b), out)
    assert "merge conflict" not in capsys.readouterr().err
    merged = load_snapshot(out)
    assert merged.build_source is not None
    assert not [e for e in merged.build_source.manifest.extractors
                if e.name == "merge_layer_conflict"]


def test_merge_conflict_digest_is_order_independent(tmp_path, capsys):
    """A2 (Codex): same facts in a different list order is NOT a conflict.

    The layer payloads are sets of facts keyed by identity downstream, so a
    reversed compile_commands.json must canonicalize to the same digest.
    """
    from abicheck.cli_buildsource import embed_build_source

    units = [
        {"directory": str(tmp_path), "file": "src/a.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/a.cpp"]},
        {"directory": str(tmp_path), "file": "src/b.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/b.cpp"]},
    ]
    fwd = tmp_path / "fwd.json"
    fwd.write_text(json.dumps(units), encoding="utf-8")
    rev = tmp_path / "rev.json"
    rev.write_text(json.dumps(list(reversed(units))), encoding="utf-8")

    a_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(a_snap, fwd, None)
    b_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(b_snap, rev, None)
    a = tmp_path / "a.json"
    save_snapshot(a_snap, a)
    b = tmp_path / "b.json"
    save_snapshot(b_snap, b)

    out = tmp_path / "baseline.json"
    # Order-only difference must NOT abort under --on-conflict=error.
    _merge((a, b), out, on_conflict="error")
    assert "merge conflict" not in capsys.readouterr().err


def test_merge_three_inputs_folds_all(tmp_path, capsys):
    """D5: merge accepts 3+ inputs — a binary base plus a fact-bearing source
    snapshot plus a no-facts snapshot — folding without conflict."""
    from abicheck.elf_metadata import ElfMetadata

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    src_path = _src_snapshot_with_l3(tmp_path, "c++17", "src.json")
    plain_path = tmp_path / "plain.json"
    save_snapshot(AbiSnapshot(library="libfoo.so", version="1"), plain_path)

    out = tmp_path / "baseline.json"
    _merge((bin_path, src_path, plain_path), out)
    assert "merge conflict" not in capsys.readouterr().err
    merged = load_snapshot(out)
    assert merged.elf is not None                         # binary base kept
    assert merged.build_source is not None
    assert merged.build_source.build_evidence is not None  # L3 folded from src


def test_merge_corrupted_input_errors_cleanly(tmp_path):
    """D5: a non-JSON input fails with a non-zero exit, not a traceback dump."""
    good = _src_snapshot_with_l3(tmp_path, "c++17", "good.json")
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")
    out = tmp_path / "baseline.json"
    with pytest.raises(click.ClickException, match="could not read input"):
        _merge((good, bad), out)
    assert not out.exists()


def test_merge_without_embedded_facts_is_noted(tmp_path, capsys):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import load_snapshot, save_snapshot

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    save_snapshot(AbiSnapshot(library="l", version="1"), a)
    save_snapshot(AbiSnapshot(library="l", version="2"), b)
    out = tmp_path / "o.json"
    _merge((a, b), out)
    assert "no input carried embedded build_source" in capsys.readouterr().err
    # Base ABI surface still written.
    assert load_snapshot(out).library == "l"


def test_dump_source_only_no_binary(tmp_path):
    """`dump --sources <tree>` with no SO_PATH writes a binary-less baseline.

    The parallel-baseline flow that `merge` consumes (Codex P2): SO_PATH is
    optional when --sources/--build-info is given.
    """
    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    out = tmp_path / "libfoo.src.json"
    result = CliRunner().invoke(main, ["dump", "--sources", str(tree), "-o", str(out)])
    assert result.exit_code == 0, result.output

    snap = load_snapshot(out)
    assert snap.elf is None and snap.pe is None and snap.macho is None  # no binary
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_dump_with_no_binary_and_no_inputs_errors():
    """A bare `dump` (no SO_PATH, no --sources/--build-info) errors clearly."""
    result = CliRunner().invoke(main, ["dump"])
    assert result.exit_code != 0
    assert "source-only" in result.output


def test_dump_source_only_then_merge_with_binary(tmp_path):
    """End-to-end: source-only dump + binary dump combine via `merge`."""
    from abicheck.elf_metadata import ElfMetadata
    from abicheck.model import AbiSnapshot

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))
    src_out = tmp_path / "libfoo.src.json"
    assert CliRunner().invoke(
        main, ["dump", "--sources", str(tree), "-o", str(src_out)]
    ).exit_code == 0

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    _merge((bin_path, src_out), out)
    merged = load_snapshot(out)
    assert merged.elf is not None  # binary base kept
    assert merged.build_source is not None and merged.build_source.build_evidence is not None


def test_mixed_build_pack_and_raw_sources_hash_distinguishes_trees(tmp_path):
    """Same build-info pack + different source trees → different content_hash.

    Codex P2: inline source facts must contribute to the combined
    build_source_pack content hash even when the build side is an on-disk pack.
    """
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import _combine_packs

    # On-disk build-info pack.
    bi = BuildSourcePack.empty(tmp_path / "bi")
    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))
    bi.build_evidence = ev
    bi.write()
    bi = BuildSourcePack.load(tmp_path / "bi")

    def _inline_with(library: str) -> BuildSourcePack:
        return BuildSourcePack(root=Path(""), source_abi=SourceAbiSurface(library=library))

    a = _combine_packs(bi, None, _inline_with("tree_a"))
    b = _combine_packs(bi, None, _inline_with("tree_b"))
    assert a is not None and b is not None
    assert a.content_hash() != b.content_hash()
    # And the build evidence still participates (same pack → shared component).
    same = _combine_packs(bi, None, _inline_with("tree_a"))
    assert a.content_hash() == same.content_hash()


def test_combined_pack_content_hash_stable_across_source_abi_coverage_timing(
    tmp_path,
):
    # Regression (Codex review): _append_chosen_payload_digests() (merge_
    # support.py), used for an inline-collected --sources contributor that
    # was never written to disk, hashed the chosen SourceAbiSurface's raw
    # payload directly -- bypassing the same replay wall-clock/cache-hit
    # normalization BuildSourcePack._artifact_digests() already applies to
    # an on-disk/self-contained pack's source_abi.json. A --build-info +
    # --sources combine of identical source facts collected under
    # different cache warmth or runner load therefore still produced a
    # different content_hash().
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import _combine_packs

    bi = BuildSourcePack.empty(tmp_path / "bi")
    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))
    bi.build_evidence = ev
    bi.write()
    bi = BuildSourcePack.load(tmp_path / "bi")

    def _inline_with(coverage: dict) -> BuildSourcePack:
        return BuildSourcePack(
            root=Path(""),
            source_abi=SourceAbiSurface(library="libfoo.so", coverage=coverage),
        )

    a = _combine_packs(
        bi,
        None,
        _inline_with(
            {
                "compile_units_parsed": 3,
                "cache_lookup_s": 0.01,
                "extract_s": 1.79,
                "elapsed_s": 1.85,
                "cache_misses": 3,
                "cache_hits": 0,
            }
        ),
    )
    b = _combine_packs(
        bi,
        None,
        _inline_with(
            {
                "compile_units_parsed": 3,
                "cache_lookup_s": 0.02,
                "extract_s": 0.11,
                "elapsed_s": 0.17,
                "cache_misses": 0,
                "cache_hits": 3,
            }
        ),
    )
    assert a is not None and b is not None
    assert a.content_hash() == b.content_hash()


def test_combined_coverage_present_layer_overrides_stale_not_collected_row():
    """AC-002: when the combined pack carries a layer's facts, that layer's
    coverage row must report present — never the supplying pack's own stale
    ``not_collected`` row (the '63 compile units but L3_build: not_collected'
    symptom)."""
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import (
        BuildSourceManifest,
        CoverageStatus,
        DataLayer,
        LayerCoverage,
    )
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_buildsource import _combine_packs

    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))
    stale = BuildSourceManifest(
        coverage=[
            LayerCoverage(
                layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
            )
        ]
    )
    bi = BuildSourcePack(root=Path(""), manifest=stale, build_evidence=ev)

    combined = _combine_packs(bi, None, None)
    assert combined is not None
    assert combined.build_evidence is not None and combined.build_evidence.compile_units
    row = combined.manifest.coverage_for("L3_build")
    assert row is not None and row.status == CoverageStatus.PRESENT


def test_combined_coverage_row_comes_from_payload_supplier_not_other_pack():
    """AC-002: the L4 coverage row must reflect the pack that actually supplied
    ``source_abi``, not a different pack in supplier order that merely carries an
    (unrelated, not_collected) L4 row."""
    from pathlib import Path

    from abicheck.buildsource.model import (
        BuildSourceManifest,
        CoverageStatus,
        DataLayer,
        LayerCoverage,
    )
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import _combine_packs

    # build-info pack: a stale L4 not_collected row but NO source_abi payload.
    bi = BuildSourcePack(
        root=Path(""),
        manifest=BuildSourceManifest(
            coverage=[
                LayerCoverage(
                    layer=DataLayer.L4_SOURCE_ABI.value,
                    status=CoverageStatus.NOT_COLLECTED,
                )
            ]
        ),
    )
    # sources pack: supplies the L4 payload but carries no L4 row of its own.
    src = BuildSourcePack(
        root=Path(""), source_abi=SourceAbiSurface(library="libfoo.so")
    )

    combined = _combine_packs(bi, src, None)
    assert combined is not None
    assert combined.source_abi is not None
    row = combined.manifest.coverage_for("L4_source_abi")
    # Pre-fix: returned bi_pack's not_collected row (wrong provenance). Fixed:
    # present, because we do embed the L4 facts src_pack supplied.
    assert row is not None and row.status == CoverageStatus.PRESENT


def test_inline_source_changed_falls_back_to_headers_only_scope(tmp_path, monkeypatch):
    """ADR-035 P3: inline dump has no PR diff, so a 'changed' scope falls back to
    'headers-only' (the public-API surface) — non-empty, but NOT the full-target
    (== s6) replay that silently paid the cost cliff before."""
    import abicheck.buildsource.inline as inline
    captured = {}

    def _spy(sources, merged, extractors, *, extractor, scope, clang_bin,
             exported_symbols=(), source_abi_cache_dir=None, changed_paths=(),
             public_header_roots=()):
        captured["scope"] = scope
        return None, []

    monkeypatch.setattr(inline, "_run_inline_source_abi", _spy)
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "f.cpp").write_text("int f(){return 0;}\n")
    (tree / "compile_commands.json").write_text(json.dumps([{
        "directory": str(tree), "file": "f.cpp",
        "arguments": ["c++", "-c", "f.cpp"]}]))
    inline.collect_inline_pack(sources=tree, build_info=None, scope="changed",
                               layers=("L3", "L4", "L5"))
    assert captured["scope"] == "headers-only"


def _tree_with_two_units(tmp_path):
    tree = tmp_path / "src"
    tree.mkdir()
    for name in ("a.cpp", "b.cpp"):
        (tree / name).write_text("int f(){return 0;}\n")
    (tree / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tree), "file": n, "arguments": ["c++", "-c", n]}
        for n in ("a.cpp", "b.cpp")
    ]))
    return tree


def _stub_call_graph(monkeypatch, seen: list[str]):
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.call_graph import CallEdge

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            seen.extend(cu.source for cu in build.compile_units)
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)


def test_inline_unseeded_call_graph_uses_l4_selection(tmp_path, monkeypatch):
    """Gap-1: an unseeded headers-only run scopes the L5 call-graph pass to the
    exact compile units the L4 replay selected, not the whole compile DB."""
    import abicheck.buildsource.inline as inline
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_abi import SourceAbiSurface

    def _spy(sources, merged, extractors, **kw):
        # L4 selected only a.cpp (a headers-only subset of the two-unit DB).
        return SourceAbiSurface(), [CompileUnit(id="cu://a.cpp", source="a.cpp")]

    monkeypatch.setattr(inline, "_run_inline_source_abi", _spy)
    seen: list[str] = []
    _stub_call_graph(monkeypatch, seen)
    tree = _tree_with_two_units(tmp_path)
    inline.collect_inline_pack(sources=tree, build_info=None, scope="changed",
                               layers=("L3", "L4", "L5"))
    assert seen == ["a.cpp"]  # scoped to the L4 selection, not both units


def test_inline_unseeded_call_graph_stays_broad_when_l4_selects_nothing(
    tmp_path, monkeypatch
):
    """Codex review: an empty L4 selection (e.g. --build-info only, no --sources)
    must NOT collapse the call-graph pass to zero units — it stays broad over the
    compile DB so a build-info-only deep scan still collects L5 call edges."""
    from pathlib import Path

    import abicheck.buildsource.inline as inline

    def _spy(sources, merged, extractors, **kw):
        return None, []  # L4 could not select any units

    monkeypatch.setattr(inline, "_run_inline_source_abi", _spy)
    seen: list[str] = []
    _stub_call_graph(monkeypatch, seen)
    tree = _tree_with_two_units(tmp_path)
    inline.collect_inline_pack(sources=tree, build_info=None, scope="changed",
                               layers=("L3", "L4", "L5"))
    # Broad, not zero: both compile-DB units are parsed for call edges.
    assert sorted(Path(s).name for s in seen) == ["a.cpp", "b.cpp"]


def test_exported_symbols_from_snapshot_extracts_mangled_names():
    """A1 plumbing: export extraction pulls mangled function/variable names from
    an already-parsed snapshot (no re-dump), and is empty for a bare snapshot."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.model import Function, Variable

    snap = AbiSnapshot(library="libfoo.so", version="1")
    snap.functions = [
        Function(name="foo", mangled="_Z3foov", return_type="void", params=[]),
        Function(name="bar", mangled="", return_type="void", params=[]),  # no symbol
    ]
    snap.variables = [Variable(name="g", mangled="_Z1g", type="int")]
    assert _exported_symbols_from_snapshot(snap) == ("_Z1g", "_Z3foov")

    assert _exported_symbols_from_snapshot(AbiSnapshot(library="l", version="1")) == ()


def test_exported_symbols_from_snapshot_uses_elf_dynamic_table():
    """The authoritative export set is the ELF dynamic symbol table, not just the
    DWARF-shaped ``functions`` list. Feeding only the modeled functions truncated
    the linker's export set (the ``merge`` symbol-matching regression), so the raw
    ``elf.symbols`` names must be unioned in."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import Function

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # A DWARF-modeled function whose linkage name is the non-ABI unified C4 tag —
    # never present in the real export table.
    snap.functions = [
        Function(name="Foo::Foo", mangled="_ZN3FooC4Ev", return_type="void", params=[])
    ]
    snap.elf = ElfMetadata()
    # The real exported clones the loader sees.
    snap.elf.symbols = [
        ElfSymbol(name="_ZN3FooC1Ev"),
        ElfSymbol(name="_ZN3FooC2Ev"),
        ElfSymbol(name="_Z3barv"),
    ]
    exports = _exported_symbols_from_snapshot(snap)
    # The raw dynamic table is authoritative and used alone.
    assert "_ZN3FooC1Ev" in exports
    assert "_ZN3FooC2Ev" in exports
    assert "_Z3barv" in exports
    # The DWARF-only unified C4 tag is NOT a real export — it must not leak into
    # the export set (or a source decl mangled C4 would exact-match a phantom and
    # inflate exported_symbols/matched_symbols; Codex review).
    assert "_ZN3FooC4Ev" not in exports


def test_exported_symbols_from_snapshot_excludes_non_default_versions():
    """A symbol that exists only as a non-default version alias (``foo@VER`` with
    no default ``foo@@VER``) cannot be linked against by an unversioned consumer,
    so it must NOT enter the relink export set — otherwise the L4 mapping marks a
    header decl backed only by that alias as exported and the crosscheck's two-way
    reconciliation wrongly suppresses ``public_not_exported`` (Codex review)."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol

    snap = AbiSnapshot(library="libfoo.so", version="1")
    snap.elf = ElfMetadata()
    snap.elf.symbols = [
        ElfSymbol(name="_Z3foov", version="LIB_1", is_default=True),  # default → in
        ElfSymbol(name="_Z3oldv", version="LIB_1", is_default=False),  # alias → out
        ElfSymbol(name="_Z3barv"),  # unversioned (is_default defaults True) → in
    ]
    exports = _exported_symbols_from_snapshot(snap)
    assert set(exports) == {"_Z3foov", "_Z3barv"}
    assert "_Z3oldv" not in exports


def test_merge_warns_on_empty_source_surface(capsys):
    """Project-level Caveat A: merging a pack whose whole source surface is empty
    while the binary exports symbols warns that public-roots was likely wrong."""
    from types import SimpleNamespace

    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource_merge import _warn_if_source_surface_empty

    empty = SourceAbiSurface(library="libfoo.so", target_id="t")  # no entities
    combined = SimpleNamespace(source_abi=empty)
    _warn_if_source_surface_empty(combined, ("_Z3foov", "_Z3barv"))
    err = capsys.readouterr().err
    assert "no public entities" in err
    assert "public-roots" in err

    # A non-empty surface (or no exports) is silent.
    populated = SourceAbiSurface(library="libfoo.so", target_id="t")
    populated.reachable_declarations.append(object())
    _warn_if_source_surface_empty(SimpleNamespace(source_abi=populated), ("_Z3foov",))
    _warn_if_source_surface_empty(SimpleNamespace(source_abi=empty), ())
    assert capsys.readouterr().err == ""


def test_exported_symbols_falls_back_to_modeled_names_without_raw_table():
    """With no raw dynamic table (a source-only snapshot), the modeled mangled
    names are the only available fallback."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.model import Function, Variable

    snap = AbiSnapshot(library="libfoo.so", version="1")
    snap.functions = [
        Function(name="foo", mangled="_Z3foov", return_type="void", params=[])
    ]
    snap.variables = [Variable(name="g", mangled="_Z1g", type="int")]
    # No .elf/.pe/.macho set → fall back to the modeled names.
    assert _exported_symbols_from_snapshot(snap) == ("_Z1g", "_Z3foov")


def test_exported_symbols_from_snapshot_uses_pe_and_macho_tables():
    """The same export-table union covers PE and Mach-O binaries, not just ELF."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.macho_metadata import MachoExport, MachoMetadata
    from abicheck.pe_metadata import PeExport, PeMetadata

    pe_snap = AbiSnapshot(library="foo.dll", version="1")
    pe_snap.pe = PeMetadata()
    pe_snap.pe.exports = [PeExport(name="CreateFoo"), PeExport(name="DestroyFoo")]
    assert _exported_symbols_from_snapshot(pe_snap) == ("CreateFoo", "DestroyFoo")

    macho_snap = AbiSnapshot(library="libfoo.dylib", version="1")
    macho_snap.macho = MachoMetadata()
    macho_snap.macho.exports = [MachoExport(name="_foo"), MachoExport(name="_bar")]
    assert _exported_symbols_from_snapshot(macho_snap) == ("_bar", "_foo")


def test_build_info_source_mismatch_records_diagnostic(tmp_path):
    """A4: a compile DB whose sources are absent from the --sources tree records
    a build_info_source_tree_mismatch diagnostic (collection-time, not a kind)."""
    from abicheck.buildsource.inline import collect_inline_pack

    # compile DB referencing files that do NOT exist under the (empty) tree.
    cdb = [{
        "directory": str(tmp_path),
        "file": f"src/missing{i}.cpp",
        "arguments": ["c++", "-std=c++17", "-c", f"src/missing{i}.cpp"],
    } for i in range(4)]
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()  # empty: none of the compile-DB sources resolve here

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    recs = [e for e in pack.manifest.extractors
            if e.name == "build_info_source_tree_mismatch"]
    assert recs and recs[0].status == "failed"
    assert pack.build_evidence is not None
    assert any("mismatch" in d for d in pack.build_evidence.diagnostics)


def test_build_info_source_match_no_mismatch(tmp_path):
    """A4: when the compile-DB sources exist under the tree, no mismatch fires."""
    from abicheck.buildsource.inline import collect_inline_pack

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    cdb = []
    for i in range(4):
        (tree / "src" / f"f{i}.cpp").write_text("int x;", encoding="utf-8")
        cdb.append({
            "directory": str(tree),
            "file": f"src/f{i}.cpp",
            "arguments": ["c++", "-std=c++17", "-c", f"src/f{i}.cpp"],
        })
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    assert not [e for e in pack.manifest.extractors
                if e.name == "build_info_source_tree_mismatch"]


def test_build_info_source_mismatch_basename_match_ignores_redacted_prefix(tmp_path):
    """A4 (Codex): redacted '~/...' compile-DB paths must not cause a false
    mismatch — matching is by basename, which redaction never strips."""
    from abicheck.buildsource.inline import collect_inline_pack

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    cdb = []
    for i in range(4):
        (tree / "src" / f"r{i}.cpp").write_text("int x;", encoding="utf-8")
        # directory/file carry a redacted home placeholder, not a real path.
        cdb.append({
            "directory": "~/proj",
            "file": f"src/r{i}.cpp",
            "arguments": ["c++", "-std=c++17", "-c", f"src/r{i}.cpp"],
        })
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    assert not [e for e in pack.manifest.extractors
                if e.name == "build_info_source_tree_mismatch"]


def test_canonical_layer_digest_sorts_nested_facts_keeps_scalar_order():
    """A2 (Codex): the per-layer digest is order-independent for nested fact
    *records* (e.g. reachable_declarations) but order-SENSITIVE for scalar
    sequences (e.g. linker_argv) which encode ABI-relevant order."""
    from abicheck.buildsource.merge_support import _canonical_layer_digest

    a = {"reachable_source_surface": {
        "reachable_declarations": [{"id": "d1"}, {"id": "d2"}]}}
    b = {"reachable_source_surface": {
        "reachable_declarations": [{"id": "d2"}, {"id": "d1"}]}}
    # Nested fact records reversed → same digest (set semantics).
    assert _canonical_layer_digest(a) == _canonical_layer_digest(b)

    # Ordered scalar sequence reordered → different digest (argv order matters).
    x = {"link_units": [{"linker_argv": ["-lfoo", "-lbar"]}]}
    y = {"link_units": [{"linker_argv": ["-lbar", "-lfoo"]}]}
    assert _canonical_layer_digest(x) != _canonical_layer_digest(y)

    # Unordered scalar fact set reordered → same digest (source_files is a set).
    p1 = {"targets": [{"source_files": ["a.cpp", "b.cpp"]}]}
    p2 = {"targets": [{"source_files": ["b.cpp", "a.cpp"]}]}
    assert _canonical_layer_digest(p1) == _canonical_layer_digest(p2)

    # Include-path order is compiler-visible → reordering must differ (Codex).
    i1 = {"compile_units": [{"include_paths": ["/a", "/b"]}]}
    i2 = {"compile_units": [{"include_paths": ["/b", "/a"]}]}
    assert _canonical_layer_digest(i1) != _canonical_layer_digest(i2)

    # abi_relevant_flags is last-wins (-fexceptions/-fno-exceptions) → reordering
    # changes the parsed ABI, so it must read as a conflict (Codex).
    f1 = {"compile_units": [{"abi_relevant_flags": ["-fexceptions", "-fno-exceptions"]}]}
    f2 = {"compile_units": [{"abi_relevant_flags": ["-fno-exceptions", "-fexceptions"]}]}
    assert _canonical_layer_digest(f1) != _canonical_layer_digest(f2)


def test_build_inline_coverage_surfaces_failed_build_query():
    """A3: a failed/blocked build query yields a `partial` L3 coverage row with
    the reason, not a silent `not_collected`."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import ExtractorRecord

    rec = ExtractorRecord(
        name="build_query", status="skipped",
        detail="build.query configured but --allow-build-query not set",
    )
    rows = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[rec])}
    l3 = rows["L3_build"]
    assert l3.status.value == "partial"
    assert "build query skipped" in l3.detail

    # No build-query record → still a silent not_collected (unchanged behaviour).
    rows2 = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[])}
    assert rows2["L3_build"].status.value == "not_collected"


def test_embedded_source_graph_l5_roundtrips(tmp_path):
    """D7: an embedded L5 source_graph survives dump-embed + snapshot round-trip."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    pack_dir = tmp_path / "ev"
    _collect(pack_dir, compile_db=cdb, source_graph="summary")
    assert BuildSourcePack.load(pack_dir).source_graph is not None

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, pack_dir, None)
    assert snap.build_source is not None and snap.build_source.source_graph is not None

    out = tmp_path / "s.json"
    save_snapshot(snap, out)
    reloaded = load_snapshot(out)
    assert reloaded.build_source is not None
    assert reloaded.build_source.source_graph is not None


def test_build_info_invalid_compile_db_is_graceful(tmp_path):
    """D3: a build dir whose compile_commands.json is malformed degrades to no L3
    facts without crashing the dump (ADR-028 D3)."""
    from abicheck.cli_buildsource import embed_build_source

    bd = tmp_path / "build"
    bd.mkdir()
    (bd / "compile_commands.json").write_text("{ not valid json", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")
    embed_build_source(snap, bd, None)  # must not raise
    # No usable L3 facts → nothing embedded (or build_source without compile units).
    if snap.build_source is not None and snap.build_source.build_evidence is not None:
        assert not snap.build_source.build_evidence.compile_units


def test_build_config_malformed_block_shape_raises(tmp_path):
    """ADR-043 CLI reset: a `build:` block that isn't a mapping is a hard
    ``ValueError`` from ``load_build_config`` now, not a silent degrade to
    defaults — the strictness the removed `abicheck config validate` command
    used to provide as a separate opt-in step now lives in the loader itself,
    so every real caller sees it (embed_build_source/compare/scan all wrap
    this in a click.UsageError -> exit 64, never an uncaught traceback)."""
    from abicheck.buildsource.inline import (
        discover_build_config,
        load_build_config,
    )

    tree = tmp_path / "src"
    tree.mkdir()
    cfg_path = tree / ".abicheck.yml"
    cfg_path.write_text("build:\n  - this is a list not a mapping\n", encoding="utf-8")
    # discover still finds it; load now raises on the malformed shape.
    assert discover_build_config(tree) == cfg_path
    with pytest.raises(ValueError, match="build must be a mapping"):
        load_build_config(cfg_path)


def test_dump_sources_and_build_info_together(tmp_path):
    """D2: --sources and --build-info together — L3 comes from --build-info, the
    source tree drives L4 (partial without clang); the call must not error and
    L3 facts must be embedded."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    tree = tmp_path / "src"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "foo.cpp").write_text("int x;", encoding="utf-8")

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, cdb, tree)  # build_info=cdb, sources=tree
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None  # L3 from --build-info


def test_collect_no_input_is_noop(tmp_path):
    """D6: collecting with no inputs collects nothing and does not crash."""
    out = tmp_path / "ev"
    # Must not raise — a graceful empty pack, never a traceback.
    pack = _collect(out)
    assert pack is not None


def test_merge_relinks_source_surface_with_binary_exports(tmp_path):
    """A1 merge plumbing: a source-only snapshot's surface (linked with no binary)
    gets the binary base's L0 exports folded in at merge time, so provenance has
    a signal in the parallel-baseline flow."""
    from pathlib import Path

    from abicheck.buildsource.model import BuildSourceManifest
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import Function

    # Source-only snapshot: a surface with one public decl, no exports yet.
    surf = SourceAbiSurface(library="libfoo.so", target_id="t")
    surf.reachable_declarations = [
        SourceEntity(id="decl://foo", kind="function", qualified_name="foo",
                     mangled_name="_Z3foov")
    ]
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    src_snap.build_source = BuildSourcePack(
        root=Path(""), manifest=BuildSourceManifest(), source_abi=surf)
    src_path = tmp_path / "src.json"
    save_snapshot(src_snap, src_path)

    # Binary snapshot exporting _Z3foov.
    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    # A realistic binary exports _Z3foov via its dynamic symbol table (the
    # authoritative export set), not merely via the DWARF-modeled functions list.
    bin_snap.elf.symbols = [ElfSymbol(name="_Z3foov")]
    bin_snap.functions = [Function(name="foo", mangled="_Z3foov",
                                   return_type="void", params=[])]
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    _merge((bin_path, src_path), out)
    merged = load_snapshot(out)
    assert merged.build_source is not None and merged.build_source.source_abi is not None
    # Exports plumbed in, and foo now maps to its exported symbol.
    assert merged.build_source.source_abi.roots["exported_symbols"] == ["_Z3foov"]
    mapping = merged.build_source.source_abi.mappings["source_decl_to_binary_symbol"]
    assert "_Z3foov" in set(mapping.values())


def test_merge_relink_rebuilds_l5_graph_and_refreshes_hash(tmp_path):
    """A1 merge plumbing (Codex): when the source-only input carries an L5 graph,
    relinking rebuilds it with the binary's exports (so it gains the
    source↔binary edges) and clears stale artifact digests so content_hash
    recomputes from the updated payloads."""
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import BuildSourceManifest
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.buildsource.source_graph import build_source_graph
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import Function

    surf = SourceAbiSurface(library="libfoo.so", target_id="t")
    surf.reachable_declarations = [
        SourceEntity(id="decl://foo", kind="function", qualified_name="foo",
                     mangled_name="_Z3foov")
    ]
    graph0 = build_source_graph(BuildEvidence(), source_abi=surf)  # empty exports
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    src_snap.build_source = BuildSourcePack(
        root=Path(""), manifest=BuildSourceManifest(), source_abi=surf,
        source_graph=graph0)
    src_path = tmp_path / "src.json"
    save_snapshot(src_snap, src_path)

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    # A realistic binary exports _Z3foov via its dynamic symbol table (the
    # authoritative export set), not merely via the DWARF-modeled functions list.
    bin_snap.elf.symbols = [ElfSymbol(name="_Z3foov")]
    bin_snap.functions = [Function(name="foo", mangled="_Z3foov",
                                   return_type="void", params=[])]
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    _merge((bin_path, src_path), out)
    merged = load_snapshot(out)
    g = merged.build_source.source_graph
    assert g is not None
    # Rebuilt graph carries a symbol-mapping edge the empty-export graph lacked.
    edge_kinds = {e.kind for e in g.edges}
    assert any("SYMBOL" in k for k in edge_kinds), edge_kinds
    # content_hash recomputes from the updated payloads (no stale artifacts).
    assert merged.build_source.content_hash()


def test_a4_redacted_absolute_source_uses_basename(tmp_path):
    """A4 (CI regression): when the compile-DB adapter redacts a source to a
    '~/...' absolute path (runner CWD under $HOME), the rooted/redacted prefix is
    unrecoverable, so matching falls back to basename and a present checkout is
    NOT flagged as a mismatch."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import _check_build_info_source_mismatch

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    units = []
    for i in range(4):
        (tree / "src" / f"r{i}.cpp").write_text("int x;", encoding="utf-8")
        # Redacted absolute source (home prefix rewritten to '~'), as the adapter
        # emits on a runner whose CWD is under $HOME.
        units.append(CompileUnit(id=f"u{i}", source=f"~/work/proj/src/r{i}.cpp",
                                 directory="~/proj"))
    merged = BuildEvidence()
    merged.compile_units = units
    extractors = []
    _check_build_info_source_mismatch(merged, tree, extractors)
    assert not [e for e in extractors if e.name == "build_info_source_tree_mismatch"]


def test_a4_basename_only_match_in_wrong_subtree_flags_mismatch(tmp_path):
    """A4 (Codex): an absolute/redacted compile-DB source must match more than
    its bare basename — a wrong checkout that ships the same filename under a
    different parent dir (tests/ vs src/) must still flag the mismatch."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import _check_build_info_source_mismatch

    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    units = []
    for i in range(4):
        # Same basename present, but under tests/ — the compile unit's src/ parent
        # is absent, so the trees are different checkouts.
        (tree / "tests" / f"f{i}.cpp").write_text("int x;", encoding="utf-8")
        # directory does NOT prefix the source, so matching takes the
        # absolute/redacted fallback (suffix match), not the directory branch.
        units.append(CompileUnit(id=f"u{i}", source=f"/build/proj/src/f{i}.cpp",
                                 directory="/unrelated"))
    merged = BuildEvidence()
    merged.compile_units = units
    extractors = []
    _check_build_info_source_mismatch(merged, tree, extractors)
    recs = [e for e in extractors if e.name == "build_info_source_tree_mismatch"]
    assert recs and recs[0].status == "failed"


def test_a3_failed_query_pack_survives_with_no_facts(tmp_path):
    """A3 (Codex): when build.query is skipped/failed and no facts are collected,
    collect_inline_pack still returns a pack carrying the partial L3 coverage row
    + the build_query diagnostic (not None), so compare can surface it."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()  # no compile DB inside → no L3 facts
    cfg = BuildConfig(query="some-build-query --emit")
    # untrusted config → query skipped, nothing collected.
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg,
        build_config_trusted_for_query=False, layers=("L3",),
    )
    assert pack is not None, "pack must survive to carry the A3 diagnostic"
    l3 = pack.manifest.coverage_for("L3_build")
    assert l3 is not None and l3.status.value == "partial"
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "skipped"]


def test_a3_query_ran_but_empty_is_reported(tmp_path):
    """A3 (Codex): an allowed build query that runs but produces no compile DB
    records `partial`; the pack must survive so that diagnostic + partial L3 row
    reach compare (not just failed/skipped)."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import ExtractorRecord

    rec = ExtractorRecord(name="build_query", status="partial",
                          detail="ran `q …` but no compile DB was produced")
    rows = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[rec])}
    assert rows["L3_build"].status.value == "partial"
    assert "build query partial" in rows["L3_build"].detail


def test_a3_diagnostic_only_pack_survives_embed_combine(tmp_path):
    """A3 (Codex): a diagnostic-only inline pack (build query skipped, no facts)
    must keep its build_query extractor + partial L3 coverage row through
    embed_build_source -> _combine_packs, not become a silent not_collected pack."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    (tree / ".abicheck.yml").write_text(
        "build:\n  query: some-build-query --emit\n", encoding="utf-8")
    snap = AbiSnapshot(library="libfoo.so", version="1")
    # allow_build_query defaults False → query skipped, no facts collected.
    embed_build_source(snap, None, tree)
    assert snap.build_source is not None
    l3 = snap.build_source.manifest.coverage_for("L3_build")
    assert l3 is not None and l3.status.value == "partial"
    assert [e for e in snap.build_source.manifest.extractors
            if e.name == "build_query"]

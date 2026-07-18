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

"""Unified ``--depth`` dial + L5-internal graph (ADR-037 D5/D6 / G22 Phase 3).

One depth vocabulary across ``compare``/``dump``/``scan``; the
G21 ``graph`` rung and the ``--collect-mode`` flag were removed outright (pre-1.0
clean-up); the L5 graph is an internal consequence of ``--depth source``, never a
user rung.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_params import DEPTH_PARAM


def _registered() -> dict:
    """Register every depth-bearing command on ``main`` and return its map."""
    import abicheck.cli_scan  # noqa: F401  — registers scan

    return main.commands


# ── One dial: every depth-bearing command shows the same user-facing ladder ──

_DEPTH_COMMANDS = ("compare", "dump", "scan")


@pytest.mark.parametrize("cmd_name", _DEPTH_COMMANDS)
def test_depth_dial_is_uniform(cmd_name: str) -> None:
    """Each command's ``--depth`` exposes exactly the four public rungs."""
    cmd = _registered()[cmd_name]
    depth = next(p for p in cmd.params if "--depth" in getattr(p, "opts", []))
    metavar = depth.type.get_metavar(depth)
    assert metavar == "[binary|headers|build|source]", (cmd_name, metavar)
    assert "graph" not in metavar and "symbols" not in metavar and "full" not in metavar


# ── Exactly four public rungs; every removed spelling is a hard CLI error ────


@pytest.mark.parametrize("depth_value", ["binary", "headers", "build", "source"])
def test_depth_user_rung_passes_through(depth_value: str) -> None:
    """Each user-ladder rung resolves to itself, independently."""
    assert DEPTH_PARAM.convert(depth_value, None, None) == depth_value


def test_user_depths_match_ladder() -> None:
    """The parametrized ladder above stays in lock-step with ``USER_DEPTHS``."""
    from abicheck.buildsource.scan_levels import USER_DEPTHS

    assert [d.value for d in USER_DEPTHS] == ["binary", "headers", "build", "source"]


@pytest.mark.parametrize("spelling", ["symbols", "SYMBOLS"])
def test_depth_symbols_is_rejected_on_cli(spelling: str) -> None:
    """``symbols`` is a hard CLI error (ADR-043 D2) -- no alias, no translation.

    (The internal Python service API, e.g. ``ScanRequest``, keeps a permissive
    ``symbols``->``binary`` alias for non-CLI callers; only the public
    ``--depth`` flag itself is strict.)
    """
    import click

    with pytest.raises(click.BadParameter):
        DEPTH_PARAM.convert(spelling, None, None)


@pytest.mark.parametrize("spelling", ["full", "FULL", "graph", "GRAPH"])
def test_depth_full_and_graph_are_rejected(spelling: str) -> None:
    """``full``/``graph`` were removed outright (ADR-043 D2): ``source`` now
    covers what ``full`` used to mean (replay *scope*, not depth), and the L5
    graph is an internal consequence of ``--depth source``. Neither is a user
    rung or a deprecated alias any more."""
    import click

    with pytest.raises(click.BadParameter):
        DEPTH_PARAM.convert(spelling, None, None)


def _all_output(res: object) -> str:
    """Combined stdout+stderr of a CliRunner result, click-version tolerant."""
    out = getattr(res, "output", "") or ""
    try:
        out += getattr(res, "stderr", "") or ""
    except ValueError:  # stderr not separately captured on this click
        pass
    return out


def test_depth_graph_rejected_on_cli() -> None:
    """The removed ``--depth graph`` is now a hard usage error on the CLI."""
    res = CliRunner().invoke(main, ["dump", "--depth", "graph", "/no/such/bin"])
    assert res.exit_code != 0
    text = _all_output(res)
    assert "graph" in text  # the rejected value is named in the error


def test_unknown_depth_rejected() -> None:
    res = CliRunner().invoke(main, ["dump", "--depth", "bogus", "/no/such/bin"])
    assert res.exit_code != 0
    assert "is not one of" in _all_output(res)


# ── Monotone ladder: each rung is a superset of the one below ────────────────


def test_depth_monotone() -> None:
    """``symbols ⊂ headers ⊂ build ⊂ source ⊂ full`` in collected layers.

    Maps each user depth through the same resolution `dump`/`compare` use and
    asserts the collected evidence layers only ever grow.
    """
    from abicheck.buildsource.scan_levels import (
        USER_DEPTHS,
        EvidenceDepth,
        depth_to_method,
        method_to_collect_mode,
    )
    from abicheck.buildsource.source_replay import collection_for_ci_mode

    def layers_for(depth: EvidenceDepth) -> set[str]:
        method = depth_to_method(depth)
        if method is None:
            return set()  # symbols/headers — no L3-L5 collection
        mode = method_to_collect_mode(method)
        _scope, layers = collection_for_ci_mode(mode)
        return set(layers)

    prev: set[str] = set()
    for depth in USER_DEPTHS:
        cur = layers_for(depth)
        assert prev <= cur, f"{depth.value} dropped layers vs the rung below: {prev - cur}"
        prev = cur


def test_graph_excluded_from_user_ladder_but_kept_internal() -> None:
    """``graph`` is dropped from the user dial (D6) yet survives internally for
    the scan ``pr-deep`` mode / S4 — removing it would break determinism."""
    from abicheck.buildsource.scan_levels import (
        USER_DEPTHS,
        EvidenceDepth,
        ScanMode,
        mode_preset,
    )

    assert EvidenceDepth.GRAPH not in USER_DEPTHS
    # still the internal target of pr-deep (the L5-edges preset).
    assert mode_preset(ScanMode.PR_DEEP)[1] is EvidenceDepth.GRAPH


# ── L5 graph is internal at --depth source (D6) ──────────────────────────────


def test_graph_built_at_source_depth() -> None:
    """``--depth source`` resolves to a collect mode whose layers include L5 —
    the graph is built automatically, with no user ``graph`` mode."""
    from abicheck.buildsource.scan_levels import (
        EvidenceDepth,
        depth_to_method,
        method_to_collect_mode,
    )
    from abicheck.buildsource.source_replay import collection_for_ci_mode

    method = depth_to_method(EvidenceDepth.SOURCE)
    assert method is not None
    _scope, layers = collection_for_ci_mode(method_to_collect_mode(method))
    assert "L5" in layers and "L4" in layers


# ── --depth symbols suppresses the L2 header AST (symbols-only) ──────────────


def test_resolve_dump_depth_binary_collects_nothing() -> None:
    from abicheck.cli_dump_helpers import resolve_dump_depth

    assert resolve_dump_depth("binary", "off") == "off"
    assert resolve_dump_depth("headers", "off") == "off"
    assert resolve_dump_depth("source", "off") != "off"


# ── config: sources.graph: summary|full (ADR-037 D6) ─────────────────────────


def test_graph_detail_config_default_and_parse() -> None:
    from abicheck.buildsource.inline import BuildConfig

    assert BuildConfig().graph_detail == "summary"
    assert BuildConfig.from_dict({}).graph_detail == "summary"
    assert BuildConfig.from_dict({"sources": {"graph": "full"}}).graph_detail == "full"


def test_graph_detail_config_rejects_bad_value() -> None:
    from abicheck.buildsource.inline import BuildConfig

    with pytest.raises(ValueError, match="summary"):
        BuildConfig.from_dict({"sources": {"graph": "deep"}})


def test_graph_detail_full_widens_changed_scope() -> None:
    """``sources.graph: full`` deepens a changed-scope collection to full scope;
    ``summary`` (default) leaves the requested scope untouched (additive only)."""
    from abicheck.buildsource.inline import effective_graph_scope

    assert effective_graph_scope("full", "changed") == "target"
    # never widens a non-changed scope, never shrinks, summary is a no-op.
    assert effective_graph_scope("full", "target") == "target"
    assert effective_graph_scope("summary", "changed") == "changed"
    assert effective_graph_scope("summary", "target") == "target"


def test_collect_inline_pack_applies_graph_scope_override() -> None:
    """``collect_inline_pack`` runs the scope override and returns None for empty
    input (exercising the override line without any compiler)."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    assert collect_inline_pack(
        sources=None,
        build_info=None,
        build_config=BuildConfig(graph_detail="full"),
        scope="changed",
        layers=("L3", "L4", "L5"),
    ) is None


# ── Command bodies: depth resolution over snapshot inputs (no compiler) ───────


def _snap(tmp_path, name: str, version: str, funcs):  # type: ignore[no-untyped-def]
    """Write a minimal JSON snapshot with the given functions; return its path."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    p = tmp_path / f"{name}_{version}.json"
    save_snapshot(AbiSnapshot(library=name, version=version, functions=funcs), p)
    return p


def _fn(name: str):  # type: ignore[no-untyped-def]
    """A trivial public extern-C ``Function`` for snapshot fixtures."""
    from abicheck.model import Function, Visibility

    return Function(
        name=name, mangled=name, return_type="int",
        visibility=Visibility.PUBLIC, is_extern_c=True,
    )


@pytest.mark.parametrize("depth", ["binary", "headers", "source"])
def test_compare_accepts_depth_over_snapshots(tmp_path, depth: str) -> None:  # type: ignore[no-untyped-def]
    """``compare`` folds ``--depth`` into the collect mode for snapshot inputs;
    ``binary`` clears headers, deeper rungs resolve without error."""
    old = _snap(tmp_path, "libx", "1.0", [_fn("a"), _fn("b")])
    new = _snap(tmp_path, "libx", "2.0", [_fn("a"), _fn("b")])
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "--depth", depth])
    assert res.exit_code == 0, _all_output(res)


def test_compare_collect_mode_flag_removed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The removed ``--collect-mode`` flag is now a hard usage error on compare."""
    old = _snap(tmp_path, "liby", "1.0", [_fn("a")])
    new = _snap(tmp_path, "liby", "2.0", [_fn("a")])
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--collect-mode", "off"]
    )
    assert res.exit_code != 0
    assert "No such option" in _all_output(res)


@pytest.mark.parametrize("depth", ["binary", "headers"])
def test_compare_depth_over_snapshots(tmp_path, depth: str) -> None:  # type: ignore[no-untyped-def]
    """``compare`` passes snapshot inputs straight through; ``--depth
    binary`` clears headers, the non-binary branch leaves them as-is."""
    old = _snap(tmp_path, "libz", "1.0", [_fn("a")])
    new = _snap(tmp_path, "libz", "2.0", [_fn("a")])
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--depth", depth]
    )
    assert res.exit_code == 0, _all_output(res)


def test_dump_source_only_depth_build_without_facts_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """CLI-audit P1: a source-only ``dump --depth build`` resolves the L3 collect
    mode, but an explicitly requested depth that finds no usable compile_commands.json
    must now hard-fail rather than silently write a weaker (binary-only) snapshot."""
    src = tmp_path / "src"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "build",
         "-o", str(tmp_path / "out.json")],
    )
    assert res.exit_code != 0, _all_output(res)
    assert "--depth build was requested but the snapshot only reached" in _all_output(res)
    assert not (tmp_path / "out.json").exists()


def test_dump_source_only_depth_binary(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``dump --depth binary`` resolves and runs the symbols-only branch."""
    src = tmp_path / "src2"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "binary",
         "-o", str(tmp_path / "out2.json")],
    )
    # Resolution + symbols-clearing ran; a source-only symbols dump writes an
    # L0-L2 snapshot and exits clean.
    assert res.exit_code == 0, _all_output(res)
    assert (tmp_path / "out2.json").is_file()


def test_dump_depth_binary_ignores_compile_db(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``dump --depth binary`` discards a -H + --compile-db invocation's L2 inputs:
    it must NOT abort on the compile-DB header requirement just because binary depth
    cleared the headers (Codex review)."""
    hdr = tmp_path / "foo.h"
    hdr.write_text("int foo(void);\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")
    res = CliRunner().invoke(
        main,
        [
            "dump", "/no/such/bin.so", "-H", str(hdr), "--compile-db", str(cdb),
            "--depth", "binary", "-o", str(tmp_path / "o.json"),
        ],
    )
    # The compile-DB-requires-headers UsageError must not fire (it would block the
    # switch to the fast binary rung). Any later failure is the missing binary, not
    # this validation.
    out = _all_output(res)
    assert "Compilation database" not in out
    assert "requires -H" not in out


def test_dump_depth_source_with_hybrid_frontend_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """CLI-audit P1: L4 source-ABI replay has no dual-backend hybrid extractor
    (unlike the L2 header AST), so --depth source + --ast-frontend hybrid must
    be rejected up front rather than silently degrading while still calling
    itself "hybrid". This is a usage error caught before any dump work runs,
    so it needs no real binary/compiler on the test machine."""
    src = tmp_path / "src3"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "source",
         "--ast-frontend", "hybrid", "-o", str(tmp_path / "out3.json")],
    )
    assert res.exit_code != 0, _all_output(res)
    out = _all_output(res)
    assert "--ast-frontend hybrid" in out
    assert "--depth source" in out
    assert not (tmp_path / "out3.json").exists()


def test_dump_depth_source_with_config_hybrid_frontend_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """CodeRabbit review: the hybrid+source rejection must also catch a
    frontend selected via .abicheck.yml's `compile.frontend: hybrid`, not
    just an explicit --ast-frontend flag -- the CLI value alone ("auto"
    here) is not the whole story once resolve_dump_compile_context folds in
    the config file's compile.frontend (CLI > config, but an unset CLI value
    inherits it). The check runs once, after that resolution, for the
    ordinary (non-source-only) binary dump path."""
    so = tmp_path / "fake.so"
    so.write_bytes(b"\x7fELF")
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: hybrid\n")
    res = CliRunner().invoke(
        main,
        ["dump", str(so), "--depth", "source", "--config", str(cfg)],
    )
    assert res.exit_code != 0, _all_output(res)
    out = _all_output(res)
    assert "--ast-frontend hybrid" in out
    assert "--depth source" in out


def test_dump_source_only_depth_source_with_config_hybrid_frontend_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Codex review: the source-only dump path (no SO_PATH) used to return
    via dump_source_only() before resolve_dump_compile_context ever ran, so
    a config-selected `compile.frontend: hybrid` reached the L4 extractor
    unchecked -- not just bypassing this validation, but genuinely using a
    different frontend than the project's .abicheck.yml requested. The
    compile-context resolution (and this check) now runs before the
    so_path-is-None dispatch, so both paths see the same resolved frontend."""
    src = tmp_path / "src5"
    src.mkdir()
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile:\n  frontend: hybrid\n")
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "source", "--config", str(cfg)],
    )
    assert res.exit_code != 0, _all_output(res)
    out = _all_output(res)
    assert "--ast-frontend hybrid" in out
    assert "--depth source" in out


def test_dump_depth_headers_with_hybrid_frontend_not_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The hybrid-vs-source usage-error rejection is scoped to --depth source
    specifically -- hybrid is the normal, supported dual-backend choice for
    the L2 header AST at every other depth, so it must not be blanket-rejected.
    (The invocation may still fail for the unrelated reason that no headers
    were actually parsed -- this test only checks it isn't *this* rejection.)"""
    src = tmp_path / "src4"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "headers",
         "--ast-frontend", "hybrid", "-o", str(tmp_path / "out4.json")],
    )
    assert "--ast-frontend hybrid" not in _all_output(res)

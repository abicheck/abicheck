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

One depth vocabulary across ``compare``/``deep-compare``/``dump``/``scan``; the
G21 ``graph`` rung and ``--collect-mode`` are deprecated aliases; the L5 graph is
an internal consequence of ``--depth source``, never a user rung.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_params import DEPTH_PARAM


def _registered() -> dict:
    """Register every depth-bearing command on ``main`` and return its map."""
    import abicheck.cli_max  # noqa: F401  — registers deep-compare
    import abicheck.cli_scan  # noqa: F401  — registers scan

    return main.commands


# ── One dial: every depth-bearing command shows the same user-facing ladder ──

_DEPTH_COMMANDS = ("compare", "deep-compare", "dump", "scan")


@pytest.mark.parametrize("cmd_name", _DEPTH_COMMANDS)
def test_depth_dial_is_uniform(cmd_name: str) -> None:
    """Each command's ``--depth`` exposes exactly the user ladder; ``graph`` gone."""
    cmd = _registered()[cmd_name]
    depth = next(p for p in cmd.params if "--depth" in getattr(p, "opts", []))
    metavar = depth.type.get_metavar(depth)
    assert metavar == "[symbols|headers|build|source|full]", (cmd_name, metavar)
    assert "graph" not in metavar


# ── Alias resolution: every legacy spelling resolves to the right depth ──────


@pytest.mark.parametrize("depth_value", ["symbols", "headers", "build", "source", "full"])
def test_depth_user_rung_passes_through(depth_value: str) -> None:
    """Each user-ladder rung resolves to itself, independently."""
    assert DEPTH_PARAM.convert(depth_value, None, None) == depth_value


def test_user_depths_match_ladder() -> None:
    """The parametrized ladder above stays in lock-step with ``USER_DEPTHS``."""
    from abicheck.buildsource.scan_levels import USER_DEPTHS

    assert [d.value for d in USER_DEPTHS] == [
        "symbols", "headers", "build", "source", "full"
    ]


@pytest.mark.parametrize("spelling", ["graph", "GRAPH"])
def test_depth_graph_alias_resolves_to_source(spelling: str) -> None:
    """``graph`` is no longer a user rung; it resolves to ``source`` (D6)."""
    assert DEPTH_PARAM.convert(spelling, None, None) == "source"


def _all_output(res: object) -> str:
    """Combined stdout+stderr of a CliRunner result, click-version tolerant."""
    out = getattr(res, "output", "") or ""
    try:
        out += getattr(res, "stderr", "") or ""
    except ValueError:  # stderr not separately captured on this click
        pass
    return out


def test_depth_graph_warns_on_stderr() -> None:
    """The deprecated ``--depth graph`` resolves but prints a one-line note."""
    # A bad operand makes the command fail fast *after* param conversion, so the
    # deprecation note (emitted during conversion) is captured regardless.
    res = CliRunner().invoke(main, ["dump", "--depth", "graph", "/no/such/bin"])
    text = _all_output(res)
    assert "deprecated" in text and "--depth source" in text


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


def test_resolve_dump_depth_symbols_collects_nothing() -> None:
    from abicheck.cli_dump_helpers import resolve_dump_depth

    assert resolve_dump_depth("symbols", False, "off", False) == "off"
    assert resolve_dump_depth("headers", False, "off", False) == "off"
    assert resolve_dump_depth("source", False, "off", False) != "off"


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


@pytest.mark.parametrize("depth", ["symbols", "headers", "source"])
def test_compare_accepts_depth_over_snapshots(tmp_path, depth: str) -> None:  # type: ignore[no-untyped-def]
    """``compare`` folds ``--depth`` into the collect mode for snapshot inputs;
    ``symbols`` clears headers, deeper rungs resolve without error."""
    old = _snap(tmp_path, "libx", "1.0", [_fn("a"), _fn("b")])
    new = _snap(tmp_path, "libx", "2.0", [_fn("a"), _fn("b")])
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "--depth", depth])
    assert res.exit_code == 0, _all_output(res)


def test_compare_collect_mode_deprecation_warns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An explicit ``--collect-mode`` on ``compare`` warns (deprecated, D5)."""
    old = _snap(tmp_path, "liby", "1.0", [_fn("a")])
    new = _snap(tmp_path, "liby", "2.0", [_fn("a")])
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--collect-mode", "off"]
    )
    assert res.exit_code == 0
    assert "deprecated" in _all_output(res)


@pytest.mark.parametrize("depth", ["symbols", "headers"])
def test_deep_compare_depth_over_snapshots(tmp_path, depth: str) -> None:  # type: ignore[no-untyped-def]
    """``deep-compare`` passes snapshot inputs straight through; ``--depth
    symbols`` clears headers, the non-symbols branch leaves them as-is."""
    import abicheck.cli_max  # noqa: F401

    old = _snap(tmp_path, "libz", "1.0", [_fn("a")])
    new = _snap(tmp_path, "libz", "2.0", [_fn("a")])
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--depth", depth]
    )
    assert res.exit_code == 0, _all_output(res)


def test_dump_source_only_collect_mode_warns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A source-only ``dump`` with an explicit ``--collect-mode`` warns before
    any collection (covers the deprecation note + the symbols branch path)."""
    src = tmp_path / "src"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--collect-mode", "build",
         "-o", str(tmp_path / "out.json")],
    )
    assert "deprecated" in _all_output(res)


def test_dump_source_only_depth_symbols(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``dump --depth symbols`` resolves and runs the symbols-only branch."""
    src = tmp_path / "src2"
    src.mkdir()
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(src), "--depth", "symbols",
         "-o", str(tmp_path / "out2.json")],
    )
    # Resolution + symbols-clearing ran; a source-only symbols dump writes an
    # L0-L2 snapshot and exits clean.
    assert res.exit_code == 0, _all_output(res)
    assert (tmp_path / "out2.json").is_file()

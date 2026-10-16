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

"""``compare``'s raw ``--sources``/``--build-info`` sides, stated on the request.

``frontends/cli/compare_inline_evidence.InlineEvidence.apply`` replaced the
nested ``dump`` command ``compare`` used to run for a raw source tree. These
cells pin the decisions it states on each side's ``InputSpec`` against oracles
written out here, not against the module's own helpers:

* which input is collected inline, which is left for resolution to ignore, and
  which pack stays on the out-of-band path, over every operand x evidence x
  collect-mode combination;
* which project config the side runs under, and whether it is trusted;
* the tree's ``compile:`` block merged under the run's context, CLI winning;
* the execution plan the nested dump used to derive (seed mode, ``-p`` match,
  folded L4 compiler, changed-path seed);
* ``scope.exclude_headers``: compare's own set when stated, else the tree's.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.dry_run_estimate import CompileContext
from abicheck.frontends.cli.compare_inline_evidence import (
    InlineEvidence,
    InlineEvidenceRun,
    OutOfBandPacks,
)
from abicheck.frontends.cli.dump_debug_config import DumpDebugConfig
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.service_compare_evidence import without_unusable_raw_evidence
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest

_OPERANDS = ("elf", "snapshot")
_EVIDENCE = ("none", "raw", "pack")
_MODES = ("off", "build", "source-target")


def _ctx() -> click.Context:
    command = click.Command(
        "compare", params=[click.Option(["--nostdinc"], is_flag=True)]
    )
    return click.Context(command)


def _run(ctx: click.Context, **overrides: object) -> InlineEvidenceRun:
    fields: dict[str, object] = {
        "ctx": ctx,
        "compile_context": CompileContext(frontend="auto"),
        "old_header_backend": None,
        "new_header_backend": None,
        "collect_mode": "source-target",
        "depth": None,
        "changed_paths": (),
        "config": None,
        "lang": "c++",
        "debug": DumpDebugConfig(),
        "old_debug_roots": (),
        "new_debug_roots": (),
    }
    fields.update(overrides)
    return InlineEvidenceRun(**fields)  # type: ignore[arg-type]


def _operand(root: Path, kind: str) -> Path:
    if kind == "elf":
        path = root / "libfoo.so"
        path.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 57)
    else:
        path = root / "old.abi.json"
        path.write_text(
            snapshot_to_json(AbiSnapshot(library="libfoo.so", version="1")),
            encoding="utf-8",
        )
    return path


def _evidence(root: Path, name: str, kind: str) -> Path | None:
    if kind == "none":
        return None
    path = root / name
    path.mkdir()
    if kind == "pack":
        (path / "manifest.json").write_text(
            json.dumps({"build_source_pack_version": 1}), encoding="utf-8"
        )
    return path


def _apply(
    ctx: click.Context,
    old: InputSpec,
    sources: Path | None,
    build_info: Path | None,
    run: InlineEvidenceRun,
) -> tuple[CompareRequest, OutOfBandPacks]:
    request = CompareRequest(old=old, new=InputSpec(path=old.path))
    evidence = InlineEvidence(sources, build_info, None, None)
    with ctx:
        applied = evidence.apply(request, run)
    return applied, evidence.out_of_band(applied)


@pytest.mark.parametrize(
    ("operand", "sources_kind", "build_info_kind", "mode"),
    list(itertools.product(_OPERANDS, _EVIDENCE, _EVIDENCE, _MODES)),
)
def test_every_input_is_collected_ignored_or_kept_out_of_band(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operand: str,
    sources_kind: str,
    build_info_kind: str,
    mode: str,
) -> None:
    """Truth table over operand x ``--sources`` x ``--build-info`` x mode.

    A raw input is collectable only on a native operand at a collecting mode.
    A pack-shaped ``--build-info`` rides along with a collected tree (that
    seeds the tree's L4 replay); every other pack stays out-of-band, and an
    ignored raw input is never mistaken for one.
    """
    monkeypatch.chdir(tmp_path)
    path = _operand(tmp_path, operand)
    sources = _evidence(tmp_path, "src", sources_kind)
    build_info = _evidence(tmp_path, "build", build_info_kind)

    collectable = operand == "elf" and mode != "off"
    sources_collected = sources_kind == "raw" and collectable
    build_info_collected = build_info_kind == "raw" and collectable
    collected = sources_collected or build_info_collected
    # What the request states for the side (an ignored raw input stays, for
    # resolution to ignore with a note).
    want_sources = sources if sources_kind == "raw" else None
    want_build_info = (
        build_info
        if build_info_kind == "raw" or (build_info_kind == "pack" and sources_collected)
        else None
    )
    want_packs = (
        build_info if build_info_kind == "pack" and not sources_collected else None,
        sources if sources_kind == "pack" else None,
    )

    ctx = _ctx()
    request, packs = _apply(
        ctx, InputSpec(path=path), sources, build_info, _run(ctx, collect_mode=mode)
    )

    assert request.resolved_collect_mode == mode
    assert (request.old.sources, request.old.build_info) == (
        want_sources,
        want_build_info,
    )
    assert (packs.old_build_info, packs.old_sources) == want_packs
    assert (request.old.execution is not None) is collected
    # Resolution's own rule then drops exactly the inputs not collected.
    noted: list[str] = []
    resolved = without_unusable_raw_evidence(request, noted.append).old
    assert resolved.sources == (sources if sources_collected else None)
    assert resolved.build_info == (want_build_info if collected else None)
    assert bool(noted) is ("raw" in (sources_kind, build_info_kind) and not collectable)


def _write_config(path: Path, body: str = "") -> Path:
    path.write_text("version: 3\n" + body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("explicit", "in_tree", "in_cwd"),
    list(itertools.product((False, True), repeat=3)),
)
def test_the_side_runs_under_dumps_config_selection_and_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
    in_tree: bool,
    in_cwd: bool,
) -> None:
    """``--config`` > the tree root's ``.abicheck.yml`` > the nearest one above
    the working directory; only ``--config`` is trusted (may run
    ``build.query``)."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    tree = tmp_path / "src"
    tree.mkdir()
    cfg = _write_config(tmp_path / "explicit.yml") if explicit else None
    tree_cfg = _write_config(tree / ".abicheck.yml") if in_tree else None
    cwd_cfg = _write_config(work / ".abicheck.yml") if in_cwd else None
    want = cfg or tree_cfg or cwd_cfg

    ctx = _ctx()
    request, _ = _apply(
        ctx,
        InputSpec(path=_operand(tmp_path, "elf")),
        tree,
        None,
        _run(ctx, config=cfg),
    )

    execution = request.old.execution
    assert execution is not None
    assert (execution.build_config, execution.build_config_explicit) == (
        want,
        explicit,
    )
    assert execution.allow_build_query is True


def test_the_trees_compile_block_merges_under_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree's ``compile:`` fills what the run left unset; a per-side
    frontend override and an already-resolved ``nostdinc`` win over it."""
    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "src"
    tree.mkdir()
    _write_config(
        tree / ".abicheck.yml",
        "compile:\n  frontend: clang\n  sysroot: /from/cfg\n  nostdinc: false\n",
    )
    operand = _operand(tmp_path, "elf")
    ctx = _ctx()

    def compile_of(**run: object) -> CompileContext:
        request, _ = _apply(ctx, InputSpec(path=operand), tree, None, _run(ctx, **run))
        assert request.old.compile is not None
        return request.old.compile

    merged = compile_of()
    assert (merged.frontend, merged.sysroot) == ("clang", Path("/from/cfg"))
    assert compile_of(old_header_backend="castxml").frontend == "castxml"
    kept = compile_of(compile_context=CompileContext(frontend="auto", nostdinc=True))
    assert kept.nostdinc is True


def test_the_execution_plan_is_the_nested_dumps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed mode, the folded L4 compiler, the changed-path seed and the ``-p``
    compile database all reach the side's plan."""
    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "src"
    tree.mkdir()
    build = tmp_path / "build"
    build.mkdir()
    db = build / "compile_commands.json"
    db.write_text("[]", encoding="utf-8")
    header = tree / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")
    ctx = _ctx()

    request, _ = _apply(
        ctx,
        InputSpec(path=_operand(tmp_path, "elf"), headers=(header,)),
        tree,
        build,
        _run(ctx, collect_mode="build", changed_paths=("src/a.c",)),
    )

    execution = request.old.execution
    assert execution is not None
    assert execution.seed_collect_mode == "build"
    assert execution.changed_paths == ("src/a.c",)
    assert execution.source_frontend_from_folded_context is True
    assert execution.compile_db == db
    # Without headers no L2 parse runs, so no database is matched.
    bare, _ = _apply(
        ctx, InputSpec(path=_operand(tmp_path, "elf")), tree, build, _run(ctx)
    )
    assert bare.old.execution is not None and bare.old.execution.compile_db is None


@pytest.mark.parametrize("stated", [(), ("internal/*",)])
@pytest.mark.parametrize("tree_has_scope", [False, True])
def test_exclude_headers_prefer_the_run_then_the_tree_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stated: tuple[str, ...],
    tree_has_scope: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "src"
    tree.mkdir()
    _write_config(
        tree / ".abicheck.yml",
        "scope:\n  exclude_headers: ['detail/*']\n" if tree_has_scope else "",
    )
    want = stated or (("detail/*",) if tree_has_scope else ())
    ctx = _ctx()

    request, _ = _apply(
        ctx,
        InputSpec(path=_operand(tmp_path, "elf"), exclude_headers=stated),
        tree,
        None,
        _run(ctx),
    )

    assert tuple(request.old.exclude_headers) == want


def test_a_side_with_no_evidence_is_unchanged(tmp_path: Path) -> None:
    ctx = _ctx()
    side = InputSpec(path=_operand(tmp_path, "elf"), headers=(tmp_path / "a.h",))
    request, packs = _apply(ctx, side, None, None, _run(ctx))
    assert request.old == replace(side)
    assert packs.old_sources is None and packs.old_build_info is None


def _snap(version: str, *names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        from_headers=True,
        functions=[
            Function(
                name=n,
                mangled=f"_Z{len(n)}{n}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for n in names
        ],
    )


def test_cli_ignores_a_raw_tree_on_a_snapshot_operand_and_still_decides(
    tmp_path: Path,
) -> None:
    """Through the public CLI: the tree reaches the shared resolution, which
    ignores it with a note, and the comparison still runs to a verdict."""
    from abicheck.cli import main

    old = tmp_path / "old.json"
    old.write_text(snapshot_to_json(_snap("1", "foo", "bar")), encoding="utf-8")
    new = tmp_path / "new.json"
    new.write_text(snapshot_to_json(_snap("2", "foo")), encoding="utf-8")
    tree = tmp_path / "src"
    tree.mkdir()

    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--sources", f"old={tree}"]
    )

    out = result.output + (result.stderr or "")
    assert "raw sources is ignored" in out, out
    assert result.exit_code == 4, out


@pytest.mark.parametrize("depth", [None, "build", "source", "SOURCE"])
@pytest.mark.parametrize("frontend_from", ["tree", "side", "none"])
def test_depth_source_rejects_a_hybrid_frontend_on_a_collected_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    depth: str | None,
    frontend_from: str,
) -> None:
    """``--depth source`` has no hybrid L4 extractor, whichever spelling chose
    ``hybrid`` for the side (its tree's ``compile.frontend`` or the per-side
    override); every other depth, and every other frontend, collects."""
    from abicheck.errors import ValidationError

    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "src"
    tree.mkdir()
    if frontend_from == "tree":
        _write_config(tree / ".abicheck.yml", "compile:\n  frontend: hybrid\n")
    ctx = _ctx()
    run = _run(
        ctx,
        depth=depth,
        old_header_backend="hybrid" if frontend_from == "side" else None,
    )
    rejected = (depth or "").lower() == "source" and frontend_from != "none"

    def apply() -> object:
        return _apply(ctx, InputSpec(path=_operand(tmp_path, "elf")), tree, None, run)

    if rejected:
        with pytest.raises(ValidationError, match="compile.frontend: castxml"):
            apply()
    else:
        apply()


def test_cli_hybrid_rejection_is_a_usage_error_naming_live_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.cli import main

    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "src"
    tree.mkdir()
    _write_config(tree / ".abicheck.yml", "compile:\n  frontend: hybrid\n")
    lib = _operand(tmp_path, "elf")

    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(lib),
            str(lib),
            "--sources",
            f"old={tree}",
            "--depth",
            "source",
        ],
    )

    out = result.output + (result.stderr or "")
    assert result.exit_code == 64, out
    assert "hybrid" in out and "compile.frontend: castxml" in out, out
    for dead in ("--old-ast-frontend", "--old-sources", "--ast-frontend"):
        assert dead not in out, out


@pytest.mark.parametrize(
    ("manifest", "is_pack"),
    [
        (None, False),  # a plain checkout
        ('{"name": "my-project"}', False),  # a stray manifest.json stays raw
        ('{"build_source_pack_version": 1}', True),
        ('{"evidence_pack_version": 1}', True),  # the legacy marker
        ('{"kind": "abicheck_inputs", "abicheck_inputs_version": 1}', True),
        ("not json{", True),  # corrupt: loaded, so the load fails loudly
    ],
)
def test_only_a_pack_manifest_routes_a_directory_out_of_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: str | None, is_pack: bool
) -> None:
    """A pack (including a Flow-2 ``abicheck_inputs`` pack) is loaded, never
    collected from -- at ``off`` it would otherwise be dropped as an ignored
    raw tree; a checkout that merely holds a ``manifest.json`` is collected."""
    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "evidence"
    tree.mkdir()
    if manifest is not None:
        (tree / "manifest.json").write_text(manifest, encoding="utf-8")
    ctx = _ctx()

    request, packs = _apply(
        ctx,
        InputSpec(path=_operand(tmp_path, "elf")),
        None,
        tree,
        _run(ctx, collect_mode="off"),
    )

    assert (packs.old_build_info == tree) is is_pack
    assert (request.old.build_info == tree) is not is_pack

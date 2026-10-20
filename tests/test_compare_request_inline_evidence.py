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

"""A typed compare side can state everything the CLI's inline ``dump`` does.

The native ``compare`` CLI used to collect a raw ``--old/new-sources`` tree by
running a nested ``dump`` and comparing the temporary snapshot it wrote. These
cells pin the typed request fields and resolution rules that let resolution do
that itself:

* ``CompareRequest.resolved_collect_mode`` -- the front end's own collect
  mode wins over depth/input inference;
* ``InputSpec.execution`` -- the side's ``DumpExecutionOptions`` reach
  ``_resolve_side_snapshot_impl`` verbatim (config trust, the ``-p`` match,
  the L2 seed, the folded L4 compiler, the changed-path seed), and the
  planner sees only an *explicit* config;
* raw evidence a run cannot collect from (collect mode ``off``, or a
  non-binary operand) is ignored with a note, packs are kept.

The raw-evidence rule is checked over its whole small domain against a truth
table written out here, not against the implementation's own predicate.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck import service_compare_evidence as sce
from abicheck.errors import ValidationError
from abicheck.model import AbiSnapshot
from abicheck.workflows.artifact.dump_execution_options import DumpExecutionOptions
from abicheck.workflows.artifact.execute import SideResolution
from abicheck.workflows.compare import (
    resolve as compare_resolve,
)
from abicheck.workflows.plan import AnalysisPlanner
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest, DumpRequest

# ── operands and evidence inputs ─────────────────────────────────────────


def _elf(path: Path) -> Path:
    path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    return path


def _snapshot(path: Path) -> Path:
    path.write_text(json.dumps({"library": "libfoo.so", "version": "1"}))
    return path


def _symvers(path: Path) -> Path:
    path.write_text("0x12345678\tfoo\tvmlinux\tEXPORT_SYMBOL\n")
    return path


def _raw_tree(path: Path) -> Path:
    path.mkdir()
    (path / "foo.c").write_text("int foo(void) { return 0; }\n")
    return path


def _pack(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    return path


OPERANDS = {"elf": _elf, "snapshot": _snapshot, "symvers": _symvers}
EVIDENCE = {"none": None, "raw": _raw_tree, "pack": _pack}
DEPTHS = (None, "binary", "headers", "build", "source")


def _expected_kept(operand: str, evidence: str, mode: str) -> bool:
    """The truth table: is this side's ``sources`` input still used?

    Written out case by case -- a pack is always kept; a raw tree only on a
    native binary, at a depth that collects build/source evidence."""
    if evidence != "raw":
        return True
    return operand == "elf" and mode != "off"


@pytest.mark.parametrize(
    ("operand", "evidence", "depth"),
    list(itertools.product(OPERANDS, EVIDENCE, DEPTHS)),
)
def test_raw_evidence_is_ignored_exactly_where_it_cannot_be_collected(
    operand: str, evidence: str, depth: str | None, tmp_path: Path
) -> None:
    path = OPERANDS[operand](tmp_path / f"op.{operand}")
    make = EVIDENCE[evidence]
    sources = make(tmp_path / "src") if make is not None else None
    other = _elf(tmp_path / "other.so")
    request = CompareRequest(
        old=InputSpec(path=path, sources=sources),
        new=InputSpec(path=other),
        depth=depth,
    )
    mode = sce.compare_collect_mode(request)
    notes: list[str] = []

    out = sce.without_unusable_raw_evidence(request, notes.append)

    kept = out.old.sources == sources
    assert kept is _expected_kept(operand, evidence, mode)
    assert out.new == request.new
    # An ignored input is never silent; a kept one adds no note.
    assert bool(notes) is (not kept)
    if not kept:
        assert "old side's raw sources is ignored" in notes[0]


def test_raw_build_info_follows_the_same_rule(tmp_path: Path) -> None:
    """``build_info`` is ignored by the same rule, each named in the note."""
    snapshot = _snapshot(tmp_path / "old.json")
    request = CompareRequest(
        old=InputSpec(
            path=snapshot,
            sources=_raw_tree(tmp_path / "src"),
            build_info=_raw_tree(tmp_path / "build"),
        ),
        new=InputSpec(path=_elf(tmp_path / "new.so")),
    )
    notes: list[str] = []
    out = sce.without_unusable_raw_evidence(request, notes.append)
    assert (out.old.sources, out.old.build_info) == (None, None)
    assert "raw sources and build_info" in notes[0]
    assert "not a native binary" in notes[0]


def test_nothing_to_ignore_returns_the_request_itself(tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so")),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
    )
    assert sce.without_unusable_raw_evidence(request) is request


# ── collect mode ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("stated", ["off", "build", "source", "source-changed"])
def test_a_stated_collect_mode_wins_over_inference(stated: str, tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so"), sources=tmp_path),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
        depth="build",
        resolved_collect_mode=stated,
    )
    assert sce.compare_collect_mode(request) == stated
    old, new = sce.resolve_compare_request_evidence(request, None)
    assert old.collect_mode == new.collect_mode == stated


def test_an_unstated_collect_mode_is_still_inferred(tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so"), sources=tmp_path),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
    )
    assert sce.compare_collect_mode(request) == sce.collect_mode_for(
        None, request.old, request.new
    )


def test_an_unknown_collect_mode_is_a_validation_error(tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so")),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
        resolved_collect_mode="Build",
    )
    with pytest.raises(ValidationError, match="resolved_collect_mode"):
        request.validate()


def test_the_planner_sees_the_stated_collect_mode(tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so")),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
        resolved_collect_mode="build",
    )
    plan = AnalysisPlanner.resolve(request)
    assert {side.resolved_collect_mode for side in plan.sides} == {"build"}


# ── per-side execution plan ───────────────────────────────────────────────


def _spy_resolution(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    seen: dict[str, dict] = {}

    def _fake_impl(side, evidence, **kwargs):
        seen[side.version] = kwargs
        return SideResolution(
            snapshot=AbiSnapshot(library="libfoo.so", version=side.version),
            effective_includes=(),
            effective_compile_context=None,
        )

    monkeypatch.setattr(compare_resolve, "_resolve_side_snapshot_impl", _fake_impl)
    return seen


def test_a_side_execution_plan_reaches_side_resolution_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _spy_resolution(monkeypatch)
    config = tmp_path / ".abicheck.yml"
    config.write_text("{}\n")
    execution = DumpExecutionOptions(
        build_config=config,
        build_config_explicit=False,
        allow_build_query=True,
        changed_paths=("src/foo.c",),
        seed_collect_mode="build",
        source_frontend_from_folded_context=True,
    )
    request = CompareRequest(
        old=InputSpec(path=_elf(tmp_path / "a.so"), version="old", execution=execution),
        new=InputSpec(path=_elf(tmp_path / "b.so"), version="new"),
        changed_paths=("other.c",),
    )

    compare_resolve.resolve_compare_request(request)

    old = seen["old"]
    assert old["build_config"] == config
    assert old["build_config_explicit"] is False
    assert old["allow_build_query"] is True
    assert old["changed_paths"] == ("src/foo.c",)
    assert old["seed_collect_mode"] == "build"
    assert old["source_frontend_from_folded_context"] is True
    # A side with no plan keeps the request-level defaults, unchanged.
    new = seen["new"]
    assert new["build_config"] is None
    assert new["build_config_explicit"] is True
    assert new["allow_build_query"] is None
    assert new["changed_paths"] == ("other.c",)
    assert new["seed_collect_mode"] is None
    assert new["source_frontend_from_folded_context"] is False


def test_a_side_execution_plan_matches_its_compile_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``-p`` match runs for a compare side as it does for a dump."""
    seen = _spy_resolution(monkeypatch)
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    src = tmp_path / "foo.c"
    src.write_text('#include "foo.h"\n')
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["cc", "-DFOO=1", "-c", str(src)],
                }
            ]
        )
    )
    notes: list[str] = []
    request = CompareRequest(
        old=InputSpec(
            path=_elf(tmp_path / "a.so"),
            version="old",
            headers=(header,),
            execution=DumpExecutionOptions(compile_db=db),
        ),
        new=InputSpec(path=_elf(tmp_path / "b.so"), version="new"),
    )

    compare_resolve.resolve_compare_request(request, notify=notes.append)

    assert seen["old"]["compile_db_matched"] is True
    assert "-DFOO=1" in seen["old"]["compile_db_tokens"]
    assert seen["new"]["compile_db_tokens"] == ()
    assert any(note.startswith("Build context:") for note in notes)


def test_build_config_is_stated_once(tmp_path: Path) -> None:
    request = CompareRequest(
        old=InputSpec(
            path=_elf(tmp_path / "a.so"),
            build_config=tmp_path / "x.yml",
            execution=DumpExecutionOptions(),
        ),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
    )
    with pytest.raises(ValidationError, match="build_config twice"):
        request.validate()


def test_a_dump_input_rejects_an_execution_plan(tmp_path: Path) -> None:
    request = DumpRequest(
        input=InputSpec(path=_elf(tmp_path / "a.so"), execution=DumpExecutionOptions())
    )
    assert any("compare side only" in e for e in request.validation_errors())


@pytest.mark.parametrize("explicit", [True, False])
def test_the_planner_sees_only_an_explicit_execution_config(
    explicit: bool, tmp_path: Path
) -> None:
    """A discovered config is not an explicit one: the pre-flight planner
    checks only the latter, as it did for the CLI's nested dump."""
    config = tmp_path / ".abicheck.yml"
    config.write_text("{}\n")
    request = CompareRequest(
        old=InputSpec(
            path=_elf(tmp_path / "a.so"),
            execution=DumpExecutionOptions(
                build_config=config, build_config_explicit=explicit
            ),
        ),
        new=InputSpec(path=_elf(tmp_path / "b.so")),
    )
    old_plan = AnalysisPlanner.resolve(request).sides[0]
    assert old_plan.build_config == (config if explicit else None)

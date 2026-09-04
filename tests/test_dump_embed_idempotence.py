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

"""The write-time L3-L5 embed must be idempotent (PR 3A blocker 5, sub-issue 3).

The ``dump`` CLI embeds inline build/source evidence at *write* time
(``cli_buildsource._write_snapshot_output``), while the typed pipeline embeds
at *resolve* time (``service_dump_pipeline.execute_dump_request`` ->
``service_input_resolution._resolve_side_snapshot_impl``). Routing the real
``dump`` run through the typed executor -- what PR 3A is working toward --
would therefore embed **twice**, re-running L4 source-ABI replay: a real
compiler invocation per translation unit, not a cheap recomputation, and one
that overwrites the pack the first embed produced.

``cli_buildsource.build_source_already_satisfies`` is the check-before-embed
guard that closes it. These tests pin both halves: that it skips a second
embed over an already-satisfied snapshot, and -- just as importantly -- that
it is a *no-op for every path that exists today*, so landing it ahead of the
migration cannot silently stop the one embed the CLI does perform.

The end-to-end count (``integration``, needs a real g++) is the one that would
catch a future double-embed for real, since it counts invocations through the
actual CLI rather than through a hand-called helper.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.model import AbiSnapshot


def _l3_pack() -> Any:
    """A ``BuildSourcePack`` whose L3 layer is genuinely complete.

    "Satisfied" is two independent conditions in
    ``_missing_requested_evidence_layers``, and both have to hold: a coverage
    row that is not ``NOT_COLLECTED``, *and* a non-empty payload
    (``_layer_payload_empty``) -- a row can read ``PRESENT`` over an empty
    payload. Built here with both, for L3 only, so the same object is
    "satisfied" for ``collect_mode="build"`` and "falls short" for
    ``collect_mode="source-target"`` (which also asks for L4/L5) -- which is
    exactly the pair of answers the guard has to tell apart.
    """
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import (
        CoverageStatus,
        DataLayer,
        LayerCoverage,
    )
    from abicheck.buildsource.pack import BuildSourcePack

    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(
            compile_units=[CompileUnit(id="cu1", source="a.c")]
        ),
    )
    pack.manifest.coverage.append(
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT)
    )
    return pack


class TestBuildSourceAlreadySatisfies:
    """The predicate itself, stated as its three distinguishable answers."""

    def test_no_pack_is_never_satisfied(self) -> None:
        """The case that keeps the guard a no-op for today's CLI.

        ``_missing_requested_evidence_layers`` answers ``[]`` for a ``None``
        pack (nothing was requested *of* it), so a predicate built on that
        function alone would read a bare snapshot as already-complete and skip
        the only embed the ``dump`` CLI performs.
        """
        from abicheck.cli_buildsource import build_source_already_satisfies

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        assert snap.build_source is None
        assert not build_source_already_satisfies(snap, "source-target")

    def test_pack_missing_a_requested_layer_is_not_satisfied(self) -> None:
        """L3-only evidence does not satisfy a source-scoped collect mode."""
        from abicheck.cli_buildsource import build_source_already_satisfies

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        snap.build_source = _l3_pack()
        assert not build_source_already_satisfies(snap, "source-target")

    def test_pack_covering_the_requested_layers_is_satisfied(self) -> None:
        from abicheck.cli_buildsource import build_source_already_satisfies

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        snap.build_source = _l3_pack()
        assert build_source_already_satisfies(snap, "build")

    def test_collect_mode_off_with_a_pack_is_satisfied(self) -> None:
        """Nothing requested, a pack already present -> nothing to do.

        Re-running the embed here could only replace an existing pack with a
        weaker one, so "off" is satisfaction rather than a special case.
        """
        from abicheck.cli_buildsource import build_source_already_satisfies

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        snap.build_source = _l3_pack()
        assert build_source_already_satisfies(snap, "off")


class TestWriteSnapshotOutputChecksBeforeEmbedding:
    """``_write_snapshot_output``'s own guard, spied at the embed boundary."""

    def _spy(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        calls: list[Any] = []
        from abicheck import cli_buildsource

        def _fake_embed(snap: Any, *args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))

        monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)
        return calls

    def test_embeds_once_when_the_snapshot_carries_no_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Today's CLI shape: the guard must not change it."""
        from abicheck.cli_buildsource import _write_snapshot_output

        calls = self._spy(monkeypatch)
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        _write_snapshot_output(
            snap,
            tmp_path / "out.json",
            sources=tmp_path,
            collect_mode="source-target",
        )
        assert len(calls) == 1

    def test_skips_the_embed_when_the_snapshot_is_already_satisfied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The migration case: resolve-time embed already ran, so write-time
        must not run L4 replay a second time."""
        from abicheck.cli_buildsource import _write_snapshot_output

        calls = self._spy(monkeypatch)
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        snap.build_source = _l3_pack()
        _write_snapshot_output(
            snap,
            tmp_path / "out.json",
            sources=tmp_path,
            collect_mode="build",
        )
        assert calls == []
        # The pack the first embed produced is still the one written out.
        assert snap.build_source is not None

    def test_still_embeds_when_the_existing_pack_falls_short(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A partial pack is not satisfaction -- the deeper layers still run.

        Without this direction the guard would be a silent evidence-loss bug
        rather than a de-duplication: an L3-only pack embedded upstream would
        suppress the write-time embed that was supposed to add L4/L5.
        """
        from abicheck.cli_buildsource import _write_snapshot_output

        calls = self._spy(monkeypatch)
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        snap.build_source = _l3_pack()
        _write_snapshot_output(
            snap,
            tmp_path / "out.json",
            sources=tmp_path,
            collect_mode="source-target",
        )
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# End-to-end: exactly one embed through the real `dump` CLI
# ---------------------------------------------------------------------------

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None


def _build_library(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A tiny real C++ library plus a matching ``compile_commands.json``.

    Same shape as ``tests/test_dump_scan_l3_comparability.py``'s builder --
    kept local rather than imported so neither module's fixture can be
    reshaped for the other's needs without noticing.
    """
    header = tmp_path / "widget.h"
    header.write_text(
        "#pragma once\nstruct Widget { int x; int y; int sum() const; };\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint Widget::sum() const { return x + y; }\n',
        encoding="utf-8",
    )
    so_path = tmp_path / "libwidget.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(so_path), str(src)],
        check=True,
        capture_output=True,
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": [
                        "g++", "-std=c++17", "-fPIC", "-c", str(src), "-o", "widget.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="ELF/Linux-scoped repro"
)
@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
def test_cli_dump_depth_source_embeds_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One real ``dump --depth source`` run performs one L3-L5 embed.

    Counted at both places L4 source-ABI replay can be driven from for this
    command: ``cli_buildsource.embed_build_source`` (the write-time
    fallback, still live for the case ``build_source_already_satisfies``
    finds the resolve-time embed insufficient) and
    ``workflows.artifact.execute.embed_side_build_source`` (the resolve-time
    embed CLI cleanup phase two, PR C's real-run migration onto
    ``execute_dump_request`` now drives). Before that migration only the
    former ever ran for this command; now only the latter does for the
    common case exercised here, and ``build_source_already_satisfies``
    is what makes the write-time step a no-op rather than a second embed --
    counting only one of the two would not have caught the write-time step
    silently doubling this if that guard ever regressed.
    """
    from abicheck import cli_buildsource
    from abicheck.cli import main
    from abicheck.workflows.artifact import execute as _artifact_execute

    so_path, header, compile_db = _build_library(tmp_path)
    real_write_time_embed = cli_buildsource.embed_build_source
    real_resolve_time_embed = _artifact_execute.embed_side_build_source
    calls: list[str] = []

    def _counting_write_time_embed(*args: Any, **kwargs: Any) -> Any:
        calls.append("write_time")
        return real_write_time_embed(*args, **kwargs)

    def _counting_resolve_time_embed(*args: Any, **kwargs: Any) -> Any:
        calls.append("resolve_time")
        return real_resolve_time_embed(*args, **kwargs)

    monkeypatch.setattr(
        cli_buildsource, "embed_build_source", _counting_write_time_embed
    )
    monkeypatch.setattr(
        _artifact_execute, "embed_side_build_source", _counting_resolve_time_embed
    )

    out = tmp_path / "snap.json"
    result = CliRunner().invoke(
        main,
        [
            "dump", str(so_path),
            "-H", str(header),
            "--sources", str(tmp_path),
            "--build-info", str(compile_db),
            "--depth", "source",
            "--ast-frontend", "clang",
            "-o", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, (calls, result.output)


class TestWriteTimeEmbedCarriesThePublicHeaderRoots:
    """The write-time embed must hand L4 replay this dump's public-header roots.

    Found by measuring whole-snapshot parity between the `dump` CLI's written
    snapshot and `execute_dump_request`'s over the same real `g++` build and
    real clang header parse (CLI cleanup phase two, PR 3A -- the item-1
    verification bar). Everything agreed except the L3-L5 payload, and the
    difference was not cosmetic: the CLI's snapshot recorded ``0/2 symbols
    matched``, ``reachable_declarations=0`` and ``fact_family_states:
    empty-confirmed`` where the typed path recorded ``1/2`` matched and a real
    ``source_decl_to_binary_symbol`` mapping.

    The cause is here: `_write_snapshot_output`'s own `embed_build_source` call
    passed no `public_headers`/`public_header_dirs` at all, so
    `collect_inline_pack` ran L4 replay with an *empty* `public_header_roots`
    set -- every declaration classifies private, nothing links, and every
    L4-derived source-ABI finding is silently inert for a `dump`-produced
    baseline while the coverage row honestly (but opaquely) reports "partial".
    `compare`'s implicit dump, `scan`'s candidate and the typed API all pass
    their roots and link correctly.

    Pinned at the embed boundary (fast lane) rather than only end to end: the
    real-toolchain sibling in `tests/test_dump_cli_typed_api_parity.py` is
    `integration`-marked and skips without castxml, so it cannot gate this.
    """

    def _spy(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        calls: list[Any] = []
        from abicheck import cli_buildsource

        def _fake_embed(snap: Any, *args: Any, **kwargs: Any) -> None:
            calls.append(kwargs)

        monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)
        return calls

    def test_public_roots_reach_the_embed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.cli_buildsource import _write_snapshot_output

        calls = self._spy(monkeypatch)
        hdr = tmp_path / "api.h"
        hdr.write_text("int f(void);\n", encoding="utf-8")
        incdir = tmp_path / "include"
        incdir.mkdir()

        _write_snapshot_output(
            AbiSnapshot(library="libfoo.so", version="1.0"),
            tmp_path / "out.json",
            sources=tmp_path,
            collect_mode="source-target",
            public_headers=(hdr,),
            public_header_dirs=(incdir,),
        )
        assert len(calls) == 1
        assert calls[0]["public_headers"] == (str(hdr),)
        assert calls[0]["public_header_dirs"] == (str(incdir),)

    def test_no_roots_still_embeds_with_empty_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other direction: a headerless dump is unchanged, not broken."""
        from abicheck.cli_buildsource import _write_snapshot_output

        calls = self._spy(monkeypatch)
        _write_snapshot_output(
            AbiSnapshot(library="libfoo.so", version="1.0"),
            tmp_path / "out.json",
            sources=tmp_path,
            collect_mode="source-target",
        )
        assert len(calls) == 1
        assert calls[0]["public_headers"] == ()
        assert calls[0]["public_header_dirs"] == ()

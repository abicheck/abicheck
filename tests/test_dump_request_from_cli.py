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

"""`dump_cmd` builds one real `DumpRequest`, and `--dry-run` renders from it.

CLI cleanup phase two, PR 3A blocker 5. Before this, `dump_cmd` never
constructed a `DumpRequest` at all: `--dry-run` and the real run each read
`dump_cmd`'s own hand-derived locals, so the preview was a second
implementation of resolution kept in step by review discipline alone.

The risk the plan names for a half-migration is specific and worth restating,
because these tests exist to foreclose it: *"a preview built from one resolver
describing an execution built from another is worse than two hand-synced
implementations, since it looks authoritative without being connected to what
actually runs."* Two things answer it here.

1. The request is built from the CLI's **already-resolved** values (the
   resolved `CompileContext`, the resolved frontend, the resolved
   explicit-language decision), so it records the run rather than re-deriving
   it.
2. For the fields `service_dump_pipeline.resolve_dump_request` *does* derive
   independently — the header set, the collect mode, the header backend —
   `TestResolvedRequestAgreesWithTheCliLocals` asserts equality against what
   `dump_cmd` computed, across the invocation shapes where they could
   plausibly differ. A divergence fails here rather than showing up as a
   dry-run report describing a run that never happened.

The real ELF/PE/Mach-O execution still goes through
`perform_elf_dump`/`handle_non_elf_dump`; see `abicheck/cli_dump_request.py`'s
module docstring and the plan's PR 3A section for the three obstacles that
remain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A header, a source tree and a compile database — no compiler needed.

    Every assertion below is about *resolution*, which is exactly the part
    that must not invoke a compiler, so a real binary is deliberately absent:
    `dump --dry-run` never executes anything.
    """
    header = tmp_path / "api.h"
    header.write_text("int f(void);\n", encoding="utf-8")
    src = tmp_path / "api.c"
    src.write_text("int f(void) { return 0; }\n", encoding="utf-8")
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": ["cc", "-std=c11", "-c", str(src), "-o", "api.o"],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return header, tmp_path, compile_db


class TestDumpCmdBuildsARequest:
    def test_dry_run_resolves_a_real_dump_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dry-run branch goes through the shared resolve-only pipeline.

        Spied at `cli.resolve_dump_request_for_cli`'s own callee so this
        asserts the *pipeline* ran, not merely that a helper with the right
        name was called.
        """
        from abicheck import service_dump_pipeline

        header, sources, compile_db = _project(tmp_path)
        seen: list[Any] = []
        real = service_dump_pipeline.resolve_dump_request

        def _spy(request: Any) -> Any:
            seen.append(request)
            return real(request)

        monkeypatch.setattr(service_dump_pipeline, "resolve_dump_request", _spy)
        result = CliRunner().invoke(
            main,
            [
                "dump", "-H", str(header), "--sources", str(sources),
                "--build-info", str(compile_db), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(seen) == 1
        request = seen[0]
        # Built from the real CLI parameters, not a placeholder.
        assert request.input.headers == (header,)
        assert request.input.sources == sources
        assert request.input.build_info == compile_db
        # And carrying the CLI's own already-resolved compile context, rather
        # than leaving the pipeline to re-derive one from raw flags.
        assert request.input.compile is not None

    def test_source_only_dry_run_builds_a_pathless_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dump --sources ./tree` with no SO_PATH is expressible.

        This is the shape blocker 5 sub-issue 1 widened `InputSpec.path` for:
        `dump_cmd` dispatches on `so_path is None`, so it could not have built
        one request covering both branches while the field was required.
        """
        from abicheck import service_dump_pipeline

        _header, sources, _db = _project(tmp_path)
        seen: list[Any] = []
        real = service_dump_pipeline.resolve_dump_request

        def _spy(request: Any) -> Any:
            seen.append(request)
            return real(request)

        monkeypatch.setattr(service_dump_pipeline, "resolve_dump_request", _spy)
        result = CliRunner().invoke(
            main, ["dump", "--sources", str(sources), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert len(seen) == 1
        assert seen[0].input.path is None
        assert seen[0].input.sources == sources


class TestResolvedRequestAgreesWithTheCliLocals:
    """The half-migration guard: resolved values must equal the CLI's own.

    `render_dump_dry_run` is now fed `resolved.headers`/`resolved.collect_mode`/
    `resolved.header_backend` instead of `dump_cmd`'s locals. These assert the
    two are the same thing for the shapes where they could diverge — a
    regression in either resolver fails here instead of silently making
    `--dry-run` describe a different run than the real one.
    """

    @pytest.mark.parametrize(
        "depth", [None, "binary", "headers", "build", "source"]
    )
    def test_collect_mode_matches(self, tmp_path: Path, depth: str | None) -> None:
        from abicheck.cli_dump_helpers import resolve_dump_collect_context
        from abicheck.cli_buildsource import (
            resolve_dump_request_for_cli,
        )

        header, sources, compile_db = _project(tmp_path)
        cli_mode, cli_headers = resolve_dump_collect_context(
            depth, None, sources, compile_db, (header,)
        )
        resolved = resolve_dump_request_for_cli(
            _request(header=header, sources=sources, build_info=compile_db, depth=depth)
        )
        assert resolved.collect_mode == cli_mode, depth
        assert resolved.headers == tuple(cli_headers), depth

    def test_header_backend_matches_the_resolved_frontend(
        self, tmp_path: Path
    ) -> None:
        """An explicit `--ast-frontend` survives into the resolved request.

        `header_backend` (not `effective_header_backend`) is what the dry-run
        report shows, and it is the value `dump_cmd` resolved from CLI-over-
        config — so it must round-trip unchanged rather than being re-resolved
        to a concrete backend.
        """
        from abicheck.cli_buildsource import (
            resolve_dump_request_for_cli,
        )

        header, sources, compile_db = _project(tmp_path)
        for backend in ("clang", "castxml", "auto"):
            resolved = resolve_dump_request_for_cli(
                _request(
                    header=header,
                    sources=sources,
                    build_info=compile_db,
                    depth=None,
                    frontend=backend,
                )
            )
            assert resolved.header_backend == backend, backend

    def test_binary_depth_clears_headers_on_both_sides(self, tmp_path: Path) -> None:
        """`--depth binary` drops the header AST in the CLI and the pipeline.

        Called out separately because it is the one shape where the two
        resolvers each clear the header list independently
        (`resolve_dump_collect_context` vs. `service_compare_evidence._headers`)
        — the case most likely to drift apart unnoticed.
        """
        from abicheck.cli_buildsource import resolve_dump_request_for_cli
        from abicheck.cli_dump_helpers import resolve_dump_collect_context

        header, sources, compile_db = _project(tmp_path)
        _mode, cli_headers = resolve_dump_collect_context(
            "binary", None, sources, compile_db, (header,)
        )
        resolved = resolve_dump_request_for_cli(
            _request(
                header=header, sources=sources, build_info=compile_db, depth="binary"
            )
        )
        assert tuple(cli_headers) == ()
        assert resolved.headers == ()


def _request(
    *,
    header: Path,
    sources: Path,
    build_info: Path,
    depth: str | None,
    frontend: str = "auto",
) -> Any:
    """A `DumpRequest` shaped the way `dump_cmd` builds one.

    Uses the real builder rather than a hand-written `DumpRequest(...)`, so a
    change to what `dump_cmd` passes is reflected here instead of quietly
    leaving these assertions checking a shape nothing produces.
    """
    from abicheck.cli_dump_request import build_dump_request
    from abicheck.compile_context import CompileContext

    return build_dump_request(
        so_path=None,
        headers=(header,),
        includes=(),
        version="unknown",
        lang="c++",
        lang_explicit=False,
        header_backend=frontend,
        compile_context=CompileContext(frontend=frontend),
        frontend_context="host",
        depth=depth,
        dwarf_only=False,
        debug_format=None,
        pdb_path=None,
        debug_roots=(),
        debuginfod=False,
        debuginfod_url=None,
        dump_manifest=None,
        sources=sources,
        build_info=build_info,
        build_targets=(),
        include_dependencies=False,
        follow_deps=False,
        search_paths=(),
        ld_library_path="",
        include_labels=None,
    )

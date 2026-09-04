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
        """`resolve_dump_collect_context` is itself SO_PATH-blind -- it only
        ever reads ``depth``/``sources``/``build_info``/``headers`` -- so the
        parity check holds regardless of whether the request is source-only.

        ``--depth binary`` is the one exception: it needs a real SO_PATH, not
        because this resolver cares, but because `DumpRequest.validation_errors()`
        now rejects a source-only ``depth="binary"`` request outright (the CLI
        already did, via `dump_cmd`'s own `so_path is None and depth ==
        "binary"` `UsageError`, raised before a request is ever built) --
        Codex review on #814, confirming the source-only `_request()` default
        can't represent that one shape.
        """
        from abicheck.cli_buildsource import (
            resolve_dump_request_for_cli,
        )
        from abicheck.cli_dump_helpers import resolve_dump_collect_context

        header, sources, compile_db = _project(tmp_path)
        cli_mode, cli_headers = resolve_dump_collect_context(
            depth, None, sources, compile_db, (header,)
        )
        so_path = _binary(tmp_path) if depth == "binary" else None
        resolved = resolve_dump_request_for_cli(
            _request(
                header=header,
                sources=sources,
                build_info=compile_db,
                depth=depth,
                so_path=so_path,
            )
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

        Needs a real SO_PATH: `--depth binary` is meaningless for a
        source-only request (no native artifact to report binary evidence
        from), and `DumpRequest.validation_errors()` rejects that combination
        the same way `dump_cmd` itself already refuses it before a request is
        ever built (Codex review on #814).
        """
        from abicheck.cli_buildsource import resolve_dump_request_for_cli
        from abicheck.cli_dump_helpers import resolve_dump_collect_context

        header, sources, compile_db = _project(tmp_path)
        _mode, cli_headers = resolve_dump_collect_context(
            "binary", None, sources, compile_db, (header,)
        )
        resolved = resolve_dump_request_for_cli(
            _request(
                header=header,
                sources=sources,
                build_info=compile_db,
                depth="binary",
                so_path=_binary(tmp_path),
            )
        )
        assert tuple(cli_headers) == ()
        assert resolved.headers == ()


def _binary(tmp_path: Path) -> Path:
    """A dummy SO_PATH -- `--dry-run` never reads its contents."""
    so_path = tmp_path / "libfoo.so"
    so_path.write_bytes(b"")
    return so_path


def _request(
    *,
    header: Path,
    sources: Path,
    build_info: Path,
    depth: str | None,
    frontend: str = "auto",
    so_path: Path | None = None,
) -> Any:
    """A `DumpRequest` shaped the way `dump_cmd` builds one.

    Uses the real builder rather than a hand-written `DumpRequest(...)`, so a
    change to what `dump_cmd` passes is reflected here instead of quietly
    leaving these assertions checking a shape nothing produces. Source-only
    (``so_path=None``) by default, matching every existing caller; pass a
    real path for a shape (``--depth binary``) that needs a native artifact.
    """
    from abicheck.cli_dump_request import build_dump_request
    from abicheck.compile_context import CompileContext

    return build_dump_request(
        so_path=so_path,
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


class TestExecutionConsumesTheResolvedPlan:
    """ADR-061 Phase 3: ``--dry-run`` and the real run read the *same* object.

    The sibling class above pins the resolved values *equal* to ``dump_cmd``'s
    own locals. That was the right guard while the two were separate
    derivations kept in sync by test. They are no longer separate: ``dump_cmd``
    resolves one :class:`ResolvedDumpRequest` above the ``if dry_run:`` branch
    and both branches read their inputs off it.

    These tests state that as an executable fact rather than a comment. A
    future edit that reintroduces a parallel local -- passing ``lang`` where it
    now passes ``_resolved.lang``, say -- fails here, because the spy sees a
    value only the resolved plan could have supplied.

    Why a spy on the execution entry point rather than an end-to-end dump: the
    property under test is *which object supplied the argument*, which is
    invisible in a snapshot (the two agreed, which is exactly why the drift
    would be silent). Reaching into the call is the only place the distinction
    exists.
    """

    def _spy(self, monkeypatch):
        # CLI cleanup phase two, PR C: the real ELF run now executes through
        # `execute_dump_request` (imported fresh, module-attribute lookup, at
        # each `dump_cmd` invocation), not `perform_elf_dump` -- patch the
        # module attribute the same way, so the local import inside
        # `dump_cmd` picks up the fake.
        from abicheck import service_dump_pipeline

        seen: dict[str, object] = {}

        def _fake_execute_dump_request(resolved, **kwargs):
            seen["resolved"] = resolved
            seen.update(kwargs)
            from abicheck.model import AbiSnapshot
            from abicheck.service_dump_pipeline import DumpResult

            side = resolved.request.input
            snap = AbiSnapshot(
                library=side.path.name if side.path is not None else "lib",
                version=resolved.request.input.version,
            )
            return DumpResult(resolved=resolved, snapshot=snap, effective_depth="binary")

        monkeypatch.setattr(
            service_dump_pipeline, "execute_dump_request", _fake_execute_dump_request
        )
        # `perform_elf_dump` used to do extraction AND writing in one call
        # (via its own `write_snapshot_output` DI parameter), so faking it
        # alone intercepted everything downstream. Now the two are separate
        # steps at the `dump_cmd` level -- fake the write step too, so this
        # resolution-only test doesn't also exercise a real embed/write
        # against the fake snapshot above.
        from abicheck import cli_buildsource

        def _fake_write_snapshot_output(*_args, **kwargs):
            seen["write_depth"] = kwargs.get("depth")

        monkeypatch.setattr(
            cli_buildsource, "_write_snapshot_output", _fake_write_snapshot_output
        )
        return seen

    def test_elf_run_reads_its_plan_fields_off_the_resolved_request(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.cli_buildsource import resolve_dump_request_for_cli
        from abicheck.cli_dump_request import build_dump_request

        header, sources, compile_db = _project(tmp_path)
        so_path = _binary(tmp_path)

        captured: dict[str, object] = {}
        real_build = build_dump_request

        def _spy_build(**kwargs):
            request = real_build(**kwargs)
            captured["resolved"] = resolve_dump_request_for_cli(request)
            return request

        monkeypatch.setattr(
            "abicheck.cli_dump_request.build_dump_request", _spy_build
        )
        seen = self._spy(monkeypatch)

        result = CliRunner().invoke(
            main,
            [
                "dump", str(so_path),
                "-H", str(header),
                "--sources", str(sources),
                "--build-info", str(compile_db),
                "--depth", "headers",
                "-o", str(tmp_path / "out.abi.json"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert seen, "execute_dump_request was never reached"

        resolved = captured["resolved"]
        exec_resolved = seen["resolved"]
        # Each of these is a field the resolved plan owns. Reading them off
        # `exec_resolved` (the object `execute_dump_request` itself received)
        # is the whole point: if `dump_cmd` went back to passing its own
        # local, these would still *pass* whenever the two agree -- so the
        # value is only half the assertion. The other half is that
        # `dump_cmd` no longer computes a second copy at all, which
        # `test_no_parallel_public_header_derivation` below checks directly.
        assert exec_resolved.headers == resolved.headers
        assert exec_resolved.lang == resolved.lang
        assert exec_resolved.lang_explicit == resolved.lang_explicit
        assert exec_resolved.header_backend == resolved.header_backend
        assert exec_resolved.collect_mode == resolved.collect_mode
        assert exec_resolved.public_headers == resolved.public_headers
        assert exec_resolved.public_header_dirs == resolved.public_header_dirs
        # `requested_depth` is the one field the execution-only copy
        # deliberately does *not* carry through to `execute_dump_request`
        # (see the real call site's own comment): its own depth gate would
        # otherwise raise a differently-worded error than
        # `DumpDepthNotSatisfiedError` for the identical condition. The
        # dry-run-resolved plan's own `requested_depth` still reaches the
        # real run -- just at the `_write_snapshot_output` call instead,
        # which is the sole enforcement point for this case.
        assert exec_resolved.requested_depth is None
        assert seen["write_depth"] == resolved.requested_depth
        # Codex review (two real regressions the initial ELF migration
        # introduced, both plain dropped kwargs -- `execute_dump_request`
        # already had `seed_collect_mode`/`source_frontend_from_folded_
        # context` parameters for `scan`'s own candidate resolution, but
        # `dump_cmd`'s call site never passed either): `perform_elf_dump`
        # always forwarded its own resolved collect mode to the L2 seed
        # (unconditionally running a zero-config inferred build query for a
        # `--sources` tree with no compile database) and always replayed L4
        # source through the L3 fold's own compiler once it applied. Both
        # must reach `execute_dump_request` unchanged, or the seed silently
        # pins to "off" and L4 replay silently uses the pre-fold compiler.
        assert seen["seed_collect_mode"] == resolved.collect_mode
        assert seen["source_frontend_from_folded_context"] is True
        # Codex review, third real regression: `dump_cmd`'s own
        # `allow_build_query` local is the deprecated, always-False no-op
        # `--allow-build-query` flag -- not the trust signal for this
        # execution step. This invocation passes no `--allow-build-query`,
        # so `allow_build_query` (the CLI local) is False; `seen`'s value
        # must be True regardless, or an explicit `--config`/`--build-query`
        # given on this exact command line would be silently nulled by
        # `_gated_build_query_inputs` for the execution step alone -- see
        # `execute_dump_cli_run`'s own docstring for the full contract.
        assert seen["allow_build_query"] is True

    def test_no_parallel_public_header_derivation(self) -> None:
        """``dump_cmd`` must not re-derive the public-header split itself.

        This is the half a value-equality assertion cannot cover: two
        derivations that agree today are exactly the shape that drifts
        silently later. The public-header split had its own hard-won
        ``--depth binary`` ordering rule (see ``resolve_dump_request``), and
        keeping a second copy of it in the CLI is what this forbids.
        """
        import inspect

        # ADR-061 Phase 4: patch the implementation owner. `abicheck.cli` is a
        # registration facade now; a `setattr` there rebinds nothing the
        # caller reads -- and `raising=False` below would have hidden that.
        from abicheck.frontends.cli.commands import dump as cli_mod

        # `dump_cmd` is a Click RichCommand at module level; the
        # undecorated function is on `.callback`.
        source = inspect.getsource(cli_mod.dump_cmd.callback)
        assert "split_public_header_inputs(" not in source


def _pe_binary(tmp_path: Path) -> Path:
    """A minimal PE binary (MZ header + PE signature + COFF header)."""
    import struct

    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    pe_offset = 64
    struct.pack_into("<I", dos_header, 0x3C, pe_offset)
    pe_sig = b"PE\x00\x00"
    coff_header = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x2000)
    pe_path = tmp_path / "foo.dll"
    pe_path.write_bytes(bytes(dos_header) + pe_sig + coff_header)
    return pe_path


class TestValidationErrorExitsAsUsageError:
    """CodeRabbit/Codex review on PR #980 (ADR-063 Phase 1).

    ``cli_resolve._dump_native_binary`` -- the function
    ``perform_elf_dump``/``handle_non_elf_dump`` both called before this
    migration -- specifically translated a ``ValidationError`` into
    :class:`click.UsageError` (exit 64), never the plain
    :class:`click.ClickException` (exit 1) it used for every other
    extraction failure (see that function's own docstring). The shared
    ``execute_dump_cli_run`` (``frontends.cli.dump_execute``) both formats
    now route through had no such special case -- a real ``ValidationError``
    from ``execute_dump_request`` (unmatched exports, a bad include
    directory, ...) silently exited 1 instead of the documented usage-error
    convention (root ``AGENTS.md``: "64 = usage error ... applies across
    commands"), for ELF and PE/Mach-O alike, since both share this one
    execution entry point. Exercised for both binary formats: whichever one
    a future edit only fixes for is exactly the sibling case a single-format
    regression test would miss.
    """

    def _fake_execute_dump_request_raises(self, monkeypatch) -> None:
        from abicheck import service_dump_pipeline
        from abicheck.errors import ValidationError

        def _raise(*_args: object, **_kwargs: object):
            raise ValidationError("no exported symbols matched the given headers")

        monkeypatch.setattr(service_dump_pipeline, "execute_dump_request", _raise)

    def test_elf_validation_error_exits_64(self, tmp_path: Path, monkeypatch) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        self._fake_execute_dump_request_raises(monkeypatch)
        result = CliRunner().invoke(main, ["dump", str(_binary(tmp_path))])
        assert result.exit_code == 64, result.output

    def test_pe_validation_error_exits_64(self, tmp_path: Path, monkeypatch) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        self._fake_execute_dump_request_raises(monkeypatch)
        result = CliRunner().invoke(main, ["dump", str(_pe_binary(tmp_path))])
        assert result.exit_code == 64, result.output

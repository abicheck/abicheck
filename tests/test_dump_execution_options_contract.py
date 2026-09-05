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

"""ADR-063 Track T4 ("Dump request contract") follow-up: ``DumpExecutionOptions``
attached to ``ResolvedDumpRequest`` itself, so ``dump --dry-run`` can render
what a real run would pass to ``execute_dump_request`` instead of that value
only ever existing at the executor's own call boundary.

Companion to ``tests/test_legacy_compile_db_typed_threading.py`` (which pins
the *typed-pipeline* behavior of threading ``legacy_compile_db_tokens``
through an explicit ``options=`` argument -- unaffected by this slice) and
``tests/test_dump_request_from_cli.py`` (which pins that the real CLI run
reads its execution plan off the resolved request, not a parallel local).
This file is the new field/wiring itself: default behavior is unchanged when
nobody resolves an ``execution_options``, the resolved value is used as
``execute_dump_request``'s own default, and ``render_dump_dry_run`` shows it
when present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _minimal_resolved(tmp_path: Path):
    from abicheck.api_types import DumpRequest, InputSpec
    from abicheck.service_dump_pipeline import resolve_dump_request

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    request = DumpRequest(input=InputSpec(path=so_path))
    return resolve_dump_request(request)


class TestResolvedDumpRequestExecutionOptionsField:
    def test_defaults_to_none(self, tmp_path: Path) -> None:
        resolved = _minimal_resolved(tmp_path)
        assert resolved.execution_options is None

    def test_attachable_via_replace_without_disturbing_other_fields(
        self, tmp_path: Path
    ) -> None:
        import dataclasses

        from abicheck.service_dump_pipeline import DumpExecutionOptions

        resolved = _minimal_resolved(tmp_path)
        opts = DumpExecutionOptions(seed_collect_mode="off")
        updated = dataclasses.replace(resolved, execution_options=opts)
        assert updated.execution_options is opts
        assert updated.lang == resolved.lang
        assert updated.header_backend == resolved.header_backend
        assert updated.evidence == resolved.evidence


class TestExecuteDumpRequestDefaultsToResolvedExecutionOptions:
    """``execute_dump_request``'s own ``options=None`` now falls back to
    ``resolved.execution_options`` before falling back to a bare
    ``DumpExecutionOptions()`` -- the mechanism that lets a caller (the
    ``dump`` CLI) attach its execution plan onto the request once, instead
    of also passing an explicit ``options=`` kwarg."""

    @staticmethod
    def _spy(monkeypatch):
        """Patch ``_resolve_side_snapshot_impl`` and record the
        ``seed_collect_mode``/``allow_build_query`` kwargs
        ``execute_dump_request`` unpacks its resolved ``options`` into --
        that function has no ``options=``-shaped parameter itself, so the
        individual fields are what actually reach it."""
        from abicheck import service_dump_pipeline

        seen: dict[str, object] = {}

        def _fake_impl(*_args, **kwargs):
            seen["seed_collect_mode"] = kwargs.get("seed_collect_mode")
            seen["allow_build_query"] = kwargs.get("allow_build_query")
            from abicheck.model import AbiSnapshot
            from abicheck.workflows.artifact.execute import SideResolution

            return SideResolution(
                snapshot=AbiSnapshot(library="lib", version="1.0"),
                effective_includes=(),
                effective_compile_context=None,
            )

        monkeypatch.setattr(
            service_dump_pipeline, "_resolve_side_snapshot_impl", _fake_impl
        )
        return seen

    def test_explicit_options_still_wins_over_resolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import dataclasses

        from abicheck import service_dump_pipeline
        from abicheck.service_dump_pipeline import DumpExecutionOptions

        resolved = _minimal_resolved(tmp_path)
        resolved = dataclasses.replace(
            resolved,
            execution_options=DumpExecutionOptions(seed_collect_mode="resolved-value"),
        )
        seen = self._spy(monkeypatch)

        service_dump_pipeline.execute_dump_request(
            resolved,
            options=DumpExecutionOptions(seed_collect_mode="explicit-value"),
        )
        assert seen["seed_collect_mode"] == "explicit-value"

    def test_falls_back_to_resolved_execution_options_when_none_passed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import dataclasses

        from abicheck import service_dump_pipeline
        from abicheck.service_dump_pipeline import DumpExecutionOptions

        resolved = _minimal_resolved(tmp_path)
        resolved = dataclasses.replace(
            resolved,
            execution_options=DumpExecutionOptions(seed_collect_mode="resolved-value"),
        )
        seen = self._spy(monkeypatch)

        service_dump_pipeline.execute_dump_request(resolved)
        assert seen["seed_collect_mode"] == "resolved-value"

    def test_bare_default_when_neither_is_set(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Every pre-existing caller (no ``options=``, no resolved
        ``execution_options``) is unaffected -- the same
        ``DumpExecutionOptions()`` bare defaults as before this slice."""
        from abicheck import service_dump_pipeline

        resolved = _minimal_resolved(tmp_path)
        assert resolved.execution_options is None
        seen = self._spy(monkeypatch)

        service_dump_pipeline.execute_dump_request(resolved)
        assert seen["seed_collect_mode"] is None
        assert seen["allow_build_query"] is None


class TestDryRunBuildContextPreview:
    """``frontends.cli.dump_build_context_preview.dry_run_build_context_preview``
    -- the silent/non-raising sibling of ``_resolve_build_context_flags``
    that also returns the derived flags, used only for ``dump --dry-run``'s
    ``execution_options`` preview."""

    def test_none_when_no_compile_db_given(self) -> None:
        from abicheck.frontends.cli.dump_build_context_preview import (
            dry_run_build_context_preview,
        )

        assert dry_run_build_context_preview(None, (), None) is None

    def test_malformed_compile_db_folds_to_empty_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        from abicheck.frontends.cli.dump_build_context_preview import (
            dry_run_build_context_preview,
        )

        bad_db = tmp_path / "compile_commands.json"
        bad_db.write_text("not json{{{", encoding="utf-8")
        assert dry_run_build_context_preview(bad_db, (), None) == ([], False)

    def test_matches_real_resolver_for_a_flagless_but_matched_entry(
        self, tmp_path: Path
    ) -> None:
        """A real compile-DB entry with no ABI-relevant flags still counts
        as ``matched`` -- mirrors ``_resolve_build_context_flags``'s own
        documented "matched, flagless" case, without needing g++/castxml:
        loading and matching a compile database is pure JSON/path
        resolution, no compiler invocation."""
        from abicheck.cli_helpers_compare import _resolve_build_context_flags
        from abicheck.frontends.cli.dump_build_context_preview import (
            dry_run_build_context_preview,
        )

        src = tmp_path / "a.cpp"
        src.write_text("int f() { return 1; }\n", encoding="utf-8")
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "arguments": ["c++", "-c", str(src), "-o", "a.o"],
                        "file": str(src),
                    }
                ]
            ),
            encoding="utf-8",
        )
        preview = dry_run_build_context_preview(compile_db, (), None)
        real_flags, real_matched = _resolve_build_context_flags(compile_db, (), None)
        assert preview == (real_flags, real_matched)
        assert preview == ([], True)


class TestExecutionOptionsDryRunSection:
    """``add_execution_options_dry_run_section`` -- the "Execution options"
    section, appended onto a ``render_dump_dry_run`` result by the caller
    (``frontends.cli.commands.dump.dump_cmd``) rather than built inline by
    ``render_dump_dry_run`` itself, since ``cli_dump_helpers.py`` has no
    line budget left for a new section (``architecture/debt.yaml``)."""

    def test_omitted_when_execution_options_not_resolved(self, tmp_path: Path) -> None:
        from abicheck.cli_dump_helpers import render_dump_dry_run
        from abicheck.frontends.cli.dump_build_context_preview import (
            add_execution_options_dry_run_section,
        )

        resolved = _minimal_resolved(tmp_path)
        assert resolved.execution_options is None
        result = render_dump_dry_run(resolved, output=None)
        add_execution_options_dry_run_section(result, resolved)
        assert "Execution options" not in result.sections

    def test_shown_when_execution_options_resolved(self, tmp_path: Path) -> None:
        import dataclasses

        from abicheck.cli_dump_helpers import render_dump_dry_run
        from abicheck.frontends.cli.dump_build_context_preview import (
            add_execution_options_dry_run_section,
        )
        from abicheck.service_dump_pipeline import DumpExecutionOptions

        resolved = _minimal_resolved(tmp_path)
        resolved = dataclasses.replace(
            resolved,
            execution_options=DumpExecutionOptions(
                build_config=tmp_path / ".abicheck.yml",
                allow_build_query=True,
                legacy_compile_db_tokens=("-DFOO=1",),
                legacy_compile_db_matched=True,
                seed_collect_mode="off",
                source_frontend_from_folded_context=True,
            ),
        )
        result = render_dump_dry_run(resolved, output=None)
        add_execution_options_dry_run_section(result, resolved)
        lines = result.sections["Execution options"]
        rendered = "\n".join(lines)
        assert "allow build query: True" in rendered
        assert "1 derived" in rendered and "matched" in rendered
        assert "seed collect mode: off" in rendered
        assert "source frontend from folded context: True" in rendered
        assert str(tmp_path / ".abicheck.yml") in rendered


@pytest.mark.parametrize("has_compile_db", [False, True])
def test_dump_dry_run_cli_reports_execution_options(
    tmp_path: Path, has_compile_db: bool
) -> None:
    """End-to-end: ``dump SO_PATH --dry-run`` renders an "Execution options"
    section, agreeing with what the real run (mocked here, no toolchain
    needed) would receive -- pure CLI/resolution wiring, no compiler
    invocation on either side of this test."""
    from click.testing import CliRunner

    from abicheck.cli import main

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    header = tmp_path / "api.h"
    header.write_text("struct S { int a; };\n", encoding="utf-8")

    args = [str(so_path), "-H", str(header), "--dry-run"]
    if has_compile_db:
        src = tmp_path / "a.cpp"
        src.write_text("int f() { return 1; }\n", encoding="utf-8")
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "arguments": ["c++", "-c", str(src), "-o", "a.o"],
                        "file": str(src),
                    }
                ]
            ),
            encoding="utf-8",
        )
        args += ["--build-info", str(compile_db)]

    result = CliRunner().invoke(main, ["dump", *args])
    assert result.exit_code == 0, result.output
    assert "Execution options:" in result.output
    assert "allow build query: True" in result.output
    assert "source frontend from folded context: True" in result.output
    if has_compile_db:
        assert "legacy compile-db flags:" in result.output

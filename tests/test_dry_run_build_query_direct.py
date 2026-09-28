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

"""Direct calls into ``add_build_query_dry_run_section`` for paths the CLI
tests in ``test_dry_run_build_query_contract.py`` cannot reach, because
``dump_cmd`` itself intercepts them first (the PE/Mach-O ``--dump-manifest``
rejection) or they need a failing filesystem probe.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest

import abicheck.workflows.extraction as extraction
from abicheck.cli_dump_dry_run_build_query import (
    _SECTION,
    add_build_query_dry_run_section,
)
from abicheck.dry_run import DryRunResult


def _run(**kwargs: object) -> DryRunResult:
    result = DryRunResult(command="dump")
    params: dict[str, object] = {
        "sources": None,
        "headers": (),
        "collect_mode": "auto",
        "build_info": None,
        "build_config": None,
    }
    params.update(kwargs)
    add_build_query_dry_run_section(result, **params)  # type: ignore[arg-type]
    return result


@pytest.mark.parametrize("fmt", ["pe", "macho"])
@pytest.mark.parametrize("normalized_fmt", [True, False])
def test_dump_manifest_rejected_for_non_elf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str, normalized_fmt: bool
) -> None:
    so = tmp_path / "lib.bin"
    so.write_bytes(b"x")
    monkeypatch.setattr(
        extraction,
        "normalize_binary_input",
        lambda p: (p, fmt if normalized_fmt else None),
    )
    monkeypatch.setattr(extraction, "detect_binary_format", lambda p: fmt)
    with pytest.raises(click.UsageError, match=fmt.upper()):
        _run(so_path=so, dump_manifest_given=True)


@pytest.mark.parametrize("fmt", ["elf", None, OSError, ValueError])
def test_dump_manifest_not_rejected_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: object
) -> None:
    so = tmp_path / "lib.so"
    so.write_bytes(b"x")

    def normalize(p: Path) -> tuple[Path, object]:
        if isinstance(fmt, type):
            raise fmt("boom")
        return p, fmt

    monkeypatch.setattr(extraction, "normalize_binary_input", normalize)
    monkeypatch.setattr(extraction, "detect_binary_format", lambda p: None)
    result = _run(so_path=so, dump_manifest_given=True)
    assert result.sections[_SECTION] == [
        "build.query: will NOT run -- neither --sources nor --build-info "
        "was given, so no build-evidence collection is attempted at all"
    ]


def test_dump_manifest_without_artifact_is_not_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(p: Path) -> None:
        raise AssertionError("must not probe without an artifact")

    monkeypatch.setattr(extraction, "normalize_binary_input", fail)
    result = _run(dump_manifest_given=True)
    assert _SECTION in result.sections


def test_compile_db_autodiscovery_failure_reports_no_conventional_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = tmp_path / "src"
    sources.mkdir()
    config = tmp_path / "explicit.yml"
    config.write_text("build:\n  query: make compdb\n", encoding="utf-8")

    def boom(_: Path) -> None:
        raise OSError("unreadable")

    monkeypatch.setattr(extraction, "_autodiscover_compile_db", boom)
    result = _run(sources=sources, build_config=config)
    lines = result.sections[_SECTION]
    assert lines[0] == "build.query: will run (trusted -- explicit --config)"
    assert lines[1] == "argv: ['make', 'compdb']"
    assert lines[2] == f"cwd: {sources}"
    assert lines[3] == (
        "resulting compile-DB path: (build.compile_db not configured, and no "
        "conventional compile DB exists yet -- the query's own default output "
        "location)"
    )
    assert not result.blockers

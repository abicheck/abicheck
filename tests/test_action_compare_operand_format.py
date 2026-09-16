# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Action regression tests for compare's implicit format operand routing."""

from __future__ import annotations

import json
from pathlib import Path

from _package_fixtures import _make_tar_mode
from test_action_run_sh_compare_pr_json_write import _compare_argv
from test_action_validate_inputs import _run_validate

from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict


def test_single_artifact_project_snapshot_allows_terminal(tmp_path: Path) -> None:
    package = tmp_path / "snapshot"
    package.mkdir()
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "versions": {},
                "artifact_ids": ["libfoo.so"],
                "variant_ids": ["default"],
            }
        ),
        encoding="utf-8",
    )
    result = _run_validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(package),
            "INPUT_NEW_LIBRARY": str(package),
            "INPUT_FORMAT": "terminal",
        }
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_archive_rejects_scalar_terminal_format(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    archive.write_bytes(b"archive fixture")
    result = _run_validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(archive),
            "INPUT_NEW_LIBRARY": str(archive),
            "INPUT_FORMAT": "terminal",
        }
    )
    assert result.returncode == 1
    assert "directory/package operand" in result.stdout


def test_implicit_format_is_terminal_for_single_snapshot_directory(
    tmp_path: Path,
) -> None:
    snapshot = AbiSnapshot(library="libfoo.so", version="1")
    package = tmp_path / "snapshot"
    write_legacy_snapshot_package(
        snapshot_to_dict(snapshot),
        package,
        artifact_id=snapshot.library,
        max_known_schema_version=SCHEMA_VERSION,
    )
    argv = _compare_argv(
        tmp_path,
        {"INPUT_FORMAT": ""},
        old=package,
        new=package,
    )
    assert "-o terminal=-" in argv


def test_implicit_format_is_markdown_for_archive(tmp_path: Path) -> None:
    archive = _make_tar_mode(tmp_path / "release.tar.gz", "w:gz")
    argv = _compare_argv(
        tmp_path,
        {"INPUT_FORMAT": ""},
        old=archive,
        new=archive,
    )
    assert "-o markdown=-" in argv

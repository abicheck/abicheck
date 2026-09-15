#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Render a bounded, report-only Actions summary from existing reports.

This is deliberately a data boundary: it stages bounded regular JSON files,
then delegates expected-target parsing, aggregation, and text rendering to the
canonical workflow/report owners.  It never invokes a shell, an analyzer, or
any GitHub API.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from abicheck.report.aggregate import render_aggregate_text
from abicheck.workflows.aggregate import (
    AggregateError,
    ExpectedTargets,
    aggregate_reports_dir,
)
from abicheck.workflows.aggregate.expected_input import (
    ExpectedInputError,
    ExpectedInputKind,
    classify_expected_input,
)

_USAGE = 64
_MAX_FILES = 200
_MAX_FILE_BYTES = 1_048_576
_MAX_SUMMARY_BYTES = 65_536


def _input(name: str) -> str:
    return os.environ.get(f"INPUT_{name}", "")


def _fail(message: str) -> int:
    # Workflow commands are line-delimited.  Treat rejected artifact text as
    # data so it cannot inject a second command through an error annotation.
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error::{escaped}", file=sys.stderr)
    return _USAGE


def _bounded_int(value: str, *, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _regular_file(path: Path, *, label: str, max_bytes: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file (symlinks are not accepted)")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte limit")


def _stage_reports(
    source: Path, destination: Path, *, limit: int, max_bytes: int
) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError("reports-dir must be a directory, not a symlink")
    reports = sorted(source.glob("*.json"))
    if len(reports) > limit:
        raise ValueError(
            f"reports-dir contains {len(reports)} JSON files; limit is {limit}"
        )
    destination.mkdir()
    for report in reports:
        _regular_file(report, label=f"report {report.name!r}", max_bytes=max_bytes)
        shutil.copyfile(report, destination / report.name)


def _expected(
    manifest: str, discovered_only: bool, staged: Path, max_bytes: int
) -> tuple[ExpectedTargets | None, str]:
    if discovered_only:
        if manifest:
            raise ValueError(
                "manifest and discovered-only: true are mutually exclusive"
            )
        return None, "default"
    if not manifest:
        raise ValueError("manifest is required unless discovered-only is true")
    source = Path(manifest)
    _regular_file(source, label="manifest", max_bytes=max_bytes)
    copy = staged / "expected.json"
    shutil.copyfile(source, copy)
    try:
        kind, document = classify_expected_input(copy)
        if kind is ExpectedInputKind.RUN_PLAN:
            from abicheck.buildsource.run_plan import RunPlan, to_aggregate_manifest

            return ExpectedTargets.from_manifest_data(
                to_aggregate_manifest(RunPlan.from_dict(document))
            ), "run-plan"
        return ExpectedTargets.from_manifest_data(document), "manifest"
    except (AggregateError, ExpectedInputError) as exc:
        raise ValueError(f"invalid manifest: {exc}") from exc


def _truncate_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = "\n\n> Summary truncated at the configured byte limit. See the component reports for full detail.\n"
    room = limit - len(marker.encode("utf-8"))
    if room <= 0:
        return marker.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return encoded[:room].decode("utf-8", errors="ignore").rstrip() + marker


def _output_path(value: str) -> Path:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\n" in value
        or "\r" in value
    ):
        raise ValueError(
            "summary-file must be a non-empty workspace-relative path without '..' or newlines"
        )
    current = Path.cwd()
    parent = current
    for part in path.parts[:-1]:
        parent /= part
        if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
            raise ValueError(
                "summary-file parent must not traverse a symlink or regular file"
            )
    if path.exists() and path.is_symlink():
        raise ValueError("summary-file must not be a symlink")
    return path


def main() -> int:
    try:
        discovered_only = _input("DISCOVERED_ONLY") in {"true", "false"}
        if not discovered_only:
            raise ValueError("discovered-only must be 'true' or 'false'")
        discovered = _input("DISCOVERED_ONLY") == "true"
        max_files = _bounded_int(
            _input("MAX_REPORT_FILES"),
            name="max-report-files",
            minimum=1,
            maximum=_MAX_FILES,
        )
        max_bytes = _bounded_int(
            _input("MAX_REPORT_BYTES"),
            name="max-report-bytes",
            minimum=1024,
            maximum=_MAX_FILE_BYTES,
        )
        max_summary = _bounded_int(
            _input("MAX_SUMMARY_BYTES"),
            name="max-summary-bytes",
            minimum=1024,
            maximum=_MAX_SUMMARY_BYTES,
        )
        add_summary = _input("ADD_JOB_SUMMARY")
        if add_summary not in {"true", "false"}:
            raise ValueError("add-job-summary must be 'true' or 'false'")
        output = _output_path(_input("SUMMARY_FILE"))
        reports_dir = Path(_input("REPORTS_DIR"))

        with tempfile.TemporaryDirectory(prefix="abicheck-action-report-") as temp:
            staged = Path(temp)
            staged_reports = staged / "reports"
            _stage_reports(
                reports_dir, staged_reports, limit=max_files, max_bytes=max_bytes
            )
            expected, source_hint = _expected(
                _input("MANIFEST"), discovered, staged, max_bytes
            )
            result = aggregate_reports_dir(
                staged_reports,
                expected=expected,
                discovered_only=discovered,
                policy_source_hint=source_hint,
            )
            summary = _truncate_utf8(render_aggregate_text(result), max_summary)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(summary, encoding="utf-8")
        if add_summary == "true" and os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(summary)
        aggregate = result.to_dict()
        verdict = str(aggregate.get("compatibility", {}).get("verdict") or "")
        print(f"report-path={output.as_posix()}")
        print(f"exit-code={result.exit_code()}")
        print(f"verdict={verdict}")
        return 0
    except (OSError, ValueError, AggregateError) as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())

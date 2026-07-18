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

"""CLI — the ``aggregate`` multi-target fan-in gate.

``aggregate`` folds the per-target ``compare``/``scan`` JSON reports produced
by a CI build matrix into one gate decision. It replaces the hand-written
"post-matrix ABI gate" shell heredoc from the GitHub Action recipes, whose
``for path in glob('*.json')`` loop silently dropped any target whose build
failed before uploading its report — passing green while a required platform
was never analyzed.

Core invariant (see :mod:`abicheck.aggregate`): an expected target with no
report is *unavailable* (unknown), never counted as compatible. Findings (the
worst ABI verdict over analyzed targets) and coverage (did every required
target report?) are two orthogonal conclusions, so a build-infrastructure
failure is never presented as an ABI regression.

Split out of :mod:`abicheck.cli` per the sibling-module pattern; imported for
side-effect at the bottom of :mod:`abicheck.cli` so ``@main.command`` runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .aggregate import (
    DEFAULT_REPORT_PREFIX,
    OnMissingRequired,
    aggregate_reports_dir,
)
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option


def _split_csv(values: tuple[str, ...]) -> list[str]:
    """Flatten repeatable + comma-separated option values into a clean list."""
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


@main.command("aggregate")
@click.argument(
    "reports_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expect",
    "expect",
    multiple=True,
    help="Required target id(s) the CI matrix was supposed to produce "
    "(repeatable, or comma-separated). A required target with no report in "
    "REPORTS_DIR is reported as unavailable and fails the coverage gate — it "
    "is never treated as compatible. Omit to aggregate whatever reports are "
    "present (pure worst-of, no coverage gate).",
)
@click.option(
    "--optional",
    "optional",
    multiple=True,
    help="Optional target id(s): analyzed when present, but a missing one "
    "never fails the coverage gate (repeatable, or comma-separated).",
)
@click.option(
    "--report-prefix",
    "report_prefix",
    default=DEFAULT_REPORT_PREFIX,
    show_default=True,
    help="Filename prefix stripped when deriving a target id from a report "
    "file's stem (e.g. 'abi-report-linux.json' -> 'linux').",
)
@click.option(
    "--on-missing-required",
    type=click.Choice(["fail", "warn"]),
    default="fail",
    show_default=True,
    help="How an unavailable required target affects the exit code: 'fail' "
    "makes incomplete required coverage a gate failure (exit 4); 'warn' still "
    "reports the gap but lets the findings verdict alone decide the exit code.",
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the aggregated result.",
)
@verbose_option
def aggregate_cmd(
    reports_dir: Path,
    expect: tuple[str, ...],
    optional: tuple[str, ...],
    report_prefix: str,
    on_missing_required: str,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Aggregate per-target ABI reports in REPORTS_DIR into one gate verdict.

    REPORTS_DIR holds the per-target ``compare``/``scan`` JSON reports
    downloaded from the build matrix (one ``abi-report-<target>.json`` per
    matrix leg). Exit code follows ``compare``'s scheme over the *analyzed*
    targets (0 compatible / 2 source break / 4 ABI break); under the default
    ``--on-missing-required fail`` an unavailable required target (or nothing
    analyzed at all) also fails at 4.
    """
    _setup_verbosity(verbose)

    result = aggregate_reports_dir(
        reports_dir,
        required=_split_csv(expect),
        optional=_split_csv(optional),
        prefix=report_prefix,
    )

    if fmt == "json":
        text = json.dumps(result.to_dict(), indent=2)
    else:
        text = result.render_text()

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(
        result.exit_code(on_missing_required=OnMissingRequired(on_missing_required))
    )

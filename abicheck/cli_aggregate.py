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

"""CLI — the ``aggregate`` multi-target fan-in gate (workflow composition).

``aggregate`` folds the per-target ``compare``/``scan`` JSON reports produced
by a CI build matrix into one gate decision. It is a *workflow-composition*
command (like a report-level ``compare``): unlike the core-analysis commands
(``dump``/``compare``/``scan``/``deps``/``compat``) it does not analyze a
binary — it reconciles already-produced reports against the set of targets the
matrix was supposed to build.

Three axes stay separate (ADR-042): **compatibility** (worst verdict, for
reporting), **gate** (each report's own ``severity`` decision, combined — never
recomputed from the verdict), and **coverage** (did every required target
report?). A required coverage gap fails at exit ``1`` — a build that never ran
is never handed an ABI-break exit ``4``.

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
    AggregateError,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
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
    type=click.Path(path_type=Path),
)
@click.option(
    "--manifest",
    "manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help='Expected-target manifest (JSON: {"targets": [{"id", "required"}]}). '
    "The single source of truth for which targets the matrix must produce — "
    "generate it in the plan job and feed the same file to both the matrix and "
    "this gate so they never drift.",
)
@click.option(
    "--expect",
    "expect",
    multiple=True,
    help="Required target id(s), as an alternative to --manifest (repeatable / "
    "comma-separated). A required target with no report is unavailable and "
    "fails the coverage gate — never treated as compatible.",
)
@click.option(
    "--optional",
    "optional",
    multiple=True,
    help="Optional target id(s) (used with --expect): analyzed when present, "
    "but a missing one never fails the coverage gate.",
)
@click.option(
    "--discovered-only",
    "discovered_only",
    is_flag=True,
    default=False,
    help="Explicitly aggregate whatever reports are present, with NO coverage "
    "gate. Required to run without a manifest/--expect — because with no "
    "declared target set the gate cannot tell a missing required target from "
    "an intentionally absent one.",
)
@click.option(
    "--report-prefix",
    "report_prefix",
    default=DEFAULT_REPORT_PREFIX,
    show_default=True,
    help="Filename prefix stripped when deriving a target id from a report "
    "file that does not self-identify a 'target_id' "
    "(e.g. 'abi-report-linux.json' -> 'linux').",
)
@click.option(
    "--on-missing-required",
    type=click.Choice(["fail", "warn"]),
    default="fail",
    show_default=True,
    help="How an unavailable required target affects the exit code: 'fail' "
    "makes incomplete required coverage a gate failure (exit 1); 'warn' "
    "reports the gap but lets the per-target gate decisions alone decide.",
)
@click.option(
    "--on-unexpected-target",
    type=click.Choice(["include", "warn", "fail", "ignore"]),
    default="include",
    show_default=True,
    help="How a report for a target not in the expected set is handled: "
    "'include' counts its real findings in the gate (but not in coverage); "
    "'warn' surfaces it without gating; 'fail' fails the gate on any such "
    "target; 'ignore' drops it.",
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the aggregated result.",
)
@verbose_option
def aggregate_cmd(
    reports_dir: Path,
    manifest: Path | None,
    expect: tuple[str, ...],
    optional: tuple[str, ...],
    discovered_only: bool,
    report_prefix: str,
    on_missing_required: str,
    on_unexpected_target: str,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Aggregate per-target ABI reports in REPORTS_DIR into one CI gate verdict.

    REPORTS_DIR holds the per-target ``compare``/``scan`` JSON reports
    downloaded from the build matrix (one ``abi-report-<target>.json`` per
    leg). Provide the expected-target set with ``--manifest`` (recommended) or
    ``--expect``/``--optional``; or opt into ``--discovered-only`` to aggregate
    whatever is present with no coverage gate.

    Exit code: 0 pass / 1 required-coverage gap or a policy-blocked
    addition-or-quality finding / 2 a source-API break / 4 an ABI break / 64
    usage error. Each target's own recorded gate decision is used — the gate is
    never recomputed from the compatibility verdict (ADR-042).
    """
    _setup_verbosity(verbose)

    expected = _resolve_expected(manifest, expect, optional, discovered_only)

    try:
        result = aggregate_reports_dir(
            reports_dir,
            expected=expected,
            discovered_only=discovered_only,
            on_missing_required=OnMissingRequired(on_missing_required),
            on_unexpected_target=OnUnexpectedTarget(on_unexpected_target),
            prefix=report_prefix,
        )
    except AggregateError as exc:
        raise click.UsageError(str(exc)) from exc

    text = (
        json.dumps(result.to_dict(), indent=2)
        if fmt == "json"
        else result.render_text()
    )
    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(result.exit_code())


def _resolve_expected(
    manifest: Path | None,
    expect: tuple[str, ...],
    optional: tuple[str, ...],
    discovered_only: bool,
) -> ExpectedTargets | None:
    """Resolve the expected-target set from exactly one source, or usage error.

    Precedence is deliberately *exclusive*, not merging: ``--discovered-only``,
    ``--manifest``, and ``--expect/--optional`` are three distinct ways to say
    what the target set is, and combining them is ambiguous.
    """
    expect_list = _split_csv(expect)
    optional_list = _split_csv(optional)
    flags_given = bool(expect_list or optional_list)

    if discovered_only:
        if manifest is not None or flags_given:
            raise click.UsageError(
                "--discovered-only cannot be combined with --manifest/--expect/"
                "--optional"
            )
        return None
    if manifest is not None:
        if flags_given:
            raise click.UsageError(
                "--manifest cannot be combined with --expect/--optional"
            )
        try:
            return ExpectedTargets.from_manifest_file(manifest)
        except AggregateError as exc:
            raise click.UsageError(str(exc)) from exc
    if flags_given:
        # from_lists only raises on an empty set, which flags_given rules out.
        return ExpectedTargets.from_lists(expect_list, optional_list)
    raise click.UsageError(
        "no expected-target set: pass --manifest or --expect (the targets the "
        "matrix must produce), or --discovered-only to aggregate whatever is "
        "present with no coverage gate"
    )

# Copyright 2026 Nikolay Petrov
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

"""``pr-comment`` — GitHub Action-only PR-comment renderer (ADR-043 D1).

Renders a sticky GitHub PR-comment body from a JSON report produced by
``compare`` (including its ``--used-by``/``--required-symbol(s)`` scoped
output). This is deliberately NOT a public ``abicheck`` subcommand -- it is
Action/library-only tooling, invoked as ``python -m abicheck.cli_pr_comment``
(see ``action/run.sh``), never as ``abicheck pr-comment``. Kept as a Click
command purely for its argument parsing/``--help``; it is never attached to
the public ``main`` group.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .frontends.cli.runtime import _write_or_echo


@click.command("pr-comment")
@click.argument("report", type=click.Path(exists=True, path_type=Path))
@click.option("--sha", default="", help="Commit SHA being scanned (PR head).")
@click.option(
    "--subject",
    default=None,
    help="Artifact/library name shown in the comment header. ``scan``'s own "
    "JSON report carries no such field (unlike ``compare``'s ``library``) -- "
    "pass the scanned artifact's basename here (see action/run.sh). Ignored "
    "for a compare/appcompat/release report, which already names its "
    "subject from the report itself.",
)
@click.option(
    "--detail",
    type=click.Choice(["summary", "standard", "full"]),
    default="standard",
    show_default=True,
    help="How much per-change detail to include in the comment.",
)
@click.option(
    "--on",
    "post_on",
    type=click.Choice(["always", "changes", "never"]),
    default="changes",
    show_default=True,
    help="When to emit a comment body: always, only on changes, or never.",
)
@click.option(
    "--run-label",
    default=None,
    help="Run label shown in the footer, e.g. 'run #128'.",
)
@click.option(
    "--report-url",
    default=None,
    help="URL of the full report/run, linked in the footer and used when the "
    "comment is condensed or truncated to fit GitHub's size limit.",
)
@click.option(
    "--gate-api-break",
    is_flag=True,
    default=False,
    help="Treat API/source breaks as breaking (mirror fail-on-api-break, which "
    "turns the check red on them).",
)
@click.option(
    "--gate-breaking/--no-gate-breaking",
    default=True,
    show_default=True,
    help="Whether an ABI break actually turns the check red (mirror "
    "fail-on-breaking). Only affects the analysis-incomplete bucket's "
    "blocking headline today — the ordinary Breaking bucket is a "
    "compatibility judgement, not a gate one, and is unaffected.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the comment markdown (default: stdout).",
)
def pr_comment_cmd(
    report: Path,
    sha: str,
    subject: str | None,
    detail: str,
    post_on: str,
    run_label: str | None,
    report_url: str | None,
    gate_api_break: bool,
    gate_breaking: bool,
    output: Path | None,
) -> None:
    """Render a sticky PR-comment body from a JSON REPORT.

    REPORT is a JSON file from 'abicheck compare -o json=...' (directory/
    package fan-out and --used-by/--required-symbol(s) scoped reports all
    produce a compatible shape) or 'abicheck compare --no-baseline ...
    -o json=...' (recognised by its own 'audit_report_schema_version' key
    -- the Action's own audit-only mode: scan translation, ADR-068,
    produces this shape). A *stored* report from the retired `scan` command
    (recognised by its own 'scan_schema_version' key) is no longer a
    supported input -- `scan` was deleted outright (ADR-068 Phase 6, no
    deprecation window) along with this tool's own scan-shaped adapter, and
    feeding one in now is a clear, loud error rather than a silently
    misrendered "no changes" comment. When --on=never, or --on=changes and
    the report has no changes, nothing is written (an empty --output file
    is produced) so the caller can skip posting. Action/library-only:
    invoke as `python -m abicheck.cli_pr_comment`, not `abicheck
    pr-comment` (this is not a public abicheck subcommand).

    \b
    Example:
      abicheck compare old.json new.so -H include/ -o json=report.json
      python -m abicheck.cli_pr_comment report.json --sha "$GITHUB_SHA" -o comment.md
    """
    from .pr_comment import (
        UnsupportedReportShapeError,
        build_model,
        render_comment,
        should_post,
    )

    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise click.ClickException(f"Cannot read JSON report: {e}") from e

    if not isinstance(data, dict):
        raise click.ClickException("JSON report must be an object")

    if subject and "subject" not in data:
        # No surviving report shape reads this key today -- it was only
        # `scan`'s own report shape (see `pr_comment.build_model`'s
        # UnsupportedReportShapeError for a stored scan report, and
        # `docs/contribute/known-gaps.md`'s closed gate_contribution entry
        # for the wider context); a compare/appcompat/release/no-baseline
        # report already names its own subject from the report itself.
        # Kept as a harmless no-op assignment rather than removed outright,
        # since --subject itself is unaffected front-end surface this PR's
        # scope does not touch.
        data["subject"] = subject

    try:
        model = build_model(
            data, gate_api_break=gate_api_break, gate_breaking=gate_breaking
        )
    except UnsupportedReportShapeError as e:
        raise click.ClickException(str(e)) from e
    if not should_post(model, post_on):
        # Nothing to post — leave an empty file so a `-s` check skips posting.
        if output is not None:
            Path(output).write_text("", encoding="utf-8")
        return

    body = render_comment(
        model, sha=sha, detail=detail, run_label=run_label, report_url=report_url
    )
    _write_or_echo(output, body)


if (
    __name__ == "__main__"
):  # pragma: no cover - exercised via subprocess in Action tests
    pr_comment_cmd()

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

"""CLI — ``pr-comment`` command.

Renders a sticky GitHub PR-comment body from a JSON report produced by
``compare`` / ``compare-release`` / ``appcompat``. Split out of
:mod:`abicheck.cli` and imported for side-effect at the bottom of that module
so the ``@main.command("pr-comment")`` decorator runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .cli import _write_or_echo, main


@main.command("pr-comment")
@click.argument("report", type=click.Path(exists=True, path_type=Path))
@click.option("--sha", default="", help="Commit SHA being scanned (PR head).")
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
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the comment markdown (default: stdout).",
)
def pr_comment_cmd(
    report: Path,
    sha: str,
    detail: str,
    post_on: str,
    run_label: str | None,
    output: Path | None,
) -> None:
    """Render a sticky PR-comment body from a JSON REPORT.

    REPORT is a JSON file from 'abicheck compare|compare-release|appcompat
    --format json'. When --on=never, or --on=changes and the report has no
    changes, nothing is written (an empty --output file is produced) so the
    caller can skip posting.

    \b
    Example:
      abicheck compare old.json new.so -H include/ --format json -o report.json
      abicheck pr-comment report.json --sha "$GITHUB_SHA" -o comment.md
    """
    from .pr_comment import build_model, render_comment, should_post

    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise click.ClickException(f"Cannot read JSON report: {e}") from e

    if not isinstance(data, dict):
        raise click.ClickException("JSON report must be an object")

    model = build_model(data)
    if not should_post(model, post_on):
        # Nothing to post — leave an empty file so a `-s` check skips posting.
        if output is not None:
            Path(output).write_text("", encoding="utf-8")
        return

    body = render_comment(model, sha=sha, detail=detail, run_label=run_label)
    _write_or_echo(output, body)

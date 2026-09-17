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

"""Action-only entry point for ``actions/report`` and ``actions/verify-source-run``.

Invoked as ``python -m abicheck.frontends.action.cli <command>``, never as an
``abicheck`` subcommand: like :mod:`abicheck.cli_pr_comment` (ADR-043 D1),
this is Action/library tooling and is deliberately not attached to the public
``main`` group. It is a Click command group purely for its argument parsing
and ``--help``.

Each command reads files and writes files. None of them performs network
I/O: the Actions' shells make the GitHub API calls and hand the responses in,
which is what lets every decision here be tested with no credentials at all
(ADR-073). None of them analyses anything either -- no snapshot, no
comparison, no compiler, no build query.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .cli_base import EXIT_REFUSED, _read_json, _write_json, action_cli
from .report_publication import (
    DEFAULT_MAX_COMMENT_BYTES,
    DEFAULT_MAX_SUMMARY_BYTES,
    POST_MODES,
    PublicationError,
    PublicationIdentity,
    decide,
    read_comments,
    render_report,
    request_payload,
    summary_for_plan,
)
from .run_selection import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_ENTRY_BYTES,
    DEFAULT_MAX_RATIO,
    DEFAULT_MAX_TOTAL_BYTES,
    ExtractionLimits,
    RunExpectation,
    SourceRun,
    SourceRunRejected,
    extract_artifact,
    resolve_pull_request,
    select_artifact,
    verify_source_run,
    verify_tested_sha,
)
from .tag_resolution import (
    TagResolutionError,
    expected_capture_revision,
    resolve_tag_commit,
    tag_object_to_fetch,
)


@action_cli.command("comment")
@click.argument("report", type=click.Path(exists=True, path_type=Path))
@click.option("--identity", required=True, help="Sticky comment identity.")
@click.option("--run-id", default="", help="Producer run id, for the ordering guard.")
@click.option("--run-attempt", default=1, type=int, help="Producer run attempt (>= 1).")
@click.option("--sha", default="", help="The analysed head/build SHA to display.")
@click.option(
    "--detail",
    type=click.Choice(["summary", "standard", "full"]),
    default="standard",
    show_default=True,
)
@click.option(
    "--on",
    "post_on",
    type=click.Choice(list(POST_MODES)),
    default="changes",
    show_default=True,
)
@click.option("--run-label", default=None)
@click.option("--report-url", default=None)
@click.option("--report-artifact-url", default=None)
@click.option("--path-prefix", default="")
@click.option("--gate-api-break", is_flag=True, default=False)
@click.option("--gate-breaking/--no-gate-breaking", default=True)
@click.option(
    "--max-comment-bytes",
    type=int,
    default=DEFAULT_MAX_COMMENT_BYTES,
    show_default=True,
)
@click.option(
    "--max-summary-bytes",
    type=int,
    default=DEFAULT_MAX_SUMMARY_BYTES,
    show_default=True,
)
@click.option(
    "--existing-comments",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Newline-delimited {id, body} records for the PR's current comments. "
    "Omit for a dry run, which decides as though the PR had none.",
)
@click.option("--body-out", type=click.Path(path_type=Path), required=True)
@click.option("--request-out", type=click.Path(path_type=Path), default=None)
@click.option("--summary-out", type=click.Path(path_type=Path), default=None)
@click.option("--plan-out", type=click.Path(path_type=Path), required=True)
def comment_cmd(
    report: Path,
    identity: str,
    run_id: str,
    run_attempt: int,
    sha: str,
    detail: str,
    post_on: str,
    run_label: str | None,
    report_url: str | None,
    report_artifact_url: str | None,
    path_prefix: str,
    gate_api_break: bool,
    gate_breaking: bool,
    max_comment_bytes: int,
    max_summary_bytes: int,
    existing_comments: Path | None,
    body_out: Path,
    request_out: Path | None,
    summary_out: Path | None,
    plan_out: Path,
) -> None:
    """Render REPORT and decide what to do with the resulting comment.

    Writes the rendered body, the API request document, the job-summary
    text, and a plan (``create``/``update``/``clear``/``skip``). Performs no
    network I/O whatsoever: the caller posts what this wrote.
    """
    data = _read_json(report)
    if not isinstance(data, dict):
        raise click.ClickException("The report must be a JSON object")
    stamp = PublicationIdentity(
        identity=identity,
        run_id=run_id,
        run_attempt=max(run_attempt, 1),
        head_sha=sha,
    )
    try:
        rendered = render_report(
            data,
            identity=stamp,
            report_dir=report.resolve().parent,
            detail=detail,
            on=post_on,
            sha=sha,
            run_label=run_label,
            report_url=report_url,
            report_artifact_url=report_artifact_url,
            path_prefix=path_prefix,
            gate_api_break=gate_api_break,
            gate_breaking=gate_breaking,
            max_comment_bytes=max_comment_bytes,
        )
    except PublicationError as exc:
        raise click.ClickException(str(exc)) from exc
    comments = (
        read_comments(existing_comments.read_text(encoding="utf-8"))
        if existing_comments is not None
        else []
    )
    plan = decide(rendered, identity=stamp, comments=comments, sha=sha)
    body = plan.body or rendered.body
    body_out.parent.mkdir(parents=True, exist_ok=True)
    body_out.write_text(body, encoding="utf-8")
    if request_out is not None and plan.posts:
        request_out.parent.mkdir(parents=True, exist_ok=True)
        request_out.write_text(request_payload(body), encoding="utf-8")
    summary_truncated = False
    # Only when something is actually published. A run that skipped as
    # `stale` rendered a body it deliberately did not post; writing that
    # body to the job summary would publish the superseded result on the
    # one channel the ordering guard does not cover.
    if summary_out is not None and plan.posts:
        summary, summary_truncated = summary_for_plan(
            rendered,
            plan,
            max_summary_bytes=max_summary_bytes,
            report_url=report_url,
        )
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(summary, encoding="utf-8")
    _write_json(
        plan_out,
        {
            **plan.to_dict(),
            "body_bytes": len(body.encode("utf-8")),
            "comment_truncated": rendered.truncated,
            "summary_truncated": summary_truncated,
        },
    )
    click.echo(f"abicheck report: plan={plan.action} bytes={len(body.encode())}")


# ---------------------------------------------------------------------------
# `verify-run` — is this the run we think it is, and whose PR is it?
# ---------------------------------------------------------------------------


@action_cli.command("verify-run")
@click.option("--run-json", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--associated-pulls-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="GET /repos/{repo}/commits/{sha}/pulls for the run's head SHA.",
)
@click.option(
    "--tested-commit-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="GET /repos/{repo}/commits/{tested-sha}, needed when the analysed "
    "commit is a merge commit rather than the PR head.",
)
@click.option(
    "--artifacts-json", type=click.Path(exists=True, path_type=Path), default=None
)
@click.option("--expect-repository", default="")
@click.option("--expect-workflow", default="")
@click.option("--expect-event", default="")
@click.option("--expect-run-id", default="")
@click.option("--expect-run-attempt", type=int, default=None)
@click.option(
    "--allow-conclusion",
    multiple=True,
    default=("success",),
    show_default=True,
    help="Repeatable. Pass an empty value to allow any conclusion.",
)
@click.option("--artifact-name", default="")
@click.option(
    "--claimed-pr-number",
    type=int,
    default=None,
    help="A PR number the artifact states. Cross-checked against the API's "
    "answer; a disagreement is a refusal, never a preference.",
)
@click.option("--tested-sha", default="")
@click.option("--out", type=click.Path(path_type=Path), required=True)
def verify_run_cmd(
    run_json: Path,
    associated_pulls_json: Path | None,
    tested_commit_json: Path | None,
    artifacts_json: Path | None,
    expect_repository: str,
    expect_workflow: str,
    expect_event: str,
    expect_run_id: str,
    expect_run_attempt: int | None,
    allow_conclusion: tuple[str, ...],
    artifact_name: str,
    claimed_pr_number: int | None,
    tested_sha: str,
    out: Path,
) -> None:
    """Verify a producer run and resolve the pull request it belongs to."""
    run_data = _read_json(run_json)
    conclusions = tuple(c for c in allow_conclusion if c)
    result: dict[str, object] = {}
    try:
        run = SourceRun.from_api(run_data)  # type: ignore[arg-type]
        verify_source_run(
            run,
            RunExpectation(
                repository=expect_repository,
                workflow=expect_workflow,
                event=expect_event,
                run_id=expect_run_id,
                run_attempt=expect_run_attempt,
                allowed_conclusions=conclusions,
            ),
        )
        result.update(
            {
                "run_id": run.run_id,
                "run_attempt": run.run_attempt,
                "head_sha": run.head_sha,
                "head_repository": run.head_repository,
                "event": run.event,
            }
        )
        if associated_pulls_json is not None:
            associated = _read_json(associated_pulls_json)
            if not isinstance(associated, list):
                raise SourceRunRejected(
                    "no-pull-request",
                    "the commit-to-pull-request association is not a list",
                )
            pull = resolve_pull_request(
                run,
                associated,
                repository=expect_repository or run.repository,
                claimed_number=claimed_pr_number,
            )
            verified_sha = verify_tested_sha(
                run,
                pull,
                tested_sha=tested_sha or run.head_sha,
                tested_commit=(
                    _read_json(tested_commit_json)  # type: ignore[arg-type]
                    if tested_commit_json is not None
                    else None
                ),
            )
            result.update(
                {
                    "pr_number": pull.number,
                    "pr_head_sha": pull.head_sha,
                    "tested_sha": verified_sha,
                    "from_fork": pull.from_fork,
                }
            )
        if artifacts_json is not None and artifact_name:
            listing = _read_json(artifacts_json)
            entries = listing.get("artifacts") if isinstance(listing, dict) else listing
            artifact = select_artifact(
                entries if isinstance(entries, list) else [],
                artifact_name,
                run_id=run.run_id,
            )
            result["artifact_id"] = artifact.get("id")
    except SourceRunRejected as exc:
        _write_json(out, {"verified": False, "code": exc.code, "reason": exc.message})
        click.echo(f"abicheck: refused source run — {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc
    result["verified"] = True
    _write_json(out, result)
    click.echo("abicheck: source run verified")


# ---------------------------------------------------------------------------
# `extract-artifact` — treat the archive as hostile
# ---------------------------------------------------------------------------


@action_cli.command("extract-artifact")
@click.argument("archive", type=click.Path(exists=True, path_type=Path))
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
@click.option("--max-entry-bytes", type=int, default=DEFAULT_MAX_ENTRY_BYTES)
@click.option("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
@click.option("--max-ratio", type=float, default=DEFAULT_MAX_RATIO)
def extract_artifact_cmd(
    archive: Path,
    destination: Path,
    max_total_bytes: int,
    max_entry_bytes: int,
    max_entries: int,
    max_ratio: float,
) -> None:
    """Unpack ARCHIVE into DESTINATION, refusing anything hostile."""
    try:
        written = extract_artifact(
            archive,
            destination,
            ExtractionLimits(
                max_total_bytes=max_total_bytes,
                max_entry_bytes=max_entry_bytes,
                max_entries=max_entries,
                max_ratio=max_ratio,
            ),
        )
    except SourceRunRejected as exc:
        click.echo(f"abicheck: refused artifact — {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc
    click.echo(f"abicheck: extracted {len(written)} file(s)")


# ---------------------------------------------------------------------------


#: What a field worth no value emits. The shells read these records with
#: ``read -r``, which cannot distinguish "empty" from "absent", so both
#: deliberately arrive as one empty line rather than as a missing record --
#: a dropped record would shift every field after it onto the wrong variable.
_ABSENT = ""


def _shell_field(value: object) -> str:
    """Render one JSON value as the single line a shell will read back.

    ``None`` and a missing key are the same answer here (see ``_ABSENT``),
    and a real boolean becomes the ``true``/``false`` a shell test compares
    against rather than Python's capitalised ``repr``.
    """
    if value is None:
        return _ABSENT
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@action_cli.command("emit-fields")
@click.argument("document", type=click.Path(exists=False, path_type=Path))
@click.argument("fields", nargs=-1, required=True)
@click.option(
    "--tolerant",
    is_flag=True,
    help="Emit empty values instead of failing when DOCUMENT cannot be read.",
)
def emit_fields_cmd(document: Path, fields: tuple[str, ...], tolerant: bool) -> None:
    """Print DOCUMENT's FIELDS as newline-delimited records, one per field.

    The one supported way for either Action's shell to read a value out of a
    JSON document this package wrote. It exists as a command rather than as
    an inline ``python -`` heredoc in each script because the record
    separator is a contract between two languages, and an inline copy is a
    place for that contract to be restated wrongly -- which is exactly what
    happened: Python's text-mode ``print`` emits ``\r\n`` on Windows, while
    ``read -r`` strips only the ``\n``. Every value a shell read that way
    carried a trailing carriage return, so ``[[ "$PLAN_ACTION" == "skip" ]]``
    was false for a plan that said ``skip``, and an id destined for an API
    path (``artifact_id``, ``pr_number``) carried a stray byte into the URL.

    So the newline is written explicitly and never translated, and a value
    holding one is refused rather than emitted: the framing is positional,
    so a value carrying ``\n`` or ``\r`` would forge an extra record and
    every later field would land on the wrong shell variable.
    """
    try:
        raw = json.loads(document.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if not tolerant:
            raise
        raw = {}
    data = raw if isinstance(raw, dict) else {}
    rendered = []
    for field in fields:
        value = _shell_field(data.get(field))
        if "\n" in value or "\r" in value:
            raise click.ClickException(
                f"{document}: field {field!r} contains a line break and cannot be "
                "passed to the shell as one record."
            )
        rendered.append(value)
    payload = "".join(f"{line}\n" for line in rendered).encode("utf-8")
    # Write through the *binary* layer. `print`, `click.echo` and even
    # `sys.stdout.write` all go through a `TextIOWrapper` that rewrites
    # "\n" to `os.linesep` -- which is the whole bug, so a text write is
    # not a fix for it, it is the same defect spelled differently.
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
        return
    # No binary layer at all (a capturing stand-in, not the real process).
    # Turn the translation off rather than writing through it.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(newline="")
    sys.stdout.write(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# `tag-peel` / `resolve-tag` — which commit does a publication tag name?
# ---------------------------------------------------------------------------


@action_cli.command("tag-peel")
@click.argument("tag")
@click.argument("ref_json", type=click.Path(exists=True, path_type=Path))
def tag_peel_cmd(tag: str, ref_json: Path) -> None:
    """Print the annotated-tag object to peel for TAG, or nothing.

    Two commands rather than one because the caller has to make a *second*
    API request in between, and only for an annotated tag. Deciding whether
    there is one is still a rule about what GitHub's document means, so it
    stays here instead of becoming a `jq` expression in a workflow (ADR-073:
    the shells call the API, this decides what the answers mean).
    """
    try:
        click.echo(tag_object_to_fetch(tag, _read_json(ref_json)))
    except TagResolutionError as exc:
        click.echo(f"abicheck: {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc


@action_cli.command("resolve-tag")
@click.argument("tag")
@click.argument("ref_json", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--tag-object-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="GET /repos/{repo}/git/tags/{sha}, required when TAG is annotated.",
)
@click.option(
    "--expected-project-ref",
    default="",
    help=(
        "What a pre-captured set's project_ref must equal: 'commit' (default), "
        "'tag' for the legacy tag-valued behavior, or a full commit SHA."
    ),
)
@click.option("--out", type=click.Path(path_type=Path), default=None)
def resolve_tag_cmd(
    tag: str,
    ref_json: Path,
    tag_object_json: Path | None,
    expected_project_ref: str,
    out: Path | None,
) -> None:
    """Resolve TAG to its commit and to the revision a capture must record.

    The two answers are emitted side by side precisely because they are not
    the same value: ``commit_sha`` is what the tag names, and
    ``expected_project_ref`` is what the caller declared a published
    baseline-set's own manifest has to say. Conflating them is the defect
    :mod:`abicheck.frontends.action.tag_resolution` exists to close.
    """
    try:
        resolved = resolve_tag_commit(
            tag,
            _read_json(ref_json),
            tag_object=(
                _read_json(tag_object_json) if tag_object_json is not None else None
            ),
        )
        expected = expected_capture_revision(
            expected_project_ref, tag=tag, resolved=resolved
        )
    except TagResolutionError as exc:
        if out is not None:
            _write_json(
                out, {"resolved": False, "code": exc.code, "reason": exc.message}
            )
        click.echo(f"abicheck: {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc
    document = {
        "resolved": True,
        "tag": resolved.tag,
        "ref": resolved.ref,
        "commit_sha": resolved.commit_sha,
        "annotated": resolved.annotated,
        "expected_project_ref": expected,
    }
    if out is not None:
        _write_json(out, document)
    click.echo(json.dumps(document, sort_keys=True))


@action_cli.command("flatten-pages")
@click.argument("raw", type=click.Path(exists=True, path_type=Path))
@click.argument("out", type=click.Path(path_type=Path))
def flatten_pages_cmd(raw: Path, out: Path) -> None:
    """Flatten ``gh api --paginate``'s concatenated arrays in RAW into OUT.

    ``--paginate`` emits one JSON array per page back to back, which is not
    a JSON document, so this walks them with a raw decoder and concatenates
    the members. Lives here rather than inline in the shell for the same
    reason ``emit-fields`` does: the Actions' shells marshal arguments and
    call the API, and every decision they rest on is importable Python that
    a test can reach without credentials (ADR-073).

    A page that is not an array is appended as a single member rather than
    dropped -- the caller decides what an unexpected shape means, and
    silently discarding it here would look to them like an empty page.
    """
    text = raw.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    pages: list[object] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        if isinstance(value, list):
            pages.extend(value)
        else:
            pages.append(value)
    out.write_text(json.dumps(pages), encoding="utf-8")


# Sibling command module, imported for its registration side effect -- it
# decorates `action_cli` at import time. Kept at the foot of the file, after
# the group and the shared helpers it uses are defined, the same way
# `abicheck/cli.py` registers its own `cli_*` siblings.
from . import cli_integration as _cli_integration  # noqa: E402,F401

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    # Safe to call the group directly: it is `cli_base`'s single object, so
    # this file's own decorations (running as `__main__`) and the sibling's
    # (running as the canonical module) both land on it. Defining the group
    # here instead would give the two module objects two different groups,
    # and every sibling command would fail with Click's "No such command".
    action_cli()

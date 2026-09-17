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

"""Which revision was analysed, and which commit does a publication tag name?

A sibling of :mod:`abicheck.frontends.action.cli`, registered on the same
group for the same reason :mod:`~abicheck.frontends.action.cli_integration`
is: that module is the publication boundary's entry point and is already
near this package's per-file ceiling, and these commands are a separate
responsibility -- establishing *identity* (which commit, which tag) rather
than deciding a publication.

They are grouped together because they answer one question from two
directions.

``record-analysis-context`` / ``read-analysis-context`` carry the one fact
the GitHub API cannot supply -- the ephemeral merge commit a
``pull_request`` producer actually built -- from the producer into the
trusted publisher, inside the artifact that already travels between them.
``verify-tested-sha`` then establishes that claim against the API over the
*first* pass's own verified result, so the whole flow costs one artifact
download rather than two.

``tag-peel`` / ``resolve-tag`` answer the publishing half: which commit a
release tag names, which is not the same string as the tag.

Like its siblings, every command here reads files and writes files. None
performs network I/O: the Actions' shells make the GitHub API calls and hand
the responses in, which is what lets each decision be tested with no
credentials at all (ADR-073).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from ...model.analysis_context import (
    ANALYSIS_CONTEXT_KEY,
    AnalysisContext,
    AnalysisContextError,
)
from .cli_base import EXIT_REFUSED, _read_json, _write_json, action_cli
from .run_selection import (
    DEFAULT_MAX_ENTRY_BYTES,
    ResolvedPullRequest,
    SourceRun,
    SourceRunRejected,
    load_json_document,
    verify_tested_sha,
)
from .tag_resolution import (
    TagResolutionError,
    expected_capture_revision,
    resolve_tag_commit,
    tag_object_to_fetch,
)

# ---------------------------------------------------------------------------
# `record-analysis-context` / `read-analysis-context` — which commit ran?
# ---------------------------------------------------------------------------

#: Every field of the recorded context is read from a named environment
#: variable rather than from an argv position, for the same reason the check
#: declaration goes through a file (actions/aggregate/run.sh): these are
#: caller-supplied values that may contain any character, and an argv round
#: trip through a composite step's shell is exactly where a quote in a branch
#: name stops being data.
_CONTEXT_ENV = {
    "tested_sha": "ABICHECK_CTX_TESTED_SHA",
    "pr_head_sha": "ABICHECK_CTX_PR_HEAD_SHA",
    "pr_base_sha": "ABICHECK_CTX_PR_BASE_SHA",
    "pr_base_ref": "ABICHECK_CTX_PR_BASE_REF",
    "pr_number": "ABICHECK_CTX_PR_NUMBER",
    "producer_repository": "ABICHECK_CTX_REPOSITORY",
    "producer_run_id": "ABICHECK_CTX_RUN_ID",
    "producer_run_attempt": "ABICHECK_CTX_RUN_ATTEMPT",
    "producer_workflow_ref": "ABICHECK_CTX_WORKFLOW_REF",
    "producer_event": "ABICHECK_CTX_EVENT",
    "orchestration_ref": "ABICHECK_CTX_ORCHESTRATION_REF",
    "profile": "ABICHECK_CTX_PROFILE",
}


@action_cli.command("record-analysis-context")
@click.argument("out", type=click.Path(path_type=Path))
def record_analysis_context_cmd(out: Path) -> None:
    """Write the producer's analysis context from ``ABICHECK_CTX_*``.

    The producer half of ADR-073's provenance flow. It exists so an
    integrating workflow stops hand-writing this JSON in a ``run:`` block --
    which is how a project ends up with a second, divergent sidecar format
    and a privileged shell parsing it. Shape validation happens here, at the
    moment of recording, so a malformed value fails the producer's own job
    rather than the trusted publisher's.
    """
    values = {
        name: os.environ.get(variable, "").strip()
        for name, variable in _CONTEXT_ENV.items()
    }
    try:
        context = AnalysisContext.from_mapping(values)
    except AnalysisContextError as exc:
        click.echo(f"abicheck: refused analysis context — {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc
    _write_json(out, context.to_dict())
    click.echo(
        "abicheck: recorded analysis context"
        + (f" for {context.tested_sha}" if context.records_tested_sha else "")
    )


@action_cli.command("read-analysis-context")
@click.argument("report", type=click.Path(path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), required=True)
@click.option(
    "--max-bytes",
    type=int,
    default=DEFAULT_MAX_ENTRY_BYTES,
    help="Cap on the document read, since it came out of a hostile archive.",
)
@click.option(
    "--require/--no-require",
    default=True,
    help=(
        "Refuse when the report records no analysis context. Off means the "
        "caller has an explicit policy for an unestablished identity."
    ),
)
def read_analysis_context_cmd(
    report: Path, out: Path, max_bytes: int, require: bool
) -> None:
    """Read REPORT's ``analysis_context`` block as bounded, validated data.

    The consumer half. REPORT is an aggregate document that came out of an
    artifact an untrusted job produced, so it is read through the same
    bounded reader every other artifact member goes through and every field
    is shape-checked before it can reach a step output -- a bare
    ``cat``-into-``$GITHUB_OUTPUT`` in a privileged job lets a newline in
    that file forge any other output of that job.

    Nothing here establishes that the recorded commit is genuine; it produces
    a claim for ``verify-tested-sha`` to check against the API.
    """
    document: dict[str, object] = {"present": False, "records_tested_sha": False}
    try:
        if not report.is_file():
            raise AnalysisContextError(
                "analysis-context-absent",
                f"{report} does not exist, so it records no analysis context",
            )
        raw = load_json_document(report, max_bytes=max_bytes)
        if not isinstance(raw, dict) or ANALYSIS_CONTEXT_KEY not in raw:
            raise AnalysisContextError(
                "analysis-context-absent",
                f"{report} carries no {ANALYSIS_CONTEXT_KEY!r} block",
            )
        context = AnalysisContext.from_mapping(raw[ANALYSIS_CONTEXT_KEY])
    except (AnalysisContextError, SourceRunRejected) as exc:
        code = getattr(exc, "code", "analysis-context-unreadable")
        document["code"] = code
        document["reason"] = getattr(exc, "message", str(exc))
        _write_json(out, document)
        if require:
            click.echo(f"abicheck: {exc}", err=True)
            raise SystemExit(EXIT_REFUSED) from exc
        click.echo(f"abicheck: no analysis context ({code})")
        return
    document = {
        "present": True,
        "records_tested_sha": context.records_tested_sha,
        **context.to_dict(),
    }
    _write_json(out, document)
    click.echo("abicheck: read analysis context")


# ---------------------------------------------------------------------------
# `verify-tested-sha` — establish the analysed commit, after extraction
# ---------------------------------------------------------------------------


@action_cli.command("verify-tested-sha")
@click.option("--run-json", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--result-json",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="`verify-run`'s own output, which already resolved the pull request.",
)
@click.option(
    "--tested-commit-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="GET /repos/{repo}/commits/{tested-sha}; the parents are what "
    "establish that the analysed commit is a merge of the pull request.",
)
@click.option("--tested-sha", required=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def verify_tested_sha_cmd(
    run_json: Path,
    result_json: Path,
    tested_commit_json: Path | None,
    tested_sha: str,
    out: Path,
) -> None:
    """Check a producer-claimed analysed commit against the verified run.

    Deliberately a *second* command over the *first* one's already-written
    result, rather than a second whole verification. The claim only becomes
    readable after the artifact has been extracted, and re-running
    ``verify-run`` to reach this one check means re-listing the run,
    re-resolving the pull request and downloading the artifact a second time
    -- which is exactly the sequence a downstream project had to write, and
    which acquires the same bytes twice with no guarantee the second copy is
    the first (an artifact can be replaced between the two requests).

    So the run and the pull request are taken from the first pass's own
    output: the identity verified here is the identity that was verified
    there, not a re-derivation that could differ.
    """
    result = _read_json(result_json)
    if not isinstance(result, dict) or not result.get("verified"):
        click.echo(
            "abicheck: refused — verify-tested-sha was given a result document "
            "that does not record a verified run",
            err=True,
        )
        raise SystemExit(EXIT_REFUSED)
    try:
        run = SourceRun.from_api(_read_json(run_json))  # type: ignore[arg-type]
        # Only `head_sha` participates in the association check below; the
        # remaining fields are reconstructed as empty because this command
        # must not re-derive them. The first pass already resolved the pull
        # request from the API and published its answer, and a second
        # derivation that disagreed would silently decide which one wins.
        pull = ResolvedPullRequest(
            number=int(result.get("pr_number") or 0),
            head_sha=str(result.get("pr_head_sha") or ""),
            head_repository="",
            base_repository="",
            state="",
        )
        verified = verify_tested_sha(
            run,
            pull,
            tested_sha=tested_sha,
            tested_commit=(
                _read_json(tested_commit_json)  # type: ignore[arg-type]
                if tested_commit_json is not None
                else None
            ),
        )
    except SourceRunRejected as exc:
        _write_json(out, {"verified": False, "code": exc.code, "reason": exc.message})
        click.echo(f"abicheck: refused analysed commit — {exc}", err=True)
        raise SystemExit(EXIT_REFUSED) from exc
    _write_json(out, {"verified": True, "tested_sha": verified})
    click.echo(f"abicheck: analysed commit {verified} verified")


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

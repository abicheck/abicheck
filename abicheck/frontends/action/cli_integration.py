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

"""Action-CLI commands for the consumer-integration owners.

A sibling of :mod:`abicheck.frontends.action.cli` rather than more commands
inside it: that module is the publication boundary's entry point and was
already close to this package's per-file ceiling, and these five commands
are a separate responsibility -- resolving a project's declared inputs and
its run's declared checks, which is analysis-adjacent configuration work,
not publication.

Like its sibling, every command here reads files and writes files. None
performs network I/O: the Actions' shells make the GitHub API calls and hand
the responses in, which is what lets each decision be tested with no
credentials at all.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import click

from abicheck.workflows.aggregate.collection import (
    AggregateValidationError,
    CollectionError,
    collect_reports,
    parse_check_declaration,
    summarize,
    validate_aggregate_document,
)

from .baseline_source import (
    BaselineProducerExpectation,
    resolve_tag,
    select_producer_run,
    verify_tag_commit,
)
from .cli_base import EXIT_REFUSED, _read_json, _write_json, action_cli
from .library_selection import (
    SelectionError,
    libraries_payload,
    resolve_library_set,
)
from .run_selection import SourceRunRejected


@action_cli.command("resolve-libraries")
@click.argument("spec", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Installation tree the spec's relative patterns are resolved against.",
)
@click.option(
    "--require-elf/--no-require-elf",
    default=True,
    help="Require every artifact to be an ELF shared object (default). Turn off for PE/Mach-O.",
)
@click.option(
    "--require-same-machine/--no-require-same-machine",
    default=True,
    help="Require every resolved ELF artifact to name the same machine (default).",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the libraries JSON here.",
)
@click.option(
    "--bind",
    "bind",
    multiple=True,
    metavar="NAME=VALUE",
    help=(
        "Fill ${NAME} in SPEC with VALUE, literally. Repeatable, and the "
        "given set is the WHOLE allowlist: an unbound ${...} is refused "
        "rather than left as text or read from the environment. For the "
        "values a checked-in declaration cannot spell because the build "
        "system decides them (an install prefix, a host-arch directory "
        "component) -- not a templating language."
    ),
)
@click.option(
    "--github-output",
    type=click.Path(path_type=Path),
    default=None,
    help="Append libraries=<json> (and machine=) to this GITHUB_OUTPUT file.",
)
def resolve_libraries_cmd(
    spec: Path,
    root: Path,
    require_elf: bool,
    require_same_machine: bool,
    out: Path | None,
    bind: tuple[str, ...],
    github_output: Path | None,
) -> None:
    """Resolve declarative component SPEC into ``actions/baseline``'s ``libraries``.

    SPEC is a JSON array using the same entry vocabulary as ``libraries``,
    except that the path fields are glob patterns and ``header_exclude`` is
    additionally accepted. See
    :mod:`abicheck.frontends.action.library_selection`.

    A refusal exits :data:`EXIT_REFUSED`, not Click's usage code: the caller
    is a CI step that must tell an unresolvable declaration apart from its own
    malformed invocation.
    """
    document = _read_json(spec)
    bindings: dict[str, str] = {}
    for entry in bind:
        # `split("=", 1)`: a VALUE is a path or an arch string and may well
        # contain `=`, so only the FIRST separator is structural.
        name, separator, value = entry.partition("=")
        if not separator:
            raise click.UsageError(f"--bind expects NAME=VALUE, got {entry!r}")
        if name in bindings and bindings[name] != value:
            raise click.UsageError(
                f"--bind {name} was given twice with different values "
                f"({bindings[name]!r} and {value!r}); which one the "
                "declaration meant is not a question this can answer by "
                "taking the last one"
            )
        bindings[name] = value
    try:
        resolved = resolve_library_set(
            document,
            root=root,
            require_elf=require_elf,
            require_same_machine=require_same_machine,
            bindings=bindings,
        )
    except SelectionError as exc:
        click.echo(f"library selection refused: {exc}", err=True)
        sys.exit(EXIT_REFUSED)

    payload = libraries_payload(resolved)
    serialized = json.dumps(payload, separators=(",", ":"))
    if out is not None:
        _write_json(out, payload)
    machines = sorted({library.machine for library in resolved if library.machine})
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"libraries={serialized}\n")
            handle.write(f"machine={machines[0] if len(machines) == 1 else ''}\n")
            handle.write(f"library-count={len(resolved)}\n")
    click.echo(serialized)
    for library in resolved:
        click.echo(
            f"  {library.name}: {library.artifact} "
            f"({len(library.headers)} header(s), {len(library.includes)} include root(s))",
            err=True,
        )


@action_cli.command("collect-checks")
@click.argument("declaration", type=click.Path(exists=True, path_type=Path))
@click.option("--reports-dir", type=click.Path(path_type=Path), required=True)
@click.option(
    "--manifest",
    type=click.Path(path_type=Path),
    required=True,
    help="Expected-target manifest to write. Must NOT be inside --reports-dir.",
)
@click.option(
    "--gate",
    default="",
    help='JSON gate block folded into the manifest, e.g. {"missing_required":"warn"}.',
)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def collect_checks_cmd(
    declaration: Path,
    reports_dir: Path,
    manifest: Path,
    gate: str,
    github_output: Path | None,
) -> None:
    """Build ``aggregate``'s reports directory and manifest from DECLARATION.

    DECLARATION is one JSON array of ``{"id", "report", "required"}`` -- the
    single statement of what this run was supposed to produce. See
    :mod:`abicheck.workflows.aggregate.collection`.
    """
    gate_block: dict[str, object] | None = None
    if gate.strip():
        parsed_gate = json.loads(gate)
        if not isinstance(parsed_gate, dict):
            raise click.ClickException("--gate must be a JSON object")
        gate_block = parsed_gate
    try:
        checks = parse_check_declaration(_read_json(declaration))
        result = collect_reports(
            checks,
            reports_dir=reports_dir,
            manifest_path=manifest,
            gate=gate_block,
        )
    except CollectionError as exc:
        click.echo(f"check collection refused: {exc}", err=True)
        sys.exit(EXIT_REFUSED)

    for check_id in result.missing:
        click.echo(
            f"::warning::no report for check {check_id!r}; it is declared expected "
            "and will aggregate as an unavailable target",
            err=True,
        )
    for check_id, why in sorted(result.unusable.items()):
        click.echo(
            f"::warning::check {check_id!r} produced a report that cannot be used "
            f"({why}); it is declared expected and will aggregate as unavailable",
            err=True,
        )
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"expected={result.expected}\n")
            handle.write(f"present={len(result.present)}\n")
            handle.write(f"missing={len(result.missing)}\n")
            handle.write(f"unusable={len(result.unusable)}\n")
            handle.write(f"manifest-path={result.manifest_path}\n")
            handle.write(f"reports-dir={result.reports_dir}\n")
    click.echo(
        f"declared {result.expected} check(s): {len(result.present)} collected, "
        f"{len(result.missing)} missing, {len(result.unusable)} unusable"
    )


@action_cli.command("validate-aggregate")
@click.argument("document", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--expected",
    type=int,
    default=None,
    help="Number of checks that were declared; the document must describe that many.",
)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def validate_aggregate_cmd(
    document: Path, expected: int | None, github_output: Path | None
) -> None:
    """Refuse DOCUMENT unless it really is an aggregate outcome.

    Exits :data:`EXIT_REFUSED` on a document that does not describe one -- an
    operational failure of the producing job, never an empty finding set.
    """
    payload = _read_json(document)
    try:
        validate_aggregate_document(payload, expected=expected)
    except AggregateValidationError as exc:
        click.echo(f"::error::aggregate document {document}: {exc}", err=True)
        sys.exit(EXIT_REFUSED)
    assert isinstance(payload, dict)  # validate_aggregate_document proved it
    summary = summarize(payload)
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            for key in ("status", "coverage", "targets", "analyzed", "unavailable"):
                handle.write(f"{key}={summary[key]}\n")
            handle.write(f"channels={json.dumps(summary['channels'])}\n")
    click.echo(
        f"aggregate ok: status {summary['status']}, coverage {summary['coverage']}, "
        f"{summary['analyzed']}/{summary['targets']} target(s) analyzed"
    )


def _maybe_json(path: Path | None) -> object | None:
    """Read *path*, or ``None`` when it is absent/empty.

    An absent document means "the API said this does not exist", which the
    callers below deliberately keep distinct from "the call itself failed"
    (their explicit ``--lookup-failed`` flag).
    """
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None
    return _read_json(path)


@action_cli.command("verify-tag")
@click.argument("name")
@click.option(
    "--ref-json",
    type=click.Path(path_type=Path),
    default=None,
    help="git/ref/tags/<name> response. Absent/empty means the ref does not exist.",
)
@click.option(
    "--tag-object-json",
    type=click.Path(path_type=Path),
    default=None,
    help="git/tags/<sha> response, required when the ref points at an annotated tag.",
)
@click.option(
    "--lookup-failed",
    is_flag=True,
    default=False,
    help="The lookup errored rather than 404'd -- an operational failure, not a missing tag.",
)
@click.option(
    "--built-sha",
    default="",
    help="Commit the capture was taken from. When given, it must equal the tag's peeled commit.",
)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def verify_tag_cmd(
    name: str,
    ref_json: Path | None,
    tag_object_json: Path | None,
    lookup_failed: bool,
    built_sha: str,
    github_output: Path | None,
) -> None:
    """Decide whether NAME is really a tag, and whether it names BUILT_SHA.

    Annotated tags are peeled; a name is never required to carry a ``v``
    prefix. Exits :data:`EXIT_REFUSED` when the answer is no.
    """
    resolution = resolve_tag(
        name,
        _maybe_json(ref_json),
        tag_object_document=_maybe_json(tag_object_json),
        lookup_failed=lookup_failed,
    )
    code = resolution.outcome
    if resolution.ok and built_sha:
        try:
            verify_tag_commit(resolution, built_sha)
        except SourceRunRejected as exc:
            code = exc.code
            resolution = replace(resolution, outcome=exc.code, message=exc.message)
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"outcome={resolution.outcome}\n")
            handle.write(
                f"is-tag={'true' if resolution.outcome == 'tag' else 'false'}\n"
            )
            handle.write(f"commit-sha={resolution.commit_sha}\n")
            handle.write(f"annotated={'true' if resolution.annotated else 'false'}\n")
    click.echo(resolution.message)
    if code != "tag":
        sys.exit(EXIT_REFUSED)


@action_cli.command("select-producer-run")
@click.argument("runs", type=click.Path(exists=True, path_type=Path))
@click.option("--expect-repository", default="")
@click.option("--expect-workflow", default="")
@click.option("--expect-event", default="")
@click.option("--expect-head-sha", default="")
@click.option("--expect-head-branch", default="")
@click.option(
    "--allowed-conclusions",
    default="success",
    help="Comma-separated run conclusions that may be used. Empty means any.",
)
@click.option(
    "--required-jobs",
    default="",
    help="Comma-separated job names that must each have concluded success.",
)
@click.option(
    "--allow-unrelated-job-failures",
    is_flag=True,
    default=False,
    help=(
        "Explicit policy: accept a run whose OVERALL conclusion failed as long as "
        "every --required-jobs entry succeeded. Without this, an unrelated failure "
        "is never silently accepted."
    ),
)
@click.option(
    "--jobs-json",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="JSON object mapping run id -> that run's jobs document.",
)
@click.option(
    "--lookup-failed",
    is_flag=True,
    default=False,
    help="The run query errored -- reported as lookup_failed, never as 'no baseline'.",
)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def select_producer_run_cmd(
    runs: Path,
    expect_repository: str,
    expect_workflow: str,
    expect_event: str,
    expect_head_sha: str,
    expect_head_branch: str,
    allowed_conclusions: str,
    required_jobs: str,
    allow_unrelated_job_failures: bool,
    jobs_json: Path | None,
    lookup_failed: bool,
    github_output: Path | None,
) -> None:
    """Choose the newest run in RUNS eligible to supply a baseline.

    RUNS is a ``workflows/<file>/runs`` response (or its bare
    ``workflow_runs`` array). Exits :data:`EXIT_REFUSED` when none is
    eligible -- with ``outcome`` distinguishing ``not_found`` (a real
    lifecycle state) from ``lookup_failed`` (an operational one).
    """
    document = _read_json(runs)
    if isinstance(document, dict):
        candidates = document.get("workflow_runs", [])
    else:
        candidates = document
    if not isinstance(candidates, list):
        raise click.ClickException(
            f"{runs} does not hold a workflow-runs array (got {type(candidates).__name__})"
        )
    jobs_by_run = _read_json(jobs_json) if jobs_json is not None else {}
    if not isinstance(jobs_by_run, dict):
        raise click.ClickException(
            "--jobs-json must hold a JSON object keyed by run id"
        )

    selection = select_producer_run(
        candidates,
        BaselineProducerExpectation(
            repository=expect_repository,
            workflow=expect_workflow,
            event=expect_event,
            head_sha=expect_head_sha,
            head_branch=expect_head_branch,
            allowed_conclusions=tuple(
                item.strip() for item in allowed_conclusions.split(",") if item.strip()
            ),
            required_jobs=tuple(
                item.strip() for item in required_jobs.split(",") if item.strip()
            ),
            allow_unrelated_job_failures=allow_unrelated_job_failures,
        ),
        lookup_failed=lookup_failed,
        jobs_by_run={str(key): value for key, value in jobs_by_run.items()},
    )
    for line in selection.rejected:
        click.echo(f"  rejected {line}", err=True)
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"outcome={selection.outcome}\n")
            handle.write(f"run-id={selection.run.run_id if selection.run else ''}\n")
            handle.write(
                f"run-attempt={selection.run.run_attempt if selection.run else ''}\n"
            )
            handle.write(
                f"head-sha={selection.run.head_sha if selection.run else ''}\n"
            )
            handle.write(f"considered={len(candidates)}\n")
            handle.write(f"rejected={len(selection.rejected)}\n")
    click.echo(selection.message)
    if not selection.ok:
        sys.exit(EXIT_REFUSED)

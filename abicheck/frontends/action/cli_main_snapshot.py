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

"""Commands behind ``actions/main-snapshot`` and ``actions/fetch-main-snapshot``.

A sibling of :mod:`abicheck.frontends.action.cli`, registered on the same
group. Like its siblings it performs no network I/O: the Actions' shells run
``git`` and the GitHub API calls and hand the results over as arguments and
files; every decision is in :mod:`~abicheck.frontends.action.main_snapshot`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .cli_base import EXIT_REFUSED, _read_json, action_cli
from .main_snapshot import (
    DeltaOutcome,
    SnapshotExpectation,
    delta_notes,
    outcome_message,
    resolve_snapshot,
    select_base_commit,
    snapshot_artifact_name,
)
from .main_snapshot_cache import (
    DEFAULT_MAX_ENTRY_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    CacheExpectation,
    provenance_record,
    snapshot_cache_key,
    verify_cached_snapshot,
)


def _rows(path: Path, key: str) -> list[object]:
    document = _read_json(path)
    if isinstance(document, dict):
        document = document.get(key, [])
    if not isinstance(document, list):
        raise click.ClickException(f"{path} does not hold a {key!r} array")
    return document


def _emit(github_output: Path | None, **fields: str) -> None:
    if github_output is None:
        return
    with github_output.open("a", encoding="utf-8") as handle:
        for name, value in fields.items():
            handle.write(f"{name.replace('_', '-')}={' '.join(value.splitlines())}\n")


@action_cli.command("snapshot-name")
@click.argument("sha")
@click.option("--profile", default="", help="Optional profile suffix.")
def snapshot_name_cmd(sha: str, profile: str) -> None:
    """Print the artifact name a snapshot of commit SHA is stored under."""
    try:
        click.echo(snapshot_artifact_name(sha, profile))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@action_cli.command("select-base")
@click.option(
    "--mode",
    type=click.Choice(["merge-commit", "head", "merge-group"]),
    required=True,
)
@click.option("--head-parents", default="", help="Space-separated parents of HEAD.")
@click.option("--merge-bases", default="", help="Space-separated merge-bases.")
@click.option("--merge-group-base", default="")
@click.option("--branch", default="main")
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def select_base_cmd(
    mode: str,
    head_parents: str,
    merge_bases: str,
    merge_group_base: str,
    branch: str,
    github_output: Path | None,
) -> None:
    """Choose the commit the build sits on; exit 3 with an outcome when none."""
    selection = select_base_commit(
        mode,
        head_parents=head_parents.split(),
        merge_bases=merge_bases.split(),
        merge_group_base=merge_group_base.strip(),
    )
    _emit(
        github_output,
        outcome=selection.outcome.value,
        base_sha=selection.sha,
        message=selection.message
        or outcome_message(selection.outcome, sha=selection.sha, branch=branch),
    )
    click.echo(selection.message or f"base {selection.sha} ({selection.rule})")
    if not selection.ok:
        sys.exit(EXIT_REFUSED)


@action_cli.command("resolve-snapshot")
@click.argument("artifacts", type=click.Path(exists=True, path_type=Path))
@click.argument("runs", type=click.Path(exists=True, path_type=Path))
@click.option("--repository", required=True)
@click.option("--workflow", required=True)
@click.option("--branch", required=True)
@click.option("--base-sha", required=True)
@click.option("--artifact-name", required=True)
@click.option("--merge-group", is_flag=True, default=False)
@click.option("--lookup-failed", is_flag=True, default=False)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def resolve_snapshot_cmd(
    artifacts: Path,
    runs: Path,
    repository: str,
    workflow: str,
    branch: str,
    base_sha: str,
    artifact_name: str,
    merge_group: bool,
    lookup_failed: bool,
    github_output: Path | None,
) -> None:
    """Pick the snapshot artifact for --base-sha; exit 3 with an outcome when none.

    ARTIFACTS is an ``actions/artifacts?name=`` response, RUNS the snapshot
    workflow's runs at that SHA in every status.
    """
    resolution = resolve_snapshot(
        _rows(artifacts, "artifacts"),
        _rows(runs, "workflow_runs"),
        SnapshotExpectation(
            repository=repository,
            workflow=workflow,
            branch=branch,
            base_sha=base_sha,
            artifact_name=artifact_name,
        ),
        lookup_failed=lookup_failed,
    )
    for line in resolution.rejected:
        click.echo(f"  rejected {line}", err=True)
    _emit(
        github_output,
        outcome=resolution.outcome.value,
        artifact_id=resolution.artifact_id,
        run_id=resolution.run_id,
        message=outcome_message(
            resolution.outcome,
            sha=base_sha,
            branch=branch,
            subject="merge-queue entry" if merge_group else "PR",
        ),
    )
    click.echo(resolution.message)
    if resolution.outcome is not DeltaOutcome.RESOLVED:
        sys.exit(EXIT_REFUSED)


@action_cli.command("delta-notes")
@click.option("--outcome", required=True)
@click.option("--base-sha", default="")
@click.option("--branch", default="main")
@click.option("--gate-verdict", default="")
@click.option("--merge-group", is_flag=True, default=False)
def delta_notes_cmd(
    outcome: str, base_sha: str, branch: str, gate_verdict: str, merge_group: bool
) -> None:
    """Print the delta-baseline comment notes, one per line."""
    try:
        lines = delta_notes(
            outcome,
            base_sha=base_sha,
            branch=branch,
            gate_verdict=gate_verdict,
            merge_group=merge_group,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    for line in lines:
        click.echo(line)


# ── storage: cache ──────────────────────────────────────────────────────────


@action_cli.command("snapshot-cache-key")
@click.argument("sha")
@click.option("--profile", default="", help="Optional profile suffix.")
def snapshot_cache_key_cmd(sha: str, profile: str) -> None:
    """Print the Actions-cache key a snapshot of commit SHA is saved under."""
    try:
        click.echo(snapshot_cache_key(sha, profile))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@action_cli.command("write-cache-provenance")
@click.argument("out", type=click.Path(path_type=Path))
@click.option("--sha", required=True)
@click.option("--profile", default="")
@click.option("--repository", required=True)
@click.option("--ref", required=True)
@click.option("--run-id", default="")
@click.option("--run-attempt", default="")
@click.option("--workflow-ref", default="")
@click.option("--event", default="")
def write_cache_provenance_cmd(
    out: Path,
    sha: str,
    profile: str,
    repository: str,
    ref: str,
    run_id: str,
    run_attempt: str,
    workflow_ref: str,
    event: str,
) -> None:
    """Write the provenance.json main-snapshot stores beside a cached snapshot.

    Prints the cache key it records, so the save step uses the same one.
    """
    try:
        key = snapshot_cache_key(sha, profile)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    record = provenance_record(
        sha=sha.strip().lower(),
        key=key,
        repository=repository,
        ref=ref,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_ref=workflow_ref,
        event=event,
    )
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    click.echo(key)


@action_cli.command("verify-cached-snapshot")
@click.argument("directory", type=click.Path(path_type=Path))
@click.option("--repository", required=True)
@click.option("--branch", required=True)
@click.option("--base-sha", required=True)
@click.option("--key", required=True)
@click.option("--cache-hit", default="", help="actions/cache/restore's cache-hit.")
@click.option("--matched-key", default="")
@click.option("--destination", type=click.Path(path_type=Path), required=True)
@click.option("--max-entry-bytes", type=int, default=DEFAULT_MAX_ENTRY_BYTES)
@click.option("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def verify_cached_snapshot_cmd(
    directory: Path,
    repository: str,
    branch: str,
    base_sha: str,
    key: str,
    cache_hit: str,
    matched_key: str,
    destination: Path,
    max_entry_bytes: int,
    max_total_bytes: int,
    github_output: Path | None,
) -> None:
    """Check a restored cache entry; exit 3 with an outcome when it is unusable."""
    result = verify_cached_snapshot(
        directory,
        CacheExpectation(
            repository=repository, branch=branch, base_sha=base_sha, key=key
        ),
        cache_hit=cache_hit.strip().lower() == "true",
        matched_key=matched_key.strip(),
        destination=destination,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
    )
    _emit(
        github_output,
        outcome=result.outcome.value,
        message=result.message,
        snapshot_path=result.snapshot_path,
    )
    click.echo(f"{result.outcome.value}: {result.reason}")
    if not result.ok:
        sys.exit(EXIT_REFUSED)

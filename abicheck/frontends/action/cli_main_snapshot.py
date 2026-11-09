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

"""Commands behind ``actions/main-snapshot``, ``actions/fetch-main-snapshot`` and the
main Action's ``delta-baseline``.

A sibling of :mod:`abicheck.frontends.action.cli`, registered on the same
group. Like its siblings it performs no network I/O: the Actions' shells run
``git`` and the GitHub API calls and hand the results over as arguments and
files; every decision is in :mod:`~abicheck.frontends.action.main_snapshot`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from .cli_base import EXIT_REFUSED, _read_json, action_cli
from .delta_base_dump import BaseDumpRefused, base_dump_argv
from .main_snapshot import (
    BUILDABLE_OUTCOMES,
    DeltaOutcome,
    SnapshotExpectation,
    delta_notes,
    outcome_message,
    resolve_snapshot,
    select_base_commit,
    snapshot_artifact_name,
)
from .main_snapshot_ancestor import (
    MAX_ANCESTORS,
    WALKABLE_OUTCOMES,
    AncestorFact,
    EquivalentPathsError,
    equivalent_pathspecs,
    plan_ancestor_walk,
    walk_message,
)
from .main_snapshot_cache import (
    DEFAULT_MAX_ENTRY_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    CacheExpectation,
    cache_scope_refusal,
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
@click.option(
    "--equivalent-to",
    default="",
    help="The PR's base commit, when --base-sha is a path-equivalent ancestor of it.",
)
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
    equivalent_to: str,
    github_output: Path | None,
) -> None:
    """Pick the snapshot artifact for --base-sha; exit 3 with an outcome when none.

    ARTIFACTS is an ``actions/artifacts?name=`` response, RUNS the snapshot
    workflow's runs at that SHA in every status. With --equivalent-to, the
    provenance checks still apply to --base-sha (the ancestor whose snapshot
    is used), and the message names both commits.
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
            sha=equivalent_to or base_sha,
            branch=branch,
            subject="merge-queue entry" if merge_group else "PR",
            snapshot_sha=base_sha if equivalent_to else "",
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
@click.option(
    "--operand-match", default="", help="delta-operand's matched rule, if any."
)
@click.option(
    "--snapshot-sha",
    default="",
    help="The commit whose snapshot was compared, when not --base-sha itself.",
)
def delta_notes_cmd(
    outcome: str,
    base_sha: str,
    branch: str,
    gate_verdict: str,
    merge_group: bool,
    operand_match: str,
    snapshot_sha: str,
) -> None:
    """Print the delta-baseline comment notes, one per line."""
    try:
        lines = delta_notes(
            outcome,
            base_sha=base_sha,
            branch=branch,
            gate_verdict=gate_verdict,
            merge_group=merge_group,
            operand_match=operand_match,
            snapshot_sha=snapshot_sha,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    for line in lines:
        click.echo(line)


@action_cli.command("delta-buildable")
@click.argument("outcome")
def delta_buildable_cmd(outcome: str) -> None:
    """Exit 0 when delta-on-missing-base: build applies to OUTCOME, else 3."""
    if outcome not in {o.value for o in BUILDABLE_OUTCOMES}:
        sys.exit(EXIT_REFUSED)


@action_cli.command("delta-base-dump-args")
@click.argument("gate_tail", type=click.Path(exists=True, path_type=Path))
@click.option("--new-library", required=True)
@click.option(
    "--cwd", required=True, help="Directory the gate's relative paths resolve against."
)
@click.option(
    "--checkout", required=True, help="Top-level directory of the PR checkout."
)
@click.option("--base-root", required=True, help="The base commit's checkout.")
@click.option("--output", required=True, help="Snapshot path for `dump -o`.")
@click.option(
    "--value-options",
    default="",
    help="'|'-separated options of the gate command that take a value.",
)
def delta_base_dump_args_cmd(
    gate_tail: Path,
    new_library: str,
    cwd: str,
    checkout: str,
    base_root: str,
    output: str,
    value_options: str,
) -> None:
    """Print, NUL-separated, the ``dump`` argv for a base built in the PR job.

    GATE_TAIL holds the gate ``compare`` argv after its two operands,
    NUL-separated. Exits 3 with the reason on stderr when the new side's
    evidence cannot be re-pointed at the base checkout.
    """
    tail = [tok for tok in gate_tail.read_bytes().decode("utf-8").split("\0") if tok]
    try:
        argv = base_dump_argv(
            tail,
            new_library=new_library,
            cwd=cwd,
            checkout=checkout,
            base_root=base_root,
            output=output,
            value_options=frozenset(o for o in value_options.split("|") if o),
        )
    except BaseDumpRefused as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_REFUSED)
    sys.stdout.write("".join(f"{tok}\0" for tok in argv))


# ── base-equivalent-paths: the path-equivalent ancestor rule ────────────────


@action_cli.command("equivalent-pathspecs")
@click.option("--paths", required=True, help="The base-equivalent-paths value.")
def equivalent_pathspecs_cmd(paths: str) -> None:
    """Print the git pathspecs of a base-equivalent-paths value, NUL-separated.

    Exits 3 with the reason on stderr when the value is not a usable
    declaration; prints nothing for an empty one (the rule is off).
    """
    try:
        specs = equivalent_pathspecs(paths)
    except EquivalentPathsError as exc:
        click.echo(f"base-equivalent-paths: {exc}", err=True)
        sys.exit(EXIT_REFUSED)
    sys.stdout.write("".join(f"{spec}\0" for spec in specs))


@action_cli.command("ancestor-walkable")
@click.argument("outcome")
def ancestor_walkable_cmd(outcome: str) -> None:
    """Exit 0 when the exact base's OUTCOME lets base-equivalent-paths walk, else 3."""
    if outcome not in {o.value for o in WALKABLE_OUTCOMES}:
        sys.exit(EXIT_REFUSED)


#: Seconds one local git query of the ancestor walk may take.
_GIT_TIMEOUT_S = 60.0


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the checkout, bounded; a missing or hung git is a failed
    call (the walk then reads as ``history_unavailable``), not a crash."""
    from ...deadline import DeadlineExceeded, run_bounded

    try:
        return run_bounded(
            ["git", *args], timeout=_GIT_TIMEOUT_S, capture_output=True, text=True
        )
    except (OSError, subprocess.TimeoutExpired, DeadlineExceeded) as exc:
        return subprocess.CompletedProcess(["git", *args], 127, "", str(exc))


def _ancestor_facts(
    base: str, specs: tuple[str, ...], limit: int
) -> tuple[list[AncestorFact], bool]:
    """The base's first-parent ancestors (nearest first, up to the first path
    change) and whether the list ends because the history does.

    Raises :class:`click.ClickException` when git cannot answer (the base is
    not in this clone).
    """
    listed = _git("rev-list", "--first-parent", f"--max-count={limit + 1}", base)
    if listed.returncode != 0:
        raise click.ClickException(
            f"git rev-list {base[:12]} failed: {listed.stderr.strip()}"
        )
    shas = listed.stdout.split()[1:]
    facts: list[AncestorFact] = []
    for sha in shas:
        diff = _git("diff", "--name-only", "--no-renames", sha, base, "--", *specs)
        if diff.returncode != 0:
            # The tree is not here (a partial or broken clone): what follows
            # is unknown, not unchanged.
            return facts, False
        changed = tuple(diff.stdout.splitlines())
        facts.append(AncestorFact(sha, changed))
        if changed:
            return facts, True
    if len(shas) >= limit:
        return facts, True
    last = shas[-1] if shas else base
    shallow_file = _git("rev-parse", "--git-path", "shallow").stdout.strip()
    boundary: set[str] = set()
    if shallow_file and Path(shallow_file).is_file():
        boundary = set(Path(shallow_file).read_text(encoding="utf-8").split())
    if last in boundary:
        return facts, False
    parents = _git("rev-list", "--parents", "-n1", last).stdout.split()[1:]
    return facts, not parents


@action_cli.command("plan-ancestor-walk")
@click.option("--base-sha", required=True)
@click.option("--paths", required=True, help="The base-equivalent-paths value.")
@click.option("--branch", default="main")
@click.option("--limit", type=click.IntRange(1, MAX_ANCESTORS), default=MAX_ANCESTORS)
@click.option("--github-output", type=click.Path(path_type=Path), required=True)
def plan_ancestor_walk_cmd(
    base_sha: str, paths: str, branch: str, limit: int, github_output: Path
) -> None:
    """Write the ancestors whose snapshot may stand in for --base-sha.

    Runs ``git`` in the current directory (the checkout). Writes one
    ``candidate=<sha>`` line per ancestor, nearest first, then
    ``exhausted=<walk result>`` and ``exhausted-message=...`` for the case
    none of them has a snapshot.
    """
    try:
        specs = equivalent_pathspecs(paths)
    except EquivalentPathsError as exc:
        raise click.UsageError(f"base-equivalent-paths: {exc}") from exc
    if not specs:
        raise click.UsageError("base-equivalent-paths is empty")
    facts, complete = _ancestor_facts(base_sha, specs, limit)
    plan = plan_ancestor_walk(facts, limit=limit, history_complete=complete)
    message = walk_message(
        plan.exhausted,
        base_sha=base_sha,
        branch=branch,
        changed_at=plan.changed_at,
        changed_paths=plan.changed_paths,
        tried=len(plan.candidates),
    )
    with github_output.open("a", encoding="utf-8") as handle:
        for sha in plan.candidates:
            handle.write(f"candidate={sha}\n")
        handle.write(f"exhausted={plan.exhausted.value}\n")
        handle.write(f"exhausted-message={' '.join(message.splitlines())}\n")
    click.echo(
        f"{len(plan.candidates)} path-equivalent ancestor(s) of {base_sha[:7]}; "
        f"then {plan.exhausted.value}"
    )


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
@click.option("--runner-os", default="")
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
    runner_os: str,
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
        runner_os=runner_os,
    )
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    click.echo(key)


@action_cli.command("cache-scope-check")
@click.option("--event", default="")
@click.option("--ref", default="")
@click.option("--base-ref", default="")
@click.option("--branch", required=True)
@click.option("--github-output", type=click.Path(path_type=Path), default=None)
def cache_scope_check_cmd(
    event: str, ref: str, base_ref: str, branch: str, github_output: Path | None
) -> None:
    """Exit 3 with `untrusted_source` when this job may read caches `branch` does not own."""
    refusal = cache_scope_refusal(
        event=event, ref=ref, base_ref=base_ref, branch=branch
    )
    if refusal is None:
        click.echo(f"cache scope ok: {event} on {ref or base_ref}")
        return
    _emit(github_output, outcome=refusal.outcome.value, message=refusal.message)
    click.echo(f"{refusal.outcome.value}: {refusal.reason}")
    sys.exit(EXIT_REFUSED)


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
@click.option(
    "--equivalent-to",
    default="",
    help="The PR's base commit, when --base-sha is a path-equivalent ancestor of it.",
)
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
    equivalent_to: str,
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
        equivalent_to=equivalent_to.strip(),
    )
    _emit(
        github_output,
        outcome=result.outcome.value,
        message=result.message,
        snapshot_path=result.snapshot_path,
        snapshot_sha=base_sha if result.ok else "",
    )
    click.echo(f"{result.outcome.value}: {result.reason}")
    if not result.ok:
        sys.exit(EXIT_REFUSED)


@action_cli.command("operand-record")
@click.argument("out", type=click.Path(path_type=Path))
@click.option(
    "--library", default="", help="The library path the snapshot was dumped from."
)
@click.option("--profile", default="")
def operand_record_cmd(out: Path, library: str, profile: str) -> None:
    """Write the ``operand.json`` main-snapshot uploads beside the snapshot."""
    import json

    from ...workflows.delta_operand import operand_record

    out.write_text(
        json.dumps(operand_record(library, profile), indent=2) + "\n", encoding="utf-8"
    )


@action_cli.command("delta-operand")
@click.option(
    "--snapshot",
    "snapshot_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The fetched snapshot.json (its operand.json sibling is read if present).",
)
@click.option(
    "--new-library",
    required=True,
    type=click.Path(path_type=Path),
    help="This PR's new-side operand.",
)
@click.option(
    "--new-given-as",
    default="",
    help="The new operand as the user spelled it (matched against a recorded path).",
)
@click.option("--map", "operand_map", default="", help="delta-operand-map text.")
def delta_operand_cmd(
    snapshot_path: Path, new_library: Path, new_given_as: str, operand_map: str
) -> None:
    """Is the snapshot the same library as this PR's?

    Prints ``outcome=``, ``rule=`` and ``message=`` lines.

    Exit 0 when matched, 3 (refused) for every named non-match, a malformed
    ``--map`` (``operand_map_invalid``) included.
    """
    from ...workflows.delta_operand import (
        OperandMatch,
        OperandMatchOutcome,
        identity_of_operand,
        identity_of_snapshot_artifact,
        match_delta_operand,
        parse_operand_map,
    )

    try:
        pairs = parse_operand_map(operand_map)
    except ValueError as exc:
        # A named outcome, not a usage error: the shell discards stderr, and
        # the message (naming the bad line) must reach the job log.
        pairs = None
        result = OperandMatch(OperandMatchOutcome.MAP_INVALID, message=str(exc))
    try:
        if pairs is not None:
            result = match_delta_operand(
                identity_of_snapshot_artifact(snapshot_path),
                identity_of_operand(new_library, new_given_as or str(new_library)),
                pairs,
            )
    except Exception as exc:  # noqa: BLE001 -- any unreadable side is one named outcome
        result = OperandMatch(
            OperandMatchOutcome.UNREADABLE, message=f"{type(exc).__name__}: {exc}"
        )
    click.echo(f"outcome={result.outcome.value}")
    click.echo(f"rule={result.rule.value}")
    click.echo(f"message={' '.join(result.message.splitlines())}")
    if not result.ok:
        sys.exit(EXIT_REFUSED)

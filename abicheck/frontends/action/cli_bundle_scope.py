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
"""``bundle-selection`` and ``bundle-candidate-plan``: a bundle check's
member-level inputs, prepared for ``compare``.

Registered on :data:`~abicheck.frontends.action.cli_base.action_cli` and
called by ``actions/check-target`` after ``resolve-baseline``. The logic is
:func:`abicheck.workflows.bundle_scope_selection.bundle_selection` and
:func:`abicheck.workflows.bundle_candidate_snapshots.plan_candidate_dumps`;
this module parses and prints.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ...workflows.bundle_candidate_snapshots import (
    CandidatePlanError,
    plan_candidate_dumps,
)
from ...workflows.bundle_scope_selection import BundleSelectionError, bundle_selection
from .cli_base import EXIT_REFUSED, action_cli


def _json_value(raw: str, *, name: str, kind: type) -> object:
    try:
        value = json.loads(raw or ("[]" if kind is list else "{}"))
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"{name}: not JSON: {exc}") from exc
    if not isinstance(value, kind):
        raise click.UsageError(f"{name}: expected a JSON {kind.__name__}")
    return value


@action_cli.command("bundle-selection")
@click.option("--members", required=True, help="JSON array of member target ids.")
@click.option(
    "--required",
    "required_raw",
    default="[]",
    help="JSON array of required member ids; empty = every member.",
)
@click.option(
    "--binary-paths",
    required=True,
    help="resolve-baseline's binary-paths JSON object (member -> baseline binary).",
)
@click.option(
    "--on-incomplete", default="", help="'fail', 'warn', or '' (not declared)."
)
def bundle_selection_command(
    members: str, required_raw: str, binary_paths: str, on_incomplete: str
) -> None:
    """Print ``args=`` (selection tokens) and ``set=`` (scope.on_incomplete)
    as ``$GITHUB_OUTPUT`` lines. Exits 3 when the policy cannot be stated."""
    member_ids = _json_value(members, name="--members", kind=list)
    required = _json_value(required_raw, name="--required", kind=list)
    paths = _json_value(binary_paths, name="--binary-paths", kind=dict)
    assert isinstance(member_ids, list)
    assert isinstance(required, list)
    assert isinstance(paths, dict)
    try:
        selection = bundle_selection(
            [str(m) for m in member_ids],
            [str(r) for r in required],
            {str(k): str(v) for k, v in paths.items()},
            on_incomplete,
        )
    except BundleSelectionError as exc:
        click.echo(f"bundle-selection: {exc}", err=True)
        sys.exit(EXIT_REFUSED)
    click.echo("args=" + " ".join(selection.compare_args()))
    click.echo("set=" + selection.set_line())


@action_cli.command("bundle-candidate-plan")
@click.option("--members", required=True, help="JSON array of member target ids.")
@click.option(
    "--member-headers",
    required=True,
    help="JSON object: member -> newline-joined public_headers.",
)
@click.option("--binary-paths", required=True, help="resolve-baseline's binary-paths.")
@click.option(
    "--snapshot-paths",
    required=True,
    help="resolve-baseline's member-snapshot-paths.",
)
@click.option(
    "--candidate-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--plan-file",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Written: one row per dump, fields joined by \\x1f, headers by \\x1e.",
)
def bundle_candidate_plan_command(
    members: str,
    member_headers: str,
    binary_paths: str,
    snapshot_paths: str,
    candidate_dir: Path,
    plan_file: Path,
) -> None:
    """Plan one header dump per bundle member; print ``missing=`` (JSON)."""
    member_ids = _json_value(members, name="--members", kind=list)
    headers = _json_value(member_headers, name="--member-headers", kind=dict)
    binaries = _json_value(binary_paths, name="--binary-paths", kind=dict)
    snapshots = _json_value(snapshot_paths, name="--snapshot-paths", kind=dict)
    assert isinstance(member_ids, list)
    assert isinstance(headers, dict)
    assert isinstance(binaries, dict)
    assert isinstance(snapshots, dict)
    try:
        plan = plan_candidate_dumps(
            [str(m) for m in member_ids],
            {str(k): str(v) for k, v in headers.items()},
            {str(k): str(v) for k, v in binaries.items()},
            {str(k): str(v) for k, v in snapshots.items()},
            candidate_dir,
        )
    except CandidatePlanError as exc:
        click.echo(f"bundle-candidate-plan: {exc}", err=True)
        sys.exit(EXIT_REFUSED)
    rows = []
    for dump in plan.dumps:
        fields = (dump.member, str(dump.candidate), dump.output_name)
        joined = "\x1e".join(dump.headers)
        if any(
            ch in "\x1e\x1f\n\r" for ch in "".join(fields) + joined.replace("\x1e", "")
        ):
            click.echo(
                f"bundle-candidate-plan: unsafe value for {dump.member!r}", err=True
            )
            sys.exit(EXIT_REFUSED)
        rows.append("\x1f".join((*fields, joined)))
    plan_file.write_text("".join(row + "\n" for row in rows), encoding="utf-8")
    click.echo("missing=" + json.dumps(list(plan.missing)))

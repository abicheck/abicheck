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
"""``bundle-selection``: a bundle check's scope policy as ``compare`` arguments.

Registered on :data:`~abicheck.frontends.action.cli_base.action_cli` and
called by ``actions/check-target`` after ``resolve-baseline``; the
translation itself is
:func:`abicheck.workflows.bundle_scope_selection.bundle_selection`.
"""

from __future__ import annotations

import json
import sys

import click

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

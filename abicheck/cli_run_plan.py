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

"""CLI — the ``run-plan`` group (G30 P1.4, ADR-047 §4/§5).

``run-plan generate`` projects a project's ``.abicheck.yml`` ``targets:``/
``bundles:``/``profiles:``/``baseline:`` block (G30 P1.5) plus each
``contract: true`` profile's ``build-output.json`` (G30 P1.1) into
``run-plan.json`` — the ordered check list ``check-project.yml``'s matrix
and ``check-single.yml``'s direct invocation both consume (not built here;
this command only produces the artifact those workflows read).
``run-plan to-aggregate-manifest`` projects that artifact down to
``abicheck aggregate --manifest``'s wire shape. Split out of
:mod:`abicheck.cli` per the sibling-module pattern; imported for
side-effect at the bottom of :mod:`abicheck.cli` so ``@main.group``/
``@run_plan_group.command`` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .buildsource.build_output import BuildOutput, load_build_output
from .buildsource.project_targets import (
    ProjectTargetsConfig,
    validate_project_targets,
)
from .buildsource.run_plan import RunPlan, generate_run_plan, to_aggregate_manifest
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option


@main.group("run-plan")
def run_plan_group() -> None:
    """Generate and project the multi-target CI run-plan (ADR-047 §4/§5).

    \b
    Subcommands:
      generate               Derive run-plan.json from .abicheck.yml + build-output.json.
      to-aggregate-manifest  Project run-plan.json to `aggregate --manifest`'s shape.
    """


def _parse_build_output_specs(
    specs: tuple[str, ...],
) -> dict[str, BuildOutput]:
    build_outputs: dict[str, BuildOutput] = {}
    for spec in specs:
        profile_id, sep, dir_str = spec.partition("=")
        if not sep or not profile_id or not dir_str:
            raise click.UsageError(f"--build-output must be PROFILE=DIR, got {spec!r}")
        if profile_id in build_outputs:
            raise click.UsageError(
                f"--build-output: profile {profile_id!r} was specified more than once"
            )
        try:
            build_outputs[profile_id] = load_build_output(dir_str)
        except (FileNotFoundError, ValueError) as exc:
            raise click.UsageError(f"--build-output {spec}: {exc}") from exc
    return build_outputs


@run_plan_group.command("generate")
@click.argument(
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=".abicheck.yml",
)
@click.option(
    "--build-output",
    "build_output_specs",
    multiple=True,
    metavar="PROFILE=DIR",
    help=(
        "One contract profile's abicheck-build/ directory (containing "
        "build-output.json), as profile_id=path/to/dir. Repeatable — pass "
        "one per profile referenced by CONFIG's checks:."
    ),
)
@click.option(
    "--project",
    default="",
    help="Project identifier recorded in run-plan.json, e.g. owner/repo.",
)
@click.option(
    "--head-sha",
    default="",
    help="Candidate commit SHA recorded in run-plan.json.",
)
@output_options(
    ["json", "text"],
    default="json",
    format_help="Output format for the generated run-plan.",
)
@verbose_option
def run_plan_generate_cmd(
    config: Path,
    build_output_specs: tuple[str, ...],
    project: str,
    head_sha: str,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Generate run-plan.json from CONFIG's targets:/bundles:/profiles: block.

    CONFIG defaults to ``.abicheck.yml``. For every ``checks[]`` entry (per
    target or per bundle), resolves which ``(target, profile)`` cells
    actually apply: an explicit ``checks[].profiles:`` selector must resolve
    against that profile's ``--build-output``, or it's an error; an implicit
    "every contract profile" sweep silently skips a profile that doesn't
    build the target (never a blind cross-product, ADR-047 §3). Each
    resolved cell's ``check_id`` is
    ``target@profile#baseline_channel@requested_depth`` (ADR-047 §7).

    \b
    Exit codes:
      0   Generated with no coverage-gap errors (warnings may still exist).
      1   A required/explicit check could not be resolved against the
          supplied --build-output directories.
      64  Usage error (CONFIG or a --build-output value is unreadable, or
          CONFIG fails project-targets validation).
    """
    _setup_verbosity(verbose)

    import yaml

    try:
        raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise click.UsageError(f"cannot read {config}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise click.UsageError(f"{config} must contain a YAML mapping.")

    try:
        parsed = ProjectTargetsConfig.from_dict(raw)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    validation = validate_project_targets(parsed)
    if not validation.ok:
        details = "; ".join(validation.errors)
        raise click.UsageError(
            "cannot generate a run-plan from an invalid project-targets "
            f"config ({len(validation.errors)} error(s)): {details} — run "
            f"`abicheck project-targets validate {config}` for the full report."
        )

    build_outputs = _parse_build_output_specs(build_output_specs)
    plan, report = generate_run_plan(
        parsed, build_outputs, project=project, head_sha=head_sha
    )

    for e in report.errors:
        click.echo(f"error: {e}", err=True)
    for w in report.warnings:
        click.echo(f"warning: {w}", err=True)

    if fmt == "json":
        text = json.dumps(plan.to_dict(), indent=2)
    else:
        lines = [f"run-plan: {len(plan.checks)} check(s)"]
        lines.extend(
            f"  - {c.check_id} (required={c.required}, gate_mode={c.gate_mode})"
            for c in plan.checks
        )
        text = "\n".join(lines)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0 if report.ok else 1)


@run_plan_group.command("to-aggregate-manifest")
@click.argument(
    "run_plan_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--head-sha",
    default=None,
    help="Override run-plan.json's own head_sha in the emitted manifest.",
)
@output_options(
    ["json"],
    default="json",
    format_help="Output format for the emitted manifest.",
)
def run_plan_to_aggregate_manifest_cmd(
    run_plan_json: Path,
    head_sha: str | None,
    fmt: str,
    output: Path | None,
) -> None:
    """Project RUN_PLAN_JSON to `abicheck aggregate --manifest`'s wire shape.

    Uses each check's own ``check_id`` as ``targets[].id`` (never the bare
    target/bundle name) so ``aggregate`` matches reports by the same
    identity ``check-target`` (G30 P1.3) writes into each report's own
    ``target_id`` — required for S17/S21's multi-profile/multi-channel same-
    target checks not to collide in ``aggregate``'s duplicate-id check.

    \b
    Exit codes:
      0   Always, for a readable run-plan.json — this command performs no
          semantic validation of its own. An empty checks: list emits a
          manifest with an empty targets: [], which `aggregate --manifest`
          itself then rejects (it requires a non-empty list) — that check
          belongs to `aggregate`, not duplicated here.
      64  Usage error (RUN_PLAN_JSON is not readable/valid JSON).
    """
    try:
        raw = json.loads(run_plan_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.UsageError(f"cannot read {run_plan_json}: {exc}") from exc
    if not isinstance(raw, dict):
        raise click.UsageError(f"{run_plan_json} must contain a JSON object.")

    plan = RunPlan.from_dict(raw)
    manifest = to_aggregate_manifest(plan, head_sha=head_sha)
    text = json.dumps(manifest, indent=2)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

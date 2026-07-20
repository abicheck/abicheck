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

"""CLI — the ``project-targets`` group (G30 P1.5, ADR-047 §3).

``project-targets validate`` checks a project's ``.abicheck.yml``
``targets:``/``bundles:``/``profiles:``/``baseline:`` block — the config
G30 P1.4's (not built yet) run-plan generator will consume — for structural
validity and cross-reference integrity (kind-specific required fields,
``library``/``bundle``/``profiles``/``channel`` references all resolve,
identifiers stay embeddable in a report ``check_id``). Split out of
:mod:`abicheck.cli` per the sibling-module pattern; imported for side-effect
at the bottom of :mod:`abicheck.cli` so ``@main.group``/
``@project_targets_group.command`` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .buildsource.project_targets import (
    ProjectTargetsConfig,
    validate_project_targets,
)
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option


@main.group("project-targets")
def project_targets_group() -> None:
    """Validate a project's target/bundle/profile/release-channel setup.

    \b
    Subcommands:
      validate  Check .abicheck.yml's targets/bundles/profiles/channels block.
    """


@project_targets_group.command("validate")
@click.argument(
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=".abicheck.yml",
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
@verbose_option
def project_targets_validate_cmd(
    config: Path,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Validate CONFIG's targets:/bundles:/profiles:/baseline: block (ADR-047 §3).

    CONFIG defaults to ``.abicheck.yml`` in the current directory. Checks:
    every target's ``kind``-specific required fields are set (and no
    kind-inappropriate field is); ``app-consumer``/``plugin-contract``
    targets' ``library`` resolves to a real ``kind: library`` target; every
    ``bundle:`` reference and bundle membership resolves and agrees; every
    ``checks[].channel`` resolves to a declared baseline channel (or is the
    ``"none"`` no-baseline sentinel); ``checks[].depth``/``gate_mode`` are
    valid; every ``checks[].profiles`` entry resolves to a declared profile;
    every id is a valid, ``check_id``-embeddable identifier.

    Structural/type errors in the YAML itself (unknown key, wrong type) fail
    immediately as a usage error; this command's own validation report only
    covers cross-reference/semantic issues on an already-well-formed block.

    \b
    Exit codes:
      0   Valid — no errors (warnings may still be present).
      1   One or more validation errors.
      64  Usage error (CONFIG is not readable YAML, or fails strict parsing).
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

    report = validate_project_targets(parsed)

    if fmt == "json":
        text = json.dumps(report.to_dict(), indent=2)
    else:
        lines = [f"project-targets validation: {config}"]
        if report.ok:
            lines.append("OK — no errors.")
        else:
            lines.append(f"FAILED — {len(report.errors)} error(s):")
            lines.extend(f"  - {e}" for e in report.errors)
        if report.warnings:
            lines.append(f"{len(report.warnings)} warning(s):")
            lines.extend(f"  - {w}" for w in report.warnings)
        text = "\n".join(lines)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0 if report.ok else 1)

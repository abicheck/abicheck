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

"""CLI — the ``build-output`` group (G30 P1.1, ADR-047 §2/§11.1).

``build-output validate`` checks a project-produced ``abicheck-build/``
directory's ``build-output.json`` against the ADR-047 §11.1 validation rules
(declared header roots non-empty, binary digests match, evidence projection
is safely 'declared', no evidence pack shared across targets) before any
G30 P1 primitive (``resolve-baseline``/``check-target``, not built yet) would
consume it. Split out of :mod:`abicheck.cli` per the sibling-module pattern;
imported for side-effect at the bottom of :mod:`abicheck.cli` so
``@main.group``/``@build_output_group.command`` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .buildsource.build_output import validate_build_output
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option


@main.group("build-output")
def build_output_group() -> None:
    """Validate a project-produced ``abicheck-build/`` directory.

    \b
    Subcommands:
      validate  Check build-output.json + its referenced artifacts.
    """


@build_output_group.command("validate")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
@verbose_option
def build_output_validate_cmd(
    directory: Path,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Validate DIRECTORY's build-output.json (ADR-047 §11.1).

    Checks: every declared public/generated header root is non-empty; every
    target's binary exists and matches its digests[] entry; evidence.projection
    is 'declared' for every target that has evidence ('inferred' is
    schema-reserved for a future attribution mechanism and is always
    rejected); no evidence pack is referenced by more than one target, and a
    referenced pack's own identity (manifest.library or a tagged TU's
    target_id) agrees with the specific target using it.

    \b
    Exit codes:
      0   Valid — no errors (warnings may still be present).
      1   One or more validation errors.
      64  Usage error (DIRECTORY is not a readable build-output.json).
    """
    _setup_verbosity(verbose)

    try:
        report = validate_build_output(directory)
    except (FileNotFoundError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc

    if fmt == "json":
        text = json.dumps(report.to_dict(), indent=2)
    else:
        lines = [f"build-output validation: {directory}"]
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

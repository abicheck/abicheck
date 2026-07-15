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

"""CLI — ``doctor`` command (environment + per-binary setup diagnostic).

Closes a usability gap: diagnosing "why didn't abicheck see the header I
expected" or "is castxml even installed" meant reading source or trial and
error. ``doctor`` answers, in one command:

* which AST frontend (castxml/clang) is selected and its version;
* which external tools (castxml, gcc/g++, clang, debuginfod) are on PATH;
* which project ``.abicheck.yml`` would be picked up;
* — and, given a binary, the same data-source resolution ``dump
  --show-data-sources`` reports (debug artifacts found, header match rate).

Split out of :mod:`abicheck.cli` to keep that module under the AI-readiness
file-size limit. Imported for side-effect at the bottom of :mod:`abicheck.cli`
so the ``@main.command("doctor")`` decorator runs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import click

from .cli import main
from .cli_helpers_compare import discover_project_config


def _tool_status(name: str, bin_name: str | None = None) -> str:
    path = shutil.which(bin_name or name)
    return f"{name}: {path}" if path else f"{name}: not found on PATH"


@main.command("doctor")
@click.argument(
    "binary",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-H",
    "--header",
    "headers",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Public header(s)/dir(s) to factor into the per-binary diagnostic "
    "(same meaning as `dump -H`). Only affects output when BINARY is given.",
)
def doctor_command(binary: Path | None, headers: tuple[Path, ...]) -> None:
    """Diagnose the local toolchain/config setup, and optionally a binary.

    With no arguments, reports environment-level facts only (frontend
    selection, tool availability, discovered project config). Given BINARY,
    also reports the same debug-artifact / header-match data-source
    diagnostic as `dump --show-data-sources`.
    """
    from .dumper import (
        _castxml_available,
        _castxml_version_note,
        _clang_available,
        _resolve_header_backend,
    )

    click.echo("== AST frontend ==")
    resolved_backend = _resolve_header_backend(os.environ.get("ABICHECK_AST_FRONTEND"))
    click.echo(
        f"  selected: {resolved_backend} (ABICHECK_AST_FRONTEND={os.environ.get('ABICHECK_AST_FRONTEND') or '(unset)'})"
    )
    click.echo(f"  {_tool_status('castxml')}")
    if _castxml_available():
        note = _castxml_version_note()
        if note:
            click.echo(f"  castxml note: {note}")
    click.echo(f"  {_tool_status('clang')}")
    if not _castxml_available() and not _clang_available():
        click.echo(
            "  WARNING: neither castxml nor clang found — header-based (L2) "
            "comparisons will not be available."
        )

    click.echo()
    click.echo("== Compiler toolchain ==")
    click.echo(f"  {_tool_status('gcc')}")
    click.echo(f"  {_tool_status('g++')}")
    click.echo(f"  {_tool_status('clang++')}")

    click.echo()
    click.echo("== debuginfod ==")
    debuginfod_urls = os.environ.get("DEBUGINFOD_URLS", "")
    click.echo(
        f"  DEBUGINFOD_URLS: {debuginfod_urls or '(unset — debuginfod network resolution disabled unless --debuginfod-url is passed)'}"
    )

    click.echo()
    click.echo("== project config ==")
    cfg_path = discover_project_config()
    click.echo(
        f"  .abicheck.yml: {cfg_path or '(none found — searched upward from cwd)'}"
    )

    if binary is not None:
        click.echo()
        click.echo(f"== data sources: {binary} ==")
        from .cli_datasources import print_data_sources

        print_data_sources(binary, bool(headers))

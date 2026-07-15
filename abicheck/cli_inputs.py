# Copyright 2026 Nikolay Petrov
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

"""CLI — the ``inputs`` command group (Flow-2 ``abicheck_inputs/`` pack tools).

``inputs validate`` runs the pre-merge pack checks from
``buildsource/inputs_validate.py`` (ADR-038 C.8, recommendation #28): manifest
validity, fact-set version, duplicate TU identities, and per-family coverage
completeness — before the pack is folded into an authoritative baseline.

``inputs compact`` merges a pack's many per-TU ``source_facts/*.jsonl`` files
into one, optionally gzip-compressed (ADR-038 C.9, recommendation P1 #21-22)
— a post-build size/transfer optimization that never changes the decoded
facts a later ``merge``/``inputs validate`` sees.

Split out of :mod:`abicheck.cli` to keep that module under the AI-readiness
file-size limit; imported for side-effect at the bottom of :mod:`abicheck.cli`
so the ``@main.group(...)`` decorator registers the command.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .cli import main
from .cli_options import output_options


@main.group("inputs")
def inputs_group() -> None:
    """Tools for a build-emitted ``abicheck_inputs/`` pack (Flow 2, ADR-035 D5)."""


@inputs_group.command("validate")
@click.argument("pack", type=click.Path(path_type=Path))
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
def validate_cmd(pack: Path, fmt: str, output: Path | None) -> None:
    """Validate a Flow-2 ``abicheck_inputs/`` pack before merging it.

    \b
    PACK is a directory produced by a build (the ``abicheck-cc`` wrapper, the
    Clang facts plugin, or a hand-written producer) — the directory containing
    ``manifest.json`` and ``source_facts/``.

    Checks manifest validity, fact-set version compatibility, duplicate TU
    identities, per-family collection coverage, and public-surface emptiness.
    A ``partial``/``failed`` mandatory fact family is reported as a warning —
    per ADR-038 C.8 its absence from other findings must not be read as proof
    nothing changed.

    \b
    Exit codes: 0 clean, 1 warnings only, 2 validation errors,
    64 PACK is not a readable Flow-2 pack.
    """
    from .buildsource.inputs_validate import validate_inputs_pack

    try:
        report = validate_inputs_pack(pack)
    except (FileNotFoundError, ValueError) as exc:
        # Click's UsageError exits 2, which the root group remaps to 64 (see
        # cli.py's _AbicheckGroup) so an invalid PACK path is never mistaken
        # for a validation-error exit.
        raise click.UsageError(str(exc)) from None

    payload = report.to_dict()
    if fmt == "json":
        text = json.dumps(payload, indent=2, sort_keys=True)
        if output:
            output.write_text(text + "\n", encoding="utf-8")
        else:
            click.echo(text)
    else:
        lines = [f"abicheck_inputs pack: {report.root}", f"  TUs: {report.tu_count}"]
        if report.fact_set:
            lines.append(
                "  fact_set: "
                f"{report.fact_set.get('name')} v{report.fact_set.get('version')} "
                f"({report.fact_set.get('producer')} {report.fact_set.get('producer_version')}, "
                f"{report.fact_set.get('compiler_family')} {report.fact_set.get('compiler_version')})"
            )
        else:
            lines.append("  fact_set: (none reported)")
        for err in report.errors:
            lines.append(f"  ERROR: {err}")
        for warn in report.warnings:
            lines.append(f"  WARNING: {warn}")
        if not report.errors and not report.warnings:
            lines.append("  OK — no issues found.")
        text = "\n".join(lines)
        if output:
            output.write_text(text + "\n", encoding="utf-8")
        else:
            click.echo(text)

    if report.errors:
        sys.exit(2)
    if report.warnings:
        sys.exit(1)


@inputs_group.command("compact")
@click.argument("pack", type=click.Path(path_type=Path))
@click.option(
    "--output-filename",
    default="compacted.jsonl",
    show_default=True,
    help="Merged source-fact filename, written under PACK/source_facts/.",
)
@click.option(
    "--compress/--no-compress",
    default=False,
    help="Gzip the merged file (a .gz suffix is added if not already present).",
)
@click.option(
    "--keep-originals",
    is_flag=True,
    default=False,
    help=(
        "Keep the per-TU files that were merged instead of deleting them "
        "(default: delete, since keeping both means a later ingest double-"
        "counts every TU)."
    ),
)
def compact_cmd(
    pack: Path, output_filename: str, compress: bool, keep_originals: bool
) -> None:
    """Merge PACK's many per-TU ``source_facts/*.jsonl`` files into one.

    \b
    PACK is a directory produced by a build (the ``abicheck-cc`` wrapper, the
    Clang facts plugin, or a hand-written producer) — the directory containing
    ``manifest.json`` and ``source_facts/``.

    A post-build step only (a still-writing parallel compile could race a
    mid-build merge): run it once the build has finished, before shipping the
    pack. Re-serializes every discovered TU through the same canonical JSON
    form the writers already use, so a later ``merge``/``inputs validate``
    decodes the exact same facts either way (ADR-038 C.9 execution-policy
    invariance) — only the on-disk file count/size changes.

    \b
    Exit codes: 0 success, 64 PACK is not a readable Flow-2 pack.
    """
    from .buildsource.inputs_emit import compact_inputs_pack
    from .buildsource.inputs_pack import load_inputs_manifest

    try:
        load_inputs_manifest(pack)
    except (FileNotFoundError, ValueError) as exc:
        raise click.UsageError(str(exc)) from None

    diagnostics: list[str] = []
    try:
        out = compact_inputs_pack(
            pack,
            output_filename=output_filename,
            compress=compress,
            remove_originals=not keep_originals,
            diagnostics=diagnostics,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None
    click.echo(f"Compacted source facts written to {out}")
    for diag in diagnostics:
        click.echo(f"  note: {diag}")

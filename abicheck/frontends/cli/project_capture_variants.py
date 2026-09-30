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

"""``abicheck project capture-variants`` -- ADR-062 D9 / storage-format-v2
A1.6's CLI surface.

A subcommand of the existing ``project`` group, not a new root command
(``AGENTS.md``'s admission bar: this is advanced multi-target CI-integration
surface, the class ``project`` exists to hold). Defined here and attached by
``cli_project.py`` with ``project_group.add_command`` rather than decorated
onto the group from this module: this module imports nothing from
``abicheck.cli``/``cli_project``/``frontends.cli.runtime``, so it stays out
of the CLI-registration import cycle (``IMPORT_CYCLE_ALLOWLIST``).

The command only translates: operands into
``workflows.bundle_variants_capture`` inputs, its typed errors into usage
errors (exit 64), and its result into a text/JSON report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ...config_paths import discover_project_config
from ...workflows.bundle_variants_capture import (
    SkippedVariant,
    VariantCaptureError,
    VariantCaptureInput,
    VariantCapturePlan,
    capture_variants,
    load_bundle_variants_config,
    plan_variant_capture,
)
from .options.export import ExportSet, export_options

__all__ = ["capture_variants_cmd", "parse_variant_assignments"]


def parse_variant_assignments(
    values: tuple[str, ...], flag: str
) -> list[tuple[str, Path]]:
    """``NAME=PATH`` tokens, in order. A malformed token is a usage error."""
    out: list[tuple[str, Path]] = []
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            raise click.UsageError(f"{flag} expects NAME=PATH, got {value!r}")
        out.append((name, Path(path)))
    return out


def _inputs(
    variants: tuple[str, ...], headers: tuple[str, ...], includes: tuple[str, ...]
) -> list[VariantCaptureInput]:
    pairs = parse_variant_assignments(variants, "--variant")
    names = {name for name, _ in pairs}
    extra: dict[str, dict[str, list[Path]]] = {}
    for flag, values in (
        ("--variant-header", headers),
        ("--variant-include", includes),
    ):
        for name, path in parse_variant_assignments(values, flag):
            if name not in names:
                raise click.UsageError(f"{flag} {name}=...: no --variant {name}=PATH")
            extra.setdefault(name, {}).setdefault(flag, []).append(path)
    return [
        VariantCaptureInput(
            name=name,
            path=path,
            headers=tuple(extra.get(name, {}).get("--variant-header", ())),
            includes=tuple(extra.get(name, {}).get("--variant-include", ())),
        )
        for name, path in pairs
    ]


def _plan_payload(plan: VariantCapturePlan) -> dict[str, Any]:
    return {
        "variants": {
            p.spec.name: {
                "required": p.spec.required,
                "declared": p.spec.declared(),
                "input": str(p.input.path),
                "libraries": sorted(p.libraries),
            }
            for p in plan.planned
        },
        "skipped": [{"variant": s.name, "reason": s.reason} for s in plan.skipped],
    }


def _text(payload: dict[str, Any], *, dry_run: bool) -> str:
    lines = [
        "capture-variants plan (dry run):"
        if dry_run
        else f"package written: {payload['output']}"
    ]
    variants = payload.get("variants")
    for name, info in variants.items() if isinstance(variants, dict) else ():
        libs = ", ".join(info.get("libraries", [])) or "(none)"
        lines.append(f"  {name}: {libs}")
        for label in ("declared", "captured"):
            coords = info.get(label)
            if coords:
                shown = ", ".join(f"{k}={v}" for k, v in sorted(coords.items()))
                lines.append(f"    {label}: {shown}")
    for skipped in payload.get("skipped") or []:
        lines.append(
            f"  skipped optional variant {skipped['variant']}: {skipped['reason']}"
        )
    return "\n".join(lines)


def _emit(exports: ExportSet, payload: dict[str, Any], *, dry_run: bool) -> None:
    rendered: dict[str, str] = {}
    for target in exports.documents:
        if target.fmt not in rendered:
            rendered[target.fmt] = (
                json.dumps(payload, indent=2, sort_keys=True)
                if target.fmt == "json"
                else _text(payload, dry_run=dry_run)
            )
        if target.destination is None:
            click.echo(rendered[target.fmt])
        else:
            try:
                target.destination.parent.mkdir(parents=True, exist_ok=True)
                target.destination.write_text(rendered[target.fmt], encoding="utf-8")
            except OSError as exc:
                raise click.ClickException(
                    f"Cannot write to {target.destination}: {exc}"
                ) from exc


def _warn_skipped(skipped: SkippedVariant) -> None:
    click.echo(
        f"Warning: optional variant {skipped.name!r} skipped: {skipped.reason}",
        err=True,
    )


@click.command("capture-variants")
@click.option(
    "--variant",
    "variants",
    metavar="NAME=PATH",
    multiple=True,
    required=True,
    help=(
        "Capture declared bundle variant NAME from PATH: a binary, a "
        "directory of binaries/snapshots, or a single snapshot (the same "
        "operand shape compare's release fan-out takes). Repeatable, one per "
        "variant. NAME must be declared under .abicheck.yml bundle_variants:."
    ),
)
@click.option(
    "--package",
    "package_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Directory to write the one multi-variant ProjectSnapshot package to. "
        "Must not exist or be empty; written atomically, so a failed capture "
        "leaves nothing behind."
    ),
)
@click.option(
    "--config",
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "The .abicheck.yml whose bundle_variants: block declares the "
        "variants. Default: the nearest project config at or above the "
        "current directory."
    ),
)
@click.option(
    "--variant-header",
    "variant_headers",
    metavar="NAME=PATH",
    multiple=True,
    help="A public header (or header directory) for variant NAME's dumps. Repeatable.",
)
@click.option(
    "--variant-include",
    "variant_includes",
    metavar="NAME=DIR",
    multiple=True,
    help="An include directory for variant NAME's header parse. Repeatable.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Resolve and check the plan (every required variant reachable, every "
        "input discoverable) and report it, without capturing or writing."
    ),
)
@export_options(["text", "json"], default_format="text")
def capture_variants_cmd(
    variants: tuple[str, ...],
    package_dir: Path,
    config: Path | None,
    variant_headers: tuple[str, ...],
    variant_includes: tuple[str, ...],
    dry_run: bool,
    exports: ExportSet,
) -> None:
    """Capture every declared bundle variant into one ProjectSnapshot package.

    Reads the ``bundle_variants:`` block of the project config (variant
    name -> ``target_triple``, ``compiler_family``, ``feature_toggles``,
    ``required``) and runs one capture per ``--variant NAME=PATH``. Each
    variant becomes one VariantRef in the package: ``declared`` from the
    config, ``captured`` from what the dumps actually recorded (DWARF
    producer toolchain, target architecture, binary format) -- two maps,
    never merged.

    \b
    A required variant with no input, a missing path, nothing to capture,
    or a failed capture is an error before anything is written. An optional
    variant in any of those states is skipped and reported; the package
    then carries no VariantRef for it.

    Compare two such packages with ``abicheck compare OLD NEW`` (select a
    variant with ``--variant old=ID``/``new=ID``); the JSON report's
    ``comparison_scope.variant_pairing`` pairs both packages' variants.

    \b
    Exit codes: 0 package written (or plan valid under --dry-run) · 64 usage
    error (bad config, unknown/unreachable required variant, failed
    required capture, non-empty --package).
    """
    config_path = config if config is not None else discover_project_config()
    if config_path is None:
        raise click.UsageError(
            "no project config found; pass --config .abicheck.yml with a "
            "bundle_variants: block"
        )
    inputs = _inputs(variants, variant_headers, variant_includes)
    try:
        spec = load_bundle_variants_config(config_path)
        plan = plan_variant_capture(spec, inputs)
        for skipped in plan.skipped:
            _warn_skipped(skipped)
        if dry_run:
            _emit(exports, _plan_payload(plan), dry_run=True)
            return
        result = capture_variants(plan, package_dir, on_skip=_warn_skipped)
    except VariantCaptureError as exc:
        raise click.UsageError(str(exc)) from exc
    _emit(exports, result.to_dict(), dry_run=False)

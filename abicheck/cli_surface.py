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

"""CLI — ``surface-report`` command (ADR-025 A1).

Emits descriptive structural facts about *one* library's public ABI surface
(no diff): header→symbol coverage, undocumented-export ratio, type fan-in, and
per-header cohesion. Split out of :mod:`abicheck.cli`; imported for side-effect
at the bottom of that module so the ``@main.command`` decorator runs.

This command is purely descriptive — it never computes a verdict or affects an
exit code beyond success/usage-error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .cli import _write_or_echo, main

if TYPE_CHECKING:
    from .idioms import IdiomTag


@main.command("surface-report")
@click.argument("library", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-H",
    "--header",
    "headers",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Public header file or directory (repeatable). Enables "
    "header-aware coverage metrics.",
)
@click.option(
    "-I",
    "--include",
    "includes",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Additional include directory passed to the header parser.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--top",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="How many highest-fan-in types to list.",
)
@click.option(
    "--idioms/--no-idioms",
    default=False,
    show_default=True,
    help="Recognise and report API idioms (opaque pointer, PIMPL, handle, "
    "factory, create/destroy, callback).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write report to a file (default: stdout).",
)
def surface_report_cmd(
    library: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    fmt: str,
    top: int,
    idioms: bool,
    output: Path | None,
) -> None:
    """Report structural metrics for a library's public ABI surface.

    LIBRARY is a shared library (ELF/PE/Mach-O) or an ``.abi.json`` snapshot.

    \b
    Example:
      abicheck surface-report libfoo.so -H include/ --format json -o surface.json
    """
    from .service import expand_header_inputs, resolve_input
    from .surface_graph import compute_surface_metrics

    header_paths = expand_header_inputs(list(headers)) if headers else []
    try:
        snap = resolve_input(
            library,
            headers=header_paths,
            includes=list(includes),
        )
    except Exception as exc:  # noqa: BLE001 — surface as a clean CLI error
        raise click.ClickException(f"Cannot read '{library}': {exc}") from exc

    metrics = compute_surface_metrics(snap, top_n=top)

    idiom_tags: dict[str, list[IdiomTag]] = {}
    if idioms:
        from .idioms import recognise_idioms
        from .surface_graph import build_surface_graph

        idiom_tags = recognise_idioms(build_surface_graph(snap))

    if fmt == "json":
        payload = metrics.to_dict()
        if idioms:
            payload["idioms"] = {
                name: [
                    {
                        "idiom": t.idiom.value,
                        "confidence": t.confidence.value,
                        "evidence": t.evidence,
                        "layout_signature": t.layout_signature,
                        "hidden_pointee": t.hidden_pointee,
                        "definition_hidden": t.definition_hidden,
                    }
                    for t in tags
                ]
                for name, tags in idiom_tags.items()
            }
        _write_or_echo(output, json.dumps(payload, indent=2))
        return

    _write_or_echo(output, _render_text(metrics, idiom_tags if idioms else None))


def _render_text(
    metrics: object, idiom_tags: dict[str, list[IdiomTag]] | None = None
) -> str:
    from .surface_graph import SurfaceMetrics

    assert isinstance(metrics, SurfaceMetrics)
    lines: list[str] = []
    lines.append(f"Surface report: {metrics.library} {metrics.version}".rstrip())
    lines.append(f"  evidence tier:        {metrics.evidence_tier}")
    lines.append(f"  public functions:     {metrics.public_functions}")
    lines.append(f"  public variables:     {metrics.public_variables}")
    lines.append(f"  public types:         {metrics.public_types}")
    lines.append(f"  public enums:         {metrics.public_enums}")
    lines.append(f"  exported symbols:     {metrics.exported_symbols}")
    pct = metrics.undocumented_export_ratio * 100.0
    lines.append(
        f"  undocumented exports: {metrics.undocumented_exports} "
        f"({pct:.1f}% of exported surface)"
    )
    if metrics.top_fan_in:
        lines.append("  highest fan-in types (blast radius if changed):")
        for name, count in metrics.top_fan_in:
            lines.append(f"    {count:>4}  {name}")
    if metrics.header_coverage:
        lines.append("  header coverage (declared / exported / clusters):")
        for hc in metrics.header_coverage:
            lines.append(
                f"    {hc.declared:>4} / {hc.exported:>4} / {hc.cohesion_clusters:>2}  "
                f"{hc.header}"
            )
    if idiom_tags:
        lines.append("  idioms recognised:")
        for name in sorted(idiom_tags):
            for tag in idiom_tags[name]:
                lines.append(
                    f"    {tag.idiom.value:<16} {name}  [{tag.confidence.value}]"
                )
    return "\n".join(lines) + "\n"

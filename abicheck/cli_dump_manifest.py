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

"""CLI — the ``plan --dump-manifest`` diagnostic command (ADR-050 D3, G32
Phase B).

Split out of :mod:`abicheck.cli` (root ``CLAUDE.md``'s "larger command ->
sibling module" convention) to keep that module under the AI-readiness
file-size limit. Imported for side-effect at the bottom of
:mod:`abicheck.cli` so the ``@main.command("plan")`` decorator runs.

``plan`` parses and normalizes a ``--dump-manifest`` document and prints its
``scope_fingerprint`` (ADR-050 D1) -- genuinely computable from the manifest
document alone, no compiler invocation needed. It never runs castxml/clang
and never prints a ``profile_fingerprint``: that fingerprint's ``-I``
component is a ``-MD``-depfile digest that only exists once a real L2
extraction actually runs, so promising it here would either be impossible to
compute honestly or invent a pre-extraction surrogate the compare gate would
later have to treat as authoritative. Cheap to run in CI before committing to
a full ``dump``/``compare`` invocation; a real ``profile_fingerprint`` check
still needs one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .cli import _safe_write_output, main
from .cli_options import verbose_option


def _manifest_to_dict(manifest: Any) -> dict[str, Any]:
    """Render a parsed :class:`~abicheck.dump_manifest.DumpManifest` as a
    plain, JSON-serializable dict — the "normalized manifest" this command
    prints (every relative path already resolved against the manifest
    file's own directory)."""
    return {
        "compiler": manifest.compiler,
        "target": manifest.target,
        "frontend_context": manifest.frontend_context,
        "public_header_paths": [str(p) for p in manifest.public_header_paths],
        "public_header_dirs": [str(p) for p in manifest.public_header_dirs],
        "roots": [str(p) for p in manifest.roots],
        "translation_units": [
            {
                "name": tu.name,
                "forced_includes": [str(p) for p in tu.forced_includes],
                "includes": [
                    {"path": str(inc.path), "project_owned": inc.project_owned}
                    for inc in tu.includes
                ],
                "required": tu.required,
                "contributes_to_abi": tu.contributes_to_abi,
            }
            for tu in manifest.translation_units
        ],
    }


def _render_json(manifest_dict: dict[str, Any], scope_fingerprint: str | None) -> str:
    return json.dumps(
        {"manifest": manifest_dict, "scope_fingerprint": scope_fingerprint},
        indent=2,
    )


def _render_text(manifest_dict: dict[str, Any], scope_fingerprint: str | None) -> str:
    lines = [
        f"compiler: {manifest_dict['compiler']}",
        f"target: {manifest_dict['target'] or '(none)'}",
        f"frontend_context: {manifest_dict['frontend_context']}",
        f"roots: {', '.join(manifest_dict['roots'])}",
    ]
    if manifest_dict["public_header_paths"]:
        lines.append(
            "public_header_paths: " + ", ".join(manifest_dict["public_header_paths"])
        )
    if manifest_dict["public_header_dirs"]:
        lines.append(
            "public_header_dirs: " + ", ".join(manifest_dict["public_header_dirs"])
        )
    lines.append("translation_units:")
    for tu in manifest_dict["translation_units"]:
        lines.append(
            f"  - {tu['name']} (required={tu['required']}, "
            f"contributes_to_abi={tu['contributes_to_abi']})"
        )
        if tu["forced_includes"]:
            lines.append(f"      forced_includes: {', '.join(tu['forced_includes'])}")
        if tu["includes"]:
            inc_repr = ", ".join(
                f"{inc['path']}{' [project_owned]' if inc['project_owned'] else ''}"
                for inc in tu["includes"]
            )
            lines.append(f"      includes: {inc_repr}")
    lines.append("")
    lines.append(f"scope_fingerprint: {scope_fingerprint or '(none)'}")
    lines.append(
        "profile_fingerprint: (not computed -- requires a real L2 extraction; "
        "run `dump --dump-manifest` or `compare --dump-manifest` instead)"
    )
    return "\n".join(lines)


@main.command("plan")
@click.option(
    "--dump-manifest",
    "dump_manifest_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The --dump-manifest YAML document to parse and normalize.",
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
    "-o", "--output", type=click.Path(path_type=Path), default=None,
    help="Write output to this path (default: stdout).",
)
@verbose_option
def plan_cmd(
    dump_manifest_path: Path, fmt: str, output: Path | None, verbose: bool
) -> None:
    """Parse and normalize a --dump-manifest document without extracting.

    Prints the normalized manifest and its ``scope_fingerprint`` (ADR-050
    D1) -- computed from the manifest document alone, no compiler
    invocation. Never prints a ``profile_fingerprint``, which requires a
    real L2 extraction to exist at all.

    \b
    Examples:
      abicheck plan --dump-manifest manifest.yaml
      abicheck plan --dump-manifest manifest.yaml --format json -o plan.json
    """
    from .cli import _setup_verbosity

    _setup_verbosity(verbose)

    from .comparability import compute_extraction_contract
    from .dump_manifest import load_manifest
    from .errors import ManifestValidationError

    try:
        manifest = load_manifest(dump_manifest_path)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc

    contract = compute_extraction_contract(
        declared_headers=list(manifest.roots),
        public_header_paths=list(manifest.public_header_paths),
        public_header_dirs=list(manifest.public_header_dirs),
        l2_frontend_ran=False,
    )
    scope_fingerprint = contract.scope_fingerprint if contract is not None else None

    manifest_dict = _manifest_to_dict(manifest)
    text = (
        _render_json(manifest_dict, scope_fingerprint)
        if fmt == "json"
        else _render_text(manifest_dict, scope_fingerprint)
    )
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

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

"""CLI — ``plugin-check`` host↔plugin load-contract command (gap G5).

Answers the plugin-load direction of ``appcompat``: "does plugin v2 still
satisfy host H's required entrypoints?" A host ``dlopen``s a plugin and
resolves a fixed set of entry-point symbols via ``dlsym``; whether a plugin's
symbol churn breaks *that host* depends on the host's required set, not the
library-wide verdict.

Split out of :mod:`abicheck.cli` and imported for side-effect at the bottom of
that module so the ``@main.command("plugin-check")`` decorator runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .checker import Verdict
from .cli import (
    _resolve_input,
    _safe_write_output,
    _setup_verbosity,
    main,
)
from .cli_params import POLICY_FILE_PARAM, _load_suppression_and_policy


def _load_required_entrypoints(
    require: tuple[str, ...], host_contract: Path | None,
) -> set[str]:
    """Collect the host's required entrypoints from ``--require`` flags and an
    optional manifest file (one symbol per line; ``#`` comments and blanks
    ignored)."""
    entrypoints: set[str] = set(require)
    if host_contract is not None:
        for raw in host_contract.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                entrypoints.add(line)
    return entrypoints


def _render_plugin_result_markdown(result: object) -> str:
    from .appcompat import PluginHostContractResult
    assert isinstance(result, PluginHostContractResult)
    lines = [
        "# Plugin host-contract check",
        "",
        f"- **Old plugin:** {result.old_plugin}",
        f"- **New plugin:** {result.new_plugin}",
        f"- **Required entrypoints:** {len(result.required_entrypoints)}",
        f"- **Entrypoint coverage:** {result.coverage:.1f}%",
        f"- **Verdict:** {result.verdict.value.upper()}",
        "",
    ]
    if result.missing_entrypoints:
        lines.append("## Missing entrypoints (host load break)")
        lines.append("")
        lines += [f"- `{sym}`" for sym in result.missing_entrypoints]
        lines.append("")
    if result.breaking_for_host:
        lines.append("## Incompatible changes affecting the host")
        lines.append("")
        lines += [
            f"- `{c.symbol}` — {c.kind.value}" for c in result.breaking_for_host
        ]
        lines.append("")
    if not result.missing_entrypoints and not result.breaking_for_host:
        lines.append("All required entrypoints are still satisfied by the new plugin.")
    return "\n".join(lines)


def _render_plugin_result_json(result: object) -> str:
    from .appcompat import PluginHostContractResult
    assert isinstance(result, PluginHostContractResult)
    return json.dumps(
        {
            "old_plugin": result.old_plugin,
            "new_plugin": result.new_plugin,
            "required_entrypoints": sorted(result.required_entrypoints),
            "missing_entrypoints": result.missing_entrypoints,
            "breaking_for_host": [
                {"symbol": c.symbol, "kind": c.kind.value}
                for c in result.breaking_for_host
            ],
            "coverage": result.coverage,
            "verdict": result.verdict.value,
        },
        indent=2,
    )


@main.command("plugin-check")
@click.argument("old_plugin", type=click.Path(exists=True, path_type=Path))
@click.argument("new_plugin", type=click.Path(exists=True, path_type=Path))
@click.option("-r", "--require", "require", multiple=True, metavar="SYMBOL",
              help="An entry-point symbol the host resolves from the plugin "
                   "(repeatable).")
@click.option("--host-contract", "host_contract",
              type=click.Path(exists=True, path_type=Path), default=None,
              help="Manifest file listing required entrypoints, one per line "
                   "(# comments allowed).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode (used only when dumping plugin binaries).")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML).")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="plugin_abi", show_default=True,
              help="Verdict policy; plugin_abi is the natural default for "
                   "in-process host/plugin builds.")
@click.option("--policy-file", "policy_file_path",
              type=POLICY_FILE_PARAM, default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def plugin_check_cmd(
    old_plugin: Path,
    new_plugin: Path,
    require: tuple[str, ...],
    host_contract: Path | None,
    lang: str,
    fmt: str,
    output: Path | None,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    verbose: bool,
) -> None:
    """Check whether a plugin upgrade still satisfies a host's load contract.

    A host ``dlopen``s a plugin and resolves a fixed set of entry-point symbols.
    Given the old and new plugin (binary or JSON snapshot) and the host's
    required entrypoints, report whether the new plugin still satisfies the
    host — the plugin-load direction of ``appcompat``.

    \b
    Examples:
      abicheck plugin-check plugin.v1.so plugin.v2.so -r plugin_init -r plugin_run
      abicheck plugin-check plugin.v1.so plugin.v2.so --host-contract host.syms

    \b
    Exit codes (host-scoped verdict):
      0  COMPATIBLE — the new plugin still satisfies the host
      2  API_BREAK — source-level break affecting a required entrypoint
      4  BREAKING — a required entrypoint was dropped or is ABI-incompatible
    """
    _setup_verbosity(verbose)

    entrypoints = _load_required_entrypoints(require, host_contract)
    if not entrypoints:
        raise click.UsageError(
            "No required entrypoints given. Provide --require SYMBOL (repeatable) "
            "and/or --host-contract FILE."
        )

    from .appcompat import check_plugin_host_contract

    old_snap = _resolve_input(old_plugin, [], [], "old", lang)
    new_snap = _resolve_input(new_plugin, [], [], "new", lang)
    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)

    result = check_plugin_host_contract(
        old_snap, new_snap, entrypoints,
        suppression=suppression, policy=policy, policy_file=pf,
    )

    text = _render_plugin_result_json(result) if fmt == "json" else _render_plugin_result_markdown(result)

    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.verdict == Verdict.BREAKING:
        sys.exit(4)
    elif result.verdict == Verdict.API_BREAK:
        sys.exit(2)

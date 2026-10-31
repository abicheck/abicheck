# SPDX-License-Identifier: Apache-2.0
"""The compare inputs ADR-091 Phase 5 moved from the command line into `.abicheck.yml`.

`consumers.manifests` and `consumers.required_symbols` take the place of
`--used-by-manifest` and `--required-symbol`; `output.view` takes the place of
`--view`. `run_compare` reads them through :func:`config_scope_inputs` after the
project config has been resolved, so a `--set` override reaches them the same
way it reaches every other key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def _resolve_config_path(item: str, cfg_path: Path | None) -> Path:
    """A ``consumers.manifests`` entry: relative to the config file that names it."""
    path = Path(item)
    if path.is_absolute() or cfg_path is None:
        return path
    return cfg_path.parent / path


def _resolve_config_required_symbol(item: str, cfg_path: Path | None) -> str:
    """A ``consumers.required_symbols`` entry; ``@FILE`` resolves like ``_resolve_config_path``."""
    if not item.startswith("@"):
        return item
    return "@" + str(_resolve_config_path(item[1:], cfg_path))


def _parse_config_view(tokens: list[str]) -> dict[str, object]:
    """``output.view`` tokens, parsed with the grammar the removed ``--view`` used."""
    from .options.compare_view import parse_view_tokens

    try:
        return parse_view_tokens(tuple(tokens))
    except ValueError as exc:
        raise click.UsageError(f"output.view: {exc}") from exc


def consumer_manifest_specs(manifests: tuple[Path, ...]) -> tuple[Any, ...]:
    """The consumer apps the manifests name, merged into the ``--used-by`` pipeline."""
    if not manifests:
        return ()
    from ...model.consumer_spec import parse_consumer_manifest

    try:
        return tuple(
            spec for path in manifests for spec in parse_consumer_manifest(path)
        )
    except ValueError as exc:
        raise click.UsageError(f"consumers.manifests: {exc}") from exc


def config_scope_inputs(
    project_cfg: Any,
    cfg_path: Path | None,
    used_by_manifests: tuple[Path, ...],
    required_symbols_opt: tuple[str, ...],
) -> tuple[tuple[Path, ...], tuple[str, ...], tuple[str, Any] | None]:
    """The run's consumer manifests, required symbols and parsed view.

    A config value wins over the empty command-line default; ``output.view``
    is ``None`` when the project sets none. *project_cfg* is ``None`` when no
    config file applies to the run.
    """
    if project_cfg is None:
        return used_by_manifests, required_symbols_opt, None
    manifests = (
        tuple(
            _resolve_config_path(item, cfg_path)
            for item in project_cfg.used_by_manifests
        )
        or used_by_manifests
    )
    symbols = (
        tuple(
            _resolve_config_required_symbol(item, cfg_path)
            for item in project_cfg.required_symbols
        )
        or required_symbols_opt
    )
    view = None
    if project_cfg.output_view:
        parsed = _parse_config_view(project_cfg.output_view)
        view = (str(parsed["report_mode"]), parsed["show_only"])
    return manifests, symbols, view

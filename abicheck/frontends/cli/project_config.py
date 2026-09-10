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

"""Resolving the project config (``.abicheck.yml``) for a ``compare`` run.

One owner, because both ``compare`` forms need the same answer and only one
of them could reach it: the ``--no-baseline`` audit dispatches before
``cli_compare_helpers`` runs, so importing the resolver from there would
close ``cli_compare_helpers -> commands.compare -> commands.compare_no_baseline
-> cli_compare_helpers``. Lifted here instead, where both import it and
neither imports the other.

That the audit could not reach it was not a tidiness problem: an
auto-discovered ``.abicheck.yml`` was invisible to it, so a malformed one
exited 0 where ordinary ``compare`` exits 64, and a valid ``scope.public:
false`` was silently dropped -- auditing a different surface than the same
directory's ``compare`` (Codex review, P1).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ...cli_helpers_compare import ResolvedCompareConfig

__all__ = ["resolve_project_compare_config"]


def resolve_project_compare_config(
    *,
    config: Path | None,
    severity_preset: str | None,
    scope_public_headers: bool,
) -> tuple[Path | None, object, ResolvedCompareConfig, str | None]:
    """Load the project config and merge CLI flags over it (CLI > config > default).

    ADR-037 D4: resolved *before* dispatch so both the single-file and the
    directory/package fan-out paths share one resolution. Auto-discovered from the
    current directory upward, overridable with ``--config``.

    The fourth element is the digest of the bytes the config was parsed from
    (``None`` when there is no config), captured by the same read so an
    ADR-049 receipt can prove *which revision* of the file supplied a value
    rather than only naming its path (Codex review, fresh evidence).

    ADR-068 D5 / Phase 7a: ``compare`` no longer has ``--debug-format``/
    ``--debuginfod``/``--debuginfod-url``/``--dwarf-only`` CLI flags (they
    were hidden, already fully config-backed duplicates of ``debug.format``/
    ``debug.debuginfod``/``debug.debuginfod_url``/``debug.dwarf_only``), so
    ``resolve_compare_config`` below is called with no ``cli_debug_format``/
    ``cli_dwarf_only``/``cli_debuginfod``/``cli_debuginfod_url`` override --
    its defaults (``None``/``None``/``None``/``None``) mean the config value
    (or the built-in default) always wins.
    """
    from ...cli_compare_options import _cli_flag
    from ...cli_helpers_compare import (
        discover_project_config,
        resolve_compare_config,
    )
    from ...workflows.extraction import load_build_config_with_digest

    cfg_path = config if config is not None else discover_project_config()
    cfg_sha: str | None = None
    try:
        project_cfg = None
        if cfg_path is not None:
            project_cfg, cfg_sha = load_build_config_with_digest(cfg_path)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    resolved_cfg = resolve_compare_config(
        project_cfg,
        cli_severity_preset=severity_preset,
        cli_scope_public=_cli_flag("scope_public_headers", scope_public_headers),
    )
    return cfg_path, project_cfg, resolved_cfg, cfg_sha

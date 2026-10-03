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

"""``compare``'s POST-manifest contract overlay, from project config.

``.abicheck.yml``'s ``contract.overlays.post_manifest`` (one-comparison-
product Phase 9c; ``buildsource/build_config_contract.py``) is the only
spelling: Phase 9d deleted ``--post-manifest``, which exits 64. This module
is the one place a ``compare`` route decides what to do with it:

- a single-pair ``compare`` applies it, a relative path resolved against the
  project root (``config_paths.project_root_for_config``);
- a route that cannot apply an overlay (the directory/package release
  fan-out, the ``--no-baseline`` audit) states on stderr that it was not
  applied. It is a property of the project, not of this invocation, so it is
  not a usage error there, and an unapplied narrowing overlay can only add
  findings, never hide one.

Split out of ``cli_compare_helpers.py``, which sits at its ADR-061
``no_growth`` baseline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = [
    "CONFIG_KEY",
    "note_unapplied_post_manifest",
    "post_manifest_allowlist_for",
    "resolve_post_manifest_path",
]

CONFIG_KEY = "contract.overlays.post_manifest"


def resolve_post_manifest_path(
    project_cfg: object, cfg_path: Path | None
) -> Path | None:
    """The configured manifest, project-root-relative paths resolved."""
    raw = getattr(project_cfg, "contract_post_manifest", None)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def note_unapplied_post_manifest(
    project_cfg: object, *, route: str, reason: str
) -> None:
    """For a *route* that applies no overlay: a stderr note when one is set."""
    if getattr(project_cfg, "contract_post_manifest", None):
        click.echo(
            f"Note: .abicheck.yml's {CONFIG_KEY} is not applied on {route} "
            f"({reason}); it applies to a single-pair compare.",
            err=True,
        )


def post_manifest_allowlist_for(
    project_cfg: object,
    cfg_path: Path | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> set[str] | None:
    """The committed public surface of the configured overlay, or ``None``.

    The manifest *is* the authoritative public surface, so this drives
    FilterNonPublicSurface directly (no header provenance needed) -- private
    ``__pp_*`` kernel churn is demoted. Union with the binaries' committed
    (``pp_*``) exports so a *removed* wrapper -- absent from a new manifest --
    stays in-surface instead of being silently demoted. A document that does
    not load is exit 64 naming the config key.
    """
    path = resolve_post_manifest_path(project_cfg, cfg_path)
    if path is None:
        return None
    from ...post_manifest import contract_scope_allowlist, load_manifest

    try:
        manifest = load_manifest(path)
    except (ValueError, OSError) as exc:
        raise click.UsageError(f"{CONFIG_KEY} {path}: {exc}") from exc
    return contract_scope_allowlist(manifest, old, new)

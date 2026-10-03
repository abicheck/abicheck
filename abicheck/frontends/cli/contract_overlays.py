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

"""``compare``'s POST-manifest contract overlay: flag or project config.

One-comparison-product Phase 9c gives ``--post-manifest`` a config home,
``.abicheck.yml``'s ``contract.overlays.post_manifest`` (see
``buildsource/build_config_contract.py``). This module is the one place a
``compare`` route decides which of the two applies:

- a single-pair ``compare`` applies the flag when given, else the config key
  (CLI > config, ADR-037 D4), with a relative config path resolved against
  the project root (``config_paths.project_root_for_config``);
- a route that cannot apply an overlay (the directory/package release
  fan-out, the ``--no-baseline`` audit) rejects the *flag* as a usage error
  and states on stderr that the *config key* was not applied. The flag used
  to be silently ignored on the release fan-out. A project-wide key is not a
  usage error there: it is a property of the project, not of this
  invocation, and an unapplied narrowing overlay can only add findings.

Split out of ``cli_compare_helpers.py``, which sits at its ADR-061
``no_growth`` baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = [
    "CONFIG_KEY",
    "PostManifestOverlay",
    "post_manifest_allowlist_for",
    "reject_or_note_unapplied_post_manifest",
    "resolve_post_manifest_overlay",
]

CONFIG_KEY = "contract.overlays.post_manifest"


@dataclass(frozen=True)
class PostManifestOverlay:
    """The manifest a run applies, and the spelling that selected it."""

    path: Path
    #: ``"--post-manifest"`` or :data:`CONFIG_KEY` -- named in every error
    #: about the document, so a user knows which input to fix.
    source: str


def _configured(project_cfg: object, cfg_path: Path | None) -> Path | None:
    raw = getattr(project_cfg, "contract_post_manifest", None)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def resolve_post_manifest_overlay(
    flag_path: Path | None, project_cfg: object, cfg_path: Path | None
) -> PostManifestOverlay | None:
    """The overlay a single-pair ``compare`` applies: flag, else config."""
    if flag_path is not None:
        return PostManifestOverlay(flag_path, "--post-manifest")
    configured = _configured(project_cfg, cfg_path)
    return None if configured is None else PostManifestOverlay(configured, CONFIG_KEY)


def reject_or_note_unapplied_post_manifest(
    flag_path: Path | None, project_cfg: object, *, route: str, reason: str
) -> None:
    """For a *route* that applies no overlay: a typed flag is exit 64, a
    configured key is a stderr note."""
    if flag_path is not None:
        raise click.UsageError(
            f"--post-manifest is not supported on {route}: {reason}."
        )
    if getattr(project_cfg, "contract_post_manifest", None):
        click.echo(
            f"Note: .abicheck.yml's {CONFIG_KEY} is not applied on {route} "
            f"({reason}); it applies to a single-pair compare.",
            err=True,
        )


def post_manifest_allowlist_for(
    flag_path: Path | None,
    project_cfg: object,
    cfg_path: Path | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> set[str] | None:
    """The committed public surface of the overlay a single-pair ``compare``
    applies (:func:`resolve_post_manifest_overlay`), or ``None``.

    The manifest *is* the authoritative public surface, so this drives
    FilterNonPublicSurface directly (no header provenance needed) -- private
    ``__pp_*`` kernel churn is demoted. Union with the binaries' committed
    (``pp_*``) exports so a *removed* wrapper -- absent from a new manifest --
    stays in-surface instead of being silently demoted. A document that does
    not load is exit 64 naming the input that selected it.
    """
    overlay = resolve_post_manifest_overlay(flag_path, project_cfg, cfg_path)
    if overlay is None:
        return None
    from ...post_manifest import contract_scope_allowlist, load_manifest

    try:
        manifest = load_manifest(overlay.path)
    except (ValueError, OSError) as exc:
        raise click.UsageError(f"{overlay.source} {overlay.path}: {exc}") from exc
    return contract_scope_allowlist(manifest, old, new)

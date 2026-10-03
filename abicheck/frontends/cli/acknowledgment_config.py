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

"""``compare``'s ADR-067 D5/D6 inputs, from ``.abicheck.yml``'s
``acknowledgment:`` block (``buildsource/build_config_acknowledgment.py``).

- ``unacknowledged_additions`` applies from any config, auto-discovered
  included: ``warn``/``block`` can only add a report section or raise a
  clean exit to ``1``, so a pull request setting it cannot hide anything.
- ``file`` -- the acknowledgment records -- applies **only from an
  explicitly named** ``--config``. A record accepts an addition, which can
  lower a ``block`` exit back to ``0``; an auto-discovered ``.abicheck.yml``
  is one the pull request under review can edit, so it is not trusted to
  supply records. A discovered value is noted on stderr and not loaded --
  the trust boundary ``contract_overlays.py`` states for
  ``contract.overlays``.
- A route that applies no acknowledgments (the directory/package release
  fan-out) says so on stderr when the block is set.

A relative ``file`` resolves against the project root.
"""

from __future__ import annotations

from pathlib import Path

import click

from ...errors import ValidationError
from ...workflows.acknowledgment_inputs import (
    CompareAcknowledgments,
    resolve_compare_acknowledgments,
)

__all__ = [
    "CONFIG_KEY",
    "compare_acknowledgments_for",
    "note_unapplied_acknowledgments",
]

CONFIG_KEY = "acknowledgment"


def _records_path(project_cfg: object, cfg_path: Path | None) -> Path | None:
    raw = getattr(getattr(project_cfg, "acknowledgment", None), "file", None)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def compare_acknowledgments_for(
    project_cfg: object,
    cfg_path: Path | None,
    policy_file: object,
    *,
    config_explicit: bool,
) -> CompareAcknowledgments:
    """The single-pair ``compare``'s records and gate policy.

    A records file that does not load is exit 64 naming the config key.
    """
    ack_cfg = getattr(project_cfg, "acknowledgment", None)
    records_path = _records_path(project_cfg, cfg_path)
    if records_path is not None and not config_explicit:
        click.echo(
            f"Note: the auto-discovered {cfg_path}'s {CONFIG_KEY}.file is not "
            "loaded: a discovered config is not trusted to accept findings. "
            "Name the config with --config to apply it.",
            err=True,
        )
        records_path = None
    try:
        return resolve_compare_acknowledgments(
            records_path,
            policy_file=policy_file,
            configured_action=getattr(ack_cfg, "unacknowledged_additions", None),
        )
    except ValidationError as exc:
        raise click.UsageError(f"{CONFIG_KEY}.file: {exc}") from exc


def note_unapplied_acknowledgments(project_cfg: object, *, route: str) -> None:
    """For a *route* that applies no acknowledgments: a stderr note when set."""
    if getattr(project_cfg, "acknowledgment", None) is not None:
        click.echo(
            f"Note: .abicheck.yml's {CONFIG_KEY} block is not applied on "
            f"{route}; it applies to a single-pair compare.",
            err=True,
        )

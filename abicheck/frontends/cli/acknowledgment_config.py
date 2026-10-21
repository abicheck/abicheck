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

"""``compare``'s ADR-067 D5/D6 inputs: acknowledgment records, the
``breaks`` policy, and the additions-review gate.

Sources, in the order they apply:

- ``--acknowledgments FILE`` names the records for this run. It is an
  operator's own explicit input, so it is trusted, and it overrides
  ``.abicheck.yml``'s ``acknowledgment.file``.
- ``.abicheck.yml``'s ``acknowledgment.file`` applies **only from an
  explicitly named** ``--config``. A record accepts an addition or a break,
  which can lower a gating exit back to ``0``; an auto-discovered
  ``.abicheck.yml`` is one the pull request under review can edit, so it is
  not trusted to supply records. A discovered value is noted on stderr and
  not loaded -- the trust boundary ``contract_overlays.py`` states for
  ``contract.overlays``.
- ``.abicheck.yml``'s ``acknowledgment.breaks`` (``gate``/``accept``) has the
  same trust rule as the records: ``accept`` loosens the exit code, so only an
  explicit ``--config`` may set it.
- ``acknowledgment.unacknowledged_additions`` applies from any config,
  auto-discovered included: ``warn``/``block`` can only add a report section
  or raise a clean exit to ``1``, so a pull request setting it cannot hide
  anything.
- The directory/package fan-out forwards the same values to every member's
  ``CompareRequest`` (:func:`release_acknowledgment_inputs`).

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
    "release_acknowledgment_inputs",
]

CONFIG_KEY = "acknowledgment"


def _records_path(
    project_cfg: object,
    cfg_path: Path | None,
    *,
    override_path: Path | None,
    config_explicit: bool,
) -> Path | None:
    """The records file this run loads, or ``None``.

    ``--acknowledgments`` wins outright. Otherwise the config key applies only
    from an explicit ``--config``; a discovered value is noted and dropped.
    """
    if override_path is not None:
        return override_path
    raw = getattr(getattr(project_cfg, "acknowledgment", None), "file", None)
    if not raw:
        return None
    if not config_explicit:
        _note_untrusted_config_key(cfg_path, "file")
        return None
    path = Path(raw)
    if path.is_absolute() or cfg_path is None:
        return path
    from ...config_paths import project_root_for_config

    return project_root_for_config(cfg_path) / path


def _configured_breaks(
    project_cfg: object, cfg_path: Path | None, *, config_explicit: bool
) -> str | None:
    """``acknowledgment.breaks`` when the config is explicit, else ``None``."""
    breaks = getattr(getattr(project_cfg, "acknowledgment", None), "breaks", None)
    if breaks is None:
        return None
    if not config_explicit:
        _note_untrusted_config_key(cfg_path, "breaks")
        return None
    return str(breaks)


def compare_acknowledgments_for(
    project_cfg: object,
    cfg_path: Path | None,
    policy_file: object,
    *,
    config_explicit: bool,
    override_path: Path | None = None,
) -> CompareAcknowledgments:
    """The single-pair ``compare``'s records and gate policy.

    A records file that does not load is exit 64 naming the config key (or
    the flag). Review warnings the records owe are echoed to stderr.
    """
    ack_cfg = getattr(project_cfg, "acknowledgment", None)
    records_path = _records_path(
        project_cfg,
        cfg_path,
        override_path=override_path,
        config_explicit=config_explicit,
    )
    try:
        acks = resolve_compare_acknowledgments(
            records_path,
            policy_file=policy_file,
            configured_action=getattr(ack_cfg, "unacknowledged_additions", None),
            configured_breaks=_configured_breaks(
                project_cfg, cfg_path, config_explicit=config_explicit
            ),
        )
    except ValidationError as exc:
        raise click.UsageError(f"{_source_label(override_path)}: {exc}") from exc
    for warning in acks.warnings:
        click.echo(f"Warning: {warning}", err=True)
    return acks


def release_acknowledgment_inputs(
    project_cfg: object,
    cfg_path: Path | None,
    *,
    config_explicit: bool,
    override_path: Path | None = None,
) -> dict[str, object]:
    """The directory/package fan-out's ``CompareRequest`` fields, under the
    same trust rules as :func:`compare_acknowledgments_for`; each member's
    ``run_compare`` resolves them (and a ``--policy`` block's precedence) the
    way a single-pair compare does."""
    records_path = _records_path(
        project_cfg,
        cfg_path,
        override_path=override_path,
        config_explicit=config_explicit,
    )
    if records_path is not None and not records_path.is_file():
        raise click.UsageError(
            f"{_source_label(override_path)}: no such file: {records_path}"
        )
    ack_cfg = getattr(project_cfg, "acknowledgment", None)
    return {
        "acknowledgments_path": records_path,
        "acknowledgment_unacknowledged_additions": getattr(
            ack_cfg, "unacknowledged_additions", None
        ),
        "acknowledgment_breaks": _configured_breaks(
            project_cfg, cfg_path, config_explicit=config_explicit
        ),
    }


def _source_label(override_path: Path | None) -> str:
    """The name a usage error gives the input it is about: the flag, or the key."""
    return "--acknowledgments" if override_path is not None else f"{CONFIG_KEY}.file"


def _note_untrusted_config_key(cfg_path: Path | None, key: str) -> None:
    click.echo(
        f"Note: the auto-discovered {cfg_path}'s {CONFIG_KEY}.{key} is not "
        "loaded: a discovered config is not trusted to accept findings or "
        "loosen the gate. Name the config with --config to apply it, or pass "
        "--acknowledgments for the records.",
        err=True,
    )

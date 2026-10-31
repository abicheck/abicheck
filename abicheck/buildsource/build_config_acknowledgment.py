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

"""``BuildConfig``'s ``acknowledgments:`` block (ADR-067 D5/D6).

The project's acknowledgment records and its additions-review gate are
stable project properties, so they live in ``.abicheck.yml``::

    acknowledgments:
      file: abi/acknowledgments.yml      # ADR-067 D5 records
      unacknowledged_additions: block    # allow (default) | warn | block
      breaks: gate                       # gate (default) | accept

A relative ``file`` resolves against the project root
(``config_paths.project_root_for_config``), like ``compile.include_dirs``.
The gate's vocabulary is :mod:`abicheck.model.acknowledgment_policy`'s, the
one the policy file's own ``acknowledgment:`` block is checked against.

A sibling of ``build_config.py`` because that file sits at its ADR-061
no-growth baseline: the block's schema, parse and serialization live here,
and the parent carries only the field and one call per stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.acknowledgment_policy import (
    VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS,
    VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS,
)

__all__ = [
    "ACKNOWLEDGMENT_KEY",
    "AcknowledgmentConfig",
    "acknowledgment_block",
    "acknowledgment_findings",
    "parse_acknowledgment_config",
]

ACKNOWLEDGMENT_KEY = "acknowledgments"
_KEYS = frozenset({"file", "unacknowledged_additions", "breaks"})


@dataclass(frozen=True)
class AcknowledgmentConfig:
    """The ``acknowledgments:`` block as written; ``None`` fields are unset."""

    file: str | None = None
    unacknowledged_additions: str | None = None
    breaks: str | None = None


def acknowledgment_findings(value: object) -> list[str]:
    """Structural findings for a raw ``acknowledgments:`` block (strict loading)."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [
            f"acknowledgments must be a mapping, got {type(value).__name__}: {value!r}"
        ]
    findings = [
        f"unknown .abicheck.yml key acknowledgments.{k!r}"
        for k in value
        if k not in _KEYS
    ]
    path = value.get("file")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        findings.append("acknowledgments.file must be a non-empty path string")
    action = value.get("unacknowledged_additions")
    if action is not None and (
        not isinstance(action, str)
        or action not in VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS
    ):
        findings.append(
            f"acknowledgments.unacknowledged_additions must be one of "
            f"{sorted(VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS)}, got {action!r}"
        )
    breaks = value.get("breaks")
    if breaks is not None and (
        not isinstance(breaks, str) or breaks not in VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS
    ):
        findings.append(
            f"acknowledgments.breaks must be one of "
            f"{sorted(VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS)}, got {breaks!r}"
        )
    return findings


def parse_acknowledgment_config(top: dict[str, object]) -> AcknowledgmentConfig | None:
    """The ``acknowledgments:`` block, or ``None`` when the config has none.

    A value :func:`acknowledgment_findings` would reject is dropped here, the
    same lenient-parse / strict-findings split every other block uses.
    """
    block = top.get(ACKNOWLEDGMENT_KEY)
    if not isinstance(block, dict):
        return None
    path = block.get("file")
    action = block.get("unacknowledged_additions")
    breaks = block.get("breaks")
    cfg = AcknowledgmentConfig(
        file=path if isinstance(path, str) and path.strip() else None,
        unacknowledged_additions=(
            action
            if isinstance(action, str)
            and action in VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS
            else None
        ),
        breaks=(
            breaks
            if isinstance(breaks, str) and breaks in VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS
            else None
        ),
    )
    if cfg.file or cfg.unacknowledged_additions or cfg.breaks:
        return cfg
    return None


def acknowledgment_block(cfg: Any) -> dict[str, Any]:
    """Non-default ``acknowledgments:`` keys of *cfg* (a ``BuildConfig``)."""
    ack = cfg.acknowledgment
    if ack is None:
        return {}
    out: dict[str, Any] = {}
    if ack.file is not None:
        out["file"] = ack.file
    if ack.unacknowledged_additions is not None:
        out["unacknowledged_additions"] = ack.unacknowledged_additions
    if ack.breaks is not None:
        out["breaks"] = ack.breaks
    return out

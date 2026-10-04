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

"""A comparison's ADR-067 D5/D6 inputs: the acknowledgment records and the
additions-review gate policy.

``policy`` is not in ``frontends.may_import``, so a CLI module reaches the
records loader and the D7 policy resolution through this workflow module
(the same rule ``workflows/suppression.py`` and ``workflows/disposition.py``
follow). What decides *whether* a project-config value applies at all -- the
trust boundary on an auto-discovered ``.abicheck.yml`` -- is the frontend's
business, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import ValidationError
from ..model.acknowledgment_policy import AcknowledgmentPolicy
from ..policy.acknowledgment import AcknowledgmentList
from ..policy.acknowledgment_gate import effective_acknowledgment_policy

__all__ = [
    "CompareAcknowledgments",
    "load_acknowledgment_records",
    "resolve_compare_acknowledgments",
]


@dataclass(frozen=True)
class CompareAcknowledgments:
    """What a comparison passes to ``compare_snapshots``.

    ``records`` is ``None`` when no review runs at all (no records and no
    stated gate policy). When a gate policy is stated but no records file
    is, ``records`` is an *empty* list: every public addition is then
    unacknowledged, which is what a ``warn``/``block`` gate with nothing
    acknowledged means.
    """

    records: AcknowledgmentList | None = None
    policy: AcknowledgmentPolicy | None = None


def load_acknowledgment_records(path: Path) -> AcknowledgmentList:
    """Load *path*'s records, or raise :class:`ValidationError` naming it."""
    try:
        return AcknowledgmentList.load(path)
    except (ValueError, OSError) as exc:
        raise ValidationError(f"acknowledgment file {path}: {exc}") from exc


def resolve_compare_acknowledgments(
    records_path: Path | None,
    *,
    policy_file: object,
    configured_action: str | None,
) -> CompareAcknowledgments:
    """The run's records and gate policy (ADR-049 D7: a ``--policy`` file's
    stated ``acknowledgment:`` block outranks *configured_action*)."""
    policy = effective_acknowledgment_policy(policy_file, configured_action)
    records = (
        load_acknowledgment_records(records_path) if records_path is not None else None
    )
    if records is None and policy is not None:
        records = AcknowledgmentList([])
    return CompareAcknowledgments(records=records, policy=policy)

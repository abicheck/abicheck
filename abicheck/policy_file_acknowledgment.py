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

"""ADR-067 D6 (plan workstream C-S3): ``policy_file.py``'s ``acknowledgment:``
YAML block parser.

A sibling of ``policy_file.py``, for the identical reason
``policy_file_versioning.py`` is one — see that module's own docstring
(``policy_file.py`` carries a ``no_growth`` architecture-debt baseline, so a
genuinely new parsing responsibility gets its own leaf module rather than
growing the capped one). ``policy_file.py`` calls
:func:`parse_acknowledgment_policy` from its own ``load()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from .errors import PolicyError
from .policy.acknowledgment_policy import (
    VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS,
    AcknowledgmentPolicy,
    UnacknowledgedAdditionsAction,
    built_in_default_acknowledgment_policy,
)

_ACKNOWLEDGMENT_KNOWN_KEYS = frozenset({"unacknowledged_additions"})


def parse_acknowledgment_policy(raw: Any, path: Path) -> AcknowledgmentPolicy:
    """Validate and parse the ``acknowledgment:`` namespace (ADR-067 D6).

    The only control today is ``unacknowledged_additions``
    (``allow``/``warn``/``block``, default ``allow`` — D6: "The default is
    ``allow``, so no existing run changes"). Mirrors
    :func:`abicheck.policy_file_versioning.parse_versioning_policy`'s own
    shape: an absent key falls back to the built-in default, and an unknown
    key or invalid value is a hard load error, not a silent fallback.
    """
    if not isinstance(raw, dict):
        raise PolicyError(
            f"'acknowledgment' must be a YAML mapping in {path}, got "
            + type(raw).__name__
        )
    unknown = set(raw) - _ACKNOWLEDGMENT_KNOWN_KEYS
    if unknown:
        raise PolicyError(
            f"acknowledgment in {path}: unknown key(s) {sorted(unknown)}. "
            f"Valid keys: {sorted(_ACKNOWLEDGMENT_KNOWN_KEYS)}"
        )
    default = built_in_default_acknowledgment_policy()
    action = raw.get("unacknowledged_additions", default.unacknowledged_additions)
    # `isinstance` first: a YAML list/mapping is valid `safe_load` input but
    # unhashable, so `not in` on the frozenset below would raise `TypeError`
    # instead of this function's documented `PolicyError` contract (Codex
    # review) -- e.g. `unacknowledged_additions: []`.
    if (
        not isinstance(action, str)
        or action not in VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS
    ):
        raise PolicyError(
            f"acknowledgment.unacknowledged_additions in {path}: invalid "
            f"value {action!r}. Valid values: "
            f"{sorted(VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS)}"
        )
    return AcknowledgmentPolicy(
        unacknowledged_additions=cast("UnacknowledgedAdditionsAction", action)
    )

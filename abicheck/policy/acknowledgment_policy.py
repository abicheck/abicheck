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

"""ADR-067 D6: the additions review gate's typed policy value.

A leaf model type, mirroring :mod:`abicheck.policy.versioning_policy`'s own
shape (a small frozen dataclass plus a ``built_in_default_*`` factory) — see
that module's sibling parser (:mod:`abicheck.policy_file_versioning`) for the
pattern this module's own parser (:mod:`abicheck.policy_file_acknowledgment`)
follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: D6's three review-gate actions. ``allow`` is the default -- "no existing
#: run changes" (D6's own wording) -- so a project must opt in to ``warn``/
#: ``block`` before an unacknowledged public addition becomes visible as a
#: policy concern rather than an ordinary, silently-accepted addition.
UnacknowledgedAdditionsAction = Literal["allow", "warn", "block"]

VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS: frozenset[str] = frozenset(
    {"allow", "warn", "block"}
)


@dataclass(frozen=True)
class AcknowledgmentPolicy:
    """D6's review-gate policy: what an unacknowledged public addition does.

    Orthogonal to the compatibility verdict by construction (D6: "This folds
    through the existing gate/exit precedence... as policy acceptance, never
    as a reclassification of the addition into a break") — nothing in this
    type can turn an addition into a different ``ChangeKind`` or move its
    verdict class; it only decides whether the *absence* of an acknowledgment
    on an addition should be reported (``warn``) or gate the run
    (``block``).
    """

    unacknowledged_additions: UnacknowledgedAdditionsAction = "allow"

    def __post_init__(self) -> None:
        # A `Literal` annotation is not enforced at runtime (Codex review):
        # a caller constructing this directly (rather than through
        # `policy_file_acknowledgment.parse_acknowledgment_policy`, which
        # already validates its own raw YAML input) can otherwise pass any
        # value through unchecked, silently disabling `block` for a typo'd
        # spelling that never equals `"block"` in `evaluate_unacknowledged_
        # additions`'s own comparison.
        if (
            not isinstance(self.unacknowledged_additions, str)
            or self.unacknowledged_additions
            not in VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS
        ):
            raise ValueError(
                "unacknowledged_additions must be one of "
                f"{sorted(VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS)}, got "
                f"{self.unacknowledged_additions!r}"
            )


def built_in_default_acknowledgment_policy() -> AcknowledgmentPolicy:
    """The policy every run has when nothing states otherwise: ``allow``."""
    return AcknowledgmentPolicy()

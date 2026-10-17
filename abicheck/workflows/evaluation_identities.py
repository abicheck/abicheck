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

"""Replayable identities of the built-in (file-less) policy bases and severity presets.

ADR-049 D6 requires every selected base to carry an ``id``/``version``/``sha256``.
A built-in base is code rather than a file, so its digest is taken over what the
base actually resolves to: :func:`builtin_policy_identity` digests the four
``ChangeKind`` sets, and :func:`severity_preset_identity` digests the four
category levels. :func:`stated_policy_base` drops a ``--policy`` value that a
``--policy-file`` overrides, so an ignored name cannot poison the receipt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ..compatibility_evaluation_config import ImmutableIdentity
from ..model.change_catalog.registry import VALID_BASE_POLICIES
from ..model.execution_cache import memoized
from ..policy.classification import policy_kind_sets
from ..policy.severity import SEVERITY_PRESETS

SEVERITY_CATEGORY_FIELDS: Mapping[str, str] = {
    category: f"gate.severity.{category}"
    for category in (
        "abi_breaking",
        "potential_breaking",
        "quality_issues",
        "addition",
    )
}


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stated_policy_base(policy: Any, policy_file: Any) -> Any:
    """*policy*, unless a ``policy_file`` overrode it with a value this
    resolver would reject.

    Both the MCP tool and the scan API validate ``policy`` **only when no
    ``policy_file`` is given** -- the file takes precedence over the base
    name -- so an unknown ``policy`` alongside a valid file is an accepted
    request whose comparison completes under the file's own base. Handing
    that ignored value to the resolver made :func:`builtin_policy_identity`
    raise and killed an otherwise-finished run: a receipt turning a
    successful comparison into a failure, the one thing a receipt must never
    do (Codex review, found once on the MCP path and then again on the scan
    path -- hence living here rather than in either front end).

    Dropped rather than repaired: the value chose nothing, so naming it in
    the receipt would be false either way, and the resolver reads the base
    off ``policy_file`` exactly as the comparison did. With no file present
    the value passes through unchanged, so a genuinely unknown policy still
    fails loudly where the front end validates it.
    """
    if policy_file is None or policy is None:
        return policy
    return policy if policy in VALID_BASE_POLICIES else None


@memoized
def builtin_policy_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--policy`` base.

    ADR-049 D6 requires every selected base to carry an
    ``id``/``version``/``sha256``, but a built-in base is code, not a file --
    there are no bytes to digest. The digest is therefore taken over what the
    base actually *is*: the four ``ChangeKind`` sets
    :func:`~abicheck.checker_policy.policy_kind_sets` resolves it to. That
    makes the identity detect the drift a digest exists to detect -- a
    registry change that moves a kind between buckets changes the base's
    meaning and changes its digest -- which a bare ``id``/``version`` pair
    could not.

    ``version`` is ``1`` for every built-in base: the built-ins are not
    independently versioned artifacts, and the digest already distinguishes
    two revisions of the same name.

    Raises ``ValueError`` for a name outside
    :data:`~abicheck.checker_policy.VALID_BASE_POLICIES` -- ``policy_kind_sets``
    itself falls back to ``strict_abi`` for an unknown name, which is the
    right behavior for classification (never crash a comparison over a typo)
    but exactly wrong for an identity (it would mint a ``sdk_vendr`` identity
    carrying ``strict_abi``'s digest).
    """
    if name not in VALID_BASE_POLICIES:
        raise ValueError(
            f"unknown base policy {name!r}: choose from {sorted(VALID_BASE_POLICIES)}"
        )
    breaking, api_break, compatible, risk = policy_kind_sets(name)
    return ImmutableIdentity(
        id=name,
        version=1,
        sha256=_digest(
            {
                "base_policy": name,
                "breaking": sorted(k.value for k in breaking),
                "api_break": sorted(k.value for k in api_break),
                "compatible": sorted(k.value for k in compatible),
                "risk": sorted(k.value for k in risk),
            }
        ),
    )


@memoized
def severity_preset_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--severity-preset``.

    Same reasoning as :func:`builtin_policy_identity`: a preset is code, so
    the digest is taken over the four category levels it resolves to.
    The CLI spelling ``info-only`` and its programmatic alias ``info_only``
    (``SEVERITY_PRESETS`` carries both keys for the same
    :class:`~abicheck.policy.severity.SeverityConfig`) normalize to one canonical id,
    so two equivalent semantic inputs resolve to an equal object (D7) instead
    of to two identities that differ only in punctuation.
    """
    preset = SEVERITY_PRESETS.get(name)
    if preset is None:
        raise ValueError(
            f"unknown severity preset {name!r}: choose from {sorted(SEVERITY_PRESETS)}"
        )
    canonical_id = name.replace("_", "-")
    return ImmutableIdentity(
        id=canonical_id,
        version=1,
        sha256=_digest(
            {
                "severity_preset": canonical_id,
                **{
                    category: getattr(preset, category).value
                    for category in SEVERITY_CATEGORY_FIELDS
                },
            }
        ),
    )

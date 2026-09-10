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

"""``policy_file.py``'s top-level document validation.

ADR-061 task routing ("decide relevance, suppression, classification,
severity, or gating" -> ``policy/``): both functions here decide something
about a policy document's shape/validity, so this module belongs under
``abicheck/policy/`` rather than the flat legacy root -- unlike
``policy_file.py``/``policy_file_versioning.py``/``policy_file_acknowledgment.py``
themselves, which stay grandfathered into ``architecture/modules.yaml``'s
``legacy_paths`` allowlist as parsing helpers of that one still-flat module.
Originally landed at ``abicheck/policy_file_top_level.py`` and grandfathered
the same way; moved here on a second review round per that same rule.

A sibling of ``policy_file.py`` rather than an addition to it either way:
that module carries a ``no_growth`` architecture-debt baseline
(``architecture/debt.yaml``), and per this repo's own convention ("the way
to shrink an entry is to move responsibility out to a properly-owned
module, never to trim the file to fit" -- root ``AGENTS.md``), a genuinely
new validation responsibility gets its own leaf module instead of growing
the capped one -- mirrors ``policy_file_versioning.py``'s identical split
for the ``versioning:`` namespace's own sub-key strictness.
"""

from __future__ import annotations

from typing import Any

from ..policy_file_versioning import _reject_unknown_keys

#: Every top-level key a ``--policy`` YAML document may carry. Kept as one
#: explicit set, consulted by ``PolicyFile.load`` before anything else
#: parses the document, so an unrecognized key is a hard load error
#: (``PolicyError``) rather than a silent no-op -- ADR-049 D8 already made
#: an unknown ``ChangeKind`` slug *inside* ``overrides:`` a hard error for
#: exactly this reason (a typo produces no finding anywhere, and the run
#: exits 0 as if the policy applied); a typo at the *top level*
#: (``suppress:`` instead of the real ``overrides:``/``reclassify:``, or a
#: bare renamed/misspelled key) used to be silently ignored the same way,
#: which is asymmetric with that precedent and a real footgun: the whole
#: document becomes a no-op with no warning, no error, and no changed exit
#: code. Every field ``PolicyFile.load`` actually reads from ``raw`` is
#: listed here -- add the new key here in the same change that teaches
#: ``load()`` to read it.
KNOWN_POLICY_FILE_KEYS = frozenset(
    {
        "base_policy",
        "overrides",
        "reclassify",
        "frozen_namespaces",
        "internal_namespaces",
        "evidence_policy",
        "versioning",
        "acknowledgment",
    }
)


def reject_unknown_top_level_keys(raw: dict[str, Any]) -> None:
    """Hard-error on a ``--policy`` document top-level key this module does
    not recognize (see :data:`KNOWN_POLICY_FILE_KEYS`'s own docstring for
    why this must be an error, not a warning-and-skip). Reuses
    ``policy_file_versioning._reject_unknown_keys`` -- the same
    unknown-key-is-an-error convention the ``versioning:``/``support_window:``/
    ``deprecation_window:`` sub-blocks already apply, rather than a second,
    differently-worded implementation of the identical check.
    """
    _reject_unknown_keys(raw, KNOWN_POLICY_FILE_KEYS, "policy file")


def parse_base_policy(raw: dict[str, Any]) -> str:
    """Extract and validate the ``base_policy`` field from a raw YAML
    mapping.

    Moved here from ``policy_file.py``'s own ``_parse_base_policy`` (second
    review round, alongside this module's relocation into ``abicheck/
    policy/``) -- "which base policy names are valid, and what a document
    that names an invalid one gets" is a top-level-document validation
    decision, the same shape as :func:`reject_unknown_top_level_keys`
    above, and moving it here is what lets ``policy_file.py`` gain the
    unknown-top-level-key check without growing past its own
    ``architecture/debt.yaml`` baseline.
    """
    from ..checker_policy import VALID_BASE_POLICIES
    from ..errors import PolicyError

    base_policy = raw.get("base_policy", "strict_abi")
    if not isinstance(base_policy, str):
        raise PolicyError(
            "'base_policy' must be a string, got " + type(base_policy).__name__
        )
    if base_policy not in VALID_BASE_POLICIES:
        raise PolicyError(
            f"Unknown base_policy {base_policy!r}. "
            f"Valid values: {sorted(VALID_BASE_POLICIES)}"
        )
    return base_policy

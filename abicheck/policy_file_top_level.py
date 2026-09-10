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

"""``policy_file.py``'s top-level-key strictness (new defect 2 fix).

A sibling of ``policy_file.py`` rather than an addition to it: that module
carries a ``no_growth`` architecture-debt baseline (``architecture/
debt.yaml``), and per this repo's own convention ("the way to shrink an
entry is to move responsibility out to a properly-owned module, never to
trim the file to fit" -- root ``AGENTS.md``), a genuinely new validation
responsibility gets its own leaf module instead of growing the capped one
-- mirrors ``policy_file_versioning.py``'s identical split for the
``versioning:`` namespace's own sub-key strictness.
"""

from __future__ import annotations

from typing import Any

from .policy_file_versioning import _reject_unknown_keys

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

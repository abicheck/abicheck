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

"""How many findings a release *summary* shows per library, and where.

A two-value leaf, extracted so the two modules that need it -- the resolver
(``cli_compare_release_matrix``) and the Markdown renderer that now applies
the cap (``cli_compare_receipt._release_md_library_findings``, reached from
``cli_compare_release_helpers``) -- can both depend on it without depending
on each other. A function-local import between those two was a real new
import cycle the ``import-cycle-growth`` gate rejects, and ``AGENTS.md``'s
own rule for that shape is to move the shared thing to a leaf, not to
extend the cycle allowlist.

This is a **presentation** limit and nothing else. It bounds what a human
summary renders; it never bounds the machine document, which carries every
finding unless the run explicitly asked for a cap (see
``cli_compare_release_matrix._release_findings_cap_is_explicit``).
"""

from __future__ import annotations

__all__ = [
    "MAX_RELEASE_FINDINGS_PER_LIBRARY",
    "MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR",
    "release_findings_cap_is_explicit",
    "resolve_max_release_findings_per_library",
]

#: Findings rendered per library in a release *summary* before the render
#: notes that the rest were omitted. Matches ``scan --against``'s own
#: display cap; overridable per run via ``--max-findings-per-library`` or
#: globally via the env var below.
MAX_RELEASE_FINDINGS_PER_LIBRARY = 10

#: Env-var override consulted when a caller passes no explicit
#: ``max_findings``. Kept distinct from a CLI/API default of ``None`` so
#: "not specified" stays distinguishable from "explicitly 10".
MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR = "ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY"


def release_findings_cap_is_explicit(max_findings: int | None) -> bool:
    """Whether *this run* asked for a per-library findings cap.

    True for ``--max-findings-per-library`` or a usable
    ``ABICHECKMAX_RELEASE_FINDINGS_PER_LIBRARY``; False when the cap
    resolves to the built-in default, which is a *presentation* choice
    nobody made. That distinction is what lets a machine document stay
    complete by default while the human summary stays bounded: a reader of
    ``--format json`` who never asked for truncation must not receive a
    silently truncated document, and one who *did* ask gets
    ``findings_truncated`` plus (under ``--output-dir``) ``complete_report``
    naming the uncapped artifact.
    """
    if max_findings is not None:
        return True
    import os

    env_value = os.environ.get(MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR)
    if not env_value:
        return False
    try:
        return int(env_value) >= 1
    except ValueError:
        return False

def resolve_max_release_findings_per_library(max_findings: int | None) -> int:
    """Resolve the effective per-library findings cap: explicit override,
    else env, else default. Mirrors
    ``cli_scan_baseline._resolve_max_baseline_findings`` exactly (same
    precedence, same "malformed override degrades to the safe default
    rather than failing the run" behavior).

    *max_findings* is the per-call override (``compare-release
    --max-findings-per-library``); it wins when given. Otherwise
    ``ABICHECKMAX_RELEASE_FINDINGS_PER_LIBRARY`` lets a CI job raise (or
    lower) the cap globally without a code change.
    """
    if max_findings is not None:
        if max_findings < 1:
            raise ValueError(
                f"max_findings_per_library must be a positive integer, got {max_findings}"
            )
        return max_findings
    import os

    env_value = os.environ.get(MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR)
    if env_value:
        try:
            parsed = int(env_value)
        except ValueError:
            return MAX_RELEASE_FINDINGS_PER_LIBRARY
        if parsed >= 1:
            return parsed
    return MAX_RELEASE_FINDINGS_PER_LIBRARY

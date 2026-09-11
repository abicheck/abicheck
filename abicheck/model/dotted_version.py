# Copyright 2026 Nikolay Petrov
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

"""Bounded dotted-numeric-version parsing (ADR-061 D1 pattern).

Split out of ``diff_versioning.py`` (a whole-file-classified ``compare``
module) for the same reason ``binary_naming.strip_vendor_hash`` was split out
of ``binary_utils.py``: :func:`parse_dotted_numeric_version` is a pure string
transform with no I/O and no other ``abicheck`` dependency, so it belongs in
``model`` rather than staying pinned to the one detector module that
originally needed it.

This split is what let ``environment_matrix.py`` (declared deployment
constraints — ``EnvironmentMatrix``) be reclassified from ``workflows`` to
``model`` in ``architecture/modules.yaml``: that dataclass's own
``_parse_runtime_floors`` was ``environment_matrix.py``'s *only* dependency
outside the standard library, and it pointed at ``diff_versioning.py``
(``compare``-classified) — a ``model -> compare`` edge ``model``'s own
``may_import: []`` forbids. Moving just this one bounded-integer-parsing
primitive here (not ``EnvironmentMatrix`` itself, and not any of
``diff_versioning.py``'s real ELF-symbol-version-policy logic) closed that
edge, which is what makes the reclassification legal per
``scripts/check_architecture.py``'s dependency-direction rule — see
``architecture/modules.yaml``'s own comment on ``environment_matrix.py`` for
the full account of why a plain flat-root reclassification (no need to
physically relocate the file) was the correct, minimal-diff fix, in
preference to raising ``buildsource/build_config.py``'s ``no_growth``
baseline to admit ``_deployment_findings`` staying pinned to that file
forever.

``diff_versioning.py`` re-exports ``parse_dotted_numeric_version`` as its own
private ``_parse_dotted_numeric_version`` name so every existing internal
call site, and every external importer (``diff_wheel_deployment.py``,
``tests/test_environment_drift.py``), is unaffected.
"""

from __future__ import annotations

#: Every dot-separated component of a version tag/floor must be purely
#: numeric and at most this many digits: the floor contract parses with
#: ``int()`` per component, so a "2.28-1" or "2.x" would silently truncate to
#: ``(2,)`` and flip verdicts, and an unbounded digit string could otherwise
#: burn CPU/memory on a runtime with no bounded-integer-conversion guard.
MAX_VERSION_COMPONENT_DIGITS = 9


def parse_dotted_numeric_version(text: str) -> tuple[int, ...] | None:
    """Parse a dotted numeric version safely, or return ``None``.

    Version tags and declared runtime floors can come from untrusted ELF
    metadata or snapshots (or a hand-edited ``.abicheck.yml``). Keep integer
    conversion bounded so pathological digit strings are treated like
    malformed versions rather than aborting the comparison via Python's
    integer-conversion guard (or burning CPU/memory on runtimes without one).
    """
    parts = text.split(".")
    if not parts:
        return None
    parsed: list[int] = []
    for part in parts:
        if (
            not part
            or not part.isascii()
            or not part.isdigit()
            or len(part) > MAX_VERSION_COMPONENT_DIGITS
        ):
            return None
        parsed.append(int(part))
    return tuple(parsed) if parsed else None

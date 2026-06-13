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

"""Versioned-symbol-scheme recogniser (field-eval P08).

Libraries like **ICU** embed the major version in *every* exported symbol name
(``u_strlen_75`` → ``u_strlen_78``). A routine, source-compatible upgrade then
reads as a wall of `func_removed` + `func_added` even though the API barely
changed (16 k changes for ICU 75→78 in the field evaluation).

This recogniser is **advisory and additive** (ADR-028 authority rule): it never
removes or downgrades an artifact-proven break. When most removed function
symbols reappear as added symbols differing *only* by a numeric version token, it
emits a single ``versioned_symbol_scheme_detected`` finding (RISK) that explains
the churn and points at the library's versioning convention. The individual
``func_removed`` / ``func_added`` findings (and the BREAKING verdict they carry)
are left untouched — flipping the verdict is a deliberate, opt-in preset, not
something this heuristic does on its own.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind

if TYPE_CHECKING:
    from .checker_types import Change

#: Collapse every digit run to a placeholder so two names that differ only by a
#: version number share a normalized form (``u_strlen_75`` ~ ``u_strlen_78``).
_DIGITS = re.compile(r"\d+")

#: Don't fire on a couple of coincidental renames — require a real, library-wide
#: pattern: an absolute floor *and* a majority of the removed surface.
_MIN_PAIRS = 3
_MIN_FRACTION = 0.6

_REMOVED_KINDS = (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY)


def _normalize(name: str) -> str:
    return _DIGITS.sub("#", name)


def _is_version_scheme_candidate(name: str) -> bool:
    """Return true when digits in *name* may encode a source-level version.

    Itanium C++ ABI names begin with ``_Z`` and contain structural digits such
    as identifier lengths (``_Z4sym1``). Collapsing those digits can make
    unrelated C++ symbols look like a versioned C naming convention, so leave
    mangled C++ names to the normal per-symbol detectors.
    """
    return bool(name) and not name.startswith("_Z")


def analyze_versioned_scheme(changes: list[Change]) -> tuple[Change | None, list[Change]]:
    """Analyze removed/added churn for a versioned-symbol scheme.

    Returns ``(advisory, matched)`` where *advisory* is the single
    ``versioned_symbol_scheme_detected`` finding (or ``None`` when no scheme is
    present) and *matched* is the list of the removed **and** added ``Change``
    objects that form the version-rename pairs — the inputs the opt-in collapse
    preset reclassifies as compatible. Pure — no snapshot/IO, unit-testable.
    """
    from .checker_types import Change

    removed = [c for c in changes
               if c.kind in _REMOVED_KINDS and _is_version_scheme_candidate(c.symbol)]
    added = [c for c in changes
             if c.kind is ChangeKind.FUNC_ADDED and _is_version_scheme_candidate(c.symbol)]
    if len(removed) < _MIN_PAIRS or not added:
        return None, []

    added_by_norm: dict[str, list[Change]] = {}
    for a in added:
        added_by_norm.setdefault(_normalize(a.symbol), []).append(a)

    matched_removed: list[Change] = []
    matched_added: list[Change] = []
    seen_added: set[int] = set()
    for r in removed:
        norm = _normalize(r.symbol)
        if norm == r.symbol:  # no digits → not a versioned name
            continue
        cands = [a for a in added_by_norm.get(norm, []) if a.symbol != r.symbol]
        if not cands:
            continue
        matched_removed.append(r)
        for a in cands:
            if id(a) not in seen_added:
                seen_added.add(id(a))
                matched_added.append(a)

    pairs = len(matched_removed)
    if pairs < _MIN_PAIRS or pairs < _MIN_FRACTION * len(removed):
        return None, []

    advisory = Change(
        kind=ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED,
        symbol="<library>",
        description=(
            f"{pairs} of {len(removed)} removed symbols reappear as added symbols "
            "differing only by a version token in the name (versioned-symbol "
            "scheme, e.g. ICU 'u_strlen_75'->'u_strlen_78'). The large "
            "removed/added churn is likely a library-wide rename, not independent "
            "API removals."
        ),
        old_value=f"{len(removed)} removed",
        new_value=f"{pairs} version-renamed",
    )
    return advisory, matched_removed + matched_added


def detect_versioned_symbol_scheme(changes: list[Change]) -> Change | None:
    """Return one advisory ``Change`` if the removed/added churn is a versioned
    scheme, else ``None`` (back-compat wrapper over :func:`analyze_versioned_scheme`)."""
    return analyze_versioned_scheme(changes)[0]

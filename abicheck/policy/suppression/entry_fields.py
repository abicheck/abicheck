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

"""Field-level validation for suppression YAML entries.

Each ``parse_*`` helper validates one optional key of a suppression entry and
raises with the entry index in the message, so :meth:`SuppressionList.load`
can report exactly which rule is malformed. Split out of ``rules.py`` (which
sits at its architecture-gate line cap) because these are pure value checks
with no dependency on the matching model.
"""

from __future__ import annotations

from datetime import date, datetime


def parse_expires(expires_raw: object, entry_index: int) -> date | None:
    """Parse and validate an ``expires`` value from a suppression entry.

    Returns a :class:`date` or *None*.  Raises :class:`ValueError` on
    invalid date formats.
    """
    if expires_raw is None:
        return None
    if isinstance(expires_raw, date):
        # datetime is a subclass of date; convert to date to avoid
        # TypeError when comparing datetime to date in is_expired()
        if isinstance(expires_raw, datetime):
            return expires_raw.date()
        return expires_raw
    try:
        return date.fromisoformat(str(expires_raw))
    except ValueError as e:
        raise ValueError(
            f"Suppression entry {entry_index}: invalid 'expires' date {expires_raw!r} "
            "(expected ISO 8601 format, e.g. 2026-06-01)"
        ) from e


def parse_allow_public_break(raw: object, entry_index: int) -> bool:
    """Parse and validate ``allow_public_break`` from a suppression entry.

    ADR-044 D2 (Codex review): this is the explicit override for suppressing
    a public-reachable BREAKING/API_BREAK change, so it must not silently
    coerce a truthy-but-wrong value — ``bool("false")`` is ``True`` in Python,
    so a stray quoted string in a hand- or template-generated YAML file
    (``allow_public_break: "false"``) would otherwise silently enable the
    exact override this safety gate exists to require an explicit, reviewed
    ``true`` for. Only an actual YAML boolean (``true``/``false``, unquoted)
    or an absent key (default ``False``) is accepted.
    """
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"Suppression entry {entry_index}: 'allow_public_break' must be a boolean "
        f"(true/false), got {raw!r}"
    )


def parse_allow_unknown_reachability(raw: object, entry_index: int) -> bool:
    """Parse and validate ``allow_unknown_reachability`` from a suppression
    entry — same strict-boolean contract as :func:`parse_allow_public_break`
    (impact-analysis-layer P0 slice)."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"Suppression entry {entry_index}: 'allow_unknown_reachability' must be "
        f"a boolean (true/false), got {raw!r}"
    )


def parse_entry_id(raw: object, entry_index: int, seen: dict[str, int]) -> str | None:
    """P2: validate one suppression entry's optional ``id:``; reject a repeat.

    *seen* maps each id already declared earlier in the same document to its
    entry index. A duplicate is a hard error: two rules sharing an id would be
    indistinguishable in every audit row.
    """
    from ...errors import PolicyError

    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyError(
            f"Suppression entry {entry_index}: 'id' must be a non-empty string, got {raw!r}"
        )
    if raw in seen:
        raise PolicyError(
            f"Suppression entry {entry_index}: duplicate id {raw!r} (already used by "
            f"suppression entry {seen[raw]}); ids must be unique per file"
        )
    seen[raw] = entry_index
    return raw

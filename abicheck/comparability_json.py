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

"""Leaf helpers shared by :mod:`abicheck.comparability` and
:mod:`abicheck.comparability_sequences` (Codex review, PR #641 follow-up:
``comparability.py`` crossed the file-size hard cap once this round's
identity-coordinate-system/duplicate-validation fixes landed).

These are pure, self-contained JSON/shape validators with no dependency on
either module they're shared between -- kept in their own leaf module (per
CLAUDE.md's "_lib/ pattern" guidance) rather than duplicated or
cross-imported between the two, and specifically NOT re-exported from
``comparability.py`` beyond what its own docstrings/tests still reference,
since nothing outside these two modules imports them directly.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The two scope_fields[...] values (see SCOPE_FIELD_KEYS) that a lone
# declared header/directory's own name collapses to above -- carrying no
# real per-entry identity, so the additive-only carve-out
# (_scope_field_is_additive_superset) can never verify a set containing one
# of these is a true superset of the other side and must decline instead of
# guessing.
_SCOPE_SINGLE_ENTRY_SENTINELS = frozenset({"<single-header>", "<single-header-dir>"})

_OWNED_HEADER_TOKEN_PREFIX = "hdrs:"
_OWNED_HEADER_SINGLE_SENTINEL = "hdrs:<single-header>"

_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_valid_digest_payload(value: str) -> bool:
    """Whether *value* is exactly the ``"sha256:<64 lowercase hex chars>"``
    form :func:`abicheck.comparability._sha256_of` always produces (Codex
    review, PR #641 follow-up, twelfth P2).

    An ``"ext:"``/``"sys:"`` token's payload is always a real
    ``_sha256_of`` digest in genuine ``compute_extraction_contract`` output
    -- never arbitrary text -- so a malformed payload like ``"ext:bogus"``
    or ``"sys:not-a-sha256"`` unchanged on both sides is exactly as
    unverifiable as the delimiter-less/non-positional slots earlier rounds
    closed: if it rides alongside a genuinely-growing ``"hdrs:"`` slot, the
    per-slot loop's equality short-circuit never re-examines it, and the
    carve-out would otherwise still return additive-safe despite one slot
    being unverifiable garbage. Not applied to ``"label:"`` (an arbitrary
    user-supplied label, never a hash) or ``"hdrs:"`` (its own
    JSON-list-of-pairs shape is validated separately, by
    :func:`_json_load_list`/:func:`_is_owned_header_pair`).
    """
    return bool(_SHA256_DIGEST_RE.match(value))


def _json_load_list(value: str) -> list[Any] | None:
    """Decode *value* as a JSON list, or ``None`` if it isn't valid JSON or
    doesn't decode to a list (Codex review, PR #641 follow-up, P2).

    Every carve-out helper treats its ``profile_fields``/``scope_fields``
    string inputs as trusted output of
    :func:`abicheck.comparability.compute_extraction_contract` -- but a
    serialized or externally constructed
    :class:`~abicheck.model.ExtractionContract` can carry an arbitrary
    string there (``_extraction_contract_from_dict`` preserves field values
    without validating their JSON encoding), so an unguarded ``json.loads``
    call risks an unhandled ``JSONDecodeError`` escaping
    :func:`abicheck.comparability.check_contracts_comparable` as a raw
    traceback instead of the clean ``ScopeMismatchError``/
    ``ProfileMismatchError`` the gate is supposed to fail closed with. A
    malformed value is exactly as unverifiable as a missing one, so callers
    treat ``None`` here the same way they already treat an absent field:
    decline the carve-out.
    """
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, list) else None


def _json_load_str_list(value: str) -> list[str] | None:
    """Like :func:`_json_load_list`, but additionally requires every
    element to be a ``str`` (Codex review, PR #641 follow-up, second P2).

    Valid JSON can decode to a list whose *members* aren't the plain string
    identities this module's ``"headers"``/``"header_sequence"``/
    ``include_sequence``-slot fields always are in a real
    ``compute_extraction_contract`` output -- e.g. a syntactically valid
    ``scope_fields["headers"] = "[{}]"`` decodes fine as
    :func:`_json_load_list` returns ``[{}]``, but every caller of that
    field then immediately does something that requires its elements to be
    hashable strings (``in _SCOPE_SINGLE_ENTRY_SENTINELS``, ``set(...)``
    membership/superset checks) -- a ``dict`` element there raises
    ``TypeError: unhashable type: 'dict'`` instead of the clean decline
    :func:`_json_load_list` alone was meant to guarantee. Validating shape
    here, once, is simpler than every caller separately guarding against
    that gap the same way :func:`_json_load_list` closed the JSON-syntax
    gap.
    """
    decoded = _json_load_list(value)
    if decoded is None:
        return None
    return decoded if all(isinstance(item, str) for item in decoded) else None


def _is_owned_header_pair(pair: object) -> bool:
    """Whether *pair* is a valid owned-header ``(identity, relative_path)``
    entry: an exact two-element list/tuple of ``str`` (see
    ``abicheck.comparability._slot_token_for_ancestor``'s real ``owned``
    construction).
    """
    return (
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(x, str) for x in pair)
    )

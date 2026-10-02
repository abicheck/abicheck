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

"""C/C++ language-standard spellings: the one owner of how a ``-std=``/
``/std:`` value is read.

Every layer that needs "which standard does this command select" or "which
of these two standards is newer" asks here, so a flag scan, a sort order and
a ``__cplusplus`` lookup cannot disagree about the same spelling. A leaf:
string in, number out, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Pre-publication edition names, each the year its standard was published.
_DRAFT_EDITION_YEARS: dict[str, int] = {
    "9x": 1999,
    "0x": 2011,
    "1x": 2011,
    "1y": 2014,
    "1z": 2017,
    "2a": 2020,
    "2x": 2023,
    "2b": 2023,
    "2c": 2026,
}
_EDITION_RE = re.compile(r"(?:c|gnu|iso9899:)(\+\+)?([0-9][0-9a-z]|[0-9]{4})")


def language_standard_year(spelling: str | None) -> int | None:
    """The publication year of a ``-std=``/``/std:`` value, or ``None``.

    The one place an edition spelling becomes an orderable number: ``c++17``
    and ``gnu++1z`` are both 2017, ``c++98`` is 1998 (so it orders *below*
    ``c++20``, which a bare two-digit suffix does not), ``c++2a`` is 2020.
    Unrecognized spellings (``c++latest``, an empty value) are ``None``
    rather than a guess.
    """
    if not spelling:
        return None
    text = spelling.strip().lower()
    # `language_standard_field` tags a value with its language
    # (``"c++:c++17"``, ``"c:c11"``); the edition is what follows the tag.
    for tag in ("c++:", "c:"):
        if text.startswith(tag):
            text = text[len(tag) :]
            break
    m = _EDITION_RE.fullmatch(text)
    if m is None:
        return None
    edition = m.group(2)
    if edition in _DRAFT_EDITION_YEARS:
        return _DRAFT_EDITION_YEARS[edition]
    if not edition.isdigit():
        return None
    number = int(edition)
    if len(edition) == 4:
        return number
    return 1900 + number if number >= 80 else 2000 + number


def language_standard_is_cxx(spelling: str | None) -> bool:
    """Whether *spelling* names a C++ (not C) edition."""
    return bool(spelling) and "++" in str(spelling)


def last_language_standard(tokens: Iterable[str]) -> str | None:
    """The value of the last ``-std=``/``--std=``/``/std:`` token, or ``None``.

    Last-wins, matching real compiler flag precedence.
    """
    value: str | None = None
    for token in tokens:
        normalized = token[1:] if token.startswith("--std=") else token
        if normalized.startswith("-std="):
            value = normalized.partition("=")[2]
        elif normalized.lower().startswith("/std:"):
            value = normalized.partition(":")[2]
    return value

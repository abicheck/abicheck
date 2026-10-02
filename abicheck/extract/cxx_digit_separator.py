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

"""The one C++14 digit-separator test every lightweight C/C++ text scanner
shares (``1'000``, ``0xFF'FF`` versus a character literal like ``u8'a'``).

A leaf on purpose: the header-text identifier index, the castxml
out-of-line-inline reader and the build-source pattern lexer all blank
literals, and each needs the same answer for an apostrophe.
"""

from __future__ import annotations

_HEXDIGITS = frozenset("0123456789abcdefABCDEF")


def is_digit_separator(text: str, i: int) -> bool:
    """True if the ``'`` at ``text[i]`` is a C++14 digit separator, not a literal.

    A digit separator sits between two hex digits *inside a numeric literal*.
    A numeric literal always starts with a decimal digit, so the maximal
    preceding identifier-run must begin with one -- this rejects a prefixed
    character literal whose prefix happens to end in a hex digit (``u8'a'``,
    where the run is ``u8`` and starts with ``u``).
    """
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if prev not in _HEXDIGITS or nxt not in _HEXDIGITS:
        return False
    k = i - 1
    while k >= 0 and (text[k].isalnum() or text[k] in "_'"):
        k -= 1
    token_start = text[k + 1] if k + 1 < i else ""
    return "0" <= token_start <= "9" and token_start != ""


__all__ = ["is_digit_separator"]

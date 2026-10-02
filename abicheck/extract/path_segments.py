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

"""Path-segment matching primitives shared by header-origin classification.

Moved out of ``provenance.py`` unchanged; that module re-exports both names.
"""

from __future__ import annotations


def _contiguous_subsequence(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *needle* appears as a contiguous run inside *hay*."""
    n = len(needle)
    if n == 0:
        return False
    return any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def _suffix_match(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *hay* ends with the segments of *needle* (a path-suffix match)."""
    n = len(needle)
    return 0 < n <= len(hay) and hay[-n:] == needle


__all__ = ["_contiguous_subsequence", "_suffix_match"]

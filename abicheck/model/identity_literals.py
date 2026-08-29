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

"""Quoted-literal span detection for identity's rename-blind substitution.

Split out of ``model/identity.py`` (its only caller,
``canonicalize_type_param_references``) purely to keep that file under the
AI-readiness gate's 800-line production maximum -- this is one small,
self-contained primitive, not a separate design decision.
"""

from __future__ import annotations

__all__ = ["quoted_literal_spans"]


def quoted_literal_spans(s: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans of every quoted literal in *s* (escapes honored)."""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in ("'", '"'):
            start = i
            j = i + 1
            while j < n:
                if s[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if s[j] == ch:
                    j += 1
                    break
                j += 1
            spans.append((start, j))
            i = j
        else:
            i += 1
    return spans

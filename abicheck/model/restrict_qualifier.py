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

"""Strip the ``restrict`` qualifier from a type spelling.

``restrict`` on a pointer is a qualifier of the pointer with no calling-convention
effect: castxml records it apart from the type, and DWARF spells it inside the
type name. The qualifier-only comparison (``name_classification.
cv_qualifiers_only_differ``) strips it so the two producers agree.
"""

from __future__ import annotations

import re

__all__ = ["strip_restrict"]

_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_RESTRICT_TOKEN_RE = re.compile(r"\b(?:restrict|__restrict__|__restrict)\b")


def strip_restrict(name: str) -> str:
    """*name* with ``restrict`` tokens removed and sigil spacing normalised."""
    stripped = _MULTI_SPACE_RE.sub(" ", _RESTRICT_TOKEN_RE.sub("", name))
    return re.sub(r"\s*([*&])\s*", r" \1", stripped).strip()

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

"""Environment-derived storage limits.

The one parser behind the snapshot envelope's decompression-bomb ceilings
(``snapshot_io``'s decoded/stored caps). A leaf: it reads ``os.environ`` and
nothing else, so a limit's parsing rule -- notably that a malformed or
non-positive value is *ignored* rather than honoured as a ceiling that would
reject every read -- has one implementation rather than one per limit.
"""

from __future__ import annotations

import os

__all__ = ["env_byte_limit"]


def env_byte_limit(names: tuple[str, ...], default: int) -> int:
    """First parseable, positive value among *names*, else *default*.

    A malformed or non-positive value falls through to the next name rather
    than being honored: a ceiling of ``0``/``-1`` would reject every snapshot,
    which is never what an operator raising a limit meant, and silently
    turning a typo into "refuse all reads" is worse than ignoring it.
    """
    for name in names:
        override = os.environ.get(name)
        if not override:
            continue
        try:
            value = int(override)
        except ValueError:
            continue
        if value > 0:
            return value
    return default

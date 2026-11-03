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

"""Match keys for forced pre-includes a derived build context must not
re-emit into a header parse.

Two sources: what the caller already supplies (``header_compile_context.
explicit_forced_include_keys``), and the **declared public headers
themselves**. A build may force-include the very header under comparison
(``-include v1.h`` while ``-H v1.h``); rendering it into the header parse is
redundant -- the parse includes it anyway -- and harmful: the forced include
enters the profile fingerprint by content, so the API change being compared
made the pair "not comparable" (catalog case28/30/31/33/35/36/39/40/41/42/
54/55/56/57/62, build-source lane). A forced include that is *not* a declared
public header (a macro-controlling ``config.h``) is still rendered.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def path_keys(paths: Iterable[str | Path]) -> frozenset[str]:
    """Each path as written plus its resolved absolute form."""
    keys: set[str] = set()
    for p in paths:
        keys.add(str(p))
        try:
            keys.add(str(Path(p).expanduser().resolve()))
        except OSError:  # pragma: no cover - resolve() is strict=False
            pass
    return frozenset(keys)

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

"""Parse a large JSON document with the cyclic garbage collector paused.

``json.loads`` of a clang AST allocates millions of dicts and lists, and
every allocation counts toward CPython's generation-0 threshold, so the
cyclic collector runs over and over *while the tree is being built* --
each pass traversing the ever-growing set of young containers the parse has
just made. None of that work can free anything: a decoded JSON document is
a tree, and a tree has no reference cycles to collect.

Measured on a 246 MiB ``clang -ast-dump=json`` document (the header-graph
memory fixture), ``json.loads`` takes 1.15-1.77 s with the collector
running and 0.83-0.87 s with it paused -- the same objects, the same
result. Pausing it is purely a scheduling decision.

The pause is process-wide (``gc.disable`` has no per-thread form), bounded
by the parse, and restores the caller's own setting on the way out, so a
caller that had already disabled collection keeps it disabled. Another
thread that allocates during the pause loses nothing but timeliness: its
cyclic garbage is collected at the next collection after the pause ends.
"""

from __future__ import annotations

import gc
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["gc_paused", "loads_acyclic"]


@contextmanager
def gc_paused() -> Iterator[None]:
    """Pause cyclic garbage collection for the ``with`` body, then restore
    whatever setting was in effect before it."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def loads_acyclic(document: str | bytes | bytearray, **kwargs: Any) -> Any:
    """``json.loads(document, **kwargs)`` with the collector paused.

    For a document that decodes to a *tree* -- any JSON without an
    ``object_hook`` that stitches decoded values into a cycle, which is
    every caller here."""
    with gc_paused():
        return json.loads(document, **kwargs)

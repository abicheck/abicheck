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

"""Fragment-at-a-time ``json.dumps(obj, indent=2)``, with lazy members.

``storage.bundle_facts_archive`` already spools *encoded blobs*; the plain
JSON baseline (``--bundle-facts-out foo.json``, the default) had no
equivalent and held three full-size copies of the document at once: every
member's snapshot dict (built by ``bundle_facts_to_dict`` before encoding
starts), the whole ``indent=2`` string, and its UTF-8 encoding. For a
six-member release at header depth those are the largest single objects in
the process.

This module removes the first two. :func:`iter_json_indented` yields the
document as fragments, and :class:`LazyItems` lets a caller supply a mapping
whose values are *produced when they are encoded and dropped immediately
after* -- so one member's snapshot dict is resident at a time instead of all
of them.

**Byte-identical to ``json.dumps(obj, indent=2)``**, and that is the
property the tests state, differentially, against ``json.dumps`` itself as
the oracle over randomly generated documents -- not against a second copy of
this module's own formatting rules. The formatting contract being matched is
small and fully specified: with an integer *indent*, ``json`` uses the item
separator ``","`` followed by a newline and the current padding, the key
separator ``": "``, and renders an empty container as ``{}``/``[]`` with no
newline inside.

Two shapes are deliberately delegated to ``json.dumps`` whole rather than
descended into, both on the "never re-implement a coercion" principle this
codebase already applies in ``dumper_cache._write_json_chunked``: a
``dict``/``list`` *subclass*, and a ``dict`` with a non-``str`` key (``json``
coerces ``1`` -> ``"1"``, ``True`` -> ``"true"``; re-deriving that here
would be a second implementation to keep in step). Their fragments are
re-padded, which is exact because ``json``'s indent output uses ``"\\n"``
plus a multiple of *indent* spaces and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["LazyItems", "iter_json_indented"]


@dataclass(frozen=True)
class LazyItems:
    """A JSON object whose values are built one at a time while encoding.

    *keys* fixes the member order (so the document stays deterministic and
    matches what an eager ``dict`` would have produced), and *produce* is
    called exactly once per key, in that order, at the moment that member is
    encoded. The value is dropped before the next one is produced, which is
    the entire point: the peak holds one member, not all of them.

    A frozen dataclass rather than a ``Mapping`` subclass on purpose --
    something that looked like a mapping would be silently *materialised* by
    any caller that iterated it, quietly restoring the retention this exists
    to remove.
    """

    keys: Sequence[str]
    produce: Callable[[str], Any]


def _dumps(value: Any, indent: int) -> str:
    return json.dumps(value, indent=indent)


def _repad(fragment: str, pad: str) -> str:
    """Re-indent a whole-subtree ``json.dumps`` fragment to *pad*.

    Exact rather than heuristic: with an integer indent, every newline
    ``json`` emits is followed by that subtree's own padding, so prefixing
    each of them with the enclosing padding reproduces the nested output
    byte for byte. Strings cannot contribute a literal newline -- ``json``
    escapes one as ``\\n`` -- so no newline inside the fragment is anything
    but structure.
    """
    return fragment.replace("\n", "\n" + pad) if pad else fragment


#: Subtree size, in JSON nodes, at or below which a subtree is handed to the
#: one-shot C encoder whole instead of being descended into in Python.
#:
#: Without it this encoder is *correct but slow*: descending every dict and
#: list in Python cost a measured ~18% of the whole run's wall clock on a
#: six-member baseline, which is a bad trade for the memory it saves --
#: the saving comes from the *member* boundary, not from splitting the
#: small objects inside one member. With it, the descent stops as soon as a
#: subtree is small, so the peak transient is one such subtree rather than
#: the whole document, at close to one-shot encoding speed.
#:
#: Deliberately a node count and not also a byte budget, unlike
#: ``dumper_cache._write_json_chunked``'s pair of bounds: the documents here
#: are snapshots of declarations, whose nodes are short spellings, not an
#: AST full of long template-qualified names where a handful of nodes can
#: encode to hundreds of MiB.
_DELEGATE_NODE_LIMIT = 20_000


def _small_enough(obj: Any, limit: int) -> bool:
    """Whether *obj* is a plain subtree of at most *limit* JSON nodes.

    Stops as soon as the answer is known, so the probe costs
    ``O(min(size, limit))`` however large the subtree really is -- which is
    what makes it safe to run on the way down rather than measuring the
    whole document up front.

    A subtree containing a :class:`LazyItems` anywhere is **never** small
    enough, whatever its size: delegating it would hand ``json.dumps`` an
    object it cannot serialise, and, if it could, would materialise every
    member -- which is the retention this module exists to remove. The
    tests caught exactly this when the bound was first added.
    """
    stack: list[Any] = [obj]
    seen = 0
    while stack:
        cur = stack.pop()
        seen += 1
        if seen > limit:
            return False
        if isinstance(cur, LazyItems):
            return False
        if type(cur) is dict:
            stack.extend(cur.values())
            seen += len(cur)
        elif type(cur) is list:
            stack.extend(cur)
    return seen <= limit


def iter_json_indented(obj: Any, *, indent: int = 2, _level: int = 0) -> Iterator[str]:
    """Yield ``json.dumps(obj, indent=indent)`` one fragment at a time.

    Descends only through plain ``dict``/``list`` (and :class:`LazyItems`),
    delegating everything else to ``json.dumps`` whole. That is deliberate:
    the shapes that make a baseline large are the member map and the
    declaration lists inside each member, and the delegated leaves are
    individually small. A caller wanting a *bound* on the delegated
    fragments splits the document itself -- which is exactly what
    :class:`LazyItems` is for.
    """
    step = " " * indent
    pad = step * _level
    inner_pad = step * (_level + 1)

    if isinstance(obj, LazyItems):
        keys = list(obj.keys)
        if not keys:
            yield "{}"
            return
        yield "{"
        for i, key in enumerate(keys):
            yield ("," if i else "") + "\n" + inner_pad + json.dumps(key) + ": "
            value = obj.produce(key)
            yield from iter_json_indented(value, indent=indent, _level=_level + 1)
            # Drop the member before the next one is produced. Without this
            # the generator's frame keeps the last member alive across the
            # production of the next, i.e. two resident instead of one.
            del value
        yield "\n" + pad + "}"
        return

    if type(obj) is dict:
        if not obj:
            yield "{}"
            return
        if not all(type(k) is str for k in obj):
            yield _repad(_dumps(obj, indent), pad)
            return
        if _small_enough(obj, _DELEGATE_NODE_LIMIT):
            yield _repad(_dumps(obj, indent), pad)
            return
        yield "{"
        for i, (key, value) in enumerate(obj.items()):
            yield ("," if i else "") + "\n" + inner_pad + json.dumps(key) + ": "
            yield from iter_json_indented(value, indent=indent, _level=_level + 1)
        yield "\n" + pad + "}"
        return

    if type(obj) is list:
        if not obj:
            yield "[]"
            return
        if _small_enough(obj, _DELEGATE_NODE_LIMIT):
            yield _repad(_dumps(obj, indent), pad)
            return
        yield "["
        for i, value in enumerate(obj):
            yield ("," if i else "") + "\n" + inner_pad
            yield from iter_json_indented(value, indent=indent, _level=_level + 1)
        yield "\n" + pad + "]"
        return

    yield _repad(_dumps(obj, indent), pad)


def join_json_indented(obj: Any, *, indent: int = 2) -> str:
    """The whole document as one string -- for callers that need one.

    Exists so a compressed write (which needs the full byte string anyway)
    and a test can share the streaming encoder rather than keeping a second
    formatting path that could drift from it.
    """
    return "".join(iter_json_indented(obj, indent=indent))


def iter_encoded(chunks: Iterable[str]) -> Iterator[bytes]:
    """UTF-8 encode a fragment stream without joining it first."""
    for chunk in chunks:
        yield chunk.encode("utf-8")

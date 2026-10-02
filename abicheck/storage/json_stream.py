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
from collections.abc import Callable, Iterator, Sequence
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


#: Target size, in characters, of one delegated fragment. Consecutive small
#: elements of a wide container are encoded together by one ``json.dumps``
#: call, and the batch size adapts to keep each fragment near this size: the
#: peak transient is one fragment, not the document, while the C encoder
#: still does nearly all of the work.
#:
#: This replaced a per-subtree node-count probe (``_small_enough``) that
#: decided delegation by walking each candidate in Python first. On a
#: snapshot -- tens of thousands of small declarations per list -- that
#: probe plus one ``json.dumps`` call per element made a streamed write
#: ~30% slower than the one-shot ``json.dumps`` it stands in for. Batching
#: by measured output needs no walk at all.
_TARGET_FRAGMENT_CHARS = 1 << 20

#: Elements a batch starts with; it then doubles while fragments stay small
#: and halves when one comes out over twice the target.
_INITIAL_BATCH = 64

#: Containers wider than this are streamed in batches; narrower ones are
#: structure (a section header, a payload's handful of fields) and are
#: descended into element by element unless every element is a scalar. A
#: length check, never a walk: on a real snapshot every large subtree is a
#: wide list or map below a few narrow structural levels, so descending the
#: narrow levels and batching the wide ones reaches all of it without ever
#: encoding a big subtree just to learn that it is big.
_DESCEND_LEN = 64


#: How many narrow levels below a narrow container may still be encoded
#: whole with it: deep enough that a small document is one fragment, shallow
#: enough that the check visits at most ``_DESCEND_LEN ** _SHALLOW_DEPTH``
#: nodes and runs only at the narrow structural levels.
_SHALLOW_DEPTH = 2


def _shallow(obj: dict[str, Any] | list[Any], depth: int) -> bool:
    """Whether *obj* holds, within *depth* levels, only scalars and narrow
    containers -- so encoding it whole is one small fragment."""
    values = obj.values() if type(obj) is dict else obj
    for value in values:
        if type(value) is dict or type(value) is list:
            if depth <= 1 or len(value) > _DESCEND_LEN:
                return False
            if not _shallow(value, depth - 1):
                return False
        elif isinstance(value, LazyItems):
            return False
    return True


def _descend_first(value: Any) -> bool:
    """Whether a wide container's element is streamed on its own rather than
    joining a batch: a :class:`LazyItems` (which ``json.dumps`` cannot
    encode, and must not materialise) or a container wide enough to be
    batched itself."""
    return isinstance(value, LazyItems) or (
        type(value) in (dict, list) and len(value) > _DESCEND_LEN
    )


def _batch_body(fragment: str, pad: str) -> str:
    """The elements of a whole-container ``json.dumps`` fragment, without
    its brackets, re-indented one level below *pad*.

    ``json.dumps(container, indent=n)`` of a non-empty container is the open
    bracket, a newline, the elements (each line prefixed by one indent
    step), a newline and the close bracket; dropping the first two and last
    two characters leaves exactly the element lines, which are then the
    same text the per-element path would have produced at this depth.
    """
    return pad + _repad(fragment[2:-2], pad)


def _iter_container(
    obj: dict[str, Any] | list[Any], *, indent: int, level: int
) -> Iterator[str]:
    """``json.dumps(obj, indent=indent)`` at *level*, streamed in batches."""
    step = " " * indent
    pad = step * level
    is_dict = type(obj) is dict
    items: list[Any] = list(obj.items()) if is_dict else list(obj)  # type: ignore[union-attr]
    open_, close = ("{", "}") if is_dict else ("[", "]")
    yield open_
    first = True
    batch = _INITIAL_BATCH
    i = 0
    n = len(items)
    while i < n:
        item = items[i]
        value = item[1] if is_dict else item
        if _descend_first(value):
            head = ("" if first else ",") + "\n" + pad + step
            if is_dict:
                head += json.dumps(item[0]) + ": "
            yield head
            yield from iter_json_indented(value, indent=indent, _level=level + 1)
            first = False
            i += 1
            continue
        stop = i + 1
        while stop < n and stop - i < batch:
            nxt = items[stop]
            if _descend_first(nxt[1] if is_dict else nxt):
                break
            stop += 1
        chunk = items[i:stop]
        try:
            fragment = _dumps(dict(chunk) if is_dict else chunk, indent)
        except TypeError:
            # Something json cannot encode as is (a LazyItems nested deeper
            # than one level): narrow the batch until the offending element
            # stands alone, then let the per-element path handle it --
            # which raises the same TypeError json.dumps would for anything
            # genuinely unserializable.
            if stop - i > 1:
                batch = max(1, (stop - i) // 2)
                continue
            head = ("" if first else ",") + "\n" + pad + step
            if is_dict:
                head += json.dumps(item[0]) + ": "
            yield head
            yield from iter_json_indented(value, indent=indent, _level=level + 1)
            first = False
            i += 1
            continue
        if len(fragment) > 2 * _TARGET_FRAGMENT_CHARS and stop - i > 1:
            batch = max(1, (stop - i) // 2)
            continue
        yield ("" if first else ",") + "\n" + _batch_body(fragment, pad)
        first = False
        i = stop
        if len(fragment) < _TARGET_FRAGMENT_CHARS // 2:
            batch *= 2
    yield "\n" + pad + close


def iter_json_indented(obj: Any, *, indent: int = 2, _level: int = 0) -> Iterator[str]:
    """Yield ``json.dumps(obj, indent=indent)`` one fragment at a time.

    A container wider than :data:`_DESCEND_LEN` is streamed in batches of
    consecutive elements, each batch one ``json.dumps`` call sized to about
    :data:`_TARGET_FRAGMENT_CHARS`, streaming on their own only elements that
    are themselves wide or a :class:`LazyItems`. A narrower container is
    encoded whole when it holds only scalars and descended into otherwise.
    The output is byte-identical to ``json.dumps(obj, indent=indent)``.
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

    if type(obj) is dict or type(obj) is list:
        if not obj:
            yield "{}" if type(obj) is dict else "[]"
            return
        if type(obj) is dict and not all(type(k) is str for k in obj):
            yield _repad(_dumps(obj, indent), pad)
            return
        if len(obj) > _DESCEND_LEN:
            yield from _iter_container(obj, indent=indent, level=_level)
            return
        if _shallow(obj, _SHALLOW_DEPTH):
            yield _repad(_dumps(obj, indent), pad)
            return
        items = obj.items() if type(obj) is dict else enumerate(obj)
        yield "{" if type(obj) is dict else "["
        for i, (key, value) in enumerate(items):
            head = ("," if i else "") + "\n" + inner_pad
            if type(obj) is dict:
                head += json.dumps(key) + ": "
            yield head
            yield from iter_json_indented(value, indent=indent, _level=_level + 1)
        yield "\n" + pad + ("}" if type(obj) is dict else "]")
        return

    yield _repad(_dumps(obj, indent), pad)


def join_json_indented(obj: Any, *, indent: int = 2) -> str:
    """The whole document as one string -- for callers that need one.

    Exists so a compressed write (which needs the full byte string anyway)
    and a test can share the streaming encoder rather than keeping a second
    formatting path that could drift from it.
    """
    return "".join(iter_json_indented(obj, indent=indent))

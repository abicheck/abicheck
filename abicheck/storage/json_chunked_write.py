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

"""Chunked, byte-identical ``json.dump`` for a very large tree.

Moved here from ``dumper_cache.py`` (the memory work): encoding JSON is not
the AST cache's responsibility, and this sits naturally beside
:mod:`abicheck.storage.json_stream`, its indent-aware sibling -- the two
answer the same question (write a large document without a second full-size
copy) for the two shapes this codebase writes, a compact clang AST cache and
an indented baseline. Kept as a separate module rather than merged into it
because the two share no code: the compact writer's bounds are tuned for an
AST of long template spellings, the indented one's for snapshots of short
ones, and folding them would mean one parameterised walker serving neither
well.

Behaviour, bounds and reasoning are unchanged from the original; see each
function's own docstring.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["_atomic_write_json", "_subtree_exceeds", "_write_json_chunked"]


#: Subtree size, in *nodes* (containers + scalars), at or below which
#: :func:`_write_json_chunked` hands a subtree to the one-shot C encoder
#: whole instead of descending into it. Sizing note: this is one of the two
#: knobs that bound peak transient memory, so it is deliberately modest.
_JSON_CHUNK_NODE_LIMIT = 200_000

#: The *other* bound, in estimated encoded bytes. A node count alone does not
#: bound the encoded size of a subtree: a DPC++ AST full of long
#: template-qualified spellings can hold well under
#: :data:`_JSON_CHUNK_NODE_LIMIT` nodes and still encode to hundreds of MiB,
#: which would be handed to a single ``json.dumps`` call and reintroduce
#: exactly the second full-size copy this writer exists to avoid (Codex
#: review, PR #1275). Both bounds are checked, so a subtree is delegated whole
#: only when it is small in *both* dimensions.
_JSON_CHUNK_BYTE_LIMIT = 8 << 20

#: Hard cap on how deep :func:`_write_json_chunked` descends before it
#: delegates a subtree whole regardless of size. Bounds this module's own
#: Python recursion independently of the size probe, which is also what makes
#: a cyclic input (impossible from ``json.load``, but not from a hand-built
#: dict) terminate here and raise from the C encoder's own circular-reference
#: check rather than blowing the stack in this walk. Deliberately well past
#: where a clang AST keeps anything *large*: the translation unit's top-level
#: ``inner`` list and the per-declaration subtrees under it are the first few
#: levels, and the genuinely deep nesting further down (expression trees) is
#: small by then, so the memory bound below is reached in practice long
#: before this cap is.
_JSON_CHUNK_MAX_DEPTH = 40

#: Encoded-fragment bytes buffered before one ``write`` call. Keeps the
#: structural fragments ("{", "\"inner\": [", ", ") from costing one
#: buffered-writer call each without ever holding a meaningful amount of the
#: document.
_JSON_WRITE_BUFFER = 1 << 18


def _subtree_exceeds(obj: object, limit: int, byte_limit: int = -1) -> bool:
    """Whether *obj* is too big to encode in one piece, either way it can be.

    Two independent bounds, because neither implies the other: more than
    *limit* JSON nodes, or an estimated encoded size over *byte_limit* (pass a
    negative value to check node count alone). A handful of nodes carrying very
    long strings is small by count and large by bytes, and that is the shape
    that made a node-only bound insufficient.

    The byte figure is a *lower-bound estimate*, not the exact encoding:
    strings contribute their own length plus quoting (escaping can only make
    the real output longer, never shorter), everything else a small constant.
    Under-estimating is the safe direction for a bound used to decide "small
    enough to encode whole" only when it answers ``False``.

    Stops the moment either answer is known, so the probe costs
    O(min(size, limit)) regardless of how large the subtree really is -- that
    bound is what makes it safe to call on the way down
    :func:`_write_json_chunked`'s descent rather than measuring the whole tree
    up front.
    """
    stack: list[object] = [obj]
    seen = 0
    weight = 0
    check_bytes = byte_limit >= 0
    while stack:
        cur = stack.pop()
        seen += 1
        if seen > limit:
            return True
        if type(cur) is dict:
            stack.extend(cur.values())
            if check_bytes:
                # Keys are encoded too; counted here rather than pushed, since
                # a key is never itself descended into.
                for key in cur:
                    weight += len(key) + 4 if type(key) is str else 8
        elif type(cur) is list:
            stack.extend(cur)
        elif check_bytes:
            weight += len(cur) + 2 if type(cur) is str else 8
        if check_bytes and weight > byte_limit:
            return True
    return False


def _write_json_chunked(obj: object, write: Any, depth: int = 0) -> None:
    """Write *obj* as JSON through *write*, one bounded chunk at a time.

    Byte-identical to ``json.dump(obj, f)`` -- same default separators
    (``", "``/``": "``), same ``ensure_ascii`` escaping, same key order --
    but **much** faster on a large tree, because ``json.dump`` does not use
    the C encoder at all: ``JSONEncoder.iterencode`` only selects
    ``c_make_encoder`` under ``_one_shot``, which is ``dumps``' path, not
    ``dump``'s. A streaming ``json.dump`` therefore encodes the entire
    document with the pure-Python fallback encoder, a fragment at a time
    (measured 27x slower than this function on a deep-template AST fixture,
    1.9x on a real header AST).

    The obvious alternative -- ``_atomic_write(path, json.dumps(obj)
    .encode())`` -- is what the streaming write was introduced to avoid: it
    holds the whole encoded document as a second full-size object on top of
    the tree itself, which is exactly the doubling this cache path cannot
    afford for a multi-GB DPC++ AST.

    So: descend through the *large* containers with Python (cheap -- there
    are few of them, and only their punctuation is written here) and hand
    every subtree that is small enough to the one-shot C encoder whole.
    "Small enough" is decided by :func:`_subtree_exceeds`, not by depth
    alone, so a single enormous namespace subtree is still split rather than
    encoded in one piece -- and by node count *and* estimated encoded bytes,
    since a few nodes holding very long strings are small by one measure and
    huge by the other. The peak transient string therefore stays bounded by
    :data:`_JSON_CHUNK_BYTE_LIMIT` no matter how the tree is shaped.

    One thing no bound here can split is a *single scalar*: a 100 MB string
    value encodes as one fragment, because JSON has nowhere to break it. That
    is inherent rather than overlooked -- the shapes this writer is for (an AST
    of many modest nodes) never contain one, and a caller that did would have
    the same peak with any encoder.

    Two shapes are deliberately delegated whole rather than descended into,
    both on the "never re-implement a coercion" principle: a ``dict``/``list``
    *subclass* (the exact-type tests below), and a ``dict`` with a non-``str``
    key. Both encode correctly this way -- only the memory bound relaxes, and
    neither occurs in a tree that came out of ``json.load``, which is the only
    thing this cache path ever writes.

    A dict with a non-``str`` key is delegated whole rather than split:
    ``json`` coerces such keys (``1`` -> ``"1"``, ``True`` -> ``"true"``) and
    re-deriving that coercion here would be a second implementation of it to
    keep byte-identical. Clang ASTs never contain one, so nothing is lost.
    """
    parts: list[str] = []
    size = 0

    def emit(fragment: str) -> None:
        nonlocal size
        parts.append(fragment)
        size += len(fragment)
        if size >= _JSON_WRITE_BUFFER:
            write("".join(parts))
            parts.clear()
            size = 0

    def walk(node: object, depth: int) -> None:
        if depth < _JSON_CHUNK_MAX_DEPTH and _subtree_exceeds(
            node, _JSON_CHUNK_NODE_LIMIT, _JSON_CHUNK_BYTE_LIMIT
        ):
            if type(node) is dict:
                if all(type(k) is str for k in node):
                    emit("{")
                    first = True
                    for key, value in node.items():
                        emit(
                            json.dumps(key) + ": "
                            if first
                            else ", " + json.dumps(key) + ": "
                        )
                        first = False
                        walk(value, depth + 1)
                    emit("}")
                    return
            elif type(node) is list:
                emit("[")
                first = True
                for value in node:
                    if not first:
                        emit(", ")
                    first = False
                    walk(value, depth + 1)
                emit("]")
                return
        emit(json.dumps(node))

    walk(obj, depth)
    if parts:
        write("".join(parts))


def _atomic_write_json(path: Path, obj: object) -> None:
    """Serialize *obj* as JSON straight into *path* via a same-directory
    temp file + ``os.replace``, without ever materializing the fully
    encoded document as one Python ``str``/``bytes`` object first.

    The encoding itself goes through :func:`_write_json_chunked` rather than
    ``json.dump`` -- same bytes, same bounded peak memory, without paying
    ``json.dump``'s pure-Python encoder for the whole document (see that
    function's own docstring for why ``dump`` never reaches the C encoder).
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _write_json_chunked(obj, f.write)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

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

"""Shared JSON container-node budget check (G40).

``json.loads()`` has no cap on the number of container nodes it
materializes, and that risk isn't specific to JSON *objects*: a payload of
millions of empty ``[]`` nodes allocates just as much as millions of empty
``{}`` nodes for the same reason, regardless of which shape the ignored
field uses (Codex review, fresh evidence -- a 100,000-array payload still
loads under an object-only budget sized just above a real snapshot's own
mapping count). ``object_pairs_hook`` is JSON's only public per-node
extension point and only ever fires for object nodes -- there is no
public ``parse_array`` hook, and reaching the pure-Python scanner that
*does* expose one (by overriding ``JSONDecoder.parse_array``) means losing
the C-accelerated scanner for every legitimate payload too (measured:
~3.7x slower on an ordinary 2 MB snapshot blob), which is a real cost paid
on every load, not just an adversarial one.

Instead, one linear pre-scan over the raw bytes counts every ``{``/``[``
that starts a container -- skipping whole string literals as a single
token so a string *value* containing a literal bracket is never
miscounted -- and raises as soon as the combined budget is exceeded,
before ``json.loads()`` ever runs. Measured to add no meaningful overhead
on a real payload (comparable to the JSON decode itself) while aborting a
container-bomb payload well before it would have been fully parsed.

The same pre-scan also tracks nesting *depth*, independent of node count:
this module's callers used to rely on ``json.loads()`` itself raising
``RecursionError`` for a pathologically deep ``[[[...]]]`` payload, but
that assumption is not portable across Python versions -- confirmed
empirically that CPython 3.14 parses 10,000 levels of array nesting with
no ``RecursionError`` at all (a real, reproduced regression, not a stale
test: the same payload silently decodes to a deeply nested ``list``
instead, which then fails a *later*, untranslated type check). Depth is
now bounded explicitly and deterministically here, before ``json.loads()``
ever gets a chance to (not) raise on its own.
"""

from __future__ import annotations

import re

#: Matches one JSON *token* that costs `json.loads()` its own Python-object
#: allocation: a string (consumed as a whole token, so a bracket inside a
#: string value is never separately counted), a container-boundary
#: character (open or close), a number, or a `true`/`false`/`null`
#: literal. Every one of these -- not just containers -- is a real
#: allocation cost (Codex review, sixth-order follow-up: a payload of
#: millions of scalar strings/numbers under an ignored field previously
#: contributed nothing here, since only container starts were counted).
#: The number pattern is deliberately loose (not a strict JSON-number
#: grammar) -- validating syntax is `json.loads()`'s job, not this scan's;
#: it only needs to consume one token per number without desyncing the
#: boundary for what follows. `re.DOTALL` so a literal newline inside a
#: string (invalid JSON, but likewise not this scan's job to reject)
#: can't desync the token boundary and leak the rest of the payload as
#: unmatched text.
_CONTAINER_TOKEN_RE = re.compile(
    rb'"(?:[^"\\]|\\.)*"|[{}\[\]]|-?\d[\d.eE+-]*|true|false|null', re.DOTALL
)

_OPEN_TOKENS = (b"{", b"[")
_CLOSE_TOKENS = (b"}", b"]")

#: Default combined object+array container-node budget for one
#: `check_json_container_budget()` call. Callers may pass a different
#: value; this is only the shared default used when neither caller
#: overrides it.
DEFAULT_MAX_JSON_CONTAINER_NODES = 1_000_000

#: Default nesting-depth budget. Deliberately far above any realistic
#: legitimate payload this codebase persists (a `BundleFacts`/`AbiSnapshot`
#: blob nests at most a few dozen levels deep -- with one deliberate
#: exception: `tests/test_bundle_facts_archive.py`'s own recursion-on-
#: clone regression test nests to 900 specifically so `json.loads()`
#: still succeeds and a *later* `copy.deepcopy()` is what raises, so this
#: budget stays comfortably above that) and comfortably below the
#: pathological depths (10,000+) a hostile payload would use, so this
#: check is the one that fires deterministically regardless of whichever
#: (version-dependent) point `json.loads()` itself would or wouldn't have
#: raised `RecursionError` at.
DEFAULT_MAX_JSON_NESTING_DEPTH = 2_000


class JsonContainerBudgetExceeded(Exception):
    """Raised by :func:`check_json_container_budget` once *raw* would
    decode to more than the given budget of container nodes.

    Not a ``ValueError`` -- a caller's own ``except (UnicodeDecodeError,
    ValueError)`` JSON-syntax-error handling must never swallow this."""


class JsonNestingTooDeepError(Exception):
    """Raised by :func:`check_json_container_budget` once *raw* nests
    containers deeper than the given depth budget.

    A separate exception from :class:`JsonContainerBudgetExceeded` --
    depth and node count are independent dimensions (a payload can have
    very few containers that nest very deeply, or very many containers
    that never nest at all), and a caller's existing ``except
    RecursionError`` translation is exactly the message this should also
    map to, not the container-count one."""


def check_json_container_budget(
    raw: bytes,
    max_container_nodes: int,
    *,
    max_nesting_depth: int = DEFAULT_MAX_JSON_NESTING_DEPTH,
) -> None:
    """Raise :class:`JsonContainerBudgetExceeded` once *raw* would cost
    `json.loads()` more than *max_container_nodes* Python-object
    allocations -- every container start (object/array) *and* every
    scalar leaf (string, number, ``true``/``false``/``null``) outside a
    string literal counts, since each is its own allocation regardless of
    shape -- or :class:`JsonNestingTooDeepError` once *raw* nests
    containers deeper than *max_nesting_depth* (a container-only measure;
    a scalar leaf never nests).

    A pure pre-check: never decodes *raw*, never allocates a container of
    its own, and stops scanning the instant either budget is exceeded
    rather than walking the whole payload first."""
    count = 0
    depth = 0
    for match in _CONTAINER_TOKEN_RE.finditer(raw):
        token = match.group()
        if token in _OPEN_TOKENS:
            count += 1
            if count > max_container_nodes:
                raise JsonContainerBudgetExceeded(count)
            depth += 1
            if depth > max_nesting_depth:
                raise JsonNestingTooDeepError(depth)
        elif token in _CLOSE_TOKENS:
            # A close with no matching open (malformed JSON) is not this
            # pre-check's job to reject -- json.loads() reports that on
            # its own, with its own precise error. `depth > 0` just keeps
            # this loop from ever going negative on such input.
            if depth > 0:
                depth -= 1
        else:
            # A scalar leaf (string, number, true/false/null) -- each is
            # its own Python object json.loads() allocates, so it counts
            # toward the same budget a container node does. Never nests,
            # so `depth` is untouched.
            count += 1
            if count > max_container_nodes:
                raise JsonContainerBudgetExceeded(count)

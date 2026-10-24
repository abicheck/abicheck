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

"""Building :class:`AbiSnapshot`'s three first-wins lookup indexes.

Moved out of ``snapshot.py`` (schema v47) rather than left there: that module
sits on ADR-061's 800-line model ceiling, and the rule for a module at its
ceiling is to give a responsibility an owner, not to trim the file to fit.
This is one responsibility -- turn the three declaration lists into
mangled/name-keyed maps, first entry wins, and report what that dropped.

What stays behind in ``AbiSnapshot.index()`` is the *caching* contract
(idempotence, and the reset protocol a caller mutating a snapshot in place
owes); that is about the object's lifecycle, not about how an index is built.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .first_wins_index import build_first_wins_index, warn_dropped

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .declarations import Function, Variable
    from .entities import RecordType

_log = logging.getLogger(__name__)


def build_snapshot_indexes(
    functions: Sequence[Function],
    variables: Sequence[Variable],
    types: Sequence[RecordType],
    owner: str,
) -> tuple[dict[str, Function], dict[str, Variable], dict[str, RecordType]]:
    """The functions-, variables- and types-by-key maps, in that order.

    Each is first-wins over a duplicate key, and each drop is logged against
    *owner* (the caller's ``library@version``) so a warning names the side it
    came from -- a comparison indexes two snapshots, and an unattributed
    "duplicate mangled symbol" line cannot say which.

    Takes the three collections rather than the ``AbiSnapshot`` itself: the
    caller is ``snapshot.py``, so accepting the object would close an import
    cycle the ``import-cycle-growth`` gate rejects (it did, on the first
    version of this module, even with the import under ``TYPE_CHECKING``).
    The narrower signature is the better one anyway -- nothing here needs a
    whole snapshot.
    """
    func_index = build_first_wins_index(functions, lambda f: f.mangled)
    warn_dropped(_log, "mangled symbols", owner, func_index.dropped)

    var_index = build_first_wins_index(variables, lambda v: v.mangled)
    warn_dropped(_log, "mangled variables", owner, var_index.dropped)

    # Keyed on the qualified identity -- the same ``qualified_name or name``
    # key ``compare.type_map_pair.type_map_key`` matches old/new types by
    # (not imported: model may not depend on compare). Header backends keep a
    # bare ``name`` and the scope in ``qualified_name``, so keying on ``name``
    # merged ``algo_a::Parameter`` and ``algo_b::Parameter`` into one entry
    # and warned about a "duplicate" that was two distinct types.
    type_index = build_first_wins_index(types, _qualified_key)
    warn_dropped(_log, "type names", owner, type_index.dropped)

    return func_index.mapping, var_index.mapping, _with_bare_aliases(type_index.mapping)


def _qualified_key(t: RecordType) -> str:
    return t.qualified_name or t.name


def _with_bare_aliases(by_qualified: dict[str, RecordType]) -> dict[str, RecordType]:
    """*by_qualified* plus a bare-``name`` alias for every record whose bare
    name belongs to exactly one qualified identity.

    A caller holding only a bare spelling still resolves an unambiguous
    type; an ambiguous bare name (two namespaces declare it) gets no alias,
    so the lookup misses rather than answering with whichever came first.
    An alias never shadows a real qualified key.
    """
    owners: dict[str, set[str]] = {}
    for key, rec in by_qualified.items():
        owners.setdefault(rec.name, set()).add(key)
    out = dict(by_qualified)
    for bare, keys in owners.items():
        if len(keys) == 1 and bare not in out:
            out[bare] = by_qualified[next(iter(keys))]
    return out

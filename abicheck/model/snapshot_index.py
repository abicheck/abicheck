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

    type_index = build_first_wins_index(types, lambda t: t.name)
    warn_dropped(_log, "type names", owner, type_index.dropped)

    return func_index.mapping, var_index.mapping, type_index.mapping

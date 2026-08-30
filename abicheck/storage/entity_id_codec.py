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

"""Keep the ADR-063 Phase 2 ``entity_id`` carrier out of the wire format.

``RecordType``/``EnumType``/``Function``/``Variable`` each carry a
parse-time-resolved ``model.identity.EntityId`` since ADR-063 Phase 2's
third slice (the plan's option (a)). That field is **runtime-only**: this
module drops it from the ``dataclasses.asdict()`` output
``serialization.snapshot_to_dict`` builds, so no snapshot document contains
it and ``SCHEMA_VERSION`` does not move.

Why dropped rather than encoded, in this slice specifically: a faithful
encoding has to preserve ``ScopePath``'s *typed segments*. Flattening them
into one string is not a reversible bridge — a record nested in a record
and the same bare names nested in a namespace render to the identical
string, so a reload would silently merge two distinct identities. The plan
already specifies the real fix (a ``ScopePath``-preserving v2 wire schema on
``storage/entity_ids.py``'s DTO pair, with its own migration adapter) and
scopes it as its own reviewable slice; inventing a second, lossy encoding
here to avoid waiting for it would be exactly the "one concept, two
representations" outcome that plan's Governing Invariant forbids.

The carrier is dropped *after* ``asdict()`` rather than being made
invisible to it, because the alternatives are worse: a ``dataclasses``
field is always included in ``asdict()`` output, and the one construct that
escapes it (an ``InitVar`` pseudo-field assigned onto ``self``) is not
expressible without failing ``mypy``'s ``attr-defined``, which is a
required gate here. The cost is one small dict per declaration built and
immediately discarded during a snapshot write — the same order as the
``Fact[...]`` siblings ``encode_fact_fields`` already re-walks.

Mirrors ``storage/fact_codec.py``'s ``encode_fact_fields`` in shape: an
in-place fix-up over the already-``asdict()``-ed snapshot dict, owned by
``storage`` (which may depend on ``model``) rather than inlined into
``serialization.py``, itself already at this repo's file-size cap.
"""

from __future__ import annotations

from typing import Any

__all__ = ["drop_entity_ids"]

#: Snapshot keys holding lists of declaration dicts that carry the field.
#: ``typedefs``/``constants`` are ``dict[str, str]`` on ``AbiSnapshot``, not
#: dataclasses, so they have no carrier to drop (and none to populate).
_DECLARATION_LIST_KEYS = ("types", "enums", "functions", "variables")


def drop_entity_ids(d: dict[str, Any]) -> dict[str, Any]:
    """Remove every ``entity_id`` key from a snapshot dict, in place.

    Returns the same dict it was given so the caller can chain it into the
    conversion pipeline it already runs, rather than spending a separate
    statement on a fix-up that is part of one wire-encoding step.
    """
    for list_key in _DECLARATION_LIST_KEYS:
        for decl_dict in d.get(list_key, []) or []:
            if isinstance(decl_dict, dict):
                decl_dict.pop("entity_id", None)
    return d

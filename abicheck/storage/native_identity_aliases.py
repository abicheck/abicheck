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

"""The `ArtifactRef.native_identity` key contract for a library's real
on-disk filename/filesystem aliases, plus the alias list's own encode/decode
pair -- split out of `bundle_facts_store.py` (ADR-062 A1.4/A1.5's writer)
into this `storage`-classified leaf module so `abicheck.bundle` can read the
same evidence back (`build_bundle_snapshot_mixed`, ADR-062 A1.7) without
creating a `bundle -> bundle_facts_store -> bundle_facts -> bundle` import
cycle: `bundle_facts_store.py` is `workflows`-classified and imports
`bundle_facts.py` at module load, which itself reaches back into `bundle.py`
via a function-local import (`bundle_snapshot_from_facts`) -- so a second,
*new* `bundle -> bundle_facts_store` edge closes a cycle
`scripts/check_ai_readiness.py`'s `import-cycle-growth` check rejects
outright (see `AGENTS.md`'s "Don't ... extend `IMPORT_CYCLE_ALLOWLIST`" —
the fix is a shared leaf module both sides depend on, not an allowlist
entry). This module depends on nothing but `storage.json_budget` (same
package) and the stdlib, so importing it from either side introduces no
cycle in either direction.

`bundle_facts_store.py` is still the single writer of these keys
(`write_bundle_facts_package`) and the sole owner of the *third*,
unrelated `native_identity` key it also defines
(`_NATIVE_IDENTITY_LIBRARY_NAME_KEY` — a fact this module doesn't need,
since it only carries filename/alias evidence, not identity). See that
module's own docstring for why there are two independent, not-yet-
reconciled multi-artifact package writers stamping these same two string
keys (this one and `storage/import_bundle_facts.py`'s `filesystem_aliases`/
`library_filenames` document fields, a related but distinct contract).
"""

from __future__ import annotations

import json

from .json_budget import (
    DEFAULT_MAX_JSON_CONTAINER_NODES as DEFAULT_MAX_JSON_CONTAINER_NODES,
    JsonContainerBudgetExceeded,
    check_json_container_budget,
)

__all__ = [
    "DEFAULT_MAX_JSON_CONTAINER_NODES",
    "NATIVE_IDENTITY_ALIASES_KEY",
    "NATIVE_IDENTITY_FILENAME_KEY",
    "decode_native_identity_aliases",
    "encode_native_identity_aliases",
]

#: `ArtifactRef.native_identity` keys a library's real on-disk filename and
#: filesystem aliases (symlink targets, hard-link aliases) are stamped
#: under -- see `bundle_facts_store.py`'s own module docstring for the full
#: "genuinely project-level vs. per-artifact" design note these keys come
#: from. The string values themselves are the real cross-writer/cross-reader
#: contract, not any one module's own private name for it.
NATIVE_IDENTITY_FILENAME_KEY = "library_filename"
NATIVE_IDENTITY_ALIASES_KEY = "filesystem_aliases"


def encode_native_identity_aliases(aliases: tuple[str, ...]) -> str:
    """*aliases*, folded into one `native_identity` string value.

    JSON, not a delimiter-joined string: POSIX allows a newline (or any
    byte but NUL/`/`) inside a real filename, so a filesystem-alias
    basename is not guaranteed delimiter-safe -- a joined-and-split
    encoding would silently split one alias into two, or merge two into
    one, changing resolution evidence (Codex review). `json.dumps` of a
    list of strings has no such ambiguity.
    """
    return json.dumps(sorted(aliases))


def decode_native_identity_aliases(
    encoded: str, nodes_so_far: int
) -> tuple[tuple[str, ...], int]:
    """The exact inverse of `encode_native_identity_aliases`, returning the
    decoded tuple alongside *nodes_so_far* updated with this array's own
    node count.

    `check_json_container_budget` runs first, capped to the *remaining*
    cross-caller allowance rather than the full budget every time: an
    untrusted array of millions of short strings can stay well under an
    aggregate byte budget while still costing `json.loads()` one Python-
    object allocation per element (a node-count amplification a byte-size
    charge alone cannot see), and capping only *one* array in isolation is
    not enough either -- many artifacts can each carry an array
    individually under the limit while summing far past it in aggregate.
    The pre-scan bounds this one array against what's left of the caller's
    own running budget; the actual decoded element count is then charged
    into that running total, which the caller is responsible for carrying
    across calls the same way `bundle_facts_store.read_bundle_facts_package`
    does.
    """
    remaining_nodes = max(DEFAULT_MAX_JSON_CONTAINER_NODES - nodes_so_far, 0)
    check_json_container_budget(encoded.encode("utf-8"), remaining_nodes)
    decoded = json.loads(encoded)
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError(
            f"native_identity[{NATIVE_IDENTITY_ALIASES_KEY!r}] must decode to a "
            f"JSON array of strings, got {encoded!r}"
        )
    # +1 for the array node itself, matching what `check_json_container_
    # budget` itself counts (every container start plus every scalar leaf).
    nodes_so_far += len(decoded) + 1
    if nodes_so_far > DEFAULT_MAX_JSON_CONTAINER_NODES:
        raise JsonContainerBudgetExceeded(nodes_so_far)
    return tuple(decoded), nodes_so_far

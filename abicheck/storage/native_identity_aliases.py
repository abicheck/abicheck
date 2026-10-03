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

"""Read-only decoder for the `ArtifactRef.native_identity` filename and
filesystem-alias keys of older multi-artifact packages.

The writer that stamped these keys (`bundle_facts_store.py`'s
`write_bundle_facts_package`) is gone: a stored bundle now records filenames
and aliases as plain lists in its bundle-composition section
(`storage/bundle_facts_codec.py`, `storage/import_bundle_facts.py`), and
`storage/bundle_facts_package.py` retires filename/alias facts on
`native_identity` for that path. `abicheck.bundle` still reads them back from
an older package (`_stored_library_identity`), which is the only reason this
module exists. It depends on nothing but `storage.json_budget` and the
stdlib, so `bundle` can import it without an import cycle.
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
]

#: `ArtifactRef.native_identity` keys a library's real on-disk filename and
#: filesystem aliases (symlink targets, hard-link aliases) were stamped
#: under by older packages. The string values are the on-disk contract.
NATIVE_IDENTITY_FILENAME_KEY = "library_filename"
NATIVE_IDENTITY_ALIASES_KEY = "filesystem_aliases"


def decode_native_identity_aliases(
    encoded: str, nodes_so_far: int
) -> tuple[tuple[str, ...], int]:
    """Decode a `native_identity` alias value (a JSON array of strings, as
    older packages wrote it with `json.dumps(sorted(aliases))`), returning the
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
    across calls.
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

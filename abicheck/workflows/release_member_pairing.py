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

"""Pair release members the file name could not pair, by the library they are.

A release compare pairs members by ``build_match_map``'s key, which is
derived from each member's *file name*. A stored ``abicheck dump`` document
is often not named like the library it records -- ``libfoo.json`` holds a
dump of ``libfoo.so.1`` -- so a stored directory against the live directory
it was dumped from paired nothing (exit 1, ``no_comparison_completed``),
while the same two files compared as scalar operands normally.

:func:`identity_renames` is the one second pass every pairing route applies
after the file-name intersection: a member left unmatched on *both* sides is
paired with its counterpart when both record the same library identity
(``extract.member_identity``: a stored snapshot's ``AbiSnapshot.library``, a
native file's own name). It is deliberately conservative, so it can never
move a pair the file name already made:

* only members unmatched on both sides take part;
* an identity is used only when it names exactly one leftover member on
  each side, and no file-name pair already holds it -- one library is one
  member, so a second document claiming an already-paired library stays
  unmatched rather than displacing anything.

A newly paired NEW member takes its OLD counterpart's key; its file, and
so its display name, is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.bundle_facts import BundleFacts


def path_identity(key: str, path: Path) -> str:
    """The release key of the library *path* is: the key of the library a
    stored snapshot records, else the member's own *key* (a native file, a
    directory-backed package member, a document recording no library)."""
    from ..binary_utils import _canonical_library_key
    from ..extract.member_identity import recorded_library_name

    recorded = recorded_library_name(path) if path.is_file() else None
    return _canonical_library_key(Path(recorded)) if recorded else key


def _unique_by_identity(
    members: Iterable[str],
    leftover: set[str],
    identity: Callable[[str], str],
) -> tuple[dict[str, str], set[str]]:
    """``({identity: key} for identities unique among *leftover*, identities
    held by the paired members)``."""
    counts: dict[str, list[str]] = {}
    for key in leftover:
        counts.setdefault(identity(key), []).append(key)
    unique = {ident: keys[0] for ident, keys in counts.items() if len(keys) == 1}
    held = {identity(key) for key in members if key not in leftover}
    return unique, held


def identity_renames(
    old_keys: Iterable[str],
    new_keys: Iterable[str],
    *,
    old_identity: Callable[[str], str],
    new_identity: Callable[[str], str],
) -> dict[str, str]:
    """``{NEW key: OLD key}`` for each file-name-unpaired NEW member that
    records the same library as a file-name-unpaired OLD member (the rules
    in this module's docstring). *old_identity*/*new_identity* give a member
    key's identity on that side. Only NEW keys are renamed, so a keyed stored
    document on the OLD side needs no rewrite; a leftover OLD key is by
    definition absent on NEW, so a rename cannot collide."""
    old_set, new_set = set(old_keys), set(new_keys)
    old_left, new_left = old_set - new_set, new_set - old_set
    if not old_left or not new_left:
        return {}
    old_unique, old_held = _unique_by_identity(old_set, old_left, old_identity)
    new_unique, new_held = _unique_by_identity(new_set, new_left, new_identity)
    return {
        new_unique[ident]: old_unique[ident]
        for ident in sorted((set(old_unique) & set(new_unique)) - old_held - new_held)
    }


def pair_path_maps_by_identity(
    old_map: Mapping[str, Path], new_map: Mapping[str, Path]
) -> dict[str, Path]:
    """*new_map* with :func:`identity_renames` applied, for two ``key ->
    member file`` maps."""
    renames = identity_renames(
        old_map,
        new_map,
        old_identity=lambda key: path_identity(key, old_map[key]),
        new_identity=lambda key: path_identity(key, new_map[key]),
    )
    return {renames.get(key, key): path for key, path in new_map.items()}


def snapshot_identity(key: str, snapshot: AbiSnapshot) -> str:
    """:func:`path_identity` for a member already loaded from a stored
    document: the key of the library its snapshot records, else *key*."""
    from ..binary_utils import _canonical_library_key

    library = (snapshot.library or "").strip().replace("\\", "/")
    name = Path(library).name if library else ""
    return _canonical_library_key(Path(name)) if name else key


def facts_paired_by_identity(old: BundleFacts, new: BundleFacts) -> BundleFacts:
    """*new* with :func:`identity_renames` applied against *old* (both
    stored ``BundleFacts`` documents)."""
    from .bundle_facts_capture import rekey_facts

    renames = identity_renames(
        old.per_library_snapshots,
        new.per_library_snapshots,
        old_identity=lambda k: snapshot_identity(k, old.per_library_snapshots[k]),
        new_identity=lambda k: snapshot_identity(k, new.per_library_snapshots[k]),
    )
    return rekey_facts(new, renames) if renames else new

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

"""Weakest-constituent ELF-binding evidence for a rolled-up
``SYMBOL_RENAMED_BATCH`` finding.

Both batch-rename shapes (``diff_symbols_renames.emit_prefix_batch_rename``'s
common-prefix rollup and ``compare.namespace_move.emit_namespace_move_batches``'s
namespace-segment-move rollup) need the same answer to "was every constituent
pair actually matched against a real, ELF-backed old-side declaration, or was
at least one only header-reconstructed" -- ``SYMBOL_RENAMED_BATCH`` is a
member of ``checker_policy._ELF_BINDING_STAMPED_KINDS``, so an unset
``Change.symbol_binding`` downgrades the whole batch to
``EvidenceStatus.UNATTRIBUTED`` on an ``elf``-tiered run. The two shapes
differ only in how a constituent resolves to its old-side ``Function`` --
by demangled name for a prefix rename, by ``"::"``-joined scope-chain
identity for a namespace move -- so that resolution is the one thing left to
each caller, passed in as *key_fn*.

Per ADR-061 D1 / ``compare/AGENTS.md``: this is genuinely new comparison
behavior (matching old-side declarations to identify what evidence backs a
raw change), so it belongs here rather than growing either legacy
``diff_symbols_renames.py`` monolith or duplicating itself across both call
sites (Codex review, fresh evidence -- the two call sites had started
independently reimplementing the identical reverse-index-then-lookup shape).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from ..model import Function


def all_constituents_elf_bound(
    constituent_keys: Iterable[str],
    old_map: Mapping[str, Function] | None,
    key_fn: Callable[[Function], str | None],
) -> bool:
    """True when every constituent key resolves to exactly one OLD-side
    declaration that itself carries a real observed ELF binding
    (``Function.elf_binding``) -- the same source
    ``diff_symbols._check_removed_function`` stamps ``Change.symbol_binding``
    from for a standalone ``FUNC_REMOVED`` finding.

    *old_map* being ``None`` (a caller with no map to offer) conservatively
    answers ``True`` -- the same "assume evidence was examined" default
    ``evidence_status_for_result``'s own ``evidence_tiers=()`` fallback uses
    -- so a caller that can't thread the map through gets prior behavior.

    *key_fn* maps an OLD-side ``Function`` to the identity key a constituent
    is looked up by (a demangled name, a scope-chain id, ...); ``None``
    means that declaration carries no resolvable identity for this shape and
    is excluded from the reverse index rather than colliding under a bogus
    key. An ambiguous (2+ declarations sharing one key) or unresolved
    constituent counts as *not* bound, never silently promoted to "confirmed
    bound" -- a direct membership test against *old_map*'s own raw keys
    (mangled symbols) previously matched nothing for either shape's own key
    space, making the aggregate vacuously ``True`` regardless of real
    evidence (Codex review, Finding C(i) and its fresh-evidence follow-up).
    """
    if old_map is None:
        return True
    by_key: dict[str, list[Function]] = {}
    for f in old_map.values():
        key = key_fn(f)
        if key is None:
            continue
        by_key.setdefault(key, []).append(f)
    for constituent in constituent_keys:
        matches = by_key.get(constituent)
        if matches is None or len(matches) != 1 or not matches[0].elf_binding:
            return False
    return True

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

"""Build-context reconciliation of context-free header-parse artifacts (ADR-039).

A public header parsed **context-free** — with no compile database to supply the
build's ``-D`` defines — can compute a *different* record layout than what was
actually built, whenever a field lives inside a ``#if defined(GUARD)`` region.
The context-free parse evaluates the guard as inactive and **prunes** the field,
so comparing two such snapshots raises a **false positive**: a
``type_field_removed`` (or ``type_field_added``) for a field whose *real*
presence never changed between the two builds.

This module clears exactly that false positive, and *only* that one, using the
higher-evidence build context carried on the snapshot:

* :attr:`~abicheck.model.AbiSnapshot.conditional_fields` — a
  ``{type: {field: guard}}`` registry of the fields a header parse knows are
  guarded, *whether or not* the context-free parse pruned them from ``fields``;
* :attr:`~abicheck.model.AbiSnapshot.build_context_defines` — the macros the
  build actually defines.

The registry carries each guarded field's **full declaration** (guard + type +
bit-field shape + C++ access + cv/mutable qualifiers), not just its guard. Each
side is evaluated with **its own**
registry and defines (Codex review #498): a guard the *new* header adds must not
be applied to the *old* build's field. A field's *effective declaration* on a
side is: taken from ``fields`` when present (unless guarded here by an undefined
macro), or from the registry entry when the context-free parse pruned it (and its
guard is defined here). A **field-presence** finding is reconciled only when the
two sides' effective *declaration* maps are **equal** — so the presence delta the
context-free parse saw was never real, *and* no pruned field's type changed.

**Soundness (Codex review #498, P1).** Only field-presence findings
(``type_field_added`` / ``type_field_removed``) are reconcilable. Build defines
prove field *presence*, not record *size* / *offset* / *alignment*, so
``type_size_changed`` and ``type_field_offset_changed`` are **never** cleared —
a real size/alignment change co-located with a pruned guarded field survives. A
correctly build-aware snapshot carries the artifact-accurate size, so a pure
context-free-pruning artifact shows up as a field-presence finding with *no*
size delta; anything with a residual size delta is kept. And because the whole
*declaration* is compared, a pruned field whose type changed (e.g. ``int`` →
``unsigned``) makes the maps differ and is kept.

**Authority rule (ADR-028 D3) preserved.** An unconditional add/remove, or a
guard that resolves differently between the two builds, makes the real field
sets differ and is kept. With no build evidence the pass is a no-op.

The ``conditional_fields`` registry is the intended output of a
build-context-aware header parse; a plain context-free castxml parse leaves it
empty, so this pass is a safe no-op on today's default dumps. See ADR-039 for
the collection-layer complement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot, RecordType

# Only field-*presence* findings are reconcilable. Whole-record layout findings
# (size / offset) are deliberately excluded: build defines prove presence, not
# that the built record size or field offsets are identical.
_RECONCILABLE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.TYPE_FIELD_REMOVED,
    }
)

#: Reason code stamped on a reconciled finding for the audit ledger.
RECONCILE_REASON = "build-context-reconciled"


def _effective_decls(
    rec: RecordType, registry: dict[str, dict[str, object]], defines: set[str]
) -> dict[str, tuple[object, ...]]:
    """The *declarations* of fields effectively present on **this** side's record.

    Maps ``field name -> (type, is_bitfield, bitfield_bits, access, is_const,
    is_volatile, is_mutable)`` for every field really present in this build,
    using only this side's own registry and defines. A field **observed** in
    ``rec.fields`` is authoritative *unless* this side's registry marks it as
    ``#ifndef``-guarded (``negative``) by a macro this build **defines** — the
    context-free parse saw it (macro undefined ⇒ guard true), but the real build
    prunes it, so it is dropped (Codex review #498). A field known **only** from
    this side's registry — a positive ``#ifdef`` the context-free parse pruned —
    is present iff its guard is defined here. Carrying the full declaration (type,
    bit-field, C++ access, and cv/mutable qualifiers) means a pruned field whose
    type, access, *or* qualifiers changed is not mistaken for an unchanged one
    (Codex review #498, P2): a ``const int``→``int`` change on a guarded field
    keeps the maps unequal, so the finding is kept rather than reconciled away.
    """
    out: dict[str, tuple[object, ...]] = {}
    for f in rec.fields:
        entry = registry.get(f.name)
        if entry is not None and entry.get("negative") and entry.get("guard") in defines:
            continue  # #ifndef-guarded field the defining build really prunes
        out[f.name] = (
            f.type,
            f.is_bitfield,
            f.bitfield_bits,
            f.access.value,
            f.is_const,
            f.is_volatile,
            f.is_mutable,
        )
    for name, entry in registry.items():
        if name in out or entry.get("negative"):
            continue  # negative entries correspond to observed fields, handled above
        guard = entry.get("guard")
        if guard is not None and guard in defines:
            out[name] = (
                entry.get("type"),
                bool(entry.get("is_bitfield", False)),
                entry.get("bitfield_bits"),
                str(entry.get("access", "public")),
                bool(entry.get("is_const", False)),
                bool(entry.get("is_volatile", False)),
                bool(entry.get("is_mutable", False)),
            )
    return out


def reconcile_build_context(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[list[Change], list[Change]]:
    """Split *changes* into ``(kept, reconciled)`` using build-context evidence.

    A field-presence finding is *reconciled* (moved out of the verdict, kept for
    audit) only when the two sides' effective field *declarations* — each
    computed from that side's own registry and defines — are equal. Comparing
    declarations (name → type/bit-field), not just names, keeps a pruned guarded
    field whose *type* changed visible.

    A no-op (returns ``(changes, [])``) unless **both** snapshots carry
    ``build_context_defines`` *and* at least one carries ``conditional_fields``.
    Requiring build defines on *both* sides matters (Codex review #498):
    ``_effective_decls`` trusts a side's observed fields as build-authoritative,
    which is only valid when that side was parsed build-aware. A context-free
    side (no defines) could have kept or pruned a field wrongly, so a mixed
    build-aware/context-free pair is never reconciled.
    """
    have_evidence = (
        bool(old.build_context_defines)
        and bool(new.build_context_defines)
        and (old.conditional_fields or new.conditional_fields)
    )
    if not have_evidence:
        return changes, []

    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}

    kept: list[Change] = []
    reconciled: list[Change] = []
    for change in changes:
        if change.kind not in _RECONCILABLE_KINDS:
            kept.append(change)
            continue
        t_old = old_types.get(change.symbol)
        t_new = new_types.get(change.symbol)
        if t_old is None or t_new is None:
            kept.append(change)
            continue

        # Exact-match only: the registry is keyed by the same ``RecordType.name``
        # the finding carries. No unqualified-tail fallback — a bare global ``S``
        # must never borrow an unrelated ``api::S`` registry (Codex review #498),
        # which would let a real removal be reconciled away.
        old_registry = old.conditional_fields.get(change.symbol, {})
        new_registry = new.conditional_fields.get(change.symbol, {})
        if not (old_registry or new_registry):
            kept.append(change)
            continue

        # Per-side effective declarations: each side resolves its own guards under
        # its own defines. If the two sides' declaration maps match, the presence
        # delta the context-free parse saw was never real *and* no pruned field's
        # declaration changed → the finding is an artifact.
        decls_old = _effective_decls(t_old, old_registry, old.build_context_defines)
        decls_new = _effective_decls(t_new, new_registry, new.build_context_defines)
        if decls_old == decls_new:
            change.surface_exclusion_reason = RECONCILE_REASON
            change.evidence_category = "build_context"
            reconciled.append(change)
        else:
            kept.append(change)
    return kept, reconciled

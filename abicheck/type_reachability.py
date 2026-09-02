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

"""Direct-reference reachability for standard-library/runtime-namespaced types
(status-review item 3: "direct vs transitive type reachability").

Every ``diff_*`` module that filters out ``std::``/``__gnu_cxx::``/etc. types
(:func:`abicheck.name_classification.is_non_abi_surface_type`) does so purely
by matching a type's own *name* against
:data:`abicheck.name_classification.STDLIB_TYPE_NAMESPACE_PREFIXES` — this is
correct for the common case (a stdlib type reached only through deep
template-instantiation internals, e.g. ``std::_Rb_tree_node_base`` or
``std::string::_Alloc_hider``, is real toolchain-artifact churn, not the
library's own ABI surface) but treats that identically to a stdlib type used
**directly** in a public function's own signature (e.g. ``void
foo(std::string s)``) or as a public (non-stdlib) type's own field — a case
where the library's ABI genuinely does depend on that stdlib type's layout,
and blanket-filtering it can hide a real, consumer-visible break (e.g. a
libstdc++ dual-ABI flip affecting every public function taking ``std::string``
by value).

This module computes, from an :class:`abicheck.model.AbiSnapshot` alone (no
build/source integration needed), which stdlib-namespaced type names are
*directly* referenced by a non-stdlib declaration's own signature — i.e.
reachable at distance one from the public surface, as opposed to only
reachable transitively via deep instantiation chains never named anywhere
outside the standard library itself.

**Wired into `diff_types.py`'s ``RecordType``-based detectors** (struct/
union size, alignment, fields, bases, vtable, kind, reserved fields,
qualifiers, renames, deprecation) via the shared ``_is_abi_surface_type``
gate. The remaining ~14 ``is_non_abi_surface_type``/``is_abi_surface_type_name``
call sites across ``diff_platform.py``/``diff_symbols.py``/
``diff_vtable_layout.py``/``diff_stdlib_impl.py``/``diff_layout.py``/
``diff_filtering.py``/``diff_type_spellings.py``, plus ``diff_types.py``'s
own enum/typedef paths, remain unwired — each needs its own site
individually verified against the FP-rate/mutation-score gates (this
codebase's test-quality guards exist specifically to catch exactly this
kind of change going wrong), a scoped follow-up rather than a drive-by
extension here.

**Known gap, deliberately not attempted here (Codex review, fresh
evidence): this module has no notion of "the library under comparison IS
the C++ runtime itself" the way :func:`abicheck.model.
stdlib_namespaces_excluded` does.** That function flips OFF the blanket
``std::``-filtering elsewhere in the pipeline when either side's
``library``/ELF SONAME names libstdc++/libc++ (via
:func:`abicheck.name_classification.is_cxx_runtime_library`), since for
those libraries ``std::`` is the actual surface under test, not a
dependency. This module's every declaration-seeding check
(:func:`_is_public_non_stdlib_declaration`) and its whole stdlib/non-stdlib
partition (:func:`_partition_snapshot_types`) instead hard-code the
opposite premise throughout — a ``std::``-namespaced declaration can never
seed the scan, and every ``std::``-namespaced ``RecordType`` is
unconditionally a *target* to search for, never a candidate *root*. For a
real libstdc++/libc++ comparison this inverts: confirmed empirically that a
public ``std::api(vector<int>)``-shaped declaration is rejected as a seed
by :func:`_is_public_non_stdlib_declaration`'s first check
(``decl.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)``) regardless of
which library is being compared, so this module's direct-reference proof
never fires for that pairing. A real fix is not a one-line threshold
change at that single check site: it needs a `library`-aware notion of
"root" flowing through both the seeding check *and* the stdlib/non-stdlib
partition consistently (a `std::`-owned declaration would need to become a
valid *root*, and reasoning about "direct reference to a stdlib
dependency" stops meaning anything once the dependency and the library are
the same thing) — its own scoped design matching
``stdlib_namespaces_excluded``'s existing library-detection convention,
not a drive-by extension of one check. Until then, a libstdc++/libc++
self-comparison under ``contract=public`` degrades exactly like every
other unresolvable case this module documents throughout: a genuine
template-layout finding on such a comparison falls back to
``UNKNOWN_UNRESOLVED`` rather than confirming ``IN_CONTRACT`` — a
false-negative under this module's own already-stated conservative
default, never a false positive.
"""

from __future__ import annotations

import importlib as _importlib
from typing import TYPE_CHECKING

from .diff_cxx_rules import owner_class_of
from .model import ScopeOrigin

# Every name below is imported with the redundant `as X` self-alias, the
# standard ruff/mypy-recognized "this import is an intentional re-export"
# idiom -- not all of them are used by this module's own code below, but
# dumper_scoping.py/export_surface.py/tests import several of them directly
# from `abicheck.type_reachability`, the path that predates the
# type_reachability_spelling.py split, and mypy's `--no-implicit-reexport`
# (the default for a module with its own `__all__`) rejects an unaliased
# forwarding import for any name a *different* module then imports back out
# of this one.
from .type_reachability_spelling import (
    _NON_PUBLIC_ORIGINS as _NON_PUBLIC_ORIGINS,
    _bare_type_name as _bare_type_name,
    _compile_spelling_pattern as _compile_spelling_pattern,
    _finditer_allow_nested as _finditer_allow_nested,
    _is_public_non_stdlib_declaration as _is_public_non_stdlib_declaration,
    _namespace_suffix_spellings as _namespace_suffix_spellings,
    _non_stdlib_signature_spellings as _non_stdlib_signature_spellings,
    _partition_snapshot_types as _partition_snapshot_types,
    _raw_typedef_spellings as _raw_typedef_spellings,
    _record_identity as _record_identity,
    _spelling_index as _spelling_index,
    _stripped_signature_spelling as _stripped_signature_spelling,
    _typedef_candidate_spellings as _typedef_candidate_spellings,
    _typedef_spelling_targets as _typedef_spelling_targets,
    type_string_references_name as type_string_references_name,
)

if TYPE_CHECKING:
    from .model import AbiSnapshot, RecordType

__all__ = [
    "directly_referenced_stdlib_type_spellings",
    "directly_referenced_stdlib_types",
    "type_string_references_name",
]


def _merged_typedefs(snapshot: AbiSnapshot) -> dict[str, str]:
    """``snapshot.typedefs`` (bare-name-keyed, lossy under a cross-class
    collision) plus every entry ``snapshot.typedefs_qualified`` carries
    (fully-qualified-name-keyed, collision-free -- schema v25, G31 Phase C).

    Closes the false-negative half of the bare-name typedef collision gap
    documented on ``AbiSnapshot.typedefs_qualified``: when two distinct
    member typedefs share a bare spelling (e.g. two unrelated ``value_type``
    aliases in different classes), only one survives in ``typedefs`` at all
    -- the other's aliasing information was previously unrecoverable by the
    time it reached this module. Merging in the qualified twin does not
    remove that pre-existing ambiguity (a bare spelling shared by two
    classes is still genuinely ambiguous, and every ambiguity-tracking rule
    in :func:`_typedef_spelling_targets` and its siblings already handles
    that safely -- dropping rather than guessing), it only restores the
    *previously invisible* declaration to the vocabulary these functions
    scan, so it can be resolved via its own qualified/namespace-suffix
    spellings instead of never appearing at all.

    A snapshot predating this field (or one from a producer that never had
    per-class qualified typedef scoping, e.g. DWARF-only) has an empty
    ``typedefs_qualified``, so this degrades to exactly the prior
    ``dict(snapshot.typedefs)`` behavior -- purely additive, no consumer
    regression possible.
    """
    return {**snapshot.typedefs, **snapshot.typedefs_qualified}


class _StdlibReferenceScan:
    """Mutable state of one :func:`directly_referenced_stdlib_types` walk.

    Owns the three compiled spelling patterns and the sets they feed: which
    stdlib identities are still unaccounted for, which have been referenced,
    which non-stdlib records have been reached, and which typedef aliases have
    already been followed. A class rather than a closure so the seeding pass and
    the record walk can share it as one explicit object.
    """

    def __init__(
        self,
        stdlib_identities: list[str],
        non_stdlib_identities: frozenset[str],
        typedefs: dict[str, str],
        enum_identities: frozenset[str] = frozenset(),
    ) -> None:
        self._stdlib_index, self._record_index = _spelling_index(
            stdlib_identities,
            non_stdlib_identities,
            enum_identities,
            _raw_typedef_spellings(typedefs),
        )
        stdlib_pattern = _compile_spelling_pattern(self._stdlib_index)
        # stdlib_index always has at least one entry here (every stdlib
        # identity maps at least itself), so _compile_spelling_pattern's
        # empty-input case never applies to this caller.
        assert stdlib_pattern is not None
        self._stdlib_pattern = stdlib_pattern
        self._record_pattern = _compile_spelling_pattern(self._record_index)
        self._typedef_targets = _typedef_spelling_targets(
            typedefs, non_stdlib_identities
        )
        self._typedef_pattern = (
            _compile_spelling_pattern(self._typedef_targets)
            if self._typedef_targets
            else None
        )
        self._referenced: set[str] = set()
        # An identity matched via its own literal, un-derived spelling
        # (``stdlib_index[identity] == {identity}``, its unconditional
        # self-key -- see :func:`_spelling_index`) -- as opposed to only
        # ever matched via a *derived* spelling (a stripped/bare form) that
        # may be shared with another stdlib identity or an unrelated
        # non-stdlib record/enum. Tracked separately from ``_referenced``
        # because a derived-spelling match's own ambiguity can only be
        # resolved by a consumer that knows *which* route actually proved
        # an identity -- this class is the only place that information
        # exists, since :func:`directly_referenced_stdlib_types`'s own
        # return value is a flat, routeless set.
        self._referenced_exact: set[str] = set()
        # An identity matched exactly, but only while recursively scanning a
        # typedef's *target* string (never in the declaration's own literal
        # text) -- keyed by identity, valued by every top-level alias
        # spelling that led to such a match. See :meth:`scan`'s own
        # ``via_typedef``/``origin_alias`` docstring for why this needs its
        # own bucket rather than folding straight into ``_referenced_exact``.
        self._exact_typedef_aliases: dict[str, set[str]] = {}
        # Broader siblings of the two sets above: trustworthy *at all* --
        # matched via *any* spelling (a derived/stripped form counts, not
        # only the identity's own self-key) in a route this scan considers
        # trustworthy (``_referenced_trusted``: found directly, via_typedef
        # False) or an alias-conditional route (``_trusted_via_alias``:
        # found only via a typedef alias, keyed the same way as
        # ``_exact_typedef_aliases``) (Codex review, fresh evidence).
        # ``directly_referenced_stdlib_type_spellings``'s own "this
        # identity's stripped form collides with nothing else in the
        # snapshot" shortcut previously trusted *any* member of
        # ``referenced()`` unconditionally for that check -- including one
        # matched only via a derived spelling inside a record's own field,
        # where the record itself was reached only through an ambiguous
        # typedef alias. The bare-spelling-uniqueness argument alone cannot
        # rescue that: "nothing else could this spelling mean" says nothing
        # about whether the signature legitimately reaches this spelling at
        # all. ``_referenced_exact``/``_exact_typedef_aliases`` stay exactly
        # as before (used by the *other*, "stripped form is ambiguous"
        # branch, which specifically needs the stronger self-key guarantee).
        self._referenced_trusted: set[str] = set()
        self._trusted_via_alias: dict[str, set[str]] = {}
        self._remaining = set(stdlib_identities)
        self._reached_records: set[str] = set()
        self._worklist: list[str] = []
        # Identities currently sitting in `_worklist`, awaiting their next
        # pop -- lets `reach_record` tell "already queued for a rescan, no
        # need to add another entry" apart from "not queued, must append"
        # (Codex review, fresh evidence: a burst of provenance upgrades for
        # the same already-walked, alias-only record arriving before its
        # first requeue is even popped previously appended one worklist
        # entry per upgrade regardless, so a record reached via ~800
        # distinct aliases queued ~800 duplicate entries, each pop
        # rescanning under every accumulated alias -- quadratic). Discarded
        # in :meth:`next_reached_record` the moment an identity is popped.
        self._record_pending: set[str] = set()
        self._resolved_typedefs: set[str] = set()
        # Every reach of a record, accumulated -- not just the *first*
        # (Codex review, fresh evidence, two rounds). Reaching a record is
        # still deduplicated for *queuing* purposes (a record's fields are
        # only ever walked once, matching ``reach_record``'s own
        # pre-existing "queue once" semantics for the worklist), but the
        # previous version stored only the *first* reach's provenance --
        # meaning a record reached first via an ambiguous typedef alias and
        # only *later* also via a trustworthy direct declaration kept the
        # ambiguous provenance forever, making the confirmation result
        # depend on declaration order (confirmed empirically: reversing two
        # otherwise-unrelated declarations changed a genuine layout break
        # from confirmed to `UNKNOWN_UNRESOLVED`) -- exactly the class of
        # order-dependence this whole module has repeatedly had to close
        # elsewhere (see :func:`_seed_scan_from_public_declarations`'s own
        # ``stop_when_exhausted`` docstring for the analogous fix on stdlib
        # identities). Safe to accumulate rather than gate on "first only":
        # :func:`_seed_scan_from_public_declarations` runs to completion
        # (``full_scan=True`` disables its own early exit) *before*
        # :func:`_walk_reached_records` ever starts popping the worklist, so
        # every seed-level reach of a record is already accumulated here by
        # the time that record's own fields are scanned -- see
        # ``_record_direct``/``_record_typedef_origins`` below.
        self._record_direct: set[str] = set()
        self._record_typedef_origins: dict[str, set[str]] = {}
        # Which reached records have already had their own fields/bases
        # scanned at least once -- lets a later provenance upgrade
        # discovered *during the record walk itself* (not just during
        # seeding) requeue an already-walked record for a rescan (Codex
        # review, fresh evidence: see :meth:`reach_record`'s own docstring).
        self._record_walked: set[str] = set()
        # Per-alias cache of exact/any-spelling stdlib identities reachable
        # from that alias's own target chain, memoized once per alias
        # (Codex review, fresh evidence): the previous version re-walked an
        # already-resolved alias's *entire remaining tail* from scratch on
        # every subsequent declaration that named it, making a snapshot
        # with N chained aliases named by N separate declarations
        # (deepest-first) quadratic overall -- confirmed empirically (a
        # 1,200-alias chain took several seconds). See
        # :meth:`_reachable_stdlib`.
        self._alias_reachable: dict[
            str, tuple[frozenset[str], frozenset[str], frozenset[str]]
        ] = {}

    @property
    def exhausted(self) -> bool:
        """True once every stdlib candidate has been accounted for.

        Both seeding loops stop early on this: there is nothing left to find.
        """
        return not self._remaining

    def scan(
        self,
        type_string: str,
        *,
        via_typedef: bool = False,
        origin_alias: str | None = None,
    ) -> None:
        """Record every stdlib/non-stdlib identity *type_string* names;
        newly-reached non-stdlib records are queued for their own fields to
        be walked in turn. Also follows a typedef alias to its own target
        (Codex review, fresh evidence: ``surface.py``'s own reachability
        closure does the same), so a public signature spelled with a
        user-defined alias name still reaches the record it actually
        names.

        *via_typedef* and *origin_alias* (both default to "not scanning a
        typedef target" -- never set by an external caller, only by this
        method's own typedef branch below) together answer "is this call
        scanning the declaration's own literal text, or a typedef *target*
        string reached by resolving an alias, and if the latter, which
        top-level alias spelling did the real declaration actually write?"
        A genuinely *unambiguous* typedef alias -- one
        :func:`_typedef_spelling_targets` already resolved to exactly one
        target -- is real proof the declaration named that identity, even
        when the *target's* own bare form happens to collide with an
        unrelated stdlib sibling elsewhere in the snapshot; only an
        *alias* that is itself ambiguous, e.g. with an unrelated enum's
        bare spelling, must not confer exactness. A nested typedef
        resolution (a target string that itself contains another alias)
        propagates the *original* top-level alias unchanged, since that is
        the only spelling the real declaration ever actually wrote.

        The typedef branch below only ever fully re-walks a given alias's
        target *once*, guarded by ``_resolved_typedefs`` (records reached
        and ``_referenced`` populated) -- but a *second*, independent
        declaration reaching the same already-walked target through a
        *different*, this-time-unambiguous alias still needs its own
        provenance recorded: see :meth:`_reachable_stdlib`, the memoized
        lookup that records just that provenance without repeating the
        full walk.

        Alias-chain expansion (both the fresh-resolution branch and the
        already-resolved provenance-propagation branch) is driven by an
        explicit worklist, not Python call-stack recursion (Codex review,
        fresh evidence: a snapshot with ~1,000 chained typedef aliases,
        exposed deepest-first by its own public declarations, reproduced a
        real ``RecursionError`` on both the fresh-resolution path and
        :meth:`_propagate_typedef_provenance`'s own recursive call --
        confirmed empirically, and this method's own recursive
        ``self.scan(target, via_typedef=True, ...)`` call had the identical
        exposure even for a *single* declaration naming the outermost alias
        of a long chain). Mirrors :func:`_finditer_allow_nested`'s own
        stack-based rewrite for the same class of unbounded-nesting risk.
        """
        if not type_string:
            return
        self._scan_stdlib_and_records(type_string, via_typedef, origin_alias)
        if self._typedef_pattern is None:
            return
        # Each entry: (alias spelling, was this alias reached via a typedef
        # target rather than the caller's own literal text, the top-level
        # alias spelling to credit for it). Order is irrelevant for
        # correctness -- every effect below is set-membership-based, so
        # popping in any order reaches the same final state -- only for
        # avoiding recursion.
        worklist: list[tuple[str, bool, str | None]] = [
            (m.group(0), via_typedef, origin_alias)
            for m in _finditer_allow_nested(self._typedef_pattern, type_string)
        ]
        while worklist:
            alias, via_td, origin = worklist.pop()
            target = self._typedef_targets[alias]
            this_origin = origin if via_td else alias
            if alias not in self._resolved_typedefs:
                self._resolved_typedefs.add(alias)
                self._scan_stdlib_and_records(target, True, this_origin)
                if self._typedef_pattern is not None:
                    worklist.extend(
                        (m.group(0), True, this_origin)
                        for m in _finditer_allow_nested(self._typedef_pattern, target)
                    )
            else:
                # Already fully walked (records reached, `_referenced`
                # populated) via a *different* origin alias -- but this
                # alias's own exact-match provenance was never recorded:
                # `_resolved_typedefs` exists to stop the expensive
                # record-walk/`_referenced` bookkeeping from repeating,
                # not to gate provenance -- an earlier, ambiguous alias
                # resolving through the same target first would
                # otherwise silently swallow a later, genuinely
                # unambiguous alias's own provenance purely because of
                # declaration order. Cheaply re-derive just this
                # alias's own exact-match provenance without repeating
                # the full walk. `this_origin` is always a real alias
                # spelling here in practice (only ``None`` when
                # ``via_typedef`` is externally forced ``True`` with no
                # ``origin_alias``, which no caller in this module ever
                # does), but the type checker cannot see that.
                if this_origin is not None:
                    exact_ids, any_ids, records = self._reachable_stdlib(alias)
                    for identity in any_ids:
                        self._trusted_via_alias.setdefault(identity, set()).add(
                            this_origin
                        )
                    for identity in exact_ids:
                        self._exact_typedef_aliases.setdefault(identity, set()).add(
                            this_origin
                        )
                    # A typedef chain can terminate at a record rather than
                    # directly at a stdlib identity (Codex review, fresh
                    # evidence) -- credit this alias as an additional origin
                    # for reaching it, the same way a fresh resolution
                    # already does via `_scan_stdlib_and_records`'s own
                    # record_pattern loop.
                    for record_identity in records:
                        self.reach_record(
                            record_identity, via_typedef=True, origin_alias=this_origin
                        )

    def _scan_stdlib_and_records(
        self, type_string: str, via_typedef: bool, origin_alias: str | None
    ) -> None:
        """The stdlib-identity and non-stdlib-record halves of :meth:`scan`,
        factored out so both the top-level call and each typedef-chain
        worklist entry can reuse them without recursing back into
        :meth:`scan` itself (see its own docstring for why). *via_typedef*/
        *origin_alias* carry the exact same meaning as on :meth:`scan`.
        """
        for match in _finditer_allow_nested(self._stdlib_pattern, type_string):
            spelling = match.group(0)
            for identity in self._stdlib_index.get(spelling, ()):
                if identity in self._remaining:
                    self._referenced.add(identity)
                    self._remaining.discard(identity)
                # Broader "trustworthy at all" tracking, independent of
                # whether *spelling* is the identity's own self-key or a
                # derived/stripped form -- see ``_referenced_trusted``'s own
                # docstring on ``__init__`` for why this is needed alongside
                # the narrower exactness tracking below.
                if not via_typedef:
                    self._referenced_trusted.add(identity)
                elif origin_alias is not None:
                    self._trusted_via_alias.setdefault(identity, set()).add(
                        origin_alias
                    )
                if spelling != identity:
                    continue
                # The matched key is the identity's own self-key, never a
                # derived/stripped spelling (a stripped form is always
                # strictly shorter than the identity it derives from, since
                # it drops a non-empty namespace prefix) -- so this
                # occurrence alone proves the identity unambiguously,
                # independent of whatever else that identity's own derived
                # spelling might collide with -- **provided the text that
                # produced it is trustworthy**: a direct match in the
                # declaration's own literal text (``via_typedef=False``)
                # always is. A match found only while recursively scanning
                # a typedef's *target* string is trustworthy only when the
                # *alias* that led there is itself unambiguous -- recorded
                # here, not decided here, since only the caller (which
                # knows the full, enum-aware non-stdlib collision
                # vocabulary) can judge that; see
                # :meth:`referenced_exact_typedef_aliases`.
                if not via_typedef:
                    self._referenced_exact.add(identity)
                elif origin_alias is not None:
                    self._exact_typedef_aliases.setdefault(identity, set()).add(
                        origin_alias
                    )
        if self._record_pattern is not None:
            for match in _finditer_allow_nested(self._record_pattern, type_string):
                for identity in self._record_index.get(match.group(0), ()):
                    self.reach_record(
                        identity, via_typedef=via_typedef, origin_alias=origin_alias
                    )

    def _reachable_stdlib(
        self, start: str
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """``(exact, any, records)`` -- the stdlib identities reachable from
        *start* alias's own target (exact self-key matches and any-spelling
        matches respectively), plus every non-stdlib record identity
        directly named anywhere in that same chain, transitively through
        further nested typedef aliases.

        ``records`` (Codex review, fresh evidence, closing a gap the
        previous revision left open) matters because a typedef chain can
        terminate at a *record*, not directly at a stdlib identity --
        ``ns::bad -> Good -> Wrapper`` (``Wrapper`` a record with its own
        stdlib field), with ``bad`` colliding with an unrelated enum's bare
        spelling. A first declaration spelling ``bad`` resolves the whole
        chain fresh, reaching ``Wrapper`` with ``bad`` as its only
        (ambiguous) origin. A *second* declaration spelling the
        already-resolved, genuinely unambiguous ``Good`` directly used to
        credit nothing at all for ``Wrapper``, since the cache tracked only
        stdlib identities -- silently leaving a real layout break
        unconfirmed even though ``Good`` is real, independent proof.
        :meth:`scan`'s own "already resolved" branch now also calls
        :meth:`reach_record` for every identity in ``records`` here, the
        same way it already credits ``exact``/``any`` for stdlib
        identities. Never expands *into* a reached record's own fields --
        that stays the separate, independently-threaded mechanism
        :meth:`reach_record`/:func:`_walk_reached_records` already own; a
        record found here is a *terminal* node for this structural walk,
        matching how the fresh-resolution path (:meth:`scan`'s own typedef
        branch calling :meth:`_scan_stdlib_and_records`) already stops
        similarly -- it queues a reached record for :func:`_walk_reached_records`
        to walk later, rather than walking it inline.

        Memoized per alias in ``_alias_reachable`` via an explicit,
        iterative post-order stack walk -- not recursion, and not a plain
        re-scan of *start*'s raw text on every call (Codex review, fresh
        evidence: see ``_alias_reachable``'s own docstring on ``__init__``
        for the quadratic blowup this closes). Each alias's own reachable
        set is computed at most once for the lifetime of this scan,
        regardless of how many different declarations later name it once
        already resolved.

        A cyclic typedef chain is broken conservatively: an alias currently
        being computed (grey) contributes nothing to itself if reached
        again before it's done, rather than looping forever.
        """
        if start in self._alias_reachable:
            return self._alias_reachable[start]
        grey: set[str] = set()
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            alias, expanded = stack.pop()
            if not expanded:
                if alias in self._alias_reachable or alias in grey:
                    continue
                grey.add(alias)
                stack.append((alias, True))
                target = self._typedef_targets[alias]
                if self._typedef_pattern is not None:
                    for m in _finditer_allow_nested(self._typedef_pattern, target):
                        stack.append((m.group(0), False))
                continue
            target = self._typedef_targets[alias]
            exact: set[str] = set()
            any_ids: set[str] = set()
            records: set[str] = set()
            for m in _finditer_allow_nested(self._stdlib_pattern, target):
                spelling = m.group(0)
                for identity in self._stdlib_index.get(spelling, ()):
                    any_ids.add(identity)
                    if spelling == identity:
                        exact.add(identity)
            if self._record_pattern is not None:
                for m in _finditer_allow_nested(self._record_pattern, target):
                    records.update(self._record_index.get(m.group(0), ()))
            if self._typedef_pattern is not None:
                for m in _finditer_allow_nested(self._typedef_pattern, target):
                    child_exact, child_any, child_records = self._alias_reachable.get(
                        m.group(0), (frozenset(), frozenset(), frozenset())
                    )
                    exact |= child_exact
                    any_ids |= child_any
                    records |= child_records
            self._alias_reachable[alias] = (
                frozenset(exact),
                frozenset(any_ids),
                frozenset(records),
            )
            grey.discard(alias)
        return self._alias_reachable[start]

    def reach_record(
        self,
        identity: str,
        *,
        via_typedef: bool = False,
        origin_alias: str | None = None,
    ) -> None:
        """Queue a non-stdlib record's own fields/bases to be walked, and
        accumulate *via_typedef*/*origin_alias* provenance for *every*
        reach, not only the first (Codex review, fresh evidence, three
        rounds -- see ``_record_direct``/``_record_typedef_origins``'s own
        docstring on ``__init__`` for why accumulating instead of "first
        reach wins" is required for order-independence).
        :func:`_walk_reached_records` reads this accumulated state via
        :meth:`record_provenance` so a record's own fields are scanned with
        every trust level it was ever reached under, instead of
        unconditionally trusting them regardless of how ambiguously the
        record itself was reached.

        A record already walked once (its fields already scanned, tracked
        in ``_record_walked`` via :meth:`mark_record_walked`) is *requeued*
        when this reach genuinely upgrades its provenance -- adds it to
        ``_record_direct`` for the first time, or adds a typedef origin it
        didn't already have (Codex review, fresh evidence: a record reached
        first through an ambiguous typedef alias during seeding, and only
        *later* also directly through another already-reached record's own
        field discovered *during the record walk itself* -- not during
        seeding, so accumulation-before-the-walk-starts doesn't cover it --
        stayed stuck with its stale, ambiguous-only provenance forever,
        since the worklist's own "queue once" dedup meant it was never
        walked again to pick up the upgrade). Requeuing is safe to repeat:
        each record can only be upgraded a bounded number of times (once
        for the direct flag, once per distinct alias in the snapshot), so
        this always terminates, and re-scanning the same field text again
        is idempotent -- it only ever adds more identities/credits, never
        removes any.

        At most one pending worklist entry per record at a time (tracked in
        ``_record_pending``, cleared on pop by :meth:`next_reached_record`):
        a burst of distinct provenance upgrades for the same already-walked,
        alias-only record arriving before its first requeue is even popped
        previously appended one worklist entry *per upgrade* regardless
        (Codex review, fresh evidence, following directly from the
        already-direct fix above: this still occurs while the target stays
        alias-only, so that shortcut never applies -- a record reached via
        ~800 distinct aliases queued ~800 duplicate entries, each pop
        rescanning under every accumulated alias, quadratic). One pending
        entry is always enough regardless of how many upgrades landed in
        between, since a pop always rescans with *all* provenance
        accumulated up to that point (:func:`_walk_reached_records` reads
        it fresh via :meth:`record_provenance` on every pop, not a snapshot
        captured at queue time).
        """
        upgraded = False
        if not via_typedef:
            if identity not in self._record_direct:
                self._record_direct.add(identity)
                upgraded = True
        elif origin_alias is not None and identity not in self._record_direct:
            # Once `identity` is unconditionally trusted (`_record_direct`),
            # accumulating further typedef-alias origins is pure bookkeeping
            # with no observable effect: `_walk_reached_records` already
            # scans a direct record's fields unconditionally (the strongest
            # trust tier), so a *new* alias origin can never add a match a
            # direct scan wouldn't already have found -- yet the old code
            # kept recording every distinct alias and requeuing on each one
            # regardless (Codex review, fresh evidence: a snapshot with a
            # public record carrying ~1,200 alias-typed fields all targeting
            # an already-directly-reached record queued the same identity
            # ~1,200 times, each pop rescanning under every accumulated
            # alias -- quadratic). Skipping the update once already direct
            # closes this without weakening provenance: `record_provenance`
            # reports `is_direct=True` regardless of `typedef_origins`'
            # contents, and every alias this branch would have added is
            # strictly redundant with the unconditional direct scan.
            origins = self._record_typedef_origins.setdefault(identity, set())
            if origin_alias not in origins:
                origins.add(origin_alias)
                upgraded = True
        if identity not in self._reached_records:
            self._reached_records.add(identity)
            self._worklist.append(identity)
            self._record_pending.add(identity)
        elif (
            upgraded
            and identity in self._record_walked
            and identity not in self._record_pending
        ):
            self._worklist.append(identity)
            self._record_pending.add(identity)

    def mark_record_walked(self, identity: str) -> None:
        """Record that *identity*'s own fields/bases were just scanned,
        so a later provenance upgrade (see :meth:`reach_record`) knows to
        requeue it for a rescan instead of assuming it's still pending."""
        self._record_walked.add(identity)

    def record_provenance(self, identity: str) -> tuple[bool, frozenset[str]]:
        """``(is_direct, typedef_origins)`` -- every trust level *identity*
        (a reached record) has ever been reached under. ``is_direct`` is
        ``True`` as soon as *any* reach was ``via_typedef=False``
        (unconditionally trustworthy on its own, matching this whole
        module's "one genuinely unambiguous route is real proof" principle
        used everywhere else); ``typedef_origins`` is every top-level alias
        spelling that reached it only via a typedef, to be judged the same
        way :meth:`referenced_exact_typedef_aliases` already is by the
        outer caller."""
        return (
            identity in self._record_direct,
            frozenset(self._record_typedef_origins.get(identity, ())),
        )

    def next_reached_record(self) -> str | None:
        """Pop the next queued record identity, or ``None`` when the queue
        is empty. Clears the popped identity's pending flag (see
        ``_record_pending``'s own docstring on ``__init__``), so a further
        provenance upgrade discovered afterward can requeue it again."""
        if not self._worklist:
            return None
        identity = self._worklist.pop()
        self._record_pending.discard(identity)
        return identity

    def referenced(self) -> frozenset[str]:
        """The stdlib identities this walk proved directly referenced."""
        return frozenset(self._referenced)

    def referenced_exact(self) -> frozenset[str]:
        """Which of :meth:`referenced`'s identities were matched at least
        once via their own literal, un-derived spelling **in a real
        declaration's own text** -- i.e. proven unambiguously, independent
        of any collision a *derived* (stripped/bare) spelling might have
        with a sibling stdlib identity or an unrelated non-stdlib
        record/enum. A subset of :meth:`referenced`. Does **not** include
        an identity matched only while recursively scanning a typedef's
        target string -- see :meth:`referenced_exact_typedef_aliases` for
        that route's own, alias-conditional provenance."""
        return frozenset(self._referenced_exact)

    def referenced_exact_typedef_aliases(self) -> dict[str, frozenset[str]]:
        """For each identity matched exactly only while recursively
        scanning a typedef's *target* string (never in a real declaration's
        own literal text): every top-level alias spelling that led there.

        The scan itself cannot decide whether such a match is trustworthy --
        that depends on whether the alias collides with an unrelated
        non-stdlib record/enum spelling, and only the caller (specifically
        :func:`directly_referenced_stdlib_type_spellings`, which already
        computes that enum-aware collision vocabulary for its own separate
        guard) has that information. An identity is safe to treat as exact
        here as soon as *any* alias that produced it is absent from the
        caller's own non-stdlib collision set -- one genuinely unambiguous
        route is real proof regardless of how many other, separately
        ambiguous aliases also happened to reach the same identity.
        """
        return {k: frozenset(v) for k, v in self._exact_typedef_aliases.items()}

    def referenced_trusted(self) -> frozenset[str]:
        """Which of :meth:`referenced`'s identities were matched at least
        once via *any* spelling (self-key or a derived/stripped form alike)
        in a route this scan considers unconditionally trustworthy (a real
        declaration's own literal text, or a reached record's own field/
        base scanned with that same trust level -- see
        :meth:`reach_record`). Broader than :meth:`referenced_exact` (which
        additionally requires the self-key spelling specifically); see
        ``_referenced_trusted``'s own docstring on ``__init__`` for why the
        broader form is needed."""
        return frozenset(self._referenced_trusted)

    def trusted_via_alias(self) -> dict[str, frozenset[str]]:
        """The alias-conditional sibling of :meth:`referenced_trusted`,
        mirroring :meth:`referenced_exact_typedef_aliases` but for *any*
        spelling rather than only the self-key one -- see that method's own
        docstring for how a caller is expected to use this (trust an
        identity here as soon as any one alias that produced it is itself
        unambiguous)."""
        return {k: frozenset(v) for k, v in self._trusted_via_alias.items()}


def _seed_scan_from_public_declarations(
    snapshot: AbiSnapshot,
    scan: _StdlibReferenceScan,
    non_stdlib_identities: frozenset[str],
    *,
    exclude_export_only: bool = False,
    committed_roots: frozenset[str] | None = None,
    stop_when_exhausted: bool = True,
) -> None:
    """Scan every public, non-stdlib function signature and variable type.

    A member function additionally seeds its *owner* class — a public method
    never repeats its own class in its return/parameter types, so without this
    the owner's fields would never be walked. The owner is queued only on an
    *exact* identity match, never through ``record_index``'s suffix matching:
    ``owner_class_of`` cannot tell an enclosing class from an enclosing
    namespace, so a bare namespace fragment could otherwise collide with an
    unrelated internal record's bare suffix (see the public function's
    docstring).

    ``exclude_export_only``/``committed_roots`` are forwarded to
    :func:`_is_public_non_stdlib_declaration` unchanged — see its own
    docstring.

    ``stop_when_exhausted`` (default ``True``, :func:`directly_referenced_stdlib_types`'s
    own performance-motivated early exit -- "found via any route" is all that
    function needs, so scanning further declarations once every candidate is
    accounted for is pure waste) must be ``False`` for a caller that also
    needs per-identity *exact-match* provenance: an identity first found via
    an ambiguous derived spelling in one declaration, with its own
    unambiguous exact spelling appearing only in a *later* declaration,
    would never have that later occurrence scanned once ``_remaining`` is
    already empty -- silently making
    :func:`directly_referenced_stdlib_type_spellings`'s result depend on
    declaration order (confirmed empirically by reversing two function
    declarations). Set ``False`` there; the ordinary
    :func:`directly_referenced_stdlib_types` path is unaffected.
    """
    for fn in snapshot.functions:
        if stop_when_exhausted and scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            fn,
            exclude_export_only=exclude_export_only,
            committed_roots=committed_roots,
        ):
            continue
        scan.scan(fn.return_type)
        for param in fn.params:
            scan.scan(param.type)
        owner = owner_class_of(fn)
        if owner is not None and owner in non_stdlib_identities:
            scan.reach_record(owner)

    for var in snapshot.variables:
        if stop_when_exhausted and scan.exhausted:
            break
        if not _is_public_non_stdlib_declaration(
            var,
            exclude_export_only=exclude_export_only,
            committed_roots=committed_roots,
        ):
            continue
        scan.scan(var.type)


def _walk_reached_records(
    scan: _StdlibReferenceScan,
    non_stdlib_records: dict[str, list[RecordType]],
    *,
    exclude_export_only: bool = False,
    stop_when_exhausted: bool = True,
) -> None:
    """Walk each reached non-stdlib record's own fields and bases, transitively.

    Every entry sharing the reached identity is walked, each checking its own
    ``origin`` independently: a private-origin duplicate excludes only itself,
    not a public-origin sibling of the same identity.

    ``exclude_export_only``, same meaning as
    :func:`_is_public_non_stdlib_declaration`'s own parameter: a record
    defined only via the binary's export table (no header at all) must not
    contribute its fields as public-header-domain evidence either, for the
    same reason a bare export-only function/variable root must not.

    ``stop_when_exhausted`` mirrors
    :func:`_seed_scan_from_public_declarations`'s own parameter -- see its
    docstring.

    Each record's own fields/bases are scanned with *every* trust level the
    record itself has ever been reached under, accumulated across every
    declaration seeding it and every other record's own field/base that
    also reaches it during this same walk (Codex review, fresh evidence,
    three rounds: see ``_StdlibReferenceScan.reach_record``'s own
    docstring) -- a record reached only through an ambiguous typedef alias
    must not have a stdlib type named directly in one of its own fields
    treated as unconditionally exact/trusted just because the field's own
    text is "direct", but a record reached via *any* trustworthy route
    (direct, or an alias later found unambiguous) must not have that
    route's confirmation depend on which declaration -- or which other
    record's own field, discovered only *during* this walk -- happened to
    be processed first. ``scan.mark_record_walked`` after scanning a
    record's own fields/bases lets a later provenance upgrade (a route
    discovered only once this record's own popped-and-scanned already)
    requeue it for a rescan instead of silently keeping the stale state.
    """
    while True:
        if stop_when_exhausted and scan.exhausted:
            return
        identity = scan.next_reached_record()
        if identity is None:
            return
        is_direct, typedef_origins = scan.record_provenance(identity)
        for rec in non_stdlib_records[identity]:
            if rec.origin in _NON_PUBLIC_ORIGINS:
                continue
            if exclude_export_only and rec.origin is ScopeOrigin.EXPORT_ONLY:
                continue
            texts = [f.type for f in rec.fields] + [
                *rec.resolved_bases(),
                *rec.resolved_virtual_bases(),
            ]
            # Both direct and virtual bases are ABI-reachable through the
            # derived type (virtual inheritance still embeds the base
            # subobject + vtable path), same as surface.py's own closure
            # (Codex review, fresh evidence): a public Derived inheriting a
            # non-stdlib Base whose own field is a stdlib record was
            # otherwise never reached, since only rec.fields was followed.
            for text in texts:
                if is_direct:
                    # The unconditional direct scan already marks any match
                    # at the strongest trust tier (`referenced_exact`), so
                    # rescanning the same text once per accumulated typedef
                    # alias below would only ever reproduce a strictly
                    # weaker or identical result -- skipped entirely (Codex
                    # review, fresh evidence: a record already trusted
                    # directly but also reached through hundreds of
                    # ambiguous typedef aliases before that direct route was
                    # found made this loop quadratic in alias count for no
                    # observable gain).
                    scan.scan(text)
                    continue
                # Scanned once per distinct alias that reached this record
                # (not just one) -- one genuinely unambiguous route among
                # several is real proof, the same principle already applied
                # to a plain stdlib identity's own multi-alias provenance.
                for alias in typedef_origins:
                    scan.scan(text, via_typedef=True, origin_alias=alias)
        scan.mark_record_walked(identity)


def _run_stdlib_reference_scan(
    snapshot: AbiSnapshot,
    *,
    exclude_export_only: bool = False,
    committed_roots: frozenset[str] | None = None,
    full_scan: bool = False,
) -> _StdlibReferenceScan | None:
    """Run the walk :func:`directly_referenced_stdlib_types` performs and
    return the completed :class:`_StdlibReferenceScan` itself, or ``None``
    when *snapshot* carries no stdlib-namespaced types at all (mirroring
    that function's own early return).

    Factored out so a caller needing more than the flat ``referenced()``
    set -- specifically :func:`directly_referenced_stdlib_type_spellings`,
    which also needs :meth:`_StdlibReferenceScan.referenced_exact` and
    :meth:`_StdlibReferenceScan.referenced_exact_typedef_aliases` -- can run
    the identical walk once rather than either re-deriving it or being
    limited to :func:`directly_referenced_stdlib_types`'s own routeless
    return value.

    ``exclude_export_only``/``committed_roots`` are passed straight through
    to :func:`_seed_scan_from_public_declarations`/
    :func:`_walk_reached_records`/:func:`_is_public_non_stdlib_declaration`
    -- see their own docstrings. ``committed_roots`` is not threaded into
    :func:`_walk_reached_records` -- that function walks a non-stdlib
    record's own fields/bases once the record is already reached from a
    committed seed, so the record's own commitment status is moot; only the
    seed declarations that can *initiate* reachability need the check.

    *full_scan* (default ``False``, matching :func:`directly_referenced_stdlib_types`'s
    own early-exit behavior) disables the "stop once every candidate is
    accounted for" optimization -- pass ``True`` for exact-match provenance
    to be complete regardless of declaration order (see
    :func:`_seed_scan_from_public_declarations`'s own ``stop_when_exhausted``
    docstring for the failure mode this closes).
    """
    stdlib_identities, non_stdlib_identities, non_stdlib_records = (
        _partition_snapshot_types(snapshot)
    )
    if not stdlib_identities:
        return None

    # Threaded into the scan's own record_index construction (Codex review,
    # fresh evidence) so a record reached only via a bare spelling that
    # collides with an unrelated enum is never treated as an unconditionally
    # trustworthy direct match -- see _spelling_index's own docstring.
    enum_identities = frozenset(
        _record_identity(en.name, en.qualified_name) for en in snapshot.enums
    )
    scan = _StdlibReferenceScan(
        stdlib_identities,
        non_stdlib_identities,
        _merged_typedefs(snapshot),
        enum_identities,
    )
    stop_when_exhausted = not full_scan
    _seed_scan_from_public_declarations(
        snapshot,
        scan,
        non_stdlib_identities,
        exclude_export_only=exclude_export_only,
        committed_roots=committed_roots,
        stop_when_exhausted=stop_when_exhausted,
    )
    _walk_reached_records(
        scan,
        non_stdlib_records,
        exclude_export_only=exclude_export_only,
        stop_when_exhausted=stop_when_exhausted,
    )
    return scan


def directly_referenced_stdlib_types(
    snapshot: AbiSnapshot, *, exclude_export_only_roots: bool = False
) -> frozenset[str]:
    """Stdlib/runtime-namespaced :class:`RecordType` names in *snapshot* that
    are directly referenced by a **public**, non-stdlib function's
    return/parameter type or a non-stdlib :class:`RecordType`'s own field
    type.

    Returns the empty set when the snapshot carries no stdlib-namespaced
    types at all (the common case) — never an error. Deliberately a single,
    snapshot-scoped, pure computation: no build/source evidence, no template
    argument resolution beyond substring matching, so a stdlib type
    mentioned only inside another stdlib type's own template arguments
    (never surfacing in a non-stdlib declaration) is correctly excluded.

    Candidate identification uses ``qualified_name or name`` (Codex review,
    fresh evidence), not ``name`` alone: castxml/direct-clang record the bare
    leaf in ``name`` and the namespace-qualified spelling separately in
    ``qualified_name``, so ``name`` alone never carries a ``std::`` prefix
    for those two backends and this helper would silently find nothing. See
    :func:`_stripped_signature_spelling`/:func:`_spelling_index` for how the
    resulting identity is matched back against the (differently-spelled,
    possibly ambiguous) signature type strings, and
    :func:`_compile_spelling_pattern` for why the matching itself is one
    compiled regex rather than a per-candidate substring scan.

    A ``Function`` whose ``visibility`` is not :attr:`Visibility.PUBLIC`
    (``HIDDEN``/``ELF_ONLY``) is never itself the referencing side (Codex
    review): a real snapshot can retain such a function for cross-reference
    purposes even though it is not part of the public ABI surface this
    helper is meant to model, and treating its signature as equivalent to a
    public one would turn an internal implementation detail into a
    stdlib-ABI dependency that isn't real. Same reasoning applies to
    ``origin`` (Codex review, fresh evidence): public-header scoping can
    retain a function whose ``visibility`` is still ``PUBLIC`` but whose
    ``origin`` is ``ScopeOrigin.PRIVATE_HEADER``/``SYSTEM_HEADER``/
    ``GENERATED`` — linkage and origin are independent axes (ADR-024 D1),
    so a function only ever declared in a private/system/generated header
    is rejected here too, before its signature is ever scanned. A public
    ``Variable`` is seeded the same way (Codex review, fresh evidence: a
    ``compute_public_surface()``-style closure already treats public
    variables as type roots, but this scan originally only walked
    ``snapshot.functions`` — a public exported global like ``Foo global``
    never seeded ``Foo`` at all).

    Both loops also check the declaration's own *recovered qualified name*
    (:func:`abicheck.diff_cxx_rules.itanium_qualified_name`, from
    ``mangled``) against ``STDLIB_TYPE_NAMESPACE_PREFIXES``, not just the
    bare ``name``/``fn.name`` field (Codex review, fresh evidence):
    CastXML/direct-clang record a function or namespace-scope variable's
    own display name bare (e.g. ``"touch"``, never
    ``"__gnu_cxx::Node::touch"`` or ``"std::touch"``), so the plain
    ``name.startswith(...)`` check cannot catch a retained, seemingly-
    public declaration that is actually part of the standard library
    itself — a stdlib-internal method or a namespace-scope stdlib variable
    — whose return type/params/type mentioning a stdlib record would
    otherwise be scanned and incorrectly marked directly referenced,
    unfiltering purely internal toolchain churn as a public break. This
    check subsumes (and replaces) an earlier, narrower version that only
    checked the *owner* class recovered by ``owner_class_of`` before
    seeding it: whenever that owner starts with a stdlib prefix, the full
    recovered qualified name (owner plus its own trailing member) always
    does too, so the owner-only check could never fire without this
    broader one already having skipped the declaration entirely — and the
    broader check additionally catches a stdlib namespace's own direct
    free function/variable (a single mangled scope component, e.g.
    ``"std::touch"``), which the owner-only check missed since
    ``owner_class_of`` returns a bare ``"std"`` (no trailing ``"::"``) for
    that shape, never matching the ``"std::"`` prefix string.

    A signature/field type string spelled with a user-defined typedef alias
    (e.g. a public function returning ``Alias`` where ``snapshot.typedefs``
    maps ``"Alias"`` to ``"Foo"``) is resolved to its target and scanned in
    turn (Codex review, fresh evidence: ``surface.py``'s own reachability
    closure does the same) — this is a different, already-solvable case
    from the typedef-*aliased stdlib type* gap noted below (``std::string``
    naming its own alias with no reverse mapping back to the owning
    ``RecordType``): here the alias's target is a plain type-string
    substitution already present in ``snapshot.typedefs``, nothing needs
    inventing.

    A non-stdlib record's own fields *and bases* (both direct and virtual —
    Codex review, fresh evidence: a public ``Derived`` inheriting a
    non-stdlib ``Base`` whose own field is a stdlib record was otherwise
    never reached, since only ``rec.fields`` was followed; mirrors
    ``surface.py``'s own closure, which follows both for the same reason —
    virtual inheritance still embeds the base subobject + vtable path) are
    only consulted once that record itself is confirmed reachable from a
    public root — by direct mention in a public function's own signature,
    by being that function's *owner* class/struct for a member function
    (Codex review, fresh evidence: a public method like ``void Foo::run()``
    never repeats ``Foo`` in its own return/parameter types, so without
    also seeding :func:`abicheck.diff_cxx_rules.owner_class_of` the
    previous version never queued ``Foo`` at all — a genuine layout break
    in one of its fields would be silently missed. A retained, seemingly-
    public method whose *owner* is itself a stdlib-internal class, e.g.
    libstdc++'s ``__gnu_cxx::Node``, is excluded from this by the
    declaration-level stdlib-scope check above, before its owner is ever
    computed), or transitively
    through another already-reachable record's fields/bases (Codex review,
    fresh evidence: the previous version scanned *every* non-stdlib
    record's fields unconditionally, so a purely internal,
    never-actually-reachable record — e.g. one a DWARF-only snapshot
    retains with the default
    ``ScopeOrigin.UNKNOWN`` even though nothing public touches it — could
    still make an unrelated stdlib type look directly referenced). A
    record's own ``origin`` being ``PRIVATE_HEADER``/``SYSTEM_HEADER``/
    ``GENERATED`` still excludes its fields from the walk, same as before.
    See :func:`_spelling_index` for why an ambiguous bare alias shared by
    two distinct non-stdlib records (Codex review, fresh evidence) is
    dropped rather than queuing both.

    An owner recovered from :func:`abicheck.diff_cxx_rules.owner_class_of`
    is queued only on an *exact* match against a non-stdlib record's full
    identity — never through the general suffix-matching mechanism
    :func:`_spelling_index`'s ``record_index`` uses for signature type
    spellings (Codex review, fresh evidence): ``owner_class_of`` derives
    its result by chopping the trailing ``"::"``-component off *any*
    already-qualified declaration name or mangled-symbol scope chain, with
    no way to tell whether what remains is really an enclosing *class* or
    just an enclosing *namespace* — e.g. a public namespace function
    ``api::run()`` makes ``owner_class_of`` return the bare namespace
    fragment ``"api"``, which could coincidentally equal the *bare suffix*
    of some unrelated internal record ``other::api``, wrongly walking that
    record's fields. Unlike a real signature type spelling (which a
    backend can legitimately partially-qualify per the ``Outer::Inner``
    case below), an owner string is always either the full, exact
    identity of a genuine class (both ``owner_class_of``'s
    already-qualified-name path and its mangled-decomposition fallback
    reconstruct the *complete* scope chain, never a partially-elided one)
    or, when the function is not actually a method, semantic noise —
    so exact-identity matching is both sufficient for every real class
    owner and immune to the namespace-collision false positive.
    Deliberately does **not** also gate on pointer-vs-by-value use the way
    a first read might expect (an earlier review round raised this): this
    module intentionally mirrors ``surface.py``'s own documented ADR-024 §D3
    position — a pointer-reached, non-opaque stdlib type is still
    layout-observable elsewhere (a consumer can dereference or allocate it
    by value), so demoting it here would risk hiding a real break. The safe
    half of that precision (a pointer-only-reached *opaque* handle) is
    already handled downstream by the existing opaque-size-change filter
    (``diff_filtering._filter_opaque_size_changes``, gated on
    ``RecordType.is_opaque``), not by this reachability computation.

    ``exclude_export_only_roots``, when set, additionally excludes any root
    or reached record whose ``origin`` is ``ScopeOrigin.EXPORT_ONLY`` — see
    :func:`_is_public_non_stdlib_declaration`'s own docstring for why a
    caller building public-header-domain contract evidence
    (``directly_referenced_stdlib_type_spellings``, used by
    ``contract_pipeline.py``) must set this, while this function's other,
    evidence-tier-agnostic caller (``diff_types.py``) leaves it at the
    default ``False``.
    """
    scan = _run_stdlib_reference_scan(
        snapshot, exclude_export_only=exclude_export_only_roots
    )
    return scan.referenced() if scan is not None else frozenset()


def directly_referenced_stdlib_type_spellings(
    snapshot: AbiSnapshot,
    *,
    exclude_export_only_roots: bool = False,
    committed_roots: frozenset[str] | None = None,
) -> frozenset[str]:
    """:func:`directly_referenced_stdlib_types`, re-expressed in the spelling
    a finding's own ``symbol``/``caused_by_type`` actually carries.

    The real implementation lives in
    :mod:`abicheck.type_reachability_stdlib_spellings` (split out to stay
    under this module's own file-size soft limit) — see that module's
    docstring for the full rationale. Resolved via ``importlib.import_module``
    rather than a static import: that sibling module itself needs
    :func:`_merged_typedefs`/:func:`_run_stdlib_reference_scan` from here, and
    a static two-way import would be the exact
    ``type_reachability <-> type_reachability_stdlib_spellings`` cycle
    ``scripts/check_ai_readiness.py``'s ``import-cycle-growth`` check rejects
    — the identical shape ``serialization.py``'s own
    ``bundle_facts_serialization.py`` split resolved the same way.
    """
    module = _importlib.import_module(
        ".type_reachability_stdlib_spellings", __package__
    )
    result: frozenset[str] = module.directly_referenced_stdlib_type_spellings(
        snapshot,
        exclude_export_only_roots=exclude_export_only_roots,
        committed_roots=committed_roots,
    )
    return result

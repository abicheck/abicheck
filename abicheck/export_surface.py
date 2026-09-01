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

"""ADR-049 ``contract=exports``: the export-rooted evidence provider.

:mod:`abicheck.surface` resolves the *header-derived* public surface
(ADR-024): roots are :data:`~abicheck.model.Visibility.PUBLIC` declarations,
and a declaration's header origin can demote it. That is exactly ADR-049's
``public`` contract domain, and deliberately **not** its ``exports`` domain,
which is defined as "only exported function/variable roots and closure
computed from the raw type graph" (plan Section 7). This module computes that
second domain:

- **roots** are the declarations whose own linker symbol appears in the
  binary's own export table (ELF ``.dynsym`` defined symbols, the PE export
  directory, the Mach-O export trie) -- observed evidence, never a
  header-origin classification;
- **closure** is the transitive walk over the *raw* record/enum/typedef graph
  from those roots' signatures, reusing
  :func:`~abicheck.policy.public_surface_closure._walk_type_closure` verbatim
  (a real ``AbiSnapshot.surface_graph`` traversal, ADR-063 Phase 3 D5) so the
  two domains cannot drift apart in how they follow fields, bases, and
  typedef targets. Only the *seeds* differ, the difference ADR-049 D2 draws.

No header-origin filtering happens anywhere here: a private-header type
reached from a real export *is* inside the export closure, and a
public-header declaration that is not exported and not reached is *not*.
Public-header/manifest/consumer evidence is unrelated (and advisory) for this
domain -- ADR-049 plan Section 7's ``exports`` row.

**Conservative in one specific direction**, mirroring
:mod:`abicheck.contract_evaluation`'s own discipline: this module never
claims more root evidence than it observed. An export table that was never
captured (a header-only snapshot, or a platform whose metadata is absent)
leaves :attr:`ExportSurface.resolvable` ``False``, so a caller must degrade
to ``UNKNOWN_UNRESOLVED`` rather than "nothing is exported, so everything is
proven out of contract" -- the failure direction that would hide a real
break.

See :doc:`ADR-049
</contribute/adr/049-contract-relevance-and-compatibility-configuration>` and
its :doc:`implementation plan </contribute/plans/public-contract-default>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

from .compare.surface_graph import referenced_identifiers_by_node
from .diff_cxx_rules import owner_class_of
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, EnumType, Function, RecordType
from .name_classification import (
    STDLIB_TYPE_NAMESPACE_PREFIXES,
    is_cxx_runtime_library,
)
from .policy.public_surface import (
    _IDENT_RE,
    _TYPE_NOISE,
    PublicSurface,
    _index_surface_types,
    _is_real_type,
    _symbol_keys,
    _type_identifiers,
)
from .policy.public_surface_closure import _walk_type_closure
from .type_reachability import (
    _namespace_suffix_spellings,
    _stripped_signature_spelling,
)


@dataclass
class ExportSurface:
    """Export-rooted ABI surface of a single snapshot (ADR-049 ``exports``).

    The ``export_*`` sets are the contract domain; the ``all_*`` sets are the
    snapshot's full universe, used by a caller to tell "this entity exists and
    is provably not reachable from an export" apart from "this entity is not
    something this snapshot knows about at all" -- the same distinction
    :class:`~abicheck.surface.PublicSurface` draws with its own ``all_*``
    sets.
    """

    #: Every symbol key (:func:`~abicheck.surface._symbol_keys`: demangled
    #: name, mangled name, and the trailing ``::`` segment) of a declaration
    #: whose linker symbol appears in the observed export table.
    export_symbols: set[str] = field(default_factory=set)
    #: The *linker identity* (:func:`_linker_identity`) of every declaration
    #: that actually matched an observed export name. Deliberately separate
    #: from ``export_symbols`` above, which is alias-expanded so a *finding*
    #: naming any encoding can be looked up: a binary exporting the C symbol
    #: ``foo`` while the headers also declare an unexported ``ns::foo`` puts
    #: the bare tail ``"foo"`` in both declarations' key sets, so an
    #: intersection against ``export_symbols`` calls the unexported C++
    #: declaration a root too. Rootness is decided here, by identity --
    #: the same rule ``_unresolved_type_edges`` already applies internally
    #: (Codex review, fresh evidence).
    root_identities: set[str] = field(default_factory=set)
    #: Every symbol key of every declaration, exported or not.
    all_symbols: set[str] = field(default_factory=set)
    #: Transitive closure over the raw record/enum/typedef graph from the
    #: export roots' own signatures.
    export_types: set[str] = field(default_factory=set)
    #: Every record/enum/typedef name in the snapshot.
    all_types: set[str] = field(default_factory=set)
    #: Names (bare or qualified) resolving to more than one record/enum --
    #: a match against one of these proves nothing about *which* type a
    #: finding's root actually is (see ``PublicSurface.ambiguous_type_names``).
    ambiguous_type_names: set[str] = field(default_factory=set)
    #: True only when an export table was actually observed on this side. When
    #: False there is no root evidence at all and no membership conclusion --
    #: in either direction -- may be drawn from this surface.
    resolvable: bool = False
    #: True when at least one declaration actually matched the observed export
    #: table. A resolvable surface with no roots means the table lists exports
    #: none of this snapshot's declarations could be matched to (a
    #: mangling-scheme gap, or a binary exporting only linker-generated
    #: symbols) -- the exports are real, so nothing may be proven *out* of a
    #: contract whose roots were never resolved.
    has_roots: bool = False
    #: True when at least one export root carried real signature type
    #: information (a parameter, or a return/variable type other than the
    #: export-only sentinel ``"?"``). When False the type closure has no
    #: usable roots at all (ADR-024 D5.2's rule, applied to this domain).
    has_typed_roots: bool = False
    #: True when *every* export root carried real signature type information.
    #: ``has_typed_roots`` alone is not enough to prove a type unreachable: a
    #: second, untyped root (an export absent from the parsed headers, or one
    #: recorded with the ``"?"`` sentinel) has an unknown closure of its own,
    #: which could well contain the very type being judged (Codex review).
    #: Vacuously True when there are no roots -- pair it with ``has_roots``,
    #: never read it alone.
    all_roots_typed: bool = True
    #: Export names that *were* matched to a declaration -- bookkeeping for
    #: :attr:`unmatched_exports`, not itself a membership set (use
    #: ``export_symbols``, which is keyed by every lookup alias).
    matched_exports: set[str] = field(default_factory=set)
    #: ABI-relevant observed exports that no declaration accounted for. Each
    #: is a real entry point whose own signature -- and therefore whose own
    #: type closure -- this snapshot knows nothing about, so while any remain,
    #: an absence from ``export_types`` is not proof of unreachability: the
    #: missing export could be exactly what reaches the type being judged
    #: (Codex review). Compiler/linker artifacts are excluded via
    #: ``elf_symbol_filter.is_abi_relevant_elf_symbol``, the repo's existing
    #: owner of that judgment, so ``_init``/``_fini``/thunks/transitive stdlib
    #: exports don't count as unexplained.
    unmatched_exports: frozenset[str] = frozenset()
    #: Type identifiers reachable from an export root that name no record,
    #: enum, or typedef this snapshot carries. Where ``unmatched_exports``
    #: records an unaccounted *root*, this records an unaccounted *edge*:
    #: the node behind it was never walked, so a type absent from
    #: ``export_types`` may simply be reachable through it (Codex review).
    #: See :func:`_unresolved_type_edges` for what is scanned and
    #: :func:`_resolvable_type_spellings` for how an edge is resolved.
    unresolved_type_edges: frozenset[str] = frozenset()

    @property
    def exclusion_is_provable(self) -> bool:
        """Whether an absence from the root/closure sets is real evidence.

        Requires an observed export table, at least one resolved root, every
        root carrying real signature types, no unexplained ABI-relevant
        export left over, and no unresolved type edge in what the closure
        walked. Read this rather than the individual flags: each covers a
        different way the traversal can be incomplete, and any one of them
        alone permits a false ``PROVEN_OUT_OF_CONTRACT``.

        ``all_roots_typed`` belongs here rather than in a separate check the
        caller has to remember (CodeRabbit review): an untyped root's closure
        is unknown just as surely as an unmatched export's is, so leaving it
        out contradicted this property's own "read this, not the individual
        flags" contract. It is vacuously ``True`` with no roots, which
        ``has_roots`` above already rules out.
        """
        return (
            self.resolvable
            and self.has_roots
            and self.all_roots_typed
            and not self.unmatched_exports
            and not self.unresolved_type_edges
        )


def observed_exports_by_platform(snap: AbiSnapshot) -> dict[str, set[str]] | None:
    """Observed export names keyed by the table they came from, or ``None``.

    Provenance is kept rather than unioned away because the "is this export
    an ABI-relevant entity or a toolchain artifact" filter is format-specific:
    :func:`~abicheck.elf_symbol_filter.is_abi_relevant_elf_symbol` encodes
    ELF/Itanium conventions (``_init``/``_fini``, ``_ZTh`` thunks, a ``__``
    infix meaning "private C symbol"), which ``dumper.py`` also applies to
    Mach-O exports (same mangling family) but never to PE. Applying it to a
    PE name silently drops legitimate exports such as ``api__v2`` (Codex
    review, confirmed empirically) -- and a dropped unmatched export is
    exactly what would let :attr:`ExportSurface.exclusion_is_provable` turn
    true on incomplete evidence.
    """
    tables: dict[str, set[str]] = {}
    elf = snap.elf
    if elf is not None and elf.symbols:
        tables["elf"] = {s.name for s in elf.symbols if s.name}
    pe = snap.pe
    if pe is not None and pe.exports:
        # An unnamed ordinal-only PE export carries an empty `name`; dropping
        # it would hide a real entry point whose signature is unknown, so a
        # named sibling could then make `exclusion_is_provable` true (Codex
        # review). The `ordinal:<n>` placeholder is exactly what
        # `dumper._dump_pe` records for the same export, so a headerless PE
        # snapshot's own declarations match it.
        tables["pe"] = {(e.name or f"ordinal:{e.ordinal}") for e in pe.exports}
    macho = snap.macho
    if macho is not None and macho.exports:
        tables["macho"] = {e.name for e in macho.exports if e.name}
    return tables or None


#: Export tables whose names follow the ELF/Itanium conventions
#: :func:`~abicheck.elf_symbol_filter.is_abi_relevant_elf_symbol` encodes.
#: PE is deliberately absent: MSVC-decorated names are a different scheme, so
#: no artifact filter is applied to them and every unmatched PE export counts
#: as unexplained (the conservative direction).
_ELF_CONVENTION_TABLES: frozenset[str] = frozenset({"elf", "macho"})


def observed_export_names(snap: AbiSnapshot) -> set[str] | None:
    """Linker-symbol names in *snap*'s own export table, or ``None``.

    ``None`` means no export table was captured at all for this snapshot --
    distinct from an empty set, which would claim "this binary exports
    nothing." A snapshot carrying platform metadata whose export list is
    empty is also reported as ``None``: an export-table-less parse and a
    genuinely empty export table are indistinguishable from the recorded
    data, and treating the ambiguous case as "exports nothing" would let this
    provider prove every entity out of contract on a parse failure.

    All three platforms are unioned rather than selected by
    ``snap.platform``: a snapshot can legitimately carry more than one (e.g.
    a wheel-derived snapshot with both ELF and Mach-O metadata), and a
    symbol exported by any of them is a real export root.
    """
    tables = observed_exports_by_platform(snap)
    if tables is None:
        return None
    return set().union(*tables.values())


def _unexplained_exports(
    tables: dict[str, set[str]],
    matched: set[tuple[str, str]],
    *,
    filter_runtime: bool = True,
) -> frozenset[str]:
    """Observed exports no declaration matched, minus toolchain artifacts.

    The artifact filter is applied per table, only where its conventions
    hold (:data:`_ELF_CONVENTION_TABLES`) -- see
    :func:`observed_exports_by_platform` for why provenance is kept. A name
    exported by several tables stays unexplained when *any* table's rules
    fail to accept it as an artifact -- the conservative direction (CodeRabbit
    review caught the docstring claiming the opposite of what the loop does),
    since a wrongly-dropped unmatched export is exactly what would let
    :attr:`ExportSurface.exclusion_is_provable` turn true on incomplete
    evidence.

    *filter_runtime* mirrors ``diff_symbols_renames._should_filter_transitive_runtime_symbols``:
    when the snapshot *is* libstdc++/libc++, its ``_ZNSt...`` exports are the
    inspected library's own ABI, not transitive runtime noise, so dropping
    them would let a partial declaration set look fully accounted for (Codex
    review).
    """
    unexplained: set[str] = set()
    for table, names in tables.items():
        for n in names:
            # Matches are keyed by ``(table, name)``, never by name alone
            # (Codex review, confirmed empirically): with an ELF and a
            # Mach-O table both listing ``_foo`` and the sole declaration
            # ``foo`` matching only through the Mach-O underscore shift, a
            # flat name set marked the *ELF* ``_foo`` accounted for too --
            # leaving ``unmatched_exports`` empty and letting types be proven
            # out of contract while a real entry point's signature was
            # entirely unknown.
            if (table, n) in matched:
                continue
            if table in _ELF_CONVENTION_TABLES and not is_abi_relevant_elf_symbol(
                n, filter_transitive_runtime_symbols=filter_runtime
            ):
                continue
            unexplained.add(n)
    return frozenset(unexplained)


def _linker_identity(name: str, mangled: str) -> str:
    """A declaration's own linker identity, or ``""`` when it has none.

    The mangled name, or the plain name when the producer recorded no mangled
    one (a C symbol, or a backend that leaves the field empty; there is no
    other identity to match on). Deliberately **not**
    :func:`~abicheck.surface._symbol_keys`, whose demangled-name and bare-tail
    aliases exist so a *finding* naming any encoding can be looked up: a
    binary exporting the C symbol ``foo`` while the headers also declare an
    unexported ``ns::foo`` would otherwise match on the bare tail ``"foo"``
    and pull that unrelated C++ declaration -- and its whole type closure --
    into the export contract (Codex review, confirmed empirically).
    """
    return mangled or name


def _macho_shifted_spellings(identity: str) -> tuple[str, ...]:
    """*identity* with one leading underscore removed and one added.

    Mach-O producers disagree with the export trie by exactly one underscore
    in *both* directions (Codex review, both confirmed by reading the
    producers):

    - clang's ``mangledName`` keeps the platform underscore
      (``"__ZN3lib3addEii"``) while ``macho_metadata``'s trie parser strips one
      (``"_ZN3lib3addEii"``) -- the declaration is one underscore *longer*;
    - the headerless Mach-O path (``dumper._dump_macho``'s
      ``_normalize_macho_sym``) strips a *second* underscore when building the
      ``Function`` from that same already-stripped export name, yielding
      ``"ZN3lib3addEii"`` -- the declaration is one underscore *shorter*.

    Matched only against the Mach-O table's own names, never the union of
    every table: on ELF/PE the leading underscore is meaningful, so distinct
    declarations ``foo`` and ``_foo`` coexist, and a snapshot carrying both an
    ELF and a Mach-O table would otherwise let an ELF export ``foo`` make an
    unrelated ``_foo`` a root (Codex review, confirmed empirically).
    """
    shorter = identity[1:] if identity.startswith("_") else None
    return tuple(c for c in (shorter, "_" + identity) if c)


def _matched_export_names(
    name: str, mangled: str, tables: dict[str, set[str]], exact_owners: set[str]
) -> set[tuple[str, str]]:
    """Every observed export this declaration is a root of, as
    ``(table, spelling)`` pairs.

    Returns the *export tables' own* spellings rather than a bool, so the
    caller can subtract matched entries from the observed tables and see what
    was left over (see :attr:`ExportSurface.unmatched_exports`) -- keyed by
    table, since the same spelling can appear in two tables while only one of
    them is the one this declaration actually answers to.

    **All** matching spellings, not the first: one C declaration exported by
    a multi-platform snapshot is legitimately ``foo`` in the ELF table and
    ``_foo`` in the Mach-O one, and returning only the first would leave the
    other sitting in ``unmatched_exports``, wrongly making
    :attr:`ExportSurface.exclusion_is_provable` false (Codex review).

    Takes the per-table mapping rather than a flat union so the Mach-O
    underscore tolerance (:func:`_macho_shifted_spellings`) applies only to
    Mach-O names -- see that function for why a union would be wrong.

    *exact_owners* is every export name some declaration's own linker
    identity claims exactly. A shifted spelling is refused for those: a
    Mach-O library declaring both C functions ``foo`` and ``_foo`` while
    exporting only ``_foo`` would otherwise root *both*, since ``foo``
    shifts onto the export ``_foo`` that ``_foo`` already owns outright
    (Codex review). The underscore tolerance exists for a producer spelling
    the *same* entity differently, never to hand one declaration's export to
    another.
    """
    identity = _linker_identity(name, mangled)
    if not identity:
        return set()
    matched = {
        (table, identity) for table, names in tables.items() if identity in names
    }
    macho = tables.get("macho")
    if macho:
        matched |= {
            ("macho", c)
            for c in _macho_shifted_spellings(identity)
            if c in macho and c not in exact_owners
        }
    return matched


class _RootSeeding(NamedTuple):
    """What :func:`_seed_export_roots` learned, beyond what it wrote to the
    surface.

    ``root_identities`` is deliberately separate from
    :attr:`ExportSurface.export_symbols`: that set is alias-expanded so a
    *finding* naming any encoding of a root resolves, which makes it the
    wrong question to ask when the caller means "is this declaration itself
    a root" (see :func:`_unresolved_type_edges`).
    """

    #: Type names seeding the closure walk.
    seed_types: set[str]
    #: ``(table, spelling)`` pairs matched in the observed export tables.
    matched_pairs: set[tuple[str, str]]
    #: The linker identity of every declaration that actually matched.
    root_identities: set[str]


def _seed_export_roots(
    snap: AbiSnapshot,
    surface: ExportSurface,
    tables: dict[str, set[str]],
    *,
    owner_seed_by_identity: dict[str, str] | None = None,
) -> _RootSeeding:
    """Record export roots on *surface*; return the closure's seed type names,
    the ``(table, spelling)`` pairs actually matched, and the roots' own
    linker identities.

    Mirrors :func:`~abicheck.surface._seed_public_roots` field for field --
    the return/parameter/variable types of every root, plus a method root's
    own enclosing class (a consumer holding an exported method can declare,
    allocate, and inherit that class, so its layout is inside the export
    contract even when no *other* signature names it) -- with exactly one
    difference: rootness is decided by observed export-table membership
    (:func:`_matched_export_names`), not by
    :data:`~abicheck.model.Visibility.PUBLIC`.

    A root's *lookup* keys are still the full
    :func:`~abicheck.surface._symbol_keys` set, so a finding naming any
    encoding of a genuine root resolves; only the rootness *decision* is
    narrowed to linker identity.

    Records the export names actually matched on ``surface.matched_exports``,
    so the caller can see which observed exports no declaration accounted for.

    *owner_seed_by_identity* maps a record's own *full* identities to the
    spelling the closure walk resolves -- see :func:`compute_export_surface`,
    which builds it and documents which spellings qualify. A method root's
    owner is seeded only through an **exact** hit in it: ``owner_class_of``
    cannot tell an enclosing *class* from an enclosing *namespace* from the
    string alone, so an exported namespace function ``api::run()`` yields the
    bare fragment ``"api"``, which the walk's own alias-tolerant
    ``record_by_name`` lookup would happily resolve to an unrelated
    ``other::api`` and pull its whole field closure in (Codex review,
    confirmed empirically). This mirrors the fix ``type_reachability.py``
    already carries for the identical collision.

    Exactness must hold on *both* sides to be worth anything, which is why
    the caller's key set is restricted rather than simply "every spelling a
    record answers to": an owner string is always either a real class's
    complete scope chain or namespace noise, but a *record* spelling need
    not be a complete identity at all -- on the castxml/clang path ``name``
    is the bare leaf, so ``other::api`` is stored as ``"api"`` and the
    namespace fragment matched it "exactly" after all (Codex review, a
    second time, on the record side of the same collision).

    Called with empty *tables* on the no-export-table path, where it fills the
    ``all_*`` universe alone (nothing can match) rather than that path keeping
    its own copy of the same key derivation (CodeRabbit review).
    """
    exact_owners = _exact_export_owners(snap, tables)
    acc = _RootAccumulator()
    _seed_function_roots(
        snap, surface, tables, exact_owners, acc, owner_seed_by_identity or {}
    )
    _seed_variable_roots(snap, surface, tables, exact_owners, acc)
    seed_types = acc.seed_types
    nonroot_keys = acc.nonroot_keys
    root_identities = acc.root_identities
    matched_pairs = acc.matched_pairs

    # Drop every lookup alias a *non*-root declaration also answers to: the
    # inverse of the linker-identity fix on the rootness decision (Codex
    # review). With `ns::foo` exported and an unrelated, unexported C `foo`
    # also declared, `_symbol_keys` puts the bare tail `"foo"` in
    # `export_symbols`, so a finding about the C `foo` matched the C++ root's
    # alias. An alias shared with a non-root proves nothing about which
    # declaration a finding names.
    #
    # Two things are exempt, and both must be: the matched export *names*, and
    # each root's own *linker identity*. They are not the same string whenever
    # the Mach-O underscore tolerance fired -- an export trie entry `_foo`
    # matched by a declaration whose identity is `foo` records `"_foo"` in
    # `matched_exports`, so exempting only that let an unexported `ns::foo`
    # sharing the bare tail `"foo"` prune the genuinely exported declaration's
    # own identity (Codex review, confirmed empirically: `export_symbols`
    # emptied while the surface still reported a resolved, complete root, so
    # removing the exported `foo` came back `PROVEN_OUT_OF_CONTRACT` and
    # `_unresolved_type_edges` skipped that root's signature entirely). A
    # root's identity is unambiguous by construction the same way a matched
    # export name is -- only the *derived* aliases around it can be shared.
    surface.export_symbols -= nonroot_keys - surface.matched_exports - root_identities
    return _RootSeeding(seed_types, matched_pairs, root_identities)


@dataclass
class _RootAccumulator:
    """The four sets :func:`_seed_export_roots`'s two declaration passes build
    up together. ``nonroot_keys`` is the one that never leaves this function --
    it exists only to prune shared aliases at the end (see there)."""

    seed_types: set[str] = field(default_factory=set)
    nonroot_keys: set[str] = field(default_factory=set)
    root_identities: set[str] = field(default_factory=set)
    matched_pairs: set[tuple[str, str]] = field(default_factory=set)


def _exact_export_owners(snap: AbiSnapshot, tables: dict[str, set[str]]) -> set[str]:
    """Every export name a declaration claims by exact linker identity.

    Computed before any matching so the Mach-O underscore shift can refuse to
    steal one (see :func:`_matched_export_names`).
    """
    identities = [_linker_identity(f.name, f.mangled) for f in snap.functions]
    identities += [_linker_identity(v.name, v.mangled) for v in snap.variables]
    return {
        ident
        for ident in identities
        if ident and any(ident in names for names in tables.values())
    }


def _record_export_root(
    surface: ExportSurface,
    acc: _RootAccumulator,
    name: str,
    mangled: str,
    keys: set[str],
    matched: set[tuple[str, str]],
) -> None:
    """The bookkeeping every matched root does, function and variable alike."""
    acc.matched_pairs |= matched
    # Truthiness-guarded: an empty identity in this set would make every
    # *other* identity-less declaration look like a root to
    # `_unresolved_type_edges` (CodeRabbit review). Unreachable today --
    # `_matched_export_names` returns nothing for an empty identity, so a
    # declaration carrying one never matches -- but the invariant belongs
    # where the set is built, not in a reader's head.
    if identity := _linker_identity(name, mangled):
        acc.root_identities.add(identity)
    surface.matched_exports |= {n for _, n in matched}
    surface.export_symbols |= keys
    surface.has_roots = True


def _record_function_root_typing(surface: ExportSurface, fn: Function) -> None:
    """Fold one function root into the surface's two typed-root flags."""
    if fn.params or _is_real_type(fn.return_type):
        surface.has_typed_roots = True
    # `all_roots_typed` is strict where `has_typed_roots` is permissive:
    # a *single* parameter recorded as the `"?"` sentinel (what
    # `dwarf_snapshot._process_param` writes for a missing `DW_AT_type`)
    # leaves that root's closure incomplete just as surely as a wholly
    # untyped root does, even though the root as a whole looks typed
    # (Codex review). Every parameter and the return type must be real.
    if not _is_real_type(fn.return_type) or not all(
        _is_real_type(getattr(p, "type", None)) for p in fn.params
    ):
        surface.all_roots_typed = False


def _seed_function_roots(
    snap: AbiSnapshot,
    surface: ExportSurface,
    tables: dict[str, set[str]],
    exact_owners: set[str],
    acc: _RootAccumulator,
    owner_seed_by_identity: dict[str, str],
) -> None:
    """The function half of :func:`_seed_export_roots`: signature types seed the
    closure, and a method root additionally seeds its own enclosing class."""
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_names(fn.name, fn.mangled, tables, exact_owners)
        if not matched:
            acc.nonroot_keys |= keys
            continue
        _record_export_root(surface, acc, fn.name, fn.mangled, keys, matched)
        _record_function_root_typing(surface, fn)
        acc.seed_types |= _type_identifiers(fn.return_type)
        for p in fn.params:
            acc.seed_types |= _type_identifiers(getattr(p, "type", None))
        owner = owner_class_of(fn)
        owner_seed = owner_seed_by_identity.get(owner) if owner else None
        if owner_seed:
            acc.seed_types.add(owner_seed)


def _seed_variable_roots(
    snap: AbiSnapshot,
    surface: ExportSurface,
    tables: dict[str, set[str]],
    exact_owners: set[str],
    acc: _RootAccumulator,
) -> None:
    """The variable half of :func:`_seed_export_roots`. A variable has one type
    and no owner, so both typed-root flags follow from that single answer."""
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_names(var.name, var.mangled, tables, exact_owners)
        if not matched:
            acc.nonroot_keys |= keys
            continue
        _record_export_root(surface, acc, var.name, var.mangled, keys, matched)
        if _is_real_type(var.type):
            surface.has_typed_roots = True
        else:
            surface.all_roots_typed = False
        acc.seed_types |= _type_identifiers(var.type)


#: ``typename``/``template`` as whole words -- C++'s explicit markers for a
#: dependent spelling, which names nothing resolvable until instantiation
#: (see :func:`_edge_is_unresolved`).
_DEPENDENT_SPELLING_RE = re.compile(r"\b(?:typename|template)\b")


def _dependent_spans(type_str: str) -> list[tuple[int, int]]:
    """Character ranges of *type_str* a dependent marker actually governs.

    A marker poisons the name it introduces, not the whole spelling it
    happens to appear in (Codex review, confirmed empirically). An earlier
    revision discarded the entire string on any marker, so a mixed argument
    list such as ``Wrapper<Missing, typename T::type>`` hid the ordinary,
    genuinely-missing ``Missing`` edge behind its dependent sibling and let
    unrelated types be proven out of contract.

    A marker's region runs from the keyword to the end of the
    template-argument it sits in: the next ``,`` at its own bracket depth, or
    the ``>`` that closes the enclosing list, whichever comes first -- and to
    the end of the string when neither does, which is the shape libstdc++'s
    own fully-dependent alias targets take (``typename
    __gnu_cxx::__alloc_traits<_Alloc>::template rebind<...>::other`` is one
    dependent name from the first keyword onward).
    """
    if not _DEPENDENT_SPELLING_RE.search(type_str):
        return []
    # Depth *at* each index: a `<` is recorded at the depth it opens from, a
    # `>` at the depth it closes to, so a comma separating two arguments and
    # the marker inside one of them compare on the same scale.
    depths: list[int] = []
    depth = 0
    for ch in type_str:
        if ch == "<":
            depths.append(depth)
            depth += 1
        elif ch == ">":
            depth -= 1
            depths.append(depth)
        else:
            depths.append(depth)

    spans: list[tuple[int, int]] = []
    for match in _DEPENDENT_SPELLING_RE.finditer(type_str):
        start = match.start()
        own_depth = depths[start]
        end = len(type_str)
        for i in range(start + 1, len(type_str)):
            if depths[i] < own_depth or (type_str[i] == "," and depths[i] <= own_depth):
                end = i
                break
        spans.append((start, end))
    return spans


def _is_dependent_token(position: int, spans: list[tuple[int, int]]) -> bool:
    """Whether the token at *position* falls inside a dependent region."""
    return any(start <= position < end for start, end in spans)


class _SpellingIndex(NamedTuple):
    """How :func:`_edge_is_unresolved` decides an edge landed somewhere.

    Both maps are keyed to the **same node identities** on purpose. Keeping
    "which node does this spelling name" and "which node does the walk
    reach" as two independent string sets let each be satisfied by a
    *different* node, which is a hole rather than a resolution -- see
    :func:`_edge_is_unresolved` for the case that exposed it.
    """

    #: Spelling -> the node identities a declaration writing that spelling
    #: could be naming. Built from each node's own identity plus every
    #: namespace-suffix and stdlib-stripped form of it.
    nodes_by_spelling: dict[str, frozenset[str]]
    #: Lookup key -> the node identities
    #: :func:`~abicheck.surface._walk_type_closure` actually reaches through
    #: it. Exactly the walk's own indexes: a record/enum by its name or bare
    #: ``::`` tail, a typedef by its exact key only.
    nodes_by_walk_key: dict[str, frozenset[str]]
    #: Bare identities of nodes carrying no scope of their own -- the only
    #: ones a *more*-qualified token may fall back to naming.
    unscoped_leaves: frozenset[str]
    #: Typedef keys every one of whose colliding records/enums is
    #: toolchain-owned -- libstdc++'s bare-key alias templates. Their targets
    #: are not judged, for the same reason a toolchain record's own fields
    #: are not (see :func:`_followed_typedef_aliases`).
    toolchain_alias_keys: frozenset[str]


#: A typedef's node identity. Prefixed so an alias can never be confused with
#: a same-named record, which is the collision the two maps above exist to
#: keep apart.
def _typedef_node_id(alias: str) -> str:
    return f"typedef:{alias}"


def _resolvable_type_spellings(
    snap: AbiSnapshot,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> _SpellingIndex:
    """Build the :class:`_SpellingIndex` for this snapshot's known nodes.

    Membership here means "this edge lands on a node we know", which is the
    only question :func:`_unresolved_type_edges` asks -- deliberately *not*
    which node, so no ambiguity handling belongs here (an edge resolving to
    two candidates is still a resolved edge).

    The index keys alone are not enough, and getting this wrong is what makes
    the whole guard useless rather than merely imprecise. A real backend
    spells a type by any of several equivalent forms, so the same
    machinery ``type_reachability.py`` built for that problem is reused
    rather than re-derived:

    - :func:`~abicheck.type_reachability._namespace_suffix_spellings` for
      partial qualification -- direct-clang prints ``api::Outer::Inner`` as
      ``"Outer::Inner"``, neither the full identity nor the bare leaf;
    - :func:`~abicheck.type_reachability._stripped_signature_spelling` for
      the stdlib namespace and inline ABI-tag markers -- the DWARF backend
      spells a ``std::string`` parameter as the bare ``"string"`` while
      ``snap.typedefs``' key is the qualified ``"std::string"``, so without
      stripping, every C++ library using a standard-library type looks
      riddled with missing edges and can never prove an exclusion.
    """
    return _SpellingIndex(
        _nodes_by_spelling(snap, record_by_name, enum_by_name),
        _nodes_by_walk_key(snap, record_by_name, enum_by_name),
        _unscoped_identities(snap),
        _toolchain_owned_aliases(snap, record_by_name, enum_by_name),
    )


def _nodes_by_spelling(
    snap: AbiSnapshot,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> dict[str, frozenset[str]]:
    """Spelling -> the node identities that spelling can name.

    Registered per *node*, so a later lookup can ask whether the node a
    spelling names is the node the walk reached -- a global spelling set
    cannot answer that (see :func:`_edge_is_unresolved`).

    A record/enum's node identity is its own ``name``: that is what
    ``_walk_type_closure`` records when it reaches one, so both indices agree
    on what "the same node" means. Its *spellings* come from ``name`` and
    ``qualified_name`` alike, since backends split the two differently, plus
    every partially-qualified and stdlib-stripped form of each (see this
    module's :func:`_resolvable_type_spellings` docstring for why).
    """
    spellings: dict[str, set[str]] = {}
    for name, qualified in _indexed_node_identities(record_by_name, enum_by_name):
        _register_spellings(spellings, name, name)
        if qualified:
            _register_spellings(spellings, qualified, name)
    for alias in snap.typedefs:
        _register_spellings(spellings, alias, _typedef_node_id(alias))
    return {k: frozenset(v) for k, v in spellings.items()}


def _register_spellings(
    spellings: dict[str, set[str]], identity: str, node_id: str
) -> None:
    """Record *identity*, and every equivalent spelling of it, as naming
    *node_id* in the in-progress *spellings* index."""
    for form in (identity, _stripped_signature_spelling(identity)):
        if not form:
            continue
        spellings.setdefault(form, set()).add(node_id)
        for suffix in _namespace_suffix_spellings(form):
            spellings.setdefault(suffix, set()).add(node_id)


def _indexed_node_identities(
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> list[tuple[str, str | None]]:
    """Every indexed record/enum node as ``(name, qualified_name)``.

    Plain tuples, and the two maps flattened by two separately-typed
    comprehensions rather than one loop over a mixed
    ``(*records, *enums)`` tuple -- mypy widens that tuple's element type to
    ``object``, the same reason :func:`_unscoped_identities` builds its own
    list this way.
    """
    identities: list[tuple[str, str | None]] = [
        (rec.name, rec.qualified_name)
        for rec_nodes in record_by_name.values()
        for rec in rec_nodes
    ]
    identities += [
        (en.name, en.qualified_name)
        for en_nodes in enum_by_name.values()
        for en in en_nodes
    ]
    return identities


def _nodes_by_walk_key(
    snap: AbiSnapshot,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> dict[str, frozenset[str]]:
    """Lookup key -> node identities the walk reaches through it: the two index
    dicts (whose keys ``_index_surface_types`` builds as each node's own name
    plus, for a namespaced one, its bare ``::`` tail) and ``snap.typedefs`` by
    *exact* key, with no tail tolerance of its own."""
    by_key: dict[str, set[str]] = {}
    for key, rec_nodes in record_by_name.items():
        by_key.setdefault(key, set()).update(r.name for r in rec_nodes)
    for key, en_nodes in enum_by_name.items():
        by_key.setdefault(key, set()).update(e.name for e in en_nodes)
    for alias in snap.typedefs:
        by_key.setdefault(alias, set()).add(_typedef_node_id(alias))
    return {k: frozenset(v) for k, v in by_key.items()}


def _unscoped_identities(snap: AbiSnapshot) -> frozenset[str]:
    """Nodes whose own recorded identity carries no scope.

    A bare name is everything the snapshot knows about where such a node
    lives, so a token that spells it with a scope contradicts nothing. A node
    that *does* record a scope is excluded -- its leaf must not absorb a
    differently-scoped token (see :func:`_edge_is_unresolved`).
    """
    # Two separately-typed comprehensions rather than one over a mixed
    # `(*snap.types, *snap.enums)` tuple, whose element type mypy widens to
    # `object`.
    identities: list[tuple[str, str | None]] = [
        (rec.name, rec.qualified_name) for rec in snap.types
    ]
    identities += [(en.name, en.qualified_name) for en in snap.enums]
    scoped_leaves = {
        _namespace_suffix_spellings(qualified)[-1]
        for name, qualified in identities
        if qualified and qualified != name
    }
    candidates = [qualified or name for name, qualified in identities]
    candidates += list(snap.typedefs)
    return frozenset(c for c in candidates if "::" not in c and c not in scoped_leaves)


def _toolchain_owned_aliases(
    snap: AbiSnapshot,
    record_by_name: dict[str, list[RecordType]],
    enum_by_name: dict[str, list[EnumType]],
) -> frozenset[str]:
    """Alias keys a record or enum also owns, where *every* colliding node is
    toolchain-owned: libstdc++'s bare-key alias templates.

    Measured on a real ``g++`` library -- ``"vector" -> "std::vector<_Tp,
    polymorphic_allocator<_Tp>>"`` collides with the record ``std::vector``,
    and ``"basic_string"`` with ``std::__cxx11::basic_string``. Judging those
    targets reports the template *parameter* names in them (``_Tp``,
    ``_CharT``, ``_Traits``) as missing edges, which is the same
    toolchain-internals noise :func:`_unresolved_type_edges` already declines
    one level up -- and any one of them blocks every exclusion for every C++
    library. "Every colliding node" rather than "any" keeps the conservative
    direction: a library's own record sharing the key means the alias may well
    be its own, so it is still followed.
    """
    aliases: set[str] = set()
    for alias in snap.typedefs:
        colliding = [
            *record_by_name.get(alias, ()),
            *enum_by_name.get(alias, ()),
        ]
        if colliding and all(
            (node.qualified_name or node.name).startswith(
                STDLIB_TYPE_NAMESPACE_PREFIXES
            )
            for node in colliding
        ):
            aliases.add(alias)
    return frozenset(aliases)


def _edge_is_unresolved(
    type_str: str | None,
    index: _SpellingIndex,
) -> set[str]:
    """Identifiers in *type_str* the closure did not follow to a known node.

    Each ``_IDENT_RE`` token is judged on its own -- a template spelling
    like ``Wrapper<Payload>`` carries two independent edges, and either can
    be the missing one. This does not reuse
    :func:`~abicheck.surface._type_identifiers`, which flattens a token and
    its bare tail into one set: a qualified token whose bare tail happens to
    be unknown would then look like a second, missing edge.

    A token must match a registered spelling **as written**. An earlier
    revision also accepted its bare leaf, which resolved a qualified edge
    through an unrelated record that merely shares the leaf: with
    ``ns::Missing`` absent and ``other::Missing`` present, the edge counted
    as resolved, the closure walked the wrong record, and
    ``exclusion_is_provable`` stayed true while a type reachable only
    through the omitted definition could be proven out (Codex review,
    confirmed with a minimal snapshot). Nothing legitimate needs that
    fallback: :func:`_resolvable_type_spellings` already registers each
    node's full identity *and* every namespace-suffix spelling of it, so a
    backend that partially qualifies or fully bares a name still matches --
    what it will not do is invent a scope the snapshot never recorded.

    **Naming a known node is necessary but not sufficient.** The token must
    also be one :func:`~abicheck.surface._walk_type_closure` could actually
    look up, which is a strictly narrower set: the walk resolves a record or
    enum through its own name or bare ``::`` tail, but a *typedef* only
    through its exact key, with no spelling tolerance at all. Registered
    spellings are broader than that by construction, so accepting one on its
    own claimed a completeness the closure does not have (Codex review,
    confirmed empirically): an exported parameter spelled ``ns::Alias``
    against a captured typedef ``outer::ns::Alias -> Victim`` counted as a
    resolved edge, while the walk -- which tries only ``ns::Alias`` and its
    tail ``Alias``, neither of them that typedef's key -- never followed the
    alias, left ``Victim`` outside ``export_types``, and let a change to the
    genuinely reachable ``Victim`` be stamped ``PROVEN_OUT_OF_CONTRACT``.

    The two conditions are independent, and each catches what the other
    misses. A token can be traversable yet wrong (a qualified ``ns::Missing``
    reaching an unrelated ``other::Missing`` through the shared tail key --
    the walk follows *something*, but not the node the edge names, so the
    real node's own closure stays unknown), and a token can name a known
    node yet be untraversable (the typedef case above). Either way the
    closure has a hole, so both are reported.
    """
    if not _is_real_type(type_str) or type_str is None:
        return set()
    # A *dependent* spelling names nothing until instantiation, so no
    # snapshot can carry a node for it and scanning it can only manufacture
    # false edges. `typename`/`template` are C++'s own explicit markers for
    # exactly that context -- a language rule, not a naming heuristic --
    # which is why this is keyed on them rather than on the template
    # parameter names a particular standard library happens to use.
    # Measured: libstdc++'s `_Bit_alloc_type` alias, whose target is
    # `typename __gnu_cxx::__alloc_traits<_Alloc>::template rebind<...>
    # ::other`, was the last false edge left on a real `g++`-built library.
    spans = _dependent_spans(type_str)
    missing: set[str] = set()
    for match in _IDENT_RE.finditer(type_str):
        tok = match.group(0)
        if tok in _TYPE_NOISE or _is_dependent_token(match.start(), spans):
            continue
        if not (_named_nodes(tok, index) & _walk_reached_nodes(tok, index)):
            missing.add(tok)
    return missing


def _named_nodes(tok: str, index: _SpellingIndex) -> frozenset[str]:
    """Node identities *tok* could be naming."""
    named = set(index.nodes_by_spelling.get(tok, ()))
    if named:
        return frozenset(named)
    # Nothing matched the token as written, so fall back: a node whose own
    # recorded identity carries no scope has a bare name that is everything
    # the snapshot knows about where it lives, so a token spelling it *with*
    # a scope contradicts nothing -- the producer-side scope loss
    # ``AGENTS.md`` documents. A node that *does* record a scope never lends
    # its leaf to a differently-scoped token, which is the
    # ``ns::Missing``/``other::Missing`` rival-scope rule.
    #
    # Strictly a fallback, never an addition (Codex review, confirmed
    # empirically): with a typedef ``outer::ns::Alias`` and an unrelated
    # global record ``Alias``, letting the leaf widen an already-matched
    # token made ``ns::Alias`` "name" that record too, which the walk does
    # reach -- so the edge counted as resolved while the typedef it really
    # names was never followed. It is only a guess where the snapshot left a
    # gap; where the token matches outright there is no gap to guess about.
    leaf = _namespace_suffix_spellings(tok)[-1]
    if leaf != tok and leaf in index.unscoped_leaves:
        named |= set(index.nodes_by_spelling.get(leaf, ()))
    return frozenset(named)


def _walk_reached_nodes(tok: str, index: _SpellingIndex) -> frozenset[str]:
    """Node identities the closure walk actually reaches for *tok*.

    ``_type_identifiers`` is what the walk itself queues from a type string,
    so asking it for this one token is exactly "which keys would the walk
    have tried" -- and each key is then resolved to the nodes behind it,
    rather than treated as a bare "something was there" signal.
    """
    reached: set[str] = set()
    for ident in _type_identifiers(tok):
        reached |= set(index.nodes_by_walk_key.get(ident, ()))
    return frozenset(reached)


def _followed_typedef_aliases(
    type_str: str | None, snap: AbiSnapshot, index: _SpellingIndex
) -> set[str]:
    """Typedef keys named by *type_str* whose target is worth scanning too.

    A token is resolved to the alias key the closure *actually traversed*,
    via :func:`~abicheck.surface._type_identifiers` -- the same function the
    walk queues from -- rather than matched against ``snap.typedefs``
    literally (Codex review, confirmed empirically). The direct-clang
    backend stores a typedef under its bare ``node["name"]`` (the
    producer-side scope loss ``AGENTS.md`` records) while a signature can
    spell it qualified, so the walk followed the bare ``Alias`` while this
    scan looked up only ``ns::Alias``, found no typedef, and never inspected
    the target: an alias pointing at an absent type left
    ``unresolved_type_edges`` empty and unrelated types provable-out.

    An ambiguous key -- one a record or enum also owns, such as libstdc++'s
    bare-key alias templates ``"vector"``/``"basic_string"`` -- is **not**
    excluded, though an earlier revision excluded it on the reasoning
    ``_confirmed_type_matches`` uses ("the spelling does not prove it reached
    the alias rather than the same-named record"). That reasoning is right
    for *membership* and wrong for *completeness* (Codex review, confirmed
    empirically): ``_walk_type_closure`` does not choose between the two
    meanings, it follows **both** -- an unconditional ``snap.typedefs.get``
    plus the record lookup -- so a dangling target behind an ambiguous alias
    is a hole the closure really has. Skipping it left
    ``unresolved_type_edges`` empty and unrelated types provable-out.

    The libstdc++ noise that guard *was* aimed at is real, and dropping the
    ambiguity condition alone brought it straight back -- measured, not
    assumed: a real ``g++`` library went from zero unresolved edges to four
    (``_Tp``, ``_CharT``, ``_Traits``, ``std::basic_string``). What separates
    the two cases is not ambiguity but **whose** node the alias collides
    with: ``"vector"``/``"basic_string"`` collide with the records
    ``std::vector``/``std::__cxx11::basic_string``, and their targets spell
    that template's own *parameter* names. So the exclusion is keyed on
    :data:`~abicheck.name_classification.STDLIB_TYPE_NAMESPACE_PREFIXES`
    (:attr:`_SpellingIndex.toolchain_alias_keys`) -- the same "whose layout
    is this" rule :func:`_unresolved_type_edges` already applies to a
    toolchain record's own fields, one level up -- rather than on a
    collision the library's own types can equally cause.
    """
    if not _is_real_type(type_str) or type_str is None:
        return set()
    spans = _dependent_spans(type_str)
    aliases: set[str] = set()
    for match in _IDENT_RE.finditer(type_str):
        tok = match.group(0)
        if tok in _TYPE_NOISE or _is_dependent_token(match.start(), spans):
            continue
        for ident in _type_identifiers(tok):
            if ident in snap.typedefs and ident not in index.toolchain_alias_keys:
                aliases.add(ident)
    return aliases


def _unresolved_type_edges(
    snap: AbiSnapshot,
    surface: ExportSurface,
    reached_types: set[str],
    root_identities: set[str],
    index: _SpellingIndex,
) -> frozenset[str]:
    """Type identifiers reachable from an export root that name nothing known.

    Such an edge is a hole in the very graph traversal
    :attr:`ExportSurface.exclusion_is_provable` claims was complete: the
    node behind it was never walked, so whatever *it* reaches is unknown,
    and a type absent from ``export_types`` may simply be sitting there
    (Codex review). ``all_roots_typed`` does not cover this -- a root whose
    every parameter is a real, non-sentinel type string still has an
    unknown closure when one of those type strings names a record the
    snapshot does not carry.

    Scanned over the roots' own signatures plus the fields and bases of the
    records the closure actually *reached*. A broken edge inside an
    unreachable internal type says nothing about what an export can reach,
    so including it would block exclusion for a graph defect that cannot
    matter.

    Rootness is decided by *linker identity* against *root_identities*, not
    by intersecting :attr:`ExportSurface.export_symbols` (Codex review,
    confirmed empirically). That set is alias-expanded so a finding naming
    any encoding of a root resolves, which is the wrong question here: with
    an exported C ``foo`` and an unexported ``ns::foo``, the latter's own
    bare-tail alias ``"foo"`` intersects it, so the *non-root*'s signature
    was scanned and any type it names but the snapshot omits became a
    spurious unresolved edge -- blocking exclusion for every finding on the
    strength of an internal declaration that no export can reach. Identity
    is exact for this question in both directions: two declarations sharing
    one identity necessarily match the same export names, so neither a
    false root nor a missed one is possible.

    A scanned spelling that resolves to a **typedef** is followed to its
    target, transitively: an alias whose own target names nothing known
    leaves the closure just as incomplete as a missing record would, and
    unlike the "alias absent entirely" shape it is invisible to the
    signature scan (the alias key itself resolves fine) -- CodeRabbit
    review. Following happens only from an already-scanned spelling, never
    over ``snap.typedefs`` wholesale, and an *ambiguous* alias key is not
    followed. Both restrictions are what keep this usable: the direct-clang
    backend stores a typedef under its bare ``node["name"]`` (the
    producer-side scope loss ``AGENTS.md`` records), so libstdc++'s alias
    *templates* land under bare keys like ``"vector"``/``"basic_string"``
    that collide with the real records of those names, and its *member*
    typedefs under keys like ``"allocator_type"``/``"pointer"`` whose targets
    are the enclosing template's own parameters (``_Alloc``) -- names no
    snapshot can carry. Reaching those requires walking a toolchain record's
    internals, which the rule below already declines to do.

    A **toolchain-owned** record's own internals are skipped for the same
    reason one level up. Measured against a real ``g++ -shared -g`` library
    whose public signature takes a ``std::vector<api::Item>``: scanning
    libstdc++'s own records reported five "missing" edges -- ``_Tp``,
    ``_Alloc``, ``_CharT``, ``_Traits`` (template *parameter* names, spelled
    verbatim in the generic definition's field types) and
    ``_S_local_capacity`` (a static constant appearing in an array bound) --
    none of which is a declaration the snapshot failed to carry, and any one
    of which would have blocked every exclusion for every C++ library.
    ``STDLIB_TYPE_NAMESPACE_PREFIXES`` is the repo's existing owner of
    "whose layout is this", and the reachability closure itself is
    unaffected: the walk still follows those fields, so nothing is hidden --
    only the *judgment* that an unresolved spelling there is evidence about
    the inspected library's graph is dropped.
    """
    unresolved: set[str] = set()
    seen_aliases: set[str] = set()

    def scan(type_str: str | None) -> None:
        # An explicit stack, not recursion: an alias chain
        # (``A0 -> A1 -> ... -> An``) is followed one link per iteration, and
        # a chain longer than Python's frame limit raised ``RecursionError``
        # and aborted the whole comparison -- reproduced at 1200 links, which
        # template-heavy C++ reaches (one real library measured here already
        # carries 399 typedefs). Same conversion, for the same reason, that
        # ``type_reachability._finditer_allow_nested`` already carries.
        pending: list[str | None] = [type_str]
        while pending:
            current = pending.pop()
            unresolved.update(_edge_is_unresolved(current, index))
            for alias in _followed_typedef_aliases(current, snap, index):
                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)
                pending.append(snap.typedefs[alias])

    for fn in snap.functions:
        if _linker_identity(fn.name, fn.mangled) not in root_identities:
            continue
        scan(fn.return_type)
        for p in fn.params:
            scan(getattr(p, "type", None))
    for var in snap.variables:
        if _linker_identity(var.name, var.mangled) in root_identities:
            scan(var.type)
    for rec in snap.types:
        if rec.name not in reached_types and rec.qualified_name not in reached_types:
            continue
        if (rec.qualified_name or rec.name).startswith(STDLIB_TYPE_NAMESPACE_PREFIXES):
            continue
        for f in rec.fields:
            scan(f.type)
        for base in (*rec.bases, *rec.virtual_bases):
            scan(base)
    return frozenset(unresolved)


def compute_export_surface(snap: AbiSnapshot) -> ExportSurface:
    """Compute *snap*'s export-rooted ABI surface (ADR-049 ``exports``).

    Roots are the declarations matching :func:`observed_export_names`; the
    type set is the transitive raw-graph closure over what those roots
    reference. With no observed export table the returned surface is
    ``resolvable=False`` and otherwise carries only the snapshot's ``all_*``
    universe, so a caller can still tell whether an entity is *known* while
    correctly refusing to decide its membership.
    """
    surface = ExportSurface()

    # A `PublicSurface` is used purely as the scratch structure
    # `_index_surface_types`/`_walk_type_closure` already know how to fill --
    # reusing surface.py's own indexing and closure walk verbatim, rather
    # than a second implementation of the same graph traversal that could
    # drift. Only its type-universe/closure outputs are read back; its
    # public_symbols/origin bookkeeping is header-domain state this domain
    # deliberately ignores.
    scratch = PublicSurface()
    record_by_name, enum_by_name = _index_surface_types(snap, scratch)
    surface.all_types = set(scratch.all_types)
    surface.ambiguous_type_names = set(scratch.ambiguous_type_names)

    # `_index_surface_types` keys the closure index by `RecordType.name` and
    # its `::` tail only. For a castxml/clang snapshot `name` is the *bare*
    # leaf, so `ns1::Foo` and `ns2::Foo` collapse onto one ambiguous key and
    # neither owner has an unambiguous handle at all -- an exported
    # `ns1::Foo::bar()` with no class-typed signature then leaves its own
    # class outside the closure, and a layout finding on that exported class
    # is reported `PROVEN_OUT_OF_CONTRACT` (Codex review, confirmed with a
    # minimal snapshot: `export_types` came back empty while
    # `exclusion_is_provable` was true). Adding each record's
    # `qualified_name` to this *local* index gives every such record an exact
    # handle. Deliberately not done in `surface.py`'s shared indexing: that
    # index is relied on by every other consumer of the closure walk, and
    # AGENTS.md already records its bare-tail lookup as needing its own
    # scoped design rather than a drive-by change. `ambiguous_type_names` is
    # computed above, from the un-augmented index, so these added keys cannot
    # make an existing name look ambiguous.
    for rec in snap.types:
        if rec.qualified_name and rec.qualified_name != rec.name:
            record_by_name.setdefault(rec.qualified_name, []).append(rec)
    # `EnumType` carries the same bare-`name`/separate-`qualified_name` split
    # on the castxml/clang path, so a namespaced enum needs the same exact
    # handle for the same reason (CodeRabbit review).
    for en in snap.enums:
        if en.qualified_name and en.qualified_name != en.name:
            enum_by_name.setdefault(en.qualified_name, []).append(en)

    # `_index_surface_types` counts collisions across the record and enum
    # indexes only -- `snap.typedefs` never enters its tally -- so a typedef
    # alias sharing a name with a record/enum key is not flagged ambiguous
    # even though `_walk_type_closure` resolves that one name through *both*
    # (Codex review, confirmed with a minimal snapshot: a global `typedef
    # Foo` alongside a castxml-recorded `ns::Foo`, whose bare `name` is also
    # `"Foo"`, left `ambiguous_type_names` empty). A finding naming it was
    # then confirmed against whichever node the walk happened to reach, with
    # no way to tell which one it was actually about -- exactly the
    # collision `ambiguous_type_names` exists to refuse, and the same
    # treatment a record-vs-record or record-vs-enum collision already gets.
    # Computed after the augmentation above so a typedef aliasing a
    # *qualified* record identity is caught too.
    surface.ambiguous_type_names |= {
        alias
        for alias in snap.typedefs
        if alias in record_by_name or alias in enum_by_name
    }

    tables = observed_exports_by_platform(snap)
    if tables is None:
        # No root evidence. Still populate `all_symbols` so a caller can
        # distinguish a known-but-undecidable entity from an unknown one --
        # via the same seeding helper (empty tables match nothing), so the two
        # paths cannot derive that universe differently.
        _seed_export_roots(snap, surface, {})
        return surface

    # A method root's owner may only be seeded through an exact identity hit
    # (see `_seed_export_roots`). Each spelling a producer can record maps to
    # the spelling the closure walk resolves *that same record* by: DWARF
    # bakes the qualified path into `name` directly, while castxml/clang keep
    # `name` bare and put the qualified form in `qualified_name` -- which the
    # index augmentation above just made resolvable on its own.
    # An ambiguous *bare* name is dropped rather than seeded:
    # `_walk_type_closure` resolves a name through `record_by_name`, which is
    # keyed by bare names and tails, so seeding the bare `Foo` shared by
    # `ns1::Foo` and `ns2::Foo` walks *both* and pulls fields reachable only
    # from the unrelated one into the closure (Codex review).
    # `ambiguous_type_names` is exactly surface.py's own record of which names
    # resolve to more than one type. A record's qualified name is kept even
    # then, since it names exactly one record; two records genuinely sharing
    # one qualified name are the same type (an ODR/incomplete duplicate), and
    # walking both is `surface.py`'s own established over-keeping direction.
    # A bare `name` counts as a *full* identity only when the producer did
    # not separately record a differing `qualified_name`: on the
    # castxml/clang path `name` is the leaf alone, so an unrelated
    # `other::api` record is stored as `name="api"`, and registering that
    # leaf let `owner_class_of`'s namespace fragment from an exported
    # `api::run()` match it "exactly" -- reopening, through the record side,
    # the very collision the exact-match rule closes on the owner side
    # (Codex review, confirmed with a minimal snapshot: `other::api`'s own
    # field `Secret` landed in `export_types`). Nothing real is lost by
    # dropping the leaf there: whenever a record carries a qualified name,
    # `owner_class_of` yields the complete scope chain too -- from an
    # already-qualified declaration name, or from the mangled symbol's full
    # nested-name -- so the qualified key below is what a genuine owner
    # matches on.
    owner_seed_by_identity: dict[str, str] = {}
    for rec in snap.types:
        bare_is_leaf_only = bool(rec.qualified_name) and rec.qualified_name != rec.name
        if not bare_is_leaf_only and rec.name not in surface.ambiguous_type_names:
            owner_seed_by_identity.setdefault(rec.name, rec.name)
        if rec.qualified_name:
            owner_seed_by_identity.setdefault(rec.qualified_name, rec.qualified_name)

    roots = _seed_export_roots(
        snap, surface, tables, owner_seed_by_identity=owner_seed_by_identity
    )
    surface.resolvable = True
    surface.root_identities = set(roots.root_identities)
    surface.unmatched_exports = _unexplained_exports(
        tables,
        roots.matched_pairs,
        filter_runtime=not (
            is_cxx_runtime_library(snap.library)
            or is_cxx_runtime_library(getattr(snap.elf, "soname", ""))
        ),
    )

    refs = referenced_identifiers_by_node(snap)
    _walk_type_closure(
        refs, snap, scratch, record_by_name, enum_by_name, roots.seed_types
    )
    surface.export_types = set(scratch.public_types)
    # After the walk, so only edges the closure actually reached are scanned.
    surface.unresolved_type_edges = _unresolved_type_edges(
        snap,
        surface,
        surface.export_types,
        roots.root_identities,
        _resolvable_type_spellings(snap, record_by_name, enum_by_name),
    )
    return surface

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
  from those roots' signatures, reusing :mod:`abicheck.surface`'s own
  :func:`~abicheck.surface._walk_type_closure` verbatim so the two domains
  cannot drift apart in how they follow fields, bases, and typedef targets.
  Only the *seeds* differ, which is precisely the difference ADR-049 D2 draws
  between the two modes.

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

from dataclasses import dataclass, field

from .diff_cxx_rules import owner_class_of
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot
from .surface import (
    PublicSurface,
    _index_surface_types,
    _is_real_type,
    _symbol_keys,
    _type_identifiers,
    _walk_type_closure,
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

    @property
    def exclusion_is_provable(self) -> bool:
        """Whether an absence from the root/closure sets is real evidence.

        Requires an observed export table, at least one resolved root, and no
        unexplained ABI-relevant export left over. Read this rather than the
        individual flags: each covers a different way the root set can be
        incomplete, and any one of them alone permits a false
        ``PROVEN_OUT_OF_CONTRACT``.
        """
        return self.resolvable and self.has_roots and not self.unmatched_exports


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
    tables: dict[str, set[str]], matched: set[str]
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
    """
    unexplained: set[str] = set()
    for table, names in tables.items():
        for n in names - matched:
            if table in _ELF_CONVENTION_TABLES and not is_abi_relevant_elf_symbol(n):
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


def _matched_export_name(
    name: str, mangled: str, tables: dict[str, set[str]]
) -> str | None:
    """The observed export name this declaration is a root of, or ``None``.

    Returns the *export table's own* spelling rather than a bool, so the
    caller can subtract matched names from the observed tables and see what
    was left over (see :attr:`ExportSurface.unmatched_exports`).

    Takes the per-table mapping rather than a flat union so the Mach-O
    underscore tolerance (:func:`_macho_shifted_spellings`) applies only to
    Mach-O names -- see that function for why a union would be wrong.
    """
    identity = _linker_identity(name, mangled)
    if not identity:
        return None
    for names in tables.values():
        if identity in names:
            return identity
    macho = tables.get("macho")
    if macho:
        for cand in _macho_shifted_spellings(identity):
            if cand in macho:
                return cand
    return None


def _seed_export_roots(
    snap: AbiSnapshot,
    surface: ExportSurface,
    tables: dict[str, set[str]],
    *,
    owner_seed_by_identity: dict[str, str] | None = None,
) -> set[str]:
    """Record export roots on *surface*; return the closure's seed type names.

    Mirrors :func:`~abicheck.surface._seed_public_roots` field for field --
    the return/parameter/variable types of every root, plus a method root's
    own enclosing class (a consumer holding an exported method can declare,
    allocate, and inherit that class, so its layout is inside the export
    contract even when no *other* signature names it) -- with exactly one
    difference: rootness is decided by observed export-table membership
    (:func:`_matched_export_name`), not by
    :data:`~abicheck.model.Visibility.PUBLIC`.

    A root's *lookup* keys are still the full
    :func:`~abicheck.surface._symbol_keys` set, so a finding naming any
    encoding of a genuine root resolves; only the rootness *decision* is
    narrowed to linker identity.

    Records the export names actually matched on ``surface.matched_exports``,
    so the caller can see which observed exports no declaration accounted for.

    *owner_seed_by_identity* maps a record's own identities (its ``name`` and,
    when the producer recorded one, its ``qualified_name``) to the spelling
    the closure walk resolves. A method root's owner is seeded only through an
    **exact** hit in it: ``owner_class_of`` cannot tell an enclosing *class*
    from an enclosing *namespace* from the string alone, so an exported
    namespace function ``api::run()`` yields the bare fragment ``"api"``,
    which the walk's own alias-tolerant ``record_by_name`` lookup would
    happily resolve to an unrelated ``other::api`` and pull its whole field
    closure in (Codex review, confirmed empirically). This mirrors the fix
    ``type_reachability.py`` already carries for the identical collision;
    unlike a genuine signature spelling, an owner is always either a real
    class's complete scope chain or namespace noise, so exact matching loses
    no real case.

    Called with empty *tables* on the no-export-table path, where it fills the
    ``all_*`` universe alone (nothing can match) rather than that path keeping
    its own copy of the same key derivation (CodeRabbit review).
    """
    owner_seed_by_identity = owner_seed_by_identity or {}
    seed_types: set[str] = set()
    nonroot_keys: set[str] = set()
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_name(fn.name, fn.mangled, tables)
        if matched is None:
            nonroot_keys |= keys
            continue
        surface.matched_exports.add(matched)
        surface.export_symbols |= keys
        surface.has_roots = True
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
        seed_types |= _type_identifiers(fn.return_type)
        for p in fn.params:
            seed_types |= _type_identifiers(getattr(p, "type", None))
        owner = owner_class_of(fn)
        owner_seed = owner_seed_by_identity.get(owner) if owner else None
        if owner_seed:
            seed_types.add(owner_seed)
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_name(var.name, var.mangled, tables)
        if matched is None:
            nonroot_keys |= keys
            continue
        surface.matched_exports.add(matched)
        surface.export_symbols |= keys
        surface.has_roots = True
        if _is_real_type(var.type):
            surface.has_typed_roots = True
        else:
            surface.all_roots_typed = False
        seed_types |= _type_identifiers(var.type)

    # Drop every lookup alias a *non*-root declaration also answers to: the
    # inverse of the linker-identity fix on the rootness decision (Codex
    # review). With `ns::foo` exported and an unrelated, unexported C `foo`
    # also declared, `_symbol_keys` puts the bare tail `"foo"` in
    # `export_symbols`, so a finding about the C `foo` matched the C++ root's
    # alias. An alias shared with a non-root proves nothing about which
    # declaration a finding names; the matched export names themselves are
    # exempt, being unambiguous by construction.
    surface.export_symbols -= nonroot_keys - surface.matched_exports
    return seed_types


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

    tables = observed_exports_by_platform(snap)
    if tables is None:
        # No root evidence. Still populate `all_symbols` so a caller can
        # distinguish a known-but-undecidable entity from an unknown one --
        # via the same seeding helper (empty tables match nothing), so the two
        # paths cannot derive that universe differently.
        _seed_export_roots(snap, surface, {})
        return surface

    # A method root's owner may only be seeded through an exact identity hit
    # (see `_seed_export_roots`). Both spellings a producer can record are
    # mapped to the record's own `name`, which is what the closure walk's
    # `record_by_name` index is keyed by: DWARF bakes the qualified path into
    # `name` directly, while castxml/clang keep `name` bare and put the
    # qualified form in `qualified_name`.
    owner_seed_by_identity: dict[str, str] = {}
    for rec in snap.types:
        owner_seed_by_identity[rec.name] = rec.name
        if rec.qualified_name:
            owner_seed_by_identity.setdefault(rec.qualified_name, rec.name)

    seed_types = _seed_export_roots(
        snap, surface, tables, owner_seed_by_identity=owner_seed_by_identity
    )
    surface.resolvable = True
    surface.unmatched_exports = _unexplained_exports(tables, surface.matched_exports)

    _walk_type_closure(snap, scratch, record_by_name, enum_by_name, seed_types)
    surface.export_types = set(scratch.public_types)
    return surface

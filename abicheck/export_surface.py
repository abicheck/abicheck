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
    names: set[str] = set()
    observed = False
    elf = getattr(snap, "elf", None)
    if elf is not None and getattr(elf, "symbols", None):
        observed = True
        names |= {s.name for s in elf.symbols if s.name}
    pe = getattr(snap, "pe", None)
    if pe is not None and getattr(pe, "exports", None):
        observed = True
        names |= {e.name for e in pe.exports if e.name}
    macho = getattr(snap, "macho", None)
    if macho is not None and getattr(macho, "exports", None):
        observed = True
        names |= {e.name for e in macho.exports if e.name}
    return names if observed else None


def _export_identity_candidates(
    name: str, mangled: str, *, underscore_alias: bool
) -> tuple[str, ...]:
    """The linker spellings a declaration could appear under in an export table.

    Linker identity only -- the declaration's mangled name, or its plain name
    when the producer recorded no mangled one (a C symbol, or a backend that
    leaves the field empty; there is no other identity to match on).
    Deliberately **not** :func:`~abicheck.surface._symbol_keys`, whose
    demangled-name and bare-tail aliases exist so a *finding* naming any
    encoding can be looked up: a binary exporting the C symbol ``foo`` while
    the headers also declare an unexported ``ns::foo`` would otherwise match on
    the bare tail ``"foo"`` and pull that unrelated C++ declaration -- and its
    whole type closure -- into the export contract (Codex review, confirmed
    empirically).

    *underscore_alias* adds **both** underscore-shifted spellings, because
    Mach-O producers disagree with each other by exactly one underscore in
    *both* directions (Codex review, both confirmed by reading the producers):

    - clang's ``mangledName`` keeps the platform underscore
      (``"__ZN3lib3addEii"``) while ``macho_metadata``'s trie parser strips one
      (``"_ZN3lib3addEii"``) -- the declaration is one underscore *longer*;
    - the headerless Mach-O path (``dumper._dump_macho``'s
      ``_normalize_macho_sym``) strips a *second* underscore when building the
      ``Function`` from that same already-stripped export name, yielding
      ``"ZN3lib3addEii"`` -- the declaration is one underscore *shorter*.

    Only ever gated on the snapshot carrying Mach-O metadata: on ELF/PE the
    leading underscore is meaningful, and distinct declarations ``foo`` and
    ``_foo`` can coexist there, so an unconditional shift invents an export
    root for one from an export table listing only the other (Codex review,
    confirmed empirically).
    """
    identity = mangled or name
    if not identity:
        return ()
    if not underscore_alias:
        return (identity,)
    shorter = identity[1:] if identity.startswith("_") else None
    return tuple(c for c in (identity, shorter, "_" + identity) if c)


def _matched_export_name(
    name: str, mangled: str, export_names: set[str], *, underscore_alias: bool
) -> str | None:
    """The observed export name this declaration is a root of, or ``None``.

    Returns the *export table's own* spelling rather than a bool, so the
    caller can subtract matched names from the observed table and see what
    was left over (see :attr:`ExportSurface.unmatched_exports`).
    """
    for cand in _export_identity_candidates(
        name, mangled, underscore_alias=underscore_alias
    ):
        if cand in export_names:
            return cand
    return None


def _seed_export_roots(
    snap: AbiSnapshot,
    surface: ExportSurface,
    export_names: set[str],
    *,
    underscore_alias: bool = False,
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

    Called with an empty *export_names* on the no-export-table path, where it
    fills the ``all_*`` universe alone (nothing can match) rather than that
    path keeping its own copy of the same key derivation (CodeRabbit review).
    """
    seed_types: set[str] = set()
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_name(
            fn.name, fn.mangled, export_names, underscore_alias=underscore_alias
        )
        if matched is None:
            continue
        surface.matched_exports.add(matched)
        surface.export_symbols |= keys
        surface.has_roots = True
        if fn.params or _is_real_type(fn.return_type):
            surface.has_typed_roots = True
        else:
            surface.all_roots_typed = False
        seed_types |= _type_identifiers(fn.return_type)
        for p in fn.params:
            seed_types |= _type_identifiers(getattr(p, "type", None))
        owner = owner_class_of(fn)
        if owner:
            seed_types |= _type_identifiers(owner)
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        matched = _matched_export_name(
            var.name, var.mangled, export_names, underscore_alias=underscore_alias
        )
        if matched is None:
            continue
        surface.matched_exports.add(matched)
        surface.export_symbols |= keys
        surface.has_roots = True
        if _is_real_type(var.type):
            surface.has_typed_roots = True
        else:
            surface.all_roots_typed = False
        seed_types |= _type_identifiers(var.type)
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

    export_names = observed_export_names(snap)
    if export_names is None:
        # No root evidence. Still populate `all_symbols` so a caller can
        # distinguish a known-but-undecidable entity from an unknown one --
        # via the same seeding helper (an empty export-name set matches
        # nothing), so the two paths cannot derive that universe differently.
        _seed_export_roots(snap, surface, set())
        return surface

    seed_types = _seed_export_roots(
        snap,
        surface,
        export_names,
        underscore_alias=getattr(snap, "macho", None) is not None,
    )
    surface.resolvable = True
    surface.unmatched_exports = frozenset(
        n
        for n in export_names - surface.matched_exports
        if is_abi_relevant_elf_symbol(n)
    )

    _walk_type_closure(snap, scratch, record_by_name, enum_by_name, seed_types)
    surface.export_types = set(scratch.public_types)
    return surface

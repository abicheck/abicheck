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
    #: True when at least one export root carried real signature type
    #: information (a parameter, or a return/variable type other than the
    #: export-only sentinel ``"?"``). When False the type closure has no
    #: usable roots, so an absence from ``export_types`` is not evidence of
    #: unreachability (ADR-024 D5.2's rule, applied to this domain).
    has_typed_roots: bool = False


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


def _matches_export_table(keys: set[str], export_names: set[str]) -> bool:
    """Whether any of a declaration's symbol *keys* names an observed export.

    Tolerates one platform quirk, exactly as ``dumper_clang._symbol_candidates``
    already does for the same reason: clang's ``mangledName`` carries an extra
    leading underscore on macOS (``"__ZN3lib3addEii"``), while the Mach-O
    export trie's own names are recorded with the platform underscore already
    stripped (``macho_metadata``'s parser, "Leading '_' stripped, matching
    exports"). Without de-prefixing, every C++ declaration in a Mach-O
    snapshot would fail to match a real export it *is* -- and in this domain a
    missed root is not a missed detail, it is a false
    ``PROVEN_OUT_OF_CONTRACT`` for the whole library.

    Deliberately one-directional and only for an underscore-prefixed key: it
    can turn a missed match into a match, never the reverse, and a de-prefixed
    spelling that collides with an unrelated real export would have to be an
    exact linker-name collision, which the export table itself already
    disallows.
    """
    if keys & export_names:
        return True
    return any(k.startswith("_") and k[1:] in export_names for k in keys)


def _seed_export_roots(
    snap: AbiSnapshot, surface: ExportSurface, export_names: set[str]
) -> set[str]:
    """Record export roots on *surface*; return the closure's seed type names.

    Mirrors :func:`~abicheck.surface._seed_public_roots` field for field --
    the return/parameter/variable types of every root, plus a method root's
    own enclosing class (a consumer holding an exported method can declare,
    allocate, and inherit that class, so its layout is inside the export
    contract even when no *other* signature names it) -- with exactly one
    difference: rootness is decided by observed export-table membership, not
    by :data:`~abicheck.model.Visibility.PUBLIC`.
    """
    seed_types: set[str] = set()
    for fn in snap.functions:
        keys = _symbol_keys(fn.name, fn.mangled)
        surface.all_symbols |= keys
        if not _matches_export_table(keys, export_names):
            continue
        surface.export_symbols |= keys
        if fn.params or _is_real_type(fn.return_type):
            surface.has_typed_roots = True
        seed_types |= _type_identifiers(fn.return_type)
        for p in fn.params:
            seed_types |= _type_identifiers(getattr(p, "type", None))
        owner = owner_class_of(fn)
        if owner:
            seed_types |= _type_identifiers(owner)
    for var in snap.variables:
        keys = _symbol_keys(var.name, var.mangled)
        surface.all_symbols |= keys
        if not _matches_export_table(keys, export_names):
            continue
        surface.export_symbols |= keys
        if _is_real_type(var.type):
            surface.has_typed_roots = True
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
        # distinguish a known-but-undecidable entity from an unknown one.
        for fn in snap.functions:
            surface.all_symbols |= _symbol_keys(fn.name, fn.mangled)
        for var in snap.variables:
            surface.all_symbols |= _symbol_keys(
                var.name, getattr(var, "mangled", "") or ""
            )
        return surface

    seed_types = _seed_export_roots(snap, surface, export_names)
    surface.resolvable = True

    _walk_type_closure(snap, scratch, record_by_name, enum_by_name, seed_types)
    surface.export_types = set(scratch.public_types)
    return surface

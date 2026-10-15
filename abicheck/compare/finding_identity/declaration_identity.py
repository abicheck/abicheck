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

"""Declaration identity: one function or variable, tiered, and an index
that refuses to join on an identity ambiguous within its snapshot."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, TypeVar

from ...model.entity_identity import source_relative_identity
from ...model.root_relative_path import RootRelativePath
from ...model.signature_normalization import canonicalize_function_signature_param_type

if TYPE_CHECKING:
    from ...model import Function, Variable

from .mangled import normalize_mangled_name

#: The declaration type a :class:`SymbolIdentityIndex` holds (``Function`` or
#: ``Variable`` in this codebase; the index itself needs nothing from either
#: beyond what its ``resolve`` callable reads).
_T = TypeVar("_T")

#: Identity-confidence tiers, matching the ``canonical``/``normalized``/
#: ``reduced`` vocabulary ``buildsource/entity_identity.py`` (ADR-048) uses
#: for the same principle at the L5 source-graph layer. Kept as independent
#: string constants (not imported from that module) to preserve the
#: core-package/optional-layer dependency direction described above.
IDENTITY_TIER_CANONICAL = "canonical"
IDENTITY_TIER_NORMALIZED = "normalized"
IDENTITY_TIER_REDUCED = "reduced"


@dataclass(frozen=True)
class FindingIdentity:
    """Canonical identity computed for one flat finding / declaration.

    ``primary_id`` is the single key old/new matching or cross-detector
    dedup should match on. ``aliases`` carries every other identity signal
    available (never used as the primary key, but available for a future
    alias-match reconciliation tier, mirroring
    ``buildsource.entity_identity.CanonicalIdentity``).
    """

    primary_id: str
    tier: str  # one of IDENTITY_TIER_*
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_id": self.primary_id,
            "tier": self.tier,
            "aliases": list(self.aliases),
        }


def normalized_signature(
    qualified_name: str, kind: str, param_types: tuple[str, ...] = ()
) -> str:
    """A normalized signature: qualified name + kind + arity/parameter
    types (or any other short discriminator tuple a caller supplies).
    Deterministic and order-preserving so two identically-declared
    overloads never collide.
    """
    parts = [qualified_name or "", kind or "", str(len(param_types)), *param_types]
    return "sig:" + "\x1f".join(parts)


def resolve_symbol_identity(
    *,
    mangled: str | None = None,
    name: str | None = None,
    qualified_name: str | None = None,
    kind: str = "",
    param_types: tuple[str, ...] = (),
    source_location: RootRelativePath | None = None,
) -> FindingIdentity:
    """Resolve the canonical identity for one function/variable declaration
    from whatever facts a producer actually supplied. Never fabricates a
    fact: a tier is claimed only when its corresponding input is present.
    """
    qn = qualified_name or name or ""
    real_mangled = normalize_mangled_name(mangled, name)

    # A present-but-not-a-verified-mangling `mangled` value is more
    # tier-stable than `qualified_name`/`name` for the NORMALIZED-tier
    # basis: it is the raw, literal exported symbol -- diff_symbols.py's
    # existing old/new maps are keyed by this same value for every
    # function/variable, mangled or not (Codex review, generalized from
    # the extern "C" function case to cover variables too, since
    # ``Variable`` has no ``is_extern_c`` field to gate a narrower fix on).
    # `qualified_name`/`name` is comparatively less stable: a DWARF-derived
    # record scope-qualifies a namespaced C-linkage entity ("ns::foo"),
    # while a symbols-only fallback snapshot of the SAME export has no
    # scope info to qualify with at all ("foo") -- using `qn` here would
    # fragment the same entity's identity across evidence tiers.
    normalized_basis = mangled if (mangled and not real_mangled) else qn
    sig = normalized_signature(normalized_basis, kind, param_types)
    # `name or qn` alone drops the raw export entirely when a partial
    # producer supplies only `mangled` (e.g. a symbols-only snapshot with
    # no name/qualified_name at all) -- normalized_basis already prefers
    # that raw mangled value in exactly this case, so falling back to it
    # here keeps the relsrc: alias's basis consistent with sig/primary_id
    # instead of silently losing the only entity signal available (Codex
    # review, fresh evidence).
    rel = source_relative_identity(source_location, name or qn or normalized_basis)

    aliases: list[str] = []
    if real_mangled:
        aliases.append(f"mangled:{real_mangled}")
    if name:
        aliases.append(f"name:{name}")
    if qn:
        aliases.append(f"qualified:{qn}")
    aliases.append(sig)
    if source_location is not None:
        aliases.append(f"relsrc:{rel}")

    if real_mangled:
        primary = f"mangled:{real_mangled}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    # `normalized_basis`, not `qn`: a partial producer supplying only
    # `mangled` (e.g. resolve_symbol_identity(mangled="plain_export",
    # name=None)) has `qn == ""` but `normalized_basis == "plain_export"`
    # (the raw export, already the actual basis `sig` was built from) --
    # checking `qn` alone previously demoted that case all the way to a
    # REDUCED synthetic hash, even though the identical export becomes
    # NORMALIZED as soon as another producer also supplies `name`,
    # fragmenting one entity's identity solely on metadata completeness
    # (Codex review, fresh evidence).
    if normalized_basis:
        return FindingIdentity(sig, IDENTITY_TIER_NORMALIZED, tuple(aliases))

    # Synthetic fallback: clearly marked low-confidence (IDENTITY_TIER_REDUCED,
    # "synthetic:" prefix) -- used only when nothing else is available at all.
    basis = "\x1f".join(
        str(x) for x in (mangled, name, qualified_name, kind, source_location) if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return FindingIdentity(synthetic, IDENTITY_TIER_REDUCED, tuple(aliases))


def resolve_function_identity(func: Function) -> FindingIdentity:
    """Canonical identity for a :class:`~abicheck.model.Function`.

    ``func.mangled``/``func.name`` are the same two fields
    ``diff_symbols._diff_functions`` already keys its old/new maps and
    extern-C name fallback by; this resolves the identical precedence as
    one documented, tested primitive instead of the inline logic there.

    Parameter types feed the NORMALIZED-tier signature only for functions
    that are NOT ``extern "C"`` -- C has no overload resolution, so a
    changed parameter list is a *modification* of the one function named
    ``func.name``, the same entity, not a different overload. Folding
    param types into an extern-C function's identity would give the old
    and new declarations different primary ids on a parameter-type change
    and break the existing name-based extern-C match
    ``diff_symbols._diff_functions`` relies on (Codex review). A
    non-extern-C function with no real mangling (e.g. a DWARF-only
    snapshot) can genuinely be overloaded, so param types still
    disambiguate there -- along with ``is_const``/``is_volatile``/
    ``ref_qualifier``, which alone distinguish legal overloads with
    otherwise identical names and parameter types (``void f()`` vs.
    ``void f() const``, ``void f() &`` vs. ``void f() &&``; Codex review).

    Per-parameter canonicalization delegates to ``model.signature_
    normalization.canonicalize_function_signature_param_type`` -- the same
    primitive ``entity_id_for_function`` uses (ADR-063 Phase 2's
    "finding_identity.py algorithm migration") -- rather than
    ``canonicalize_type_name`` alone, which keeps a top-level by-value
    cv-qualifier distinguishing where the shared primitive correctly drops
    it (``void f(int)``/``void f(const int)`` are the same function). A
    *pointee* cv-qualifier still distinguishes either way.
    """
    param_types = (
        ()
        if func.is_extern_c
        else (
            *(canonicalize_function_signature_param_type(p.type) for p in func.params),
            f"const:{func.is_const}",
            f"volatile:{func.is_volatile}",
            f"ref:{func.ref_qualifier}",
            # variadic (...) is not itself a Param, so two overloads with
            # identical fixed parameters (void f(int) vs. void f(int, ...))
            # would otherwise get the same NORMALIZED-tier signature (Codex
            # review). bool | None: distinguishes producers that don't know
            # (None) from a confirmed non-variadic (False).
            f"variadic:{func.is_variadic}",
        )
    )
    # qualified_name=func.name: resolve_symbol_identity itself prefers
    # func.mangled over this whenever mangled is present but not a
    # verified mangling (the extern "C"/C-linkage case), so a namespaced
    # extern "C" function's identity is already tier-stable without
    # special-casing is_extern_c here.
    return resolve_symbol_identity(
        mangled=func.mangled,
        name=func.name,
        qualified_name=func.name,
        kind="function",
        param_types=param_types,
        source_location=RootRelativePath.from_project_layout(func.source_location),
    )


def resolve_variable_identity(var: Variable) -> FindingIdentity:
    """Canonical identity for a :class:`~abicheck.model.Variable`."""
    return resolve_symbol_identity(
        mangled=var.mangled,
        name=var.name,
        qualified_name=var.name,
        kind="variable",
        source_location=RootRelativePath.from_project_layout(var.source_location),
    )


@dataclass(frozen=True)
class IdentityMatch(Generic[_T]):
    """One old/new counterpart resolved by :class:`SymbolIdentityIndex`."""

    #: The matched entry's own key in the map the index was built from --
    #: what ``diff_symbols.py`` reports as the finding's ``symbol``.
    key: str
    declaration: _T
    identity: FindingIdentity
    #: ``"key"`` for an exact-key hit, otherwise the alias string that
    #: resolved it -- so a caller can record (or assert on) *how* specific
    #: the join that produced a finding actually was.
    matched_on: str


class SymbolIdentityIndex(Mapping[str, _T]):
    """An old/new matching index over resolved :class:`FindingIdentity` values.

    The flat-symbol counterpart of ``diff_helpers.TypeMap`` (ADR-045), and
    the piece :func:`resolve_function_identity`/:func:`resolve_variable_identity`
    were missing: those resolve *one* declaration's identity and have no
    notion of "this identity is ambiguous within its own snapshot, so do not
    join on it". That notion is what a matching engine actually needs, and
    why ``diff_symbols.py`` had to hand-roll a name multimap plus a
    ``len(candidates) == 1`` check to make its ``extern "C"`` fallback safe.

    It is a ``Mapping`` over the entries' own keys, exactly like ``TypeMap``,
    so every existing ``key in map`` / ``map.values()`` / ``map.items()``
    loop keeps working unchanged and iteration still visits each declaration
    once.

    **Aliases are deliberately not resolved by ``__getitem__``**, which is
    where this differs from ``TypeMap``. A type's bare-name alias is a
    schema-evolution accident worth healing silently; a *symbol*'s bare-name
    alias is not: two mangled names that differ are two different exports,
    and joining them by display name would hide a genuine removal behind a
    "modified" finding. An alias join is therefore always explicit, always
    ambiguity-checked, and always the caller's decision --
    :meth:`unique_alias_match`.
    """

    def __init__(
        self,
        entries: Mapping[str, _T],
        resolve: Callable[[_T], FindingIdentity],
    ) -> None:
        self._entries: dict[str, IdentityMatch[_T]] = {}
        self._by_alias: dict[str, list[IdentityMatch[_T]]] = {}
        for key, declaration in entries.items():
            identity = resolve(declaration)
            entry = IdentityMatch(
                key=key,
                declaration=declaration,
                identity=identity,
                matched_on="key",
            )
            self._entries[key] = entry
            for alias in identity.aliases:
                self._by_alias.setdefault(alias, []).append(entry)

    @classmethod
    def for_functions(
        cls, functions: Mapping[str, Function]
    ) -> SymbolIdentityIndex[Function]:
        return SymbolIdentityIndex(functions, resolve_function_identity)

    @classmethod
    def for_variables(
        cls, variables: Mapping[str, Variable]
    ) -> SymbolIdentityIndex[Variable]:
        return SymbolIdentityIndex(variables, resolve_variable_identity)

    # -- Mapping interface (over the entries' own keys) ---------------------

    def __getitem__(self, key: str) -> _T:
        return self._entries[key].declaration

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    # -- identity access ---------------------------------------------------

    def entry(self, key: str) -> IdentityMatch[_T] | None:
        """The full match record for *key*, or ``None``."""
        return self._entries.get(key)

    def alias_candidates(
        self,
        alias: str,
        *,
        where: Callable[[_T], bool] | None = None,
    ) -> list[IdentityMatch[_T]]:
        """Every entry carrying *alias* (optionally filtered by *where*).

        Exposed alongside :meth:`unique_alias_match` so a caller can tell
        "no candidate" from "several candidates" -- the two cases that
        method deliberately collapses to one answer.
        """
        return [
            entry
            for entry in self._by_alias.get(alias, ())
            if where is None or where(entry.declaration)
        ]

    def unique_alias_match(
        self,
        alias: str,
        *,
        where: Callable[[_T], bool] | None = None,
    ) -> IdentityMatch[_T] | None:
        """The single entry carrying *alias*, or ``None`` if not exactly one.

        Zero candidates and two-or-more candidates both answer ``None``, on
        purpose: "nothing to join to" and "cannot tell which to join to" are
        equally unsafe to guess at, and guessing is what would turn an
        overload set or a template instantiation family into a wrong pairing.
        This is the ambiguity-safe fallback tier ADR-045 established for
        types and ADR-048 for L5 source-graph nodes, applied to flat symbols.

        *where* narrows the candidate set before counting, for a caller whose
        fallback is only legitimate for a subset of declarations (the
        ``extern "C"`` rule in ``diff_symbols._match_old_function``).
        """
        candidates = self.alias_candidates(alias, where=where)
        if len(candidates) != 1:
            return None
        return replace(candidates[0], matched_on=alias)

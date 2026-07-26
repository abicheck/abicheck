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

"""Canonical identity for flat (L0-L2) ABI findings (ADR-049 Phase 2).

Preference order (most to least specific):

1. **canonical** -- a verified mangled symbol name (Itanium ``_Z...`` or
   MSVC ``?...``), the same signal ``diff_symbols.py`` already keys its
   old/new function and variable maps by. Never a bare name that merely
   rode in the mangled field (``extern "C"`` producers report
   ``mangled == name`` deliberately).
2. **normalized** -- a normalized qualified-name + kind + parameter-type
   signature, for declarations with no real mangling (C-linkage symbols,
   or a flat finding whose only name-like field is a type name rather than
   a mangled symbol).
3. **reduced** -- a source-relative identity (file + name) alias, promoted
   to a low-confidence synthetic ``sha256`` primary id only when nothing
   more specific is available at all.

This module generalizes, rather than replaces, the several hand-rolled
matching fallbacks already scattered through ``diff_symbols.py`` (the
mangled-primary + name-based extern-C fallback in ``_diff_functions``,
``_synthetic_ctor_scope``'s key parsing) into one reusable, documented,
independently-tested primitive -- following the same "most specific
available identity, ambiguity-safe fallback" principle ADR-045 already
established for flat type matching (``diff_helpers.TypeMap``) and ADR-048
established for L5 source-graph nodes
(``buildsource/entity_identity.py``). This is the L0-L2 flat-finding
analogue of those two, not a third independent implementation of the
underlying principle -- and deliberately does not import
``buildsource.entity_identity`` (the optional L3-L5 layer must depend on
this core package, never the other way around).

**Not yet wired into any live comparison path.** ``diff_symbols.py``'s
old/new function and variable matching and ``diff_filtering.py``'s
``_deduplicate_cross_detector`` cross-detector dedup key are unchanged by
this module -- see ``docs/contribute/plans/public-contract-default.md``'s
Phase 2 section for the remaining wiring and fact-conservation-gate work.
This module is additive only: existing detection behavior is unchanged
until a follow-up change opts a call site in.

NEVER invents a fact a producer did not supply: a tier is only claimed
when the corresponding input field is actually present.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import Function, Variable

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


def is_real_mangled_name(mangled_name: str | None, plain_name: str | None) -> bool:
    """Whether *mangled_name* is a genuine mangling, not a bare name that
    merely rode in the "mangled" field (``extern "C"``/C-linkage producers
    report ``mangled_name == name`` deliberately -- mirrors
    ``buildsource.entity_identity.is_real_mangled_name``'s identical check,
    duplicated rather than imported per this module's dependency-direction
    note above).
    """
    return bool(mangled_name) and mangled_name != plain_name


#: Structural (no external tool) check that *mangled_name* has the shape of
#: an Itanium mangled name: ``_Z`` followed by the restricted character set
#: Itanium mangling actually uses (matches ``demangle._MANGLED_TOKEN_RE``'s
#: character class, anchored to the whole string here rather than scanning
#: free text for embedded tokens).
_ITANIUM_MANGLED_RE = re.compile(r"\A_Z[A-Za-z0-9_$]+\Z")


def normalize_mangled_name(
    mangled_name: str | None, plain_name: str | None
) -> str | None:
    """Return *mangled_name* if it is a real, verifiable mangled name, else
    ``None`` -- never a guess.

    Deliberately does NOT call :func:`abicheck.demangle.demangle`: that
    function needs the optional ``cxxfilt`` package or a ``c++filt``
    binary, and this identity is meant to be deterministic and
    host-independent (the same input snapshot must resolve to the same
    identity for reconciliation/replay regardless of which machine runs
    the comparison) -- a mangled name that only degrades to the NORMALIZED
    tier on a host with no demangler installed would silently change
    identity across CI runners or between a contributor's laptop and CI.
    Itanium ``_Z...`` and MSVC ``?...`` manglings are therefore both
    accepted on their prefix + character-set convention alone
    (best-effort, matches how the rest of the codebase already treats
    MSVC-mangled names it cannot independently demangle).
    """
    if not is_real_mangled_name(mangled_name, plain_name):
        return None
    assert mangled_name is not None  # for type-checkers; guarded above
    if mangled_name.startswith("_Z"):
        return mangled_name if _ITANIUM_MANGLED_RE.match(mangled_name) else None
    if mangled_name.startswith("?"):
        return mangled_name
    return None


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


def source_relative_identity(file: str, name: str) -> str:
    """File + name -- an alias, never a primary key: two distinct entities
    can legitimately share this pair (an ODR-violating build, or two
    findings on the same line), so it is not trusted alone as a canonical
    id.
    """
    return f"{file or ''}\x1f{name or ''}"


def resolve_symbol_identity(
    *,
    mangled: str | None = None,
    name: str | None = None,
    qualified_name: str | None = None,
    kind: str = "",
    param_types: tuple[str, ...] = (),
    source_location: str = "",
) -> FindingIdentity:
    """Resolve the canonical identity for one function/variable declaration
    from whatever facts a producer actually supplied. Never fabricates a
    fact: a tier is claimed only when its corresponding input is present.
    """
    qn = qualified_name or name or ""
    sig = normalized_signature(qn, kind, param_types)
    rel = source_relative_identity(source_location, name or qn)

    aliases: list[str] = []
    real_mangled = normalize_mangled_name(mangled, name)
    if real_mangled:
        aliases.append(f"mangled:{real_mangled}")
    if name:
        aliases.append(f"name:{name}")
    if qn:
        aliases.append(f"qualified:{qn}")
    aliases.append(sig)
    if source_location:
        aliases.append(f"relsrc:{rel}")

    if real_mangled:
        primary = f"mangled:{real_mangled}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    if qn:
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
    disambiguate there.
    """
    param_types = () if func.is_extern_c else tuple(p.type for p in func.params)
    return resolve_symbol_identity(
        mangled=func.mangled,
        name=func.name,
        qualified_name=func.name,
        kind="function",
        param_types=param_types,
        source_location=func.source_location or "",
    )


def resolve_variable_identity(var: Variable) -> FindingIdentity:
    """Canonical identity for a :class:`~abicheck.model.Variable`."""
    return resolve_symbol_identity(
        mangled=var.mangled,
        name=var.name,
        qualified_name=var.name,
        kind="variable",
        source_location=var.source_location or "",
    )


def resolve_change_identity(change: Change) -> FindingIdentity:
    """Tiered identity for an already-emitted flat finding
    (:class:`~abicheck.checker_types.Change`).

    ``change.symbol`` doubles as "mangled name or type name" (see
    ``Change``'s docstring) -- ``change.qualified_name`` stands in for the
    "plain name" :func:`normalize_mangled_name` needs to tell a real
    mangling apart from a bare symbol that merely rode in ``symbol``
    (matching how ``extern "C"`` producers report the two as equal). A
    type-level change's ``symbol`` is a type name, not a mangling, so it
    never passes that check and correctly degrades to the NORMALIZED tier.

    Unlike :func:`resolve_function_identity`/:func:`resolve_variable_identity`
    (which identify one *entity*, so a bare mangled name is already
    unambiguous), this identifies one *finding* -- two distinct findings
    routinely share a symbol (e.g. ``FUNC_RETURN_CHANGED`` and
    ``FUNC_PARAMS_CHANGED`` both on ``foo``). The change's kind/old/new
    value/description are therefore folded into ``primary_id`` at every
    tier, including CANONICAL, mirroring the discriminator
    ``reporter_markdown._finding_id`` already established as the minimal
    set that disambiguates one finding from another on the same symbol
    (Codex review: a bare ``mangled:<symbol>`` canonical id would collapse
    unrelated findings and violate this module's stated dedup-key
    contract).
    """
    kind_value = str(getattr(change.kind, "value", change.kind))
    qn = change.qualified_name or change.symbol or ""
    sig = normalized_signature(
        qn,
        kind_value,
        (change.old_value or "", change.new_value or "", change.description or ""),
    )
    rel = source_relative_identity(change.source_location or "", change.symbol or "")

    aliases: list[str] = []
    real_mangled = normalize_mangled_name(change.symbol, change.qualified_name)
    if real_mangled:
        aliases.append(f"mangled:{real_mangled}")
    if change.symbol:
        aliases.append(f"symbol:{change.symbol}")
    if change.qualified_name:
        aliases.append(f"qualified:{change.qualified_name}")
    aliases.append(sig)
    if change.source_location:
        aliases.append(f"relsrc:{rel}")

    if real_mangled:
        primary = f"mangled:{real_mangled}\x1f{sig}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    if qn:
        return FindingIdentity(sig, IDENTITY_TIER_NORMALIZED, tuple(aliases))

    basis = "\x1f".join(
        str(x)
        for x in (
            change.symbol,
            kind_value,
            change.source_location,
            change.old_value,
            change.new_value,
            change.description,
        )
        if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return FindingIdentity(synthetic, IDENTITY_TIER_REDUCED, tuple(aliases))

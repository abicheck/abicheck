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
#: Itanium mangling actually uses, plus GCC's dotted clone-suffix convention
#: (``.isra.0``, ``.constprop.0``, ``.cold``, ...) -- matches
#: ``demangle._MANGLED_TOKEN_RE``'s character class and repeated dotted
#: suffix exactly (CodeRabbit review), anchored to the whole string here
#: rather than scanning free text for embedded tokens.
_ITANIUM_MANGLED_RE = re.compile(r"\A_Z[A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*\Z")

#: The fixed, enumerable Itanium ABI ``<operator-name>`` two-letter codes
#: (``nw`` = ``operator new``, ``pl`` = ``operator+``, ``cv`` = a conversion
#: operator, ...) -- used only to recognize a *global* operator overload
#: (``_Znwm``) as a real encoding start, not a full operator-name grammar.
_ITANIUM_OPERATOR_CODES = frozenset(
    {
        "nw", "na", "dl", "da", "ps", "ng", "ad", "de", "co", "pl", "mi",
        "ml", "dv", "rm", "an", "or", "eo", "aS", "pL", "mI", "mL", "dV",
        "rM", "aN", "oR", "eO", "ls", "rs", "lS", "rS", "eq", "ne", "lt",
        "gt", "le", "ge", "ss", "nt", "aa", "oo", "cm", "pm", "pt", "cl",
        "ix", "qu", "cv", "li",
    }
)  # fmt: skip


_SOURCE_NAME_LENGTH_RE = re.compile(r"\A[0-9]+")


def _valid_source_name(rest: str) -> bool:
    """Whether a leading ``<source-name>`` production (``<number>``
    followed by exactly that many bytes, e.g. ``3foo``) has a declared
    length *rest* actually has room for.

    Catches ``_Z9abc`` -- passes a bare "starts with a digit" check, but
    the ``9`` claims nine following bytes and only three (``abc``) are
    there, so it is not a valid encoding (Codex review, round 2 on this
    exact production). Only checks that at least the declared number of
    bytes exist, not that they form a further-valid identifier/that
    whatever trails them is valid too -- a real ``<source-name>`` may be
    followed by more encoding (template args, parameter types, ...) this
    function does not parse.
    """
    m = _SOURCE_NAME_LENGTH_RE.match(rest)
    assert m is not None  # only called when rest[0].isdigit()
    declared_len = int(m.group())
    identifier = rest[m.end() :]
    return len(identifier) >= declared_len


def _looks_like_itanium_encoding(rest: str) -> bool:
    """Whether *rest* (``mangled_name`` with the ``_Z`` prefix and any
    clone suffix already stripped) plausibly starts a real Itanium
    ``<encoding>`` production, not just any ``_Z``-prefixed string.

    A best-effort structural check on the *first* production only --
    NOT a full Itanium grammar parser (that is exactly what an external
    demangler exists to do, and :func:`normalize_mangled_name` deliberately
    avoids calling one; see its docstring). Rejects a `_Z`-prefixed token
    like ``_Zebra`` (no real production starts with a bare letter) or
    ``_Z9abc`` (claims a 9-byte name, has 3) that pass a coarser check but
    are not real encodings (Codex review, two rounds). A production this
    function doesn't recognize or fully validate (an obscure vendor
    extension, a length-invalid ``<source-name>`` *nested* inside an
    ``N...E`` production, template-args internals, ...) only degrades to
    the NORMALIZED tier -- the same ambiguity-safe-fallback bias as
    everywhere else in this module, and the accepted boundary of this
    heuristic: it validates the top-level production's shape and its own
    declared length, not every length recursively nested inside it. A
    fully grammar-correct implementation is out of scope for this
    additive, not-yet-wired primitive -- that is exactly the job an
    external demangler exists to do, deliberately not called here (see
    :func:`normalize_mangled_name`'s docstring).
    """
    if not rest:
        return False
    if rest[0].isdigit():  # <source-name>: digit-prefixed length
        return _valid_source_name(rest)
    if rest[0] in "NZ":  # <nested-name> / <local-name>
        return True
    if rest[0] == "L" and len(rest) > 1 and rest[1].isdigit():
        # GCC internal-linkage prefix (_ZL7g_count) + <source-name>
        return _valid_source_name(rest[1:])
    if rest[0] == "T" and len(rest) > 1 and rest[1] in "VITSFWJ":
        # <special-name>: vtable/typeinfo/typename/VTT/...
        return True
    if rest[0] == "G" and len(rest) > 1 and rest[1] in "VR":
        # guard variable / reference temporary
        return True
    if rest[0] == "S" and len(rest) > 1 and (rest[1].isalnum() or rest[1] == "_"):
        # substitution-abbreviated std:: name
        return True
    return rest[:2] in _ITANIUM_OPERATOR_CODES


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
    Itanium ``_Z...`` names are validated structurally
    (:func:`_looks_like_itanium_encoding`) rather than merely on the ``_Z``
    prefix and character set alone. MSVC ``?...`` manglings have no such
    check available (no demangler in this codebase to cross-check against
    at all) and are accepted on the prefix convention alone (best-effort,
    matches how the rest of the codebase already treats MSVC-mangled names
    it cannot independently demangle).
    """
    if not is_real_mangled_name(mangled_name, plain_name):
        return None
    assert mangled_name is not None  # for type-checkers; guarded above
    if mangled_name.startswith("_Z"):
        if not _ITANIUM_MANGLED_RE.match(mangled_name):
            return None
        rest = mangled_name[2:].split(".", 1)[0]  # drop clone suffix
        return mangled_name if _looks_like_itanium_encoding(rest) else None
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
    disambiguate there -- along with ``is_const``/``is_volatile``/
    ``ref_qualifier``, which alone distinguish legal overloads with
    otherwise identical names and parameter types (``void f()`` vs.
    ``void f() const``, ``void f() &`` vs. ``void f() &&``; Codex review).
    """
    param_types = (
        ()
        if func.is_extern_c
        else (
            *(p.type for p in func.params),
            f"const:{func.is_const}",
            f"volatile:{func.is_volatile}",
            f"ref:{func.ref_qualifier}",
        )
    )
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


#: ChangeKind slug *prefixes* that are unambiguously "about one
#: function/variable symbol" (see ``checker_policy.py``'s
#: ``func_*``/``var_*``/``ifunc_*``/``symbol_*`` naming convention). The
#: ``symbol_*`` family (``symbol_binding_changed``, ``symbol_type_changed``,
#: ``symbol_size_changed``, ...) is the ELF-level symbol-table diff --
#: equally unambiguous, added after Codex review flagged it missing.
_SYMBOL_LEVEL_KIND_PREFIXES = (
    "func_",
    "var_",
    "ifunc_",
    "symbol_",
    # A function's own parameters/return value are never independently
    # type-level -- ``param_*``/``return_*`` always describe one function's
    # signature (verified against every current param_*/return_* slug).
    "param_",
    "return_",
)

#: Individually-named kinds that don't share one of the prefixes above but
#: are still unambiguously about one function/variable symbol (Codex
#: review, four rounds: ``VIRTUAL_METHOD_ADDED``/``CALLING_CONVENTION_CHANGED``/
#: ``METHOD_ACCESS_CHANGED``/``DEFAULT_ARGUMENT_CHANGED``/the two
#: function-template ``TEMPLATE_*_TYPE_CHANGED`` kinds were flagged as
#: missing in turn).
#:
#: This allowlist-of-individual-kinds approach has a structural limit: it
#: can only ever be as complete as whatever review has spot-checked so far
#: against ~395 `ChangeKind` values, and each round has found another real
#: gap in the same way. That is an accepted, deliberate property of this
#: primitive, not an oversight to keep closing case-by-case -- the module's
#: own contract (see :func:`_is_symbol_level_kind`'s docstring) is that
#: missing an entry here only causes an unnecessary degrade to NORMALIZED,
#: never a wrong CANONICAL promotion, so incompleteness is safe by
#: construction. Extend this set when a *concrete* wiring change (Phase 2's
#: remaining work: actually consuming this identity in
#: ``diff_symbols.py``/``diff_filtering.py``) needs a specific kind
#: reclassified, rather than continuing to enumerate hypothetical gaps
#: against an unwired primitive.
_SYMBOL_LEVEL_KIND_SLUGS = frozenset(
    {
        "virtual_method_added",
        "calling_convention_changed",
        "value_abi_trait_changed",
        "hidden_friend_added",
        "hidden_friend_removed",
        "method_access_changed",
        "default_argument_changed",
        "template_param_type_changed",
        "template_return_type_changed",
    }
)

#: ``symbol_*`` kinds that are the one exception to "``symbol_*`` is always
#: entity-bearing" -- their ``Change.symbol`` is a version-node/requirement
#: label (``diff_versioning.py``/``diff_platform_elf_symbols.py``: e.g.
#: ``symbol=node`` where ``node`` is a string like ``"GLIBC_2.17"``) or a
#: synthetic batch identifier (``diff_symbols.py``'s ``_emit_batch_rename``:
#: ``symbol=f"batch_rename:{prefix}*"``), never a real exported function or
#: variable name (Codex review: a version label or batch id that happens to
#: resemble a mangling must not be promoted to CANONICAL and aliased
#: alongside an actual function's mangled name). Verified per-kind against
#: the actual ``make_change(..., symbol=...)`` call site, not inferred from
#: the slug alone -- ``symbol_moved_version_node``/
#: ``symbol_version_alias_changed`` also live in ``diff_versioning.py``/
#: ``diff_platform.py`` but pass a real exported symbol name and are
#: correctly NOT in this set.
_NON_ENTITY_SYMBOL_KIND_SLUGS = frozenset(
    {
        "symbol_version_defined_removed",
        "symbol_version_defined_added",
        "symbol_version_required_added",
        "symbol_version_required_added_compat",
        "symbol_version_required_removed",
        "symbol_version_node_removed",
        "symbol_renamed_batch",
    }
)


#: ``resolve_change_identity`` only attempts to interpret ``change.symbol``
#: as a mangled name when its kind matches one of the two sets above (and
#: is not in :data:`_NON_ENTITY_SYMBOL_KIND_SLUGS`) -- a type-level kind's
#: ``symbol`` is a type name (e.g. a type named ``_Zebra`` structurally
#: resembles an Itanium mangling but is not one), and
#: ``change.qualified_name`` -- the signal that would normally catch this
#: via ``normalize_mangled_name``'s mangled-vs-plain-name check -- is
#: documented as unset for exactly this case (``Change.qualified_name``'s
#: docstring: "None when no matching Function record was found (e.g.
#: type-level changes)"), so it cannot be relied on alone (Codex review).
#: Missing a real symbol-level kind here only means an unnecessary degrade
#: to the NORMALIZED tier, never a wrong CANONICAL promotion -- the same
#: ambiguity-safe-fallback bias as everywhere else in this module.
def _is_symbol_level_kind(kind_value: str) -> bool:
    if kind_value in _NON_ENTITY_SYMBOL_KIND_SLUGS:
        return False
    return kind_value.startswith(_SYMBOL_LEVEL_KIND_PREFIXES) or (
        kind_value in _SYMBOL_LEVEL_KIND_SLUGS
    )


#: Change kinds ``diff_filtering`` already treats as one logical event
#: reported by two different detectors -- mirrored here (not imported, so
#: this leaf module stays independent of the diff layer; future wiring work
#: has ``diff_filtering`` depend on this module, not the reverse). Keep in
#: sync with the two mappings this generalizes:
#: ``_deduplicate_cross_detector``'s local ``_DEDUP_CATEGORIES``
#: (rich-vs-L0 function/variable add/remove, symbol-version-node pairs) and
#: module-level ``_DWARF_TO_AST_EQUIV`` (DWARF ``struct_*``/AST ``type_*``
#: pairs for the same type -- e.g. ``STRUCT_SIZE_CHANGED``/
#: ``TYPE_SIZE_CHANGED``, Codex review).
_EQUIVALENT_CHANGE_CATEGORIES = {
    "func_removed": "func_removal",
    "func_removed_elf_only": "func_removal",
    "func_added": "func_addition",
    "var_removed": "var_removal",
    "var_added": "var_addition",
    "symbol_version_node_removed": "version_def_removal",
    "symbol_version_defined_removed": "version_def_removal",
    "struct_size_changed": "type_size_change",
    "type_size_changed": "type_size_change",
    "struct_alignment_changed": "type_alignment_change",
    "type_alignment_changed": "type_alignment_change",
    "struct_field_offset_changed": "type_field_offset_change",
    "type_field_offset_changed": "type_field_offset_change",
    "struct_field_removed": "type_field_removal",
    "type_field_removed": "type_field_removal",
    "struct_field_type_changed": "type_field_type_change",
    "type_field_type_changed": "type_field_type_change",
}


def _change_discriminator(change: Change, kind_value: str) -> str:
    """The part of a finding's identity that tells it apart from another
    finding sharing the same symbol.

    Most kinds need the full ``(kind, old_value, new_value, description)``
    tuple -- e.g. ``FUNC_RETURN_CHANGED`` and ``FUNC_PARAMS_CHANGED`` on the
    same function must not collide (Codex review). But a handful of kind
    *pairs* in :data:`_EQUIVALENT_CHANGE_CATEGORIES` are one logical event
    under two different detectors' own wording (e.g. ``FUNC_REMOVED`` vs.
    ``FUNC_REMOVED_ELF_ONLY``) -- those must collide on symbol + category
    alone, ignoring the detector-specific kind/old/new/description text, or
    this identity could never actually perform the rich/L0 reconciliation
    it is meant to drive once wired in (Codex review).
    """
    category = _EQUIVALENT_CHANGE_CATEGORIES.get(kind_value)
    if category is not None:
        return f"category:{category}"
    return "\x1f".join(
        (
            kind_value,
            change.old_value or "",
            change.new_value or "",
            change.description or "",
        )
    )


def resolve_change_identity(change: Change) -> FindingIdentity:
    """Tiered identity for an already-emitted flat finding
    (:class:`~abicheck.checker_types.Change`).

    ``change.symbol`` doubles as "mangled name or type name" (see
    ``Change``'s docstring); mangled-name interpretation is therefore only
    attempted when :func:`_is_symbol_level_kind` recognizes the kind, never
    for a type-level change whose ``symbol`` merely happens to look like a
    mangling (Codex review).

    Unlike :func:`resolve_function_identity`/:func:`resolve_variable_identity`
    (which identify one *entity*, so a bare mangled name is already
    unambiguous), this identifies one *finding* -- two distinct findings
    routinely share a symbol. :func:`_change_discriminator` is folded into
    ``primary_id`` at every tier, including CANONICAL, mirroring the
    discriminator ``reporter_markdown._finding_id`` already established as
    the minimal set that disambiguates one finding from another on the same
    symbol (Codex review: a bare ``mangled:<symbol>`` canonical id would
    collapse unrelated findings and violate this module's stated dedup-key
    contract).
    """
    kind_value = str(getattr(change.kind, "value", change.kind))
    qn = change.qualified_name or change.symbol or ""
    discriminator = _change_discriminator(change, kind_value)
    sig = f"sig:{qn}\x1f{discriminator}"
    rel = source_relative_identity(change.source_location or "", change.symbol or "")

    real_mangled = None
    if _is_symbol_level_kind(kind_value):
        real_mangled = normalize_mangled_name(change.symbol, change.qualified_name)

    aliases: list[str] = []
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
        primary = f"mangled:{real_mangled}\x1f{discriminator}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    if qn:
        return FindingIdentity(sig, IDENTITY_TIER_NORMALIZED, tuple(aliases))

    basis = "\x1f".join(
        str(x) for x in (change.symbol, discriminator, change.source_location) if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return FindingIdentity(synthetic, IDENTITY_TIER_REDUCED, tuple(aliases))

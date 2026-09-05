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
established for L5 source-graph nodes (``buildsource/entity_identity.py``).
This is the L0-L2 flat-finding analogue of those two, not a third
independent implementation of the
underlying principle -- and deliberately does not import
``buildsource.entity_identity`` (the optional L3-L5 layer must depend on
this core package, never the other way around).

**Wired.** Every flat old/new join now goes through this module:

- ``diff_filtering.py``'s ``_deduplicate_cross_detector`` uses
  :func:`resolve_change_identity` as its cross-detector dedup key,
  replacing a hand-rolled ``(change_category, symbol)`` tuple.
- ``diff_symbols.py``'s function and variable *matching*
  (``_diff_functions``/``_match_old_function``/``_diff_variables``) joins
  through :class:`SymbolIdentityIndex`, replacing a second hand-rolled name
  multimap plus an inline ``len(candidates) == 1`` count.

That second wiring waited for the piece this module was missing rather than
for the refactor to get smaller: :func:`resolve_function_identity` resolves
*one* declaration's identity and has no notion of "this identity is
ambiguous within its own snapshot, so do not join on it" -- the whole of
what made the ``extern "C"`` fallback safe. :class:`SymbolIdentityIndex`
supplies it, so the matching engine states the rule instead of
re-implementing it. Matching behavior is deliberately unchanged -- the
hand-tuned join logic (elf-only-mode, unconfirmed-parameter/LLP64
threading, virtual-method-addition, inline transitions, hidden friends) is
untouched, pinned by the golden/FP-rate/tier-accuracy/detector-oracle suites.

NEVER invents a fact a producer did not supply: a tier is only claimed
when the corresponding input field is actually present.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, TypeVar

from .finding_identity_atomic import canonicalize_atomic_slot
from .model.signature_normalization import canonicalize_function_signature_param_type
from .name_classification import canonicalize_type_name

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import Function, Variable

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


def is_real_mangled_name(mangled_name: str | None, plain_name: str | None) -> bool:
    """Whether *mangled_name* is a genuine mangling, not a bare name that
    merely rode in the "mangled" field (``extern "C"``/C-linkage producers
    report ``mangled_name == name`` deliberately).

    ``mangled_name == plain_name`` is usually that C-linkage signal, but
    some symbols-only fallback dumpers (``dumper_elf_fallback.py``'s ELF
    export scan, ``dumper.py``'s PE-only export path) populate *both*
    fields with the same raw exported symbol string even for a genuine
    C++/MSVC-mangled export (e.g. ``_Z3foov``/``?foo@@YAHXZ``), since no
    separate demangled name is available without debug info -- they still
    compute ``is_extern_c`` correctly (checking the same ``_Z``/``?``
    prefix), but that fact never reaches this string-only check (Codex
    review). Equality is therefore only trusted as C-linkage evidence when
    the value doesn't independently look like a real platform mangling;
    :func:`_looks_structurally_mangled` runs the same structural check
    :func:`normalize_mangled_name` uses.
    """
    if not mangled_name:
        return False
    return mangled_name != plain_name or _looks_structurally_mangled(mangled_name)


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

#: A real Itanium ``<source-name>`` length prefix is never remotely close to
#: this many digits (it would claim a billion-plus-byte identifier). Bounds
#: the digit run *before* ``int()`` sees it -- Python raises ``ValueError``
#: converting a digit string longer than ``sys.get_int_max_str_digits()``
#: (~4300 by default since 3.11/3.10.7+, CVE-2020-10735 mitigation), and
#: this module's snapshot data (an ELF/DWARF/PE symbol table) is untrusted
#: input a crafted binary could shape (CodeRabbit review) -- an uncaught
#: ValueError here must not be how that gets rejected.
_MAX_SOURCE_NAME_LENGTH_DIGITS = 9


def _source_name_end(text: str) -> int | None:
    """If *text* starts with a valid ``<source-name>`` production
    (``<number>`` followed by exactly that many bytes, e.g. ``3foo``),
    return the index just past it; otherwise ``None``.

    Unlike :func:`_valid_source_name` (a plain bool), callers that need to
    know *where* the component ends -- e.g. to search for whatever
    terminator follows it, rather than just accepting/rejecting the whole
    string -- use this instead. Applies the same digit-run bound and
    zero-length rejection :func:`_valid_source_name` documents.
    """
    if not text or not text[0].isdigit():
        return None
    m = _SOURCE_NAME_LENGTH_RE.match(text)
    assert m is not None  # guarded by the isdigit() check above
    digits = m.group()
    if len(digits) > _MAX_SOURCE_NAME_LENGTH_DIGITS:
        return None
    declared_len = int(digits)
    if declared_len == 0:
        return None
    end = m.end() + declared_len
    return end if len(text) >= end else None


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

    Also rejects a declared length of zero (``_Z0``): a real identifier
    can never be zero bytes long, so ``declared_len == 0`` always
    "succeeds" against any following content without actually being a
    valid ``<source-name>`` (Codex review, round 3).
    """
    return _source_name_end(rest) is not None


def _operand_looks_valid(operand: str) -> bool:
    """Whether *operand* (the content directly after a two-letter
    ``<special-name>``/guard-variable prefix, e.g. ``"6Widget"`` in
    ``TV6Widget``) is not an obviously-invalid digit-prefixed
    ``<source-name>``.

    These operands can be an arbitrarily complex ``<type>`` or ``<name>``
    production -- fully validating them is exactly the unbounded
    full-grammar case this module deliberately doesn't attempt (see
    :func:`_looks_like_itanium_encoding`'s docstring). But when the
    operand happens to start with a digit, no other Itanium production
    starts that way, so it can ONLY be a ``<source-name>`` -- that
    narrower case IS fully checkable, and ``_ZTV0`` (Codex review, fresh
    evidence: the operand ``"0"`` is neither a valid type encoding nor a
    positive-length source name, but the earlier length-only check
    accepted it anyway) previously slipped through with no operand
    validation at all. A non-digit-prefixed operand (a builtin-type
    letter code, a nested-name, ...) is accepted outright -- the unbounded
    case, preserving this module's ambiguity-safe bias.
    """
    if not operand[0].isdigit():
        return True
    return _source_name_end(operand) is not None


def _consume_nested_prefix_components(rest: str) -> tuple[int, bool, bool] | None:
    """Walk the ``<prefix>`` components of an ``N``/``Z`` production, starting
    just past the leading ``N``/``Z`` byte.

    Returns ``(pos, consumed_any_component, pending_prefix_only)`` -- *pos*
    being the index of the first byte this walk could not consume -- or
    ``None`` when a digit-prefixed component is itself malformed (declared
    length 0, or claiming more bytes than exist), which invalidates the whole
    production, matching the single-component "_ZN0E"/"_ZN9abcE" fix (round 3).

    Consuming components at all exists to keep an ``E`` byte that belongs to a
    component's OWN spelling from being mistaken for the production's
    terminator -- e.g. "_ZN1E" is incomplete (the "1E" is a length-1
    <source-name> whose one-byte identifier IS "E", leaving no separate
    terminator), but a naive ``rest.find("E", 1)`` found that embedded byte and
    wrongly accepted it (Codex review, fresh evidence). A <nested-name>'s
    <prefix> can chain MULTIPLE consecutive source-name components (e.g.
    "N1A1BE" = namespace A, class B), so a single-component skip left a later
    component's own trailing 'E' exposed to the identical confusion: "_ZN1A1E"
    is incomplete (after consuming "1A", "1E" is a second length-1
    <source-name> whose identifier IS "E" -- the complete form is "_ZN1A1EE"),
    but a single skip still found that embedded byte (Codex review, fresh
    evidence, round 4). No other <nested-name>/<local-name> first-component
    production starts with a bare digit -- a digit-prefixed component can only
    be a <source-name>, the same reasoning as :func:`_operand_looks_valid`.
    """
    pos = 1
    consumed_any_component = False
    pending_prefix_only = False
    while pos < len(rest):
        if rest[pos] == "S" and pos + 1 < len(rest) and rest[pos + 1] in "tabdios":
            # A standard substitution ("St" = std:: prefix; "Sa" / "Sb" /
            # "Sd" / "Si" / "So" / "Ss" = the complete named substitutions,
            # e.g. std::allocator/std::string) is itself a <prefix>
            # component -- consuming just its two bytes without continuing
            # the loop left a trailing digit-prefixed <source-name> exposed
            # to the same embedded-terminator confusion the earlier fixes
            # addressed: "_ZNSt1E" and "_ZNSa1E" are both incomplete ("1E"
            # is a length-1 <source-name> whose identifier IS "E", leaving
            # no separate terminator) but a loop that only recognized "St"
            # literally -- or only digits -- never started skipping here for
            # the other five letters (Codex review, fresh evidence, round 7).
            #
            # Whichever letter follows "S", the production is still
            # <nested-name> ::= N <prefix> <unqualified-name> E, and
            # <substitution> (what ANY of these six letters spells) is never
            # itself a valid <unqualified-name> -- so even though
            # "Sa"/"Sb"/"Sd"/"Si"/"So"/"Ss" are complete, context-free
            # substitutions in their OWN right (see _substitution_looks_valid),
            # a <nested-name> still needs a real <unqualified-name> after
            # them, same as "St". "_ZNSaE" leaves `pos` pointing straight at
            # the 'E' right after "Sa", which the terminator search previously
            # accepted as though "Sa" alone had completed the required
            # trailing unqualified-name (Codex review, fresh evidence,
            # round 9; round 6 fixed only the "St" spelling of this same
            # gap). `pending_prefix_only` tracks that the most recent thing
            # consumed was a bare substitution with nothing completing it
            # yet, and is cleared as soon as a real component (a
            # <source-name>) follows it.
            pos += 2
            consumed_any_component = True
            pending_prefix_only = True
            continue
        if not rest[pos].isdigit():
            break
        component_end = _source_name_end(rest[pos:])
        if component_end is None:
            return None
        pos += component_end
        consumed_any_component = True
        pending_prefix_only = False
    return pos, consumed_any_component, pending_prefix_only


def _nested_or_local_name_looks_valid(rest: str) -> bool:
    """The ``N``/``Z`` branch of :func:`_looks_like_itanium_encoding`:
    ``<nested-name>`` (``N...E``) and ``<local-name>``
    (``Z<encoding>E<entity>``).

    Both productions terminate with a literal 'E' after at least one non-empty
    component -- a bare "N" or content with no terminator at all (e.g.
    "_ZNonsense", "_ZN") was previously accepted outright regardless of
    structure (Codex review). Does not validate that everything between N/Z and
    E is itself well-formed -- that is the unbounded full-grammar case this
    heuristic deliberately doesn't attempt (see
    :func:`_looks_like_itanium_encoding`'s docstring on its accepted boundary);
    a component shape this walk doesn't recognize (constructor/destructor
    names, template-args, ...) falls back to the original naive terminator
    scan.
    """
    walked = _consume_nested_prefix_components(rest)
    if walked is None:
        return False
    pos, consumed_any_component, pending_prefix_only = walked
    if pending_prefix_only:
        return False
    if consumed_any_component:
        e_index = rest.find("E", pos)
        terminator_ok = e_index >= pos
    else:
        e_index = rest.find("E", 1)
        terminator_ok = e_index > 1
    if not terminator_ok:
        return False
    if rest[0] == "Z":
        # Unlike <nested-name> (complete once its own terminator E is
        # found), a <local-name> is "Z <function encoding> E <entity
        # name> [<discriminator>]" -- the terminator MUST be followed
        # by a non-empty entity name. "_ZZ1fvE" has a terminator (the
        # trailing E) but nothing after it, so it previously passed
        # the shared N/Z check above despite being incomplete (Codex
        # review, fresh evidence). A merely non-empty suffix isn't
        # enough either: "_ZZ1fvE0" has one trailing byte, but "0" is
        # a zero-length <source-name> and not a valid entity name --
        # reuse _operand_looks_valid's same digit-prefixed check
        # (Codex review, fresh evidence, round 2).
        suffix = rest[e_index + 1 :]
        return bool(suffix) and _operand_looks_valid(suffix)
    return True


def _internal_linkage_name_looks_valid(rest: str) -> bool:
    """The ``L`` branch: GCC internal-linkage prefix (_ZL7g_count) +
    ``<source-name>``."""
    return len(rest) > 1 and rest[1].isdigit() and _valid_source_name(rest[1:])


def _special_name_looks_valid(rest: str) -> bool:
    """The ``T`` branch -- ``<special-name>``: vtable(V)/VTT(T)/typeinfo(I)/
    typeinfo-name(S)/thread-local-init(H)/thread-local-wrapper(W).

    Previously included unverified "F"/"J" (CodeRabbit review: no corresponding
    Itanium production found for either) -- dropped rather than guessed at,
    matching this module's ambiguity-safe bias (a real production this set is
    missing only degrades to NORMALIZED, never a wrong promotion the other
    way). ``len(rest) > 2``, not ``> 1``: every one of these productions
    requires an operand (a type or source-name) after the two-letter prefix --
    a bare "_ZTV" has no such operand and is not a complete encoding (Codex
    review). The operand itself is then checked by :func:`_operand_looks_valid`
    for the narrower digit-prefixed case (Codex review, fresh evidence:
    "_ZTV0").
    """
    return len(rest) > 2 and rest[1] in "VITSHW" and _operand_looks_valid(rest[2:])


def _guard_variable_looks_valid(rest: str) -> bool:
    """The ``G`` branch -- guard variable / reference temporary. Same "requires
    an operand after the two-letter prefix" reasoning as
    :func:`_special_name_looks_valid`; a bare "_ZGV" is not a complete encoding
    either."""
    return len(rest) > 2 and rest[1] in "VR" and _operand_looks_valid(rest[2:])


def _substitution_looks_valid(rest: str) -> bool:
    """The ``S`` branch -- substitution-abbreviated std:: name.

    "St" specifically abbreviates the std:: NAMESPACE PREFIX only, not a
    complete substitution by itself -- it must be followed by an
    unqualified-name (e.g. "St9terminate" for std::terminate), unlike the other
    single-letter abbreviations (Sa/Sb/Sd/Si/So/Ss, each a complete named
    substitution on its own) or numbered back-references (S_, S0_, ...). A bare
    "_ZSt" previously passed this check with nothing after it (Codex review,
    fresh evidence).
    """
    if len(rest) < 2 or not (rest[1].isalnum() or rest[1] == "_"):
        return False
    if rest[1] == "t":
        # A length-only check let "_ZSt0"/"_ZSt9abc" through: "0" is a
        # zero-length <source-name> and "9abc" is truncated (claims 9
        # bytes, has 3) -- neither is a valid unqualified-name after
        # the namespace prefix. Reuse _operand_looks_valid's same
        # digit-prefixed <source-name> check (Codex review, fresh
        # evidence, round 3).
        return len(rest) > 2 and _operand_looks_valid(rest[2:])
    if rest[1].isdigit() or (rest[1].isalpha() and rest[1].isupper()):
        # A numbered substitution (<seq-id> ::= [0-9A-Z]+, ALWAYS
        # followed by a literal terminating "_", e.g. "S0_", "SA_")
        # references an EARLIER substitution-table entry -- one only
        # populated by <name>/<type> productions already emitted
        # earlier in the SAME encoding. This function validates only
        # the FIRST production of a top-level <encoding>, where no
        # such entry can exist yet: a well-formed "S0_"/"SA_" here is
        # a context-free reference to nothing, never a valid first
        # production, regardless of whether its own terminator is
        # present (Codex review, fresh evidence, round 8; previously
        # only the missing-terminator shape "_ZS0"/"_ZSA" was
        # rejected, while the well-formed-but-context-free "_ZS0_"
        # still passed).
        return False
    # Only Sa/Sb/Sd/Si/So/Ss are real complete single-letter
    # abbreviations (allocator/basic_string/basic_iostream/
    # basic_istream/basic_ostream/string) that don't need any prior
    # context -- they denote fixed, well-known components, unlike a
    # numbered back-reference. Bare "S_" (back-reference-0) has the
    # exact same "no earlier entry can exist yet" problem as the
    # numbered case above and is rejected for the same reason (Codex
    # review, fresh evidence, round 8). Any other lowercase letter
    # (e.g. "_ZSx") is not a real Itanium substitution at all, but
    # previously matched this fallback outright regardless of which
    # letter followed "S" (Codex review, fresh evidence, round 2).
    return rest[1] in "abdios"


#: Leading-byte dispatch for the non-``<source-name>`` ``<encoding>``
#: productions :func:`_looks_like_itanium_encoding` recognizes. A handler owns
#: its production's *whole* condition, including the guard that used to sit in
#: the ``if`` -- returning ``False`` where the pre-table code fell through to
#: the ``<operator-name>`` tail is equivalent, since no Itanium operator code
#: begins with any of these six bytes (verified against
#: :data:`_ITANIUM_OPERATOR_CODES`: every code's first byte is lowercase, and
#: none of ``N``/``Z``/``L``/``T``/``G``/``S`` appears there).
_ENCODING_PRODUCTION_HANDLERS: dict[str, Callable[[str], bool]] = {
    "N": _nested_or_local_name_looks_valid,
    "Z": _nested_or_local_name_looks_valid,
    "L": _internal_linkage_name_looks_valid,
    "T": _special_name_looks_valid,
    "G": _guard_variable_looks_valid,
    "S": _substitution_looks_valid,
}


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
    handler = _ENCODING_PRODUCTION_HANDLERS.get(rest[0])
    if handler is not None:
        return handler(rest)
    # Two <operator-name> codes are not complete by themselves the way the
    # other 47 are: "cv" (conversion operator) requires a following
    # <type>, and "li" (C++11 literal operator) requires a following
    # <source-name> for its suffix identifier -- a bare "_Zcv"/"_Zli" is a
    # legal C export name, not a complete encoding, but previously matched
    # `rest[:2] in _ITANIUM_OPERATOR_CODES` outright with no operand check
    # at all, the same "requires an operand after the prefix" gap already
    # fixed for <special-name>/guard-variable above (Codex review, fresh
    # evidence). `_operand_looks_valid` catches the same digit-prefixed
    # invalid-<source-name> case for this operand too -- "_Zcv0"/"_Zli0"
    # pass the length check but "0" is not a valid operand for either
    # production (Codex review, fresh evidence, round 2).
    if rest[:2] in ("cv", "li"):
        return len(rest) > 2 and _operand_looks_valid(rest[2:])
    return rest[:2] in _ITANIUM_OPERATOR_CODES


def _looks_structurally_mangled(value: str) -> bool:
    """Whether *value* has the shape of a real Itanium or MSVC mangling, on
    its own -- independent of whether it also happens to equal a
    declaration's plain name (used by :func:`is_real_mangled_name` to tell
    a genuine mangling that rode into both fields apart from actual
    C-linkage evidence).

    Itanium ``_Z...`` names are validated structurally
    (:func:`_looks_like_itanium_encoding`) rather than merely on the ``_Z``
    prefix and character set alone. MSVC ``?...`` manglings have no such
    check available (no demangler in this codebase to cross-check against
    at all) and are accepted on the prefix convention alone (best-effort,
    matches how the rest of the codebase already treats MSVC-mangled names
    it cannot independently demangle) -- beyond requiring at least one
    byte of encoded payload after the ``?``: a bare ``"?"`` has no payload
    at all and is not a real mangling, but previously matched the prefix
    check alone (Codex review, fresh evidence).
    """
    if value.startswith("_Z"):
        if not _ITANIUM_MANGLED_RE.match(value):
            return False
        rest = value[2:].split(".", 1)[0]  # drop clone suffix
        return _looks_like_itanium_encoding(rest)
    return value.startswith("?") and len(value) > 1


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
    """
    if not is_real_mangled_name(mangled_name, plain_name):
        return None
    assert mangled_name is not None  # for type-checkers; guarded above
    return mangled_name if _looks_structurally_mangled(mangled_name) else None


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
    if source_location:
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
#: review, five rounds: ``VIRTUAL_METHOD_ADDED``/``CALLING_CONVENTION_CHANGED``/
#: ``METHOD_ACCESS_CHANGED``/``DEFAULT_ARGUMENT_CHANGED``/the two
#: function-template ``TEMPLATE_*_TYPE_CHANGED`` kinds/``TLS_VAR_SIZE_CHANGED``/
#: ``PROTECTED_VISIBILITY_CHANGED`` were flagged as missing in turn -- the
#: last two verified against ``diff_platform.py``'s ``_diff_tls_symbols``/
#: ``_diff_protected_visibility``, both of which pass a real exported
#: variable's mangled name as ``symbol=sym_name``).
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
        "tls_var_size_changed",
        "protected_visibility_changed",
        # diff_platform_elf_symbols.py's alignment check passes the real
        # exported data object's symbol (symbol=sym_name), the same shape
        # as tls_var_size_changed/protected_visibility_changed above
        # (Codex review).
        "exported_object_alignment_reduced",
    }
)

#: ``symbol_*`` kinds that are the one exception to "``symbol_*`` is always
#: entity-bearing" -- their ``Change.symbol`` is a version-node/requirement
#: label (``diff_versioning.py``/``diff_platform_elf_symbols.py``: e.g.
#: ``symbol=node`` where ``node`` is a string like ``"GLIBC_2.17"``) or a
#: synthetic batch identifier (``diff_symbols_renames.py``'s
#: ``emit_prefix_batch_rename``/``emit_namespace_move_batches``:
#: ``symbol=f"batch_rename:{prefix}*"`` and
#: ``symbol=f"batch_rename:{old_segment}->{new_segment}"``), never a real exported function or
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


#: A handful of ``symbol_*`` kinds are emitted by *two* different detectors
#: under the same :class:`~abicheck.checker_policy.ChangeKind` -- one
#: per-symbol (``change.symbol`` names the one real entity that transitioned),
#: the other library-level/batch (an arbitrary, alphabetically-first affected
#: symbol is sampled into ``change.symbol`` as a "spokesperson" for the whole
#: release). ``diff_platform_elf_symbols.py``'s ``_check_gained_gnu_unique``
#: is the concrete case: it fires once per release when a build newly turns
#: on ``-fgnu-unique``, reporting *how many* exports gained
#: ``STB_GNU_UNIQUE`` binding rather than describing one specific symbol's
#: transition (Codex review). Promoting that arbitrary sample to CANONICAL
#: would alias it against whatever *other*, unrelated finding genuinely owns
#: that mangled name.
#:
#: The two shapes share a kind but never share ``old_value``: only the batch
#: call site passes this literal sentinel (verified against the one
#: ``make_change`` call that produces it); the sibling per-symbol detector
#: (``_check_binding_change``) always passes a real ``SymbolBinding`` enum
#: value (``"GLOBAL"``, ``"WEAK"``, ``"LOCAL"``, ...) as ``old``, never this
#: sentence -- so matching on it can't misclassify a legitimate per-symbol
#: finding.
_BATCH_SHAPED_OLD_VALUES = {
    "symbol_binding_became_unique": "(no GNU_UNIQUE exports)",
}

#: Kinds with no per-symbol sibling detector at all -- every emission is
#: batch-shaped, so no value-based sentinel is needed to tell it apart from
#: a legitimate per-entity finding (unlike :data:`_BATCH_SHAPED_OLD_VALUES`'s
#: kinds, which share a slug with a genuine per-symbol detector).
#: ``diff_platform_elf_symbols.py``'s ``_diff_allocator_replacement`` is the
#: concrete case (Codex review): it fires once per release when a build
#: newly gains/loses a global allocator replacement, sampling the
#: alphabetically-first affected allocator export into both ``symbol`` and
#: (via ``detail``) ``description`` -- the exact same "arbitrary sample as
#: spokesperson" shape as the GNU_UNIQUE case above, just with no
#: SymbolBinding-style sibling to disambiguate against, since these two
#: ``ChangeKind`` values have exactly one producer each (verified: no other
#: ``make_change`` call site in the codebase emits either kind).
#:
#: ``visibility_leak`` joins this set for the same reason (Codex review,
#: fresh evidence): ``diff_platform_elf_dynamic.py``'s ``_diff_visibility_leak``
#: is the sole producer, fires once per release with a fixed
#: ``symbol="<visibility>"`` sentinel (never a real exported name), and
#: embeds up to five names sampled from unsorted ``old.functions`` into
#: ``description`` -- two semantically identical snapshots whose functions
#: happen to serialize in a different order would otherwise get different
#: discriminators (via ``description``) and fragment into separate
#: ``primary_id``s for the same logical finding.
#:
#: ``header_binary_context_mismatch`` is deliberately NOT in this set,
#: unlike the two cases above -- see :data:`_STRUCTURED_EVIDENCE_KIND_SLUGS`
#: below for why blanket batch-shaped treatment is wrong for it (Codex
#: review, fresh evidence, round 2: it would collapse two genuinely
#: different findings -- old-side and new-side -- into one identity).
_ALWAYS_BATCH_SHAPED_KIND_SLUGS = frozenset(
    {
        "allocator_replacement_added",
        "allocator_replacement_removed",
        "visibility_leak",
    }
)

#: ``header_binary_context_mismatch``: unlike the always-batch-shaped kinds
#: above, ``diff_layout_coherence.py``'s ``_mismatch_change`` can legitimately
#: emit TWO findings from a single comparison -- one for the old snapshot,
#: one for the new -- each with its OWN, possibly entirely different,
#: mismatched-record set, but the same ``symbol=snapshot.library`` value on
#: both (Codex review, fresh evidence, round 2). Treating this kind as
#: batch-shaped (clearing ``entity_symbol``/dropping ``description``, as
#: this module already does for the two kinds above) would make both
#: findings resolve to the identical primary_id regardless of their actual
#: content -- a real dedup collision between two distinct findings, not
#: just a specificity degrade. ``Change.affected_symbols`` (unlike
#: ``description``, whose up-to-five-name sample is order-dependent prose)
#: carries the finding's COMPLETE mismatched-record set as structured data
#: -- :func:`_change_discriminator` sorts it for these kinds instead of
#: using ``description``, so two orderings of the *same* set still collide
#: (fixing the original order-sensitivity report) while two *different*
#: sets (e.g. the old-side vs. new-side case) reliably do not. The residual
#: gap when both sides happen to have the exact same mismatched-record set
#: (Codex review, fresh evidence, round 2) is closed too:
#: :data:`_MISMATCH_SIDE_RE` extracts the side word from ``description``'s
#: stable, fixed-wording lead-in ("The old/new snapshot's...") -- unlike
#: the order-dependent five-name sample later in that same string, this
#: prefix never varies for a given side, so it's safe to fold into the
#: discriminator without reintroducing the original order-sensitivity bug.
_STRUCTURED_EVIDENCE_KIND_SLUGS = frozenset({"header_binary_context_mismatch"})

#: Matches ``diff_layout_coherence.py``'s ``_mismatch_change`` description
#: lead-in ("The old snapshot's ..." / "The new snapshot's ...") -- the
#: ONLY place ``Change`` carries which snapshot side a
#: ``header_binary_context_mismatch`` finding is about, since ``side`` is
#: never passed as ``old_value``/``new_value`` (both left ``None``) or any
#: other structured field. Anchored to this specific detector's exact,
#: hand-verified wording (not a general free-text heuristic) -- a
#: description that doesn't match this prefix (a future producer, a
#: hand-built test ``Change``) degrades ``side`` to ``""``, this module's
#: usual ambiguity-safe fallback.
_MISMATCH_SIDE_RE = re.compile(r"\AThe (old|new) snapshot's")


def _is_batch_shaped_change(change: Change, kind_value: str) -> bool:
    """True when *change* is a library-level/batch finding, not a per-entity one.

    See :data:`_BATCH_SHAPED_OLD_VALUES`/:data:`_ALWAYS_BATCH_SHAPED_KIND_SLUGS`
    -- gates :func:`resolve_change_identity` against promoting a batch
    finding's sampled symbol to a CANONICAL entity identity.
    """
    if kind_value in _ALWAYS_BATCH_SHAPED_KIND_SLUGS:
        return True
    sentinel = _BATCH_SHAPED_OLD_VALUES.get(kind_value)
    return sentinel is not None and change.old_value == sentinel


#: Change kinds ``diff_filtering`` already treats as one logical event
#: reported by two different detectors -- mirrored here (this leaf module
#: still doesn't import ``diff_filtering``, so the dependency direction stays
#: one-way: ``diff_filtering`` depends on this module for its
#: ``_deduplicate_cross_detector`` dedup key, ADR-049 Phase 2 wiring, not the
#: reverse). Keep in sync with the two mappings this generalizes:
#: ``_deduplicate_cross_detector``'s local ``_DEDUP_CATEGORIES``
#: (rich-vs-L0 function/variable add/remove, symbol-version-node pairs --
#: wired) and module-level ``_DWARF_TO_AST_EQUIV``'s two *whole-type* pairs
#: (``STRUCT_SIZE_CHANGED``/``TYPE_SIZE_CHANGED``,
#: ``STRUCT_ALIGNMENT_CHANGED``/``TYPE_ALIGNMENT_CHANGED``) -- safe to
#: collapse because ``symbol`` names the whole type on both sides, with no
#: field-level substructure to lose.
#:
#: Deliberately excludes ``_DWARF_TO_AST_EQUIV``'s three *field-level*
#: pairs (``STRUCT_FIELD_OFFSET_CHANGED``/``STRUCT_FIELD_REMOVED``/
#: ``STRUCT_FIELD_TYPE_CHANGED`` vs. their ``TYPE_FIELD_*`` counterparts):
#: the DWARF side field-qualifies ``symbol`` (``"Point::x"``,
#: ``diff_platform.py``), but the AST side does not (``symbol="Point"``,
#: the field name only in ``description`` via ``detail=fname``,
#: ``diff_types.py``) -- collapsing these to a bare category would make
#: two *different* AST-side field findings on the same struct (``Point::x``
#: vs. ``Point::y``) collide with each other, not just with their DWARF
#: counterpart (Codex review: caught exactly this for
#: ``TYPE_FIELD_OFFSET_CHANGED``). Safely fixing this needs a real
#: per-field discriminator this module doesn't
#: have a reliable source for (the two detectors don't encode field
#: identity in a common field) -- left as the conservative default
#: (full kind/old/new/description discriminator, no cross-detector
#: collision for these three kinds) rather than a fix that risks losing a
#: distinct field-level fact, matching this module's ambiguity-safe bias.
#:
#: The three enum entries (self-mapped: same kind on both sides, not a
#: kind pair) mirror ``diff_filtering._dedup_enum_same_kind``'s own
#: ``(kind, symbol)`` dedup key exactly (Codex review): its AST detector
#: (``diff_types.py``) uses the registry description template with no
#: embedded values, while its DWARF detector (``diff_platform.py``) passes
#: a bespoke ``description`` embedding ``"({old} → {new})"`` -- the same
#: "one logical event, two detectors' own wording" shape as the kind-pair
#: entries above, just sharing one kind slug instead of two. Both
#: detectors populate ``old_value``/``new_value`` identically
#: (``str(old_val)``/``str(new_val)``), so only ``description`` actually
#: needed dropping, but collapsing the full discriminator (like every
#: other entry here) matches ``_dedup_enum_same_kind``'s own key precisely
#: rather than assuming every current and future producer of these kinds
#: populates old/new consistently.
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
    "enum_member_value_changed": "enum_member_value_changed",
    "enum_member_removed": "enum_member_removed",
    "enum_last_member_value_changed": "enum_last_member_value_changed",
    # diff_types._diff_enums (L2 header tier, bare EnumType.name) and
    # diff_platform._diff_enum_layouts (L1 DWARF tier, fully-qualified DWARF
    # dict key) both emit this same kind for the same enum's underlying-type
    # size change — self-mapped so its discriminator collapses across
    # producers the same way the three sibling enum-member kinds above
    # already do; see diff_filtering._deduplicate_cross_detector's own
    # docstring for the qualified-name bridge this also depends on.
    "enum_underlying_size_changed": "enum_underlying_size_changed",
    # diff_symbols._detect_newly_deleted_functions emits FUNC_DELETED
    # (castxml is_deleted attribute) or FUNC_DELETED_DWARF (DWARF
    # DW_AT_deleted) for the same symbol/callable->deleted transition --
    # which kind you get depends only on which evidence source (header
    # analysis vs. binary DWARF) observed the deletion, the same
    # evidence-tier-producer variance every other pair in this map
    # normalizes (Codex review).
    "func_deleted": "func_deletion",
    "func_deleted_dwarf": "func_deletion",
}

# Kinds whose old_value/new_value (and, for a handful, description) genuinely
# hold a C/C++ type spelling -- verified individually against each kind's
# emission call site and change_registry.py description_template, not
# guessed. Used only by _change_discriminator's canonicalize_values=True path
# (report_canonical_finding_id) to decide which findings need
# canonicalize_type_name normalization at all (Codex review, PR #753, two
# follow-up rounds): applying it to every kind corrupted non-type old/new
# text (e.g. PUBLIC_MACRO_VALUE_CHANGED's raw macro replacement text, where
# whitespace is semantically meaningful), and unconditionally dropping
# description to avoid that same corruption instead collided distinct
# findings that share kind+symbol+old+new and differ only by a per-item
# identity description embeds via change_registry's `{detail}` (e.g.
# TYPE_FIELD_TYPE_CHANGED's own sibling struct_field_type_changed embeds a
# field name this way). Deliberately not exhaustive -- every kind here was
# checked against a real call site; a kind absent from this set still has
# the pre-fix backend-spelling-mismatch gap on canonical_finding_id, and
# adding it needs the same per-kind verification, not a blanket sweep.
#
# Previously a known gap for ATOMIC_QUALIFIER_CHANGED, CHAR8T_MIGRATION, and
# BIT_INT_WIDTH_CHANGED (Codex review, PR #753, fifth round): all three
# detectors (`diff_atomic.py`, `diff_char8t.py`, `diff_bit_int.py`) pass
# `iter_type_slot_changes`'s raw type-slot spellings straight through as
# `old=`/`new=`, with no `{detail}`-carried per-item identity at stake --
# `detail` there is a fixed direction string ("qualifier added"/"char-family
# → char8_t"/"_BitInt width changed N1 → N2"), never a per-item identity like
# a field/parameter name, so canonicalizing old/new can't collide two
# distinct findings the way it would for e.g. TYPE_FIELD_TYPE_CHANGED.
# Verified individually against each kind's emission call site, same bar
# this set's own docstring above already commits to -- not the rejected
# *derived* classification (every kind whose emitter calls
# `iter_type_slot_changes`), added here as three more explicit, individually
# checked entries instead.
#
# A *derived* classification (e.g. every kind whose emitter calls
# `iter_type_slot_changes`/passes a `RecordType`/`Param.type`-sourced value
# as `old=`/`new=`) was considered instead of one more reactive addition --
# rejected: it needs the same per-kind call-site verification this file's
# own docstring above already commits to (a value's *source* doesn't by
# itself prove no `{detail}`-carried per-item identity is also at stake, the
# exact trap the earlier description-dropping attempt fell into), just
# automated instead of explicit, which trades a visible, auditable list for
# an implicit one that's harder to review. Matches this codebase's own
# "known gaps over risky reactive patches" convention (AGENTS.md) rather
# than a same-session revision of this exact function with no per-kind
# verification.
_TYPE_BEARING_DISCRIMINATOR_KINDS = frozenset(
    {
        "func_return_changed",
        "var_type_changed",
        "type_field_type_changed",
        "struct_field_type_changed",
        "union_field_type_changed",
        "typedef_base_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        # diff_atomic.py/diff_char8t.py/diff_bit_int.py: old_value/new_value
        # are iter_type_slot_changes()'s raw type-slot spellings, and
        # `detail` (folded into `description` via change_registry's
        # `{detail}` placeholder) is always a fixed direction string, never
        # a per-item identity -- see this set's own comment above.
        "atomic_qualifier_changed",
        "char8t_migration",
        "bit_int_width_changed",
        # diff_symbols._check_params_change: old/new are
        # _format_params()'s comma-joined `Param.type` spellings, one
        # function-wide finding (no per-parameter `detail`, so nothing is
        # lost by canonicalizing the whole joined string as a *set of
        # parameters* -- see _canonicalize_params_list below, which splits
        # and canonicalizes each parameter individually rather than the
        # joined string as one type. A whole-string canonicalize_type_name
        # call only reorders const/strips a struct-prefix on the FIRST
        # parameter (both anchored to string start) -- a real change on
        # parameter N plus an unrelated const-spelling difference on some
        # OTHER parameter M would then still produce two different
        # discriminators for the identical function-wide event across
        # producers (Codex review, fresh evidence).
        "func_params_changed",
    }
)

#: Kinds whose ``change_registry`` ``description_template`` embeds the raw
#: ``{old}``/``{new}`` type spelling verbatim, NOT at the start of the
#: sentence (Codex review, fresh evidence): ``struct_field_type_changed``
#: ("Field type changed: {name}::{detail} {old} → {new}"),
#: ``template_param_type_changed``/``template_return_type_changed``
#: ("... ({old} → {new})"). Canonicalizing the whole rendered sentence
#: with :func:`canonicalize_type_name` doesn't fix these -- its
#: struct-prefix-strip/const-reorder passes are anchored to string start,
#: so they never reach text embedded mid-sentence, and the embedded
#: spelling stays raw even after old_str/new_str themselves are correctly
#: canonicalized. ``atomic_qualifier_changed``/``char8t_migration``/
#: ``bit_int_width_changed`` have the identical shape --
#: ``"_Atomic {detail} on {name}: {old} → {new}. ..."`` and siblings
#: (``change_registry.py``) all embed ``{old} → {new}`` after a leading
#: ``{detail}`` clause, never at sentence start. The remaining allowlisted
#: kinds' templates never embed {old}/{new} at all (`"Return type changed:
#: {name}"` and siblings), so this set is still narrower than
#: _TYPE_BEARING_DISCRIMINATOR_KINDS.
_DESCRIPTION_EMBEDS_VALUES_KINDS = frozenset(
    {
        "struct_field_type_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        "atomic_qualifier_changed",
        "char8t_migration",
        "bit_int_width_changed",
    }
)


def _split_top_level_commas(value: str) -> list[str]:
    """Split *value* on ``,`` at nesting depth 0 only.

    ``_format_params()`` comma-joins ``Param.type`` spellings, but a
    parameter's own type can itself contain a comma inside template
    arguments (``std::pair<int, int>``) or a function-pointer parameter
    list (``void (*)(int, int)``) -- a naive ``str.split(",")`` would
    wrongly split those apart. Tracks ``<>``/``()``/``[]`` nesting depth
    and only treats a comma at depth 0 as a real parameter separator.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(value):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(value[start:i])
            start = i + 1
    parts.append(value[start:])
    return parts


def _canonicalize_params_list(value: str) -> str:
    """Canonicalize a ``_format_params()``-shaped comma-joined parameter
    list one parameter at a time, rejoining with ``", "`` (matching
    ``_format_params``'s own join).

    Degrades to whole-string canonicalization for the sentinel ``"(none)"``
    ``_format_params`` emits for a zero-parameter function -- splitting
    that on commas would be a no-op anyway, but routing it through the
    same call keeps this function's contract uniform.
    """
    return ", ".join(
        canonicalize_type_name(part.strip()) for part in _split_top_level_commas(value)
    )


def _stringify_change_value(value: object) -> str:
    """Deterministic string form of a ``Change.old_value``/``new_value``.

    Both fields are annotated ``str | None``, but that annotation isn't
    runtime-enforced and at least one real producer violates it:
    ``diff_python.py``'s ``PYTHON_STABLE_ABI_VIOLATION`` emissions pass a
    list (e.g. ``new_value=sorted(group)``) for several of its findings.
    Joining that list directly into ``_change_discriminator``'s
    ``\\x1f``-separated ``parts`` crashed with ``TypeError: sequence item
    ...: expected str instance, list found`` instead of producing an
    identity (Codex review, fresh evidence) -- this module must resolve an
    identity for every real flat ``Change``, not only ones matching the
    aspirational type annotation. Lists/tuples (the one structured shape
    actually observed) join deterministically, preserving the producer's
    own ordering (already sorted where order matters); any other
    unforeseen structured value falls back to ``str()`` rather than
    crashing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


# Only the bit-valued half of each _EQUIVALENT_CHANGE_CATEGORIES size/
# alignment pair needs converting -- diff_platform.py's byte-valued
# STRUCT_SIZE_CHANGED/STRUCT_ALIGNMENT_CHANGED are already the target unit.
# Used only by _change_discriminator's canonicalize_values=True path, so a
# category-collapsed kind's old/new agree regardless of the detector
# (Codex review, fresh evidence -- see that call site's own comment).
_CATEGORY_VALUE_UNIT_DIVISOR = {
    "type_size_changed": 8,
    "type_alignment_changed": 8,
}

#: Categories where old_value/new_value must be dropped from the
#: canonicalize_values discriminator entirely, rather than folded in
#: (Codex review, fresh evidence, PR #753 round 5): unlike the size/
#: alignment pair above -- where old/new differ only by *unit* across
#: producers and normalizing fixes the fold -- a removed/added symbol can
#: have at most one such event per (symbol, category) in a single
#: comparison, so old/new carries no disambiguating power here at all,
#: only producer-specific noise. Confirmed for "func_removal": the rich
#: ELF detector (diff_symbols._check_removed_function) stamps
#: old_value=f_old.name, while the PE/Mach-O export-table detectors
#: (diff_platform._diff_pe/_diff_macho_exports) leave old_value/new_value
#: unset entirely -- so the identical removed-function event folds to two
#: different discriminators ("category:func_removal\x1f<name>\x1f" vs.
#: "category:func_removal\x1f\x1f") depending purely on which detector's
#: finding survived reconciliation, defeating this fold's whole purpose
#: for the one pair it exists to reconcile. "func_addition" carries the
#: identical asymmetry for the same two detector families. The remaining
#: three categories ("var_removal"/"var_addition"/"version_def_removal")
#: are not included here: each is populated by exactly one detector
#: family with consistent old/new population (verified by reading every
#: producer of each), so there is no cross-producer inconsistency to
#: guard against for them, and dropping their values would only lose
#: information for no benefit.
_VALUE_INSENSITIVE_CATEGORIES = frozenset({"func_removal", "func_addition"})


def _divide_numeric_string(value: str, divisor: int) -> str:
    """Return ``str(int(value) // divisor)``, or *value* unchanged if it
    isn't a base-10 integer (defensive -- every real producer of a
    _CATEGORY_VALUE_UNIT_DIVISOR kind stamps a plain bit count, but this
    must never raise on an unexpected input)."""
    try:
        return str(int(value) // divisor)
    except ValueError:
        return value


def _change_discriminator(
    change: Change,
    kind_value: str,
    *,
    include_description: bool = True,
    canonicalize_values: bool = False,
) -> str:
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

    ``include_description=False`` additionally drops ``description`` alone
    (keeping ``old_value``/``new_value``) -- used by
    :func:`resolve_change_identity` for a batch-shaped change whose
    ``description`` embeds the arbitrary sampled symbol via its
    ``description_template`` (``diff_platform_elf_symbols.py``'s
    ``_check_gained_gnu_unique``: ``"Symbol binding became GNU_UNIQUE:
    {name} ..."`` where ``name`` is the sample). Clearing ``entity_symbol``
    alone is not enough, since ``description`` still varies with the sample
    even though ``old_value``/``new_value`` do not (Codex review: verified
    changing only the sampled export still produced a different id).

    ``canonicalize_values=True`` -- used only by
    :func:`report_canonical_finding_id`'s call into
    :func:`resolve_change_identity` -- runs ``old_value``/``new_value``,
    and (for the same kinds) ``description``, through
    :func:`~abicheck.name_classification.canonicalize_type_name` before
    joining, but **only** for :data:`_TYPE_BEARING_DISCRIMINATOR_KINDS`.
    Without it, a kind like ``FUNC_RETURN_CHANGED`` folds the *raw* type
    spelling into every identity tier including CANONICAL -- CastXML's
    ``"char const*"`` and Clang's ``"char const *"`` for the identical
    change would then hash to two different canonical ids (Codex review).
    Scoped to that allowlist rather than every kind (Codex review, follow-up
    round): canonicalizing an arbitrary kind's ``old_value``/``new_value``
    corrupts a non-type value where whitespace is semantically meaningful
    (e.g. ``PUBLIC_MACRO_VALUE_CHANGED``'s raw macro replacement text).
    ``description`` is **never dropped** for this reason too (an earlier
    revision of this fix did, and review found real collisions: several
    kinds -- e.g. ``struct_field_type_changed``'s own sibling
    ``type_field_type_changed`` -- embed a per-item identity, such as a
    field name, in ``description`` via ``change_registry``'s ``{detail}``
    template placeholder, with no other structured field carrying it;
    dropping description collapsed two distinct findings differing only by
    which field/parameter changed). Canonicalizing the whole sentence for
    an allowlisted kind is safe rather than corrupting: ``canonicalize_
    type_name``'s struct/const-prefix rewrites only match at the string's
    start, and its pointer/reference-sigil pass only touches ``*``/``&`` --
    neither appears in an ordinary field/parameter name, so any embedded
    raw type spelling normalizes the same way ``old_value``/``new_value`` do.
    """
    category = _EQUIVALENT_CHANGE_CATEGORIES.get(kind_value)
    if category is not None:
        if not canonicalize_values or category in _VALUE_INSENSITIVE_CATEGORIES:
            # See _VALUE_INSENSITIVE_CATEGORIES: for these categories old/
            # new is producer-inconsistent noise, not a disambiguator (at
            # most one such event exists per symbol/category in a single
            # comparison), so folding it in would defeat the fold instead
            # of refining it -- same bare-category discriminator as the
            # non-canonicalize_values path above.
            return f"category:{category}"
        # canonicalize_values (report_canonical_finding_id): a bare
        # category discriminates the rich-vs-L0 *kind* pair away, which is
        # exactly what the reconciliation use of this identity wants -- but
        # a persistent suppression identity must still tell apart two
        # structurally different transitions on the same symbol/category
        # (e.g. TYPE_SIZE_CHANGED 8->16 vs. a later, unrelated 16->32 on
        # the same type), or accepting a `finding_id:` rule for the first
        # would silently suppress the second too (Codex review, fresh
        # evidence). Folding in old_value/new_value is NOT unit-safe by
        # itself: diff_types.py's TYPE_SIZE_CHANGED/TYPE_ALIGNMENT_CHANGED
        # stamp RecordType.size_bits/alignment_bits (bits), while
        # diff_platform.py's collapsed sibling STRUCT_SIZE_CHANGED/
        # STRUCT_ALIGNMENT_CHANGED stamp StructLayout.byte_size/alignment
        # (bytes) -- the identical 8-byte layout change reports as
        # "64\x1f128" from one detector and "8\x1f16" from the other
        # (Codex review, fresh evidence), which would make the canonical
        # id depend on which detector happened to supply the *retained*
        # finding after reconciliation, defeating this fold's own point.
        # _CATEGORY_VALUE_UNIT_DIVISOR converts every such kind to bytes
        # before folding, leaving every other collapsed kind's value
        # (a removed symbol's own name, an enum value, ...) untouched.
        old_val = _stringify_change_value(change.old_value)
        new_val = _stringify_change_value(change.new_value)
        divisor = _CATEGORY_VALUE_UNIT_DIVISOR.get(kind_value)
        if divisor is not None:
            old_val = _divide_numeric_string(old_val, divisor)
            new_val = _divide_numeric_string(new_val, divisor)
        old_str = canonicalize_type_name(old_val)
        new_str = canonicalize_type_name(new_val)
        return f"category:{category}\x1f{old_str}\x1f{new_str}"
    # header_binary_context_mismatch: change.affected_symbols carries this
    # finding's complete mismatched-record set as structured data (unlike
    # description's order-dependent five-name sample) -- sorting it makes
    # the discriminator order-independent while still varying whenever the
    # actual evidence differs (see _STRUCTURED_EVIDENCE_KIND_SLUGS). The
    # side prefix (extracted via _MISMATCH_SIDE_RE, itself immune to the
    # sampled-order issue) keeps an old-side and new-side finding distinct
    # even when they happen to report the exact same mismatched-record set
    # (Codex review, fresh evidence, round 2).
    if kind_value in _STRUCTURED_EVIDENCE_KIND_SLUGS:
        # A comma join is not injective when a C++ record name itself
        # contains a comma (common in template types, e.g.
        # "A<int,int>") -- mismatch sets ("A,B", "C") and ("A", "B,C")
        # would both join to "A,B,C", colliding two distinct evidence
        # sets (Codex review, fresh evidence). "\x1f" (unit separator,
        # the same delimiter the fallback `parts` join below uses) is
        # not a byte any real record name contains, so the join stays
        # unambiguous.
        evidence = "\x1f".join(sorted(change.affected_symbols or ()))
        side_match = _MISMATCH_SIDE_RE.match(change.description or "")
        side = side_match.group(1) if side_match else ""
        return f"evidence:{side}:{evidence}"
    canonicalize_this_kind = (
        canonicalize_values and kind_value in _TYPE_BEARING_DISCRIMINATOR_KINDS
    )
    raw_old_str = _stringify_change_value(change.old_value)
    raw_new_str = _stringify_change_value(change.new_value)
    old_str = raw_old_str
    new_str = raw_new_str
    if canonicalize_this_kind:
        if kind_value == "func_params_changed":
            # See _TYPE_BEARING_DISCRIMINATOR_KINDS's own comment: a
            # comma-joined parameter list needs per-parameter
            # canonicalization, not one whole-string call.
            old_str = _canonicalize_params_list(old_str)
            new_str = _canonicalize_params_list(new_str)
        elif kind_value == "atomic_qualifier_changed":
            # See canonicalize_atomic_slot's own docstring
            # (finding_identity_atomic.py): CastXML's bare "_Atomic"
            # sentinel and Clang's real wrapped spelling must collapse to
            # the same value, which plain canonicalize_type_name cannot do
            # on its own. Each side needs the OTHER side's raw spelling to
            # tell a redundant qualifier-only wrap apart from a compound
            # change that also altered the payload.
            old_str = canonicalize_atomic_slot(raw_old_str, raw_new_str)
            new_str = canonicalize_atomic_slot(raw_new_str, raw_old_str)
        else:
            old_str = canonicalize_type_name(old_str)
            new_str = canonicalize_type_name(new_str)
    parts = [kind_value, old_str, new_str]
    if include_description:
        desc = change.description or ""
        if canonicalize_this_kind:
            if kind_value in _DESCRIPTION_EMBEDS_VALUES_KINDS:
                # See _DESCRIPTION_EMBEDS_VALUES_KINDS's own comment:
                # canonicalize_type_name's anchored passes can't reach an
                # embedded type mid-sentence, so substitute the *known*
                # raw old/new substrings with their already-canonicalized
                # forms directly, rather than canonicalizing the sentence
                # as one opaque blob.
                #
                # Substitute the LONGER raw string first (Codex review,
                # fresh evidence): atomic_qualifier_changed's own
                # "qualifier added" direction embeds the unqualified raw
                # spelling verbatim *inside* the qualified one (e.g. raw_old
                # "struct Foo *" is a literal substring of raw_new
                # "_Atomic(struct Foo *)") -- replacing the shorter raw_old
                # first would also rewrite the copy embedded inside
                # raw_new's own span before raw_new's own replace ever runs,
                # so raw_new's exact text no longer appears in desc and its
                # replace becomes a silent no-op, leaving the qualified
                # side's stale, backend-specific spelling in the discriminator.
                for raw, canon in sorted(
                    ((raw_old_str, old_str), (raw_new_str, new_str)),
                    key=lambda pair: len(pair[0]),
                    reverse=True,
                ):
                    if raw:
                        desc = desc.replace(raw, canon)
            else:
                desc = canonicalize_type_name(desc)
        parts.append(desc)
    return "\x1f".join(parts)


def resolve_change_identity(
    change: Change, *, canonicalize_values: bool = False
) -> FindingIdentity:
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
    ``primary_id`` at every tier, including CANONICAL, mirroring
    ``reporter_markdown._finding_id``'s own established minimal
    disambiguating set (Codex review: a bare ``mangled:<symbol>`` canonical
    id would collapse unrelated findings, violating this module's dedup-key
    contract).

    ``canonicalize_values=False`` (the default) preserves this function's
    pre-existing, exact-value discriminator for its established callers
    (``diff_filtering.py``'s cross-detector dedup, ``aggregate_findings.py``,
    ``contract_evaluation.py``) -- none verified against a type-spelling-
    insensitive comparison. Pass ``True`` only for a genuinely cross-backend
    use (:func:`report_canonical_finding_id`), where
    :func:`_change_discriminator`'s own docstring explains why the raw
    value must not leak into a "backend-independent" identity.

    ``change.entity_id``, when set, folds in as an additional ``entity:``
    alias (ADR-063 Phase 2) -- additive, never ``primary_id``/tier.
    """
    kind_value = str(getattr(change.kind, "value", change.kind))
    is_batch = _is_batch_shaped_change(change, kind_value)
    # A batch-shaped finding's `symbol` is an arbitrary sample (the
    # alphabetically-first affected export), not a real entity this finding
    # is "about" -- using it for `qn`/aliases/the REDUCED-tier basis, not
    # only for CANONICAL mangled-name promotion, would still let the
    # identity change whenever the sampled export changes and could still
    # collide with an unrelated finding that genuinely owns that symbol
    # (Codex review: the CANONICAL-only guard left this same leak at the
    # NORMALIZED tier and in the `symbol`/`relsrc` aliases). `entity_symbol`
    # is `None` for a batch-shaped change so every downstream identity/alias
    # below naturally omits the sample instead of needing its own check.
    entity_symbol = None if is_batch else change.symbol
    # `_enrich_source_locations` runs on every Change and can populate
    # `qualified_name` by looking up `change.symbol` -- for a batch-shaped
    # change that symbol is the arbitrary sample, so an enrichment hit
    # would derive `qualified_name` from the sample too, leaking it right
    # back in even with `entity_symbol` cleared above (Codex review: caught
    # for the sibling `_diff_allocator_replacement` batch kinds, but the
    # same channel exists for every batch-shaped kind).
    qualified_name = None if is_batch else change.qualified_name
    # `_enrich_source_locations` also populates `source_location` from the
    # sampled export for a batch-shaped change (Codex review, round 3 on
    # this same leak channel) -- feeding `change.source_location` into
    # `rel`/the `relsrc:` alias/the REDUCED-tier synthetic basis below
    # would still make the supposedly library-level identity change
    # whenever the sampled export's file changes.
    source_location = None if is_batch else change.source_location
    # Same is_batch guard as the three fields above, defensively -- no
    # producer sets entity_id on a batch-shaped Change today.
    entity_id = None if is_batch else change.entity_id
    is_symbol_level = _is_symbol_level_kind(kind_value) and not is_batch
    # For a symbol-level change, `entity_symbol` (the raw exported name) is
    # the more tier-stable NORMALIZED-tier basis than `qualified_name` --
    # mirroring `resolve_symbol_identity`'s identical extern-C preference
    # for entity identity. `_enrich_source_locations` scope-qualifies a
    # namespaced extern "C" removal's `qualified_name` ("ns::foo") whenever a
    # rich snapshot has the matching declaration, while the equivalent L0/
    # ELF-only finding for the SAME event has no scope info to qualify with
    # at all (`symbol="foo"`). Preferring `qualified_name` here would give
    # the two `_EQUIVALENT_CHANGE_CATEGORIES`-collapsed findings (e.g. rich
    # FUNC_REMOVED vs. L0 FUNC_REMOVED_ELF_ONLY) different `sig:`-based
    # primary ids even after the discriminator itself collapses to one
    # shared category, defeating the collapse (Codex review).
    qn = (
        entity_symbol
        if is_symbol_level and entity_symbol
        else qualified_name or entity_symbol or ""
    )
    # A batch-shaped change's `description` embeds the same arbitrary
    # sample as `symbol` (Codex review, round 2 on this same finding) --
    # `entity_symbol` alone isn't enough to make the identity
    # sample-independent, since `discriminator` (used in `sig`, every
    # alias, and the REDUCED-tier synthetic basis) would still vary.
    discriminator = _change_discriminator(
        change,
        kind_value,
        include_description=not is_batch,
        canonicalize_values=canonicalize_values,
    )
    sig = f"sig:{qn}\x1f{discriminator}"
    rel = source_relative_identity(source_location or "", entity_symbol or "")

    real_mangled = None
    if is_symbol_level:
        real_mangled = normalize_mangled_name(change.symbol, qualified_name)

    # Every alias below is qualified with `discriminator`, matching `primary`/
    # `sig`: a bare `mangled:<x>`/`symbol:<x>`/`qualified:<x>` alias would
    # identify the *entity*, not this *finding* -- two distinct findings on
    # the same entity (e.g. a return-type and a param-type change on the
    # same function) would share every entity-scoped alias, and a future
    # alias-match reconciliation tier would wrongly pair them whenever each
    # side has only one candidate (Codex review).
    aliases: list[str] = []
    if real_mangled:
        aliases.append(f"mangled:{real_mangled}\x1f{discriminator}")
    if entity_symbol:
        aliases.append(f"symbol:{entity_symbol}\x1f{discriminator}")
    if qualified_name:
        aliases.append(f"qualified:{qualified_name}\x1f{discriminator}")
    aliases.append(sig)
    if source_location:
        aliases.append(f"relsrc:{rel}\x1f{discriminator}")
    if entity_id is not None:
        # ADR-063 Phase 2: first real read of Change.entity_id. Additive
        # only -- EntityId.key isn't yet proven stable across releases
        # enough to replace what stored suppression rules key against.
        aliases.append(f"entity:{entity_id.key}\x1f{discriminator}")

    if real_mangled:
        primary = f"mangled:{real_mangled}\x1f{discriminator}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    if qn:
        return FindingIdentity(sig, IDENTITY_TIER_NORMALIZED, tuple(aliases))

    basis = "\x1f".join(
        str(x) for x in (entity_symbol, discriminator, source_location) if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return FindingIdentity(synthetic, IDENTITY_TIER_REDUCED, tuple(aliases))


# --------------------------------------------------------------------------
# ADR-049 Phase 2: the ambiguity-safe old/new join.
# --------------------------------------------------------------------------


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


def report_finding_id(c: object) -> str:
    """Stable per-finding fingerprint (schema 2.3, additive).

    Deterministic across repeated runs of the same comparison, so a
    consumer can tell "is this the same finding" across two report runs
    (e.g. to correlate a suppression/waiver, or diff two CI runs' findings)
    without relying on array order or index — neither of which abicheck
    guarantees stays stable release to release.

    Derived only from fields that identify the finding's *identity* (kind,
    symbol, old/new value, source location, description) — deliberately
    excluding ``severity``/``evidence_status``, which are policy-derived and
    would make the same underlying finding hash differently under a
    different ``--policy``.

    ``description`` is included as a discriminator: two findings of the same
    kind on the same symbol with the same old/new value and no distinct
    source location (e.g. ``param_pointer_level_changed`` on two different
    parameters of one function, both going from pointer-depth 1 to 2) would
    otherwise collide on an identical id even though they are different
    findings — ``description`` embeds the per-finding detail (parameter
    name/index, member name, …) that disambiguates them.

    Lives in this dependency-free leaf module so every producer of the id can
    reach it without an import cycle: ``reporter_markdown`` keys
    ``--report-mode root-cause`` groups with it (and re-exports it as
    ``_finding_id``, which ``reporter`` re-exports in turn, so existing
    import paths are unchanged), and ``checker`` keys ADR-049's persisted
    decision receipt with it -- a receipt keyed any other way could not be
    correlated with the report's own findings, and importing ``reporter_markdown``
    from ``checker`` would close a cycle the ``import-cycle-growth``
    AI-readiness gate rejects.

    ``disambiguator`` (``Change``'s ODR/multi-TU occurrence discriminator,
    Codex review, PR #1078, eighteenth round) is appended **only when set**:
    unconditionally joining it would rehash every id this function has ever
    produced (always ``None`` before this field existed) -- the same
    universal-rehash cost a prior round declined to pay for a *narrower*
    risk (``docs/contribute/plans/public-contract-default.md``'s
    "delimiter" finding). Conditional append costs nothing for a pre-
    existing finding while resolving the real, newly-reachable collision:
    two ODR-duplicate occurrences now legitimately both survive
    ``diff_filtering._dedup_exact`` with otherwise-identical fields.
    """
    parts = [
        str(getattr(getattr(c, "kind", None), "value", getattr(c, "kind", ""))),
        str(getattr(c, "symbol", None) or ""),
        str(getattr(c, "old_value", None) or ""),
        str(getattr(c, "new_value", None) or ""),
        str(getattr(c, "source_location", None) or ""),
        str(getattr(c, "description", None) or ""),
    ]
    disambiguator = getattr(c, "disambiguator", None)
    if disambiguator:
        parts.append(str(disambiguator))
    key = "\x1f".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def report_canonical_finding_id(c: object) -> str:
    """Backend-independent finding identity for cross-producer suppression
    (schema 2.36, additive; ``suppression.py``'s ``finding_id:`` selector).

    Unlike :func:`report_finding_id` -- which folds in ``source_location``
    and ``description`` specifically to disambiguate two same-kind,
    same-symbol findings from each other -- this id is deliberately
    **not** guaranteed unique per finding. It is :func:`resolve_change_identity`'s
    ``primary_id`` (mangled-symbol CANONICAL tier when available, a
    normalized qualified-name+kind+parameter-signature NORMALIZED tier
    otherwise), called with ``canonicalize_values=True`` -- the one thing
    that call has that ``diff_filtering.py``'s cross-detector dedup and
    ``diff_symbols.py``'s old/new symbol matching's own (default,
    uncanonicalized) calls don't need. Without it, a kind outside
    ``_EQUIVALENT_CHANGE_CATEGORIES`` (most kinds -- e.g.
    ``FUNC_RETURN_CHANGED``) folds ``change.old_value``/``new_value``
    *verbatim* into every identity tier including CANONICAL, so CastXML's
    ``"char const*"`` and Clang's ``"char const *"`` for the identical
    return-type change would hash to two different canonical ids -- exactly
    the discrepancy :func:`canonicalize_type_name` exists to normalize
    (Codex review, fresh evidence: an earlier revision of this function
    called :func:`resolve_change_identity` with the default, and the claim
    in this docstring that the NORMALIZED tier was already canonicalized
    was simply wrong for any non-equivalent-category kind).
    ``canonicalize_values=True`` normalizes (never drops) ``description``
    for the same reason it normalizes ``old_value``/``new_value``: for
    :data:`_DESCRIPTION_EMBEDS_VALUES_KINDS`, it routinely embeds the same
    raw type spelling those fields do, so the known raw substrings are
    swapped for their already-canonicalized forms; for every other kind
    ``description`` still folds in as-is. ``source_location`` is the one
    field this identity never uses at all (Codex review, fresh evidence:
    an earlier revision of this docstring claimed ``description`` was
    dropped entirely, which stopped being true once description
    normalization was added — this identity is not source-location/
    description-*blind*, only *normalized* against them). Either way,
    ``report_finding_id``'s raw ``source_location``/``description`` are
    exactly the fields two header backends are *not* guaranteed to spell
    identically (a raw file:line, or a description embedding a raw type
    spelling) — which is why it is not itself usable as a suppression key
    meant to survive a `--ast-frontend castxml` vs. `--ast-frontend clang`
    switch, and why this sibling id exists instead of widening that one's
    contract.

    A batch-shaped finding (e.g. an allocator-replacement summary covering
    many symbols) resolves through the same REDUCED-tier fallback
    :func:`resolve_change_identity` already documents for that shape — this
    function adds no special-casing of its own.

    Unlike :func:`report_finding_id`, :func:`resolve_change_identity` reads
    ``change.qualified_name``/``change.kind`` via plain attribute access
    (not ``getattr`` with a default) -- a real :class:`~abicheck.checker_types.Change`
    always has both, but a lightweight test double standing in for one (see
    ``cli_scan_baseline._baseline_finding_dicts``'s own "safe to call against
    fakes/stubs" contract, which this id is emitted alongside) may not. That
    case degrades to :func:`report_finding_id` itself -- self-consistent and
    still deterministic, just not guaranteed cross-backend-stable for the
    one input this function couldn't resolve a real identity for.

    **Never returns ``primary_id`` verbatim.** ``primary_id``'s own aliases
    embed a literal ``\\x1f`` (ASCII unit separator) field delimiter (see
    ``resolve_change_identity``'s ``sig``/``mangled:``/``qualified:``
    construction) -- valid inside a Python string and inside a JSON string
    (``json.dumps`` escapes control characters), but YAML's spec forbids an
    unescaped C0 control character in a plain scalar, so a value copied
    verbatim out of a JSON report into a ``--suppress`` YAML file's
    ``finding_id:`` selector would fail to parse (confirmed empirically:
    PyYAML's ``safe_load`` raises ``ReaderError: unacceptable character
    #x001f``). Hashed into the same fixed-length hex-digest shape
    :func:`report_finding_id` already uses instead, which is unconditionally
    printable/YAML-safe/copy-pasteable, and preserves everything this
    function's contract actually promises: two changes with the same
    ``primary_id`` still hash identically, and different ``primary_id``s
    practically never collide (SHA-256, birthday-bound truncated to 16 hex
    chars -- the same collision risk this function's ``finding_id`` sibling
    already accepts for the identical reason).
    """
    try:
        primary_id = resolve_change_identity(
            c,  # type: ignore[arg-type]
            canonicalize_values=True,
        ).primary_id
    except AttributeError:
        return report_finding_id(c)
    return hashlib.sha256(primary_id.encode("utf-8")).hexdigest()[:16]


def missing_contract_kind(gate_scope: object) -> str:
    """The synthetic finding kind a missing contract member is reported under.

    ``--used-by`` and ``--required-symbol`` report the same event -- "this
    label is required and the new library does not have it" -- under two
    different kind slugs, and every report format derives the slug from
    ``DiffResult.gate_scope`` the same way. Centralized here, alongside
    :func:`missing_contract_finding`, because the slug is part of that
    synthetic finding's *identity*: two producers disagreeing on it would
    hash to two different ids for one event.
    """
    return (
        "used_by_missing_symbol"
        if gate_scope == "used_by"
        else "required_symbol_missing"
    )


@dataclass(frozen=True)
class MissingContractFinding:
    """A missing contract member's identity, in ``Change``-compatible shape.

    A missing ``--used-by``/``--required-symbol`` label has no backing
    ``Change`` at all -- each report format synthesizes its own entry for it
    -- so there was nothing for :func:`report_finding_id` to read, and the
    emitted entry carried no ``finding_id`` a consumer could join to
    ADR-049's ``decision_receipt`` (Codex review, fresh evidence). This
    carries exactly the six fields that function hashes, so the synthesized
    entry and the receipt agree by construction rather than by two
    hand-copied literals happening to match.
    """

    kind: str
    symbol: str
    description: str
    old_value: None = None
    new_value: None = None
    source_location: None = None


def missing_contract_finding(kind: str, label: str) -> MissingContractFinding:
    """The identity of the missing-contract finding for *label* under *kind*.

    The description is built here, not by each caller, for the reason
    :class:`MissingContractFinding` gives: it is one of
    :func:`report_finding_id`'s own inputs, so a producer spelling it
    differently would silently mint a different id for the same event.
    """
    return MissingContractFinding(
        kind=kind,
        symbol=label,
        description=(
            f"Required symbol/version '{label}' is missing from the new library."
        ),
    )

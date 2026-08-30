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

"""Whether a caller-supplied mangled-name string is a genuine mangling
(ADR-063 Phase 2, sixth slice) -- split out of ``model/identity.py`` as
its own leaf sibling once that module outgrew its production-size cap,
the same "split out a sibling module, don't raise the cap" pattern this
codebase already applies to ``diff_*.py``/``cli_*.py``.

Relocated verbatim from ``finding_identity.py``, which owned this
~450-line, independently multi-round-reviewed Itanium-mangling-validation
machinery until this slice moved it to the ``model`` layer so
``model.identity.entity_id_for_function``/``entity_id_for_variable`` (the
constructors that need this determination) and
``finding_identity.resolve_function_identity``/``resolve_variable_identity``
(which need the identical determination for a different identity shape)
can both call one algorithm instead of each keeping an independent copy.
See ``model/identity.py``'s own docstring for the direction-of-reuse
reasoning (``model`` is a leaf; ``finding_identity.py`` is comparison-layer
code that imports model entities and ``checker_types``, so the dependency
can only run this way).

Named ``mangled_name_validity`` -- not ``mangled_name`` -- because
``model/mangled_name.py`` already exists (ADR-061 D1's Itanium
scope-component *parsing* chain, an unrelated purpose: recovering a
declaration's scope from a mangled string, not judging whether a string
is genuinely mangled at all).

:func:`is_real_mangled_name`/:func:`normalize_mangled_name` are
``model.identity``'s public surface for this determination --
``model/identity.py`` imports and re-exports both names under their
original spelling, so ``model.identity.is_real_mangled_name`` still
resolves; this module's own private ``_looks_*``/``_valid_*``/``_consume_*``
helpers are not part of that public surface and are reached (by tests)
only via this module directly.

Leaf module: no dependency on ``checker_types``/``diff_*``/anything above
``model``, per ADR-063 D10 -- identical contract to ``model/identity.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = [
    "is_real_mangled_name",
    "normalize_mangled_name",
]


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

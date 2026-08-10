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

"""Preprocessor ``#if``/``#elif``/``#else`` chain reachability, for the C++20
dialect heuristic.

One job: given header bytes, blank out the arms of a conditional chain that a
``-std=`` choice can never make reachable — an ``#if 0`` compatibility stub, or
an arm guarded on the very ``__cplusplus``/``__cpp_*`` macro whose value the
choice being made would decide. What remains is the code that scan may honestly
draw a dialect conclusion from.

Split out of :mod:`abicheck.dumper_ast_config_cpp20` (which sits within a
handful of lines of the AI-readiness 2000-line hard cap) as one self-contained
unit: the ~30 compiled directive patterns here are used by nothing else, and
the fragments they are built from are used by nothing outside this file — so
the dependency runs one way, ``dumper_ast_config_cpp20`` importing this and
never the reverse (no new import cycle; CLAUDE.md "M1-3").
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_PP_IF_OPEN_PATTERN = re.compile(rb"^[ \t]*#[ \t]*(?:if|ifdef|ifndef)\b")
# A parenthesized literal (``#if (0)``, ``#elif (1)``) is the same
# permanently-false/-true guard as the bare spelling — valid C/C++
# preprocessor syntax, just with redundant grouping parens around the
# whole condition (Codex review, ninth round). Both parenthesized and bare
# forms are accepted; a stray unbalanced paren is invalid C anyway, so
# matching only the balanced pair here is sufficient.
_PP_LITERAL_ZERO = rb"(?:\([ \t]*(?:0+|false)[ \t]*\)|(?:0+|false))"
_PP_LITERAL_TRUE = rb"(?:\([ \t]*(?:1|true)[ \t]*\)|(?:1|true))"
_PP_IF_ZERO_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_LITERAL_ZERO + rb"[ \t]*$"
)
# ``#if 1``/``#if true`` (Codex review, tenth round): an unconditionally
# *true* opening arm was never recognized as starting a trackable chain at
# all (only ``#if 0``/feature-guard openings were), so a dead ``#else``/
# ``#elif`` sibling of a genuinely-true ``#if 1`` fell through to plain
# pass-through scanning right along with the live arm -- a real C++20
# construct sitting only in that dead sibling would then force
# ``-std=gnu++20`` onto a header whose *active* code never needed it, and
# could break that active code outright if it uses a soon-to-be-reserved
# word (``consteval``, ``concept``, ...) as an ordinary identifier. Track
# it like ``#elif 1``/``#elif true``: the opening arm itself stays
# unmasked (it's live), but the chain is immediately ``settled`` so every
# later sibling arm is masked as unreachable.
_PP_IF_TRUE_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_LITERAL_TRUE + rb"[ \t]*$"
)
_PP_ELSE_PATTERN = re.compile(rb"^[ \t]*#[ \t]*else\b")
_PP_ELIF_PATTERN = re.compile(rb"^[ \t]*#[ \t]*elif\b")
_PP_ELIF_ZERO_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_LITERAL_ZERO + rb"[ \t]*$"
)
_PP_ELIF_TRUE_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_LITERAL_TRUE + rb"[ \t]*$"
)
_PP_ENDIF_PATTERN = re.compile(rb"^[ \t]*#[ \t]*endif\b")
# A guard on __cplusplus (or a standard feature-test macro, __cpp_*) is
# self-consistent under *any* -std= this heuristic ends up choosing — the
# guard's own condition is what makes its content reachable or not, driven
# by that same choice, so the content behind it must never be allowed to
# drive the choice itself (Codex review). Masking it exactly like #if 0
# breaks that circularity: whichever std gets picked, the guard evaluates
# consistently and the header parses either way.
_PP_IF_CPLUSPLUS_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+.*\b(?:__cplusplus|__cpp_\w+)\b"
)
_PP_ELIF_CPLUSPLUS_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+.*\b(?:__cplusplus|__cpp_\w+)\b"
)
# ``#if __cplusplus < N`` (or ``<=``) is the same "not yet at this
# dialect" polarity as ``#ifndef __cpp_x`` (Codex review, fifteenth
# round), not the ``__cplusplus >= N`` polarity the general guard pattern
# above assumes: the general pattern masks the *guarded* arm and trusts
# the ``#else`` uniformly, which is only correct when the guarded arm is
# the feature-*present* content (``>=``/``>``). For a less-than
# comparison the guarded arm is the pre-C++20-safe fallback and the
# ``#else`` is the circular, feature-present content instead — reusing
# the general pattern's treatment here masked the safe pre-C++20 arm and
# trusted the circular ``#else``, so a header written "if older than
# C++20, do X; else do Y" forced ``-std=gnu++20`` purely because Y was
# there, which could then break an unrelated, genuinely pre-C++20 use of
# the same word as an ordinary identifier in the masked-away X arm.
#
# Restricted to the exact C++20-boundary literals (Codex review,
# seventeenth round): the inverted polarity only holds when the
# threshold *is* C++20 (``202002L``/``201703L``, the two ISO values that
# both mean "not yet C++20" depending on which operator's used). A
# less-than check against an *older* standard's threshold
# (``__cplusplus < 201103L``, "before C++11") is not about the C++20
# decision at all — there the guarded arm is the ancient-dialect
# fallback that's realistically never reached (castxml/clang always
# default to well past C++11), and the ``#else`` is the one that's
# practically always active, so the *general* (mask-guarded/trust-else)
# treatment is already correct for it. Blanket-inverting every
# less-than comparison regardless of threshold masked that always-active
# ``#else`` too, hiding a genuine, unconditional C++20 construct there.
# Deliberately narrow otherwise (matching this file's
# incremental-per-reported-case scope, not a full expression parser):
# reversed operand order (``202002L > __cplusplus``), a combined
# condition (``__cplusplus < 202002L && defined(FOO)``), and other
# comparison operators (``==``, ``!=``) are not attempted here and keep
# falling through to the general pattern above, the same conservative
# default an entirely unrecognized condition gets.
#
# The whole comparison may also be wrapped in one level of parentheses
# (``#if (__cplusplus < 202002L)``, Codex review, twenty-sixth round) —
# just as common a spelling as the parenthesized ``#if (0)``/``#if (1)``
# literals this file already accepts (``_PP_LITERAL_ZERO``/
# ``_PP_LITERAL_TRUE``) — matched the same way: an outer optional-parens
# alternation around the bare comparison.
_PP_CPLUSPLUS_LESS_THAN_CPP20_COMPARISON = (
    rb"__cplusplus[ \t]*(?:<[ \t]*202002L?|<=[ \t]*201703L?)"
)
_PP_CPLUSPLUS_LESS_THAN_CPP20_TAIL = (
    rb"(?:\([ \t]*"
    + _PP_CPLUSPLUS_LESS_THAN_CPP20_COMPARISON
    + rb"[ \t]*\)|"
    + _PP_CPLUSPLUS_LESS_THAN_CPP20_COMPARISON
    + rb")"
)
_PP_IF_CPLUSPLUS_LESS_THAN_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_CPLUSPLUS_LESS_THAN_CPP20_TAIL + rb"[ \t]*$"
)
# Same fix, ``#elif`` spelling (Codex review, sixteenth round): a
# less-than fallback can just as well be written as a later arm in a
# chain that starts with a disabled/unrelated arm
# (``#if 0 ... #elif __cplusplus < N ... #else ... #endif``). Treated
# exactly like ``#elif 1``/``#elif true`` for masking purposes: it's the
# trustworthy arm once reached, so it settles the chain the same way —
# masked if an earlier arm already settled it, otherwise live with every
# later sibling masked from here on. Same C++20-boundary-literal
# restriction as the ``#if`` form above.
_PP_ELIF_CPLUSPLUS_LESS_THAN_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_CPLUSPLUS_LESS_THAN_CPP20_TAIL + rb"[ \t]*$"
)
# ``#if defined(__cplusplus)``/``#if defined __cplusplus`` (the
# parenthesized *and* the equally-valid no-parens ``defined`` operator
# spelling — Codex review, ninth round)/``#if __cplusplus`` (bare, no
# comparison) are all semantically identical to ``#ifdef __cplusplus`` —
# unconditionally true for every ``-std=`` this heuristic could pick — so
# they must be excluded from the general guard pattern above the same way
# ``#ifdef __cplusplus`` already is, not masked as if circular (Codex
# review, eighth round). Anchored ``$`` so it only matches when this is
# the *entire* condition: ``#if defined(__cplusplus) && __cplusplus >=
# 202002L`` still has a genuine version comparison attached and stays
# mask-worthy.
_PP_DEFINED_CPLUSPLUS = (
    rb"defined[ \t]*(?:\([ \t]*__cplusplus[ \t]*\)|[ \t]+__cplusplus)"
)
# ``#if defined(__cplusplus) && !defined(SWIG)`` (Codex review, twentieth
# round) — a common real-world idiom wrapping a header's C++-only section
# so a non-C++ consumer (SWIG, Doxygen, ...) doesn't see it, not a genuine
# dialect/feature-test guard at all: the second conjunct doesn't reference
# __cplusplus/__cpp_* so its truth value has nothing to do with the -std=
# this heuristic picks, and this heuristic never *is* one of those non-C++
# consumers, so the whole condition is unconditionally true here just like
# the bare ``defined(__cplusplus)`` form above. The identifier is
# excluded from matching __cplusplus/__cpp_* itself (a negative lookahead)
# so a genuinely dialect-related second conjunct (``defined(__cplusplus)
# && !defined(__cpp_concepts)``) is not swept in here and stays
# mask-worthy, same as any other feature-test guard. Deliberately narrow
# (single ``&&``, one negated macro) rather than a full expression parser,
# matching this file's incremental-per-reported-case scope.
_PP_NOT_DEFINED_UNRELATED_MACRO = (
    rb"!defined[ \t]*(?:\([ \t]*(?!__cplusplus\b|__cpp_)\w+[ \t]*\)"
    rb"|[ \t]+(?!__cplusplus\b|__cpp_)\w+)"
)
_PP_CPLUSPLUS_ALWAYS_TRUE_TAIL = (
    rb"(?:"
    + _PP_DEFINED_CPLUSPLUS
    + rb"|__cplusplus"
    + rb"|(?:"
    + _PP_DEFINED_CPLUSPLUS
    + rb")[ \t]*&&[ \t]*"
    + _PP_NOT_DEFINED_UNRELATED_MACRO
    + rb")"
)
_PP_IF_CPLUSPLUS_ALWAYS_TRUE_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_CPLUSPLUS_ALWAYS_TRUE_TAIL + rb"[ \t]*$"
)
_PP_ELIF_CPLUSPLUS_ALWAYS_TRUE_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_CPLUSPLUS_ALWAYS_TRUE_TAIL + rb"[ \t]*$"
)
# The common shorthand spellings of a feature-test guard (Codex review,
# seventh round): ``#ifdef __cpp_concepts`` is exactly as circular as
# ``#if defined(__cpp_concepts)`` — masked for the same reason (the
# feature-guarded arm is untrustworthy; its ``#else`` fallback is treated
# as live, same as the general ``#if defined(__cpp_x)`` case).
# ``#ifndef __cplusplus`` is different and still mask-worthy the same
# way: castxml/clang always parse these headers in a C++-ish mode (see
# ``_CPP_PATTERNS`` above), so that branch is never reachable regardless
# of which ``-std=`` gets picked, exactly like ``#if 0``.
# ``#ifdef __cplusplus`` is deliberately *not* included here — unlike a
# version/feature-test comparison, it is unconditionally true for every
# ``-std=`` this heuristic could pick (castxml always defines it), so its
# content is a genuine, unconditional signal and must be scanned normally
# rather than masked away.
_PP_IFDEF_CPLUSPLUS_FEATURE_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*ifdef[ \t]+__cpp_\w+\b"
)
_PP_IFNDEF_CPLUSPLUS_PATTERN = re.compile(rb"^[ \t]*#[ \t]*ifndef[ \t]+__cplusplus\b")
# ``#ifdef __cplusplus`` itself (Codex review, twenty-first round): the
# reasoning above ("unconditionally true for every -std= this heuristic
# could pick") only holds once the language mode is *already* C++ --
# ``mask_cplusplus_defined_guards`` (see the docstring below) is for the
# one caller (deciding whether to treat an auto-detected header as C or
# C++ at all) where that assumption doesn't hold: in C mode __cplusplus
# is undefined, so this guard's content is exactly as unreachable there
# as any other feature-test guard, and forcing C++ purely because C++20
# syntax exists behind it would itself be the circular mistake this
# masking exists to avoid.
_PP_IFDEF_CPLUSPLUS_PATTERN = re.compile(rb"^[ \t]*#[ \t]*ifdef[ \t]+__cplusplus\b")
# ``#ifndef __cplusplus``/``#if !defined(__cplusplus)`` (Codex review,
# twenty-third round): the general mask-bucket's unconditional treatment
# of these ("always parsed in a C++-ish mode, so never reachable") is the
# same dialect-decision-only assumption as ``#ifdef __cplusplus`` above,
# and just as wrong for ``mask_cplusplus_defined_guards`` -- in C mode
# ``__cplusplus`` really is undefined, so this guarded arm (the C
# fallback) is exactly the reachable one there, and the ``#else`` (C++-
# only content) is the circular one instead. Opposite polarity from
# ``#ifdef __cplusplus`` for the same reason ``#ifndef __cpp_x`` is
# opposite from ``#ifdef __cpp_x``.
_PP_NOT_DEFINED_CPLUSPLUS = rb"![ \t]*" + _PP_DEFINED_CPLUSPLUS
_PP_IF_NOT_DEFINED_CPLUSPLUS_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_NOT_DEFINED_CPLUSPLUS + rb"[ \t]*$"
)
_PP_ELIF_NOT_DEFINED_CPLUSPLUS_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_NOT_DEFINED_CPLUSPLUS + rb"[ \t]*$"
)
# ``#ifndef __cpp_x`` is the *negated* mirror of ``#ifdef __cpp_x`` —
# logically ``#if defined(__cpp_x) ... [B] ... #else ... [A] ... #endif``
# with the arms swapped, so it needs the opposite polarity, not the same
# masking as ``#ifndef __cplusplus`` (Codex review, fourteenth round). The
# ``#ifndef``-guarded arm here is the feature-*absent* content — the
# portable/pre-C++20-safe fallback, exactly like the ``#else`` of the
# positive form — and must be the one trusted as live; the ``#else`` here
# is the feature-*present* content (typically the C++20-specific code,
# circular for the same reason the positive form's guarded arm is) and
# must be the one masked instead. Reusing the ``__cplusplus``-style
# dead-first treatment for this pattern got the polarity backwards: it
# masked the safe fallback and trusted the circular feature-present
# ``#else`` arm, so a header whose *only* C++20 construct sat behind such
# an ``#else`` forced ``-std=gnu++20`` purely because the guard's content
# was there — which could then break an unrelated, genuinely pre-C++20
# use of the same word as an ordinary identifier elsewhere in the header.
_PP_IFNDEF_CPP_FEATURE_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*ifndef[ \t]+__cpp_\w+\b"
)
# ``#if !defined(__cpp_x)``/``#if !defined __cpp_x`` (Codex review,
# eighteenth round) is just the ``defined``-operator spelling of
# ``#ifndef __cpp_x`` — same negated-mirror polarity, same reasoning as
# the comment above. Without this, the general ``__cplusplus``/``__cpp_*``
# guard pattern still matches it (it contains ``__cpp_x`` too) and applies
# the *positive*-form polarity instead: masking the guarded arm (the
# feature-absent, portable fallback) and trusting the ``#else`` (the
# circular feature-present content) -- backwards, exactly the same bug the
# ``#ifndef`` spelling above was fixed for. Anchored ``$`` so only a bare
# negated feature-test counts as the whole condition, matching how the
# other single-purpose guards here are deliberately narrow rather than a
# full expression parser. Checked ahead of the general branch below, the
# same way the ``#ifndef`` spelling doesn't need to be (it can never match
# the general branch's ``#if``-only pattern), since this spelling starts
# with ``#if`` and would otherwise be caught by it first.
_PP_NOT_DEFINED_CPP_FEATURE_TAIL = (
    rb"![ \t]*defined[ \t]*(?:\([ \t]*__cpp_\w+[ \t]*\)|[ \t]+__cpp_\w+)"
)
_PP_IF_NOT_DEFINED_CPP_FEATURE_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*if[ \t]+" + _PP_NOT_DEFINED_CPP_FEATURE_TAIL + rb"[ \t]*$"
)
# Same ``#elif`` companion pattern this file already adds for every other
# inverted-polarity ``#if`` spelling (the less-than-C++20 guard above),
# for a chain that opens on something else and reaches the negated
# feature-test guard as a later arm.
_PP_ELIF_NOT_DEFINED_CPP_FEATURE_GUARD_PATTERN = re.compile(
    rb"^[ \t]*#[ \t]*elif[ \t]+" + _PP_NOT_DEFINED_CPP_FEATURE_TAIL + rb"[ \t]*$"
)


#: The three stack frames a chain can open with, as
#: ``(recognized, masking, settled)`` — see :func:`_frame_for_chain_open`.
_FRAME_LIVE_SETTLED = (True, False, True)
_FRAME_MASKED = (True, True, False)
_FRAME_UNRECOGNIZED = (False, False, False)


@dataclass(frozen=True)
class _ChainPolicy:
    """The two masking-policy flags every chain-classifier rule consults.

    Bundled into one object so the rule tables below can share a single
    ``(line, policy)`` matcher signature; the flags themselves are documented
    on :func:`_strip_inactive_if_zero_blocks`, the only caller that sets them.
    """

    invert_dialect_fallback_guards: bool
    mask_cplusplus_defined_guards: bool


_ChainMatcher = Callable[[bytes, _ChainPolicy], bool]


def _opens_pre_cpp20_dialect_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#if __cplusplus < N`` — the pre-C++20-safe fallback arm.

    Opposite polarity from the general __cplusplus-guard rule: a less-than
    comparison's guarded arm is the pre-C++20-safe fallback, so it's scanned
    live and immediately settled so any #else (feature present, circular)
    stays masked -- ordered ahead of the general rule since it would otherwise
    also match. Skipped entirely when invert_dialect_fallback_guards is False
    (shadow-scan callers), falling through to the general rule instead -- see
    :func:`_strip_inactive_if_zero_blocks`.
    """
    return policy.invert_dialect_fallback_guards and bool(
        _PP_IF_CPLUSPLUS_LESS_THAN_PATTERN.match(line)
    )


def _opens_missing_feature_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#if !defined(__cpp_x)`` — the feature-absent fallback arm.

    Same inverted polarity as ``#ifndef __cpp_x`` below -- ordered ahead of the
    general rule since this spelling (unlike ``#ifndef``) starts with ``#if``
    and would otherwise be caught there first. Same shadow-scan exception as
    above.
    """
    return policy.invert_dialect_fallback_guards and bool(
        _PP_IF_NOT_DEFINED_CPP_FEATURE_GUARD_PATTERN.match(line)
    )


def _opens_missing_cplusplus_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#ifndef __cplusplus`` / ``#if !defined(__cplusplus)``, C-only arm.

    Opposite polarity from the general rule's unconditional treatment of the
    same spellings -- ordered ahead of it since it would otherwise also match.
    See the docstring above ``_PP_NOT_DEFINED_CPLUSPLUS``.
    """
    return policy.mask_cplusplus_defined_guards and bool(
        _PP_IFNDEF_CPLUSPLUS_PATTERN.match(line)
        or _PP_IF_NOT_DEFINED_CPLUSPLUS_PATTERN.match(line)
    )


def _opens_unreachable_or_circular_guard(line: bytes, policy: _ChainPolicy) -> bool:
    """``#if 0`` or a dialect/feature-test guard whose arm must not be scanned.

    The general masking rule: permanently-unreachable literals plus the
    circular guards (``__cplusplus``, ``__cpp_*``) whose own content must never
    be allowed to decide the ``-std=`` this heuristic is picking.
    """
    return bool(
        _PP_IF_ZERO_PATTERN.match(line)
        or _PP_IFDEF_CPLUSPLUS_FEATURE_GUARD_PATTERN.match(line)
        or _PP_IFNDEF_CPLUSPLUS_PATTERN.match(line)
        or (
            not policy.invert_dialect_fallback_guards
            and _PP_IFNDEF_CPP_FEATURE_GUARD_PATTERN.match(line)
        )
        or (
            policy.mask_cplusplus_defined_guards
            and _PP_IFDEF_CPLUSPLUS_PATTERN.match(line)
        )
        or (
            _PP_IF_CPLUSPLUS_GUARD_PATTERN.match(line)
            and (
                policy.mask_cplusplus_defined_guards
                or not _PP_IF_CPLUSPLUS_ALWAYS_TRUE_PATTERN.match(line)
            )
        )
    )


def _opens_unconditionally_true(line: bytes, policy: _ChainPolicy) -> bool:
    """``#if 1``/``#if true``, or ``#ifdef``/``#if defined(__cplusplus)``.

    ``#if defined(__cplusplus)``/``#ifdef __cplusplus``/bare ``#if __cplusplus``
    (Codex review, twenty-second round): tracked as *settled*-true here, the
    same way ``#if 1``/``#if true`` already is, not left as an unrecognized
    chain (whose #else/#elif arms then get scanned right along with this one,
    unmasked). Without this, a dual C/C++ header's ``#else`` (C-only) fallback
    -- e.g. a ``struct concept {};`` compatibility shim -- stayed visible to the
    shadow-name scan alongside a genuine C++20 declaration in the *active*
    ``#if`` arm, wrongly shadowing it. Elif-level already settles the equivalent
    ``#elif`` spelling this same way; this closes the gap for it as the chain's
    *opening* condition. The ``__cplusplus`` spellings are skipped when
    mask_cplusplus_defined_guards is True (the caller already masked that
    guard's own content in the general rule above, and never reaches here).
    """
    return bool(
        _PP_IF_TRUE_PATTERN.match(line)
        or (
            not policy.mask_cplusplus_defined_guards
            and (
                _PP_IFDEF_CPLUSPLUS_PATTERN.match(line)
                or _PP_IF_CPLUSPLUS_ALWAYS_TRUE_PATTERN.match(line)
            )
        )
    )


def _opens_ifndef_feature_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#ifndef __cpp_x`` — the feature-absent fallback arm.

    Opposite polarity from the general masking rule above: the
    ``#ifndef``-guarded arm (feature absent) is the trustworthy one here, so
    it's scanned live and immediately settled so any #else (feature present,
    circular) stays masked. Skipped for a shadow-scan caller -- already folded
    into the general mask rule above instead.
    """
    return policy.invert_dialect_fallback_guards and bool(
        _PP_IFNDEF_CPP_FEATURE_GUARD_PATTERN.match(line)
    )


#: Ordered ``(matcher, frame)`` rules for a chain-opening directive. Order is
#: load-bearing: every inverted-polarity spelling is tested ahead of the
#: general guard rule that would otherwise also match it.
_CHAIN_OPEN_RULES: tuple[tuple[_ChainMatcher, tuple[bool, bool, bool]], ...] = (
    (_opens_pre_cpp20_dialect_fallback, _FRAME_LIVE_SETTLED),
    (_opens_missing_feature_fallback, _FRAME_LIVE_SETTLED),
    (_opens_missing_cplusplus_fallback, _FRAME_LIVE_SETTLED),
    (_opens_unreachable_or_circular_guard, _FRAME_MASKED),
    (_opens_unconditionally_true, _FRAME_LIVE_SETTLED),
    # The one inverted-polarity rule that does *not* precede the general mask
    # rule, and safely so: the two are mutually exclusive by construction --
    # `_opens_unreachable_or_circular_guard` only tests
    # `_PP_IFNDEF_CPP_FEATURE_GUARD_PATTERN` when
    # `invert_dialect_fallback_guards` is False, and this rule only fires when
    # it is True, so neither can shadow the other whatever the order. Stated
    # here so a later edit to that rule's gating cannot silently change this
    # spelling's classification (CodeRabbit review).
    (_opens_ifndef_feature_fallback, _FRAME_LIVE_SETTLED),
)


def _frame_for_chain_open(line: bytes, policy: _ChainPolicy) -> list[bool]:
    """Classify a chain-opening ``#if``/``#ifdef``/``#ifndef`` directive.

    Returns the ``[recognized, masking, settled]`` stack frame this chain
    opens with. The caller has already established the chain is not itself
    nested inside dead code, so this answers only what *this* condition
    says. The rules are consulted in ``_CHAIN_OPEN_RULES`` order, which is
    load-bearing; an unrecognized condition falls through to the same
    conservative pass-through the top level always applied to an ``#if`` it
    can't evaluate.
    """
    for matches, frame in _CHAIN_OPEN_RULES:
        if matches(line, policy):
            return list(frame)
    return list(_FRAME_UNRECOGNIZED)


def _arm_settle(frame: list[bool]) -> None:
    """A decisive arm: flip which arm is masked, then settle the level.

    Every later sibling in the chain is unconditionally unreachable once an
    unconditionally-reachable arm has been seen, so masking resumes for them.
    """
    frame[1] = frame[2]
    frame[2] = True


def _arm_mask(frame: list[bool]) -> None:
    """A provably-unreachable or circular arm (``#elif 0``, dialect guard)."""
    frame[1] = True


def _arm_flip(frame: list[bool]) -> None:
    """A plain ``#elif``/``#else``: masked iff the level has already settled."""
    frame[1] = frame[2]


def _arm_is_pre_cpp20_dialect_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#elif __cplusplus < N``.

    Same inverted polarity as the ``#if``-opening case, ordered ahead of the
    general rule below since it would otherwise also match. Same shadow-scan
    exception as the ``#if``-opening case: falls through to the general rule
    (mask) when invert_dialect_fallback_guards is False.
    """
    return policy.invert_dialect_fallback_guards and bool(
        _PP_ELIF_CPLUSPLUS_LESS_THAN_PATTERN.match(line)
    )


def _arm_is_missing_feature_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#elif !defined(__cpp_x)``.

    Same inverted polarity as ``#if !defined(__cpp_x)``, ordered ahead of the
    general rule since it would otherwise also match. Same shadow-scan
    exception.
    """
    return policy.invert_dialect_fallback_guards and bool(
        _PP_ELIF_NOT_DEFINED_CPP_FEATURE_GUARD_PATTERN.match(line)
    )


def _arm_is_missing_cplusplus_fallback(line: bytes, policy: _ChainPolicy) -> bool:
    """``#elif !defined(__cplusplus)``.

    Opposite polarity from the general rule's unconditional
    ``!defined(__cplusplus)`` treatment, ordered ahead of it since it would
    otherwise also match. Same reasoning as the ``#if``-opening case.
    """
    return policy.mask_cplusplus_defined_guards and bool(
        _PP_ELIF_NOT_DEFINED_CPLUSPLUS_PATTERN.match(line)
    )


def _arm_is_unreachable_or_circular_guard(line: bytes, policy: _ChainPolicy) -> bool:
    """``#elif 0``/``#elif false``, or a circular dialect/feature-test guard."""
    return bool(
        _PP_ELIF_ZERO_PATTERN.match(line)
        or (
            _PP_ELIF_CPLUSPLUS_GUARD_PATTERN.match(line)
            and (
                policy.mask_cplusplus_defined_guards
                or not _PP_ELIF_CPLUSPLUS_ALWAYS_TRUE_PATTERN.match(line)
            )
        )
    )


def _arm_is_unconditionally_true(line: bytes, policy: _ChainPolicy) -> bool:
    """``#elif 1``/``#elif true``, or ``#elif defined(__cplusplus)``."""
    return bool(
        _PP_ELIF_TRUE_PATTERN.match(line)
        or (
            not policy.mask_cplusplus_defined_guards
            and _PP_ELIF_CPLUSPLUS_ALWAYS_TRUE_PATTERN.match(line)
        )
    )


def _arm_is_plain_alternative(line: bytes, policy: _ChainPolicy) -> bool:
    """Any other ``#elif``/``#else`` — a condition this heuristic can't read."""
    return bool(_PP_ELIF_PATTERN.match(line) or _PP_ELSE_PATTERN.match(line))


#: Ordered ``(matcher, mutator)`` rules for one arm of an already-open chain.
#: Same load-bearing ordering rationale as ``_CHAIN_OPEN_RULES``.
_OPEN_ARM_RULES: tuple[tuple[_ChainMatcher, Callable[[list[bool]], None]], ...] = (
    (_arm_is_pre_cpp20_dialect_fallback, _arm_settle),
    (_arm_is_missing_feature_fallback, _arm_settle),
    (_arm_is_missing_cplusplus_fallback, _arm_settle),
    (_arm_is_unreachable_or_circular_guard, _arm_mask),
    (_arm_is_unconditionally_true, _arm_settle),
    (_arm_is_plain_alternative, _arm_flip),
)


def _line_for_open_arm(line: bytes, frame: list[bool], policy: _ChainPolicy) -> bytes:
    """Advance the innermost open chain by one line; return what to emit.

    *frame* is mutated in place: an ``#elif``/``#else`` arm changes which of
    this level's arms is currently masked, and may settle the level. A
    non-directive line inside the chain matches no rule and is emitted or
    blanked purely by the frame's current masking state.
    """
    if not frame[0]:
        # This level's own #if condition was unrecognized — its
        # #elif/#else arms are never specially interpreted,
        # exactly like the top level's unrecognized-chain
        # pass-through.
        return line
    for matches, advance in _OPEN_ARM_RULES:
        if matches(line, policy):
            advance(frame)
            break
    return b"" if frame[1] else line


def _strip_inactive_if_zero_blocks(
    content: bytes,
    *,
    invert_dialect_fallback_guards: bool = True,
    mask_cplusplus_defined_guards: bool = False,
) -> bytes:
    """Blank out unreachable arms of an ``#if 0``/``#if false`` chain.

    A disabled compatibility stub (``#if 0\\nstruct consteval {};\\n#endif``)
    must not shadow a genuine ``consteval``/``constinit``/``concept`` keyword
    used as an identifier elsewhere in the header (Codex review) — the
    ``*_type_shadowed`` scan otherwise sees the inactive type definition and
    wrongly treats a real C++20 declaration as ambiguous. Line count is
    preserved so this can be composed with the rest of the shadow scan.

    Only *unreachable* arms are masked. ``#if 0``/``#elif 0``/``#elif
    false`` are permanently unreachable, exactly like their guard says
    (Codex review, second round). An ``#else``/``#elif`` whose own
    condition isn't a recognizable ``0``/``1``/``false``/``true`` literal
    has a condition this heuristic can't evaluate, so it's treated as
    possibly-reachable (unmasked) — the conservative direction, since it
    can only request C++20 mode when not strictly needed, never the
    reverse. But once an ``#elif 1``/``#elif true`` arm is seen, it is
    *unconditionally* reachable in every build configuration, which makes
    every later sibling arm in that same chain unconditionally
    *unreachable* — masking must resume for them too (Codex review, third
    round), not stay lifted for the rest of the chain the way it correctly
    does after a merely-unevaluated condition. The same applies when the
    chain *opens* on an unconditionally-true literal (``#if 1``/``#if
    true``, Codex review, tenth round): that arm is scanned normally like
    any other unmasked arm, but the chain is entered and immediately
    settled so a dead ``#else``/``#elif`` sibling doesn't fall through to
    plain pass-through scanning — a real C++20 construct sitting only in
    that dead sibling would otherwise force ``-std=gnu++20`` onto a header
    whose live code never needed it, breaking any active use of a
    soon-to-be-reserved word as an ordinary identifier.

    An ``#if``/``#elif`` guarded on ``__cplusplus`` or a standard
    feature-test macro (``__cpp_concepts``, ``__cpp_consteval``, ...) is
    masked the same way as ``#if 0`` (Codex review, sixth round) — not
    because it's provably false, but because it's circular: whichever
    ``-std=`` this heuristic ends up choosing is exactly what decides
    whether the guard is reachable, so its content must never be allowed
    to drive that same choice. Forcing C++20 mode purely because a
    ``#if __cplusplus >= 202002L`` block contains C++20 syntax doesn't
    just needlessly activate that block — passing ``-std=gnu++20`` turns
    every *unguarded* pre-C++20 use of ``consteval``/``constinit``/
    ``concept``/``requires`` as an ordinary identifier elsewhere in the
    same header into a reserved-word parse error too.

    A nested ``#if`` inside a *reachable* arm gets the exact same
    treatment, recursively, via an explicit per-depth stack rather than a
    flat "opaque nested" counter (Codex review, eleventh round). A flat
    counter that just tracks depth to find the matching ``#endif`` cannot
    tell a nested ``#if 0`` compatibility stub from nested live code, so a
    dead nested construct inside an otherwise-live arm previously leaked
    into the shadow/requirements scan (or, symmetrically, a live nested
    construct inside a dead-sibling ancestor was wrongly treated as
    reachable). Each stack frame independently recognizes zero/true/
    feature-guard conditions the same way the top level does; a frame
    whose own condition isn't recognizable stays fully unmasked (pass
    through, the same conservative default the top level already applies
    to an unrecognized ``#if``), but any frame with an unreachable
    *ancestor* is unconditionally unreachable regardless of its own
    condition.

    ``invert_dialect_fallback_guards`` (default ``True``) controls the
    ``__cplusplus < N``/``#ifndef __cpp_x``/``#if !defined(__cpp_x)``
    inverted-polarity treatment described above. It must be **disabled**
    (``False``) for masking content that feeds the *shadow*-name scan
    (Codex review, nineteenth round): that scan asks "does 'concept' name
    an ordinary pre-C++20 compatibility type *in code that would still be
    reachable if C++20 were chosen*" — the inverted polarity is exactly
    backwards for that question, since it treats the guarded arm of e.g.
    ``#if __cplusplus < 202002L`` as live specifically *because* it is a
    pre-C++20 fallback shim, i.e. content that goes away once C++20 is
    chosen. Left enabled there, a ``struct concept {};`` compatibility
    shim confined entirely to such a guard would wrongly shadow a genuine
    ``concept`` declaration in another header of the same aggregate,
    permanently keeping the whole translation unit off ``-std=gnu++20``
    even though the shim itself would never coexist with that declaration
    under any dialect choice. The *requirements* scan (deciding whether
    unconditionally-reachable code needs C++20) is unaffected and keeps
    the default ``True``.

    ``mask_cplusplus_defined_guards`` (default ``False``) additionally
    masks ``#if __cplusplus``/``#ifdef __cplusplus``/``#if
    defined(__cplusplus)`` (bare or with the SWIG-idiom ``&&
    !defined(X)`` extension) instead of treating them as an
    unconditionally-true, unmasked signal. Those forms are exempted by
    default because *given the header is already being parsed as C++*,
    ``__cplusplus`` really is always defined — correct for deciding the
    C++ **dialect** (which ``-std=gnu++NN``). It must be **enabled**
    (``True``) for the one caller that instead uses this scan's result to
    decide whether to treat an auto-detected header as C or C++ **at
    all** (Codex review, twenty-first round): in C mode ``__cplusplus``
    is undefined, so a ``consteval``/``concept``/... construct confined
    to such a guard is not actually reachable there, and letting it force
    C++ mode is the exact same circularity this function exists to
    prevent elsewhere — worse, it then turns an *active*, unguarded use
    of the same word as an ordinary C identifier elsewhere in the header
    into a reserved-word parse error once C++20 mode is wrongly forced.
    """
    lines = content.split(b"\n")
    out: list[bytes] = []
    # One entry per currently-open #if, innermost last. Each entry is
    # [recognized, masking, settled]:
    #   recognized: this level's own condition matched one of the
    #     zero/true/feature-guard patterns, so its later #elif/#else arms
    #     are interpreted the same way the top level interprets them.
    #   masking: the arm *currently open at this level* should be
    #     blanked, judged purely by this level's own condition (ignoring
    #     ancestors).
    #   settled: an unconditionally-true arm already fired at this level,
    #     so every later sibling arm here is unconditionally unreachable.
    stack: list[list[bool]] = []
    # Built once for the whole scan, not per line: both flags are constant for
    # this call, and `_line_for_open_arm` runs once per line inside every chain
    # of every scanned header (CodeRabbit review).
    policy = _ChainPolicy(invert_dialect_fallback_guards, mask_cplusplus_defined_guards)

    def unreachable() -> bool:
        # Unreachable if this level or any ancestor is masking — a dead
        # ancestor arm makes everything nested inside it dead too,
        # regardless of the nested content's own condition.
        return any(frame[1] for frame in stack)

    for raw_line in lines:
        # A CRLF source (or a CRLF-normalizing text-mode write, e.g. the
        # test suite's Path.write_text() on Windows) leaves a trailing "\r"
        # on every line after splitting on "\n" alone — which the trailing
        # "$" in these patterns then fails to match, since "\r" isn't in
        # their "[ \t]*" tail (Windows CI regression). Strip it before
        # matching; harmless on genuine LF input.
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line

        if _PP_IF_OPEN_PATTERN.match(line):
            if unreachable():
                # Already inside dead code — this nested chain's own
                # condition is irrelevant either way; track it only to
                # find its matching #endif.
                stack.append([False, True, True])
                out.append(b"")
                continue
            frame = _frame_for_chain_open(line, policy)
            stack.append(frame)
            # Every recognized opener is itself blanked; only the
            # unrecognized pass-through frame keeps its own directive line.
            out.append(line if frame == [False, False, False] else b"")
            continue

        if stack and _PP_ENDIF_PATTERN.match(line):
            stack.pop()
            out.append(b"" if unreachable() else line)
            continue

        if stack:
            if any(f[1] for f in stack[:-1]):
                # A dead ancestor arm makes this line dead regardless of
                # what the innermost frame's own condition says.
                out.append(b"")
                continue
            out.append(
                _line_for_open_arm(line, stack[-1], policy)
            )
            continue

        out.append(line)
    return b"\n".join(out)

# SPDX-License-Identifier: Apache-2.0
"""Small predicates for forwarded compiler dialect options."""

from __future__ import annotations

import os
import shlex

#: Matches ``shlex.shlex``'s own POSIX-mode default (space/tab/CR/LF) --
#: kept as an explicit constant so :func:`_split_gcc_options_windows`'s
#: hand-rolled tokenizer stays byte-for-byte compatible with plain
#: ``shlex.split`` wherever the two aren't deliberately diverging (see
#: that function's docstring for what does diverge and why).
_WHITESPACE = " \t\r\n"


def split_gcc_options(text: str) -> list[str]:
    """Quote-aware split of a ``--gcc-options``-style compiler-flags string
    (e.g. ``-DMSG="hello world" -DOK=1``).

    Five earlier revisions of this helper each traded away a real,
    independently-confirmed case (Codex review, every example below
    verified against real Python ``shlex`` output or this function
    directly):

    1. ``shlex.split(text, posix=os.name != "nt")`` -- the original,
       pre-existing pattern this helper replaced everywhere -- picked
       quote-collapsing behavior on POSIX and backslash-preserving
       behavior on Windows, but never both on the same platform: a quoted
       value with an embedded space (``-DMSG="hello world"``) silently
       split into malformed tokens on Windows specifically (the original
       regression this whole helper exists to fix; see
       ``tests/test_action_compile_context_parity.py::
       TestCompileContextForwardingParity::test_gcc_options_quoted_value_stays_one_token``).
    2. A hand-rolled ``shlex.shlex(text, posix=True)`` with ``escape=""``
       disabled everywhere, to *also* preserve a literal Windows path's
       backslashes (``-IC:\\mypath\\include``) unconditionally -- broke a
       real ``\\``-escaped character inside a quoted value
       (``-DVERSION=\\"1.2\\"``) and left ``shlex``'s default
       ``#``-starts-a-comment behavior active, silently truncating any
       token containing ``#`` (``-I/build/#generated``) and dropping every
       flag after it.
    3. Plain ``shlex.split(text, posix=True)`` everywhere -- fixed both of
       #2's regressions, but real POSIX escaping treats *any* unquoted
       backslash as an escape character, so an ordinary unquoted Windows
       path (``-IC:\\mypath\\include``, no quotes at all -- the single most
       common real shape this flag carries on Windows) got its backslashes
       silently eaten (``-IC:mypathinclude``), corrupting the include path
       for every migrated compiler command.
    4. A hand-rolled tokenizer (see :func:`_split_gcc_options_windows`
       below) applied unconditionally on *every* platform, escaping a
       backslash only before a quote character or whitespace -- fixed #3's
       Windows-path corruption, but item #1's original, always-correct
       POSIX behavior (real ``shlex``, honoring *every* backslash escape,
       e.g. ``-DVAR=\\$HOME`` -> ``-DVAR=$HOME``, ``-DREGEX=a\\*b`` ->
       ``-DREGEX=a*b``) never needed fixing in the first place -- #1's bug
       was Windows-only (``posix=False`` there, not POSIX's own
       ``posix=True``), so applying a Windows-shaped compromise to POSIX
       too was an unforced, unnecessary change of real, working behavior.
    5. The same tokenizer, now correctly platform-gated to Windows only,
       but still escaping a backslash before whitespace (kept from #4, to
       *also* satisfy real POSIX's ``-DMSG=hello\\ world`` -> ``-DMSG=hello
       world`` idiom even though the POSIX branch already handles real
       POSIX input on its own) -- created a genuine Windows-specific
       ambiguity with the *far* more common shape of a path ending in a
       trailing directory separator right before the next flag
       (``-IC:\\sdk\\ -DOK=1``): the separator and the following space were
       swallowed into one corrupted token instead of staying two separate
       ones. Since nothing on the Windows branch depends on replicating a
       POSIX-only escaping idiom real Windows shells don't even use,
       :func:`_split_gcc_options_windows` no longer treats whitespace as
       escapable at all -- only a quote character, which cannot be
       confused with a real, unescaped path component the way whitespace
       can.
    6. The same tokenizer, still treating a bare ``'`` outside quotes as a
       real POSIX single-quote grouping delimiter (matching item #3's
       POSIX branch) -- but a Windows path or filename can legally contain
       a literal apostrophe (e.g. ``O'Brien``), which POSIX single-quote
       parsing is not: an unquoted ``-IC:\\Users\\O'Brien\\include`` opened
       a quoted region at the apostrophe and then found no closing ``'``,
       raising ``ValueError`` and aborting header extraction outright, and
       a value like ``-DCHAR='x'`` (a real, if unusual, C character-
       constant macro value) silently lost its quote characters, changing
       the macro's meaning. Unlike ``"`` (illegal in every Windows
       filename, so treating it as a real delimiter never conflicts with
       genuine unescaped path content), ``'`` is an ordinary, legal
       Windows path/filename character with no POSIX-shell-adjacent
       meaning on Windows at all (``cmd.exe``/PowerShell don't treat it
       specially either) -- so :func:`_split_gcc_options_windows` no
       longer treats ``'`` as special in any way, outside or inside
       double quotes: it is always literal, never a grouping delimiter
       and never escapable (escaping it would be meaningless once it
       carries no special meaning to escape).
    7. The double-quote branch still followed real POSIX double-quote
       escaping unconditionally, which collapses ``\\\\`` to a single
       ``\\`` -- but a quoted Windows UNC path (``-I"\\\\server\\share
       path\\include"``) legitimately starts with a literal two-backslash
       prefix, and that collapse silently turned it into a single-slash,
       non-UNC path the compiler can't resolve. Since real POSIX escaping
       inside quotes is not something anything on this Windows-only branch
       needs to replicate for its own sake (the same reasoning item #5
       already applied to backslash-before-whitespace), only a backslash
       immediately escaping the closing quote character itself is treated
       as an escape now; any other backslash inside quotes -- including a
       doubled one -- is left completely literal, so ``\\\\server\\share``
       survives intact.

    The fix is not a cleverer single grammar (#2-#4 each tried and
    regressed something with one) -- it's recognizing that #1's underlying
    ``posix=os.name != "nt"`` branch was never wrong for choosing to
    special-case Windows; it only implemented the Windows branch *badly*
    (``posix=False`` skips quote-parsing and escaping almost entirely,
    which is what made the original CI-failing quoted-value test fail).
    So this keeps the platform branch, but fixes what's actually inside
    it: POSIX still gets plain, unmodified ``shlex.split(text,
    posix=True)`` (identical to every ``gcc_options``-consuming call site's
    historical behavior on Linux/macOS -- zero behavior change, so nothing
    that already worked there can regress), and only Windows gets the
    hand-rolled :func:`_split_gcc_options_windows`, which fixes the
    original quoted-value bug *and* preserves an unquoted Windows path's
    backslashes -- both real improvements over Windows's own historical
    ``posix=False`` behavior, which supported neither.

    Raises ``ValueError`` on malformed input (e.g. an unbalanced quote),
    the same way ``shlex.split`` does -- callers that need to tolerate
    that already catch it.
    """
    if os.name == "nt":
        return _split_gcc_options_windows(text)
    return shlex.split(text, posix=True)


def _split_gcc_options_windows(text: str) -> list[str]:
    """The Windows-only half of :func:`split_gcc_options` -- see that
    function's docstring (item #4) for why this exists as a separate,
    platform-gated branch rather than applying everywhere.

    The general case is genuinely ambiguous: an unquoted ``\\`` immediately
    before an ARBITRARY character could mean "Windows path separator" or
    "POSIX escape of the next character," and no rule can read both from
    the character alone. An earlier revision of this function also treated
    a backslash before whitespace as an escape (keeping the space as part
    of the current token, matching real ``shlex``'s ``-DMSG=hello\\
    world`` -> ``-DMSG=hello world``) -- but real POSIX usage of that idiom
    is already fully handled by :func:`split_gcc_options`'s separate POSIX
    branch (this function is only ever reached when ``os.name == "nt"``),
    so nothing depends on this function replicating it, and doing so
    created a real, common Windows-specific ambiguity a review round
    caught: an ordinary Windows path ending in a trailing directory
    separator right before the next flag (``-IC:\\sdk\\ -DOK=1``) hits the
    identical backslash-before-whitespace shape, so the separator and the
    space were swallowed into one corrupted token instead of staying two.
    Unlike that case, escaping a double-quote character is unambiguous
    with an ordinary Windows path component -- ``"`` is illegal in every
    Windows filename, so a real path never legally contains an unescaped
    one to begin with, and never ends in one the way it routinely ends in
    a directory separator. A single quote (``'``) gets no such exception
    at all (see item #6 above) -- it is a completely ordinary, legal path
    character on Windows with no special meaning, unlike on POSIX. So
    outside quotes, a backslash immediately followed by ``"`` escapes
    exactly that one character (consumed together, dropping the
    backslash, keeping the literal quote character as part of the current
    token); a backslash followed by anything else -- an ordinary path
    character, whitespace, ``'``, end of string, or another backslash --
    is left completely untouched, both characters preserved literally one
    at a time (matching the pre-existing, already-correct Windows behavior
    for this exact shape, before this function existed to fix the actual
    quoted-value bug). Verified against real ``shlex.split`` output where
    a comparison applies: ``-DVERSION=\\"1.2\\"`` -> one token
    ``-DVERSION="1.2"`` (matches plain ``shlex``); ``-IC:\\mypath\\include``
    and ``-IC:\\sdk\\ -DOK=1`` -> unchanged, backslashes and trailing
    separators intact, two tokens each where a space is a real separator
    (diverging from plain ``shlex``, which would corrupt both -- the whole
    reason this function exists); ``-IC:\\Users\\O'Brien\\include`` ->
    unchanged, one token, apostrophe intact (also diverging from plain
    ``shlex``, which would raise ``ValueError`` for an unterminated
    quote). Inside double quotes, only a backslash immediately escaping
    the closing quote character is special (``\\"`` -> ``"``); any other
    backslash -- including a doubled one, e.g. a quoted UNC path's
    ``\\\\server\\share`` prefix -- is left completely literal rather than
    collapsed the way real POSIX double-quote rules would (``'`` still
    always literal, matching the unquoted case). This is a deliberate
    divergence from plain ``shlex`` (see item #7 above) precisely because
    a quoted Windows path is still a Windows path, not an opt-in to POSIX
    backslash-collapsing semantics the way it would be on a POSIX shell.
    """
    tokens: list[str] = []
    current: list[str] = []
    have_current = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _WHITESPACE:
            if have_current:
                tokens.append("".join(current))
                current = []
                have_current = False
            i += 1
            continue
        # Outside any quote, a backslash escapes exactly a following
        # double-quote character (dropping the backslash, keeping the
        # literal quote character as part of the current token). Anything
        # else after the backslash -- an ordinary path character,
        # whitespace, a single quote, another backslash, or end of string
        # -- is left untouched, so an unquoted Windows path's backslashes
        # (including a trailing directory separator right before the next
        # flag) survive intact (see the docstring above for why whitespace
        # and ``'`` are deliberately excluded here).
        if ch == "\\" and i + 1 < n and text[i + 1] == '"':
            have_current = True
            current.append(text[i + 1])
            i += 2
            continue
        have_current = True
        # A bare single quote is never special on Windows -- unlike POSIX,
        # it's an ordinary, legal path/filename character (see the
        # docstring above), so it falls through to the plain "ordinary
        # character" branch at the end of this loop, same as any letter.
        if ch == '"':
            i += 1
            while True:
                if i >= n:
                    raise ValueError("No closing quotation")
                c2 = text[i]
                if c2 == '"':
                    i += 1
                    break
                # Only a backslash immediately escaping the closing-quote
                # character itself is special here -- a bare `\\` (e.g. the
                # leading pair of a UNC path, `\\server\share`) is left as
                # two literal backslashes rather than collapsed to one (see
                # item #7 in the docstring above for why this diverges from
                # plain shlex's real POSIX double-quote rule, which treats
                # `\\` -> `\` unconditionally).
                if c2 == "\\" and i + 1 < n and text[i + 1] == '"':
                    current.append(text[i + 1])
                    i += 2
                    continue
                current.append(c2)
                i += 1
            continue
        # Ordinary character outside any quote, backslash included -- always
        # literal, so an unquoted Windows path's backslashes survive intact.
        current.append(ch)
        i += 1
    if have_current:
        tokens.append("".join(current))
    return tokens


def has_explicit_std(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> bool:
    """Return whether forwarded options explicitly select any language standard."""
    if gcc_options and ("-std=" in gcc_options or "/std:" in gcc_options):
        return True
    return any(("-std=" in token or "/std:" in token) for token in gcc_option_tokens)


def has_explicit_cpp_std(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> bool:
    """Return whether forwarded options explicitly select a C++ dialect."""
    tokens = list(gcc_option_tokens)
    if gcc_options:
        tokens.extend(split_gcc_options(gcc_options))
    for token in tokens:
        normalized = token.lower()
        if normalized.startswith("--"):
            # GCC/Clang accept the GNU long-option spelling (--std=c++17) as
            # an alias for -std=c++17; strip one dash so both are recognized.
            normalized = normalized[1:]
        if normalized.startswith("-std=") and "++" in normalized.partition("=")[2]:
            return True
        if normalized.startswith("/std:c++"):
            return True
    return False


def explicit_language_standard(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> str | None:
    """Return the last explicitly forwarded ``-std=``/``--std=``/``/std:``
    value, or ``None`` if forwarded options select none (ADR-050 D1's
    ``language_standard`` profile field).

    Last-wins, matching real compiler flag precedence — a later ``-std=``
    overrides an earlier one on the same command line. ``gcc_options`` is
    split and placed before ``gcc_option_tokens``, mirroring the actual
    frontend command lines built in ``dumper_ast_config.py`` (both castxml
    and clang append ``gcc_options`` first, then ``gcc_option_tokens``), so
    a later token in ``gcc_option_tokens`` correctly wins over an earlier
    one in ``gcc_options``. Deliberately does **not** reconstruct a
    frontend's own auto-injected default (e.g. castxml forcing
    ``-std=gnu11``/``-std=gnu++20`` when the caller supplied none — see
    ``dumper_ast_config.py``'s ``_build_castxml_cmd``); only what the
    caller actually asked for.
    """
    tokens: list[str] = []
    if gcc_options:
        try:
            tokens = split_gcc_options(gcc_options)
        except ValueError:
            # Malformed --gcc-options (e.g. an unbalanced quote) must not
            # abort the dump (Codex review, PR #624 follow-up, same rule
            # already applied to dumper_contract.py's own shlex.split call):
            # this is the ADR-050 profile-fingerprint path, invoked
            # unconditionally on every header-based dump, so a crash here
            # would be a new failure mode a pre-ADR-050 dump never had.
            pass
    tokens.extend(gcc_option_tokens)
    value: str | None = None
    for token in tokens:
        normalized = token[1:] if token.startswith("--std=") else token
        if normalized.startswith("-std="):
            value = normalized.partition("=")[2]
        elif normalized.lower().startswith("/std:"):
            value = normalized.partition(":")[2]
    return value


def language_standard_field(
    lang: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    *,
    resolved_standard: str | None = None,
) -> str | None:
    """ADR-050 D1's ``language_standard`` profile field: combines the
    explicit ``--lang`` mode (if any) with the dump's actual C/C++ standard.

    A same-executable, no-``-std=`` dump differing only by ``lang="c"`` vs.
    ``lang="c++"`` must still fingerprint differently — the actual frontend
    command forces a different language mode (``-x c`` vs. C++ default)
    regardless of whether an explicit standard was also given (Codex
    review, PR #624 follow-up).

    *resolved_standard*, when given, is ``AbiSnapshot.ast_resolved_standard``
    (schema v15, P1 toolchain-provenance audit) — the frontend's own
    *resolved* standard, which is a strict superset of the explicit-``-std=``
    value this function used to fall back to alone: it already reproduces
    that value verbatim when one was given, but also captures the case this
    function's own docstring used to flag as a gap — pure content-based
    auto-detection (no explicit ``--lang``/``-std=``, header content alone
    triggering the C++20 requires/concept heuristic's forced ``gnu++20``).
    Without this, two dumps that differ only by one side's headers silently
    triggering that heuristic shared an identical ``profile_fingerprint``
    despite having been parsed under genuinely different dialects — the
    comparability gate (:func:`abicheck.comparability.check_contracts_comparable`)
    had nothing to catch that divergence on. Preferred over the raw
    explicit-``-std=`` parse whenever given; falls back to the old
    explicit-only behaviour when ``None`` (a non-header dump, or a caller
    that hasn't threaded the resolved value through).
    """
    lang_mode = (lang or "").strip().lower()
    standard = resolved_standard or explicit_language_standard(
        gcc_options, gcc_option_tokens
    )
    if lang_mode and standard:
        return f"{lang_mode}:{standard}"
    return standard or (lang_mode or None)

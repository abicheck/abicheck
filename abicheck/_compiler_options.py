# SPDX-License-Identifier: Apache-2.0
"""Small predicates for forwarded compiler dialect options."""

from __future__ import annotations

#: Matches ``shlex.shlex``'s own POSIX-mode default (space/tab/CR/LF) --
#: kept as an explicit constant so :func:`split_gcc_options`'s hand-rolled
#: tokenizer stays byte-for-byte compatible with plain ``shlex.split``
#: wherever the two aren't deliberately diverging (see that function's
#: docstring for what does diverge and why).
_WHITESPACE = " \t\r\n"


def split_gcc_options(text: str) -> list[str]:
    """Quote-aware split of a ``--gcc-options``-style compiler-flags string
    (e.g. ``-DMSG="hello world" -DOK=1``).

    Three earlier revisions of this helper each traded away a real,
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
       disabled, to *also* preserve a literal Windows path's backslashes
       (``-IC:\\mypath\\include``) unconditionally -- broke a real
       ``\\``-escaped character inside a quoted value
       (``-DVERSION=\\"1.2\\"``) and left ``shlex``'s default
       ``#``-starts-a-comment behavior active, silently truncating any
       token containing ``#`` (``-I/build/#generated``) and dropping every
       flag after it.
    3. Plain ``shlex.split(text, posix=True)`` -- fixed both of #2's
       regressions, but real POSIX escaping treats *any* unquoted
       backslash as an escape character, so an ordinary unquoted Windows
       path (``-IC:\\mypath\\include``, no quotes at all -- the single most
       common real shape this flag carries on Windows) got its backslashes
       silently eaten (``-IC:mypathinclude``), corrupting the include path
       for every migrated compiler command.

    The general case is genuinely ambiguous: an unquoted ``\\`` immediately
    before an ARBITRARY character could mean "Windows path separator" or
    "POSIX escape of the next character," and no rule can read both from
    the character alone. But the two *specific* escape uses real POSIX
    shells (and item #1's own pre-existing Windows behavior for #2's own
    cited examples) actually need outside quotes are narrower than "escape
    anything": escaping a quote character so it doesn't open real quoting
    (``-DVERSION=\\"1.2\\"``), and escaping whitespace so it doesn't end
    the current token (``-DMSG=hello\\ world``). Neither of those two cases
    can be confused with an ordinary Windows path component -- a real path
    never needs literal quote or whitespace characters embedded via
    backslash, since those aren't legal unescaped/unquoted path characters
    to begin with. So outside quotes, a backslash immediately followed by
    ``"``, ``'``, or whitespace escapes exactly that one character
    (consumed together, dropping the backslash, keeping the literal
    character -- including keeping an escaped space as part of the current
    token rather than ending it); a backslash followed by anything else
    (an ordinary path character, end of string, or another backslash) is
    left completely untouched, both characters preserved literally one at
    a time. This satisfies every example from all three items above
    simultaneously (verified against real ``shlex.split`` output where a
    comparison applies): ``-DVERSION=\\"1.2\\"`` -> one token
    ``-DVERSION="1.2"`` (matches plain ``shlex``); ``-DMSG=hello\\ world``
    -> one token ``-DMSG=hello world`` (matches plain ``shlex``);
    ``-IC:\\mypath\\include`` -> unchanged, backslashes intact (matches
    item #1's pre-existing Windows behavior, diverging from plain
    ``shlex``, which would corrupt it). Inside quotes, escaping follows
    real POSIX rules unconditionally (double quotes: ``\\"`` -> ``"``,
    ``\\\\`` -> ``\\``, any other ``\\x`` stays literal; single quotes: no
    escaping at all) -- matching plain ``shlex`` exactly, since a quoted
    Windows path is already an explicit, deliberate opt-in to POSIX
    quoting semantics.

    Raises ``ValueError`` on an unterminated quote, the same way
    ``shlex.split`` does -- callers that need to tolerate that already
    catch it.
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
        # Outside any quote, a backslash escapes exactly a following quote
        # character or whitespace character (dropping the backslash,
        # keeping the literal character as part of the current token --
        # including an escaped space, which does NOT end the token here).
        # Anything else after the backslash -- an ordinary path character,
        # another backslash, or end of string -- is left untouched, so an
        # unquoted Windows path's backslashes survive intact (see the
        # docstring above for why only these two escapes are honored).
        if ch == "\\" and i + 1 < n and text[i + 1] in ('"', "'", *_WHITESPACE):
            have_current = True
            current.append(text[i + 1])
            i += 2
            continue
        have_current = True
        if ch == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ValueError("No closing quotation")
            current.append(text[i + 1 : end])
            i = end + 1
            continue
        if ch == '"':
            i += 1
            while True:
                if i >= n:
                    raise ValueError("No closing quotation")
                c2 = text[i]
                if c2 == '"':
                    i += 1
                    break
                # POSIX double-quote rule: only a backslash escaping the
                # quote itself or another backslash is special; any other
                # `\x` (e.g. the literal-path case) is kept as-is, matching
                # plain shlex exactly (see the docstring above).
                if c2 == "\\" and i + 1 < n and text[i + 1] in ('"', "\\"):
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

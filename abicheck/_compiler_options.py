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

    Implements the standard Windows command-line backslash/quote parsing
    rule (the same one ``CommandLineToArgvW`` and real MSVC-family command
    lines use, not a bespoke grammar): backslashes are literal unless they
    immediately precede a double-quote character, in which case a *run* of
    ``k`` consecutive backslashes followed by ``"`` contributes ``k // 2``
    literal backslashes, and then:

    - if ``k`` is even, the ``"`` is a real delimiter (toggles whether the
      parser is currently "inside quotes");
    - if ``k`` is odd, the trailing backslash escapes the ``"``, which is
      appended as a literal character instead of toggling anything.

    A single, unified "in quotes" boolean governs the whole string (not a
    separate outside-quotes/inside-quotes code path, unlike this
    function's earlier item #4-#7 revisions) -- a bare ``"`` with no
    preceding backslash is just the ``k == 0`` case of the same rule, and
    always toggles. Whitespace is a token separator only while not inside
    quotes; a bare ``'`` is never special anywhere (see item #6 below) --
    it always falls through to the plain "ordinary character" case, so an
    apostrophe in a path or macro value survives unchanged either way.

    Six earlier revisions of this function (and, before it existed, of
    :func:`split_gcc_options` applying one grammar to every platform) each
    traded away a real, independently-confirmed case -- see that
    function's own docstring for the full history through item #7. This
    revision (item #8) is not one more hand-patch on top of that history;
    it replaces the whole hand-rolled "outside quotes: only a
    backslash-before-quote is special; inside quotes: only a
    backslash-before-quote is special, but as a *separate* code path with
    its own rules" design with the one real rule Windows command lines
    actually use, closing an entire remaining class of bug at once: item
    #7's separate inside-quotes handling could not correctly *close* a
    quoted region that ends in an even run of backslashes right before the
    closing quote (e.g. a quoted Windows path ending in a directory
    separator, ``-I"C:\\Program Files\\SDK\\\\"``) -- it treated the final
    backslash as escaping the quote regardless of parity, consuming what
    should have been the closing delimiter and raising ``ValueError`` on
    the resulting unterminated quote. Under the real parity rule, ``k=2``
    (even) contributes one literal backslash and closes the quote, exactly
    matching what a real Windows compiler driver would parse.

    Verified against every case the earlier revisions were built for,
    confirming none regressed under the unified rule: a quoted value with
    an embedded space (``-DMSG="hello world"``) stays one token; an
    unquoted backslash before whitespace or end of string is preserved
    literally, keeping a trailing directory separator right before the
    next flag as two tokens (``-IC:\\sdk\\ -DOK=1``); a single backslash
    before a quote character, quoted or not, escapes exactly that quote
    (``-DVERSION=\\"1.2\\"`` -> one token ``-DVERSION="1.2"``, matching
    plain POSIX ``shlex``); an ordinary unquoted Windows path's
    backslashes survive untouched (``-IC:\\mypath\\include``); a quoted
    value's *interior* single backslashes (not immediately before the
    closing quote) also survive untouched, matching item #7
    (``-DPATH="C:\\a\\b"`` -> ``-DPATH=C:\\a\\b``, and a quoted UNC path's
    leading ``\\\\server\\share`` prefix survives intact); an apostrophe
    never raises or opens grouping, quoted or not
    (``-DCHAR='x'``, ``-IC:\\Users\\O'Brien\\include``); and a genuinely
    unterminated quote (an odd, unresolved "still inside quotes" state at
    end of string) still raises ``ValueError``, the same way plain
    ``shlex.split`` does.
    """
    tokens: list[str] = []
    current: list[str] = []
    have_current = False
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            # A run of consecutive backslashes is only special when
            # followed by a quote character -- otherwise every backslash
            # in the run is literal, regardless of quoting state (see the
            # docstring above).
            j = i
            while j < n and text[j] == "\\":
                j += 1
            run_length = j - i
            if j < n and text[j] == '"':
                have_current = True
                current.append("\\" * (run_length // 2))
                if run_length % 2 == 1:
                    # Odd run: the trailing backslash escapes the quote --
                    # a literal `"` character, quoting state unchanged.
                    current.append('"')
                else:
                    # Even run: the quote is a real delimiter.
                    in_quotes = not in_quotes
                i = j + 1
            else:
                current.append("\\" * run_length)
                have_current = True
                i = j
            continue
        if ch == '"':
            # k == 0 case of the same rule above: an unescaped quote is
            # always a real delimiter.
            in_quotes = not in_quotes
            have_current = True
            i += 1
            continue
        if ch in _WHITESPACE and not in_quotes:
            if have_current:
                tokens.append("".join(current))
                current = []
                have_current = False
            i += 1
            continue
        # Ordinary character (a bare `'` included -- see the docstring
        # above for why it's never special on this platform), literal
        # whether inside or outside quotes.
        current.append(ch)
        have_current = True
        i += 1
    if in_quotes:
        raise ValueError("No closing quotation")
    if have_current:
        tokens.append("".join(current))
    return tokens


def has_explicit_std(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> bool:
    """Return whether forwarded options explicitly select any language standard.

    Known, narrower residual gap (Codex review, fresh evidence, not fixed
    here): recognizes only ``-std=``/``/std:``, not GCC's standard-selecting
    *alias* ``-ansi`` (``-std=c90``/``-std=c++98`` depending on language mode
    -- verified against a real compiler: ``g++ -ansi`` reports
    ``__cplusplus=199711L``, not the default ``201703L``). A forwarded
    ``-ansi`` therefore reads as "no explicit standard" everywhere this
    function gates on it -- both ``dumper_ast_config.py``'s command builders
    (which would then append their own ``-std=gnu11``/``-std=gnu++20``
    *after* a user's ``-ansi``, silently overriding it via last-flag-wins)
    and ``dumper_toolchain._resolve_standard_provenance`` (which would then
    probe or report a forced standard that never actually parsed the
    headers). A correct fix needs ``-ansi`` recognized here (its resolved
    value depends on the caller's language mode, which this function does
    not currently receive) *and* every one of this function's several
    call sites across ``dumper_ast_config.py``/``dumper_toolchain.py``/
    ``cli_helpers_compare.py``/``service_compare_evidence.py``/
    ``service_scan.py`` re-verified against the new signature -- a genuine,
    cross-cutting change, not a follow-up to one caller.
    """
    if gcc_options and ("-std=" in gcc_options or "/std:" in gcc_options):
        return True
    return any(("-std=" in token or "/std:" in token) for token in gcc_option_tokens)


def has_explicit_cpp_std(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> bool:
    """Return whether forwarded options explicitly select a C++ dialect."""
    tokens = list(gcc_option_tokens)
    if gcc_options:
        try:
            tokens.extend(split_gcc_options(gcc_options))
        except ValueError:
            # Same rule as explicit_language_standard's identical guard just
            # below: malformed --gcc-options (e.g. an unbalanced quote) must
            # not abort the dump (Codex review, fresh evidence -- this
            # sibling helper previously disagreed with that one on the same
            # failure mode).
            pass
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

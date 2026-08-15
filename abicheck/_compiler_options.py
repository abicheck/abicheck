# SPDX-License-Identifier: Apache-2.0
"""Small predicates for forwarded compiler dialect options."""

from __future__ import annotations

import shlex


def split_gcc_options(text: str) -> list[str]:
    """Quote-aware split of a ``--gcc-options``-style compiler-flags string
    (e.g. ``-DMSG="hello world" -DOK=1``).

    Always uses POSIX-style quote/whitespace-splitting rules, on every
    platform, but with backslash escaping disabled: a plain POSIX
    ``shlex.split`` would otherwise consume a backslash as an escape
    character, corrupting a literal Windows path (``-IC:\\mypath\\include``)
    that never intended one. Disabling escaping (rather than the previous
    ``shlex.split(text, posix=os.name != "nt")`` split, which picked
    quote-collapsing behavior on POSIX and backslash-preserving behavior on
    Windows -- but never both on the same platform) keeps both properties
    intact everywhere: a quoted value with an embedded space stays one
    token *and* a Windows path's backslashes survive, regardless of which
    OS this process happens to be running on. The previous per-platform
    split silently broke a quoted value with a space into malformed tokens
    on Windows specifically (Codex review; see
    ``tests/test_action_compile_context_parity.py::
    TestCompileContextForwardingParity::test_gcc_options_quoted_value_stays_one_token``,
    which exercises ``action/run.sh``'s own bash mirror of this identical
    fix).

    Raises ``ValueError`` the same way ``shlex.split`` does on malformed
    input (e.g. an unbalanced quote) -- callers that need to tolerate that
    already catch it.
    """
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    return list(lexer)


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

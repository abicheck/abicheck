# SPDX-License-Identifier: Apache-2.0
"""Small predicates for forwarded compiler dialect options."""

from __future__ import annotations

import os
import shlex


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
        tokens.extend(shlex.split(gcc_options, posix=os.name != "nt"))
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
    tokens = (
        list(shlex.split(gcc_options, posix=os.name != "nt")) if gcc_options else []
    )
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
    lang: str | None, gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> str | None:
    """ADR-050 D1's ``language_standard`` profile field: combines the
    explicit ``--lang`` mode (if any) with an explicit ``-std=`` value (if
    any).

    A same-executable, no-``-std=`` dump differing only by ``lang="c"`` vs.
    ``lang="c++"`` must still fingerprint differently — the actual frontend
    command forces a different language mode (``-x c`` vs. C++ default)
    regardless of whether an explicit standard was also given (Codex
    review, PR #624 follow-up). Pure content-based auto-detection (no
    explicit ``--lang``, header content alone triggering C++ mode via
    ``_detect_cpp_headers``) is **not** captured here — that needs the
    frontend's own *resolved* ``force_cpp`` decision threaded out as
    toolchain metadata, deferred as a narrower follow-up.
    """
    lang_mode = (lang or "").strip().lower()
    explicit_std = explicit_language_standard(gcc_options, gcc_option_tokens)
    if lang_mode and explicit_std:
        return f"{lang_mode}:{explicit_std}"
    return explicit_std or (lang_mode or None)

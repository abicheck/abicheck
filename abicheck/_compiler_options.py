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
        if normalized.startswith("-std=") and "++" in normalized.partition("=")[2]:
            return True
        if normalized.startswith("/std:c++"):
            return True
    return False

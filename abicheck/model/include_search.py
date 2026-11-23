# SPDX-License-Identifier: Apache-2.0
"""Ordered include roots encoded in a recorded compiler invocation."""

import posixpath
from collections.abc import Sequence

#: Longest first, so ``-isystem`` is not read as ``-i`` + ``system``.
_INCLUDE_FLAGS = ("-idirafter", "-isystem", "-iquote", "-I")


def include_roots(compile_args: Sequence[str]) -> tuple[str, ...]:
    """The include-search directories *compile_args* name, normalized, in
    order, de-duplicated: ``-I dir``, ``-Idir``, and the ``-isystem``/
    ``-iquote``/``-idirafter`` forms."""
    roots: list[str] = []
    index = 0
    while index < len(compile_args):
        arg = compile_args[index]
        for flag in _INCLUDE_FLAGS:
            if arg == flag:
                if index + 1 < len(compile_args):
                    roots.append(compile_args[index + 1])
                index += 1
                break
            if arg.startswith(flag):
                roots.append(arg[len(flag) :])
                break
        index += 1
    return tuple(
        dict.fromkeys(posixpath.normpath(r.replace("\\", "/")) for r in roots if r)
    )

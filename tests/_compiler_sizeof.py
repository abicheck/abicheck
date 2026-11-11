# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""An object-format-neutral ``sizeof`` oracle: the compiler itself decides.

Reading a compiled object's symbol size ties a test to one object format
(pyelftools rejects a Mach-O or COFF object outright). Here the compiler only
type-checks ``char probe[(sizeof(X) <= N) ? 1 : -1];``: a binary search over
``N`` finds the size, and a final ``==`` check confirms it. No object file, no
binary reader, no code run -- and the answer is the compiler's, never
abicheck's.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

_MAX_BYTES = 4096


def _accepts(
    compiler: str,
    language: str,
    include: Path,
    condition: str,
    flags: Sequence[str],
    workdir: Path,
) -> bool:
    source = workdir / f"sizeof_probe.{'cpp' if language == 'c++' else 'c'}"
    source.write_text(
        f'#include "{include}"\nchar abicheck_probe[({condition}) ? 1 : -1];\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [compiler, "-x", language, *flags, "-fsyntax-only", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def compiler_sizeof(
    compiler: str,
    language: str,
    include: Path,
    expression: str,
    *,
    flags: Sequence[str] = (),
    workdir: Path,
) -> int:
    """``sizeof(expression)`` in bytes, as *compiler* computes it after
    ``#include``-ing *include* with *flags*."""

    def check(condition: str) -> bool:
        return _accepts(compiler, language, include, condition, flags, workdir)

    size = f"sizeof({expression})"
    if not check(f"{size} <= {_MAX_BYTES}"):
        raise AssertionError(f"{size} is not a constant <= {_MAX_BYTES}")
    low, high = 1, _MAX_BYTES
    while low < high:
        mid = (low + high) // 2
        if check(f"{size} <= {mid}"):
            high = mid
        else:
            low = mid + 1
    if not check(f"{size} == {low}"):
        raise AssertionError(f"{size} oracle did not confirm {low}")
    return low

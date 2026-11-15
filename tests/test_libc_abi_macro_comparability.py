"""A header parse that differs only in an ABI-selecting macro stays comparable.

Bug class: rebuilding an i386 library with ``-D_TIME_BITS=64
-D_FILE_OFFSET_BITS=64`` (and parsing its header the same way) made the two
sides' compile profiles differ, and the comparison was refused
(``ProfileMismatchError``) instead of reporting ``time64_abi_changed``.
Oracle, on the inputs: the profile delta is waived iff every differing macro
operation names an ABI selector (libstdc++'s ``_GLIBCXX_USE_CXX11_ABI`` or
glibc's ``_TIME_BITS``/``_FILE_OFFSET_BITS``) and the remaining operations
match in order. The libstdc++ dual-ABI detector's own selector list is not
widened: a time macro is never read as a dual-ABI flip.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.model.glibcxx_dual_abi import (
    LIBC_ABI_MACROS,
    RUNTIME_ABI_MACROS,
    macro_ops_differ_only_in_runtime_abi,
    runtime_abi_ops,
)

_SELECTORS = ("_TIME_BITS=64", "_FILE_OFFSET_BITS=64", "_GLIBCXX_USE_CXX11_ABI=0")
_OTHER = ("NDEBUG", "FOO=1")


def _raw(ops: list[tuple[str, str]]) -> str:
    return json.dumps([[op, arg] for op, arg in ops])


@pytest.mark.parametrize(
    "added",
    [
        list(c)
        for n in range(0, 3)
        for c in itertools.combinations(_SELECTORS + _OTHER, n)
    ],
)
def test_waived_iff_every_differing_operation_is_an_abi_selector(
    added: list[str],
) -> None:
    base = [("D", "KEEP=1")]
    new = base + [("D", a) for a in added]
    expected = bool(added) and all(a in _SELECTORS for a in added)
    assert macro_ops_differ_only_in_runtime_abi(_raw(base), _raw(new)) is expected


def test_libc_selectors_are_not_libstdcxx_dual_abi_operations() -> None:
    ops = [("D", m + "=64") for m in LIBC_ABI_MACROS]
    assert runtime_abi_ops(ops) == []
    assert not set(LIBC_ABI_MACROS) & set(RUNTIME_ABI_MACROS)

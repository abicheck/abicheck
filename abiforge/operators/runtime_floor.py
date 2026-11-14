"""Edit operators that raise the runtime a library needs from the binary alone.

A versioned import names its provider's version node (``getrandom@GLIBC_2.25``);
the highest node a library needs from a provider is the oldest runtime it
loads against. Calling a newer libc function raises that floor: an existing
consumer on an older libc can no longer load the library. Nothing in the
interface changes, so the ground truth is a deployment risk.
"""

from __future__ import annotations

from abiforge.model.opkit import EditOp, _set

__all__ = ["RUNTIME_FLOOR_OPERATORS"]

RUNTIME_FLOOR_OPERATORS: tuple[EditOp, ...] = (
    EditOp(
        "glibc_floor_raised",
        ("elf.vget",),
        lambda r: _set(
            r,
            "elf.vget",
            "int vget(int x);",
            # Declared by hand: <sys/random.h> may be absent from an old sysroot.
            "typedef __SIZE_TYPE__ edit_size_t;\n"
            "long getrandom(void *buf, edit_size_t len, unsigned flags);\n"
            "int vget(int x) { unsigned char b = 0; getrandom(&b, 1, 1); "
            "return x * 2 + (b & 0); }",
        ),
        "getrandom@GLIBC_2.25 is newer than any GLIBC node the library needed "
        "(memset@GLIBC_2.2.5, __stack_chk_fail@GLIBC_2.4): a consumer on glibc "
        "older than 2.25 can no longer load it",
        expected=frozenset(
            {
                "IMPORTED_SYMBOL_ADDED",
                "SYMBOL_VERSION_REQUIRED_ADDED",
                "RUNTIME_FLOOR_RAISED",
            }
        ),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="COMPATIBLE_WITH_RISK",
        touches=("elf.vget",),
        min_evidence="binary",
    ),
)

# SPDX-License-Identifier: Apache-2.0
"""Identity of the abicheck code that produced a cached artifact.

A disk cache that stores something abicheck *computed* -- a whole
``AbiSnapshot``, normalized ``BuildEvidence``, a per-TU source-ABI dump -- is a
function of its inputs **and of the code that computed it**. Each such cache
keyed only its inputs plus a hand-bumped version constant, so any extraction
change that nobody remembered to pair with a bump kept serving the previous
code's answer for byte-identical inputs. That is not hypothetical for the
example catalog: its debug builds are byte-reproducible (same source, flags
and working directory), so a restored cache answered a pull request with the
snapshot its base branch's extractor produced.

:func:`abicheck_code_fingerprint` closes that class: a content hash of every
source file in the installed ``abicheck`` package, folded into each of those
keys. Any edit to the package -- a development checkout between commits as
much as a release upgrade -- is a different identity, so a cache miss is
guaranteed rather than remembered. It prefers false misses over false hits,
the same rule ADR-033 D5 states for these caches.

Caches of an *external tool's* output for inputs abicheck generates (the
castxml/clang header-AST cache) do not use this: their entries do not depend
on abicheck's code except through the generated input itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..model.execution_cache import memoized

__all__ = [
    "PACKAGE_ROOT",
    "SOURCE_SUFFIXES",
    "abicheck_code_fingerprint",
    "compute_code_fingerprint",
]

#: The installed package directory (``abicheck/``).
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: File kinds that make up the package's code. Bytecode is a derivative of
#: these and is excluded, as is anything under ``__pycache__``.
SOURCE_SUFFIXES = frozenset({".py", ".pyi"})


def compute_code_fingerprint(root: Path) -> str:
    """Content hash of every source file under *root*.

    Order-independent of directory listing (paths are sorted), independent of
    mtimes and bytecode, and sensitive to a file's relative path as well as its
    content, so a rename or a moved module is a different identity too. An
    unreadable file contributes a marker rather than being skipped, so it can
    never make two different trees hash alike.
    """
    files = sorted(
        p
        for p in root.rglob("*")
        if p.suffix in SOURCE_SUFFIXES
        and "__pycache__" not in p.relative_to(root).parts
        and p.is_file()
    )
    h = hashlib.sha256(b"abicheck-code-identity-v1\0")
    for path in files:
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        try:
            h.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            h.update(b"UNREADABLE")
    return h.hexdigest()


@memoized
def abicheck_code_fingerprint() -> str:
    """The identity of the abicheck code running in this process.

    Computed once per process (~50 ms over ~1000 files) and deliberately
    without a freshness witness: the identity names the code this process
    loaded, and an edit made on disk after start-up does not change what the
    running interpreter executes.
    """
    return compute_code_fingerprint(PACKAGE_ROOT)

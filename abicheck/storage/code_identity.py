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

Caches of an *external tool's* output (the castxml/clang header-AST cache)
fold something narrower, so an unrelated edit does not discard them: the exact
input abicheck generates for the tool, plus :func:`abicheck_modules_fingerprint`
over the named modules that shape what is stored
(``extract.header_ast_cache_producers``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..model.execution_cache import memoized

__all__ = [
    "PACKAGE_ROOT",
    "SOURCE_SUFFIXES",
    "abicheck_code_fingerprint",
    "abicheck_modules_fingerprint",
    "compute_code_fingerprint",
    "compute_modules_fingerprint",
    "module_source_path",
]

#: The installed package directory (``abicheck/``).
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: File kinds that make up the package's code. Bytecode is a derivative of
#: these and is excluded, as is anything under ``__pycache__``.
SOURCE_SUFFIXES = frozenset({".py", ".pyi"})


def _digest_sources(root: Path, files: list[Path]) -> str:
    """The shared hash: each file's root-relative path, then its content digest."""
    h = hashlib.sha256(b"abicheck-code-identity-v1\0")
    for path in files:
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        try:
            h.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            h.update(b"UNREADABLE")
    return h.hexdigest()


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
    return _digest_sources(root, files)


def module_source_path(root: Path, module: str) -> Path | None:
    """The source file of dotted *module* (``abicheck.x.y``) under *root*."""
    parts = module.split(".")[1:]
    if not parts:
        candidates = [root / "__init__.py"]
    else:
        base = root.joinpath(*parts)
        candidates = [base.with_suffix(".py"), base / "__init__.py"]
    return next((c for c in candidates if c.is_file()), None)


def compute_modules_fingerprint(root: Path, modules: tuple[str, ...]) -> str:
    """Content hash of the named modules' source only.

    For a cache whose entries depend on a *known, small* part of abicheck --
    where folding the whole package would make every unrelated edit a miss.
    The caller owns the claim that *modules* is complete; pair it with a test
    that derives the set from what actually runs. A module that cannot be found
    hashes as a marker rather than being dropped, so a rename is still a change.
    """
    files: list[Path] = []
    h = hashlib.sha256()
    for module in sorted(set(modules)):
        path = module_source_path(root, module)
        if path is None:
            h.update(f"MISSING:{module}\0".encode())
        else:
            files.append(path)
    return hashlib.sha256(
        (_digest_sources(root, files) + h.hexdigest()).encode()
    ).hexdigest()


@memoized
def abicheck_modules_fingerprint(modules: tuple[str, ...]) -> str:
    """:func:`compute_modules_fingerprint` over the running package, per process."""
    return compute_modules_fingerprint(PACKAGE_ROOT, modules)


@memoized
def abicheck_code_fingerprint() -> str:
    """The identity of the abicheck code running in this process.

    Computed once per process (~50 ms over ~1000 files) and deliberately
    without a freshness witness: the identity names the code this process
    loaded, and an edit made on disk after start-up does not change what the
    running interpreter executes.
    """
    return compute_code_fingerprint(PACKAGE_ROOT)

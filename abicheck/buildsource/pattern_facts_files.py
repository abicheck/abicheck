# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""File discovery for the lexical pattern pre-scan (ADR-035 D2).

Split out of ``pattern_facts.py``, which owns the *scanning* -- the regex rules,
the per-file worker, the parallel fan-out, the result and its coverage row. This
module owns the question that comes first: given a caller's roots, which files
is the scan expected to read, and what became of each one. That is the half the
expected-input-set model (``source_inputs.py``) applies to, so it is also the
half that holds this scanner's walk policy (the suffix allowlist, the
extensionless-header heuristic, the pruned directories, the changed-path join)
and the licence its direct-root entry points run under.

``pattern_facts.py`` re-exports ``SOURCE_SUFFIXES`` and ``iter_source_files``,
so existing importers of either are unaffected.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .build_query import ABICHECK_BUILD_DIR
from .source_inputs import (
    SourceInputDisposition,
    SourceInputSet,
    SourceReadLicence,
    resolve_source_inputs,
)

#: File suffixes the lexical scanner treats as C/C++ source or headers. Headers
#: without a suffix (the libstdc++ ``<vector>`` style) are not on disk under a
#: project tree, so an extension allowlist is sufficient and keeps the walk cheap.
SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".h++",
        ".inl",
        ".inc",
        ".ipp",
        ".tpp",
        ".tcc",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".c++",
    }
)

#: Directory names whose contents are never part of a library's source surface.
#: VCS metadata holds packed/loose blobs and indexes (a shallow clone's
#: ``.git/index`` is a multi-hundred-KB binary file) that the extensionless
#: heuristic below would otherwise feed to every regex rule. Pruned from the walk.
#: ``ABICHECK_BUILD_DIR`` is included because zero-config cmake inference writes a
#: configure tree under ``sources/.abicheck-build``; without pruning it the lexical
#: pre-scan would flag generated build output (config.h / CMakeFiles) as project
#: source (review).
_PRUNED_DIR_SEGMENTS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", ABICHECK_BUILD_DIR}
)

#: Licence for the **direct-root** entry points (:func:`find_pattern_facts`,
#: :func:`iter_source_files`). A caller that hands this module concrete paths
#: is naming inputs it means *right now* -- a ``scan -H include/`` invocation,
#: a compile DB just produced by this build. That is live extraction, so the
#: filesystem may be read. The deny-by-default rule this licence is the
#: exception to applies where paths come from a *snapshot* rather than from a
#: caller: see ``workflows/pattern_preprocessor_scan.py``, which resolves its
#: own licence per side and never lets a stored snapshot reach this default.
_DIRECT_ROOT_LICENCE: SourceReadLicence = SourceReadLicence.live_extraction()

#: Size ceiling for the **extensionless** heuristic only. A genuine extensionless
#: C++ header (``include/mylib/Core``) is small; multi-MB extensionless files are
#: build/test *data* (e.g. oneDNN's ``tests/benchdnn/inputs/...`` option sets,
#: several MB each) or VCS blobs — never headers. Files with a known C/C++
#: suffix are *not* capped (a real ``dnnl.hpp`` is legitimately large).
_EXTENSIONLESS_MAX_BYTES = 256 * 1024


def _looks_binary(path: Path) -> bool:
    """Heuristic: a NUL byte in the first 8 KiB marks a non-text (binary) file.

    Unreadable files are treated as binary so they fall out of the scan set
    (``find_pattern_facts`` would skip them anyway).
    """
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def _is_scannable(path: Path) -> bool:
    """True if a directory-walked file should be lexically scanned.

    Known C/C++ suffixes are always scannable. **Extensionless** files are
    accepted too — many C++ libraries ship extensionless public headers
    (``include/mylib/Core``), and the D2 scope is "changed + public headers", not
    "files with a C/C++ extension" — but only when they are small *text* files:
    an oversized or binary extensionless file is build/test data or a VCS blob,
    not a header, and scanning it is pure cost with no ABI signal (ADR-035 D2;
    the pre-scan is advisory, so a missed exotic giant header is harmless).
    Files with a different, explicit extension (``.md``, ``.txt``, ``.bin``) are
    skipped.
    """
    suffix = path.suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        return True
    if suffix != "":
        return False
    try:
        if path.stat().st_size > _EXTENSIONLESS_MAX_BYTES:
            return False
    except OSError:
        return False
    return not _looks_binary(path)


def iter_source_files(
    roots: Iterable[str | Path],
    changed_paths: Iterable[str] | None = None,
) -> list[Path]:
    """Collect C/C++ source/header files under ``roots`` (files or directories).

    A ``root`` that is a **file** is honored regardless of suffix — the caller
    pointed at it directly. A ``root`` that is a **directory** is walked (with
    VCS metadata dirs pruned, see :data:`_PRUNED_DIR_SEGMENTS`) and filtered by
    :func:`_is_scannable` (known suffixes + small text extensionless headers).
    When ``changed_paths`` is given, the result is intersected with it (by
    suffix-matching the path tail), implementing the ADR-035 D2 "changed +
    public" scope: callers pass public roots and the PR's changed paths. The
    walk is deterministic (sorted) for reproducible reports.
    """
    return [
        Path(i.path)
        for i in resolve_expected_source_inputs(roots, changed_paths).inputs
        if i.disposition is SourceInputDisposition.SELECTED
    ]


def resolve_expected_source_inputs(
    roots: Iterable[str | Path],
    changed_paths: Iterable[str] | None = None,
    *,
    licence: SourceReadLicence = _DIRECT_ROOT_LICENCE,
) -> SourceInputSet:
    """Account for every declared root, using this scanner's own walk policy.

    The expected-input counterpart of :func:`iter_source_files`: that returns
    only the survivors, this the disposition of every root, so a caller can
    tell "scanned and found nothing" from "the root is gone".
    """
    return resolve_source_inputs(
        roots,
        changed_paths,
        licence=licence,
        is_scannable=_is_scannable,
        path_changed=_path_changed,
        pruned_dirs=_PRUNED_DIR_SEGMENTS,
    )


def _path_changed(candidate: Path, changed: set[str]) -> bool:
    """True if ``candidate`` tail-matches any of the changed-path strings.

    The changed list usually holds repo-relative paths (``include/foo.h``)
    while ``candidate`` may be absolute or rooted elsewhere, so a suffix match
    in either direction is the robust join; a bare filename in the changed list
    matches by basename.
    """
    norm = str(candidate).replace("\\", "/")
    for ch in changed:
        c = ch.replace("\\", "/")
        if norm == c or norm.endswith("/" + c) or c.endswith("/" + norm):
            return True
        if "/" not in c and candidate.name == c:
            return True
    return False

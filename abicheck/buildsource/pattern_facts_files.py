# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""File discovery for the lexical pattern pre-scan (ADR-035 D2).

Split out of ``pattern_facts.py``, which owns the *scanning* -- the regex rules,
the per-file worker, the parallel fan-out, the result and its coverage row. This
module owns the question that comes first: given a caller's roots, which files
is the scan expected to read, and what became of each one. That is the half the
expected-input-set model (``source_inputs.py``) applies to, so it is also the
half that holds this scanner's walk policy (the suffix allowlist, the
extensionless-header heuristic, the pruned directories)
and the licence its direct-root entry points run under.

``pattern_facts.py`` re-exports ``SOURCE_SUFFIXES`` and ``iter_source_files`` (removed),
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
#: :func:`iter_source_files` (removed)). A caller that hands this module concrete paths
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


def _extensionless_is_text(path: Path) -> bool | None:
    """Tri-state text check for the extensionless heuristic.

    ``True``/``False`` answer "is this small text" only when the file could
    actually be examined; ``None`` means it could not be read at all, which is a
    *coverage gap* rather than an answer. :func:`_looks_binary` collapses that
    third case onto "binary", which is right for its own callers but wrong for
    discovery: an unreadable extensionless header would be dropped from the
    expected-input set entirely, and a sibling file scanning successfully would
    then let the set report full coverage over a header nobody read (Codex
    review, P2 -- the same silent-drop shape as the ``os.walk`` traversal
    error).
    """
    try:
        if path.stat().st_size > _EXTENSIONLESS_MAX_BYTES:
            return False
    except OSError:
        return None
    try:
        with open(path, "rb") as fh:
            return b"\x00" not in fh.read(8192)
    except OSError:
        return None


def classify_walked_file(path: Path) -> SourceInputDisposition | None:
    """Disposition for one file found under a directory root, or ``None``.

    ``None`` means "never expected evidence" -- a ``.md``, a ``.bin``, an
    oversized or binary extensionless blob. That is not a gap and must not spoil
    sufficiency. ``UNREADABLE`` means the opposite: this *was* a candidate and
    we could not look at it, so it is recorded and does spoil sufficiency.
    Keeping the two apart is the whole point of the tri-state.
    """
    suffix = path.suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        return SourceInputDisposition.SELECTED
    if suffix != "":
        return None
    is_text = _extensionless_is_text(path)
    if is_text is None:
        return SourceInputDisposition.UNREADABLE
    return SourceInputDisposition.SELECTED if is_text else None


def resolve_expected_source_inputs(
    roots: Iterable[str | Path],
    *,
    licence: SourceReadLicence = _DIRECT_ROOT_LICENCE,
) -> SourceInputSet:
    """Account for every declared root, using this scanner's own walk policy.

    The expected-input counterpart of :func:`iter_source_files` (removed): that returns
    only the survivors, this the disposition of every root, so a caller can
    tell "scanned and found nothing" from "the root is gone".
    """
    return resolve_source_inputs(
        roots,
        licence=licence,
        classify_candidate=classify_walked_file,
        pruned_dirs=_PRUNED_DIR_SEGMENTS,
    )

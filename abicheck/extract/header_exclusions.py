# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Removing named headers from a resolved ``-H`` operand.

A header *directory* operand is otherwise all-or-nothing, and a real release
tree routinely cannot be parsed whole: Intel MKL's ``include/`` ships FFTW2
and FFTW3 headers that declare conflicting typedefs, so ``-H include/``
fails outright with no flag, config key or descriptor element able to
rescue it.

Filtering happens *after* the directory walk, on the resolved list, which is
what keeps it cache-correct with no cache-key change: both header-parse
cache keys (``extract/cache_header_scan.py``, ``snapshot_cache``) already
hash the resolved header list, and a run with no pattern passes its list
through untouched, so no warm cache entry is invalidated.

Split out of ``header_utils.py``, a ``legacy_generic_modules`` grab-bag
carrying a ``no_growth`` baseline; reading and narrowing header inputs is
``extract``'s job under ADR-061's routing table in any case.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

from ..errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..model import AbiSnapshot


def apply_header_exclusions(
    headers: Sequence[Path], patterns: Sequence[str]
) -> list[Path]:
    """*headers* minus every entry matching one of *patterns*.

    The one shared implementation of ``--exclude-header``. A pattern is
    fnmatch-style and is tried against three spellings of each header, so a
    user does not have to know which one this codebase happens to carry:

    * the bare file name (``fftw3.h``),
    * the full path (``/opt/include/fftw/fftw3.h``), and
    * the path with ``**/`` semantics (``**/fftw/*`` matching any depth).

    Why this exists at all: a header *directory* operand is all-or-nothing
    without it. A library whose public include tree contains two headers that
    cannot be parsed in the same translation unit -- the FFTW2/FFTW3 typedef
    clash in Intel MKL's ``include/`` is the reported case -- makes
    ``-H include/`` fail outright, and before this there was no flag, config
    key, or descriptor element anywhere in abicheck that could exclude one
    header from a parse. The only options were to name every other header
    individually or to give up on header-aware analysis for that library.

    Deliberately a *path* filter and nothing more. It does not know about
    ABI visibility, public/private surface, or include graphs -- excluding a
    header removes it from the parsed translation unit, and anything only it
    declared is then simply not observed, which the surface/evidence layers
    already know how to report as reduced evidence rather than as removal.
    """
    if not patterns:
        return list(headers)
    kept: list[Path] = []
    for h in headers:
        text = str(h)
        name = h.name
        if any(
            fnmatch(name, pat) or fnmatch(text, pat) or fnmatch(text, f"*/{pat}")
            for pat in patterns
        ):
            continue
        kept.append(h)
    return kept


def apply_header_exclusions_to_inputs(
    headers: list[Path], exclude_headers: Sequence[str]
) -> list[Path]:
    """*headers* with ``--exclude-header`` patterns applied.

    A directory operand is expanded first, because a pattern naming one
    header cannot otherwise match anything inside a directory entry -- and a
    header directory is precisely the case ``--exclude-header`` exists for
    (``extract.header_exclusions.apply_header_exclusions`` explains why).

    Expansion happens **only** when at least one pattern was given, so a run
    without the flag passes its header list through untouched and behaves
    byte-for-byte as before, directory entries included. This matters beyond
    tidiness: the resulting list is hashed into both the AST cache key
    (``dumper_ast_config._cache_key``) and the whole-snapshot cache key
    (``snapshot_cache._cache_key``), so unconditionally expanding here would
    invalidate every warm cache entry in every existing checkout for no
    behavioural gain. Conversely, that same hashing is what makes the
    exclusion cache-correct without touching either key: excluding a header
    changes the list, which changes both keys, so a filtered parse can never
    reuse an unfiltered entry.

    Provenance is unaffected: ``public_headers``/``public_header_dirs`` are
    separate parameters that this never touches, so a ``-H`` directory keeps
    its directory-shaped scope fingerprint (see ``header_utils.
    split_public_header_inputs`` for why that distinction is load-bearing).
    """
    if not exclude_headers:
        return headers
    from ..buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
    from ..header_utils import iter_directory_headers

    expanded: list[Path] = []
    for h in headers:
        if h.is_dir():
            expanded.extend(iter_directory_headers(h, PRUNED_HEADER_DIR_SEGMENTS))
        else:
            expanded.append(h)
    return apply_header_exclusions(expanded, exclude_headers)


def record_header_exclusions(
    snapshot: AbiSnapshot,
    exclude_headers: Sequence[str],
    *,
    extracted_now: bool = True,
) -> AbiSnapshot:
    """*snapshot* carrying the ``--exclude-header`` patterns it was built under.

    Returns it unchanged when there were none, so every run that does not use
    the flag produces a byte-identical snapshot to before this existed.

    *extracted_now* is ``False`` for an operand this run **loaded** rather
    than extracted. Such a snapshot parsed no headers in this invocation, so
    this run's patterns say nothing about it -- and it already carries the
    patterns it was really built under. Stamping anyway overwrote that
    provenance with an unrelated request: a snapshot dumped under
    ``original.h``, loaded under ``--exclude-header current.h``, came back
    claiming ``current.h`` (Codex review, reproduced). Worse than a wrong
    label, it can make an asymmetric pair look symmetric -- which is exactly
    the comparison the recorded patterns exist to expose.
    """
    if not exclude_headers or not extracted_now:
        return snapshot
    snapshot.excluded_header_patterns = tuple(exclude_headers)
    return snapshot


def reject_exclusions_against_a_manifest(
    exclude_headers: Sequence[str],
    dump_manifest: object | None,
) -> None:
    """Refuse ``--exclude-header`` together with ``--dump-manifest``.

    A manifest dump parses ``translation_units[]`` and
    ``public_header_paths`` from the manifest document, never the ``-H``
    header list this filter narrows -- so the patterns would match nothing,
    change nothing, and still be recorded on the snapshot and reported as
    headers omitted. That is a request recorded as achieved when it was not,
    which is the single failure this whole area exists to stop (Codex
    review).

    Rejected rather than applied, deliberately. A manifest is an exact,
    self-describing extraction contract (ADR-050 D3): narrowing it from the
    command line would contradict the document the run was told to honour,
    and the roots it declares are matched exactly by
    ``dumper_scoping.dump_manifest_header_roots``. Excluding a header from a
    manifest dump is a real capability, but it belongs in the manifest --
    recorded in ``docs/contribute/known-gaps.md`` rather than approximated
    here.
    """
    if not exclude_headers or dump_manifest is None:
        return
    raise ValidationError(
        "--exclude-header cannot be combined with --dump-manifest: a manifest "
        "dump parses the translation units and public headers the manifest "
        "itself declares, not the -H header list --exclude-header narrows, so "
        "the pattern would match nothing while still being recorded as an "
        "omission. Remove the header from the manifest instead."
    )

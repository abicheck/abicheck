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
from ..model.header_exclusion_record import exclusions_are_symmetric

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


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


def exclusion_asymmetry_reason(
    old_patterns: Sequence[str], new_patterns: Sequence[str]
) -> str | None:
    """Why this pair is not comparable, or ``None`` when the two agree.

    ADR-050 D2's scope axis, answered from the field rather than from a
    fingerprint. `scope_fingerprint` already refuses most of this shape --
    the exclusion narrows the `-H` list before the contract is computed, so
    `scope_fields["headers"]` differs and the pair is refused with exit 16
    (verified against a real pair). It cannot refuse the one shape that
    matters most, though: a baseline that carries **no** contract at all --
    a pre-ADR-050 snapshot, or one whose contract was stripped -- has no
    fingerprint to differ, so a run excluding a header from the candidate
    alone reported every declaration that header carried as `func_removed`
    and exited `4` (reproduced; Codex review). A finding manufactured by
    the run's own narrowing is exactly what `vision.md` forbids.

    So this asks the field directly, which is sound with no ambiguity
    carve-out of the sort `_check_dependency_scope_comparable` needs for
    `dependency_scope`: `--exclude-header` and the field arrived together
    in schema v47, so a pre-v47 snapshot's `()` is not an unknown, it is a
    certainty -- no snapshot predating the field could have been dumped
    under an exclusion, because no flag existed to request one.

    Comparing the *sets* rather than the sequences: the patterns are a
    filter, and stating one twice, or in the other order, narrows the
    surface identically. Only a genuine difference in what was excluded is
    a difference in what was compared.
    """
    if exclusions_are_symmetric(old_patterns, new_patterns):
        return None
    old_set = frozenset(old_patterns)
    new_set = frozenset(new_patterns)

    def _render(patterns: frozenset[str]) -> str:
        return ", ".join(sorted(patterns)) if patterns else "none"

    return (
        "old and new snapshots do not cover the same declared surface: the "
        "--exclude-header patterns differ (old: "
        f"{_render(old_set)}; new: {_render(new_set)}). Anything only an "
        "excluded header declared was never parsed on that side, so a "
        "difference between the two would be this run's own narrowing rather "
        "than a change in the library. Exclude the same headers on both "
        "sides, or re-dump the baseline under the same exclusions."
    )

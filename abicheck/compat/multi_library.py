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

"""Multi-library ``compat check``: pairing a descriptor's ``<libs>`` entries
across OLD/NEW, and merging the per-pair results into one.

An ABICC descriptor may name more than one library, and once a ``<libs>``
*directory* expands (``_helpers.expand_descriptor_libs``) it routinely names
dozens. ``compat check`` previously took ``desc.libs[0]`` and printed a
warning naming the rest -- so a descriptor covering a 28-library release was
answered by comparing one library and reporting the verdict as though it
covered the release. A warning is not a disposition: the other 27 libraries
were neither compared, nor excluded by a rule, nor reported as uncompared
(``AGENTS.md``'s "record before disposing", ADR-067).

Pairing is by filename, then by SONAME-insensitive stem, so a release that
bumped ``libfoo.so.1`` to ``libfoo.so.2`` still pairs. A library present on
only one side is **not** a finding: a descriptor's ``<libs>`` list is a
*selection*, and an unmatched entry is "not supplied on the other side", not
"deleted" (ADR-065's "absent is not removed" -- the same rule the release
fan-out applies, and the reason this module reports such an entry as a
coverage warning rather than manufacturing a removal from it).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from ..checker_types import DiffResult
from ..policy.analysis_assurance_merge import merge_analysis_assurance

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _soname_stem(path: Path) -> str:
    """``libfoo.so.1.2.3`` -> ``libfoo``; ``libfoo.1.dylib`` -> ``libfoo``.

    Everything from the first ``.so``/``.dylib``/``.dll`` component onward is
    dropped, so every version suffix of one library collapses to one key.

    That alone handles only the ELF convention, where the version *follows*
    the extension. macOS and Windows put it *before* (``libfoo.1.dylib``,
    ``libfoo-1.dll``), which truncation at the marker leaves as ``libfoo.1``
    -- so ``libfoo.1.dylib`` and ``libfoo.2.dylib`` would key apart and a
    SONAME bump would read as one library removed and another added, which is
    the exact failure the stem pass exists to prevent. Trailing all-numeric
    ``.``/``-``/``_``-separated components are therefore stripped as well.

    The result is never empty: see the fallback at the end of the body.

    Only *fully numeric* trailing components are stripped, so a name whose
    real identity ends in a word (``libfoo_debug``) keeps it. A library whose
    own name genuinely ends in a number (``libjpeg.8``) collapses with its
    other versions, which is the intended reading -- that *is* the version
    convention -- and pairing it is still gated on both sides resolving to a
    single candidate.
    """
    name = path.name
    for marker in (".so", ".dylib", ".dll"):
        idx = name.find(marker)
        if idx != -1:
            name = name[:idx]
            break
    else:
        name = path.stem
    stripped = _strip_trailing_version(name)
    # A file named exactly ``.so`` (or ``.dylib``) truncates to nothing, and
    # an empty key is the one value that collapses *unrelated* libraries into
    # one group -- ``.so`` and ``.dylib`` would key together and pair. Fall
    # back to the full filename, which keys such a file only with itself.
    return stripped or path.name


def _strip_trailing_version(name: str) -> str:
    """*name* with every trailing all-numeric ``.``/``-``/``_`` component removed.

    Never strips the whole name: ``"1.2"`` stays ``"1.2"`` rather than
    collapsing to ``""``, which would key every such library together.
    """
    separators = (".", "-", "_")
    while True:
        cut = max((name.rfind(sep) for sep in separators), default=-1)
        if cut <= 0:
            return name
        head, tail = name[:cut], name[cut + 1 :]
        if not tail.isdigit():
            return name
        name = head


def _pair_on_key(
    old_libs: list[Path],
    new_libs: list[Path],
    key: Callable[[Path], str],
    pairs: list[tuple[Path, Path]],
) -> list[Path]:
    """Pair OLD with NEW on *key*, appending to *pairs*; return unpaired OLD.

    A key is paired only when **both** sides resolve it to exactly one
    candidate. Ambiguity on either side leaves every candidate under that key
    unpaired rather than picking one.

    This symmetry is the whole point. A recursive ``<libs>`` expansion
    routinely finds the same basename under several directories -- an
    architecture split (``lib/intel64/libfoo.so`` beside
    ``lib/ia32/libfoo.so``) is the ordinary case, not a corner one. Keying a
    dictionary by basename silently keeps whichever entry was inserted last,
    so the run would compare one architecture's OLD against another's NEW and
    report the rest as merely unpaired -- a wrong verdict presented as a
    complete one. The unpaired entries are returned, and the caller reports
    them as uncompared coverage (ADR-065: unmatched is not removed), which is
    the honest answer when the descriptor does not say which is which.
    """
    old_by_key: dict[str, list[Path]] = {}
    for p in old_libs:
        old_by_key.setdefault(key(p), []).append(p)
    new_by_key: dict[str, list[Path]] = {}
    for p in new_libs:
        new_by_key.setdefault(key(p), []).append(p)

    unpaired: list[Path] = []
    for k, olds in old_by_key.items():
        news = new_by_key.get(k, [])
        if len(olds) == 1 and len(news) == 1:
            pairs.append((olds[0], news[0]))
        else:
            unpaired.extend(olds)
    return unpaired


def pair_libraries(
    old_libs: Sequence[Path], new_libs: Sequence[Path]
) -> tuple[list[tuple[Path, Path]], list[Path], list[Path]]:
    """Pair OLD and NEW libraries, returning ``(pairs, old_only, new_only)``.

    Exact filename first -- unambiguous when a release did not rename
    anything -- then the version-insensitive stem, which is what makes a
    SONAME bump (``libfoo.so.1`` -> ``libfoo.so.2``) pair rather than read as
    one library removed and a different one added.

    Both passes pair a key only when **both** sides resolve it to exactly one
    candidate (:func:`_pair_on_key`). A key shared by several libraries on
    either side is left unpaired: guessing which ``libfoo.so.1``/
    ``libfoo.so.2`` in OLD corresponds to which in NEW -- or which
    ``lib/intel64/libfoo.so``/``lib/ia32/libfoo.so`` a bare ``libfoo.so``
    means -- would silently attribute one library's findings to another.
    Unpaired entries are returned, not dropped, and the caller reports them.
    """
    pairs: list[tuple[Path, Path]] = []

    unmatched_old = _pair_on_key(
        list(old_libs), list(new_libs), lambda p: p.name, pairs
    )
    matched_new_ids = {id(n) for _o, n in pairs}
    remaining_new = [p for p in new_libs if id(p) not in matched_new_ids]

    # Second pass over what the exact-name pass could not place.
    still_unmatched_old = _pair_on_key(
        unmatched_old, remaining_new, _soname_stem, pairs
    )

    matched_new = {id(n) for _o, n in pairs}
    new_only = [p for p in new_libs if id(p) not in matched_new]
    return (
        sorted(pairs, key=lambda pr: pr[0].name),
        sorted(still_unmatched_old, key=lambda p: p.name),
        sorted(new_only, key=lambda p: p.name),
    )


def _concat(values: Sequence[object]) -> list[object]:
    """Every entry of every list in *values*, in library order."""
    out: list[object] = []
    for v in values:
        if isinstance(v, list):
            out.extend(v)
    return out


def _union_sorted(values: Sequence[object]) -> list[object]:
    """The distinct entries across *values*, in a deterministic order.

    Order-insensitive by construction (sorted by ``str``), because these are
    set-like fields -- evidence tiers, scope notes -- whose meaning does not
    depend on which library contributed an entry first.
    """
    seen: list[object] = []
    for v in values:
        if not isinstance(v, list):
            continue
        for item in v:
            if item not in seen:
                seen.append(item)
    return sorted(seen, key=str)


#: How each :class:`~abicheck.checker_types.DiffResult` field is combined
#: across per-library results. Every field is listed, and
#: :func:`merge_results` raises on one that is not -- a new field added to
#: ``DiffResult`` must state how it merges rather than silently taking the
#: first library's value and reading, in a 28-library run, as though it
#: described the release.
#:
#: * ``"concat"``  -- list fields whose entries are per-finding.
#: * ``"sum"``     -- counts, ``None`` when every side is ``None``.
#: * ``"union"``   -- set-like lists (evidence tiers, scope notes).
#: * ``"worst"``   -- ordinal fields where the release takes the worst value.
#: * ``"all"``     -- booleans true only if true for every library.
#: * ``"any"``     -- booleans true if true for any library.
#: * ``"assurance_block"`` -- the ``analysis_assurance`` roll-up.
#: * ``"first"``   -- genuinely run-wide values, identical across libraries
#:   because they come from the same descriptor/CLI invocation.
#: * ``"drop"``    -- per-library objects with no defined release-level
#:   meaning; see :func:`merge_results` for why dropping beats keeping one
#:   library's copy.
_FIELD_POLICY: dict[str, str] = {
    # Run-wide: one descriptor, one policy, one invocation.
    "old_version": "first",
    "new_version": "first",
    "policy": "first",
    "policy_file": "first",
    "check_id": "first",
    "profile_id": "first",
    "requested_depth": "first",
    "baseline_channel": "first",
    "suppression_file_provided": "any",
    "suppression_source_sha256": "first",
    "explicit_scope_source_sha256": "first",
    "env_matrix_source_sha256": "first",
    "pattern_verdicts_enabled": "first",
    "collapse_versioned_symbols_enabled": "first",
    "surface_metrics_enabled": "first",
    "reconcile_build_context_enabled": "first",
    "scope_to_public_surface": "first",
    "scope_to_public_surface_requested": "first",
    # Findings and their audit trails.
    "changes": "concat",
    "suppressed_changes": "concat",
    "redundant_changes": "concat",
    "out_of_surface_changes": "concat",
    "reconciled_changes": "concat",
    "resolved_findings": "concat",
    "detector_results": "concat",
    "pattern_modulations": "concat",
    "layer_coverage": "concat",
    "coverage_warnings": "concat",
    "suppressed_count": "sum",
    "redundant_count": "sum",
    "out_of_surface_count": "sum",
    "reconciled_count": "sum",
    "old_symbol_count": "sum",
    # Worst/weakest wins: a release is only as trustworthy as its least
    # well-evidenced member.
    "verdict": "worst",
    "confidence": "worst",
    "evidence_tier": "worst",
    "surface_scope_confidence": "worst",
    "old_evidence_depth": "worst",
    "new_evidence_depth": "worst",
    "effective_depth": "worst",
    "contract_coverage": "worst",
    "assurance": "worst",
    # The one per-library *object* with a defined release-level aggregate:
    # see `analysis_assurance_merge.merge_analysis_assurance` for what rolls
    # up (every judgement axis, to its weakest member) and what deliberately
    # does not (per-library accounting). Dropping it is not neutral -- the
    # reporters omit the block entirely and `--require-complete-analysis`
    # stops gating on a member whose evidence was partial or failed.
    "analysis_assurance": "assurance_block",
    "scope_resolved": "all",
    "evidence_contract_error": "any",
    "budget_overflow": "any",
    "evidence_tiers": "union",
    "surface_scope_notes": "union",
    # Per-library objects with no release-level aggregate. Keeping one
    # library's copy would present it as the release's, which is the exact
    # failure this module exists to fix; a real aggregate for each of these
    # is its own design question (the release fan-out has one for some of
    # them), so the merged result carries none rather than a wrong one.
    "old_metadata": "drop",
    "new_metadata": "drop",
    "evidence_metrics": "drop",
    "comparability_assurance": "drop",
    "contract_context": "drop",
    "contract_conflicts": "drop",
    "use_case_impact": "drop",
    "evaluation_config": "drop",
    "disposition_ledger": "drop",
    "acknowledgments": "drop",
    "unacknowledged_additions_review": "drop",
    "suppression_audit": "drop",
    "pattern_preprocessor_scan": "drop",
    "library": "drop",  # replaced by the caller-supplied label
}

#: Ordinal scales for the ``"worst"`` fields, **every one ordered
#: worst-last**, because :func:`_worst` takes the ``max``. That single
#: convention is the whole contract of this table, and it is not optional
#: bookkeeping: the depth and ``evidence_tier`` scales were originally
#: written in their natural reading order (shallow -> deep), which made
#: ``max`` select the *strongest* member. A two-library release with one
#: binary-only member and one reaching source evidence then reported
#: ``effective_depth='source'`` and ``HEADER_AWARE`` release-wide --
#: presenting the best member's assurance as the release's, which is
#: exactly the inversion of ``vision.md``'s "weaker evidence narrows
#: conclusions" (Codex review).
#:
#: A value absent from its scale ranks worst: an unrecognised depth or
#: assurance level is not evidence of a good one.
#:
#: ``TestWorstScalesAreOrderedWorstLast`` asserts the direction of every
#: entry, not merely its membership -- the membership tests that were here
#: first passed throughout, because a reversed scale contains exactly the
#: right values.
_WORST_SCALES: dict[str, tuple[str, ...]] = {
    # Depth and tier: deepest/richest evidence FIRST, so the shallowest
    # member wins the max.
    "old_evidence_depth": ("source", "build", "headers", "debug", "binary"),
    "new_evidence_depth": ("source", "build", "headers", "debug", "binary"),
    "effective_depth": ("source", "build", "headers", "debug", "binary"),
    "evidence_tier": ("header_aware", "dwarf_aware", "elf_only"),
    # Already worst-last in their natural reading order.
    "surface_scope_confidence": ("high", "reduced"),
    "confidence": ("high", "medium", "low"),
    "contract_coverage": ("partial",),
    "assurance": ("none",),
    "verdict": (
        "NO_CHANGE",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        "API_BREAK",
        "BREAKING",
    ),
}


def _worst(field_name: str, values: list[object]) -> object:
    """The worst (most severe / least assured) of *values* for *field_name*.

    Every ordinal this merges is spelled out in :data:`_WORST_SCALES` rather
    than inferred from a type's declaration order. Inferring it -- the first
    version of this function ranked enum members by their position in
    ``list(type(v))`` -- silently reverses the moment someone reorders an
    enum body for readability, and produces an answer for *any* enum,
    including ones where declaration order means nothing. A value outside its
    scale ranks worst: an unrecognised depth or assurance level is not
    evidence of a good one.
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    scale = _WORST_SCALES.get(field_name)
    if scale is None:
        # No declared ordinal: keep the first library's value rather than
        # inventing a comparison. Reached only for a field whose policy is
        # "worst" but whose scale is missing, which the exhaustiveness test
        # forbids.
        return present[0]
    return max(present, key=lambda v: _rank(v, scale))


def _rank(value: object, scale: tuple[str, ...]) -> int:
    key = getattr(value, "value", value)
    try:
        return scale.index(key)  # type: ignore[arg-type]
    except ValueError:
        return len(scale)


def _field_default(f: dataclasses.Field[object]) -> object:
    """*f*'s own declared default -- what a dropped field resets to.

    Not ``None``: several droppable fields are non-optional with a
    ``default_factory`` (``evidence_metrics`` is a plain ``dict``), and
    writing ``None`` into one produces a ``DiffResult`` that type-checks
    nowhere and breaks the first renderer that iterates it. Reading the
    declared default keeps a dropped field indistinguishable from one the
    run never populated, which is exactly what it is.
    """
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()
    if f.default is not dataclasses.MISSING:
        return f.default
    return None


def merge_results(results: Sequence[DiffResult], *, label: str) -> DiffResult:
    """Combine per-library *results* into one, under :data:`_FIELD_POLICY`.

    Field-by-field over ``dataclasses.fields(DiffResult)`` rather than a
    hand-written constructor call: ``DiffResult`` carries 62 fields today and
    grows, and a hand-written merge drops every field added after it was
    written -- silently, and in the direction that looks like success (an
    empty ledger, a ``None`` assurance, a missing contract context all read
    as "nothing to report"). An unlisted field is a hard error here, so
    adding one to ``DiffResult`` forces a decision about what it means for a
    release rather than defaulting to one library's answer.
    """
    if not results:
        raise ValueError("merge_results requires at least one result")
    merged: dict[str, object] = {}
    for f in dataclasses.fields(DiffResult):
        policy = _FIELD_POLICY.get(f.name)
        if policy is None:
            raise AssertionError(
                f"DiffResult.{f.name} has no multi-library merge policy. Add "
                f"it to compat.multi_library._FIELD_POLICY -- see that "
                f"table's docstring for why this is an error rather than a "
                f"default."
            )
        values = [getattr(r, f.name) for r in results]
        if policy == "first":
            merged[f.name] = values[0]
        elif policy == "concat":
            merged[f.name] = _concat(values)
        elif policy == "union":
            merged[f.name] = _union_sorted(values)
        elif policy == "sum":
            present = [v for v in values if v is not None]
            merged[f.name] = sum(present) if present else None  # type: ignore[arg-type]
        elif policy == "worst":
            merged[f.name] = _worst(f.name, values)
        elif policy == "all":
            merged[f.name] = all(values)
        elif policy == "any":
            merged[f.name] = any(values)
        elif policy == "assurance_block":
            merged[f.name] = merge_analysis_assurance(values)  # type: ignore[arg-type]
        elif policy == "drop":
            merged[f.name] = _field_default(f)
        else:  # pragma: no cover - guarded by test_merge_policy_values_are_known
            raise AssertionError(f"unknown merge policy {policy!r}")
    merged["library"] = label
    return DiffResult(**merged)  # type: ignore[arg-type]

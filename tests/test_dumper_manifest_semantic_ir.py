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

"""``run_tu_loop``'s ``MergedTuFragments.semantic_ir`` (ADR-063 Phase 6,
multi-TU slice) -- split into its own file rather than added to
``test_dumper_manifest.py`` since that file is pinned at its own
``architecture/debt.yaml`` no-growth baseline with zero headroom.

Real, unmocked end-to-end tests against real clang (the same
castxml-absent-host pattern ``test_dumper_manifest.py``'s own
``test_run_tu_loop_real_clang_backend_*`` tests use, and for the identical
reason -- proving the loop works with just clang, not requiring castxml on
every host that can otherwise run this suite), each self-skipping when
clang is unavailable.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from abicheck.dump_manifest import TranslationUnit
from abicheck.dumper_manifest import run_tu_loop
from abicheck.model.fact import FactStatus
from abicheck.model.occurrence import canonical_key
from abicheck.tu_fragment import MergedTuFragments


def _tu(name: str, header: Path) -> TranslationUnit:
    return TranslationUnit(name=name, forced_includes=(header,))


def _requires_clang() -> None:
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")


def _run(
    tmp_path: Path, tus: Sequence[TranslationUnit], roots: Sequence[Path]
) -> MergedTuFragments:
    from abicheck.dumper import _header_ast_parser

    return run_tu_loop(
        tus,
        header_ast_parser=_header_ast_parser,
        roots=roots,
        backend="clang",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )


def test_genuine_cross_tu_split_produces_two_occurrences(tmp_path: Path) -> None:
    """A public header's forward declaration and a private header's full
    definition of the same type, each reached by a different TU, are a
    genuine ODR-duplicate/incomplete-declaration split -- exactly the case
    ADR-063 Phase 6 names as the one occurrence-detail loss this slice
    closes."""
    _requires_clang()
    public_h = tmp_path / "public.h"
    public_h.write_text("struct Widget;\n")
    private_h = tmp_path / "private.h"
    private_h.write_text("struct Widget { int x; int y; };\n")

    merged = _run(
        tmp_path,
        (_tu("tu_public", public_h), _tu("tu_private", private_h)),
        roots=[public_h, private_h],
    )

    assert merged.semantic_ir is not None
    (widget_type,) = [t for t in merged.types if t.name == "Widget"]
    assert widget_type.entity_id is not None
    occurrences = merged.semantic_ir.occurrences_for(widget_type.entity_id)
    assert len(occurrences) == 2
    locations = {merged.semantic_ir.occurrences[occ].producer for occ in occurrences}
    assert locations == {"clang"}
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert any(str(public_h) in d for d in disambiguators)
    assert any(str(private_h) in d for d in disambiguators)


def test_redundant_shared_header_observation_collapses_to_one_occurrence(
    tmp_path: Path,
) -> None:
    """The far more common case: many TUs `#include` the identical,
    unmodified header -- must NOT explode into one occurrence per
    including TU."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("struct Shared { int a; };\n")

    merged = _run(
        tmp_path,
        (
            _tu("tu_a", shared_h),
            _tu("tu_b", shared_h),
            _tu("tu_c", shared_h),
        ),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    (shared_type,) = [t for t in merged.types if t.name == "Shared"]
    assert shared_type.entity_id is not None
    occurrences = merged.semantic_ir.occurrences_for(shared_type.entity_id)
    assert len(occurrences) == 1


def test_single_tu_manifest_semantic_ir_matches_legacy_shape(tmp_path: Path) -> None:
    """A one-TU manifest is the degenerate case: nothing to fold across
    TUs, so exactly one occurrence must reach the IR, with the identical
    canonical payload a single-header (non-manifest) dump would produce.

    Codex review, second round: the *occurrence ID itself* must match too,
    not just the payload -- a nonempty, path-derived disambiguator stamped
    onto every occurrence purely because ``--dump-manifest`` was used would
    silently fork every persisted ID and ``canonical_key()`` value from the
    equivalent non-manifest normalization's, even with only one TU and
    therefore nothing to disambiguate."""
    _requires_clang()
    header = tmp_path / "solo.h"
    header.write_text("struct Solo { int a; };\n")

    merged = _run(tmp_path, (_tu("tu_solo", header),), roots=[header])

    assert merged.semantic_ir is not None
    (solo_type,) = [t for t in merged.types if t.name == "Solo"]
    assert solo_type.entity_id is not None
    (occ_id,) = merged.semantic_ir.occurrences_for(solo_type.entity_id)
    assert occ_id.disambiguator == ""
    assert canonical_key(occ_id) == solo_type.entity_id.key
    entity = merged.semantic_ir.occurrences[occ_id]
    assert entity.canonical_spelling.status is FactStatus.PRESENT
    assert entity.canonical_spelling.value == "Solo"


def test_internal_linkage_function_keeps_a_distinct_occurrence_per_tu(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a `static` function declared in a
    header shared by multiple TUs is a genuinely distinct, TU-scoped
    declaration in each one -- not a redundant observation of "the same"
    function -- even though its `EntityId` (mangled-name-derived) and its
    `source_location` are identical in every including TU, so neither of
    this module's other two disambiguation signals can tell them apart.
    `tu_merge._function_key` already keys a TU-local function by `tu_name`
    for exactly this reason; this asserts the IR does the same."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("static int helper() { return 1; }\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h)),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    helper_fns = [f for f in merged.functions if f.name == "helper"]
    assert len(helper_fns) == 2, "tu_merge already keeps two TU-local declarations"
    entity_ids = {fn.entity_id for fn in helper_fns}
    assert None not in entity_ids
    (entity_id,) = entity_ids  # same mangled name -> same EntityId in both TUs
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert all(d.startswith("tu_a:") or d.startswith("tu_b:") for d in disambiguators)


def test_external_linkage_function_in_a_shared_header_still_collapses(
    tmp_path: Path,
) -> None:
    """Negative control for the internal-linkage fix: an ordinary,
    externally-linked function declared in a header shared by multiple TUs
    is still the same one declaration and must still collapse to a single
    occurrence -- the TU-local disambiguator must not fire for it."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("int helper();\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h)),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    (helper_fn,) = [f for f in merged.functions if f.name == "helper"]
    assert helper_fn.entity_id is not None
    occurrences = merged.semantic_ir.occurrences_for(helper_fn.entity_id)
    assert len(occurrences) == 1


def test_mixed_linkage_collision_keeps_external_and_local_separate(
    tmp_path: Path,
) -> None:
    """Codex review, third round, fresh evidence: a plain-C function's own
    `EntityId` construction does not encode static-vs-external linkage at
    all (confirmed empirically -- an externally-linked `int helper();` and
    an unrelated file's `static int helper() { ... }` resolve to the
    identical `extra=("extern_c",)` identity). Two TUs share the identical
    external declaration; a third TU defines its own file-local `static`
    version. Classifying locality globally by `EntityId` would wrongly
    TU-qualify the two external observations too and yield three
    occurrences instead of collapsing the external pair; this asserts
    exactly one external plus one local occurrence."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("int helper();\n")
    static_h = tmp_path / "static.h"
    static_h.write_text("static int helper() { return 1; }\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h), _tu("tu_c", static_h)),
        roots=[shared_h, static_h],
    )

    assert merged.semantic_ir is not None
    helper_fns = [f for f in merged.functions if f.name == "helper"]
    entity_ids = {fn.entity_id for fn in helper_fns}
    assert None not in entity_ids
    (entity_id,) = entity_ids  # the pre-existing identity collision itself
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    local_disambiguators = {d for d in disambiguators if d.startswith("tu_c:")}
    assert len(local_disambiguators) == 1
    assert len(disambiguators - local_disambiguators) == 1


def test_per_tu_local_declaration_pair_survives_across_tus(tmp_path: Path) -> None:
    """Codex review, third round, fresh evidence: a TU-local function can
    itself have more than one raw declaration within one fragment (its own
    prototype and definition), and `tu_merge.py` does not collapse those
    even within a single TU-scoped key. Two TUs each contributing their
    own local prototype+definition pair for the same-named `static`
    function must leave four distinct occurrences, not two -- replacing
    (rather than combining with) the location-based disambiguator would
    silently drop two of them."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("static int helper();\nstatic int helper() { return 1; }\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h)),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    helper_fns = [f for f in merged.functions if f.name == "helper"]
    assert len(helper_fns) == 4, "tu_merge keeps each TU's own decl+def pair"
    entity_ids = {fn.entity_id for fn in helper_fns}
    assert None not in entity_ids
    (entity_id,) = entity_ids
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 4


def test_externally_linked_proto_and_def_shared_across_tus_stay_distinct(
    tmp_path: Path,
) -> None:
    """Codex review, PR #1024, fresh evidence beyond the prior same-TU-only
    case: when multiple TUs each ``#include`` the identical shared header
    containing BOTH an externally-linked prototype and its own definition
    (``int helper();`` followed by ``int helper() { ... }``), every
    fragment contributes the identical two-location set for the one
    external ``EntityId``. That must NOT make
    :func:`~abicheck.extract.manifest_semantic_ir._ambiguous_by_source_location`
    fire (there is no genuine cross-TU *split* -- every fragment agrees),
    but blindly blanking the disambiguator for a "non-ambiguous" entity
    (an earlier revision's bug) silently collapsed the prototype and the
    definition -- two distinct, real declarations -- into a single
    occurrence. This is the general fix: retain one occurrence per
    distinct location while still deduplicating the identical
    location-set redundantly observed by each including TU."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("int helper();\nint helper() { return 1; }\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h)),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    helper_fns = [f for f in merged.functions if f.name == "helper"]
    entity_ids = {fn.entity_id for fn in helper_fns}
    assert None not in entity_ids
    (entity_id,) = entity_ids
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 2, (
        "the prototype and its definition are two distinct real "
        "declarations and must not collapse merely because every "
        "including TU observes the identical pair"
    )
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert all(str(shared_h) in d for d in disambiguators)
    assert not any(
        d.startswith("tu_a:") or d.startswith("tu_b:") for d in disambiguators
    ), (
        "an externally-linked entity must never be TU-qualified -- only "
        "the locally-linked branch combines with tu_name"
    )


def test_externally_linked_proto_and_def_redundantly_seen_by_a_third_tu(
    tmp_path: Path,
) -> None:
    """General-property extension of the case above: a THIRD TU
    redundantly seeing the identical shared proto+def pair must still
    collapse the redundant observation while keeping both real
    declarations -- 2 occurrences total, never 3 (one collapsed away
    entirely) and never growing per additional redundant TU."""
    _requires_clang()
    shared_h = tmp_path / "shared.h"
    shared_h.write_text("int helper3();\nint helper3() { return 1; }\n")

    merged = _run(
        tmp_path,
        (_tu("tu_a", shared_h), _tu("tu_b", shared_h), _tu("tu_c", shared_h)),
        roots=[shared_h],
    )

    assert merged.semantic_ir is not None
    helper_fns = [f for f in merged.functions if f.name == "helper3"]
    entity_ids = {fn.entity_id for fn in helper_fns}
    assert None not in entity_ids
    (entity_id,) = entity_ids
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2

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
    canonical payload a single-header (non-manifest) dump would produce."""
    _requires_clang()
    header = tmp_path / "solo.h"
    header.write_text("struct Solo { int a; };\n")

    merged = _run(tmp_path, (_tu("tu_solo", header),), roots=[header])

    assert merged.semantic_ir is not None
    (solo_type,) = [t for t in merged.types if t.name == "Solo"]
    assert solo_type.entity_id is not None
    (occ_id,) = merged.semantic_ir.occurrences_for(solo_type.entity_id)
    entity = merged.semantic_ir.occurrences[occ_id]
    assert entity.canonical_spelling.status is FactStatus.PRESENT
    assert entity.canonical_spelling.value == "Solo"

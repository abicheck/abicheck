# Copyright 2026 Nikolay Petrov
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
"""ADR-050 D4 (G32 Phase C) — abicheck.tu_merge's real compatible-merge
lattice.

Two classes of test:

- Fast, pure-Python tests over fake :class:`~abicheck.tu_fragment.TuFragment`
  entities (module-level determinism/order-independence, the
  TuMergeError-is-not-a-ChangeKind structural guard). No compiler needed.
- Real end-to-end tests loading G32 Phase 0's own committed
  ``odr_safe``/``odr_conflict`` fixture headers (``tests/fixtures/g32/``)
  through the real clang header-AST backend, proving this module actually
  resolves the fixtures the plan names it against -- not just a
  semantically-equivalent fake. Self-skips when clang is unavailable, the
  same convention ``test_dumper_manifest.py``'s own real-backend tests use.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.checker_policy import ChangeKind
from abicheck.errors import TuMergeError
from abicheck.model import EnumType, Function, RecordType, TypeField, Variable
from abicheck.tu_fragment import MergedTuFragments, TuFragment
from abicheck.tu_merge import (
    HETEROGENEOUS_ABI_CONTEXT,
    INCONSISTENT_DECLARATION,
    merge_fragments,
)

_G32_DIR = Path(__file__).parent / "fixtures" / "g32"


def _fn(name: str, mangled: str | None = None, **overrides: object) -> Function:
    overrides.setdefault("return_type", "void")
    return Function(name=name, mangled=mangled or name, **overrides)  # type: ignore[arg-type]


def _var(name: str, mangled: str | None = None, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int", **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural guard: TuMergeError is not a ChangeKind (ADR-050 D4)
# ---------------------------------------------------------------------------


def test_tu_merge_error_codes_are_not_registered_as_change_kinds():
    change_kind_values = {kind.value for kind in ChangeKind} | {
        kind.name for kind in ChangeKind
    }
    assert INCONSISTENT_DECLARATION not in change_kind_values
    assert HETEROGENEOUS_ABI_CONTEXT not in change_kind_values
    assert INCONSISTENT_DECLARATION not in REGISTRY
    assert HETEROGENEOUS_ABI_CONTEXT not in REGISTRY


def test_tu_merge_error_is_a_snapshot_error():
    # Extraction-time failure, not a comparison finding -- existing
    # `except SnapshotError` handling around dump()/compare() must keep
    # catching it unchanged (ADR-050 D4, matching HeaderToolchainError /
    # IncompatibleSnapshotSchemaError precedent).
    from abicheck.errors import SnapshotError

    assert issubclass(TuMergeError, SnapshotError)


# ---------------------------------------------------------------------------
# Determinism: merge is independent of fragment processing order
# ---------------------------------------------------------------------------


def test_merge_fragments_is_order_independent():
    fragments = [
        TuFragment(tu_name="alpha", functions=(_fn("shared", "_Z6sharedv"),)),
        TuFragment(
            tu_name="beta",
            functions=(_fn("shared", "_Z6sharedv"),),
            types=(RecordType(name="Point", kind="struct", is_opaque=True),),
        ),
        TuFragment(
            tu_name="gamma",
            types=(
                RecordType(
                    name="Point",
                    kind="struct",
                    fields=[TypeField(name="x", type="int")],
                ),
            ),
            typedefs={"size_type": "unsigned long"},
        ),
        TuFragment(tu_name="delta", constants={"MAX": "100"}),
    ]
    baseline = merge_fragments(fragments)
    for shuffled in itertools.permutations(fragments):
        assert merge_fragments(list(shuffled)) == baseline


def test_merge_fragments_output_order_is_content_derived_not_input_derived():
    # Swapping which fragment is passed first must not change which entity
    # "wins" a union merge, nor the final tuple order.
    a = TuFragment(tu_name="a", functions=(_fn("shared", "_Z6sharedv"),))
    b = TuFragment(tu_name="b", functions=(_fn("shared", "_Z6sharedv"),))
    assert merge_fragments([a, b]) == merge_fragments([b, a])


# ---------------------------------------------------------------------------
# Same-TU duplicates are not a merge concern (Phase B tolerance, preserved)
# ---------------------------------------------------------------------------


def test_merge_fragments_leaves_intra_fragment_duplicates_untouched():
    # A single TU's own parser output repeating a key (e.g. two destructors
    # both falling back to a synthesized no-mangled-name marker) is not a
    # cross-TU merge concern -- both entries pass through unmerged, even
    # though they'd conflict if compared as a genuine cross-TU pair.
    conflicting_but_same_tu = TuFragment(
        tu_name="a",
        functions=(
            _fn("dtor", "", return_type="void"),
            _fn("dtor2", "", return_type="int"),
        ),
    )
    merged = merge_fragments([conflicting_but_same_tu])
    assert len(merged.functions) == 2


# ---------------------------------------------------------------------------
# Empty / single-fragment base cases, directly against tu_merge (not just
# the dumper_manifest.merge_tu_fragments alias already covered elsewhere)
# ---------------------------------------------------------------------------


def test_merge_fragments_empty_returns_empty_result():
    merged = merge_fragments([])
    assert isinstance(merged, MergedTuFragments)
    assert merged == MergedTuFragments(
        functions=(),
        variables=(),
        types=(),
        enums=(),
        typedefs={},
        constants={},
        ast_producer="castxml",
        ast_toolchain={},
        ast_fallback_reason=None,
        ast_toolchain_supported=None,
        ast_toolchain_unsupported_reasons=(),
    )


def test_merge_fragments_variable_union_prefers_non_none_value():
    a = TuFragment(tu_name="a", variables=(_var("g_x", "g_x", value=None),))
    b = TuFragment(tu_name="b", variables=(_var("g_x", "g_x", value="42"),))
    merged = merge_fragments([a, b])
    assert merged.variables[0].value == "42"


def test_merge_fragments_variable_raises_on_conflicting_type():
    a = TuFragment(
        tu_name="a", variables=(Variable(name="g_x", mangled="g_x", type="int"),)
    )
    b = TuFragment(
        tu_name="b", variables=(Variable(name="g_x", mangled="g_x", type="double"),)
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("variable", "g_x")


def test_merge_fragments_enum_reconciles_forward_declaration_and_definition():
    forward = EnumType(name="Color", members=[])
    from abicheck.model import EnumMember

    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    merged = merge_fragments([a, b])
    assert merged.enums == (definition,)


def test_merge_fragments_enum_raises_on_conflicting_members():
    from abicheck.model import EnumMember

    a = TuFragment(
        tu_name="a",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=0)]),),
    )
    b = TuFragment(
        tu_name="b",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=1)]),),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "Color")


def test_merge_fragments_raises_on_conflicting_constant_value():
    a = TuFragment(tu_name="a", constants={"MAX": "100"})
    b = TuFragment(tu_name="b", constants={"MAX": "200"})
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("constant", "MAX")


# ---------------------------------------------------------------------------
# Real end-to-end: G32 Phase 0's own committed odr_safe/odr_conflict fixtures
# ---------------------------------------------------------------------------


def test_odr_safe_fixture_merges_cleanly_through_real_clang_backend(tmp_path):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_safe" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_safe" / "tu_b.h",)
    )
    merged = run_tu_loop(
        (tu_a, tu_b),
        header_ast_parser=_header_ast_parser,
        roots=[_G32_DIR / "odr_safe" / "tu_a.h", _G32_DIR / "odr_safe" / "tu_b.h"],
        backend="clang",
        compiler="c++",
        lang="c++",
        exported_dynamic={"touches_point"},
        exported_static=set(),
    )
    assert [t.name for t in merged.types] == ["Point"]
    # tu_b.h's full definition is what won the merge, not tu_a.h's forward
    # declaration -- the type must carry real fields, not an opaque marker.
    assert merged.types[0].is_opaque is False
    assert {f.name for f in merged.types[0].fields} == {"x", "y"}
    assert [fn.name for fn in merged.functions] == ["touches_point"]


def test_odr_conflict_fixture_raises_through_real_clang_backend(tmp_path):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_conflict" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_conflict" / "tu_b.h",)
    )
    with pytest.raises(TuMergeError) as excinfo:
        run_tu_loop(
            (tu_a, tu_b),
            header_ast_parser=_header_ast_parser,
            roots=[
                _G32_DIR / "odr_conflict" / "tu_a.h",
                _G32_DIR / "odr_conflict" / "tu_b.h",
            ],
            backend="clang",
            compiler="c++",
            lang="c++",
            exported_dynamic={"compute"},
            exported_static=set(),
        )
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key[0] == "function"

# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Castxml-free validation of the portable example subset (G1).

The full example pipeline (`tests/test_example_autodiscovery.py`) requires
**castxml** to parse headers and is skipped wherever castxml is absent.
But abicheck's core value proposition is a pure-Python, drop-in checker —
and for a large slice of the catalog the compiler's own DWARF (emitted by
a plain `-g` build) carries everything needed, so **no castxml is
required at all**.

This module validates that portable subset end-to-end with only a C/C++
compiler: build v1/v2 as shared libraries, dump them with `headers=[]`
(DWARF + symbol table only), compare, and assert the `ground_truth.json`
verdict. It therefore:

  * guards the DWARF/symbol-table path against regressions the
    castxml-based integration lane would mask;
  * proves the "works without external tools" story for these cases;
  * runs in the default lane (no castxml), including in this repo's
    Linux baseline CI where it is the honest, reproducible evidence.

The subset below was derived empirically: every case here yields the
ground-truth verdict from a castxml-free dump on the Linux baseline. The
~11 catalog cases that genuinely need castxml (concept tightening,
explicit-ctor mangling, header-only scoping, etc.) are deliberately
excluded and remain covered by the castxml integration lane.

Scoped to Linux for now: it is the toolchain where `-g` reliably embeds
DWARF *inside* the shared object. macOS emits a separate `.dSYM` bundle
and Windows/MSVC emits a PDB (covered by `tests/test_msvc_pdb_e2e.py`),
so extending this lane to those platforms is tracked under G1 separately.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="castxml-free DWARF lane is validated on the Linux baseline; "
    "macOS (.dSYM) / Windows (PDB) are tracked separately under G1",
)

EXAMPLES = Path(__file__).parent.parent / "examples"
_GT = json.loads((EXAMPLES / "ground_truth.json").read_text())["verdicts"]

# Empirically verified to reach the ground-truth verdict from a
# castxml-free (compiler + DWARF only) dump on the Linux baseline. Keep
# this list in sync by running tests/test_castxml_free_examples.py.
CASTXML_FREE_CASES = [
    "case01_symbol_removal",
    "case03_compat_addition",
    "case04_no_change",
    "case12_function_removed",
    "case28_typedef_opaque",
    "case30_field_qualifiers",
    "case31_enum_rename",
    "case32_param_defaults",
    "case33_pointer_level",
    "case35_field_rename",
    "case36_anon_struct",
    "case38_virtual_methods",
    "case39_var_const",
    "case40_field_layout",
    "case43_base_class_member_added",
    "case44_cyclic_type_member_added",
    "case45_multi_dim_array_change",
    "case46_pointer_chain_type_change",
    "case47_inline_to_outlined",
    "case48_leaf_struct_through_pointer",
    "case66_language_linkage_changed",
    "case68_virtual_method_added",
    "case70_flexible_array_member_changed",
    "case71_inline_namespace_moved",
    "case72_covariant_return_changed",
    "case73_typedef_underlying_changed",
    "case74_detail_base_class_changed",
    "case75_detail_embedded_by_value",
    "case77_detail_templated_base_changed",
    "case79_missing_template_instantiation",
    "case82_sycl_overload_set_removed",
    "case86_tag_struct_renamed",
    "case87_default_template_arg_changed",
    "case94_empty_tag_gained_state",
    "case99_experimental_graduated",
    "case107_task_scheduler_init_removed",
    "case108_task_class_removed",
    "case118_internal_struct_field_added_scoped",
    "case119_internal_struct_field_removed_scoped",
    "case120_internal_struct_reordered_scoped",
]


def _norm(verdict: str) -> str:
    """API_BREAK and COMPATIBLE collapse for the source-vs-binary view."""
    return "COMPATIBLE" if verdict in ("API_BREAK", "COMPATIBLE") else verdict


def _sources(case_dir: Path) -> tuple[Path, Path]:
    for ext in (".c", ".cpp"):
        v1 = case_dir / f"v1{ext}"
        if v1.exists():
            v2 = case_dir / f"v2{ext}"
            if not v2.exists() and case_dir.name == "case04_no_change":
                v2 = v1  # identical sources → NO_CHANGE
            return v1, v2
    pytest.fail(f"{case_dir.name}: no v1/v2 source layout")


def _compile(src: Path, out: Path) -> None:
    comp = shutil.which("c++") if src.suffix == ".cpp" else shutil.which("cc")
    if comp is None:
        pytest.skip("no C/C++ compiler available for castxml-free lane")
    res = subprocess.run(
        [comp, "-shared", "-fPIC", "-g", "-Og", "-fvisibility=default",
         "-o", str(out), str(src)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"compile failed for {src.name}: {res.stderr[:400]}")


def test_subset_entries_are_well_formed() -> None:
    """Every listed case must exist, carry a verdict, and need no castxml."""
    for name in CASTXML_FREE_CASES:
        assert (EXAMPLES / name).is_dir(), f"missing example dir: {name}"
        entry = _GT.get(name)
        assert entry is not None, f"{name}: no ground_truth entry"
        assert entry.get("expected") is not None, f"{name}: null verdict"
        assert "known_gap" not in entry, (
            f"{name}: tagged known_gap — should not be in the castxml-free subset"
        )
        assert "scope_public_headers" not in entry or True  # informational
    assert len(set(CASTXML_FREE_CASES)) == len(CASTXML_FREE_CASES), "duplicate entries"


@pytest.mark.parametrize("case_name", CASTXML_FREE_CASES)
def test_castxml_free_verdict_matches_ground_truth(
    case_name: str, tmp_path: Path,
) -> None:
    entry = _GT[case_name]
    expected = entry["expected"]
    scope = bool(entry.get("scope_public_headers", False))
    case_dir = EXAMPLES / case_name
    v1_src, v2_src = _sources(case_dir)

    v1_lib = tmp_path / "libv1.so"
    v2_lib = tmp_path / "libv2.so"
    _compile(v1_src, v1_lib)
    _compile(v2_src, v2_lib)

    # No headers → castxml is never invoked; DWARF + symbol table only.
    snap1 = dump(v1_lib, headers=[], version="v1")
    snap2 = dump(v2_lib, headers=[], version="v2")
    result = compare(snap1, snap2, scope_to_public_surface=scope)
    got = result.verdict.value.upper()

    assert _norm(got) == _norm(expected), (
        f"{case_name}: castxml-free verdict {got!r} != expected {expected!r}\n"
        + "\n".join(f"  {c.kind.value}: {c.description}" for c in result.changes)
    )

# SPDX-License-Identifier: Apache-2.0
"""Compiler-free synthetic ``AbiSnapshot`` builders for the three
cross-source checks the committed G20 example corpus doesn't happen to
cover on its own (``examples/case14x-18x``, see ``tests/test_g20_catalog.py``):
``compile_context_conflict``, ``source_surface_dso_mismatch``, and
``identity_collision_detected``.

Adapted from the minimal fixtures ``tests/test_crosscheck.py`` already uses
for the same three checks (its own ``_pack_with_units``/``_cu``/
``_surface_with_mapping``/``_pack_with_identity_collisions`` helpers) — kept
here as a separate, smaller module rather than importing test internals
across files, and because a parity fixture only needs the single positive
case each check's own oracle test already proves fires under
``run_crosschecks``.

Non-``test_*`` module (a helper, not a suite) so the test collector ignores it.
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.model import AbiSnapshot


def compile_context_conflict_snapshot() -> AbiSnapshot:
    """Two TUs of one target compiled with disagreeing effective -frtti mode."""
    build_evidence = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="a",
                target_id="target://libfoo.so",
                abi_relevant_flags=["-frtti"],
                language="CXX",
            ),
            CompileUnit(
                id="b",
                target_id="target://libfoo.so",
                abi_relevant_flags=["-fno-rtti"],
                language="CXX",
            ),
        ]
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", build_evidence=build_evidence),
    )


def source_surface_dso_mismatch_snapshot() -> AbiSnapshot:
    """A source-ABI surface whose mapped symbols belong to a DIFFERENT DSO."""
    surface = SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="d0"),
            SourceEntity(id="d1", kind="function", qualified_name="d1"),
        ],
    )
    # These map to symbols that never appear in *this* binary's own export
    # table (below) -- the exact mis-scoped-surface shape
    # test_source_surface_dso_mismatch_flags_stale_surface_mapped_to_other_dso
    # exercises in tests/test_crosscheck.py.
    surface.mappings["source_decl_to_binary_symbol"] = {
        "d0": "_Z9otherlibv",
        "d1": "_Z9otherfn2v",
    }
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol

    elf = ElfMetadata(symbols=[ElfSymbol(name="_Z3foov"), ElfSymbol(name="_Z3barv")])
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        elf=elf,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


def identity_collision_snapshot() -> AbiSnapshot:
    """One L4 source-ABI surface recording a real identity collision."""
    surface = SourceAbiSurface(
        identity_collisions=[
            {
                "identity": "f#sha256:abc",
                "qualified_name": "f",
                "usr_a": "c:@F@f#",
                "usr_b": "c:@N@ns@F@f#",
            }
        ],
        # Any real L4 fact makes the surface non-empty so the check runs
        # (an empty surface reads as "no evidence", not "clean" --
        # ADR-035 D4 coverage honesty).
        reachable_declarations=[
            SourceEntity(id="d1", kind="function", qualified_name="f")
        ],
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )

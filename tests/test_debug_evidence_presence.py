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

"""Bug class ``evidence.container_presence_read_as_evidence_content``.

The reported instance: a library with no ``.debug_info`` at all -- ``readelf``
finds none, and abicheck's own check list says "no debug info: checks limited
to symbol-level" in the same run -- still reported ``L1: present /
confidence: high / detail: DWARF`` in the comparison JSON.

The mechanism generalizes past that one row, which is why this module tests
the class rather than the row. Both debug-metadata classes are plain
dataclasses with no ``__bool__``, so **every instance is truthy**, and every
ELF dump attaches one unconditionally -- including the symbols-only fallback
that logs "no DWARF debug info" while attaching it, and
``cheap_dwarf_presence_metadata``, which returns that same empty shape on any
extraction exception. So ``bool(snap.dwarf)`` and ``snap.dwarf is not None``
answer "was an object attached", which is always yes, and never "was debug
info collected". Four sites read the container that way; each is enumerated
below against its own real consumer, so a regression at any one of them fails
here rather than only at whichever site the next report happens to come from.

The fourth site needed a narrower question still (Codex review).
``AdvancedDwarfMetadata.has_dwarf`` is itself overloaded: the presence-only
paths set it from a section lookup and parse no payload, so even the corrected
"has debug info" reading declared the ``advanced_dwarf`` detector supported
over empty dicts. ``advanced_facts_collected`` answers from the payload that
detector actually reads.

The oracle is deliberately not ``debug_info_present`` itself: expectations are
derived from the ``has_dwarf`` attributes directly, and the end-to-end cases
are anchored to a real ``g++`` build whose debug info is settled by ``-g`` vs
``-g0`` rather than by anything this codebase computes.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from abicheck.buildsource.evidence_report import intrinsic_coverage
from abicheck.buildsource.model import CoverageStatus, LayerConfidence
from abicheck.checker import compare
from abicheck.cli import main
from abicheck.diff_helpers import typedef_flat_map_is_dwarf_qualified
from abicheck.model import AbiSnapshot, debug_info_present
from abicheck.model.dwarf_facts import (
    AdvancedDwarfMetadata,
    DwarfMetadata,
    ToolchainInfo,
    advanced_facts_collected,
)
from abicheck.model.elf_facts import ElfMetadata
from abicheck.service import run_dump
from abicheck.surface_graph import _evidence_tier

# Every way a debug-metadata slot can be filled, and -- independently of this
# codebase -- whether that filling represents collected evidence. "empty" is
# the shape the production dump attaches to a stripped binary; it is the one
# the buggy predicates counted as evidence.
_DWARF_STATES: dict[str, tuple[DwarfMetadata | None, bool]] = {
    "absent": (None, False),
    "empty": (DwarfMetadata(), False),
    "empty_with_payload_keys": (DwarfMetadata(structs={}, enums={}), False),
    "collected": (DwarfMetadata(has_dwarf=True), True),
}
# ``AdvancedDwarfMetadata.has_dwarf`` is overloaded, so the advanced slot needs
# a third state the dwarf slot does not: the presence-only paths (``--depth
# binary``, ``symbols_only``) set the flag from a section lookup and parse no
# payload at all. The second element is "the binary has debug info" (what the
# L1 row asks); the third is "advanced facts were collected" (what a detector
# consuming those fields needs), and ``presence_only`` is exactly the case
# where the two answers differ.
_ADVANCED_STATES: dict[str, tuple[AdvancedDwarfMetadata | None, bool, bool]] = {
    "absent": (None, False, False),
    "empty": (AdvancedDwarfMetadata(), False, False),
    "presence_only": (AdvancedDwarfMetadata(has_dwarf=True), True, False),
    "collected": (
        AdvancedDwarfMetadata(has_dwarf=True, packed_structs={"S"}),
        True,
        True,
    ),
}


def _snapshot(dwarf, advanced, *, from_headers: bool = False) -> AbiSnapshot:
    """A minimally-populated ELF snapshot with the given debug slots."""
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        platform="elf",
        elf=ElfMetadata(),
        dwarf=dwarf,
        dwarf_advanced=advanced,
        from_headers=from_headers,
    )


def test_empty_metadata_is_truthy_which_is_why_this_class_exists():
    """The premise, stated as a test so it cannot silently stop holding.

    If either class ever grows a ``__bool__``, the original predicates would
    start working by accident and this module's reason for existing changes.
    """
    assert bool(DwarfMetadata()) is True
    assert bool(AdvancedDwarfMetadata()) is True
    assert DwarfMetadata().has_dwarf is False
    assert AdvancedDwarfMetadata().has_dwarf is False


@pytest.mark.parametrize("advanced_name", sorted(_ADVANCED_STATES))
@pytest.mark.parametrize("dwarf_name", sorted(_DWARF_STATES))
def test_predicate_answers_content_not_presence(dwarf_name, advanced_name):
    """Exhaustive over the whole slot domain, not just the reported shape."""
    dwarf, dwarf_is_evidence = _DWARF_STATES[dwarf_name]
    advanced, advanced_is_evidence, _ = _ADVANCED_STATES[advanced_name]

    assert debug_info_present(dwarf) is dwarf_is_evidence
    assert debug_info_present(advanced) is advanced_is_evidence
    assert debug_info_present(dwarf, advanced) is (
        dwarf_is_evidence or advanced_is_evidence
    )
    # Order must not matter, and the empty call is False rather than a crash.
    assert debug_info_present(advanced, dwarf) is (
        dwarf_is_evidence or advanced_is_evidence
    )
    assert debug_info_present() is False


def test_oracle_is_not_vacuous():
    """Guard against an expectation table that accidentally became constant.

    Without this, a `debug_info_present` returning a fixed value would pass
    the sweep above if the oracle had collapsed the same way.
    """
    expected = {e for _, e in _DWARF_STATES.values()} | {
        e for _, e, _c in _ADVANCED_STATES.values()
    }
    assert expected == {True, False}
    # The advanced slot's two questions must genuinely disagree somewhere, or
    # `presence_only` would not be testing anything.
    assert any(e and not c for _m, e, c in _ADVANCED_STATES.values())


@pytest.mark.parametrize("advanced_name", sorted(_ADVANCED_STATES))
@pytest.mark.parametrize("dwarf_name", sorted(_DWARF_STATES))
def test_l1_coverage_row_tracks_collected_evidence(dwarf_name, advanced_name):
    """The reported row itself, over the same exhaustive domain.

    ``status``, ``confidence`` and ``detail`` must agree with each other: the
    report claimed all three at once ("present / high / DWARF"), so a fix that
    corrected only ``status`` would leave a reader just as misled.
    """
    dwarf, dwarf_is_evidence = _DWARF_STATES[dwarf_name]
    advanced, advanced_is_evidence, _ = _ADVANCED_STATES[advanced_name]
    has_evidence = dwarf_is_evidence or advanced_is_evidence

    rows = {row.layer: row for row in intrinsic_coverage(_snapshot(dwarf, advanced))}
    l1 = rows["L1"]

    if has_evidence:
        assert l1.status is CoverageStatus.PRESENT
        assert l1.confidence is LayerConfidence.HIGH
        assert l1.detail == "DWARF"
    else:
        assert l1.status is CoverageStatus.NOT_COLLECTED
        assert l1.confidence is LayerConfidence.UNKNOWN
        assert l1.detail == ""

    # L0 must stay independent: the fix must not have coupled the rows.
    assert rows["L0"].status is CoverageStatus.PRESENT


@pytest.mark.parametrize("dwarf_name", sorted(_DWARF_STATES))
def test_surface_graph_evidence_tier_tracks_collected_evidence(dwarf_name):
    """Second site: ``ELF_ONLY`` was unreachable on the normal dump path.

    Every ELF dump attaches a metadata object, so an ``is not None`` tier
    check labelled a stripped binary DWARF-aware and no snapshot could ever
    report the ELF-only tier it was built to describe.
    """
    dwarf, dwarf_is_evidence = _DWARF_STATES[dwarf_name]
    tier = _evidence_tier(_snapshot(dwarf, None))
    assert tier == ("dwarf_aware" if dwarf_is_evidence else "elf_only")
    # A header-parsed snapshot still outranks both, whatever the debug slot.
    assert _evidence_tier(_snapshot(dwarf, None, from_headers=True)) == "header_aware"


@pytest.mark.parametrize("dwarf_name", sorted(_DWARF_STATES))
def test_typedef_qualified_trust_requires_collected_dwarf(dwarf_name):
    """Third site: a stripped, header-less snapshot claimed DWARF key shape.

    The helper's own premise is "DWARF keys typedefs by the full qualified
    name", which only holds if DWARF was parsed at all.
    """
    dwarf, dwarf_is_evidence = _DWARF_STATES[dwarf_name]
    assert typedef_flat_map_is_dwarf_qualified(_snapshot(dwarf, None)) is (
        dwarf_is_evidence
    )
    # ``from_headers`` still wins, unchanged by this fix.
    assert (
        typedef_flat_map_is_dwarf_qualified(_snapshot(dwarf, None, from_headers=True))
        is False
    )


@pytest.mark.parametrize("advanced_name", sorted(_ADVANCED_STATES))
def test_advanced_dwarf_detector_support_tracks_collected_evidence(advanced_name):
    """Fourth site: the detector declared itself supported over empty dicts.

    It produced no changes either way, so only the *reported* support status
    was wrong -- which is exactly the failure this class is about, and exactly
    the kind a findings-only assertion would miss.

    ``presence_only`` is the case the coarser ``has_dwarf`` check still got
    wrong after the first three sites were fixed (Codex review): a
    ``--depth binary`` scan of a binary that *does* carry DWARF sets the
    advanced flag from a section lookup while parsing none of the fields this
    detector reads, so the flag said "supported" over empty dicts. The gate
    asks about collected payload instead.
    """
    advanced, _evidence, facts_collected = _ADVANCED_STATES[advanced_name]
    snap = _snapshot(DwarfMetadata(), advanced)
    result = compare(snap, snap)

    entry = next(
        r for r in (result.detector_results or []) if r.name == "advanced_dwarf"
    )
    # ``not_evaluated`` is the field that separates "ran, found nothing" from
    # "never ran" -- the exact distinction the presence check was collapsing.
    assert entry.not_evaluated is not facts_collected
    assert entry.changes_count == 0
    if not facts_collected:
        # A declined detector must carry the gate's reason, not a silent zero.
        assert entry.coverage_gap


# ── End to end, against a real toolchain ────────────────────────────────────
#
# The in-process cases above build their snapshots by hand. This one settles
# the question the report actually asked -- what a *real* stripped library
# reports -- with the debug info decided by `g++ -g` vs `-g0` rather than by
# anything under test.

_NEEDS_GPP = pytest.mark.skipif(
    shutil.which("g++") is None, reason="needs g++ to build a real shared library"
)
#: The CLI case below passes ``--header``, which runs the **default** header-AST
#: backend -- castxml. Guarding only on ``g++`` left it failing rather than
#: skipping on a host that has a compiler but no castxml (Codex review), which
#: tests/CLAUDE.md forbids for a test selected by the default lane.
_NEEDS_CASTXML = pytest.mark.skipif(
    shutil.which("castxml") is None,
    reason="`compare --header` runs the default castxml header backend",
)


@_NEEDS_GPP
@pytest.mark.parametrize("debug_flag,expect_evidence", [("-g0", False), ("-g", True)])
def test_real_library_l1_row_matches_its_actual_debug_info(
    tmp_path, debug_flag, expect_evidence
):
    """Both directions, so the fix cannot be 'always report not_collected'."""
    src = tmp_path / "lib.cpp"
    src.write_text("namespace lib { struct S { int a; }; int f(S s){return s.a;} }\n")
    lib = tmp_path / "libx.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", debug_flag, "-o", str(lib), str(src)],
        check=True,
        capture_output=True,
    )

    snap = run_dump(lib, "elf")

    # Independent check of the ground truth: the section really is/isn't there.
    raw = lib.read_bytes()
    assert (b".debug_info" in raw) is expect_evidence

    rows = {row.layer: row for row in intrinsic_coverage(snap)}
    assert (rows["L1"].status is CoverageStatus.PRESENT) is expect_evidence
    assert debug_info_present(snap.dwarf, snap.dwarf_advanced) is expect_evidence
    # The production dump attaches a metadata object either way -- the precise
    # condition that made the original predicate wrong.
    assert snap.dwarf is not None


@_NEEDS_GPP
@_NEEDS_CASTXML
@pytest.mark.integration
@pytest.mark.parametrize("debug_flag,expect_present", [("-g0", False), ("-g", True)])
def test_compare_json_layer_coverage_matches_actual_debug_info(
    tmp_path, debug_flag, expect_present
):
    """The public surface the report came from, not an internal helper.

    The defect was read off ``compare``'s emitted JSON -- ``{"layer": "L1",
    "status": "present", "confidence": "high", "detail": "DWARF"}`` on a
    library with no ``.debug_info``. Every other test in this module stops at
    ``intrinsic_coverage()``, which is one projection short of that: it cannot
    catch a regression in how the row is carried through
    ``service_compare_pipeline`` -> ``reporter``, and it is not the artifact a
    user reads. This runs the real CLI and asserts on the real report.

    Parametrized both ways so "always not_collected" fails, and the ground
    truth is settled by ``g++ -g``/``-g0`` plus an independent look for the
    section in the file's own bytes.
    """
    header = tmp_path / "lib.hpp"
    header.write_text(
        "#pragma once\nnamespace lib { struct S { int a; }; int f(S); }\n"
    )
    src = tmp_path / "lib.cpp"
    src.write_text('#include "lib.hpp"\nnamespace lib { int f(S s){return s.a;} }\n')
    lib = tmp_path / "libx.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", debug_flag, "-o", str(lib), str(src)],
        check=True,
        capture_output=True,
    )
    assert (b".debug_info" in lib.read_bytes()) is expect_present

    # `--header` is what activates the evidence-coverage section, and it also
    # matches the shape the defect was reported from (a real library compared
    # against its own public headers).
    report = tmp_path / "out.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(lib),
            str(lib),
            "--header",
            str(header),
            "-o",
            f"json={report}",
        ],
    )
    assert report.exists(), result.output

    rows = {r["layer"]: r for r in json.loads(report.read_text())["layer_coverage"]}
    l1 = rows["L1"]
    # All three fields together: the report claimed all three at once, so a fix
    # that corrected only `status` would leave a reader just as misled.
    if expect_present:
        assert (l1["status"], l1["confidence"], l1["detail"]) == (
            "present",
            "high",
            "DWARF",
        )
    else:
        assert (l1["status"], l1["confidence"], l1["detail"]) == (
            "not_collected",
            "unknown",
            "",
        )
    # L0 stays independent -- the binary is there in both runs.
    assert rows["L0"]["status"] == "present"


#: One populated field per family `dwarf_advanced.diff_advanced_dwarf` reads,
#: named by the sub-diff that consumes it. Enumerated rather than spot-checked
#: because omitting a family from `advanced_facts_collected` is a **false
#: negative**: the gate requires it on both sides, so a miss skips the entire
#: detector and real drift in that family is never reported. The three
#: `toolchain` flag sets were missed exactly that way in the first revision.
_CONSUMED_ADVANCED_FIELDS = {
    "_diff_calling_conventions": {"calling_conventions": {"_Z1fv": "fastcall"}},
    "_diff_callee_saved_regs": {"callee_saved_regs": {"_Z1fv": frozenset({"rbx"})}},
    "_diff_value_abi_traits/traits": {"value_abi_traits": {"_Z1fv": "trivial"}},
    "_diff_value_abi_traits/sizes": {"return_value_sizes": {"_Z1fv": 16}},
    "_diff_value_abi_traits/sret": {"return_memory_classified": {"_Z1fv"}},
    "_diff_struct_packing/packed": {"packed_structs": {"S"}},
    "_diff_struct_packing/names": {"all_struct_names": {"S"}},
    "_diff_frame_registers": {"frame_registers": {"_Z1fv": "rbp"}},
    "_diff_toolchain_flags": {"toolchain": ToolchainInfo(abi_flags={"-fshort-enums"})},
    "_diff_vector_abi_flags": {
        "toolchain": ToolchainInfo(vector_abi_flags={"simdlen"})
    },
    "_diff_wchar_flags": {"toolchain": ToolchainInfo(wchar_flags={"-fshort-wchar"})},
    # Not a consumed *finding* but the discriminator for a parse that ran and
    # established nothing: `dwarf_advanced` sets it from the ELF header, the
    # presence-only helpers leave it "". Without it such a snapshot reads as
    # presence-only and disables the detector on both sides.
    "successful parse, no findings": {"target_arch": "x86_64"},
}


@pytest.mark.parametrize("sub_diff", sorted(_CONSUMED_ADVANCED_FIELDS))
def test_every_field_the_advanced_detector_reads_counts_as_collected(sub_diff):
    """A snapshot carrying only this field must read as collected.

    Stated per consumed family rather than as one example, because the failure
    mode is silent: a family left out of the predicate disables the detector
    wholesale, and no test that only checks a *different* field would notice.
    """
    meta = AdvancedDwarfMetadata(has_dwarf=True, **_CONSUMED_ADVANCED_FIELDS[sub_diff])
    assert advanced_facts_collected(meta) is True


def test_presence_only_advanced_metadata_is_not_collected():
    """The complement, so the predicate cannot be satisfied by returning True.

    This is the shape the presence-only dump paths produce: the flag set from a
    section lookup, every payload field empty.
    """
    assert advanced_facts_collected(AdvancedDwarfMetadata(has_dwarf=True)) is False
    assert advanced_facts_collected(AdvancedDwarfMetadata()) is False
    assert advanced_facts_collected(None) is False

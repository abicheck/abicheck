# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the (now closed) pattern_scan gap (plan §3 #6, Phase 2b).

``test_engine_primitive_call_sites.py`` proves ``find_pattern_facts`` has exactly
two production callers now: ``scan_engine.py`` and
``workflows/pattern_preprocessor_scan.py``. This module proves the positive
side concretely and cheaply -- ``find_pattern_facts`` is pure lexical text scanning
(no compiler, no castxml) and really does find an ABI-risk construct scan
surfaces today, over a tiny fixture file, and that the same construct now
also reaches ``compare()``'s own JSON report.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.pattern_facts import find_pattern_facts

from .gaps import EXPECTED_GAPS
from .runner import compare_json, invoke_cli


def test_scan_files_finds_explicit_template_instantiation(tmp_path: Path) -> None:
    header = tmp_path / "risky.hpp"
    header.write_text("template class Widget<int>;\n", encoding="utf-8")

    result = find_pattern_facts([tmp_path])
    kinds = {f.kind.value for f in result.facts}
    assert "explicit_template_instantiation" in kinds
    assert result.files_scanned == 1


def test_pattern_scan_no_longer_a_registered_gap() -> None:
    """`gaps.py` is the migration's definition of done: a capability
    `compare` has reached must not still be listed as scan-only."""
    assert "pattern_scan" not in EXPECTED_GAPS


def test_compare_has_no_pattern_scan_cli_surface() -> None:
    """ADR-068 D4/D5: the stage is automatic, evidence-gated per side --
    never a CLI opt-in flag, even now that `compare` reaches it."""
    result = invoke_cli("compare", "--help-all")
    assert result.exit_code == 0
    assert "pattern-scan" not in result.output.lower()
    assert "pattern_scan" not in result.output.lower()


def _side(tmp_path: Path, name: str, body: str, version: str):
    from abicheck.model import AbiSnapshot, Function

    header = tmp_path / name
    header.write_text(body, encoding="utf-8")
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name="widget_get",
                mangled="widget_get",
                return_type="int",
                source_header=str(header),
            )
        ],
    )


def test_compare_of_stored_snapshots_declines_to_re_derive_from_disk(
    tmp_path: Path,
) -> None:
    """`compare` of two **stored** snapshots must not re-characterise either
    side from today's filesystem.

    This test asserted ``introduced`` until the source-read licence landed,
    which is exactly the P1 defect it was unwittingly pinning: both operands
    are `.json` snapshots, and the construct was "found" only by reopening the
    ``source_header`` path they record. On a runner where that path holds
    something else -- the normal case for a baseline published from another
    checkout -- the same run would have reported a different history. A stored
    side now reports the honest ``not_evaluated``, with the reason stated in
    the block's per-check ``coverage``. See
    ``tests/test_stored_snapshot_source_licence.py`` for the registered bug
    classes, and the live-side case below for the capability itself.
    """
    from abicheck.serialization import snapshot_to_json

    old = _side(tmp_path, "old.hpp", "int widget_get(void);\n", "1.0")
    new = _side(tmp_path, "new.hpp", "template class Widget<int>;\n", "2.0")
    old_path = tmp_path / "old.abi.json"
    new_path = tmp_path / "new.abi.json"
    old_path.write_text(snapshot_to_json(old), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new), encoding="utf-8")

    block = compare_json(old_path, new_path)["pattern_preprocessor_scan"]
    assert block["pattern"]["escalation_evolution"] == {}
    coverage = block["coverage"]["pattern_escalation"]
    for side in ("old", "new"):
        assert coverage[side]["established"] is False
        assert "provenance" in coverage[side]["reason"]


def test_live_sides_still_surface_a_pattern_scan_escalation_as_introduced(
    tmp_path: Path,
) -> None:
    """The capability itself, unchanged: when both sides really were extracted
    from today's tree, the construct `find_pattern_facts` finds still reaches
    the folded result as ``introduced`` -- and `report/pattern_preprocessor_
    scan.py` carries that map into the JSON report verbatim, which is why this
    asserts on the folded result rather than re-dumping a real binary."""
    from abicheck.workflows.pattern_preprocessor_scan import (
        compute_pattern_preprocessor_scan,
    )

    old = _side(tmp_path, "old.hpp", "int widget_get(void);\n", "1.0")
    new = _side(tmp_path, "new.hpp", "template class Widget<int>;\n", "2.0")
    old.live_source_evidence = True
    new.live_source_evidence = True

    result = compute_pattern_preprocessor_scan(old, new)
    assert (
        result.pattern_escalation_evolution.get("explicit_template_instantiation")
        == "introduced"
    )
    assert (
        result.to_dict()["pattern"]["escalation_evolution"][
            "explicit_template_instantiation"
        ]
        == "introduced"
    )

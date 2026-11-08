"""ADR-090: a compatibility claim without type-level evidence is UNCONFIRMED.

Exhaustive over a small domain, checked against an oracle written from the
ADR's D1 text rather than from the implementation's helpers:

* each side is one of five evidence shapes: a hand-built model surface (no
  binary metadata), a symbols-only ELF dump, an ELF dump with DWARF, an ELF
  dump with a parsed header AST, and an ELF dump with both;
* the change-derived verdict is each of the five ``Verdict`` values;
* the pair is or is not one identical input;
* ``assurance.unconfirmed`` is ``fail`` or ``warn``.

The oracle: only an ELF side with neither DWARF nor headers lacks type-level
evidence; a run is triggered when its verdict is a compatibility claim, some
side lacks evidence, and it is not an identical pair whose sides both lack
it. A triggered ``fail`` run exits 1 (folded with ``max``, so a 2/4 never
moves); every surface states ``UNCONFIRMED``.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.checker_types import DiffResult
from abicheck.dwarf_metadata import DwarfMetadata
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.type_evidence import type_evidence_facts
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.exit_decision import ExitReason, resolve_compare_exit_decision

#: side shape -> (is a binary operand, has DWARF, has headers)
SHAPES: dict[str, tuple[bool, bool, bool]] = {
    "model": (False, False, False),
    "symbols_only": (True, False, False),
    "dwarf": (True, True, False),
    "headers": (True, False, True),
    "dwarf_headers": (True, True, True),
}
CLAIMS = {"NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"}
LEGACY_EXIT = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 0,
    "COMPATIBLE_WITH_RISK": 0,
    "API_BREAK": 2,
    "BREAKING": 4,
}


def _snapshot(shape: str) -> AbiSnapshot:
    binary, dwarf, headers = SHAPES[shape]
    kwargs: dict[str, object] = {}
    if binary:
        kwargs["elf"] = ElfMetadata()
    if dwarf:
        kwargs["dwarf"] = DwarfMetadata(has_dwarf=True)
    if headers:
        kwargs["from_headers"] = True
    return AbiSnapshot(library="libx.so.1", version="1", **kwargs)


def _lacks(shape: str) -> bool:
    binary, dwarf, headers = SHAPES[shape]
    return binary and not dwarf and not headers


def _oracle_triggered(old: str, new: str, verdict: str, identical: bool) -> bool:
    if verdict not in CLAIMS:
        return False
    if not (_lacks(old) or _lacks(new)):
        return False
    return not (identical and _lacks(old) and _lacks(new))


CELLS = list(
    itertools.product(
        SHAPES, SHAPES, [v.value for v in Verdict], [False, True], ["fail", "warn"]
    )
)


@pytest.mark.parametrize(("old", "new", "verdict", "identical", "mode"), CELLS)
def test_unconfirmed_trigger_and_exit_match_the_adr_oracle(
    old: str, new: str, verdict: str, identical: bool, mode: str
) -> None:
    from abicheck.policy.unconfirmed_compatibility import (
        report_compatibility,
        unconfirmed_block,
    )

    result = DiffResult(
        library="libx.so.1",
        old_version="1",
        new_version="2",
        changes=[],
        verdict=Verdict(verdict),
    )
    result.type_evidence = type_evidence_facts(
        _snapshot(old), _snapshot(new), identical_inputs=identical
    )
    result.unconfirmed_mode = mode  # type: ignore[assignment]
    triggered = _oracle_triggered(old, new, verdict, identical)

    assert (report_compatibility(result) == "UNCONFIRMED") is triggered
    assert (unconfirmed_block(result) is not None) is triggered

    decision = resolve_compare_exit_decision(result, EffectiveGate.from_severity(None))
    expected_axis = 1 if (triggered and mode == "fail") else 0
    assert decision.unconfirmed_contribution == expected_axis
    assert decision.code == max(LEGACY_EXIT[verdict], expected_axis)
    if expected_axis and LEGACY_EXIT[verdict] == 0:
        assert decision.reasons == (ExitReason.UNCONFIRMED_COMPATIBILITY,)
    # Never a clean claim with exit 0 under the default mode.
    if mode == "fail" and triggered:
        assert decision.code != 0


def test_unevaluated_result_never_triggers() -> None:
    """A hand-built result with no recorded fact keeps its verdict."""
    from abicheck.policy.unconfirmed_compatibility import is_unconfirmed

    result = DiffResult(
        library="l",
        old_version="1",
        new_version="2",
        changes=[],
        verdict=Verdict.NO_CHANGE,
    )
    assert result.type_evidence is None
    assert not is_unconfirmed(result)


def test_no_baseline_audit_never_triggers() -> None:
    """`--no-baseline` compares nothing, so it asserts no compatibility."""
    from abicheck.policy.unconfirmed_compatibility import is_unconfirmed

    result = DiffResult(
        library="l",
        old_version="1",
        new_version="2",
        changes=[],
        verdict=Verdict.NO_CHANGE,
    )
    result.type_evidence = type_evidence_facts(None, _snapshot("symbols_only"))
    assert not is_unconfirmed(result)


def test_stored_stripped_snapshot_lacks_type_evidence(tmp_path) -> None:
    """Maintainer answer 3, the stored route first: a symbols-only snapshot
    round-tripped through JSON still reads as lacking type-level evidence,
    and a DWARF one still reads as carrying it."""
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_encode import snapshot_to_json

    for shape, expected in (
        ("symbols_only", False),
        ("dwarf", True),
        ("headers", True),
    ):
        path = tmp_path / f"{shape}.json"
        path.write_text(snapshot_to_json(_snapshot(shape)), encoding="utf-8")
        loaded = load_snapshot(path)
        assert type_evidence_facts(loaded, loaded).new is expected, shape


def test_snapshot_missing_the_fields_reads_as_no_evidence(tmp_path) -> None:
    """A stored ELF snapshot with the evidence keys removed reads at their
    absent defaults: no type-level evidence, never clean."""
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_encode import snapshot_to_json

    doc = json.loads(snapshot_to_json(_snapshot("headers")))
    payload = doc["sections"]["debug"]["payload"]
    assert payload.pop("from_headers") is True
    path = tmp_path / "old_schema.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert type_evidence_facts(None, load_snapshot(path)).new is False


@pytest.mark.parametrize(
    "old,new",
    [
        ("symbols_only", "symbols_only"),
        ("dwarf", "symbols_only"),
        ("symbols_only", "headers"),
    ],
)
def test_checker_compare_records_the_fact_and_reports_unconfirmed(
    old: str, new: str
) -> None:
    """Through the typed `checker.compare` and the JSON renderer: the fact is
    recorded on every pairwise route, and the report says UNCONFIRMED with the
    observed verdict preserved."""
    from abicheck import checker
    from abicheck.report.diff_report import to_json

    o = _snapshot(old)
    n = _snapshot(new)
    n.version = "2"
    result = checker.compare(o, n, scope_to_public_surface=False)
    assert result.type_evidence is not None
    doc = json.loads(to_json(result))
    if result.verdict.value in CLAIMS:
        assert doc["verdict"] == "UNCONFIRMED"
        assert doc["unconfirmed"]["observed_verdict"] == result.verdict.value
        assert doc["run_outcome"]["compatibility"] == "UNCONFIRMED"
        assert doc["exit"]["code"] >= 1
        assert doc["release_recommendation"]["version_bump"] == "undetermined"

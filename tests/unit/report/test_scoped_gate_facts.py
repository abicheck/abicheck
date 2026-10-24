# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Scoped-gate facts: built once in the builder, read by renderers.

The oracle for the JUnit assertions is the :class:`ScopedGateFacts` value
itself, not the renderer's own helper, so a renderer that drops or re-derives
a field fails here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot
from abicheck.report.document_builder import build_report_envelope
from abicheck.report.junit_properties import add_scoped_properties
from abicheck.report.scoped_gate_facts import ScopedGateFacts, build_scoped_gate_facts


def _result(**scoped: object) -> DiffResult:
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so.1",
        changes=[],
        verdict=Verdict.BREAKING,
    )
    for name, value in scoped.items():
        setattr(result, name, value)
    return result


def _snapshot(version: str) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so.1", version=version)


SCOPED_CASES = [
    pytest.param({}, id="unscoped"),
    pytest.param(
        {
            "scoped_verdict": Verdict.COMPATIBLE,
            "gate_scope": "used_by",
            "used_by": [{"app": "/bin/myapp"}],
        },
        id="used-by",
    ),
    pytest.param(
        {"scoped_verdict": Verdict.BREAKING, "scoped_exit_code": 0},
        id="exit-code-without-scheme",
    ),
    pytest.param(
        {
            "scoped_verdict": Verdict.BREAKING,
            "scoped_exit_code": 4,
            "scoped_exit_code_scheme": "severity",
        },
        id="exit-code-with-scheme",
    ),
    pytest.param(
        {
            "scoped_verdict": Verdict.COMPATIBLE,
            "required_symbols": {"verdict": "BREAKING"},
        },
        id="required-symbols",
    ),
]


def _props(element: ET.Element) -> dict[str, str]:
    return {
        p.get("name"): p.get("value")  # type: ignore[misc]
        for p in element.findall("property")
    }


@pytest.mark.parametrize("scoped", SCOPED_CASES)
def test_envelope_carries_the_builder_fact(scoped: dict[str, object]) -> None:
    result = _result(**scoped)
    envelope = build_report_envelope(result, _snapshot("1.0"), _snapshot("2.0"))
    assert envelope.scoped_gate == build_scoped_gate_facts(result)


@pytest.mark.parametrize("scoped", SCOPED_CASES)
def test_junit_properties_are_the_fact(scoped: dict[str, object]) -> None:
    result = _result(**scoped)
    envelope = build_report_envelope(result, _snapshot("1.0"), _snapshot("2.0"))
    facts = envelope.scoped_gate
    props = ET.Element("properties")
    add_scoped_properties(props, result, envelope)
    emitted = _props(props)
    if facts is None:
        assert emitted == {}
        return
    expected: dict[str, str] = {
        "abicheck.gate_verdict": facts.gate_verdict,
        "abicheck.full_library_verdict": facts.full_library_verdict,
        "abicheck.scoped_verdict": facts.gate_verdict,
        "abicheck.relevant_finding_count": str(facts.relevant_finding_count),
        "abicheck.unrelated_finding_count": str(facts.unrelated_finding_count),
    }
    if facts.gate_scope is not None:
        expected["abicheck.gate_scope"] = facts.gate_scope
    if facts.gate_exit_code is not None:
        scheme = str(facts.gate_exit_code_scheme)
        expected["abicheck.gate_exit_code"] = str(facts.gate_exit_code)
        expected["abicheck.gate_exit_code_scheme"] = scheme
        expected["abicheck.scoped_exit_code"] = str(facts.gate_exit_code)
        expected["abicheck.scoped_exit_code_scheme"] = scheme
    if facts.used_by_app_count is not None:
        expected["abicheck.used_by_app_count"] = str(facts.used_by_app_count)
    if facts.required_symbol_contract_verdict is not None:
        expected["abicheck.required_symbol_contract_verdict"] = (
            facts.required_symbol_contract_verdict
        )
    assert emitted == expected


@pytest.mark.parametrize("scoped", SCOPED_CASES)
def test_direct_caller_without_envelope_emits_the_same_properties(
    scoped: dict[str, object],
) -> None:
    result = _result(**scoped)
    envelope = build_report_envelope(result, _snapshot("1.0"), _snapshot("2.0"))
    with_envelope = ET.Element("properties")
    without_envelope = ET.Element("properties")
    add_scoped_properties(with_envelope, result, envelope)
    add_scoped_properties(without_envelope, result)
    assert ET.tostring(with_envelope) == ET.tostring(without_envelope)


def test_unscoped_run_has_no_fact() -> None:
    assert build_scoped_gate_facts(_result()) is None


def test_relevant_count_includes_scoped_only_and_missing_members() -> None:
    result = _result(
        scoped_verdict=Verdict.COMPATIBLE,
        scoped_only_changes=["a", "b"],
        scoped_missing_labels=["sym"],
    )
    facts = build_scoped_gate_facts(result)
    assert isinstance(facts, ScopedGateFacts)
    assert facts.relevant_finding_count == 3
    assert facts.unrelated_finding_count == 0

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

"""JUnit ``<testcase>`` ``<properties>`` writers (ADR-061 ``report`` layer).

``junit_report.py`` is a pre-ADR-061 flat module at its own debt baseline, so
the property writers live here rather than growing it: each answers "what
extra facts does this testcase carry", which is report-projection work, and
they share one rule that must not drift -- a testcase carries **at most one**
``<properties>`` block, because every consumer (this repo's own tests
included) reads it through a single ``tc.find("properties")`` and never sees
a second.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, cast

from .envelope import resolved_scoped_gate

if TYPE_CHECKING:
    from datetime import date

    from ..checker_types import DiffResult
    from ..model.change import Change
    from ..policy.severity import SeverityConfig
    from .envelope import ReportEnvelope


def testcase_properties(tc: ET.Element) -> ET.Element:
    """The testcase's one ``<properties>`` block, created if absent.

    Extracted from :func:`~abicheck.junit_report._add_correlation_property` so the two property
    writers cannot drift on the two rules that block encodes: a testcase
    carries at most one ``<properties>`` element (every consumer reads it
    via a single ``tc.find("properties")``), and a freshly created one is
    *inserted first*, because the JUnit XSD requires it to precede any
    ``<failure>``/``<error>``/``<skipped>`` a primary change may already
    have added.
    """
    props = tc.find("properties")
    if props is None:
        props = ET.Element("properties")
        tc.insert(0, props)
    return props


def add_demangled_symbol_property(tc: ET.Element, change: Change) -> None:
    """Append ``abicheck.demangled_symbol`` when *change*'s symbol demangles.

    Codex review, PR #1284: plan slice 7o resolves demangling per format --
    human formats demangle their text, machine formats keep the raw mangled
    symbol and carry the readable one in a field beside it. JUnit had no
    such field, so a JUnit-only consumer was left with the mangled name and
    (the retired ``--view demangle`` gone) no way to ask for the other. The
    testcase ``name`` stays the exact mangled symbol -- it is a stable test
    identity consumers key on across runs -- and the readable name travels
    as a property, which is JUnit's own extension point for exactly this.
    """
    from .change_json import (
        resolve_demangled_symbol,
    )

    demangled = resolve_demangled_symbol(change)
    if not demangled:
        return
    prop = ET.SubElement(testcase_properties(tc), "property")
    prop.set("name", "abicheck.demangled_symbol")
    prop.set("value", demangled)


def add_scoped_properties(
    props: ET.Element, result: DiffResult, envelope: ReportEnvelope | None = None
) -> None:
    """Append the ``--used-by``/``consumers.required_symbols`` scoping properties
    into the testsuite's ``<properties>`` element (ADR-043 + CLI-audit P1).

    The facts come from the envelope's :class:`~abicheck.report.scoped_gate_facts.
    ScopedGateFacts` (computed once by the builder, not here); *result* is read
    only when no envelope is given. *props* is created by the caller and shared
    with the ADR-067 disposition audit above; this function adds nothing at all
    when no scoping was requested, which is the pre-existing behaviour of its
    own rows.

    **Purely informational (workstream D-S1).** ``failures``/pass-fail
    status always follow ``result.verdict``, reported here unswapped as
    ``abicheck.full_library_verdict`` -- never this block's own
    ``abicheck.gate_verdict``/``abicheck.gate_exit_code``, a supplied
    consumer's own impact, reported *beside* the full-library result.
    """
    facts = resolved_scoped_gate(envelope, result)
    if facts is None:
        return

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    if facts.gate_scope is not None:
        _prop("abicheck.gate_scope", facts.gate_scope)
    _prop("abicheck.gate_verdict", facts.gate_verdict)
    _prop("abicheck.full_library_verdict", facts.full_library_verdict)
    # Back-compat alias for the property's original name.
    _prop("abicheck.scoped_verdict", facts.gate_verdict)
    _prop("abicheck.relevant_finding_count", str(facts.relevant_finding_count))
    _prop("abicheck.unrelated_finding_count", str(facts.unrelated_finding_count))
    if facts.gate_exit_code is not None:
        _prop("abicheck.gate_exit_code", str(facts.gate_exit_code))
        _prop("abicheck.gate_exit_code_scheme", str(facts.gate_exit_code_scheme))
        # Back-compat aliases.
        _prop("abicheck.scoped_exit_code", str(facts.gate_exit_code))
        _prop("abicheck.scoped_exit_code_scheme", str(facts.gate_exit_code_scheme))
    if facts.used_by_app_count is not None:
        _prop("abicheck.used_by_app_count", str(facts.used_by_app_count))
    if facts.required_symbol_contract_verdict is not None:
        _prop(
            "abicheck.required_symbol_contract_verdict",
            facts.required_symbol_contract_verdict,
        )


def add_contract_properties(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    severity_config: SeverityConfig | None,
    *,
    today: date | None = None,
) -> None:
    """Append a ``<properties>`` block to testcase *tc* with the same
    canonical per-finding contract shape reporter.py's JSON output and
    sarif.py's ``properties`` already carry (contract_relevance/
    contract_reason_code/contract_assurance/compatibility_evaluation_status/
    compatibility_decision/gate_contribution/contract_evidence_refs).

    A finding whose ``contract_relevance`` was never stamped (every run
    without ``--contract``, the default) gets nothing appended --
    this keeps every pre-existing JUnit report byte-for-byte unchanged.
    """
    from ..contract_relevance_types import CompatibilityEvaluationStatus
    from ..model.contract_finding_relevance import (
        contract_relevance_of,
        evaluation_status_of,
    )
    from ..policy.gate_contribution import gate_contribution_for_change

    relevance = contract_relevance_of(change)
    if relevance is None:
        return
    # One shared block, never a second (Codex review, PR #1284): see
    # `testcase_properties` for why.
    props = testcase_properties(tc)

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    _prop("abicheck.contract_relevance", relevance.value)
    if change.contract_reason_code:
        _prop("abicheck.contract_reason_code", change.contract_reason_code)
    if change.contract_assurance is not None:
        _prop("abicheck.contract_assurance", change.contract_assurance.value)
    # evaluation_status_of always resolves to a real status once `relevance`
    # is known non-None (it falls back to deriving one from the relevance
    # itself -- see its own docstring), so there is no reachable `None`
    # branch to guard here -- `cast` tells mypy that without adding one.
    status = cast(CompatibilityEvaluationStatus, evaluation_status_of(change))
    _prop("abicheck.compatibility_evaluation_status", status.value)
    decision = getattr(change, "compatibility_decision", None)
    _prop("abicheck.compatibility_decision", getattr(decision, "value", "") or "")
    _prop(
        "abicheck.gate_contribution",
        str(
            gate_contribution_for_change(
                change,
                severity_config,
                policy=result.policy,
                policy_file=result.policy_file,
                today=today,
            )
        ),
    )
    if change.contract_evidence_refs is not None:
        _prop(
            "abicheck.contract_evidence_refs", ",".join(change.contract_evidence_refs)
        )

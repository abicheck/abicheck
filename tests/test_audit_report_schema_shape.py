# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The audit report's published-schema contract for the pattern/preprocessor
block, and the one-sided projection that feeds it.

Split out of ``test_no_baseline_report_formats.py`` (at its architecture-gate
size cap) rather than trimmed: this is a distinct concern — whether the
*published schema* describes what the emitter emits, and whether the audit's
candidate-side reduction keeps the facts an audit is able to state.

Why a schema test at all, when a positive-validation test already exists: an
opaque ``{"type": "object"}`` declaration validates every document, so
``test_the_audit_json_validates_against_its_own_published_schema`` passed
throughout the version bump that introduced ``coverage`` while the schema
described nothing and still advertised the previous version. Positive
validation cannot catch that; pinning the described keys can.
"""

from __future__ import annotations

import json

import pytest
from test_no_baseline_report_formats import _result  # noqa: E402

from abicheck.buildsource.preprocessor_probe_families import PROBE_FAMILIES
from abicheck.buildsource.source_inputs import SourceInputDisposition
from abicheck.report.no_baseline import (
    AUDIT_REPORT_SCHEMA_VERSION,
    render_no_baseline,
)
from abicheck.workflows.pattern_preprocessor_scan import (
    CHECK_HEADER_LEAK,
    CHECK_MACRO_DIVERGENCE,
    CHECK_PATTERN_ESCALATION,
)


def test_the_published_schema_describes_the_pattern_preprocessor_scan_block() -> None:
    """The audit schema is where a consumer discovers this block's shape, so a
    key the emitter added must be discoverable there — not merely permitted by
    an opaque `{"type": "object"}` (Codex review, P2).

    This asserts the schema *describes* the keys, not just that a document
    validates: an opaque object declaration validates every document, so the
    positive-validation test above passed throughout the version bump that
    introduced `coverage` while the schema still said 1.4 and described
    nothing. Pinning the described keys is what makes that drift fail.
    """
    from abicheck.schemas import load_audit_report_schema

    schema = load_audit_report_schema()
    block = schema["properties"]["pattern_preprocessor_scan"]
    assert "properties" in block, (
        "the block must be described, not declared as an opaque object"
    )
    assert set(block["properties"]) >= {
        "version",
        "pattern",
        "preprocessor",
        "coverage",
    }

    # The version the schema advertises is the one the emitter stamps.
    assert (
        f'currently "{AUDIT_REPORT_SCHEMA_VERSION}"'
        in (schema["properties"]["audit_report_schema_version"]["description"])
    ), "the schema's own advertised version drifted from the emitted constant"

    # An audit has one operand, so its side blocks and its coverage are keyed
    # `candidate`, never old/new -- the same reduction `_candidate_side_scan`
    # applies. A schema promising old/new here would invite a reader to look
    # for a baseline this document never had.
    assert set(block["properties"]["pattern"]["properties"]) == {"candidate"}
    assert set(block["properties"]["preprocessor"]["properties"]) == {"candidate"}
    assert set(
        block["properties"]["coverage"]["additionalProperties"]["properties"]
    ) == {"candidate"}

    # Every $ref the block reaches for really resolves.
    defs = schema["$defs"]
    for name in (
        "patternScanSide",
        "preprocessorScanSide",
        "evolutionMap",
        "checkSufficiency",
        "sourceInputAccount",
        "probeFamilyTally",
    ):
        assert name in defs, f"${{defs}}/{name} is referenced but not defined"

    # The four evolution states, and only those.
    assert set(defs["evolutionMap"]["additionalProperties"]["enum"]) == {
        "persistent",
        "introduced",
        "resolved",
        "not_evaluated",
    }
    # Every disposition the input account can report.
    assert set(
        defs["sourceInputAccount"]["properties"]["counts"]["propertyNames"]["enum"]
    ) == {d.value for d in SourceInputDisposition}
    # Every probe family, and every check the coverage object can key on.
    assert set(defs["probeFamilyTally"]["propertyNames"]["enum"]) == set(PROBE_FAMILIES)
    assert set(block["properties"]["coverage"]["propertyNames"]["enum"]) == {
        CHECK_PATTERN_ESCALATION,
        CHECK_MACRO_DIVERGENCE,
        CHECK_HEADER_LEAK,
    }


def test_a_live_audit_block_validates_against_the_described_schema() -> None:
    """The described shape must accept a real emitted block, including its
    `coverage` object and the per-family probe tallies — the negative half of
    the test above, which only checks the schema says something."""
    jsonschema = pytest.importorskip("jsonschema")

    from abicheck.model import AbiSnapshot, Function, ScopeOrigin
    from abicheck.schemas import load_audit_report_schema
    from abicheck.workflows.pattern_preprocessor_scan import (
        compute_pattern_preprocessor_scan,
    )

    def _side(version: str) -> AbiSnapshot:
        snap = AbiSnapshot(
            library="libfoo.so",
            version=version,
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="void",
                    source_header=__file__,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        snap.live_source_evidence = True
        return snap

    emitted = compute_pattern_preprocessor_scan(_side("1.0"), _side("2.0")).to_dict()
    block_schema = load_audit_report_schema()["properties"]["pattern_preprocessor_scan"]
    # Resolve $refs against the whole schema, as a real consumer would.
    validator = jsonschema.Draft202012Validator(
        {**block_schema, "$defs": load_audit_report_schema()["$defs"]}
    )
    errors = list(validator.iter_errors(emitted))
    assert not errors, [e.message for e in errors]
    assert set(emitted["coverage"]) == {
        CHECK_PATTERN_ESCALATION,
        CHECK_MACRO_DIVERGENCE,
        CHECK_HEADER_LEAK,
    }
    assert "family_attempted" in emitted["preprocessor"]["old"]


def test_the_audit_carries_candidate_side_coverage() -> None:
    """An audit must be able to say whether its own source-derived facts rest
    on complete evidence.

    `_candidate_side_scan` reduces the two-sided stage to its candidate half,
    and for a while dropped `coverage` along with the evolution maps. The
    evolution maps genuinely cannot be stated without an OLD; coverage can —
    it is a one-sided fact, and it is the only thing that tells "the candidate
    has none of these constructs" apart from "we could not look" (Codex
    review, P2).
    """
    from abicheck.report.no_baseline import _candidate_side_scan

    two_sided = {
        "version": 1,
        "pattern": {"old": {"files_scanned": 1}, "new": {"files_scanned": 2}},
        "preprocessor": {"old": {"ran": False}, "new": {"ran": True}},
        "coverage": {
            CHECK_PATTERN_ESCALATION: {
                "old": {"established": False, "reason": "stale"},
                "new": {"established": True, "reason": ""},
            },
            CHECK_MACRO_DIVERGENCE: {
                "old": {"established": False, "reason": "x"},
                "new": {"established": False, "reason": "no macro probe was run"},
            },
        },
    }
    reduced = _candidate_side_scan(two_sided)
    assert reduced is not None
    # The candidate's own coverage survives, and only the candidate's.
    assert reduced["coverage"] == {
        CHECK_PATTERN_ESCALATION: {"candidate": {"established": True, "reason": ""}},
        CHECK_MACRO_DIVERGENCE: {
            "candidate": {"established": False, "reason": "no macro probe was run"}
        },
    }
    # The OLD side is nowhere in the projection, for coverage as for the rest.
    assert "old" not in json.dumps(reduced)
    # And the evolution maps stay dropped — those really are OLD -> NEW claims.
    assert "escalation_evolution" not in json.dumps(reduced)


def test_a_real_audit_document_emits_candidate_coverage() -> None:
    """End to end through the real renderer, and validating against the schema
    that now describes it."""
    jsonschema = pytest.importorskip("jsonschema")
    from abicheck.schemas import load_audit_report_schema

    result = _result("case143_audit_accidental_export")
    payload, _ = render_no_baseline(result, "json")
    doc = json.loads(payload)

    errors = list(
        jsonschema.Draft202012Validator(load_audit_report_schema()).iter_errors(doc)
    )
    assert not errors, [e.message for e in errors]

    block = doc.get("pattern_preprocessor_scan")
    if block is None:  # stage did not run for this fixture
        return
    assert set(block["coverage"]) == {
        CHECK_PATTERN_ESCALATION,
        CHECK_MACRO_DIVERGENCE,
        CHECK_HEADER_LEAK,
    }
    for sides in block["coverage"].values():
        assert set(sides) == {"candidate"}
        assert set(sides["candidate"]) == {"established", "reason"}

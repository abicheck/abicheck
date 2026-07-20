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

"""Validate that `compare` JSON output conforms to the published schema.

The schema (`abicheck/schemas/compare_report.schema.json`) is the stable
machine-readable contract documented in docs/user-guide/output-formats.md.
These tests pin three things:

1. The schema file itself is well-formed JSON Schema and ships in the package.
2. Real `to_json` output validates against it (full / show-only / severity).
3. The emitted ``report_schema_version`` matches the package constant and is
   present in every projection (full / ``--stat`` / ``--report-mode leaf``).

`jsonschema` is an optional dependency; structural validation tests skip
cleanly when it is absent, while the non-jsonschema invariants always run.
"""

from __future__ import annotations

import json

import pytest

from abicheck import reporter
from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.schemas import (
    COMPARE_REPORT_SCHEMA_PATH,
    REPORT_SCHEMA_VERSION,
    load_compare_report_schema,
)
from abicheck.severity import SeverityConfig

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

_requires_jsonschema = pytest.mark.skipif(
    jsonschema is None, reason="jsonschema not installed"
)


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A pair that yields a mix of breaking, addition, and type changes."""
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        types=[
            RecordType(
                name="Cfg",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="x", type="int", offset_bits=0)],
            )
        ],
        enums=[EnumType(name="Color", members=[EnumMember(name="RED", value=0)])],
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_c", "_Z5api_cv")],
        types=[
            RecordType(
                name="Cfg",
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(name="x", type="int", offset_bits=0),
                    TypeField(name="y", type="int", offset_bits=32),
                ],
            )
        ],
        enums=[
            EnumType(
                name="Color",
                members=[
                    EnumMember(name="RED", value=0),
                    EnumMember(name="BLUE", value=1),
                ],
            )
        ],
    )
    return old, new


class TestSchemaFile:
    def test_schema_file_ships_in_package(self):
        assert COMPARE_REPORT_SCHEMA_PATH.is_file()

    @_requires_jsonschema
    def test_schema_is_valid_jsonschema(self):
        schema = load_compare_report_schema()
        # Raises SchemaError if the schema itself is malformed.
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_schema_declares_version(self):
        schema = load_compare_report_schema()
        assert "report_schema_version" in schema["required"]


@_requires_jsonschema
class TestReportValidatesAgainstSchema:
    def _validate(self, payload: dict) -> None:
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)

    def test_no_change_report_validates(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        self._validate(payload)

    def test_breaking_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        assert payload["verdict"] in {
            "NO_CHANGE",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            "API_BREAK",
            "BREAKING",
        }

    def test_show_only_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), show_only="breaking"))
        self._validate(payload)

    def test_severity_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(
            reporter.to_json(compare(old, new), severity_config=SeverityConfig())
        )
        self._validate(payload)

    def test_addition_reviewer_action_validates_against_packaged_schema(self):
        # Regression guard (Codex review, PR #595): reviewer_action was added
        # to reporter.py and the *docs* schema copy, but the packaged schema
        # abicheck/schemas/compare_report.schema.json -- what
        # load_compare_report_schema() actually reads at runtime -- was left
        # stale. jsonschema.validate would still pass a stale schema (its
        # "change" object has additionalProperties: true), so this doesn't
        # just check "no error" -- it asserts the field the real payload
        # carries is the one actually declared in the packaged schema's enum.
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        additions = [
            c
            for c in payload["changes"]
            if c.get("recommended_action") == "no_action_required"
        ]
        assert additions, "fixture must produce at least one addition finding"
        schema = load_compare_report_schema()
        declared_enum = schema["$defs"]["change"]["properties"]["reviewer_action"][
            "enum"
        ]
        for c in additions:
            assert c["reviewer_action"] in declared_enum


@_requires_jsonschema
class TestReportIdentityEnvelope:
    """ADR-047 §7 report-identity envelope subset (G30 P0.3): check_id,
    profile_id, requested_depth, effective_depth, baseline_channel are
    additive, optional fields nothing populates yet -- this only pins the
    schema/round-trip contract so G30 P1's primitives have somewhere to
    write."""

    def test_unset_by_default(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        for key in (
            "check_id",
            "profile_id",
            "requested_depth",
            "effective_depth",
            "baseline_channel",
        ):
            assert key not in payload

    def test_set_fields_round_trip_and_validate(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@linux-x86_64-gcc13#accepted-main@source"
        result.profile_id = "linux-x86_64-gcc13"
        result.requested_depth = "source"
        result.effective_depth = "headers"
        result.baseline_channel = "accepted-main"
        payload = json.loads(reporter.to_json(result))
        jsonschema.validate(instance=payload, schema=load_compare_report_schema())
        assert payload["check_id"] == "libfoo@linux-x86_64-gcc13#accepted-main@source"
        assert payload["profile_id"] == "linux-x86_64-gcc13"
        assert payload["requested_depth"] == "source"
        assert payload["effective_depth"] == "headers"
        assert payload["baseline_channel"] == "accepted-main"

    def test_stat_mode_carries_identity_fields_too(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@profile#channel@binary"
        payload = json.loads(reporter.to_json(result, stat=True))
        assert payload["check_id"] == "libfoo@profile#channel@binary"

    def test_invalid_depth_enum_fails_schema_validation(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.requested_depth = "not-a-real-depth"
        payload = json.loads(reporter.to_json(result))
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=payload, schema=load_compare_report_schema())


class TestScanReportIdentityEnvelope:
    """Same ADR-047 §7 fields (G30 P0.3), mirrored on the scan side --
    ScanOutcome.to_dict() rather than a compare_report.schema.json (scan's
    JSON output has no packaged JSON Schema to validate against)."""

    def _outcome(self, **identity: str) -> object:
        from abicheck.buildsource.risk import RiskScore
        from abicheck.scan_engine import ScanOutcome

        return ScanOutcome(
            mode="scan",
            resolved_method="auto",
            depth="headers",
            collect_mode="off",
            risk=RiskScore(total=0),
            auto=True,
            changed_path_count=0,
            changed_path_source="none",
            **identity,
        )

    def test_unset_by_default(self):
        payload = self._outcome().to_dict()
        for key in (
            "check_id",
            "profile_id",
            "requested_depth",
            "effective_depth",
            "baseline_channel",
        ):
            assert key not in payload

    def test_set_fields_round_trip(self):
        payload = self._outcome(
            check_id="libfoo@profile#channel@source",
            profile_id="linux-x86_64-gcc13",
            requested_depth="source",
            effective_depth="build",
            baseline_channel="accepted-main",
        ).to_dict()
        assert payload["check_id"] == "libfoo@profile#channel@source"
        assert payload["profile_id"] == "linux-x86_64-gcc13"
        assert payload["requested_depth"] == "source"
        assert payload["effective_depth"] == "build"
        assert payload["baseline_channel"] == "accepted-main"

    def test_scan_schema_version_bumped_for_the_new_fields(self):
        from abicheck.schemas import SCAN_SCHEMA_VERSION

        payload = self._outcome().to_dict()
        assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
        assert SCAN_SCHEMA_VERSION != "1.0"


class TestSchemaVersion:
    def test_emitted_version_matches_constant(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION

    def test_version_is_major_minor(self):
        parts = REPORT_SCHEMA_VERSION.split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_stat_mode_carries_version(self):
        """--stat JSON is a different shape but must still carry the version marker."""
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), stat=True))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION

    def test_leaf_mode_carries_version(self):
        """--report-mode leaf JSON must still carry the version marker."""
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), report_mode="leaf"))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION

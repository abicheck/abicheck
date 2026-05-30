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
    return Function(name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC)


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A pair that yields a mix of breaking, addition, and type changes."""
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        types=[
            RecordType(
                name="Cfg", kind="struct", size_bits=32,
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
                name="Cfg", kind="struct", size_bits=64,
                fields=[
                    TypeField(name="x", type="int", offset_bits=0),
                    TypeField(name="y", type="int", offset_bits=32),
                ],
            )
        ],
        enums=[
            EnumType(
                name="Color",
                members=[EnumMember(name="RED", value=0), EnumMember(name="BLUE", value=1)],
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
            "NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING",
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

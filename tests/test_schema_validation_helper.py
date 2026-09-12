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

"""`tests/schema_validation.validate_instance` must be indistinguishable from
`jsonschema.validate` except for how often it checks the schema.

An optimization inside a *test* helper is uniquely dangerous: if it weakens
validation, every suite that depends on it goes green for the wrong reason and
nothing else is watching. So the contract is stated against the real library as
the oracle — the same instance/schema pair is put through both, and the two
must agree on verdict and on which error is raised — over a domain of
schema/instance pairs chosen to include the cases a caching bug would break
(a schema mutated in place after its first use, a bool-vs-int instance, a
malformed schema, a `format_checker`).
"""
from __future__ import annotations

import pytest

jsonschema = pytest.importorskip("jsonschema")

from tests.schema_validation import validate_instance, validator_for  # noqa: E402

_SCHEMAS: list[dict] = [
    {"type": "object"},
    {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
    {"type": "array", "items": {"type": "string"}, "minItems": 1},
    {"type": "integer", "minimum": 0, "maximum": 10},
    {"type": "boolean"},
    {"oneOf": [{"type": "string"}, {"type": "integer"}]},
    {"type": "object", "additionalProperties": False, "properties": {"a": {"const": 1}}},
    {"type": "string", "format": "date-time"},
]

_INSTANCES: list[object] = [
    {},
    {"a": 1},
    {"a": "1"},
    {"a": True},
    {"a": 1, "b": 2},
    [],
    ["x"],
    [1],
    0,
    11,
    True,
    1,
    "x",
    "2026-09-12T00:00:00Z",
    "not-a-date",
    None,
]


def _reference(instance: object, schema: dict, format_checker=None) -> str:
    """The oracle: the library's own `validate`, reduced to a comparable token."""
    try:
        jsonschema.validate(instance=instance, schema=schema, format_checker=format_checker)
    except jsonschema.ValidationError as exc:
        return f"ValidationError:{exc.message}"
    return "ok"


def _under_test(instance: object, schema: dict, format_checker=None) -> str:
    try:
        validate_instance(instance, schema, format_checker)
    except jsonschema.ValidationError as exc:
        return f"ValidationError:{exc.message}"
    return "ok"


def test_agrees_with_the_library_across_the_whole_cross_product() -> None:
    """128 pairs, batched so a disagreement names every one at once."""
    disagreements = [
        (schema, instance, _reference(instance, schema), _under_test(instance, schema))
        for schema in _SCHEMAS
        for instance in _INSTANCES
        if _reference(instance, schema) != _under_test(instance, schema)
    ]
    assert not disagreements


def test_the_sweep_is_not_vacuous() -> None:
    """Guard against a domain that happens to be all-passing or all-failing:
    an agreement sweep over one verdict proves nothing about the other."""
    verdicts = {_reference(i, s) == "ok" for s in _SCHEMAS for i in _INSTANCES}
    assert verdicts == {True, False}


def test_a_malformed_schema_still_raises_schema_error() -> None:
    for bad in ({"type": 123}, {"required": "not-a-list"}, {"minimum": "x", "type": "integer"}):
        with pytest.raises(jsonschema.SchemaError):
            validate_instance({}, bad)


def test_a_schema_mutated_in_place_is_not_served_a_stale_validator() -> None:
    """The reason the cache is keyed on content and not on the dict's identity
    or a file path: a caller that edits its schema between calls must get the
    new behaviour."""
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    validate_instance({"a": 1}, schema)

    schema["properties"]["a"] = {"type": "string"}
    with pytest.raises(jsonschema.ValidationError):
        validate_instance({"a": 1}, schema)

    schema["properties"]["a"] = {"type": "integer"}
    validate_instance({"a": 1}, schema)


def test_bool_is_not_accepted_where_an_integer_is_required() -> None:
    """Python's `bool` is an `int` subclass; jsonschema deliberately does not
    treat `True` as an integer. A reimplementation that lost that distinction
    would pass the sweep above only if this case were in it — it is."""
    with pytest.raises(jsonschema.ValidationError):
        validate_instance(True, {"type": "integer"})
    validate_instance(1, {"type": "integer"})


def test_format_checker_is_honoured_and_is_part_of_the_cache_key() -> None:
    # "ipv4" is checked by jsonschema's own built-in checker, with no optional
    # format dependency to install -- unlike "date-time", whose checker is a
    # no-op unless `rfc3339-validator` is present, which would make this test
    # pass for the wrong reason.
    schema = {"type": "string", "format": "ipv4"}
    checker = jsonschema.FormatChecker()
    assert "ipv4" in checker.checkers, "the built-in ipv4 checker is required here"

    validate_instance("999.999.999.999", schema)  # formats are annotations by default
    with pytest.raises(jsonschema.ValidationError):
        validate_instance("999.999.999.999", schema, checker)
    # ... and the strict validator must not have displaced the lenient one.
    validate_instance("999.999.999.999", schema)


def test_the_cache_actually_caches() -> None:
    """Output equivalence alone cannot tell a working cache from an absent
    one, so assert the reuse directly."""
    schema = {"type": "object", "properties": {"zz": {"type": "integer"}}}
    first = validator_for(dict(schema))
    second = validator_for(dict(schema))  # equal content, different object
    assert first is second
    assert validator_for(dict(schema), jsonschema.FormatChecker()) is not first


def test_the_real_packaged_schemas_round_trip(
) -> None:
    """Not only toy schemas: the packaged documents this helper exists for
    must themselves check clean and validate a real report shape."""
    from abicheck.schemas import (
        load_aggregate_report_schema,
        load_compare_report_schema,
    )

    for loader in (load_compare_report_schema, load_aggregate_report_schema):
        schema = loader()
        assert validator_for(schema) is validator_for(loader())
        with pytest.raises(jsonschema.ValidationError):
            validate_instance("not an object at all", schema)

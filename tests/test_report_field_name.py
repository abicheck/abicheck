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

"""Report schema 7.7: a field-level finding carries its member as ``field_name``.

Before 7.7 a ``type_field_*`` finding's ``symbol`` named only the record and
the member was readable only from ``description``, so a consumer (and the
formal record-edit fragment, formal/RECORD_EDIT_FRAGMENT.md) had to parse
prose to know *which* field moved.
"""

from __future__ import annotations

import pytest

from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.report.change_json import change_to_dict, suppressed_change_entry
from abicheck.schemas import load_compare_report_schema
from tests.schema_validation import validate_instance

_FIELD_KINDS = [
    ChangeKind.TYPE_FIELD_REMOVED,
    ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
    ChangeKind.TYPE_FIELD_TYPE_CHANGED,
    ChangeKind.STRUCT_FIELD_REMOVED,
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
]


def _change_schema() -> dict:
    schema = load_compare_report_schema()
    return {"$defs": schema["$defs"], **schema["$defs"]["change"]}


@pytest.mark.parametrize("kind", _FIELD_KINDS)
@pytest.mark.parametrize("field", ["f2", "s10", "x"])
def test_field_level_finding_serializes_its_member(
    kind: ChangeKind, field: str
) -> None:
    c = Change(kind=kind, symbol="s1", description="d", field_name=field)
    d = change_to_dict(c)
    assert d["field_name"] == field
    assert d["symbol"] == "s1"
    validate_instance(d, _change_schema())
    assert suppressed_change_entry(c)["field_name"] == field


@pytest.mark.parametrize(
    "kind", [ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.FUNC_REMOVED, *_FIELD_KINDS]
)
def test_finding_without_a_member_omits_the_key(kind: ChangeKind) -> None:
    c = Change(kind=kind, symbol="s1", description="d")
    assert "field_name" not in change_to_dict(c)
    assert "field_name" not in suppressed_change_entry(c)

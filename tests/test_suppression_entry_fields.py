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

"""Branch-level tests for the suppression entry-field validators.

``policy/suppression/entry_fields.py`` holds the strict per-key parsers that
``SuppressionList.load`` runs on every YAML entry. Each branch is pinned here
directly, so a malformed value fails with the entry index in its message
rather than being coerced silently.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from abicheck.errors import PolicyError
from abicheck.policy.suppression.entry_fields import (
    parse_allow_public_break,
    parse_allow_unknown_reachability,
    parse_entry_id,
    parse_expires,
)


class TestParseExpires:
    def test_absent_key_means_no_expiry(self) -> None:
        assert parse_expires(None, 0) is None

    def test_date_is_returned_unchanged(self) -> None:
        assert parse_expires(date(2026, 6, 1), 0) == date(2026, 6, 1)

    def test_datetime_is_narrowed_to_its_date(self) -> None:
        parsed = parse_expires(datetime(2026, 6, 1, 13, 30), 0)
        assert parsed == date(2026, 6, 1)
        assert type(parsed) is date

    def test_iso_string_is_parsed(self) -> None:
        assert parse_expires("2026-06-01", 3) == date(2026, 6, 1)

    def test_invalid_string_names_the_entry_index(self) -> None:
        with pytest.raises(ValueError, match="Suppression entry 4: invalid 'expires'"):
            parse_expires("01/06/2026", 4)


@pytest.mark.parametrize(
    "parse",
    [parse_allow_public_break, parse_allow_unknown_reachability],
    ids=["allow_public_break", "allow_unknown_reachability"],
)
class TestStrictBooleanFlags:
    def test_absent_key_defaults_to_false(self, parse) -> None:
        assert parse(None, 0) is False

    @pytest.mark.parametrize("value", [True, False])
    def test_yaml_booleans_pass_through(self, parse, value: bool) -> None:
        assert parse(value, 1) is value

    @pytest.mark.parametrize("value", ["false", "true", 1, 0])
    def test_truthy_or_quoted_values_are_rejected(self, parse, value) -> None:
        # bool("false") is True in Python: a quoted string must not enable an
        # override that the safety gate exists to require an explicit true for.
        with pytest.raises(ValueError, match=r"Suppression entry 2: '\w+' must be"):
            parse(value, 2)


class TestParseEntryId:
    def test_absent_id_is_none_and_not_recorded(self) -> None:
        seen: dict[str, int] = {}
        assert parse_entry_id(None, 0, seen) is None
        assert seen == {}

    def test_new_id_is_returned_and_recorded_with_its_index(self) -> None:
        seen: dict[str, int] = {}
        assert parse_entry_id("waiver-1", 5, seen) == "waiver-1"
        assert seen == {"waiver-1": 5}

    @pytest.mark.parametrize("bad", [7, "", "   ", ["x"]])
    def test_non_string_or_blank_id_is_rejected(self, bad) -> None:
        with pytest.raises(PolicyError, match="'id' must be a non-empty string"):
            parse_entry_id(bad, 1, {})

    def test_duplicate_id_names_the_earlier_entry(self) -> None:
        seen = {"waiver-1": 0}
        with pytest.raises(PolicyError, match="duplicate id 'waiver-1'") as exc:
            parse_entry_id("waiver-1", 3, seen)
        assert "already used by suppression entry 0" in str(exc.value)

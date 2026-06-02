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

"""SOURCE_DATE_EPOCH support for reproducible snapshot timestamps."""

from __future__ import annotations

from abicheck.cli import _provenance_timestamp


def test_source_date_epoch_fixed() -> None:
    # 2021-01-01T00:00:00Z
    assert _provenance_timestamp("1609459200") == "2021-01-01T00:00:00+00:00"


def test_source_date_epoch_deterministic() -> None:
    assert _provenance_timestamp("1700000000") == _provenance_timestamp("1700000000")


def test_source_date_epoch_whitespace_tolerated() -> None:
    assert _provenance_timestamp("  1609459200\n") == "2021-01-01T00:00:00+00:00"


def test_malformed_epoch_falls_back_to_now() -> None:
    # Non-numeric → current time (just assert it produces a valid ISO string,
    # not the fixed epoch).
    out = _provenance_timestamp("not-a-number")
    assert out and out != "2021-01-01T00:00:00+00:00"


def test_out_of_range_epoch_falls_back_to_now() -> None:
    # An absurd out-of-range epoch makes datetime.fromtimestamp raise
    # OverflowError/OSError; it must fall back rather than abort the dump.
    out = _provenance_timestamp("99999999999999999999")
    assert out and out != "2021-01-01T00:00:00+00:00"


def test_unset_epoch_uses_now() -> None:
    out = _provenance_timestamp(None)
    assert out  # current wall-clock ISO timestamp

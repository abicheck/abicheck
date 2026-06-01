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

"""Tests for the ABI surface (RTTI / internal-namespace) churn breakdown.

Real-world motivation: for C++ libraries built without -fvisibility=hidden
(e.g. oneDAL), the breaking count is dominated by churn in RTTI artifacts and
internal-namespace symbols rather than genuine public-API breaks. The report
should quantify that split so the headline number is not misleading.
"""
from __future__ import annotations

import json

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.report_summary import (
    classify_symbol_origin,
    surface_breakdown,
)
from abicheck.reporter import to_json, to_markdown


def _result(changes, verdict=Verdict.BREAKING):
    return DiffResult(
        old_version="1.0", new_version="2.0", library="libtest.so.1",
        changes=changes, verdict=verdict,
    )


class TestClassifySymbolOrigin:
    def test_rtti_typeinfo_and_vtable(self):
        assert classify_symbol_origin("_ZTIN4daal8internal3FooE") == "rtti"
        assert classify_symbol_origin("_ZTSN4daal3FooE") == "rtti"
        assert classify_symbol_origin("_ZTVN4daal3FooE") == "rtti"
        assert classify_symbol_origin("_ZTTN4daal3FooE") == "rtti"

    def test_internal_namespace_components(self):
        # Length-prefixed Itanium component for ``internal`` (8) / ``detail`` (6).
        assert classify_symbol_origin("_ZN4daal8internal3barEv") == "internal"
        assert classify_symbol_origin("_ZN6oneapi3dal6detail3bazEv") == "internal"

    def test_public_symbol(self):
        assert classify_symbol_origin("_ZN6oneapi3dal7computeEv") == "public"
        assert classify_symbol_origin("plain_c_symbol") == "public"
        assert classify_symbol_origin("") == "public"

    def test_rtti_takes_precedence_over_internal(self):
        # RTTI of an internal type is counted as RTTI churn, not double-counted.
        assert classify_symbol_origin("_ZTIN4daal8internal3FooE") == "rtti"


class TestSurfaceBreakdown:
    def test_counts_split_by_origin(self):
        changes = [
            Change(ChangeKind.VAR_REMOVED, "_ZTIN4daal3FooE", "rtti1"),
            Change(ChangeKind.VAR_REMOVED, "_ZTSN4daal3FooE", "rtti2"),
            Change(ChangeKind.FUNC_REMOVED, "_ZN4daal8internal3barEv", "internal1"),
            Change(ChangeKind.FUNC_REMOVED, "_ZN6oneapi3dal7computeEv", "public1"),
        ]
        bd = surface_breakdown(changes)
        assert (bd.total, bd.rtti, bd.internal, bd.public) == (4, 2, 1, 1)

    def test_empty(self):
        bd = surface_breakdown([])
        assert (bd.total, bd.rtti, bd.internal, bd.public) == (0, 0, 0, 0)


class TestReporterSurfacing:
    def _churny_result(self):
        return _result([
            Change(ChangeKind.VAR_REMOVED, "_ZTIN4daal3FooE", "typeinfo removed"),
            Change(ChangeKind.VAR_REMOVED, "_ZTSN4daal3FooE", "typeinfo name removed"),
            Change(ChangeKind.FUNC_REMOVED, "_ZN4daal8internal3barEv", "internal removed"),
            Change(ChangeKind.FUNC_REMOVED, "_ZN6oneapi3dal7computeEv", "public removed"),
        ])

    def test_markdown_banner_present_with_churn(self):
        md = to_markdown(self._churny_result())
        assert "internal/RTTI churn" in md
        assert "3 of 4 breaking findings" in md
        assert "Genuine public-surface breaking findings: **1**" in md

    def test_json_breakdown_present_with_churn(self):
        d = json.loads(to_json(self._churny_result()))
        bd = d["abi_surface_breakdown"]
        assert bd == {"breaking_total": 4, "public": 1, "rtti_churn": 2, "internal_churn": 1}

    def test_no_banner_or_key_for_purely_public_breaks(self):
        result = _result([
            Change(ChangeKind.FUNC_REMOVED, "_ZN6oneapi3dal7computeEv", "public removed"),
        ])
        md = to_markdown(result)
        assert "internal/RTTI churn" not in md
        d = json.loads(to_json(result))
        assert "abi_surface_breakdown" not in d

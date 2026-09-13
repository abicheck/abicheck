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

"""Per-finding identity under multi-library attribution.

A sibling of ``test_compat_multi_library.py`` rather than another class in
it: that file is at the architecture gate's 1200-line test-file cap, and the
rule for a file at its cap is to move responsibility out, not to trim it to
fit. This module owns one question -- does a finding's id stay unique once
two DSOs can produce the same finding -- and the report-schema declaration
that goes with it.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change


class TestFindingIdDistinguishesLibraries:
    """Two DSOs' otherwise-identical findings must not share one ``finding_id``.

    A multi-library descriptor routinely pairs DSOs that share symbols (a
    facade and its implementation, an OpenMP and a sequential build of the
    same library). Every identity field ``report_finding_id`` folds in --
    kind, symbol, old/new value, source location, description -- is then
    equal, so before ``library`` joined them the two findings hashed to one
    id and a consumer indexing the report by id kept exactly one of them
    (Codex review).
    """

    @staticmethod
    def _change(library: str | None) -> Change:
        return Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="shared_entry",
            description="Function 'shared_entry' was removed",
            library=library,
        )

    def test_two_libraries_get_two_ids(self):
        from abicheck.finding_identity import report_finding_id

        ids = {
            report_finding_id(self._change(lib))
            for lib in ("libfoo.so.1", "libbar.so.1", "libbaz.so.1")
        }
        assert len(ids) == 3

    def test_same_library_is_stable(self):
        from abicheck.finding_identity import report_finding_id

        assert report_finding_id(self._change("libfoo.so.1")) == report_finding_id(
            self._change("libfoo.so.1")
        )

    def test_scalar_id_is_unchanged_by_the_new_field(self):
        """The conditional append's whole point: an unattributed finding's id
        must be byte-for-byte what it was before ``library`` existed."""
        import hashlib

        from abicheck.finding_identity import report_finding_id

        c = self._change(None)
        pre_field_key = "\x1f".join(
            [
                c.kind.value,
                c.symbol or "",
                c.old_value or "",
                c.new_value or "",
                c.source_location or "",
                c.description or "",
            ]
        )
        expected = hashlib.sha256(pre_field_key.encode("utf-8")).hexdigest()[:16]
        assert report_finding_id(c) == expected

    def test_library_is_declared_in_the_report_schema(self):
        """Emitted-but-undeclared is the gap the schema bump closes."""
        import json

        from abicheck.schemas import REPORT_SCHEMA_VERSION

        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "abicheck"
                / "schemas"
                / "compare_report.schema.json"
            ).read_text()
        )
        prop = schema["$defs"]["change"]["properties"]["library"]
        assert prop["type"] == "string"
        assert "library" not in schema["$defs"]["change"].get("required", [])
        assert REPORT_SCHEMA_VERSION == "4.6"


class TestLibraryAttributionReachesEveryProjection:
    """JSON and itemized Markdown carried `library`; HTML and the compat XML
    did not, so in the *default* report two paired DSOs' identical findings
    were indistinguishable (Codex review).

    Bug class: a per-finding field wired into some output projections and not
    others. The tests below are one per projection rather than one shared
    loop, since each renders the value its own way and a shared assertion
    would hide which one regressed.
    """

    @staticmethod
    def _changes() -> list[Change]:
        return [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="shared_entry",
                description="Function 'shared_entry' was removed",
                library=lib,
            )
            for lib in ("liba.so", "libb.so")
        ]

    def test_html_names_both_libraries(self):
        from abicheck.html_report import compute_full_change_rows
        from abicheck.report.render_html import render_changes_table

        rows = compute_full_change_rows(self._changes())
        assert [r.library for r in rows] == ["liba.so", "libb.so"]
        html = render_changes_table(rows)
        assert "liba.so" in html
        assert "libb.so" in html

    def test_html_omits_the_row_entirely_for_a_scalar_comparison(self):
        """The negative control: an unconditional row would add a stray,
        empty 'Library:' line to every single-library report."""
        from abicheck.html_report import compute_full_change_rows
        from abicheck.report.render_html import render_changes_table

        scalar = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="s",
                description="removed",
            )
        ]
        rows = compute_full_change_rows(scalar)
        assert rows[0].library is None
        assert "Library:" not in render_changes_table(rows)

    def test_compat_xml_problem_elements_name_their_library(self):
        import xml.etree.ElementTree as ET

        from abicheck.compat.xml_report import _add_problem_element

        root = ET.Element("root")
        for change in self._changes():
            _add_problem_element(root, change)
        libs = [p.get("library") for p in root.findall("problem")]
        assert libs == ["liba.so", "libb.so"]

    def test_compat_xml_omits_the_attribute_for_a_scalar_comparison(self):
        import xml.etree.ElementTree as ET

        from abicheck.compat.xml_report import _add_problem_element

        root = ET.Element("root")
        _add_problem_element(
            root, Change(kind=ChangeKind.FUNC_REMOVED, symbol="s", description="x")
        )
        assert root.find("problem").get("library") is None

    def test_compat_xml_symbol_lists_name_their_library(self):
        """The element that needs it most: a bare <name> carries nothing else
        to tell two DSOs' identical removals apart."""
        import xml.etree.ElementTree as ET

        from abicheck.compat.xml_report import _build_symbol_list

        root = ET.Element("root")
        _build_symbol_list(
            root, "removed_symbols", self._changes(), frozenset({"func_removed"})
        )
        names = root.find("removed_symbols").findall("name")
        assert [n.get("library") for n in names] == ["liba.so", "libb.so"]
        assert {n.text for n in names} == {"shared_entry"}

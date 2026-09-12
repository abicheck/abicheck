# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One display selector, every export -- for a directory/package operand.

Split out of ``test_cli_compare_release_view.py`` rather than added to it
(``architecture/debt.yaml``'s test-file ceiling): this states plan slice
7m's *export-grammar* uniformity rule as it applies to the release fan-out,
which is a different claim from that module's subject (which ``--view``
tokens the fan-out honours at all). The single-pair statement of the same
rule, over generated permutations, lives in
``test_cli_export_grammar.py``.

Fixtures are imported from the view module rather than re-created: sharing
the *inputs* is fine, and re-deriving a release pair here would be a second
place for it to drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_cli_compare_release_view import (  # noqa: E402
    _invoke,
    _write_removed_function_pair,
)


class TestReleaseViewShowOnlyAppliesToEveryExport:
    """A display filter means the same thing for every export.

    This class previously pinned the opposite (a secondary ``--write`` was
    contracted full/unfiltered whatever ``--format`` asked for), an
    asymmetry answerable only while those were two flags: under plan slice
    7m's one repeatable ``-o FORMAT=DESTINATION`` there is no principled
    way to name the unfiltered one. Nothing is hidden -- the complete
    accounting stays in the machine projection's disposition/suppression
    ledger and its `release_filtered_summary`'s true pre-filter totals.

    The regression this class was added for is unchanged and still guarded:
    the shared ``library_results`` projection must not be filtered *in
    place* upstream of the renderers, or a filtered view would be all any
    consumer could ever see.
    """

    def test_every_export_sees_the_same_filtered_view(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        first = tmp_path / "one.json"
        second = tmp_path / "two.json"

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--view",
            "show=variables",
            "-o",
            f"json={first}",
            "-o",
            f"json={second}",
        )
        assert result.exit_code == 4, result.output

        docs = [json.loads(p.read_text(encoding="utf-8")) for p in (first, second)]
        assert docs[0] == docs[1]
        for doc in docs:
            (entry,) = doc["libraries"]
            # `show=variables` keeps only variable-element kinds; this pair's
            # findings are a function removal plus a surface note. Nothing is
            # hidden by narrowing: the filter states what it excluded, over
            # the true uncapped pool.
            assert [f["kind"] for f in entry.get("findings", [])] == []
            assert doc["release_filtered_summary"]["total"] >= 2
            assert doc["release_filtered_summary"]["displayed"] == 0

    def test_the_unfiltered_result_is_still_one_invocation_away(
        self, tmp_path: Path
    ) -> None:
        """The capability the old asymmetry provided -- a complete machine
        artifact -- is not lost, only spelled by not asking for a filter.
        Stated so "the filter applies everywhere" cannot quietly become
        "the complete document is unobtainable"."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        full = tmp_path / "full.json"

        result = _invoke("compare", str(old_dir), str(new_dir), "-o", f"json={full}")
        assert result.exit_code == 4, result.output
        (entry,) = json.loads(full.read_text(encoding="utf-8"))["libraries"]
        assert {f["kind"] for f in entry["findings"]} == {
            "func_removed",
            "public_surface_shrank",
        }

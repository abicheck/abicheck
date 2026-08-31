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

"""Cross-profile aggregate-finding reconciliation tests (G34 Phase D).

The governing rule: a profile may be reported *unaffected* by a finding only
when its reports enumerated their findings in full. Anything short of that
— a missing, unreadable, not-comparable, or partially-unparseable report, or
a ``compare-release`` report, which lists bundle/matrix findings but only
per-library counts — is *undetermined*, never rounded down to clean. An
incomplete report's findings are still read: seeing a finding proves it is
there, whereas not seeing one proves nothing.

The helpers here are deliberately local rather than imported from
``test_aggregate.py``: a few lines of report-writing each, not worth
coupling two files that otherwise share only the subject under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir
from abicheck.workflows.aggregate.matrix import (
    ProfileContractState,
    build_finding_matrix,
    render_finding_matrix_lines,
)
from abicheck.workflows.aggregate.reconcile import (
    _CONFORMANT_CHANGE_FIELDS,
    ReportFinding,
    ReportFindings,
    comparable_mangled_symbol,
    cross_abi_declaration,
    mangling_scheme,
    parse_report_findings,
    resolve_cross_abi_identity,
    resolve_report_change_identity,
)

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

LINUX = "linux-x86_64"


def _change_entry(**fields: object) -> dict:
    """A `changes[]` entry carrying every field `compare_report.schema.json`
    marks *required*, defaulted the way a real `reporter.to_json` emits
    them -- hand-written fixtures are how this module's validation drifted
    from producer reality before (Codex review); this helper keeps a
    fixture testing what its name says."""
    return {"old_value": None, "new_value": None, "severity": "error", **fields}


def _write_report(d: Path, target_id: str, verdict: str | None, **extra) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    path = d / f"abi-report-{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _expect(*required: str) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), [])


def _write_not_comparable_report(d: Path, target_id: str) -> Path:
    """An ADR-050 D2 not_comparable report: a real JSON ``null`` verdict (not
    merely an absent key) plus a structured ``reason``."""
    path = d / f"abi-report-{target_id}.json"
    path.write_text(
        json.dumps(
            {
                "report_schema_version": "2.17",
                "library": "libfoo.so",
                "verdict": None,
                "reason": {"kind": "scope_mismatch", "message": "scope drift"},
            }
        )
    )
    return path


GCC = "libfoo@linux-gcc14#release@headers"
CLANG = "libfoo@linux-clang20#release@headers"
MSVC = "libfoo@windows-msvc#release@headers"

#: The same logical removal, as a profile with rich DWARF evidence reports it
#: and as a symbols-only profile reports it — one event, two detector kinds.
_REMOVED_RICH = {
    "kind": "func_removed",
    "symbol": "_ZN3lib3addEii",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}
_REMOVED_L0 = {
    "kind": "func_removed_elf_only",
    "symbol": "_ZN3lib3addEii",
    "description": "Exported symbol disappeared",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}
_SIZE_CHANGED = {
    "kind": "type_size_changed",
    "symbol": "Foo",
    "description": "size 8 -> 16",
    "old_value": "8",
    "new_value": "16",
    "severity": "error",
}

#: One `compare-release` `bundle_findings[]` entry — the shape a `kind: bundle`
#: check produces, which keeps the library attribution in its own fields
#: rather than in `description`.
_BUNDLE_FINDING = {
    "kind": "bundle_intra_dep_removed",
    "symbol": "_ZN3lib3addEii",
    "consumer_library": "libapp.so",
    "provider_library": "libfoo.so",
    "description": "Symbol no longer provided",
    "old_value": None,
    "new_value": None,
    "affected_libraries": ["libapp.so"],
}


def _write_findings_report(
    d: Path, target_id: str, verdict: str, changes: list[dict] | None
) -> Path:
    """A report that *enumerates* its findings (``changes`` present), or —
    with ``changes=None`` — one that does not, the case that must read as
    "unknown" rather than "clean"."""
    return _write_report(
        d, target_id, verdict, **({} if changes is None else {"changes": changes})
    )


class TestFindingMatrix:
    """G34 Phase D: per-finding cross-profile reconciliation. ``profile_matrix``
    says *which profiles* are affected; this says *which finding* differs
    between them, and never claims a profile is clean of a finding it was
    never checked for."""

    def test_same_finding_on_every_profile_is_one_entry(self, tmp_path: Path) -> None:
        for tid in (GCC, CLANG, MSVC):
            _write_findings_report(tmp_path, tid, "BREAKING", [_REMOVED_RICH])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG, MSVC))
        (entry,) = r.finding_matrix
        assert entry.base_target == "libfoo"
        assert entry.symbol == "_ZN3lib3addEii"
        assert entry.affected_profiles == (
            "linux-clang20",
            "linux-gcc14",
            "windows-msvc",
        )
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ()
        assert entry.scope == "all_profiles"

    def test_profile_specific_finding_names_the_clean_profiles(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_RICH])
        _write_findings_report(
            tmp_path, CLANG, "BREAKING", [_REMOVED_RICH, _SIZE_CHANGED]
        )
        _write_findings_report(tmp_path, MSVC, "BREAKING", [_REMOVED_RICH])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG, MSVC))
        by_kind = {e.kinds: e for e in r.finding_matrix}
        shared = by_kind[("func_removed",)]
        specific = by_kind[("type_size_changed",)]
        assert shared.scope == "all_profiles"
        assert specific.scope == "profile_specific"
        assert specific.affected_profiles == ("linux-clang20",)
        assert specific.unaffected_profiles == ("linux-gcc14", "windows-msvc")
        assert specific.undetermined_profiles == ()

    def test_partial_scope_when_some_but_not_all_profiles_affected(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, MSVC, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG, MSVC))
        (entry,) = r.finding_matrix
        assert entry.scope == "partial"
        assert entry.affected_profiles == ("linux-clang20", "linux-gcc14")
        assert entry.unaffected_profiles == ("windows-msvc",)

    def test_rich_and_symbols_only_kinds_reconcile_to_one_finding(
        self, tmp_path: Path
    ) -> None:
        """A profile that had DWARF and one that only had symbols reported the
        same removal under two kinds — the matrix must show one event, with
        both kinds visible, not two unrelated per-profile findings."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_RICH])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_REMOVED_L0])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.scope == "all_profiles"
        assert entry.kinds == ("func_removed", "func_removed_elf_only")
        assert entry.identity_tier == "canonical"

    def test_report_without_changes_array_is_undetermined_not_clean(
        self, tmp_path: Path
    ) -> None:
        """The governing invariant, per finding: a report that never
        enumerated its findings must not be listed as proven unaffected."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", None)
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-clang20",)
        assert entry.scope == "undetermined"

    def test_missing_report_is_undetermined_not_clean(self, tmp_path: Path) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.undetermined_profiles == ("linux-clang20",)
        assert entry.scope == "undetermined"

    def test_not_comparable_profile_is_undetermined(self, tmp_path: Path) -> None:
        """A not-comparable leg produced a blocking verdict but no usable
        finding set — it can neither carry nor be cleared of a finding."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_not_comparable_report(tmp_path, CLANG)
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.undetermined_profiles == ("linux-clang20",)

    def test_affected_outranks_undetermined_across_a_profiles_own_checks(
        self, tmp_path: Path
    ) -> None:
        """One profile, two checks: one reported the finding, the other never
        reported at all. The profile demonstrably has the finding, so it is
        affected rather than softened to "not sure"."""
        second_check = "libfoo@linux-gcc14#release@build"
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, second_check))
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.undetermined_profiles == ()

    def test_no_findings_anywhere_yields_an_empty_matrix(self, tmp_path: Path) -> None:
        _write_findings_report(tmp_path, GCC, "COMPATIBLE", [])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        assert r.finding_matrix == ()

    def test_single_profile_setup_has_no_finding_matrix(self, tmp_path: Path) -> None:
        """A bare, non-``check_id``-shaped target has no profile to group by —
        same participation rule as ``profile_matrix``."""
        _write_findings_report(tmp_path, LINUX, "BREAKING", [_REMOVED_RICH])
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.finding_matrix == ()

    def test_unexpected_targets_do_not_participate(self, tmp_path: Path) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_RICH])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_REMOVED_RICH])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC))
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-gcc14",)

    def test_malformed_finding_entry_does_not_abort_aggregation(
        self, tmp_path: Path
    ) -> None:
        _write_report(
            tmp_path, GCC, "BREAKING", changes=["not a mapping", _SIZE_CHANGED]
        )
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.kinds == ("type_size_changed",)
        # The valid sibling entry still convicts its profile.
        assert entry.affected_profiles == ("linux-gcc14",)

    def test_partially_malformed_array_cannot_clear_a_profile(
        self, tmp_path: Path
    ) -> None:
        """A profile whose `changes` array had an unparseable element did not
        enumerate its findings in full, so it must not be reported as proven
        clean of another profile's finding (Codex review)."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(tmp_path, CLANG, "COMPATIBLE", changes=["not a mapping"])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-clang20",)
        assert entry.scope == "undetermined"

    def test_release_report_bundle_findings_participate(self, tmp_path: Path) -> None:
        """A `kind: bundle` check routes through the per-library release
        fan-out, whose report carries `bundle_findings`/`matrix_findings`
        instead of `changes` — those must reconcile too (Codex review)."""
        _write_report(
            tmp_path,
            GCC,
            "BREAKING",
            libraries=[{"library": "libfoo.so", "verdict": "BREAKING", "breaking": 1}],
            bundle_findings=[dict(_BUNDLE_FINDING)],
        )
        _write_report(
            tmp_path,
            CLANG,
            "BREAKING",
            libraries=[{"library": "libfoo.so", "verdict": "BREAKING", "breaking": 1}],
            bundle_findings=[dict(_BUNDLE_FINDING)],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.kinds == ("bundle_intra_dep_removed",)
        assert entry.affected_profiles == ("linux-clang20", "linux-gcc14")

    def test_release_report_matrix_findings_participate(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path,
            GCC,
            "BREAKING",
            libraries=[],
            matrix_findings=[dict(_SIZE_CHANGED)],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC))
        (entry,) = r.finding_matrix
        assert entry.kinds == ("type_size_changed",)
        assert entry.affected_profiles == ("linux-gcc14",)

    def test_release_report_is_never_complete(self, tmp_path: Path) -> None:
        """A release report lists bundle/matrix findings but only *counts* its
        per-library ones, so it can convict a profile but never clear it."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(
            tmp_path,
            CLANG,
            "COMPATIBLE",
            libraries=[{"library": "libfoo.so", "verdict": "COMPATIBLE"}],
            bundle_findings=[],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-clang20",)

    def test_bundle_attribution_keeps_distinct_library_pairs_apart(
        self, tmp_path: Path
    ) -> None:
        """Two bundle findings identical on every identity field except the
        library pair are two findings, not one."""
        other_pair = {**_BUNDLE_FINDING, "consumer_library": "libbar.so"}
        _write_report(
            tmp_path,
            GCC,
            "BREAKING",
            libraries=[],
            bundle_findings=[dict(_BUNDLE_FINDING), other_pair],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC))
        assert len(r.finding_matrix) == 2
        assert {e.description for e in r.finding_matrix} == {
            "[libapp.so ← libfoo.so] Symbol no longer provided",
            "[libbar.so ← libfoo.so] Symbol no longer provided",
        }

    def test_ordering_is_stable(self, tmp_path: Path) -> None:
        changes = [_SIZE_CHANGED, _REMOVED_RICH]
        _write_findings_report(tmp_path, GCC, "BREAKING", changes)
        _write_findings_report(tmp_path, CLANG, "BREAKING", list(reversed(changes)))
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        assert [e.kinds for e in r.finding_matrix] == [
            ("func_removed",),
            ("type_size_changed",),
        ]

    def test_finding_matrix_does_not_change_the_gate(self, tmp_path: Path) -> None:
        """This is a reporting view — it must not move the exit code."""
        _write_findings_report(tmp_path, GCC, "COMPATIBLE", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        assert r.finding_matrix
        assert r.exit_code() == 0

    def test_render_text_distinguishes_shared_from_profile_specific(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_RICH])
        _write_findings_report(
            tmp_path, CLANG, "BREAKING", [_REMOVED_RICH, _SIZE_CHANGED]
        )
        text = aggregate_reports_dir(
            tmp_path, expected=_expect(GCC, CLANG)
        ).render_text()
        assert "Cross-profile findings:" in text
        assert (
            "libfoo type_size_changed [Foo]: only on linux-clang20; "
            "not on linux-gcc14" in text
        )
        assert (
            "libfoo func_removed [_ZN3lib3addEii]: on every checked profile "
            "(linux-clang20, linux-gcc14)" in text
        )

    def test_render_text_never_says_not_on_an_unread_profile(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", None)
        text = aggregate_reports_dir(
            tmp_path, expected=_expect(GCC, CLANG)
        ).render_text()
        assert "unknown on linux-clang20" in text
        assert "not on linux-clang20" not in text

    def test_render_text_omits_section_when_matrix_is_empty(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, LINUX, "BREAKING", [_REMOVED_RICH])
        text = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).render_text()
        assert "Cross-profile findings:" not in text

    def test_json_output_carries_the_matrix(self, tmp_path: Path) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        d = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG)).to_dict()
        (entry,) = d["finding_matrix"]
        assert entry["scope"] == "profile_specific"
        assert entry["affected_profiles"] == ["linux-gcc14"]
        assert entry["unaffected_profiles"] == ["linux-clang20"]
        assert entry["undetermined_profiles"] == []
        assert entry["identity_tier"] in {"canonical", "normalized", "reduced"}

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_matrix_output_validates_against_schema(self, tmp_path: Path) -> None:
        from abicheck.schemas import load_aggregate_report_schema

        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_RICH])
        _write_findings_report(
            tmp_path, CLANG, "BREAKING", [_REMOVED_L0, _SIZE_CHANGED]
        )
        _write_findings_report(tmp_path, MSVC, "COMPATIBLE", None)
        d = aggregate_reports_dir(
            tmp_path, expected=_expect(GCC, CLANG, MSVC)
        ).to_dict()
        jsonschema.validate(d, load_aggregate_report_schema())
        assert d["finding_matrix"]


class TestReportFindingIdentity:
    """The read-back adapter the matrix keys on
    (``aggregate_findings.resolve_report_change_identity``)."""

    def test_same_logical_finding_from_two_reports_shares_an_identity(self) -> None:
        a = resolve_report_change_identity(dict(_REMOVED_RICH))
        b = resolve_report_change_identity(dict(_REMOVED_L0))
        assert a.primary_id == b.primary_id
        assert a.tier == "canonical"

    def test_distinct_findings_do_not_collide(self) -> None:
        a = resolve_report_change_identity(dict(_REMOVED_RICH))
        b = resolve_report_change_identity(dict(_SIZE_CHANGED))
        assert a.primary_id != b.primary_id

    def test_unknown_kind_from_a_newer_abicheck_still_resolves(self) -> None:
        """A report is an external artifact — a kind slug this build has never
        heard of must resolve, not raise."""

        identity = resolve_report_change_identity(
            {"kind": "some_future_kind", "symbol": "Foo", "description": "d"}
        )
        assert identity.primary_id

    def test_empty_entry_degrades_to_reduced_tier(self) -> None:
        assert resolve_report_change_identity({}).tier == "reduced"

    def test_resolves_a_real_finding_shape_without_raising(self) -> None:
        """Regression test for #958: ``resolve_change_identity``'s
        unconditional ``entity_id`` read (#957) must not assume
        ``_ReportChangeView`` carries a live ``Change``'s full fields."""
        identity = resolve_report_change_identity(dict(_REMOVED_RICH))
        assert identity.primary_id
        assert identity.tier == "canonical"


class TestParseReportFindingsUnit:
    """`parse_report_findings` on its own — the completeness rule is the part
    every other claim in this module rests on."""

    def test_changes_array_is_the_only_complete_source(self) -> None:
        assert parse_report_findings({"changes": []}).complete is True
        assert parse_report_findings({}).complete is False
        assert parse_report_findings({"bundle_findings": []}).complete is False
        assert parse_report_findings({"matrix_findings": []}).complete is False

    def test_malformed_entry_marks_incomplete_but_keeps_the_rest(self) -> None:
        result = parse_report_findings({"changes": [_SIZE_CHANGED, 42]})
        assert len(result.findings) == 1
        assert result.complete is False

    def test_malformed_release_entry_marks_incomplete(self) -> None:
        result = parse_report_findings({"bundle_findings": ["not a mapping"]})
        assert result.findings == ()
        assert result.complete is False

    @pytest.mark.parametrize(
        "attribution,expected_prefix",
        [
            ({"provider_library": "libfoo.so"}, "[libfoo.so] "),
            ({"consumer_library": "libapp.so"}, "[libapp.so] "),
            (
                {"consumer_library": "libapp.so", "provider_library": "libfoo.so"},
                "[libapp.so ← libfoo.so] ",
            ),
            ({}, ""),
        ],
    )
    def test_bundle_attribution_prefixes(
        self, attribution: dict, expected_prefix: str
    ) -> None:
        entry = {
            "kind": "bundle_intra_dep_removed",
            "symbol": "_ZN3lib3addEii",
            "description": "Symbol no longer provided",
            **attribution,
        }
        (finding,) = parse_report_findings({"bundle_findings": [entry]}).findings
        assert finding.description == expected_prefix + "Symbol no longer provided"


class TestBuildFindingMatrixUnit:
    """`build_finding_matrix` driven directly — the seam the leaf split
    exists for, reachable without constructing a whole aggregate result."""

    def _findings(self, *entries: dict) -> ReportFindings:
        return ReportFindings(
            findings=tuple(ReportFinding.from_report_entry(e) for e in entries),
            complete=True,
        )

    def test_profile_with_no_checks_at_all_is_undetermined(self) -> None:
        (entry,) = build_finding_matrix(
            {
                "libfoo": {
                    "gcc": [self._findings(_SIZE_CHANGED)],
                    "clang": [],
                }
            }
        )
        assert entry.affected_profiles == ("gcc",)
        assert entry.undetermined_profiles == ("clang",)

    def test_partial_scope_render(self) -> None:
        matrix = build_finding_matrix(
            {
                "libfoo": {
                    "clang": [self._findings(_SIZE_CHANGED)],
                    "gcc": [self._findings(_SIZE_CHANGED)],
                    "msvc": [self._findings()],
                }
            }
        )
        (entry,) = matrix
        assert entry.scope == "partial"
        (line,) = render_finding_matrix_lines(matrix)[2:]
        assert line == ("  libfoo type_size_changed [Foo]: on clang, gcc; not on msvc")

    def test_undetermined_render_names_both_unknown_and_clean_profiles(self) -> None:
        matrix = build_finding_matrix(
            {
                "libfoo": {
                    "gcc": [self._findings(_SIZE_CHANGED)],
                    "clang": [self._findings()],
                    "msvc": [ReportFindings()],
                }
            }
        )
        (line,) = render_finding_matrix_lines(matrix)[2:]
        assert line == (
            "  libfoo type_size_changed [Foo]: on gcc; unknown on msvc; not on clang"
        )

    def test_finding_with_no_kind_renders_a_placeholder(self) -> None:
        matrix = build_finding_matrix(
            {"libfoo": {"gcc": [self._findings({"symbol": "Foo"})]}}
        )
        (line,) = render_finding_matrix_lines(matrix)[2:]
        assert "(unknown kind)" in line

    def test_finding_with_no_symbol_renders_without_a_bracket(self) -> None:
        """A type-level finding carries no symbol, and the rendered line must
        not grow an empty `[]` for it."""
        matrix = build_finding_matrix(
            {"libfoo": {"gcc": [self._findings({"kind": "abi_tag_changed"})]}}
        )
        (line,) = render_finding_matrix_lines(matrix)[2:]
        assert line.startswith("  libfoo abi_tag_changed:")
        assert "[" not in line

    def test_empty_matrix_renders_nothing(self) -> None:
        assert render_finding_matrix_lines([]) == []


#: The same declaration as an Itanium toolchain and MSVC each spell it.
_REMOVED_ITANIUM = {
    "kind": "func_removed",
    "symbol": "_ZN3lib3addEii",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}
_REMOVED_MSVC = {
    "kind": "func_removed",
    "symbol": "?add@lib@@YAHHH@Z",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}
#: A *different* overload of that same declaration — indistinguishable from
#: the one above by qualified name alone, since neither mangling parser
#: recovers parameter types.
_REMOVED_ITANIUM_OVERLOAD = {
    "kind": "func_removed",
    "symbol": "_ZN3lib3addEd",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}
#: The MSVC spelling of yet another overload of the same declaration.
_REMOVED_MSVC_OTHER_OVERLOAD = {
    "kind": "func_removed",
    "symbol": "?add@lib@@YAHN@Z",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}

MSVC_CHECK = "libfoo@windows-msvc#release@headers"


class TestCrossAbiReconciliation:
    """Findings spelled under different C++ mangling schemes (Codex review).

    Two rules, and the second is what keeps the first honest. A profile
    holding a finding on the same declaration under another mangling is never
    reported *clean* of this one — that was the original bug. But the two
    findings are never *merged* either: neither mangling parser recovers
    parameter types, so nothing in a report distinguishes "both profiles lost
    the same overload" from "each lost a different one", and asserting the
    former would invent a fact no producer supplied.
    """

    def test_same_declaration_under_two_schemes_is_never_called_clean(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert len(r.finding_matrix) == 2
        assert {e.scope for e in r.finding_matrix} == {"undetermined"}
        assert all(e.unaffected_profiles == () for e in r.finding_matrix)

    def test_the_shared_declaration_is_exposed_so_a_consumer_can_link_them(
        self, tmp_path: Path
    ) -> None:
        """Not merging must not mean hiding the relationship."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert {e.cross_abi_declaration for e in r.finding_matrix} == {"lib::add"}

    def test_differing_overloads_are_not_asserted_to_be_one_finding(
        self, tmp_path: Path
    ) -> None:
        """The case an earlier revision got wrong: one finding per profile,
        same qualified name, *different* overloads. Cardinality alone looks
        like a clean pairing, but nothing proves it — so these must not
        collapse into a single all-profiles entry."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(
            tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC_OTHER_OVERLOAD]
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert len(r.finding_matrix) == 2
        assert all(e.scope == "undetermined" for e in r.finding_matrix)

    def test_unrelated_declarations_stay_genuinely_unaffected(
        self, tmp_path: Path
    ) -> None:
        """The withholding must not over-fire: two different declarations
        leave the clean profile provably clean."""
        other = _change_entry(
            kind="func_removed",
            symbol="?other@lib@@YAHXZ",
            description="Function removed",
        )
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [other])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert [e.scope for e in r.finding_matrix] == [
            "profile_specific",
            "profile_specific",
        ]
        assert all(e.unaffected_profiles for e in r.finding_matrix)

    def test_a_removal_and_an_addition_are_not_siblings(self, tmp_path: Path) -> None:
        """The discriminator rides along, so sharing a declaration is not
        enough — the two profiles must be reporting the same *event* before
        either is withheld from a clean verdict."""
        added = _change_entry(
            kind="func_added",
            symbol="?add@lib@@YAHHH@Z",
            description="Function added",
        )
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [added])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert all(e.unaffected_profiles for e in r.finding_matrix)

    def test_type_level_finding_has_no_cross_abi_key(self) -> None:
        """`symbol` is a type name, not a mangling — nothing to normalize, and
        the helper must not guess one."""
        assert resolve_cross_abi_identity(_SIZE_CHANGED) is None
        assert cross_abi_declaration("Foo") is None

    def test_finding_with_no_symbol_has_no_cross_abi_key(self) -> None:
        assert resolve_cross_abi_identity({"kind": "func_removed"}) is None
        assert cross_abi_declaration("") is None

    def test_extern_c_symbol_has_no_cross_abi_key(self) -> None:
        """An unmangled C symbol is already scheme-independent."""
        assert (
            resolve_cross_abi_identity(
                {"kind": "func_removed", "symbol": "plain_c", "description": "gone"}
            )
            is None
        )

    def test_both_schemes_reduce_to_the_same_declaration(self) -> None:
        assert cross_abi_declaration("_ZN3lib3addEii") == "lib::add"
        assert cross_abi_declaration("?add@lib@@YAHHH@Z") == "lib::add"

    def test_cross_abi_key_keeps_a_removal_and_an_addition_apart(self) -> None:
        removed = resolve_cross_abi_identity(_REMOVED_ITANIUM)
        added = resolve_cross_abi_identity(
            {**_REMOVED_ITANIUM, "kind": "func_added", "description": "Function added"}
        )
        assert removed is not None and added is not None
        assert removed.primary_id != added.primary_id


class TestKindsAcrossOneProfilesChecks:
    def test_kinds_from_every_check_of_a_profile_are_retained(
        self, tmp_path: Path
    ) -> None:
        """One profile can run several checks for a target (different baseline
        channels/depths). A depth-`binary` check reports the L0 kind and a
        depth-`headers` check the rich one; both belong in `kinds` (Codex
        review — the first sample used to win and the other was dropped)."""
        headers = "libfoo@linux-gcc14#release@headers"
        binary = "libfoo@linux-gcc14#release@binary"
        _write_findings_report(tmp_path, headers, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(
            tmp_path,
            binary,
            "BREAKING",
            [
                {
                    "kind": "func_removed_elf_only",
                    "symbol": "_ZN3lib3addEii",
                    "description": "Exported symbol disappeared",
                }
            ],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(headers, binary))
        (entry,) = r.finding_matrix
        assert entry.kinds == ("func_removed", "func_removed_elf_only")
        assert entry.affected_profiles == ("linux-gcc14",)


#: The smallest entry a conformant producer writes: every field the
#: compare-report schema marks `required` on a `changes[]` entry that this
#: module reads. Type-rejection cases build on it so they exercise the type
#: check rather than tripping the required-field check first.
_MINIMAL = {"kind": "func_removed", "symbol": "x", "description": "d"}


class TestFindingEntryValidation:
    """A mapping is not automatically a usable finding (Codex review)."""

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param({"foo": 1}, id="no-kind"),
            pytest.param({"kind": 42}, id="non-string-kind"),
            pytest.param({"kind": ""}, id="empty-kind"),
            pytest.param({**_MINIMAL, "symbol": ["x"]}, id="list-symbol"),
            pytest.param({**_MINIMAL, "old_value": {"a": 1}}, id="mapping-old-value"),
            pytest.param(
                {**_MINIMAL, "source_location": ["h.h:1"]}, id="list-source-location"
            ),
            pytest.param({**_MINIMAL, "description": 7}, id="non-string-description"),
        ],
    )
    def test_unusable_entry_marks_the_report_incomplete(self, entry: dict) -> None:
        result = parse_report_findings({"changes": [entry]})
        assert result.findings == ()
        assert result.complete is False

    def test_valid_siblings_survive_an_unusable_entry(self) -> None:
        result = parse_report_findings({"changes": [_SIZE_CHANGED, {"foo": 1}]})
        assert len(result.findings) == 1
        assert result.complete is False

    def test_absent_optional_fields_are_fine(self) -> None:
        """Everything past the schema's own required set really is optional —
        `source_location`, `affected_symbols`, and `qualified_name` are all
        absent from plenty of genuine findings."""
        result = parse_report_findings({"changes": [_change_entry(**_MINIMAL)]})
        assert len(result.findings) == 1
        assert result.complete is True

    @pytest.mark.parametrize("missing", list(_CONFORMANT_CHANGE_FIELDS))
    def test_a_non_conformant_entry_withholds_completeness(self, missing: str) -> None:
        """`compare_report.schema.json` marks all six `required` on a
        `changes[]` entry, and every `compare` report mode emits all six — so
        an entry without one was not written by a conformant producer and is
        no evidence the rest were enumerated either (Codex review).

        `kind` is the one that also makes the entry unreadable; the other five
        leave a real finding that is kept, since a kept finding can only ever
        convict a profile.
        """
        entry = _change_entry(**_MINIMAL)
        del entry[missing]
        result = parse_report_findings({"changes": [entry]})
        assert result.complete is False
        assert len(result.findings) == (0 if missing == "kind" else 1)

    @pytest.mark.parametrize("field", ["old_value", "new_value"])
    def test_null_is_accepted_where_the_schema_allows_it(self, field: str) -> None:
        """`old_value`/`new_value` are declared `["string", "null"]` because
        `reporter._change_to_dict` really writes them that way for a finding
        carrying no before/after value — an ordinary report, not a malformed
        one."""
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, field: None})]}
        )
        assert len(result.findings) == 1
        assert result.complete is True

    @pytest.mark.parametrize("field", ["kind", "symbol", "description", "severity"])
    def test_null_is_rejected_where_the_schema_requires_a_string(
        self, field: str
    ) -> None:
        """The other four are plain `"string"`. A `null` there is
        schema-invalid *and* reads as an empty spelling, so the entry
        resolves to a different identity than the same finding elsewhere —
        exactly the entry that must not clear another profile (Codex
        review)."""
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, field: None})]}
        )
        assert result.complete is False

    @pytest.mark.parametrize(
        "value", [pytest.param(42, id="int"), pytest.param([], id="empty-list")]
    )
    def test_a_wrong_typed_severity_is_readable_but_not_conformant(
        self, value: object
    ) -> None:
        """`severity` is required by the schema but not part of the identity,
        so a non-string there leaves the finding perfectly readable while
        still being something no conformant producer wrote — the one field
        where the two predicates genuinely disagree.

        The list case is its own parameter because the structured-value
        carve-out that exists for `old_value`/`new_value` must not reach any
        other field (Codex review): a first cut applied it to all six, so
        `severity: []` read as conformant.
        """
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, "severity": value})]}
        )
        assert len(result.findings) == 1
        assert result.complete is False

    @pytest.mark.parametrize("field", ["old_value", "new_value"])
    @pytest.mark.parametrize(
        "value", [pytest.param(["a", "b"], id="list"), pytest.param(("a",), id="tuple")]
    )
    def test_the_structured_carve_out_covers_exactly_old_and_new_value(
        self, field: str, value: object
    ) -> None:
        """The other half of the same rule: these two fields really do carry a
        list from `diff_python.py`, and rejecting that would mark a genuine
        report incomplete."""
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, field: value})]}
        )
        assert len(result.findings) == 1
        assert result.complete is True

    def test_a_null_required_field_still_convicts_its_own_profile(
        self, tmp_path: Path
    ) -> None:
        """Rejected for completeness, kept as a finding — the same asymmetry
        a missing field gets."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(
            tmp_path,
            CLANG,
            "BREAKING",
            changes=[_change_entry(**{**_MINIMAL, "symbol": None})],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        by_kind = {e.kinds: e for e in r.finding_matrix}
        assert by_kind[("type_size_changed",)].undetermined_profiles == (
            "linux-clang20",
        )
        assert by_kind[("func_removed",)].affected_profiles == ("linux-clang20",)

    def test_a_readable_but_non_conformant_entry_still_convicts(
        self, tmp_path: Path
    ) -> None:
        """The asymmetry, end to end: the malformed profile cannot clear the
        other one, but its own finding is still its own."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(
            tmp_path,
            CLANG,
            "BREAKING",
            changes=[{"kind": "func_removed", "symbol": "_Z3foov", "description": "d"}],
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        by_kind = {e.kinds: e for e in r.finding_matrix}
        assert by_kind[("type_size_changed",)].undetermined_profiles == (
            "linux-clang20",
        )
        assert by_kind[("func_removed",)].affected_profiles == ("linux-clang20",)


class TestStructuredElementValidation:
    """A structured value's *elements* are identity evidence too (Codex
    review). Checking only the container let `[{"bad": 1}]` through, which
    `_stringify_change_value` folds into the spelling `"{'bad': 1}"` — an
    identity no producer wrote."""

    @pytest.mark.parametrize("field", ["old_value", "new_value"])
    def test_a_non_string_element_withholds_completeness(self, field: str) -> None:
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, field: [{"bad": 1}]})]}
        )
        assert len(result.findings) == 1
        assert result.complete is False

    def test_affected_symbols_elements_are_validated_too(self) -> None:
        """Optional, but `resolve_change_identity` folds the whole set into
        `header_binary_context_mismatch`'s discriminator."""
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, "affected_symbols": [123]})]}
        )
        assert len(result.findings) == 1
        assert result.complete is False

    def test_a_string_affected_symbols_array_stays_conformant(self) -> None:
        result = parse_report_findings(
            {"changes": [_change_entry(**{**_MINIMAL, "affected_symbols": ["a", "b"]})]}
        )
        assert result.complete is True

    def test_a_non_string_element_is_not_coerced_into_a_spelling(self) -> None:
        """The sharper half: `str(123)` would mint `"123"` and let a malformed
        report collide with an unrelated profile's genuine `"123"` symbol."""
        entry = {**_MINIMAL, "kind": "header_binary_context_mismatch"}
        invented = ReportFinding.from_report_entry(
            _change_entry(**{**entry, "affected_symbols": [123]})
        )
        genuine = ReportFinding.from_report_entry(
            _change_entry(**{**entry, "affected_symbols": ["123"]})
        )
        assert invented.identity != genuine.identity


class TestMsvcTargetDecorations:
    """Same scheme, different symbol is proof of distinctness for Itanium but
    not for MSVC: an MSVC decoration can encode the *target ABI* rather than
    the declaration (Codex review)."""

    #: The same declaration as `_REMOVED_MSVC`, as ARM64EC spells it — the
    #: `$$h` tag is a target marker, not a different overload.
    _ARM64EC = {
        "kind": "func_removed",
        "symbol": "?add@lib@@$$hYAHHH@Z",
        "description": "Function removed",
        "old_value": None,
        "new_value": None,
        "severity": "error",
    }

    def test_both_spellings_reduce_to_one_declaration(self) -> None:
        assert cross_abi_declaration("?add@lib@@YAHHH@Z") == "lib::add"
        assert cross_abi_declaration("?add@lib@@$$hYAHHH@Z") == "lib::add"

    def test_two_msvc_targets_do_not_clear_each_other(self, tmp_path: Path) -> None:
        arm64ec = "libfoo@windows-arm64ec#release@headers"
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        _write_findings_report(tmp_path, arm64ec, "BREAKING", [self._ARM64EC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(MSVC_CHECK, arm64ec))
        assert all(e.unaffected_profiles == () for e in r.finding_matrix)
        assert {e.scope for e in r.finding_matrix} == {"undetermined"}

    def test_itanium_keeps_its_precision(self, tmp_path: Path) -> None:
        """The withholding is scheme-specific, not a retreat to the old
        blanket rule — a GCC/Clang matrix is the commonest configuration
        there is and must still resolve cleanly."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_REMOVED_ITANIUM_OVERLOAD])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        assert all(e.unaffected_profiles for e in r.finding_matrix)
        assert {e.scope for e in r.finding_matrix} == {"profile_specific"}


class TestSchemaRequiredFieldsAgree:
    """`_CONFORMANT_CHANGE_FIELDS` mirrors `compare_report.schema.json`'s own
    `required` list *and* its per-field nullability, so the check stays a
    cheap per-key test instead of a full validation on every entry. The
    schema is the fact owner; this is what stops either half drifting."""

    @staticmethod
    def _change_def() -> dict:
        return json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "abicheck"
                / "schemas"
                / "compare_report.schema.json"
            ).read_text()
        )["$defs"]["change"]

    def test_the_field_set_matches_the_schema(self) -> None:
        assert sorted(_CONFORMANT_CHANGE_FIELDS) == sorted(
            self._change_def()["required"]
        )

    def test_the_nullability_matches_the_schema(self) -> None:
        """Checking presence alone let a `null` `symbol` count as conformant
        (Codex review); this pins the split that fixed it to its source."""
        properties = self._change_def()["properties"]
        for field, nullable in _CONFORMANT_CHANGE_FIELDS.items():
            declared = properties[field]["type"]
            allows_null = isinstance(declared, list) and "null" in declared
            assert allows_null is nullable, field

    def test_unusable_entry_cannot_clear_another_profile(self, tmp_path: Path) -> None:
        """The end-to-end consequence: a profile whose findings array holds
        only an unidentifiable object has not enumerated anything, so it must
        not be reported clean of another profile's finding."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(tmp_path, CLANG, "COMPATIBLE", changes=[{"foo": 1}])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-clang20",)

    def test_unusable_release_entry_marks_incomplete(self) -> None:
        result = parse_report_findings({"bundle_findings": [{"symbol": "x"}]})
        assert result.findings == ()
        assert result.complete is False


class TestScanReportShape:
    """`scan --against` is the third report shape, and it keeps its findings
    under `diff.findings` rather than `changes` (Codex review). Reading them
    is what keeps a scan-checked profile in the matrix at all — but it can
    never be complete: only the gating buckets are itemized, and the list is
    capped."""

    def test_scan_findings_are_read(self) -> None:
        result = parse_report_findings({"diff": {"findings": [dict(_SIZE_CHANGED)]}})
        assert len(result.findings) == 1

    def test_scan_findings_are_never_complete(self) -> None:
        result = parse_report_findings({"diff": {"findings": [dict(_SIZE_CHANGED)]}})
        assert result.complete is False

    def test_a_non_mapping_diff_block_is_ignored(self) -> None:
        result = parse_report_findings({"diff": "summary text"})
        assert result.findings == ()
        assert result.complete is False

    def test_a_scan_profile_is_affected_not_unknown(self, tmp_path: Path) -> None:
        """The end-to-end consequence: a scan-checked profile that reported a
        finding is *affected* by it, while still never clearing another
        profile's."""
        _write_report(
            tmp_path, GCC, "BREAKING", diff={"findings": [dict(_SIZE_CHANGED)]}
        )
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.unaffected_profiles == ("linux-clang20",)

    def test_a_scan_profile_never_clears_another_profiles_finding(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(tmp_path, CLANG, "COMPATIBLE", diff={"findings": []})
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-clang20",)


class TestDisplayFilteredReports:
    """`compare --show-only` narrows the `changes` array while the verdict,
    the gate, and the `summary` block keep describing the whole diff — so a
    well-formed array is not by itself proof that nothing else was found
    (Codex review)."""

    def test_show_only_filter_flag_withholds_completeness(self) -> None:
        result = parse_report_findings(
            {
                "show_only_filter": "breaking",
                "summary": {"total_changes": 3},
                "changes": [dict(_SIZE_CHANGED)],
            }
        )
        assert len(result.findings) == 1
        assert result.complete is False

    def test_a_count_mismatch_alone_withholds_completeness(self) -> None:
        """`--report-mode leaf` emits no `show_only_filter` at all, so the
        summary's own count against the array length is the only signal."""
        result = parse_report_findings(
            {"summary": {"total_changes": 3}, "changes": [dict(_SIZE_CHANGED)]}
        )
        assert result.complete is False

    def test_an_agreeing_summary_keeps_completeness(self) -> None:
        result = parse_report_findings(
            {"summary": {"total_changes": 1}, "changes": [dict(_SIZE_CHANGED)]}
        )
        assert result.complete is True

    def test_a_report_without_a_summary_is_unaffected(self) -> None:
        result = parse_report_findings({"changes": [dict(_SIZE_CHANGED)]})
        assert result.complete is True

    @pytest.mark.parametrize("mode", ["full", "leaf", "root-cause"])
    def test_real_reporter_output_round_trips(self, mode: str) -> None:
        """Against the real producer rather than a hand-built dict, in every
        report mode — including `leaf`, whose filtered output carries no flag
        to key on."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
        from abicheck.reporter import to_json

        def _result() -> DiffResult:
            return DiffResult(
                library="lib",
                old_version="1",
                new_version="2",
                verdict=Verdict.BREAKING,
                changes=[
                    Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed foo"),
                    Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added bar"),
                ],
            )

        whole = parse_report_findings(json.loads(to_json(_result(), report_mode=mode)))
        assert whole.complete is True
        assert len(whole.findings) == 2

        filtered = parse_report_findings(
            json.loads(to_json(_result(), report_mode=mode, show_only="breaking"))
        )
        assert filtered.complete is False
        assert len(filtered.findings) == 1

    def test_a_filtered_profile_cannot_clear_another_profiles_finding(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end consequence Codex named: the filtered profile hid
        the finding rather than not having it."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        _write_report(
            tmp_path,
            CLANG,
            "BREAKING",
            changes=[dict(_REMOVED_ITANIUM)],
            show_only_filter="breaking",
            summary={"total_changes": 4},
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        by_kind = {e.kinds: e for e in r.finding_matrix}
        assert by_kind[("type_size_changed",)].undetermined_profiles == (
            "linux-clang20",
        )
        assert by_kind[("func_removed",)].affected_profiles == ("linux-clang20",)


#: `diff_python.py` really passes a list for these findings (e.g.
#: `new_value=sorted(group)`), and `_change_to_dict` preserves it in JSON.
_PYTHON_VIOLATION = _change_entry(
    kind="python_stable_abi_violation",
    symbol="PyFoo",
    description="uses unstable API",
    new_value=["Py_INCREF", "Py_DECREF"],
)


class TestStructuredFindingValues:
    """`old_value`/`new_value` are annotated `str | None` but not enforced,
    and one real producer emits a list (Codex review). The validator must
    accept exactly what the identity resolver handles — no less, or a genuine
    finding is dropped and its profile demoted to undetermined."""

    def test_structured_value_is_a_usable_finding(self) -> None:
        result = parse_report_findings({"changes": [dict(_PYTHON_VIOLATION)]})
        assert len(result.findings) == 1
        assert result.complete is True

    def test_structured_value_still_discriminates_the_identity(self) -> None:
        """Routing it through the string-only filter would have read the list
        as absent, silently dropping a discriminator the finding really has."""
        both = ReportFinding.from_report_entry(dict(_PYTHON_VIOLATION))
        one = ReportFinding.from_report_entry(
            {**_PYTHON_VIOLATION, "new_value": ["Py_INCREF"]}
        )
        assert both.identity != one.identity

    def test_a_tuple_value_is_accepted_too(self) -> None:
        result = parse_report_findings(
            {"changes": [{**_PYTHON_VIOLATION, "new_value": ("a", "b")}]}
        )
        assert len(result.findings) == 1
        assert result.complete is True

    def test_a_mapping_value_is_still_rejected(self) -> None:
        """The carve-out is for the shapes the resolver folds
        deterministically, not for anything non-string."""
        result = parse_report_findings(
            {"changes": [{**_PYTHON_VIOLATION, "new_value": {"a": 1}}]}
        )
        assert result.findings == ()
        assert result.complete is False

    def test_such_a_profile_is_affected_not_undetermined(self, tmp_path: Path) -> None:
        """The end-to-end consequence Codex named."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [dict(_PYTHON_VIOLATION)])
        _write_findings_report(tmp_path, CLANG, "COMPATIBLE", [])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        (entry,) = r.finding_matrix
        assert entry.kinds == ("python_stable_abi_violation",)
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.undetermined_profiles == ()
        assert entry.scope == "profile_specific"


MACOS_CHECK = "libfoo@macos-clang#release@headers"

#: The same declaration and the same overload as `_REMOVED_ITANIUM`, spelled
#: the way a Mach-O toolchain spells it — one extra leading underscore.
_REMOVED_ITANIUM_MACHO = {
    "kind": "func_removed",
    "symbol": "__ZN3lib3addEii",
    "description": "Function removed",
    "old_value": None,
    "new_value": None,
    "severity": "error",
}


class TestManglingComparability:
    """Withholding a clean verdict is for spellings that *cannot be compared*,
    not for every pair sharing a qualified name (Codex review).

    Two Itanium manglings encode their parameter types, so their inequality
    is proof of distinctness — treating that as ambiguous cost real precision
    on the commonest configuration there is: a GCC profile and a Clang
    profile, both mangling Itanium.
    """

    def test_same_scheme_different_overloads_are_provably_distinct(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_REMOVED_ITANIUM_OVERLOAD])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG))
        assert [e.scope for e in r.finding_matrix] == [
            "profile_specific",
            "profile_specific",
        ]
        assert all(e.unaffected_profiles for e in r.finding_matrix)
        assert all(e.undetermined_profiles == () for e in r.finding_matrix)

    def test_incomparable_schemes_still_withhold(self, tmp_path: Path) -> None:
        """The Itanium/MSVC pair keeps the old, conservative answer."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert {e.scope for e in r.finding_matrix} == {"undetermined"}

    def test_same_entity_across_platform_spellings_is_one_finding(
        self, tmp_path: Path
    ) -> None:
        """A Mach-O toolchain's extra leading underscore makes one entity look
        like two raw symbols. Normalized, the two are byte-identical *complete*
        Itanium encodings — parameter types included — so they are provably one
        declaration and reconcile into a single `all_profiles` entry rather
        than two half-known ones (Codex review)."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(
            tmp_path, MACOS_CHECK, "BREAKING", [_REMOVED_ITANIUM_MACHO]
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MACOS_CHECK))
        (entry,) = r.finding_matrix
        assert entry.scope == "all_profiles"
        assert entry.affected_profiles == ("linux-gcc14", "macos-clang")
        assert entry.undetermined_profiles == ()

    def test_platform_spelling_merge_does_not_merge_distinct_overloads(
        self, tmp_path: Path
    ) -> None:
        """The merge is keyed on the whole normalized mangling, never on the
        qualified name — so two genuinely different overloads spelled by a
        Linux and a Mach-O toolchain stay two findings, exactly as two Linux
        toolchains' would."""
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM_OVERLOAD])
        _write_findings_report(
            tmp_path, MACOS_CHECK, "BREAKING", [_REMOVED_ITANIUM_MACHO]
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MACOS_CHECK))
        assert [e.scope for e in r.finding_matrix] == [
            "profile_specific",
            "profile_specific",
        ]
        assert all(e.unaffected_profiles for e in r.finding_matrix)

    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("_ZN3lib3addEii", "itanium"),
            ("__ZN3lib3addEii", "itanium"),
            ("?add@lib@@YAHHH@Z", "msvc"),
            ("plain_c", None),
            ("Foo", None),
            ("", None),
        ],
    )
    def test_mangling_scheme(self, symbol: str, expected: str | None) -> None:
        assert mangling_scheme(symbol) == expected

    def test_platform_prefix_is_normalized_away(self) -> None:
        assert comparable_mangled_symbol("__ZN3lib3addEii") == "_ZN3lib3addEii"
        assert comparable_mangled_symbol("_ZN3lib3addEii") == "_ZN3lib3addEii"
        assert comparable_mangled_symbol("?add@lib@@YAHHH@Z") == "?add@lib@@YAHHH@Z"


#: Same finding as `_SIZE_CHANGED`, but as a profile that ran
#: `--contract` would stamp it -- IN_CONTRACT and gating.
_SIZE_CHANGED_IN_CONTRACT = {
    **_SIZE_CHANGED,
    "contract_relevance": "IN_CONTRACT",
    "compatibility_evaluation_status": "EVALUATED",
    "compatibility_decision": "BREAKING",
    "gate_contribution": 1,
}
#: The same logical finding, as a profile that ran `--contract`
#: under a domain that could not resolve it would stamp it -- unresolved and
#: not gating.
_SIZE_CHANGED_UNRESOLVED = {
    **_SIZE_CHANGED,
    "contract_relevance": "UNKNOWN_UNRESOLVED",
    "compatibility_evaluation_status": "NOT_EVALUATED",
    "compatibility_decision": None,
    "gate_contribution": 0,
}


class TestProfileContractState:
    """CLI-audit P1 (aggregate semantic matrix): `finding_matrix` entries
    carry each affected profile's own ADR-049 contract decision, not just
    membership -- the review's own example of "GCC: IN_CONTRACT/gating" vs.
    "Clang: UNKNOWN_UNRESOLVED/not evaluated" for what looks like one finding."""

    def _findings(self, *entries: dict) -> ReportFindings:
        return ReportFindings(
            findings=tuple(ReportFinding.from_report_entry(e) for e in entries),
            complete=True,
        )

    def test_from_report_entry_reads_the_four_fields(self) -> None:
        finding = ReportFinding.from_report_entry(_SIZE_CHANGED_IN_CONTRACT)
        assert finding.contract_relevance == "IN_CONTRACT"
        assert finding.compatibility_evaluation_status == "EVALUATED"
        assert finding.compatibility_decision == "BREAKING"
        assert finding.gate_contribution == 1

    def test_from_report_entry_defaults_to_none_when_unstamped(self) -> None:
        finding = ReportFinding.from_report_entry(_SIZE_CHANGED)
        assert finding.contract_relevance is None
        assert finding.compatibility_evaluation_status is None
        assert finding.compatibility_decision is None
        assert finding.gate_contribution is None

    def test_from_report_entry_ignores_a_wrongly_typed_gate_contribution(self) -> None:
        finding = ReportFinding.from_report_entry(
            {**_SIZE_CHANGED, "gate_contribution": "not-an-int"}
        )
        assert finding.gate_contribution is None

    def test_from_report_entry_rejects_a_bool_gate_contribution(self) -> None:
        # bool is an int subclass in Python -- a plain isinstance(x, int)
        # check would accept a JSON true/false and round-trip it back out
        # as a JSON boolean, which no schema's gate_contribution allows
        # (CodeRabbit review).
        finding = ReportFinding.from_report_entry(
            {**_SIZE_CHANGED, "gate_contribution": True}
        )
        assert finding.gate_contribution is None

    def test_profile_contract_state_to_dict(self) -> None:
        state = ProfileContractState(
            profile="gcc",
            contract_relevance="IN_CONTRACT",
            compatibility_evaluation_status="EVALUATED",
            compatibility_decision="BREAKING",
            gate_contribution=1,
        )
        assert state.to_dict() == {
            "profile": "gcc",
            "contract_relevance": "IN_CONTRACT",
            "compatibility_evaluation_status": "EVALUATED",
            "compatibility_decision": "BREAKING",
            "gate_contribution": 1,
        }

    def test_absent_when_no_profile_ever_evaluated_a_contract(self) -> None:
        (entry,) = build_finding_matrix(
            {"libfoo": {"gcc": [self._findings(_SIZE_CHANGED)]}}
        )
        assert entry.profile_contract == ()
        assert "profile_contract" not in entry.to_dict()

    def test_present_and_per_profile_when_one_profile_evaluated_a_contract(
        self,
    ) -> None:
        (entry,) = build_finding_matrix(
            {
                "libfoo": {
                    "gcc": [self._findings(_SIZE_CHANGED_IN_CONTRACT)],
                    "clang": [self._findings(_SIZE_CHANGED_UNRESOLVED)],
                }
            }
        )
        assert entry.affected_profiles == ("clang", "gcc")
        by_profile = {p.profile: p for p in entry.profile_contract}
        assert set(by_profile) == {"clang", "gcc"}
        assert by_profile["gcc"].contract_relevance == "IN_CONTRACT"
        assert by_profile["gcc"].compatibility_decision == "BREAKING"
        assert by_profile["gcc"].gate_contribution == 1
        assert by_profile["clang"].contract_relevance == "UNKNOWN_UNRESOLVED"
        assert by_profile["clang"].compatibility_decision is None
        assert by_profile["clang"].gate_contribution == 0

    def test_to_dict_includes_profile_contract_when_populated(self) -> None:
        (entry,) = build_finding_matrix(
            {"libfoo": {"gcc": [self._findings(_SIZE_CHANGED_IN_CONTRACT)]}}
        )
        d = entry.to_dict()
        assert d["profile_contract"] == [
            {
                "profile": "gcc",
                "contract_relevance": "IN_CONTRACT",
                "compatibility_evaluation_status": "EVALUATED",
                "compatibility_decision": "BREAKING",
                "gate_contribution": 1,
            }
        ]

    def test_only_affected_profiles_carry_a_contract_state(self) -> None:
        # A profile unaffected by this finding says nothing about its
        # contract decision *for this finding* -- there is none to report.
        (entry,) = build_finding_matrix(
            {
                "libfoo": {
                    "gcc": [self._findings(_SIZE_CHANGED_IN_CONTRACT)],
                    "clang": [self._findings()],
                }
            }
        )
        assert entry.unaffected_profiles == ("clang",)
        assert {p.profile for p in entry.profile_contract} == {"gcc"}

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_matrix_output_validates_against_schema(self, tmp_path: Path) -> None:
        from abicheck.schemas import load_aggregate_report_schema

        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED_IN_CONTRACT])
        _write_findings_report(tmp_path, CLANG, "BREAKING", [_SIZE_CHANGED_UNRESOLVED])
        d = aggregate_reports_dir(tmp_path, expected=_expect(GCC, CLANG)).to_dict()
        jsonschema.validate(d, load_aggregate_report_schema())
        (entry,) = d["finding_matrix"]
        assert entry["profile_contract"]

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_matrix_output_without_any_contract_evaluation_still_validates(
        self, tmp_path: Path
    ) -> None:
        from abicheck.schemas import load_aggregate_report_schema

        _write_findings_report(tmp_path, GCC, "BREAKING", [_SIZE_CHANGED])
        d = aggregate_reports_dir(tmp_path, expected=_expect(GCC)).to_dict()
        jsonschema.validate(d, load_aggregate_report_schema())
        (entry,) = d["finding_matrix"]
        assert "profile_contract" not in entry

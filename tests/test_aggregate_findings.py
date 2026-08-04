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

"""Unit tests for abicheck.aggregate_findings — per-finding cross-profile
reconciliation (G34 Phase D).

Sibling of ``test_aggregate.py``, mirroring the source split. The rule under
test throughout: a profile may be reported *unaffected* by a finding only
when its reports enumerated their findings in full. Anything short of that
— a missing, unreadable, not-comparable, or partially-unparseable report, or
a ``compare-release`` report, which lists bundle/matrix findings but only
per-library counts — is *undetermined*, never rounded down to clean. An
incomplete report's findings are still read: seeing a finding proves it is
there, whereas not seeing one proves nothing.

The helpers here are deliberately local rather than imported from
``test_aggregate.py``: they are a few lines of report-writing each, and a
cross-test-module import would couple two files that otherwise share only
the subject under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir
from abicheck.aggregate_findings import (
    ReportFinding,
    ReportFindings,
    build_finding_matrix,
    parse_report_findings,
    render_finding_matrix_lines,
    resolve_cross_abi_identity,
    resolve_report_change_identity,
)

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

LINUX = "linux-x86_64"


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
}
_REMOVED_L0 = {
    "kind": "func_removed_elf_only",
    "symbol": "_ZN3lib3addEii",
    "description": "Exported symbol disappeared",
}
_SIZE_CHANGED = {
    "kind": "type_size_changed",
    "symbol": "Foo",
    "description": "size 8 -> 16",
    "old_value": "8",
    "new_value": "16",
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
    """G34 Phase D: per-finding cross-profile reconciliation.

    ``profile_matrix`` says *which profiles* are affected; this says *which
    finding* differs between them, and never claims a profile is clean of a
    finding it was never checked for.
    """

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

    def test_empty_matrix_renders_nothing(self) -> None:
        assert render_finding_matrix_lines([]) == []


#: The same declaration as an Itanium toolchain and MSVC each spell it.
_REMOVED_ITANIUM = {
    "kind": "func_removed",
    "symbol": "_ZN3lib3addEii",
    "description": "Function removed",
}
_REMOVED_MSVC = {
    "kind": "func_removed",
    "symbol": "?add@lib@@YAHHH@Z",
    "description": "Function removed",
}
#: A *different* overload of that same declaration — indistinguishable from
#: the one above by qualified name alone, since neither mangling parser
#: recovers parameter types.
_REMOVED_ITANIUM_OVERLOAD = {
    "kind": "func_removed",
    "symbol": "_ZN3lib3addEd",
    "description": "Function removed",
}

MSVC_CHECK = "libfoo@windows-msvc#release@headers"


class TestCrossAbiReconciliation:
    """Findings spelled under different C++ mangling schemes (Codex review).

    Without this, a Linux profile and a Windows profile reporting one logical
    removal produced two profile-specific entries, each claiming the *other*
    profile was clean of it — the false clean claim this module exists to
    prevent, in the one place the identity model could not see it.
    """

    def test_itanium_and_msvc_spellings_reconcile_to_one_finding(
        self, tmp_path: Path
    ) -> None:
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        (entry,) = r.finding_matrix
        assert entry.scope == "all_profiles"
        assert entry.affected_profiles == ("linux-gcc14", "windows-msvc")
        assert entry.identity_tier == "normalized"

    def test_unrelated_declarations_do_not_merge(self, tmp_path: Path) -> None:
        """The merge must not over-fire: two genuinely different declarations
        stay two findings, and the clean profile really is clean."""
        other = {
            "kind": "func_removed",
            "symbol": "?other@lib@@YAHXZ",
            "description": "Function removed",
        }
        _write_findings_report(tmp_path, GCC, "BREAKING", [_REMOVED_ITANIUM])
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [other])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert [e.scope for e in r.finding_matrix] == [
            "profile_specific",
            "profile_specific",
        ]
        assert all(e.unaffected_profiles for e in r.finding_matrix)

    def test_ambiguous_overloads_stay_split_but_nobody_is_called_clean(
        self, tmp_path: Path
    ) -> None:
        """Neither mangling parser recovers parameter types, so two overloads
        share a cross-ABI key. A profile carrying both makes the pairing
        unguessable — the entries stay separate, and every profile holding a
        sibling finding on the same declaration is undetermined, never
        unaffected."""
        _write_findings_report(
            tmp_path,
            GCC,
            "BREAKING",
            [_REMOVED_ITANIUM, _REMOVED_ITANIUM_OVERLOAD],
        )
        _write_findings_report(tmp_path, MSVC_CHECK, "BREAKING", [_REMOVED_MSVC])
        r = aggregate_reports_dir(tmp_path, expected=_expect(GCC, MSVC_CHECK))
        assert len(r.finding_matrix) == 3
        assert {e.scope for e in r.finding_matrix} == {"undetermined"}
        assert all(e.unaffected_profiles == () for e in r.finding_matrix)

    def test_type_level_finding_has_no_cross_abi_key(self) -> None:
        """`symbol` is a type name, not a mangling — nothing to normalize, and
        the helper must not guess one."""
        assert resolve_cross_abi_identity(_SIZE_CHANGED) is None

    def test_finding_with_no_symbol_has_no_cross_abi_key(self) -> None:
        assert resolve_cross_abi_identity({"kind": "func_removed"}) is None

    def test_extern_c_symbol_has_no_cross_abi_key(self) -> None:
        """An unmangled C symbol is already scheme-independent."""
        assert (
            resolve_cross_abi_identity(
                {"kind": "func_removed", "symbol": "plain_c", "description": "gone"}
            )
            is None
        )

    def test_cross_abi_key_keeps_a_removal_and_an_addition_apart(self) -> None:
        """The discriminator rides along, so normalizing the *name* never
        merges two different events on one declaration."""
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

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

"""ADR-065's scope-completeness axis as a gate ``abicheck aggregate`` folds
-- the third sibling of ``tests/test_aggregate.py``'s
``TestContractCoverageAxis`` and ``tests/test_aggregate_analysis_assurance.py``.

Before this, a release report whose ``run_outcome.scope`` read
``incomplete`` under ``--on-incomplete-scope block`` (or that completed no
comparison) carried its blocking ``1`` only in the ``exit`` block, which
the aggregate never read: ``gate``/``operational`` stay ``none`` on that
axis, so the originating comparison exited ``1`` while the aggregate read
the target green (Codex review, tenth round on PR #1079). A sibling file
for the same file-size reason its siblings are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

LINUX = "linux-x86_64"


def _write_report(
    d: Path,
    target_id: str,
    verdict: str | None,
    *,
    prefix: str = "abi-report-",
    severity: dict | None = None,
    **extra,
) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    if severity is not None:
        payload["severity"] = severity
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _expect(*required: str) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), [])


def _exit(**keys: object) -> dict[str, object]:
    return {"exit": dict(keys)}


class TestScopeCompletenessAxis:
    @pytest.mark.parametrize(
        "key", ["incomplete_scope_contribution", "no_comparison_completed_contribution"]
    )
    def test_a_gated_target_raises_a_clean_aggregate_to_one(
        self, tmp_path: Path, key: str
    ) -> None:
        _write_report(tmp_path, LINUX, "NO_CHANGE", **_exit(**{key: 1}))
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 1
        assert result.scope_completeness_targets == (LINUX,)
        assert result.exit_code() == 1
        assert not result.passed

    def test_it_never_lowers_a_real_break(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path,
            LINUX,
            "BREAKING",
            severity={"exit_code": 4, "blocking": True},
            **_exit(incomplete_scope_contribution=1),
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 1
        assert result.exit_code() == 4

    def test_a_report_without_the_block_contributes_nothing(
        self, tmp_path: Path
    ) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0
        assert result.exit_code() == 0

    def test_a_warn_policy_release_contributes_nothing(self, tmp_path: Path) -> None:
        # `--on-incomplete-scope warn` (the default) records 0: the
        # incompleteness is stated, accepted, and never folded here either.
        _write_report(
            tmp_path,
            LINUX,
            "NO_CHANGE",
            **_exit(
                incomplete_scope_contribution=0, no_comparison_completed_contribution=0
            ),
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0
        assert result.exit_code() == 0

    def test_the_axis_fires_even_when_the_severity_gate_reads_clean(
        self, tmp_path: Path
    ) -> None:
        # The exact scenario Codex reproduced: `run_outcome.scope: incomplete`
        # with gate/operational `none`, the blocking 1 only in `exit`.
        _write_report(
            tmp_path,
            LINUX,
            "NO_CHANGE",
            severity={"exit_code": 0, "blocking": False},
            run_outcome={
                "schema_version": "1.1",
                "compatibility": "NO_CHANGE",
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "existing",
                "scope": "incomplete",
            },
            **_exit(incomplete_scope_contribution=1),
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 1
        assert result.exit_code() == 1

    def test_a_scan_report_carries_the_block_inside_its_diff(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.28",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "diff": _exit(no_comparison_completed_contribution=1),
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 1
        assert result.scope_completeness_targets == (LINUX,)

    def test_the_summary_block_and_target_entry_name_it(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path, LINUX, "NO_CHANGE", **_exit(incomplete_scope_contribution=1)
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        payload = result.to_dict()
        assert payload["scope_completeness"] == {
            "exit_contribution": 1,
            "incomplete_targets": [LINUX],
        }
        assert payload["targets"][0]["scope_completeness_exit"] == 1
        text = result.render_text()
        assert "Comparison scope:" in text
        assert f"incomplete on {LINUX}" in text

    def test_a_clean_target_is_not_listed_or_rendered(self, tmp_path: Path) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.to_dict()["scope_completeness"] == {
            "exit_contribution": 0,
            "incomplete_targets": [],
        }
        assert "Comparison scope:" not in result.render_text()

    def test_reported_separately_from_the_other_floors(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            contract_coverage_exit_contribution=1,
            analysis_assurance_exit_contribution=1,
            **_exit(incomplete_scope_contribution=1),
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.analysis_assurance_exit == 1
        assert result.scope_completeness_exit == 1
        assert result.exit_code() == 1

    def test_an_unavailable_target_contributes_nothing(self, tmp_path: Path) -> None:
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0

    @pytest.mark.parametrize(  # noqa: PT006 - single unnamed param, matches sibling
        "bad",
        ["1", True, -1, None, {"exit": 1}, 2, 4],
        ids=["str", "bool", "negative", "null", "object", "two", "four"],
    )
    def test_a_malformed_contribution_fails_open(self, tmp_path: Path, bad) -> None:
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", **_exit(incomplete_scope_contribution=bad)
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0
        assert result.exit_code() == 0

    def test_a_non_mapping_exit_block_fails_open(self, tmp_path: Path) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE", exit=1)
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0

    @pytest.mark.skipif(
        pytest.importorskip("jsonschema", reason="jsonschema not installed") is None,
        reason="jsonschema not installed",
    )
    def test_the_output_validates_against_the_schema(self, tmp_path: Path) -> None:
        import jsonschema

        from abicheck.schemas import load_aggregate_report_schema

        _write_report(
            tmp_path, LINUX, "NO_CHANGE", **_exit(incomplete_scope_contribution=1)
        )
        payload = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).to_dict()
        jsonschema.validate(payload, load_aggregate_report_schema())


class TestScopeCompletenessFromARealRelease:
    """The release fan-out's own JSON, written by a real `compare` over two
    stored packages and aggregated back: the number that gated the release
    is the number the aggregate folds."""

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_an_incomplete_release_aggregates_as_it_exited(
        self, tmp_path: Path, policy: str
    ) -> None:
        from click.testing import CliRunner
        from test_release_scope_completeness import _write, _write_stored_package

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot

        old = tmp_path / "old_pkg"
        _write_stored_package(
            old,
            {
                "liba.so": AbiSnapshot(library="liba.so", version="1"),
                "libb.so": AbiSnapshot(library="libb.so", version="1"),
            },
        )
        new = tmp_path / "new"
        _write(new, "liba.so.json", AbiSnapshot(library="liba.so", version="2"))
        reports = tmp_path / "reports"
        reports.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "-j",
                "1",
                "--on-incomplete-scope",
                policy,
                "--format",
                "json",
                "-o",
                str(reports / f"abi-report-{LINUX}.json"),
            ],
        )
        expected_exit = 1 if policy == "block" else 0
        assert result.exit_code == expected_exit, result.output
        agg = aggregate_reports_dir(reports, expected=_expect(LINUX))
        assert agg.scope_completeness_exit == expected_exit
        assert agg.exit_code() == expected_exit

    def test_a_release_that_completed_nothing_aggregates_to_one(
        self, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner
        from test_release_scope_completeness import _write

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot

        old, new = tmp_path / "old", tmp_path / "new"
        _write(old, "liba.so.json", AbiSnapshot(library="liba.so", version="1"))
        _write(new, "libb.so.json", AbiSnapshot(library="libb.so", version="1"))
        reports = tmp_path / "reports"
        reports.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "-j",
                "1",
                "--format",
                "json",
                "-o",
                str(reports / f"abi-report-{LINUX}.json"),
            ],
        )
        assert result.exit_code == 1, result.output
        agg = aggregate_reports_dir(reports, expected=_expect(LINUX))
        assert agg.scope_completeness_exit == 1
        assert agg.exit_code() == 1


class TestStoredDispatchShapeAndAcceptedGaps:
    """Eleventh Codex round: the stored/stored and stored/live drivers emit
    no root ``exit`` block, so the axis must read ``comparison_scope`` too;
    and a target that accepted its gap under ``warn`` must still be named."""

    @pytest.mark.parametrize(
        "key",
        [
            "incomplete_scope_exit_contribution",
            "no_comparison_completed_exit_contribution",
        ],
    )
    def test_a_comparison_scope_section_without_an_exit_block_gates(
        self, tmp_path: Path, key: str
    ) -> None:
        _write_report(
            tmp_path,
            LINUX,
            "NO_CHANGE",
            comparison_scope={"completeness": "incomplete", key: 1},
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 1
        assert result.scope_completeness_targets == (LINUX,)
        assert result.exit_code() == 1

    @pytest.mark.parametrize("via", ["run_outcome", "comparison_scope"])
    def test_an_accepted_gap_is_listed_but_does_not_gate(
        self, tmp_path: Path, via: str
    ) -> None:
        marker = (
            {
                "run_outcome": {
                    "schema_version": "1.1",
                    "compatibility": "NO_CHANGE",
                    "assurance": None,
                    "gate": "none",
                    "operational": "none",
                    "lifecycle": "existing",
                    "scope": "incomplete",
                }
            }
            if via == "run_outcome"
            else {"comparison_scope": {"completeness": "incomplete", "policy": "warn"}}
        )
        _write_report(
            tmp_path,
            LINUX,
            "NO_CHANGE",
            **marker,
            **_exit(
                incomplete_scope_contribution=0, no_comparison_completed_contribution=0
            ),
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_exit == 0
        assert result.scope_completeness_targets == (LINUX,)
        assert result.exit_code() == 0
        payload = result.to_dict()
        assert payload["scope_completeness"] == {
            "exit_contribution": 0,
            "incomplete_targets": [LINUX],
        }
        assert payload["targets"][0]["scope_completeness_exit"] == 0
        assert f"incomplete on {LINUX}" in result.render_text()

    def test_a_complete_scope_is_not_listed(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path, LINUX, "NO_CHANGE", comparison_scope={"completeness": "complete"}
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.scope_completeness_targets == ()

    def test_an_accepted_gap_is_named_per_profile(self, tmp_path: Path) -> None:
        check = "libfoo@linux-gcc14#release@headers"
        _write_report(
            tmp_path,
            check,
            "NO_CHANGE",
            comparison_scope={"completeness": "incomplete"},
        )
        res = aggregate_reports_dir(tmp_path, expected=_expect(check))
        (entry,) = res.profile_matrix
        assert entry.scope_incomplete_profiles == ("linux-gcc14",)
        assert entry.affected_profiles == ()
        assert res.exit_code() == 0

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_a_real_stored_pair_report_aggregates_as_it_exited(
        self, tmp_path: Path, policy: str
    ) -> None:
        from click.testing import CliRunner
        from test_release_scope_completeness import _elf_snap, _facts_file

        from abicheck.cli import main

        libs = {"libok.so": _elf_snap("libok.so"), "libdeg.so": _elf_snap("libdeg.so")}
        old = _facts_file(
            tmp_path, "old.bundlefacts.json", libs, degraded={"libdeg.so": "boom"}
        )
        new = _facts_file(tmp_path, "new.bundlefacts.json", libs)
        reports = tmp_path / "reports"
        reports.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old),
                str(new),
                "--on-incomplete-scope",
                policy,
                "--format",
                "json",
                "-o",
                str(reports / f"abi-report-{LINUX}.json"),
            ],
        )
        expected_exit = 1 if policy == "block" else 0
        assert result.exit_code == expected_exit, result.output
        doc = json.loads((reports / f"abi-report-{LINUX}.json").read_text())
        assert "exit" not in doc  # the stored dispatcher's own shape
        agg = aggregate_reports_dir(reports, expected=_expect(LINUX))
        assert agg.scope_completeness_exit == expected_exit
        assert agg.scope_completeness_targets == (LINUX,)
        assert agg.exit_code() == expected_exit


class TestIncompleteScopePolicyIsInTheDigest:
    def test_warn_and_block_digests_differ(self, tmp_path: Path) -> None:
        """Two otherwise identical incomplete releases exit 0 and 1 under
        the two policies, so their effective-config digests must differ
        (Codex review, eleventh round)."""
        from click.testing import CliRunner
        from test_release_scope_completeness import _write, _write_stored_package

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot

        old = tmp_path / "old_pkg"
        _write_stored_package(
            old,
            {
                "liba.so": AbiSnapshot(library="liba.so", version="1"),
                "libb.so": AbiSnapshot(library="libb.so", version="1"),
            },
        )
        new = tmp_path / "new"
        _write(new, "liba.so.json", AbiSnapshot(library="liba.so", version="1"))
        docs = {}
        for policy in ("warn", "block"):
            out = tmp_path / f"{policy}.json"
            CliRunner().invoke(
                main,
                [
                    "compare",
                    str(old),
                    str(new),
                    "-j",
                    "1",
                    "--on-incomplete-scope",
                    policy,
                    "--format",
                    "json",
                    "-o",
                    str(out),
                ],
            )
            docs[policy] = json.loads(out.read_text())
        for policy, doc in docs.items():
            assert doc["effective_config_fields"]["gate.on_incomplete_scope"] == policy
        assert (
            docs["warn"]["effective_config_digest"]
            != docs["block"]["effective_config_digest"]
        )
        # And the single-pair report records the axis as not applicable.
        single = tmp_path / "single.json"
        CliRunner().invoke(
            main,
            [
                "compare",
                str(new / "liba.so.json"),
                str(new / "liba.so.json"),
                "--format",
                "json",
                "-o",
                str(single),
            ],
        )
        fields = json.loads(single.read_text())["effective_config_fields"]
        assert fields["gate.on_incomplete_scope"] == ""

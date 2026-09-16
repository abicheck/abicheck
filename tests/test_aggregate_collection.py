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

"""One declaration -> ``aggregate``'s two inputs, and the outcome it produces.

The failures these tests pin are all ways a run reports *less* than it had and
looks clean doing it: a check that never ran silently dropped from the expected
set, a manifest picked up as an extra target, a report re-labelled as another
check, and a document that carries a version key while describing nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.workflows.aggregate.collection import (
    AggregateValidationError,
    CollectionError,
    DeclaredCheck,
    collect_reports,
    parse_check_declaration,
    summarize,
    validate_aggregate_document,
)

_PROFILE = "linux-x86_64-gcc"


def _report(
    path: Path, *, target_id: str | None = None, verdict: str = "COMPATIBLE"
) -> Path:
    payload: dict[str, object] = {"verdict": verdict, "changes": []}
    if target_id is not None:
        payload["target_id"] = target_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _declare(tmp_path: Path, **kwargs: object) -> tuple[Path, Path]:
    return tmp_path / "reports", tmp_path / "expected.json"


def _ok_document(target_ids: list[str], *, status: str = "pass") -> dict[str, object]:
    return {
        "aggregate_schema_version": "1.0",
        "status": status,
        "compatibility": {},
        "coverage": {"status": "complete"},
        "gate": {},
        "targets": [
            {"target_id": target_id, "state": "analyzed"} for target_id in target_ids
        ],
    }


class TestDeclaration:
    def test_an_empty_declaration_is_refused(self) -> None:
        """Zero declared checks aggregates zero targets, which reads as clean."""
        with pytest.raises(CollectionError, match="non-empty"):
            parse_check_declaration([])

    def test_a_duplicate_check_id_is_refused(self) -> None:
        entry = {
            "id": f"libfoo@{_PROFILE}#main@headers",
            "report": "",
            "required": True,
        }
        with pytest.raises(CollectionError, match="declared twice"):
            parse_check_declaration([entry, dict(entry)])

    def test_a_misspelled_field_is_refused(self) -> None:
        with pytest.raises(CollectionError, match="unrecognized field"):
            parse_check_declaration(
                [{"id": "a@p#c@headers", "report": "", "require": False}]
            )

    @pytest.mark.parametrize(
        "bad_id", ["a/b@p#c@headers", "../escape", "a\\b@p#c@headers", "x..y"]
    )
    def test_an_id_that_cannot_be_a_filename_is_refused(self, bad_id: str) -> None:
        with pytest.raises(CollectionError, match="cannot appear in"):
            parse_check_declaration([{"id": bad_id, "report": ""}])

    @pytest.mark.parametrize(
        "good_id",
        [
            f"libfoo@{_PROFILE}#accepted-main@headers",
            f"libfoo@{_PROFILE}#release-contract@headers~release",
            f"libfoo@{_PROFILE}#release-contract@binary!env1~rel",
        ],
    )
    def test_every_check_id_separator_survives(self, good_id: str) -> None:
        """``@``/``#``/``~``/``!`` carry the identity.

        Sanitizing them away -- the obvious "make it filename-safe" move --
        makes ``aggregate`` read a different id back out of the filename, so
        every report aggregates as an unavailable target while the run looks
        like it produced them.
        """
        (check,) = parse_check_declaration([{"id": good_id, "report": ""}])
        assert check.id == good_id

    def test_an_absent_report_is_a_state_not_an_error(self) -> None:
        (check,) = parse_check_declaration([{"id": "a@p#c@headers", "report": ""}])
        assert check.report is None
        assert check.required is True


class TestCollection:
    def test_both_components_and_both_channels_keep_their_identity(
        self, tmp_path: Path
    ) -> None:
        ids = [
            f"libfoo@{_PROFILE}#accepted-main@headers",
            f"libbar@{_PROFILE}#accepted-main@headers",
            f"libfoo@{_PROFILE}#release-contract@headers~release",
            f"libbar@{_PROFILE}#release-contract@headers~release",
        ]
        checks = [
            DeclaredCheck(id=cid, report=_report(tmp_path / f"in{i}.json"))
            for i, cid in enumerate(ids)
        ]
        reports_dir, manifest = _declare(tmp_path)
        result = collect_reports(
            checks, reports_dir=reports_dir, manifest_path=manifest
        )
        assert sorted(result.present) == sorted(ids)
        assert sorted(p.name for p in reports_dir.glob("*.json")) == sorted(
            f"abi-report-{cid}.json" for cid in ids
        )
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        assert sorted(row["id"] for row in declared["targets"]) == sorted(ids)

    def test_a_check_with_no_report_stays_declared(self, tmp_path: Path) -> None:
        reports_dir, manifest = _declare(tmp_path)
        checks = [
            DeclaredCheck(id="ran@p#c@headers", report=_report(tmp_path / "r.json")),
            DeclaredCheck(id="never@p#c@headers", report=None),
        ]
        result = collect_reports(
            checks, reports_dir=reports_dir, manifest_path=manifest
        )
        assert result.missing == ["never@p#c@headers"]
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        assert {row["id"] for row in declared["targets"]} == {
            "ran@p#c@headers",
            "never@p#c@headers",
        }
        assert not (reports_dir / "abi-report-never@p#c@headers.json").exists()

    def test_the_manifest_may_not_live_in_the_reports_directory(
        self, tmp_path: Path
    ) -> None:
        reports_dir = tmp_path / "reports"
        with pytest.raises(CollectionError, match="picked up as an extra"):
            collect_reports(
                [DeclaredCheck(id="a@p#c@headers")],
                reports_dir=reports_dir,
                manifest_path=reports_dir / "expected.json",
            )

    def test_a_foreign_document_in_the_destination_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A previous run's ``aggregate.json`` is a target ``aggregate`` would read."""
        reports_dir, manifest = _declare(tmp_path)
        reports_dir.mkdir()
        (reports_dir / "aggregate.json").write_text("{}", encoding="utf-8")
        with pytest.raises(CollectionError, match="additional targets"):
            collect_reports(
                [DeclaredCheck(id="a@p#c@headers")],
                reports_dir=reports_dir,
                manifest_path=manifest,
            )

    def test_a_previous_collection_is_cleared_not_accumulated(
        self, tmp_path: Path
    ) -> None:
        reports_dir, manifest = _declare(tmp_path)
        checks = [
            DeclaredCheck(id="a@p#c@headers", report=_report(tmp_path / "r.json"))
        ]
        collect_reports(checks, reports_dir=reports_dir, manifest_path=manifest)
        (reports_dir / "abi-report-ghost@p#c@headers.json").write_text(
            "{}", encoding="utf-8"
        )
        collect_reports(checks, reports_dir=reports_dir, manifest_path=manifest)
        assert [p.name for p in reports_dir.glob("*.json")] == [
            "abi-report-a@p#c@headers.json"
        ]

    def test_a_reports_own_target_id_outranks_the_filename(
        self, tmp_path: Path
    ) -> None:
        """A renamed file must not lose an identity the report already records."""
        recorded = f"libfoo@{_PROFILE}#accepted-main@headers"
        source = _report(tmp_path / "scrambled-name.json", target_id=recorded)
        reports_dir, manifest = _declare(tmp_path)
        collect_reports(
            [DeclaredCheck(id=recorded, report=source)],
            reports_dir=reports_dir,
            manifest_path=manifest,
        )
        assert (reports_dir / f"abi-report-{recorded}.json").is_file()

    def test_a_declaration_disagreeing_with_the_report_is_refused(
        self, tmp_path: Path
    ) -> None:
        source = _report(tmp_path / "r.json", target_id="libfoo@p#c@headers")
        reports_dir, manifest = _declare(tmp_path)
        with pytest.raises(CollectionError, match="records its own target_id"):
            collect_reports(
                [DeclaredCheck(id="libRENAMED@p#c@headers", report=source)],
                reports_dir=reports_dir,
                manifest_path=manifest,
            )

    @pytest.mark.parametrize(
        ("content", "why"),
        [("not json{", "not valid JSON"), ("[1,2]", "not a JSON object")],
    )
    def test_a_malformed_report_is_unusable_not_missing(
        self, tmp_path: Path, content: str, why: str
    ) -> None:
        """The check ran and produced something. Losing that distinction is how
        a corrupted report reads as "never ran"."""
        bad = tmp_path / "bad.json"
        bad.write_text(content, encoding="utf-8")
        reports_dir, manifest = _declare(tmp_path)
        result = collect_reports(
            [DeclaredCheck(id="a@p#c@headers", report=bad)],
            reports_dir=reports_dir,
            manifest_path=manifest,
        )
        assert result.missing == []
        assert why in result.unusable["a@p#c@headers"]
        assert result.expected == 1

    def test_one_components_failure_keeps_the_others_diagnostics(
        self, tmp_path: Path
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{", encoding="utf-8")
        reports_dir, manifest = _declare(tmp_path)
        result = collect_reports(
            [
                DeclaredCheck(
                    id="good@p#c@headers", report=_report(tmp_path / "g.json")
                ),
                DeclaredCheck(id="bad@p#c@headers", report=bad),
                DeclaredCheck(id="absent@p#c@headers", report=None),
            ],
            reports_dir=reports_dir,
            manifest_path=manifest,
        )
        assert result.present == ["good@p#c@headers"]
        assert (reports_dir / "abi-report-good@p#c@headers.json").is_file()
        assert result.expected == 3

    def test_the_gate_block_is_folded_into_the_manifest(self, tmp_path: Path) -> None:
        reports_dir, manifest = _declare(tmp_path)
        collect_reports(
            [DeclaredCheck(id="a@p#c@headers")],
            reports_dir=reports_dir,
            manifest_path=manifest,
            gate={"missing_required": "warn"},
        )
        assert json.loads(manifest.read_text(encoding="utf-8"))["gate"] == {
            "missing_required": "warn"
        }

    def test_the_manifest_is_written_atomically(self, tmp_path: Path) -> None:
        """No temp file may survive, and the manifest must never be partial.

        A half-written manifest is still a file ``aggregate`` reads, and it
        declares a *shorter* expected set than the run actually had.
        """
        reports_dir, manifest = _declare(tmp_path)
        collect_reports(
            [DeclaredCheck(id="a@p#c@headers")],
            reports_dir=reports_dir,
            manifest_path=manifest,
        )
        assert list(manifest.parent.glob("*.tmp")) == []
        json.loads(manifest.read_text(encoding="utf-8"))


class TestValidation:
    def test_the_happy_document_passes(self) -> None:
        validate_aggregate_document(_ok_document(["a@p#c@headers"]), expected=1)

    def test_a_version_key_alone_is_not_a_valid_document(self) -> None:
        """The original hazard: a file that carries a schema version and
        describes nothing would satisfy a key-presence check."""
        with pytest.raises(AggregateValidationError):
            validate_aggregate_document({"aggregate_schema_version": "1.0"})

    @pytest.mark.parametrize("block", ["compatibility", "coverage", "gate"])
    def test_a_missing_block_is_refused(self, block: str) -> None:
        document = _ok_document(["a@p#c@headers"])
        document.pop(block)
        with pytest.raises(AggregateValidationError, match=block):
            validate_aggregate_document(document)

    def test_zero_targets_is_an_operational_failure(self) -> None:
        document = _ok_document([])
        with pytest.raises(AggregateValidationError, match="no targets at all"):
            validate_aggregate_document(document)

    @pytest.mark.parametrize("status", ["", "ok", "PASS", None, 1])
    def test_only_pass_or_fail_is_a_status(self, status: object) -> None:
        document = _ok_document(["a@p#c@headers"])
        document["status"] = status
        with pytest.raises(AggregateValidationError, match="status"):
            validate_aggregate_document(document)

    @pytest.mark.parametrize("coverage", ["", "ok", "COMPLETE", "done"])
    def test_coverage_is_checked_against_the_canonical_vocabulary(
        self, coverage: str
    ) -> None:
        """Checked against ``CoverageStatus`` itself, so it cannot drift from
        what ``aggregate`` emits the way a hand-copied string list does."""
        document = _ok_document(["a@p#c@headers"])
        document["coverage"] = {"status": coverage}
        with pytest.raises(AggregateValidationError, match="coverage status"):
            validate_aggregate_document(document)

    @pytest.mark.parametrize("coverage", ["complete", "partial", "empty"])
    def test_every_canonical_coverage_value_is_accepted(self, coverage: str) -> None:
        document = _ok_document(["a@p#c@headers"])
        document["coverage"] = {"status": coverage}
        validate_aggregate_document(document)

    def test_an_unknown_target_state_is_refused(self) -> None:
        document = _ok_document(["a@p#c@headers"])
        document["targets"] = [{"target_id": "a@p#c@headers", "state": "maybe"}]
        with pytest.raises(AggregateValidationError, match="unrecognised state"):
            validate_aggregate_document(document)

    def test_a_duplicated_target_is_refused(self) -> None:
        document = _ok_document(["a@p#c@headers", "a@p#c@headers"])
        with pytest.raises(AggregateValidationError, match="twice"):
            validate_aggregate_document(document)

    def test_a_document_shorter_than_the_declaration_is_refused(self) -> None:
        """The one thing the document cannot know it is short of."""
        with pytest.raises(AggregateValidationError, match="but 4 were declared"):
            validate_aggregate_document(_ok_document(["a@p#c@headers"]), expected=4)


class TestSummary:
    def test_channels_are_reported_separately(self) -> None:
        document = _ok_document(
            [
                f"libfoo@{_PROFILE}#accepted-main@headers",
                f"libbar@{_PROFILE}#accepted-main@headers",
                f"libfoo@{_PROFILE}#release-contract@headers~release",
            ]
        )
        document["targets"].append(
            {
                "target_id": f"libbar@{_PROFILE}#release-contract@headers~release",
                "state": "unavailable",
            }
        )
        summary = summarize(document)
        assert summary["analyzed"] == 3
        assert summary["unavailable"] == 1
        # "one component missing on the release channel" is a different fact
        # from "one of four checks missing", and a single total cannot say it.
        assert summary["channels"]["accepted-main"] == {"analyzed": 2, "unavailable": 0}
        assert summary["channels"]["release-contract"] == {
            "analyzed": 1,
            "unavailable": 1,
        }

    def test_a_non_check_id_target_groups_under_no_channel(self) -> None:
        summary = summarize(_ok_document(["plain-report-name"]))
        assert summary["channels"] == {"": {"analyzed": 1, "unavailable": 0}}

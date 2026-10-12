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

"""A failed release member's classification: four outcomes, and their order.

`workflows.release_member_failures.classify_member_failure`, driven through
the member worker that owns it (`workflows.release_members.
compare_release_member`, with its comparison made to raise) and the CLI's
reporter, which writes a `not_comparable` member's `--output-dir` document. The classification carries real ADR
consequences per branch -- `not_comparable` (ADR-050 D2) and `unsupported`
(ADR-065 D6) are deliberately *not* the `ERROR`/exit-4 bucket a crash uses,
because one is an expected contract outcome, one is an incompleteness signal
on the scope axis, and only the third is an abicheck bug.

The order is load-bearing, so it is tested as order rather than as four
independent cases: an exception matching an earlier branch must not fall
through to a later one even though `click.ClickException` and `Exception`
would both also accept it.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from abicheck.errors import (
    IncompatibleSnapshotSchemaError,
    ProfileMismatchError,
    ScopeMismatchError,
    UnsupportedArtifactError,
)
from abicheck.frontends.cli.release_member_reporter import CliReleaseReporter
from abicheck.workflows.release_members import (
    ReleaseMemberContext,
    compare_release_member,
    release_parent_request,
)


def _entry(exc: BaseException, output_dir: Path | None = None) -> dict[str, object]:
    def _raise(*_a: object, **_k: object) -> object:
        raise exc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("abicheck.workflows.release_members.compare_member_pair", _raise)
        return compare_release_member(
            "libfoo.so",
            ReleaseMemberContext(
                request=release_parent_request(old_version="1.0", new_version="2.0"),
                old_map={"libfoo.so": Path("/rel/libfoo.so")},
                new_map={"libfoo.so": Path("/rel2/libfoo.so")},
                reporter=CliReleaseReporter(output_dir=output_dir),
            ),
        )


class TestEachOutcomeIsItsOwnVerdict:
    """The four buckets, and that they stay distinct.

    Collapsing any two would change a release's exit code: `not_comparable`
    ranks above `ERROR` in `RELEASE_VERDICT_ORDER` (exit 16 vs 4), and
    `unsupported` is a scope-completeness signal that must not be floored to
    4 the way an operational crash is.
    """

    @pytest.mark.parametrize(
        ("exc", "expected_verdict", "detail_key"),
        (
            (ProfileMismatchError("profiles differ"), "not_comparable", "reason"),
            (ScopeMismatchError("scopes differ"), "not_comparable", "reason"),
            (
                IncompatibleSnapshotSchemaError("schema 99 is newer"),
                "unsupported",
                "reason",
            ),
            (UnsupportedArtifactError("no backend"), "unsupported", "reason"),
            (click.ClickException("bad flag"), "ERROR", "error"),
            (click.UsageError("bad usage"), "ERROR", "error"),
            (RuntimeError("boom"), "ERROR", "error"),
        ),
    )
    def test_verdict_and_detail_key(
        self, exc: BaseException, expected_verdict: str, detail_key: str
    ) -> None:
        entry = _entry(exc)
        assert entry["library"] == "libfoo.so"
        assert entry["verdict"] == expected_verdict
        # Which key carries the text matters: the release renderers read
        # `reason` for an expected outcome and `error` for a failure, so a
        # branch putting the text under the wrong key loses it in the report.
        assert detail_key in entry
        assert str(entry[detail_key])
        # Exactly that key -- an entry carrying both would let a renderer
        # reading either one appear correct while the other went stale.
        assert ({"reason", "error"} & set(entry)) == {detail_key}

    def test_the_four_verdict_strings_are_exactly_three_distinct_values(self) -> None:
        """A guard against a future branch quietly reusing another's verdict."""
        verdicts = {
            str(_entry(exc)["verdict"])
            for exc in (
                ProfileMismatchError("x"),
                IncompatibleSnapshotSchemaError("x"),
                RuntimeError("x"),
            )
        }
        assert verdicts == {"not_comparable", "unsupported", "ERROR"}


class TestOrderIsLoadBearing:
    """Earlier branches must win over the later, broader ones.

    Every exception the first two branches catch is also an `Exception`, and
    a `click.UsageError` is also a `click.ClickException`, so a reordering
    would silently reclassify a contract mismatch as a crash -- changing the
    release's exit from 16 to 4 with no test failing anywhere unless the
    precedence itself is asserted.
    """

    def test_a_contract_mismatch_is_not_swallowed_by_the_generic_branch(self) -> None:
        # Subclass of Exception, so the final `return` would also accept it.
        assert isinstance(ProfileMismatchError("x"), Exception)
        assert _entry(ProfileMismatchError("x"))["verdict"] == "not_comparable"

    def test_an_unsupported_artifact_is_not_swallowed_either(self) -> None:
        assert isinstance(UnsupportedArtifactError("x"), Exception)
        assert _entry(UnsupportedArtifactError("x"))["verdict"] == "unsupported"

    def test_a_click_error_uses_format_message_not_str(self) -> None:
        """`ClickException.format_message()`, not `str(exc)`.

        For a `UsageError` these differ, and the formatted one is what a
        user is meant to read.
        """
        exc = click.UsageError("no such option: --nope")
        assert _entry(exc)["error"] == exc.format_message()


class TestTheNotComparableSidecarReport:
    """`--output-dir` gets a per-member document, and only for this branch."""

    def test_written_with_the_mismatch_kind_and_reason(self, tmp_path: Path) -> None:
        entry = _entry(ScopeMismatchError("scopes differ"), output_dir=tmp_path)
        written = list(tmp_path.glob("*.json"))
        assert [p.name for p in written] == ["libfoo.json"]
        doc = json.loads(written[0].read_text())
        # The document has to name *which* contract disagreed and why, or a
        # consumer sees only "not comparable" with no way to act on it.
        text = json.dumps(doc)
        assert "scope_mismatch" in text
        assert "scopes differ" in text
        assert entry["verdict"] == "not_comparable"

    def test_profile_mismatch_records_its_own_kind(self, tmp_path: Path) -> None:
        _entry(ProfileMismatchError("profiles differ"), output_dir=tmp_path)
        doc = json.dumps(json.loads((tmp_path / "libfoo.json").read_text()))
        assert "profile_mismatch" in doc
        assert "scope_mismatch" not in doc

    @pytest.mark.parametrize(
        "exc",
        (
            IncompatibleSnapshotSchemaError("x"),
            UnsupportedArtifactError("x"),
            click.ClickException("x"),
            RuntimeError("x"),
        ),
    )
    def test_no_other_branch_writes_a_sidecar(
        self, exc: BaseException, tmp_path: Path
    ) -> None:
        """Only the contract-mismatch branch has a document to write.

        An `unsupported` or `ERROR` member writing a `not_comparable`
        document would misreport its own outcome in the sidecar a
        report-driven consumer reads.
        """
        _entry(exc, output_dir=tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_nothing_is_written_without_an_output_dir(self) -> None:
        entry = _entry(ScopeMismatchError("scopes differ"), output_dir=None)
        assert entry["verdict"] == "not_comparable"

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

"""Negative controls for the trusted publisher's run selection (ADR-073).

Every check in :mod:`abicheck.frontends.action.run_selection` exists because
something specific goes wrong without it, so each is tested by *breaking* the
thing it guards and asserting the exact refusal code -- not merely that
"something raised". A test that accepts any exception passes equally against
an implementation that refuses everything, which would be the same defect
wearing a different hat: a boundary nobody can get through is not a boundary
that works.

A positive control accompanies each group. Without one, an implementation
that rejected every input would satisfy all the negatives at once.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import abicheck.frontends.action.run_selection as run_selection_module
from abicheck.frontends.action.cli import EXIT_REFUSED, action_cli
from abicheck.frontends.action.run_selection import (
    ExtractionLimits,
    RunExpectation,
    SourceRun,
    SourceRunRejected,
    extract_artifact,
    inspect_archive,
    load_json_document,
    resolve_pull_request,
    safe_entry_path,
    select_artifact,
    verify_source_run,
    verify_tested_sha,
)

REPO = "example-org/example-lib"
HEAD_SHA = "1" * 40
MERGE_SHA = "2" * 40


def run_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "id": 555,
        "run_attempt": 1,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": "a-contributor/example-lib"},
        "path": ".github/workflows/abi-analysis.yml",
        "name": "ABI analysis",
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD_SHA,
        "head_branch": "feature",
    }
    document.update(overrides)
    return document


EXPECTED = RunExpectation(
    repository=REPO,
    workflow=".github/workflows/abi-analysis.yml",
    event="pull_request",
    run_id="555",
    allowed_conclusions=("success",),
)


def pull_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "number": 216,
        "state": "open",
        "head": {"sha": HEAD_SHA, "repo": {"full_name": "a-contributor/example-lib"}},
        "base": {"sha": "0" * 40, "repo": {"full_name": REPO}},
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# 1. Is this the run we think it is?
# ---------------------------------------------------------------------------


class TestSourceRunVerification:
    def test_positive_control_a_matching_run_is_accepted(self) -> None:
        """Without this, every negative below is satisfied by an
        implementation that refuses unconditionally."""
        verify_source_run(SourceRun.from_api(run_document()), EXPECTED)

    @pytest.mark.parametrize(
        "overrides,code",
        [
            ({"repository": {"full_name": "attacker/other-repo"}}, "wrong-repository"),
            ({"repository": {}}, "wrong-repository"),
            (
                {"path": ".github/workflows/release.yml", "name": "Release"},
                "wrong-workflow",
            ),
            ({"event": "workflow_dispatch"}, "wrong-event"),
            ({"event": "push"}, "wrong-event"),
            ({"id": 556}, "wrong-run"),
            ({"conclusion": "failure"}, "wrong-conclusion"),
            ({"conclusion": None}, "wrong-conclusion"),
            ({"conclusion": "cancelled"}, "wrong-conclusion"),
        ],
        ids=[
            "wrong-repo",
            "no-repo",
            "wrong-workflow",
            "dispatch-event",
            "push-event",
            "wrong-run-id",
            "failed-run",
            "unfinished-run",
            "cancelled-run",
        ],
    )
    def test_each_mismatch_is_refused_with_its_own_code(
        self, overrides: dict[str, object], code: str
    ) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_source_run(SourceRun.from_api(run_document(**overrides)), EXPECTED)
        assert excinfo.value.code == code

    def test_the_workflow_may_be_pinned_by_name_instead_of_path(self) -> None:
        verify_source_run(
            SourceRun.from_api(run_document()),
            RunExpectation(repository=REPO, workflow="ABI analysis"),
        )

    def test_a_wrong_attempt_is_refused_only_when_one_was_declared(self) -> None:
        run = SourceRun.from_api(run_document(run_attempt=2))
        verify_source_run(run, RunExpectation(repository=REPO))
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_source_run(run, RunExpectation(repository=REPO, run_attempt=1))
        assert excinfo.value.code == "wrong-attempt"

    def test_an_empty_conclusion_allowlist_means_any_conclusion(self) -> None:
        """A publisher that reports analysis *failures* needs the failed run;
        the check is configurable, not merely bypassable."""
        verify_source_run(
            SourceRun.from_api(run_document(conclusion="failure")),
            RunExpectation(repository=REPO, allowed_conclusions=()),
        )

    def test_a_non_object_run_document_is_refused(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            SourceRun.from_api("not a mapping")  # type: ignore[arg-type]
        assert excinfo.value.code == "run-unreadable"


# ---------------------------------------------------------------------------
# 2. Which pull request, and was the analysed commit really its?
# ---------------------------------------------------------------------------


class TestPullRequestResolution:
    def test_positive_control_a_single_association_resolves(self) -> None:
        run = SourceRun.from_api(run_document())
        pull = resolve_pull_request(run, [pull_document()], repository=REPO)
        assert pull.number == 216
        assert pull.from_fork is True

    def test_no_association_is_a_refusal_not_an_empty_answer(self) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(run, [], repository=REPO)
        assert excinfo.value.code == "no-pull-request"

    def test_several_open_associations_are_refused_rather_than_guessed(self) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(
                run,
                [pull_document(), pull_document(number=217)],
                repository=REPO,
            )
        assert excinfo.value.code == "ambiguous-pull-request"

    def test_a_pull_request_against_another_repository_is_not_ours(self) -> None:
        run = SourceRun.from_api(run_document())
        elsewhere = pull_document(
            base={"sha": "0" * 40, "repo": {"full_name": "other-org/other-lib"}}
        )
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(run, [elsewhere], repository=REPO)
        assert excinfo.value.code == "no-pull-request"

    def test_a_closed_association_is_used_only_when_no_open_one_exists(self) -> None:
        run = SourceRun.from_api(run_document())
        closed = pull_document(number=100, state="closed")
        assert resolve_pull_request(run, [closed], repository=REPO).number == 100
        assert (
            resolve_pull_request(run, [closed, pull_document()], repository=REPO).number
            == 216
        )

    def test_an_artifact_supplied_number_that_disagrees_is_refused(self) -> None:
        """The attack this closes: a fork's artifact naming someone else's
        pull request, so its analysis posts there instead."""
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(
                run, [pull_document()], repository=REPO, claimed_number=9999
            )
        assert excinfo.value.code == "pull-request-mismatch"

    def test_an_agreeing_artifact_number_is_accepted(self) -> None:
        run = SourceRun.from_api(run_document())
        pull = resolve_pull_request(
            run, [pull_document()], repository=REPO, claimed_number=216
        )
        assert pull.number == 216


class TestTestedShaAssociation:
    def _pull(self):
        return resolve_pull_request(
            SourceRun.from_api(run_document()), [pull_document()], repository=REPO
        )

    def test_positive_control_the_pr_head_itself_is_accepted(self) -> None:
        run = SourceRun.from_api(run_document())
        assert verify_tested_sha(run, self._pull(), tested_sha=HEAD_SHA) == HEAD_SHA

    def test_a_merge_commit_is_accepted_when_the_head_is_one_of_its_parents(
        self,
    ) -> None:
        """A ``pull_request`` run analyses an ephemeral merge commit, not the
        PR head. Conflating the two misreports which tree was checked, so the
        relationship is verified rather than assumed either way."""
        run = SourceRun.from_api(run_document())
        merge_commit = {
            "sha": MERGE_SHA,
            "parents": [{"sha": "0" * 40}, {"sha": HEAD_SHA}],
        }
        assert (
            verify_tested_sha(
                run, self._pull(), tested_sha=MERGE_SHA, tested_commit=merge_commit
            )
            == MERGE_SHA
        )

    def test_an_unrelated_commit_is_refused_even_with_a_commit_document(self) -> None:
        run = SourceRun.from_api(run_document())
        unrelated = {"sha": "9" * 40, "parents": [{"sha": "8" * 40}]}
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tested_sha(
                run, self._pull(), tested_sha="9" * 40, tested_commit=unrelated
            )
        assert excinfo.value.code == "unassociated-tested-sha"

    def test_a_non_head_sha_without_evidence_is_refused_not_assumed(self) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tested_sha(run, self._pull(), tested_sha=MERGE_SHA)
        assert excinfo.value.code == "unverified-tested-sha"

    def test_a_run_whose_head_is_not_the_pull_requests_head_is_refused(self) -> None:
        run = SourceRun.from_api(run_document(head_sha="7" * 40))
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tested_sha(run, self._pull(), tested_sha="7" * 40)
        assert excinfo.value.code == "head-sha-mismatch"

    def test_no_tested_sha_at_all_is_refused(self) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            verify_tested_sha(run, self._pull(), tested_sha="")
        assert excinfo.value.code == "no-tested-sha"


class TestArtifactSelection:
    def _listing(self, **overrides: object) -> list[dict[str, object]]:
        entry: dict[str, object] = {
            "id": 8080,
            "name": "abi-reports",
            "expired": False,
            "workflow_run": {"id": 555},
        }
        entry.update(overrides)
        return [entry]

    def test_positive_control(self) -> None:
        assert (
            select_artifact(self._listing(), "abi-reports", run_id="555")["id"] == 8080
        )

    def test_an_artifact_from_another_run_is_refused(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            select_artifact(
                self._listing(workflow_run={"id": 999}), "abi-reports", run_id="555"
            )
        assert excinfo.value.code == "artifact-not-found"

    def test_a_missing_name_is_refused(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            select_artifact(self._listing(), "other-name", run_id="555")
        assert excinfo.value.code == "artifact-not-found"

    def test_two_artifacts_with_one_name_are_refused(self) -> None:
        listing = self._listing() + self._listing(id=8081)
        with pytest.raises(SourceRunRejected) as excinfo:
            select_artifact(listing, "abi-reports", run_id="555")
        assert excinfo.value.code == "ambiguous-artifact"

    def test_an_expired_artifact_is_refused_explicitly(self) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            select_artifact(self._listing(expired=True), "abi-reports", run_id="555")
        assert excinfo.value.code == "artifact-expired"


# ---------------------------------------------------------------------------
# 3. Is the archive safe to unpack?
# ---------------------------------------------------------------------------

_UNIX_HOST = 3


def _zip(
    tmp_path: Path,
    entries: list[tuple[str, bytes]],
    *,
    symlinks: tuple[str, ...] = (),
    modes: dict[str, int] | None = None,
    compress: int = zipfile.ZIP_DEFLATED,
) -> Path:
    path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(path, "w", compression=compress) as zf:
        for name, payload in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = compress
            info.create_system = _UNIX_HOST
            mode = 0o120000 if name in symlinks else (modes or {}).get(name, 0o100644)
            info.external_attr = mode << 16
            zf.writestr(info, payload)
    return path


class TestArchivePathRules:
    @pytest.mark.parametrize(
        "name,code",
        [
            ("/etc/passwd", "artifact-absolute-path"),
            ("//etc/passwd", "artifact-absolute-path"),
            ("C:\\Windows\\win.ini", "artifact-absolute-path"),
            ("C:/Windows/win.ini", "artifact-absolute-path"),
            ("../escaped.json", "artifact-traversal"),
            ("a/../../escaped.json", "artifact-traversal"),
            ("./../escaped.json", "artifact-traversal"),
            ("..\\escaped.json", "artifact-traversal"),
            ("", "artifact-bad-path"),
            (".", "artifact-bad-path"),
        ],
    )
    def test_hostile_entry_names_are_refused(
        self, tmp_path: Path, name: str, code: str
    ) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            safe_entry_path(tmp_path, name)
        assert excinfo.value.code == code

    @pytest.mark.parametrize(
        "name", ["report.json", "reports/linux.json", "a/b/c/deep.json", "./ok.json"]
    )
    def test_positive_control_ordinary_names_resolve_inside(
        self, tmp_path: Path, name: str
    ) -> None:
        target = safe_entry_path(tmp_path, name)
        assert target.resolve().is_relative_to(tmp_path.resolve())


class TestHostileArchives:
    def test_positive_control_an_ordinary_artifact_extracts(
        self, tmp_path: Path
    ) -> None:
        archive = _zip(
            tmp_path,
            [("aggregate.json", b'{"ok": true}'), ("reports/linux.json", b"{}")],
        )
        written = extract_artifact(archive, tmp_path / "out")
        assert sorted(p.name for p in written) == ["aggregate.json", "linux.json"]
        assert load_json_document(tmp_path / "out" / "aggregate.json") == {"ok": True}

    def test_a_zip_slip_entry_is_refused_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        sentinel = tmp_path / "sentinel.json"
        archive = _zip(tmp_path, [("../sentinel.json", b'{"owned": true}')])
        out = tmp_path / "out"
        with pytest.raises(SourceRunRejected) as excinfo:
            extract_artifact(archive, out)
        assert excinfo.value.code == "artifact-traversal"
        assert not sentinel.exists(), "the escaping entry was written anyway"

    def test_an_absolute_entry_is_refused(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [("/opt/abicheck-owned.json", b"{}")])
        with pytest.raises(SourceRunRejected) as excinfo:
            extract_artifact(archive, tmp_path / "out")
        assert excinfo.value.code in ("artifact-absolute-path", "artifact-traversal")

    def test_a_symlink_entry_is_refused(self, tmp_path: Path) -> None:
        """``zipfile`` has no notion of symlinks: it writes the target path
        into a plain file, which is only harmless until something follows
        it. The entry type is read from the recorded Unix mode."""
        archive = _zip(
            tmp_path, [("link.json", b"/etc/passwd")], symlinks=("link.json",)
        )
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive)
        assert excinfo.value.code == "artifact-symlink"

    @pytest.mark.parametrize(
        "mode",
        [0o010000, 0o020000, 0o060000, 0o140000],
        ids=["fifo", "chr", "blk", "sock"],
    )
    def test_every_non_regular_entry_type_is_refused(
        self, tmp_path: Path, mode: int
    ) -> None:
        archive = _zip(tmp_path, [("odd", b"x")], modes={"odd": mode})
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive)
        assert excinfo.value.code == "artifact-non-regular"

    def test_an_executable_payload_is_never_left_executable(
        self, tmp_path: Path
    ) -> None:
        """Nothing from an artifact is run, so an executable bit on it has no
        legitimate purpose and is not preserved."""
        archive = _zip(
            tmp_path,
            [("payload.sh", b"#!/bin/sh\nid\n")],
            modes={"payload.sh": 0o100755},
        )
        written = extract_artifact(archive, tmp_path / "out")
        assert written[0].stat().st_mode & 0o111 == 0

    def test_too_many_entries_are_refused(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [(f"f{i}.json", b"{}") for i in range(50)])
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive, ExtractionLimits(max_entries=10))
        assert excinfo.value.code == "artifact-too-many-entries"

    def test_an_oversized_entry_is_refused(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [("big.json", b"x" * 100_000)])
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive, ExtractionLimits(max_entry_bytes=1000))
        assert excinfo.value.code == "artifact-entry-too-large"

    def test_an_oversized_archive_is_refused(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [(f"f{i}.json", b"y" * 20_000) for i in range(10)])
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(
                archive,
                ExtractionLimits(max_entry_bytes=1_000_000, max_total_bytes=50_000),
            )
        assert excinfo.value.code == "artifact-too-large"

    def test_a_decompression_bomb_is_refused(self, tmp_path: Path) -> None:
        """A 10 MiB run of one byte compresses roughly 1000x. The ratio check
        is what catches it while the absolute caps are still satisfied by the
        *declared* compressed size."""
        archive = _zip(tmp_path, [("bomb.json", b"\0" * (10 * 1024 * 1024))])
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive, ExtractionLimits(max_entry_bytes=64 * 1024 * 1024))
        assert excinfo.value.code == "artifact-compression-bomb"

    def test_a_realistic_report_is_not_mistaken_for_a_bomb(
        self, tmp_path: Path
    ) -> None:
        """The positive control for the ratio check: real JSON reports
        compress well, and a threshold that refused them would be a
        threshold nobody could ship."""
        payload = (
            b'{"changes": ['
            + b",".join(
                b'{"kind": "func_removed", "symbol": "entry_%d", "severity": "breaking"}'
                % i
                for i in range(20_000)
            )
            + b"]}"
        )
        archive = _zip(tmp_path, [("report.json", payload)])
        infos = inspect_archive(archive, ExtractionLimits())
        assert infos[0].file_size == len(payload)

    def test_the_caps_are_re_enforced_against_the_bytes_actually_read(
        self, tmp_path: Path
    ) -> None:
        """The declared sizes in a central directory are attacker-supplied.

        `inspect_archive` uses them to refuse an archive up front, but
        `extract_artifact` re-counts what it actually reads, so a header
        understating a member buys nothing. Driven here by *passing the
        up-front check* (limits wide enough for the declaration) and then
        tightening only the streaming budget, which is the same position a
        lying header would put the extractor in.
        """
        archive = _zip(tmp_path, [("big.json", b"z" * 200_000)])
        inspect_archive(archive, ExtractionLimits())  # the declaration is fine
        with pytest.raises(SourceRunRejected) as excinfo:
            extract_artifact(
                archive,
                tmp_path / "out",
                ExtractionLimits(max_entry_bytes=1000, max_total_bytes=10_000_000),
            )
        assert excinfo.value.code == "artifact-entry-too-large"

    def test_a_non_zip_payload_is_refused(self, tmp_path: Path) -> None:
        bogus = tmp_path / "artifact.zip"
        bogus.write_bytes(b"this is not a zip file")
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(bogus)
        assert excinfo.value.code == "artifact-unreadable"

    def test_a_directory_entry_is_created_not_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "artifact.zip"
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("reports/")
            info.create_system = _UNIX_HOST
            info.external_attr = (0o040755 << 16) | 0x10
            zf.writestr(info, b"")
            zf.writestr("reports/x.json", b"{}")
        written = extract_artifact(path, tmp_path / "out")
        assert (tmp_path / "out" / "reports").is_dir()
        assert [p.name for p in written] == ["x.json"]


class TestArtifactContentIsOnlyEverJson:
    def test_a_document_is_parsed_as_json_and_nothing_else(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "doc.json"
        path.write_text('{"a": 1}', encoding="utf-8")
        assert load_json_document(path) == {"a": 1}

    @pytest.mark.parametrize(
        "content",
        [
            "!!python/object/apply:os.system ['id']",
            "\x80\x81 not utf-8",
            "{not json",
        ],
    )
    def test_a_non_json_payload_is_refused_never_interpreted(
        self, tmp_path: Path, content: str
    ) -> None:
        path = tmp_path / "doc.json"
        path.write_bytes(content.encode("utf-8", errors="surrogateescape"))
        with pytest.raises(SourceRunRejected) as excinfo:
            load_json_document(path)
        assert excinfo.value.code == "artifact-unreadable"

    def test_an_oversized_document_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.json"
        path.write_text("{}" + " " * 5000, encoding="utf-8")
        with pytest.raises(SourceRunRejected) as excinfo:
            load_json_document(path, max_bytes=100)
        assert excinfo.value.code == "artifact-entry-too-large"


class TestTheModuleNeverExecutesArtifactContent:
    """An executable statement of the rule the module docstring states.

    Asserting the *text* of a source file would prove nothing about
    behaviour (AGENTS.md: the #705 -> #758 escape was exactly that), so this
    checks the imported module's own bytecode for the names that would make
    an artifact executable: there is no ``pickle``, ``yaml``, ``exec``,
    ``eval``, ``__import__`` or ``subprocess`` reachable from it at all.
    """

    def test_the_module_imports_nothing_that_can_execute_a_payload(self) -> None:
        module = run_selection_module
        forbidden = {"pickle", "yaml", "marshal", "shelve", "subprocess", "os"}
        module_globals = {
            name
            for name, value in vars(module).items()
            if getattr(value, "__name__", "") in forbidden
        }
        assert module_globals == set()

    def test_no_dynamic_execution_builtin_is_referenced(self) -> None:
        module = run_selection_module
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = compile(source, module.__file__, "exec", dont_inherit=True)

        def names(code: object) -> set[str]:
            found = set(getattr(code, "co_names", ()))
            for const in getattr(code, "co_consts", ()):
                if hasattr(const, "co_names"):
                    found |= names(const)
            return found

        assert names(tree) & {"exec", "eval", "compile", "__import__"} == set()


class TestEndToEndSelection:
    """The three questions in sequence, as the Action asks them."""

    def test_a_fork_pull_request_flows_through_every_check(
        self, tmp_path: Path
    ) -> None:
        run = SourceRun.from_api(run_document())
        verify_source_run(run, EXPECTED)
        pull = resolve_pull_request(
            run, [pull_document()], repository=REPO, claimed_number=216
        )
        merge_commit = {
            "sha": MERGE_SHA,
            "parents": [{"sha": "0" * 40}, {"sha": HEAD_SHA}],
        }
        tested = verify_tested_sha(
            run, pull, tested_sha=MERGE_SHA, tested_commit=merge_commit
        )
        artifact = select_artifact(
            [
                {
                    "id": 42,
                    "name": "abi-reports",
                    "expired": False,
                    "workflow_run": {"id": 555},
                }
            ],
            "abi-reports",
            run_id=run.run_id,
        )
        archive = _zip(tmp_path, [("compare.json", b'{"verdict": "BREAKING"}')])
        written = extract_artifact(archive, tmp_path / "out")
        assert pull.number == 216
        assert pull.from_fork
        assert tested == MERGE_SHA
        assert artifact["id"] == 42
        assert load_json_document(written[0])["verdict"] == "BREAKING"


def test_zip_helper_actually_records_the_modes_it_claims(tmp_path: Path) -> None:
    """Vacuity guard on this module's own fixture builder.

    Every symlink/non-regular negative above depends on ``_zip`` really
    writing the Unix mode it was given; a builder that silently dropped it
    would make those tests pass against an implementation with no type check
    at all.
    """
    archive = _zip(tmp_path, [("link", b"target")], symlinks=("link",))
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
    assert info.create_system == _UNIX_HOST
    assert (info.external_attr >> 16) & 0o170000 == 0o120000


def test_deflate_is_actually_used_by_the_fixture_builder(tmp_path: Path) -> None:
    """Companion vacuity guard: a stored (uncompressed) fixture would make
    the compression-bomb test unable to exercise a ratio at all."""
    archive = _zip(tmp_path, [("f.json", b"a" * 10_000)])
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
    assert info.compress_type == zipfile.ZIP_DEFLATED
    assert info.compress_size < info.file_size


# ---------------------------------------------------------------------------
# The Action-only CLI around all three
# ---------------------------------------------------------------------------


class TestVerifyRunCommand:
    """`... cli verify-run` / `... cli extract-artifact`, as the shell calls them.

    The shell hands these files and reads back a result document, so what
    those files say *is* the Action's answer. A refusal in particular must
    reach the caller as a distinct exit code and a machine-readable code,
    not as a stack trace or a zero exit with an empty result.
    """

    @staticmethod
    def _write(tmp_path: Path, name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _invoke(self, tmp_path: Path, *extra: str, **overrides: object):
        run_json = self._write(tmp_path, "run.json", run_document(**overrides))
        pulls_json = self._write(tmp_path, "pulls.json", [pull_document()])
        artifacts_json = self._write(
            tmp_path,
            "artifacts.json",
            {
                "artifacts": [
                    {
                        "id": 42,
                        "name": "abi-reports",
                        "expired": False,
                        "workflow_run": {"id": 555},
                    }
                ]
            },
        )
        out = tmp_path / "result.json"
        result = CliRunner().invoke(
            action_cli,
            [
                "verify-run",
                "--run-json",
                str(run_json),
                "--associated-pulls-json",
                str(pulls_json),
                "--artifacts-json",
                str(artifacts_json),
                "--expect-repository",
                REPO,
                "--expect-workflow",
                ".github/workflows/abi-analysis.yml",
                "--expect-event",
                "pull_request",
                "--expect-run-id",
                "555",
                "--artifact-name",
                "abi-reports",
                "--out",
                str(out),
                *extra,
            ],
        )
        return result, json.loads(out.read_text(encoding="utf-8"))

    def test_a_verified_run_reports_the_api_resolved_pull_request(
        self, tmp_path: Path
    ) -> None:
        result, document = self._invoke(tmp_path)
        assert result.exit_code == 0, result.output
        assert document["verified"] is True
        assert document["pr_number"] == 216
        assert document["tested_sha"] == HEAD_SHA
        assert document["from_fork"] is True
        assert document["artifact_id"] == 42

    def test_a_merge_commit_is_verified_through_the_commit_document(
        self, tmp_path: Path
    ) -> None:
        commit = self._write(
            tmp_path,
            "commit.json",
            {"sha": MERGE_SHA, "parents": [{"sha": "0" * 40}, {"sha": HEAD_SHA}]},
        )
        result, document = self._invoke(
            tmp_path,
            "--tested-sha",
            MERGE_SHA,
            "--tested-commit-json",
            str(commit),
        )
        assert result.exit_code == 0, result.output
        assert document["tested_sha"] == MERGE_SHA
        assert document["pr_head_sha"] == HEAD_SHA

    @pytest.mark.parametrize(
        "overrides,extra,code",
        [
            ({"repository": {"full_name": "attacker/other"}}, (), "wrong-repository"),
            ({"event": "push"}, (), "wrong-event"),
            ({"conclusion": "failure"}, (), "wrong-conclusion"),
            ({}, ("--claimed-pr-number", "9999"), "pull-request-mismatch"),
            ({}, ("--tested-sha", MERGE_SHA), "unverified-tested-sha"),
            ({}, ("--artifact-name", "other"), "artifact-not-found"),
        ],
        ids=[
            "wrong-repo",
            "wrong-event",
            "failed-run",
            "artifact-claims-another-pr",
            "unverified-merge-commit",
            "no-such-artifact",
        ],
    )
    def test_a_refusal_exits_distinctly_and_names_its_own_code(
        self,
        tmp_path: Path,
        overrides: dict[str, object],
        extra: tuple[str, ...],
        code: str,
    ) -> None:
        result, document = self._invoke(tmp_path, *extra, **overrides)
        assert result.exit_code == EXIT_REFUSED
        assert document == {
            "verified": False,
            "code": code,
            "reason": document["reason"],
        }
        assert document["reason"]

    def test_an_allow_any_conclusion_accepts_a_failed_run(self, tmp_path: Path) -> None:
        result, document = self._invoke(
            tmp_path, "--allow-conclusion", "", conclusion="failure"
        )
        assert result.exit_code == 0, result.output
        assert document["verified"] is True

    def test_a_non_list_association_document_is_refused(self, tmp_path: Path) -> None:
        run_json = self._write(tmp_path, "run.json", run_document())
        pulls_json = self._write(tmp_path, "pulls.json", {"not": "a list"})
        out = tmp_path / "result.json"
        result = CliRunner().invoke(
            action_cli,
            [
                "verify-run",
                "--run-json",
                str(run_json),
                "--associated-pulls-json",
                str(pulls_json),
                "--expect-repository",
                REPO,
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert json.loads(out.read_text("utf-8"))["code"] == "no-pull-request"


class TestExtractArtifactCommand:
    def test_an_ordinary_archive_extracts(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [("compare.json", b'{"verdict": "COMPATIBLE"}')])
        result = CliRunner().invoke(
            action_cli, ["extract-artifact", str(archive), str(tmp_path / "out")]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "compare.json").is_file()

    def test_a_hostile_archive_exits_distinctly(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [("../escaped.json", b"{}")])
        result = CliRunner().invoke(
            action_cli, ["extract-artifact", str(archive), str(tmp_path / "out")]
        )
        assert result.exit_code == EXIT_REFUSED
        assert "artifact-traversal" in result.output

    def test_limits_are_honoured_from_the_command_line(self, tmp_path: Path) -> None:
        archive = _zip(tmp_path, [(f"f{i}.json", b"{}") for i in range(20)])
        result = CliRunner().invoke(
            action_cli,
            [
                "extract-artifact",
                str(archive),
                str(tmp_path / "out"),
                "--max-entries",
                "5",
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert "artifact-too-many-entries" in result.output


class TestMalformedApiDocumentsAreSkippedNotFatal:
    """One unexpected record must not cost the whole selection.

    The API's answers reach this code through a shell redirect and a file,
    so a truncated or surprising entry is a real possibility. Each is
    ignored as a *candidate* — never coerced into one, which would resolve a
    pull request from a record that carries none.
    """

    @pytest.mark.parametrize(
        "entry",
        [
            "not a mapping",
            {"no": "number"},
            {"number": "216"},
            {"number": True},
            {"number": None},
        ],
    )
    def test_an_unusable_association_entry_is_not_a_candidate(
        self, entry: object
    ) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(run, [entry], repository=REPO)  # type: ignore[list-item]
        assert excinfo.value.code == "no-pull-request"

    def test_a_usable_entry_beside_unusable_ones_still_resolves(self) -> None:
        run = SourceRun.from_api(run_document())
        pull = resolve_pull_request(
            run,
            ["junk", {"number": None}, pull_document()],  # type: ignore[list-item]
            repository=REPO,
        )
        assert pull.number == 216

    def test_a_pull_request_with_no_head_or_base_objects_is_still_readable(
        self,
    ) -> None:
        run = SourceRun.from_api(run_document())
        with pytest.raises(SourceRunRejected) as excinfo:
            resolve_pull_request(run, [{"number": 1}], repository=REPO)
        # Its base repository is unknown, so it is not a candidate for this
        # repository -- refused rather than assumed to belong here.
        assert excinfo.value.code == "no-pull-request"


class TestArchiveEdgeCases:
    def test_a_missing_archive_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(tmp_path / "nowhere.zip")
        assert excinfo.value.code == "artifact-unreadable"

    def test_an_entry_named_only_dot_segments_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            safe_entry_path(tmp_path, "./")
        assert excinfo.value.code == "artifact-bad-path"

    def test_an_archive_from_a_non_unix_host_is_not_assumed_hostile(
        self, tmp_path: Path
    ) -> None:
        """A Windows-produced zip records no Unix mode at all, so there is
        nothing to type-check -- and nothing that could carry a symlink
        either. Refusing it wholesale would break a real, ordinary producer."""
        path = tmp_path / "artifact.zip"
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("report.json")
            info.create_system = 0  # FAT / Windows
            zf.writestr(info, b"{}")
        assert [i.filename for i in inspect_archive(path)] == ["report.json"]

    def test_a_whole_archive_bomb_is_refused_even_when_each_entry_passes(
        self, tmp_path: Path
    ) -> None:
        """The per-entry ratio check alone is evadable, and this is how.

        Each of these entries compresses to well under the small-entry floor
        the ratio check exempts -- individually meaningless, and individually
        *unmeasurable*, since a ratio over a few hundred bytes says nothing.
        Together they expand 40 MB from 40 KB. Only the archive-wide ratio
        sees it, which is why both are checked, under the **default** limits
        rather than a configuration invented to make the assertion pass.
        """
        entries = [(f"f{i}.json", b"\0" * 200_000) for i in range(200)]
        archive = _zip(tmp_path, entries)
        # The premise: every entry is individually exempt or within ratio.
        with zipfile.ZipFile(archive) as zf:
            assert all(i.compress_size < 1024 for i in zf.infolist())
        with pytest.raises(SourceRunRejected) as excinfo:
            inspect_archive(archive, ExtractionLimits())
        assert excinfo.value.code == "artifact-compression-bomb"
        assert "overall" in excinfo.value.message

    def test_the_total_budget_is_enforced_while_reading_too(
        self, tmp_path: Path
    ) -> None:
        archive = _zip(tmp_path, [(f"f{i}.json", b"z" * 50_000) for i in range(5)])
        inspect_archive(archive, ExtractionLimits())
        with pytest.raises(SourceRunRejected) as excinfo:
            extract_artifact(
                archive,
                tmp_path / "out",
                ExtractionLimits(max_entry_bytes=10_000_000, max_total_bytes=60_000),
            )
        assert excinfo.value.code == "artifact-too-large"

    def test_a_missing_document_is_refused_by_the_json_reader(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceRunRejected) as excinfo:
            load_json_document(tmp_path / "nowhere.json")
        assert excinfo.value.code == "artifact-unreadable"


#: Sentinel for "the entry has no ``workflow_run`` key at all", which is a
#: different input from one carrying an empty mapping.
_ABSENT_OWNER = object()


class TestArtifactOwnershipFailsClosed:
    """An entry that states no owner is refused like one stating the wrong
    owner.

    Review finding: the guard read ``if owner_id and owner_id != run_id``,
    so an entry carrying no ``workflow_run.id`` at all passed. That is the
    case an attacker controls, and it defeats the re-establishment this
    function's own docstring promises: the listing endpoint is chosen by
    the shell, so "the entry did not say which run it belongs to" can never
    be read as "the entry belongs to the run we asked about".
    """

    @staticmethod
    def _entry(name: str, owner: object) -> dict[str, object]:
        entry: dict[str, object] = {"id": 7, "name": name, "expired": False}
        if owner is not _ABSENT_OWNER:
            entry["workflow_run"] = owner
        return entry

    @pytest.mark.parametrize(
        "owner",
        [
            _ABSENT_OWNER,
            {},
            {"id": ""},
            {"id": None},
            {"other": "field"},
            "not-a-mapping",
            [],
        ],
        ids=[
            "no-key",
            "empty-mapping",
            "empty-id",
            "null-id",
            "no-id-field",
            "scalar",
            "list",
        ],
    )
    def test_an_unstated_owner_is_refused(self, owner: object) -> None:
        with pytest.raises(SourceRunRejected) as caught:
            select_artifact([self._entry("reports", owner)], "reports", run_id="99")
        assert caught.value.code == "artifact-not-found"

    def test_a_wrong_owner_is_still_refused(self) -> None:
        with pytest.raises(SourceRunRejected) as caught:
            select_artifact(
                [self._entry("reports", {"id": 12345})], "reports", run_id="99"
            )
        assert caught.value.code == "artifact-not-found"

    @pytest.mark.parametrize("run_id", ["99", 99])
    def test_the_matching_owner_is_accepted_whichever_type_it_is(
        self, run_id: object
    ) -> None:
        """Positive control, and the reason the comparison stringifies both
        sides: the API states the id as an integer, the shell passes it as
        text, and a guard that fails closed on a type mismatch would refuse
        every artifact rather than the wrong one."""
        chosen = select_artifact(
            [self._entry("reports", {"id": 99})], "reports", run_id=str(run_id)
        )
        assert chosen["name"] == "reports"

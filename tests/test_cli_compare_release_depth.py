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

"""``compare --depth`` against a directory/package (release) operand.

**The bug class this file exists for** (registry:
``cli_surface.capability_guard_diverged_from_pipeline``): a front-end guard
that hard-codes *which* rungs a downstream pipeline can reach, instead of
letting that pipeline answer from the evidence actually resolved. Such a
guard is a snapshot of a belief, and it goes stale silently — nothing fails
when the pipeline gains the capability the guard still denies.

That is exactly what happened here. ``--depth`` was first rejected wholesale
for a set input, then narrowed (D1) to a per-rung allow-list: ``binary``
accepted, ``headers`` rejected for "the per-library fan-out does not enforce
a per-library evidence floor", ``build``/``source`` rejected for needing
inline ``--sources``/``--build-info``. Both surviving rejections were false
by the time they were written down:

* the fan-out routes every member pair through ``service.run_compare``, so
  the depth contract is enforced per member there — the enforcement the
  ``headers`` message said had no home;
* a member may itself be a pre-dumped snapshot carrying embedded L3/L4/L5
  evidence, which satisfies ``build``/``source`` with no inline collection
  at all.

So the tests below deliberately do **not** assert "``--depth headers`` is
now accepted" (the one reported input). They enumerate the *whole* public
ladder against members of *every* evidence level.

**The invariant, and why it is stated as parity.** A first version of this
file asserted each member's outcome against a hand-written ladder, and
passed — while the fan-out was flattening a three-way contract (exit 0 for
a satisfied or ``headers`` rung, 7 for an unmet ``build``/``source`` one, 0
again for a stored-snapshot side the pin cannot apply to) onto a single
``ERROR``/4 for every case. A Codex review round caught that; a per-member
oracle could not, because it never looked at what the *other* surface
answers. So the primary invariant here is now **packaging an operand does
not change the answer**: a one-member directory exits exactly what a
single-pair ``compare`` of that same member exits, at every rung. Its
oracle is the other public surface, which is what makes it survive the
contract itself being re-decided.

The hand-written ladder is kept for the complementary half only — that a
rung a member *does* reach still produces a real comparison — since parity
alone would also be satisfied by both paths failing identically. It is
deliberately not ``evidence_depth.DEPTH_RANK``, the table the
implementation ranks with.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_evidence import BuildEvidence, Target, TargetKind
from abicheck.buildsource.model import BuildSourceManifest
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

#: The public ``--depth`` ladder, weakest first — restated here by hand on
#: purpose. Importing ``abicheck.evidence_depth.DEPTH_RANK`` would make the
#: oracle the very table the code under test ranks with, so a reordering bug
#: would agree with itself and pass (AGENTS.md: "a stated oracle that is not
#: the same formula/helper the implementation itself uses").
LADDER = ("binary", "headers", "build", "source")

#: Fixture evidence levels, by construction rather than by measurement:
#: what each ``_snap`` shape below genuinely carries.
EVIDENCE_LEVELS = ("binary", "headers", "build")


# ── fixtures ───────────────────────────────────────────────────────────────


def _snap(
    evidence: str, version: str = "1.0", library: str = "libfoo.so"
) -> AbiSnapshot:
    """A snapshot carrying exactly *evidence*'s level and nothing above it."""
    snap = AbiSnapshot(
        library=library,
        version=version,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=evidence in ("headers", "build"),
    )
    if evidence == "build":
        snap.build_source = BuildSourcePack(
            root=Path("."),
            manifest=BuildSourceManifest(),
            build_evidence=BuildEvidence(
                targets=[Target(id="t1", name="foo", kind=TargetKind.SHARED_LIBRARY)]
            ),
        )
    return snap


def _release_dirs(tmp_path: Path, evidence: str) -> tuple[Path, Path]:
    old_dir = tmp_path / f"old-{evidence}"
    new_dir = tmp_path / f"new-{evidence}"
    for side in (old_dir, new_dir):
        side.mkdir()
        (side / "libfoo.json").write_text(
            snapshot_to_json(_snap(evidence)), encoding="utf-8"
        )
    return old_dir, new_dir


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _release_json(out: str) -> dict:
    """The release JSON document out of a mixed stdout/stderr capture.

    ``CliRunner`` interleaves both streams, and warnings/notices can land on
    either side of the document, so this decodes just the first complete
    JSON value rather than assuming everything from the first ``{`` onward
    is the document (which broke the moment this file started asserting on
    a run that also emits a stderr notice).
    """
    match = re.search(r"^\{", out, re.M)
    assert match is not None, f"no JSON document in output: {out[:400]}"
    document, _ = json.JSONDecoder().raw_decode(out[match.start() :])
    assert isinstance(document, dict), document
    return document


# ── the invariant ──────────────────────────────────────────────────────────


class TestEveryDepthRungReachesTheFanOut:
    """The whole ladder × every member evidence level (12 cases).

    This is the generalized statement of the class, not a reproducer for the
    ``--depth headers`` report: it fails for *any* rung a future front-end
    guard starts pre-judging, in either direction.
    """

    @pytest.mark.parametrize("requested", LADDER)
    @pytest.mark.parametrize("evidence", EVIDENCE_LEVELS)
    def test_no_rung_is_ever_rejected_up_front(
        self, tmp_path: Path, requested: str, evidence: str
    ) -> None:
        """No ``--depth`` value is a *usage* error on a set input (exit 64).

        Whether the evidence is there is a question about the members, and
        the members are not read until dispatch — so a pre-dispatch usage
        error can only ever be a guess.
        """
        old_dir, new_dir = _release_dirs(tmp_path, evidence)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert code != 64, out
        assert "not supported for directory/package" not in out

    @pytest.mark.parametrize("requested", LADDER)
    @pytest.mark.parametrize("evidence", EVIDENCE_LEVELS)
    def test_exit_code_equals_the_single_pair_compare_of_the_same_member(
        self, tmp_path: Path, requested: str, evidence: str
    ) -> None:
        """**The invariant this file exists for.** Packaging an operand must
        not change the answer: a one-member directory must exit exactly what
        a single-pair ``compare`` of that same member exits, at every rung.

        The oracle is the other public surface, not a table of expected
        codes -- so this keeps holding if the depth contract itself is ever
        re-decided, and fails the moment the two paths disagree in either
        direction. It is the check that would have caught the flattening
        Codex's P1 review found: at the point that review landed, this same
        matrix read scalar 0/7/7/0 against fan-out 4/4/4/4.
        """
        old_dir, new_dir = _release_dirs(tmp_path, evidence)
        member = "libfoo.json"
        scalar_code, _ = _invoke(
            "compare",
            str(old_dir / member),
            str(new_dir / member),
            "--depth",
            requested,
            "--format",
            "json",
        )
        release_code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert release_code == scalar_code, out

    @pytest.mark.parametrize("requested", LADDER)
    @pytest.mark.parametrize("evidence", EVIDENCE_LEVELS)
    def test_a_reachable_rung_still_compares_the_member(
        self, tmp_path: Path, requested: str, evidence: str
    ) -> None:
        """Parity alone would be satisfied by both paths failing identically.

        So state the other half too: a rung the member's own evidence *does*
        reach must produce a real comparison, not a skipped or errored one.
        The oracle is ``LADDER``'s hand-written order against the fixture's
        constructed evidence level -- deliberately not
        ``evidence_depth.DEPTH_RANK``, the table the implementation ranks
        with.
        """
        old_dir, new_dir = _release_dirs(tmp_path, evidence)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        if LADDER.index(evidence) >= LADDER.index(requested):
            data = _release_json(out)
            [lib] = data["libraries"]
            assert lib["verdict"] == "NO_CHANGE", lib
            assert code == 0, out

    @pytest.mark.parametrize("requested", LADDER)
    def test_every_rung_is_forwarded_verbatim_to_every_pair(
        self, tmp_path: Path, requested: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the value reaches ``service.run_compare``, not merely that
        the CLI stopped rejecting it — a guard could equally have been
        "fixed" by accepting the flag and dropping it on the floor, which is
        the silent-no-op failure the sibling ``--sources`` guard exists to
        prevent."""
        import abicheck.service as service

        old_dir, new_dir = _release_dirs(tmp_path, "build")
        captured: list[object] = []
        real_run_compare = service.run_compare

        def _capturing(*args: object, **kwargs: object) -> object:
            captured.append(kwargs.get("depth"))
            return real_run_compare(*args, **kwargs)

        monkeypatch.setattr(service, "run_compare", _capturing)
        _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert captured == [requested]


@pytest.fixture
def live_release_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """One real ``.so`` member per side, built with no headers supplied.

    The stored-snapshot fixtures above cannot reach the exit-7 axis at all:
    ``policy.depth_evidence_contract`` carves out a side this run never
    extracted, so a shortfall on a pre-dumped member is correctly a no-op.
    The rows where the two paths actually disagreed before this fix were the
    *live* ones, so they need a real artifact.
    """
    from shutil import which

    if which("gcc") is None:
        pytest.skip("gcc is required to build the live release fixture")
    src = tmp_path / "foo.c"
    src.write_text("int foo(void){return 1;}\n", encoding="utf-8")
    old_dir, new_dir = tmp_path / "live-old", tmp_path / "live-new"
    for side in (old_dir, new_dir):
        side.mkdir()
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(side / "libfoo.so"), str(src)],
            check=True,
            capture_output=True,
        )
    return old_dir, new_dir


@pytest.mark.integration
class TestLiveMemberShortfallMatchesTheScalarPath:
    """The rows the flattening actually broke, on a real artifact.

    With no headers supplied a live member reaches only ``binary`` evidence,
    so ``headers`` is satisfied (symbol extraction alone backs that rung)
    while ``build``/``source`` are not. Before this fix a single-pair
    ``compare`` answered 0/7/7 here and the identical directory answered
    4/4/4.
    """

    @pytest.mark.parametrize("requested", LADDER)
    def test_exit_code_equals_the_single_pair_compare_of_the_same_member(
        self, live_release_dirs: tuple[Path, Path], requested: str
    ) -> None:
        old_dir, new_dir = live_release_dirs
        scalar_code, _ = _invoke(
            "compare",
            str(old_dir / "libfoo.so"),
            str(new_dir / "libfoo.so"),
            "--depth",
            requested,
            "--format",
            "json",
        )
        release_code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert release_code == scalar_code, out


@pytest.mark.integration
class TestDepthShortfallIsExplainedNotSilent:
    """A release that exits 7 must say why, and say something followable.

    The per-member ``DiffResult`` is discarded before the note a single-pair
    ``compare`` renders from ``record_depth_evidence_contract_error`` would
    be produced, so the fan-out has to emit its own -- otherwise the exit
    code is correct and completely unexplained. The scalar note also points
    at ``--build-info old=/new=`` on ``compare``, which a directory operand
    rejects outright, so the release note names the alternatives that do
    work here.
    """

    @pytest.mark.parametrize("requested", ("build", "source"))
    def test_the_release_names_the_short_member_and_the_contribution(
        self, live_release_dirs: tuple[Path, Path], requested: str
    ) -> None:
        old_dir, new_dir = live_release_dirs
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert code == 7, out
        assert "Requested --depth evidence was not reached in: libfoo.so" in out
        assert "Contributes 7" in out

    @pytest.mark.parametrize("requested", ("build", "source"))
    def test_it_names_only_remediation_that_works_on_this_operand(
        self, live_release_dirs: tuple[Path, Path], requested: str
    ) -> None:
        """``cli_surface.retired_spelling_in_remediation``'s rule applied to
        this notice: advice a user follows must not itself be a usage error
        on the command being advised."""
        old_dir, new_dir = live_release_dirs
        _, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        assert "dump --sources" in out
        assert "compare the library individually" in out
        # The flags the scalar note would have suggested are rejected here,
        # so the notice must not offer them as a `compare` option.
        assert "pass --build-info old=" not in out


@pytest.mark.integration
class TestTheReportAgreesWithTheProcessExit:
    """A release's rendered decision must equal the code it exits with.

    ``resolve_release_exit_decision_for_report``'s own docstring states this
    as an invariant ("`.code` is nonetheless *provably* always equal to what
    ``_exit_compare_release`` sys.exits with"), and a first version of this
    axis broke it: the exit was taken *after* the summary had rendered, so a
    one-member directory exited 7 while its own JSON reported
    ``exit.code: 0``, ``reasons: ["clean"]`` and a zero contribution. A
    report-driven CI consumer -- which never sees the process status -- read
    that run as clean (Codex review, P1 on `2c1af1f`).

    So this asserts agreement across *every* surface that publishes a
    decision, not just the one field that was wrong, and does it by
    comparing the two rather than pinning 7 in three places.
    """

    def test_stdout_json_exit_block_matches_the_process_exit(
        self, live_release_dirs: tuple[Path, Path]
    ) -> None:
        old_dir, new_dir = live_release_dirs
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "build",
            "--format",
            "json",
        )
        data = _release_json(out)
        assert data["exit"]["code"] == code, data["exit"]
        assert "evidence_contract_error" in data["exit"]["reasons"], data["exit"]
        assert data["exit"]["evidence_contract_error_contribution"] == code

    def test_output_dir_sidecar_matches_the_process_exit(
        self, live_release_dirs: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The sidecar builds its own ``exit`` block from its own call, so
        it can drift independently of the stdout one -- and did."""
        old_dir, new_dir = live_release_dirs
        out_dir = tmp_path / "reports"
        code, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            "build",
            "--format",
            "json",
            "--output-dir",
            str(out_dir),
        )
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["exit"]["code"] == code, summary["exit"]

    def test_a_clean_release_still_reports_clean(
        self, live_release_dirs: tuple[Path, Path]
    ) -> None:
        """The complementary half: the fix must not stamp the axis on runs
        that never pinned a rung."""
        old_dir, new_dir = live_release_dirs
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        data = _release_json(out)
        assert code == 0, out
        assert data["exit"]["code"] == 0
        assert data["exit"]["evidence_contract_error_contribution"] == 0


class TestSetInputEvidenceFlagsStillRejected:
    """Regression guard for the *other* direction: the flags that genuinely
    have no per-library home must keep being rejected. Removing the rung
    allow-list must not weaken the input guard next to it."""

    @pytest.mark.parametrize(
        ("flag", "value"),
        (("--sources", "new={src}"), ("--build-info", "new={src}")),
    )
    def test_inline_evidence_flags_are_still_usage_errors(
        self, tmp_path: Path, flag: str, value: str
    ) -> None:
        old_dir, new_dir = _release_dirs(tmp_path, "headers")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        code, out = _invoke(
            "compare", str(old_dir), str(new_dir), flag, value.format(src=src_dir)
        )
        assert code == 64
        assert flag in out
        assert "does not collect inline build/source evidence" in out

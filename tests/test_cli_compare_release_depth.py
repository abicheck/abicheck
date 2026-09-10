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

* the fan-out routes every member pair through ``service.run_compare``, and
  ``service_compare_pipeline.resolve_compare_request`` calls
  ``workflows.artifact.execute.enforce_requested_depth`` for every pair it
  resolves — the floor enforcement the ``headers`` message said had no home;
* a member may itself be a pre-dumped snapshot carrying embedded L3/L4/L5
  evidence, which satisfies ``build``/``source`` with no inline collection
  at all.

So the tests below deliberately do **not** assert "``--depth headers`` is
now accepted" (the one reported input). They enumerate the *whole* public
ladder against members of *every* evidence level and state the invariant the
guard violated: **no rung is rejected up front; each member's own resolved
evidence decides its own outcome.** The oracle is a hand-written ladder in
this module, compared against fixtures whose evidence level is known by
construction — never ``evidence_depth.DEPTH_RANK``, which is the same table
the implementation consults.
"""

from __future__ import annotations

import json
import re
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
    match = re.search(r"^\{", out, re.M)
    assert match is not None, f"no JSON document in output: {out[:400]}"
    return json.loads(out[match.start() :])


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
    def test_member_outcome_follows_that_member_s_own_evidence(
        self, tmp_path: Path, requested: str, evidence: str
    ) -> None:
        """A member fails iff *it* falls short of the requested rung.

        The oracle is ``LADDER``'s hand-written order against the fixture's
        constructed evidence level — not the rank table the implementation
        uses, and not the front end's own allow-list.
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
        data = _release_json(out)
        [lib] = data["libraries"]
        short = LADDER.index(evidence) < LADDER.index(requested)
        if short:
            assert lib["verdict"] == "ERROR", lib
            assert "evidence depth" in str(lib["error"])
            assert data["run_outcome"]["operational"] == "extraction_error"
        else:
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


class TestDepthShortfallGuidanceSurvivesTheGuardRemoval:
    """The removed usage errors carried advice the floor message cannot.

    ``enforce_requested_depth`` tells the user to supply ``--sources``; on a
    set input that flag is itself rejected. Dropping a guard must not drop
    the guidance it carried, so the release-shaped alternatives are appended
    to the per-member failure.
    """

    @pytest.mark.parametrize("requested", ("build", "source"))
    def test_release_shaped_alternatives_are_named(
        self, tmp_path: Path, requested: str
    ) -> None:
        old_dir, new_dir = _release_dirs(tmp_path, "headers")
        _, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--depth",
            requested,
            "--format",
            "json",
        )
        error = str(_release_json(out)["libraries"][0]["error"])
        assert "dump --sources/--build-info" in error
        assert "compare this library individually" in error

    def test_hint_is_scoped_to_depth_pins(self, tmp_path: Path) -> None:
        """A run with no ``--depth`` must not acquire the depth hint — the
        handler that appends it also catches unrelated ``ValidationError``s.
        """
        old_dir, new_dir = _release_dirs(tmp_path, "binary")
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, out
        assert "dump --sources/--build-info" not in out


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

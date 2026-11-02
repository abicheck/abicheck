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

"""A bundle's scope policy (``bundles.<id>.required``/``on_incomplete``)
reaches ``compare`` with the meaning it was declared with.

Bug class ``config.propagation_completeness``: an accepted configuration
value either reaches every relevant consumer with identical semantics, or is
rejected at the public boundary. The chain is ``.abicheck.yml`` ->
``BundleSpec`` -> the run plan's bundle cell (schema v4) -> ``check-target``'s
``bundle-required``/``bundle-on-incomplete`` -> ``--select-required``/
``--select``/``--set scope.on_incomplete`` -> the release scope gate.

The middle hop changes key space: a bundle names members by project target
id, ``--select-required`` matches canonical release keys of files. The
oracle for the end-to-end tests is the policy as the docs state it, computed
here from the declared sets alone: the check fails exactly when a required
member is missing on the candidate side and ``on_incomplete`` is ``fail``.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_bundles import BundleSpec
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.project_targets_validation import validate_project_targets
from abicheck.buildsource.run_plan import RunPlan, generate_run_plan
from abicheck.cli import main
from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.cli_base import EXIT_REFUSED
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.aggregate import AggregateError
from abicheck.workflows.bundle_scope_selection import (
    BundleSelectionError,
    bundle_selection,
)

_MEMBERS = ("liba", "libb", "libc")

#: The staging step runs under the runner's POSIX bash; on a Windows test
#: host plain `bash -c` cannot run it (same skip as the sibling staging
#: tests in tests/test_reusable_workflows.py).
_POSIX_BASH = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX bash for the staging step"
)
#: A bundle check is ELF-only (run-plan generation refuses a non-Linux
#: bundle profile), and the fixture links with GNU ld's -soname.
_ELF_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="bundle checks are ELF-only"
)
_ON_INCOMPLETE = ("", "fail", "warn")


def _subsets(items: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [
        combo
        for n in range(len(items) + 1)
        for combo in itertools.combinations(items, n)
    ]


def _raw(required: tuple[str, ...] | None, on_incomplete: str) -> dict:
    bundle: dict = {
        "targets": list(_MEMBERS),
        "checks": [{"channel": "release", "depth": "binary"}],
    }
    if required is not None:
        bundle["required"] = list(required)
    if on_incomplete:
        bundle["on_incomplete"] = on_incomplete
    return {
        "targets": {
            m: {"kind": "library", "binary_pattern": f"lib/{m}.so*", "bundle": "rel"}
            for m in _MEMBERS
        },
        "bundles": {"rel": bundle},
        "profiles": {"linux": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "x-*"}
            }
        },
    }


# Every declarable policy: no `required:` key, or a non-empty subset of the
# members; crossed with every on_incomplete spelling.
_POLICIES = [
    (req, oi)
    for req in [None, *[s for s in _subsets(_MEMBERS) if s]]
    for oi in _ON_INCOMPLETE
]


class TestBundleSpecParsing:
    @pytest.mark.parametrize(("required", "on_incomplete"), _POLICIES)
    def test_every_declarable_policy_round_trips(self, required, on_incomplete):
        cfg = ProjectTargetsConfig.from_dict(_raw(required, on_incomplete))
        assert validate_project_targets(cfg).errors == []
        spec = cfg.bundles["rel"]
        assert spec.required == list(required or [])
        assert spec.on_incomplete == on_incomplete
        # Oracle: "omitted means every member is required".
        expected_required = list(required) if required else list(_MEMBERS)
        assert spec.required_members == expected_required
        assert spec.declares_scope_policy == bool(required or on_incomplete)
        again = BundleSpec.from_dict("rel", spec.to_dict())
        assert again == spec

    @pytest.mark.parametrize(
        ("patch", "message"),
        [
            ({"required": []}, "non-empty list"),
            ({"required": "liba"}, "non-empty list"),
            ({"required": ["liba", "liba"]}, "more than once"),
            ({"required": ["libz"]}, "not in this bundle's targets"),
            ({"required": [1]}, "list of strings"),
            ({"on_incomplete": "block"}, "must be one of"),
            ({"on_incomplete": True}, "must be one of"),
            ({"on_incomplete": ""}, "must be one of"),
            ({"on_incomplete": ["fail"]}, "must be one of"),
            ({"on_incomplete": {"fail": 1}}, "must be one of"),
        ],
    )
    def test_malformed_policy_is_refused(self, patch, message):
        raw = {"targets": list(_MEMBERS), **patch}
        with pytest.raises(ValueError, match=message):
            BundleSpec.from_dict("rel", raw)


class TestRunPlanCarriesThePolicy:
    @pytest.mark.parametrize(("required", "on_incomplete"), _POLICIES)
    def test_bundle_cell_and_schema(self, required, on_incomplete):
        cfg = ProjectTargetsConfig.from_dict(_raw(required, on_incomplete))
        bo = BuildOutput(
            targets=[BuildOutputTarget(id=m, binary=f"lib/{m}.so") for m in _MEMBERS]
        )
        plan, report = generate_run_plan(cfg, {"linux": bo})
        assert report.ok
        [cell] = plan.checks
        declared = bool(required or on_incomplete)
        expected = (list(required) if required else list(_MEMBERS)) if declared else []
        assert cell.bundle_required == expected
        assert cell.bundle_on_incomplete == on_incomplete
        doc = plan.to_dict()
        assert doc["schema"] == (
            "abicheck.run-plan/v4" if declared else "abicheck.run-plan/v1"
        )
        assert ("bundle_required" in doc["checks"][0]) is declared
        back = RunPlan.from_dict(json.loads(json.dumps(doc)))
        assert back.checks[0].bundle_required == expected
        assert back.checks[0].bundle_on_incomplete == on_incomplete

    @pytest.mark.parametrize("schema", ["abicheck.run-plan/v1", "abicheck.run-plan/v2"])
    @pytest.mark.parametrize(
        "field", [("bundle_required", ["liba"]), ("bundle_on_incomplete", "fail")]
    )
    def test_pre_v4_plan_carrying_a_policy_is_refused(self, schema, field):
        """An older reader would drop the keys and run the bundle with no
        selection, so a plan claiming an older schema must not carry them."""
        key, value = field
        doc = {
            "schema": schema,
            "checks": [{"kind": "bundle", "name": "rel", key: value}],
        }
        with pytest.raises(AggregateError, match="run-plan/v4"):
            RunPlan.from_dict(doc)

    @pytest.mark.parametrize(
        ("required", "on_incomplete"),
        [(["liba"], ""), ([], "fail"), (["liba"], "warn")],
    )
    def test_hand_built_plan_round_trips_through_its_own_reader(
        self, required, on_incomplete
    ):
        """Whatever subset of the two fields a bundle cell carries, the
        writer stamps the schema its own reader accepts."""
        from abicheck.buildsource.run_plan import RunPlanCheck

        cell = RunPlanCheck(
            kind="bundle",
            name="rel",
            bundle_members=["liba", "libb"],
            bundle_required=required,
            bundle_on_incomplete=on_incomplete,
        )
        doc = RunPlan(checks=[cell]).to_dict()
        assert doc["schema"] == "abicheck.run-plan/v4"
        back = RunPlan.from_dict(json.loads(json.dumps(doc))).checks[0]
        assert (back.bundle_required, back.bundle_on_incomplete) == (
            required,
            on_incomplete,
        )

    def test_target_cell_never_selects_v4(self):
        from abicheck.buildsource.run_plan import RunPlanCheck

        cell = RunPlanCheck(kind="target", name="liba", bundle_on_incomplete="fail")
        assert RunPlan(checks=[cell]).to_dict()["schema"] == "abicheck.run-plan/v1"

    @pytest.mark.parametrize(
        "fields",
        [
            {"bundle_required": ["liba", 1]},
            {"bundle_required": [1]},
            {"bundle_required": "liba"},
            {"bundle_required": [""]},
            {"bundle_on_incomplete": "block"},
            {"bundle_on_incomplete": ["fail"]},
        ],
    )
    def test_malformed_policy_fields_are_refused_not_filtered(self, fields):
        doc = {
            "schema": "abicheck.run-plan/v4",
            "checks": [{"kind": "bundle", "name": "rel", **fields}],
        }
        with pytest.raises(AggregateError, match="bundle scope policy"):
            RunPlan.from_dict(doc)

    def test_v5_plan_is_refused_as_newer_than_supported(self):
        with pytest.raises(AggregateError, match="newer than this tool"):
            RunPlan.from_dict({"schema": "abicheck.run-plan/v5", "checks": []})


# Baseline file names for the three members, deliberately not equal to the
# target ids and in three different shapes. The expected canonical keys are
# written out by hand -- the oracle does not call the key function.
_BASELINE_FILES = {
    "liba": "/b/liba.so.1.2.3",
    "libb": "/b/libB-core.so",
    # The shape actions/baseline publishes: binaries/<target id>.
    "libc": "/b/binaries/libc",
}
_EXPECTED_KEY = {"liba": "liba.so", "libb": "libB-core.so", "libc": "libc"}


class TestTargetIdsBecomeReleaseKeys:
    @pytest.mark.parametrize(("required", "on_incomplete"), _POLICIES)
    def test_selection_partitions_every_member(self, required, on_incomplete):
        sel = bundle_selection(_MEMBERS, required or (), _BASELINE_FILES, on_incomplete)
        wanted = set(required) if required else set(_MEMBERS)
        assert set(sel.required_keys) == {_EXPECTED_KEY[m] for m in wanted}
        assert set(sel.optional_keys) == {
            _EXPECTED_KEY[m] for m in _MEMBERS if m not in wanted
        }
        # Every member is selected exactly once: none is dropped out of scope.
        assert sorted(sel.required_keys + sel.optional_keys) == sorted(
            _EXPECTED_KEY.values()
        )
        assert (
            sel.set_line()
            == {
                "": "",
                "fail": "scope.on_incomplete=block",
                "warn": "scope.on_incomplete=warn",
            }[on_incomplete]
        )

    @pytest.mark.parametrize(
        ("members", "required", "paths", "message"),
        [
            (("liba", "libb"), (), {"liba": "/b/liba.so"}, "no baseline binary"),
            (("liba",), ("libz",), {"liba": "/b/liba.so"}, "not bundle members"),
            (
                ("liba", "libb"),
                (),
                {"liba": "/b/libx.so.1", "libb": "/c/libx.so.2"},
                "share the release key",
            ),
            (("liba",), (), {"liba": "/b/lib a.so"}, "one selection argument"),
        ],
    )
    def test_unstatable_policy_is_refused(self, members, required, paths, message):
        with pytest.raises(BundleSelectionError, match=message):
            bundle_selection(members, required, paths)

    def test_action_command_prints_github_output_lines(self):
        result = CliRunner().invoke(
            action_cli,
            [
                "bundle-selection",
                "--members",
                json.dumps(list(_MEMBERS)),
                "--required",
                '["libc"]',
                "--binary-paths",
                json.dumps(_BASELINE_FILES),
                "--on-incomplete",
                "fail",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [
            "args=--select-required libc --select liba.so --select libB-core.so",
            "set=scope.on_incomplete=block",
        ]

    def test_action_command_refuses_with_its_boundary_exit(self):
        result = CliRunner().invoke(
            action_cli,
            [
                "bundle-selection",
                "--members",
                '["liba", "libb"]',
                "--binary-paths",
                '{"liba": "/b/liba.so"}',
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert "no baseline binary" in result.output


def _snap(library: str) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


class TestPolicyGatesTheRealCompare:
    """The translated arguments, through the real `compare` directory route.

    Every (policy, missing member) combination: the candidate directory lacks
    one member (or none). Oracle: the check exits 1 exactly when the missing
    member is required and the policy is ``fail``; otherwise 0.
    """

    @pytest.mark.parametrize("missing", [None, *_MEMBERS])
    @pytest.mark.parametrize(
        ("required", "on_incomplete"),
        [(r, oi) for r, oi in _POLICIES if oi],  # a stated on_incomplete
    )
    def test_exit_follows_the_declared_policy(
        self, tmp_path: Path, missing, required, on_incomplete
    ):
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        paths = {}
        for member in _MEMBERS:
            snap = snapshot_to_json(_snap(f"{member}.so"))
            (old_dir / f"{member}.json").write_text(snap, encoding="utf-8")
            paths[member] = str(old_dir / f"{member}.json")
            if member != missing:
                (new_dir / f"{member}.json").write_text(snap, encoding="utf-8")
        sel = bundle_selection(_MEMBERS, required or (), paths, on_incomplete)
        args = ["compare", str(old_dir), str(new_dir), *sel.compare_args()]
        args += ["--set", sel.set_line(), "-o", "json=-"]
        result = CliRunner().invoke(main, args)

        wanted = set(required) if required else set(_MEMBERS)
        incomplete = missing is not None and missing in wanted
        expected = 1 if incomplete and on_incomplete == "fail" else 0
        assert result.exit_code == expected, result.output
        doc = json.loads(result.stdout)
        states = {m["member"]: m for m in doc["comparison_scope"]["members"]}
        # Nothing was silently dropped out of scope by the selection.
        assert all(m["state"] != "out_of_scope" for m in states.values())
        assert {k for k, m in states.items() if m["required"]} == {
            f"{m}.json" for m in wanted
        }


_REPO = Path(__file__).resolve().parent.parent


def _steps(path: str) -> dict[str, dict]:
    import _yaml_fast

    doc = _yaml_fast.safe_load((_REPO / path).read_text(encoding="utf-8"))
    return {step.get("id", step.get("name")): step for step in doc["runs"]["steps"]}


class TestActionForwardsThePolicy:
    """The hops between the run plan and `compare` that live in YAML."""

    def test_check_project_forwards_both_run_plan_fields(self):
        import _yaml_fast

        doc = _yaml_fast.safe_load(
            (_REPO / ".github/workflows/check-project.yml").read_text(encoding="utf-8")
        )
        steps = [
            s
            for job in doc["jobs"].values()
            for s in job.get("steps", [])
            if s.get("id") == "run"
        ]
        [run] = steps
        assert "matrix.bundle_required" in run["with"]["bundle-required"]
        assert "matrix.bundle_on_incomplete" in run["with"]["bundle-on-incomplete"]

    def test_check_target_translates_then_forwards(self):
        steps = _steps("actions/check-target/action.yml")
        selection = steps["bundle_selection"]
        assert "bundle-selection" in selection["run"]
        assert "steps.resolve.outputs.binary-paths" in str(selection["env"])
        analysis = steps["analysis"]
        # A failed translation stops the analysis rather than running the
        # bundle without its policy ...
        assert "steps.bundle_selection.outcome != 'failure'" in analysis["if"]
        # ... and a successful one reaches compare through both channels.
        assert "steps.bundle_selection.outputs.args" in analysis["with"]["extra-args"]
        root_set = steps["root_set_main"]
        assert root_set["env"]["ROOT_BUNDLE_SCOPE_SET"] == (
            "${{ steps.bundle_selection.outputs.set }}"
        )
        assert "ROOT_BUNDLE_SCOPE_SET" in root_set["run"]
        finalize = steps["finalize"]
        assert "BUNDLE_SELECTION_OUTCOME" in finalize["env"]
        run_sh = (_REPO / "actions/check-target/run.sh").read_text(encoding="utf-8")
        assert '"$BUNDLE_SELECTION_OUTCOME" == "failure"' in run_sh


def _staging_script() -> str:
    import _yaml_fast

    doc = _yaml_fast.safe_load(
        (_REPO / ".github/workflows/check-project.yml").read_text(encoding="utf-8")
    )
    steps = doc["jobs"]["check"]["steps"]
    return next(
        s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
    )["run"]


@pytest.mark.integration
@_ELF_ONLY
class TestStagedBundleMembersPairWithTheirBaseline:
    """gcc-built members: actions/baseline's `binaries/<target id>` layout
    against check-project's real staging step, through a real `compare`.

    Before the fix the candidate was staged under its build basename
    (`liba.so.1`), which never pairs with `binaries/liba`: every member read
    one-sided and nothing was compared. Oracle: every present member is
    compared, and the exit follows the declared policy."""

    @pytest.mark.parametrize("missing", [None, "libb", "libc"])
    def test_members_pair_and_the_policy_gates(self, tmp_path, missing):
        import os
        import shutil
        import subprocess

        from _workflow_exec import bash_executable, require_bash

        if shutil.which("gcc") is None:
            pytest.skip("gcc not available")
        require_bash()
        baseline = tmp_path / "baseline-binaries"
        candidate = tmp_path / "candidate" / "lib"
        baseline.mkdir()
        candidate.mkdir(parents=True)
        for m in _MEMBERS:
            src = tmp_path / f"{m}.c"
            src.write_text(f"int {m}_fn(void) {{ return 1; }}\n", encoding="utf-8")
            built = tmp_path / f"{m}.so.1"
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    f"-Wl,-soname,{m}.so.1",
                    "-o",
                    str(built),
                    str(src),
                ],
                check=True,
            )
            shutil.copy(built, baseline / m)  # actions/baseline: binaries/<id>
            if m != missing:
                shutil.copy(built, candidate / f"{m}.so.1")
        matrix = {
            "kind": "bundle",
            "name": "rel",
            "member_binary_patterns": {m: f"lib/{m}.so*" for m in _MEMBERS},
            "bundle_required": ["liba", "libb"],
        }
        gh_out = tmp_path / "gh_out"
        gh_out.write_text("")
        proc = subprocess.run(
            [bash_executable(), "-c", _staging_script()],
            cwd=tmp_path,
            env={
                **os.environ,
                "MATRIX_JSON": json.dumps(matrix),
                "GITHUB_OUTPUT": str(gh_out),
            },
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        staging = tmp_path / "bundle-staging"
        sel = bundle_selection(
            _MEMBERS,
            ("liba", "libb"),
            {m: str(baseline / m) for m in _MEMBERS},
            "fail",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(baseline),
                str(staging),
                *sel.compare_args(),
                "--set",
                sel.set_line(),
                "-o",
                "json=-",
            ],
        )
        doc = json.loads(result.stdout)
        compared = sorted(lib["library"] for lib in doc["libraries"])
        assert compared == sorted(m for m in _MEMBERS if m != missing)
        expected = 1 if missing in ("liba", "libb") else 0
        assert result.exit_code == expected, result.output


@_POSIX_BASH
class TestHostileMemberIdIsNotAPath:
    """A member id becomes a staged file name, so an id that is a path must be
    refused before anything is written -- executed, not text-asserted."""

    @pytest.mark.parametrize("member", ["../escape", "a/b", ".hidden", "..", "x\ny"])
    def test_staging_refuses_and_writes_nothing_outside(self, tmp_path, member):
        import os
        import subprocess

        from _workflow_exec import bash_executable, require_bash

        require_bash()
        (tmp_path / "candidate").mkdir()
        (tmp_path / "candidate" / "libx.so").write_bytes(b"x")
        sentinel = tmp_path / "escape"
        gh_out = tmp_path / "gh_out"
        gh_out.write_text("")
        matrix = {
            "kind": "bundle",
            "name": "rel",
            "member_binary_patterns": {member: "libx.so"},
        }
        proc = subprocess.run(
            [bash_executable(), "-c", _staging_script()],
            cwd=tmp_path,
            env={
                **os.environ,
                "MATRIX_JSON": json.dumps(matrix),
                "GITHUB_OUTPUT": str(gh_out),
            },
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "cannot be a staged file name" in proc.stderr
        staging = tmp_path / "bundle-staging"
        assert not sentinel.exists()
        assert list(staging.iterdir()) == []
        assert "new-library=" not in gh_out.read_text()


def _stage(tmp_path: Path, matrix: dict):
    import os
    import subprocess

    from _workflow_exec import bash_executable, require_bash

    require_bash()
    gh_out = tmp_path / "gh_out"
    gh_out.write_text("")
    return subprocess.run(
        [bash_executable(), "-c", _staging_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(matrix),
            "GITHUB_OUTPUT": str(gh_out),
        },
        capture_output=True,
        text=True,
    )


@_POSIX_BASH
class TestStagingNamesMembersByTargetId:
    """check-project.yml stages each member under its target id (the name
    actions/baseline gives it in binaries/). Replaces the former
    basename-collision test: two members whose files share a basename no
    longer overwrite each other; two members resolving to one file are still
    refused."""

    def test_members_sharing_a_basename_stage_under_their_own_ids(self, tmp_path):
        candidate = tmp_path / "candidate"
        (candidate / "linux").mkdir(parents=True)
        (candidate / "plugins").mkdir(parents=True)
        (candidate / "linux" / "libfoo.so").write_bytes(b"core")
        (candidate / "plugins" / "libfoo.so").write_bytes(b"plugin")
        proc = _stage(
            tmp_path,
            {
                "kind": "bundle",
                "name": "mybundle",
                "member_binary_patterns": {
                    "core": "linux/libfoo.so",
                    "plugin": "plugins/libfoo.so",
                },
            },
        )
        assert proc.returncode == 0, proc.stderr
        staging = tmp_path / "bundle-staging"
        assert sorted(p.name for p in staging.iterdir()) == ["core", "plugin"]
        assert (staging / "core").read_bytes() == b"core"
        assert (staging / "plugin").read_bytes() == b"plugin"

    def test_two_members_resolving_to_one_file_are_refused(self, tmp_path):
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "libfoo.so").write_bytes(b"core")
        proc = _stage(
            tmp_path,
            {
                "kind": "bundle",
                "name": "mybundle",
                "member_binary_patterns": {"core": "libfoo.so", "alias": "libfoo*.so"},
            },
        )
        assert proc.returncode != 0
        assert "'core'" in proc.stderr and "'alias'" in proc.stderr

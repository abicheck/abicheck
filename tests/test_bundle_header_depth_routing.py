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

"""A ``depth: headers`` bundle check compares each member's own historical
header evidence with its own candidate header evidence.

Bug class ``evidence.depth_projection_diverges_from_shallow_dump`` is the
nearest home, but the defect here is a routing one: ``check-target`` took a
bundle's OLD side from the baseline's binary directory, which carries no
header evidence, and parsed both sides against one project-wide header set.
A header-only change (an inline function removed) between baseline and
candidate was therefore invisible, so the depth was refused outright.

Now the OLD side is ``resolve-baseline``'s per-member snapshots and the NEW
side is one dump per member against that member's own ``public_headers:``.
The oracle for the end-to-end test is the source edit itself: exactly the
member whose header changed reports a break, and the old routing (binaries
plus one header set) is shown to miss the same edit.
"""

from __future__ import annotations

import itertools
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.project_targets_validation import validate_project_targets
from abicheck.buildsource.run_plan import RunPlan, generate_run_plan
from abicheck.cli import main
from abicheck.workflows.bundle_candidate_snapshots import (
    CandidatePlanError,
    plan_candidate_dumps,
)

_MEMBERS = ("libalpha", "libbeta", "libgamma")


def _raw(depth: str, headerless: set[str]) -> dict:
    return {
        "targets": {
            m: {
                "kind": "library",
                "binary_pattern": f"lib/{m}.so*",
                "bundle": "rel",
                **({} if m in headerless else {"public_headers": [f"include/{m}.h"]}),
            }
            for m in _MEMBERS
        },
        "bundles": {
            "rel": {
                "targets": list(_MEMBERS),
                "checks": [{"channel": "release", "depth": depth}],
            }
        },
        "profiles": {"linux": {"contract": True}},
        "baseline": {
            "channels": {
                "release": {"source": "github-release", "asset_pattern": "x-*"}
            }
        },
    }


_HEADERLESS = [
    set(c)
    for n in range(len(_MEMBERS) + 1)
    for c in itertools.combinations(_MEMBERS, n)
]


class TestHeaderDepthBundleConfig:
    @pytest.mark.parametrize(
        "headerless", _HEADERLESS, ids=lambda s: ",".join(sorted(s)) or "none"
    )
    @pytest.mark.parametrize("depth", ["binary", "headers"])
    def test_headers_needs_every_member_to_declare_headers(self, depth, headerless):
        cfg = ProjectTargetsConfig.from_dict(_raw(depth, headerless))
        errors = validate_project_targets(cfg).errors
        # Oracle: headers depth is valid exactly when no member lacks headers;
        # binary depth never needs them.
        expected_error = depth == "headers" and bool(headerless)
        assert bool(errors) is expected_error, errors
        if expected_error:
            assert all(m in " ".join(errors) for m in headerless)

    def test_bundle_cell_carries_each_members_own_headers(self):
        cfg = ProjectTargetsConfig.from_dict(_raw("headers", set()))
        bo = BuildOutput(
            targets=[BuildOutputTarget(id=m, binary=f"lib/{m}.so") for m in _MEMBERS]
        )
        plan, report = generate_run_plan(cfg, {"linux": bo})
        assert report.ok
        [cell] = plan.checks
        assert cell.requested_depth == "headers"
        assert {m: v.splitlines() for m, v in cell.member_headers.items()} == {
            m: [f"include/{m}.h"] for m in _MEMBERS
        }
        back = RunPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert back.checks[0].member_headers == cell.member_headers

    @pytest.mark.parametrize("value", [["x"], {"liba": ["a.h"]}, {"liba": 1}, "a.h"])
    def test_malformed_member_headers_is_refused_not_filtered(self, value):
        from abicheck.workflows.aggregate import AggregateError

        doc = {"checks": [{"kind": "bundle", "name": "rel", "member_headers": value}]}
        with pytest.raises(AggregateError, match="member_headers"):
            RunPlan.from_dict(doc)


def _planner_case(tmp_path: Path, seed: int):
    """A generated bundle: baseline binaries/snapshots and a candidate dir.

    Each member gets a random SONAME version on each side and a random
    compression on its baseline snapshot; a random subset of members is absent
    from the candidate. The expected pairing is recorded as the files are
    made, so the oracle never calls the key function."""
    rng = random.Random(seed)
    members = [f"lib{chr(97 + i)}{seed}" for i in range(rng.randint(1, 5))]
    cand = tmp_path / "cand"
    cand.mkdir()
    binaries, snapshots, headers, expected, missing = {}, {}, {}, {}, []
    for m in members:
        # actions/baseline's layout (binaries/<target id>) paired with
        # check-project's id-named staging, or versioned file names on both.
        by_id = rng.random() < 0.5
        binaries[m] = (
            f"/base/binaries/{m}"
            if by_id
            else f"/base/binaries/{m}.so.{rng.randint(1, 9)}"
        )
        suffix = rng.choice(["", ".gz", ".zst"])
        snapshots[m] = f"/stage/member-snapshots/{m}.abicheck.json{suffix}"
        headers[m] = "\n".join(f"include/{m}/h{i}.h" for i in range(rng.randint(1, 3)))
        if rng.random() < 0.3:
            missing.append(m)
            continue
        cfile = cand / (
            m if by_id else f"{m}.so.{rng.randint(1, 9)}.{rng.randint(0, 9)}"
        )
        cfile.write_bytes(b"\x7fELF")
        expected[m] = (cfile, f"{m}.abicheck.json", tuple(headers[m].splitlines()))
    return members, headers, binaries, snapshots, cand, expected, missing


class TestCandidatePlan:
    @pytest.mark.parametrize("seed", range(12))
    def test_each_member_pairs_with_its_own_candidate(self, tmp_path, seed):
        members, headers, binaries, snapshots, cand, expected, missing = _planner_case(
            tmp_path, seed
        )
        plan = plan_candidate_dumps(members, headers, binaries, snapshots, cand)
        got = {d.member: (d.candidate, d.output_name, d.headers) for d in plan.dumps}
        assert got == expected
        assert list(plan.missing) == missing

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda h, b, s, c: h.pop("liba"), "declares no public_headers"),
            (lambda h, b, s, c: s.pop("liba"), "staged baseline snapshot"),
            (lambda h, b, s, c: b.pop("liba"), "baseline binary"),
            (
                lambda h, b, s, c: (c / "liba.so.9").write_bytes(b""),
                "share the release key",
            ),
            (
                lambda h, b, s, c: b.update(libb="/base/binaries/liba.so.3"),
                "share the release key",
            ),
        ],
    )
    def test_unsafe_plan_is_refused(self, tmp_path, mutate, message):
        cand = tmp_path / "cand"
        cand.mkdir()
        (cand / "liba.so.2").write_bytes(b"")
        (cand / "libb.so.2").write_bytes(b"")
        headers = {"liba": "a.h", "libb": "b.h"}
        binaries = {"liba": "/b/liba.so.1", "libb": "/b/libb.so.1"}
        snapshots = {"liba": "/s/liba.abicheck.json", "libb": "/s/libb.abicheck.json"}
        mutate(headers, binaries, snapshots, cand)
        with pytest.raises(CandidatePlanError, match=message):
            plan_candidate_dumps(["liba", "libb"], headers, binaries, snapshots, cand)


_REPO = Path(__file__).resolve().parent.parent


def _steps() -> dict[str, dict]:
    import _yaml_fast

    doc = _yaml_fast.safe_load(
        (_REPO / "actions/check-target/action.yml").read_text(encoding="utf-8")
    )
    return {s.get("id", s.get("name")): s for s in doc["runs"]["steps"]}


class TestCheckTargetRouting:
    def test_header_depth_bundle_compares_snapshot_directories(self):
        steps = _steps()
        resolve = steps["resolve"]["with"]
        assert "inputs.depth == 'headers'" in resolve["stage-member-snapshots"]
        analysis = steps["analysis"]
        assert (
            "steps.resolve.outputs.member-snapshots-dir"
            in analysis["with"]["old-library"]
        )
        assert (
            "steps.bundle_candidate.outputs.snapshots-dir"
            in analysis["with"]["new-library"]
        )
        assert "steps.bundle_candidate.outcome != 'failure'" in analysis["if"]
        # Selection keys come from the operands compare pairs.
        assert "member-snapshot-paths" in str(steps["bundle_selection"]["env"])

    def test_validate_inputs_requires_member_headers_at_headers_depth(self, tmp_path):
        from _check_target_exec import _BASE_IDENTITY, VALIDATE_SH, _run

        base = {
            **_BASE_IDENTITY,
            "INPUT_KIND": "bundle",
            "INPUT_BUNDLE_MEMBERS": '["liba"]',
            "INPUT_BASELINE_PATH": "b",
        }
        for value, ok in (("", False), ("{}", False), ('{"liba": "a.h"}', True)):
            proc = _run(VALIDATE_SH, {**base, "INPUT_MEMBER_HEADERS": value}, tmp_path)
            assert (proc.returncode == 0) is ok, proc.stderr
        proc = _run(
            VALIDATE_SH,
            {
                **_BASE_IDENTITY,
                "INPUT_KIND": "target",
                "INPUT_MEMBER_HEADERS": '{"x": "a.h"}',
                "INPUT_BASELINE_PATH": "b",
            },
            tmp_path,
        )
        assert proc.returncode != 0 and "member-headers" in proc.stdout


_A_V1 = "struct S { int x; };\nstatic inline int a_inline(void) { return 1; }\nint a_fn(struct S *s);\n"
_A_V2 = "struct S { int x; };\nint a_fn(struct S *s);\n"
_B_H = "int b_fn(void);\n"


def _gcc_lib(tmp: Path, name: str, body: str, header_dir: Path) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(body, encoding="utf-8")
    out = tmp / f"{name}.so.1"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", f"-I{header_dir}", "-o", str(out), str(src)],
        check=True,
    )
    return out


def _dump(lib: Path, header: Path, out: Path) -> None:
    result = CliRunner().invoke(
        main, ["dump", str(lib), "-H", str(header), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="bundle checks are ELF-only"
)
class TestRealHeaderDepthBundle:
    """gcc-built members, the real `bundle_candidate` step, a real compare."""

    @pytest.fixture
    def bundle(self, tmp_path):
        if shutil.which("gcc") is None:
            pytest.skip("gcc not available")
        from _workflow_exec import require_bash

        require_bash()
        v1, v2 = tmp_path / "v1", tmp_path / "v2"
        for d, a in ((v1, _A_V1), (v2, _A_V2)):
            d.mkdir()
            (d / "a.h").write_text(a, encoding="utf-8")
            (d / "b.h").write_text(_B_H, encoding="utf-8")
        build = tmp_path / "build"
        build.mkdir()
        liba = _gcc_lib(
            build, "liba", '#include "a.h"\nint a_fn(struct S *s){return s->x;}\n', v1
        )
        libb = _gcc_lib(build, "libb", "int b_fn(void){return 2;}\n", v1)
        # Baseline: binaries plus one snapshot per member, each dumped with its
        # own v1 header (what actions/baseline publishes).
        binaries, stage = tmp_path / "binaries", tmp_path / "member-snapshots"
        binaries.mkdir()
        stage.mkdir()
        for name, lib, h in (("liba", liba, "a.h"), ("libb", libb, "b.h")):
            shutil.copy(lib, binaries / name)
            _dump(lib, v1 / h, stage / f"{name}.abicheck.json")
        # Candidate: the same binaries, staged under their target ids as
        # check-project.yml does; the headers moved to v2.
        cand = tmp_path / "bundle-staging"
        cand.mkdir()
        shutil.copy(liba, cand / "liba")
        shutil.copy(libb, cand / "libb")
        return {
            "tmp": tmp_path,
            "v2": v2,
            "binaries": binaries,
            "stage": stage,
            "cand": cand,
            "binary_paths": {
                "liba": str(binaries / "liba"),
                "libb": str(binaries / "libb"),
            },
            "snapshot_paths": {
                "liba": str(stage / "liba.abicheck.json"),
                "libb": str(stage / "libb.abicheck.json"),
            },
        }

    def _run_candidate_step(self, b, evidence: str):
        from _workflow_exec import make_workspace, run_step

        ws = make_workspace(b["tmp"] / f"ws-{evidence}")
        env = {
            "BUNDLE_MEMBERS": '["liba", "libb"]',
            "MEMBER_HEADERS": json.dumps(
                {"liba": str(b["v2"] / "a.h"), "libb": str(b["v2"] / "b.h")}
            ),
            "BINARY_PATHS": json.dumps(b["binary_paths"]),
            "SNAPSHOT_PATHS": json.dumps(b["snapshot_paths"]),
            "HEADER_EVIDENCE": evidence,
            "CANDIDATE_DIR": str(b["cand"]),
            "INCLUDE_DIRS": "",
            "BUILD_CONFIG": "",
        }
        return run_step(_steps()["bundle_candidate"], workspace=ws, env=env)

    def test_header_only_change_is_reported_for_its_member_only(self, bundle):
        result = self._run_candidate_step(bundle, "complete")
        assert result.returncode == 0, result.stderr
        out_dir = Path(result.outputs["snapshots-dir"])
        assert sorted(p.name for p in out_dir.iterdir()) == [
            "liba.abicheck.json",
            "libb.abicheck.json",
        ]
        args = ["compare", str(bundle["stage"]), str(out_dir), "--depth", "headers"]
        run = CliRunner().invoke(main, [*args, "-o", "json=-"])
        doc = json.loads(run.stdout)
        verdicts = {lib["library"]: lib["verdict"] for lib in doc["libraries"]}
        # Oracle: the source edit removed an inline function from a.h only.
        assert verdicts["libb.abicheck.json"] == "NO_CHANGE"
        assert verdicts["liba.abicheck.json"] != "NO_CHANGE"
        assert run.exit_code in (2, 4)

        # The old routing -- binaries on the OLD side, one header set for both
        # sides -- misses the same edit: the reason the depth was refused.
        old_route = CliRunner().invoke(
            main,
            [
                "compare",
                str(bundle["binaries"]),
                str(bundle["cand"]),
                "--depth",
                "headers",
                "-H",
                str(bundle["v2"] / "a.h"),
                "-H",
                str(bundle["v2"] / "b.h"),
                "-o",
                "json=-",
            ],
        )
        assert old_route.exit_code == 0, old_route.output
        # ... while really comparing both members (not a vacuous exit 0).
        old_doc = json.loads(old_route.stdout)
        assert sorted(lib["library"] for lib in old_doc["libraries"]) == [
            "liba",
            "libb",
        ]
        assert all(lib["verdict"] == "NO_CHANGE" for lib in old_doc["libraries"])

    @pytest.mark.parametrize("evidence", ["partial", "none"])
    def test_baseline_without_header_evidence_is_refused(self, bundle, evidence):
        result = self._run_candidate_step(bundle, evidence)
        assert result.returncode != 0
        assert "snapshots-dir" not in result.outputs
        assert "header evidence" in result.stderr


class TestCandidateStepTreatsInputsAsData:
    """`member-headers` comes from the project's `.abicheck.yml`; each entry
    reaches `abicheck dump` as one argv element and is never evaluated by the
    shell -- executed, not text-asserted."""

    @pytest.mark.parametrize(
        "header",
        ["$(touch {s})", "`touch {s}`", "a.h; touch {s}", "a.h\n$(touch {s})"],
    )
    def test_shell_metacharacters_in_headers_run_nothing(self, tmp_path, header):
        from _workflow_exec import make_workspace, require_bash, run_step

        require_bash()
        sentinel = tmp_path / "PWNED"
        cand = tmp_path / "bundle-staging"
        cand.mkdir()
        (cand / "liba").write_bytes(b"not an elf")
        stage = tmp_path / "member-snapshots"
        stage.mkdir()
        ws = make_workspace(tmp_path / "ws")
        env = {
            "BUNDLE_MEMBERS": '["liba"]',
            "MEMBER_HEADERS": json.dumps({"liba": header.format(s=sentinel)}),
            "BINARY_PATHS": json.dumps({"liba": str(tmp_path / "binaries" / "liba")}),
            "SNAPSHOT_PATHS": json.dumps({"liba": str(stage / "liba.abicheck.json")}),
            "HEADER_EVIDENCE": "complete",
            "CANDIDATE_DIR": str(cand),
            "INCLUDE_DIRS": "$(touch " + str(sentinel) + ")",
            "BUILD_CONFIG": "",
        }
        result = run_step(_steps()["bundle_candidate"], workspace=ws, env=env)
        # The dump fails (no such header, not an ELF); nothing was executed.
        assert result.returncode != 0
        assert not sentinel.exists()
        assert "snapshots-dir" not in result.outputs

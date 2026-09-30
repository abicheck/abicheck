# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""G41 Phase 1: a profile's baseline is dumped under the compile context its
candidate cells use.

Invariant, over an exhaustive small domain of profile overlays: the baseline
context resolved by ``baseline_extraction_context`` equals the context the
candidate side actually applies, read off the real run-plan cell
(``generate_run_plan`` -> ``RunPlanCheck``) as check-project.yml consumes it:
the consumer overlay's fields when ``consumer_compile_active``, else the
producer compile overlay's. The oracle is the run-plan output, not the
resolver helpers the implementation calls.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from abicheck.buildsource.baseline_extraction_context import (
    CONTEXT_CONSUMER,
    CONTEXT_DEFAULT,
    CONTEXT_PRODUCER,
    BaselineExtractionContext,
    compile_overlay,
    main as main_entry,
    prepare_baseline_build_config,
    resolve_baseline_extraction_context,
    write_baseline_build_config,
)
from abicheck.buildsource.build_config import load_build_config
from abicheck.buildsource.build_output import BuildOutput, BuildOutputTarget
from abicheck.buildsource.build_output_profile import BuildOutputProfile
from abicheck.buildsource.project_targets import ProjectTargetsConfig
from abicheck.buildsource.run_plan import generate_run_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
BINDINGS = {"gcc14": "/opt/gcc-14/bin/g++", "clang20": "/opt/clang-20/bin/clang++"}

# Overlay shapes: None (absent), {} (declared but empty), and populated ones.
_OVERLAYS: list[dict[str, Any] | None] = [
    None,
    {},
    {"binding": "gcc14"},
    {"standard": "c++17"},
    {"frontend": "castxml"},
    {
        "binding": "clang20",
        "standard": "c++20",
        "stdlib": "libc++",
        "frontend": "clang",
    },
    {"abi_macros": {"_GLIBCXX_USE_CXX11_ABI": "0"}, "args": ["-fno-rtti"]},
]


def _config(compile_: dict | None, consumer: dict | None) -> ProjectTargetsConfig:
    profile: dict[str, Any] = {"contract": True}
    if compile_ is not None:
        profile["compile"] = compile_
    if consumer is not None:
        profile["consumer_compile"] = consumer
    return ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "build/libfoo*.so",
                    "checks": [{"channel": "accepted-main", "depth": "headers"}],
                }
            },
            "profiles": {"p": profile},
            "baseline": {
                "channels": {
                    "accepted-main": {
                        "source": "github-release",
                        "asset_pattern": "x-*",
                    }
                }
            },
        }
    )


def _candidate_context(cfg: ProjectTargetsConfig) -> tuple[str, str, str]:
    bo = BuildOutput(
        profile=BuildOutputProfile(id="p"),
        targets=[BuildOutputTarget(id="libfoo", binary="a/libfoo.so")],
    )
    plan, report = generate_run_plan(cfg, {"p": bo}, resolved_bindings=BINDINGS)
    assert not report.errors, report.errors
    (cell,) = plan.checks
    if cell.consumer_compile_active:
        return (
            cell.consumer_compile_gcc_path,
            cell.consumer_compile_gcc_options,
            cell.consumer_compile_ast_frontend,
        )
    return cell.compile_gcc_path, cell.compile_gcc_options, cell.compile_ast_frontend


def test_baseline_context_matches_candidate_cell_over_every_overlay_pair():
    sources = set()
    mismatches = []
    for compile_, consumer in itertools.product(_OVERLAYS, repeat=2):
        cfg = _config(compile_, consumer)
        ctx = resolve_baseline_extraction_context(cfg, "p", BINDINGS)
        sources.add(ctx.source)
        got = (ctx.gcc_path, ctx.gcc_options, ctx.ast_frontend)
        want = _candidate_context(cfg)
        if got != want:
            mismatches.append((compile_, consumer, got, want))
    assert sources == {CONTEXT_CONSUMER, CONTEXT_PRODUCER, CONTEXT_DEFAULT}
    assert not mismatches, mismatches[:5]


def test_unknown_profile_is_default_context():
    ctx = resolve_baseline_extraction_context(
        _config({"standard": "c++17"}, None), "nope"
    )
    assert ctx.source == CONTEXT_DEFAULT and ctx.is_default


def test_default_context_leaves_the_build_config_untouched(tmp_path):
    base = tmp_path / "cfg.yml"
    base.write_text("severity:\n  preset: default\n")
    ctx = BaselineExtractionContext("p", CONTEXT_DEFAULT)
    assert write_baseline_build_config(ctx, base, tmp_path / "out.json") == base
    assert write_baseline_build_config(ctx, None, tmp_path / "out.json") is None
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize(
    "ctx",
    [
        BaselineExtractionContext("p", CONTEXT_PRODUCER, "/opt/gcc/bin/g++", "", ""),
        BaselineExtractionContext(
            "p", CONTEXT_CONSUMER, "", "-std=c++20 -DX=1", "clang"
        ),
        BaselineExtractionContext(
            "p", CONTEXT_CONSUMER, "/c++", "-DA -DB=2", "castxml"
        ),
    ],
)
def test_overlay_wins_and_other_keys_survive(tmp_path, ctx):
    proj = tmp_path / "proj"
    (proj / "inc").mkdir(parents=True)
    base = proj / ".abicheck.yml"
    base.write_text(
        yaml.safe_dump(
            {
                "compile": {
                    "compiler": "/usr/bin/old",
                    "options": ["-DOLD"],
                    "include_dirs": ["inc"],
                },
                "severity": {"preset": "strict"},
            }
        )
    )
    out = tmp_path / "tmp" / "baseline.json"
    written = write_baseline_build_config(ctx, base, out)
    assert written == out
    doc = json.loads(out.read_text())
    assert doc["severity"] == {"preset": "strict"}
    for key, value in compile_overlay(ctx).items():
        assert doc["compile"][key] == value
    if not ctx.gcc_path:
        assert doc["compile"]["compiler"] == "/usr/bin/old"
    # Relocation keeps relative include dirs pointing at the same place.
    assert [Path(p) for p in doc["compile"]["include_dirs"]] == [
        (proj / "inc").resolve()
    ]
    # The written document is a config the real loader accepts.
    loaded = load_build_config(out)
    assert loaded is not None


def test_prepare_reads_project_config_and_bindings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "gcc": {"compile": {"binding": "gcc14"}},
                    "clang-client": {
                        "consumer_compile": {"binding": "clang20", "standard": "c++20"}
                    },
                    "plain": {},
                }
            }
        )
    )
    bindings = tmp_path / "bindings.yml"
    bindings.write_text(
        yaml.safe_dump(
            {"schema": "abicheck.toolchain-bindings/v1", "bindings": BINDINGS}
        )
    )
    out = tmp_path / "o.json"
    ctx, path = prepare_baseline_build_config(
        project_config=cfg,
        profile_id="clang-client",
        bindings_path=bindings,
        build_config=None,
        out=out,
    )
    assert ctx.source == CONTEXT_CONSUMER and ctx.gcc_path == BINDINGS["clang20"]
    assert path == out and json.loads(out.read_text())["compile"]["options"] == [
        "-std=c++20"
    ]
    ctx, path = prepare_baseline_build_config(
        project_config=cfg,
        profile_id="plain",
        bindings_path=bindings,
        build_config=None,
        out=out,
    )
    assert ctx.source == CONTEXT_DEFAULT and path is None


def test_unresolvable_binding_fails_closed(tmp_path):
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        yaml.safe_dump({"profiles": {"gcc": {"compile": {"binding": "missing"}}}})
    )
    bindings = tmp_path / "b.yml"
    bindings.write_text(
        yaml.safe_dump({"schema": "abicheck.toolchain-bindings/v1", "bindings": {}})
    )
    with pytest.raises(ValueError, match="missing"):
        prepare_baseline_build_config(
            project_config=cfg,
            profile_id="gcc",
            bindings_path=bindings,
            build_config=None,
            out=tmp_path / "o",
        )


# ── the workflow step: wiring, and the module entry point it runs ─────────

_WORKFLOWS = {
    "publish-baseline.yml": "publish",
    "update-main-baseline.yml": None,
}


def _steps(workflow: str) -> list[dict[str, Any]]:
    wf = yaml.safe_load(
        (REPO_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    )
    jobs = [
        j
        for j in wf["jobs"].values()
        if any(s.get("name") == "Dump baseline-set" for s in j.get("steps", []))
    ]
    assert len(jobs) == 1, workflow
    return jobs[0]["steps"]


def _named(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(s for s in steps if s.get("name") == name)


@pytest.mark.parametrize("workflow", sorted(_WORKFLOWS))
def test_both_baseline_workflows_resolve_then_dump_under_the_context(workflow):
    steps = _steps(workflow)
    names = [s.get("name") for s in steps]
    assert names.index("Resolve baseline extraction context") < names.index(
        "Dump baseline-set"
    )
    resolve = _named(steps, "Resolve baseline extraction context")
    assert resolve["id"] == "context"
    assert (
        resolve["run"].strip()
        == "python3 -I -m abicheck.buildsource.baseline_extraction_context"
    )
    assert resolve["env"] == {
        "PROFILE_ID": "${{ matrix.profile_id }}",
        "PROJECT_CONFIG": "${{ inputs.project-config }}",
        "BUILD_CONFIG": "${{ inputs.build-config }}",
        "BINDINGS_PATH": "${{ inputs.toolchain-bindings-path }}",
    }
    dump = _named(steps, "Dump baseline-set")["with"]
    assert dump["build-config"] == "${{ steps.context.outputs.build-config }}"
    assert (
        dump["extraction-context"] == "${{ steps.context.outputs.extraction-context }}"
    )
    wf = yaml.safe_load(
        (REPO_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    )
    inputs = (
        wf[True]["workflow_call"]["inputs"]
        if True in wf
        else wf["on"]["workflow_call"]["inputs"]
    )
    assert {"project-config", "toolchain-bindings-path"} <= set(inputs)


def _run_main(tmp_path, monkeypatch, **env):
    monkeypatch.chdir(tmp_path)
    gh_out = tmp_path / "gh_output"
    rc = main_entry(
        {
            "PROJECT_CONFIG": "",
            "BUILD_CONFIG": "",
            "BINDINGS_PATH": "",
            "GITHUB_OUTPUT": str(gh_out),
            "RUNNER_TEMP": str(tmp_path),
            **env,
        }
    )
    outputs = (
        dict(line.split("=", 1) for line in gh_out.read_text().splitlines())
        if gh_out.exists()
        else {}
    )
    return rc, outputs


def test_entry_point_writes_outputs_for_an_overlay_profile(tmp_path, monkeypatch):
    (tmp_path / ".abicheck.yml").write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "clang-client": {
                        "consumer_compile": {"standard": "c++20", "frontend": "clang"}
                    }
                }
            }
        )
    )
    rc, outputs = _run_main(tmp_path, monkeypatch, PROFILE_ID="clang-client")
    assert rc == 0
    ctx = json.loads(outputs["extraction-context"])
    assert ctx["source"] == CONTEXT_CONSUMER and ctx["ast_frontend"] == "clang"
    config = json.loads(Path(outputs["build-config"]).read_text())
    assert config["compile"]["frontend"] == "clang"
    assert config["compile"]["options"] == ["-std=c++20"]


def test_entry_point_default_profile_passes_build_config_through(tmp_path, monkeypatch):
    bc = tmp_path / "bc.yml"
    bc.write_text("severity:\n  preset: default\n")
    rc, outputs = _run_main(tmp_path, monkeypatch, PROFILE_ID="p", BUILD_CONFIG=str(bc))
    assert rc == 0
    assert Path(outputs["build-config"]) == bc.resolve()
    assert json.loads(outputs["extraction-context"])["source"] == CONTEXT_DEFAULT


def test_entry_point_fails_closed_on_unresolvable_binding(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".abicheck.yml").write_text(
        yaml.safe_dump({"profiles": {"g": {"compile": {"binding": "nope"}}}})
    )
    b = tmp_path / "b.yml"
    b.write_text(
        yaml.safe_dump({"schema": "abicheck.toolchain-bindings/v1", "bindings": {}})
    )
    rc, outputs = _run_main(tmp_path, monkeypatch, PROFILE_ID="g", BINDINGS_PATH=str(b))
    assert rc == 1 and not outputs
    assert "::error::" in capsys.readouterr().err


# ── the manifest records it ───────────────────────────────────────────────


def _build_manifest_module():
    path = REPO_ROOT / "actions/baseline/build_manifest.py"
    spec = importlib.util.spec_from_file_location("bm_ctx_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_manifest_rejects_non_object_context(tmp_path):
    mod = _build_manifest_module()
    with pytest.raises(SystemExit, match="JSON object"):
        mod.main(
            [
                "--output-dir",
                str(tmp_path),
                "--libraries",
                "[]",
                "--manifest-out",
                str(tmp_path / "m.json"),
                "--extraction-context",
                "[1]",
            ]
        )


def test_build_manifest_records_context_verbatim_and_omits_when_absent(
    tmp_path, capsys
):
    mod = _build_manifest_module()
    ctx = BaselineExtractionContext(
        "p", CONTEXT_CONSUMER, "/c++", "-std=c++20", "clang"
    ).to_dict()
    common = ["--output-dir", str(tmp_path), "--libraries", "[]", "--profile", "p"]
    mod.main(
        [
            *common,
            "--manifest-out",
            str(tmp_path / "a.json"),
            "--extraction-context",
            json.dumps(ctx),
        ]
    )
    mod.main([*common, "--manifest-out", str(tmp_path / "b.json")])
    assert json.loads((tmp_path / "a.json").read_text())["extraction_context"] == ctx
    assert "extraction_context" not in json.loads((tmp_path / "b.json").read_text())

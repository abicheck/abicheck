"""Regression coverage for project-cell evidence and consumer extraction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECK_PROJECT = ROOT / ".github" / "workflows" / "check-project.yml"
CHECK_TARGET = ROOT / "actions" / "check-target" / "action.yml"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(container: dict[str, Any]) -> list[dict[str, Any]]:
    return container["steps"]


def test_consumer_compile_context_uses_a_separate_extraction() -> None:
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    assert (
        "matrix.consumer_compile_ast_frontend"
        in run_step["with"]["consumer-ast-frontend"]
    )
    assert "matrix.consumer_compile_gcc_path" in run_step["with"]["consumer-gcc-path"]
    assert (
        "matrix.consumer_compile_gcc_options"
        in run_step["with"]["consumer-gcc-options"]
    )

    target = _load(CHECK_TARGET)
    steps = _steps(target["runs"])
    consumer = next(
        step
        for step in steps
        if step.get("name") == "Extract candidate consumer context"
    )
    assert consumer["with"]["mode"] == "dump"
    assert consumer["with"]["new-library"] == "${{ inputs.new-library }}"
    assert consumer["with"]["ast-frontend"] == "${{ inputs.consumer-ast-frontend }}"
    # gcc-path/gcc-options were the two fields this hop's own `if:` guard
    # already gated activation on (see test_consumer_dump_activates_from_
    # the_overlay_marker_alone) but never itself asserted forwarding for --
    # a real edge in the wiring chain a config's consumer_compile.binding/
    # standard/stdlib ultimately has to cross to reach the actual dump
    # invocation (bug-class-regression-testing.md Phase 6), left untested
    # even though ast-frontend's identically-shaped sibling line was.
    assert consumer["with"]["gcc-path"] == "${{ inputs.consumer-gcc-path }}"
    assert consumer["with"]["gcc-options"] == "${{ inputs.consumer-gcc-options }}"
    analysis = next(step for step in steps if step.get("name") == "Run analysis")
    assert "check-target-consumer.abi.json" in analysis["with"]["new-library"]
    assert "consumer_context.outcome == 'success'" in analysis["with"]["header"]


def test_consumer_dump_activates_from_the_overlay_marker_alone() -> None:
    """Codex review (P2): an overlay declaring only e.g. `binding:` with no

    matching --toolchain-bindings entry (and no workflow-global compiler
    input to fall back to) resolves consumer-ast-frontend/consumer-gcc-path/
    consumer-gcc-options all to the empty string even though the profile
    genuinely declared a consumer_compile: overlay. Requiring one of those
    three to be non-empty before activating the separate candidate dump let
    such a cell silently skip it, leaking the producer's own compiler
    options into what the schema documents as an isolated client view. The
    activation condition must also fire on the overlay's own presence
    marker, forwarded independently of what its fields resolved to.
    """
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    expr = run_step["with"]["consumer-compile-active"]
    assert "matrix.consumer_compile_active" in expr
    assert "matrix.kind != 'bundle'" in expr

    target = _load(CHECK_TARGET)
    consumer = next(
        step
        for step in _steps(target["runs"])
        if step.get("name") == "Extract candidate consumer context"
    )
    assert "inputs.consumer-compile-active == 'true'" in consumer["if"]


def test_resolved_evidence_pack_also_reaches_build_info_for_every_producer() -> None:
    """check-target's own evidence-pack-path -> --build-info conversion is

    gated on evidence-producer: wrapper/clang-plugin, so a replay (or
    unset) producer would otherwise never see this cell's resolved
    per-target build-output.json evidence at all (Codex review P1). The
    workflow must forward it through the caller-supplied `build-info` input
    independently of that gate, falling back to the caller's own
    `inputs.build-info` when this cell resolved no target evidence.
    """
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    expr = run_step["with"]["build-info"]
    assert "steps.candidate.outputs.evidence-pack" in expr
    assert "steps.candidate.outputs.evidence-producer" in expr
    assert "inputs.evidence-producer) != 'wrapper'" in expr
    assert "inputs.evidence-producer) != 'clang-plugin'" in expr
    assert expr.rstrip().endswith("|| inputs.build-info }}")


def test_build_info_routing_tests_the_resolved_producer_not_the_legacy_input() -> None:
    """Codex review (P2): the evidence-producer field this same step sets

    (two lines below) resolves as `steps.candidate.outputs.evidence-producer
    || inputs.evidence-producer` -- a target whose build-output.json names
    'replay' while the caller's legacy workflow-global input still defaults
    to 'wrapper'/'clang-plugin' must be tested against that SAME resolved
    value here, or this cell's resolved pack silently never reaches
    build-info even though check-target itself received 'replay'.
    """
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    build_info_expr = run_step["with"]["build-info"]
    producer_expr = run_step["with"]["evidence-producer"]
    assert (
        "steps.candidate.outputs.evidence-producer || inputs.evidence-producer"
        in producer_expr
    )
    assert (
        "(steps.candidate.outputs.evidence-producer || inputs.evidence-producer)"
        in build_info_expr
    )


def test_consumer_dump_prefers_new_side_over_shared_header_and_include() -> None:
    """mode: dump has no old/new distinction, so passing both `header` and

    `new-header` (or `include`/`new-include`) would union them instead of
    letting the candidate-specific value replace the shared one the way
    `compare` mode's own new side does (Codex review P2).
    """
    target = _load(CHECK_TARGET)
    consumer = next(
        step
        for step in _steps(target["runs"])
        if step.get("name") == "Extract candidate consumer context"
    )
    assert "new-header" not in consumer["with"]
    assert "new-include" not in consumer["with"]
    header_expr = consumer["with"]["header"]
    assert "inputs.new-header != ''" in header_expr
    assert header_expr.rstrip().endswith("|| inputs.header }}")
    include_expr = consumer["with"]["include"]
    assert "inputs.new-include != ''" in include_expr
    assert include_expr.rstrip().endswith("|| inputs.include }}")


def test_consumer_dump_forwards_gcc_prefix() -> None:
    """A cross-compilation caller selects its toolchain via `gcc-prefix`;

    the separate consumer-context dump forwarded the compiler path/options
    but omitted this global-only field, so it would resolve a host/default
    compiler instead (Codex review P2).
    """
    target = _load(CHECK_TARGET)
    consumer = next(
        step
        for step in _steps(target["runs"])
        if step.get("name") == "Extract candidate consumer context"
    )
    assert consumer["with"]["gcc-prefix"] == "${{ inputs.gcc-prefix }}"


def test_consumer_compile_fields_fall_back_to_global_compiler_inputs() -> None:
    """An omitted consumer_compile field must defer to the caller's

    workflow-global input, not the empty string, matching the schema's
    documented per-field precedence and the producer field's own fallback
    two lines up (Codex review P2) -- but ONLY for a cell whose profile
    actually declares a consumer_compile: overlay. A second review round
    found the first fix used the global fallback unconditionally, so a
    profile with NO consumer_compile: overlay at all still got a non-empty
    consumer-ast-frontend/gcc-path/gcc-options the moment the caller set
    any workflow-global --ast-frontend/--gcc-path/--gcc-options -- which
    activates check-target's separate consumer-context dump for every
    cell, not just the ones with a real overlay. The fallback must be
    gated on matrix.consumer_compile_active (the overlay's own presence).
    """
    project = _load(CHECK_PROJECT)
    run_step = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Run check-target"
    )
    for field in ("consumer-ast-frontend", "consumer-gcc-path", "consumer-gcc-options"):
        expr = run_step["with"][field]
        assert "matrix.consumer_compile_active" in expr, field
        assert expr.rstrip().endswith("|| '' }}"), field

    from abicheck.buildsource.run_plan import RunPlanCheck

    active = RunPlanCheck(consumer_compile_active=True)
    assert "consumer_compile_active" in active.to_dict()
    inactive = RunPlanCheck(consumer_compile_active=False)
    assert "consumer_compile_active" not in inactive.to_dict()


def test_target_evidence_path_is_forwarded_per_matrix_cell() -> None:
    project = _load(CHECK_PROJECT)
    steps = _steps(project["jobs"]["check"])
    candidate = next(
        step
        for step in steps
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    assert "target_evidence.get('path'" in candidate["run"]
    run_step = next(step for step in steps if step.get("name") == "Run check-target")
    assert run_step["with"]["evidence-pack-path"] == (
        "${{ steps.candidate.outputs.evidence-pack }}"
    )
    assert run_step["with"]["evidence-producer"].startswith(
        "${{ steps.candidate.outputs.evidence-producer"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_selects_only_the_current_targets_evidence(tmp_path: Path) -> None:
    """validate_build_output() (added alongside the injection hardening

    below) validates every targets[] entry, not just the one this cell is
    resolving -- so both "core" and "math" need a genuinely valid binary
    + digest + evidence pack here, matching a real build-output.json a
    build wrapper would actually produce, or the resolver would (correctly)
    fail closed before ever reaching the per-cell selection this test is
    about.
    """
    from test_build_output import _binary, _write_pack

    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    (tmp_path / "candidate").mkdir()
    (tmp_path / "candidate" / "libmath.so").write_text("binary")
    build_root = tmp_path / "build-output"
    digest_core = _binary(build_root, "artifacts/libcore.so")
    digest_math = _binary(build_root, "artifacts/libmath.so")
    _write_pack(build_root, "evidence/core", library="core")
    _write_pack(build_root, "evidence/math", library="math")
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "evidence_producer": {"kind": "clang-plugin"},
                "targets": [
                    {
                        "id": "core",
                        "binary": "artifacts/libcore.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": "evidence/core",
                            "projection": "declared",
                        },
                    },
                    {
                        "id": "math",
                        "binary": "artifacts/libmath.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": "evidence/math",
                            "projection": "declared",
                        },
                    },
                ],
                "digests": {
                    "artifacts/libcore.so": f"sha256:{digest_core}",
                    "artifacts/libmath.so": f"sha256:{digest_math}",
                },
            }
        )
    )
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(
                {"kind": "target", "name": "math", "binary_pattern": "*.so"}
            ),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text().splitlines()
        if "=" in line
    )
    assert outputs["evidence-pack"] == str((build_root / "evidence" / "math").resolve())
    assert outputs["evidence-producer"] == "clang-plugin"
    assert "evidence/core" not in outputs["evidence-pack"]


def _write_valid_build_output(
    build_root: Path, *, target_id: str = "math", producer_kind: str = "wrapper"
) -> None:
    """A build-output.json that genuinely passes validate_build_output(),

    reusing test_build_output.py's own fixture helpers rather than
    hand-rolling a second copy of what "valid" means.
    """
    from test_build_output import _binary, _write_pack

    binary_rel = f"artifacts/lib{target_id}.so"
    digest = _binary(build_root, binary_rel)
    _write_pack(build_root, f"evidence/{target_id}", library=target_id)
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {
                        "id": target_id,
                        "binary": binary_rel,
                        "evidence": {
                            "kind": "source-facts",
                            "path": f"evidence/{target_id}",
                            "projection": "declared",
                        },
                    }
                ],
                "digests": {binary_rel: f"sha256:{digest}"},
                "evidence_producer": {"kind": producer_kind},
            }
        )
    )


def _run_resolver(resolver_run: str, tmp_path: Path, target_id: str) -> Any:
    candidate = tmp_path / "candidate"
    candidate.mkdir(exist_ok=True)
    (candidate / f"lib{target_id}.so").write_text("binary")
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    result = subprocess.run(
        ["bash", "-c", resolver_run],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(
                {
                    "kind": "target",
                    "name": target_id,
                    "binary_pattern": f"lib{target_id}.so",
                }
            ),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text().splitlines()
        if "=" in line
    )
    return result, outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_forwards_a_validated_targets_evidence_and_producer(
    tmp_path: Path,
) -> None:
    """Happy-path control for the two hardening tests below: a genuinely

    valid build-output.json (passes validate_build_output()) still resolves
    its evidence-pack and evidence-producer normally.
    """
    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    _write_valid_build_output(tmp_path / "build-output")
    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode == 0, result.stderr
    assert outputs["evidence-producer"] == "wrapper"
    assert outputs["evidence-pack"] == str(
        (tmp_path / "build-output" / "evidence" / "math").resolve()
    )


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_validates_an_evidence_claim_with_an_empty_path(
    tmp_path: Path,
) -> None:
    """Codex review (P2): a target declaring an `evidence` object at all --

    even one whose `path` is empty or missing -- must be validated before
    being treated as though no evidence were declared. Gating
    validate_build_output() on a truthy `path` let a malformed claim (an
    `evidence: {}` with no `projection`, which validate_build_output()
    rejects as not being one of 'declared'/'inferred') silently fall
    through to the empty-evidence branch instead of failing closed.
    """
    from test_build_output import _binary

    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    build_root = tmp_path / "build-output"
    digest = _binary(build_root, "artifacts/libmath.so")
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {
                        "id": "math",
                        "binary": "artifacts/libmath.so",
                        "evidence": {},
                    }
                ],
                "digests": {"artifacts/libmath.so": f"sha256:{digest}"},
            }
        )
    )
    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode != 0
    assert "evidence.projection" in result.stderr
    assert "evidence-pack" not in outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
@pytest.mark.parametrize("newline_char", ["\n", "\r"], ids=["lf", "cr"])
def test_resolver_rejects_newline_in_evidence_producer_kind(
    tmp_path: Path, newline_char: str
) -> None:
    """Codex review (P1): evidence_producer.kind is read from an untrusted

    build artifact and was written straight to the line-oriented
    $GITHUB_OUTPUT file -- an embedded newline could inject or override a
    later output record (e.g. a spoofed new-library=), the same class of
    bug already guarded against for resolved candidate/evidence paths.
    """
    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    _write_valid_build_output(
        tmp_path / "build-output",
        producer_kind=f"wrapper{newline_char}new-library=evil",
    )
    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode != 0
    assert "newline character" in result.stderr
    # The injected line must never have landed as a second, overriding
    # new-library= record.
    # new-library is resolved relative to the (relative) `candidate` root the
    # script glob.glob()s against -- not an absolute path.
    assert outputs.get("new-library") == "candidate/libmath.so"
    assert "evidence-producer" not in outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_rejects_evidence_shared_across_targets(tmp_path: Path) -> None:
    """Codex review (P2): path confinement alone only proves evidence.path

    *points* somewhere safe -- it says nothing about whether the
    build-output.json making a 'declared' claim is actually trustworthy.
    Two targets sharing one evidence pack is exactly the shape
    validate_build_output() (the same check `project validate-build` runs,
    which `project plan` never calls) already rejects; the resolver must
    run it before treating this target's evidence as scoped to it.
    """
    from test_build_output import _binary, _write_pack

    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    build_root = tmp_path / "build-output"
    digest_math = _binary(build_root, "artifacts/libmath.so")
    digest_core = _binary(build_root, "artifacts/libcore.so")
    _write_pack(build_root, "evidence/shared", library="math")
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {
                        "id": "math",
                        "binary": "artifacts/libmath.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": "evidence/shared",
                            "projection": "declared",
                        },
                    },
                    {
                        "id": "core",
                        "binary": "artifacts/libcore.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": "evidence/shared",
                            "projection": "declared",
                        },
                    },
                ],
                "digests": {
                    "artifacts/libmath.so": f"sha256:{digest_math}",
                    "artifacts/libcore.so": f"sha256:{digest_core}",
                },
                "evidence_producer": {"kind": "wrapper"},
            }
        )
    )
    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode != 0
    assert "referenced by more than one target" in result.stderr
    assert "evidence-pack" not in outputs
    assert "evidence-producer" not in outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_rejects_evidence_whose_manifest_names_another_target(
    tmp_path: Path,
) -> None:
    """Codex review (P2), the sibling scenario: a pack whose own

    manifest.library disagrees with the target referencing it.
    """
    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    _write_valid_build_output(tmp_path / "build-output")
    # Overwrite the pack's manifest so its declared library disagrees with
    # the target ("math") that references it.
    manifest_path = tmp_path / "build-output" / "evidence" / "math" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "kind": "abicheck_inputs",
                "abicheck_inputs_version": 1,
                "library": "some-other-target",
            }
        )
    )
    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode != 0
    assert "does not match the target referencing it" in result.stderr
    assert "evidence-pack" not in outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
def test_resolver_rejects_inferred_projection_evidence(tmp_path: Path) -> None:
    """Codex review (P1): a 'declared' pack is exclusive to one target

    (validate_build_output() already rejects sharing it), but an
    'inferred' pack is deliberately build-wide and relies on
    evidence.attribution_path + this target's id to filter which TUs
    actually belong to it -- filtering nothing in this workflow (or any
    real dump/compare CLI caller of ingest_inputs_pack()) currently
    performs. Forwarding a genuinely valid (per validate_build_output())
    'inferred' pack unfiltered would let this target's build/source check
    silently incorporate every other target sharing the same pack, so the
    resolver must reject it outright rather than treat it as scoped.
    """
    from test_build_output import BUILD_OUTPUT_SCHEMA, _binary, _write_attribution

    from abicheck.buildsource.build_evidence import BuildEvidence, Target, TargetKind

    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    build_root = tmp_path / "build-output"
    digest = _binary(build_root, "artifacts/libmath.so")
    pack_dir = build_root / "evidence" / "abicheck_inputs"
    (pack_dir / "source_facts").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(json.dumps({"kind": "abicheck_inputs"}))
    (pack_dir / "source_facts" / "tu0.jsonl").write_text(
        json.dumps({"tu_id": "cu://src/math.cpp", "source": "src/math.cpp"}) + "\n"
    )
    _write_attribution(
        build_root,
        "evidence/attribution.json",
        BuildEvidence(
            targets=[
                Target(
                    id="target://math",
                    kind=TargetKind.SHARED_LIBRARY,
                    source_files=["src/math.cpp"],
                )
            ]
        ),
    )
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": BUILD_OUTPUT_SCHEMA,
                "targets": [
                    {
                        "id": "math",
                        "binary": "artifacts/libmath.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": "evidence/abicheck_inputs",
                            "projection": "inferred",
                            "attribution_path": "evidence/attribution.json",
                        },
                    }
                ],
                "digests": {"artifacts/libmath.so": f"sha256:{digest}"},
                "evidence_producer": {"kind": "wrapper"},
            }
        )
    )
    # Confirm the fixture is genuinely valid per validate_build_output()
    # itself -- otherwise this test would pass for the wrong reason (a
    # pre-existing validation error, not the new projection: inferred
    # rejection).
    from abicheck.buildsource.build_output import validate_build_output

    assert validate_build_output(build_root).ok

    result, outputs = _run_resolver(resolver["run"], tmp_path, "math")
    assert result.returncode != 0
    assert "'inferred'" in result.stderr
    assert "evidence-pack" not in outputs


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX bash")
@pytest.mark.parametrize("malicious_path", ["../outside", "/tmp/outside"])
def test_resolver_rejects_escape_without_outside_side_effects(
    tmp_path: Path, malicious_path: str
) -> None:
    """validate_build_output() now runs before the resolver's own manual

    confinement checks, and its own `_declared_evidence_sharing_issues`
    check independently rejects an escaping evidence.path for a
    projection: declared target -- so this target must otherwise be
    genuinely valid (a real binary + digest) for the escaping path to be
    what actually trips the rejection, rather than an unrelated "no
    binary declared"/"missing schema" failure.
    """
    from test_build_output import _binary

    project = _load(CHECK_PROJECT)
    resolver = next(
        step
        for step in _steps(project["jobs"]["check"])
        if step.get("name") == "Resolve candidate binary/binaries"
    )
    (tmp_path / "candidate").mkdir()
    (tmp_path / "candidate" / "libcore.so").write_text("binary")
    build_root = tmp_path / "build-output"
    build_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    digest_core = _binary(build_root, "artifacts/libcore.so")
    (build_root / "build-output.json").write_text(
        json.dumps(
            {
                "schema": "abicheck.build-output/v1",
                "targets": [
                    {
                        "id": "core",
                        "binary": "artifacts/libcore.so",
                        "evidence": {
                            "kind": "source-facts",
                            "path": malicious_path,
                            "projection": "declared",
                        },
                    }
                ],
                "digests": {"artifacts/libcore.so": f"sha256:{digest_core}"},
            }
        )
    )
    github_output = tmp_path / "github_output"
    github_output.write_text("")
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(
                {"kind": "target", "name": "core", "binary_pattern": "*.so"}
            ),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "absolute or escapes" in result.stderr
    assert sentinel.read_text() == "unchanged"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]
    assert "evidence-pack=" not in github_output.read_text()


def _assert_step_input_forwards(
    step: dict[str, Any], field: str, expected_expr: str
) -> None:
    """A single-hop propagation assertion (bug-class-regression-testing.md
    Phase 6's "mechanism": one field, through one edge of the chain), with
    a message that names both the missing/wrong field and what it actually
    resolved to -- the "proving the harness would have caught #860/#883
    before merge, not only after" bar Phase 6's own mutation-check
    requirement sets, restated as one reusable, directly-testable
    predicate rather than a bespoke ``assert`` at every call site.
    """
    actual = step.get("with", {}).get(field)
    if actual != expected_expr:
        name = step.get("name", "<unnamed step>")
        raise AssertionError(
            f"step {name!r} input {field!r} does not forward "
            f"{expected_expr!r} -- got {actual!r} instead. A forwarding "
            f"edge in the consumer_compile propagation chain was dropped "
            f"or renamed."
        )


class TestConsumerCompilePropagationChainMutationCheck:
    """Proves `_assert_step_input_forwards` -- the primitive the
    consumer_compile chain's own hop-2/hop-3 tests above are built from --
    actually fails, and names the missing edge, when a forwarding edge is
    dropped. Per Phase 6's own "mutation check": deliberately removing one
    forwarding edge in a copy of the matrix harness must make the suite
    fail and name the missing path, not silently pass.

    Mutates an in-memory COPY of the real, parsed check-target/action.yml
    (never the file on disk) so this is a property of the assertion
    helper itself, not a live edit to shipped wiring.
    """

    @staticmethod
    def _consumer_context_step() -> dict[str, Any]:
        target = _load(CHECK_TARGET)
        steps = _steps(target["runs"])
        return next(
            step
            for step in steps
            if step.get("name") == "Extract candidate consumer context"
        )

    def test_passes_against_the_real_wiring(self) -> None:
        step = self._consumer_context_step()
        _assert_step_input_forwards(step, "gcc-path", "${{ inputs.consumer-gcc-path }}")
        _assert_step_input_forwards(
            step, "gcc-options", "${{ inputs.consumer-gcc-options }}"
        )

    def test_fails_and_names_the_field_when_the_edge_is_dropped(self) -> None:
        """Simulates the exact #860/#883 shape: a forwarding edge silently
        reverting to some other value (here, the shared/producer gcc-path
        input) rather than the consumer-specific one -- the mistake a
        careless refactor of this step could make, since both inputs exist
        on the same step and a copy-paste error is a real, plausible way to
        drop this specific edge."""
        step = self._consumer_context_step()
        mutated = dict(step)
        mutated["with"] = {**step["with"], "gcc-path": "${{ inputs.gcc-path }}"}

        with pytest.raises(AssertionError) as excinfo:
            _assert_step_input_forwards(
                mutated, "gcc-path", "${{ inputs.consumer-gcc-path }}"
            )

        message = str(excinfo.value)
        assert "gcc-path" in message
        assert "consumer-gcc-path" in message
        assert "${{ inputs.gcc-path }}" in message

    def test_fails_when_the_field_is_removed_entirely(self) -> None:
        """The other real shape a dropped edge takes: the key vanishes
        from `with:` altogether rather than being repointed."""
        step = self._consumer_context_step()
        mutated = dict(step)
        mutated["with"] = {k: v for k, v in step["with"].items() if k != "gcc-options"}

        with pytest.raises(AssertionError, match="gcc-options"):
            _assert_step_input_forwards(
                mutated, "gcc-options", "${{ inputs.consumer-gcc-options }}"
            )

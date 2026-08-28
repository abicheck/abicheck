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

"""CLI — the ``project`` group: advanced multi-target project integration
(ADR-047, consolidated per ADR-054's CLI-organization review).

``project`` is the single root command for the ADR-047 project-integration
lifecycle (project config -> build output -> run plan -> per-target checks
-> aggregate gate). It replaces three former *separate* root command groups
(``project-targets``, ``build-output``, ``run-plan``) that each promoted one
intermediate pipeline artifact to its own top-level verb — CLAUDE.md's
"Adding a new top-level command" bar now requires a new root command's
operand to be a user-facing domain object, not an internal transport
artifact, and grouping the three under one advanced-integration namespace is
how that bar is satisfied here without losing any of the three checks:

\b
  project validate        was: project-targets validate
  project validate-build  was: build-output validate
  project plan            was: run-plan generate

Two former subcommands are **not** carried forward as public CLI surface:
``build-output baseline-libraries`` (a wire-format adapter for exactly one
GitHub Action's input, not a general project-integration operation — its
underlying :func:`~abicheck.buildsource.baseline_publish.derive_baseline_libraries`
stays a library function callers invoke directly) and ``run-plan
to-aggregate-manifest`` (a pure intermediate-format conversion now folded into
``aggregate --run-plan``, so a caller never has to know the projection exists).

Split out of :mod:`abicheck.cli` per the sibling-module pattern; imported for
side-effect at the bottom of :mod:`abicheck.cli` so ``@main.group``/
``@project_group.command`` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .buildsource.build_output import (
    BuildOutput,
    load_build_output,
    validate_build_output,
)
from .buildsource.project_targets import (
    ProjectTargetsConfig,
    validate_project_targets,
)
from .buildsource.run_plan import generate_run_plan
from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option
from .workflows.extraction import (
    BindingsFile,
    BindingsFileError,
    check_profile_bindings_resolve,
    check_profile_toolchain_identity,
    load_bindings_file,
)


@main.group("project")
def project_group() -> None:
    """Advanced multi-target project integration (ADR-047).

    \b
    Subcommands:
      validate         Check .abicheck.yml's targets/bundles/profiles/channels block.
      validate-build   Check a project-produced abicheck-build/ directory.
      plan             Derive run-plan.json from .abicheck.yml + build-output.json.

    Most libraries never need this group — it exists for projects that check
    several targets/build profiles/baseline channels together, wired through
    the reusable ``check-project.yml`` GitHub Actions workflow. A single
    library checking one artifact just uses ``dump``/``compare``/``scan``.

    A whole-*product* baseline archive (multiple interdependent libraries
    packed into one deterministic ``.tar.zst``, so a bundle-aware
    ``compare`` sees every cross-library edge in one invocation instead of
    one per-library ``scan``) is a library-only surface, not a CLI command
    here — see :mod:`abicheck.product_baseline`'s
    :func:`~abicheck.product_baseline.pack_product_baseline`/
    :func:`~abicheck.product_baseline.unpack_product_baseline`.
    """


# --------------------------------------------------------------------------
# project validate  (was: project-targets validate)
# --------------------------------------------------------------------------


@project_group.command("validate")
@click.argument(
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=".abicheck.yml",
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
@click.option(
    "--toolchain-bindings",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a trusted toolchain-bindings file (schema "
        "abicheck.toolchain-bindings/v1) to additionally check every "
        "declared profiles.<id>.compile.binding (and consumer_compile.binding) "
        "resolves, and — when compiler_family/compiler_version/target is also "
        "declared — that the resolved executable's probed identity actually "
        "matches. Loaded only from this explicit path — never auto-"
        "discovered, per the untrusted-config trust boundary "
        "ProfileCompileSpec.binding documents."
    ),
)
@verbose_option
def project_validate_cmd(
    config: Path,
    fmt: str,
    output: Path | None,
    toolchain_bindings: Path | None,
    verbose: bool,
) -> None:
    """Validate CONFIG's targets:/bundles:/profiles:/baseline: block (ADR-047 §3).

    CONFIG defaults to ``.abicheck.yml`` in the current directory. Checks:
    every target's ``kind``-specific required fields are set (and no
    kind-inappropriate field is); ``app-consumer``/``plugin-contract``
    targets' ``library`` resolves to a real ``kind: library`` target; every
    ``bundle:`` reference and bundle membership resolves and agrees; every
    ``checks[].channel`` resolves to a declared baseline channel (or is the
    ``"none"`` no-baseline sentinel); ``checks[].depth``/``gate_mode`` are
    valid; every ``checks[].profiles`` entry resolves to a declared profile;
    every id is a valid, ``check_id``-embeddable identifier. With
    ``--toolchain-bindings``, also checks every declared
    ``profiles.<id>.compile.binding``/``consumer_compile.binding`` resolves
    against that file, and that a resolved binding's probed compiler
    identity matches any declared
    ``compiler_family``/``compiler_version``/``target``
    (G34 Phase A; MSVC bindings are skipped — see
    ``abicheck.buildsource.toolchain_probe``'s module docstring).

    Structural/type errors in the YAML itself (unknown key, wrong type) fail
    immediately as a usage error; this command's own validation report only
    covers cross-reference/semantic issues on an already-well-formed block.

    \b
    Exit codes:
      0   Valid — no errors (warnings may still be present).
      1   One or more validation errors.
      64  Usage error (CONFIG is not readable YAML, or fails strict parsing).
    """
    _setup_verbosity(verbose)

    parsed = _load_project_targets_config(config)
    report = validate_project_targets(parsed)

    if toolchain_bindings is not None:
        try:
            bindings_file = load_bindings_file(toolchain_bindings)
        except BindingsFileError as exc:
            raise click.UsageError(str(exc)) from exc
        report.errors.extend(
            check_profile_bindings_resolve(parsed.profiles, bindings_file)
        )
        report.errors.extend(
            check_profile_toolchain_identity(parsed.profiles, bindings_file)
        )

    if fmt == "json":
        text = json.dumps(report.to_dict(), indent=2)
    else:
        lines = [f"project validation: {config}"]
        if report.ok:
            lines.append("OK — no errors.")
        else:
            lines.append(f"FAILED — {len(report.errors)} error(s):")
            lines.extend(f"  - {e}" for e in report.errors)
        if report.warnings:
            lines.append(f"{len(report.warnings)} warning(s):")
            lines.extend(f"  - {w}" for w in report.warnings)
        text = "\n".join(lines)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0 if report.ok else 1)


def _load_project_targets_config(config: Path) -> ProjectTargetsConfig:
    import yaml

    try:
        raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise click.UsageError(f"cannot read {config}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise click.UsageError(f"{config} must contain a YAML mapping.")

    try:
        return ProjectTargetsConfig.from_dict(raw)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


# --------------------------------------------------------------------------
# project validate-build  (was: build-output validate)
# --------------------------------------------------------------------------


@project_group.command("validate-build")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
@verbose_option
def project_validate_build_cmd(
    directory: Path,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Validate DIRECTORY's build-output.json (ADR-047 §11.1).

    Checks: every declared public/generated header root is non-empty; every
    target's binary exists and matches its digests[] entry; evidence.projection
    is 'declared' for every target that has evidence ('inferred' is
    schema-reserved for a future attribution mechanism and is always
    rejected); no evidence pack is referenced by more than one target, and a
    referenced pack's own identity (manifest.library or a tagged TU's
    target_id) agrees with the specific target using it.

    \b
    Exit codes:
      0   Valid — no errors (warnings may still be present).
      1   One or more validation errors.
      64  Usage error (DIRECTORY is not a readable build-output.json).
    """
    _setup_verbosity(verbose)

    try:
        report = validate_build_output(directory)
    except (FileNotFoundError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc

    if fmt == "json":
        text = json.dumps(report.to_dict(), indent=2)
    else:
        lines = [f"build-output validation: {directory}"]
        if report.ok:
            lines.append("OK — no errors.")
        else:
            lines.append(f"FAILED — {len(report.errors)} error(s):")
            lines.extend(f"  - {e}" for e in report.errors)
        if report.warnings:
            lines.append(f"{len(report.warnings)} warning(s):")
            lines.extend(f"  - {w}" for w in report.warnings)
        text = "\n".join(lines)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0 if report.ok else 1)


# --------------------------------------------------------------------------
# project validate-use-cases  (G29 Phase 4, ADR-057 amendment)
# --------------------------------------------------------------------------


@project_group.command("validate-use-cases")
@click.argument(
    "manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the validation report.",
)
@verbose_option
def project_validate_use_cases_cmd(
    manifest: Path,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Validate MANIFEST, an ``impact-use-cases.yaml`` file (G29 Phase 4,
    ADR-057 amendment; see docs/contribute/use-case-impact.md).

    Checks the manifest is a well-formed YAML list of use cases: a
    non-mapping entry, an unrecognized field, or a missing/blank
    ``use_case`` name is a usage error.

    Structure only. Resolving a manifest's entrypoints against a real
    library, and attributing a real comparison's findings to the use cases
    that reach them, is ``abicheck compare --use-cases MANIFEST`` -- one
    comparison, reported in the same place as every other finding, rather
    than a second snapshot-diffing surface grown inside a validator (the
    ``--against``/``--against-new`` pair that used to live here).

    \b
    Exit codes:
      0   Valid — the manifest is well-formed.
      64  Usage error (MANIFEST is malformed or unreadable).
    """
    _setup_verbosity(verbose)

    from .errors import UseCaseManifestError
    from .impact.use_cases import load_use_case_manifest

    try:
        definitions = load_use_case_manifest(manifest)
    except (UseCaseManifestError, OSError) as exc:
        # OSError alongside the manifest-specific error (Codex review, fresh
        # evidence): load_use_case_manifest() deliberately leaves a missing/
        # unreadable MANIFEST unwrapped (its own docstring) — Click's
        # exists=True check only guarantees the path was there at argument
        # parsing time, not at the read a moment later (permissions change,
        # the file disappearing), so an unhandled OSError here would exit 1
        # with a bare traceback instead of the documented usage-error path.
        raise click.UsageError(str(exc)) from exc

    if fmt == "json":
        text = json.dumps(
            {
                "manifest": str(manifest),
                "ok": True,
                "use_case_count": len(definitions),
            },
            indent=2,
        )
    else:
        text = "\n".join(
            [
                f"use-case manifest validation: {manifest}",
                f"OK — {len(definitions)} use case(s), structurally well-formed.",
            ]
        )

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0)


# --------------------------------------------------------------------------
# project plan  (was: run-plan generate)
# --------------------------------------------------------------------------


def _parse_build_output_specs(
    specs: tuple[str, ...],
) -> dict[str, BuildOutput]:
    build_outputs: dict[str, BuildOutput] = {}
    for spec in specs:
        profile_id, sep, dir_str = spec.partition("=")
        if not sep or not profile_id or not dir_str:
            raise click.UsageError(f"--build-output must be PROFILE=DIR, got {spec!r}")
        if profile_id in build_outputs:
            raise click.UsageError(
                f"--build-output: profile {profile_id!r} was specified more than once"
            )
        try:
            build_output = load_build_output(dir_str)
        except (FileNotFoundError, ValueError) as exc:
            raise click.UsageError(f"--build-output {spec}: {exc}") from exc
        manifest_profile_id = build_output.profile.id
        if manifest_profile_id and manifest_profile_id != profile_id:
            # A stale or misnamed build-output directory passed under the
            # wrong PROFILE key would otherwise make generate_run_plan()
            # emit cells as if a build for a different ABI environment
            # covered this profile (Codex review). profile.id is optional/
            # defaulted like every build-output.json field, so an empty
            # (unset) id can't be verified and is allowed through -- only a
            # manifest that *does* declare a profile.id gets cross-checked.
            raise click.UsageError(
                f"--build-output {spec}: manifest declares profile.id "
                f"{manifest_profile_id!r}, which does not match {profile_id!r}"
            )
        build_outputs[profile_id] = build_output
    return build_outputs


@project_group.command("plan")
@click.argument(
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=".abicheck.yml",
)
@click.option(
    "--build-output",
    "build_output_specs",
    multiple=True,
    metavar="PROFILE=DIR",
    help=(
        "One contract profile's abicheck-build/ directory (containing "
        "build-output.json), as profile_id=path/to/dir. Repeatable — pass "
        "one per profile referenced by CONFIG's checks:."
    ),
)
@click.option(
    "--project",
    default="",
    help="Project identifier recorded in run-plan.json, e.g. owner/repo.",
)
@click.option(
    "--head-sha",
    default="",
    help="Candidate commit SHA recorded in run-plan.json.",
)
@click.option(
    "--toolchain-bindings",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to a trusted toolchain-bindings file (schema "
        "abicheck.toolchain-bindings/v1). Every declared "
        "profiles.<id>.compile.binding (and consumer_compile.binding) is "
        "checked against it (an unresolvable binding, or a resolved "
        "binding whose probed identity disagrees with a declared "
        "compiler_family/compiler_version/target, is a generation error, "
        "same severity as an unresolvable build-output target) -- "
        "identity probing only covers the profiles the generated plan "
        "actually resolves a check for, not every profile declared in "
        "CONFIG (unlike `project validate`, which checks every declared "
        "profile); each resolved cell's compile_gcc_path is "
        "populated from it. Omitting this flag skips the check entirely "
        "and leaves compile_gcc_path empty on every cell — backward "
        "compatible, matching `project validate --toolchain-bindings`."
    ),
)
@click.option(
    "--allow-empty",
    is_flag=True,
    default=False,
    help=(
        "Accept a run-plan that resolves to zero checks (exit 0 instead of "
        "1). Off by default: an empty run-plan silently skips every "
        "downstream matrix/aggregate step, so a consumer that doesn't add "
        "its own guard would report success having checked nothing. Pass "
        "this only for a deliberately empty bootstrap run (e.g. before any "
        "targets: are declared yet)."
    ),
)
@output_options(
    ["json", "text"],
    default="json",
    format_help="Output format for the generated run-plan.",
)
@verbose_option
def project_plan_cmd(
    config: Path,
    build_output_specs: tuple[str, ...],
    project: str,
    head_sha: str,
    toolchain_bindings: Path | None,
    allow_empty: bool,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Generate run-plan.json from CONFIG's targets:/bundles:/profiles: block.

    CONFIG's optional ``aggregate: gate:`` block (CLI cleanup phase two, PR 2
    follow-up) is stamped onto the generated ``run-plan.json``'s own ``gate``
    block, exactly as a hand-authored ``aggregate --manifest``'s own ``gate``
    block would be -- so ``abicheck aggregate --run-plan run-plan.json``
    applies the same policy either way. This replaces the former
    ``--gate-missing-required``/``--gate-unexpected-target`` flags (removed,
    no CLI alias): the policy is durable project configuration, not
    something to re-type on every invocation. Omitting ``aggregate:`` (or
    either of its ``gate:`` sub-keys) leaves the field unset on the plan, and
    ``aggregate``'s own hard-coded defaults (``missing_required: fail``,
    ``unexpected_target: include``) apply, unchanged from before this block
    existed.

    CONFIG defaults to ``.abicheck.yml``. For every ``checks[]`` entry (per
    target or per bundle), resolves which ``(target, profile)`` cells
    actually apply: an explicit ``checks[].profiles:`` selector must resolve
    against that profile's ``--build-output``, or it's an error; an implicit
    "every contract profile" sweep silently skips a profile that doesn't
    build the target (never a blind cross-product, ADR-047 §3). Each
    resolved cell's ``check_id`` is
    ``target@profile#baseline_channel@requested_depth`` (ADR-047 §7).

    With ``--toolchain-bindings``, each resolved cell's profile
    ``compile.binding`` (if declared; ``consumer_compile.binding`` is
    resolved independently the same way) additionally resolves into that
    cell's ``compile_gcc_path`` — an unresolvable declared binding is a
    generation error, the same severity as an unresolvable build-output
    target. Any declared ``compiler_family``/``compiler_version``/``target``
    is also checked against the resolved binding's real identity (G34 Phase
    A) — a mismatch here is exactly as much a generation error as an
    unresolvable binding, since a run-plan that silently emits the wrong
    compiler's path is worse than one that fails to generate at all. This
    identity probing only covers the profiles the generated plan actually
    resolves a check for, not every profile declared in CONFIG — unlike
    ``project validate``, which checks every declared profile regardless of
    whether the current ``--build-output`` set resolves a check for it.
    Every cell's profile ``compile`` overlay
    (``standard``/``stdlib``/``target``/``abi_macros``/``args``) is always
    composed into ``compile_gcc_options`` regardless of
    ``--toolchain-bindings`` (P1 toolchain-profile audit).

    \b
    Exit codes:
      0   Generated with no coverage-gap errors (warnings may still exist),
          and at least one check resolved (or --allow-empty was given).
      1   A required/explicit check could not be resolved against the
          supplied --build-output directories, (with --toolchain-bindings) a
          declared profiles.<id>.compile.binding does not resolve against
          it or its probed identity disagrees with the declared
          compiler_family/compiler_version/target, or the run-plan resolved
          to zero checks without --allow-empty.
      64  Usage error (CONFIG, a --build-output value, or
          --toolchain-bindings is unreadable, or CONFIG fails
          project validation).
    """
    _setup_verbosity(verbose)

    parsed = _load_project_targets_config(config)

    validation = validate_project_targets(parsed)
    if not validation.ok:
        details = "; ".join(validation.errors)
        raise click.UsageError(
            "cannot generate a run-plan from an invalid project config "
            f"({len(validation.errors)} error(s)): {details} — run "
            f"`abicheck project validate {config}` for the full report."
        )

    build_outputs = _parse_build_output_specs(build_output_specs)

    resolved_bindings: dict[str, str] | None = None
    bindings_file_for_identity: BindingsFile | None = None
    binding_errors: list[str] = []
    if toolchain_bindings is not None:
        try:
            bindings_file = load_bindings_file(toolchain_bindings)
        except BindingsFileError as exc:
            raise click.UsageError(str(exc)) from exc
        resolved_bindings = bindings_file.bindings
        bindings_file_for_identity = bindings_file
        binding_errors = check_profile_bindings_resolve(parsed.profiles, bindings_file)

    plan, report = generate_run_plan(
        parsed,
        build_outputs,
        project=project,
        head_sha=head_sha,
        resolved_bindings=resolved_bindings,
        gate_missing_required=(
            parsed.aggregate_gate.missing_required if parsed.aggregate_gate else None
        ),
        gate_unexpected_target=(
            parsed.aggregate_gate.unexpected_target if parsed.aggregate_gate else None
        ),
    )

    if bindings_file_for_identity is not None:
        # Probe only profiles the generated plan actually resolved a check
        # for, not every profile declared in the config. A bindings file may
        # legitimately be shared across runners (e.g. one committed file
        # naming both a Linux and a macOS toolchain), and an unselected or
        # non-contract profile's binding can name a platform-specific
        # executable that simply doesn't exist on the current host. Probing
        # it anyway would abort an otherwise-valid plan over a profile the
        # plan never uses (Codex review, fresh evidence: an unused macOS
        # profile aborted a Linux-only plan on ubuntu-latest CI). `project
        # validate` intentionally keeps checking every declared profile,
        # since it validates the *config*, not one runner's resolved plan.
        used_profile_ids = {c.profile_id for c in plan.checks if c.profile_id}
        used_profiles = {
            profile_id: profile
            for profile_id, profile in parsed.profiles.items()
            if profile_id in used_profile_ids
        }
        binding_errors.extend(
            check_profile_toolchain_identity(used_profiles, bindings_file_for_identity)
        )

    report.errors.extend(binding_errors)

    if not plan.checks and not allow_empty:
        # Fail-closed by default (ADR-054): an empty run-plan otherwise
        # silently skips every downstream matrix/aggregate step, so a
        # consumer with no guard of its own would report success having
        # gated nothing. `generate_run_plan` itself only warns here — that
        # warning documents *why* the plan is empty, this error is what
        # makes it a hard stop for a caller that doesn't add its own check.
        report.errors.append(
            "run-plan resolved to zero checks -- pass --allow-empty to "
            "accept this (e.g. bootstrapping .abicheck.yml before any "
            "targets:/bundles: checks[] are declared yet)."
        )

    for e in report.errors:
        click.echo(f"error: {e}", err=True)
    for w in report.warnings:
        click.echo(f"warning: {w}", err=True)

    if fmt == "json":
        text = json.dumps(plan.to_dict(), indent=2)
    else:
        lines = [f"run-plan: {len(plan.checks)} check(s)"]
        lines.extend(
            f"  - {c.check_id} (required={c.required}, gate_mode={c.gate_mode})"
            for c in plan.checks
        )
        text = "\n".join(lines)

    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(0 if report.ok else 1)

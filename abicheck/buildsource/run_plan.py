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

"""``run-plan.json`` generation (G30 P1.4, ADR-047 §4/§5).

Projects a project's ``.abicheck.yml`` ``targets:``/``bundles:``/
``profiles:``/``baseline:`` block (G30 P1.5,
:mod:`abicheck.buildsource.project_targets`) plus each ``contract: true``
profile's ``build-output.json`` (G30 P1.1, :mod:`abicheck.buildsource.
build_output`) into a concrete, ordered list of checks -- one per
``(target-or-bundle, profile, checks[] entry)`` cell -- that
``check-project.yml``'s matrix strategy and ``check-single.yml``'s direct
invocation both consume.

**Cell derivation is the "never a blind cross-product" rule
``project_targets.py`` documents but deliberately defers here:**

- When a ``checks[]`` entry carries an explicit ``profiles:`` selector, only
  those profiles are considered for that check -- and each one *must* build
  the referenced target/library (a `build-output.json` `targets[]` entry
  with a matching id), or it's a hard error (a caller explicitly asked for
  an impossible cell).
- When a ``checks[]`` entry omits ``profiles:``, every ``contract: true``
  profile is *considered*, but a profile whose ``build-output.json``
  doesn't list the referenced target/library is silently skipped -- no
  error, since the whole point of the implicit sweep is "run this check on
  every profile where it makes sense," not "every profile, or fail."
- Either way, a **declared contract profile with no `build-output.json` at
  all** (as opposed to one whose `build-output.json` was provided but
  doesn't build this particular target) is always a hard error, explicit or
  implicit sweep alike -- it almost always means that profile's build/
  upload failed or was misnamed, and letting the implicit sweep silently
  drop it would let ``aggregate`` pass over an under-covered matrix without
  anyone noticing (Codex review).

**The ``app-consumer``/``plugin-contract`` library redirect (ADR-047 §3):**
both kinds resolve their build-output existence check, and the candidate
binary a caller globs for, through their own ``library`` field -- neither
kind ever gets its own ``build-output.json`` ``targets[]`` entry, since
build-output describes real build products and an app-consumer/
plugin-contract target is a *check*, not a build product. The generated
:class:`RunPlanCheck` carries ``baseline_target`` (empty for ``kind:
library``, the referenced library's id otherwise) for
``actions/check-target``'s own ``baseline-target`` input, and
``binary_pattern`` sourced from the *referenced library's* own
``binary_pattern`` (never the contract target's, which doesn't have one).

**No build-output paths are carried through.** ``build-output.json`` is
used here purely as an existence/membership oracle ("does this profile's
build actually produce this target"), never as the source of a binary path
to check -- the candidate artifact a real check-project.yml matrix cell
compares is whatever the *current* job's build produced, addressed via each
target's own ``binary_pattern``/``consumer_binary_pattern`` glob (resolved
by the calling workflow, not this module, since resolving a glob against a
live filesystem is I/O this module deliberately stays free of).

Pure: no file I/O, no subprocess. Callers read ``.abicheck.yml`` (via
:func:`~.project_targets.load_project_targets_config`) and each profile's
``build-output.json`` (via :mod:`~.build_output`) themselves and pass the
already-parsed objects in.

**Precondition, not re-checked here:** *config* must already have passed
:func:`~.project_targets.validate_project_targets` with no errors. This
module trusts ``depth``/``gate_mode``/``channel``/references are valid --
the same "parsing alone isn't validation" split ``project_targets.py``'s
own docstring documents for its caller.

**The ``profiles.<id>.compile`` overlay reaches the generated cell** (P1
toolchain-profile audit, closing the gap ``ProfileCompileSpec``'s own
docstring flagged: "no run-plan generator/toolchain resolver lives here
yet"). Each resolved cell whose profile declares a ``compile:`` overlay gets
:attr:`RunPlanCheck.compile_gcc_options` -- a single composed extra-flags
string (``-std=``/``-stdlib=``/``--target=``/``-D<macro>``/``args``, in that
order, space-joined) a caller forwards verbatim as ``check-target``'s
``gcc-options`` input. ``compile.binding`` (a logical id, e.g. ``"gcc14"``)
resolves to :attr:`RunPlanCheck.compile_gcc_path` only when the caller
passes an already-loaded *resolved_bindings* mapping to
:func:`generate_run_plan` (this module stays pure -- loading the trusted
``--toolchain-bindings`` file is the CLI layer's job, same split as
``build_outputs`` above); with no mapping, or a binding id absent from it,
``compile_gcc_path`` stays empty and a caller's own ``gcc-path``/global
fallback applies. ``compiler_family``/``compiler_version`` are deliberately
**not** projected into any forwarded field -- ``compiler_family`` selects a
toolchain only through ``binding`` (there is no separate "pick a family"
invocation flag), and ``compiler_version`` is a *constraint* (e.g.
``">=14.0,<15"``), not a value to pass through; verifying a resolved
binding's actual version against it needs a real toolchain-identity probe
(subprocess), which stays out of this module by design. A P0 audit round
briefly made :func:`_compose_gcc_options` consult ``compiler_family`` to
drop ``-stdlib=``/``--target=`` for GCC-family profiles; a later review
round found that broke real cross-compilation-target correctness for the
direct-clang backend (the composed string is never actually fed to a
literal GCC binary anywhere in this pipeline, only ever to Clang -- see
that function's own docstring), so it was reverted -- a documented gap,
not an oversight.

**The ``profiles.<id>.consumer_compile`` overlay (G34 Phase 0) projects the
same way, into its own separate fields:** :attr:`RunPlanCheck.
consumer_compile_gcc_path`/:attr:`RunPlanCheck.consumer_compile_gcc_options`,
resolved identically to ``compile:``'s own pair but from the profile's
separate consumer-toolchain overlay (see :class:`~.project_targets.
ProfileSpec`'s docstring for the producer/consumer distinction). A profile
with no ``consumer_compile:`` simply leaves both fields empty -- this
module does not fall back to the producer ``compile:`` overlay's own
fields for them, since "no consumer overlay" and "an actual empty overlay"
are meant to look the same to a caller either way. Actually applying these
fields to a separate header-AST (L2) extraction pass, merged with the
producer-toolchain binary facts, is not yet wired here -- this module only
projects the config-schema axis; the extraction/merge integration is
G34 Phase 0's remaining, larger piece.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .build_output import BuildOutput
from .check_report import build_check_id
from .project_targets import (
    TARGET_KIND_LIBRARY,
    BundleSpec,
    CheckSpec,
    ProfileCompileSpec,
    ProjectTargetsConfig,
    TargetSpec,
)

#: Schema discriminator stamped into every ``run-plan.json`` (mirrors
#: ``BUILD_OUTPUT_SCHEMA``'s naming convention).
RUN_PLAN_SCHEMA = "abicheck.run-plan/v1"

#: ``kind`` discriminator for a :class:`RunPlanCheck` cell.
RUN_PLAN_KIND_TARGET = "target"
RUN_PLAN_KIND_BUNDLE = "bundle"


def _opt_str(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, str) and value else default


def _compose_gcc_options(compile_spec: ProfileCompileSpec) -> str:
    """Compose ``compile_spec``'s standard/stdlib/target/abi_macros/args axes
    into one space-joined extra-flags string, forwarded verbatim as
    ``check-target``'s ``gcc-options`` input (P1 toolchain-profile audit).

    Every atom was already whitespace-validated by
    ``ProfileCompileSpec.from_dict`` (``_safe_profile_atom`` -- no argv
    smuggling), so plain space-joining is safe here. A consequence of that
    validation: no atom -- including an ``abi_macros`` value -- may itself
    contain a space, since this function has no further escaping step to
    fall back on. ``abi_macros`` are emitted sorted by name for
    deterministic output; ``args`` are appended verbatim, in declared
    order, last -- the operator's own explicit escape hatch wins over the
    structured axes this function derives flags from.

    **Deliberately not family-aware.** A P0 audit round had this function
    drop ``-stdlib=``/``--target=`` whenever ``compiler_family: gcc`` was
    set, reasoning that a real GCC binary rejects both (true: confirmed
    against GCC 14.2). A later review round found that fix backwards: this
    composed string is *never* fed to a literal GCC binary anywhere in the
    current pipeline -- ``--ast-frontend`` only has ``auto``/``castxml``/
    ``clang``/``hybrid`` (no ``gcc``); castxml's own frontend is always its
    internal bundled Clang (``--castxml-cc-<id>`` selects an *emulation*
    mode, not a literal execution path); and the direct-clang backend's
    ``_resolve_clang_bin`` (``dumper_clang.py``) explicitly *rejects* a
    ``gcc-path`` that doesn't look clang-family and falls back to host
    ``clang``/``clang++`` instead. Since the real consumer is always Clang,
    dropping ``--target=`` actively broke cross-compilation-target
    correctness for the direct-clang backend: it is the *only* signal
    available there to steer parsing away from the host architecture (no
    "probe the real compiler" auto-discovery step exists on that path the
    way castxml has one), so a GCC-family profile with an explicit
    ``target:`` would silently have its headers parsed for the runner's
    architecture instead -- a correctness bug, not merely a redundant flag.
    Reverted; both flags are emitted unconditionally regardless of
    ``compiler_family`` again, same as before that audit. A genuine
    family-specific argv resolver belongs to the toolchain-profile-
    execution-contract work (AGENTS.md's "Known gaps"), where the actual
    consuming frontend is known at composition time -- not a per-flag
    heuristic here that cannot tell which frontend will read its output.
    """
    parts: list[str] = []
    if compile_spec.standard:
        parts.append(f"-std={compile_spec.standard}")
    if compile_spec.stdlib:
        parts.append(f"-stdlib={compile_spec.stdlib}")
    if compile_spec.target:
        parts.append(f"--target={compile_spec.target}")
    for name in sorted(compile_spec.abi_macros):
        value = compile_spec.abi_macros[name]
        parts.append(f"-D{name}={value}" if value else f"-D{name}")
    parts.extend(compile_spec.args)
    return " ".join(parts)


@dataclass
class RunPlanCheck:
    """One resolved ``(target-or-bundle, profile, checks[] entry)`` cell.

    Field names deliberately mirror ``actions/check-target/action.yml``'s
    own input names (``kind``, ``target-kind`` -> ``target_kind``,
    ``baseline-target`` -> ``baseline_target``, ...) so a workflow
    generating a matrix ``include:`` entry from this dict can forward each
    field through with no renaming.
    """

    check_id: str = ""
    kind: str = RUN_PLAN_KIND_TARGET
    #: ``""`` for ``kind: bundle``; else ``library``/``app-consumer``/
    #: ``plugin-contract`` (ADR-047 §3 discriminator).
    target_kind: str = TARGET_KIND_LIBRARY
    #: The target or bundle id -- this check's own reporting identity.
    name: str = ""
    profile_id: str = ""
    baseline_channel: str = ""
    requested_depth: str = ""
    required: bool = True
    gate_mode: str = "local"
    #: Non-empty only for ``target_kind: app-consumer``/``plugin-contract``
    #: -- the referenced ``kind: library`` target's id (ADR-047 §3's
    #: "library redirect"; forwarded as check-target's ``baseline-target``).
    baseline_target: str = ""
    #: The glob pattern a caller resolves against the *current* build's
    #: artifacts to find the candidate binary. For ``target_kind:
    #: app-consumer``/``plugin-contract`` this is the *redirected library's*
    #: pattern, never the contract target's own (it doesn't have one).
    binary_pattern: str = ""
    #: ``target_kind: app-consumer`` only.
    consumer_binary_pattern: str = ""
    #: ``target_kind: plugin-contract`` only.
    contract_file: str = ""
    #: ``kind: bundle`` only -- member target ids.
    bundle_members: list[str] = field(default_factory=list)
    #: ``kind: bundle`` only -- member target id -> that member's own
    #: ``binary_pattern``, so a caller can stage a member-binaries directory
    #: without re-reading ``.abicheck.yml``.
    member_binary_patterns: dict[str, str] = field(default_factory=dict)
    #: This cell's profile's ``compile.binding``, resolved to an exact
    #: executable path (P1 toolchain-profile audit) -- forwarded as
    #: ``check-target``'s ``gcc-path`` input. Empty unless the profile
    #: declares a ``binding`` AND :func:`generate_run_plan` was given a
    #: *resolved_bindings* mapping that contains it.
    compile_gcc_path: str = ""
    #: This cell's profile's ``compile`` overlay, composed into one
    #: extra-flags string (see :func:`_compose_gcc_options`) -- forwarded as
    #: ``check-target``'s ``gcc-options`` input. Empty when the profile has
    #: no ``compile:`` overlay, or the overlay sets none of
    #: ``standard``/``stdlib``/``target``/``abi_macros``/``args``.
    compile_gcc_options: str = ""
    #: This cell's profile's ``consumer_compile.binding`` (G34 Phase 0),
    #: resolved the same way :attr:`compile_gcc_path` is. Empty unless the
    #: profile declares a ``consumer_compile:`` overlay with a ``binding``
    #: AND :func:`generate_run_plan` was given a *resolved_bindings* mapping
    #: that contains it.
    consumer_compile_gcc_path: str = ""
    #: This cell's profile's ``consumer_compile`` overlay, composed the same
    #: way :attr:`compile_gcc_options` is. Empty when the profile has no
    #: ``consumer_compile:`` overlay, or the overlay sets none of
    #: ``standard``/``stdlib``/``target``/``abi_macros``/``args``.
    consumer_compile_gcc_options: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "check_id": self.check_id,
            "kind": self.kind,
            "name": self.name,
            "profile_id": self.profile_id,
            "baseline_channel": self.baseline_channel,
            "requested_depth": self.requested_depth,
            "required": self.required,
            "gate_mode": self.gate_mode,
        }
        if self.kind == RUN_PLAN_KIND_BUNDLE:
            d["bundle_members"] = list(self.bundle_members)
            if self.member_binary_patterns:
                d["member_binary_patterns"] = dict(self.member_binary_patterns)
        else:
            d["target_kind"] = self.target_kind
            if self.baseline_target:
                d["baseline_target"] = self.baseline_target
            if self.binary_pattern:
                d["binary_pattern"] = self.binary_pattern
            if self.consumer_binary_pattern:
                d["consumer_binary_pattern"] = self.consumer_binary_pattern
            if self.contract_file:
                d["contract_file"] = self.contract_file
        if self.compile_gcc_path:
            d["compile_gcc_path"] = self.compile_gcc_path
        if self.compile_gcc_options:
            d["compile_gcc_options"] = self.compile_gcc_options
        if self.consumer_compile_gcc_path:
            d["consumer_compile_gcc_path"] = self.consumer_compile_gcc_path
        if self.consumer_compile_gcc_options:
            d["consumer_compile_gcc_options"] = self.consumer_compile_gcc_options
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunPlanCheck:
        member_patterns_raw = d.get("member_binary_patterns")
        member_patterns = (
            {str(k): str(v) for k, v in member_patterns_raw.items()}
            if isinstance(member_patterns_raw, dict)
            else {}
        )
        return cls(
            check_id=_opt_str(d.get("check_id")),
            kind=_opt_str(d.get("kind"), RUN_PLAN_KIND_TARGET),
            target_kind=_opt_str(d.get("target_kind"), TARGET_KIND_LIBRARY),
            name=_opt_str(d.get("name")),
            profile_id=_opt_str(d.get("profile_id")),
            baseline_channel=_opt_str(d.get("baseline_channel")),
            requested_depth=_opt_str(d.get("requested_depth")),
            required=bool(d.get("required", True)),
            gate_mode=_opt_str(d.get("gate_mode"), "local"),
            baseline_target=_opt_str(d.get("baseline_target")),
            binary_pattern=_opt_str(d.get("binary_pattern")),
            consumer_binary_pattern=_opt_str(d.get("consumer_binary_pattern")),
            contract_file=_opt_str(d.get("contract_file")),
            bundle_members=[
                str(x) for x in (d.get("bundle_members") or []) if isinstance(x, str)
            ],
            member_binary_patterns=member_patterns,
            compile_gcc_path=_opt_str(d.get("compile_gcc_path")),
            compile_gcc_options=_opt_str(d.get("compile_gcc_options")),
            consumer_compile_gcc_path=_opt_str(d.get("consumer_compile_gcc_path")),
            consumer_compile_gcc_options=_opt_str(
                d.get("consumer_compile_gcc_options")
            ),
        )


@dataclass
class RunPlan:
    """The full ordered list of checks a run derives (ADR-047 §5)."""

    schema: str = RUN_PLAN_SCHEMA
    project: str = ""
    head_sha: str = ""
    checks: list[RunPlanCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"schema": self.schema}
        if self.project:
            d["project"] = self.project
        if self.head_sha:
            d["head_sha"] = self.head_sha
        d["checks"] = [c.to_dict() for c in self.checks]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunPlan:
        checks_raw = d.get("checks")
        checks = (
            [RunPlanCheck.from_dict(c) for c in checks_raw if isinstance(c, dict)]
            if isinstance(checks_raw, list)
            else []
        )
        return cls(
            schema=_opt_str(d.get("schema"), RUN_PLAN_SCHEMA),
            project=_opt_str(d.get("project")),
            head_sha=_opt_str(d.get("head_sha")),
            checks=checks,
        )


@dataclass
class RunPlanGenerationReport:
    """Result of :func:`generate_run_plan` (mirrors
    :class:`~.build_output.BuildOutputValidationReport`'s shape)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _resolve_profile_ids(
    check: CheckSpec, config: ProjectTargetsConfig
) -> tuple[list[str], bool]:
    """Returns ``(profile_ids, explicit)``.

    ``explicit`` is ``True`` when *check* named its own ``profiles:``
    selector -- a profile named there that turns out not to build the
    referenced target is a hard error, unlike the implicit "every contract
    profile" sweep, where a non-matching profile is silently skipped
    (that's the whole reason the implicit sweep is safe -- see module
    docstring).
    """
    if check.profiles:
        return list(check.profiles), True
    return [p.id for p in config.profiles.values() if p.contract], False


def _resolved_compile_fields(
    compile_spec: ProfileCompileSpec | None,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(gcc_path, gcc_options)`` for one already-resolved
    :class:`ProfileCompileSpec` (either a profile's ``compile:`` or its
    ``consumer_compile:`` overlay, G34 Phase 0) -- ``("", "")`` when
    *compile_spec* is ``None``."""
    if compile_spec is None:
        return "", ""
    gcc_path = ""
    if compile_spec.binding and resolved_bindings is not None:
        gcc_path = resolved_bindings.get(compile_spec.binding, "")
    return gcc_path, _compose_gcc_options(compile_spec)


def _compile_fields_for_profile(
    config: ProjectTargetsConfig,
    profile_id: str,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(compile_gcc_path, compile_gcc_options)`` for *profile_id*
    (P1 toolchain-profile audit) -- ``("", "")`` when the profile has no
    ``compile:`` overlay, is unknown, or declares no ``binding``/no
    resolvable-flags fields."""
    profile = config.profiles.get(profile_id)
    compile_spec = profile.compile if profile is not None else None
    return _resolved_compile_fields(compile_spec, resolved_bindings)


def _consumer_compile_fields_for_profile(
    config: ProjectTargetsConfig,
    profile_id: str,
    resolved_bindings: Mapping[str, str] | None,
) -> tuple[str, str]:
    """Returns ``(consumer_compile_gcc_path, consumer_compile_gcc_options)``
    for *profile_id* (G34 Phase 0) -- ``("", "")`` when the profile has no
    ``consumer_compile:`` overlay, is unknown, or declares no ``binding``/no
    resolvable-flags fields. Mirrors :func:`_compile_fields_for_profile`
    exactly, resolved from the profile's separate consumer-toolchain overlay
    instead of its producer ``compile:`` block."""
    profile = config.profiles.get(profile_id)
    consumer_compile_spec = profile.consumer_compile if profile is not None else None
    return _resolved_compile_fields(consumer_compile_spec, resolved_bindings)


def _library_lookup_and_pattern(
    config: ProjectTargetsConfig, target: TargetSpec
) -> tuple[str, str]:
    """Returns ``(lookup_id, binary_pattern)`` -- the id to look up in a
    profile's ``build-output.json`` ``targets[]`` and the pattern a caller
    globs for the candidate binary. For ``kind: library`` both come from
    *target* itself; for ``app-consumer``/``plugin-contract`` both are
    redirected through *target*'s own ``library`` field (ADR-047 §3)."""
    if target.kind == TARGET_KIND_LIBRARY:
        return target.id, target.binary_pattern
    referenced = config.targets.get(target.library)
    pattern = referenced.binary_pattern if referenced is not None else ""
    return target.library, pattern


def _generate_target_checks(
    config: ProjectTargetsConfig,
    build_outputs: Mapping[str, BuildOutput],
    target: TargetSpec,
    report: RunPlanGenerationReport,
    resolved_bindings: Mapping[str, str] | None = None,
) -> list[RunPlanCheck]:
    if target.bundle_only:
        # validate_project_targets already forbids a bundle_only target from
        # declaring its own checks:, but this module trusts a pre-validated
        # config per its own docstring rather than re-deriving that rule --
        # skip defensively instead of emitting a check no caller asked for.
        return []
    lookup_id, binary_pattern = _library_lookup_and_pattern(config, target)
    baseline_target = target.library if target.kind != TARGET_KIND_LIBRARY else ""
    out: list[RunPlanCheck] = []
    for check in target.checks:
        profile_ids, explicit = _resolve_profile_ids(check, config)
        for profile_id in profile_ids:
            bo = build_outputs.get(profile_id)
            if bo is None:
                # Distinct from "this profile's build-output.json doesn't
                # build the target" below -- that's the implicit sweep's
                # legitimate "run this check on every profile where it makes
                # sense" skip. A DECLARED contract profile with no
                # build-output.json at all almost always means the caller's
                # build/upload for that profile failed or was misnamed, so a
                # partial plan would silently under-cover it; a hard error
                # either way (explicit or implicit) surfaces that at
                # generation time instead of aggregate quietly passing with
                # an incomplete matrix (Codex review).
                report.errors.append(
                    f"target {target.id!r}: profile {profile_id!r} has no "
                    "build-output.json provided"
                )
                continue
            bo_target = next((t for t in bo.targets if t.id == lookup_id), None)
            if bo_target is None:
                if explicit:
                    report.errors.append(
                        f"target {target.id!r}: profile {profile_id!r}'s "
                        f"build-output.json does not build {lookup_id!r} "
                        "(named explicitly in this check's profiles:)"
                    )
                # Implicit sweep: this profile simply doesn't build the
                # target -- not an error, that's the point of the sweep.
                continue
            check_id = build_check_id(target.id, profile_id, check.channel, check.depth)
            compile_gcc_path, compile_gcc_options = _compile_fields_for_profile(
                config, profile_id, resolved_bindings
            )
            consumer_compile_gcc_path, consumer_compile_gcc_options = (
                _consumer_compile_fields_for_profile(
                    config, profile_id, resolved_bindings
                )
            )
            out.append(
                RunPlanCheck(
                    check_id=check_id,
                    kind=RUN_PLAN_KIND_TARGET,
                    target_kind=target.kind,
                    name=target.id,
                    profile_id=profile_id,
                    baseline_channel=check.channel,
                    requested_depth=check.depth,
                    required=check.required,
                    gate_mode=check.gate_mode,
                    baseline_target=baseline_target,
                    binary_pattern=binary_pattern,
                    consumer_binary_pattern=(
                        target.consumer_binary_pattern
                        if target.kind != TARGET_KIND_LIBRARY
                        else ""
                    ),
                    contract_file=(
                        target.contract_file
                        if target.kind != TARGET_KIND_LIBRARY
                        else ""
                    ),
                    compile_gcc_path=compile_gcc_path,
                    compile_gcc_options=compile_gcc_options,
                    consumer_compile_gcc_path=consumer_compile_gcc_path,
                    consumer_compile_gcc_options=consumer_compile_gcc_options,
                )
            )
    return out


def _generate_bundle_checks(
    config: ProjectTargetsConfig,
    build_outputs: Mapping[str, BuildOutput],
    bundle: BundleSpec,
    report: RunPlanGenerationReport,
    resolved_bindings: Mapping[str, str] | None = None,
) -> list[RunPlanCheck]:
    out: list[RunPlanCheck] = []
    for check in bundle.checks:
        profile_ids, explicit = _resolve_profile_ids(check, config)
        for profile_id in profile_ids:
            # abicheck/bundle.py's build_bundle_snapshot() skips non-ELF
            # inputs outright, so a bundle check against a declared
            # Windows/macOS profile can never resolve (Codex review). An
            # EXPLICIT profiles: entry naming a non-ELF profile is already
            # rejected as a config-validation error by
            # project_targets.validate_project_targets -- this is a
            # defensive backstop for a caller that invokes
            # generate_run_plan() directly without validating first. The
            # IMPLICIT sweep case (no profiles: list -- "every contract
            # profile") is not a misconfiguration to error on, though: not
            # every profile is expected to support bundle checks, the same
            # way a profile that simply doesn't build a given target is
            # silently skipped below rather than flagged.
            profile = config.profiles.get(profile_id)
            if profile is not None and profile.os and profile.os != "linux":
                if explicit:
                    report.errors.append(
                        f"bundle {bundle.id!r}: profile {profile_id!r} has "
                        f"os: {profile.os!r}, but a bundle check's backend is "
                        "ELF-only (named explicitly in this check's profiles:)"
                    )
                continue
            bo = build_outputs.get(profile_id)
            if bo is None:
                # See the identical branch in _generate_target_checks: a
                # DECLARED contract profile with no build-output.json at all
                # is always a hard error, distinct from the "doesn't build
                # this bundle's members" skip below (Codex review).
                report.errors.append(
                    f"bundle {bundle.id!r}: profile {profile_id!r} has no "
                    "build-output.json provided"
                )
                continue
            bo_target_ids = {t.id for t in bo.targets}
            missing = [m for m in bundle.targets if m not in bo_target_ids]
            if missing:
                if explicit:
                    report.errors.append(
                        f"bundle {bundle.id!r}: profile {profile_id!r}'s "
                        f"build-output.json is missing member(s) {missing} "
                        "(named explicitly in this check's profiles:)"
                    )
                continue
            check_id = build_check_id(bundle.id, profile_id, check.channel, check.depth)
            member_patterns = {
                member: config.targets[member].binary_pattern
                for member in bundle.targets
                if member in config.targets
            }
            compile_gcc_path, compile_gcc_options = _compile_fields_for_profile(
                config, profile_id, resolved_bindings
            )
            consumer_compile_gcc_path, consumer_compile_gcc_options = (
                _consumer_compile_fields_for_profile(
                    config, profile_id, resolved_bindings
                )
            )
            out.append(
                RunPlanCheck(
                    check_id=check_id,
                    kind=RUN_PLAN_KIND_BUNDLE,
                    target_kind="",
                    name=bundle.id,
                    profile_id=profile_id,
                    baseline_channel=check.channel,
                    requested_depth=check.depth,
                    required=check.required,
                    gate_mode=check.gate_mode,
                    bundle_members=list(bundle.targets),
                    member_binary_patterns=member_patterns,
                    compile_gcc_path=compile_gcc_path,
                    compile_gcc_options=compile_gcc_options,
                    consumer_compile_gcc_path=consumer_compile_gcc_path,
                    consumer_compile_gcc_options=consumer_compile_gcc_options,
                )
            )
    return out


def generate_run_plan(
    config: ProjectTargetsConfig,
    build_outputs: Mapping[str, BuildOutput],
    *,
    project: str = "",
    head_sha: str = "",
    resolved_bindings: Mapping[str, str] | None = None,
) -> tuple[RunPlan, RunPlanGenerationReport]:
    """Derive the ordered :class:`RunPlan` from *config* + each contract
    profile's parsed ``build-output.json`` (keyed by profile id).

    *resolved_bindings* (P1 toolchain-profile audit) is an optional
    already-loaded ``{binding_id: executable_path}`` mapping -- typically a
    trusted ``toolchain_bindings.BindingsFile.bindings`` the caller loaded
    itself (this module stays pure, no file I/O) -- used to resolve each
    cell's profile's ``compile.binding`` into
    :attr:`RunPlanCheck.compile_gcc_path`. With no mapping (the default), or
    a binding id absent from it, that field stays empty and a caller's own
    ``gcc-path``/global fallback applies; this is never an error at this
    layer (the CLI wrapper's ``check_profile_bindings_resolve`` step is
    where an operator opted into strict resolution surfaces one).

    Never raises for a structurally valid, pre-validated *config* --
    coverage gaps are reported via the returned
    :class:`RunPlanGenerationReport`, matching
    :func:`~.build_output.validate_build_output`'s/
    :func:`~.project_targets.validate_project_targets`'s own contract. A
    caller that wants a hard failure on any error should check
    ``report.ok`` itself (the CLI wrapper does).
    """
    report = RunPlanGenerationReport()
    checks: list[RunPlanCheck] = []
    for target in config.targets.values():
        checks.extend(
            _generate_target_checks(
                config, build_outputs, target, report, resolved_bindings
            )
        )
    for bundle in config.bundles.values():
        checks.extend(
            _generate_bundle_checks(
                config, build_outputs, bundle, report, resolved_bindings
            )
        )
    # check_id (target@profile#baseline_channel@depth) is the id
    # to_aggregate_manifest() projects into aggregate --manifest's targets[]
    # -- ExpectedTargets.from_manifest_data() rejects a duplicate id there.
    # Two checks[] entries on the same target/bundle that resolve to the
    # same (profile, channel, depth) -- e.g. differing only in required:/
    # gate_mode:, neither of which is part of check_id -- would otherwise
    # only surface as that late aggregate-projection failure, after every
    # matrix cell already ran (Codex review). Catch it here instead.
    seen_ids: dict[str, int] = {}
    for check in checks:
        seen_ids[check.check_id] = seen_ids.get(check.check_id, 0) + 1
    duplicate_ids = sorted(cid for cid, count in seen_ids.items() if count > 1)
    for check_id in duplicate_ids:
        report.errors.append(
            f"check_id {check_id!r} is generated by more than one checks[] "
            "entry -- two checks[] entries (on the same target/bundle, or "
            "an explicit profiles: selector repeating a profile) resolved "
            "to the same (profile, baseline_channel, requested_depth), "
            "which aggregate's own manifest requires to be unique. Remove "
            "the duplicate checks[] entry, or give it a distinct channel/"
            "depth/profile."
        )
    if not checks and report.ok:
        report.warnings.append(
            "run-plan is empty -- no targets:/bundles: checks[] resolved to any "
            "profile (nothing declared, or every profile is missing from "
            "build_outputs)."
        )
    plan = RunPlan(project=project, head_sha=head_sha, checks=checks)
    return plan, report


def to_aggregate_manifest(
    plan: RunPlan, *, head_sha: str | None = None
) -> dict[str, Any]:
    """Project a :class:`RunPlan` down to ``abicheck aggregate --manifest``'s
    ``{"targets": [{"id", "required"}]}`` wire shape (ADR-047 §5's required
    sub-task).

    Uses each check's own :attr:`RunPlanCheck.check_id` (``target@profile#
    baseline_channel@depth``) as ``targets[].id``, never the bare target/
    bundle name -- ``abicheck/aggregate.py``'s manifest matching is an exact
    string comparison against each report's own ``target_id``, which
    ``actions/check-target`` (G30 P1.3) always writes as the identical
    ``check_id``-shaped string. Projecting to a bare name here would collide
    S17/S21's multi-profile/multi-channel same-target checks against each
    other in ``aggregate``'s duplicate-target-id check.
    """
    from ..aggregate import AGGREGATE_MANIFEST_VERSION

    manifest: dict[str, Any] = {
        "aggregate_manifest_version": AGGREGATE_MANIFEST_VERSION,
        "targets": [{"id": c.check_id, "required": c.required} for c in plan.checks],
    }
    resolved_head_sha = head_sha if head_sha is not None else plan.head_sha
    if resolved_head_sha:
        manifest["head_sha"] = resolved_head_sha
    return manifest

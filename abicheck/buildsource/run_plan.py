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
(subprocess), which stays out of this module by design. See AGENTS.md's
"Toolchain-profile compiler-family rendering" entry and
:func:`_compose_gcc_options`'s own docstring for why that function does
*not* special-case ``compiler_family`` -- a P0 audit round tried and
reverted it after finding it broke real cross-compilation for the
direct-clang backend.

**The ``profiles.<id>.consumer_compile`` overlay (G34 Phase 0) projects the
same way, into its own separate fields:** :attr:`RunPlanCheck.
consumer_compile_gcc_path`/:attr:`RunPlanCheck.consumer_compile_gcc_options`,
resolved from the profile's separate consumer-toolchain overlay (see
:class:`~.project_targets.ProfileSpec`'s docstring for the producer/
consumer split), with no fallback to the producer ``compile:`` overlay's
own fields when absent. The native ``check-project`` caller applies these
to a separate candidate dump: same producer binary, headers reparsed
under the consumer context, and that snapshot is what gets compared.

**Known gap:** only the candidate side gets this treatment --
``publish-baseline.yml``/``update-main-baseline.yml`` never apply a
``consumer_compile:`` overlay to the baseline side; see
``docs/reference/project-targets-schema.md`` and the G34 plan's Phase 0.

**``compile.frontend``/``consumer_compile.frontend`` (G34 Phase B) project
the same way**, into :attr:`RunPlanCheck.compile_ast_frontend`/
:attr:`RunPlanCheck.consumer_compile_ast_frontend` -- one of the same four
values the global ``--ast-frontend`` flag accepts, resolved independently
per overlay, with no fallback from one overlay's field to the other's.

:attr:`~RunPlanCheck.compile_ast_frontend` is threaded through as
``matrix.compile_ast_frontend || inputs.ast-frontend``, the same
per-cell-first precedence :attr:`~RunPlanCheck.compile_gcc_path` uses.
:attr:`~RunPlanCheck.consumer_compile_ast_frontend` is forwarded to the
separate consumer-context candidate dump, never onto the producer-context
comparison invocation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .build_output import BuildOutput
from .check_report import build_check_id
from .project_targets import (
    DEFAULT_PROFILE_RUNNER_LABEL,
    TARGET_KIND_LIBRARY,
    BundleSpec,
    CheckSpec,
    ProjectTargetsConfig,
    TargetSpec,
)

# _compose_gcc_options/_scheduling_fields_for_profile are re-exported into
# this module's own namespace (not just used internally) -- see
# run_plan_profile_fields.py's own module docstring for why this split
# exists and why every pre-existing `from .run_plan import _compose_gcc_
# options, ...` call site (this package's own tests included) still works.
from .run_plan_profile_fields import (  # noqa: F401
    _compile_ast_frontend_for_profile,
    _compile_fields_for_profile,
    _compose_gcc_options,
    _consumer_compile_active_for_profile,
    _consumer_compile_ast_frontend_for_profile,
    _consumer_compile_fields_for_profile,
    _scheduling_fields_for_profile,
)

#: Schema discriminator stamped into every ``run-plan.json`` (mirrors
#: ``BUILD_OUTPUT_SCHEMA``'s naming convention).
RUN_PLAN_SCHEMA = "abicheck.run-plan/v1"

#: Schema discriminator stamped instead of :data:`RUN_PLAN_SCHEMA` whenever a
#: plan carries a ``gate`` block (CLI cleanup phase two, PR 2 continuation).
#: Mirrors ``AGGREGATE_MANIFEST_VERSION``'s MAJOR-bump reasoning exactly: a
#: plan generated with an explicit gate policy must declare a schema an old,
#: pre-gate reader is guaranteed to reject, rather than one it silently
#: accepts and misreads (Codex review, fresh evidence -- an earlier revision
#: left every plan stamped ``v1`` regardless of whether ``gate`` was
#: present, so an old ``RunPlan.from_dict()`` would ignore the unknown key
#: and project a ``1.0`` aggregate manifest applying the hard-coded default
#: policy instead of what the plan actually asked for, silently). A plan
#: with no gate policy keeps the unchanged ``v1`` spelling -- this bump is
#: additive-only, scoped to the one new capability, not a blanket
#: version-everything policy.
RUN_PLAN_SCHEMA_GATE = "abicheck.run-plan/v2"

#: Highest ``vN`` suffix this reader understands, parsed from either schema
#: constant above.
_RUN_PLAN_SCHEMA_MAX_SUPPORTED = 2

#: ``kind`` discriminator for a :class:`RunPlanCheck` cell.
RUN_PLAN_KIND_TARGET = "target"
RUN_PLAN_KIND_BUNDLE = "bundle"


def _opt_str(value: Any, default: str = "") -> str:
    return str(value) if isinstance(value, str) and value else default


def _run_plan_schema_version(schema: str) -> int | None:
    """Parse the trailing ``vN`` off a ``run-plan.json`` ``schema`` string.

    ``None`` for anything not of the ``"abicheck.run-plan/vN"`` shape --
    callers treat that the same as "no version to check" (an unrecognized
    schema string is a separate, pre-existing problem this function doesn't
    try to diagnose).
    """
    prefix = "abicheck.run-plan/v"
    if not schema.startswith(prefix):
        return None
    suffix = schema[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


def _parse_run_plan_gate(d: dict[str, Any]) -> tuple[str | None, str | None]:
    """Validate a ``run-plan.json`` top-level ``gate`` block.

    The key absent -> ``(None, None)``, same as everywhere else in this
    module. Present but malformed -- not an object, an unknown key, or
    a value outside :class:`~abicheck.aggregate_manifest.OnMissingRequired`/
    :class:`~abicheck.aggregate_manifest.OnUnexpectedTarget` -- is a loud
    :class:`~abicheck.aggregate_manifest.AggregateError`, not a silent
    coercion to "no gate" (Codex review, fresh evidence: an earlier revision
    treated any non-dict/malformed ``gate`` the same as an absent one, so a
    corrupted or hand-authored v2 plan's requested policy could be silently
    discarded and `aggregate` would fall back to the hard-coded defaults --
    potentially reversing the requested CI outcome instead of failing loud).
    Mirrors :func:`abicheck.aggregate_manifest._parse_manifest_gate`'s own
    key/value validation exactly, kept as a separate function here (not a
    shared call) since that function's own version check is shaped for the
    manifest's ``MAJOR.MINOR`` scheme, not this module's ``vN`` one -- the
    two callers already do their own, differently-shaped version checks.

    Takes the whole top-level mapping (not a pre-extracted ``gate`` value)
    so it can distinguish the key being absent from it being explicitly
    present with a JSON ``null`` -- a plain ``.get("gate")`` on the caller's
    side would conflate the two, and an explicit ``"gate": null`` (or a
    sub-key like ``"missing_required": null``) is rejected outright rather
    than silently treated the same as "not specified" (Codex review, fresh
    evidence -- the same conflation the sibling manifest-side fix closes).
    """
    if "gate" not in d:
        return None, None
    gate_raw = d["gate"]
    from ..workflows.aggregate import (
        AggregateError,
        OnMissingRequired,
        OnUnexpectedTarget,
    )

    if gate_raw is None:
        raise AggregateError("run-plan 'gate' must not be null")
    if not isinstance(gate_raw, dict):
        raise AggregateError("run-plan 'gate' must be an object")
    unknown = sorted(set(gate_raw) - {"missing_required", "unexpected_target"})
    if unknown:
        raise AggregateError(f"run-plan 'gate': unknown key(s) {unknown!r}")
    missing_required: str | None = None
    if "missing_required" in gate_raw:
        mr_raw = gate_raw["missing_required"]
        if mr_raw is None:
            raise AggregateError("run-plan 'gate.missing_required' must not be null")
        try:
            missing_required = OnMissingRequired(mr_raw).value
        except ValueError as exc:
            raise AggregateError(
                f"run-plan 'gate.missing_required' {mr_raw!r} must be one of "
                f"{[v.value for v in OnMissingRequired]}"
            ) from exc
    unexpected_target: str | None = None
    if "unexpected_target" in gate_raw:
        ut_raw = gate_raw["unexpected_target"]
        if ut_raw is None:
            raise AggregateError("run-plan 'gate.unexpected_target' must not be null")
        try:
            unexpected_target = OnUnexpectedTarget(ut_raw).value
        except ValueError as exc:
            raise AggregateError(
                f"run-plan 'gate.unexpected_target' {ut_raw!r} must be one "
                f"of {[v.value for v in OnUnexpectedTarget]}"
            ) from exc
    return missing_required, unexpected_target


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
    #: This target's ``public_headers:`` (``TargetSpec.public_headers``),
    #: newline-joined to match ``action/run.sh``'s ``add_flag()`` multi-value
    #: input convention (ADR-047's own worked example declares this per
    #: target, but nothing downstream read it until this field existed --
    #: see ``docs/reference/reusable-workflows.md``'s "Shared analysis
    #: options" section for the per-cell-override precedent this follows,
    #: identical in shape to :attr:`compile_ast_frontend`). Newline-joined
    #: rather than space-joined (Codex review, fresh evidence) -- a
    #: space-joined value put a declared header root containing whitespace
    #: (e.g. a Windows SDK path under ``Program Files``) through
    #: ``add_flag()``'s single-line legacy branch, which splits on IFS
    #: whitespace and silently produced two malformed ``--header`` operands
    #: instead of one; ``add_flag()`` treats a multi-line value as
    #: already-tokenized (one full, space-safe item per line), the same
    #: convention every other multi-value Action input (``old-header``,
    #: ``new-header``, …) already relies on. Empty when the target declares
    #: no ``public_headers:`` (a caller then falls back to its own
    #: workflow-global ``header`` input, unchanged from before this field
    #: existed). ``kind: bundle`` cells never set this -- see
    #: ``BUNDLE_CHECK_DEPTHS``'s own docstring in ``project_targets.py`` for
    #: why per-bundle-member header staging doesn't exist yet.
    header: str = ""
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
    #: This cell's profile's ``compile.frontend`` (G34 Phase B) -- one of
    #: ``auto``/``castxml``/``clang``/``hybrid``, overriding the global
    #: ``--ast-frontend`` default for this profile's cell only. Empty when
    #: the profile's ``compile:`` overlay sets no ``frontend`` (a caller
    #: then falls back to its own global ``--ast-frontend``/default).
    compile_ast_frontend: str = ""
    #: This cell's profile's ``consumer_compile.frontend`` (G34 Phase B),
    #: resolved the same way :attr:`compile_ast_frontend` is, from the
    #: separate consumer-toolchain overlay (G34 Phase 0) -- never falls
    #: back to :attr:`compile_ast_frontend` when absent.
    consumer_compile_ast_frontend: str = ""
    #: The GitHub-hosted runner this cell must be scheduled on, derived from
    #: its profile's ``os:`` (G34 Phase C). Always populated -- a profile
    #: with no ``os:`` resolves to ``ubuntu-latest``, which is what every
    #: cell hardcoded before this phase, so an existing project's plan is
    #: unmoved. Unlike every other field here it is emitted even at its
    #: default, because ``check-project.yml`` reads it as
    #: ``matrix.runs_on``: a matrix entry silently missing the key would
    #: schedule nothing rather than fall back.
    runs_on: str = DEFAULT_PROFILE_RUNNER_LABEL
    #: This cell's profile's ``dependency_source:`` (G34 Phase C) --
    #: forwarded as ``check-target``'s own ``dependency-source`` input so a
    #: GCC-profile cell and a Clang-profile cell in one run each provision a
    #: matching toolchain. Empty when the profile declares none, which lets
    #: the caller's workflow-level default stand.
    dependency_source: str = ""
    #: This cell's ``checks[].allow_new_target`` (``CheckSpec.
    #: allow_new_target``), forwarded as ``check-target``'s own
    #: ``allow-new-target`` input. ``False`` for every ``kind: bundle`` cell
    #: (``_generate_bundle_checks`` never sets it -- see
    #: ``CheckSpec.allow_new_target``'s own docstring for why a bundle check
    #: can never support this lifecycle state).
    allow_new_target: bool = False
    #: Whether the profile declares a non-empty ``consumer_compile:``
    #: overlay -- gates the fallback for the three fields above.
    consumer_compile_active: bool = False

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
            if self.header:
                d["header"] = self.header
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
        if self.compile_ast_frontend:
            d["compile_ast_frontend"] = self.compile_ast_frontend
        if self.consumer_compile_ast_frontend:
            d["consumer_compile_ast_frontend"] = self.consumer_compile_ast_frontend
        # Unconditional -- see the field's own comment: this one is read as a
        # matrix key, where "absent" means "schedule on nothing".
        d["runs_on"] = self.runs_on
        if self.dependency_source:
            d["dependency_source"] = self.dependency_source
        if self.allow_new_target:
            d["allow_new_target"] = True
        if self.consumer_compile_active:
            d["consumer_compile_active"] = True
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
            header=_opt_str(d.get("header")),
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
            compile_ast_frontend=_opt_str(d.get("compile_ast_frontend")),
            consumer_compile_ast_frontend=_opt_str(
                d.get("consumer_compile_ast_frontend")
            ),
            runs_on=_opt_str(d.get("runs_on"), DEFAULT_PROFILE_RUNNER_LABEL),
            dependency_source=_opt_str(d.get("dependency_source")),
            allow_new_target=bool(d.get("allow_new_target", False)),
            consumer_compile_active=bool(d.get("consumer_compile_active", False)),
        )


@dataclass
class RunPlan:
    """The full ordered list of checks a run derives (ADR-047 §5)."""

    schema: str = RUN_PLAN_SCHEMA
    project: str = ""
    head_sha: str = ""
    checks: list[RunPlanCheck] = field(default_factory=list)
    #: The aggregate fan-in's gate policy (CLI cleanup phase two, PR 2),
    #: carried on the plan so `to_aggregate_manifest()` can project it into
    #: the manifest's own `gate` block -- the same mechanism a hand-authored
    #: `--manifest` uses, so `--run-plan`/`--manifest` never diverge in what
    #: they can express. Raw, unvalidated strings here (validated once, at
    #: `ExpectedTargets.from_manifest_data()`, the same place a hand-authored
    #: manifest's `gate` block is validated) -- this module stays free of an
    #: `..aggregate` import for anything but the manifest version constant.
    #: `None` (the default) means "the run-plan generator wasn't given an
    #: explicit gate policy", not "apply a specific value" -- omitted from
    #: the projected manifest entirely, same as an unset field anywhere else
    #: in this dataclass.
    gate_missing_required: str | None = None
    gate_unexpected_target: str | None = None

    def _validated_gate(self) -> tuple[str | None, str | None]:
        """Validate :attr:`gate_missing_required`/:attr:`gate_unexpected_target`
        against the same enum vocabulary :func:`_parse_run_plan_gate`
        enforces on read, so a direct construction (``RunPlan(...,
        gate_missing_required="bogus")``) cannot serialize an artifact this
        tool's own reader would reject (CodeRabbit review, fresh evidence --
        validation previously only ran on the read path, so a hand-built
        plan's bad value reached disk unchecked and only failed later, on
        whatever consumer read it back)."""
        from ..workflows.aggregate import (
            AggregateError,
            OnMissingRequired,
            OnUnexpectedTarget,
        )

        if self.gate_missing_required is not None:
            try:
                OnMissingRequired(self.gate_missing_required)
            except ValueError as exc:
                raise AggregateError(
                    f"RunPlan.gate_missing_required {self.gate_missing_required!r} "
                    f"must be one of {[v.value for v in OnMissingRequired]}"
                ) from exc
        if self.gate_unexpected_target is not None:
            try:
                OnUnexpectedTarget(self.gate_unexpected_target)
            except ValueError as exc:
                raise AggregateError(
                    f"RunPlan.gate_unexpected_target {self.gate_unexpected_target!r} "
                    f"must be one of {[v.value for v in OnUnexpectedTarget]}"
                ) from exc
        return self.gate_missing_required, self.gate_unexpected_target

    def to_dict(self) -> dict[str, Any]:
        gate_missing_required, gate_unexpected_target = self._validated_gate()
        has_gate = (
            gate_missing_required is not None or gate_unexpected_target is not None
        )
        # The gate-bearing schema is always stamped when a gate policy is
        # set, regardless of whatever self.schema was constructed with --
        # this is a discriminator an old reader must see, not a caller-
        # overridable label (see RUN_PLAN_SCHEMA_GATE's own docstring).
        d: dict[str, Any] = {
            "schema": RUN_PLAN_SCHEMA_GATE if has_gate else self.schema
        }
        if self.project:
            d["project"] = self.project
        if self.head_sha:
            d["head_sha"] = self.head_sha
        if has_gate:
            gate: dict[str, Any] = {}
            if gate_missing_required is not None:
                gate["missing_required"] = gate_missing_required
            if gate_unexpected_target is not None:
                gate["unexpected_target"] = gate_unexpected_target
            d["gate"] = gate
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
        schema = _opt_str(d.get("schema"), RUN_PLAN_SCHEMA)
        version = _run_plan_schema_version(schema)
        if version is not None and version > _RUN_PLAN_SCHEMA_MAX_SUPPORTED:
            from ..workflows.aggregate import AggregateError

            raise AggregateError(
                f"run-plan 'schema' {schema!r} is newer than this tool "
                f"supports (max v{_RUN_PLAN_SCHEMA_MAX_SUPPORTED}); upgrade "
                "abicheck"
            )
        gate_missing_required, gate_unexpected_target = _parse_run_plan_gate(d)
        if "gate" in d and (version is None or version < 2):
            from ..workflows.aggregate import AggregateError

            raise AggregateError(
                "run-plan 'gate' requires 'schema' >= 'abicheck.run-plan/v2' "
                f"(got {schema!r}); a pre-v2 reader would silently ignore "
                "this block and apply the hard-coded default policy instead "
                "of what it asked for"
            )
        return cls(
            schema=schema,
            project=_opt_str(d.get("project")),
            head_sha=_opt_str(d.get("head_sha")),
            checks=checks,
            gate_missing_required=gate_missing_required,
            gate_unexpected_target=gate_unexpected_target,
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


def _newline_join_headers(headers: list[str]) -> str:
    """Newline-join *headers* for ``action/run.sh``'s ``add_flag()`` multi-
    value convention (``RunPlanCheck.header``'s own docstring).

    A single-element list needs special handling (Codex review, fresh
    evidence): ``"\\n".join([x])`` is just ``x`` with no internal separator,
    so ``add_flag()``'s ``[[ "$value" == *$'\\n'* ]]`` newline check reads
    false and it falls through to the legacy branch that splits on IFS
    whitespace -- exactly the whitespace-mis-splitting bug the newline-join
    fix was meant to close, for the one-element case specifically. A
    trailing newline forces the multi-line branch without changing what any
    *multi*-element join already produces (no existing caller reads a
    trailing newline off a 2+-element ``header`` value)."""
    if not headers:
        return ""
    joined = "\n".join(headers)
    if len(headers) == 1:
        joined += "\n"
    return joined


def _library_lookup_and_pattern(
    config: ProjectTargetsConfig, target: TargetSpec
) -> tuple[str, str, str]:
    """Returns ``(lookup_id, binary_pattern, header)`` -- the id to look up
    in a profile's ``build-output.json`` ``targets[]``, the pattern a caller
    globs for the candidate binary, and the newline-joined ``public_headers:``
    to forward as ``check-target``'s own ``header`` input (RunPlanCheck.
    header's own docstring -- newline-joined, not space-joined, so a header
    root containing whitespace survives ``action/run.sh``'s ``add_flag()``
    intact). For ``kind: library`` all three come from *target* itself; for
    ``app-consumer``/``plugin-contract`` all three are redirected through
    *target*'s own ``library`` field (ADR-047 §3) -- an app-consumer/
    plugin-contract target carries no ``public_headers:`` of its own
    (``TargetSpec.to_dict()`` only ever emits that key for ``kind:
    library``), so its header scoping is necessarily the redirected
    library's."""
    if target.kind == TARGET_KIND_LIBRARY:
        return (
            target.id,
            target.binary_pattern,
            _newline_join_headers(target.public_headers),
        )
    referenced = config.targets.get(target.library)
    if referenced is None:
        return target.library, "", ""
    return (
        target.library,
        referenced.binary_pattern,
        _newline_join_headers(referenced.public_headers),
    )


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
    lookup_id, binary_pattern, header = _library_lookup_and_pattern(config, target)
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
            runs_on, dependency_source = _scheduling_fields_for_profile(
                config, profile_id
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
                    header=header,
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
                    compile_ast_frontend=_compile_ast_frontend_for_profile(
                        config, profile_id
                    ),
                    consumer_compile_ast_frontend=(
                        _consumer_compile_ast_frontend_for_profile(config, profile_id)
                    ),
                    consumer_compile_active=_consumer_compile_active_for_profile(
                        config, profile_id
                    ),
                    runs_on=runs_on,
                    dependency_source=dependency_source,
                    allow_new_target=check.allow_new_target,
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
            runs_on, dependency_source = _scheduling_fields_for_profile(
                config, profile_id
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
                    compile_ast_frontend=_compile_ast_frontend_for_profile(
                        config, profile_id
                    ),
                    consumer_compile_ast_frontend=(
                        _consumer_compile_ast_frontend_for_profile(config, profile_id)
                    ),
                    consumer_compile_active=_consumer_compile_active_for_profile(
                        config, profile_id
                    ),
                    runs_on=runs_on,
                    dependency_source=dependency_source,
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
    gate_missing_required: str | None = None,
    gate_unexpected_target: str | None = None,
) -> tuple[RunPlan, RunPlanGenerationReport]:
    """Derive the ordered :class:`RunPlan` from *config* + each contract
    profile's parsed ``build-output.json`` (keyed by profile id).

    *gate_missing_required*/*gate_unexpected_target* (CLI cleanup phase two,
    PR 2) are stamped onto the returned plan unvalidated -- this module has
    no dependency on ``..aggregate``'s ``OnMissingRequired``/
    ``OnUnexpectedTarget`` enums, so an invalid value surfaces once, at
    ``ExpectedTargets.from_manifest_data()``, the same place a hand-authored
    manifest's own ``gate`` block is validated.

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
    plan = RunPlan(
        project=project,
        head_sha=head_sha,
        checks=checks,
        gate_missing_required=gate_missing_required,
        gate_unexpected_target=gate_unexpected_target,
    )
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
    from ..workflows.aggregate import AGGREGATE_MANIFEST_VERSION

    manifest: dict[str, Any] = {
        "aggregate_manifest_version": AGGREGATE_MANIFEST_VERSION,
        "targets": [{"id": c.check_id, "required": c.required} for c in plan.checks],
    }
    resolved_head_sha = head_sha if head_sha is not None else plan.head_sha
    if resolved_head_sha:
        manifest["head_sha"] = resolved_head_sha
    # CLI cleanup phase two, PR 2: project the plan's own gate policy into
    # the manifest's `gate` block -- the same field `--manifest` reads,
    # so `--run-plan`/`--manifest` express identical policy shapes.
    # _validated_gate() rejects a bogus value the same way to_dict() does
    # (CodeRabbit review, fresh evidence) -- both persistence paths off one
    # RunPlan must agree on whether its gate fields are well-formed, not
    # just the JSON serialization one.
    gate_missing_required, gate_unexpected_target = plan._validated_gate()
    if gate_missing_required is not None or gate_unexpected_target is not None:
        gate: dict[str, Any] = {}
        if gate_missing_required is not None:
            gate["missing_required"] = gate_missing_required
        if gate_unexpected_target is not None:
            gate["unexpected_target"] = gate_unexpected_target
        manifest["gate"] = gate
    return manifest

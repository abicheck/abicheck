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
"""Cross-reference and kind-specific validation for the ``targets:``/
``bundles:``/``profiles:``/``baseline:`` block (ADR-047 §3, G30 P1.5).

:func:`validate_project_targets` reports problems in an already-parsed
:class:`~abicheck.buildsource.project_targets.ProjectTargetsConfig` rather
than raising, so ``project validate`` and the run-plan generator can present
every error at once. Structural/type errors are raised earlier, by the
parser in :mod:`abicheck.buildsource.project_targets`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .analysis_assurance_gate import analysis_assurance_issues
from .project_profile_routing import runner_label_for_os, unroutable_os_message
from .project_targets import (
    BUNDLE_CHECK_DEPTHS,
    CHECK_DEPTHS,
    GATE_MODES,
    NO_BASELINE_CHANNEL,
    TARGET_KIND_APP_CONSUMER,
    TARGET_KIND_LIBRARY,
    TARGET_KIND_PLUGIN_CONTRACT,
    BaselineChannelSpec,
    BundleSpec,
    CheckSpec,
    ProfileSpec,
    ProjectTargetsConfig,
    TargetSpec,
)

#: The identifier charset every target/bundle/profile/channel id must satisfy
#: — matches the per-component pattern the report-identity envelope (ADR-047
#: §7, ``compare_report.schema.json``'s ``check_id``) already enforces for
#: ``target@profile#baseline_channel@depth``, so a name valid here can never
#: produce an ambiguous/unparseable check_id downstream. ``\Z``, not a
#: trailing ``$`` -- without ``re.MULTILINE``, ``$`` also matches just
#: before a trailing ``\n`` (a common PyYAML block-scalar artifact), which
#: would let an embedded newline through into a generated ``check_id`` and
#: silently break ``GITHUB_OUTPUT`` downstream (Codex review).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")


@dataclass
class ProjectTargetsValidationReport:
    """Result of :func:`validate_project_targets` (mirrors
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


def _identifier_issues(kind: str, name: str) -> list[str]:
    if not _IDENTIFIER_RE.match(name):
        return [
            f"{kind} id {name!r} is not a valid identifier — must match "
            f"{_IDENTIFIER_RE.pattern!r} (the same charset "
            "check_id components require, so every id stays embeddable in a "
            "target@profile#baseline_channel@depth string without ambiguity)."
        ]
    return []


#: Every kind-specific "content" field a ``targets:`` entry can carry
#: (excludes ``kind``/``checks``, which every kind allows).
_ALL_KIND_FIELDS = frozenset(
    {
        "binary_pattern",
        "public_headers",
        "bundle",
        "bundle_only",
        "consumer_binary_pattern",
        "library",
        "contract_file",
    }
)
#: Which of `_ALL_KIND_FIELDS` each ``kind`` allows — the complement is each
#: kind's forbidden set, so a newly-added field is automatically forbidden
#: everywhere it isn't explicitly allowed (CodeRabbit review: a partial,
#: hand-maintained forbidden list previously let e.g. a `kind: library`
#: target silently set `library:`, or an `app-consumer` silently set
#: `bundle:`/`bundle_only:`, neither of which means anything for those kinds).
_KIND_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    TARGET_KIND_LIBRARY: frozenset(
        {"binary_pattern", "public_headers", "bundle", "bundle_only"}
    ),
    TARGET_KIND_APP_CONSUMER: frozenset({"consumer_binary_pattern", "library"}),
    TARGET_KIND_PLUGIN_CONTRACT: frozenset({"contract_file", "library"}),
}


def _forbidden_field_issues(target: TargetSpec) -> list[str]:
    allowed = _KIND_ALLOWED_FIELDS.get(target.kind, frozenset())
    issues: list[str] = []
    for name in sorted(_ALL_KIND_FIELDS - allowed):
        if getattr(target, name):
            issues.append(
                f"target {target.id!r}: kind: {target.kind} must not set {name}."
            )
    return issues


def _library_target_issues(
    config: ProjectTargetsConfig, target: TargetSpec
) -> list[str]:
    """Validate a ``kind: library`` target's own required/consistent fields."""
    issues: list[str] = []
    if not target.binary_pattern:
        issues.append(f"target {target.id!r}: kind: library requires binary_pattern.")
    if target.bundle_only and not target.bundle:
        issues.append(f"target {target.id!r}: bundle_only requires bundle to be set.")
    if target.bundle_only and target.checks:
        issues.append(
            f"target {target.id!r}: bundle_only: true target must not set "
            "its own checks: — it is checked only as a bundle member, "
            "never standalone, so a target-level check here would never "
            "run; declare it under bundles:.checks instead."
        )
    if not target.bundle:
        return issues
    declared_bundle = config.bundles.get(target.bundle)
    if declared_bundle is None:
        issues.append(
            f"target {target.id!r}: bundle {target.bundle!r} is not "
            "declared under bundles:."
        )
    elif target.id not in declared_bundle.targets:
        issues.append(
            f"target {target.id!r}: declares bundle: {target.bundle!r} "
            f"but bundles.{target.bundle}.targets does not list "
            f"{target.id!r} back — a target's own bundle: field and its "
            "membership in that bundle's targets: list must agree in "
            "both directions."
        )
    return issues


def _no_baseline_channel_issues(target: TargetSpec) -> list[str]:
    """Reject ``channel: none`` on any target kind other than ``library``.

    ``actions/check-target/validate-inputs.sh`` rejects ``baseline-channel:
    none`` for any target kind other than library -- a no-baseline audit routes
    to ``scan`` (a one-build check), which has no ``--used-by``/
    ``consumers.required_symbols`` equivalent to scope an app-consumer/plugin-contract
    check against. Rejected at generation time rather than letting a
    validated-looking config produce a run-plan cell that check-target refuses
    with no per-cell report for aggregate to read.
    """
    if target.kind == TARGET_KIND_LIBRARY:
        return []
    return [
        f"target {target.id!r}.checks[{i}]: channel: "
        f"{NO_BASELINE_CHANNEL!r} is not supported for kind: "
        f"{target.kind!r} -- a no-baseline audit check has no "
        "--used-by/consumers.required_symbols equivalent to scope an "
        "app-consumer/plugin-contract check against "
        "(actions/check-target/validate-inputs.sh). Use kind: "
        "library for a no-baseline audit, or set a real channel."
        for i, check in enumerate(target.checks)
        if check.channel == NO_BASELINE_CHANNEL
    ]


def _target_issues(config: ProjectTargetsConfig, target: TargetSpec) -> list[str]:
    issues = _identifier_issues("target", target.id)
    issues.extend(_forbidden_field_issues(target))
    if target.kind == TARGET_KIND_LIBRARY:
        issues.extend(_library_target_issues(config, target))
    elif target.kind == TARGET_KIND_APP_CONSUMER:
        if not target.consumer_binary_pattern:
            issues.append(
                f"target {target.id!r}: kind: app-consumer requires "
                "consumer_binary_pattern."
            )
        issues.extend(_library_reference_issues(config, target))
    elif target.kind == TARGET_KIND_PLUGIN_CONTRACT:
        if not target.contract_file:
            issues.append(
                f"target {target.id!r}: kind: plugin-contract requires contract_file."
            )
        issues.extend(_library_reference_issues(config, target))
    issues.extend(_no_baseline_channel_issues(target))
    for i, check in enumerate(target.checks):
        issues.extend(_check_issues(config, f"target {target.id!r}.checks[{i}]", check))
    return issues


def _library_reference_issues(
    config: ProjectTargetsConfig, target: TargetSpec
) -> list[str]:
    if not target.library:
        return [f"target {target.id!r}: kind: {target.kind} requires library."]
    referenced = config.targets.get(target.library)
    if referenced is None:
        return [
            f"target {target.id!r}: library {target.library!r} is not declared "
            "under targets:."
        ]
    if referenced.kind != TARGET_KIND_LIBRARY:
        return [
            f"target {target.id!r}: library {target.library!r} must be a "
            f"kind: library target, not kind: {referenced.kind!r} — "
            "app-consumer/plugin-contract targets resolve their baseline/"
            "candidate lookup through a real library target only."
        ]
    return []


def _check_issues(
    config: ProjectTargetsConfig,
    where: str,
    check: CheckSpec,
    *,
    is_bundle: bool = False,
) -> list[str]:
    issues: list[str] = []
    if (
        check.channel != NO_BASELINE_CHANNEL
        and check.channel not in config.baseline_channels
    ):
        issues.append(
            f"{where}: channel {check.channel!r} is not declared under "
            f"baseline.channels: (use {NO_BASELINE_CHANNEL!r} for a no-baseline "
            "audit check)."
        )
    if check.depth not in CHECK_DEPTHS:
        issues.append(
            f"{where}: depth must be one of {sorted(CHECK_DEPTHS)}, got {check.depth!r}."
        )
    if check.gate_mode not in GATE_MODES:
        issues.append(
            f"{where}: gate_mode must be one of {sorted(GATE_MODES)}, got {check.gate_mode!r}."
        )
    # G42 "Explicit check identifiers": id/analysis.* charset validation,
    # deferred here (not in from_dict) like every other identifier here.
    if check.id and not _IDENTIFIER_RE.match(check.id):
        issues.append(
            f"{where}: id {check.id!r} is not a valid identifier -- must match "
            f"{_IDENTIFIER_RE.pattern!r}."
        )
    for field_name, value in (
        ("analysis.evidence", check.analysis_evidence),
        ("analysis.policy", check.analysis_policy),
        ("analysis.assurance", check.analysis_assurance),
    ):
        if value and not _IDENTIFIER_RE.match(value):
            issues.append(
                f"{where}: {field_name} {value!r} is not a valid identifier -- "
                f"must match {_IDENTIFIER_RE.pattern!r}."
            )
    issues.extend(analysis_assurance_issues(check.analysis_assurance, where=where))
    for profile_id in check.profiles:
        profile = config.profiles.get(profile_id)
        if profile is None:
            issues.append(
                f"{where}: profiles entry {profile_id!r} is not declared under profiles:."
            )
            continue
        if not profile.contract and check.channel != NO_BASELINE_CHANNEL:
            # contract: false profiles are documented as test-only lanes that
            # never get a baseline (S17) -- a real-channel check scoped only
            # to one can never be satisfied. A channel: "none" audit check has
            # no baseline to resolve in the first place, so it's exempt (S5
            # audits on a non-contract lane are a legitimate use case).
            issues.append(
                f"{where}: profiles entry {profile_id!r} has contract: false "
                "(a test-only lane that never gets a baseline) but this check "
                f"declares a real channel ({check.channel!r}) — only a "
                f"{NO_BASELINE_CHANNEL!r}-channel audit check may scope to a "
                "non-contract profile."
            )
        # abicheck/bundle.py's build_bundle_snapshot() skips non-ELF inputs
        # outright (baseline_set.py's _not_elf_issue), so a bundle check
        # explicitly scoped to a declared Windows/macOS profile can never
        # resolve -- it always produces an operationally-failing matrix
        # leg rather than a usable check (Codex review). An UNSET os
        # (profile.os == "") is left unrejected -- most projects never
        # bother declaring it, and treating "unspecified" as an error
        # would punish that common case for a field that's still purely
        # informational everywhere else in this module. Implicit (not
        # explicitly profile-scoped) bundle checks are handled separately
        # at run-plan generation time, where they're silently skipped
        # rather than erroring, the same way a profile that simply
        # doesn't build a given target is skipped.
        if is_bundle and profile.os and profile.os != "linux":
            issues.append(
                f"{where}: profiles entry {profile_id!r} has os: {profile.os!r}, "
                "but a bundle check's backend (abicheck/bundle.py) is ELF-only "
                "and skips every non-ELF member -- explicitly scope this bundle "
                "check to a linux profile, or drop it from profiles: and let "
                "the implicit sweep skip non-ELF profiles automatically."
            )
    if is_bundle and check.allow_new_target:
        issues.append(
            f"{where}: allow_new_target is not supported for a bundle check -- "
            "a bundle comparison needs one coherent release where every "
            "member already coexisted, so there is no well-defined old side "
            "for a member that's new (abicheck.buildsource.baseline_set."
            "resolve_bundle never returns new_target). Scope the new member "
            "individually with a channel/allow_new_target library-kind "
            "target check instead, and add it to this bundle once a real "
            "release has published a baseline-set covering every member."
        )
    return issues


def _bundle_issues(config: ProjectTargetsConfig, bundle: BundleSpec) -> list[str]:
    issues = _identifier_issues("bundle", bundle.id)
    for member in bundle.targets:
        referenced = config.targets.get(member)
        if referenced is None:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} is not declared under targets:."
            )
        elif referenced.kind != TARGET_KIND_LIBRARY:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} must be kind: library, "
                f"not kind: {referenced.kind!r}."
            )
        elif referenced.bundle and referenced.bundle != bundle.id:
            issues.append(
                f"bundle {bundle.id!r}: target {member!r} declares bundle: "
                f"{referenced.bundle!r}, not {bundle.id!r} — a target's own "
                "bundle: field and its membership here must agree."
            )
    bundle_depths = sorted(BUNDLE_CHECK_DEPTHS)
    for i, check in enumerate(bundle.checks):
        if check.depth not in BUNDLE_CHECK_DEPTHS and check.depth in CHECK_DEPTHS:
            # A depth outside CHECK_DEPTHS entirely is already reported by
            # _check_issues below -- only flag the bundle-specific
            # restriction for an otherwise-valid depth (build/source).
            issues.append(
                f"bundle {bundle.id!r}.checks[{i}]: depth {check.depth!r} is not "
                f"supported for a bundle check -- use one of "
                f"{bundle_depths} (actions/check-target/"
                "validate-inputs.sh rejects build/source for kind: bundle, "
                "which always compares directories)."
            )
        if check.channel == NO_BASELINE_CHANNEL:
            # channel: none routes check-target to the root Action's scan
            # mode (no baseline to compare against) -- but a bundle check's
            # candidate is always a staged directory of member binaries
            # (check-project.yml's own bundle-staging step), and scan mode
            # rejects a directory/package new-library outright (Codex
            # review). There is no real bundle audit path today.
            issues.append(
                f"bundle {bundle.id!r}.checks[{i}]: channel: "
                f"{NO_BASELINE_CHANNEL!r} is not supported for a bundle "
                "check -- a bundle's candidate is always a staged directory "
                "of member binaries, and action/validate-inputs.sh rejects "
                "a directory/package new-library for scan mode (the "
                "no-baseline routing). Set a real baseline channel for a "
                "bundle check, or scope each member individually with a "
                "channel: 'none' library-kind target check instead."
            )
        issues.extend(
            _check_issues(
                config, f"bundle {bundle.id!r}.checks[{i}]", check, is_bundle=True
            )
        )
    return issues


def _profile_issues(profile: ProfileSpec) -> list[str]:
    issues = _identifier_issues("profile", profile.id)
    # G34 Phase C: `os:` now selects a runner, so a value no runner can be
    # derived from is a real misconfiguration rather than the harmless free
    # text it used to be. Caught here, at `project validate` time, so it
    # surfaces before a matrix cell exists to be scheduled wrongly.
    if profile.os and runner_label_for_os(profile.os) is None:
        issues.append(unroutable_os_message("profiles", profile.id, profile.os))
    return issues


def _baseline_channel_issues(channel: BaselineChannelSpec) -> list[str]:
    issues = _identifier_issues("baseline channel", channel.id)
    if channel.id == NO_BASELINE_CHANNEL:
        issues.append(
            f"baseline channel {channel.id!r} is reserved as the no-baseline "
            "sentinel and cannot be declared as a real "
            "channel — a checks[].channel: 'none' entry would then be "
            "ambiguous between 'skip resolve-baseline' and 'resolve this "
            "declared channel', and check-target always takes the former."
        )
    if channel.source == "github-release" and not channel.asset_pattern:
        issues.append(
            f"baseline channel {channel.id!r}: source: github-release requires "
            "asset_pattern."
        )
    if channel.source == "actions-cache" and not channel.key_prefix:
        issues.append(
            f"baseline channel {channel.id!r}: source: actions-cache requires "
            "key_prefix."
        )
    return issues


def validate_project_targets(
    config: ProjectTargetsConfig,
) -> ProjectTargetsValidationReport:
    """Validate cross-references and kind-specific rules across the whole block.

    Never raises for a structurally-parsed :class:`ProjectTargetsConfig` —
    problems are reported, not thrown, matching
    :func:`~.build_output.validate_build_output`'s same contract. Structural/
    type errors already raised during :meth:`ProjectTargetsConfig.from_dict`.
    """
    report = ProjectTargetsValidationReport()
    if not config.targets and not config.bundles and not config.profiles:
        report.warnings.append(
            "no targets:/bundles:/profiles: declared — nothing for a G30 "
            "run-plan generator to act on yet."
        )
    for target in config.targets.values():
        report.errors.extend(_target_issues(config, target))
    for bundle in config.bundles.values():
        report.errors.extend(_bundle_issues(config, bundle))
    for profile in config.profiles.values():
        report.errors.extend(_profile_issues(profile))
    for channel in config.baseline_channels.values():
        report.errors.extend(_baseline_channel_issues(channel))
    return report

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

"""The ``aggregate`` expected-target *manifest* input: its version, its gate
policy vocabulary, and :class:`ExpectedTargets` itself.

Split out of :mod:`abicheck.workflows.aggregate` (AI-readiness file-size cap) rather
than left inline -- this is a self-contained input-parsing concern (a
manifest file's shape and validation) distinct from the report-fan-in logic
that consumes it, and :mod:`abicheck.workflows.aggregate` re-exports every public name
here so no other module's import path changes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

#: SemVer-style (MAJOR.MINOR) version of the expected-target *manifest* input
#: (``--manifest``). Independent of the report-output schema
#: (``AGGREGATE_SCHEMA_VERSION`` in ``aggregate.py``). A manifest may carry
#: ``"aggregate_manifest_version"``; a MAJOR component newer than this is
#: rejected (the reader cannot know the newer structure), a matching or older
#: one is accepted (additive-only within a MAJOR).
#:
#: ``2.0`` (CLI cleanup phase two, PR 2) adds the manifest's own ``gate``
#: block (:data:`OnMissingRequired`/:data:`OnUnexpectedTarget` policy,
#: replacing the removed ``--on-missing-required``/``--on-unexpected-target``
#: CLI flags). This is deliberately a MAJOR bump, not an additive ``1.1``:
#: ``_check_manifest_version`` only rejects a MAJOR *newer* than what this
#: reader supports, so a ``1.x``-vintage reader given a manifest carrying
#: ``gate`` at ``1.1`` would pass the version check, never read the unknown
#: key, and silently fall back to the hard-coded defaults
#: (``missing_required: fail``, ``unexpected_target: include``) -- which can
#: be exactly the wrong policy the manifest asked for, misapplied with no
#: error. A ``gate`` block therefore ships only at ``2.0``, which every
#: pre-``2.0`` reader's ``major > supported`` check is guaranteed to reject
#: loudly instead.
AGGREGATE_MANIFEST_VERSION = "2.0"


class OnMissingRequired(str, Enum):
    """Gate policy for a required target that never reported."""

    FAIL = "fail"  # incomplete required coverage fails the gate (default)
    WARN = "warn"  # report the gap but do not fail on coverage alone


class OnUnexpectedTarget(str, Enum):
    """Gate policy for a report whose target is not in the expected set."""

    INCLUDE = "include"  # count its real findings in the gate, not in coverage
    WARN = "warn"  # surface it and warn, but never fail the gate on it
    FAIL = "fail"  # any unexpected target fails the gate
    IGNORE = "ignore"  # drop it entirely


class AggregateError(ValueError):
    """A malformed input the caller must fix (usage error / exit 64)."""


def _check_manifest_version(raw: Any) -> None:
    """Validate an optional manifest ``aggregate_manifest_version`` field.

    Absent → accepted (an unversioned manifest is treated as the current
    MAJOR). Present → must be a ``"MAJOR.MINOR"`` string whose MAJOR component
    does not exceed :data:`AGGREGATE_MANIFEST_VERSION`'s (a newer MAJOR carries
    structure this reader cannot interpret; fail loud rather than silently
    mis-read it).
    """
    if raw is None:
        return
    if not isinstance(raw, str) or not raw:
        raise AggregateError("manifest 'aggregate_manifest_version' must be a string")
    # Exactly two dot-separated numeric components -- "2" (no minor), "2.x"
    # (a non-numeric minor the old split(".", 1)[0] parse never looked at),
    # and "2.0.1" (three components) all previously passed this check
    # silently, since only the prefix before the first "." was ever
    # inspected (CodeRabbit review, fresh evidence).
    parts = raw.split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise AggregateError(
            f"manifest 'aggregate_manifest_version' is not a MAJOR.MINOR "
            f"version: {raw!r}"
        )
    # Both values are guaranteed numeric here: ``parts`` passed the digit
    # validation above, and the supported version is a module-owned constant.
    # Keeping a defensive ``ValueError`` arm after that proof created dead code
    # which could neither be exercised nor explain a real input failure.
    major = int(parts[0])
    supported = int(AGGREGATE_MANIFEST_VERSION.split(".", 1)[0])
    if major > supported:
        raise AggregateError(
            f"manifest 'aggregate_manifest_version' {raw!r} is newer than this "
            f"tool supports (max major {supported}); upgrade abicheck"
        )


def _parse_manifest_gate(
    data: Mapping[str, Any],
    version_raw: Any = None,
) -> tuple[OnMissingRequired | None, OnUnexpectedTarget | None]:
    """Parse a manifest/run-plan-projected ``gate`` block (schema 2.0+).

    Key absent → ``(None, None)`` (the caller applies the hard-coded
    defaults). Key present → both sub-keys are optional and independently
    validated; an invalid value is a loud :class:`AggregateError`, not a
    silent fallback to the default (CLI cleanup phase two, PR 2 — this block
    replaces the removed ``--on-missing-required``/``--on-unexpected-target``
    CLI flags, so a typo here has nowhere else to be caught). An explicit
    JSON ``null`` — for ``gate`` itself, or for either sub-key — is
    deliberately **not** treated the same as the key being absent (Codex
    review, fresh evidence): the two are different producer intents (never
    mentioned this at all, vs. this field explicitly present with a
    JSON-``null`` value), and conflating them let a hand-authored, templated,
    or corrupted v2 manifest with e.g. ``"gate": null`` or ``"gate":
    {"missing_required": null}`` silently fall back to the hard-coded
    ``fail``/``include`` defaults instead of failing closed on the malformed
    input.

    A manifest that carries ``gate`` must *explicitly* declare
    ``aggregate_manifest_version`` at major ``2`` or newer — an absent
    version is rejected too, not just a declared pre-2.0 one (Codex review,
    fresh evidence, second round): "absent version = treat as this reader's
    own current MAJOR" is inherently reader-relative, so it can never be a
    safe signal that *every* reader understands ``gate`` — a genuinely old,
    pre-gate reader given the identical unversioned manifest applies its
    *own* "absent = my current major" rule too, and its major has no notion
    of ``gate`` at all, so it silently ignores the block and applies the
    hard-coded default policy regardless of what this (2.0+) reader would
    have done with the same input. The whole reason ``gate`` shipped at a
    MAJOR bump is to give a pre-2.0 reader something concrete to reject; an
    absent version gives it nothing to reject on. A manifest with a declared
    pre-2.0 version is rejected for the same reason and was already covered
    before this round: a producer that stamps an old version number on a
    manifest carrying a field that version predates is internally
    inconsistent, and honoring ``gate`` here anyway (this reader is 2.0+ and
    could) would recreate exactly the version-skew inversion the MAJOR bump
    exists to prevent, just moved from "old reader, new manifest" to
    "manifest lies about its own version". *version_raw* is the same
    already-validated (by :func:`_check_manifest_version`) raw value.
    Absent version stays accepted for every *other* manifest field, per
    :func:`_check_manifest_version`'s own "absent = current MAJOR" rule —
    only a manifest that also carries ``gate`` needs the explicit
    declaration.
    """
    if "gate" not in data:
        return None, None
    gate_raw = data["gate"]
    if gate_raw is None:
        raise AggregateError("manifest 'gate' must not be null")
    if not (isinstance(version_raw, str) and version_raw):
        raise AggregateError(
            "manifest 'gate' requires an explicit 'aggregate_manifest_version' "
            ">= '2.0' (none was given); a pre-2.0 reader given the same "
            "unversioned manifest would apply its own 'absent = current "
            "major' rule and silently ignore this block, applying the "
            "hard-coded default policy instead of what it asked for"
        )
    # Already validated as a well-formed MAJOR.MINOR string by
    # _check_manifest_version before this function is ever called.
    major = int(version_raw.split(".", 1)[0])
    if major < 2:
        raise AggregateError(
            "manifest 'gate' requires 'aggregate_manifest_version' >= "
            f"'2.0' (declared {version_raw!r}); a pre-2.0 reader would "
            "silently ignore this block and apply the hard-coded "
            "default policy instead of what it asked for"
        )
    if not isinstance(gate_raw, dict):
        raise AggregateError("manifest 'gate' must be an object")
    unknown = sorted(set(gate_raw) - {"missing_required", "unexpected_target"})
    if unknown:
        raise AggregateError(f"manifest 'gate': unknown key(s) {unknown!r}")
    missing_required: OnMissingRequired | None = None
    if "missing_required" in gate_raw:
        mr_raw = gate_raw["missing_required"]
        if mr_raw is None:
            raise AggregateError("manifest 'gate.missing_required' must not be null")
        try:
            missing_required = OnMissingRequired(mr_raw)
        except ValueError as exc:
            raise AggregateError(
                f"manifest 'gate.missing_required' {mr_raw!r} must be one of "
                f"{[v.value for v in OnMissingRequired]}"
            ) from exc
    unexpected_target: OnUnexpectedTarget | None = None
    if "unexpected_target" in gate_raw:
        ut_raw = gate_raw["unexpected_target"]
        if ut_raw is None:
            raise AggregateError("manifest 'gate.unexpected_target' must not be null")
        try:
            unexpected_target = OnUnexpectedTarget(ut_raw)
        except ValueError as exc:
            raise AggregateError(
                f"manifest 'gate.unexpected_target' {ut_raw!r} must be one of "
                f"{[v.value for v in OnUnexpectedTarget]}"
            ) from exc
    return missing_required, unexpected_target


@dataclass(frozen=True)
class ExpectedTargets:
    """The declared expected-target set (from a manifest or CLI flags)."""

    #: target_id → required
    targets: Mapping[str, bool]
    head_sha: str | None = None
    #: The manifest's own ``gate.missing_required``/``gate.unexpected_target``
    #: (CLI cleanup phase two, PR 2) — ``None`` when the manifest omits
    #: ``gate`` or the sub-key, meaning "apply the hard-coded default", not
    #: "apply `OnMissingRequired.FAIL`/`OnUnexpectedTarget.INCLUDE`
    #: explicitly" (the two happen to coincide today, but only the caller
    #: that resolves this against a default should get to say so).
    gate_missing_required: OnMissingRequired | None = None
    gate_unexpected_target: OnUnexpectedTarget | None = None

    @classmethod
    def from_manifest_file(cls, path: Path) -> ExpectedTargets:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise AggregateError(f"cannot read manifest {path}: {exc}") from exc
        return cls.from_manifest_data(data)

    @classmethod
    def from_manifest_data(cls, data: Any) -> ExpectedTargets:
        if not isinstance(data, dict):
            raise AggregateError("manifest must be a JSON object")
        _check_manifest_version(data.get("aggregate_manifest_version"))
        raw = data.get("targets")
        if not isinstance(raw, list) or not raw:
            raise AggregateError("manifest 'targets' must be a non-empty list")
        targets: dict[str, bool] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise AggregateError(f"manifest target must be an object: {entry!r}")
            tid = entry.get("id")
            if not isinstance(tid, str) or not tid:
                raise AggregateError(f"manifest target needs a string 'id': {entry!r}")
            if tid in targets:
                raise AggregateError(f"duplicate manifest target id: {tid!r}")
            required = entry.get("required", True)
            if not isinstance(required, bool):
                raise AggregateError(
                    f"manifest target 'required' must be a boolean: {entry!r}"
                )
            targets[tid] = required
        head_sha = data.get("head_sha")
        if "head_sha" in data and (not isinstance(head_sha, str) or not head_sha):
            # A present-but-malformed head_sha must not silently become None —
            # that would disable the commit-identity guard the manifest asked
            # for. Fail loud instead.
            raise AggregateError("manifest 'head_sha' must be a non-empty string")
        gate_missing_required, gate_unexpected_target = _parse_manifest_gate(
            data, data.get("aggregate_manifest_version")
        )
        return cls(
            targets=targets,
            head_sha=head_sha,
            gate_missing_required=gate_missing_required,
            gate_unexpected_target=gate_unexpected_target,
        )

    @classmethod
    def from_lists(
        cls, required: Iterable[str], optional: Iterable[str] = ()
    ) -> ExpectedTargets:
        targets: dict[str, bool] = {tid: False for tid in optional}
        for tid in required:
            targets[tid] = True
        if not targets:
            raise AggregateError("no expected targets given")
        return cls(targets=targets)


def resolve_gate_policy(
    expected: ExpectedTargets | None,
    *,
    explicit_missing_required: OnMissingRequired | None = None,
    explicit_unexpected_target: OnUnexpectedTarget | None = None,
    source_hint: str = "manifest",
) -> tuple[OnMissingRequired, OnUnexpectedTarget, str]:
    """Resolve the effective gate policy, and where it came from.

    CLI cleanup phase two, PR 2 replaced the standalone
    ``--on-missing-required``/``--on-unexpected-target`` CLI flags with a
    manifest-carried ``gate`` block, so "what policy applies" now has one
    versioned source of truth instead of two independently-typeable flags.
    Precedence: an *explicit* value (for a direct API/test caller that still
    wants to force one -- there is no longer a CLI spelling for this) wins
    outright; otherwise *expected*'s own manifest ``gate`` block; otherwise
    the hard-coded default (``FAIL``/``INCLUDE``, unchanged from before this
    PR). *source_hint* names which expected-target source this run actually
    used (``"manifest"``/``"run-plan"``) -- both are parsed through
    :meth:`ExpectedTargets.from_manifest_data`, so the field itself can't
    tell the two apart; the caller (``cli_aggregate.py``) knows which flag
    it received and passes the right label. Returns
    ``(missing_required, unexpected_target, policy_source)``, where
    ``policy_source`` is ``"explicit"`` when the caller passed at least one
    explicit override (Codex review, fresh evidence -- an earlier revision
    reported this case as ``"default"``, which is factually wrong: the
    *resolved* value is the caller's own override, not the hard-coded
    default, so labeling it "default" misrepresents the audit field to
    anyone reading ``effective_policy`` back), else *source_hint* when the
    manifest supplied at least one of the two fields, else ``"default"``
    (also the value for discovered-only mode, where *expected* is ``None``
    and neither policy is applicable). This is a single scalar covering both
    fields, same coarse-grained approximation the manifest/default split
    already had before explicit overrides were distinguished -- a caller
    overriding only one of the two fields still reports one combined source,
    not independent per-field provenance.
    """
    manifest_missing_required = expected.gate_missing_required if expected else None
    manifest_unexpected_target = expected.gate_unexpected_target if expected else None
    resolved_missing_required = (
        explicit_missing_required
        if explicit_missing_required is not None
        else manifest_missing_required
        if manifest_missing_required is not None
        else OnMissingRequired.FAIL
    )
    resolved_unexpected_target = (
        explicit_unexpected_target
        if explicit_unexpected_target is not None
        else manifest_unexpected_target
        if manifest_unexpected_target is not None
        else OnUnexpectedTarget.INCLUDE
    )
    manifest_supplied_something = (
        manifest_missing_required is not None or manifest_unexpected_target is not None
    )
    has_explicit_override = (
        explicit_missing_required is not None or explicit_unexpected_target is not None
    )
    if has_explicit_override:
        policy_source = "explicit"
    elif manifest_supplied_something:
        policy_source = source_hint
    else:
        policy_source = "default"
    return resolved_missing_required, resolved_unexpected_target, policy_source

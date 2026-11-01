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
"""One ``checks:`` entry of ``.abicheck.yml``'s ``targets:``/``bundles:``
block (ADR-047 §3), and the strict field readers its parsers share.

A leaf split out of :mod:`abicheck.buildsource.project_targets` (an
``architecture/debt.yaml`` ``no_growth`` module) so that
:mod:`~abicheck.buildsource.project_bundles` can own ``BundleSpec`` without
importing ``project_targets`` back: both the target and the bundle parsers
read a ``checks:`` list through :func:`parse_checks_list`, and both read
their scalar fields through the same ADR-043 strict helpers. Pure: parses a
dict, never touches the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Sentinel ``channel`` value for a ``baseline: none`` check (ADR-047 §6 S5
#: correction) — ``check-target`` (P1.3) must skip ``resolve-baseline``
#: entirely for a check carrying this value, never look it up as a declared
#: channel name.
NO_BASELINE_CHANNEL = "none"


def opt_str_field(d: dict[str, Any], key: str, *, where: str) -> str:
    """A strictly-typed optional string field: absent/``None`` -> ``""``, any
    non-string present value is a hard error (ADR-043 strict-config
    convention — never silently coerced via ``str(...)``, unlike a bare
    ``str(d.get(key, "") or "")`` which would turn e.g. a YAML list into the
    synthetic string ``"['x']"``)."""
    value = d.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"{where}.{key} must be a string, got {type(value).__name__}: {value!r}"
        )
    return value


def require_str_list(d: dict[str, Any], key: str, *, where: str) -> list[str]:
    """A strictly-typed optional list-of-strings field: absent -> ``[]``, a
    non-list or a list containing a non-string element is a hard error."""
    raw = d.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{where}.{key} must be a list of strings, got {raw!r}")
    bad = [x for x in raw if not isinstance(x, str)]
    if bad:
        raise ValueError(f"{where}.{key} must be a list of strings, got {bad!r}")
    return list(raw)


def parse_checks_list(d: dict[str, Any], *, where: str) -> list[CheckSpec]:
    """Parse an optional ``checks:`` list, shared by ``TargetSpec``/
    ``BundleSpec`` (both accept the identical shape — review finding)."""
    checks_raw = d.get("checks")
    if checks_raw is not None and not isinstance(checks_raw, list):
        raise ValueError(f"{where}.checks must be a list")
    checks: list[CheckSpec] = []
    for i, c in enumerate(checks_raw or []):
        if not isinstance(c, dict):
            raise ValueError(f"{where}.checks[{i}] must be a mapping")
        checks.append(CheckSpec.from_dict(c, where=f"{where}.checks[{i}]"))
    return checks


def unknown_keys(d: dict[str, Any], known: set[str]) -> list[Any]:
    """``sorted(set(d) - known)``, but safe when *d* carries a non-string key
    (a bare PyYAML 1.1 ``on``/``off``/``yes``/``no`` mapping key parses as a
    bool) alongside a string one -- plain ``sorted()`` would raise ``TypeError``
    comparing ``bool``/``str`` instead of surfacing the documented ``ValueError``
    usage error (Codex finding, mirrors the top-level key check's own fix)."""
    return sorted(set(d) - known, key=repr)


def parse_analysis_block(raw: object, *, where: str) -> tuple[str, str, str]:
    """Parse an optional ``checks[].analysis:`` mapping (G42 "Explicit check
    identifiers"): ``{evidence, policy, assurance}``, each an optional
    non-empty string. Returns ``("", "", "")`` when *raw* is absent -- the
    field is entirely optional, and every existing ``checks[]`` entry that
    predates this block parses unchanged.

    Only structural validity (mapping shape, known keys, non-empty string
    values) is checked here -- same "parsing alone isn't validation" split
    this module's own docstring documents elsewhere; the identifier-charset
    check is deferred to :func:`_check_issues`, alongside every other
    checks[]-level cross-reference/charset rule.
    """
    if raw is None:
        return "", "", ""
    if not isinstance(raw, dict):
        raise ValueError(
            f"{where}.analysis must be a mapping, got {type(raw).__name__}: {raw!r}"
        )
    unknown = unknown_keys(raw, {"evidence", "policy", "assurance"})
    if unknown:
        raise ValueError(f"{where}.analysis: unknown key(s) {unknown}")
    values: dict[str, str] = {}
    for key in ("evidence", "policy", "assurance"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{where}.analysis.{key} must be a non-empty string")
        values[key] = value
    return (
        values.get("evidence", ""),
        values.get("policy", ""),
        values.get("assurance", ""),
    )


@dataclass
class CheckSpec:
    """One ``{channel, depth, required, gate_mode, profiles}`` tuple (ADR-047 §3).

    Closes the gap ADR-047 §3 flags: ``baseline: channels:`` alone declares
    which channels *exist*, not which channel/depth/policy a given target
    actually runs — this is the per-check assignment that does.
    """

    channel: str = ""
    depth: str = ""
    required: bool = True
    #: Direct-construction default is ``"local"`` (matching this field's own
    #: default); ``from_dict`` instead derives an unset ``gate_mode`` from
    #: ``channel`` — ``"advisory"`` for the ``NO_BASELINE_CHANNEL`` sentinel,
    #: ``"local"`` otherwise (ADR-047 §8 S5: "Advisory by default").
    gate_mode: str = "local"
    #: Explicit profile-id selector (see module docstring). Empty = every
    #: ``contract: true`` profile, filtered against ``build-output.json`` by
    #: G30 P1.4's run-plan generator — not resolved here.
    profiles: list[str] = field(default_factory=list)
    #: Forwarded to ``check-target``'s own ``allow-new-target`` input (and,
    #: through it, ``resolve-baseline``'s) — ``False`` (default) means a
    #: target absent from this check's otherwise-resolved baseline-set
    #: always fails ``ambiguous``; ``True`` opts this specific check into
    #: the ``new_target`` lifecycle state instead (a new library's first
    #: release checked against a baseline-set that predates it). Rejected
    #: outright on a bundle check by :func:`_check_issues` — a bundle
    #: comparison needs one coherent release where every member already
    #: coexisted, so "this member is new" has no well-defined old side (see
    #: ``abicheck.buildsource.baseline_set.resolve_bundle``'s docstring).
    allow_new_target: bool = False
    #: G42 "Explicit check identifiers": an optional project-owned logical
    #: id, appended to the generated ``check_id`` as a ``~<id>`` tail
    #: (``abicheck.buildsource.check_report.build_check_id``). Lets two
    #: checks that would otherwise generate the identical ``target@profile
    #: #channel@depth`` string (e.g. two ``checks[]`` entries differing only
    #: in ``analysis:``) each carry a distinct, non-colliding identity.
    #: Empty (the default) means "no explicit id" -- the generated string is
    #: unchanged from the pre-G42 shape.
    id: str = ""
    #: G42 "Explicit check identifiers": which extraction/comparison
    #: *method* produced the facts this check consumes (e.g. "replay" vs.
    #: "clang-plugin") -- see G39's per-finding evidence-provider
    #: vocabulary. Purely a distinguishing/reporting label at this phase;
    #: nothing downstream yet selects a different extraction pipeline based
    #: on this value. Empty means "not declared".
    analysis_evidence: str = ""
    #: G42 "Explicit check identifiers": which policy profile this check's
    #: analysis is evaluated under. References the same policy-profile
    #: mechanism ``--policy``/``profiles.<id>`` already select -- this field
    #: is the identity slot it's selected through, not a second mechanism.
    #: Empty means "not declared".
    analysis_policy: str = ""
    #: Which assurance requirement this check must meet; "" = not declared.
    #: Validated against ``analysis_assurance_gate``'s supported set.
    analysis_assurance: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "channel": self.channel,
            "depth": self.depth,
            "required": self.required,
            "gate_mode": self.gate_mode,
        }
        if self.profiles:
            d["profiles"] = list(self.profiles)
        if self.allow_new_target:
            d["allow_new_target"] = True
        if self.id:
            d["id"] = self.id
        analysis: dict[str, Any] = {}
        if self.analysis_evidence:
            analysis["evidence"] = self.analysis_evidence
        if self.analysis_policy:
            analysis["policy"] = self.analysis_policy
        if self.analysis_assurance:
            analysis["assurance"] = self.analysis_assurance
        if analysis:
            d["analysis"] = analysis
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, where: str) -> CheckSpec:
        known = {
            "channel",
            "depth",
            "required",
            "gate_mode",
            "profiles",
            "allow_new_target",
            "id",
            "analysis",
        }
        unknown = unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        channel = d.get("channel")
        if not isinstance(channel, str) or not channel:
            raise ValueError(f"{where}.channel must be a non-empty string")
        depth = d.get("depth")
        if not isinstance(depth, str) or not depth:
            raise ValueError(f"{where}.depth must be a non-empty string")
        required = d.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(
                f"{where}.required must be a boolean, got "
                f"{type(required).__name__}: {required!r}"
            )
        if "gate_mode" in d:
            gate_mode = d["gate_mode"]
            if not isinstance(gate_mode, str):
                raise ValueError(
                    f"{where}.gate_mode must be a string, got "
                    f"{type(gate_mode).__name__}: {gate_mode!r}"
                )
        else:
            # ADR-047 §8: a channel: "none" no-baseline audit (S5) defaults
            # to advisory, not local -- unlike a real-channel check, it has
            # no baseline-drift verdict to gate CI on in the first place, so
            # defaulting it to a blocking gate would surprise a minimal
            # `{channel: none, depth: ...}` entry into failing CI.
            gate_mode = "advisory" if channel == NO_BASELINE_CHANNEL else "local"
        if d.get("profiles") == []:
            # `require_str_list` can't distinguish an omitted `profiles:`
            # key from an explicit `profiles: []` -- both parse to `[]` --
            # but this field's own semantics (see the dataclass docstring)
            # treat an empty selector as "every contract profile," so a
            # config author who wrote `profiles: []` expecting "select
            # nothing" would silently get the opposite (Codex review).
            # Reject the explicit-empty spelling outright instead of
            # reinterpreting it: omit the key for "every profile," or name
            # at least one profile id.
            raise ValueError(
                f"{where}.profiles must not be an explicit empty list -- "
                "omit the key entirely to run on every contract profile, "
                "or list at least one profile id"
            )
        profiles = require_str_list(d, "profiles", where=where)
        allow_new_target = d.get("allow_new_target", False)
        if not isinstance(allow_new_target, bool):
            raise ValueError(
                f"{where}.allow_new_target must be a boolean, got "
                f"{type(allow_new_target).__name__}: {allow_new_target!r}"
            )
        check_id = ""
        if "id" in d:
            # Structural validity only -- the identifier-charset check is
            # deferred to _check_issues, same split parse_analysis_block's
            # own docstring documents.
            raw_id = d["id"]
            if not isinstance(raw_id, str) or not raw_id:
                raise ValueError(f"{where}.id must be a non-empty string")
            check_id = raw_id
        analysis_evidence, analysis_policy, analysis_assurance = parse_analysis_block(
            d.get("analysis"), where=where
        )
        return cls(
            channel=channel,
            depth=depth,
            required=required,
            gate_mode=gate_mode,
            profiles=profiles,
            allow_new_target=allow_new_target,
            id=check_id,
            analysis_evidence=analysis_evidence,
            analysis_policy=analysis_policy,
            analysis_assurance=analysis_assurance,
        )

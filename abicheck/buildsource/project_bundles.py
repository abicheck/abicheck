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
"""``.abicheck.yml`` ``bundles:`` entries (ADR-047 §3): :class:`BundleSpec`.

Split out of :mod:`abicheck.buildsource.project_targets` (an
``architecture/debt.yaml`` ``no_growth`` module) when bundles gained their
own scope policy. A bundle is a release group of library targets, checked
together through ``compare``'s directory fan-out; its two scope keys are
that fan-out's ADR-065 selection, stated in the project config instead of
per invocation:

``required:``
    The member target ids that must be present on both sides. Each is
    forwarded as ``--select-required``; every other member is forwarded as
    ``--select`` (still compared, but its absence does not make the scope
    incomplete). A subset of ``targets:``. Omitted means every member is
    required, which is what a bundle check enforced before this key existed.
``on_incomplete:``
    ``fail`` or ``warn`` -- what an incomplete scope (a required member
    missing) does to the check's exit code. Forwarded as
    ``--set scope.on_incomplete=block|warn``; ``fail`` is the project-config
    spelling of the CLI's ``block``. Omitted means the project's own
    ``scope.on_incomplete`` (default ``warn``) decides.

Declaring either key turns the policy on: ``check-project.yml`` then stages
the members it finds instead of refusing the whole bundle for one missing
candidate, and leaves the verdict on the absence to ``compare``'s scope
axis. With neither key the bundle behaves as before: every member must be
staged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.release_selection import BUNDLE_ON_INCOMPLETE_TO_SCOPE
from .project_check_spec import CheckSpec, parse_checks_list, unknown_keys

__all__ = ["BundleSpec"]


def _parse_required(d: dict[str, Any], targets: list[str], *, where: str) -> list[str]:
    """``required:``: a non-empty, duplicate-free subset of *targets*.

    Absent -> ``[]`` (no declared subset). An explicit empty list is refused
    rather than read as "no member required": that would silently turn a
    missing member into a pass, the opposite of what omitting the key means.
    """
    if "required" not in d:
        return []
    raw = d["required"]
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"{where}.required must be a non-empty list of member target ids "
            "(omit the key to require every member)"
        )
    bad = [x for x in raw if not isinstance(x, str) or not x]
    if bad:
        raise ValueError(f"{where}.required must be a list of strings, got {bad!r}")
    seen: set[str] = set()
    repeated: set[str] = set()
    for x in raw:
        (repeated if x in seen else seen).add(x)
    dupes = sorted(repeated)
    if dupes:
        raise ValueError(f"{where}.required lists {dupes!r} more than once")
    outside = [x for x in raw if x not in targets]
    if outside:
        raise ValueError(
            f"{where}.required names {outside!r}, which are not in this "
            f"bundle's targets {targets!r}"
        )
    return list(raw)


def _parse_on_incomplete(d: dict[str, Any], *, where: str) -> str:
    if "on_incomplete" not in d:
        return ""
    raw = d["on_incomplete"]
    if raw not in BUNDLE_ON_INCOMPLETE_TO_SCOPE:
        raise ValueError(
            f"{where}.on_incomplete must be one of "
            f"{sorted(BUNDLE_ON_INCOMPLETE_TO_SCOPE)}, got {raw!r}"
        )
    return str(raw)


@dataclass
class BundleSpec:
    """One ``bundles:`` entry (ADR-047 §3) — a release group of library targets.

    ``checks:`` (same ``{channel, depth, required, gate_mode, profiles}``
    shape as a target's — review finding, ADR-047 §5): the run-plan example
    emits a ``kind: "bundle"`` check entry alongside per-target ones (S14
    bundle-scoped analysis), and that cell needs its own baseline-channel/
    depth/gate policy just like a target's checks do.

    ``required``/``on_incomplete``: the bundle's scope policy (module
    docstring). ``[]``/``""`` mean "not declared".
    """

    id: str = ""
    targets: list[str] = field(default_factory=list)
    checks: list[CheckSpec] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    on_incomplete: str = ""

    @property
    def declares_scope_policy(self) -> bool:
        """Whether this bundle states a scope policy at all."""
        return bool(self.required or self.on_incomplete)

    @property
    def required_members(self) -> list[str]:
        """The members whose absence makes the scope incomplete, in
        ``targets:`` order: the declared subset, else every member."""
        if not self.required:
            return list(self.targets)
        declared = set(self.required)
        return [t for t in self.targets if t in declared]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"targets": list(self.targets)}
        if self.required:
            d["required"] = list(self.required)
        if self.on_incomplete:
            d["on_incomplete"] = self.on_incomplete
        if self.checks:
            d["checks"] = [c.to_dict() for c in self.checks]
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> BundleSpec:
        where = f"bundles.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = unknown_keys(d, {"targets", "checks", "required", "on_incomplete"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        targets = d.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"{where}.targets must be a non-empty list of target ids")
        bad = [t for t in targets if not isinstance(t, str)]
        if bad:
            raise ValueError(f"{where}.targets must be a list of strings, got {bad!r}")
        members = [str(t) for t in targets]
        return cls(
            id=name,
            targets=members,
            checks=parse_checks_list(d, where=where),
            required=_parse_required(d, members, where=where),
            on_incomplete=_parse_on_incomplete(d, where=where),
        )

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

"""``.abicheck.yml`` ``targets:``/``bundles:``/``profiles:``/``baseline:``
block (ADR-047 §3, G30 P1.5).

Extends the project config with the portable, project-owned surface
``check-project.yml``'s run-plan generator (G30 P1.4, not built yet) will
consume: which libraries/consumers/plugin-contracts exist, how they group
into release bundles, which build profiles are ABI contracts, which baseline
channels exist, and — the schema gap ADR-047 §3 flags and this module
resolves — exactly which ``{channel, depth, required, gate_mode, profiles}``
checks run against each target/bundle.

This module only defines the contract and validates a hand-authored
``targets:``/``bundles:``/``profiles:``/``baseline:`` block — there is no
run-plan generator here yet (that's G30 P1.4, which *consumes* this) and no
``abicheck project-targets init`` scaffolding either. Pure: parses a dict,
never touches the filesystem beyond reading the one YAML file.

``BuildConfig`` (:mod:`abicheck.buildsource.inline`) recognizes
``targets``/``bundles``/``profiles``/``baseline`` as known top-level
``.abicheck.yml`` keys (so their presence doesn't trip its own strict
unknown-key error) but does not parse them itself — the same
recognized-but-not-parsed treatment it already gives ``risk_rules``/
``crosschecks``, which are likewise owned by a sibling module. This module's
own loader (:func:`load_project_targets_config`) re-reads the same YAML file
and is the sole owner of this block's schema.

Two design choices this module makes, where ADR-047 §3 flagged an open gap
and deliberately left the choice to P1.5:

- **Profile scoping for ``checks:`` entries.** Rather than assume the naive
  "cross every check with every ``contract: true`` profile" product is safe
  (ADR-047 §3 explicitly warns this produces impossible cells for a target
  that doesn't exist on every profile), each ``checks:`` entry carries an
  *optional* explicit ``profiles:`` selector. When set, the check runs only
  on the listed profile ids (validated against ``profiles:`` block). When
  omitted, this module does not resolve it to a profile list at all — G30
  P1.4's run-plan generator is responsible for deriving the actual
  ``(target, profile)`` cells from each profile's own ``build-output.json``
  ``targets[]`` list (the ADR's second, safer option), never from a blind
  cross-product. This module's validator does not and cannot enforce that
  downstream behaviour; it only validates that an *explicit* ``profiles:``
  selector, when present, names real declared profile ids.
- **``app-consumer``/``plugin-contract`` redirection.** Per ADR-047 §3's
  two "unstated rule" corrections, both the baseline-lookup key and the
  candidate-artifact lookup for these two ``kind``s resolve through their
  ``library`` field, while the check's own reporting identity stays the
  contract target's own name. This module validates that ``library`` names
  a real ``kind: library`` target (not an app-consumer/plugin-contract
  target, which cannot itself be resolved further) but does not perform the
  redirection itself — that is G30 P1.2 (``resolve-baseline``)/P1.3
  (``check-target``)'s job at run time.

``bundles:`` entries also carry their own ``checks:`` (same shape as a
target's) — the ADR-047 §5 run-plan emits a ``kind: "bundle"`` check
alongside per-target ones (S14 bundle-scoped analysis), and that cell needs
its own baseline-channel/depth/gate policy just like a target's does; see
:class:`BundleSpec`.

``ProjectTargetsConfig.from_dict`` validates every top-level key in the raw
mapping against :data:`~.inline.KNOWN_TOP_LEVEL_KEYS` — the *full*
``.abicheck.yml`` key set, not just this module's four owned keys — so a
misspelled block (``tagrets:``) is a hard error rather than silently
parsing as an empty, all-default config. Every ``targets:``/``bundles:``/
``profiles:``/``baseline.channels:`` mapping key must itself be a real YAML
string (PyYAML's default resolver reads a bare ``on``/``off``/``yes``/``no``
key as a bool and a bare digit key as an int; silently ``str()``-coercing
either would mint an id the user never actually wrote). ``"none"`` is
reserved and cannot be declared as a real ``baseline.channels`` id — it is
:data:`NO_BASELINE_CHANNEL`, the sentinel a ``checks[].channel`` uses to
bypass ``resolve-baseline`` entirely (ADR-047 §6 S5); allowing a real
channel of that name would make the sentinel ambiguous with an actual
baseline lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .inline import KNOWN_TOP_LEVEL_KEYS
from .scan_levels import USER_DEPTHS, EvidenceDepth

#: The identifier charset every target/bundle/profile/channel id must satisfy
#: — matches the per-component pattern the report-identity envelope (ADR-047
#: §7, ``compare_report.schema.json``'s ``check_id``) already enforces for
#: ``target@profile#baseline_channel@depth``, so a name valid here can never
#: produce an ambiguous/unparseable check_id downstream.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: ADR-047 §3 ``targets:`` ``kind`` discriminator.
TARGET_KIND_LIBRARY = "library"
TARGET_KIND_APP_CONSUMER = "app-consumer"
TARGET_KIND_PLUGIN_CONTRACT = "plugin-contract"
TARGET_KINDS = frozenset(
    {TARGET_KIND_LIBRARY, TARGET_KIND_APP_CONSUMER, TARGET_KIND_PLUGIN_CONTRACT}
)

#: ADR-047 §4/§7 ``check-target`` gate-mode values.
GATE_MODES = frozenset({"local", "deferred", "advisory"})

#: ADR-047 §10 baseline storage backends (external object store is P2, out of
#: scope here).
BASELINE_SOURCES = frozenset({"github-release", "actions-cache", "git"})

#: The evidence-depth ladder a ``checks:`` entry's ``depth`` must be one of —
#: the same four public rungs ``requested_depth``/``effective_depth`` accept
#: in the report schema (ADR-047 §7).
CHECK_DEPTHS = frozenset(d.value for d in USER_DEPTHS)

#: A ``bundle`` check's ``depth`` is further restricted to these two rungs --
#: ``kind: bundle`` always compares directories (the resolved binaries-dir vs.
#: the candidate bundle directory) in ``actions/check-target``, which routes
#: through the CLI's per-library release fan-out and never collects inline
#: build/source evidence for that path (``actions/check-target/
#: validate-inputs.sh`` rejects ``build``/``source`` for ``kind: bundle``).
BUNDLE_CHECK_DEPTHS = frozenset(
    {EvidenceDepth.BINARY.value, EvidenceDepth.HEADERS.value}
)

#: Sentinel ``channel`` value for a ``baseline: none`` check (ADR-047 §6 S5
#: correction) — ``check-target`` (P1.3) must skip ``resolve-baseline``
#: entirely for a check carrying this value, never look it up as a declared
#: channel name.
NO_BASELINE_CHANNEL = "none"


def _opt_str_field(d: dict[str, Any], key: str, *, where: str) -> str:
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


def _require_str_list(d: dict[str, Any], key: str, *, where: str) -> list[str]:
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


def _parse_checks_list(d: dict[str, Any], *, where: str) -> list[CheckSpec]:
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


def _unknown_keys(d: dict[str, Any], known: set[str]) -> list[Any]:
    """``sorted(set(d) - known)``, but safe when *d* carries a non-string key
    (a bare PyYAML 1.1 ``on``/``off``/``yes``/``no`` mapping key parses as a
    bool) alongside a string one -- plain ``sorted()`` would raise ``TypeError``
    comparing ``bool``/``str`` instead of surfacing the documented ``ValueError``
    usage error (Codex finding, mirrors the top-level key check's own fix)."""
    return sorted(set(d) - known, key=repr)


def _require_mapping(data: object, block: str) -> dict[str, Any]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{block} must be a mapping, got {type(data).__name__}: {data!r}"
        )
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        # PyYAML's default (YAML 1.1) resolver reads a bare `on`/`off`/`yes`/`no`
        # mapping key as a bool, and a bare digit key as an int -- silently
        # stringifying either here (e.g. `str(True)` -> "True") would mint a
        # target/bundle/profile/channel id the user never actually wrote.
        raise ValueError(f"{block}: key(s) must be strings, got {bad_keys!r}")
    return data


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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "channel": self.channel,
            "depth": self.depth,
            "required": self.required,
            "gate_mode": self.gate_mode,
        }
        if self.profiles:
            d["profiles"] = list(self.profiles)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, where: str) -> CheckSpec:
        known = {"channel", "depth", "required", "gate_mode", "profiles"}
        unknown = _unknown_keys(d, known)
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
            # `_require_str_list` can't distinguish an omitted `profiles:`
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
        profiles = _require_str_list(d, "profiles", where=where)
        return cls(
            channel=channel,
            depth=depth,
            required=required,
            gate_mode=gate_mode,
            profiles=profiles,
        )


@dataclass
class TargetSpec:
    """One ``targets:`` entry (ADR-047 §3)."""

    id: str = ""
    kind: str = TARGET_KIND_LIBRARY
    binary_pattern: str = ""
    public_headers: list[str] = field(default_factory=list)
    bundle: str = ""
    bundle_only: bool = False
    #: ``app-consumer`` only.
    consumer_binary_pattern: str = ""
    #: ``app-consumer``/``plugin-contract`` only — the ``kind: library``
    #: target this one resolves its baseline/candidate lookup through.
    library: str = ""
    #: ``plugin-contract`` only — a ``.syms`` file (one required linker
    #: symbol per line, ``#`` comments allowed), not YAML.
    contract_file: str = ""
    checks: list[CheckSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.kind == TARGET_KIND_LIBRARY:
            if self.binary_pattern:
                d["binary_pattern"] = self.binary_pattern
            if self.public_headers:
                d["public_headers"] = list(self.public_headers)
            if self.bundle:
                d["bundle"] = self.bundle
            if self.bundle_only:
                d["bundle_only"] = self.bundle_only
        elif self.kind == TARGET_KIND_APP_CONSUMER:
            d["consumer_binary_pattern"] = self.consumer_binary_pattern
            d["library"] = self.library
        elif self.kind == TARGET_KIND_PLUGIN_CONTRACT:
            d["contract_file"] = self.contract_file
            d["library"] = self.library
        if self.checks:
            d["checks"] = [c.to_dict() for c in self.checks]
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> TargetSpec:
        where = f"targets.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        known = {
            "kind",
            "binary_pattern",
            "public_headers",
            "bundle",
            "bundle_only",
            "consumer_binary_pattern",
            "library",
            "contract_file",
            "checks",
        }
        unknown = _unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        kind = d.get("kind", TARGET_KIND_LIBRARY)
        if not isinstance(kind, str) or kind not in TARGET_KINDS:
            raise ValueError(
                f"{where}.kind must be one of {sorted(TARGET_KINDS)}, got {kind!r}"
            )
        bundle_only = d.get("bundle_only", False)
        if not isinstance(bundle_only, bool):
            raise ValueError(f"{where}.bundle_only must be a boolean")
        checks = _parse_checks_list(d, where=where)
        return cls(
            id=name,
            kind=kind,
            binary_pattern=_opt_str_field(d, "binary_pattern", where=where),
            public_headers=_require_str_list(d, "public_headers", where=where),
            bundle=_opt_str_field(d, "bundle", where=where),
            bundle_only=bundle_only,
            consumer_binary_pattern=_opt_str_field(
                d, "consumer_binary_pattern", where=where
            ),
            library=_opt_str_field(d, "library", where=where),
            contract_file=_opt_str_field(d, "contract_file", where=where),
            checks=checks,
        )


@dataclass
class BundleSpec:
    """One ``bundles:`` entry (ADR-047 §3) — a release group of library targets.

    ``checks:`` (same ``{channel, depth, required, gate_mode, profiles}``
    shape as a target's — review finding, ADR-047 §5): the run-plan example
    emits a ``kind: "bundle"`` check entry alongside per-target ones (S14
    bundle-scoped analysis), and that cell needs its own baseline-channel/
    depth/gate policy just like a target's checks do — this plan's own
    ``checks:`` design note says "per target, **or per bundle**", which an
    earlier draft of this schema only implemented the target half of.
    """

    id: str = ""
    targets: list[str] = field(default_factory=list)
    checks: list[CheckSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"targets": list(self.targets)}
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
        unknown = _unknown_keys(d, {"targets", "checks"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        targets = d.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"{where}.targets must be a non-empty list of target ids")
        bad = [t for t in targets if not isinstance(t, str)]
        if bad:
            raise ValueError(f"{where}.targets must be a list of strings, got {bad!r}")
        checks = _parse_checks_list(d, where=where)
        return cls(id=name, targets=[str(t) for t in targets], checks=checks)


@dataclass
class ProfileSpec:
    """One ``profiles:`` entry (ADR-047 §3) — a build-lane identity.

    ``contract: true`` (default) means this profile is an ABI contract (gets
    a baseline, gates CI); ``contract: false`` marks a test-only CI lane that
    never gets a baseline (S17's point).
    """

    id: str = ""
    contract: bool = True
    os: str = ""
    arch: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"contract": self.contract}
        if self.os:
            d["os"] = self.os
        if self.arch:
            d["arch"] = self.arch
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> ProfileSpec:
        where = f"profiles.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(d, {"contract", "os", "arch"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        contract = d.get("contract", True)
        if not isinstance(contract, bool):
            raise ValueError(f"{where}.contract must be a boolean")
        for key in ("os", "arch"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        return cls(
            id=name,
            contract=contract,
            os=str(d.get("os", "") or ""),
            arch=str(d.get("arch", "") or ""),
        )


@dataclass
class BaselineChannelSpec:
    """One ``baseline: channels:`` entry (ADR-047 §3/§10)."""

    id: str = ""
    source: str = ""
    asset_pattern: str = ""
    key_prefix: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.source}
        if self.asset_pattern:
            d["asset_pattern"] = self.asset_pattern
        if self.key_prefix:
            d["key_prefix"] = self.key_prefix
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> BaselineChannelSpec:
        where = f"baseline.channels.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = _unknown_keys(d, {"source", "asset_pattern", "key_prefix"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        source = d.get("source")
        if not isinstance(source, str) or source not in BASELINE_SOURCES:
            raise ValueError(
                f"{where}.source must be one of {sorted(BASELINE_SOURCES)}, got {source!r}"
            )
        for key in ("asset_pattern", "key_prefix"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        return cls(
            id=name,
            source=source,
            asset_pattern=str(d.get("asset_pattern", "") or ""),
            key_prefix=str(d.get("key_prefix", "") or ""),
        )


@dataclass
class ProjectTargetsConfig:
    """Parsed ``targets:``/``bundles:``/``profiles:``/``baseline:`` block.

    All four sub-blocks are optional; an absent block yields an empty dict,
    matching the ``buildsource``-wide convention that a project not yet
    using G30's CI-integration primitives sees no behavior change at all.
    """

    targets: dict[str, TargetSpec] = field(default_factory=dict)
    bundles: dict[str, BundleSpec] = field(default_factory=dict)
    profiles: dict[str, ProfileSpec] = field(default_factory=dict)
    baseline_channels: dict[str, BaselineChannelSpec] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.targets:
            out["targets"] = {k: v.to_dict() for k, v in self.targets.items()}
        if self.bundles:
            out["bundles"] = {k: v.to_dict() for k, v in self.bundles.items()}
        if self.profiles:
            out["profiles"] = {k: v.to_dict() for k, v in self.profiles.items()}
        if self.baseline_channels:
            out["baseline"] = {
                "channels": {k: v.to_dict() for k, v in self.baseline_channels.items()}
            }
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectTargetsConfig:
        """Parse the four top-level blocks out of a raw ``.abicheck.yml`` mapping.

        Structural/type errors raise ``ValueError`` immediately (ADR-043
        strict-config convention — the same treatment ``BuildConfig`` gives
        the rest of ``.abicheck.yml``). Cross-reference/semantic issues
        (unknown ``library``/``bundle`` reference, kind-specific required
        fields, identifier charset) are **not** raised here — see
        :func:`validate_project_targets`, which needs the fully-assembled
        config to check references across blocks.

        **A clean parse from this method alone does not mean the config is
        usable.** ``CheckSpec.from_dict`` only checks ``depth``/``gate_mode``/
        ``channel`` are non-empty strings here; it does *not* check ``depth``
        is one of :data:`CHECK_DEPTHS`, ``gate_mode`` is one of
        :data:`GATE_MODES`, or ``channel`` resolves to a declared baseline
        channel — those (and every other cross-reference rule) are
        :func:`validate_project_targets`'s job. Every real caller (today,
        only ``abicheck project-targets validate``) must call both in
        sequence; treating a successful ``from_dict`` alone as "this config
        is valid" will let e.g. ``depth: "banana"`` through unnoticed.

        Every key in *data* is checked against the *full* ``.abicheck.yml``
        top-level key set (:data:`~.inline.KNOWN_TOP_LEVEL_KEYS`), not just
        this module's own four owned keys — a misspelled block (e.g.
        ``tagrets:``) would otherwise be silently ignored as an unrecognized,
        unrelated key rather than caught as the typo it is (review finding).
        Keys this module doesn't itself parse (``build``, ``severity``, ...)
        are still accepted here and simply ignored, since a real
        ``.abicheck.yml`` legitimately carries those alongside this block.
        """
        unknown_top = sorted((set(data) - KNOWN_TOP_LEVEL_KEYS), key=repr)
        if unknown_top:
            raise ValueError(f"unknown .abicheck.yml key(s) {unknown_top!r}")
        targets_raw = _require_mapping(data.get("targets"), "targets")
        bundles_raw = _require_mapping(data.get("bundles"), "bundles")
        profiles_raw = _require_mapping(data.get("profiles"), "profiles")
        baseline_raw = _require_mapping(data.get("baseline"), "baseline")
        unknown_baseline = sorted(set(baseline_raw) - {"channels"})
        if unknown_baseline:
            raise ValueError(f"baseline: unknown key(s) {unknown_baseline}")
        channels_raw = _require_mapping(
            baseline_raw.get("channels"), "baseline.channels"
        )

        targets = {
            name: TargetSpec.from_dict(name, t) for name, t in targets_raw.items()
        }
        bundles = {
            name: BundleSpec.from_dict(name, b) for name, b in bundles_raw.items()
        }
        profiles = {
            name: ProfileSpec.from_dict(name, p) for name, p in profiles_raw.items()
        }
        baseline_channels = {
            name: BaselineChannelSpec.from_dict(name, c)
            for name, c in channels_raw.items()
        }
        return cls(
            targets=targets,
            bundles=bundles,
            profiles=profiles,
            baseline_channels=baseline_channels,
        )


def load_project_targets_config(path: Path) -> ProjectTargetsConfig:
    """Load the ``targets:``/``bundles:``/``profiles:``/``baseline:`` block from
    a ``.abicheck.yml`` at *path*.

    Tolerant of a missing/empty file (yields an all-empty config), matching
    :func:`abicheck.buildsource.inline.load_build_config`'s same contract.
    """
    if not path.is_file():
        return ProjectTargetsConfig()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read project config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return ProjectTargetsConfig()
    return ProjectTargetsConfig.from_dict(raw)


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
            f"{_IDENTIFIER_RE.pattern!r} (the same charset ADR-047 §7's "
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


def _target_issues(config: ProjectTargetsConfig, target: TargetSpec) -> list[str]:
    issues = _identifier_issues("target", target.id)
    issues.extend(_forbidden_field_issues(target))
    if target.kind == TARGET_KIND_LIBRARY:
        if not target.binary_pattern:
            issues.append(
                f"target {target.id!r}: kind: library requires binary_pattern."
            )
        if target.bundle_only and not target.bundle:
            issues.append(
                f"target {target.id!r}: bundle_only requires bundle to be set."
            )
        if target.bundle_only and target.checks:
            issues.append(
                f"target {target.id!r}: bundle_only: true target must not set "
                "its own checks: — it is checked only as a bundle member, "
                "never standalone, so a target-level check here would never "
                "run; declare it under bundles:.checks instead."
            )
        if target.bundle:
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
    if target.kind != TARGET_KIND_LIBRARY:
        # actions/check-target/validate-inputs.sh rejects baseline-channel:
        # none for any target-kind other than library -- a no-baseline audit
        # routes to `scan` (a one-build check), which has no
        # --used-by/--required-symbols equivalent to scope an app-consumer/
        # plugin-contract check against. Reject at generation time rather
        # than letting a validated-looking config produce a run-plan cell
        # that check-target refuses with no per-cell report for aggregate
        # to read.
        for i, check in enumerate(target.checks):
            if check.channel == NO_BASELINE_CHANNEL:
                issues.append(
                    f"target {target.id!r}.checks[{i}]: channel: "
                    f"{NO_BASELINE_CHANNEL!r} is not supported for kind: "
                    f"{target.kind!r} -- a no-baseline audit check has no "
                    "--used-by/--required-symbols equivalent to scope an "
                    "app-consumer/plugin-contract check against "
                    "(actions/check-target/validate-inputs.sh). Use kind: "
                    "library for a no-baseline audit, or set a real channel."
                )
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
            "candidate lookup through a real library target only (ADR-047 §3)."
        ]
    return []


def _check_issues(
    config: ProjectTargetsConfig, where: str, check: CheckSpec
) -> list[str]:
    issues: list[str] = []
    if (
        check.channel != NO_BASELINE_CHANNEL
        and check.channel not in config.baseline_channels
    ):
        issues.append(
            f"{where}: channel {check.channel!r} is not declared under "
            f"baseline.channels: (use {NO_BASELINE_CHANNEL!r} for a no-baseline "
            "audit check, ADR-047 §6 S5)."
        )
    if check.depth not in CHECK_DEPTHS:
        issues.append(
            f"{where}: depth must be one of {sorted(CHECK_DEPTHS)}, got {check.depth!r}."
        )
    if check.gate_mode not in GATE_MODES:
        issues.append(
            f"{where}: gate_mode must be one of {sorted(GATE_MODES)}, got {check.gate_mode!r}."
        )
    for profile_id in check.profiles:
        profile = config.profiles.get(profile_id)
        if profile is None:
            issues.append(
                f"{where}: profiles entry {profile_id!r} is not declared under profiles:."
            )
        elif not profile.contract and check.channel != NO_BASELINE_CHANNEL:
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
    for i, check in enumerate(bundle.checks):
        if check.depth not in BUNDLE_CHECK_DEPTHS and check.depth in CHECK_DEPTHS:
            # A depth outside CHECK_DEPTHS entirely is already reported by
            # _check_issues below -- only flag the bundle-specific
            # restriction for an otherwise-valid depth (build/source).
            issues.append(
                f"bundle {bundle.id!r}.checks[{i}]: depth {check.depth!r} is not "
                f"supported for a bundle check -- use one of "
                f"{sorted(BUNDLE_CHECK_DEPTHS)} (actions/check-target/"
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
        issues.extend(_check_issues(config, f"bundle {bundle.id!r}.checks[{i}]", check))
    return issues


def _profile_issues(profile: ProfileSpec) -> list[str]:
    return _identifier_issues("profile", profile.id)


def _baseline_channel_issues(channel: BaselineChannelSpec) -> list[str]:
    issues = _identifier_issues("baseline channel", channel.id)
    if channel.id == NO_BASELINE_CHANNEL:
        issues.append(
            f"baseline channel {channel.id!r} is reserved as the no-baseline "
            "sentinel (ADR-047 §6 S5) and cannot be declared as a real "
            "channel — a checks[].channel: 'none' entry would then be "
            "ambiguous between 'skip resolve-baseline' and 'resolve this "
            "declared channel', and check-target always takes the former."
        )
    if channel.source == "github-release" and not channel.asset_pattern:
        issues.append(
            f"baseline channel {channel.id!r}: source: github-release requires "
            "asset_pattern (ADR-047 §10)."
        )
    if channel.source == "actions-cache" and not channel.key_prefix:
        issues.append(
            f"baseline channel {channel.id!r}: source: actions-cache requires "
            "key_prefix (ADR-047 §10)."
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

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

This module defines the contract and parses a hand-authored
``targets:``/``bundles:``/``profiles:``/``baseline:`` block — there is no
run-plan generator here yet (that's G30 P1.4, which *consumes* this) and no
``abicheck project-targets init`` scaffolding either. Pure: parses a dict,
never touches the filesystem beyond reading the one YAML file. Cross-reference
and kind-specific validation lives in :mod:`~.project_targets_validation`;
the compile-option denylist applied to ``compile:`` atoms lives in
:mod:`~.project_compile_arg_guard`; runner-label routing and the
``dependency_source`` vocabulary live in :mod:`~.project_profile_routing`.

``BuildConfig`` (:mod:`abicheck.buildsource.build_config`) recognizes
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
:class:`~.project_bundles.BundleSpec`, which also owns the bundle's scope
policy (``required:``/``on_incomplete:``). ``CheckSpec`` and the strict
field readers both parsers share live in :mod:`~.project_check_spec`.

``ProjectTargetsConfig.from_dict`` validates every top-level key in the raw
mapping against :data:`~.build_config.KNOWN_TOP_LEVEL_KEYS` — the *full*
``.abicheck.yml`` key set, not just this module's four owned keys — so a
misspelled block (``tagrets:``) is a hard error rather than silently
parsing as an empty, all-default config. Every ``targets:``/``bundles:``/
``profiles:``/``baseline.channels:`` mapping key must itself be a real YAML
string (PyYAML's default resolver reads a bare ``on``/``off``/``yes``/``no``
key as a bool and a bare digit key as an int; silently ``str()``-coercing
either would mint an id the user never actually wrote). ``"none"`` is
reserved and cannot be declared as a real ``baseline.channels`` id — it is
:data:`~.project_check_spec.NO_BASELINE_CHANNEL`, the sentinel a ``checks[].channel`` uses to
bypass ``resolve-baseline`` entirely (ADR-047 §6 S5); allowing a real
channel of that name would make the sentinel ambiguous with an actual
baseline lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model.evidence_depth_levels import USER_DEPTHS, EvidenceDepth
from ..model.header_ast_frontends import HEADER_AST_FRONTENDS
from .build_config import KNOWN_TOP_LEVEL_KEYS
from .project_bundles import BundleSpec
from .project_check_spec import (
    CheckSpec,
    opt_str_field,
    parse_checks_list,
    require_str_list,
    unknown_keys,
)
from .project_compile_arg_guard import safe_profile_atom
from .project_profile_routing import PROFILE_DEPENDENCY_SOURCES

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

#: A ``bundle`` check's ``depth`` is further restricted to this single rung --
#: ``kind: bundle`` always compares directories (the resolved binaries-dir vs.
#: the candidate bundle directory) in ``actions/check-target``, which routes
#: through the CLI's per-library release fan-out and never collects inline
#: build/source evidence for that path (``actions/check-target/
#: validate-inputs.sh`` rejects ``build``/``source`` for ``kind: bundle``).
#: ``headers`` is *also* rejected here (not just build/source): a bundle
#: baseline's old-library operand is always a directory of raw binaries
#: (``actions/baseline``'s bundle staging, unlike its single-target mode,
#: never produces pre-dumped ``.abi.json`` snapshots with historical header
#: data already baked in), so at ``depth: headers`` both the old and new
#: sides would be freshly header-parsed at compare time using the SAME
#: current checkout's headers (``check-project.yml`` has only one
#: project-wide ``header:`` input) -- a header-only change between baseline
#: and candidate (e.g. an inline function or template removed) would be
#: silently invisible, since only one version of the headers is ever parsed
#: (Codex review). The baseline half now exists (``bundle_member_snapshots``
#: stages it); the candidate half does not -- see that module's docstring.
BUNDLE_CHECK_DEPTHS = frozenset({EvidenceDepth.BINARY.value})


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
        unknown = unknown_keys(d, known)
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
        checks = parse_checks_list(d, where=where)
        return cls(
            id=name,
            kind=kind,
            binary_pattern=opt_str_field(d, "binary_pattern", where=where),
            public_headers=require_str_list(d, "public_headers", where=where),
            bundle=opt_str_field(d, "bundle", where=where),
            bundle_only=bundle_only,
            consumer_binary_pattern=opt_str_field(
                d, "consumer_binary_pattern", where=where
            ),
            library=opt_str_field(d, "library", where=where),
            contract_file=opt_str_field(d, "contract_file", where=where),
            checks=checks,
        )


@dataclass
class ProfileCompileSpec:
    """Optional ``profiles.<id>.compile`` overlay (P1 toolchain-profile audit).

    Declares the compiler/dialect/ABI contract axes a profile pins, as plain
    data — never an executable path or shell-interpretable string. Per the
    trust boundary this module's docstring and ``AGENTS.md`` "M1" describe: an
    auto-discovered (untrusted) ``.abicheck.yml`` may *declare* a compiler
    family, a version constraint, a target triple, a dialect/standard, a
    standard-library name, ABI macros, and normalized extra args — but never a
    raw executable path/command. ``binding`` is a *logical* identifier (e.g.
    ``"gcc14"``) meant to be resolved against a separately-trusted toolchain
    bindings file (an explicit ``--config``/CI-managed source), not looked up
    here — this module only validates shape, same as the rest of the file.
    The resolver itself is :func:`~.run_plan.generate_run_plan`'s
    *resolved_bindings* parameter (G30 P1.4); this dataclass stays the pure
    parse/validate layer either way.

    All fields are optional and additive; an empty ``ProfileCompileSpec`` is
    indistinguishable from an absent ``compile:`` block.

    ``frontend`` (G34 Phase B) overrides the global ``--ast-frontend``
    default for this profile's cell only -- one of the same four values
    the CLI flag accepts (``auto``/``castxml``/``clang``/``hybrid``,
    shape-validated the same way; empty means "no override, inherit the
    global default"). Applies identically whichever overlay it's set on
    (``compile:`` for the producer/artifact toolchain, ``consumer_compile:``
    for the client toolchain, G34 Phase 0) since both share this same
    dataclass.
    """

    compiler_family: str = ""
    compiler_version: str = ""
    target: str = ""
    standard: str = ""
    stdlib: str = ""
    binding: str = ""
    frontend: str = ""
    abi_macros: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self == ProfileCompileSpec()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.compiler_family:
            d["compiler_family"] = self.compiler_family
        if self.compiler_version:
            d["compiler_version"] = self.compiler_version
        if self.target:
            d["target"] = self.target
        if self.standard:
            d["standard"] = self.standard
        if self.stdlib:
            d["stdlib"] = self.stdlib
        if self.binding:
            d["binding"] = self.binding
        if self.frontend:
            d["frontend"] = self.frontend
        if self.abi_macros:
            d["abi_macros"] = dict(self.abi_macros)
        if self.args:
            d["args"] = list(self.args)
        return d

    @classmethod
    def from_dict(cls, where: str, d: dict[str, Any]) -> ProfileCompileSpec:
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        known = {
            "compiler_family",
            "compiler_version",
            "target",
            "standard",
            "stdlib",
            "binding",
            "frontend",
            "abi_macros",
            "args",
        }
        unknown = unknown_keys(d, known)
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")

        str_fields: dict[str, str] = {}
        for key in (
            "compiler_family",
            "compiler_version",
            "target",
            "standard",
            "stdlib",
            "binding",
        ):
            if key not in d:
                continue
            value = d[key]
            if not isinstance(value, str):
                raise ValueError(f"{where}.{key} must be a string, got {value!r}")
            str_fields[key] = safe_profile_atom(where, key, value)

        frontend = ""
        if "frontend" in d:
            value = d["frontend"]
            if not isinstance(value, str):
                raise ValueError(f"{where}.frontend must be a string, got {value!r}")
            if value not in HEADER_AST_FRONTENDS:
                allowed = ", ".join(sorted(HEADER_AST_FRONTENDS))
                raise ValueError(
                    f"{where}.frontend must be one of {{{allowed}}}, got {value!r}"
                )
            frontend = value

        abi_macros_raw = d.get("abi_macros", {})
        if not isinstance(abi_macros_raw, dict):
            raise ValueError(f"{where}.abi_macros must be a mapping")
        abi_macros: dict[str, str] = {}
        for macro_name, macro_value in abi_macros_raw.items():
            if not isinstance(macro_name, str) or not isinstance(macro_value, str):
                raise ValueError(
                    f"{where}.abi_macros entries must be string: string, "
                    f"got {macro_name!r}: {macro_value!r}"
                )
            abi_macros[safe_profile_atom(where, "abi_macros", macro_name)] = (
                safe_profile_atom(where, "abi_macros", macro_value)
                if macro_value
                else macro_value
            )

        args_raw = d.get("args", [])
        if not isinstance(args_raw, list):
            raise ValueError(f"{where}.args must be a list of strings")
        args: list[str] = []
        for a in args_raw:
            if not isinstance(a, str):
                raise ValueError(f"{where}.args must be a list of strings, got {a!r}")
            args.append(safe_profile_atom(where, "args", a, reject_dangerous=True))

        return cls(
            compiler_family=str_fields.get("compiler_family", ""),
            compiler_version=str_fields.get("compiler_version", ""),
            target=str_fields.get("target", ""),
            standard=str_fields.get("standard", ""),
            stdlib=str_fields.get("stdlib", ""),
            binding=str_fields.get("binding", ""),
            frontend=frontend,
            abi_macros=abi_macros,
            args=args,
        )


@dataclass
class ProfileSpec:
    """One ``profiles:`` entry (ADR-047 §3) — a build-lane identity.

    ``contract: true`` (default) means this profile is an ABI contract (gets
    a baseline, gates CI); ``contract: false`` marks a test-only CI lane that
    never gets a baseline (S17's point). The optional ``compile:`` overlay
    (:class:`ProfileCompileSpec`, P1 toolchain-profile audit) declares the
    compiler/dialect/ABI-macro axes this profile pins — additive over the
    root ``compile:`` block (:class:`~abicheck.buildsource.build_config.BuildConfig`);
    a run-plan consumer is expected to merge root-then-profile, same
    precedence as every other config layer in this project.

    The optional ``consumer_compile:`` overlay (G34 Phase 0, same
    :class:`ProfileCompileSpec` shape) separates the two axes ``compile:``
    otherwise conflates: ``compile:`` is the *producer/artifact* toolchain
    the library binary was actually built with; ``consumer_compile:`` is a
    *client* toolchain a user of the library compiles their own code with
    against the public headers, when it differs from the producer (e.g. a
    ``.so`` built once with GCC 14 but contractually supporting a Clang 20
    client under a different standard/standard-library). A profile with no
    ``consumer_compile:`` behaves exactly as today — its ``compile:`` block
    doubles as the consumer's, so existing single-toolchain projects need no
    edits. Shape-validated identically to ``compile:``; a run-plan/dumper
    consumer applying it to the header-AST (L2) extraction step only is not
    yet wired (see ``docs/contribute/plans/
    g34-producer-consumer-compiler-profile-separation.md``'s Phase 0).

    ``os:`` is no longer purely informational (G34 Phase C): it selects the
    runner a ``check-project.yml`` check cell for this profile is scheduled
    on (:func:`~.project_profile_routing.runner_label_for_os`). ``dependency_source:`` — new in the
    same phase — selects how that cell provisions its own system
    dependencies, so a GCC-profile cell and a Clang-profile cell in one run
    can each get a matching conda environment instead of sharing whatever
    the workflow-level input happened to say.
    """

    id: str = ""
    contract: bool = True
    os: str = ""
    arch: str = ""
    #: One of :data:`PROFILE_DEPENDENCY_SOURCES`, or ``""`` to inherit the
    #: caller's own workflow-level default (which is what every profile does
    #: today).
    dependency_source: str = ""
    compile: ProfileCompileSpec | None = None
    consumer_compile: ProfileCompileSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"contract": self.contract}
        if self.os:
            d["os"] = self.os
        if self.arch:
            d["arch"] = self.arch
        if self.dependency_source:
            d["dependency_source"] = self.dependency_source
        if self.compile is not None and not self.compile.is_empty:
            d["compile"] = self.compile.to_dict()
        if self.consumer_compile is not None and not self.consumer_compile.is_empty:
            d["consumer_compile"] = self.consumer_compile.to_dict()
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> ProfileSpec:
        where = f"profiles.{name}"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = unknown_keys(
            d,
            {
                "contract",
                "os",
                "arch",
                "dependency_source",
                "compile",
                "consumer_compile",
            },
        )
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        contract = d.get("contract", True)
        if not isinstance(contract, bool):
            raise ValueError(f"{where}.contract must be a boolean")
        for key in ("os", "arch"):
            if key in d and not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        dependency_source = ""
        if "dependency_source" in d:
            value = d["dependency_source"]
            if not isinstance(value, str):
                raise ValueError(
                    f"{where}.dependency_source must be a string, got {value!r}"
                )
            if value and value not in PROFILE_DEPENDENCY_SOURCES:
                allowed = ", ".join(sorted(PROFILE_DEPENDENCY_SOURCES))
                raise ValueError(
                    f"{where}.dependency_source must be one of {{{allowed}}}, "
                    f"got {value!r}"
                )
            dependency_source = value
        compile_spec: ProfileCompileSpec | None = None
        if "compile" in d:
            compile_spec = ProfileCompileSpec.from_dict(
                f"{where}.compile", d["compile"]
            )
        consumer_compile_spec: ProfileCompileSpec | None = None
        if "consumer_compile" in d:
            consumer_compile_spec = ProfileCompileSpec.from_dict(
                f"{where}.consumer_compile", d["consumer_compile"]
            )
        return cls(
            id=name,
            contract=contract,
            os=str(d.get("os", "") or ""),
            arch=str(d.get("arch", "") or ""),
            dependency_source=dependency_source,
            compile=compile_spec,
            consumer_compile=consumer_compile_spec,
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
        unknown = unknown_keys(d, {"source", "asset_pattern", "key_prefix"})
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
class AggregateGateSpec:
    """``aggregate: gate:`` (CLI cleanup phase two, PR 2 follow-up).

    The durable, project-owned home for the policy ``abicheck project plan``
    stamps onto the ``gate`` block of every ``run-plan.json`` it generates
    (:attr:`RunPlan.gate_missing_required`/
    :attr:`~.run_plan.RunPlan.gate_unexpected_target`) -- the same fields a
    hand-authored ``aggregate --manifest``'s own ``gate`` block carries, so
    the two entry points can never diverge in what they express. Before this,
    the only way to set this policy was ``project plan``'s own
    ``--gate-missing-required``/``--gate-unexpected-target`` flags, re-typed
    on every invocation; this block replaces both (removed, no CLI alias --
    same "no deprecation window" stance as the rest of this cleanup).

    Both fields are optional and independently settable, mirroring
    ``RunPlan``'s own "unset means no policy stated here" contract: an absent
    field is projected as absent from the generated ``run-plan.json``'s
    ``gate`` block too, so ``aggregate``'s own hard-coded defaults
    (``missing_required: fail``, ``unexpected_target: include``) apply
    exactly as they do with no ``aggregate:`` block at all.
    """

    missing_required: str | None = None
    unexpected_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.missing_required is not None:
            d["missing_required"] = self.missing_required
        if self.unexpected_target is not None:
            d["unexpected_target"] = self.unexpected_target
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AggregateGateSpec:
        """Validated against the same ``OnMissingRequired``/
        ``OnUnexpectedTarget`` enum vocabulary ``aggregate --manifest``'s own
        ``gate`` block is validated against (``ExpectedTargets.
        from_manifest_data``), so a bad value here is caught at ``project
        plan`` time rather than surfacing later at ``aggregate`` time from a
        run-plan.json this config produced. Imported lazily (function-local),
        the same way :mod:`abicheck.buildsource.run_plan` keeps its own
        ``..aggregate_manifest`` reference lazy, so this leaf validation
        module carries no module-level dependency on it.

        Key **absent** -> that field stays unset (``None``). Key **present**
        with an explicit YAML ``null`` is a hard error, not silently treated
        the same as absent (Codex review, fresh evidence) -- the two are
        different author intents (never mentioned this field at all, vs.
        stating it with a null value), and conflating them would let a
        malformed ``aggregate: gate: {missing_required: null}`` block
        silently fall back to the hard-coded ``fail``/``include`` defaults
        instead of failing closed on the typo. Mirrors
        :func:`abicheck.buildsource.run_plan_gate.parse_run_plan_gate`'s
        identical key/value distinction for the projected ``run-plan.json``
        ``gate`` block this class feeds.
        """
        from ..workflows.aggregate import OnMissingRequired, OnUnexpectedTarget

        where = "aggregate.gate"
        if not isinstance(d, dict):
            raise ValueError(
                f"{where} must be a mapping, got {type(d).__name__}: {d!r}"
            )
        unknown = unknown_keys(d, {"missing_required", "unexpected_target"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        missing_required: str | None = None
        if "missing_required" in d:
            missing_required = d["missing_required"]
            if missing_required is None:
                raise ValueError(f"{where}.missing_required must not be null")
            if not isinstance(missing_required, str) or missing_required not in {
                v.value for v in OnMissingRequired
            }:
                raise ValueError(
                    f"{where}.missing_required must be one of "
                    f"{[v.value for v in OnMissingRequired]}, got {missing_required!r}"
                )
        unexpected_target: str | None = None
        if "unexpected_target" in d:
            unexpected_target = d["unexpected_target"]
            if unexpected_target is None:
                raise ValueError(f"{where}.unexpected_target must not be null")
            if not isinstance(unexpected_target, str) or unexpected_target not in {
                v.value for v in OnUnexpectedTarget
            }:
                raise ValueError(
                    f"{where}.unexpected_target must be one of "
                    f"{[v.value for v in OnUnexpectedTarget]}, got {unexpected_target!r}"
                )
        return cls(
            missing_required=missing_required, unexpected_target=unexpected_target
        )

    def is_empty(self) -> bool:
        return self.missing_required is None and self.unexpected_target is None


@dataclass
class ProjectTargetsConfig:
    """Parsed ``targets:``/``bundles:``/``profiles:``/``baseline:``/
    ``aggregate:`` block.

    All five sub-blocks are optional; an absent block yields an empty dict
    (or ``None`` for :attr:`aggregate_gate`), matching the
    ``buildsource``-wide convention that a project not yet using G30's
    CI-integration primitives sees no behavior change at all.
    """

    targets: dict[str, TargetSpec] = field(default_factory=dict)
    bundles: dict[str, BundleSpec] = field(default_factory=dict)
    profiles: dict[str, ProfileSpec] = field(default_factory=dict)
    baseline_channels: dict[str, BaselineChannelSpec] = field(default_factory=dict)
    aggregate_gate: AggregateGateSpec | None = None

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
        if self.aggregate_gate is not None and not self.aggregate_gate.is_empty():
            out["aggregate"] = {"gate": self.aggregate_gate.to_dict()}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectTargetsConfig:
        """Parse the five top-level blocks out of a raw ``.abicheck.yml`` mapping.

        Structural/type errors raise ``ValueError`` immediately (ADR-043
        strict-config convention — the same treatment ``BuildConfig`` gives
        the rest of ``.abicheck.yml``). Cross-reference/semantic issues
        (unknown ``library``/``bundle`` reference, kind-specific required
        fields, identifier charset) are **not** raised here — see
        :func:`~.project_targets_validation.validate_project_targets`, which needs the fully-assembled
        config to check references across blocks.

        **A clean parse from this method alone does not mean the config is
        usable.** ``CheckSpec.from_dict`` only checks ``depth``/``gate_mode``/
        ``channel`` are non-empty strings here; it does *not* check ``depth``
        is one of :data:`CHECK_DEPTHS`, ``gate_mode`` is one of
        :data:`GATE_MODES`, or ``channel`` resolves to a declared baseline
        channel — those (and every other cross-reference rule) are
        :func:`~.project_targets_validation.validate_project_targets`'s job. Every real caller (today,
        only ``abicheck project-targets validate``) must call both in
        sequence; treating a successful ``from_dict`` alone as "this config
        is valid" will let e.g. ``depth: "banana"`` through unnoticed.

        Every key in *data* is checked against the *full* ``.abicheck.yml``
        top-level key set (:data:`~.build_config.KNOWN_TOP_LEVEL_KEYS`), not just
        this module's own five owned keys — a misspelled block (e.g.
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
        aggregate_raw = _require_mapping(data.get("aggregate"), "aggregate")
        unknown_aggregate = sorted(set(aggregate_raw) - {"gate"})
        if unknown_aggregate:
            raise ValueError(f"aggregate: unknown key(s) {unknown_aggregate}")
        aggregate_gate: AggregateGateSpec | None = None
        if "gate" in aggregate_raw:
            aggregate_gate = AggregateGateSpec.from_dict(aggregate_raw["gate"])

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
            aggregate_gate=aggregate_gate,
        )


def load_project_targets_config(path: Path) -> ProjectTargetsConfig:
    """Load the ``targets:``/``bundles:``/``profiles:``/``baseline:`` block from
    a ``.abicheck.yml`` at *path*.

    Tolerant of a missing/empty file (yields an all-empty config), matching
    :func:`abicheck.buildsource.build_config.load_build_config`'s same contract.
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

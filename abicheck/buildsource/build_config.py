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


"""``.abicheck.yml`` project-config schema: :class:`BuildConfig` + loading.

Every recognized key is declared once, in :data:`KEY_TABLE` below, as a
:class:`~abicheck.buildsource.build_config_schema.KeySpec` row. That table
drives the strict load (unknown keys, wrong YAML types, bad enum values, all
collected into one usage error), the ``BuildConfig`` construction, and the
non-default ``to_dict()`` round-trip. The mechanism lives in
``build_config_schema.py``; the table lives here because it needs the policy
enumeration in :mod:`abicheck.policy.support_promise`.

:mod:`abicheck.buildsource.inline` re-exports :class:`BuildConfig`,
:func:`load_build_config`, :func:`discover_build_config`, and
:data:`KNOWN_TOP_LEVEL_KEYS` for back-compat, so existing imports keep working.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..config_paths import discover_build_config as _discover_build_config
from ..environment_matrix import EnvironmentMatrix
from ..model.ownership_rules import OwnershipRules
from ..model.performance import parse_performance_profile
from ..policy.support_promise import (
    SUPPORT_PROMISE_POLICIES as _SUPPORT_PROMISE_POLICIES,
)
from .build_config_acknowledgment import (
    AcknowledgmentConfig,
    acknowledgment_block,
    acknowledgment_findings,
    parse_acknowledgment_config,
)
from .build_config_contract import (
    contract_block,
    contract_findings,
    parse_contract_post_manifest,
)
from .build_config_overrides import apply_config_overrides
from .build_config_schema import (
    KeySpec,
    KeyTable,
    Kind,
    bundle_variants_block_findings,
    deployment_findings,
    parse_debug_info_entry,
)
from .build_config_scope import (
    dependencies_findings,
    dependency_evidence_findings,
    ownership_block,
    parse_ownership_rules,
)
from .compile_options_safety import (  # PR #1146 finding #2 (sibling leaf module)
    reject_plugin_loading_options as _reject_plugin_loading_options,
)

#: Valid per-category severity levels (ADR-037 D4 ``severity:`` block).
_SEVERITY_LEVELS = ("error", "warning", "info")
#: Valid severity presets (mirror of ``severity.SEVERITY_PRESETS`` spelling).
_SEVERITY_PRESETS = ("default", "strict", "info-only")
#: Recognized top-level keys parsed by sibling modules (project_targets.py,
#: ADR-047) or reserved without a parser, so they must not trip the
#: unknown-key error. ``risk_rules`` was consumed by the deleted ``risk.py``
#: (ADR-068 Phase 6) and ``crosschecks`` was never parsed by anything.
_OPAQUE_TOP_KEYS = frozenset(
    {
        "risk_rules",
        "crosschecks",
        "targets",
        "bundles",
        "profiles",
        "baseline",
        "aggregate",
    }
)


def _enum(allowed: tuple[str, ...] | frozenset[str], *, lower: bool = False) -> Any:
    """A normalizer: the value (case-folded when *lower*) must be one of *allowed*."""

    def check(value: str, path: str) -> str | None:
        return _one_of(_lowered(value) if lower else value, allowed, path)

    return check


def _one_of(
    value: str | None, allowed: tuple[str, ...] | frozenset[str], where: str
) -> str | None:
    """*value* if it is ``None`` or a member of *allowed*, else ``ValueError``."""
    if value is not None and value not in allowed:
        raise ValueError(f"{where} must be one of {allowed}, got {value!r}")
    return value


def _lowered(value: str | None) -> str | None:
    """Case-normalize an optional enum-ish config scalar."""
    return value.lower() if value is not None else None


def _safe_compile_atom(path: str, value: str) -> str:
    """One ``compile.*`` scalar, rejected unless it is a single argv atom.

    Values from auto-discovered source-tree configs are later embedded in
    individual compiler flags (``-std=<value>``/``-D<value>``) and flow through
    legacy shlex-split ``gcc_options`` plumbing. Reject whitespace so one
    config scalar cannot become multiple compiler arguments such as
    ``-Xclang -load ./evil.so``.
    """
    if not value or any(ch.isspace() for ch in value):
        raise ValueError(f"{path} must be a single compiler option atom, got {value!r}")
    return value


#: One validator, shared by every ``severity:`` level row.
_SEVERITY_LEVEL = _enum(_SEVERITY_LEVELS)


def _system(value: str, path: str) -> str:  # noqa: ARG001 -- KeySpec normalize(value, path) signature
    return value or "auto"


def _graph_detail(value: str, path: str) -> str:
    return _one_of(value or "summary", ("summary", "full"), path) or "summary"


def _compile_atom(value: str, path: str) -> str:
    return _safe_compile_atom(path, value)


def _compile_atoms(values: list[str], path: str) -> list[str]:
    return [_safe_compile_atom(path, item) for item in values]


def _compile_options(values: list[str], path: str) -> list[str]:
    atoms = _compile_atoms(values, path)
    _reject_plugin_loading_options(atoms)
    return atoms


def _stripped(values: list[str], path: str) -> list[str]:  # noqa: ARG001 -- KeySpec normalize(value, path) signature
    """Entries stripped once at the one choke point every consumer reads through,
    with whitespace-only entries dropped (Codex review: a quoted entry with stray
    whitespace matched one consumer's SONAME comparison and missed another's)."""
    return [s.strip() for s in values if s.strip()]


def _debug_info(values: list[str], path: str) -> list[str]:  # noqa: ARG001 -- KeySpec normalize(value, path) signature
    """``debug.info`` entries, each checked against the ``--debug-info`` grammar."""
    for value in values:
        parse_debug_info_entry(value)
    return [str(value).strip() for value in values]


def _performance_profile(value: str, path: str) -> str:  # noqa: ARG001 -- KeySpec normalize(value, path) signature
    """``performance.profile`` as its canonical spelling (an unknown value is a
    ``ValueError`` naming the valid ones)."""
    try:
        return parse_performance_profile(str(value)).value
    except ValueError as exc:
        raise ValueError(f"performance.profile: {exc}") from None


def _read_deployment(top: dict[str, Any]) -> EnvironmentMatrix | None:
    return EnvironmentMatrix.from_dict_or_none(top.get("deployment"), strict=True)


def _write_deployment(cfg: Any) -> dict[str, Any]:
    return EnvironmentMatrix.dump_or_empty(cfg.deployment)


#: Every recognized ``.abicheck.yml`` key: one row per key. Dump order is this
#: order (blocks are emitted at their first row). Adding a key means adding one
#: row here plus its dataclass field; nothing else in this file or in
#: ``build_config_schema.py`` needs touching.
KEY_TABLE = KeyTable(
    (
        KeySpec("build.system", Kind.STR, "system", "auto", normalize=_system),
        KeySpec("build.query", Kind.STR, "query", ""),
        KeySpec("build.compile_db", Kind.STR, "compile_db", ""),
        KeySpec("build.compile_db_filter", Kind.STR, "compile_db_filter", ""),
        KeySpec("build.targets", Kind.LIST, "targets", []),
        KeySpec("sources.public_headers", Kind.LIST, "public_headers", []),
        KeySpec("sources.exclude", Kind.LIST, "exclude", []),
        KeySpec(
            "sources.graph",
            Kind.STR,
            "graph_detail",
            "summary",
            normalize=_graph_detail,
        ),
        KeySpec(
            "severity.preset",
            Kind.STR,
            "severity_preset",
            normalize=_enum(_SEVERITY_PRESETS),
        ),
        KeySpec(
            "severity.abi_breaking",
            Kind.STR,
            "severity_abi_breaking",
            normalize=_SEVERITY_LEVEL,
        ),
        KeySpec(
            "severity.potential_breaking",
            Kind.STR,
            "severity_potential_breaking",
            normalize=_SEVERITY_LEVEL,
        ),
        KeySpec(
            "severity.quality_issues",
            Kind.STR,
            "severity_quality_issues",
            normalize=_SEVERITY_LEVEL,
        ),
        KeySpec(
            "severity.addition",
            Kind.STR,
            "severity_addition",
            normalize=_SEVERITY_LEVEL,
        ),
        KeySpec("scope.public", Kind.BOOL, "scope_public"),
        KeySpec(
            "scope.collapse_versioned_symbols", Kind.BOOL, "collapse_versioned_symbols"
        ),
        KeySpec("scope.public_symbols", Kind.LIST, "public_symbols", []),
        KeySpec("output.view", Kind.LIST, "output_view", []),
        KeySpec("consumers.manifests", Kind.LIST, "used_by_manifests", []),
        KeySpec("consumers.required_symbols", Kind.LIST, "required_symbols", []),
        KeySpec("scope.show_redundant", Kind.BOOL, "scope_show_redundant"),
        KeySpec("scope.public_header_dirs", Kind.LIST, "public_header_dirs", []),
        KeySpec("scope.exclude_headers", Kind.LIST, "exclude_headers", []),
        KeySpec(
            "scope.on_incomplete",
            Kind.STR,
            "scope_on_incomplete",
            normalize=_enum(("warn", "block")),
        ),
        # Ownership keys (ADR-075): validated here, parsed as one group by the
        # `scope` GROUP row below, which also owns their dump.
        KeySpec("scope.dependencies", Kind.CUSTOM, validate=dependencies_findings),
        KeySpec(
            "scope.dependency_evidence",
            Kind.CUSTOM,
            validate=dependency_evidence_findings,
        ),
        KeySpec("scope.private_headers", Kind.LIST),
        KeySpec("scope.private_namespaces", Kind.LIST),
        KeySpec(
            "scope",
            Kind.GROUP,
            read=lambda scope: {"ownership": parse_ownership_rules(scope)},
            write=ownership_block,
        ),
        KeySpec(
            "contract",
            Kind.WHOLE,
            "contract_post_manifest",
            validate=contract_findings,
            read=parse_contract_post_manifest,
            write=contract_block,
        ),
        KeySpec(
            "acknowledgments",
            Kind.WHOLE,
            "acknowledgment",
            validate=acknowledgment_findings,
            read=parse_acknowledgment_config,
            write=acknowledgment_block,
        ),
        KeySpec("suppression.strict", Kind.BOOL, "suppression_strict"),
        KeySpec(
            "suppression.require_justification",
            Kind.BOOL,
            "suppression_require_justification",
        ),
        KeySpec("sources.method", Kind.STR, "source_method"),
        KeySpec(
            "compile.frontend",
            Kind.STR,
            "compile_frontend",
            normalize=_enum(("auto", "castxml", "clang", "hybrid"), lower=True),
        ),
        KeySpec("compile.std", Kind.STR, "compile_std", normalize=_compile_atom),
        KeySpec("compile.include_dirs", Kind.LIST, "compile_include_dirs", []),
        KeySpec(
            "compile.defines",
            Kind.LIST,
            "compile_defines",
            [],
            normalize=_compile_atoms,
        ),
        KeySpec("compile.sysroot", Kind.STR, "compile_sysroot"),
        KeySpec("compile.nostdinc", Kind.BOOL, "compile_nostdinc"),
        KeySpec("compile.compiler", Kind.STR, "compile_compiler"),
        KeySpec(
            "compile.options",
            Kind.LIST,
            "compile_options",
            [],
            normalize=_compile_options,
        ),
        KeySpec(
            "compile.ast_frontend_fallback", Kind.BOOL, "compile_ast_frontend_fallback"
        ),
        KeySpec(
            "compile.allow_unsupported_castxml",
            Kind.BOOL,
            "compile_allow_unsupported_castxml",
        ),
        KeySpec(
            "compile.frontend_context",
            Kind.STR,
            "compile_frontend_context",
            normalize=_enum(("host", "device"), lower=True),
        ),
        KeySpec(
            "compile.lang",
            Kind.STR,
            "compile_lang",
            normalize=_enum(("c++", "c"), lower=True),
        ),
        KeySpec(
            "debug.format",
            Kind.STR,
            "debug_format",
            normalize=_enum(("auto", "dwarf", "btf", "ctf"), lower=True),
        ),
        KeySpec("debug.dwarf_only", Kind.BOOL, "debug_dwarf_only"),
        KeySpec("debug.debuginfod", Kind.BOOL, "debug_debuginfod"),
        KeySpec("debug.debuginfod_url", Kind.STR, "debug_debuginfod_url"),
        KeySpec("debug.pdb_path", Kind.STR, "debug_pdb_path"),
        KeySpec("debug.info", Kind.LIST, "debug_info", [], normalize=_debug_info),
        KeySpec("deps.follow", Kind.BOOL, "deps_follow"),
        KeySpec(
            "deps.search_paths", Kind.LIST, "deps_search_paths", [], normalize=_stripped
        ),
        KeySpec("deps.ld_library_path", Kind.STR, "deps_ld_library_path"),
        KeySpec(
            "bundle.system_providers",
            Kind.LIST,
            "bundle_system_providers",
            [],
            normalize=_stripped,
        ),
        KeySpec("bundle.cohorts", Kind.LIST, "bundle_cohorts", [], normalize=_stripped),
        KeySpec("python.abi3_floor", Kind.STR, "python_abi3_floor"),
        KeySpec(
            "gate.fail_on_removed_library", Kind.BOOL, "gate_fail_on_removed_library"
        ),
        KeySpec("release.dso_only", Kind.BOOL, "release_dso_only"),
        KeySpec(
            "release.include_private_dso", Kind.BOOL, "release_include_private_dso"
        ),
        KeySpec(
            "release.support_promise",
            Kind.STR,
            "release_support_promise",
            normalize=_enum(_SUPPORT_PROMISE_POLICIES),
        ),
        KeySpec(
            "resource_limits.max_bundle_facts_decode_nodes",
            Kind.INT,
            "resource_limits_max_bundle_facts_decode_nodes",
        ),
        KeySpec("policy.overrides", Kind.DICT_STR, "policy_overrides", {}),
        KeySpec("assurance.require_complete", Kind.BOOL, "assurance_require_complete"),
        KeySpec(
            "performance.profile",
            Kind.STR,
            "performance_profile",
            normalize=_performance_profile,
        ),
        KeySpec(
            "deployment",
            Kind.WHOLE,
            "deployment",
            validate=deployment_findings,
            read=_read_deployment,
            write=_write_deployment,
            always_emit=True,
        ),
        KeySpec("bundle_variants", Kind.WHOLE, validate=bundle_variants_block_findings),
        KeySpec("version", Kind.INT, "version", 0),
    ),
    _OPAQUE_TOP_KEYS,
)


@dataclass
class BuildConfig:
    """Parsed ``.abicheck.yml`` project config (ADR-028 amendment D4 + ADR-037 D4).

    All fields are optional; an absent file yields the all-defaults config. The
    ``build:`` / ``sources:`` blocks drive inline build/source collection
    (``system`` is advisory; ``query`` runs only with an explicit ``--config``;
    ``compile_db`` is where it lands).

    ADR-037 D4 adds the project-contract blocks consumed by ``compare`` — the
    settings that are stable, reviewed-in-a-PR properties rather than per-run
    invocation flags: ``severity:`` (per-category levels + preset), ``scope:``
    (public-surface FP tuning), ``suppression:`` (hygiene policy), ``source:``
    (precise S-axis), plus the top-level ``version:``. CLI cleanup phase two
    PR G2 removed the top-level ``exit_code_scheme:`` key -- the one
    automatic gate algorithm (ADR-064) is no longer user-selectable; see
    :func:`abicheck.workflows.compare_configuration.resolve_compare_config`'s own
    ``exit_code_scheme`` field docstring for the replacement, purely-derived
    computation. CLI flags override these; see :func:`abicheck.workflows.compare_configuration.resolve_compare_config`
    for the precedence resolver (CLI > config > built-in default).

    A field left at its ``None`` / ``""`` / empty default means "unset — inherit
    the next level down", which is what makes the precedence merge unambiguous.
    """

    system: str = "auto"
    query: str = ""
    compile_db: str = ""
    #: ``build.compile_db_filter`` -- §4.2's CONFIG row, the former
    #: ``dump --compile-db-filter`` (``""`` = unset).
    compile_db_filter: str = ""
    targets: list[str] = field(
        default_factory=list
    )  # build.targets (P0.2): root target(s) scoping L3 collection (Bazel only). Empty = unscoped.
    public_headers: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    #: L5 source-graph detail cap (ADR-037 D6): ``summary`` (default — changed
    #: scope, the cheap CI graph) or ``full`` (full replay scope). The user no
    #: longer selects a ``graph-*`` mode on the CLI; ``--depth source`` builds the
    #: graph at this configured detail.
    graph_detail: str = "summary"

    # ── ADR-037 D4: project-contract blocks (consumed by `compare`) ───────────
    #: ``severity:`` — preset + per-category overrides. ``None`` = unset.
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None
    #: ``scope:`` — public-surface FP tuning. ``scope_public``/``collapse_*``
    #: are ``None`` when unset so the CLI flag can override either way.
    scope_public: bool | None = None
    collapse_versioned_symbols: bool | None = None
    public_symbols: list[str] = field(default_factory=list)
    #: ``output.view``: the ``compare`` rendering selector tokens (the same
    #: grammar as the removed ``--view``), applied when the run has one.
    output_view: list[str] = field(default_factory=list)
    #: ``consumers.manifests``/``consumers.required_symbols``: the ADR-043
    #: scoped-gate inputs that ``--used-by-manifest``/``--required-symbol``
    #: used to carry on the command line. Paths resolve against the config.
    used_by_manifests: list[str] = field(default_factory=list)
    required_symbols: list[str] = field(default_factory=list)
    #: ``scope.public_header_dirs`` (ADR-068 plan §5 P4): directories
    #: (project-root-relative or absolute) whose headers are this project's
    #: public/internal boundary for the boundary-dependent cross-source checks
    #: (``workflows/cross_source_hygiene.py``). Unrelated to the
    #: ``scope.public`` boolean above (header-origin scoping, the config home
    #: of the retired ``--scope-public-headers``). Folded in beside any ``-H``
    #: *directory* through ``InputSpec.public_header_dirs``; empty by default,
    #: which evidence-gates those checks to ``NOT_EVALUATED``. Full rationale:
    #: ``docs/reference/config-file.md``'s ``scope:`` section.
    public_header_dirs: list[str] = field(default_factory=list)
    #: ``scope.exclude_headers``: the config spelling of ``--exclude-header``
    #: -- fnmatch patterns dropping headers from the *parsed* surface.
    #: Deliberately **not** ``sources.exclude``, which narrows *source
    #: collection*: different inputs, different layers, neither implying the
    #: other. Weaker than the flag and never unioned with it, since the rule
    #: set is the run's scope identity. Full rationale:
    #: ``docs/reference/config-file.md``'s ``scope:`` section.
    exclude_headers: list[str] = field(default_factory=list)
    #: ``scope.dependencies``/``private_headers``/``private_namespaces``
    #: (``extract.ownership``); see ``build_config_scope.py``.
    ownership: OwnershipRules = field(default_factory=OwnershipRules)
    #: ``contract.overlays.post_manifest`` (Phase 9c); see ``build_config_contract.py``.
    contract_post_manifest: str | None = None
    #: ``acknowledgments:`` (ADR-067 D5/D6); see ``build_config_acknowledgment.py``.
    acknowledgment: AcknowledgmentConfig | None = None
    #: ``scope.show_redundant`` — a reporting/FP-tuning toggle demoted off the CLI
    #: (ADR-040 Lever 2). ``None`` = unset. The ``--show-filtered`` debugging view
    #: stays a visible CLI flag.
    scope_show_redundant: bool | None = None
    #: ``scope.on_incomplete`` — Phase 7d (one-comparison-product.md §4.1):
    #: the former ``compare --on-incomplete-scope`` (ADR-065 D6), demoted off
    #: the CLI entirely (no surviving override) -- a project's CI strictness
    #: for an incompletely checked directory/package release scope is a
    #: stable property, not a per-run choice. ``None`` = unset, defaulting to
    #: ``"warn"`` (the removed flag's own default) downstream.
    scope_on_incomplete: str | None = None
    #: ``suppression:`` — hygiene policy (a project rule, not a per-run flag).
    suppression_strict: bool | None = None
    suppression_require_justification: bool | None = None
    #: ``source:`` — precise S-axis for power users (``s0``..``s6``/``auto``).
    source_method: str | None = None
    #: ``compile:`` — the stable half of the L2 header compile context (ADR-035
    #: D6.1 / ADR-037 D4). The project's reviewed include roots / dialect / feature
    #: macros / frontend; per-invocation cross-compile flags stay CLI overrides
    #: (CLI > config). ``None``/empty = unset, so the CLI flag wins unambiguously.
    compile_frontend: str | None = None
    compile_std: str | None = None
    compile_include_dirs: list[str] = field(default_factory=list)
    compile_defines: list[str] = field(default_factory=list)
    compile_sysroot: str | None = None
    compile_nostdinc: bool | None = None
    #: Phase 7 (one-comparison-product.md §4.1/§4.2, ADR-037 D8.1): the
    #: remaining compile-context knobs demoted off ``compare``/``dump``
    #: entirely (CONFIG class — no surviving CLI override, unlike the
    #: ``debug:`` knobs below which still keep a CLI escape hatch on
    #: ``compare``/``dump``... no, see the module docstring: as of Phase 7
    #: those are gone too). ``compile.compiler`` merges the former
    #: ``--compiler``/``--compiler-prefix`` pair into one spelling: a value
    #: ending in ``-`` is a cross-toolchain *prefix* (``gcc_prefix``,
    #: e.g. ``"aarch64-linux-gnu-"``); anything else is a full path to the
    #: compiler binary (``gcc_path``). ``compile.options`` is the former
    #: ``--compiler-option`` — repeatable single compiler flags, each a
    #: single-atom string like ``compile.std``/``compile.defines`` above.
    compile_compiler: str | None = None
    compile_options: list[str] = field(default_factory=list)
    #: ``compile.ast_frontend_fallback``/``compile.allow_unsupported_castxml``
    #: are the former ``--allow-ast-frontend-fallback``/
    #: ``--allow-unsupported-castxml`` flags, which were themselves already
    #: pure env-var togglers (``ABICHECK_ALLOW_AST_FALLBACK``/
    #: ``ABICHECK_ALLOW_UNSUPPORTED_CASTXML``) scoped to one invocation —
    #: a config ``true`` here has the identical effect, applied for the
    #: whole run.
    compile_ast_frontend_fallback: bool | None = None
    compile_allow_unsupported_castxml: bool | None = None
    #: ``compile.frontend_context`` — the former ``--frontend-context``
    #: (``"host"``/``"device"``); unset defers to :data:`CompileContext`'s
    #: own ``"host"`` default.
    compile_frontend_context: str | None = None
    #: ``compile.lang`` — the former ``--lang`` (``"c++"``/``"c"``) on
    #: ``compare``/``dump``. No source/header-content language sniffer
    #: exists in this codebase to infer it from evidence (investigated;
    #: none found), so this stays a plain declared property defaulting to
    #: ``cli_options.LANG_DEFAULT`` ("c++") when unset — the same default
    #: the removed CLI flag carried, so an unconfigured project's behavior
    #: is unchanged.
    compile_lang: str | None = None
    #: ``debug:`` — separate-debug-file resolution (ADR-021a) demoted off the CLI
    #: (ADR-040 Lever 2, Phase 7). Stable per-project debug-artifact knobs; the
    #: coarse per-run ``--debug-root`` stays a visible CLI input on both
    #: ``compare``/``dump``. ``None`` = unset.
    debug_format: str | None = None
    debug_dwarf_only: bool | None = None
    debug_debuginfod: bool | None = None
    debug_debuginfod_url: str | None = None
    #: ``debug.pdb_path`` — Phase 7 7c: the former ``dump --pdb-path``
    #: (explicit PE/PDB path override, superseding automatic discovery).
    debug_pdb_path: str | None = None
    #: ``debug.info``/``deps.*`` -- the config homes of ``--debug-info``,
    #: ``--follow-deps``, ``--search-path`` and ``--ld-library-path`` (ADR-091
    #: D4, 2026-10-31 amendment); the flags stay and win. Resolved by
    #: ``workflows/config_run_inputs.py``. ``debug_info`` entries use the
    #: flag's ``[old=|new=]PATH`` grammar.
    debug_info: list[str] = field(default_factory=list)
    deps_follow: bool | None = None
    deps_search_paths: list[str] = field(default_factory=list)
    deps_ld_library_path: str | None = None
    #: ``bundle:`` — release/scan bundle topology (CLI cleanup phase two,
    #: PR J), demoted off the CLI from ``--bundle-system-providers``/
    #: ``--bundle-cohort``: a project's system-provider allow-list extension
    #: and co-versioned library cohort prefixes are stable, reviewed-in-a-PR
    #: properties of the release/bundle, not a per-run invocation flag —
    #: unlike ``severity:``/``scope:`` above, there is no CLI override at
    #: all, so ``.abicheck.yml`` is these two fields' only source. Empty =
    #: no extension beyond the built-in system-provider allow-list / no
    #: declared cohorts.
    bundle_system_providers: list[str] = field(default_factory=list)
    bundle_cohorts: list[str] = field(default_factory=list)
    #: ``python:`` — the project's CPython stable-ABI (``abi3``) floor, e.g.
    #: ``"3.9"`` (ADR-068 D5, plan §3 #15): which ``Py_LIMITED_API`` version
    #: this project promises is a stable property of the project, reviewed in
    #: a PR, not a per-run decision — so it lives here, with ``compare
    #: --abi3`` acting as the per-run override on top of it. ``None`` = unset
    #: (no audit unless the flag asks for one). Kept as the raw spelling the
    #: flag accepts, validated at the same place the flag is
    #: (``workflows.compare.enrichment.parse_abi3_floor``), so config and CLI cannot accept
    #: different version syntaxes.
    python_abi3_floor: str | None = None
    #: ``gate:`` — Phase 7d (one-comparison-product.md §4.1): CI gate policy
    #: demoted off the CLI. ``gate.fail_on_removed_library`` is the former
    #: ``compare --fail-on-removed-library`` (exit 8 when a library present
    #: in OLD is proven removed in NEW, ADR-065 D2) -- a stable project
    #: policy, no surviving CLI override. ``None`` = unset (no exit-8 gate).
    gate_fail_on_removed_library: bool | None = None
    #: ``release:`` — Phase 7d: directory/package release topology demoted
    #: off the CLI, same shape as ``bundle:`` above (no surviving override).
    #: The former ``compare --dso-only`` / ``--include-private-dso``.
    #: ``None`` = unset (both default to the removed flags' own ``False``).
    release_dso_only: bool | None = None
    release_include_private_dso: bool | None = None
    #: ``release.support_promise`` -- Phase 7: the former
    #: ``compare --support-promise`` (``off``/``declared``, ``None`` = unset).
    release_support_promise: str | None = None
    #: ``resource_limits:`` — Phase 7g: former ``--max-json-object-nodes``.
    resource_limits_max_bundle_facts_decode_nodes: int | None = None
    #: ``performance.profile`` -- the memory/speed trade-off (``None`` = unset).
    performance_profile: str | None = None
    #: ``assurance:`` — rulings.py deferred-option followup: the former
    #: ``compare --require-complete-analysis`` (P0.4's orthogonal assurance
    #: floor: fail the build when ``analysis_assurance.status`` is not
    #: "complete", independent of the compatibility verdict) -- a project's
    #: CI strictness is a stable property, no surviving CLI override, same
    #: shape as ``gate.fail_on_removed_library`` above. ``None`` = unset (no
    #: exit-1 assurance gate).
    assurance_require_complete: bool | None = None
    #: ``deployment:`` — ADR-020b §4.1 / ADR-068 D5: the former
    #: ``compare --env-matrix FILE``. Embeds ``EnvironmentMatrix``'s YAML
    #: shape inline (parsed via ``EnvironmentMatrix.from_dict``) instead of
    #: a side file kept in sync by hand. ``None`` = unset; no surviving CLI
    #: override at all (guard 2, no escape hatch).
    deployment: EnvironmentMatrix | None = None
    #: ``version:`` — config schema version (forward-compat; Phase 7 wires the
    #: unknown-key warning). ``0`` = unset.
    version: int = 0
    #: ``policy.overrides`` (ADR-068 §3 #23's `--crosscheck` replacement) --
    #: raw ``ChangeKind`` slug -> severity strings; see
    #: `docs/reference/config-file.md`'s own `policy:` section.
    policy_overrides: dict[str, str] = field(default_factory=dict)

    #: Recognized top-level / block key registries, derived from :data:`KEY_TABLE`.
    _KNOWN_TOP_KEYS: ClassVar[frozenset[str]] = KEY_TABLE.known_top
    _KNOWN_BLOCK_KEYS: ClassVar[dict[str, frozenset[str]]] = KEY_TABLE.known_blocks

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BuildConfig:
        """Parse a raw ``.abicheck.yml`` mapping into a :class:`BuildConfig`.

        Strict (ADR-043): every structural problem -- unknown key, wrong YAML
        type, bad enum value -- is collected and raised together as one
        ``ValueError``, so a single bad file reports everything wrong with it.
        """
        if isinstance(data, dict):
            findings = KEY_TABLE.findings(data)
            if findings:
                raise ValueError("; ".join(findings))
        top = data if isinstance(data, dict) else {}
        return cls(**KEY_TABLE.read(top))

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to a ``.abicheck.yml`` mapping (round-trips via from_dict).

        Only non-default blocks/keys are emitted so a dumped config stays minimal
        and a reload reproduces the same :class:`BuildConfig` (ADR-037 D4
        round-trip contract, ``test_config_roundtrip``).
        """
        return KEY_TABLE.dump(self)


def _kinds(kind: Kind) -> dict[str, frozenset[str]]:
    """``block`` -> the sub-keys :data:`KEY_TABLE` types as *kind*."""
    out: dict[str, frozenset[str]] = {}
    for block, subs in KEY_TABLE.blocks.items():
        keys = frozenset(sub for sub, spec in subs.items() if spec.kind is kind)
        if keys:
            out[block] = keys
    return out


#: Per-type views of :data:`KEY_TABLE`, kept under their historical names: the
#: generated ``config-keys-reference.md`` and its sync test read these.
BOOL_SUBKEYS = _kinds(Kind.BOOL)
STR_SUBKEYS = _kinds(Kind.STR)
LIST_SUBKEYS = _kinds(Kind.LIST)
INT_SUBKEYS = _kinds(Kind.INT)
DICT_STR_STR_SUBKEYS = _kinds(Kind.DICT_STR)
TOP_LEVEL_STR_KEYS: frozenset[str] = frozenset(
    key for key, spec in KEY_TABLE.top_scalars.items() if spec.kind is Kind.STR
)
TOP_LEVEL_INT_KEYS: frozenset[str] = frozenset(
    key for key, spec in KEY_TABLE.top_scalars.items() if spec.kind is Kind.INT
)

#: Public re-export of every recognized ``.abicheck.yml`` top-level key —
#: including the four ``project_targets.py``-owned ones. That sibling module
#: reuses this (rather than its own partial guess) to reject a misspelled
#: top-level key (e.g. ``tagrets:``) even though it only *parses* four of
#: these keys itself — a key outside this whole set is unknown to every
#: ``.abicheck.yml`` consumer, not just this one, so the strictness belongs
#: to one shared source of truth.
KNOWN_TOP_LEVEL_KEYS: frozenset[str] = BuildConfig._KNOWN_TOP_KEYS


def load_build_config(
    path: Path | None, *, config_overrides: Mapping[str, object] | None = None
) -> BuildConfig:
    """Load a ``.abicheck.yml`` build config; tolerant of a missing/empty file.

    *config_overrides* (ADR-091 D4, ``--set``) are laid over the file through
    :func:`abicheck.buildsource.build_config_overrides.apply_config_overrides`
    and validated by the same schema. With *path* ``None`` (no file) the
    overrides alone build the config.
    """
    raw: Any = None
    if path is not None and path.is_file():
        import yaml  # hard dep; imported out of the try so the except can name it

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            # yaml.YAMLError (e.g. ParserError) is not a ValueError; catch it so a
            # malformed .abicheck.yml surfaces as a wrapped error (→ ClickException in
            # embed_build_source) instead of a raw traceback (Codex review).
            raise ValueError(f"cannot read build config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raw = None
    if raw is None and not config_overrides:
        return BuildConfig()
    return BuildConfig.from_dict(
        apply_config_overrides(raw, config_overrides, KEY_TABLE)
    )


#: Re-exported for back-compat — the implementation now lives in
#: :mod:`abicheck.config_paths` (shared with `compare`'s own
#: ``discover_project_config()``), since both must agree on the same set of
#: recognized ``.abicheck.yml`` locations. See that module's docstring.
discover_build_config = _discover_build_config

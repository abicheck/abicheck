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

Split out of :mod:`abicheck.buildsource.inline` (G38 Phase 15 file-split
prerequisite — see ``docs/contribute/plans/
g38-bundle-facts-model-and-multibuild-comparability.md``'s Phase 13/15
tables): that module was within a couple of lines of the AI-readiness
2000-line hard cap, and the config *schema* (parsing/validating a raw
``.abicheck.yml`` mapping into a typed :class:`BuildConfig`) has no
dependency on, and shares no state with, ``inline.py``'s own inline
build/source *collection* pipeline (``collect_inline_pack`` and everything
it calls) — the two were bundled in one file purely by history, not by any
real coupling. Every private dict-parsing helper this module needs
(``_block``/``_str``/``_opt_str``/``_opt_bool``/``_strs``/``_lowered``/
``_one_of``/``_safe_compile_atom``) is used *only* by :class:`BuildConfig`
itself, confirmed by a full-file grep before the split — so this is a
mechanical extraction (unchanged function/method bodies), not a redesign,
mirroring how ``build_config_schema.py``/``build_config_io.py`` were already
split out of the same file for the identical reason.

:mod:`abicheck.buildsource.inline` re-exports :class:`BuildConfig`,
:func:`load_build_config`, :func:`discover_build_config`, and
:data:`KNOWN_TOP_LEVEL_KEYS` for back-compat (``BuildConfig as BuildConfig``
— the same explicit-re-export spelling ``checker_policy.py`` uses for
``ChangeKind``), so every existing ``from abicheck.buildsource.inline import
BuildConfig`` (or ``KNOWN_TOP_LEVEL_KEYS``) call site keeps working
unchanged; new code should import from here directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..config_paths import discover_build_config as _discover_build_config
from ..policy.support_promise import (
    SUPPORT_PROMISE_POLICIES as _SUPPORT_PROMISE_POLICIES,
)
from .build_config_schema import (
    BOOL_SUBKEYS as _BOOL_SUBKEYS,
    LIST_SUBKEYS as _LIST_SUBKEYS,
    STR_SUBKEYS as _STR_SUBKEYS,
    TOP_LEVEL_INT_KEYS as _TOP_LEVEL_INT_KEYS,
    TOP_LEVEL_STR_KEYS as _TOP_LEVEL_STR_KEYS,
    dict_str_str_subkey_findings as _dict_str_str_subkey_findings,
    int_subkey_findings as _int_subkey_findings,
    opt_int as _opt_int,
)
from .compile_options_safety import (  # PR #1146 finding #2 (sibling leaf module)
    reject_plugin_loading_options as _reject_plugin_loading_options,
)

#: Valid per-category severity levels (ADR-037 D4 ``severity:`` block).
_SEVERITY_LEVELS = ("error", "warning", "info")
#: Valid severity presets (mirror of ``severity.SEVERITY_PRESETS`` spelling).
_SEVERITY_PRESETS = ("default", "strict", "info-only")

# ── strict-schema knowledge (ADR-043 CLI reset: no separate `config validate`
# command — every real ingestion path enforces this) ─────────────────────────
#
# The subkey-type tables (_BOOL_SUBKEYS/_STR_SUBKEYS/_LIST_SUBKEYS/
# _TOP_LEVEL_STR_KEYS/_TOP_LEVEL_INT_KEYS) are imported at module top from
# `build_config_schema.py` (split out purely to stay under the AI-readiness
# 2000-line hard cap).


def _block(data: dict[str, object], key: str) -> dict[str, object]:
    """One top-level config block as a mapping (``{}`` when absent or wrong-typed).

    A wrong type here is never silently accepted -- :meth:`BuildConfig.
    _validate_structure` already rejected it -- so falling back to ``{}`` only
    covers a caller that bypassed validation with a non-dict payload.
    """
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _str(d: dict[str, object], key: str, default: str = "") -> str:
    v = d.get(key)
    return v if isinstance(v, str) else default


def _opt_str(d: dict[str, object], key: str) -> str | None:
    v = d.get(key)
    return v if isinstance(v, str) else None


def _opt_bool(d: dict[str, object], key: str) -> bool | None:
    v = d.get(key)
    return v if isinstance(v, bool) else None


def _strs(d: dict[str, object], key: str) -> list[str]:
    v = d.get(key)
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


def _lowered(value: str | None) -> str | None:
    """Case-normalize an optional enum-ish config scalar."""
    return value.lower() if value is not None else None


def _one_of(
    value: str | None, allowed: tuple[str, ...] | frozenset[str], where: str
) -> str | None:
    """*value* if it is ``None`` or a member of *allowed*, else ``ValueError``."""
    if value is not None and value not in allowed:
        raise ValueError(f"{where} must be one of {allowed}, got {value!r}")
    return value


def _safe_compile_atom(key: str, value: str) -> str:
    """One ``compile.*`` scalar, rejected unless it is a single argv atom.

    Values from auto-discovered source-tree configs are later embedded in
    individual compiler flags (``-std=<value>``/``-D<value>``) and flow through
    legacy shlex-split ``gcc_options`` plumbing. Reject whitespace so one
    config scalar cannot become multiple compiler arguments such as
    ``-Xclang -load ./evil.so``.
    """
    if not value or any(ch.isspace() for ch in value):
        raise ValueError(
            f"compile.{key} must be a single compiler option atom, got {value!r}"
        )
    return value


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
    :func:`abicheck.cli_helpers_compare.resolve_compare_config`'s own
    ``exit_code_scheme`` field docstring for the replacement, purely-derived
    computation. CLI flags override these; see :func:`abicheck.cli_helpers_compare.resolve_compare_config`
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
    #: ``scope.public_header_dirs`` (ADR-068 plan §5 P4): directory paths
    #: (relative to the project root, or absolute) whose headers are treated
    #: as this project's public/internal boundary for the four
    #: boundary-dependent cross-source checks migrated onto ``compare()``'s
    #: pipeline in ``workflows/cross_source_evolution.py``
    #: (``exported_not_public``/``public_not_exported``/
    #: ``rtti_for_internal_type``/``public_to_internal_dependency``). This is
    #: deliberately a *new* key, not a repurposing of the pre-existing
    #: ``scope.public`` boolean above (which toggles ``--scope-public-headers``
    #: FP-filtering behavior and has nothing to do with declaration
    #: provenance) -- naming collision aside, the two config values answer
    #: unrelated questions. Folded additively alongside any ``-H``/``--header``
    #: *directory* argument (never a file -- see ``workflows.scan_config.
    #: public_provenance_set``'s file-vs-directory asymmetry, preserved
    #: verbatim here since this field only ever contributes directories) via
    #: ``cli_compare_helpers.run_compare`` -> ``cli_resolve.
    #: _resolve_compare_snapshots``'s ``config_public_header_dirs`` parameter,
    #: which feeds the same ``InputSpec.public_header_dirs`` /
    #: ``provenance.apply_provenance`` machinery a ``-H`` directory already
    #: does -- one shared boundary primitive, two input sources. Empty by
    #: default: a project with no such config, and no ``-H`` directory
    #: either, tags every declaration ``ScopeOrigin.UNKNOWN`` exactly as
    #: before, which is what makes the four checks above evidence-gate to
    #: ``NOT_EVALUATED`` (never a fabricated finding) when neither source is
    #: present.
    public_header_dirs: list[str] = field(default_factory=list)
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
    #: (``cli_options.parse_abi3_floor``), so config and CLI cannot accept
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
    #: ``version:`` — config schema version (forward-compat; Phase 7 wires the
    #: unknown-key warning). ``0`` = unset.
    version: int = 0
    #: ``policy.overrides`` -- ADR-068 §3 #23's documented replacement route
    #: for the retired ``--crosscheck KEY=LEVEL`` flag: a ``ChangeKind`` slug
    #: -> severity string mapping, raw (unvalidated beyond str/str shape;
    #: `policy_file._parse_overrides` validates the real slugs/severities
    #: once this is folded into a `PolicyFile` for the run -- see
    #: `compatibility_evaluation_wiring.resolve_project_config_policy_
    #: overrides`). Empty dict = no project-level overrides stated.
    policy_overrides: dict[str, str] = field(default_factory=dict)

    #: ADR-037 §Backward-compat (G22 Phase 7): recognized ``.abicheck.yml`` keys.
    #: ``version:`` makes the config forward-compatible — an *unknown* key (a
    #: newer schema read by an older abicheck) **warns**, never errors, so a
    #: project can adopt a future key without breaking older installs. Keys parsed
    #: by sibling modules (``risk_rules`` → ``risk.py``, ``crosschecks`` →
    #: ``cross_source_checks.py``, ``targets``/``bundles``/``profiles``/``baseline``/
    #: ``aggregate`` → ``project_targets.py``, ADR-047 §3/G30 P1.5; CLI cleanup
    #: phase two, PR 2 follow-up for ``aggregate``) are listed so they don't
    #: trip the warning.
    _KNOWN_TOP_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "build",
            "sources",
            "severity",
            "scope",
            "suppression",
            "source",
            "compile",
            "debug",
            "bundle",
            "python",
            "gate",
            "release",
            "resource_limits",
            "version",
            "risk_rules",
            "crosschecks",
            "targets",
            "bundles",
            "profiles",
            "baseline",
            "aggregate",
            # ADR-068 §3 #23 (one-comparison-product.md): the documented
            # `.abicheck.yml` replacement route for the retired
            # `--crosscheck KEY=LEVEL` flag -- `policy.overrides` folds into
            # the same `PolicyFile.overrides` mechanism `--policy <file>`'s
            # own `overrides:` block already feeds.
            "policy",
        }
    )
    _KNOWN_BLOCK_KEYS: ClassVar[dict[str, frozenset[str]]] = {
        "build": frozenset(
            {"system", "query", "compile_db", "compile_db_filter", "targets"}
        ),
        "sources": frozenset({"public_headers", "exclude", "graph"}),
        "severity": frozenset(
            {
                "preset",
                "abi_breaking",
                "potential_breaking",
                "quality_issues",
                "addition",
            }
        ),
        "scope": frozenset(
            {
                "public",
                "collapse_versioned_symbols",
                "public_symbols",
                "show_redundant",
                "public_header_dirs",
                "on_incomplete",
            }
        ),
        "suppression": frozenset({"strict", "require_justification"}),
        "source": frozenset({"method"}),
        "compile": frozenset(
            {
                "frontend",
                "std",
                "include_dirs",
                "defines",
                "sysroot",
                "nostdinc",
                "compiler",
                "options",
                "ast_frontend_fallback",
                "allow_unsupported_castxml",
                "frontend_context",
                "lang",
            }
        ),
        "debug": frozenset(
            {"format", "dwarf_only", "debuginfod", "debuginfod_url", "pdb_path"}
        ),
        # Distinct from the plural `bundles:` block (`project_targets.py`,
        # ADR-047 §3 — named release groups of project *targets*, consumed
        # only by the `project` command family). This singular `bundle:`
        # block is release/scan-wide topology consumed directly by
        # `compare`'s directory/package fan-out and `scan --artifact-set`,
        # with no dependency on any `targets:`/`bundles:` declaration.
        "bundle": frozenset({"system_providers", "cohorts"}),
        # ADR-068 D5: the stable half of the `--abi3` audit (its floor).
        "python": frozenset({"abi3_floor"}),
        # Phase 7d (one-comparison-product.md §4.1): CI gate policy and
        # directory/package release topology demoted off the CLI.
        "gate": frozenset({"fail_on_removed_library"}),
        "release": frozenset({"dso_only", "include_private_dso", "support_promise"}),
        "resource_limits": frozenset({"max_bundle_facts_decode_nodes"}),  # Phase 7g
        # ADR-068 §3 #23: `policy.overrides` -- a `ChangeKind` slug -> severity
        # mapping, deep-validated (real slug, real severity) only once folded
        # into a `PolicyFile` (`policy_file._parse_overrides`); this table
        # only gates the block's own shape (a dict).
        "policy": frozenset({"overrides"}),
    }

    @classmethod
    def _scalar_findings(cls, key: str, value: object) -> list[str]:
        """Type findings for a recognized top-level *scalar* key.

        ``risk_rules``/``crosschecks`` are deliberately excluded:
        :meth:`from_dict` never parses them at all (they are consumed by
        ``risk.py``/``cross_source_checks.py`` instead), so there is no from_dict-level
        type contract to enforce for them here.
        """
        if value is None:
            return []
        if key in _TOP_LEVEL_STR_KEYS and not isinstance(value, str):
            return [f"{key} must be a string, got {type(value).__name__}: {value!r}"]
        if key in _TOP_LEVEL_INT_KEYS and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            return [f"{key} must be an integer, got {type(value).__name__}: {value!r}"]
        return []

    @classmethod
    def _subkey_findings(cls, key: str, sub: str, sub_value: object) -> list[str]:
        """Type findings for one ``<block>.<subkey>`` entry."""
        if sub in _BOOL_SUBKEYS.get(key, ()) and not isinstance(sub_value, bool):
            return [
                f"{key}.{sub} must be a boolean, got "
                f"{type(sub_value).__name__}: {sub_value!r}"
            ]
        if sub in _STR_SUBKEYS.get(key, ()) and not isinstance(sub_value, str):
            return [
                f"{key}.{sub} must be a string, got "
                f"{type(sub_value).__name__}: {sub_value!r}"
            ]
        if int_findings := _int_subkey_findings(key, sub, sub_value):
            return int_findings
        if dict_findings := _dict_str_str_subkey_findings(key, sub, sub_value):
            return dict_findings
        if sub not in _LIST_SUBKEYS.get(key, ()):
            return []
        if not isinstance(sub_value, (list, str)):
            return [
                f"{key}.{sub} must be a string or list of strings, "
                f"got {type(sub_value).__name__}: {sub_value!r}"
            ]
        # `_strs()` accepts a list container but a non-string element must be
        # rejected outright, not coerced via `str(x)`.
        bad = (
            [x for x in sub_value if not isinstance(x, str)]
            if isinstance(sub_value, list)
            else []
        )
        if bad:
            return [
                f"{key}.{sub} must be a list of strings, got "
                f"non-string element(s): {bad!r}"
            ]
        return []

    @classmethod
    def _block_findings(cls, key: str, value: object, known_block: object) -> list[str]:
        """Type findings for one recognized top-level *block* key."""
        if value is None:
            return []
        if not isinstance(value, dict):
            return [f"{key} must be a mapping, got {type(value).__name__}: {value!r}"]
        findings: list[str] = []
        for sub, sub_value in value.items():
            if sub not in known_block:  # type: ignore[operator]
                findings.append(f"unknown .abicheck.yml key {key}.{sub!r}")
                continue
            findings += cls._subkey_findings(key, sub, sub_value)
        return findings

    @classmethod
    def _validate_structure(cls, data: dict[str, object]) -> None:
        """Raise ``ValueError`` for every structural problem in a raw ``.abicheck.yml``.

        ADR-043 (pre-1.0 CLI reset): unknown keys and wrong-typed values used
        to only ``warnings.warn`` (forward-compat) or be silently
        coerced/dropped, which is what the now-removed ``abicheck config
        validate`` command existed to catch as a separate, easy-to-skip step.
        That strictness now lives here, so it fires on every real dump/
        compare/scan ingestion of a project config — no opt-in step needed.
        Collects every finding (not just the first) so a single bad file
        reports everything wrong with it at once.
        """
        findings: list[str] = []
        for key, value in data.items():
            if key not in cls._KNOWN_TOP_KEYS:
                findings.append(f"unknown .abicheck.yml key {key!r}")
                continue
            known_block = cls._KNOWN_BLOCK_KEYS.get(key)
            if known_block is None:
                findings += cls._scalar_findings(key, value)
            else:
                findings += cls._block_findings(key, value, known_block)
        if findings:
            raise ValueError("; ".join(findings))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BuildConfig:
        if isinstance(data, dict):
            cls._validate_structure(data)
        top = data if isinstance(data, dict) else {}
        build = _block(top, "build")
        sources = _block(top, "sources")
        severity = _block(top, "severity")
        scope = _block(top, "scope")
        suppression = _block(top, "suppression")
        source = _block(top, "source")
        compile_blk = _block(top, "compile")
        debug = _block(top, "debug")
        bundle = _block(top, "bundle")
        python_blk = _block(top, "python")
        gate = _block(top, "gate")
        release = _block(top, "release")
        resource_limits = _block(top, "resource_limits")
        policy = _block(top, "policy")

        def _safe_compile_atoms(key: str) -> list[str]:
            atoms = [_safe_compile_atom(key, item) for item in _strs(compile_blk, key)]
            if key == "options":
                _reject_plugin_loading_options(atoms)
            return atoms

        def _level(key: str) -> str | None:
            return _one_of(_opt_str(severity, key), _SEVERITY_LEVELS, f"severity.{key}")

        version_raw = top.get("version")
        return cls(
            system=_str(build, "system", "auto") or "auto",
            query=_str(build, "query"),
            compile_db=_str(build, "compile_db"),
            compile_db_filter=_str(build, "compile_db_filter"),
            targets=_strs(build, "targets"),
            public_headers=_strs(sources, "public_headers"),
            exclude=_strs(sources, "exclude"),
            graph_detail=_one_of(
                _str(sources, "graph", "summary") or "summary",
                ("summary", "full"),
                "sources.graph",
            )
            or "summary",
            severity_preset=_one_of(
                _opt_str(severity, "preset"), _SEVERITY_PRESETS, "severity.preset"
            ),
            severity_abi_breaking=_level("abi_breaking"),
            severity_potential_breaking=_level("potential_breaking"),
            severity_quality_issues=_level("quality_issues"),
            severity_addition=_level("addition"),
            scope_public=_opt_bool(scope, "public"),
            collapse_versioned_symbols=_opt_bool(scope, "collapse_versioned_symbols"),
            public_symbols=_strs(scope, "public_symbols"),
            scope_show_redundant=_opt_bool(scope, "show_redundant"),
            public_header_dirs=_strs(scope, "public_header_dirs"),
            scope_on_incomplete=_one_of(
                _opt_str(scope, "on_incomplete"),
                ("warn", "block"),
                "scope.on_incomplete",
            ),
            suppression_strict=_opt_bool(suppression, "strict"),
            suppression_require_justification=_opt_bool(
                suppression, "require_justification"
            ),
            source_method=_opt_str(source, "method"),
            # The CLI accepts the frontend case-insensitively (Click Choice
            # case_sensitive=False); normalize the config value to match.
            compile_frontend=_one_of(
                _lowered(_opt_str(compile_blk, "frontend")),
                ("auto", "castxml", "clang", "hybrid"),
                "compile.frontend",
            ),
            compile_std=(
                _safe_compile_atom("std", std)
                if (std := _opt_str(compile_blk, "std")) is not None
                else None
            ),
            compile_include_dirs=_strs(compile_blk, "include_dirs"),
            compile_defines=_safe_compile_atoms("defines"),
            compile_sysroot=_opt_str(compile_blk, "sysroot"),
            compile_nostdinc=_opt_bool(compile_blk, "nostdinc"),
            compile_compiler=_opt_str(compile_blk, "compiler"),
            compile_options=_safe_compile_atoms("options"),
            compile_ast_frontend_fallback=_opt_bool(
                compile_blk, "ast_frontend_fallback"
            ),
            compile_allow_unsupported_castxml=_opt_bool(
                compile_blk, "allow_unsupported_castxml"
            ),
            compile_frontend_context=_one_of(
                _lowered(_opt_str(compile_blk, "frontend_context")),
                ("host", "device"),
                "compile.frontend_context",
            ),
            compile_lang=_one_of(
                _lowered(_opt_str(compile_blk, "lang")),
                ("c++", "c"),
                "compile.lang",
            ),
            debug_format=_one_of(
                _lowered(_opt_str(debug, "format")),
                ("auto", "dwarf", "btf", "ctf"),
                "debug.format",
            ),
            debug_dwarf_only=_opt_bool(debug, "dwarf_only"),
            debug_debuginfod=_opt_bool(debug, "debuginfod"),
            debug_debuginfod_url=_opt_str(debug, "debuginfod_url"),
            debug_pdb_path=_opt_str(debug, "pdb_path"),
            # Stripped here, once, at the single choke point every consumer
            # (compare's fan-out, scan --artifact-set, stored-BundleFacts
            # compare) reads through -- compare's own fan-out incidentally
            # stripped via a comma-join/split round trip through a legacy
            # string parameter, but the other two forwarded the raw tuple
            # unchanged, so a quoted entry with stray whitespace matched one
            # consumer's SONAME comparison and silently missed another's
            # (Codex review, fresh evidence).
            bundle_system_providers=[
                s.strip() for s in _strs(bundle, "system_providers") if s.strip()
            ],
            bundle_cohorts=[s.strip() for s in _strs(bundle, "cohorts") if s.strip()],
            python_abi3_floor=_opt_str(python_blk, "abi3_floor"),
            gate_fail_on_removed_library=_opt_bool(gate, "fail_on_removed_library"),
            release_dso_only=_opt_bool(release, "dso_only"),
            release_include_private_dso=_opt_bool(release, "include_private_dso"),
            release_support_promise=_one_of(
                _opt_str(release, "support_promise"),
                _SUPPORT_PROMISE_POLICIES,
                "release.support_promise",
            ),
            resource_limits_max_bundle_facts_decode_nodes=_opt_int(
                resource_limits, "max_bundle_facts_decode_nodes"
            ),
            version=(
                version_raw
                if isinstance(version_raw, int) and not isinstance(version_raw, bool)
                else 0
            ),
            policy_overrides=(
                dict(policy_overrides_raw)
                if isinstance(policy_overrides_raw := policy.get("overrides"), dict)
                else {}
            ),
        )

    def _build_block(self) -> dict[str, Any]:
        """Non-default ``build:`` keys (empty when the block is all-defaults)."""
        build: dict[str, Any] = {}
        if self.system and self.system != "auto":
            build["system"] = self.system
        if self.query:
            build["query"] = self.query
        if self.compile_db:
            build["compile_db"] = self.compile_db
        if self.compile_db_filter:
            build["compile_db_filter"] = self.compile_db_filter
        if self.targets:
            build["targets"] = list(self.targets)
        return build

    def _sources_block(self) -> dict[str, Any]:
        """Non-default ``sources:`` keys (headers/excludes/graph detail)."""
        sources: dict[str, Any] = {}
        if self.public_headers:
            sources["public_headers"] = list(self.public_headers)
        if self.exclude:
            sources["exclude"] = list(self.exclude)
        if self.graph_detail and self.graph_detail != "summary":
            sources["graph"] = self.graph_detail
        return sources

    def _severity_block(self) -> dict[str, Any]:
        """Non-default ``severity:`` keys (preset + per-category levels)."""
        severity: dict[str, Any] = {}
        if self.severity_preset is not None:
            severity["preset"] = self.severity_preset
        for key in ("abi_breaking", "potential_breaking", "quality_issues", "addition"):
            val = getattr(self, f"severity_{key}")
            if val is not None:
                severity[key] = val
        return severity

    def _scope_block(self) -> dict[str, Any]:
        """Non-default ``scope:`` keys (public-surface FP tuning)."""
        scope: dict[str, Any] = {}
        if self.scope_public is not None:
            scope["public"] = self.scope_public
        if self.collapse_versioned_symbols is not None:
            scope["collapse_versioned_symbols"] = self.collapse_versioned_symbols
        if self.public_symbols:
            scope["public_symbols"] = list(self.public_symbols)
        if self.scope_show_redundant is not None:
            scope["show_redundant"] = self.scope_show_redundant
        if self.public_header_dirs:
            scope["public_header_dirs"] = list(self.public_header_dirs)
        if self.scope_on_incomplete is not None:
            scope["on_incomplete"] = self.scope_on_incomplete
        return scope

    def _suppression_block(self) -> dict[str, Any]:
        """Non-default ``suppression:`` keys (hygiene policy)."""
        suppression: dict[str, Any] = {}
        if self.suppression_strict is not None:
            suppression["strict"] = self.suppression_strict
        if self.suppression_require_justification is not None:
            suppression["require_justification"] = (
                self.suppression_require_justification
            )
        return suppression

    def _source_block(self) -> dict[str, Any]:
        """``source:`` block (``method`` only; empty when unset)."""
        if self.source_method is not None:
            return {"method": self.source_method}
        return {}

    def _compile_block(self) -> dict[str, Any]:
        """Non-default ``compile:`` keys (stable L2 header compile context)."""
        compile_blk: dict[str, Any] = {}
        if self.compile_frontend is not None:
            compile_blk["frontend"] = self.compile_frontend
        if self.compile_std is not None:
            compile_blk["std"] = self.compile_std
        if self.compile_include_dirs:
            compile_blk["include_dirs"] = list(self.compile_include_dirs)
        if self.compile_defines:
            compile_blk["defines"] = list(self.compile_defines)
        if self.compile_sysroot is not None:
            compile_blk["sysroot"] = self.compile_sysroot
        if self.compile_nostdinc is not None:
            compile_blk["nostdinc"] = self.compile_nostdinc
        if self.compile_compiler is not None:
            compile_blk["compiler"] = self.compile_compiler
        if self.compile_options:
            compile_blk["options"] = list(self.compile_options)
        if self.compile_ast_frontend_fallback is not None:
            compile_blk["ast_frontend_fallback"] = self.compile_ast_frontend_fallback
        if self.compile_allow_unsupported_castxml is not None:
            compile_blk["allow_unsupported_castxml"] = (
                self.compile_allow_unsupported_castxml
            )
        if self.compile_frontend_context is not None:
            compile_blk["frontend_context"] = self.compile_frontend_context
        if self.compile_lang is not None:
            compile_blk["lang"] = self.compile_lang
        return compile_blk

    def _debug_block(self) -> dict[str, Any]:
        """Non-default ``debug:`` keys (separate-debug-file resolution; ADR-040 L2)."""
        debug: dict[str, Any] = {}
        if self.debug_format is not None:
            debug["format"] = self.debug_format
        if self.debug_dwarf_only is not None:
            debug["dwarf_only"] = self.debug_dwarf_only
        if self.debug_debuginfod is not None:
            debug["debuginfod"] = self.debug_debuginfod
        if self.debug_debuginfod_url is not None:
            debug["debuginfod_url"] = self.debug_debuginfod_url
        if self.debug_pdb_path is not None:
            debug["pdb_path"] = self.debug_pdb_path
        return debug

    def _bundle_block(self) -> dict[str, Any]:
        """Non-default ``bundle:`` keys (release/scan topology; CLI cleanup
        phase two, PR J)."""
        bundle: dict[str, Any] = {}
        if self.bundle_system_providers:
            bundle["system_providers"] = list(self.bundle_system_providers)
        if self.bundle_cohorts:
            bundle["cohorts"] = list(self.bundle_cohorts)
        return bundle

    def _python_block(self) -> dict[str, Any]:
        """Non-default ``python:`` keys (the project's ``abi3`` floor)."""
        if self.python_abi3_floor is not None:
            return {"abi3_floor": self.python_abi3_floor}
        return {}

    def _gate_block(self) -> dict[str, Any]:
        """Non-default ``gate:`` keys (Phase 7d: CI gate policy)."""
        if self.gate_fail_on_removed_library is not None:
            return {"fail_on_removed_library": self.gate_fail_on_removed_library}
        return {}

    def _release_block(self) -> dict[str, Any]:
        """Non-default ``release:`` keys (Phase 7d: directory/package
        release topology)."""
        release: dict[str, Any] = {}
        if self.release_dso_only is not None:
            release["dso_only"] = self.release_dso_only
        if self.release_include_private_dso is not None:
            release["include_private_dso"] = self.release_include_private_dso
        if self.release_support_promise is not None:
            release["support_promise"] = self.release_support_promise
        return release

    def _policy_block(self) -> dict[str, Any]:
        """Non-default ``policy:`` keys (ADR-068 §3 #23's ``policy.overrides``,
        the documented ``.abicheck.yml`` replacement route for the retired
        ``--crosscheck KEY=LEVEL`` flag)."""
        if self.policy_overrides:
            return {"overrides": dict(self.policy_overrides)}
        return {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to a ``.abicheck.yml`` mapping (round-trips via from_dict).

        Only non-default blocks/keys are emitted so a dumped config stays minimal
        and a reload reproduces the same :class:`BuildConfig` (ADR-037 D4
        round-trip contract, ``test_config_roundtrip``).
        """
        out: dict[str, Any] = {}
        # Insertion order is the stable dump order: block by block, then the
        # top-level scalars — keep it in sync with the dataclass field order.
        for key, block in (
            ("build", self._build_block()),
            ("sources", self._sources_block()),
            ("severity", self._severity_block()),
            ("scope", self._scope_block()),
            ("suppression", self._suppression_block()),
            ("source", self._source_block()),
            ("compile", self._compile_block()),
            ("debug", self._debug_block()),
            ("bundle", self._bundle_block()),
            ("python", self._python_block()),
            ("gate", self._gate_block()),
            ("release", self._release_block()),
            (
                "resource_limits",
                {}
                if (n := self.resource_limits_max_bundle_facts_decode_nodes) is None
                else {"max_bundle_facts_decode_nodes": n},
            ),
            ("policy", self._policy_block()),
        ):
            if block:
                out[key] = block

        if self.version:
            out["version"] = self.version
        return out


#: Public re-export of every recognized ``.abicheck.yml`` top-level key —
#: including the four ``project_targets.py``-owned ones. That sibling module
#: reuses this (rather than its own partial guess) to reject a misspelled
#: top-level key (e.g. ``tagrets:``) even though it only *parses* four of
#: these keys itself — a key outside this whole set is unknown to every
#: ``.abicheck.yml`` consumer, not just this one, so the strictness belongs
#: to one shared source of truth.
KNOWN_TOP_LEVEL_KEYS: frozenset[str] = BuildConfig._KNOWN_TOP_KEYS


def load_build_config(path: Path) -> BuildConfig:
    """Load a ``.abicheck.yml`` build config; tolerant of a missing/empty file."""
    if not path.is_file():
        return BuildConfig()
    import yaml  # hard dep; imported out of the try so the except can name it

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # yaml.YAMLError (e.g. ParserError) is not a ValueError; catch it so a
        # malformed .abicheck.yml surfaces as a wrapped error (→ ClickException in
        # embed_build_source) instead of a raw traceback (Codex review).
        raise ValueError(f"cannot read build config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return BuildConfig()
    return BuildConfig.from_dict(raw)


#: Re-exported for back-compat — the implementation now lives in
#: :mod:`abicheck.config_paths` (shared with `compare`'s own
#: ``discover_project_config()``), since both must agree on the same set of
#: recognized ``.abicheck.yml`` locations. See that module's docstring.
discover_build_config = _discover_build_config

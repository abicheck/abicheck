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

"""Inline build/source collection for ``dump --build-info``/``--sources``.

The source-tree-centric model (ADR-028..033 amendment, 2026-06-12): instead of
attaching a prebuilt pack directory, ``dump`` collects
the normalized L3/L4/L5 facts *inline* from raw inputs and embeds them in the
``.abi.json``:

- ``--sources <tree>`` — a source checkout (e.g. at the build tag). Runs L4
  source ABI replay and the L5 source graph summary internally.
- ``--build-info <path>`` — an optional build dir / ``compile_commands.json`` /
  pre-captured build-evidence pack supplying L3 build context. When omitted, a
  ``compile_commands.json`` inside the source tree is auto-discovered.

A per-project ``.abicheck.yml`` ``build:`` block can name the build system and a
*query* command that emits a compile DB without performing a full build; running
that query is gated by an explicit, operator-supplied ``--config`` alone
(ADR-032 D5 ``query_build_system`` action ceiling — read by default, trusted
query opt-in, full build never). ``--allow-build-query`` is a deprecated
no-op kept only for backward compatibility — it neither grants nor restricts
this permission (see :func:`collect_inline_pack`'s ``allow_build_query``
docstring). The separate abicheck-authored *inferred* cmake/bazel/make query
(:func:`_resolve_compile_db`) runs whenever ``--sources`` needs L3 regardless
of any flag — pointing abicheck at a source tree is itself the request to
analyse it.

Everything here is best-effort (ADR-028 D3): a missing tool or unreadable input
degrades L3/L4/L5 to partial/not-collected coverage and never aborts the dump —
the artifact tiers (L0/L1/L2) stay authoritative.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from .. import deadline
from .build_evidence import BuildEvidence
from .model import (
    CoverageStatus,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .pack import BuildSourcePack
from .redaction import DEFAULT_REDACTION

if TYPE_CHECKING:
    from .source_abi import SourceAbiSurface
    from .source_extractors import SourceAbiExtractor
    from .source_graph import SourceGraphSummary

#: Default places to look for a compile DB inside a source checkout, in order.
logger = logging.getLogger(__name__)

_COMPILE_DB_NAME = "compile_commands.json"
#: ``builddir`` is the name the Meson docs/tutorials use for `meson setup builddir`
#: (P12); ``build``/``_build``/``out`` cover CMake/Ninja conventions.
_COMPILE_DB_HINTS = ("", "build", "builddir", "out", "_build", "cmake-build-debug")

#: Build-query subprocess wall-clock ceiling. A query/extraction command
#: (cquery/aquery/ninja -t/make -n) should be fast; a runaway one is treated as
#: a failed extractor rather than hanging the dump.
_QUERY_TIMEOUT_S = 300
# build_query extractor statuses worth surfacing as an A3 diagnostic (no facts):
# skipped (not allowed), failed (errored/unparseable), partial (ran, no compile
# DB produced). "ok" means a DB was produced, so it needs no special handling.
_BUILD_QUERY_DIAG_STATUSES = ("failed", "skipped", "partial")

# Extractor names that carry a build-query no-facts diagnostic: the explicit
# trusted `build.query` ("build_query") and the zero-config inferred query
# ("build_query_auto"). Both must be treated alike in the pack-survival gate and
# the L3 coverage row so an inferred-query-only run keeps its explanation.
_BUILD_QUERY_DIAG_NAMES = ("build_query", "build_query_auto")


#: Valid per-category severity levels (ADR-037 D4 ``severity:`` block).
_SEVERITY_LEVELS = ("error", "warning", "info")
#: Valid severity presets (mirror of ``severity.SEVERITY_PRESETS`` spelling).
_SEVERITY_PRESETS = ("default", "strict", "info-only")
#: Valid exit-code schemes (ADR-037 D12 ``exit_code_scheme:``).
_EXIT_CODE_SCHEMES = ("auto", "legacy", "severity")

# ── strict-schema knowledge (ADR-043 CLI reset: no separate `config validate`
# command — every real ingestion path enforces this) ─────────────────────────
#
# Block subkeys BuildConfig.from_dict() parses with `_opt_bool`/`_opt_str`/
# `_str`/`_strs` — a value of the wrong type there (e.g. the YAML string
# "false" for a boolean, or a bare number for a string/list field) must be
# rejected outright rather than silently dropped/coerced. Keep these three
# maps in sync with `BuildConfig.from_dict`'s helper calls when a new subkey
# is added — nothing enforces that automatically.
_BOOL_SUBKEYS: dict[str, frozenset[str]] = {
    "scope": frozenset({"public", "collapse_versioned_symbols", "show_redundant"}),
    "suppression": frozenset({"strict", "require_justification"}),
    "compile": frozenset({"nostdinc"}),
    "debug": frozenset({"dwarf_only", "debuginfod"}),
}
_STR_SUBKEYS: dict[str, frozenset[str]] = {
    "build": frozenset({"system", "query", "compile_db"}),
    "sources": frozenset({"graph"}),
    "severity": frozenset(
        {"preset", "abi_breaking", "potential_breaking", "quality_issues", "addition"}
    ),
    "source": frozenset({"method"}),
    "compile": frozenset({"frontend", "std", "sysroot"}),
    "debug": frozenset({"format", "debuginfod_url"}),
}
# `_strs()` accepts either a list of strings or a single bare string (folded
# to a 1-element list), so both shapes are valid here — anything else isn't.
_LIST_SUBKEYS: dict[str, frozenset[str]] = {
    "sources": frozenset({"public_headers", "exclude"}),
    "scope": frozenset({"public_symbols"}),
    "compile": frozenset({"include_dirs", "defines"}),
}
# Recognized top-level keys that are scalars, not blocks (i.e. absent from
# _KNOWN_BLOCK_KEYS) — the same wrong-type gap as the block subkeys above, one
# level up.
_TOP_LEVEL_STR_KEYS: frozenset[str] = frozenset({"exit_code_scheme"})
_TOP_LEVEL_INT_KEYS: frozenset[str] = frozenset({"version"})


@dataclass
class BuildConfig:
    """Parsed ``.abicheck.yml`` project config (ADR-028 amendment D4 + ADR-037 D4).

    All fields are optional; an absent file yields the all-defaults config. The
    ``build:`` / ``sources:`` blocks drive inline build/source collection
    (``system`` is advisory; ``query`` runs only with an explicit config +
    ``--allow-build-query``; ``compile_db`` is where it lands).

    ADR-037 D4 adds the project-contract blocks consumed by ``compare`` — the
    settings that are stable, reviewed-in-a-PR properties rather than per-run
    invocation flags: ``severity:`` (per-category levels + preset), ``scope:``
    (public-surface FP tuning), ``suppression:`` (hygiene policy), ``source:``
    (precise S-axis), plus the top-level ``exit_code_scheme:`` and ``version:``.
    CLI flags override these; see :func:`abicheck.cli_helpers_compare.resolve_compare_config`
    for the precedence resolver (CLI > config > built-in default).

    A field left at its ``None`` / ``""`` / empty default means "unset — inherit
    the next level down", which is what makes the precedence merge unambiguous.
    """

    system: str = "auto"
    query: str = ""
    compile_db: str = ""
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
    #: ``scope.show_redundant`` — a reporting/FP-tuning toggle demoted off the CLI
    #: (ADR-040 Lever 2). ``None`` = unset. The ``--show-filtered`` debugging view
    #: stays a visible CLI flag.
    scope_show_redundant: bool | None = None
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
    #: ``debug:`` — separate-debug-file resolution (ADR-021a) demoted off the CLI
    #: (ADR-040 Lever 2). These are stable per-project debug-artifact knobs; the
    #: coarse per-run ``--debug-root`` stays a visible CLI override, while the
    #: format/debuginfod/dwarf-only knobs move here. ``None`` = unset.
    debug_format: str | None = None
    debug_dwarf_only: bool | None = None
    debug_debuginfod: bool | None = None
    debug_debuginfod_url: str | None = None
    #: ``exit_code_scheme:`` — ADR-037 D12; CI keys on it, so it lives in config.
    exit_code_scheme: str = "auto"
    #: ``version:`` — config schema version (forward-compat; Phase 7 wires the
    #: unknown-key warning). ``0`` = unset.
    version: int = 0

    #: ADR-037 §Backward-compat (G22 Phase 7): recognized ``.abicheck.yml`` keys.
    #: ``version:`` makes the config forward-compatible — an *unknown* key (a
    #: newer schema read by an older abicheck) **warns**, never errors, so a
    #: project can adopt a future key without breaking older installs. Keys parsed
    #: by sibling modules (``risk_rules`` → ``risk.py``, ``crosschecks`` →
    #: ``crosscheck.py``) are listed so they don't trip the warning.
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
            "exit_code_scheme",
            "version",
            "risk_rules",
            "crosschecks",
        }
    )
    _KNOWN_BLOCK_KEYS: ClassVar[dict[str, frozenset[str]]] = {
        "build": frozenset({"system", "query", "compile_db"}),
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
            {"public", "collapse_versioned_symbols", "public_symbols", "show_redundant"}
        ),
        "suppression": frozenset({"strict", "require_justification"}),
        "source": frozenset({"method", "graph"}),
        "compile": frozenset(
            {
                "frontend",
                "std",
                "include_dirs",
                "defines",
                "sysroot",
                "nostdinc",
            }
        ),
        "debug": frozenset({"format", "dwarf_only", "debuginfod", "debuginfod_url"}),
    }

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
                # A recognized top-level *scalar* (not a block key) — e.g.
                # exit_code_scheme/version. risk_rules/crosschecks are
                # deliberately excluded: from_dict never parses them at all
                # (consumed by risk.py/crosscheck.py instead), so there is no
                # from_dict-level type contract to enforce here.
                if value is not None:
                    if key in _TOP_LEVEL_STR_KEYS and not isinstance(value, str):
                        findings.append(
                            f"{key} must be a string, got "
                            f"{type(value).__name__}: {value!r}"
                        )
                    elif key in _TOP_LEVEL_INT_KEYS and (
                        not isinstance(value, int) or isinstance(value, bool)
                    ):
                        findings.append(
                            f"{key} must be an integer, got "
                            f"{type(value).__name__}: {value!r}"
                        )
                continue
            if value is None:
                continue
            if not isinstance(value, dict):
                findings.append(
                    f"{key} must be a mapping, got {type(value).__name__}: {value!r}"
                )
                continue
            for sub, sub_value in value.items():
                if sub not in known_block:
                    findings.append(f"unknown .abicheck.yml key {key}.{sub!r}")
                    continue
                if sub in _BOOL_SUBKEYS.get(key, ()) and not isinstance(
                    sub_value, bool
                ):
                    findings.append(
                        f"{key}.{sub} must be a boolean, got "
                        f"{type(sub_value).__name__}: {sub_value!r}"
                    )
                elif sub in _STR_SUBKEYS.get(key, ()) and not isinstance(
                    sub_value, str
                ):
                    findings.append(
                        f"{key}.{sub} must be a string, got "
                        f"{type(sub_value).__name__}: {sub_value!r}"
                    )
                elif sub in _LIST_SUBKEYS.get(key, ()):
                    if not isinstance(sub_value, (list, str)):
                        findings.append(
                            f"{key}.{sub} must be a string or list of strings, "
                            f"got {type(sub_value).__name__}: {sub_value!r}"
                        )
                    elif isinstance(sub_value, list):
                        # `_strs()` accepts a list container but a non-string
                        # element must be rejected outright, not coerced via
                        # `str(x)`.
                        bad = [x for x in sub_value if not isinstance(x, str)]
                        if bad:
                            findings.append(
                                f"{key}.{sub} must be a list of strings, got "
                                f"non-string element(s): {bad!r}"
                            )
        if findings:
            raise ValueError("; ".join(findings))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BuildConfig:
        if isinstance(data, dict):
            cls._validate_structure(data)
        build = data.get("build") if isinstance(data, dict) else None
        build = build if isinstance(build, dict) else {}
        sources = data.get("sources") if isinstance(data, dict) else None
        sources = sources if isinstance(sources, dict) else {}
        severity = data.get("severity") if isinstance(data, dict) else None
        severity = severity if isinstance(severity, dict) else {}
        scope = data.get("scope") if isinstance(data, dict) else None
        scope = scope if isinstance(scope, dict) else {}
        suppression = data.get("suppression") if isinstance(data, dict) else None
        suppression = suppression if isinstance(suppression, dict) else {}
        source = data.get("source") if isinstance(data, dict) else None
        source = source if isinstance(source, dict) else {}
        compile_blk = data.get("compile") if isinstance(data, dict) else None
        compile_blk = compile_blk if isinstance(compile_blk, dict) else {}
        debug = data.get("debug") if isinstance(data, dict) else None
        debug = debug if isinstance(debug, dict) else {}

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

        def _safe_compile_atom(key: str, value: str) -> str:
            # Values from auto-discovered source-tree configs are later embedded
            # in individual compiler flags (``-std=<value>``/``-D<value>``) and
            # flow through legacy shlex-split ``gcc_options`` plumbing.  Reject
            # whitespace so one config scalar cannot become multiple compiler
            # arguments such as ``-Xclang -load ./evil.so``.
            if not value or any(ch.isspace() for ch in value):
                raise ValueError(
                    f"compile.{key} must be a single compiler option atom, got {value!r}"
                )
            return value

        def _safe_compile_atoms(key: str) -> list[str]:
            return [_safe_compile_atom(key, item) for item in _strs(compile_blk, key)]

        def _level(key: str) -> str | None:
            raw = _opt_str(severity, key)
            if raw is not None and raw not in _SEVERITY_LEVELS:
                raise ValueError(
                    f"severity.{key} must be one of {_SEVERITY_LEVELS}, got {raw!r}"
                )
            return raw

        graph_detail = _str(sources, "graph", "summary") or "summary"
        if graph_detail not in ("summary", "full"):
            raise ValueError(
                f"sources.graph must be 'summary' or 'full', got {graph_detail!r}"
            )

        preset = _opt_str(severity, "preset")
        if preset is not None and preset not in _SEVERITY_PRESETS:
            raise ValueError(
                f"severity.preset must be one of {_SEVERITY_PRESETS}, got {preset!r}"
            )

        scheme = (
            _str(data if isinstance(data, dict) else {}, "exit_code_scheme", "auto")
            or "auto"
        )
        if scheme not in _EXIT_CODE_SCHEMES:
            raise ValueError(
                f"exit_code_scheme must be one of {_EXIT_CODE_SCHEMES}, got {scheme!r}"
            )

        version_raw = data.get("version") if isinstance(data, dict) else None
        version = (
            version_raw
            if isinstance(version_raw, int) and not isinstance(version_raw, bool)
            else 0
        )

        debug_format = _opt_str(debug, "format")
        if debug_format is not None:
            debug_format = debug_format.lower()
            if debug_format not in ("auto", "dwarf", "btf", "ctf"):
                raise ValueError(
                    "debug.format must be one of ('auto', 'dwarf', 'btf', 'ctf'), "
                    f"got {debug_format!r}"
                )

        compile_frontend = _opt_str(compile_blk, "frontend")
        if compile_frontend is not None:
            # The CLI accepts the frontend case-insensitively (Click Choice
            # case_sensitive=False); normalize the config value to match.
            compile_frontend = compile_frontend.lower()
        if compile_frontend is not None and compile_frontend not in (
            "auto",
            "castxml",
            "clang",
            "hybrid",
        ):
            raise ValueError(
                "compile.frontend must be one of ('auto', 'castxml', 'clang', "
                f"'hybrid'), got {compile_frontend!r}"
            )

        return cls(
            system=_str(build, "system", "auto") or "auto",
            query=_str(build, "query"),
            compile_db=_str(build, "compile_db"),
            public_headers=_strs(sources, "public_headers"),
            exclude=_strs(sources, "exclude"),
            graph_detail=graph_detail,
            severity_preset=preset,
            severity_abi_breaking=_level("abi_breaking"),
            severity_potential_breaking=_level("potential_breaking"),
            severity_quality_issues=_level("quality_issues"),
            severity_addition=_level("addition"),
            scope_public=_opt_bool(scope, "public"),
            collapse_versioned_symbols=_opt_bool(scope, "collapse_versioned_symbols"),
            public_symbols=_strs(scope, "public_symbols"),
            scope_show_redundant=_opt_bool(scope, "show_redundant"),
            suppression_strict=_opt_bool(suppression, "strict"),
            suppression_require_justification=_opt_bool(
                suppression, "require_justification"
            ),
            source_method=_opt_str(source, "method"),
            compile_frontend=compile_frontend,
            compile_std=(
                _safe_compile_atom("std", std)
                if (std := _opt_str(compile_blk, "std")) is not None
                else None
            ),
            compile_include_dirs=_strs(compile_blk, "include_dirs"),
            compile_defines=_safe_compile_atoms("defines"),
            compile_sysroot=_opt_str(compile_blk, "sysroot"),
            compile_nostdinc=_opt_bool(compile_blk, "nostdinc"),
            debug_format=debug_format,
            debug_dwarf_only=_opt_bool(debug, "dwarf_only"),
            debug_debuginfod=_opt_bool(debug, "debuginfod"),
            debug_debuginfod_url=_opt_str(debug, "debuginfod_url"),
            exit_code_scheme=scheme,
            version=version,
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
        return debug

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
        ):
            if block:
                out[key] = block

        if self.exit_code_scheme and self.exit_code_scheme != "auto":
            out["exit_code_scheme"] = self.exit_code_scheme
        if self.version:
            out["version"] = self.version
        return out


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


def discover_build_config(source_tree: Path | None) -> Path | None:
    """Return the ``.abicheck.yml`` at the source-tree root, if present."""
    if source_tree is None or not source_tree.is_dir():
        return None
    candidate = source_tree / ".abicheck.yml"
    return candidate if candidate.is_file() else None


def is_pack_dir(path: Path | None) -> bool:
    """True when *path* is a real ``BuildSourcePack`` directory.

    Validates the manifest *content*, not just its presence: a raw source checkout
    or build dir that merely contains a top-level ``manifest.json`` must not be
    mistaken for a pack — ``BuildSourcePack.load`` would otherwise accept it with
    sparse defaults and silently drop the real L3-L5 evidence the caller meant to
    collect. Requires the BuildSourcePack version marker
    (``build_source_pack_version`` / legacy ``evidence_pack_version``).
    """
    if path is None or not path.is_dir():
        return False
    manifest = path / "manifest.json"
    if not manifest.is_file():
        return False
    import json

    try:
        with manifest.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        return False
    except ValueError:
        # Present but unparseable: keep treating it as a (corrupt) pack so the
        # downstream load raises a loud error rather than silently collecting —
        # a corrupt `collect` output must never be ignored.
        return True
    # Valid JSON *without* the BuildSourcePack marker is a non-pack file (e.g. a
    # stray project manifest.json in a raw checkout) — collect from the tree, do
    # not mis-load it as an empty pack.
    return isinstance(data, dict) and (
        "build_source_pack_version" in data or "evidence_pack_version" in data
    )


def effective_graph_scope(graph_detail: str, scope: str) -> str:
    """Apply the ADR-037 D6 ``sources.graph`` detail cap to a replay scope.

    ``full`` deepens a ``changed`` scope to ``target`` (full replay); ``summary``
    (the default) leaves the requested scope untouched. The override only ever
    *widens* — it never silently drops evidence.
    """
    if graph_detail == "full" and scope == "changed":
        return "target"
    return scope


def _run_cleanups(cleanups: list[Callable[[], None]]) -> None:
    for fn in cleanups:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


def collect_inline_pack(
    *,
    sources: Path | None,
    build_info: Path | None,
    build_config: BuildConfig | None = None,
    allow_build_query: bool = False,
    build_config_trusted_for_query: bool = True,
    compile_db_explicit: bool = False,
    allow_inferred_build_query: bool = True,
    base_build: BuildEvidence | None = None,
    clang_bin: str = "clang",
    extractor: str = "clang",
    scope: str = "target",
    layers: tuple[str, ...] = ("L3", "L4", "L5"),
    build_cache_dir: Path | None = None,
    source_abi_cache_dir: Path | None = None,
    exported_symbols: tuple[str, ...] = (),
    changed_paths: tuple[str, ...] = (),
    public_header_roots: tuple[str, ...] = (),
    defer_cleanup: list[Callable[[], None]] | None = None,
) -> BuildSourcePack | None:
    """Collect an in-memory pack from raw source-tree / build-info inputs.

    Resolves L3 build evidence (from ``build_info`` or an auto-discovered /
    queried compile DB), runs L4 source ABI replay over a source tree, folds both
    into an L5 graph summary, and returns an embeddable :class:`BuildSourcePack`
    (``root=""``). Returns ``None`` when no input produced any facts.

    ``base_build`` seeds the L3 evidence from an already-loaded pack (e.g. an
    explicit ``--build-info`` pack directory) so a raw ``--sources`` tree can
    replay L4 against it without re-resolving a compile DB.

    ``build_config_trusted_for_query`` must be true before a tree-local
    ``build.query`` command can run. CLI auto-discovered ``.abicheck.yml`` files
    live inside the supplied source tree and may be attacker-controlled, so they
    are not trusted for subprocess execution. (The abicheck-authored *inferred*
    cmake/bazel query is separate — it runs whenever ``--sources`` needs L3, since
    pointing abicheck at a source tree is itself the request to analyse it; see
    :func:`_resolve_compile_db`.) ``allow_build_query`` is accepted only for
    backward compatibility and is ignored — ``--allow-build-query`` is a
    deprecated no-op.

    ``layers`` selects which layers to collect (ADR-033 D2 CI modes): the
    ``build`` mode passes ``("L3",)`` to capture build context only, skipping the
    L4 source replay and L5 graph entirely. ``L5`` requires ``L4``.
    """
    cfg = build_config or BuildConfig()
    scope = effective_graph_scope(cfg.graph_detail, scope)
    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    # Cleanup thunks for temp build dirs (out-of-tree inferred cmake) that must
    # outlive L4 replay — clang runs with each compile unit's `directory` (the cmake
    # build dir) as cwd, so the dir can't be removed (nor its lock released) until
    # after replay. Invoked below once L3/L4/L5 are collected into in-memory
    # evidence. Each thunk removes its dir and releases the dir's exclusive lock.
    query_build_cleanups: list[Callable[[], None]] = []

    try:
        if base_build is not None:
            merged.merge(base_build)

        if merged.compile_units:
            compile_db = None  # already seeded from a build-info pack
        elif _maybe_collect_bazel_build_info(build_info, merged, extractors):
            # A pre-captured Bazel aquery/cquery jsonproto produces BuildEvidence
            # directly (no compile_commands.json to load) — ADR-037 D5 #5 sniffing.
            compile_db = None
        else:
            compile_db = _resolve_compile_db(
                build_info,
                sources,
                cfg,
                build_config_trusted_for_query,
                merged,
                extractors,
                cleanup=query_build_cleanups,
                compile_db_explicit=compile_db_explicit,
                allow_inferred_build_query=allow_inferred_build_query,
            )
        if compile_db is not None:
            _run_compile_db(compile_db, cfg.system, merged, extractors, build_cache_dir)

        # A4: with both a --sources tree and L3 compile units, flag when the build
        # metadata describes a different checkout than the source tree (decoupled
        # inputs assembled from different trees). Collection-time diagnostic, not a
        # ChangeKind — collection has no findings list (cf. A2).
        _check_build_info_source_mismatch(merged, sources, extractors)

        surface = None
        call_graph_units: list[Any] | None = None
        if "L4" in layers:
            # A 'changed' scope with no PR diff would select zero TUs and embed an
            # empty L4 surface (Codex review), so fall back to a non-empty scope that
            # still enables the source-only checks. But when the caller *did* thread an
            # explicit changed-path set (PR replay, ADR-035 D7 POI focusing), honour
            # 'changed' so the scan narrows to the affected TUs.
            #
            # The unseeded fallback is 'headers-only' (the public-API-covering TU
            # subset), NOT 'target' (the whole target): an unseeded s5/pr run otherwise
            # silently pays full-target (== s6) replay cost — the ADR-035 P3 cliff
            # (validation/uxl-scan-levels-timing-2026-06.md). 'headers-only' keeps a
            # non-empty public surface for the cross-checks at a fraction of the cost;
            # the caller (cli_scan) emits the advisory naming --since to focus further.
            replay_scope = (
                "headers-only" if (scope == "changed" and not changed_paths) else scope
            )
            # L4 per-TU cache dir: explicit arg wins, else the ABICHECK_L4_CACHE_DIR
            # env (the CI-friendly knob — point it at a restored cache directory).
            l4_cache_dir = source_abi_cache_dir
            if l4_cache_dir is None:
                env_dir = os.environ.get("ABICHECK_L4_CACHE_DIR")
                l4_cache_dir = Path(env_dir) if env_dir else None
            surface, l4_selected_units = _run_inline_source_abi(
                sources,
                merged,
                extractors,
                extractor=extractor,
                scope=replay_scope,
                clang_bin=clang_bin,
                exported_symbols=exported_symbols,
                source_abi_cache_dir=l4_cache_dir,
                changed_paths=changed_paths,
                public_header_roots=public_header_roots,
            )
            # Gap-1: on an unseeded headers-only replay, scope the L5 call-graph
            # pass to the *same* TU set L4 used instead of the whole compile DB.
            # (Seeded runs scope by changed_paths; full/target keep the broad pass.)
            #
            # Only narrow when L4 *actually* selected units. An empty set means L4
            # could not select (no --sources tree, no compile units, or no
            # extractor) — NOT "scope to zero" — so a build-info-only deep scan must
            # keep the broad call-graph pass over ``merged`` rather than silently
            # collecting zero call edges (Codex review).
            if (
                replay_scope == "headers-only"
                and not changed_paths
                and l4_selected_units
            ):
                call_graph_units = l4_selected_units
        # Fold a call graph (DECL_CALLS_DECL edges) into the L5 graph whenever L4 also
        # ran — i.e. a semantic source mode (source-*/graph-summary/graph-full), not
        # the structural-only graph-build (L3+L5, no L4). This is what makes the
        # decl-dependency cross-checks (public_to_internal_dependency, ADR-035 D4)
        # reachable from `scan --source-method s5`/`--depth graph`; best-effort and
        # gated on clang++ availability (ADR-035 D4 reviewer wiring request).
        with_call_graph = "L5" in layers and "L4" in layers
        graph = (
            _build_inline_graph(
                merged,
                surface,
                with_call_graph=with_call_graph,
                clang_bin=clang_bin,
                extractors=extractors,
                changed_paths=changed_paths,
                call_graph_units=call_graph_units,
            )
            if "L5" in layers
            else None
        )

    # Always hand off (or drain) the inferred-build-dir cleanup thunks — even if
    # _resolve_compile_db / _run_compile_db / L4 replay / L5 fold raised — so the
    # build dir and its lock never leak. With `defer_cleanup`, the caller's finally
    # owns them (it runs after the scan's later phases, e.g. S2 `clang -E`); without
    # it (e.g. `dump --sources`), drain immediately (CodeRabbit).
    finally:
        if defer_cleanup is not None:
            defer_cleanup.extend(query_build_cleanups)
        else:
            from .build_query import drain_build_dir_cleanups

            drain_build_dir_cleanups(query_build_cleanups)

    has_build = bool(
        merged.compile_units
        or merged.targets
        or merged.toolchains
        or merged.link_units
        or merged.build_options
    )
    # A3: a failed/blocked build query produces no facts but is still worth
    # surfacing — keep the (near-empty) pack so its `partial` L3 coverage row and
    # the build_query diagnostic reach `compare`, rather than dropping it as if
    # nothing was attempted (Codex).
    has_query_diag = any(
        e.name in _BUILD_QUERY_DIAG_NAMES and e.status in _BUILD_QUERY_DIAG_STATUSES
        for e in extractors
    )
    if not (has_build or surface is not None or graph is not None or has_query_diag):
        return None

    pack = BuildSourcePack.empty(
        Path(""),
        abicheck_version="",
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    pack.manifest.extractors = extractors
    pack.manifest.inputs = {
        "sources": DEFAULT_REDACTION.path(str(sources)) if sources else None,
        "build_info": DEFAULT_REDACTION.path(str(build_info)) if build_info else None,
        "collected": "inline",
    }
    if has_build:
        pack.build_evidence = merged
    if surface is not None:
        pack.source_abi = surface
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = build_inline_coverage(
        merged, has_build, surface, graph, extractors
    )
    return pack


# ── L3: compile-DB resolution ─────────────────────────────────────────────────


def _resolve_compile_db(
    build_info: Path | None,
    sources: Path | None,
    cfg: BuildConfig,
    build_config_trusted_for_query: bool,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    cleanup: list[Callable[[], None]] | None = None,
    compile_db_explicit: bool = False,
    allow_inferred_build_query: bool = True,
) -> Path | None:
    """Resolve the compile DB to feed L3 (zero-config; ADR-032 amended).

    Order: an explicit ``--build-info`` path (file or dir) → a trusted
    ``--config`` ``build.query`` command result → ``build.compile_db`` in the
    source tree → an auto-discovered ``compile_commands.json`` → the **inferred,
    abicheck-authored** build-system query (cmake/make/bazel). No
    ``--allow-build-query`` flag is required: providing ``--sources`` is the
    request to collect build evidence. The only command never auto-run is an
    arbitrary ``build.query`` string from an auto-discovered (untrusted)
    ``.abicheck.yml`` — that still needs an explicit ``--config``.
    """
    # Track whether the operator gave an EXPLICIT L3 input (--build-info or a
    # build.compile_db path) that yielded nothing. If so, the default inferred
    # query must not run: a cleaned/mistyped build-info path should surface, not
    # be masked by a fresh `cmake`/`bazel` query under different flags (review).
    explicit_input_missed = False
    if build_info is not None:
        found = _compile_db_at(build_info)
        if found is not None:
            return found
        merged.diagnostics.append(
            f"build-info {build_info}: no {_COMPILE_DB_NAME} found"
        )
        explicit_input_missed = True

    # build.query (ADR-032 D5 query_build_system): a tree-supplied command that
    # EMITS a compile DB / exports without a full build. Runs only when the config
    # came from an explicit operator-supplied path (build_config_trusted_for_query);
    # an auto-discovered .abicheck.yml is never trusted to execute. No
    # --allow-build-query flag is involved any more (it is a deprecated no-op).
    if cfg.query:
        if not build_config_trusted_for_query:
            extractors.append(
                ExtractorRecord(
                    name="build_query",
                    status="skipped",
                    detail=(
                        "build.query ignored from auto-discovered .abicheck.yml; "
                        "pass a trusted config with --config to permit queries"
                    ),
                )
            )
            # Untrusted query is never run — fall through to compile_db /
            # auto-discovery / the abicheck-authored inferred query below.
        else:
            # Trusted operator config (--config): run its query automatically. No
            # --allow-build-query flag is required any more — pointing abicheck at
            # sources *is* the request to collect build evidence (ADR-032 amended).
            queried = _run_build_query(cfg, sources, merged, extractors)
            if queried is not None:
                return queried
            # The operator supplied an explicit query and it failed / produced no
            # compile DB. Surface that — do NOT mask it by falling back to a
            # compile_db glob, a stale auto-discovered DB from a prior/default
            # configure, or abicheck's default inferred query, which would collect
            # L3 with the wrong flags the custom query existed to avoid (review).
            # The build_query diagnostic _run_build_query recorded explains the miss.
            return None

    if cfg.compile_db and sources is not None:
        # Only an *operator-supplied* build.compile_db (a CLI --build-compile-db or
        # an explicit --config path) counts as an explicit input whose miss should
        # suppress fallback — tracked by `compile_db_explicit`, which is distinct
        # from query-execution trust (review): --build-compile-db makes the DB
        # explicit without trusting a query, and --build-query trusts a query
        # without making a DB explicit. A build.compile_db from an auto-discovered
        # .abicheck.yml is not something the user chose, so a stale/cleaned path
        # there still falls through to the zero-config inferred query.
        if compile_db_explicit:
            explicit_input_missed = True
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                return match

    if explicit_input_missed:
        # An explicit --build-info / --build-compile-db / --config compile-DB input
        # was given but resolved to nothing. Surface that miss rather than masking
        # it with a stale auto-discovered DB OR abicheck's default inferred query
        # under different flags — checked BEFORE auto-discovery so a stray
        # build/compile_commands.json can't silently stand in (review).
        return None

    discovered = _autodiscover_compile_db(sources)
    if discovered is not None:
        return discovered

    if not allow_inferred_build_query:
        # An L2-only caller (--depth headers / collect_mode "off") reached the
        # zero-config fallback: it wants build-derived include dirs to parse headers,
        # but no evidence was requested, so we must not run a build system. Passive
        # discovery above is honoured; the inferred cmake/make/bazel query is not —
        # that would violate the L2-only depth contract and could spend up to the
        # inferred-query timeout evaluating build scripts (Codex review).
        merged.diagnostics.append(
            "inferred build-system query skipped: no evidence depth requested "
            "(L2-only); pass --build-info or generate a compile_commands.json to "
            "seed include dirs"
        )
        return None

    # Zero-config fallback: no compile DB exists and no explicit L3 input was
    # given, but a --sources tree is present. Detect the build system and run
    # abicheck's OWN fixed query (cmake configure / bazel aquery / make dry-run)
    # to produce L3 —
    # so "just provide sources" works with no flag and no manual build step. Only
    # an abicheck-authored command runs here; an arbitrary tree-local
    # .abicheck.yml `build.query` string is never auto-executed.
    from .build_query import run_inferred_build_query

    return run_inferred_build_query(sources, merged, extractors, cleanup=cleanup)


def _compile_db_at(path: Path) -> Path | None:
    """Resolve a build-info input to a concrete ``compile_commands.json``.

    A directory is searched with the shared P4 strategy (hint dirs + any
    immediate subdirectory) so ``--build-info <dir>`` honours the same contract
    as ``--sources`` auto-discovery (Codex review).
    """
    if path.is_file():
        # An explicit --build-info file is honoured as the compile DB whatever
        # its name (the user pointed straight at it).
        return path
    if path.is_dir():
        return _find_compile_db_in_dir(path)
    return None


#: How many bytes to sniff from the head of a ``--build-info`` file when
#: classifying its format (ADR-037 D5 #5). Enough to see the top-level JSON
#: shape + the first discriminating key without reading a huge aquery dump.
_BUILD_INFO_SNIFF_BYTES = 65536


def sniff_build_info_format(path: Path) -> str:
    """Classify a ``--build-info`` path by content (ADR-037 D5 #5).

    Returns one of ``"pack"`` (a ``collect`` pack dir), ``"build_dir"`` (a
    directory to search for ``compile_commands.json``), ``"compile_db"`` (a
    Clang/CMake ``compile_commands.json`` — a JSON *array*), ``"bazel_aquery"`` /
    ``"bazel_cquery"`` (Bazel ``--output=jsonproto`` — a JSON *object* keyed by
    ``actions`` / ``results``), or ``"unknown"``. Lets a Bazel query result and a
    pack "just work" when passed to ``--build-info`` instead of being mis-parsed
    as a compile DB. The top-level shape is read from a bounded head (``[`` = a
    compile-DB array); a ``{`` object is fully parsed so a large aquery preamble
    can't hide the discriminating key (Codex review). Never executes anything.
    """
    if path.is_dir():
        return "pack" if is_pack_dir(path) else "build_dir"
    try:
        with open(path, "rb") as f:
            head = f.read(_BUILD_INFO_SNIFF_BYTES)
    except OSError:
        return "unknown"
    text = head.decode("utf-8", "replace").lstrip()
    if not text:
        return "unknown"
    if text[0] == "[":
        return "compile_db"  # compile_commands.json is a top-level JSON array
    if text[0] != "{":
        return "unknown"
    # A JSON object: a Bazel jsonproto (aquery→"actions", cquery→"results") or an
    # object-wrapped compile DB. The discriminating key can sit far past the sniff
    # window in a large aquery dump (long artifacts/pathFragments preamble), so
    # parse the whole object to classify by key, not a bounded prefix (Codex).
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # Truncated / not-quite-JSON: fall back to the bounded-prefix heuristic.
        if '"actions"' in text:
            return "bazel_aquery"
        if '"results"' in text:
            return "bazel_cquery"
        return "unknown"
    if isinstance(data, dict):
        if "actions" in data:
            return "bazel_aquery"
        if "results" in data:
            return "bazel_cquery"
        if any(k in data for k in ("file", "command", "arguments")):
            return "compile_db"
    return "unknown"


def _maybe_collect_bazel_build_info(
    build_info: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> bool:
    """Route a pre-captured Bazel aquery/cquery ``--build-info`` to the adapter.

    Returns ``True`` (and merges the normalized :class:`BuildEvidence` into
    *merged*) when *build_info* is a Bazel jsonproto file, else ``False`` so the
    caller falls back to compile-DB resolution. Pre-captured only — the adapter is
    constructed with ``allow_query=False`` so no ``bazel`` subprocess ever runs.
    """
    if build_info is None or not build_info.is_file():
        return False
    fmt = sniff_build_info_format(build_info)
    if fmt not in ("bazel_aquery", "bazel_cquery"):
        return False
    from .adapters.bazel import BazelAdapter

    if fmt == "bazel_aquery":
        kind = "aquery"
        adapter = BazelAdapter(aquery=build_info, allow_query=False)
    else:
        kind = "cquery"
        adapter = BazelAdapter(cquery=build_info, allow_query=False)
    ev = adapter.collect()
    merged.merge(ev)
    extractors.append(
        ExtractorRecord(
            name="bazel",
            status="present" if ev.compile_units else "partial",
            detail=(
                f"pre-captured {kind} jsonproto from --build-info, "
                f"{len(ev.compile_units)} compile unit(s)"
            ),
        )
    )
    return True


def _find_compile_db_in_dir(
    directory: Path, skip_segments: frozenset[str] = frozenset()
) -> Path | None:
    """Locate a ``compile_commands.json`` under *directory* (the P4 strategy).

    Conventional build-dir hints first (fast, deterministic), then a fallback to
    *any* immediate subdirectory holding a compile DB — so a non-standard but
    common out-of-tree dir (``cmake-build-debug-gcc``, ``build-release``, an
    IDE/preset dir, …) is still found instead of silently yielding no L3
    evidence. The fallback stays at depth 1 to remain cheap and is deterministic
    (sorted). Shared by ``--sources`` auto-discovery and ``--build-info <dir>``
    resolution so both honour the same "any immediate subdirectory" contract.

    *skip_segments* names immediate subdirectories to ignore — used by
    auto-discovery to skip a stale ``.abicheck-build`` left by an older in-tree
    inferred-CMake run, so it can't short-circuit a fresh out-of-tree query with
    stale flags (Codex P2).
    """
    for hint in _COMPILE_DB_HINTS:
        if hint in skip_segments:
            continue
        candidate = (
            (directory / hint / _COMPILE_DB_NAME)
            if hint
            else (directory / _COMPILE_DB_NAME)
        )
        if candidate.is_file():
            return candidate
    fallback = sorted(
        p
        for p in directory.glob("*/" + _COMPILE_DB_NAME)
        if p.is_file() and p.parent.name not in skip_segments
    )
    return fallback[0] if fallback else None


def _autodiscover_compile_db(source_tree: Path | None) -> Path | None:
    """Best-effort search for a ``compile_commands.json`` inside a source tree.

    Skips a stale ``.abicheck-build/compile_commands.json`` (an older in-tree
    inferred-CMake artifact) so a zero-config ``--sources`` run refreshes the build
    query instead of replaying with stale flags/include paths (Codex P2).
    """
    if source_tree is None or not source_tree.is_dir():
        return None
    from .build_query import ABICHECK_BUILD_DIR

    return _find_compile_db_in_dir(
        source_tree, skip_segments=frozenset({ABICHECK_BUILD_DIR})
    )


def _run_compile_db(
    compile_db: Path,
    system: str,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    cache_dir: Path | None = None,
) -> None:
    """Normalize a compile DB into L3 build evidence (never raises).

    With ``cache_dir`` set, a content-addressed L3 cache (ADR-033 D5) skips the
    adapter when the same compile DB was normalized before (false-miss-preferring).
    """
    from .adapters import CompileDbAdapter

    hint = system if system in ("cmake", "ninja", "bazel", "make") else "generic"
    cache = None
    key = None
    if cache_dir is not None:
        from .build_cache import BuildEvidenceCache, compute_build_cache_key

        cache = BuildEvidenceCache(cache_dir)
        key = compute_build_cache_key(compile_db, hint)
        cached = cache.get(key)
        if cached is not None:
            merged.merge(cached)
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="ok",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=f"{len(cached.compile_units)} compile units (cached)",
                )
            )
            return
    try:
        ev = CompileDbAdapter(compile_db, build_system=hint).collect()
    except (OSError, ValueError) as exc:
        extractors.append(
            ExtractorRecord(
                name="compile_commands",
                status="failed",
                inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                detail=str(exc),
            )
        )
        merged.diagnostics.append(f"compile_commands: {exc}")
        return
    if cache is not None and key is not None:
        cache.put(key, ev)
    merged.merge(ev)
    extractors.append(
        ExtractorRecord(
            name="compile_commands",
            status="ok",
            inputs=[DEFAULT_REDACTION.path(str(compile_db))],
            detail=f"{len(ev.compile_units)} compile units",
        )
    )


def _run_build_query(
    cfg: BuildConfig,
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> Path | None:
    """Run the configured ``build.query`` command and return the emitted DB.

    Runs the explicit operator-configured command with ``shell=False`` (parsed
    via ``shlex``) in the source-tree cwd. This is the ADR-032 D5 ``query_build_system``
    tier: it emits flags/exports (a configured-graph/action query, ``make -n``,
    a CMake File API regeneration) — never ``cmake --build`` / ``make all``. A
    non-zero exit, missing tool, or timeout is recorded as a failed extractor and
    collection continues with whatever else is available (ADR-028 D3).
    """
    cwd = sources if sources is not None and sources.is_dir() else None
    try:
        argv = shlex.split(cfg.query)
    except ValueError as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"could not parse build.query command: {exc}",
            )
        )
        return None
    if not argv:
        return None
    scan_remaining = deadline.remaining()
    effective_timeout = (
        _QUERY_TIMEOUT_S
        if scan_remaining is None
        else min(_QUERY_TIMEOUT_S, scan_remaining)
    )
    try:
        # Bound by min(local 300s default, active scan --budget) —
        # run_bounded() alone would honor a generous outer deadline verbatim
        # instead of this query's own cap, letting a hung configured query
        # burn the whole remaining scan budget — and process-group-safe on
        # timeout. This operator-configured query runs inside
        # run_scan_core's L2-L5 deadline scope just like the zero-config
        # inferred query (Codex review, PR #591, round 8).
        with deadline.deadline_scope(effective_timeout):
            proc = deadline.run_bounded(  # noqa: S603 - operator-configured, shell=False, opt-in
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=_QUERY_TIMEOUT_S,
            )
    except deadline.DeadlineExceeded as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query aborted: scan deadline exceeded ({exc})",
            )
        )
        merged.diagnostics.append(f"build_query: scan deadline exceeded ({exc})")
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query failed to run ({argv[0]}): {exc}",
            )
        )
        merged.diagnostics.append(f"build_query: {exc}")
        return None
    if proc.returncode != 0:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
            )
        )
        merged.diagnostics.append(f"build_query: command exited {proc.returncode}")
        return None
    # The query is expected to have written/refreshed the configured compile DB.
    db: Path | None = None
    if cfg.compile_db and sources is not None:
        # The operator told us exactly where this query writes its DB. Use only
        # that path: if the query exited 0 but didn't actually produce it, do NOT
        # fall back to an auto-discovered stale compile_commands.json — that would
        # collect L3 with the wrong (default) flags the custom query existed to
        # set, while reporting success (Codex P2). Surface the miss as partial.
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                db = match
                break
    else:
        # No explicit path configured: discover the conventional compile DB the
        # query is expected to have refreshed.
        db = _autodiscover_compile_db(sources)
    extractors.append(
        ExtractorRecord(
            name="build_query",
            status="ok" if db is not None else "partial",
            detail=(
                f"ran `{argv[0]} …`; compile DB at {DEFAULT_REDACTION.path(str(db))}"
                if db is not None
                else f"ran `{argv[0]} …` but no compile DB was produced"
            ),
        )
    )
    return db


# ── L4: source ABI replay ─────────────────────────────────────────────────────


# A4 thresholds: fire only on a *strong* signal (almost no compile-DB source
# resolves under the tree) over a non-trivial number of units, so an unusual
# build layout is not mistaken for a wrong checkout.
_MISMATCH_MIN_UNITS = 3
_MISMATCH_THRESHOLD = 0.9


def _check_build_info_source_mismatch(
    merged: BuildEvidence,
    sources: Path | None,
    extractors: list[ExtractorRecord],
) -> None:
    """A4: record a diagnostic when the L3 compile units describe a different
    checkout than the ``--sources`` tree.

    Collection-time only: ``merge``/collection has no ``DiffResult`` list, so this
    is **not** a ``ChangeKind`` — it rides in the extractor ledger and
    ``BuildEvidence.diagnostics`` (the channels the later compare's coverage
    report surfaces), never as a verdict-bearing finding. Conservative by design
    (see thresholds) so it does not trip the FP-rate gate on unusual layouts.
    """
    if sources is None or not merged.compile_units:
        return
    tree = Path(sources)
    if not tree.is_dir():
        return

    # Match each compile-DB source against the tree by its *relative* path
    # (directory-prefix-stripped, forward-slash normalized), falling back to the
    # basename only when the source is not under its own compile-DB directory.
    # All comparison is string-based on precomputed posix paths — no filesystem
    # resolution — so it is robust to platform separators/drives (Windows CI) and
    # to redacted home prefixes (`~/proj/...`), while still distinguishing two
    # different checkouts that merely share filenames (review).
    tree_rel: set[str] = set()
    tree_names: set[str] = set()
    # Two-component suffixes (`parent/name`) of every tree file, so an
    # absolute/redacted compile-DB source can be matched on more than its bare
    # basename — a wrong checkout that ships `tests/foo.cpp` must not satisfy a
    # compile unit whose source is `src/foo.cpp` (review).
    tree_tail2: set[str] = set()
    for root, _dirs, files in os.walk(tree):
        for fn in files:
            rel = (Path(root) / fn).relative_to(tree).as_posix()
            tree_rel.add(rel)
            tree_names.add(fn)
            parts = rel.split("/")
            if len(parts) >= 2:
                tree_tail2.add("/".join(parts[-2:]))

    def _present(cu: object) -> bool | None:
        src = getattr(cu, "source", "")
        if not src:
            return None
        posix = str(src).replace("\\", "/")
        name = PurePosixPath(posix).name
        directory = (
            str(getattr(cu, "directory", "") or "").replace("\\", "/").rstrip("/")
        )
        if directory and posix.startswith(directory + "/"):
            return posix[len(directory) + 1 :] in tree_rel
        # A genuinely relative source (not rooted at "/", a drive "X:", or a
        # redacted home "~") can be matched against the tree's relative paths.
        rooted = (
            posix.startswith("/")
            or posix.startswith("~")
            or (len(posix) >= 2 and posix[1] == ":")
        )
        if not rooted:
            return posix in tree_rel
        # Absolute / redacted with an unknown root → the redacted/abs prefix is
        # unrecoverable, but require the source's `parent/name` suffix to exist in
        # the tree rather than its basename alone, so a same-named file in a
        # different subtree does not mask a checkout mismatch. Sources with no
        # parent component fall back to the basename.
        parts = [p for p in posix.split("/") if p and p != "~"]
        if len(parts) >= 2:
            return "/".join(parts[-2:]) in tree_tail2
        return name in tree_names

    flags = [r for r in (_present(cu) for cu in merged.compile_units) if r is not None]
    if len(flags) < _MISMATCH_MIN_UNITS:
        return
    missing = sum(1 for present in flags if not present)
    if missing / len(flags) >= _MISMATCH_THRESHOLD:
        detail = (
            f"{missing}/{len(flags)} compile-DB source files are absent from the "
            "--sources tree; build metadata and sources may be different checkouts"
        )
        extractors.append(
            ExtractorRecord(
                name="build_info_source_tree_mismatch", status="failed", detail=detail
            )
        )
        merged.diagnostics.append(f"build_info/source mismatch: {detail}")


def _run_inline_source_abi(
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    extractor: str,
    scope: str,
    clang_bin: str,
    exported_symbols: tuple[str, ...] = (),
    source_abi_cache_dir: Path | None = None,
    changed_paths: tuple[str, ...] = (),
    public_header_roots: tuple[str, ...] = (),
) -> tuple[SourceAbiSurface | None, list[Any]]:
    """Run L4 replay over a source tree; ``(None, [])`` when no source tree given.

    Returns ``(surface, selected_units)`` — the L4 surface plus the exact
    compile-unit set the replay scope selected, so the L5 call-graph pass can match
    that scope on an unseeded run (Gap-1 fix) instead of re-parsing all TUs.

    Requires L3 compile units to replay against (ADR-030 D5). A missing source
    extractor (clang/castxml) yields a partial surface and a clear note rather
    than aborting — the artifact tiers stay authoritative (ADR-028 D3).

    ``extractor == "hybrid"`` is likewise recorded as skipped rather than run:
    L4 source-ABI replay has only ever had ONE extractor implementation per
    TU (``_make_source_extractor`` special-cases "castxml", else clang) —
    there is no dual-backend merge here the way ``dumper_hybrid.py`` provides
    for the L2 header-AST snapshot. ``--ast-frontend hybrid`` reaches this
    function unchanged (it is the shared ``compile_context_options`` flag,
    passed straight through as ``extractor`` by ``dump_source_only`` — see
    `cli.py`), so treating it like any other extractor name would silently
    run clang alone while recording ``source_abi:hybrid`` as if both
    backends had (Codex review).
    """
    if sources is None:
        return None, []
    from .source_abi import SourceAbiSurface
    from .source_replay import (
        SourceAbiCache,
        public_header_roots_for,
        run_source_replay,
    )

    if extractor == "hybrid":
        extractors.append(
            ExtractorRecord(
                name="source_abi:hybrid",
                status="skipped",
                detail=(
                    "L4 source-ABI replay has no dual-backend hybrid extractor "
                    "(unlike the L2 header-AST snapshot); pass "
                    "--ast-frontend castxml or --ast-frontend clang for a "
                    "--sources/--build-info dump"
                ),
            )
        )
        return None, []

    if not merged.compile_units:
        # No L3 to replay against: source ABI replay needs compile commands to
        # know how each TU is parsed. Record why, but do not synthesize an empty
        # L4 surface — otherwise a bare tree with no build info would embed an
        # all-empty pack. With no other facts the caller drops the pack entirely.
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="skipped",
                detail=(
                    "no compile units (L3) to replay; pass --build-info or add a "
                    "compile_commands.json to the source tree"
                ),
            )
        )
        return None, []

    impl, tool_name = _make_source_extractor(extractor, clang_bin)
    if not impl.available():
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"{tool_name} not found in PATH; source-only checks disabled",
            )
        )
        return SourceAbiSurface(), []

    roots = sorted(set(public_header_roots_for(merged)) | set(public_header_roots))
    include_map = _include_map_for_replay(
        merged,
        scope=scope,
        roots=tuple(roots),
        clang_bin=clang_bin,
        extractors=extractors,
    )
    # The exact compile-unit set this replay scope selects (pure, reuses the
    # already-computed include graph — no extra clang pass). Returned so the L5
    # call-graph pass can match the L4 scope for an unseeded run (Gap-1 fix) rather
    # than re-parsing the whole compile DB.
    from .source_replay import select_compile_units

    selected_units = select_compile_units(
        merged,
        scope=scope,
        changed_paths=changed_paths,
        include_map=include_map,
        public_header_roots=roots,
    )
    # D8 per-TU cache: re-extracting every TU on every `dump --sources` is the
    # cold-start cost (eval E4: zstd 48.6 s cold → 3.4 s warm). Wire the cache
    # when a dir is given (CLI/env), so a persisted dir restored across CI runs
    # makes each run start warm. Absent a dir, behaviour is unchanged (no cache).
    cache = SourceAbiCache(source_abi_cache_dir) if source_abi_cache_dir else None
    started = time.monotonic()
    surface, diagnostics = run_source_replay(
        merged,
        impl,
        scope=scope,
        changed_paths=changed_paths,
        public_header_roots=roots,
        exported_symbols=exported_symbols,
        cache=cache,
        include_map=include_map,
    )
    elapsed = time.monotonic() - started
    if surface is not None:
        surface.coverage.setdefault("elapsed_s", round(elapsed, 3))
    if cache is not None:
        rate = cache.hit_rate
        if rate is not None:
            merged.diagnostics.append(
                f"source_abi: L4 cache hit rate {rate:.0%} "
                f"({cache.hits}/{cache.hits + cache.misses})"
            )
        # Thread the cache stats into the surface so the live L4 coverage row can
        # report them too (ADR-035 P5) — not only `scan --estimate` (which probes
        # the cache up front). `build_inline_coverage` reads these keys.
        if surface is not None:
            surface.coverage["cache_hits"] = cache.hits
            surface.coverage["cache_misses"] = cache.misses
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    extra = f", {elapsed:.2f}s"
    if surface.coverage.get("scope_widened_to_full"):
        extra += ", widened-to-full"
    extractors.append(
        ExtractorRecord(
            name=f"source_abi:{extractor}",
            status="ok" if parsed else "partial",
            detail=(
                f"scope={scope}, {parsed}/{selected} TUs parsed, "
                f"{len(diagnostics)} failures{extra}"
            ),
        )
    )
    return surface, selected_units


def _include_map_for_replay(
    build: BuildEvidence,
    *,
    scope: str,
    roots: tuple[str, ...],
    clang_bin: str,
    extractors: list[ExtractorRecord],
) -> dict[str, list[str]]:
    """Best-effort include map for narrowing L4 replay.

    ``headers-only`` can shrink from all TUs to the TUs that include public
    headers, but only when it has an exact textual include graph. Recorded action
    inputs are an over-approximation, so headers-only replay uses a cheap depfile
    pass instead. Failure keeps the old fail-open selector, never drops evidence.
    """
    if scope != "headers-only" or not roots or not build.compile_units:
        return {}
    from .include_graph import ClangIncludeExtractor

    extractor = ClangIncludeExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    include_map = extractor.extract_from_build(build)
    status = "ok" if include_map else "skipped"
    detail = f"{len(include_map)}/{len(build.compile_units)} compile units"
    if extractor.diagnostics:
        status = "partial" if include_map else "failed"
        detail += "; " + "; ".join(extractor.diagnostics[:3])
    extractors.append(
        ExtractorRecord(name="include_graph:clang", status=status, detail=detail)
    )
    return include_map


def _make_source_extractor(
    extractor: str, clang_bin: str
) -> tuple[SourceAbiExtractor, str]:
    if extractor == "castxml":
        from .source_extractors import CastxmlSourceExtractor

        return CastxmlSourceExtractor(), "castxml"
    from .source_extractors import ClangSourceExtractor

    return ClangSourceExtractor(clang_bin=clang_bin), clang_bin


# ── L5: source graph ──────────────────────────────────────────────────────────


def _build_inline_graph(
    merged: BuildEvidence,
    surface: SourceAbiSurface | None,
    *,
    with_call_graph: bool = False,
    clang_bin: str = "clang",
    extractors: list[ExtractorRecord] | None = None,
    changed_paths: tuple[str, ...] = (),
    call_graph_units: list[Any] | None = None,
) -> SourceGraphSummary | None:
    """Fold L3 + optional L4 into the compact L5 source graph (always when L3).

    Per the amendment D2 the graph is built whenever a source surface or build
    evidence exists — it is compact by design (ADR-031 D7), so there is no
    separate opt-in flag.

    When ``with_call_graph`` is set, Clang call/type-graph passes fold
    ``DECL_CALLS_DECL``/``TYPE_INHERITS``/``TYPE_HAS_FIELD_TYPE``/
    ``DECL_HAS_TYPE``/``DECL_REFERENCES_DECL`` edges into the graph
    (best-effort — a missing ``clang++`` or a parse failure records an
    extractor row and leaves the graph without those edges, never aborting),
    and an include-graph pass folds ``COMPILE_UNIT_INCLUDES_FILE`` edges the
    same way (preferring already-recorded build-tool inputs over a fresh
    ``clang -M`` invocation when available). Those edges are what the
    decl-dependency cross-checks (ADR-035 D4) and the D6
    ``include_graph_public_header_drift`` finding consume, so this is gated to
    the semantic L4 modes by the caller — no separate opt-in flag for any of
    the three (ADR-041 header-only-graph addendum follow-up: these used to be
    ``collect``-only, explicit-flag-gated passes with no equivalent here at
    all).
    """
    has_build = bool(merged.compile_units or merged.targets)
    if not has_build and surface is None:
        return None
    from .source_graph import build_source_graph

    graph = build_source_graph(merged, source_abi=surface)
    if with_call_graph:
        from .inline_graph_fold import (
            fold_call_graph,
            fold_include_graph,
            fold_type_graph,
        )

        # NOTE: this always runs the replay passes even when `surface`'s
        # source_edges are already confirmed complete (build_source_graph()
        # above already folded those in via fold_source_edges) -- an earlier
        # revision skipped the replay in that case, but the raw source_edges
        # wire format carries only bare endpoint identities, not the
        # dst_file/project-file provenance fold_call_graph/fold_type_graph
        # attach via `project_files` (`defined_in_project`). Without that
        # provenance, `crosscheck.public_to_internal_dependency` cannot
        # classify an unannotated callee/referenced node as internal, so a
        # public-to-internal dependency addition would silently go
        # undetected (Codex review). The replay stays unconditional until
        # source_edges carries equivalent provenance end-to-end.
        fold_call_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=call_graph_units,
        )
        fold_type_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=call_graph_units,
        )
        fold_include_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=call_graph_units,
        )
    graph.finalize()
    return graph


# ── coverage rows ─────────────────────────────────────────────────────────────


def _l4_coverage_detail(surface: SourceAbiSurface) -> str:
    """A human L4 coverage detail from the surface's recorded counts (ADR-035 P5).

    The live row was previously blank — only ``scan --estimate`` reported TU
    counts. Mirror that here: replay scope, parsed/selected TUs, matched/exported
    symbols, and (when an L4 cache ran) its hit/miss tally.
    """
    cov = surface.coverage
    scope = cov.get("replay_scope")
    parts: list[str] = []
    if scope:
        parts.append(f"scope={scope}")
    selected = cov.get("compile_units_selected")
    parsed = cov.get("compile_units_parsed")
    if selected is not None or parsed is not None:
        parts.append(f"{int(parsed or 0)}/{int(selected or 0)} TUs parsed")
    matched = cov.get("matched_symbols")
    exported = cov.get("exported_symbols")
    if matched is not None or exported is not None:
        m = int(matched or 0)
        e = int(exported or 0)
        parts.append(f"{m}/{e} symbols matched")
        # A bare "matched/exported" ratio reads like a coverage gap: for a real
        # C++ library most exports are RTTI/vtable/thunk (synthesized) or
        # stdlib/internal (classified), not direct decl matches, so "matched"
        # alone can look ~50% while every symbol is in fact accounted for.
        # Surface the full accounting so 100% coverage is visible, not hidden.
        attributed = (
            int(cov.get("synthesized_symbols_matched", 0) or 0)
            + int(cov.get("template_instantiation_symbols_matched", 0) or 0)
            + int(cov.get("allocator_interposer_symbols_matched", 0) or 0)
            + int(cov.get("non_public_symbols_classified", 0) or 0)
        )
        unmatched = cov.get("unmatched_symbols")
        if attributed or unmatched is not None:
            accounted = m + attributed
            u = int(unmatched if unmatched is not None else max(e - accounted, 0))
            parts.append(f"{accounted}/{e} accounted, {u} unmatched")
    if "cache_hits" in cov or "cache_misses" in cov:
        hits = int(cov.get("cache_hits", 0) or 0)
        misses = int(cov.get("cache_misses", 0) or 0)
        total = hits + misses
        if total:
            parts.append(f"cache {hits}/{total} hit ({hits / total:.0%})")
    if cov.get("scope_widened_to_full"):
        parts.append("headers-only widened to full")
    uncovered = int(cov.get("public_headers_uncovered", 0) or 0)
    if uncovered:
        parts.append(f"{uncovered} public header(s) not reached by include graph")
    elapsed = cov.get("elapsed_s")
    if elapsed is not None:
        parts.append(f"{float(elapsed):.2f}s")
    failures = int(cov.get("extractor_failures", 0) or 0)
    if failures:
        parts.append(f"{failures} extractor failures")
    return ", ".join(parts)


def build_inline_coverage(
    merged: BuildEvidence,
    has_build: bool,
    surface: SourceAbiSurface | None,
    graph: SourceGraphSummary | None,
    extractors: list[ExtractorRecord] | tuple[ExtractorRecord, ...] = (),
) -> list[LayerCoverage]:
    """Build L3/L4/L5 coverage rows for an inline-collected pack (ADR-028 D7)."""
    if has_build:
        systems = sorted({g.kind for g in merged.generators}) or ["generic"]
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.HIGH
            if merged.targets
            else LayerConfidence.REDUCED,
            detail=(
                f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
                f"{len(merged.targets)} targets"
            ),
        )
    else:
        # A3: a build query that was attempted but failed (or was blocked because
        # --allow-build-query was not set) yielded no L3 facts. Surface that as a
        # `partial` row with the reason instead of a silent `not_collected`, so
        # the coverage/capability report tells the user exactly what to fix.
        bq = next(
            (
                e
                for e in extractors
                if e.name in _BUILD_QUERY_DIAG_NAMES
                and e.status in _BUILD_QUERY_DIAG_STATUSES
            ),
            None,
        )
        if bq is not None:
            l3 = LayerCoverage(
                layer=DataLayer.L3_BUILD.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.UNKNOWN,
                detail=f"build query {bq.status}: {bq.detail}",
            )
        else:
            l3 = LayerCoverage(
                layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
            )

    if surface is not None:
        any_entities = bool(
            surface.reachable_declarations
            or surface.reachable_types
            or surface.reachable_macros
            or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        cov = surface.coverage or {}
        exported = int(cov.get("exported_symbols", 0) or 0)
        matched = int(cov.get("matched_symbols", 0) or 0)
        zero_match_degraded = exported > 0 and matched == 0
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value,
            status=CoverageStatus.PRESENT
            if any_entities and not zero_match_degraded
            else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.HIGH
            if any_entities and not zero_match_degraded
            else LayerConfidence.REDUCED,
            detail=_l4_coverage_detail(surface),
            elapsed_s=float(cov.get("elapsed_s", 0.0) or 0.0),
        )
    else:
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        )

    if graph is not None:
        # AC-006: a degraded call/type pass folded structural/plugin edges but the
        # live replay it stands in for never completed (`degraded_passes`, set by
        # `mark_source_edges_extractor_coverage` and the scoped-graph fold). Those
        # edges make `graph.edges` non-empty, which must NOT let L5 read as a full
        # `present` graph — the failed pass would be silently hidden. Downgrade to
        # `partial` whenever any pass is degraded, and name the passes so the
        # report says which live walk is missing.
        degraded = sorted(k for k, v in graph.degraded_passes.items() if v)
        l5_present = bool(graph.edges) and not degraded
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if l5_present else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED
            if l5_present
            else LayerConfidence.UNKNOWN,
            detail=(
                "degraded passes (structural/plugin edges only, live replay "
                f"incomplete): {', '.join(degraded)}"
                if degraded
                else None
            ),
        )
    else:
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED
        )
    return [l3, l4, l5]


def __getattr__(name: str) -> object:
    """Lazily re-export the L2 include-seeding helpers from :mod:`l2_seed`.

    ``derive_l2_include_dirs`` / ``seed_l2_includes`` were split into a sibling
    module to keep this file under the size cap, but they have historically been
    imported from ``inline`` (the CLI callers and tests use that path). Resolving
    them here via ``importlib`` on attribute access preserves those import paths
    without a static ``inline`` -> ``l2_seed`` import edge, which would re-create
    the import cycle the split avoids (l2_seed imports collect_inline_pack etc.
    from here). See ADR-037 D10.1's cli_buildsource shim for the same pattern.
    """
    if name in ("derive_l2_include_dirs", "seed_l2_includes"):
        import importlib

        return getattr(importlib.import_module(".l2_seed", __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

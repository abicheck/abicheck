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

"""The typed request for a stored-bundle-facts ``compare``, and the rules
that decide whether it can honor every option it carries.

A stored OLD_INPUT (and optionally a stored NEW_INPUT) is a resolved
snapshot, so most ``compare`` options have no channel to act on it. Each
such option is rejected here rather than silently ignored -- the rule
``compare_bundle_facts`` documents at its module level. This module is the
Click-free owner of those rules: the CLI adapter translates
:class:`BundleFactsCompareError` into an exit code and never decides which
combinations are supported itself.

Rules run in a fixed order, and the first violated rule names the error, so
the order is part of the contract and is pinned by the CLI characterization
tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..model.sided_inputs import compose_sided_paths

#: The only report formats a stored-bundle-facts comparison renders.
#:
#: One owner for both directions: :func:`validate_bundle_facts_request`
#: rejects an explicitly requested format outside this set, and the CLI's
#: default-format fallback draws from it. Two copies would be free to
#: disagree about which formats exist.
#: Formats a stored-bundle-facts comparison renders. ``junit``/``oneline`` go
#: through the same release renderer a live directory comparison uses; HTML
#: and ``--view`` stay out because the HTML document needs the effective-
#: config receipt, which a stored comparison does not resolve yet.
STORED_BUNDLE_FACTS_FORMATS: frozenset[str] = frozenset(
    {"json", "markdown", "junit", "oneline"}
)


class BundleFactsCompareError(Exception):
    """A stored-bundle-facts comparison that cannot run as requested.

    *usage* is true when the request itself is malformed or asks for
    something this operand shape cannot honor (the CLI renders exit ``64``);
    false when the run failed operationally (exit ``1``). *param_hint*, when
    set, names the option a usage error is about.
    """

    def __init__(
        self, message: str, *, usage: bool, param_hint: str | None = None
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.param_hint = param_hint


def _discard_warning(_message: str) -> None:
    """Default *warn* sink: a library caller that wants no stderr output."""


@dataclass(frozen=True)
class BundleFactsCompareRequest:
    """Everything a stored-bundle-facts ``compare`` is asked to do.

    The CLI fills this from parsed flags and resolved compile context. Each
    field that exists only to be rejected is a presence flag or the raw
    value the rule inspects; nothing here is re-derived from a Click context.
    """

    old_facts_path: Path
    new_input: Path
    new_is_stored: bool = False

    # Project config and compile context. ``config_path`` is already resolved
    # (explicit or cwd-discovered); ``config_explicit`` says which.
    config_path: Path | None = None
    config_explicit: bool = False
    compile_context: Any = None

    # Policy and suppression. The CLI loads the suppression list and policy
    # file itself (its own flag parsing owns those paths), so the loader is
    # a callable: it runs at the same point in the sequence as before, after
    # the request is validated.
    policy: str = "strict_abi"
    load_suppression_and_policy: Callable[[], tuple[Any, Any]] | None = None

    # Output. ``warn`` receives bare text; the caller decides how to present it.
    fmt: str = "json"
    secondary_writes: tuple[tuple[str, Path], ...] = ()
    output: Path | None = None
    output_dir: Path | None = None
    bundle_facts_out: Path | None = None
    warn: Callable[[str], None] = _discard_warning

    # Comparison shape.
    depth: str | None = None
    lang: str = "c++"
    lang_explicit: bool = False
    new_version: str | None = "new"
    old_version: str | None = None
    include_dependencies: bool = False
    show_only: bool = False
    report_mode: str | None = None
    contract_mode: str | None = None
    severity_preset: str | None = None
    pack_paths: tuple[Any, ...] = ()
    dry_run: bool = False

    # Header and include operands, uniform and side-scoped (ADR-040).
    headers: tuple[Any, ...] = ()
    new_headers_only: tuple[Any, ...] = ()
    old_headers_only: tuple[Any, ...] = ()
    includes: tuple[Any, ...] = ()
    new_includes_only: tuple[Any, ...] = ()
    old_includes_only: tuple[Any, ...] = ()
    new_devel_pkg: Path | None = None
    old_devel_pkg_given: bool = False
    manifest_path: Path | None = None
    bundle_facts_library_manifest: Path | None = None

    # Presence of options with no channel into a stored comparison. Only
    # truthiness is ever read, so the CLI reduces each to a bool.
    used_by_apps: tuple[Any, ...] = ()
    required_symbols: tuple[Any, ...] = ()
    use_cases_manifest: Path | None = None
    diagnostic_comparison: bool = False
    include_labels: Any = None
    build_or_source_operands_given: bool = False
    probe_matrix_given: bool = False
    debug_info_given: bool = False
    debug_roots_given: bool = False
    dependency_flags_given: bool = False


def resolve_max_json_object_nodes_cfg(
    configured: int | None, *, config_explicit: bool, default: int
) -> int | None:
    """Resolve ``resource_limits.max_bundle_facts_decode_nodes`` for a
    stored-bundle-facts ``compare`` (Phase 7g).

    This budget guards a pre-``json.loads()`` decode-bomb defense over content
    that may itself be attacker-controlled. Only an *explicit* ``--config`` --
    an operator's own deliberate choice -- may *raise* the budget past
    *default*; an auto-discovered ``.abicheck.yml`` can come from the same
    untrusted checkout being decoded. An auto-discovered config may still
    *lower* the budget, since narrowing a ceiling is never a decode-bomb risk.
    """
    if configured is None or config_explicit:
        return configured
    return min(configured, default)


def load_request_config(config_path: Path | None) -> Any:
    """The parsed build config for *config_path*, or ``None`` when there is
    none. A malformed file is a usage error, never a raw ``ValueError``."""
    if config_path is None:
        return None
    from .extraction import load_build_config_with_digest

    try:
        return load_build_config_with_digest(Path(config_path))[0]
    except ValueError as exc:
        raise BundleFactsCompareError(
            f"cannot parse build config {config_path}: {exc}", usage=True
        ) from exc


def reject_explicit_compile_config(config_path: Path) -> None:
    """Reject an *explicitly* given ``--config`` that declares ``compile:``
    settings a stored/stored comparison has no channel to honor.

    An auto-discovered ``.abicheck.yml`` is deliberately not checked: an
    ambient project config silently applying nothing is the point of not
    letting an unrelated project default break a stored/stored comparison.
    """
    bc = load_request_config(config_path)
    if (
        bc.compile_frontend is not None
        or bc.compile_std is not None
        or bc.compile_include_dirs
        or bc.compile_defines
        or bc.compile_sysroot is not None
        or bc.compile_nostdinc is not None
        or bc.compile_compiler is not None
        or bc.compile_options
        or bc.compile_ast_frontend_fallback is not None
        or bc.compile_allow_unsupported_castxml is not None
        or bc.compile_frontend_context is not None
        or bc.compile_lang is not None
    ):
        raise BundleFactsCompareError(
            f"{config_path} declares compile: settings, which are not "
            "supported when both OLD_INPUT and NEW_INPUT are stored "
            "BundleFacts documents: neither side runs any header-frontend "
            "extraction for them to configure. Omit --config to let an "
            "ambient project config, if any, stay harmlessly unused here "
            "instead, or use a --config that declares no compile: block.",
            usage=True,
        )


def validate_bundle_facts_request(request: BundleFactsCompareRequest) -> Any:
    """Raise :class:`BundleFactsCompareError` for any option this request's
    operand shape has no channel to honor; return the loaded build config.

    Pure apart from reading the config file and probing NEW_INPUT's shape.
    The rule order below is the contract -- see the module docstring.
    """
    cfg = load_request_config(request.config_path)
    new_is_single_file = not request.new_is_stored and not _new_input_is_bundle(
        request.new_input
    )
    dso_only = bool(cfg.release_dso_only) if cfg else False
    include_private_dso = bool(cfg.release_include_private_dso) if cfg else False
    fail_on_removed = bool(cfg.gate_fail_on_removed_library) if cfg else False
    support_promise = cfg.release_support_promise if cfg else None

    _reject_unsupported_output(request, fail_on_removed, support_promise)
    _reject_unsupported_comparison_options(request)
    _reject_release_only_set_inputs(request)
    _reject_unsupported_inputs(request)
    if request.depth in ("build", "source"):
        raise BundleFactsCompareError(
            f"--depth {request.depth} is not supported together with "
            "a stored-bundle-facts OLD_INPUT: this driver has no channel for L3-L5 "
            "build/source evidence.",
            usage=True,
        )
    if request.show_only:
        raise BundleFactsCompareError(
            "--view show=... is not supported together with a "
            "stored-bundle-facts OLD_INPUT (was --show-only).",
            usage=True,
        )
    if request.report_mode not in (None, "full"):
        raise BundleFactsCompareError(
            "--view <mode> is not supported together "
            "with a stored-bundle-facts OLD_INPUT (was --report-mode).",
            usage=True,
        )
    _reject_unsupported_config_blocks(request, cfg)
    if request.old_headers_only or request.old_includes_only:
        raise BundleFactsCompareError(
            "--header old=.../--include old=... are not supported together "
            "with a stored-bundle-facts OLD_INPUT: OLD_FACTS is already a resolved, "
            "stored snapshot with no header re-extraction available.",
            usage=True,
        )
    if request.old_version not in (None, "", "old"):
        raise BundleFactsCompareError(
            "--version old=... is not supported together with "
            "a stored-bundle-facts OLD_INPUT: OLD_FACTS's own per-library "
            "snapshots already carry whatever version they were captured with.",
            usage=True,
        )
    if new_is_single_file and (dso_only or include_private_dso):
        raise BundleFactsCompareError(
            "release.dso_only/release.include_private_dso are not supported "
            "when NEW_INPUT resolves to a single library file rather than a "
            "directory or package archive: compare_release_against_bundle_"
            "facts() accepts a single-file NEW_INPUT as-is, with no ELF-type "
            "check and no directory to scope -- these settings would "
            "silently have no effect. Point NEW_INPUT at a directory or "
            "package archive if you need executable filtering, or unset "
            "these settings.",
            usage=True,
        )
    if request.new_is_stored:
        _reject_new_side_extraction_options(
            request, dso_only=dso_only, include_private_dso=include_private_dso
        )
    return cfg


def _new_input_is_bundle(new_input: Path) -> bool:
    """True when NEW_INPUT is a directory or a recognized package archive --
    the two shapes ``compare_release_against_bundle_facts`` can scope."""
    from .extraction import is_package

    return new_input.is_dir() or is_package(new_input)


def _reject_unsupported_output(
    request: BundleFactsCompareRequest,
    fail_on_removed: bool,
    support_promise: str | None,
) -> None:
    if request.fmt not in STORED_BUNDLE_FACTS_FORMATS:
        raise BundleFactsCompareError(
            f"-o {request.fmt}=... is not available with a stored-bundle-facts OLD_INPUT: only "
            "json/markdown/junit/oneline are supported for a stored-bundle-facts "
            "comparison. Choose one of: json, markdown, junit, oneline.",
            usage=True,
        )
    for secondary_fmt, _secondary_path in request.secondary_writes:
        if secondary_fmt not in STORED_BUNDLE_FACTS_FORMATS:
            raise BundleFactsCompareError(
                f"-o {secondary_fmt}=... is not available with "
                "a stored-bundle-facts OLD_INPUT: only json/markdown/junit/oneline are "
                "supported for a stored-bundle-facts comparison.",
                usage=True,
            )
    if fail_on_removed:
        raise BundleFactsCompareError(
            "gate.fail_on_removed_library is not supported together with "
            "a stored-bundle-facts OLD_INPUT: answering it would require re-scanning "
            "OLD_FACTS a second time, defeating the point of handing in an "
            "already-loaded facts document. Diff the stored facts' own "
            "per_library_snapshots keys against the release directory "
            "yourself if you need this accounting.",
            usage=True,
        )
    if support_promise and support_promise != "off":
        raise BundleFactsCompareError(
            "release.support_promise is not supported together with "
            "a stored-bundle-facts OLD_INPUT: this driver builds no proven-"
            "inventory acquisition record to derive a support-promise "
            "finding from. Compare the release directory/package itself "
            "(not a stored bundle-facts document) to use it.",
            usage=True,
        )
    if request.bundle_facts_out is not None:
        raise BundleFactsCompareError(
            "--bundle-facts-out is not supported together with "
            "a stored-bundle-facts OLD_INPUT: the OLD side is already a stored facts "
            "document, so there is nothing new to persist from it.",
            usage=True,
        )


def _reject_unsupported_comparison_options(request: BundleFactsCompareRequest) -> None:
    if request.dry_run:
        raise BundleFactsCompareError(
            "--dry-run is not supported together with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.contract_mode is not None:
        raise BundleFactsCompareError(
            "--contract is not supported together with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.severity_preset is not None or request.pack_paths:
        raise BundleFactsCompareError(
            "--severity-preset/--pack are not supported together with "
            "a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )


def _reject_release_only_set_inputs(request: BundleFactsCompareRequest) -> None:
    """Directory/package-only flags, rejected for a stored OLD_INPUT.

    The messages are the release fan-out's own, reused verbatim: this driver
    NEW_INPUT is exactly that operand shape, and every one of these flags is
    equally unconsumed here. Parity with ``cli_compare_options.
    _reject_set_input_flags`` is pinned by a test, so the two cannot drift.
    ``--depth binary`` is deliberately not in this set: it is a supported
    combination here.
    """
    if request.used_by_apps:
        raise BundleFactsCompareError(
            "--used-by/--used-by-manifest is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no per-app scoping. Compare the specific library "
            "individually with --used-by/--used-by-manifest.",
            usage=True,
        )
    if request.required_symbols:
        raise BundleFactsCompareError(
            "--required-symbol is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no plugin-host-contract scoping. Compare the "
            "specific library individually with --required-symbol.",
            usage=True,
        )
    if request.use_cases_manifest is not None:
        raise BundleFactsCompareError(
            "--use-cases is not supported for directory/package (release) "
            "comparisons: attribution walks one pair's own call graphs, and "
            "the per-library fan-out never builds them, so the manifest "
            "would be accepted and attribute nothing. Compare the specific "
            "library individually with --use-cases.",
            usage=True,
        )
    if request.diagnostic_comparison:
        raise BundleFactsCompareError(
            "--diagnostic-comparison is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "wire the comparability gate's diagnostic escape "
            "hatch (a mismatch there still raises unhandled). Compare the "
            "specific library individually to use it.",
            usage=True,
        )
    if request.include_labels:
        raise BundleFactsCompareError(
            "A labeled --include (old:LABEL=PATH/new:LABEL=PATH/"
            "both:LABEL=PATH) is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "thread the include labels into its per-library "
            "dumps, so the label would be silently dropped. Compare the "
            "specific library individually to use it.",
            usage=True,
        )


def _reject_unsupported_inputs(request: BundleFactsCompareRequest) -> None:
    if request.build_or_source_operands_given:
        raise BundleFactsCompareError(
            "--sources/--build-info/--dump-manifest are not supported "
            "together with a stored-bundle-facts OLD_INPUT: this driver has no channel "
            "for inline build/source evidence on either side.",
            usage=True,
        )
    if request.probe_matrix_given:
        raise BundleFactsCompareError(
            "A --build-info build-configuration matrix is not supported together "
            "with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.debug_info_given:
        raise BundleFactsCompareError(
            "--debug-info is not supported together with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.old_devel_pkg_given:
        raise BundleFactsCompareError(
            "A --header old=... development package is not supported together with "
            "a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.dependency_flags_given:
        raise BundleFactsCompareError(
            "--follow-deps/--search-path/--ld-library-path are not "
            "supported together with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )
    if request.debug_roots_given:
        raise BundleFactsCompareError(
            "--debug-info is not supported together with a stored-bundle-facts OLD_INPUT.",
            usage=True,
        )


def _reject_unsupported_config_blocks(
    request: BundleFactsCompareRequest, cfg: Any
) -> None:
    """Config-file blocks whose settings this operand shape cannot honor.

    Each is the config-key twin of an explicit flag rejected above: a project
    can declare the setting in ``.abicheck.yml`` with no flag at all."""
    if request.config_path is None or cfg is None:
        return
    unsupported: list[str] = []
    if cfg.severity_preset is not None or any(
        getattr(cfg, field) is not None
        for field in (
            "severity_abi_breaking",
            "severity_potential_breaking",
            "severity_quality_issues",
            "severity_addition",
        )
    ):
        unsupported.append("severity:")
    if (
        cfg.scope_public is not None
        or cfg.collapse_versioned_symbols is not None
        or cfg.public_symbols
        or cfg.scope_show_redundant is not None
    ):
        unsupported.append("scope:")
    if cfg.contract_post_manifest is not None:
        unsupported.append("contract:")
    if (
        cfg.suppression_strict is not None
        or cfg.suppression_require_justification is not None
    ):
        unsupported.append("suppression:")
    if any(
        getattr(cfg, field) is not None
        for field in (
            "debug_format",
            "debug_dwarf_only",
            "debug_debuginfod",
            "debug_debuginfod_url",
            "debug_pdb_path",
        )
    ):
        unsupported.append("debug:")
    if cfg.source_method is not None:
        unsupported.append("source:")
    if unsupported:
        raise BundleFactsCompareError(
            f"{request.config_path} declares "
            f"{', '.join(unsupported)} settings, which "
            "are not supported together with a stored-bundle-facts OLD_INPUT: "
            "compare_release_against_bundle_facts() has no channel to "
            "honor them (same reason --severity-preset/--pack/"
            "--contract are rejected as explicit flags). Use a --config that only sets compile: options.",
            usage=True,
        )


def _reject_new_side_extraction_options(
    request: BundleFactsCompareRequest,
    *,
    dso_only: bool,
    include_private_dso: bool,
) -> None:
    """Stored/stored: the NEW-side mirror of the OLD-side extraction rules.

    ``compare_stored_bundle_facts_pair()`` reads no binaries and parses no
    header AST on either side, so a flag that only applies to a live NEW_INPUT
    has no channel here either."""
    if (
        request.new_headers_only
        or request.new_includes_only
        or request.headers
        or request.includes
    ):
        raise BundleFactsCompareError(
            "--header new=.../--include new=... (or the uniform --header/"
            "--include) are not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: neither side has "
            "any header re-extraction available.",
            usage=True,
        )
    if request.new_devel_pkg is not None:
        raise BundleFactsCompareError(
            "A --header new=... development package is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: there is no live "
            "NEW-side package to extract a devel companion package's "
            "headers into.",
            usage=True,
        )
    if request.bundle_facts_library_manifest is not None:
        raise BundleFactsCompareError(
            "--bundle-facts-library-manifest is not supported when both "
            "OLD_INPUT and NEW_INPUT are stored BundleFacts documents: "
            "there is no live NEW-side library set to apply per-library "
            "header/include/compile overrides to.",
            usage=True,
        )
    if include_private_dso:
        raise BundleFactsCompareError(
            "release.include_private_dso is not supported when both OLD_INPUT "
            "and NEW_INPUT are stored BundleFacts documents: neither side "
            "discovers shared libraries from a live directory/package.",
            usage=True,
        )
    if dso_only:
        raise BundleFactsCompareError(
            "release.dso_only is not supported when both OLD_INPUT and NEW_INPUT "
            "are stored BundleFacts documents: a persisted document carries "
            "no per-library executable/library distinction to filter by.",
            usage=True,
        )
    if request.new_version not in (None, "", "new"):
        raise BundleFactsCompareError(
            "--version new=... is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: NEW_INPUT's own "
            "per-library snapshots already carry whatever version they "
            "were captured with.",
            usage=True,
        )
    if request.include_dependencies:
        raise BundleFactsCompareError(
            "--include-system-declarations is not supported when both "
            "OLD_INPUT and NEW_INPUT are stored BundleFacts documents: "
            "there is no live NEW-side resolution for it to scope.",
            usage=True,
        )


def compose_request_header_operands(
    request: BundleFactsCompareRequest,
) -> tuple[list[Path], list[Path]]:
    """NEW-side headers and includes, composed from the uniform and
    new=-scoped operands (``model.sided_inputs``). The OLD side has none of
    its own here: it is already a resolved, stored snapshot."""
    headers = compose_sided_paths(request.headers, request.new_headers_only)
    includes = compose_sided_paths(request.includes, request.new_includes_only)
    return headers, includes

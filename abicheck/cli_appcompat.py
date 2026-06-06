# Copyright 2026 Nikolay Petrov
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

"""CLI — ``appcompat`` application-compatibility command.

Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom of
:mod:`abicheck.cli` so the ``@main.command("appcompat")`` decorator runs.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .checker import Verdict
from .cli import (
    _expand_header_inputs,
    _load_suppression_and_policy,
    _resolve_per_side_options,
    _safe_write_output,
    _setup_verbosity,
    main,
)

if TYPE_CHECKING:
    from .appcompat import AppRequirements


def _validate_appcompat_args(
    weak_mode: bool,
    old_lib: Path | None, new_lib: Path | None,
    list_symbols: bool,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    headers: tuple[Path, ...] = (), includes: tuple[Path, ...] = (),
) -> None:
    """Validate appcompat CLI argument combinations."""
    if weak_mode and (old_lib is not None or new_lib is not None):
        raise click.UsageError(
            "--check-against cannot be used with positional OLD_LIB/NEW_LIB arguments."
        )
    if not weak_mode and (old_lib is None or new_lib is None):
        raise click.UsageError(
            "Provide OLD_LIB and NEW_LIB arguments, or use --check-against for weak mode."
        )
    if (weak_mode or list_symbols) and (headers or includes):
        # Plain -H/-I are silently ignored in these modes because the library
        # ABI is never extracted there; warn rather than fail so existing
        # invocations keep working.
        click.echo(
            "Warning: -H/--header and -I/--include are ignored in weak "
            "(--check-against) / --list-required-symbols mode; library ABI is "
            "not extracted there.",
            err=True,
        )
    if weak_mode or list_symbols:
        _reject_per_side_flags(
            weak_mode,
            old_headers_only, new_headers_only,
            old_includes_only, new_includes_only,
        )


def _reject_per_side_flags(
    weak_mode: bool,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
) -> None:
    """Reject per-side header/include flags in weak / list-symbols modes."""
    _flags = (
        ("--old-header", old_headers_only),
        ("--new-header", new_headers_only),
        ("--old-include", old_includes_only),
        ("--new-include", new_includes_only),
    )
    _rejected = [name for name, value in _flags if value]
    if _rejected:
        mode_label = "--check-against" if weak_mode else "--list-required-symbols"
        raise click.UsageError(
            f"{', '.join(_rejected)} cannot be used with {mode_label}. "
            f"Per-side header/include flags are only supported in full "
            f"comparison mode (OLD_LIB NEW_LIB)."
        )


def _handle_list_required_symbols(
    app_path: Path,
    check_against_lib: Path | None,
    old_lib: Path | None, new_lib: Path | None,
    weak_mode: bool, fmt: str,
    _get_lib_soname: Callable[[Path], str], parse_app_requirements: Callable[..., AppRequirements],
) -> None:
    """Handle the --list-required-symbols flow."""
    target_lib = check_against_lib if weak_mode else (old_lib or new_lib)
    if target_lib is None:
        raise click.UsageError(
            "--list-required-symbols requires a library path "
            "(via positional args or --check-against)."
        )
    lib_name = _get_lib_soname(target_lib)
    reqs = parse_app_requirements(app_path, lib_name)
    if fmt == "json":
        import json as _json
        click.echo(_json.dumps({
            "application": str(app_path),
            "library": lib_name,
            "needed_libs": reqs.needed_libs,
            "required_symbols": sorted(reqs.undefined_symbols),
            "required_versions": reqs.required_versions,
        }, indent=2))
    else:
        click.echo(f"Application: {app_path}")
        click.echo(f"Library filter: {lib_name}")
        click.echo(f"Needed libraries: {', '.join(reqs.needed_libs) or '(none)'}")
        click.echo(f"Required symbols ({len(reqs.undefined_symbols)}):")
        for sym in sorted(reqs.undefined_symbols):
            click.echo(f"  {sym}")
        if reqs.required_versions:
            click.echo(f"Required versions ({len(reqs.required_versions)}):")
            for ver, lib in sorted(reqs.required_versions.items()):
                click.echo(f"  {ver} (from {lib})")


@main.command("appcompat")
@click.argument("app_path", type=click.Path(exists=True, path_type=Path))
@click.argument("old_lib", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.argument("new_lib", type=click.Path(exists=True, path_type=Path), required=False, default=None)
# ── Weak mode ─────────────────────────────────────────────────────────────────
@click.option("--check-against", "check_against_lib",
              type=click.Path(exists=True, path_type=Path), default=None,
              help="Weak mode: check if a library provides everything the app needs "
                   "(no old library required).")
# ── Dump options ──────────────────────────────────────────────────────────────
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory for library ABI extraction "
                   "(applied to both sides).")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml (applied to both sides).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for castxml.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for old library only (overrides -H for old).")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for new library only (overrides -H for new).")
@click.option("--old-include", "old_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for old library only (overrides -I for old).")
@click.option("--new-include", "new_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for new library only (overrides -I for new).")
@click.option("--old-version", "old_version", default="old", show_default=True)
@click.option("--new-version", "new_version", default="new", show_default=True)
# ── Output options ────────────────────────────────────────────────────────────
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--show-irrelevant", is_flag=True, default=False,
              help="Include library changes that don't affect the application.")
@click.option("--list-required-symbols", "list_symbols", is_flag=True, default=False,
              help="List symbols the application requires and exit.")
# ── Suppression + policy ─────────────────────────────────────────────────────
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML).")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="strict_abi", show_default=True)
@click.option("--policy-file", "policy_file_path",
              type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--scope-public-headers/--no-scope-public-headers", "scope_public_headers",
              default=True, show_default=True,
              help="Restrict findings to the public-header ABI surface (ADR-024). "
                   "On by default; matches `compare`. Use --no-scope-public-headers "
                   "to report every finding.")
# ── Severity (mirrors `compare`) ──────────────────────────────────────────────
@click.option("--severity-preset", "severity_preset",
              type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
              default=None,
              help="Severity preset: 'default', 'strict', or 'info-only'. "
                   "When set (or any --severity-* option), exit codes follow the "
                   "severity-aware scheme instead of the verdict-based one.")
@click.option("--severity-abi-breaking", "severity_abi_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for clear ABI/API incompatibilities (overrides preset).")
@click.option("--severity-potential-breaking", "severity_potential_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for potential incompatibilities needing review (overrides preset).")
@click.option("--severity-quality-issues", "severity_quality_issues",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for problematic behaviors (overrides preset).")
@click.option("--severity-addition", "severity_addition",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for new public API additions (overrides preset).")
@click.option("-v", "--verbose", is_flag=True, default=False)
def appcompat_cmd(
    app_path: Path,
    old_lib: Path | None,
    new_lib: Path | None,
    check_against_lib: Path | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    lang: str,
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    fmt: str,
    output: Path | None,
    show_irrelevant: bool,
    list_symbols: bool,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    scope_public_headers: bool,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    verbose: bool,
) -> None:
    """Check if an application is compatible with a library update.

    Answers: "Will my application still work with the new library version?"
    by intersecting the app's required symbols with the library diff.

    \b
    Full check (with old and new library):
      abicheck appcompat myapp libfoo.so.1 libfoo.so.2
      abicheck appcompat myapp libfoo.so.1 libfoo.so.2 -H include/foo.h

    \b
    Weak mode (only new library — symbol availability check):
      abicheck appcompat myapp --check-against libfoo.so.2

    \b
    List required symbols:
      abicheck appcompat myapp --list-required-symbols --check-against libfoo.so.2

    \b
    Exit codes (verdict-based, the default):
      0  COMPATIBLE — application is safe with the new library
      2  API_BREAK — source-level break affecting app's symbols
      4  BREAKING — binary ABI break or missing symbols

    \b
    With any --severity-* option (full mode only — weak/--check-against keeps the
    verdict-based codes above), exit codes follow the severity-aware scheme over
    the app-relevant changes:
      0  no error-level findings
      1  error in quality/addition categories only
      2  error in potential_breaking
      4  error in abi_breaking
    Missing required symbols/versions always floor the exit at 4.
    """
    _setup_verbosity(verbose)

    from .appcompat import _get_lib_soname, check_appcompat, parse_app_requirements
    from .appcompat import check_against as _check_against
    from .appcompat_html import appcompat_to_html
    from .reporter import appcompat_to_json, appcompat_to_markdown

    weak_mode = check_against_lib is not None
    _validate_appcompat_args(
        weak_mode, old_lib, new_lib, list_symbols,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
        headers, includes,
    )

    if list_symbols:
        _handle_list_required_symbols(
            app_path, check_against_lib, old_lib, new_lib,
            weak_mode, fmt,
            _get_lib_soname, parse_app_requirements,
        )
        return

    if weak_mode:
        assert check_against_lib is not None
        result = _check_against(app_path, check_against_lib)
    else:
        assert old_lib is not None and new_lib is not None
        suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
        old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
            headers, includes,
            old_headers_only, new_headers_only,
            old_includes_only, new_includes_only,
        )
        resolved_old_h = _expand_header_inputs(old_h) if old_h else []
        resolved_new_h = _expand_header_inputs(new_h) if new_h else []
        result = check_appcompat(
            app_path, old_lib, new_lib,
            old_headers=resolved_old_h,
            new_headers=resolved_new_h,
            old_includes=old_inc,
            new_includes=new_inc,
            old_version=old_version,
            new_version=new_version,
            lang=lang,
            suppression=suppression,
            policy=policy,
            policy_file=pf,
            scope_to_public_surface=scope_public_headers,
        )

    if fmt == "json":
        text = appcompat_to_json(result)
    elif fmt == "html":
        text = appcompat_to_html(result)
    else:
        text = appcompat_to_markdown(result, show_irrelevant=show_irrelevant)

    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    severity_set = any(
        v is not None
        for v in (
            severity_preset, severity_abi_breaking, severity_potential_breaking,
            severity_quality_issues, severity_addition,
        )
    )
    # Severity-aware exit only applies in full-compare mode, where a full
    # library DiffResult (with effective kind-sets) is available. Weak mode
    # has no extracted library ABI, so it keeps the verdict-based exit.
    if severity_set and not weak_mode and result.full_diff is not None:
        from .severity import compute_exit_code, resolve_severity_config
        resolved_config = resolve_severity_config(
            severity_preset,
            abi_breaking=severity_abi_breaking,
            potential_breaking=severity_potential_breaking,
            quality_issues=severity_quality_issues,
            addition=severity_addition,
        )
        # appcompat is application-scoped: classify only the changes that
        # actually affect the application (result.breaking_for_app), mirroring
        # how the app verdict is computed — NOT the whole library diff. A
        # library break in a symbol the app never imports must not gate the app.
        # full_diff carries the effective kind sets (incl. policy-file overrides)
        # and breaking_for_app is a subset of its changes, so they share sets.
        exit_code = compute_exit_code(
            result.breaking_for_app, resolved_config,
            kind_sets=result.full_diff._effective_kind_sets(),
        )
        # Missing required symbols/versions are a hard runtime break (the app
        # won't load) that is NOT represented in the diff's changes, so
        # compute_exit_code() can't see it. Never let a severity preset (e.g.
        # info-only) downgrade that below BREAKING.
        if result.missing_symbols or result.missing_versions:
            exit_code = max(exit_code, 4)
        if exit_code != 0:
            sys.exit(exit_code)
        return

    if result.verdict == Verdict.BREAKING:
        sys.exit(4)
    elif result.verdict == Verdict.API_BREAK:
        sys.exit(2)

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

"""CLI — ``init`` command and ``config`` group (project-config diagnostics).

Closes a usability gap: ``.abicheck.yml`` had no way to scaffold, validate,
or introspect from the command line — an unknown key only ever warned
(``BuildConfig._warn_unknown_keys``) and silently kept running, and the
CLI/config/default precedence in ``resolve_compare_config`` was only
observable by reading source or trial-and-error.

* ``abicheck init`` — scaffold a starter ``.abicheck.yml``.
* ``abicheck config validate [PATH]`` — structured unknown-key / parse-error
  report (never just a warning that can be missed).
* ``abicheck config show-effective [PATH]`` — the resolved severity/scope/
  suppression/exit-code settings for a hypothetical ``compare`` invocation,
  one row per setting with where its value came from (cli/config/default).

Split out of :mod:`abicheck.cli` to keep that module under the AI-readiness
file-size limit. Imported for side-effect at the bottom of :mod:`abicheck.cli`
so the ``@main.command("init")``/``@main.group("config")`` decorators run.
"""

from __future__ import annotations

from pathlib import Path

import click

from .cli import _EXIT_USAGE_ERROR, main
from .cli_helpers_compare import discover_project_config
from .cli_options import scope_options, severity_options

# Block subkeys BuildConfig.from_dict() parses with `_opt_bool` (see
# buildsource/inline.py) — a non-bool value there (e.g. YAML string "false"
# instead of the boolean `false`) is silently dropped rather than raising, so
# `config validate` checks these explicitly (Codex review).
_BOOL_SUBKEYS: dict[str, frozenset[str]] = {
    "scope": frozenset({"public", "collapse_versioned_symbols", "show_redundant"}),
    "suppression": frozenset({"strict", "require_justification"}),
    "compile": frozenset({"nostdinc"}),
    "debug": frozenset({"dwarf_only", "debuginfod"}),
}

_INIT_TEMPLATE = """\
# abicheck project configuration (ADR-037 D4). Every key is optional; a
# missing key falls back to the built-in default. See
# https://abicheck.github.io/abicheck/reference/config/ for the full schema.
#
# version: 1

# severity:
#   preset: default        # default | strict | info-only
#   abi_breaking: error
#   potential_breaking: warning
#   quality_issues: warning
#   addition: info

# scope:
#   public: true            # restrict findings to the public-header ABI surface
#   collapse_versioned_symbols: false
#   show_redundant: false

# suppression:
#   strict: false            # fail if any suppression rule has expired
#   require_justification: false

# debug:
#   format: auto             # auto | dwarf | btf | ctf
#   dwarf_only: false
#   debuginfod: false
#   debuginfod_url: null

# exit_code_scheme: auto     # auto | legacy | severity
"""


@main.command("init")
@click.option(
    "--path",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".abicheck.yml"),
    show_default=True,
    help="Where to write the starter config.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing file instead of failing.",
)
def init_command(out_path: Path, force: bool) -> None:
    """Scaffold a starter .abicheck.yml with every key documented and commented out."""
    if out_path.exists() and not force:
        raise click.ClickException(
            f"{out_path} already exists; pass --force to overwrite it."
        )
    out_path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    click.echo(f"Wrote {out_path}")


def _resolve_config_path(path: Path | None) -> Path | None:
    if path is not None:
        return path
    return discover_project_config()


@main.group("config")
def config_group() -> None:
    """Inspect and validate the project .abicheck.yml."""


@config_group.command("validate")
@click.argument(
    "path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def config_validate(path: Path | None) -> None:
    """Validate .abicheck.yml's keys AND values against the known schema.

    Unlike the loader's forward-compat ``warnings.warn`` (which a suppressed
    or redirected warnings stream can hide entirely), this reports every
    unknown top-level/block key as a structured, always-visible finding. It
    also checks that a known block key is actually a mapping (not e.g.
    ``severity: strict``, which ``from_dict`` otherwise silently treats as
    ``{}``) and that a known boolean subkey holds an actual boolean (not e.g.
    ``scope: {public: "false"}``, which ``from_dict`` otherwise silently
    treats as unset) — both are quiet no-ops in the real loader, not errors,
    so `validate` would report OK on a file that behaves nothing like the
    user intended. It also runs the same ``BuildConfig.from_dict`` parser
    every real command uses, so a recognized key with an invalid enum value
    (``severity.preset: bogus``, ``exit_code_scheme: typo``, ...) is caught
    here too. Exits 0 when clean, 1 when unknown keys or
    invalid values are found, 64 when no config file could be found or it
    isn't valid YAML.
    """
    from .buildsource.inline import BuildConfig

    resolved = _resolve_config_path(path)
    if resolved is None:
        exc = click.ClickException(
            "no .abicheck.yml found (searched upward from the current "
            "directory); pass a path explicitly."
        )
        exc.exit_code = _EXIT_USAGE_ERROR
        raise exc

    import yaml

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc_read:
        exc = click.ClickException(f"cannot read {resolved}: {exc_read}")
        exc.exit_code = _EXIT_USAGE_ERROR
        raise exc from None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        exc = click.ClickException(
            f"{resolved}: top level must be a mapping, got {type(raw).__name__}."
        )
        exc.exit_code = _EXIT_USAGE_ERROR
        raise exc

    findings: list[str] = []
    for key, value in raw.items():
        if key not in BuildConfig._KNOWN_TOP_KEYS:
            findings.append(f"unknown top-level key: {key!r}")
            continue
        known_block = BuildConfig._KNOWN_BLOCK_KEYS.get(key)
        if known_block is None or value is None:
            continue
        if not isinstance(value, dict):
            # `severity: strict` (a bare scalar where a mapping is required)
            # is silently coerced to `{}` by BuildConfig.from_dict's
            # isinstance guards — every key under it is then quietly dropped
            # rather than raising. Report it here instead of letting
            # `validate` print OK on a file `compare` would parse differently
            # (Codex review).
            findings.append(
                f"{key} must be a mapping, got {type(value).__name__}: {value!r}"
            )
            continue
        for sub, sub_value in value.items():
            if sub not in known_block:
                findings.append(f"unknown key: {key}.{sub!r}")
                continue
            if sub in _BOOL_SUBKEYS.get(key, ()) and not isinstance(sub_value, bool):
                # Same silent-coercion problem one level down: `_opt_bool`
                # returns None for a non-bool value (e.g. the YAML string
                # "false" instead of the boolean `false`), so the setting is
                # quietly ignored rather than rejected (Codex review).
                findings.append(
                    f"{key}.{sub} must be a boolean, got "
                    f"{type(sub_value).__name__}: {sub_value!r}"
                )

    import warnings

    try:
        with warnings.catch_warnings():
            # Unknown-key warnings are already reported above as structured
            # findings; suppress from_dict's own warnings.warn for the same
            # keys so they aren't reported twice in different shapes.
            warnings.simplefilter("ignore", UserWarning)
            BuildConfig.from_dict(raw)
    except ValueError as exc_parse:
        findings.append(f"invalid value: {exc_parse}")

    click.echo(f"{resolved}:")
    if not findings:
        click.echo("  OK — every key is recognized.")
        return
    for finding in findings:
        click.echo(f"  {finding}")
    click.echo(f"\n{len(findings)} finding(s).")
    raise SystemExit(1)


@config_group.command("show-effective")
@click.argument(
    "path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@severity_options
@scope_options
@click.option(
    "--strict-suppressions",
    is_flag=True,
    default=False,
    help="Same flag as `compare` (config: suppression.strict).",
)
@click.option(
    "--require-justification",
    is_flag=True,
    default=False,
    help="Same flag as `compare` (config: suppression.require_justification).",
)
@click.option(
    "--exit-code-scheme",
    "exit_code_scheme",
    type=click.Choice(["auto", "legacy", "severity"], case_sensitive=True),
    default=None,
    help="Same flag as `compare` (config: exit_code_scheme).",
)
def config_show_effective(
    path: Path | None,
    severity_preset: str | None,
    severity_addition: str | None,
    severity_quality_issues: str | None,
    severity_potential_breaking: str | None,
    severity_abi_breaking: str | None,
    scope_public_headers: bool,
    strict_suppressions: bool,
    require_justification: bool,
    exit_code_scheme: str | None,
) -> None:
    """Show the resolved settings a `compare` run would use, and where each came from.

    Precedence is CLI flag > project .abicheck.yml > built-in default (ADR-037
    D4) — this renders exactly that resolution for the flags you pass (or
    don't), so "what would --severity-preset strict actually change" is a
    direct answer instead of reading resolve_compare_config's source.
    """
    from .buildsource.inline import BuildConfig, load_build_config
    from .cli_compare_helpers import _cli_flag
    from .cli_helpers_compare import resolve_compare_config

    resolved_path = _resolve_config_path(path)
    if resolved_path:
        try:
            cfg = load_build_config(resolved_path)
        except ValueError as exc_parse:
            exc = click.ClickException(f"{resolved_path}: {exc_parse}")
            exc.exit_code = _EXIT_USAGE_ERROR
            raise exc from None
    else:
        cfg = BuildConfig()

    cli_scope_public = _cli_flag("scope_public_headers", scope_public_headers)
    cli_strict_suppressions = _cli_flag("strict_suppressions", strict_suppressions)
    cli_require_justification = _cli_flag(
        "require_justification", require_justification
    )

    resolved = resolve_compare_config(
        cfg,
        cli_severity_preset=severity_preset,
        cli_severity_abi_breaking=severity_abi_breaking,
        cli_severity_potential_breaking=severity_potential_breaking,
        cli_severity_quality_issues=severity_quality_issues,
        cli_severity_addition=severity_addition,
        cli_scope_public=cli_scope_public,
        cli_collapse_versioned_symbols=None,
        cli_strict_suppressions=cli_strict_suppressions,
        cli_require_justification=cli_require_justification,
        cli_exit_code_scheme=exit_code_scheme,
    )

    click.echo(f"config file: {resolved_path or '(none found)'}")
    click.echo()

    def _source(cli_value: object, config_value: object) -> str:
        if cli_value is not None:
            return "cli"
        if config_value is not None:
            return "config"
        return "default"

    rows: list[tuple[str, str, str]] = [
        (
            "severity.preset",
            str(severity_preset or cfg.severity_preset or "default"),
            _source(severity_preset, cfg.severity_preset),
        ),
        (
            "scope.public",
            str(resolved.scope_public),
            _source(cli_scope_public, cfg.scope_public),
        ),
        (
            "suppression.strict",
            str(resolved.strict_suppressions),
            _source(cli_strict_suppressions, cfg.suppression_strict),
        ),
        (
            "suppression.require_justification",
            str(resolved.require_justification),
            _source(cli_require_justification, cfg.suppression_require_justification),
        ),
        (
            "exit_code_scheme",
            resolved.exit_code_scheme,
            _source(
                exit_code_scheme,
                None if cfg.exit_code_scheme == "auto" else cfg.exit_code_scheme,
            ),
        ),
    ]
    click.echo(resolved.severity.describe(prefix="  ", title="severity (effective):"))
    click.echo()
    width = max(len(r[0]) for r in rows)
    for name, value, source in rows:
        click.echo(f"  {name.ljust(width)}  {value:<10}  ({source})")

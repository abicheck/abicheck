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

"""Resolving the project config (``.abicheck.yml``) for a ``compare`` run.

One owner, because both ``compare`` forms need the same answer and only one
of them could reach it: the ``--no-baseline`` audit dispatches before
``cli_compare_helpers`` runs, so importing the resolver from there would
close ``cli_compare_helpers -> commands.compare -> commands.compare_no_baseline
-> cli_compare_helpers``. Lifted here instead, where both import it and
neither imports the other.

That the audit could not reach it was not a tidiness problem: an
auto-discovered ``.abicheck.yml`` was invisible to it, so a malformed one
exited 0 where ordinary ``compare`` exits 64, and a valid ``scope.public:
false`` was silently dropped -- auditing a different surface than the same
directory's ``compare`` (Codex review, P1).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ...workflows.compare_configuration import CompareSettingsError

if TYPE_CHECKING:
    from ...workflows.compare_configuration import ResolvedCompareConfig

__all__ = [
    "compare_settings_usage_errors",
    "config_exclude_headers",
    "enter_performance_profile",
    "resolve_project_compare_config",
]


@contextmanager
def compare_settings_usage_errors() -> Iterator[None]:
    """Report a :class:`CompareSettingsError` as a Click usage error (exit 64).

    The compare-settings resolution lives in
    :mod:`abicheck.workflows.compare_configuration`, which may not import
    ``click``; this is the CLI's one translation of its error, with the
    message unchanged.
    """
    try:
        yield
    except CompareSettingsError as exc:
        raise click.UsageError(str(exc)) from exc


def resolve_project_compare_config(
    *,
    config: Path | None,
    severity_preset: str | None,
    config_overrides: Mapping[str, object] | None = None,
) -> tuple[Path | None, object, ResolvedCompareConfig, str | None]:
    """Load the project config and merge CLI flags over it (CLI > config > default).

    ADR-037 D4: resolved *before* dispatch so both the single-file and the
    directory/package fan-out paths share one resolution. Auto-discovered from the
    current directory upward, overridable with ``--config``.

    The fourth element is the digest of the bytes the config was parsed from
    (``None`` when there is no config), captured by the same read so an
    ADR-049 receipt can prove *which revision* of the file supplied a value
    rather than only naming its path (Codex review, fresh evidence).

    ADR-068 D5 / Phase 7a: ``compare`` no longer has ``--debug-format``/
    ``--debuginfod``/``--debuginfod-url``/``--dwarf-only`` CLI flags (they
    were hidden, already fully config-backed duplicates of ``debug.format``/
    ``debug.debuginfod``/``debug.debuginfod_url``/``debug.dwarf_only``), so
    ``resolve_compare_config`` below is called with no ``cli_debug_format``/
    ``cli_dwarf_only``/``cli_debuginfod``/``cli_debuginfod_url`` override --
    its defaults (``None``/``None``/``None``/``None``) mean the config value
    (or the built-in default) always wins.

    One-comparison-product Phase 9b did the same to
    ``--scope-public-headers``/``--no-scope-public-headers``: no
    ``cli_scope_public`` override is passed, so ``scope.public`` (or its
    built-in ``True``) decides header-origin scoping for a run with no
    ``--contract``. ``--contract all`` replaces the opt-out flag.
    """
    from ...config_paths import resolve_project_config
    from ...workflows.compare_configuration import load_and_resolve_compare_config

    cfg_path = resolve_project_config(config, search_from=Path.cwd()).path
    with compare_settings_usage_errors():
        project_cfg, resolved_cfg, cfg_sha = load_and_resolve_compare_config(
            cfg_path,
            severity_preset=severity_preset,
            config_overrides=config_overrides,
        )
    return cfg_path, project_cfg, resolved_cfg, cfg_sha


def config_exclude_headers(
    exclude_headers: tuple[str, ...], project_cfg: object
) -> tuple[str, ...]:
    """The run's ``exclude_headers``: ``--exclude-header``, else ``scope.exclude_headers``.

    ``scope.exclude_headers`` is the config equivalent of ``--exclude-header``
    (see ``BuildConfig.exclude_headers`` for why it is its own key and not
    ``sources.exclude``). Weaker than the flag and never unioned with it: a run
    stating ``--exclude-header`` at all uses exactly the rules it stated, because
    the rule *set* is the run's scope identity and silently widening it would
    make the run narrower than the command line says. The result is the one
    canonical rule set every path -- scalar, directory/package fan-out, stranded
    capture, dry-run receipt, configuration digest -- reads.
    """
    if exclude_headers:
        return exclude_headers
    return tuple(getattr(project_cfg, "exclude_headers", ()) or ())


def enter_performance_profile(ctx: click.Context, explicit: str | None) -> None:
    """Hold the run's performance profile for the rest of *ctx*
    (``ctx.with_resource``), so every ``compare`` route -- scalar, release
    fan-out, stored bundles, ``--no-baseline`` -- runs under it.

    ``--performance-profile`` wins; otherwise the project config's
    ``performance.profile`` (found by the same rule as every other setting,
    :func:`resolve_project_compare_config`), otherwise the default. A config
    that fails to load is left for the ordinary resolution to report -- it
    is loaded again there, and exits 64 with its own message.
    """
    from ...config_paths import resolve_project_config
    from ...model.performance import (
        parse_performance_profile,
        performance_profile_scope,
    )
    from ...workflows.extraction import load_build_config_with_digest

    value = explicit
    if value is None:
        cfg_path = resolve_project_config(
            ctx.params.get("config"), search_from=Path.cwd()
        ).path
        overrides = ctx.params.get("config_overrides")
        if cfg_path is not None or overrides:
            try:
                value = load_build_config_with_digest(
                    cfg_path, config_overrides=overrides
                )[0].performance_profile
            except (OSError, ValueError):
                value = None
    profile = None if value is None else parse_performance_profile(value)
    ctx.with_resource(performance_profile_scope(profile))

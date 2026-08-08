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

"""What ``compare`` rejects or normalizes before any snapshot is read.

Flag bookkeeping (which spelling did the user actually type, which typed
parameters are inert for the operands given) and the usage errors that
follow from it: an evidence/compile-context flag passed alongside a
pre-extracted set input, a ``--debug-format`` that only means something for
ELF, a ``--demangle``/``--no-demangle`` pair, the debug-root list.

Split out of :mod:`abicheck.cli_compare_helpers`, which sat one line under
the 2000-line hard cap. The seam is not "small helpers" but "the part that
needs no engine at all" -- everything here answers a question about the
argv, so it imports ``click`` and nothing from ``abicheck``. That matters
structurally, not just aesthetically: ``cli_compare_helpers`` is inside the
baselined CLI-registration import cycle (``IMPORT_CYCLE_ALLOWLIST``), and a
module carved out of it that pulled in ``cli``/``cli_resolve``/
``cli_dump_helpers`` would join that cycle -- which the
``import-cycle-growth`` gate rejects, and which CLAUDE.md says needs an ADR
rather than a wider allowlist. The option helpers that *do* reach those
modules deliberately stayed behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import click


def _cli_flag(name: str, value: bool) -> bool | None:
    """Return *value* only when *name* actually came from the command line.

    So a flag default (e.g. ``--scope-public-headers``'s True) doesn't mask config.
    """
    src = click.get_current_context().get_parameter_source(name)
    return value if src == click.core.ParameterSource.COMMANDLINE else None

def _param_from_cli(name: str) -> bool:
    """True when parameter *name*'s value came from the command line (not default)."""
    src = click.get_current_context().get_parameter_source(name)
    return bool(src == click.core.ParameterSource.COMMANDLINE)

def _merge_cli_debug_format(
    debug_format_opt: str | None,
    legacy_debug_format: str | None,
    *,
    legacy_from_cli: bool,
) -> str | None:
    """Effective *command-line* debug format across all CLI spellings (ADR-040 L2).

    ``--debug-format`` (``debug_format_opt``) is the primary selector; the hidden
    compatibility flags ``--btf``/``--ctf``/``--dwarf`` write the ``debug_format``
    dest. Either, when typed, must beat a ``.abicheck.yml`` ``debug.format`` — so
    fold a *command-line-sourced* legacy flag in here (the flag's own default is
    ``None``, so ``legacy_from_cli`` distinguishes "typed" from "unset"). Returns
    ``None`` when no format was given on the command line, letting config win.
    """
    if debug_format_opt is not None:
        return debug_format_opt
    if legacy_from_cli:
        return legacy_debug_format
    return None

def _reject_set_input_flags(
    exit_code_scheme: str | None,
    reconcile_build_context: bool,
    env_matrix_path: Path | None,
    secondary_fmt: str | None = None,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols: tuple[str, ...] = (),
    diagnostic_comparison: bool = False,
    audit_suppressions: bool = False,
    pack_paths: tuple[Path, ...] = (),
    include_labels: dict[Path, str] | None = None,
) -> None:
    """Reject single-pair-only flags on a directory/package (release) compare.

    The per-library fan-out has no public CLI support for these, so reject them
    loudly rather than silently ignore them (ADR-037 D12).
    """
    if exit_code_scheme is not None:
        raise click.UsageError(
            "--exit-code-scheme is not supported for directory/package "
            "(release) comparisons: the per-library fan-out uses the legacy "
            "verdict scheme, or severity-aware when severity is configured in "
            ".abicheck.yml. Compare libraries individually for explicit "
            "scheme control."
        )
    if reconcile_build_context:
        raise click.UsageError(
            "--reconcile-build-context is not supported for directory/package "
            "(release) comparisons; it applies to single-file / snapshot "
            "inputs. Compare the libraries individually to use it."
        )
    if env_matrix_path is not None:
        raise click.UsageError(
            "--env-matrix is not supported for directory/package (release) "
            "comparisons yet; it applies to single-file / snapshot inputs. "
            "Compare the libraries individually to use it."
        )
    if secondary_fmt is not None:
        raise click.UsageError(
            "--secondary-format is not supported for directory/package "
            "(release) comparisons yet; it applies to single-file / snapshot "
            "inputs. Compare the libraries individually to use it."
        )
    if used_by_apps:
        raise click.UsageError(
            "--used-by is not supported for directory/package (release) "
            "comparisons: the per-library fan-out has no per-app scoping. "
            "Compare the specific library individually with --used-by."
        )
    if required_symbols:
        raise click.UsageError(
            "--required-symbol/--required-symbols is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no plugin-host-contract scoping. Compare the "
            "specific library individually with --required-symbol."
        )
    if diagnostic_comparison:
        raise click.UsageError(
            "--diagnostic-comparison is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "wire the ADR-050 D2 comparability gate's diagnostic escape "
            "hatch (a mismatch there still raises unhandled). Compare the "
            "specific library individually to use it."
        )
    # --contract-evaluation/--contract are deliberately NOT rejected here
    # (CLI-audit P1, release/package contract parity): the per-library
    # fan-out now threads both straight into each pair's own
    # service.run_compare(contract_evaluation=..., contract_mode=...) call
    # (compare_release_cmd), the exact same Tier-2 chokepoint a single-pair
    # `compare` uses -- so a library compared through the fan-out gets the
    # identical contract decision it would from comparing it individually.
    # --pack stays rejected below: applying a pack's policy/contract/gate
    # overrides per library still needs its own resolve-once-apply-per-pair
    # design (ADR-049 D8 pack-vs-pack conflict detection resolves against a
    # single library pair today), which this change does not attempt.
    if audit_suppressions:
        raise click.UsageError(
            "--audit-suppressions is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out has no "
            "single suppression-audit result to attach. Compare the "
            "specific library individually to use it."
        )
    if pack_paths:
        raise click.UsageError(
            "--pack is not supported for directory/package (release) "
            "comparisons yet: the fan-out dispatches before the effective "
            "configuration is resolved, so a pack would be accepted and then "
            "score nothing. Compare the specific library individually to "
            "use it."
        )
    if include_labels:
        raise click.UsageError(
            "A labeled --include (old:LABEL=PATH/new:LABEL=PATH/"
            "both:LABEL=PATH) is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "thread ADR-050 D1's project_include_labels into its per-library "
            "dumps, so the label would be silently dropped. Compare the "
            "specific library individually to use it."
        )

class _NormalizedCompareOptions(NamedTuple):
    collect_mode: str
    headers: tuple[Path, ...]
    old_headers_only: tuple[Path, ...]
    new_headers_only: tuple[Path, ...]
    effective_debug_format: str | None
    demangle: bool
    report_mode: str
    show_impact: bool

def _resolve_demangle(fmt: str, demangle: bool | None) -> bool:
    """Resolve the tri-state ``--demangle`` flag against a specific format.

    Default ON for the text formats whose renderer post-processes symbols
    through ``demangle_text`` (markdown/review), OFF for machine formats
    (json/sarif/junit) and HTML — the HTML renderer emits symbols
    structurally and demangling its string would inject unescaped
    ``<``/``>``/``&`` from C++ names and corrupt the markup. An explicit
    flag always wins over the per-format default.

    Shared by the primary render (:func:`_normalize_compare_options`) and
    the ``--secondary-format`` render in :func:`run_compare`, each resolved
    against its own format — a machine primary format paired with a text
    secondary format (or vice versa) must not inherit the other's default.
    """
    return fmt in {"markdown", "review"} if demangle is None else demangle

def _reject_debug_format_for_non_elf(
    effective_debug_format: str | None,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """Reject --debug-format / legacy --btf/--ctf/--dwarf for PE/Mach-O inputs.

    They force an ELF debug format and are silently ignored by the PE/Mach-O dump
    paths, so reject them up front (mirrors dump_cmd). JSON-snapshot / dump inputs
    have ``*_fmt == None`` and are unaffected.
    """
    if effective_debug_format is None:
        return
    for side, bfmt in (("old", old_fmt), ("new", new_fmt)):
        if bfmt in ("pe", "macho"):
            raise click.BadParameter(
                f"--debug-format {effective_debug_format} is only supported "
                f"for ELF binaries, but the {side} input is {bfmt.upper()}."
            )

def _resolve_debug_roots(
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
) -> tuple[list[Path], list[Path]]:
    """Per-side debug roots: --debug-root old=/new= override the both-sides value."""
    resolved_old = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new = list(debug_roots_new) if debug_roots_new else list(debug_roots)
    return resolved_old, resolved_new

def _warn_force_public_ignored(
    force_public: object, scope_public_headers: bool,
) -> None:
    """Warn that --public-symbol overlays need --scope-public-headers to apply."""
    if force_public and not scope_public_headers:
        click.echo(
            "Warning: --public-symbol/--public-symbols-list only take effect with "
            "--scope-public-headers; ignoring the widening overlay.",
            err=True,
        )

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

"""``compare --dry-run``'s "Cost preview" section -- the *render* half
(one-comparison-product.md #35, Phase 2f).

Takes the caller's already-computed
:func:`~abicheck.workflows.compare_cost_preview.estimate_compare_dry_run_cost`
result (a list of :class:`~abicheck.dry_run_estimate.CostEstimate` rows, or an
error string) and turns it into ``DryRunResult`` section lines -- the same
split :mod:`abicheck.frontends.cli.scan_dry_run` already applies between a
workflow's computed estimate and its CLI rendering, and for the identical
reason: this module lives under ``frontends/cli`` (may import ``model``/
``workflows``/``report`` only, per ``architecture/modules.yaml``), so the
*computation* -- which needs ``dry_run_estimate`` -- stays in
:mod:`abicheck.workflows.compare_cost_preview` (the ``workflows`` layer,
where ``dry_run_estimate.py`` itself is classified).

PR #1154 merge-conflict follow-up (file-size hard cap): also hosts
:func:`build_compare_dry_run_result`, the whole-report builder that used to
be ``cli_compare_helpers._render_compare_dry_run`` -- moved here (rather
than to a new flat ``cli_*`` sibling, which ``architecture/modules.yaml``'s
``frozen-root-family`` rule forbids for new files) once merging this PR's
own accounting-flags/``--view`` work with a parallel CONFIG-demotion phase
pushed ``cli_compare_helpers.py`` past the 2000-line hard cap with no
``architecture/debt.yaml`` baseline room to absorb the combined growth.
Its depth/collect-mode resolution (``cli_compare_helpers.
_resolve_compare_collect_mode``) stays put and is now a caller-supplied
``collect_mode``/``effective_depth_label`` pair rather than something this
function resolves itself -- this module may not import a ``frontends``
*legacy* sibling like ``cli_compare_helpers`` (only ``model``/``workflows``/
``report``), so passing the already-resolved values in is what keeps this a
pure render step, the same reasoning ``add_compare_cost_preview_section``
above already follows for its own caller-supplied ``estimates``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ...dry_run import DryRunResult
    from ...dry_run_estimate import CostEstimate
    from ...model.consumer_spec import ConsumerAppInput


def add_compare_cost_preview_section(
    result: DryRunResult,
    estimates: list[CostEstimate] | None,
    estimate_error: str | None,
) -> None:
    """Append the "Cost preview" section (or a warning) to *result*."""
    if estimate_error is not None:
        result.warn(
            f"could not project per-layer evidence-collection cost: {estimate_error}"
        )
        return
    assert estimates is not None
    total = sum(e.est_seconds for e in estimates)
    result.add(
        "Cost preview",
        *(
            f"{e.layer}: TU count/cost unknown -- {e.note}"
            if "[UNKNOWN" in e.note
            else f"{e.layer}: {e.tus} TU(s), ~{e.est_seconds:.2f}s -- {e.note}"
            for e in estimates
        ),
        f"projected total (old + new sides): {total:.2f}s",
        "note: at least one layer's TU count/cost is unknown (see above) "
        "-- it contributes 0.0s to the projected total, understating it"
        if any("[UNKNOWN" in e.note for e in estimates)
        else None,
    )


def effective_compare_defines(
    resolve_compile_context: Any, **kwargs: Any
) -> tuple[str, ...]:
    """The EFFECTIVE macro set a ``compare --dry-run`` receipt must state
    (ADR-074): ``.abicheck.yml``'s ``compile.defines`` folded with the CLI's
    own ``-D``, by macro name -- not the raw ``--define`` values.

    Takes the resolver itself rather than importing it, so this
    ``frontends``-classified module does not reach back into
    ``cli_options``. The caller passes the *same* keyword arguments the real
    run's own ``resolve_compile_context`` call uses, and a dry run returns
    before that call, so this runs instead of it and the receipt cannot
    disagree with the run it describes.
    """
    from ...model.macro_definition import define_spellings_from_tokens

    context, _includes = resolve_compile_context(**kwargs)
    return define_spellings_from_tokens(context.gcc_option_tokens)


def build_compare_dry_run_result(
    *,
    old_input: Path,
    new_input: Path,
    old_kind: str,
    new_kind: str,
    depth: str | None,
    collect_mode: str,
    effective_depth_label: str,
    source_method: str | None = None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
    cfg_path: Path | None,
    fmt: str,
    exit_code_scheme: str | None,
    header_backend: str,
    used_by_apps: tuple[ConsumerAppInput, ...] = (),
    required_symbols: tuple[str, ...] = (),
    select: tuple[str, ...] = (),
    select_required: tuple[str, ...] = (),
    exclude_headers: tuple[str, ...] = (),
    defines: tuple[str, ...] = (),
) -> Any:
    """Build the ``compare --dry-run`` report (ADR-043 D4): resolve, never diff.

    *collect_mode*/*effective_depth_label* are the caller's own already-
    resolved ``cli_compare_helpers._resolve_compare_collect_mode()`` result
    (this module's own docstring explains why they're a parameter here
    rather than resolved locally).
    """
    from ...dry_run import DryRunResult, tool_status

    result = DryRunResult(command="compare")
    result.add(
        "Inputs",
        f"old: {old_input} ({old_kind})",
        f"new: {new_input} ({new_kind})",
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(not given)'}",
        f"effective depth: {effective_depth_label}",
        f"effective collect mode: {collect_mode}",
        "source scope: target on each side (compare has no PR change seed)"
        if collect_mode in ("source-target", "source-changed", "graph-full")
        else None,
    )
    from ...workflows.compare_cost_preview import estimate_compare_dry_run_cost

    add_compare_cost_preview_section(
        result,
        *estimate_compare_dry_run_cost(
            old_input=old_input,
            new_input=new_input,
            depth=depth,
            source_method=source_method,
            headers=headers,
            includes=includes,
            old_headers_only=old_headers_only,
            new_headers_only=new_headers_only,
            old_sources=old_sources,
            new_sources=new_sources,
            old_build_info=old_build_info,
            new_build_info=new_build_info,
        ),
    )
    # Each side's *effective* header list, composed by the one shared rule
    # the run itself applies (`model.sided_inputs`): a side-specific
    # `--header old=` adds to the both-sides value rather than replacing it,
    # and the receipt has to show what will actually be parsed. Rendered as
    # one line while the two sides agree, so the common case reads exactly
    # as it did before this became side-aware.
    from ...model.sided_inputs import compose_sided_paths

    old_effective = compose_sided_paths(headers, old_headers_only)
    new_effective = compose_sided_paths(headers, new_headers_only)

    def _fmt(paths: list[Path]) -> str:
        """One side's header list as the receipt renders it."""
        return ", ".join(str(h) for h in paths)

    if old_effective == new_effective:
        header_lines = [f"headers: {_fmt(old_effective)}"] if old_effective else []
    else:
        header_lines = [
            f"headers (old): {_fmt(old_effective) or '(none)'}",
            f"headers (new): {_fmt(new_effective) or '(none)'}",
        ]
    # The effective `--exclude-header` rules, stated wherever the receipt
    # states the header list they narrow -- and for a directory/package
    # operand exactly as much as for a file pair, since silently dropping
    # them at the directory branch was the defect this line makes visible.
    # Rendered canonically (sorted, de-duplicated, with the matching rule)
    # so it reads as the same identity the configuration digest computes.
    from ...model.header_exclusion_record import canonical_exclusion_identity

    exclusion_identity = canonical_exclusion_identity(exclude_headers)
    result.add(
        "Headers and compile context",
        f"ast-frontend: {header_backend}",
        # ADR-074: already the EFFECTIVE set (config compile.defines folded
        # with the CLI's own -D) -- the caller resolves it through the same
        # `resolve_compile_context` the real run uses. Stated once for the
        # pair, never per side: -D/--define has no old=/new= form by design.
        f"defines: {', '.join(defines)}" if defines else None,
        *header_lines,
        f"exclude-header: {exclusion_identity}" if exclusion_identity else None,
    )
    result.add(
        "Build/source inputs",
        f"old sources/build-info: {old_sources or old_build_info or '(embedded)'}",
        f"new sources/build-info: {new_sources or new_build_info or '(embedded)'}",
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Configuration and value origins",
        f".abicheck.yml: {cfg_path if cfg_path else '(none found)'}",
    )
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        f"exit-code scheme: {exit_code_scheme or 'legacy (0/2/4)'}; contract coverage adds an orthogonal 1 under --contract",
    )
    if {old_kind, new_kind} & {"directory", "package"}:
        result.add("Consumer/contract scoping", "dispatch: per-library release fan-out")
        from .release_dry_run import add_comparison_plan_section

        add_comparison_plan_section(
            result, old_input, new_input, old_kind, new_kind, select, select_required
        )
    if used_by_apps:
        from ...appcompat import parse_app_requirements
        from ...model.consumer_spec import as_consumer_spec

        for app in used_by_apps:
            app_label = as_consumer_spec(app).path
            try:
                reqs = parse_app_requirements(app, old_input.stem)
                result.add(
                    "Consumer/contract scoping",
                    f"--used-by {app_label}: {len(reqs.undefined_symbols)} required "
                    f"symbol(s), {len(reqs.required_versions)} required version(s)",
                )
            except Exception as exc:  # noqa: BLE001 - best-effort dry-run probe
                result.warn(
                    f"--used-by {app_label}: could not parse requirements: {exc}"
                )
    if required_symbols:
        result.add(
            "Consumer/contract scoping",
            f"--required-symbol(s): {len(required_symbols)} entrypoint(s) required",
        )
    return result

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

"""CLI adapter for a stored-bundle-facts OLD_INPUT ``compare``.

Dispatch order is ``compare.compare_cmd`` -> :func:`dispatch` (this module)
-> ``workflows.bundle_facts_stored_compare.execute_bundle_facts_compare``.
This module only parses the Click kwargs into a
:class:`~abicheck.workflows.bundle_facts_stored_request.BundleFactsCompareRequest`,
renders the outcome, writes the artifacts, and maps the verdict to an exit
code. Which options a stored comparison can honor, and how it compares,
live in ``workflows/``: a rule that changes must not require a Click
context to test.

Lives under ``frontends/cli/commands/`` (ADR-061), not as a flat
``cli_compare_bundle_facts.py`` root sibling: the ``cli_`` root prefix family
is frozen (``architecture/modules.yaml``'s ``frozen-root-family`` gate).

Library-removal accounting (``--fail-on-removed-library``) is rejected, not
silently ignored: answering it would re-scan OLD_FACTS a second time.
A zero-match comparison is a ``ClickException``, not a ``NO_CHANGE``
verdict -- exit 0 must mean a real comparison found nothing broken.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import click

from ....model.sided_inputs import compose_sided_paths

# Via `report`, which re-exports it: `frontends -> policy` is forbidden
# (`architecture/modules.yaml`), even for a signature's own annotation.
from ....report.comparison_scope import comparison_scope_terms
from ....report.release_document import (
    ReleaseEvaluation,
    build_release_report,
    stored_reported_verdict,
)
from ....report.release_formats import STORED_RELEASE_FORMATS
from ....report.render_release_report import (
    render_release_text,
    render_stored_release_json,
)
from ....workflows.bundle_facts_stored_compare import (
    execute_bundle_facts_compare,
    per_library_report_path,
)
from ....workflows.bundle_facts_stored_request import (
    BundleFactsCompareError,
    BundleFactsCompareRequest,
    reject_explicit_compile_config,
)
from .compare_bundle_facts_release import (
    stored_document_details,
    stored_release_evaluation,
)


def _click_error(exc: BundleFactsCompareError) -> click.ClickException:
    """The Click exception a workflow error renders as: exit 64 for a usage
    error, exit 1 for an operational failure."""
    if exc.param_hint is not None:
        return click.BadParameter(str(exc), param_hint=exc.param_hint)
    if exc.usage:
        return click.UsageError(str(exc))
    return click.ClickException(str(exc))


def _resolve_new_side_headers_includes(
    kwargs: dict[str, Any],
) -> tuple[list[Path], list[Path]]:
    """NEW-side headers/includes (`model.sided_inputs`), for the compile
    context. The OLD side has none of its own (already a resolved, stored
    snapshot)."""
    headers = compose_sided_paths(
        kwargs.get("headers") or (), kwargs.get("new_headers_only") or ()
    )
    includes = compose_sided_paths(
        kwargs.get("includes") or (), kwargs.get("new_includes_only") or ()
    )
    return headers, includes


def resolve_dispatch_compile_context(
    ctx: click.Context, kwargs: dict[str, Any], *, new_is_stored: bool
) -> Any:
    """Resolve ``dispatch()``'s ``compile_context`` argument, mutating
    *kwargs* the same way ``compare_cmd`` used to before delegating here.

    When *new_is_stored* this skips ``resolve_compile_context`` and returns
    ``None``: neither side does any header-frontend extraction, so running it
    anyway would merge ``.abicheck.yml``'s own ``compile.include_dirs`` into
    ``kwargs["includes"]`` and the stored/stored rejections would then wrongly
    refuse it as an explicit ``--include`` (Codex review, PR #1060). An
    *explicit* ``--config`` with a real ``compile:`` block is still rejected.
    """
    from ....config_paths import resolve_project_config

    # Codex review: mirror run_compare's own cwd-upward cfg_path fallback --
    # resolve_compile_context alone never auto-discovers without a --sources tree.
    _config_explicit = (
        ctx.get_parameter_source("config") == click.core.ParameterSource.COMMANDLINE
    )
    kwargs["config"] = resolve_project_config(
        kwargs.get("config"), search_from=Path.cwd()
    ).path
    if new_is_stored:
        if _config_explicit and kwargs["config"] is not None:
            try:
                reject_explicit_compile_config(kwargs["config"])
            except BundleFactsCompareError as exc:
                raise _click_error(exc) from exc
        return None

    from ....cli_options import (
        apply_compile_config_env_toggles,
        resolve_compile_context,
    )

    _headers, _includes = _resolve_new_side_headers_includes(kwargs)
    # The compile-context flags are gone from compare's CLI (ADR-037 D8.1), so
    # every one is passed as its "nothing explicit" default -- i.e. "defer to
    # .abicheck.yml's compile: block".
    compile_context, merged_includes = resolve_compile_context(
        ctx,
        sysroot=None,
        nostdinc=False,
        header_backend="auto",
        includes=tuple(_includes),
        build_config=kwargs["config"],
        frontend_context="host",
        compiler_path=None,
        compiler_prefix=None,
        compiler_option_tokens=(),
        # ADR-074's -D/--define -- Click really does populate this one.
        defines=tuple(kwargs.get("defines") or ()),
        # Use the real `_config_explicit` so the compile.compiler trust gate
        # isn't fooled by an auto-discovered path (PR #1154).
        config_explicit=_config_explicit,
    )
    apply_env_toggles_for_stored_pair(
        ctx, kwargs["config"], apply_compile_config_env_toggles
    )
    # Forward the *merged* include list (Codex review), so .abicheck.yml's
    # compile.include_dirs is not dropped by dispatch()'s own re-derivation.
    kwargs["includes"] = tuple(merged_includes)
    kwargs["new_includes_only"] = ()
    return compile_context


def apply_env_toggles_for_stored_pair(
    ctx: click.Context,
    config_path: Path | None,
    apply_env_toggles: Any,
) -> None:
    """Apply ``compile.ast_frontend_fallback``/``compile.allow_unsupported_
    castxml`` for the stored-OLD_FACTS + live-NEW_INPUT ``compare`` path.

    This path does real header-AST extraction later, but it never applied these
    two toggles the way ``run_compare``/``dump`` do (CodeRabbit review, PR
    #1146). Call this after ``resolve_compile_context`` has validated
    *config_path* and before extraction runs. *apply_env_toggles* is the
    caller's own ``cli_options.apply_compile_config_env_toggles``, passed in
    rather than imported here to keep ``cli_options`` out of this module's
    import graph (the CLI-registration cycle).
    """
    if config_path is None:
        return
    from ....workflows.extraction import load_build_config_with_digest

    try:
        cfg = load_build_config_with_digest(config_path)[0]
    except ValueError as exc:
        # A malformed config is a usage error (exit 64), discovered or not;
        # the compile-context merge may have tolerated it with a warning.
        raise click.UsageError(
            f"cannot parse build config {config_path}: {exc}"
        ) from exc
    apply_env_toggles(ctx, cfg)


def _reject_unrendered_formats(request: Any) -> None:
    """Refuse a ``-o`` format a stored comparison does not render.

    The one format list is ``report.release_formats.STORED_RELEASE_FORMATS``;
    this is the layer that renders, so it owns the check. Usage error (exit 64).
    """
    for fmt in (request.fmt, *(f for f, _path in request.secondary_writes)):
        if fmt not in STORED_RELEASE_FORMATS:
            raise click.UsageError(
                f"-o {fmt}=... is not available with a stored-bundle-facts OLD_INPUT: "
                "only json/markdown/junit/oneline are supported for a stored-bundle-facts "
                "comparison. Choose one of: json, markdown, junit, oneline."
            )


def _warn(message: str) -> None:
    click.echo(f"Warning: {message}", err=True)


#: The kwargs that together mean "a build/source operand was given" for a
#: stored comparison's rejection. The rule itself is the workflow's.
_BUILD_SOURCE_KWARGS = (
    "old_sources",
    "new_sources",
    "old_build_info",
    "new_build_info",
    "old_dump_manifest",
    "new_dump_manifest",
)


def _request_from_kwargs(
    kwargs: dict[str, Any],
    *,
    compile_context: Any,
    new_is_stored: bool,
    config_explicit: bool,
) -> BundleFactsCompareRequest:
    """Map compare's parsed, normalized kwargs onto the workflow request.

    Each field is a faithful projection of one kwarg; presence-only fields
    reduce to a bool here because the workflow's rules read nothing else.
    """
    from ..options.params import _load_suppression_and_policy

    output = kwargs.get("output")
    output_dir = kwargs.get("output_dir")
    bundle_facts_out = kwargs.get("bundle_facts_out")
    return BundleFactsCompareRequest(
        old_facts_path=kwargs["old_input"],
        new_input=kwargs["new_input"],
        new_is_stored=new_is_stored,
        config_path=kwargs.get("config"),
        config_explicit=config_explicit,
        compile_context=compile_context,
        policy=kwargs["policy"],
        load_suppression_and_policy=lambda: _load_suppression_and_policy(
            kwargs.get("suppress"),
            kwargs["policy"],
            kwargs.get("policy_file_path"),
        ),
        warn=_warn,
        fmt=kwargs.get("fmt", "json"),
        secondary_writes=kwargs.get("secondary_writes", ()),
        output=Path(output) if output is not None else None,
        output_dir=Path(output_dir) if output_dir is not None else None,
        bundle_facts_out=Path(bundle_facts_out)
        if bundle_facts_out is not None
        else None,
        depth=kwargs.get("depth"),
        lang=kwargs.get("lang", "c++"),
        lang_explicit=bool(kwargs.get("lang_explicit", False)),
        new_version=kwargs.get("new_version", "new"),
        old_version=kwargs.get("old_version"),
        include_dependencies=bool(kwargs.get("include_dependencies", False)),
        show_only=bool(kwargs.get("show_only")),
        report_mode=kwargs.get("report_mode"),
        contract_mode=kwargs.get("contract_mode"),
        severity_preset=kwargs.get("severity_preset"),
        pack_paths=tuple(kwargs.get("pack_paths") or ()),
        dry_run=bool(kwargs.get("dry_run")),
        headers=tuple(kwargs.get("headers") or ()),
        new_headers_only=tuple(kwargs.get("new_headers_only") or ()),
        old_headers_only=tuple(kwargs.get("old_headers_only") or ()),
        includes=tuple(kwargs.get("includes") or ()),
        new_includes_only=tuple(kwargs.get("new_includes_only") or ()),
        old_includes_only=tuple(kwargs.get("old_includes_only") or ()),
        new_devel_pkg=kwargs.get("devel_pkg2"),
        old_devel_pkg_given=kwargs.get("devel_pkg1") is not None,
        manifest_path=kwargs.get("manifest_path"),
        bundle_facts_library_manifest=kwargs.get("bundle_facts_library_manifest"),
        # A --used-by-manifest-named consumer is as unsupported as a bare
        # --used-by one, so both spellings reach the same rule.
        used_by_apps=(
            tuple(kwargs.get("used_by_apps") or ())
            + tuple(kwargs.get("used_by_manifests") or ())
        ),
        required_symbols=tuple(kwargs.get("required_symbols_opt") or ()),
        use_cases_manifest=kwargs.get("use_cases_manifest"),
        diagnostic_comparison=bool(kwargs.get("diagnostic_comparison", False)),
        include_labels=kwargs.get("include_labels"),
        build_or_source_operands_given=any(
            kwargs.get(name) is not None for name in _BUILD_SOURCE_KWARGS
        ),
        probe_matrix_given=(
            kwargs.get("probe_matrix_old") is not None
            or kwargs.get("probe_matrix_new") is not None
        ),
        debug_info_given=(
            kwargs.get("debug_info2") is not None
            or kwargs.get("debug_info1") is not None
        ),
        debug_roots_given=bool(
            kwargs.get("debug_roots")
            or kwargs.get("debug_roots_old")
            or kwargs.get("debug_roots_new")
        ),
        dependency_flags_given=bool(
            kwargs.get("follow_deps")
            or kwargs.get("search_paths")
            or kwargs.get("ld_library_path")
        ),
    )


def dispatch(
    *,
    compile_context: Any,
    new_is_stored: bool = False,
    config_explicit: bool = False,
    **kwargs: Any,
) -> None:
    """Handle a ``compare OLD_FACTS NEW_INPUT`` invocation where OLD_FACTS
    classified as a stored BundleFacts document.

    *new_is_stored*, when true, means NEW_INPUT classified as a stored
    BundleFacts document too -- both sides are then diffed in memory by
    ``compare_stored_bundle_facts_pair``. The default ``False`` is the
    stored/live shape, which extracts NEW_INPUT as a live directory/package.

    *config_explicit* is ``compare_cmd``'s own ``_config_explicit``: by dispatch
    time ``kwargs["config"]`` no longer distinguishes an explicit path from an
    auto-discovered one.

    *kwargs* is ``compare_cmd``'s already-parsed, already-normalized option
    dict. Never returns normally: like every other verdict-emitting command,
    it exits via ``sys.exit`` (through ``_exit_compare_release``).

    *compile_context* is resolved by the caller (``compare_cmd``), not here:
    this leaf module is kept out of ``cli_options``'s own import graph.
    """
    from ..release_exit import _exit_compare_release
    from ..runtime import _safe_write_output

    request = _request_from_kwargs(
        kwargs,
        compile_context=compile_context,
        new_is_stored=new_is_stored,
        config_explicit=config_explicit,
    )
    _reject_unrendered_formats(request)
    try:
        outcome = execute_bundle_facts_compare(request)
    except BundleFactsCompareError as exc:
        raise _click_error(exc) from exc

    scope_terms = comparison_scope_terms(outcome.scope_decision)
    result = outcome.result
    # One live-shaped evaluation for every format of this run: the receipt is
    # resolved once, from the same policy flags the stored run was given.
    details = stored_document_details(result, new_is_stored=new_is_stored)
    release = stored_release_evaluation(
        result,
        worst_verdict=stored_reported_verdict(result),
        old_facts_path=request.old_facts_path,
        new_dir=request.new_input,
        scope_terms=scope_terms,
        assurance_decision=outcome.assurance_decision,
        require_complete_analysis=bool(
            outcome.assurance_decision and outcome.assurance_decision.require_complete
        ),
        effective_config=_stored_effective_config(kwargs),
    )
    text = _render(request.fmt, release, details)
    if request.output is not None:
        _safe_write_output(request.output, text)
    else:
        click.echo(text)
    for secondary_fmt, secondary_output in request.secondary_writes:
        # Every promised secondary artifact is rendered and written, not
        # dropped -- re-rendered since a secondary format can differ.
        _safe_write_output(
            Path(secondary_output),
            _render(secondary_fmt, release, details),
        )
    if request.output_dir is not None:
        # NEW_INPUT is a release-style operand, so --output-dir writes one
        # `{library}.json` per matched library, mirroring the live release
        # fan-out's own layout.
        from ....report.diff_report import to_json

        for diff in result.per_library:
            _safe_write_output(
                per_library_report_path(request.output_dir, diff.library),
                to_json(
                    diff, require_complete_analysis=outcome.require_complete_analysis
                ),
            )

    _exit_compare_release(
        stored_reported_verdict(result),
        fail_on_removed=False,
        removed_keys=[],
        incomplete_scope_exit_contribution=outcome.scope_decision.incomplete_scope_exit_contribution,
        no_comparison_completed_exit_contribution=outcome.scope_decision.no_comparison_completed_exit_contribution,
        assurance_decision=outcome.assurance_decision,
    )


def _stored_effective_config(kwargs: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """The receipt a stored run recomputes from the policy flags it was given.

    The stored run has no live fan-out to resolve it from, so it resolves the
    same baseline receipt ``compare`` states for a single release, from the
    stored run's own ``--policy``/``--policy-file``/``--suppress``.
    """
    from ....cli_compare_receipt import _release_summary_effective_config_block

    return _release_summary_effective_config_block(
        None,
        policy=kwargs["policy"],
        policy_file_path=kwargs.get("policy_file_path"),
        suppress=kwargs.get("suppress"),
    )


def _render(fmt: str, release: ReleaseEvaluation, details: Mapping[str, object]) -> str:
    """Render the stored comparison's *release* evaluation as *fmt*.

    JSON is the stored release document (the live release document with its
    stored markers); every other format is the live release renderer's
    projection of the same evaluation, so the formats share one document.
    """
    if fmt == "json":
        return render_stored_release_json(release, details=details)
    return render_release_text(
        fmt, build_release_report(release), demangle=fmt == "markdown"
    )

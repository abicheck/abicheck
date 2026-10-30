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

"""Classify a resolved compare pair and run the typed compare end to end.

The second half of the typed compare pipeline (ADR-055 D1) and its composition.
:func:`run_compare_request` is the typed chokepoint every non-CLI caller uses:
it resolves a :class:`CompareRequest` through
:func:`~abicheck.workflows.compare.resolve.resolve_compare_request`, then
classifies the pair here with :func:`classify_compare_pair`. A front end that
must configure the run between the two phases (the native ``compare`` CLI's
ADR-049 configuration step) calls them directly instead.

:func:`run_compare` is the keyword-argument shim over the typed request kept
for existing Python callers; new code builds a :class:`CompareRequest`.

Moved from ``abicheck/service_compare_pipeline.py`` alongside
:mod:`abicheck.workflows.compare.resolve`. Behaviour is unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...compile_context import CompileContext
from ...environment_matrix import EnvironmentMatrix
from ...serialization import run_scoped_digest_cache
from ..compare_classify import ResolvedComparePair
from ..request_inputs import InputSpec
from ..service_requests import CompareRequest, CompareResult
from .resolve import resolve_compare_request

__all__ = [
    "classify_compare_pair",
    "run_compare",
    "run_compare_request",
]


@run_scoped_digest_cache
def classify_compare_pair(
    request: CompareRequest,
    pair: ResolvedComparePair,
) -> CompareResult:
    """Classify an already-resolved pair — the second half of ``run_compare_request``.

    Resolves the request's policy inputs (suppression list, policy file,
    forwarded and selected packs, evaluation config, project fold,
    acknowledgments), classifies through
    :func:`~abicheck.workflows.compare_classify.classify_resolved_pair` -- the
    one classification implementation, which the native ``compare`` CLI calls
    too with the policy inputs its own ADR-049 configuration step resolved --
    and then scores the result under the request's gate
    (:func:`~abicheck.workflows.compare_finalize.finalize_classified_pair`).

    The environment matrix is read straight off ``request.env_matrix``: the
    field already holds the resolved
    :class:`~abicheck.environment_matrix.EnvironmentMatrix`.
    """
    from ..compare_classify import classify_resolved_pair
    from ..compare_finalize import finalize_classified_pair
    from ..compare_policy import load_suppression_and_policy
    from ..compare_request_policy import resolve_request_policy_inputs

    suppression, pf = load_suppression_and_policy(
        request.suppress,
        request.policy,
        request.policy_file_path,
        strict_suppressions=request.strict_suppressions,
        require_justification=request.require_justification,
    )
    policy_inputs = resolve_request_policy_inputs(request, suppression, pf)
    classified = classify_resolved_pair(request, pair, policy_inputs)
    return finalize_classified_pair(
        request,
        pair.resolved_execution_context,
        classified,
        policy_inputs.suppression,
        policy_inputs.evaluation_config,
        pack_application=policy_inputs.pack_application,
    )


def run_compare_request(request: CompareRequest) -> CompareResult:
    """Compare two ABI inputs described by a :class:`CompareRequest`.

    The single classification chokepoint (ADR-037 D1/D2): every front-end
    builds a ``CompareRequest`` and calls this, so defaults cannot diverge
    between invocation paths. The keyword-argument :func:`run_compare` is a
    thin shim that builds the request and delegates here.

    Exactly the composition of this module's own two phases --
    :func:`resolve_compare_request` then :func:`classify_compare_pair`. A
    front end that must configure the run between them (the native
    ``compare`` CLI's ADR-049 ``resolve_compare_configuration``, whose ``--pack`` can
    move the policy file and severity levels the classification is scored
    under) calls the two phases directly rather than keeping a second
    resolution implementation.

    Returns a :class:`~abicheck.workflows.service_requests.CompareResult` (ADR-055 D2), not the
    bare ``(DiffResult, old, new)`` tuple it returned before 0.6: a struct can
    gain a field without breaking positional callers, which a tuple cannot.
    ``CompareResult.as_tuple()`` reproduces the old shape for a caller that
    wants it back in one line.

    Raises:
        ValidationError: If the request fails :meth:`CompareRequest.validate`.
        PlanningError: See :func:`resolve_compare_request` — raised from
            inside its own call here.
        SnapshotError: If either input cannot be loaded.

    Moved here from ``service.py`` (CLI cleanup phase two, "PR B" slice 1,
    alongside :func:`run_compare`) once the latter's two new pack-forwarding
    parameters pushed that file over the AI-readiness file-size cap --
    re-exported from ``service.py`` unchanged, the same pattern
    ``resolve_compare_request``/``classify_compare_pair`` already use.

    ADR-068 §3 #19: ``request.budget_s`` (``None`` = unbounded) bounds both
    phases under one ``deadline.deadline_scope``. A typed caller gets a
    clear :class:`~abicheck.deadline.DeadlineExceeded` rather than a partial
    result -- ``cli_compare_helpers.run_compare`` is what maps this axis
    onto exit 5 for the CLI, which needs an exit code instead.
    """
    import abicheck.deadline as deadline

    request = _with_config_overrides(request)
    with deadline.deadline_scope(request.budget_s):
        # deadline_scope never raises on entry -- check() before the work
        # `resolve_compare_request` performs at its own top.
        deadline.check()
        pair = resolve_compare_request(request)
        return classify_compare_pair(request, pair)


#: ``CompareRequest`` severity fields and the resolved-config attribute each
#: one reads from (``ResolvedCompareConfig.merged_severity_*``).
_SEVERITY_OVERRIDE_FIELDS: tuple[tuple[str, str], ...] = (
    ("severity_preset", "merged_severity_preset"),
    ("severity_abi_breaking", "merged_severity_abi_breaking"),
    ("severity_potential_breaking", "merged_severity_potential_breaking"),
    ("severity_quality_issues", "merged_severity_quality_issues"),
    ("severity_addition", "merged_severity_addition"),
)


def _with_config_overrides(request: CompareRequest) -> CompareRequest:
    """*request* with its ``config_overrides`` resolved into the fields they set.

    Resolved by :func:`~abicheck.workflows.compare_configuration.load_and_resolve_compare_config`,
    the function the CLI's ``compare --set`` calls, so the two read the same
    values. A field the caller already set away from its default is kept:
    the typed argument beats an override, as the CLI flag does.
    """
    if not request.config_overrides:
        return request
    from ..compare_configuration import load_and_resolve_compare_config

    _, resolved, _ = load_and_resolve_compare_config(
        None,
        severity_preset=None,
        config_overrides=request.config_overrides,
    )
    defaults = {f.name: f.default for f in dataclasses.fields(CompareRequest)}
    updates: dict[str, Any] = {}

    def take(name: str, value: Any) -> None:
        if getattr(request, name) == defaults[name] and value is not None:
            updates[name] = value

    take("scope_public", resolved.scope_public)
    take("collapse_versioned_symbols", resolved.collapse_versioned_symbols)
    if request.force_public_symbols is None and resolved.public_symbols:
        updates["force_public_symbols"] = frozenset(resolved.public_symbols)
    for field_name, resolved_attr in _SEVERITY_OVERRIDE_FIELDS:
        take(field_name, getattr(resolved, resolved_attr))
    return dataclasses.replace(request, **updates) if updates else request


def _declared_default(owner: type, name: str) -> Any:
    """A request field's declared default, read from its dataclass.

    The keyword shim restates the defaults of the ``CompareRequest``/
    ``InputSpec`` fields it forwards onto; reading them here keeps the shim
    and the request from disagreeing. The signature shows the same values.
    """
    return next(f.default for f in dataclasses.fields(owner) if f.name == name)


def run_compare(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path] | None = None,
    new_headers: list[Path] | None = None,
    old_includes: list[Path] | None = None,
    new_includes: list[Path] | None = None,
    old_version: str = _declared_default(InputSpec, "version"),
    new_version: str = _declared_default(InputSpec, "version"),
    lang: str = _declared_default(CompareRequest, "lang"),
    lang_explicit: bool = _declared_default(CompareRequest, "lang_explicit"),
    frontend: str = _declared_default(CompareRequest, "frontend"),
    suppress: Path | tuple[Path, ...] | None = None,
    policy: str = _declared_default(CompareRequest, "policy"),
    policy_file_path: Path | None = None,
    old_pdb_path: Path | None = None,
    new_pdb_path: Path | None = None,
    old_debug_roots: list[Path] | None = None,
    new_debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = _declared_default(CompareRequest, "enable_debuginfod"),
    scope_to_public_surface: bool = _declared_default(CompareRequest, "scope_public"),
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = _declared_default(CompareRequest, "pattern_verdicts"),
    public_surface_allowlist: set[str] | None = None,
    debuginfod_url: str | None = None,
    diagnostic_comparison: bool = _declared_default(
        CompareRequest, "diagnostic_comparison"
    ),
    contract_evaluation: bool = _declared_default(
        CompareRequest, "contract_evaluation"
    ),
    # `False` matches the CLI flag and every other typed surface; this one
    # matters most, since it is written into *both* `InputSpec`s below and so
    # overrides their own field default (Codex review, PR #1258).
    include_dependencies: bool = _declared_default(InputSpec, "include_dependencies"),
    contract_mode: str | None = None,
    pack_policy_overrides: dict[Any, Any] | None = None,
    pack_internal_namespaces: tuple[str, ...] | None = None,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    *,
    severity_preset: str | None = None,
    public_header_dirs: list[Path] | None = None,
    collapse_versioned_symbols: bool = _declared_default(
        CompareRequest, "collapse_versioned_symbols"
    ),
    project_policy_overrides: dict[Any, Any] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
    exclude_headers: tuple[str, ...] = _declared_default(InputSpec, "exclude_headers"),
    acknowledgments_path: Path | None = None,
    acknowledgment_unacknowledged_additions: str | None = None,
    acknowledgment_breaks: str | None = None,
    # ADR-091 D4: appended last, same rule as every parameter above.
    config_overrides: Mapping[str, object] | None = None,
) -> CompareResult:
    """Compare two ABI inputs and return the classified diff result.

    Keyword-argument shim over :func:`run_compare_request`: it assembles a
    :class:`CompareRequest` from loose arguments and delegates, so existing
    callers keep working while the typed request is the real chokepoint
    (ADR-037 D2). New callers should build a ``CompareRequest`` directly.
    Trailing params (``debuginfod_url`` onward) are appended after every
    pre-existing one, so a positional caller keeps binding each argument to
    the same parameter it always did (Codex review, PR #551). Only
    ``severity_preset`` (the newest, never previously released) is actually
    ``*``-enforced keyword-only -- enforcing it retroactively for the older
    ones too would reject a real caller still passing one positionally, the
    public-API break that "keeps binding positionally" promise forbids
    (Codex review, fresh evidence, PR #1032). (Its sibling
    ``exit_code_scheme`` parameter was deleted in CLI cleanup phase two PR
    G2 -- the manual algorithm selector no longer exists anywhere.)
    ``include_dependencies`` applies to *both* sides — build a
    ``CompareRequest`` for a per-side override.

    ``pack_policy_overrides``/``pack_internal_namespaces``: an already-
    resolved ``--pack``'s ``policy.overrides``/``surface.internal_namespaces``
    contribution -- see ``CompareRequest.pack_policy_overrides``'s own
    docstring for what folds them in and why. ``None``/empty is a no-op.

    ``project_policy_overrides`` (ADR-068 §3 #23): weaker than both an
    explicit ``--policy <file>`` and ``pack_policy_overrides`` above --
    see ``CompareRequest.project_policy_overrides``. ``None``/empty is a
    no-op.

    ``compile_context`` is a both-sides :class:`~abicheck.compile_context.
    CompileContext` (the L2 cross-toolchain/frontend family --
    ``--ast-frontend``/``--compiler``/``--compiler-prefix``/
    ``--compiler-option``/``--sysroot``/``--nostdinc``/``--frontend-context``),
    applied identically to :class:`InputSpec.compile` on both sides -- the
    same both-sides-only granularity ``include_dependencies`` already has in
    this shim; a caller needing a genuine per-side override should build a
    :class:`CompareRequest` directly instead. ``None`` (the default) is a
    no-op, matching every pre-existing caller.

    ``depth`` forwards straight onto :class:`CompareRequest.depth` -- the
    release fan-out's ``compare_member_pair`` needed a way to forward its own
    resolved ``--depth binary`` per pair; every other caller passes ``None``.

    ``severity_preset`` (Codex review): forwards onto ``CompareRequest``'s
    identically-named field; ``None`` is a no-op.

    ``public_header_dirs`` (CodeRabbit review, PR #1138): a project's
    ``scope.public_header_dirs``, folded unchanged into both sides'
    ``InputSpec.public_header_dirs`` -- closes the release fan-out's own
    gap, since ``workflows.release_members.compare_member_pair`` calls
    this shim rather than ``cli_resolve._resolve_compare_snapshots``
    (which already threads this config key for a single-pair compare).
    ``None`` is a no-op, matching every pre-existing caller.

    ``env_matrix`` (ADR-020b / ADR-068 D5): the already-resolved declared
    deployment matrix (`.abicheck.yml`'s `deployment:`, former
    `--env-matrix FILE`), forwarded onto `CompareRequest`'s identically-
    named field so a release fan-out member gets the same declared-floor
    reclassification a single-pair compare would. `None` is a no-op.

    ``exclude_headers``: the run's ``--exclude-header`` patterns, folded
    identically into *both* sides' :attr:`InputSpec.exclude_headers` -- the
    same both-sides granularity ``include_dependencies`` has in this shim,
    and the granularity the flag itself promises ("applies to both sides").
    Applying them asymmetrically is what
    ``extract.header_exclusions.exclusion_asymmetry_reason`` refuses, so
    there is deliberately no per-side knob here; a caller needing one builds
    a :class:`CompareRequest` directly and accepts that gate.

    This shim is the release fan-out's only route to the exclusion rules: a
    directory/package ``compare`` reaches it through
    ``workflows.release_members.compare_member_pair``, not through
    ``cli_resolve._resolve_compare_snapshots`` (which already threaded them
    for a single-pair compare). Without this parameter the patterns were
    accepted on the command line, dropped at the directory branch, and every
    library parsed the unfiltered header tree -- so an MKL release whose
    ``include/`` cannot be parsed whole failed on all 28 libraries under the
    exact arguments that made the single-library comparison succeed. ``()``
    (the default) is a no-op, matching every pre-existing caller.

    ``collapse_versioned_symbols`` (Codex review, fresh evidence): forwards
    onto ``CompareRequest``'s identically-named field -- closes the release
    fan-out's own gap the same way ``public_header_dirs`` above did: a
    ``scope.collapse_versioned_symbols: true`` project config resolved to
    this shim's caller previously had no channel to reach a directory/
    package member's own per-library compare, so each member silently kept
    the field at its ``False`` default and could report a version-renamed
    symbol as a spurious removal/addition where the identical scalar
    comparison collapsed it. ``False`` (the default) is a no-op, matching
    every pre-existing caller.

    Returns:
        A :class:`~abicheck.workflows.service_requests.CompareResult`. This returned the bare
        ``(DiffResult, old, new)`` tuple before 0.6; ``.as_tuple()`` gives that
        shape back for a caller that unpacks positionally.
    Raises:
        SnapshotError: If either input cannot be loaded.
        ValidationError: If inputs have unrecognised formats.
    """
    _public_header_dirs = tuple(public_header_dirs or ())
    _exclude_headers = tuple(exclude_headers or ())
    request = CompareRequest(
        old=InputSpec(
            path=old_input,
            headers=tuple(old_headers or ()),
            exclude_headers=_exclude_headers,
            includes=tuple(old_includes or ()),
            version=old_version,
            pdb=old_pdb_path,
            debug_roots=tuple(old_debug_roots or ()),
            include_dependencies=include_dependencies,
            compile=compile_context,
            public_header_dirs=_public_header_dirs,
        ),
        new=InputSpec(
            path=new_input,
            headers=tuple(new_headers or ()),
            exclude_headers=_exclude_headers,
            includes=tuple(new_includes or ()),
            version=new_version,
            pdb=new_pdb_path,
            debug_roots=tuple(new_debug_roots or ()),
            include_dependencies=include_dependencies,
            compile=compile_context,
            public_header_dirs=_public_header_dirs,
        ),
        lang=lang,
        lang_explicit=lang_explicit,
        frontend=frontend,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        scope_public=scope_to_public_surface,
        force_public_symbols=(
            frozenset(force_public_symbols) if force_public_symbols else None
        ),
        public_surface_allowlist=(
            frozenset(public_surface_allowlist)
            if public_surface_allowlist is not None
            else None
        ),
        pattern_verdicts=pattern_verdicts,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        pack_policy_overrides=(
            tuple(pack_policy_overrides.items()) if pack_policy_overrides else None
        ),
        pack_internal_namespaces=pack_internal_namespaces,
        project_policy_overrides=(
            tuple(project_policy_overrides.items())
            if project_policy_overrides
            else None
        ),
        depth=depth,
        severity_preset=severity_preset,
        config_overrides=dict(config_overrides or {}),
        collapse_versioned_symbols=collapse_versioned_symbols,
        env_matrix=env_matrix,
        acknowledgments_path=acknowledgments_path,
        acknowledgment_unacknowledged_additions=acknowledgment_unacknowledged_additions,
        acknowledgment_breaks=acknowledgment_breaks,
    )
    return run_compare_request(request)

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

"""The single-pair ``compare`` session: one resolved request in, one outcome out.

``abicheck compare OLD NEW`` for two single operands (a binary, a snapshot,
a dump) runs here, in one place and in this order:

1. **collect inputs** -- ``--depth`` folded into the collect mode and the
   header roots (``fold_compare_collect_inputs``), narrowed by the
   changed-path seed; the ``--abi3`` floor (``enrichment``);
2. **compile context** -- the ``compile:`` block folded over the CLI's L2
   knobs (``workflows.compile_config``), split per side, the pair-wide
   C++20 dialect applied;
3. **operands** -- a GNU ld linker script followed, the operand-shape rules
   (``operand_rules``) checked, unused inputs named;
4. **resolution** -- one ``CompareRequest`` per pair, a raw
   ``--sources``/``--build-info`` side stated for inline collection
   (``inline_evidence``), both sides resolved by
   ``workflows.compare.resolve`` under the ``--budget`` deadline;
5. **policy** -- suppression and policy documents, the ADR-049 evaluation
   configuration, ``.abicheck.yml``'s ``policy.overrides``, out-of-band
   packs, the POST-manifest overlay and acknowledgments (``project_inputs``);
6. **classification** -- the shared ``classify_resolved_pair`` every scalar
   route runs (cli-contract, ADR-037 D10.1).

The CLI (``cli_compare_helpers.run_compare``) parses options, runs the
preflight a ``--dry-run`` shares, builds the :class:`PairSessionRequest`,
and renders the :class:`PairSessionOutcome`. It translates exactly three
things back into Click: :class:`~abicheck.workflows.run_input_errors.
RunInputError` (exit 64 or 1, message unchanged), :class:`PairNotComparable`
(the ADR-050 D2 report, exit 16) and :class:`PairBudgetExceeded` (the
``--budget`` report). Any other exception propagates as it always did.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...deadline import DeadlineExceeded, check as check_deadline
from ...errors import (
    AbicheckError,
    ProfileMismatchError,
    ScopeMismatchError,
    SnapshotError,
    ValidationError,
)
from ..run_input_errors import RunInputError
from .session_inputs import (
    note_debug_resolution,
    operand_rule_refusals,
    pair_request,
    resolve_compile_context,
    resolve_pair,
    warn_ignored_development_packages,
    warn_ignored_header_inputs,
)
from .session_request import PairSessionOutcome, PairSessionRequest, SessionHost

if TYPE_CHECKING:
    from ...model import AbiSnapshot
    from ..compare_classify import ResolvedComparePair
    from ..service_requests import CompareRequest

__all__ = [
    "PairBudgetExceeded",
    "PairNotComparable",
    "run_pair_session",
    "with_out_of_band_packs",
]

Notify = Callable[[str], None]


class PairNotComparable(AbicheckError):
    """The ADR-050 D2 comparability gate refused the resolved pair."""

    def __init__(
        self,
        cause: ProfileMismatchError | ScopeMismatchError,
        old: AbiSnapshot,
        new: AbiSnapshot,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.old = old
        self.new = new


class PairBudgetExceeded(AbicheckError):
    """``--budget`` expired; *label* names what was being compared."""

    def __init__(
        self,
        cause: DeadlineExceeded,
        *,
        label: str,
        library: str,
        old_version: str,
        new_version: str,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.label = label
        self.library = library
        self.old_version = old_version
        self.new_version = new_version


def run_pair_session(
    request: PairSessionRequest, host: SessionHost
) -> PairSessionOutcome:
    """Resolve, evaluate and classify one pair (module docstring, steps 1-6)."""
    from ..compare.configuration import (
        fold_compare_collect_inputs,
        split_compile_context_frontend,
    )
    from ..compare_configuration import (
        CompareSettingsError,
        _pair_wide_dialect_override,
    )
    from .enrichment import resolve_compare_enrichment_inputs

    notify = host.notify
    hdr, ev, cfg = request.headers, request.evidence, request.project
    # 1. Collect inputs. --depth when given, else inferred from
    # --sources/--build-info (or config source.method), ADR-037 D5.
    try:
        collect_mode, headers, old_headers, new_headers = fold_compare_collect_inputs(
            depth=ev.depth,
            source_method=cfg.resolved.source_method,
            headers=hdr.headers,
            old_headers_only=hdr.old_headers,
            new_headers_only=hdr.new_headers,
            old_sources=ev.old_sources,
            new_sources=ev.new_sources,
            old_build_info=ev.old_build_info,
            new_build_info=ev.new_build_info,
        )
    except CompareSettingsError as exc:
        raise RunInputError.usage(str(exc)) from exc
    # An explicit "auto" returns to auto-detection.
    debug_format = ev.debug.format
    if debug_format is not None and debug_format.lower() == "auto":
        debug_format = None
    # ADR-068 Phase 2c/2d: the changed-path seed (scoping only, narrowing the
    # L4/L5 points of interest per ADR-043 D7) and the abi3 floor.
    enrich = resolve_compare_enrichment_inputs(
        since=ev.since,
        changed_paths_opt=ev.changed_paths,
        abi3=ev.abi3,
        project_cfg=cfg.document,
        sources=ev.new_sources or ev.old_sources,
        notify=notify,
    )
    collect_mode = enrich.localize_collect_mode(collect_mode)

    # 2. Compile context, applying to both sides.
    compile_context, merged_includes = resolve_compile_context(request, notify)
    # The dirs the config appended past the CLI -I roots apply to *both*
    # sides, so they must survive a per-side include override (which
    # replaces the both-sides -I for that side): re-appended after per-side
    # resolution rather than folded into the shared tuple (Codex review).
    config_includes = tuple(merged_includes[len(hdr.includes) :])
    # The merged frontend flows to both sides through the explicit
    # header_backend (so a per-side backend can still override it); the
    # threaded context's frontend is neutralised so run_dump's
    # `compile.frontend` does not outrank it.
    header_backend, side_compile_context = split_compile_context_frontend(
        compile_context
    )
    from ...model.sided_inputs import resolve_per_side_inputs
    from ..operand_rules import (
        reject_debug_format_for_non_elf,
        reject_manifest_header_conflicts,
        reject_manifest_non_elf,
    )

    old_h, new_h, old_inc, new_inc = resolve_per_side_inputs(
        headers,
        hdr.includes,
        old_headers,
        new_headers,
        hdr.old_includes,
        hdr.new_includes,
    )
    with operand_rule_refusals():
        reject_manifest_header_conflicts(
            hdr.old_dump_manifest, hdr.new_dump_manifest, old_h, new_h
        )
    if config_includes:
        old_inc = list(old_inc) + list(config_includes)
        new_inc = list(new_inc) + list(config_includes)
    # Pair-wide C++20 dialect resolution (P0 fix): both the inline-embed
    # context and the per-side one.
    compile_context, side_compile_context = _pair_wide_dialect_override(
        request.compile.lang, old_h, new_h, compile_context, side_compile_context
    )

    # 3. Operands: follow a GNU ld linker script up front so the resolved DSO
    # (not the text script) drives format detection, metadata and dependency
    # analysis.
    from ..extraction import follow_linker_script

    old_input, old_fmt = follow_linker_script(request.operands.old, notify)
    new_input, new_fmt = follow_linker_script(request.operands.new, notify)
    with operand_rule_refusals():
        reject_manifest_non_elf(
            hdr.old_dump_manifest, hdr.new_dump_manifest, old_fmt, new_fmt
        )
        reject_debug_format_for_non_elf(debug_format, old_fmt, new_fmt)
    warn_ignored_header_inputs(
        request,
        old_fmt is not None,
        new_fmt is not None,
        headers,
        old_headers,
        new_headers,
        notify,
    )
    warn_ignored_development_packages(
        ev.old_devel_package, ev.new_devel_package, notify
    )
    note_debug_resolution(old_input, new_input, request, notify)

    # 4. Resolution. A raw --sources/--build-info tree (not a pack) is
    # collected by the shared resolution under the decisions `dump` would
    # make for it; packs go out-of-band below.
    from .inline_evidence import InlineEvidence, InlineEvidenceRun

    inline = InlineEvidence(
        ev.old_sources, ev.old_build_info, ev.new_sources, ev.new_build_info
    )
    inline_run = InlineEvidenceRun(
        on_close=host.on_close,
        notify=host.notify,
        nostdinc_explicit="nostdinc" in request.stated_options,
        compile_context=compile_context,
        old_header_backend=request.compile.old_header_backend,
        new_header_backend=request.compile.new_header_backend,
        collect_mode=collect_mode,
        depth=ev.depth,
        changed_paths=enrich.changed_paths,
        config=cfg.config,
        lang=request.compile.lang,
        debug=dataclasses.replace(ev.debug, format=debug_format),
        old_debug_roots=ev.old_debug_roots,
        new_debug_roots=ev.new_debug_roots,
    )
    # ADR-068 §3 #19: `--budget`'s deadline is ambient (the front end entered
    # it); this check closes the gap for a stored-snapshot-only pair, which
    # neither extraction nor a bounded subprocess would consult.
    try:
        check_deadline()
        resolution_request = pair_request(
            request,
            old_input,
            new_input,
            old_h,
            new_h,
            old_inc,
            new_inc,
            header_backend=header_backend,
            side_compile_context=side_compile_context,
            debug_format=debug_format,
            changed_paths=enrich.changed_paths,
        )
        resolution_request, pair = resolve_pair(
            resolution_request, lambda r: inline.apply(r, inline_run), notify
        )
    except DeadlineExceeded as exc:
        raise PairBudgetExceeded(
            exc,
            label=f"'{old_input}'/'{new_input}'",
            library=old_input.stem,
            old_version=str(old_input),
            new_version=str(new_input),
        ) from exc
    return _evaluate_and_classify(
        request,
        host,
        pair,
        inline,
        resolution_request,
        headers=(
            # Only a side this run extracts is classified under -H; a stored
            # snapshot side ignores it.
            *(headers if old_fmt or new_fmt else ()),
            *(old_headers if old_fmt else ()),
            *(new_headers if new_fmt else ()),
        ),
        abi3_floor=enrich.abi3_floor,
        old_input=old_input,
        new_input=new_input,
    )


def _evaluate_and_classify(
    request: PairSessionRequest,
    host: SessionHost,
    pair: ResolvedComparePair,
    inline: Any,
    resolution_request: CompareRequest,
    *,
    headers: tuple[Path, ...],
    abi3_floor: tuple[int, int] | None,
    old_input: Path,
    new_input: Path,
) -> PairSessionOutcome:
    """Steps 5-6: the policy inputs, then the shared classification."""
    from ..compare_classify import classify_resolved_pair
    from ..compare_configuration import resolve_force_public_scope
    from ..compare_policy import load_policy_inputs, stated_contract_mode
    from ..compare_request_policy import RequestPolicyInputs
    from ..gate import effective_gate_for_resolved_compare_config
    from ..policy_file import pending_validate_overrides_warnings
    from .invocation import build_resolved_compare_request
    from .project_inputs import compare_acknowledgments_for, post_manifest_allowlist_for

    notify = host.notify
    pol, cfg, ev = request.policy, request.project, request.evidence
    resolved_cfg = cfg.resolved
    strict_suppressions = resolved_cfg.strict_suppressions
    require_justification = resolved_cfg.require_justification
    scope_public = resolved_cfg.scope_public
    collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols

    suppression, pf = load_policy_inputs(
        pol.suppress,
        pol.policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )
    if pf is not None:
        # A policy override that downgrades a critical or otherwise-BREAKING
        # kind is exactly what `validate_overrides()` exists to flag.
        for warning in pending_validate_overrides_warnings(pf):
            notify(f"Warning: {warning}")
    # One read for both consumers -- the live overlay and the ADR-049 receipt
    # that names this file with its digest.
    force_public, symbols_list = resolve_force_public_scope(
        resolved_cfg.public_symbols, None
    )
    if force_public and not scope_public:
        notify(
            "Warning: .abicheck.yml's scope.public_symbols overlay only takes "
            "effect with header-origin scoping on (scope.public: true, the "
            "default); ignoring the widening overlay."
        )
    evaluation_config, pf, resolved_cfg = _evaluation_configuration(
        request,
        headers=headers,
        policy_file=pf,
        suppression=suppression,
        symbols_list=symbols_list,
        require_justification=require_justification,
    )
    # ADR-068 §3 #23 / ADR-049 D7: `.abicheck.yml`'s `policy.overrides` at
    # PROJECT_CONFIG tier, after the pack fold above (the typed API's order).
    pf = _fold_project_policy_overrides(pf, cfg, pol.policy, notify)

    # An out-of-band --build-info/--sources pack overrides the snapshot's
    # embedded payload per layer, attached before classification so the
    # shared depth projection caps it exactly as it caps an embedded pack
    # (the typed route's order; Codex review, PR #1020).
    pair = with_out_of_band_packs(pair, inline.out_of_band(resolution_request), notify)
    # contract.overlays.post_manifest: narrowing, so only an explicitly
    # named --config is trusted to apply it.
    post_manifest_allowlist = post_manifest_allowlist_for(
        cfg.document,
        cfg.path,
        pair.old,
        pair.new,
        config_explicit=cfg.config_explicit,
        notify=notify,
    )
    # ADR-067 D5/D6: records only from an explicit --config.
    acks = compare_acknowledgments_for(
        cfg.document,
        cfg.path,
        pf,
        config_explicit=cfg.config_explicit,
        override_path=pol.acknowledgments,
        notify=notify,
    )
    # The run's one resolved gate, read after any gate pack moved a severity
    # level: `None` exactly under the legacy scheme.
    report_severity = effective_gate_for_resolved_compare_config(resolved_cfg).severity
    # The evaluation config's resolved contract.mode, unless only the
    # built-in default chose it (ADR-049 Phase 7: that default depends on
    # evidence the comparison decides).
    resolved_contract_mode = stated_contract_mode(
        evaluation_config,
        pol.contract_mode,
        contract_evaluation=pol.contract_evaluation,
    )
    # ADR-068 D4: pattern-verdict modulation and surface metrics are
    # unconditional; ADR-020b/068 D5: `deployment:` is config-only.
    classify_request = build_resolved_compare_request(
        resolution_request,
        policy=pol.policy,
        policy_file_path=pol.policy_file_path,
        suppress=pol.suppress,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
        scope_public=scope_public,
        force_public=force_public,
        post_manifest_allowlist=post_manifest_allowlist,
        collapse_versioned_symbols=collapse_versioned_symbols,
        env_matrix=resolved_cfg.deployment,
        diagnostic_comparison=pol.diagnostic_comparison,
        require_same_generation=pol.require_same_generation,
        contract=resolved_contract_mode,
        depth=ev.depth,
        probe_matrix_old=ev.probe_matrix_old,
        probe_matrix_new=ev.probe_matrix_new,
        abi3_floor=abi3_floor,
    )
    try:
        classified = classify_resolved_pair(
            classify_request,
            pair,
            RequestPolicyInputs(suppression, pf, evaluation_config, acks),
            on_output=notify,
        )
    except ValidationError as exc:  # a one-sided --build-info probe matrix
        raise RunInputError.usage(str(exc)) from exc
    except SnapshotError as exc:  # an unloadable build-source pack: exit 1
        raise RunInputError.operational(str(exc)) from exc
    except (ProfileMismatchError, ScopeMismatchError) as exc:
        raise PairNotComparable(exc, pair.old, pair.new) from exc
    except DeadlineExceeded as exc:
        # ADR-068 §3 #19: the budget expired during classification (the
        # automatic cross-source/pattern/preprocessor scans can still run
        # `clang -E`) rather than during resolution -- same axis, same exit.
        raise PairBudgetExceeded(
            exc,
            label=f"'{pair.old.library}'",
            library=pair.old.library,
            old_version=pair.old.version,
            new_version=pair.new.version,
        ) from exc
    return PairSessionOutcome(
        result=classified.result,
        old=classified.old,
        new=classified.new,
        old_input=old_input,
        new_input=new_input,
        evaluation_config=evaluation_config,
        policy_file=pf,
        resolved_config=resolved_cfg,
        suppression=suppression,
        report_severity=report_severity,
        abi3_failure=classified.abi3_failure,
    )


# ── Policy-step helpers ─────────────────────────────────────────────────────


def _evaluation_configuration(
    request: PairSessionRequest,
    *,
    headers: tuple[Path, ...],
    policy_file: Any,
    suppression: Any,
    symbols_list: Any,
    require_justification: bool,
) -> tuple[Any, Any, Any]:
    """``(evaluation_config, policy_file, resolved_cfg)`` (ADR-049).

    *params* carries the raw option values (never merged locals, which would
    read as stated); which of them the invocation stated is
    :attr:`PairSessionRequest.stated_options`.
    """
    from ..compare.configuration import resolve_evaluation_configuration
    from ..compare_configuration import (
        ConfigurationConflictError,
        ProjectPolicyOverridesError,
    )
    from ..compare_input_adapters import typed_parameter_names

    pol, cfg = request.policy, request.project
    try:
        return resolve_evaluation_configuration(
            {
                "contract_mode": pol.contract_mode,
                "policy": pol.policy,
                "policy_file_path": pol.policy_file_path,
                "suppress": pol.suppress,
                "require_justification": require_justification,
                "severity_preset": pol.severity_preset,
                "pack_paths": pol.pack_paths,
                # The roots each side was classified under (ADR-075 D7).
                "headers": headers,
            },
            typed={n for n in typed_parameter_names() if n in request.stated_options},
            resolved_cfg=cfg.resolved,
            project_cfg=cfg.document,
            cfg_path=cfg.path,
            cfg_sha=cfg.sha256,
            config_explicit=cfg.config_explicit,
            policy=pol.policy,
            policy_file=policy_file,
            suppression=suppression,
            suppress=pol.suppress,
            symbols_list=symbols_list,
            contract_evaluation=pol.contract_evaluation,
            policy_selected_by=pol.policy_selected_by,
            policy_selected_path=pol.policy_selected_path,
            policy_selected_sha=pol.policy_selected_sha,
        )
    except ConfigurationConflictError as exc:
        raise RunInputError.usage(str(exc)) from exc
    except ProjectPolicyOverridesError as exc:
        raise RunInputError.parameter(str(exc), "--policy") from exc


def _fold_project_policy_overrides(
    policy_file: Any, cfg: Any, policy: str, notify: Notify
) -> Any:
    """``.abicheck.yml``'s ``policy.overrides`` folded at PROJECT_CONFIG tier."""
    from ..compare_configuration import (
        CompareConfigurationError,
        fold_project_policy_overrides,
        project_policy_overrides,
    )

    try:
        project_fold = fold_project_policy_overrides(
            policy_file,
            project_policy_overrides(cfg.document, cfg.path),
            base_policy=policy,
            project_path=cfg.path,
        )
    except CompareConfigurationError as exc:
        raise RunInputError.parameter(str(exc), "--policy") from exc
    for warning in project_fold.warnings:
        notify(f"Warning: {warning}")
    return project_fold.policy_file


def with_out_of_band_packs(
    pair: ResolvedComparePair, packs: Any, notify: Notify
) -> ResolvedComparePair:
    """*pair* with each side's out-of-band pack folded over its embedded payload.

    A side with no out-of-band pack is returned as-is; otherwise a shallow
    copy with only ``build_source`` rebound, so the resolved snapshot itself
    is never mutated. An unloadable pack is operational (exit 1): the
    command line was well-formed and the pack was not.
    """
    from ...buildsource.evidence_report import resolve_side_pack

    def _side(
        snap: AbiSnapshot, build_info: Path | None, sources: Path | None
    ) -> AbiSnapshot:
        if build_info is None and sources is None:
            return snap
        out = copy.copy(snap)
        try:
            out.build_source = resolve_side_pack(
                build_info, sources, snap, on_warning=notify
            )
        except SnapshotError as exc:
            raise RunInputError.operational(str(exc)) from exc
        return out

    return dataclasses.replace(
        pair,
        old=_side(pair.old, packs.old_build_info, packs.old_sources),
        new=_side(pair.new, packs.new_build_info, packs.new_sources),
    )

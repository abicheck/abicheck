# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One release member's comparison: the per-member compare primitive.

:func:`compare_member` runs one member's already-resolved
:class:`~abicheck.workflows.release_member_request.ReleaseMemberCompareRequest`
through the single Tier-2 chokepoint (``run_compare`` ->
``run_compare_request``, ADR-037 D1) and records the release's resolved
rich-tier config on the result. A directory/package ``compare`` and the
typed API therefore score a member exactly as a single-pair comparison of
the same operands does (ADR-063 "one model, any cardinality"). Operand
normalization (following GNU ld linker scripts, with its user-facing note)
stays with the frontend that owns the terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..service_compare_pipeline import run_compare
from .release_member_request import ReleaseMemberCompareRequest, run_compare_kwargs

if TYPE_CHECKING:
    from .contracts import CompareResult

__all__ = ["compare_member", "record_release_resolved_config"]


def compare_member(
    request: ReleaseMemberCompareRequest,
    pack_application: Any = None,
) -> CompareResult:
    """Compare one member's *request* and return its result.

    *request* is the release's parent request with this member's
    :class:`~abicheck.workflows.release_member_request.MemberDelta` applied;
    every one of its fields is forwarded by :func:`run_compare_kwargs`, so no
    setting the release resolved can be dropped here. That forwarding used to
    be a hand-written keyword list, and each historical gap in it
    (``include_dependencies``, the contract flags, ``--pack``, the compile
    context, ``--depth``, ``scope.public_header_dirs``, ``--exclude-header``,
    ``lang_explicit``, the deployment matrix) was a release member silently
    disagreeing with the identical single-pair comparison.

    *pack_application* is the release's resolved ``--pack`` contribution
    (a ``pack_application.PackApplication``, typed loosely because that module
    is not ADR-061-classified yet); its
    overrides are already on *request*. It is passed separately only for the
    rich-tier config record below.
    """
    result = run_compare(**run_compare_kwargs(request))
    # The rich-tier config is recorded under exactly the condition the
    # single-pair CLI (`resolve_and_apply`) and the typed API
    # (`install_resolved_gate_receipt`) record one: a contract evaluation, or
    # a pack that contributed. A plain member run stays on the documented
    # baseline tier -- stamping the no-pack application's always-resolved
    # config here made every release member report a different
    # `effective_config_digest` than the identical single-pair `compare`
    # (F2 route parity). A `.abicheck.yml` override still reaches the
    # baseline tier's `policy.overrides`, read off the scoring policy file.
    resolved_config = getattr(pack_application, "resolved_config", None)
    if not request.contract_evaluation and (
        pack_application is None or pack_application.is_empty()
    ):
        resolved_config = None
    record_release_resolved_config(result.diff, resolved_config)
    return result


def record_release_resolved_config(result: Any, config: Any) -> None:
    """``record_resolved_config``'s release-fan-out sibling: the config-merge
    half only, called from :func:`compare_member` once per library (CLI
    cleanup phase two, "PR B" effective-config parity).

    Deliberately narrower than ``record_resolved_config``: this stamps
    *config* onto *result.evaluation_config* unconditionally (so
    ``effective_config_digest``'s rich tier is reachable for a ``--pack``-
    only release run, which never builds a ``PersistedContractContext`` at
    all -- same reasoning as that function's own leading comment) and, when
    *result* does carry one (a release run given ``--contract``), merges
    *config* into it via :func:`~abicheck.contract_context.
    with_resolved_config` -- closing the same "rich tier silently
    unreachable" gap for the ``--pack`` + ``--contract`` combination, which
    ``effective_config_fields`` prefers reading off the context over the
    bare attribute whenever one exists (Codex review, fresh evidence).

    Never calls :func:`~abicheck.contract_context.with_resolved_gate`, unlike
    ``record_resolved_config``: that call needs a resolved gate config
    (exit-code scheme/severity) the release fan-out has no per-library
    equivalent of yet -- ``cli_compare_release_helpers.apply_release_gate_
    pack``'s own docstring already documents that as a separate, deferred
    "GateOptions unification" slice, not something this function should
    reach for on its own.

    Lives here, in ``workflows`` beside the release's other per-member
    stamping, now that ``contract_context`` is ``workflows``-classified and
    ``contract_evidence`` is a public root surface.

    **Preserves the context's own ``suppressions`` when *config* has none**
    (Codex review, fresh evidence): unlike single-pair `compare`'s
    ``resolve_and_apply`` (which passes the real, already-loaded
    ``SuppressionList`` as ``suppression=`` into ``resolve_cli_config``),
    ``resolve_release_pack_application(_from_ctx)`` only ever passes
    ``suppress_path=`` -- and ``_suppression_source`` returns ``None``
    whenever no already-loaded object is given, path or not. So *config*'s
    own ``suppressions`` is always ``None`` regardless of whether the
    release actually has ``--suppress`` active, while *result*'s own
    ``contract_context`` (built per library by ``service.run_compare``) DID
    resolve the real one. A plain ``with_resolved_config`` merge -- which
    replaces the observed ``resolved_config`` wholesale, preserving only the
    two overlay fields it documents -- would silently drop that real
    suppression digest/rule identities from the persisted receipt. Restoring
    it here (rather than fixing the root cause in ``resolve_release_pack_
    application``, which would mean threading an already-loaded
    ``SuppressionList`` through the release CLI's own preflight, before this
    function's caller even exists) keeps the fix local to the one place this
    PR already owns.
    """
    if config is None:
        return

    from ..contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if isinstance(ctx, PersistedContractContext):
        from dataclasses import replace

        from ..compatibility_evaluation_frontend import SUPPRESSIONS_FIELD
        from ..contract_context import with_resolved_config

        observed_config = ctx.evaluation_context.resolved_config
        if config.suppressions is None and observed_config.suppressions is not None:
            provenance = dict(config.provenance)
            observed_provenance = observed_config.provenance.get(SUPPRESSIONS_FIELD)
            if observed_provenance is not None:
                provenance[SUPPRESSIONS_FIELD] = observed_provenance
            else:
                provenance.pop(SUPPRESSIONS_FIELD, None)
            config = replace(
                config,
                suppressions=observed_config.suppressions,
                provenance=provenance,
            )
        result.contract_context = with_resolved_config(ctx, config)

    # Stamped last, from the (possibly suppression-restored) *config* above --
    # never the pre-restoration object -- so a Python API consumer reading
    # DiffResult.evaluation_config directly sees the same resolved
    # suppressions as the one merged into contract_context, rather than two
    # disagreeing "resolved" configs on the same result (Codex review, fresh
    # evidence).
    result.evaluation_config = config

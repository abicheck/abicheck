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

"""Policy-parameterised comparison -- the ``service.py`` half that needed
``PolicyFile``/``SuppressionList``.

ADR-061 Phase 4's ``service.py`` thinning slice moved ``resolve_input`` and
friends into ``workflows/input_resolution.py`` early because that half had
zero ``PolicyFile`` dependency; ``compare_snapshots``/
``load_suppression_and_policy``/``_validate_contract_mode``/
``dedup_policy_override_warnings`` stayed behind because moving them into a
real, migrated ``workflows/`` package location would have made the
destination file ``migrated_source`` -- and at the time, ``policy_file.py``
was still unclassified, so an import of it from here would have tripped
``unclassified-import`` unconditionally (unlike ``service.py``'s own flat,
``legacy_paths``-classified copy of the identical import, which
``check_architecture.py`` never checks that way).

That blocker is closed: ``abicheck/policy_file.py`` is now classified
``policy`` in ``architecture/modules.yaml`` (the ``model``-owned
``PolicyFileProtocol``/``ReclassifyRuleProtocol`` pair in
``model/policy_file_protocol.py`` resolved the one real edge that
reclassification surfaced -- ``checker_types.py``'s own ``DiffResult.
policy_file`` field type -- see that module's docstring for the mechanism
and the ADR's Phase 4 section for the full investigation this closes). This
module is these four functions' real, physical home; ``service.py``
re-exports them via a plain static import (``workflows -> workflows``, not
the forbidden ``workflows -> frontends`` shape ``render.py`` needed an
``importlib`` bridge for -- nothing this module imports reaches back to
``service.py`` or the pre-existing CLI-registration SCC, so no cycle risk
forces that mechanism here).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from ..checker import compare
from ..checker_types import DiffResult
from ..errors import ValidationError
from ..model import AbiSnapshot
from ..model.comparison_memo import comparison_memo_scope
from ..policy.public_surface_query import ComparisonPublicSurfaces, PublicSurfaceQuery
from ..policy_file import (
    dedup_validate_overrides_warnings as _dedup_validate_overrides_warnings,
    pending_validate_overrides_warnings as _pending_validate_overrides_warnings,
)
from .run_input_errors import InputRefusal, RunInputError

if TYPE_CHECKING:
    from ..environment_matrix import EnvironmentMatrix
    from ..model.acknowledgment_policy import AcknowledgmentPolicy
    from ..model.change import Change
    from ..policy.acknowledgment import AcknowledgmentList
    from ..policy.suppression.merge import SuppressPaths
    from ..policy.suppression.rules import SuppressionList
    from ..policy_file import PolicyFile

_logger = logging.getLogger(__name__)

# Codex review, PR #730: re-exported so an existing `service.
# dedup_policy_override_warnings()` caller (and this module's own tests)
# keep working. The dedup state itself lives in `policy_file.py` -- the leaf
# module both this loader and `cli_params._load_suppression_and_policy` share
# -- specifically so *one* scope dedupes a warning repeated across both
# loaders, not just repeated calls to this one. See
# `policy_file.dedup_validate_overrides_warnings`'s own docstring for what
# this does and does not cover.
dedup_policy_override_warnings = _dedup_validate_overrides_warnings


class PolicyInputError(RunInputError):
    """A suppression or policy file that cannot be loaded.

    *typed_prefix* is the label the typed API prefixes its own
    ``ValidationError`` message with (``None``: the message stands alone).
    """

    def __init__(
        self,
        message: str,
        refusal: InputRefusal,
        param_hint: str | None = None,
        typed_prefix: str | None = None,
    ) -> None:
        super().__init__(message, refusal, param_hint=param_hint)
        self.typed_prefix = typed_prefix


def load_policy_inputs(
    suppress: SuppressPaths | None,
    policy_file_path: Path | None,
    *,
    strict_suppressions: bool = False,
    require_justification: bool = False,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load the suppression files and the policy document, stating each refusal.

    The one loader both front ends share. Every refusal is a
    :class:`~abicheck.workflows.run_input_errors.RunInputError` carrying the
    loader's own message and how the CLI reports it (a
    :class:`PolicyInputError`) -- an unreadable or
    malformed file is a bad ``--suppress``/``--policy`` value (exit 64); a
    rule without the ``reason`` *require_justification* demands, an expired
    rule under *strict_suppressions*, and a policy document needing an
    uninstalled extra are operational (exit 1). The typed API words them
    its own way (:func:`load_suppression_and_policy`). Emits no warnings:
    each caller reports the policy's pending ``validate_overrides()``
    warnings through its own channel.
    """
    from ..policy.suppression.merge import load_suppression_files
    from ..policy_file import PolicyFile as _PolicyFile
    from .run_input_errors import InputRefusal

    bad_suppression = "Invalid suppression file"
    suppression: SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = load_suppression_files(
                suppress, require_justification=require_justification
            )
        except OSError as e:
            raise PolicyInputError(
                str(e), InputRefusal.PARAMETER, "--suppress", bad_suppression
            ) from e
        except ValueError as e:
            msg = str(e)
            if "no 'reason' field" in msg:
                raise PolicyInputError(
                    msg, InputRefusal.OPERATIONAL, None, bad_suppression
                ) from e
            raise PolicyInputError(
                msg, InputRefusal.PARAMETER, "--suppress", bad_suppression
            ) from e
        expired_message = strict_expiry_message(suppression, strict_suppressions)
        if expired_message:
            raise PolicyInputError(expired_message, InputRefusal.OPERATIONAL)

    pf: PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = _PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise PolicyInputError(str(e), InputRefusal.OPERATIONAL) from e
        except (ValueError, OSError) as e:
            raise PolicyInputError(
                str(e), InputRefusal.PARAMETER, "--policy", "Invalid policy file"
            ) from e
    return suppression, pf


def load_suppression_and_policy(
    suppress: SuppressPaths | None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
    *,
    strict_suppressions: bool = False,
    require_justification: bool = False,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from paths (the typed API's wording).

    *require_justification* rejects a rule with no ``reason``;
    *strict_suppressions* rejects a file holding any expired rule
    (:func:`expired_suppressions_message` names them).

    Raises:
        ValidationError: If the suppression or policy file is invalid.
    """
    try:
        suppression, pf = load_policy_inputs(
            suppress,
            policy_file_path,
            strict_suppressions=strict_suppressions,
            require_justification=require_justification,
        )
    except PolicyInputError as e:
        message = f"{e.typed_prefix}: {e}" if e.typed_prefix else str(e)
        raise ValidationError(message) from e

    if pf is not None:
        if policy != "strict_abi":
            # Named as Tier-2 *parameters*, not CLI flags: the CLI merged
            # --policy/--policy-file into one --policy that routes an operand
            # to exactly one of these, so it can no longer set both and this
            # branch is now reachable only from a typed API caller, for whom
            # the flag spellings would name nothing.
            _logger.warning(
                "policy=%r is ignored when policy_file_path is given. "
                "Set base_policy in the YAML file to override the base policy.",
                policy,
            )
        # The Tier-2 chokepoint every non-CLI-validation consumer loads its
        # policy through, none of which reach the CLI's own stderr warning
        # (Codex review). Routed through `pending_validate_overrides_warnings`
        # so a shared `dedup_policy_override_warnings()` scope collapses
        # repeats to one.
        for warning in _pending_validate_overrides_warnings(pf):
            _logger.warning("%s", warning)
    return suppression, pf


def strict_expiry_message(suppression: Any, strict: bool) -> str | None:
    """The strict-suppressions error for *suppression*, or ``None``.

    One message block per ``--suppress`` file holding an expired rule, each
    naming that file and the rule's position in it, so a run merging several
    files never blames the wrong one.
    """
    from ..policy.suppression.merge import expired_by_file

    if not strict or suppression is None:
        return None
    blocks = [
        expired_suppressions_message(rules, Path(source or "<suppressions>"))
        for source, rules in expired_by_file(suppression)
    ]
    return "\n".join(blocks) or None


def expired_suppressions_message(expired: list[tuple[int, Any]], path: Path) -> str:
    """The error a strict-suppressions run reports for *expired* rules in *path*.

    One spelling for every front end: the CLI and the typed API name each
    expired rule by its first selector, and the rule's 1-based position.
    """
    parts = [f"ERROR: {len(expired)} expired suppression rule(s) found in {path}:"]
    for idx, rule in expired:
        target = (
            rule.symbol_pattern
            and f'symbol_pattern="{rule.symbol_pattern}"'
            or rule.symbol
            and f'symbol="{rule.symbol}"'
            or rule.type_pattern
            and f'type_pattern="{rule.type_pattern}"'
            # Canonical (backend-independent) identity selector (PR #753): a
            # finding_id-only rule would otherwise render as the bare "?".
            or rule.finding_id
            and f'finding_id="{rule.finding_id}"'
            or rule.source_location
            and f'source_location="{rule.source_location}"'
            or "?"
        )
        parts.append(f"  Rule {idx + 1}: {target} expired on {rule.expires}")
    parts.append("Remove or renew expired rules before proceeding.")
    return "\n".join(parts)


def _validate_contract_mode(
    contract_mode: str | None, contract_evaluation: bool
) -> None:
    """Apply ADR-049 Phase 6's two ``contract_mode`` rules at a Tier-2 entry.

    Same allowed values and same ``contract_evaluation`` dependency as
    ``CompareRequest.validation_errors`` and the CLI's ``--contract``, so the
    three front ends cannot disagree about what is accepted.
    """
    if contract_mode is None:
        return
    from ..contract_relevance_types import ContractMode

    allowed = {mode.value for mode in ContractMode}
    if contract_mode not in allowed:
        raise ValidationError(
            f"unsupported contract mode {contract_mode!r}: "
            f"choose from {', '.join(sorted(allowed))}"
        )
    if not contract_evaluation:
        raise ValidationError(
            "contract_mode requires contract_evaluation: it selects which "
            "evidence domain the shadow contract evaluator judges against, "
            "and without that flag no contract decision is computed at all"
        )


_F = TypeVar("_F", bound=Callable[..., Any])


def _within_comparison_memo_scope(fn: _F) -> _F:
    """Open the comparison memo scope before the public-surface resolution
    below, so it shares its per-snapshot facts with ``checker.compare``
    (which joins this scope rather than opening its own)."""

    @functools.wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        with comparison_memo_scope():
            return fn(*args, **kwargs)

    return cast("_F", _wrapper)


@_within_comparison_memo_scope
def compare_snapshots(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = True,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = True,
    contract_mode: str | None = None,
    acknowledgments: AcknowledgmentList | None = None,
    acknowledgment_policy: AcknowledgmentPolicy | None = None,
) -> DiffResult:
    """Classify two already-resolved snapshots — the Tier-2 snapshot verb.

    Thin wrapper over the Tier-1 core (:func:`abicheck.checker.compare`) so that
    *front-ends never call the core directly* (ADR-037 D1/D10.1). Front-ends
    that have already resolved their own snapshots (the native ``compare``
    command with embedded build-source evidence, ``scan``, ``appcompat``) route
    through here instead of importing ``checker.compare``; the kwargs mirror the
    core verb exactly so no capability is lost.

    ADR-063 Phase 3 (D5): resolves each side's own public-surface
    ``EntityId`` set via ``PublicSurfaceQuery().resolve()`` and forwards the
    pair into :func:`~abicheck.checker.compare` as *old_public_entity_ids*/
    *new_public_entity_ids* -- ``compare()`` itself never resolves this
    (``policy/`` -> ``compare/`` stays a one-way edge), so this Tier-2
    chokepoint is where it happens for every caller that reaches ``compare()``
    through here, ``workflows.compare.execute.classify_compare_pair`` included.

    The migrated cross-source stage (ADR-068 D3/D4/D5, see
    :func:`~abicheck.checker.compare`'s own ``cross_source_checks``
    keyword) always runs here -- every front end reaching ``compare()``
    through this Tier-2 chokepoint (CLI, typed API, Action) gets it
    automatically, with no flag of any kind. Deliberately **not**
    forwarded as a parameter of this public wrapper (unlike the Tier-1
    core, which keeps it as an internal keyword purely so a test can
    isolate the stage): this function is generated into
    ``docs/reference/python-api-reference.md`` as the documented public
    Python API, so exposing a way to disable it here would be exactly the
    "flag that merely enables useful analysis" D5 rejects, reachable by
    every real caller of this module.

    Raises:
        ValidationError: *contract_mode* is not one of ``public``/``exports``/
            ``all``, or is given without *contract_evaluation*. This is a
            documented Tier-2 entry point that direct Python callers reach
            without building a ``CompareRequest``, so it applies the same two
            rules that request object and the CLI do rather than silently
            accepting a no-op or failing later inside the core (Codex review).
    """
    _validate_contract_mode(contract_mode, contract_evaluation)
    # Centralized POST committed-wrapper recovery: when a committed-surface
    # allowlist is supplied, union the callable `pp_*` wrappers exported by
    # the old snapshot (contract_scope_allowlist's snapshot half) -- keeps
    # both dropped and still-exported-but-omitted wrappers in-surface, so a
    # scope against a new manifest can't hide an ABI break via omission.
    # Every scope caller routes through here (one place, no-op with no
    # `pp_*` wrappers, idempotent if already unioned).
    if public_surface_allowlist is not None:
        from ..post_manifest import _snapshot_contract_symbols

        public_surface_allowlist = set(
            public_surface_allowlist
        ) | _snapshot_contract_symbols(old)
    # One resolution per side for the whole comparison: the entity-id sets
    # below and every later consumer inside ``compare()`` share it.
    surfaces = ComparisonPublicSurfaces()
    # Codex review, second look (PR #1154 follow-up: "Obtain ADR approval
    # before forcing verdict modulation"): the prior version of this
    # chokepoint forced `pattern_verdicts=True` unconditionally, citing
    # ADR-068 D4/D5's "no legitimate off position" principle the same way
    # `cross_source_checks` earns it above. That citation doesn't hold for
    # the Tier-2 verb: ADR-068 (Accepted, Phase 6 landed) makes pattern
    # verdicts unconditional on the CLI, but the typed Tier-2 verb is a
    # separate caller surface, and ADR-027 gates flipping the
    # `pattern_verdicts` default on FP-rate and parity validation
    # (docs/contribute/adr/027-api-surface-intelligence.md). So
    # `pattern_verdicts` stays a real, forwarded parameter here (default
    # True, matching `CompareRequest`) -- a direct caller can still score raw
    # detector output. `surface_metrics=True` is kept forced
    # (ADR-027 Phase 5's `--surface-metrics` findings; see the pending
    # follow-up to reconcile the change-catalog/doc text that still
    # describes them as request-only with this forced-on behavior).
    return compare(
        old,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        surface_metrics=True,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
        reconcile_build_context=True,
        env_matrix=env_matrix,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        old_public_entity_ids=PublicSurfaceQuery.resolve(old, surfaces.of(old)),
        new_public_entity_ids=PublicSurfaceQuery.resolve(new, surfaces.of(new)),
        public_surfaces=surfaces,
        acknowledgments=acknowledgments,
        acknowledgment_policy=acknowledgment_policy,
    )


__all__ = [
    "PolicyInputError",
    "compare_snapshots",
    "dedup_policy_override_warnings",
    "load_policy_inputs",
    "load_suppression_and_policy",
]


def stated_contract_mode(
    evaluation_config: Any, contract_mode: str | None, *, contract_evaluation: bool
) -> Any:
    """The ``contract_mode`` a front end forwards to :func:`compare_snapshots`.

    The resolved configuration's ``contract.mode`` when some layer stated it
    (a CLI flag, ``.abicheck.yml``, a pack) and contract evaluation is on;
    otherwise *contract_mode* as given. A mode only the built-in default
    chose is not forwarded: since ADR-049 Phase 7 that default is decided
    from the comparison's own evidence
    (:func:`~abicheck.policy.contract_default_mode.evidence_adaptive_contract_mode`),
    which no front end has when it resolves its configuration.
    """
    from ..policy.contract_default_mode import mode_is_built_in_default

    if (
        evaluation_config is None
        or not contract_evaluation
        or mode_is_built_in_default(evaluation_config)
    ):
        return contract_mode
    return evaluation_config.contract.mode

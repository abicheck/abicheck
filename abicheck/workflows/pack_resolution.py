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

"""Policy-pack resolution for the compare front ends.

The three decisions a front end makes about ``--pack`` before one run's
compatibility configuration can be resolved: the manifest checks that run
ahead of any comparison (:func:`validate_pack_manifests`), the best-effort
``--policy-file`` pre-read that answers whether a file already states a field
a pack would set (:func:`preread_policy_file`), and the release fan-out's
resolution of the pack's contribution (:func:`resolve_release_pack_application`).

The configuration resolver itself is
:func:`~abicheck.workflows.compare_configuration.resolve_release_pack_application`;
this module shapes a front end's parameters into its inputs. A front end
keeps only the translation of its own flags and of the typed errors into its
exit codes.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .policy_file import PolicyFile

#: The ``--policy`` default a release fan-out falls back to. The frontends
#: constant of the same value (``DEFAULT_POLICY_PROFILE``) cannot be imported
#: from ``workflows`` (ADR-061 direction); a test pins the two equal.
DEFAULT_BASE_POLICY = "strict_abi"


def preread_policy_file(policy_file_path: Path | None) -> PolicyFile | None:
    """The ``--policy-file`` loaded for the pack-shadowing check, or ``None``.

    A best-effort read purely to answer "does the real ``--policy-file``
    already state this ChangeKind override" (ADR-049 D8 precedence, mirroring
    :func:`_shadowed_inert_fields`): a broken file is not reported here -- the
    real per-library load a moment later reports it with its own framing.
    """
    if policy_file_path is None:
        return None
    import yaml

    from .policy_file import PolicyFile

    try:
        return PolicyFile.load(policy_file_path)
    except (ValueError, OSError, ImportError, yaml.YAMLError):
        return None


def _shadowed_inert_fields(policy_file_path: Any) -> frozenset[str]:
    """Which `INERT_PACK_VALUES` fields a `--policy-file` already states.

    Loaded here rather than proxied on "a path was given": a file setting
    only `base_policy` shadows nothing, and treating it as shadowing skipped
    a rejection the real run then made (Codex review). The predicate is the
    resolver's own, so this cannot disagree with `pinned_contract`.

    A file that fails to load yields "nothing shadowed" rather than raising:
    the real run loads it properly a moment later and reports that failure
    with its own `--policy-file` framing. This is also the only place that
    reads it early, and it runs only when `--pack` is selected, so no
    pre-existing invocation changes.

    Only the three failures `PolicyFile.load` documents are swallowed. A
    blanket `except Exception` here would also absorb whatever the *caller's*
    own `PackManifestError` handling is meant to see, and would hide a real
    defect in the loader behind a silently-empty answer (CodeRabbit review).
    """
    if policy_file_path is None:
        return frozenset()
    from ..compatibility_evaluation_wiring import policy_file_pins_internal_namespaces
    from .policy_file import PolicyFile

    try:
        loaded = PolicyFile.load(policy_file_path)
    except (ValueError, OSError, ImportError):
        return frozenset()
    if policy_file_pins_internal_namespaces(loaded):
        return frozenset({"surface.internal_namespaces"})
    return frozenset()


def validate_pack_manifests(
    pack_paths: Collection[Any],
    *,
    policy_file_path: Any = None,
    contract_evaluation: bool = True,
) -> None:
    """Reject an unusable ``--pack`` manifest before anything else runs.

    Called ahead of ``compare``'s ``--dry-run`` emit, where that command
    already validates every other flag combination: a dry run must not report
    "ok" for an invocation the identical real run rejects with exit 64. The
    full pack *resolution* cannot move that early -- it needs the
    ``--policy-file`` the command loads much later -- but manifest validity
    is a property of the manifests alone, so a malformed document, an unknown
    ``kind``/``ChangeKind`` slug, an unroutable field, or an assignment this
    build resolves but does not apply is answerable here (Codex and
    CodeRabbit review, both reproduced).

    Pack-vs-pack *conflict* detection deliberately stays behind: D8 exempts a
    field another layer already states, so the answer depends on layers not
    resolved yet. Checking it here would make a dry run *stricter* than the
    real run -- the same divergence in the other direction.

    Reads the manifests a second time (the resolver loads its own). That is
    inherent to validating before resolution rather than an oversight: the
    two answer different questions, and the resolver's own "one read per
    resolution" rule is about not splitting *one* resolution's identities
    across two reads, which this does not do.

    Raises :class:`~abicheck.errors.PackManifestError`; the caller maps it to
    a usage error.
    """
    if not pack_paths:
        return
    from ..pack_application import check_pack_fields_applied

    check_pack_fields_applied(
        list(pack_paths),
        shadowed_fields=_shadowed_inert_fields(policy_file_path),
        contract_evaluation=contract_evaluation,
    )


def resolve_release_pack_application(
    params: Mapping[str, Any],
    *,
    contract_evaluation: bool = False,
    **kwargs: Any,
) -> Any:
    """Resolve ``--pack`` contributions for the directory/package release fan-out.

    Returns a :class:`~abicheck.pack_application.PackApplication` -- never
    ``None`` (finding 1, round 4): with no ``--pack`` this is still where a
    project-backed ``.abicheck.yml`` ``policy.overrides`` reaches a real
    ``resolved_config`` (:func:`record_release_resolved_config`), via an
    *inert* application. ``policy_overrides``/``internal_namespaces`` thread
    into each library's own ``CompareRequest`` fields the same way, folded
    per library by :func:`~abicheck.workflows.compare.execute.classify_compare_pair`.

    Distinct from the single-pair ``workflows.compare_configuration.
    resolve_compare_configuration``, which folds the packs into *one*
    ``PolicyFile`` -- fits a single-pair
    ``compare``'s single ambient policy file, but the release fan-out
    reloads its own ``PolicyFile`` fresh per library, so this returns the
    pack's *contribution* for the caller to fold in once per library.

    **Accepts a ``kind: gate`` pack (CLI cleanup phase two, "PR B" slice 2).**
    Folds ``PackApplication``'s ``severity_levels`` (no ``exit_code_scheme``
    any more -- PR G2 deleted the manual selector) into the release fan-out's
    own resolved :class:`~abicheck.policy.release_gate_options.GateOptions`
    (``resolve_release_gate_options``, ADR-064) via ``cli_compare_release_
    helpers.apply_release_gate_pack`` -- which shares the *one*
    :func:`~abicheck.policy.gate_pack_fold.fold_gate_pack_severity` with
    :func:`~abicheck.pack_application.apply_to_compare_config` instead of
    mirroring its logic. ``scan --against`` accepts a ``kind: gate`` pack
    too (a later "PR B" slice): unlike the release fan-out, it already has a
    real ``ResolvedCompareConfig`` to fold into directly via
    ``apply_to_compare_config`` -- as the retired
    ``cli_scan._resolve_scan_evaluation_config`` did.

    **No longer rejects a pack asserting ``contract.unresolved`` (Track 2 7B
    residual, closed).** An earlier revision rejected it unconditionally --
    not merely when ``contract_evaluation`` is false, the way
    :func:`~abicheck.pack_application.check_resolved_config_applies_packs`'s
    own ``CONTRACT_EVALUATION_ONLY_FIELDS`` check does for the single-pair
    path -- while leaving *why* an open question for a future slice
    (ADR-063 Track 4's 7B ledger entry). Re-reading that question against the
    plumbing settles it: ``service.run_compare`` already creates a
    per-library ``PersistedContractContext`` when this release invocation
    passed ``--contract``, and :func:`record_release_resolved_config` (this
    module) already merges *this* function's resolved config -- pack
    contribution included -- into that context via
    ``contract_context.with_resolved_config``, read back by
    ``contract_coverage_exit._accepts_unresolved``. So a pack-asserted
    ``contract.unresolved=warn`` reaches the same consumer, through the same
    merge, that a single-pair ``compare --pack`` already goes through --
    there is no release-specific consumer or semantics to get wrong, and no
    per-library-vs-release-wide hazard the way there could be for a field
    with library-specific *content* (a symbol list, a namespace):
    ``contract.unresolved=warn`` changes nothing about evidence, labels, or
    ``GateDecision`` for any library (ADR-049 Section 6.2) -- only the
    orthogonal contract-coverage exit-floor contribution
    (``policy.contract_coverage_exit.coverage_exit_for_context``), the same
    uniform accept-incomplete-assurance decision ``policy.overrides``/
    ``surface.internal_namespaces`` already apply release-wide. A library's
    own ``contract_coverage_failures`` ledger stays untouched either way, so
    nothing is hidden; only the exit code's willingness to fail on that gap
    is. So this now applies the same ``contract_evaluation`` gate the
    single-pair path uses: a pack asserting ``contract.unresolved`` without
    ``--contract`` is still rejected as decorative, and with ``--contract``
    it is accepted and threaded through like every other pack field.

    Raises :class:`~abicheck.workflows.compare_configuration.
    ConfigurationConflictError` (a D7 same-tier conflict, a D8 pack conflict,
    an inapplicable, gate-only, or unresolved-only pack) or
    ``ProjectPolicyOverridesError``; mapping those onto exit 64 is the
    caller's job.
    """
    # Runs regardless of `pack_paths` (finding 1) -- see docstring above.
    # This function supplies the CLI's inputs only; the resolution itself is
    # the one the typed release route shares.
    from ..compatibility_evaluation_frontend import FrontEnd
    from .compare_configuration import (
        resolve_release_pack_application as _resolve_release_configuration,
    )
    from .compare_input_adapters import cli_configuration_inputs

    explicit, project = cli_configuration_inputs(params, **kwargs)
    return _resolve_release_configuration(
        explicit,
        front_end=FrontEnd.CLI,
        project=project,
        policy_file=kwargs.get("policy_file"),
        base_policy=params.get("policy") or DEFAULT_BASE_POLICY,
        contract_evaluation=contract_evaluation,
    )

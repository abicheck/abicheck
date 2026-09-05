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

"""ADR-049 Phase 5: the ``compare`` CLI's own resolved configuration.

``checker.compare`` builds a persisted contract context from the arguments it
was handed, so it can honestly claim no more than ``API_REQUEST`` for any of
them (see :mod:`abicheck.contract_context`). This module is where the
``compare`` command hands over what it -- and only it -- knows: which flags
the user really typed, which ``.abicheck.yml`` supplied a value, which
``--profile`` filled one in, and the exit-code scheme and severity levels the
run was actually scored with.

Those inputs go through Phase 1's canonical resolver
(:func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`),
and the resulting object *replaces* the core verb's narrower reconstruction.
That is the whole point of the phase: one resolver decides D7 precedence for
every front end, instead of each one patching the fields it happens to know
about after the fact.

**Values and provenance come from different places for the gate, on purpose.**
The scheme and the four severity levels are resolved by
``cli_helpers_compare.resolve_compare_config`` *before* this runs, and that
resolution is what the run's verdict and exit code were computed from -- so
those are the values a receipt must report, and they are written through
:func:`~abicheck.contract_context.with_resolved_gate` unchanged. The
canonical resolver re-derives the same two from the same inputs and is used
for their *provenance*. The two agreeing is a real, checkable claim rather
than an assumption: ``tests/test_cli_compare_config_receipt.py`` asserts it
across the input matrix, so a divergence surfaces as a failing parity test
instead of a receipt that quietly describes a different run.

Split out of :mod:`abicheck.cli_compare_helpers` when that file reached the
2000-line hard cap -- a cohesive unit (one concern, one caller) rather than
an arbitrary cut. Deliberately a **leaf**: it imports nothing from its
caller, so no cycle forms. "Which parameters did the user actually type" is
the caller's question to answer (it holds the Click context), so the answers
arrive here as data (*typed*) rather than this module reaching back for them,
and ``resolved_cfg``/``project_cfg`` stay ``Any`` for the same reason --
typing them would import the very module this one is split out of.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .frontends.cli.options.params import DEFAULT_POLICY_PROFILE

if TYPE_CHECKING:
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig

#: The ``compare`` kwargs that feed the compatibility configuration. Named
#: here so the caller forwards exactly the mapping
#: ``compare_cli_inputs`` reads, and an option renamed on one side fails
#: loudly on the other rather than resolving to "not stated".
COMPARE_CONFIG_PARAMS: tuple[str, ...] = (
    "contract_mode",
    "scope_public_headers",
    "policy",
    "policy_file_path",
    "suppress",
    "require_justification",
    "severity_preset",
    "pack_paths",
)

#: The four :class:`~abicheck.severity.SeverityConfig` categories, in the
#: spelling ``compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS``
#: keys its ``gate.severity.<category>`` provenance entries by.
_SEVERITY_CATEGORIES = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)


def typed_parameter_names() -> tuple[str, ...]:
    """The parameters the caller must answer "was this typed?" for.

    Read off the resolver's own
    :data:`~abicheck.compatibility_evaluation_frontend.DEFAULTED_COMPARE_PARAMETERS`
    rather than restated here: those are exactly the options whose click
    default (``--policy strict_abi``, ``--scope-public-headers``) is
    indistinguishable from a value the user chose, and a second copy of that
    list is a second thing to keep in sync. Every other option already
    spells "not given" as ``None``/``()``.
    """
    from .compatibility_evaluation_frontend import DEFAULTED_COMPARE_PARAMETERS

    return tuple(sorted(DEFAULTED_COMPARE_PARAMETERS))


def _suppression_source(suppression: Any, path: Any) -> Any:
    """The already-loaded ``--suppress`` list, as a resolver input.

    Built from the list the run really used rather than re-reading *path*:
    a second read could pair one content's digest with another's rules, the
    trap :meth:`SuppressionSource.from_file` documents for its own single
    read. A list with no ``source_sha256`` (``merge()`` drops it, and the
    ABICC front end constructs several without one) still selected a source,
    so it is reported as one -- the same absent-vs-empty rule
    ``contract_context.suppression_config_for`` follows.
    """
    if suppression is None:
        return None
    from .compatibility_evaluation_frontend import SuppressionSource

    return SuppressionSource(
        path=str(path) if path is not None else None,
        sha256=getattr(suppression, "source_sha256", None) or "",
        rules=tuple(suppression.rule_identities()),
    )


def _profile_inputs(run_profile: Mapping[str, Any] | None) -> Any:
    """A ``--profile``'s injected values, as a resolver input.

    *run_profile* is what ``cli_options.apply_compare_profile`` recorded:
    ``{"name": ..., "injected": {dest: value}}``. Only the keys the
    configuration actually has a field for are read; the rest of a profile
    (depth, format, ``--recommend``, ``--stat``) is execution/report surface
    with nothing to resolve here.
    """
    from .compatibility_evaluation_frontend import RunProfileInputs

    if not run_profile:
        return None
    injected = run_profile.get("injected") or {}
    # CLI cleanup phase two PR G2: `ci-gate` now injects `severity_preset`
    # (not the deleted `exit_code_scheme`) to get the identical severity-
    # aware behavior -- see `RunProfileInputs`'s own docstring.
    preset = injected.get("severity_preset")
    return RunProfileInputs(
        name=run_profile.get("name"),
        severity_preset=str(preset) if preset is not None else None,
    )


def resolve_cli_config(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any,
    project_path: Path | None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    run_profile: Mapping[str, Any] | None = None,
    policy_option: str | None = None,
    policy_path: Path | None = None,
    policy_sha256: str | None = None,
    project_sha256: str | None = None,
    symbols_list: Any = None,
) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this invocation.

    *policy_option* names the flag that selected ``policy`` when it was not
    ``--policy`` -- ``--required-symbol``/``--required-symbols``, whose
    contract switches an untouched ``--policy`` to ``plugin_abi``, with
    *policy_path*/*policy_sha256* identifying the list file when that is the
    form used. *project_sha256* is the digest of the ``.abicheck.yml`` bytes
    *project_cfg* was parsed from, so a project-supplied value names a
    revision rather than only a path.

    Raises whatever the canonical resolver raises (a D7 same-tier conflict, a
    D8 pack conflict, a malformed pack manifest); mapping those onto an exit
    code is the caller's job, as that module documents.
    """
    from .compatibility_evaluation_frontend import (
        FrontEnd,
        ProjectCompatibilityInputs,
        compare_cli_inputs,
        resolve_compatibility_evaluation_config,
    )

    # The declared contract, enforced rather than only documented: every one
    # of these keys is read below, and a caller that renamed or dropped one
    # would have it resolve silently to "not stated" -- indistinguishable
    # from a user who did not pass the flag (CodeRabbit review).
    missing = [name for name in COMPARE_CONFIG_PARAMS if name not in params]
    if missing:
        raise KeyError(
            f"compare config params missing {missing}; every name in "
            "COMPARE_CONFIG_PARAMS must be forwarded, since an absent one "
            "resolves as unstated rather than failing"
        )

    # A profile injects its values *into the command's kwargs*, so by the time
    # they reach here they are indistinguishable from typed ones -- and read
    # as `EXPLICIT_CLI`, the one layer a profile must never claim. Blanked
    # here and re-contributed at the `run_profile` tier below, which is both
    # the honest source and the precedence D7 gives it.
    injected = (run_profile or {}).get("injected") or {}
    params = {
        name: (None if name in injected else value) for name, value in params.items()
    }
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI,
        explicit=compare_cli_inputs(
            params,
            explicit_parameters=typed,
            policy_file=policy_file,
            suppression=_suppression_source(suppression, suppress_path),
            policy_base_option=policy_option,
            policy_base_path=str(policy_path) if policy_path is not None else None,
            policy_base_sha256=policy_sha256,
            public_symbols_list=symbols_list,
        ),
        project=ProjectCompatibilityInputs.from_build_config(
            project_cfg, path=project_path, sha256=project_sha256
        ),
        profile=_profile_inputs(run_profile),
    )


def dry_run_scheme_label(resolved_cfg: Any, pack_paths: Collection[Any]) -> str:
    """How ``compare --dry-run`` should describe the exit-code scheme.

    The renderer was previously handed the *raw* ``--exit-code-scheme`` value,
    so it printed "legacy (0/2/4)" whenever the flag was absent -- including
    when ``.abicheck.yml`` configured severity and the real run therefore used
    the severity scheme. That predates ``--pack`` and is wrong for the plain
    config case too, so this reports the *resolved* scheme instead (Codex
    review, reproduced against a config-only run with no pack involved).

    A selected pack is called out rather than resolved: a gate pack can move
    the scheme, but resolving one here is not safe. The configuration cannot
    be resolved before the ``--policy-file`` this command loads much later,
    and a *partial* resolution would run D8 conflict detection against
    different pins than the real one -- which can reject a pack pair the real
    run accepts. Saying the scheme may still move is honest; asserting one
    computed under different precedence would not be.
    """
    scheme = getattr(resolved_cfg, "exit_code_scheme", None)
    label = "legacy (0/2/4)" if scheme == "legacy" else (scheme or "legacy (0/2/4)")
    return f"{label}; a selected --pack may adjust it" if pack_paths else label


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
    from .compatibility_evaluation_wiring import policy_file_pins_internal_namespaces
    from .workflows.policy_file import PolicyFile

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
    from .pack_application import check_pack_fields_applied

    check_pack_fields_applied(
        list(pack_paths),
        shadowed_fields=_shadowed_inert_fields(policy_file_path),
        contract_evaluation=contract_evaluation,
    )


def resolve_and_apply(
    params: Mapping[str, Any],
    *,
    resolved_cfg: Any,
    policy: str,
    contract_evaluation: bool = False,
    **kwargs: Any,
) -> tuple[Any, Any, Any]:
    """Resolve this invocation's configuration, then let its packs configure it.

    Returns ``(config, policy_file, resolved_cfg)``: the resolved
    configuration (``None`` when nothing would read one -- see
    :func:`record_resolved_config`), and the policy file and compare config
    the comparison should actually run with.

    The selected packs are read from ``params['pack_paths']`` and nowhere
    else. An earlier revision also took them as a keyword and let that
    overwrite the ``params`` entry, so the two could silently disagree -- and
    the branches below read only the keyword, meaning a caller that filled
    ``params`` alone would have its packs *recorded in the receipt and
    applied to nothing*. That is precisely the decorative-``--pack``
    regression this module exists to prevent, so there is one source
    (CodeRabbit review).

    Both halves come from the *same* resolution, which is what makes
    ``--pack`` real rather than decorative: the resolver has already applied
    D7 precedence and D8 conflict detection, and
    :func:`~abicheck.pack_application.pack_application` reads back only the
    fields whose provenance names a pack. Nothing here decides precedence.

    The order matters and is not incidental: the configuration is resolved
    from the *explicitly given* ``policy_file`` (``kwargs['policy_file']``),
    and only then are the packs' contributions folded into a new one. Folding
    first would present a pack's override to the resolver as an explicitly
    stated ``--policy-file`` value -- outranking the packs it came from, and
    misreported in the receipt.

    Raises what the canonical resolver and the pack loader raise; mapping
    those onto exit 64 is the caller's job, as both modules document.
    """
    pack_paths = tuple(params.get("pack_paths") or ())
    if not contract_evaluation and not pack_paths:
        # Nothing would read a resolution: no context exists to record one
        # onto, and no pack can contribute to the run. Resolving anyway would
        # make every ordinary `compare` newly able to fail on a D7 conflict
        # it never previously computed.
        return None, kwargs.get("policy_file"), resolved_cfg
    config = resolve_cli_config(params, **kwargs)
    policy_file = kwargs.get("policy_file")
    if not pack_paths:
        return config, policy_file, resolved_cfg
    from .pack_application import (
        apply_to_compare_config,
        check_resolved_config_applies_packs,
        pack_application,
        policy_file_with_packs,
    )

    # Checked again here -- but against the *resolution*, not a second read of
    # the files. `validate_pack_manifests` ran much earlier (before the
    # dry-run emit) and validated whatever was on disk then; re-reading here
    # would only move that window rather than close it, since the resolver had
    # already loaded its own copy. Asking the resolved config is exact: it is
    # the revision that configures the run, by construction.
    # `contract_evaluation` is passed because a field like
    # `contract.unresolved` only has a consumer when a domain is selected --
    # accepting it otherwise would record active configuration that reads
    # back as nothing, the decorative-pack failure again (Codex review).
    check_resolved_config_applies_packs(config, contract_evaluation=contract_evaluation)

    application = pack_application(config, policy_file=policy_file)
    return (
        config,
        policy_file_with_packs(policy_file, application, base_policy=policy),
        apply_to_compare_config(resolved_cfg, application),
    )


def resolve_release_pack_application(
    params: Mapping[str, Any],
    *,
    contract_evaluation: bool = False,
    **kwargs: Any,
) -> Any:
    """Resolve ``--pack`` contributions for the directory/package release fan-out.

    Returns a :class:`~abicheck.pack_application.PackApplication` (``None``
    when no ``--pack`` was given), whose ``policy_overrides``/
    ``internal_namespaces`` the caller threads into every library's own
    ``CompareRequest.pack_policy_overrides``/``pack_internal_namespaces`` --
    :func:`~abicheck.service_compare_pipeline.classify_compare_pair` folds
    them against each library's freshly-loaded ``PolicyFile``.

    Distinct from :func:`resolve_and_apply`, which also merges the packs into
    *one* ``PolicyFile`` object and *one* ``ResolvedCompareConfig``. That fits
    a single-pair ``compare``'s single ambient policy file, but the release
    fan-out reloads its own ``PolicyFile`` fresh per library (``policy_file_
    path`` is a filesystem path, not an object shared across the run) -- so
    this returns the pack's *contribution* for the caller to fold in later,
    once per library, rather than one merged object upfront.

    **Accepts a ``kind: gate`` pack (CLI cleanup phase two, "PR B" slice 2).**
    Folds ``PackApplication``'s ``severity_levels`` (no ``exit_code_scheme``
    any more -- PR G2 deleted the manual selector, so a pack can no longer
    assign one at all) into the release fan-out's own resolved
    :class:`~abicheck.policy.release_gate_options.GateOptions`
    (``resolve_release_gate_options``, ADR-064, landed 2026-09-02) via
    ``cli_compare_release_helpers.apply_release_gate_pack`` -- which, since
    duplication-and-convergence-assessment T6, shares the *one*
    :func:`~abicheck.policy.gate_pack_fold.fold_gate_pack_severity` with
    :func:`~abicheck.pack_application.apply_to_compare_config` instead of
    mirroring its logic, leaving only the two call sites' genuinely
    different fold targets (raw strings here, a resolved ``SeverityConfig``
    there) separate. Historical account of that residual: ADR-063 Track 4's
    7B ledger entry, ``docs/_meta/one-semantic-pipeline-status.yaml``.
    ``scan --against`` accepts a ``kind: gate`` pack too (a later "PR B"
    slice): unlike the release fan-out, it already has a real
    ``ResolvedCompareConfig`` to fold into directly via
    ``apply_to_compare_config`` -- see
    ``cli_scan._resolve_scan_evaluation_config``.

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

    Raises what the canonical resolver and the pack loader raise (a D7
    same-tier conflict, a D8 pack conflict, an inapplicable, gate-only, or
    unresolved-only pack); mapping those onto exit 64 is the caller's job.
    """
    pack_paths = tuple(params.get("pack_paths") or ())
    if not pack_paths:
        return None
    config = resolve_cli_config(params, **kwargs)
    from .pack_application import (
        check_resolved_config_applies_packs,
        pack_application,
    )

    check_resolved_config_applies_packs(
        config,
        # `gate_supported` defaults to True: since CLI cleanup phase two "PR
        # B" slice 2, the release fan-out folds a `kind: gate` pack's
        # `severity.*` (no `exit_code_scheme` any more, PR G2) into its own
        # raw severity inputs (`cli_compare_release_helpers.
        # apply_release_gate_pack`) the same way `compare --pack` folds them
        # into `ResolvedCompareConfig` -- see this function's own docstring.
        # `contract_evaluation` is this release invocation's own real value
        # (whether *this* run passed `--contract`) -- the same gate the
        # single-pair path applies via `resolve_and_apply`, now that this
        # function's own docstring has confirmed there is no release-specific
        # hazard left to guard against beyond that.
        contract_evaluation=contract_evaluation,
    )
    return pack_application(config, policy_file=kwargs.get("policy_file"))


def resolve_release_pack_application_from_ctx(
    ctx: Any,
    *,
    contract_mode: str | None,
    scope_public_headers: bool,
    policy: str,
    policy_file_path: Path | None,
    suppress: Path | None,
    require_justification: bool,
    severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    contract_evaluation: bool,
    project_cfg: Any,
    project_path: Path | None,
    project_sha256: str | None,
    policy_option: str | None,
    policy_path: Path | None,
    policy_sha256: str | None,
    run_profile: Mapping[str, Any] | None = None,
) -> Any:
    """``resolve_release_pack_application``, but reading "was this typed?"
    (and a best-effort ``--policy-file`` pre-read) off the real Click
    *ctx* itself, the way ``_resolve_evaluation_config`` does for the
    single-pair path -- split out so the caller (``cli_compare_helpers.
    run_compare``, already at the AI-readiness file-size cap) stays a single
    call rather than this whole resolution inlined at the call site.

    *run_profile* is ``ctx.meta.get(cli_options.RUN_PROFILE_META_KEY)`` --
    read by the caller, not here: importing ``cli_options`` from this module
    would close a real cycle back to this file (``cli_options ->
    service_scan -> scan_engine -> cli_scan_baseline ->
    cli_compare_helpers -> cli_compare_receipt``), the exact ``import-cycle-
    growth`` regression this module's own "leaf" design avoids elsewhere.

    ``None`` when *pack_paths* is empty -- no Click/file access at all in
    that case, matching ``resolve_release_pack_application``'s own contract.
    Raises ``click.UsageError`` directly (mapping a D7 same-tier conflict, a
    D8 pack conflict, or an inapplicable/gate-only pack) rather than the raw
    resolver exceptions, so the caller does not need its own except clause.
    """
    if not pack_paths:
        return None
    import click
    import yaml

    from .compatibility_evaluation_resolver import (
        FieldResolutionError,
        PackConflictError,
    )
    from .errors import PackManifestError
    from .workflows.policy_file import PolicyFile

    # A best-effort read purely to answer "does the real --policy-file
    # already state this ChangeKind override" (D8 precedence, mirroring
    # `_shadowed_inert_fields`'s identical reasoning): a broken file is not
    # reported here -- the real per-library load a moment later (inside
    # `compare_release_cmd`) reports it with its own framing.
    loaded_policy_file = None
    if policy_file_path is not None:
        try:
            loaded_policy_file = PolicyFile.load(policy_file_path)
        except (ValueError, OSError, ImportError, yaml.YAMLError):
            loaded_policy_file = None
    typed = {
        name
        for name in typed_parameter_names()
        if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
    }
    try:
        return resolve_release_pack_application(
            {
                "contract_mode": contract_mode,
                "scope_public_headers": scope_public_headers,
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
            },
            contract_evaluation=contract_evaluation,
            typed=typed,
            project_cfg=project_cfg,
            project_path=project_path,
            policy_file=loaded_policy_file,
            suppress_path=suppress,
            run_profile=run_profile,
            policy_option=policy_option,
            policy_path=policy_path,
            policy_sha256=policy_sha256,
            project_sha256=project_sha256,
            symbols_list=None,
        )
    except (
        FieldResolutionError,
        PackConflictError,
        PackManifestError,
        # `resolve_release_pack_application`'s own `resolve_cli_config` call
        # loads `--policy`'s document a *second* time (for D7 provenance,
        # via `compatibility_evaluation_frontend`), independently of the
        # already-guarded pre-read a few lines up -- a genuinely malformed
        # policy document (an unknown ChangeKind slug, a non-mapping
        # `overrides:`, an unreadable file, or a plain YAML syntax error)
        # raises `PolicyError`/`OSError`/`ImportError`/`yaml.YAMLError` there,
        # uncaught, unlike the single-pair path (whose own
        # `_load_suppression_and_policy` call already converts the identical
        # failure to a clean error *before* ever reaching this resolver, so
        # it never hits this second load at all). Caught here instead of left
        # to propagate as a raw traceback (Codex review, found while adding
        # direct test coverage for this function; `yaml.YAMLError` added in a
        # second round once real syntax-error input was tried, not just a
        # semantically-invalid-but-well-formed document). `yaml` is safe to
        # import unconditionally here: if PyYAML itself were missing,
        # `PolicyFile.load`'s own `ImportError` would already have fired at
        # the guarded pre-read above, before this call ever runs.
        ValueError,
        OSError,
        ImportError,
        yaml.YAMLError,
    ) as exc:
        raise click.UsageError(str(exc)) from exc


def record_resolved_config(
    result: Any,
    resolved_cfg: Any,
    config: Any,
) -> None:
    """Install this front end's resolved configuration onto the context.

    A no-op unless ``--contract`` produced a context (and unless
    the caller resolved a *config* at all -- a run with neither
    ``--contract`` nor ``--pack`` resolves nothing, since nothing
    would read the result). Runs before any report is rendered, so every
    output path sees one configuration resolved by the canonical resolver
    rather than the core verb's argument-shaped reconstruction, and sees the
    gate the run was actually scored with rather than :class:`GateConfig`'s
    built-in defaults.

    *config* arrives already resolved rather than being resolved here: since
    ADR-049's ``--pack`` landed, the same object also *configures* the run
    (``pack_application``), and it has to exist before the comparison for
    that. Resolving a second time here would re-read every pack manifest --
    the "one read per resolution" rule ``resolve_compatibility_evaluation_config``
    keeps internally, for the same reason -- and would be handed the
    already-pack-folded policy file, so the receipt would report a pack's
    contribution as an explicitly stated ``--policy-file`` override.
    """
    if config is None:
        return
    # CLI cleanup phase two, PR B (Codex review, PR #803): stamped
    # unconditionally, *before* the contract_context-only branch below --
    # a `--pack`-only run (no `--contract`) never builds a
    # `PersistedContractContext` at all, so `effective_config_digest`'s
    # rich tier would otherwise be silently unreachable for it even though
    # `config` (this same object) is a real, fully-resolved
    # `CompatibilityEvaluationConfig` with real pack identities.
    result.evaluation_config = config

    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .compatibility_evaluation_frontend import SEVERITY_CATEGORY_FIELDS
    from .contract_context import with_resolved_config, with_resolved_gate

    ctx = with_resolved_config(ctx, config)
    # The values the run was scored with, with the canonical resolver's
    # provenance -- see this module's docstring for why the two halves come
    # from different resolutions, and which test holds them to agreeing.
    result.contract_context = with_resolved_gate(
        ctx,
        exit_code_scheme=resolved_cfg.exit_code_scheme,
        severity=resolved_cfg.severity,
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in _SEVERITY_CATEGORIES
        },
    )


def record_release_resolved_config(result: Any, config: Any) -> None:
    """``record_resolved_config``'s release-fan-out sibling: the config-merge
    half only, called from ``cli_compare_release._run_compare_pair`` once
    per library (CLI cleanup phase two, "PR B" effective-config parity).

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

    Lives beside ``record_resolved_config`` rather than in ``abicheck.
    workflows`` (where the rest of the release fan-out's own per-library
    stamping lives, ``cli_compare_release._run_compare_pair``'s own call
    site) because the merge needs real ``contract_context``/
    ``contract_evidence`` objects, and neither module is ADR-061-classified
    yet -- ``scripts/check_architecture.py``'s ``unclassified-import`` check
    (correctly) refuses a ``workflows/`` module importing either. This
    mirrors ``record_resolved_config``'s own home: the single-pair
    equivalent of this exact operation already lives in this same flat,
    not-yet-migrated module, for the identical reason.

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

    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if isinstance(ctx, PersistedContractContext):
        from dataclasses import replace

        from .compatibility_evaluation_frontend import SUPPRESSIONS_FIELD
        from .contract_context import with_resolved_config

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


def _release_summary_effective_config_block(
    severity_config: SeverityConfig | None,
    *,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | None = None,
    pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
) -> tuple[str, dict[str, str]]:
    """The ``(digest, fields)`` pair for a release-level *summary* document
    (the primary release JSON and ``--output-dir``'s ``summary.json``
    alike) -- narrower than a per-library sidecar's own digest, since no
    ``CompatibilityEvaluationConfig`` exists at release-summary scope at
    all, so this always resolves the *baseline* tier (see
    ``effective_config_digest.py``'s own docstring for the two tiers).

    P1 (CLI-audit): this used to compute the baseline tier from a bare,
    empty ``SimpleNamespace()`` -- carrying only *severity_config* -- so
    ``policy.base``/``policy.reclassify``/``policy.overrides``/
    ``suppressions`` all read empty regardless of the real
    ``--policy``/``--policy-file``/``--suppress`` every library was
    actually compared under, as if no policy existed at all. Every library
    shares one such input (the per-library fan-out reloads it once per
    library), so resolving it once more here the same way
    (:func:`~abicheck.frontends.cli.options.params._load_suppression_and_policy`,
    folding *pack_application* like
    :func:`~abicheck.cli_compare_release_matrix._collect_matrix_result`
    does) reproduces what any one library's own report shows for these
    fields. Reloaded rather than threaded down because no per-library
    ``PolicyFile`` is retained at this scope -- the reload runs inside the
    same ``dedup_validate_overrides_warnings()`` scope ``compare_release_cmd``
    opens, so it doesn't duplicate a warning already logged per-library.

    Called from ``cli_compare_release_helpers``/``cli_compare_release_matrix``
    -- lives here since both callers are at their own ``no_growth`` cap.

    *scope_public_headers* (found by a generalized parity test, PR #1016,
    once the ``policy.base`` fix above showed the class was worth searching
    for systematically): the same bug shape as ``policy`` above, just for a
    second field. ``effective_config_fields_from_diff_result`` reads
    ``result.scope_to_public_surface``/``.scope_to_public_surface_requested``
    off whatever it's given; a bare ``SimpleNamespace`` that never sets them
    falls back to that function's own ``getattr(..., default)`` -- ``False``/
    ``True`` respectively -- regardless of what ``--scope-public-headers``/
    ``--no-scope-public-headers`` actually resolved to for this run, exactly
    as ``policy``/``policy_file`` did before P1. The release fan-out has no
    ``--post-manifest``/forced-public-symbols concept of its own (unlike a
    single-pair ``compare``, where ``scope_to_public_surface`` can diverge
    from ``scope_to_public_surface_requested`` when a forced-public-symbols
    allowlist is active), so both fields are simply the raw CLI value here.
    """
    from types import SimpleNamespace

    from .contract_context import suppression_config_for
    from .effective_config_digest import (
        effective_config_digest,
        effective_config_fields,
    )
    from .frontends.cli.options.params import _load_suppression_and_policy
    from .workflows.gate import gate_exit_code_scheme

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if pack_application is not None:
        from .pack_application import policy_file_with_packs

        pf = policy_file_with_packs(pf, pack_application, base_policy=policy)
    suppression_config = suppression_config_for(suppression)
    # `pf.base_policy`, not the raw `policy` argument, when a `--policy-file`
    # resolved one (Codex review, PR #1016): `checker.compare`'s own
    # `effective_policy = policy_file.base_policy if policy_file is not None
    # else policy` is what a real per-library report's `policy.base` field
    # reflects, so a policy document naming a non-default `base_policy:`
    # (e.g. `sdk_vendor`) produced a release-summary digest still reading
    # the CLI default (`strict_abi`) while every per-library report agreed
    # on the real base -- this stand-in must resolve the identical way.
    effective_policy = pf.base_policy if pf is not None else policy
    ec_result = SimpleNamespace(
        policy=effective_policy,
        policy_file=pf,
        suppression_source_sha256=(
            suppression_config.sha256 if suppression_config is not None else None
        ),
        scope_to_public_surface=scope_public_headers,
        scope_to_public_surface_requested=scope_public_headers,
    )
    ec_scheme = gate_exit_code_scheme(severity_config is not None)
    ec_fields = effective_config_fields(
        ec_result, severity_config=severity_config, exit_code_scheme=ec_scheme
    )
    return effective_config_digest(ec_fields), ec_fields


def _release_md_library_findings(library_results: list[dict[str, object]]) -> list[str]:
    """Per-library findings (kind/symbol/description) -- symbol names
    included. R2 (CLI-audit): the release Markdown report's own
    ``## Libraries`` table is counts only; reuses ``entry["findings"]`` --
    the same capped list ``cli_compare_release_matrix._release_finding_dicts``
    already projects, built regardless of output format -- mirroring
    ``cli_compare_release_helpers``'s existing per-finding sections
    (``_release_md_bundle_findings``/``_release_md_matrix_findings``, which
    call this). Notes a truncated list (``entry["findings_truncated"]``)
    rather than presenting it as complete. Lives here (not next to its
    callers) for the identical reason as
    :func:`_release_summary_effective_config_block` above -- both
    ``cli_compare_release_helpers.py``/``cli_compare_release_matrix.py``
    are at their own ``no_growth`` cap.

    **Not routed through ``report/`` (Codex review, PR #1016), checked
    rather than assumed fine:** AGENTS.md's task-routing table says a new
    output-format section belongs in ``report/``, and this genuinely is one.
    Two things stop a clean move today, though -- this function's own real
    siblings, not just this one: neither ``_release_md_bundle_findings`` nor
    ``_release_md_matrix_findings`` (the two existing per-finding sections
    this mirrors) lives in ``report/`` either (``cli_compare_release_helpers.py``/
    ``bundle.py``), so this is an already-unmigrated neighborhood, not one
    function breaking an otherwise-followed rule. More fundamentally, this
    function's input is *not* the shape ``report/``'s ``compute_*``/``render_*``
    split is built around: every per-library ``DiffResult`` is deliberately
    discarded before this ever runs (``cli_compare_release_matrix.
    _strip_diff_results_and_adjust_verdict``, to bound peak memory across a
    whole release run's ``library_results``), so this reads an already-
    flattened ``list[dict]`` projection, not a live ``DiffResult``/
    ``ReportDocument``. Moving only the rendering half into ``report/``
    while leaving that flattening decision here would be a new, unreviewed
    input shape for the package to accept, not a mechanical relocation --
    real, separately-justified follow-up work (migrating this whole
    three-function neighborhood together, once its memory-lifecycle
    constraint has a place in that package's own model), not a same-PR fix.
    """
    lines: list[str] = []
    for lib in library_results:
        findings = lib.get("findings")
        if not findings:
            continue
        lines += ["", f"### `{lib['library']}` Findings", ""]
        for f in cast(list[dict[str, object]], findings):
            symbol = f.get("symbol")
            lines.append(
                f"- **{f.get('kind')}**" + (f" — `{symbol}`" if symbol else "")
            )
            description = f.get("description")
            if description:
                lines.append(f"  - {description}")
        if lib.get("findings_truncated"):
            # `--format json` is *not* a complete-list source (Codex review,
            # PR #1016): the release JSON's own `findings` field is this
            # same `_MAX_RELEASE_FINDINGS_PER_LIBRARY`-capped projection --
            # a reader following that advice would see the identical
            # truncated list again. `--output-dir` is the only source that
            # writes each library's real, uncapped `DiffResult` in the same
            # shape (a per-library `compare` re-run is the other option).
            lines.append(
                "  - _...additional findings omitted; see `--output-dir` "
                "(or compare this library individually) for the complete "
                "list._"
            )
    return ["", "## Per-Library Findings", *lines] if lines else []

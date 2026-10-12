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
the user really typed, which ``.abicheck.yml`` supplied a value, and the
exit-code scheme and severity levels the run was actually scored with.

Those inputs go through Phase 1's canonical resolver
(:func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`),
and the resulting object *replaces* the core verb's narrower reconstruction.
That is the whole point of the phase: one resolver decides D7 precedence for
every front end, instead of each one patching the fields it happens to know
about after the fact.

**Values and provenance come from different places for the gate, on purpose.**
The scheme and the four severity levels are resolved by
``workflows.compare_configuration.resolve_compare_config`` *before* this runs, and that
resolution is what the run's verdict and exit code were computed from -- so
those are the values a receipt must report, and they are written through
:func:`~abicheck.contract_context.with_resolved_gate` unchanged. The
canonical resolver re-derives the same two from the same inputs and is used
for their *provenance*. The two agreeing is a real, checkable claim rather
than an assumption: ``tests/test_cli_compare_config_receipt.py`` asserts it
across the input matrix.

Split out of :mod:`abicheck.cli_compare_helpers` when that file reached the
2000-line hard cap -- a cohesive unit (one concern, one caller), not an
arbitrary cut. Deliberately a **leaf**: it imports nothing from its caller,
so no cycle forms; answers arrive here as data (*typed*), and
``resolved_cfg``/``project_cfg`` stay ``Any`` since typing them would
import the very module this one is split out of.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    "policy",
    "policy_file_path",
    "suppress",
    "require_justification",
    "severity_preset",
    "pack_paths",
    # ADR-075 D7: the -H roots the snapshots were classified under.
    "headers",
)


def typed_parameter_names() -> tuple[str, ...]:
    """The parameters the caller must answer "was this typed?" for.

    Read off the resolver's own
    :data:`~abicheck.compatibility_evaluation_frontend.DEFAULTED_COMPARE_PARAMETERS`
    rather than restated here: those are exactly the options whose click
    default (``--policy strict_abi``) is
    indistinguishable from a value the user chose, and a second copy of that
    list is a second thing to keep in sync. Every other option already
    spells "not given" as ``None``/``()``.
    """
    from .compatibility_evaluation_frontend import DEFAULTED_COMPARE_PARAMETERS

    return tuple(sorted(DEFAULTED_COMPARE_PARAMETERS))


def _suppression_source(suppression: Any, path: Any) -> Any:
    """The already-loaded ``--suppress`` list, as a resolver input.

    Built from the list the run really used rather than re-reading *path*
    (a second read could pair one content's digest with another's rules,
    the trap :meth:`SuppressionSource.from_file` documents). A list with no
    ``source_sha256`` still selected a source, so it is reported as one --
    the same absent-vs-empty rule ``contract_context.
    suppression_config_for`` follows."""
    if suppression is None:
        return None
    from .compatibility_evaluation_frontend import SuppressionSource

    return SuppressionSource(
        path=str(path) if path is not None else None,
        sha256=getattr(suppression, "source_sha256", None) or "",
        rules=tuple(suppression.rule_identities()),
    )


def cli_configuration_inputs(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any,
    project_path: Path | None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | None = None,
    policy_option: str | None = None,
    policy_path: Path | None = None,
    policy_sha256: str | None = None,
    project_sha256: str | None = None,
    symbols_list: Any = None,
    project_overlays_applied: bool = False,
) -> tuple[Any, Any]:
    """This invocation's ``(explicit, project)`` resolver inputs.

    *typed* names the parameters the user really typed (the CLI answers that
    from Click's ``ParameterSource`` at its own call site and hands it over as
    data -- the same stated-settings set a typed caller puts in
    ``CompareRequest.stated_settings``).
    *policy_option* names the flag that selected ``policy`` when it was not
    ``--policy`` -- ``--required-symbol``, whose contract switches an
    untouched ``--policy`` to ``plugin_abi``, with *policy_path*/
    *policy_sha256* identifying the ``@FILE`` form's list file when used.
    *project_sha256* is the digest of the ``.abicheck.yml`` bytes
    *project_cfg* was parsed from, naming a revision, not only a path.
    *project_overlays_applied*: this route applies the config's
    ``contract.overlays`` (a single-pair compare under an explicit
    ``--config``), so the receipt states them at ``project_config`` tier.
    """
    from .compatibility_evaluation_frontend import (
        ProjectCompatibilityInputs,
        compare_cli_inputs,
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
    explicit = compare_cli_inputs(
        params,
        explicit_parameters=typed,
        policy_file=policy_file,
        suppression=_suppression_source(suppression, suppress_path),
        policy_base_option=policy_option,
        policy_base_path=str(policy_path) if policy_path is not None else None,
        policy_base_sha256=policy_sha256,
        public_symbols_list=symbols_list,
    )
    project = ProjectCompatibilityInputs.from_build_config(
        project_cfg,
        path=project_path,
        sha256=project_sha256,
        overlays_applied=project_overlays_applied,
    )
    return explicit, project


def resolve_cli_config(params: Mapping[str, Any], **kwargs: Any) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this invocation.

    :func:`cli_configuration_inputs` through the canonical resolver. Raises
    whatever that resolver raises (a D7 same-tier conflict, a D8 pack
    conflict, a malformed pack manifest); mapping those onto an exit code is
    the caller's job, as that module documents.
    """
    from .compatibility_evaluation_frontend import (
        FrontEnd,
        resolve_compatibility_evaluation_config,
    )

    explicit, project = cli_configuration_inputs(params, **kwargs)
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI, explicit=explicit, project=project
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
    per library by :func:`~abicheck.service_compare_pipeline.classify_compare_pair`.

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

    Raises what the canonical resolver and the pack loader raise (a D7
    same-tier conflict, a D8 pack conflict, an inapplicable, gate-only, or
    unresolved-only pack); mapping those onto exit 64 is the caller's job.
    """
    pack_paths = tuple(params.get("pack_paths") or ())
    # Runs regardless of `pack_paths` (finding 1) -- see docstring above.
    config = resolve_cli_config(params, **kwargs)
    from .pack_application import (
        PackApplication,
        check_resolved_config_applies_packs,
        pack_application,
    )

    if not pack_paths:
        return PackApplication(policy_overrides={}, resolved_config=config)
    check_resolved_config_applies_packs(
        config,
        # A `kind: gate` pack needs no check here: since CLI cleanup phase two
        # "PR B" slice 2, the release fan-out folds its `severity.*` (no
        # `exit_code_scheme` any more, PR G2) into its own raw severity inputs
        # (`cli_compare_release_helpers.apply_release_gate_pack`) the same way
        # `compare --pack` folds them into `ResolvedCompareConfig` -- see this
        # function's own docstring.
        # `contract_evaluation` is this release invocation's own real value
        # (whether *this* run passed `--contract`) -- the same gate the
        # single-pair path applies via `resolve_compare_configuration`, now that this
        # function's own docstring has confirmed there is no release-specific
        # hazard left to guard against beyond that.
        contract_evaluation=contract_evaluation,
    )
    return pack_application(config, policy_file=kwargs.get("policy_file"))


def resolve_release_pack_application_from_ctx(
    ctx: Any,
    *,
    contract_mode: str | None,
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
    headers: tuple[Path, ...] = (),
) -> Any:
    """``resolve_release_pack_application``, but reading "was this typed?"
    (and a best-effort ``--policy-file`` pre-read) off the real Click *ctx*
    itself, the way ``_resolve_evaluation_config`` does for the single-pair
    path -- split out so the caller stays a single call rather than this
    whole resolution inlined at the call site.

    Resolved even when *pack_paths* is empty (finding 1, round 4) -- see
    ``resolve_release_pack_application``'s own docstring. Raises
    ``click.UsageError`` rather than the raw resolver exceptions, so the
    caller needs no except.
    """
    import click
    import yaml

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
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
                "headers": headers,
            },
            contract_evaluation=contract_evaluation,
            typed=typed,
            project_cfg=project_cfg,
            project_path=project_path,
            policy_file=loaded_policy_file,
            suppress_path=suppress,
            policy_option=policy_option,
            policy_path=policy_path,
            policy_sha256=policy_sha256,
            project_sha256=project_sha256,
            symbols_list=None,
        )
    except (
        # A D7 same-tier / D8 pack conflict is a `ValueError` subclass of the
        # resolver's own, covered by the `ValueError` arm below.
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
    *,
    project_config_path: Any = None,
    project_config_sha256: str | None = None,
    require_complete_analysis_stated: bool = False,
) -> None:
    """Install this front end's resolved configuration onto the context.

    *project_config_path*/*project_config_sha256*/*require_complete_
    analysis_stated* are forwarded, not re-derived, to :func:`~abicheck.
    contract_context.with_resolved_gate` -- see its own docstring.

    A no-op unless ``--contract`` produced a context (and unless the caller
    resolved a *config* at all). Runs before any report is rendered, so
    every output path sees one configuration resolved by the canonical
    resolver rather than the core verb's argument-shaped reconstruction,
    and sees the gate the run was actually scored with rather than
    :class:`GateConfig`'s built-in defaults.

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
            for category in SEVERITY_CATEGORY_FIELDS
        },
        require_complete_analysis=resolved_cfg.require_complete_analysis,
        require_complete_analysis_stated=require_complete_analysis_stated,
        project_config_path=str(project_config_path) if project_config_path else None,
        project_config_sha256=project_config_sha256,
    )


def _release_summary_effective_config_block(
    severity_config: SeverityConfig | None,
    *,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | None = None,
    pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
    on_incomplete_scope: str = "",
    fail_on_removed_library: bool | None = None,
    require_complete_analysis: bool = False,
    env_matrix_source_sha256: str | None = None,
    excluded_header_patterns: str = "",
) -> tuple[str, dict[str, str]]:
    """The ``(digest, fields)`` pair for a release-level *summary* document
    (the primary release JSON and a per-component export's ``summary.json``
    alike) -- narrower than a per-library sidecar's own digest: this always
    resolves the *baseline* tier's field *shape* (see
    ``effective_config_digest.py``'s docstring for the two tiers), even
    though ``pack_application.resolved_config`` is a real config since round
    4's fix -- ``policy.overrides`` reads straight from it (round 6 finding
    1) rather than switching the whole receipt to the rich tier, which would
    move every other field and the pinned ``_tier`` for no reason this needs.

    P1 (CLI-audit): this used to compute the baseline tier from a bare,
    empty ``SimpleNamespace()`` -- carrying only *severity_config* -- so
    ``policy.base``/``policy.reclassify``/``policy.overrides``/
    ``suppressions`` all read empty regardless of the real
    ``--policy``/``--policy-file``/``--suppress`` every library was
    actually compared under. Every library shares one such input, so
    resolving it once more here the same way
    (:func:`~abicheck.frontends.cli.options.params._load_suppression_and_policy`,
    folding *pack_application* like
    :func:`~abicheck.cli_compare_release_matrix._collect_matrix_result`
    does) reproduces what any one library's own report shows for these
    fields; reloaded rather than threaded down since no per-library
    ``PolicyFile`` is retained at this scope (the reload runs inside the
    same ``dedup_validate_overrides_warnings()`` scope ``compare_release_cmd``
    opens, so it doesn't duplicate a per-library warning).

    Called from ``frontends.cli.release_report``/``cli_compare_release_matrix``/
    ``frontends.cli.release_summary`` (all three at their own ``no_growth``
    cap, hence living here).

    *scope_public_headers* (PR #1016): same bug shape as ``policy`` above,
    for a second field -- ``effective_config_fields_from_diff_result`` reads
    ``result.scope_to_public_surface``/``.scope_to_public_surface_requested``
    off whatever it's given, so a bare unset ``SimpleNamespace`` would fall
    back to ``False``/``True`` regardless of what ``scope.public``
    actually resolved to. The release fan-out has no ``--post-manifest``/
    forced-public-symbols concept of its own (unlike single-pair ``compare``,
    where the two can diverge), so both fields are simply the resolved
    value here.

    *excluded_header_patterns* is the release's canonical ``--exclude-header``
    identity (``model.header_exclusion_record.canonical_exclusion_identity``
    of the resolved rules), computed once at release scope for the same
    reason *env_matrix_source_sha256* below is.

    *env_matrix_source_sha256* (Codex review, P2 follow-up): the release-
    wide deployment-floor digest, computed once by the caller at release
    scope from the resolved ``EnvironmentMatrix`` -- not read off any
    per-library ``DiffResult`` (none exists at this scope), so a release
    with zero matched/completed pairs still reports a real ``policy.
    env_matrix`` rather than leaving it empty.
    """
    from types import SimpleNamespace

    from .contract_context import suppression_config_for
    from .effective_config_digest import (
        effective_config_digest,
        release_summary_fields,
    )
    from .frontends.cli.options.params import _load_suppression_and_policy
    from .workflows.gate import EffectiveGate

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if pack_application is not None:
        from dataclasses import replace as _replace

        from .pack_application import policy_file_with_packs
        from .workflows.policy_file import PolicyFile

        pf = policy_file_with_packs(pf, pack_application, base_policy=policy)
        # Round 6 finding 1: fold above carries only the pack's own overrides -- resolved_config.policy.overrides is the one canonical D7 merge (file+pack+project); read it directly.
        rc = getattr(pack_application.resolved_config, "policy", None)
        rc_overrides = getattr(rc, "overrides", None)
        if rc_overrides is not None:
            pf = (
                _replace(pf, overrides=dict(rc_overrides))
                if pf
                else PolicyFile(base_policy=policy, overrides=dict(rc_overrides))
            )
    suppression_config = suppression_config_for(suppression)
    # `pf.base_policy`, not the raw `policy` argument, when a `--policy-file` resolved one (Codex review, PR #1016): `checker.compare`'s own `effective_policy = policy_file.base_policy if policy_file is not None else policy` is what a real per-library report's `policy.base` field reflects, so a policy document naming a non-default `base_policy:` (e.g. `sdk_vendor`) produced a release-summary digest still reading the CLI default (`strict_abi`) while every per-library report agreed on the real base -- this stand-in must resolve the identical way.
    effective_policy = pf.base_policy if pf is not None else policy
    ec_result = SimpleNamespace(
        policy=effective_policy,
        policy_file=pf,
        suppression_source_sha256=(
            suppression_config.sha256 if suppression_config is not None else None
        ),
        scope_to_public_surface=scope_public_headers,
        scope_to_public_surface_requested=scope_public_headers,
        # Codex review, P2 follow-up: feeds `policy.env_matrix` below.
        env_matrix_source_sha256=env_matrix_source_sha256,
        # Feeds `surface.exclude_headers`; release-wide for the same reason
        # `env_matrix_source_sha256` above is (no per-library DiffResult
        # survives to this scope, and a release that matched nothing must
        # still report the rules it would have applied).
        excluded_header_patterns=excluded_header_patterns,
        # ADR-068 D4/Phase 5 + §4.1's AUTO rows: modulation, surface metrics and ADR-039 reconciliation are unconditional now (forced on at the Tier-2 chokepoint every library here routes through), so this stand-in must agree rather than default to the old "off".
        pattern_verdicts_enabled=True,
        surface_metrics_enabled=True,
        reconcile_build_context_enabled=True,
    )
    # No result/scope at this release-summary scope, but require_complete_analysis/on_incomplete_scope/fail_on_removed_library ARE (Codex review, PR #1192 fourth round for the latter two; PR #1238 for the first, once `assurance.require_complete` started applying to a release at all) -- read from `gate.*` below, not the now-removed `ec_result.on_incomplete_scope` this used to set instead.
    gate = EffectiveGate.from_severity(
        severity_config,
        require_complete_analysis=require_complete_analysis,
        on_incomplete_scope=on_incomplete_scope or None,
        fail_on_removed_library=fail_on_removed_library,
    )
    resolved = getattr(pack_application, "resolved_config", None)
    ec_fields = release_summary_fields(resolved, ec_result, policy_file=pf, gate=gate)
    return effective_config_digest(ec_fields), ec_fields

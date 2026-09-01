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

"""ADR-049 Phase 5: turning a selected pack into something the engine runs.

Phase 1 built pack *manifests* (``compatibility_evaluation_packs.py``), the
D8 conflict rule (``compatibility_evaluation_resolver.detect_pack_conflicts``),
and the wiring that folds a selected pack into one resolved
:class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
(``compatibility_evaluation_wiring.py``). What none of that does is *apply* the
result: a resolved configuration is a receipt, and the comparison itself is
scored from a :class:`~abicheck.policy_file.PolicyFile` and a
:class:`~abicheck.severity.SeverityConfig` that nothing was folding a pack into.

A ``--pack`` flag was written and removed once before merge for exactly that
reason (recorded in the plan's Phase 5 section): it reached the receipt and
never the verdict, so a ``kind: policy`` pack overriding ``func_removed``
was reported as active configuration while the exit code ignored it.
**Exposing configuration that does not configure is worse than not exposing
it**, so this module exists before the flag came back.

Two rules keep it honest:

**1. The applied value is read off the resolved configuration, never
re-derived.** :func:`pack_application` takes the object the canonical
resolver already produced and asks, per field, whether *a pack* supplied the
value -- which is a question the resolver already answered, in
``ValueProvenance.source_kind == "pack_manifest"``. Nothing here re-implements
D7 precedence or D8 conflict detection, so a front end cannot apply a pack
value the resolver would have ruled out: if ``--policy-file``, ``--exit-code-
scheme``, a ``--profile``, or ``.abicheck.yml`` stated the field, its
provenance names *that* source and this module contributes nothing for it.

**2. A pack may only assign a field this build actually applies.**
:data:`UNAPPLIED_PACK_FIELDS` names the routable fields with no engine
consumer yet, each with the reason, and
:func:`check_resolved_config_applies_packs` rejects a resolution carrying
one (:func:`check_pack_fields_applied` asks the same of the files, early
enough that ``--dry-run`` agrees). That is the same posture the manifest
loader already takes toward an unknown key -- the alternative is a pack
whose assignment lands in the receipt and changes nothing, which is the
exact failure this module was written to prevent. Wiring a field's consumer
is what removes it from that mapping, so the registry doubles as the list of
what is left.

A leaf: nothing here imports a ``cli*`` module, and its two consumers
(``cli_compare_helpers``/``cli_scan``) pass what they already loaded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .change_registry_types import Verdict
from .checker_policy import ChangeKind
from .compatibility_evaluation_frontend import (
    EXIT_CODE_SCHEME_FIELD,
    SEVERITY_CATEGORY_FIELDS,
)
from .compatibility_evaluation_packs import PackKind
from .compatibility_evaluation_wiring import (
    INTERNAL_NAMESPACES_FIELD,
    load_selected_packs,
)
from .errors import PackManifestError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .policy_file import PolicyFile

#: ``ValueProvenance.source_kind`` the resolver stamps on a field a selected
#: pack supplied. Imported rather than restated wherever possible; spelled
#: here because the resolver writes it as a literal at its own two sites.
PACK_SOURCE_KIND = "pack_manifest"

#: Routable pack fields this build resolves but does **not** apply, and why.
#: A manifest assigning one is rejected by :func:`check_pack_fields_applied`
#: rather than accepted into a receipt it cannot act on.
#:
#: Every entry is a field ``compatibility_evaluation_wiring``'s
#: ``CONTRACT_PACK_FIELD_ROUTES``/``GATE_PACK_FIELD_ROUTES`` accepts, so the
#: two together partition the routable vocabulary into "applied" and "not
#: yet" -- :func:`applied_pack_fields` derives the first half from the second
#: rather than keeping a second hand-maintained list.
#:
#: ``contract.unresolved`` left this mapping in Phase 7, when the coverage
#: exit it configures became real (``contract_coverage_exit.py``). It is
#: also the one applied field :func:`pack_application` does *not* fold into
#: anything: its consumer reads it straight off the resolved configuration
#: the receipt already persists, so there is no legacy object to graft it
#: onto -- which is what "applied" means for a field the engine learned to
#: read natively, as opposed to one translated into a ``PolicyFile``.
UNAPPLIED_PACK_FIELDS: Mapping[str, str] = {
    "contract.overlays": (
        "the public domain's overlays come from --post-manifest and "
        ".abicheck.yml's scope.public_symbols, which name concrete inputs; a "
        "pack naming an overlay kind has nothing to point those at"
    ),
    "assurance.require_evidence": (
        "PolicyFile.require_evidence is a per-layer mapping the compare "
        "evidence pipeline enforces, not the single bool this field resolves to"
    ),
}


#: Field values that route and resolve but are inert at runtime, and why.
#: Narrower than :data:`UNAPPLIED_PACK_FIELDS`: the field *is* applied, just
#: not with this particular value, so only the value is rejected.
#:
#: ``surface.internal_namespaces: []`` is a *statement* ("this project has
#: none") that `PolicyFile.internal_namespaces_stated` carries into the
#: provenance receipt -- but no further. ``checker._run_post_processing``
#: turns an empty list into ``None`` and the pipeline steps then fall back to
#: ``DEFAULT_INTERNAL_NAMESPACES``, so `detail`/`impl`/... keep applying and
#: the pack changed nothing (Codex review). That collapse is *pre-existing*
#: and shared with a `--policy-file` writing the same empty list, so honoring
#: stated-empty means changing that shared runtime semantic -- its own scoped
#: change, with its own FP-rate re-verification. Rejecting the inert value
#: here keeps this module's own rule true in the meantime, and is what should
#: be deleted once the runtime learns to honor it.
INERT_PACK_VALUES: Mapping[str, tuple[object, str]] = {
    "surface.internal_namespaces": (
        (),
        "an empty internal-namespace set is not honored at runtime: "
        "post-processing treats it as unset and falls back to the built-in "
        "default namespaces, so the assignment would change nothing",
    ),
}


def _resolved_field(config: Any, field_name: str) -> object:
    """The resolved value of a dotted *field_name* on *config*."""
    namespace, _, attr = field_name.partition(".")
    return getattr(getattr(config, namespace, None), attr, None)


def _supplying_pack(config: Any, field_name: str) -> str:
    """The manifest path (or identity) that supplied *field_name*.

    Read off the same provenance entry :func:`_pack_supplied` consults, so a
    rejection names *which* `--pack` is at fault when several are selected
    (CodeRabbit review). Falls back to a generic phrase rather than inventing
    a path when the entry carries none.
    """
    provenance = getattr(config, "provenance", {}).get(field_name)
    return (
        getattr(provenance, "path", None)
        or getattr(provenance, "reference", None)
        or "a selected pack"
    )


def _inert_value_reason(field_name: str, value: object) -> str | None:
    """Why *value* is inert for *field_name*, or ``None`` if it applies."""
    entry = INERT_PACK_VALUES.get(field_name)
    if entry is not None and value == entry[0]:
        return entry[1]
    return None


# The two rejections below are each raised from two places -- once against the
# files (early, so `--dry-run` agrees) and once against the resolution (the
# authoritative answer). Only the *source token* differs: a manifest path
# there, the provenance-named pack here. Written out twice, an edit to one
# wording would leave `--dry-run` and the real run explaining the same
# condition differently, which is the "two things that can disagree" shape
# this module's own rules exist to avoid (CodeRabbit review).
def _unapplied_field_error(
    source: str, field_name: str, reason: str
) -> PackManifestError:
    """The rejection for a routable field with no engine consumer."""
    return PackManifestError(
        f"{source}: {field_name!r} is resolvable but not applied by this "
        f"build ({reason}). A pack assignment that changes nothing is "
        "rejected rather than recorded as active configuration."
    )


def _needs_contract_evaluation_error(
    source: str, field_name: str, reason: str
) -> PackManifestError:
    """The rejection for a field this *invocation* enabled no consumer for."""
    return PackManifestError(
        f"{source}: {field_name!r} needs --contract to have any "
        f"effect ({reason}). Add it, or drop the assignment -- a pack "
        "recorded as active configuration while changing nothing is the "
        "failure this check exists to prevent."
    )


def _inert_value_error(source: str, field_name: str, reason: str) -> PackManifestError:
    """The rejection for an applied field assigned a value nothing acts on."""
    return PackManifestError(
        f"{source}: {field_name!r} is assigned a value this build does not "
        f"act on ({reason}). Rejected rather than recorded as active "
        "configuration."
    )


def applied_pack_fields(kind: PackKind) -> frozenset[str]:
    """The fields a *kind* pack may assign and this build really applies."""
    from .compatibility_evaluation_wiring import PACK_FIELD_ROUTES_BY_KIND

    routes = PACK_FIELD_ROUTES_BY_KIND.get(kind, {})
    return frozenset(routes) - frozenset(UNAPPLIED_PACK_FIELDS)


@dataclass(frozen=True)
class PackApplication:
    """What the selected packs, and only they, contribute to this run.

    Every attribute is ``None``/empty unless a pack actually supplied the
    value, so an application built from a run with no ``--pack`` is inert by
    construction -- the property the "no behaviour change without a pack"
    tests assert directly rather than by sampling outputs.
    """

    #: ``ChangeKind -> Verdict`` a policy pack contributed, already excluding
    #: every kind an explicit ``--policy-file`` states (D8: explicit override
    #: > selected packs > base policy).
    policy_overrides: Mapping[ChangeKind, Verdict]
    #: ``surface.internal_namespaces`` when a contract pack supplied it.
    internal_namespaces: tuple[str, ...] | None = None
    #: ``gate.exit_code_scheme`` when a gate pack supplied it.
    exit_code_scheme: str | None = None
    #: ``gate.severity.<category>`` levels a gate pack supplied, keyed by the
    #: :class:`~abicheck.severity.SeverityConfig` field name.
    severity_levels: Mapping[str, Any] = field(default_factory=dict)
    #: The resolver's own ``gate.exit_code_scheme``, whoever supplied it.
    #: Not a pack contribution and never applied on its own -- carried so
    #: :func:`apply_to_compare_config` can *read* the resolved answer when a
    #: pack severity level would otherwise make it re-derive one. See that
    #: function for why re-deriving was wrong.
    resolved_exit_code_scheme: str | None = None
    #: The full, already-resolved ``CompatibilityEvaluationConfig`` this
    #: application was built from (CLI cleanup phase two, "PR B" effective-
    #: config parity). Not a pack *contribution* -- ``is_empty()`` below
    #: deliberately ignores it -- but real pack identity/provenance a caller
    #: without its own resolved-config object (the directory/package release
    #: fan-out, which reloads a fresh ``PolicyFile`` per library rather than
    #: sharing one ``CompatibilityEvaluationConfig``) can stamp onto each
    #: library's own ``DiffResult.evaluation_config`` the same way single-pair
    #: ``compare``'s ``cli_compare_receipt.record_resolved_config`` does --
    #: closing the "release per-library digest loses pack identity" gap
    #: documented in ``effective_config_digest``'s own module docstring.
    #: Since a release resolves its ``PackApplication`` once for the whole
    #: run (not per library), every library shares this identical object,
    #: which is exactly what makes the digest sensitive to *which pack
    #: revision* ran rather than only to its current field assignments.
    resolved_config: Any = None

    def is_empty(self) -> bool:
        """True when no pack contributed anything this run applies."""
        return not (
            self.policy_overrides
            or self.internal_namespaces is not None
            or self.exit_code_scheme is not None
            or self.severity_levels
        )


def _pack_supplied(config: Any, field_name: str) -> bool:
    """Did a selected pack supply *field_name*'s resolved value?

    The resolver's own answer, not a re-derivation: a field another layer
    stated carries that layer's provenance, so reading ``source_kind`` is
    what makes this module unable to apply a value D7 precedence rejected.
    An absent entry is "not resolved here", which is never a pack.
    """
    provenance = getattr(config, "provenance", {}).get(field_name)
    return getattr(provenance, "source_kind", None) == PACK_SOURCE_KIND


def pack_application(config: Any, *, policy_file: PolicyFile | None) -> PackApplication:
    """The pack-supplied half of *config*, in the shapes the engine takes.

    *policy_file* is the ``--policy-file`` the run really loaded (``None``
    when none was given). Its own overrides are subtracted from the resolved
    ``policy.overrides``, which the resolver deliberately returns *merged*
    (explicit re-applied last, so it is the final value rather than a delta):
    keeping only the difference is what makes the result "what the packs
    added", so folding it back onto that same policy file cannot silently
    restate — or, on a future shape change, contradict — a value the file
    already owns.
    """
    explicit_kinds = set((policy_file.overrides if policy_file else {}) or {})
    resolved_overrides = getattr(getattr(config, "policy", None), "overrides", {}) or {}
    pack_overrides: dict[ChangeKind, Verdict] = {}
    for slug, verdict in resolved_overrides.items():
        kind = slug if isinstance(slug, ChangeKind) else ChangeKind(slug)
        if kind not in explicit_kinds:
            pack_overrides[kind] = verdict

    namespaces: tuple[str, ...] | None = None
    if _pack_supplied(config, INTERNAL_NAMESPACES_FIELD):
        namespaces = tuple(config.surface.internal_namespaces)

    scheme: str | None = None
    if _pack_supplied(config, EXIT_CODE_SCHEME_FIELD):
        scheme = config.gate.exit_code_scheme

    severity_levels: dict[str, Any] = {}
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        if _pack_supplied(config, field_name):
            severity_levels[category] = getattr(config.gate.severity, category)

    return PackApplication(
        policy_overrides=pack_overrides,
        internal_namespaces=namespaces,
        exit_code_scheme=scheme,
        severity_levels=severity_levels,
        resolved_exit_code_scheme=getattr(
            getattr(config, "gate", None), "exit_code_scheme", None
        ),
        resolved_config=config,
    )


def policy_file_with_packs(
    policy_file: PolicyFile | None,
    application: PackApplication,
    *,
    base_policy: str,
) -> PolicyFile | None:
    """*policy_file* with the packs' policy/surface contributions folded in.

    Returns *policy_file* unchanged when the packs contributed neither, so a
    run without ``--pack`` reaches ``compare_snapshots`` with the identical
    object it does today.

    With no ``--policy-file`` given there is nothing to fold into, so one is
    synthesized -- carrying *base_policy*, the ``--policy`` the run resolved,
    because ``checker`` reads a present file's ``base_policy`` **instead of**
    the ``policy`` argument (``effective_policy``). Synthesizing one with the
    dataclass default would silently reset a ``--policy plugin_abi`` run to
    ``strict_abi``: a pack overriding one kind would change the base policy
    for every other one. It carries no ``source_path``/``source_sha256``
    because it came from no file; the packs' own identities and digests are
    already in the receipt, which is where a replay reads them from.
    """
    if not application.policy_overrides and application.internal_namespaces is None:
        return policy_file
    from .policy_file import PolicyFile

    if policy_file is None:
        policy_file = PolicyFile(base_policy=base_policy)
    overrides = dict(policy_file.overrides)
    # Explicit-last, mirroring `resolve_policy_pack_overrides`'s own merge:
    # `application.policy_overrides` already excludes every kind the file
    # states, so this cannot overwrite one -- the ordering states the rule
    # rather than relying on it.
    overrides.update(application.policy_overrides)
    updated = replace(policy_file, overrides=overrides)
    if application.internal_namespaces is not None:
        updated = replace(
            updated,
            internal_namespaces=list(application.internal_namespaces),
            # A pack that supplied this field made the same statement a file
            # would have; `checker` distinguishes "stated empty" from "unset"
            # and the resolver only reaches a pack when nothing else stated it.
            internal_namespaces_stated=True,
        )
    return updated


def resolve_bundle_policy_file(
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    application: PackApplication | None,
) -> PolicyFile | None:
    """Resolve the ``PolicyFile`` a ``compare-release`` bundle analysis
    should score against (G38 Phase 16), mirroring the per-library/matrix
    resolve-then-fold-packs pattern (``_load_suppression_and_policy`` then,
    when a ``--pack`` was resolved, :func:`policy_file_with_packs`) so
    ``bundle_*`` findings honor the same ``--policy``/``--pack`` overrides
    every other finding kind already does. Lives here rather than in
    ``cli_compare_release_helpers.py`` because that module -- and its
    sibling ``cli_compare_release.py`` -- are pinned at a no-growth line-
    count baseline (``architecture/debt.yaml``, ADR-061); this module isn't.
    """
    from .frontends.cli.options.params import _load_suppression_and_policy

    _, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if application is not None:
        pf = policy_file_with_packs(pf, application, base_policy=policy)
    return pf


def apply_to_compare_config(resolved_cfg: Any, application: PackApplication) -> Any:
    """*resolved_cfg* with the gate packs' contributions folded in.

    A no-op unless a gate pack supplied a value. Only reachable for a field
    ``resolve_compare_config`` left at its built-in default: the resolver
    exempts ``gate.exit_code_scheme``/``gate.severity.*`` from pack
    assignment whenever the CLI, a ``--profile``, or ``.abicheck.yml`` stated
    it (or stated a preset that owns it), so overwriting here can never
    displace a value the run was configured with.

    A severity level *is* severity being configured, so it can move an
    unstated ``--exit-code-scheme auto``, exactly as a level set in
    ``.abicheck.yml`` already does. Without that, a gate pack's severity
    would resolve, be reported, and then be scored under the legacy scheme
    that never reads it.

    **Which way it moves is the resolver's answer, not one re-derived here.**
    An earlier revision wrote ``"severity" if severity_active else ...``,
    which silently overrode an explicitly selected ``--exit-code-scheme
    legacy`` whenever a gate pack assigned a severity level -- a
    warning-level pack turned a BREAKING comparison's exit 4 into 0, exactly
    the "a pack never overrides a stated value" rule D8 exists to enforce
    (Codex review, reproduced). The resolver already decides this correctly:
    its ``auto`` default is computed by ``_severity_active``, which *includes*
    the gate pack's own levels, while an explicit ``--exit-code-scheme``
    contributes an ``EXPLICIT_CLI`` candidate that outranks it. So the fix is
    the same one this module's docstring states as its first rule -- read the
    resolved value instead of re-deriving one.
    """
    if application.exit_code_scheme is None and not application.severity_levels:
        return resolved_cfg
    severity = resolved_cfg.severity
    severity_active = resolved_cfg.severity_active
    if application.severity_levels:
        severity = replace(severity, **application.severity_levels)
        severity_active = True
    scheme = application.exit_code_scheme
    if scheme is None and application.severity_levels:
        # The resolver folded these very levels into its own `auto` decision,
        # and let any stated scheme outrank it. Fall back to the pre-pack
        # value only if it somehow resolved nothing.
        scheme = application.resolved_exit_code_scheme
    if scheme is None:
        scheme = resolved_cfg.exit_code_scheme
    return replace(
        resolved_cfg,
        severity=severity,
        severity_active=severity_active,
        exit_code_scheme=scheme,
    )


#: Pack fields whose engine consumer only runs under contract evaluation,
#: mapped to why. Assigning one without ``--contract`` resolves
#: and records a value nothing will read -- the decorative-pack failure this
#: module exists to prevent -- so it is rejected rather than accepted
#: (Codex review). Distinct from :data:`UNAPPLIED_PACK_FIELDS`, which is
#: about what this *build* applies at all; this is about what this
#: *invocation* enabled.
CONTRACT_EVALUATION_ONLY_FIELDS: Mapping[str, str] = {
    "contract.unresolved": (
        "it configures the contract-coverage exit, which is only computed "
        "when --contract selects a domain to measure coverage of"
    ),
}


def check_resolved_config_applies_packs(
    config: Any,
    *,
    gate_supported: bool = True,
    gate_reason: str = "",
    contract_evaluation: bool = True,
) -> None:
    """Reject a pack this build, command, or invocation cannot apply.

    Every question is answered from the resolved configuration: an unapplied
    field from ``provenance[field].source_kind``, a selected gate pack from
    ``gate.packs`` -- which lists exactly the ``kind: gate`` manifests, since
    :func:`resolve_selected_packs` groups them by kind -- and, when
    *contract_evaluation* is false, any
    :data:`CONTRACT_EVALUATION_ONLY_FIELDS` a pack supplied.

    The authoritative half, and the *only* place the gate question is asked:
    :func:`check_pack_fields_applied` re-reads the files, and asking there
    too would answer one question from two revisions. That is the window
    re-reading leaves open -- the resolver has already loaded the manifests,
    and between that read and any later one a generated or concurrently
    edited pack can change, so a second read validates a revision that is not
    the one configuring the run (Codex review, twice -- first for the gap
    before this check existed at all, then for the gap re-reading still
    left).

    Asking the resolved object is exact rather than merely closer: a pack's
    contribution is recorded in ``provenance[field].source_kind`` by the same
    mechanism :func:`pack_application` reads, so this sees precisely what was
    resolved, needs no file access, and cannot disagree with what gets
    applied. It also needs nothing from the resolver's internals -- no
    threading of loaded manifests through a public signature that exists to
    return a configuration.
    """
    gate: Any = getattr(config, "gate", None)
    if not gate_supported and getattr(gate, "packs", None):
        raise PackManifestError(
            "a `kind: gate` pack cannot be applied here"
            + (f" -- {gate_reason}" if gate_reason else "")
        )
    for field_name, reason in UNAPPLIED_PACK_FIELDS.items():
        if _pack_supplied(config, field_name):
            raise _unapplied_field_error(
                _supplying_pack(config, field_name), field_name, reason
            )
    if not contract_evaluation:
        for field_name, reason in CONTRACT_EVALUATION_ONLY_FIELDS.items():
            if _pack_supplied(config, field_name):
                raise _needs_contract_evaluation_error(
                    _supplying_pack(config, field_name), field_name, reason
                )
    for field_name in INERT_PACK_VALUES:
        # `_pack_supplied` is what makes this precedence-aware: a field an
        # explicit `--policy-file` states carries *that* source's provenance,
        # so a shadowed pack value is never reached here.
        if not _pack_supplied(config, field_name):
            continue
        inert = _inert_value_reason(field_name, _resolved_field(config, field_name))
        if inert is not None:
            raise _inert_value_error(
                _supplying_pack(config, field_name), field_name, inert
            )


def check_pack_fields_applied(
    pack_paths: Sequence[str | Path],
    *,
    shadowed_fields: frozenset[str] = frozenset(),
    contract_evaluation: bool = True,
    loaded: Iterable[tuple[str, Any]] | None = None,
) -> None:
    """Reject a manifest assigning a field this build does not apply.

    The file-level half of :data:`UNAPPLIED_PACK_FIELDS`, asked early enough
    that ``compare --dry-run`` answers it the same way the real run does.
    Deliberately *not* asked here: whether the selected command has a gate to
    configure at all. That is a precedence question about the resolution
    rather than a fact about the file -- ``scan`` asks it of
    :func:`check_resolved_config_applies_packs`, which reads the resolved
    ``gate.packs``, so it stays answered in exactly one place.

    Raises :class:`~abicheck.errors.PackManifestError`, the same error class
    a malformed manifest raises, since both are "this manifest cannot be
    used as written" rather than a fact about the libraries being compared.
    """
    # `load_selected_packs` rejects a manifest that assigns nothing, so the
    # emptiness rule is asked by the same read that answers everything below
    # -- and, for the real run, by the resolver's own read rather than this
    # earlier one.
    entries = list(loaded) if loaded is not None else load_selected_packs(pack_paths)
    for path, pack in entries:
        for field_name, value in pack.assignments.items():
            # An *inert value* is a precedence question -- an explicit
            # `--policy-file` stating the same field shadows it, and checking
            # blindly here failed an invocation D8 says is fine (Codex review,
            # reproduced). But it is only *partly* unanswerable: a
            # `--policy-file` is the one layer that can pin any field in
            # `INERT_PACK_VALUES`, and whether it actually does is answerable
            # from the file itself via the resolver's own shared predicate
            # (`policy_file_pins_internal_namespaces`). *shadowed_fields* is
            # that answer; a field not in it is certainly active, so the dry
            # run and the real run agree. An earlier revision passed a coarse
            # "was any --policy-file given" boolean, which treated a file
            # setting only `base_policy` as shadowing and re-opened the same
            # divergence (Codex review, reproduced twice).
            if field_name not in shadowed_fields:
                inert = _inert_value_reason(field_name, value)
                if inert is not None:
                    raise _inert_value_error(str(path), field_name, inert)
            reason = UNAPPLIED_PACK_FIELDS.get(field_name)
            if reason is not None:
                raise _unapplied_field_error(str(path), field_name, reason)
            # Answerable from the file, unlike the inert-value rule above,
            # because no layer other than a pack can state any
            # `CONTRACT_EVALUATION_ONLY_FIELDS` entry -- their resolver
            # candidate lists are empty, so there is no shadowing case in
            # which the assignment would be ruled out anyway. That is what
            # lets `--dry-run` answer it too, instead of approving a plan the
            # identical real run rejects (Codex review).
            needs_evaluation = CONTRACT_EVALUATION_ONLY_FIELDS.get(field_name)
            if needs_evaluation is not None and not contract_evaluation:
                raise _needs_contract_evaluation_error(
                    str(path), field_name, needs_evaluation
                )

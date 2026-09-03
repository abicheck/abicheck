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

"""ADR-049 Phase 1's closing slice: one whole
:class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
resolved from a real front end's own inputs.

``compatibility_evaluation_wiring.py`` resolves *individual* fields from real
input (``contract.mode`` from the legacy scope flag,
``surface.internal_namespaces`` from a ``--policy-file``, the three
``*.packs`` fields plus a policy pack's overrides from real manifests). This
module is the layer above it: it collects **every** field a front end can
actually state today, resolves each one through
:func:`~abicheck.compatibility_evaluation_resolver.resolve_field`, and
assembles the seven typed namespaces plus one provenance receipt entry per
resolved field -- the "one typed object" ADR-049 D7 requires and Phase 1's
own gate names: *"every front end resolves equivalent semantic input to an
equal ``CompatibilityEvaluationConfig`` and provenance receipt."*

Two real front ends are wired, and the gate above is executable rather than
aspirational -- :func:`cross_front_end_differences` compares two resolved
configs modulo exactly one permitted difference (*which* front end stated a
value: ``explicit_cli`` vs. ``api_request``, and the option spelling that
goes with it), and ``tests/test_compatibility_evaluation_frontend.py`` runs
the CLI's own ``compare`` kwargs against the equivalent
:class:`~abicheck.api_types.CompareRequest` through it:

- :func:`compare_cli_inputs` -- the ``compare`` command's real kwargs
  (``cli.py``'s option destinations: ``--contract``, ``--scope-public-headers``,
  ``--policy``/``--policy-file``, ``--severity-preset``, ``--exit-code-scheme``,
  ``--public-symbol``, ``--suppress``), plus the set of parameters the user
  *actually typed* (Click's ``ctx.get_parameter_source(...)`` is what a live
  caller would pass), since several of those options carry a non-``None``
  click default that must not be mistaken for a stated value.
- :func:`compare_request_inputs` -- the same semantic fields off a typed
  :class:`~abicheck.api_types.CompareRequest`.
- :meth:`ProjectCompatibilityInputs.from_build_config` -- the project's own
  ``.abicheck.yml`` (``buildsource.build_config.BuildConfig``), contributing
  at ``project_config`` tier.

**Still resolved to its built-in default, because no front end can state it
today** (recorded here rather than silently defaulted):
:class:`~abicheck.compatibility_evaluation_config.EvidenceConfig` in full (no
CLI/API surface selects evidence providers or a variant set),
``contract.unresolved``/``contract.overlays``/``assurance.require_evidence``
(no flag; a ``kind: contract`` pack is the only input that reaches them, via
:func:`~abicheck.compatibility_evaluation_wiring.resolve_pack_field_assignments`),
and ``--strict-suppressions``/``--require-justification``, which are real
inputs with no field in the ADR's own typed shape to carry them.

Not called from any live command: this module *resolves* configuration, it
does not apply it. Nothing here changes a verdict, a finding, or an exit
code -- consuming the resolved object in the authoritative comparison path is
Phase 5's "same typed config" work, and the default flip is Phase 7. See
``docs/contribute/plans/public-contract-default.md``.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Collection, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .change_registry_types import Verdict
from .checker_policy import VALID_BASE_POLICIES, policy_kind_sets
from .compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    DigestedItems,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    SelectedByEntry,
    SuppressionConfig,
    SurfaceConfig,
    ValueProvenance,
)
from .compatibility_evaluation_packs import PackKind
from .compatibility_evaluation_resolver import FieldCandidate, resolve_field
from .compatibility_evaluation_wiring import (
    BUILT_IN_DEFAULT_CONTRACT_MODE,
    CONTRACT_MODE_FIELD,
    INTERNAL_NAMESPACES_FIELD,
    RoutedPackAssignment,
    internal_namespaces_candidate,
    legacy_contract_mode_candidate,
    load_selected_packs,
    policy_file_pins_internal_namespaces,
    resolve_pack_field_assignments,
    resolve_policy_pack_overrides,
    resolve_selected_packs,
)
from .contract_relevance_types import ContractMode, SelectorLayer, coerce_contract_mode
from .severity import SEVERITY_PRESETS, SeverityConfig, SeverityLevel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .api_types import CompareRequest
    from .buildsource.build_config import BuildConfig
    from .policy_file import PolicyFile

# Field names double as provenance-receipt keys. Declared once so the receipt
# a consumer reads and the pack-route table in
# `compatibility_evaluation_wiring.py` cannot drift apart on a spelling.
CONTRACT_UNRESOLVED_FIELD = "contract.unresolved"
CONTRACT_OVERLAYS_FIELD = "contract.overlays"
CONTRACT_PACKS_FIELD = "contract.packs"
POLICY_BASE_FIELD = "policy.base"
POLICY_PACKS_FIELD = "policy.packs"
POLICY_OVERRIDES_FIELD = "policy.overrides"
EXPLICIT_SCOPE_FIELD = "surface.explicit_scope"
REQUIRE_EVIDENCE_FIELD = "assurance.require_evidence"
EXIT_CODE_SCHEME_FIELD = "gate.exit_code_scheme"
GATE_PRESET_FIELD = "gate.preset"
GATE_PACKS_FIELD = "gate.packs"
SUPPRESSIONS_FIELD = "suppressions"

#: ``gate.severity.<category>`` field name per :class:`SeverityConfig` field.
SEVERITY_CATEGORY_FIELDS: Mapping[str, str] = {
    category: f"gate.severity.{category}"
    for category in (
        "abi_breaking",
        "potential_breaking",
        "quality_issues",
        "addition",
    )
}


class FrontEnd(str, Enum):
    """Which front end stated a run's explicit inputs.

    ADR-049 D7 puts "explicit CLI" and "explicit API request" in the *same*
    precedence tier -- neither outranks the other, and a single resolution
    only ever has one of them -- so this selects nothing but the
    :class:`~abicheck.contract_relevance_types.SelectorLayer` each explicit
    candidate is tagged with, and the option spelling recorded alongside it.
    """

    CLI = "cli"
    API = "api"

    @property
    def layer(self) -> SelectorLayer:
        return (
            SelectorLayer.EXPLICIT_CLI
            if self is FrontEnd.CLI
            else SelectorLayer.API_REQUEST
        )


# --------------------------------------------------------------------------
# D6 immutable identities for the built-in (file-less) base policy and
# severity preset.
# --------------------------------------------------------------------------


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stated_policy_base(policy: Any, policy_file: Any) -> Any:
    """*policy*, unless a ``policy_file`` overrode it with a value this
    resolver would reject.

    Both the MCP tool and the scan API validate ``policy`` **only when no
    ``policy_file`` is given** -- the file takes precedence over the base
    name -- so an unknown ``policy`` alongside a valid file is an accepted
    request whose comparison completes under the file's own base. Handing
    that ignored value to the resolver made :func:`builtin_policy_identity`
    raise and killed an otherwise-finished run: a receipt turning a
    successful comparison into a failure, the one thing a receipt must never
    do (Codex review, found once on the MCP path and then again on the scan
    path -- hence living here rather than in either front end).

    Dropped rather than repaired: the value chose nothing, so naming it in
    the receipt would be false either way, and the resolver reads the base
    off ``policy_file`` exactly as the comparison did. With no file present
    the value passes through unchanged, so a genuinely unknown policy still
    fails loudly where the front end validates it.
    """
    if policy_file is None or policy is None:
        return policy
    return policy if policy in VALID_BASE_POLICIES else None


@functools.cache
def builtin_policy_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--policy`` base.

    ADR-049 D6 requires every selected base to carry an
    ``id``/``version``/``sha256``, but a built-in base is code, not a file --
    there are no bytes to digest. The digest is therefore taken over what the
    base actually *is*: the four ``ChangeKind`` sets
    :func:`~abicheck.checker_policy.policy_kind_sets` resolves it to. That
    makes the identity detect the drift a digest exists to detect -- a
    registry change that moves a kind between buckets changes the base's
    meaning and changes its digest -- which a bare ``id``/``version`` pair
    could not.

    ``version`` is ``1`` for every built-in base: the built-ins are not
    independently versioned artifacts, and the digest already distinguishes
    two revisions of the same name.

    Raises ``ValueError`` for a name outside
    :data:`~abicheck.checker_policy.VALID_BASE_POLICIES` -- ``policy_kind_sets``
    itself falls back to ``strict_abi`` for an unknown name, which is the
    right behavior for classification (never crash a comparison over a typo)
    but exactly wrong for an identity (it would mint a ``sdk_vendr`` identity
    carrying ``strict_abi``'s digest).
    """
    if name not in VALID_BASE_POLICIES:
        raise ValueError(
            f"unknown base policy {name!r}: choose from {sorted(VALID_BASE_POLICIES)}"
        )
    breaking, api_break, compatible, risk = policy_kind_sets(name)
    return ImmutableIdentity(
        id=name,
        version=1,
        sha256=_digest(
            {
                "base_policy": name,
                "breaking": sorted(k.value for k in breaking),
                "api_break": sorted(k.value for k in api_break),
                "compatible": sorted(k.value for k in compatible),
                "risk": sorted(k.value for k in risk),
            }
        ),
    )


@functools.cache
def severity_preset_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--severity-preset``.

    Same reasoning as :func:`builtin_policy_identity`: a preset is code, so
    the digest is taken over the four category levels it resolves to.
    The CLI spelling ``info-only`` and its programmatic alias ``info_only``
    (``SEVERITY_PRESETS`` carries both keys for the same
    :class:`~abicheck.severity.SeverityConfig`) normalize to one canonical id,
    so two equivalent semantic inputs resolve to an equal object (D7) instead
    of to two identities that differ only in punctuation.
    """
    preset = SEVERITY_PRESETS.get(name)
    if preset is None:
        raise ValueError(
            f"unknown severity preset {name!r}: choose from {sorted(SEVERITY_PRESETS)}"
        )
    canonical_id = name.replace("_", "-")
    return ImmutableIdentity(
        id=canonical_id,
        version=1,
        sha256=_digest(
            {
                "severity_preset": canonical_id,
                **{
                    category: getattr(preset, category).value
                    for category in SEVERITY_CATEGORY_FIELDS
                },
            }
        ),
    )


# --------------------------------------------------------------------------
# Normalized, front-end-agnostic inputs.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SuppressionSource:
    """A selected ``--suppress`` file, already read and digested.

    Kept separate from :class:`ExplicitCompatibilityInputs` so the resolver
    itself stays I/O-free: :meth:`from_file` is the one place that touches
    the filesystem, and a caller that already loaded the rule file (every
    real front end does, well before configuration is resolved) can build
    this from what it has.
    """

    path: str | None
    sha256: str
    rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))

    @classmethod
    def from_loaded(
        cls, suppression: Any, path: Any = None
    ) -> SuppressionSource | None:
        """Build one from a list a front end already loaded. ``None`` passes through.

        The single-read rule: built from the list that really scored the run,
        never a re-read of *path*, so the digest cannot describe content that
        did not.

        **The digest falls back to the rules' own content** when the list
        carries no ``source_sha256``. That is not a rare case: a
        programmatically constructed or merged
        :class:`~abicheck.suppression.SuppressionList` has none, and every
        front end that accepts one could hand it over. Taking ``""`` there --
        which each of them previously did, by spelling this
        ``getattr(...) or ""`` inline -- made :class:`SuppressionConfig`
        reject the input and fail an otherwise-valid run (Codex review, found
        on the scan path; the MCP path had it too). ``contract_context.
        suppression_config_for`` already derived the fallback correctly, so
        this is that rule shared rather than a fourth transcription of it:
        the rules are persisted verbatim beside the digest, so a digest over
        them authenticates exactly what a replay would re-read.
        """
        if suppression is None:
            return None
        from .contract_evidence_collect import content_digest

        rules = tuple(suppression.rule_identities())
        return cls(
            path=str(path) if path is not None else None,
            sha256=getattr(suppression, "source_sha256", None)
            or content_digest(list(rules)),
            rules=rules,
        )

    @classmethod
    def from_file(
        cls, path: str | Path, *, require_justification: bool = False
    ) -> SuppressionSource:
        """Read, digest, and summarize a real suppression file.

        ``sha256`` is the digest ``SuppressionList.load`` captured over the
        raw bytes it parsed (exact replay, ADR-049 D6); ``rules`` is
        :meth:`~abicheck.suppression.SuppressionList.rule_identities`. One
        read produces both, so they always describe the same content.
        *require_justification* is forwarded to the loader so a front end
        running with ``--require-justification`` gets the same hard error
        here that it gets from its own load.
        """
        from .suppression import SuppressionList

        file_path = Path(path)
        loaded = SuppressionList.load(
            file_path, require_justification=require_justification
        )
        # The digest comes from the same read that produced these rules --
        # digesting the path separately could pair one content's hash with
        # another's rules if the file changed in between (Codex review).
        return cls(
            path=str(file_path),
            sha256=loaded.source_sha256 or "",
            rules=loaded.rule_identities(),
        )


@dataclass(frozen=True)
class PublicSymbolsList:
    """A selected ``--public-symbols-list`` file, already read.

    Kept apart from the inline ``--public-symbol`` values rather than merged
    into them: flattening the two lost which symbols came from the file, so
    the receipt for ``surface.explicit_scope`` named only ``--public-symbol``
    and no path — even for a list-file-only invocation, which a replay then
    had no way to reproduce (Codex review).
    """

    path: str | None
    items: tuple[str, ...] = ()
    #: The digest of the file's raw bytes. The items alone cannot stand in for
    #: it: reading drops comments and blank lines, and the union that forms
    #: ``surface.explicit_scope`` sorts and deduplicates, so a real edit to
    #: this file can leave the item list identical -- exactly the drift
    #: :class:`~abicheck.compatibility_evaluation_config.DigestedItems` says a
    #: digest exists to catch (Codex review, fresh evidence).
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))

    @classmethod
    def from_file(cls, path: str | Path) -> PublicSymbolsList:
        items, digest = read_symbols_list_with_digest(path)
        return cls(path=str(path), items=items, sha256=digest)


def _normalized_symbols(values: Iterable[str]) -> tuple[str, ...]:
    """Inline symbol values, normalized the way the live run normalizes them.

    :func:`collect_force_public_symbols` -- what the real comparison forces --
    strips every value and drops blanks, so keeping ``--public-symbol ' foo '``
    verbatim here resolved ``surface.explicit_scope`` to ``" foo "`` for a run
    that actually forces ``"foo"``: an effective configuration, and a digest,
    that misrepresent the run (Codex review, fresh evidence). Applied in the
    input models rather than in one adapter so every front end -- and a caller
    constructing the dataclass directly -- normalizes identically. The
    list-file reader (:func:`read_symbols_list`) already does the same.
    """
    return tuple(stripped for value in values if (stripped := value.strip()))


@dataclass(frozen=True)
class ExplicitCompatibilityInputs:
    """What the user stated on *this* invocation (CLI flags / API request).

    Every field's "not stated" value is ``None`` (or an empty collection), so
    an untouched option contributes no candidate at all and the next layer
    down wins -- ADR-049 D7's "a selector layer only participates when it
    actually selected something". A front end whose own default is
    non-``None`` (``--policy`` defaults to ``strict_abi``,
    ``--scope-public-headers`` to ``True``) must therefore decide
    *explicitness* before building this; :func:`compare_cli_inputs` does that
    from the set of parameters the user actually typed.
    """

    #: ``--contract`` / ``CompareRequest.contract_mode``.
    contract_mode: str | None = None
    #: ``--scope-public-headers``/``--no-`` (the D2 legacy alias for
    #: ``contract.mode``); ``None`` = flag untouched.
    scope_public_headers: bool | None = None
    #: ``--policy`` / ``CompareRequest.policy``.
    policy_base: str | None = None
    #: Which option selected *policy_base*, when it was not ``--policy``
    #: itself. ``compare`` switches an untouched ``--policy`` to
    #: ``plugin_abi`` for a ``--required-symbol`` contract (ADR-043), so the
    #: value really was selected -- by a different typed flag. Recording it
    #: as ``--policy`` would name an option the user never passed, and
    #: leaving it unstated made the receipt claim ``strict_abi`` for a run
    #: that used ``plugin_abi`` (Codex review, fresh evidence).
    policy_base_option: str | None = None
    #: The file that option named, when it has one
    #: (``--required-symbols FILE``), and the digest of the bytes read from
    #: it. Without them the receipt can say a symbol list selected the policy
    #: but not *which* list, which is the same gap the policy-file and
    #: suppression sources carry digests to close (Codex review).
    policy_base_path: str | None = None
    policy_base_sha256: str | None = None
    #: An already-loaded ``--policy-file`` document.
    policy_file: PolicyFile | None = None
    #: Digest of that file's own bytes, for the receipt (ADR-049 D6). Left
    #: unset, it is computed from ``policy_file.source_path`` -- supply it
    #: only when the caller already has the digest, or when the document did
    #: not come from a readable path.
    policy_file_sha256: str | None = None
    #: ``--public-symbol``/``--public-symbols-list`` /
    #: ``CompareRequest.force_public_symbols`` (ADR-024 D6 widening overlay).
    #: Inline values only -- a ``--public-symbols-list`` file is carried
    #: separately so the receipt can name it.
    public_symbols: tuple[str, ...] = ()
    #: An already-read ``--public-symbols-list`` file.
    public_symbols_list: PublicSymbolsList | None = None
    #: An already-read ``--suppress`` source.
    suppression: SuppressionSource | None = None
    #: ``--exit-code-scheme``. ``"auto"`` is a stated selection whose value is
    #: resolved at resolution time (see
    #: :func:`resolve_compatibility_evaluation_config`).
    exit_code_scheme: str | None = None
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None
    #: ADR-049 D8 pack-manifest paths. No CLI flag supplies these yet (see
    #: the plan's Phase 1 notes); the resolver accepts them so the composition
    #: path is real and tested ahead of the flag.
    pack_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "public_symbols", _normalized_symbols(self.public_symbols)
        )
        object.__setattr__(self, "pack_paths", tuple(str(p) for p in self.pack_paths))

    def severity_category(self, category: str) -> str | None:
        return cast("str | None", getattr(self, f"severity_{category}"))


@dataclass(frozen=True)
class ProjectCompatibilityInputs:
    """The project's ``.abicheck.yml`` contribution (``project_config`` tier).

    A deliberately narrow projection of ``BuildConfig``: only the keys that
    map onto a field of the ADR-049 typed configuration. The rest of the file
    (``build:``/``sources:``/``compile:``/``debug:``/``source:``) drives
    extraction, not compatibility evaluation.
    """

    path: str | None = None
    #: The digest of the ``.abicheck.yml`` bytes these values were parsed
    #: from, when the caller has it. Deliberately not computed here from
    #: *path*: this object is built from an already-loaded ``BuildConfig``,
    #: and re-reading the file to hash it could pair one content's digest
    #: with another's values -- the same trap the policy-file and suppression
    #: sources were fixed for. Left ``None``, a project-contributed value is
    #: still recorded in the receipt by path; only byte-level drift in that
    #: file goes undetected.
    sha256: str | None = None
    #: ``scope.public`` -- the project-config spelling of the same legacy
    #: alias ``--scope-public-headers`` is (D2), stated at ``project_config``
    #: tier rather than typed on this invocation.
    scope_public: bool | None = None
    #: ``scope.public_symbols``.
    public_symbols: tuple[str, ...] = ()
    #: ``exit_code_scheme:`` (``"auto"``/``None`` = unset).
    exit_code_scheme: str | None = None
    #: Whether the project literally wrote ``exit_code_scheme:`` -- lets a
    #: stated ``auto`` outrank a lower-precedence gate pack instead of
    #: reading as unstated (``BuildConfig.exit_code_scheme`` alone can't
    #: tell an explicit ``auto`` apart from an absent key).
    exit_code_scheme_explicit: bool = False
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "public_symbols", _normalized_symbols(self.public_symbols)
        )

    def severity_category(self, category: str) -> str | None:
        return cast("str | None", getattr(self, f"severity_{category}"))

    @classmethod
    def from_build_config(
        cls,
        cfg: BuildConfig | None,
        *,
        path: str | Path | None = None,
        sha256: str | None = None,
    ) -> ProjectCompatibilityInputs | None:
        """Project a loaded ``.abicheck.yml`` onto the fields this resolver
        understands, or ``None`` when there is no config at all.

        *sha256* is the digest of the bytes *cfg* was parsed from, which a
        caller that read the file should pass so a composed receipt can catch
        drift in it (see the field's own note on why it is not computed here).
        """
        if cfg is None:
            return None
        return cls(
            path=str(path) if path is not None else None,
            sha256=sha256,
            scope_public=cfg.scope_public,
            public_symbols=tuple(cfg.public_symbols),
            exit_code_scheme=cfg.exit_code_scheme,
            exit_code_scheme_explicit=cfg.exit_code_scheme_explicit,
            severity_preset=cfg.severity_preset,
            severity_abi_breaking=cfg.severity_abi_breaking,
            severity_potential_breaking=cfg.severity_potential_breaking,
            severity_quality_issues=cfg.severity_quality_issues,
            severity_addition=cfg.severity_addition,
        )


@dataclass(frozen=True)
class RunProfileInputs:
    """What a selected ``--profile`` filled in, at D7's ``run_profile`` tier.

    ``cli_options.apply_compare_profile`` folds a profile's defaults into the
    command's kwargs only where the user left the option alone, so by the
    time the live run reads them a profile-selected value is
    indistinguishable from a built-in default. Resolving the receipt without
    this layer therefore did not merely under-claim the source -- it produced
    a *wrong* value: ``--profile ci-gate`` runs with
    ``exit_code_scheme="severity"``, while a resolution that never saw the
    profile answers ``"legacy"`` for a run stating no severity flag.

    **A deliberate deviation from D7, recorded rather than smoothed over.**
    ADR-049 scopes ``run_profile`` to execution fields (depth, format,
    budget, workflow) and assigns the exit-code scheme to the *gate*
    namespace -- yet the pre-existing ``ci-gate`` bundle
    (:data:`~abicheck.cli_options.COMPARE_PROFILES`) really does select it.
    Encoding what the bundle does today is the honest receipt; the two ways
    to remove the deviation (move the key out of ``ci-gate`` into a gate
    pack, or amend D7) both change user-visible behavior or the ADR, so
    neither belongs in the wiring change that found it. Only this one field
    passes ``allow_run_profile=True``; a profile assigning any other field
    still raises, which is what keeps the deviation from spreading.

    The remaining ``ci-gate``/``release-cut``/``quick`` keys (``depth``,
    ``fmt``, ``recommend``, ``stat``) are execution/report concerns with no
    field in this configuration at all, so they are not modelled here.
    """

    #: The profile's own name (``ci-gate``/``release-cut``/``quick``), for
    #: the receipt's ``reference``.
    name: str | None = None
    #: ``exit_code_scheme``, only when the profile actually supplied it --
    #: an explicitly typed flag is filtered out by ``apply_compare_profile``
    #: before it ever reaches here.
    exit_code_scheme: str | None = None


# --------------------------------------------------------------------------
# Candidate construction helpers.
# --------------------------------------------------------------------------


def _candidate(
    layer: SelectorLayer,
    value: Hashable,
    *,
    option: str | None = None,
    source_kind: str | None = None,
    reference: str | None = None,
    version: int | None = None,
    sha256: str | None = None,
    source_sha256: str | None = None,
    path: str | None = None,
    field_location: str | None = None,
) -> FieldCandidate:
    """One candidate and the single hop that selected it.

    *sha256* identifies what the value *is* (a preset's or manifest's own
    revision); *source_sha256* identifies the file that selected it, and
    defaults to *sha256* when the two are the same artifact. They differ for
    a project config naming a built-in preset: the entry-level digest is the
    preset's, while the hop still has to name the ``.abicheck.yml`` a replay
    would re-read (Codex review, fresh evidence).
    """
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=layer,
            source_kind=source_kind,
            reference=reference,
            version=version,
            sha256=sha256,
            path=path,
            field_location=field_location,
            selected_by=(
                SelectedByEntry(
                    layer=layer,
                    option=option,
                    path=path,
                    sha256=source_sha256 if source_sha256 is not None else sha256,
                ),
            ),
        ),
        value=value,
    )


def _default(value: Hashable, *, source_kind: str | None = None) -> FieldCandidate:
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=SelectorLayer.BUILT_IN_DEFAULT, source_kind=source_kind
        ),
        value=value,
    )


def _resolve(
    field_name: str,
    candidates: Sequence[FieldCandidate],
    *,
    default: FieldCandidate,
    pack: RoutedPackAssignment | None = None,
    pack_layer: SelectorLayer = SelectorLayer.EXPLICIT_CLI,
    pack_option: str = "--pack",
    default_is_stated: bool = False,
    require_legacy_alias_agreement: bool = True,
    allow_run_profile: bool = False,
) -> tuple[Hashable, ValueProvenance]:
    """Resolve one field, letting a selected pack fill it only if nothing else did.

    ADR-049 D8 composes as "explicit per-kind override > selected packs >
    base". A pack is *selected by* a layer but is not itself a precedence
    layer (plan §3.2), so a pack assignment cannot be handed to
    :func:`resolve_field` as an ordinary candidate: tagged with the layer that
    selected it, it would tie with -- and be reported as conflicting with --
    an explicit value for the same field, which is precisely the case D8 says
    the explicit value resolves.

    So the rule is applied here instead, and deliberately in its conservative
    form: a pack contributes only when *no* other layer stated the field,
    project config included. D8's own wording covers the explicit override;
    extending it to "a pack never silently overrides a value the user or the
    project stated" is the reading that cannot surprise anyone, and a project
    that wants the pack's value simply stops stating its own.

    *default_is_stated* extends that same rule to a field whose value is
    *derived* from something the user or project stated rather than stated
    directly, and therefore arrives as this call's ``default`` -- today, the
    per-category severity levels a ``--severity-preset``/``severity.preset``
    selection expands into. Without it, ``--severity-preset strict`` plus a
    gate pack assigning ``gate.severity.addition: info`` resolved to
    ``INFO``: the preset had put its ``ERROR`` in the default slot, leaving
    the field looking unstated, so the pack replaced a level the user really
    had asked for (Codex review, fresh evidence). The derived levels stay in
    the ``default`` slot rather than becoming candidates because a candidate
    at the stating layer would *tie* with an explicit per-category flag at
    that same layer -- and refining a preset with one category flag
    (``--severity-preset strict`` plus ``severity.addition: info``) is legal, not a
    conflict.

    *allow_run_profile* is forwarded to :func:`resolve_field`, which rejects a
    ``RUN_PROFILE`` candidate for any field the caller has not opted in --
    D7 scopes that layer to execution fields. See :class:`RunProfileInputs`
    for the one field this module opts in, and why.
    """
    all_candidates = list(candidates)
    if not all_candidates and pack is not None and not default_is_stated:
        all_candidates.append(
            _candidate(
                pack_layer,
                pack.value,
                option=pack_option,
                source_kind="pack_manifest",
                reference=pack.identity.id,
                version=pack.identity.version,
                sha256=pack.identity.sha256,
                path=pack.path,
                field_location=field_name,
            )
        )
    return resolve_field(
        field_name,
        all_candidates,
        default=default,
        require_legacy_alias_agreement=require_legacy_alias_agreement,
        allow_run_profile=allow_run_profile,
    )


#: Marker value for "some other layer already states this field", used as the
#: value in the ``explicit_overrides`` mapping the pack functions take. Those
#: functions read only its keys (ADR-049 D8 exempts an already-stated field
#: from pack-vs-pack conflict detection), and several of the fields marked
#: this way have no resolved value yet at the point the mapping is built.
_STATED_ELSEWHERE: Hashable = "<stated by another layer>"


def collect_force_public_symbols(
    public_symbols: tuple[str, ...],
    symbols_list: Path | None,
    *,
    already_read: PublicSymbolsList | None = None,
) -> set[str]:
    """Merge ``--public-symbol`` values with a ``--public-symbols-list`` file.

    The list file is one symbol per line; blank lines and ``#`` comments are
    ignored (à la abi-compliance-checker ``-symbols-list``). Inline trailing
    comments are not stripped — a ``#`` must start the line to be a comment.

    Lives here rather than in ``cli_helpers_compare.py`` (which re-exports it
    as ``_collect_force_public_symbols`` for its existing callers) so this
    module can build ``surface.explicit_scope`` from *both* sources without
    importing a CLI-layer module. Reading only the inline tuple made a
    list-file-only invocation resolve to no explicit scope at all, and a
    mixed one silently drop every symbol the file supplied (Codex review).

    *already_read* lets a caller that will *also* build a receipt from this
    file supply the one read both need. Reading it twice pairs the receipt's
    digest with content that may not be what scored the run, and lets a file
    deleted mid-run fail an otherwise-finished comparison during receipt
    generation (Codex review, fresh evidence) -- the same single-read rule
    the policy, suppression, project-config, and required-symbol sources
    already follow.
    """
    out: set[str] = {s.strip() for s in public_symbols if s.strip()}
    if already_read is not None:
        out.update(already_read.items)
    elif symbols_list is not None:
        out.update(read_symbols_list(symbols_list))
    return out


def read_symbols_list(path: str | Path) -> tuple[str, ...]:
    """The symbols named by a ``--public-symbols-list`` file, in file order."""
    return _parse_symbols_list(Path(path).read_bytes())


def read_symbols_list_with_digest(path: str | Path) -> tuple[tuple[str, ...], str]:
    """The symbols a list file names, plus the digest of the bytes they were
    parsed from.

    One read produces both, the same rule ``PolicyFile.load``/
    ``SuppressionList.load`` follow: digesting the path in a separate read
    could pair one content's hash with another's items. The digest is over
    the raw bytes, so a line-ending-only change is drift, not a no-op.
    """
    raw = Path(path).read_bytes()
    return _parse_symbols_list(raw), hashlib.sha256(raw).hexdigest()


def _parse_symbols_list(raw: bytes) -> tuple[str, ...]:
    return tuple(
        line
        for source in raw.decode("utf-8").splitlines()
        if (line := source.strip()) and not line.startswith("#")
    )


def _policy_file_override_slugs(policy_file: PolicyFile | None) -> dict[str, Verdict]:
    """``--policy-file`` ``overrides:`` as the slug-keyed mapping the typed
    config takes (``PolicyFile.overrides`` is keyed by ``ChangeKind``)."""
    if policy_file is None:
        return {}
    return {kind.value: verdict for kind, verdict in policy_file.overrides.items()}


@dataclass(frozen=True)
class _PolicyFileSource:
    """A loaded ``--policy-file`` with its path and content digest."""

    path: str | None
    sha256: str | None

    @classmethod
    def of(
        cls, policy_file: PolicyFile | None, override: str | None
    ) -> _PolicyFileSource:
        """The path and digest to record for *policy_file*.

        The digest is never computed by re-reading ``source_path`` here:
        the document was parsed earlier, and hashing whatever is on disk
        *now* would, for a file edited in between, produce a receipt whose
        digest identifies content this resolution never applied — the exact
        failure a digest exists to catch (Codex review). It comes from the
        caller's explicit *override*, or from
        :attr:`~abicheck.policy_file.PolicyFile.source_sha256`, which
        ``PolicyFile.load`` captures over the bytes it actually parsed.
        """
        if policy_file is None:
            return cls(path=None, sha256=None)
        path = str(policy_file.source_path) if policy_file.source_path else None
        return cls(path=path, sha256=override or policy_file.source_sha256)


def _explicit_scope(
    explicit: ExplicitCompatibilityInputs,
    project: ProjectCompatibilityInputs | None,
    layer: SelectorLayer,
    *,
    symbol_option: str = "scope.public_symbols",
    list_option: str = "scope.public_symbols",
) -> tuple[DigestedItems | None, ValueProvenance]:
    """Resolve ``surface.explicit_scope`` -- an *additive* overlay field.

    ``--public-symbol``, ``--public-symbols-list`` and ``scope.public_symbols``
    are documented (ADR-037 D4, ``cli_helpers_compare.resolve_compare_config``)
    as merging, not overriding: the CLI list widens the project's rather than
    replacing it. Each of the three is recorded as its own ``selected_by``
    entry, with the path of the file it came from where there is one. That is a genuine exception to D7's per-field
    highest-layer-wins rule, and it is the live behavior, so the resolved
    value is the union of every contributing layer. The receipt records the
    highest-precedence contributor as the provenance layer and lists **every**
    contributor in ``selected_by``, so an additive resolution is still exactly
    replayable.

    ``sha256`` covers the canonical item list *and* every contributing
    source's own digest. The items alone are not enough once a file
    contributes: reading a ``--public-symbols-list`` drops comments and blank
    lines and the union sorts and deduplicates, so a real edit to that file
    can leave the items identical -- exactly the drift
    :class:`DigestedItems` exists to catch, which an earlier version of this
    function wrongly argued could not happen here (Codex review, fresh
    evidence). Inline ``--public-symbol`` values have no external source, and
    contribute only themselves. A source with no digest available (a project
    config whose loader did not supply one) still contributes its path, so
    the receipt names it; only byte-level drift in that one file stays
    invisible.
    """
    selected_by: list[SelectedByEntry] = []
    merged: list[str] = []
    sources: list[dict[str, str | None]] = []
    winning_layer = SelectorLayer.BUILT_IN_DEFAULT
    if explicit.public_symbols:
        winning_layer = layer
        selected_by.append(SelectedByEntry(layer=layer, option=symbol_option))
        merged.extend(explicit.public_symbols)
    # The list file is its own contributor, with its own path: a replay has to
    # be able to tell "these symbols were typed" from "these came from that
    # file" (Codex review). Its mere *presence* counts, empty or not -- a
    # selected file that resolved to zero symbols is a different fact from no
    # file at all, which is exactly the distinction `DigestedItems` documents,
    # and dropping it would lose the path too (Codex review, second round).
    if explicit.public_symbols_list is not None:
        winning_layer = layer
        selected_by.append(
            SelectedByEntry(
                layer=layer,
                option=list_option,
                path=explicit.public_symbols_list.path,
                sha256=explicit.public_symbols_list.sha256,
            )
        )
        merged.extend(explicit.public_symbols_list.items)
        sources.append(
            {
                "option": list_option,
                "path": explicit.public_symbols_list.path,
                "sha256": explicit.public_symbols_list.sha256,
            }
        )
    if project is not None and project.public_symbols:
        if winning_layer is SelectorLayer.BUILT_IN_DEFAULT:
            winning_layer = SelectorLayer.PROJECT_CONFIG
        selected_by.append(
            SelectedByEntry(
                layer=SelectorLayer.PROJECT_CONFIG,
                option="scope.public_symbols",
                path=project.path,
                sha256=project.sha256,
            )
        )
        merged.extend(project.public_symbols)
        sources.append(
            {
                "option": "scope.public_symbols",
                "path": project.path,
                "sha256": project.sha256,
            }
        )

    # Keyed on whether a source was *selected*, not on whether it yielded
    # anything: `DigestedItems` exists to tell "selected, resolved to zero"
    # from "never selected", and only the latter is `None`.
    if not selected_by:
        return None, ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)

    items = tuple(dict.fromkeys(sorted(merged)))
    return (
        DigestedItems(
            sha256=_digest({"explicit_scope": list(items), "sources": sources}),
            items=items,
        ),
        ValueProvenance(
            layer=winning_layer,
            source_kind="public_symbol_overlay",
            selected_by=tuple(selected_by),
        ),
    )


# --------------------------------------------------------------------------
# The whole-object resolver.
# --------------------------------------------------------------------------


def resolve_compatibility_evaluation_config(
    *,
    front_end: FrontEnd = FrontEnd.CLI,
    explicit: ExplicitCompatibilityInputs | None = None,
    project: ProjectCompatibilityInputs | None = None,
    profile: RunProfileInputs | None = None,
    api_spellings: Mapping[str, str] | None = None,
) -> CompatibilityEvaluationConfig:
    """Resolve one complete effective configuration plus its receipt.

    Every field is resolved independently through
    :func:`~abicheck.compatibility_evaluation_resolver.resolve_field`, so
    D7's precedence, its same-tier-conflict rule, and its
    equivalent-duplicates rule apply uniformly rather than per call site.
    The returned object's ``provenance`` maps each dotted field name to the
    :class:`ValueProvenance` of the layer that won it.

    Two D7 compatibility exceptions are applied, both with
    ``require_legacy_alias_agreement=False`` (the explicit value wins and the
    shadowed legacy input is retained in ``provenance[...].shadowed_legacy``)
    rather than as usage errors:

    - ``policy.base``: "when both ``--policy`` and ``--policy-file`` are
      supplied, ``--policy-file`` keeps winning as documented and tested
      today" (D7, verbatim) -- ``--policy``'s own help text already says it is
      ignored then.
    - ``contract.mode``: an explicit ``--contract`` outranks
      ``--scope-public-headers``/``--no-`` rather than colliding with it, per
      the Phase 6 flag's own documented contract ("an explicit value outranks
      those"). Making that pair an error would reject a combination the live
      CLI accepts today.

    *profile* is what a selected ``--profile`` filled in; it contributes at
    D7's ``run_profile`` tier, between the explicit and project-config
    layers. See :class:`RunProfileInputs` for the one field it can state and
    the ADR deviation that field records.

    ``"auto"`` never reaches a resolved *value*: ADR-037 D12's third
    ``--exit-code-scheme`` choice means "decide from whether a severity
    setting is in effect", and
    :class:`~abicheck.compatibility_evaluation_config.GateConfig` rejects it
    for that reason. It is still a stated *selection* where a front end can
    really state it, though: an explicit ``--exit-code-scheme auto``
    contributes a candidate carrying that decision's answer and outranks a
    lower layer's concrete scheme, matching
    ``cli_helpers_compare.resolve_compare_config``. A project config's
    ``auto`` contributes the same way, but only when
    :attr:`ProjectCompatibilityInputs.exit_code_scheme_explicit` says the
    key was literally written -- ``BuildConfig``'s own default for that key
    *is* ``"auto"``, otherwise indistinguishable from an absent one.

    Raises :class:`~abicheck.compatibility_evaluation_resolver.FieldResolutionError`
    (D7 usage errors), :class:`~abicheck.compatibility_evaluation_resolver.PackConflictError`
    (D8), or :class:`~abicheck.errors.PackManifestError` (a malformed or
    mis-scoped pack manifest). Mapping those to an exit code is a front end's
    job; this module has no notion of one.
    """
    explicit = explicit or ExplicitCompatibilityInputs()
    layer = front_end.layer
    prov: dict[str, ValueProvenance] = {}

    def spell(cli_option: str, api_field: str | None = None) -> str:
        """The selector spelling for the front end that stated this value.

        A receipt has to name the input a replay would actually set. Letting
        every explicit candidate default to the CLI flag made an API-stated
        value claim a flag the caller never passed, and
        :func:`cross_front_end_differences` normalizes option spellings on
        purpose, so it could not catch that (Codex review). *api_field* is
        ``None`` for an input only the CLI can state, where the CLI spelling
        is the only truthful one.

        *api_field* is the :class:`~abicheck.api_types.CompareRequest`
        spelling, because that is the typed surface this resolver was built
        against -- but "the API" is not one namespace. ``ScanRequest`` names
        the same three inputs ``scope_to_public_surface``/``policy_file``/
        ``suppression``, so resolving its request at ``FrontEnd.API`` alone
        still produced field names that entity does not have (Codex review,
        a second round on the same receipt). *api_spellings* lets a caller
        remap them per request type; an unmapped field keeps the default.
        """
        if front_end is FrontEnd.CLI or api_field is None:
            return cli_option
        return (api_spellings or {}).get(api_field, api_field)

    policy_overrides_explicit = _policy_file_override_slugs(explicit.policy_file)
    policy_source = _PolicyFileSource.of(
        explicit.policy_file, explicit.policy_file_sha256
    )
    pack_paths = explicit.pack_paths

    # ── D8: which fields another layer already states, per pack namespace ────
    # A field stated elsewhere is exempt from pack-vs-pack conflict detection:
    # two packs may legitimately disagree about a field this resolution never
    # takes from a pack anyway. `detect_pack_conflicts` reads only the *keys*
    # of this mapping, so the value is a marker rather than the real resolved
    # value (which several of these fields do not have yet at this point).
    pinned_contract: dict[str, Hashable] = {}
    # One shared predicate, so what the resolver treats as "stated elsewhere"
    # and what a front end treats as shadowing cannot drift apart.
    if policy_file_pins_internal_namespaces(explicit.policy_file):
        pinned_contract[INTERNAL_NAMESPACES_FIELD] = _STATED_ELSEWHERE
    pinned_gate: dict[str, Hashable] = {}
    if (
        explicit.exit_code_scheme is not None
        or (profile is not None and profile.exit_code_scheme is not None)
        or (
            project is not None
            and (
                _stated_exit_code_scheme(project.exit_code_scheme) is not None
                # An explicit project-config `auto` is also a real statement
                # (see `project_scheme`'s own derivation below) -- without
                # this, two conflicting gate packs assigning
                # `gate.exit_code_scheme` were never flagged as conflicting
                # when the project had explicitly pinned `auto`, since this
                # check alone couldn't see that pin either.
                or project.exit_code_scheme_explicit
            )
        )
    ):
        pinned_gate[EXIT_CODE_SCHEME_FIELD] = _STATED_ELSEWHERE
    # A stated preset owns *every* category it expands into, so those fields
    # are exempt too: without this, two gate packs disagreeing about a
    # category raised a conflict the resolution would then have ignored
    # anyway, since the preset -- not either pack -- supplies the value
    # (Codex review, fresh evidence). One expression decides this and the
    # `default_is_stated` gate below, so the exemption and the precedence it
    # anticipates cannot drift apart.
    preset_stated = explicit.severity_preset is not None or (
        project is not None and project.severity_preset is not None
    )
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        if (
            preset_stated
            or explicit.severity_category(category) is not None
            or (project is not None and project.severity_category(category) is not None)
        ):
            pinned_gate[field_name] = _STATED_ELSEWHERE

    pack_option = spell("--pack", "pack_paths")
    # Read each manifest exactly once for this whole resolution: the three
    # pack resolvers below would otherwise re-read them, and a manifest edited
    # between those reads would leave one configuration's receipt entries
    # carrying different identities for the same pack (CodeRabbit review).
    loaded_packs = load_selected_packs(pack_paths)
    packs_by_field = resolve_selected_packs(
        pack_paths,
        explicit_overrides={
            PackKind.CONTRACT: pinned_contract,
            PackKind.GATE: pinned_gate,
            PackKind.POLICY: policy_overrides_explicit,
        },
        # The selecting front end's own layer/spelling: a pack selected
        # through the typed API must not have its `*.packs` receipt claim the
        # CLI selected it while the fields that same pack assigned claim the
        # API did (Codex review).
        layer=layer,
        option=pack_option,
        loaded=loaded_packs,
    )
    # Each entry carries its own pack identity/path, so a field a pack filled
    # gets a receipt naming the exact manifest revision it came from.
    contract_pack_fields = resolve_pack_field_assignments(
        pack_paths,
        PackKind.CONTRACT,
        explicit_overrides=pinned_contract,
        loaded=loaded_packs,
    )
    gate_pack_fields = resolve_pack_field_assignments(
        pack_paths, PackKind.GATE, explicit_overrides=pinned_gate, loaded=loaded_packs
    )

    # ── contract ────────────────────────────────────────────────────────────
    mode_candidates: list[FieldCandidate] = []
    if explicit.contract_mode is not None:
        mode_candidates.append(
            _candidate(
                layer,
                coerce_contract_mode(explicit.contract_mode),
                option=spell("--contract", "contract_mode"),
                source_kind="contract_mode",
            )
        )
    legacy_mode = legacy_contract_mode_candidate(
        scope_public_headers=bool(explicit.scope_public_headers),
        scope_public_headers_is_explicit=explicit.scope_public_headers is not None,
        # An API caller set a request field, not a CLI flag -- and which
        # field depends on the request type, so it goes through `spell()`
        # rather than hard-coding `CompareRequest`'s own name. `None` keeps
        # the CLI's existing "the alias names itself" behaviour.
        option=None if front_end is FrontEnd.CLI else spell("", "scope_public"),
    )
    if legacy_mode is not None:
        mode_candidates.append(legacy_mode)
    project_mode = (
        legacy_contract_mode_candidate(
            scope_public_headers=bool(project.scope_public),
            scope_public_headers_is_explicit=project.scope_public is not None,
            layer=SelectorLayer.PROJECT_CONFIG,
            # The project's own key and file, not the CLI flag's spelling: the
            # receipt has to name the source a replay would have to re-read.
            option="scope.public",
            path=project.path,
            sha256=project.sha256,
        )
        if project is not None
        else None
    )
    if project_mode is not None:
        mode_candidates.append(project_mode)

    mode, prov[CONTRACT_MODE_FIELD] = _resolve(
        CONTRACT_MODE_FIELD,
        mode_candidates,
        default=_default(BUILT_IN_DEFAULT_CONTRACT_MODE),
        require_legacy_alias_agreement=False,
    )

    unresolved, prov[CONTRACT_UNRESOLVED_FIELD] = _resolve(
        CONTRACT_UNRESOLVED_FIELD,
        [],
        default=_default("not_checkable"),
        pack=contract_pack_fields.get(CONTRACT_UNRESOLVED_FIELD),
        pack_layer=layer,
        pack_option=pack_option,
    )
    overlays, prov[CONTRACT_OVERLAYS_FIELD] = _resolve(
        CONTRACT_OVERLAYS_FIELD,
        [],
        default=_default(()),
        pack=contract_pack_fields.get(CONTRACT_OVERLAYS_FIELD),
        pack_layer=layer,
        pack_option=pack_option,
    )
    contract_packs, prov[CONTRACT_PACKS_FIELD] = packs_by_field[CONTRACT_PACKS_FIELD]

    contract = ContractConfig(
        mode=cast(ContractMode, mode),
        unresolved=cast(str, unresolved),
        overlays=cast("tuple[str, ...]", overlays),
        packs=contract_packs,
    )

    # ── surface ─────────────────────────────────────────────────────────────
    namespace_candidate = internal_namespaces_candidate(
        policy_file=explicit.policy_file,
        layer=layer,
        sha256=policy_source.sha256,
        option=spell("--policy", "policy_file_path"),
    )
    internal_namespaces, prov[INTERNAL_NAMESPACES_FIELD] = _resolve(
        INTERNAL_NAMESPACES_FIELD,
        [] if namespace_candidate is None else [namespace_candidate],
        default=_default(()),
        pack=contract_pack_fields.get(INTERNAL_NAMESPACES_FIELD),
        pack_layer=layer,
        pack_option=pack_option,
    )
    explicit_scope, prov[EXPLICIT_SCOPE_FIELD] = _explicit_scope(
        explicit,
        project,
        layer,
        # The CLI spelling is `scope.public_symbols`, not a flag: the
        # `--public-symbol`/`--public-symbols-list` pair were hidden
        # duplicates of that key and were removed, so a CLI-front-end value
        # here can only have come from `.abicheck.yml` and a receipt naming a
        # flag would send a replay to an unknown option (Codex review). The
        # typed API still states both fields, and `spell` still names those.
        symbol_option=spell("scope.public_symbols", "force_public_symbols"),
        list_option=spell("scope.public_symbols", "public_symbols_list"),
    )
    surface = SurfaceConfig(
        explicit_scope=explicit_scope,
        internal_namespaces=cast("tuple[str, ...]", internal_namespaces),
    )

    # ── assurance ───────────────────────────────────────────────────────────
    require_evidence, prov[REQUIRE_EVIDENCE_FIELD] = _resolve(
        REQUIRE_EVIDENCE_FIELD,
        [],
        default=_default(True),
        pack=contract_pack_fields.get(REQUIRE_EVIDENCE_FIELD),
        pack_layer=layer,
        pack_option=pack_option,
    )
    assurance = AssuranceConfig(require_evidence=cast(bool, require_evidence))

    # ── policy ──────────────────────────────────────────────────────────────
    base_candidates: list[FieldCandidate] = []
    if explicit.policy_file is not None:
        base_candidates.append(
            _candidate(
                layer,
                builtin_policy_identity(explicit.policy_file.base_policy),
                option=spell("--policy", "policy_file_path"),
                source_kind="policy_file",
                reference=explicit.policy_file.base_policy,
                sha256=policy_source.sha256,
                path=policy_source.path,
                field_location="base_policy",
            )
        )
    if explicit.policy_base is not None:
        base_candidates.append(
            _candidate(
                SelectorLayer.LEGACY_ALIAS,
                builtin_policy_identity(explicit.policy_base),
                # Same slot whichever option supplied it: an explicit
                # `--policy-file` outranks a `--required-symbol`-derived base
                # exactly as it outranks a typed `--policy`, which is what the
                # live run does too (`effective_policy`).
                option=explicit.policy_base_option or spell("--policy", "policy"),
                source_kind="builtin_policy",
                reference=explicit.policy_base,
                # The file that selected it, when the selecting option named
                # one -- `source_sha256`, not `sha256`: the digest identifies
                # the *source*, while the value is a file-less built-in
                # policy identity with a digest of its own.
                path=explicit.policy_base_path,
                source_sha256=explicit.policy_base_sha256,
            )
        )
    policy_base, prov[POLICY_BASE_FIELD] = _resolve(
        POLICY_BASE_FIELD,
        base_candidates,
        default=_default(builtin_policy_identity("strict_abi")),
        require_legacy_alias_agreement=False,
    )

    policy_packs, prov[POLICY_PACKS_FIELD] = packs_by_field[POLICY_PACKS_FIELD]
    policy_overrides = resolve_policy_pack_overrides(
        pack_paths, explicit_overrides=policy_overrides_explicit, loaded=loaded_packs
    )
    # One entry per pack that actually supplied a surviving override, each
    # naming its own manifest: a pack whose every assignment the policy file
    # also overrides contributed nothing, and a contract/gate pack in the same
    # `--pack` list never could. Collapsing them into a single pathless hop
    # left the receipt unable to say which manifest supplied which value
    # (Codex review, two rounds).
    # Deduplicated: naming one path twice selects one pack, exactly as
    # `resolve_selected_packs` already treats it, so repeating it must not
    # make an otherwise identical resolution unequal (Codex review, fresh
    # evidence).
    override_pack_contributors = list(
        dict.fromkeys(
            (path, pack.identity)
            for path, pack in loaded_packs
            if pack.kind is PackKind.POLICY
            and set(pack.assignments) - set(policy_overrides_explicit)
        )
    )
    prov[POLICY_OVERRIDES_FIELD] = _overrides_provenance(
        layer,
        policy_source=policy_source,
        pack_contributors=override_pack_contributors,
        pack_option=pack_option,
        policy_file_option=spell("--policy", "policy_file_path"),
        explicit_overrides=policy_overrides_explicit,
    )
    policy = CompatibilityPolicyConfig(
        base=cast(ImmutableIdentity, policy_base),
        packs=policy_packs,
        overrides=policy_overrides,
    )

    # ── gate ────────────────────────────────────────────────────────────────
    preset_candidates: list[FieldCandidate] = []
    if explicit.severity_preset is not None:
        explicit_preset = severity_preset_identity(explicit.severity_preset)
        preset_candidates.append(
            _candidate(
                layer,
                explicit_preset,
                option=spell("--severity-preset", "severity_preset"),
                source_kind="severity_preset",
                reference=explicit.severity_preset,
                version=explicit_preset.version,
                sha256=explicit_preset.sha256,
            )
        )
    if project is not None and project.severity_preset is not None:
        project_preset = severity_preset_identity(project.severity_preset)
        preset_candidates.append(
            _candidate(
                SelectorLayer.PROJECT_CONFIG,
                project_preset,
                option="severity.preset",
                source_kind="severity_preset",
                reference=project.severity_preset,
                version=project_preset.version,
                sha256=project_preset.sha256,
                source_sha256=project.sha256,
                path=project.path,
            )
        )
    gate_preset, prov[GATE_PRESET_FIELD] = _resolve(
        GATE_PRESET_FIELD, preset_candidates, default=_default(None)
    )

    preset_base = (
        SEVERITY_PRESETS[cast(ImmutableIdentity, gate_preset).id]
        if gate_preset is not None
        else SeverityConfig()
    )
    levels: dict[str, SeverityLevel] = {}
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        candidates: list[FieldCandidate] = []
        stated = explicit.severity_category(category)
        if stated is not None:
            candidates.append(
                _candidate(
                    layer,
                    SeverityLevel(stated),
                    option=spell(
                        f"--severity-{category.replace('_', '-')}",
                        f"severity_{category}",
                    ),
                    source_kind="severity_override",
                )
            )
        project_stated = (
            project.severity_category(category) if project is not None else None
        )
        if project_stated is not None:
            candidates.append(
                _candidate(
                    SelectorLayer.PROJECT_CONFIG,
                    SeverityLevel(project_stated),
                    option=f"severity.{category}",
                    source_kind="severity_override",
                    sha256=project.sha256 if project is not None else None,
                    path=project.path if project is not None else None,
                )
            )
        value, prov[field_name] = _resolve(
            field_name,
            candidates,
            default=_default(
                getattr(preset_base, category),
                source_kind="severity_preset" if gate_preset is not None else None,
            ),
            pack=gate_pack_fields.get(field_name),
            pack_layer=layer,
            pack_option=pack_option,
            # A stated preset already decided every category it covers, so a
            # gate pack may not quietly replace one of them -- the same
            # statement that exempted these fields from pack-vs-pack conflict
            # detection above.
            default_is_stated=preset_stated,
        )
        if preset_stated and prov[field_name].layer is SelectorLayer.BUILT_IN_DEFAULT:
            # The level came from the preset, so the receipt names the preset:
            # its layer, its selector, and the identity/digest of the exact
            # preset revision. `resolve_field` requires the `default` slot to
            # carry BUILT_IN_DEFAULT, and the derived level cannot become an
            # ordinary candidate either -- at the preset's own layer it would
            # tie with a per-category flag from that same layer, and refining
            # a preset with one category is legal, not a conflict. So the
            # value resolves as a default and the receipt is corrected after
            # the fact, leaving precedence untouched (Codex review, fresh
            # evidence: the receipt claimed BUILT_IN_DEFAULT for a level the
            # user had chosen).
            prov[field_name] = replace(
                prov[GATE_PRESET_FIELD], source_kind="severity_preset"
            )
        levels[category] = cast(SeverityLevel, value)

    severity_active = _severity_active(explicit, project, gate_pack_fields)
    auto_scheme = "severity" if severity_active else "legacy"
    scheme_candidates: list[FieldCandidate] = []
    if explicit.exit_code_scheme is not None:
        # An explicit `--exit-code-scheme auto` is a *stated selection* --
        # "decide from whether a severity setting is in effect" -- so it
        # contributes a candidate carrying that decision's answer, and
        # outranks a lower layer's concrete scheme exactly as any other
        # explicit value would. Treating it as "not stated" instead let a
        # project config's concrete scheme win, diverging from
        # `cli_helpers_compare.resolve_compare_config`, where the CLI value
        # wins whatever it is (Codex review).
        scheme_candidates.append(
            _candidate(
                layer,
                _stated_exit_code_scheme(explicit.exit_code_scheme) or auto_scheme,
                option="--exit-code-scheme",
                source_kind="exit_code_scheme",
                reference=explicit.exit_code_scheme,
            )
        )
    # A `--profile` fills this in only where the user left the flag alone, so
    # it never ties with the explicit candidate above -- it sits between that
    # and the project config, exactly where D7 puts `run_profile`. See
    # `RunProfileInputs` for why a gate field accepts that layer at all.
    profile_scheme = (
        _stated_exit_code_scheme(profile.exit_code_scheme) if profile else None
    )
    if profile_scheme is not None:
        scheme_candidates.append(
            _candidate(
                SelectorLayer.RUN_PROFILE,
                profile_scheme,
                option="--profile",
                source_kind="run_profile",
                reference=profile.name if profile is not None else None,
            )
        )
    # `BuildConfig.exit_code_scheme` defaults to the *string* "auto" when
    # absent, so unlike the CLI flag, a bare `"auto"` is only a real
    # statement when `exit_code_scheme_explicit` confirms the key was
    # actually written -- mirroring the explicit-CLI branch above.
    project_scheme = (
        _stated_exit_code_scheme(project.exit_code_scheme) or auto_scheme
        if project is not None and project.exit_code_scheme_explicit
        else (_stated_exit_code_scheme(project.exit_code_scheme) if project else None)
    )
    if project_scheme is not None:
        scheme_candidates.append(
            _candidate(
                SelectorLayer.PROJECT_CONFIG,
                project_scheme,
                option="exit_code_scheme",
                source_kind="exit_code_scheme",
                sha256=project.sha256 if project is not None else None,
                path=project.path if project is not None else None,
            )
        )
    exit_code_scheme, prov[EXIT_CODE_SCHEME_FIELD] = _resolve(
        EXIT_CODE_SCHEME_FIELD,
        scheme_candidates,
        default=_default(auto_scheme, source_kind="auto"),
        pack=gate_pack_fields.get(EXIT_CODE_SCHEME_FIELD),
        pack_layer=layer,
        pack_option=pack_option,
        allow_run_profile=True,
    )

    gate_packs, prov[GATE_PACKS_FIELD] = packs_by_field[GATE_PACKS_FIELD]
    gate = GateConfig(
        exit_code_scheme=cast(str, exit_code_scheme),
        preset=cast("ImmutableIdentity | None", gate_preset),
        packs=gate_packs,
        severity=SeverityConfig(**levels),
    )

    # ── suppressions ────────────────────────────────────────────────────────
    suppressions: SuppressionConfig | None = None
    if explicit.suppression is not None:
        suppressions = SuppressionConfig(
            sha256=explicit.suppression.sha256, rules=explicit.suppression.rules
        )
        prov[SUPPRESSIONS_FIELD] = ValueProvenance(
            layer=layer,
            source_kind="suppression_file",
            # Same rule as every other file-derived entry: the receipt names
            # the digest of the source, not just its path (CodeRabbit review).
            sha256=explicit.suppression.sha256,
            path=explicit.suppression.path,
            selected_by=(
                SelectedByEntry(
                    layer=layer,
                    option=spell("--suppress", "suppress"),
                    path=explicit.suppression.path,
                ),
            ),
        )
    else:
        prov[SUPPRESSIONS_FIELD] = ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)

    return CompatibilityEvaluationConfig(
        contract=contract,
        # No front end selects evidence providers or a variant set today --
        # see this module's docstring.
        evidence=EvidenceConfig(),
        surface=surface,
        assurance=assurance,
        policy=policy,
        gate=gate,
        suppressions=suppressions,
        provenance=prov,
    )


def _stated_exit_code_scheme(value: str | None) -> str | None:
    """``None`` unless *value* names a real, already-resolved scheme.

    ``"auto"`` is a resolution-time choice, not a value (ADR-037 D12), so it
    never passes through as one. Whether *selecting* ``auto`` still counts as
    stating the field depends on the layer, and is decided by the caller --
    see :func:`resolve_compatibility_evaluation_config`.
    """
    if value is None or value == "auto":
        return None
    return value


def _severity_active(
    explicit: ExplicitCompatibilityInputs,
    project: ProjectCompatibilityInputs | None,
    gate_pack_fields: Mapping[str, Hashable],
) -> bool:
    """Whether *any* severity setting is in effect (drives ``auto``).

    Mirrors ``cli_helpers_compare.resolve_compare_config``'s own
    ``severity_active`` -- a preset or any per-category value, from the CLI or
    the project config -- and additionally counts a ``kind: gate`` pack that
    assigns a category, since a pack-supplied severity is no less "in effect"
    than a config-supplied one.
    """
    if explicit.severity_preset is not None or (
        project is not None and project.severity_preset is not None
    ):
        return True
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        if explicit.severity_category(category) is not None:
            return True
        if project is not None and project.severity_category(category) is not None:
            return True
        if field_name in gate_pack_fields:
            return True
    return False


def _overrides_provenance(
    layer: SelectorLayer,
    *,
    policy_source: _PolicyFileSource,
    pack_contributors: Sequence[tuple[str, ImmutableIdentity]],
    pack_option: str,
    policy_file_option: str,
    explicit_overrides: Mapping[str, Verdict],
) -> ValueProvenance:
    """Receipt entry for the merged ``policy.overrides`` mapping.

    Like ``surface.explicit_scope``, this field is composed rather than
    won outright (D8: "explicit per-``ChangeKind`` override > selected packs
    > base policy" merges across sources instead of picking one), so the
    receipt records the highest-precedence contributor as its layer and lists
    **each source that actually contributed** in ``selected_by`` -- one entry
    per contributing pack, naming that pack's own manifest *and its identity*,
    so a reader can tell which of several selected manifests supplied a given
    value and prove which revision of it did. A source whose every assignment
    a higher-precedence one shadowed did not contribute and is not listed.
    """
    selected_by: list[SelectedByEntry] = []
    if explicit_overrides:
        selected_by.append(
            SelectedByEntry(
                layer=layer, option=policy_file_option, path=policy_source.path
            )
        )
    # Each hop carries its own pack's identity: for two or more contributors
    # there is no single winning manifest for the entry-level
    # `reference`/`version`/`sha256` to describe, so without this the
    # revisions that actually produced the merged mapping were lost --
    # `policy.packs` cannot stand in for them, since it also lists packs
    # every one of whose assignments was shadowed (Codex review, fresh
    # evidence).
    selected_by.extend(
        SelectedByEntry(layer=layer, option=pack_option, path=path, identity=identity)
        # Sorted on the fields themselves rather than on tuple order, so the
        # receipt does not depend on `ImmutableIdentity`'s default ordering
        # if a path ever contributes more than once (CodeRabbit review).
        for path, identity in sorted(
            pack_contributors,
            key=lambda c: (c[0], c[1].id, c[1].version, c[1].sha256),
        )
    )
    if not selected_by:
        return ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)
    if explicit_overrides:
        return ValueProvenance(
            layer=layer,
            source_kind="policy_file",
            sha256=policy_source.sha256,
            path=policy_source.path,
            selected_by=tuple(selected_by),
        )
    # Pack-only: name the single contributing manifest outright when there is
    # exactly one, rather than claiming a source the value did not all come
    # from.
    only = pack_contributors[0] if len(pack_contributors) == 1 else None
    return ValueProvenance(
        layer=layer,
        source_kind="pack_manifest",
        reference=only[1].id if only else None,
        version=only[1].version if only else None,
        sha256=only[1].sha256 if only else None,
        path=only[0] if only else None,
        selected_by=tuple(selected_by),
    )


# --------------------------------------------------------------------------
# Front-end adapters.
# --------------------------------------------------------------------------

#: ``compare`` kwargs that carry a non-``None`` click default, so their value
#: alone cannot distinguish "the user typed this" from "click filled it in".
#: A live Click caller resolves these with
#: ``ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE``.
DEFAULTED_COMPARE_PARAMETERS: frozenset[str] = frozenset(
    {"policy", "scope_public_headers"}
)


def _load_policy_file(path: str | Path) -> PolicyFile:
    """Load a ``--policy-file`` document from a path an invocation named."""
    from .policy_file import PolicyFile as _PolicyFile

    return _PolicyFile.load(Path(path))


def compare_cli_inputs(
    kwargs: Mapping[str, Any],
    *,
    explicit_parameters: Collection[str] = (),
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
    policy_base_option: str | None = None,
    policy_base_path: str | None = None,
    policy_base_sha256: str | None = None,
    public_symbols_list: PublicSymbolsList | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize the ``compare`` command's real kwargs into resolver inputs.

    *kwargs* is the command's own parameter mapping (``cli.py``'s option
    destinations, verbatim). *explicit_parameters* names the parameters the
    user actually typed; it only matters for
    :data:`DEFAULTED_COMPARE_PARAMETERS`, whose click defaults are
    indistinguishable from a stated value. Every other option already uses
    ``None``/``()`` for "not given", so it is read directly.

    *policy_file* and *suppression* let a caller pass what it already loaded
    (the CLI loads both long before configuration would be resolved). Left
    unset, they are loaded from the ``policy_file_path``/``suppress`` kwargs
    the command itself carries -- silently ignoring a path the invocation
    really named would make the resolved configuration misrepresent the run.

    *policy_base_option* names the option that selected ``policy`` when it was
    not ``--policy``: ``compare`` switches an untouched ``--policy`` to
    ``plugin_abi`` for a ``--required-symbol``/``--required-symbols``
    contract, and that value is read as stated regardless of
    *explicit_parameters* (it was not typed, but it was chosen -- see
    :attr:`ExplicitCompatibilityInputs.policy_base_option`).
    *policy_base_path*/*policy_base_sha256* identify the list file when that
    option is the file form.

    *public_symbols_list* is the same "pass what you already read" affordance
    as *policy_file*/*suppression*: the CLI reads this file to build the live
    force-public set, so re-reading it here could pair the receipt's digest
    with content that did not score the run -- and a file deleted mid-run
    would fail an otherwise-finished comparison (Codex review).

    ``--public-symbols-list`` is read into its own
    :class:`PublicSymbolsList` rather than flattened into ``public_symbols``,
    so a list-file-only invocation resolves a real ``surface.explicit_scope``
    *and* a receipt that names the file it came from. The union itself is
    formed in :func:`_explicit_scope`, matching what the live CLI's
    :func:`collect_force_public_symbols` produces.
    """
    typed = set(explicit_parameters)

    def _defaulted(name: str) -> Any:
        return kwargs.get(name) if name in typed else None

    symbols_list_path = kwargs.get("public_symbols_list")
    if public_symbols_list is None and symbols_list_path is not None:
        public_symbols_list = PublicSymbolsList.from_file(symbols_list_path)
    symbols_list = public_symbols_list
    if policy_file is None and kwargs.get("policy_file_path") is not None:
        policy_file = _load_policy_file(kwargs["policy_file_path"])
    if suppression is None and kwargs.get("suppress") is not None:
        suppression = SuppressionSource.from_file(
            kwargs["suppress"],
            require_justification=bool(kwargs.get("require_justification")),
        )
    return ExplicitCompatibilityInputs(
        contract_mode=kwargs.get("contract_mode"),
        scope_public_headers=_defaulted("scope_public_headers"),
        policy_base=(
            kwargs.get("policy") if policy_base_option else _defaulted("policy")
        ),
        policy_base_option=policy_base_option,
        policy_base_path=policy_base_path,
        policy_base_sha256=policy_base_sha256,
        policy_file=policy_file,
        public_symbols=tuple(kwargs.get("public_symbols") or ()),
        public_symbols_list=symbols_list,
        suppression=suppression,
        exit_code_scheme=kwargs.get("exit_code_scheme"),
        severity_preset=kwargs.get("severity_preset"),
        severity_abi_breaking=kwargs.get("severity_abi_breaking"),
        severity_potential_breaking=kwargs.get("severity_potential_breaking"),
        severity_quality_issues=kwargs.get("severity_quality_issues"),
        severity_addition=kwargs.get("severity_addition"),
        pack_paths=tuple(str(p) for p in kwargs.get("pack_paths") or ()),
    )


def compare_request_inputs(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize a typed :class:`~abicheck.api_types.CompareRequest`.

    Every field is read as *stated*: unlike the CLI, the typed request has no
    "unset" representation for ``policy`` (defaults to ``"strict_abi"``) or
    ``scope_public`` (defaults to ``True``) -- a caller constructing the
    request chose those values, whether deliberately or by accepting the
    dataclass default. ``checker.compare`` treats the same two the same way
    and for the same reason (see ``_apply_contract_evaluation_shadow``'s
    ``scope_public_headers_is_explicit=True``).

    ``severity_preset``/``exit_code_scheme`` forward too (2026-09-03, Codex
    review, PR #1032) -- else this receipt's ``gate.*`` block could
    disagree with ``CompareResult.exit_decision``, which reads them
    directly. No per-category or pack fields: neither exists on
    ``CompareRequest`` (gate/reporting concerns the Tier-2 API doesn't
    own), so both resolve to their built-in defaults here, matching a CLI
    run stating neither (:func:`cross_front_end_differences`).

    ``policy_file_path`` and ``suppress`` *are* request fields, though, and
    are loaded from the request when the caller does not pass an
    already-loaded *policy_file*/*suppression*. Ignoring them unless the
    caller separately re-loaded the same files would let a request naming an
    ``sdk_vendor`` policy file resolve to ``strict_abi`` with no suppression
    source at all -- an effective configuration that does not represent the
    typed request it was built from (Codex review).
    """
    if policy_file is None and request.policy_file_path is not None:
        policy_file = _load_policy_file(request.policy_file_path)
    if suppression is None and request.suppress is not None:
        suppression = SuppressionSource.from_file(request.suppress)
    return ExplicitCompatibilityInputs(
        contract_mode=request.contract_mode,
        scope_public_headers=request.scope_public,
        policy_base=request.policy,
        policy_file=policy_file,
        public_symbols=tuple(sorted(request.force_public_symbols or ())),
        suppression=suppression,
        exit_code_scheme=request.exit_code_scheme,
        severity_preset=request.severity_preset,
    )


def compatibility_config_from_compare_request(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
    project: ProjectCompatibilityInputs | None = None,
) -> CompatibilityEvaluationConfig:
    """One-call adapter: typed request -> resolved effective configuration."""
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.API,
        explicit=compare_request_inputs(
            request, policy_file=policy_file, suppression=suppression
        ),
        project=project,
    )


# --------------------------------------------------------------------------
# Phase 1's gate, as an executable comparison.
# --------------------------------------------------------------------------

_SECTIONS = ("contract", "evidence", "surface", "assurance", "policy", "gate")


def _normalized_provenance(prov: ValueProvenance) -> tuple[Any, ...]:
    """*prov* with the two front-end-specific details dropped, and no more.

    ADR-049 D7 puts ``explicit_cli`` and ``api_request`` in one precedence
    tier, and the same semantic input is spelled differently by construction
    (``--policy`` vs. the ``policy`` field). Exactly those two -- which of the
    pair the layer is, and the option spelling recorded with it -- are
    legitimately different *records of how* a value was stated.

    **Everything else is compared**, including each entry's own ``identity``,
    ``sha256``, ``path``, and ``argument_index``: dropping the digest let two
    runs whose receipts name differently-digested policy files compare as
    equivalent, which is precisely the drift the digest exists to catch
    (Codex review).
    """
    explicit = {SelectorLayer.EXPLICIT_CLI, SelectorLayer.API_REQUEST}

    def _layer(value: SelectorLayer) -> str:
        return "explicit" if value in explicit else value.value

    return (
        _layer(prov.layer),
        prov.source_kind,
        prov.reference,
        prov.version,
        prov.sha256,
        prov.path,
        prov.field_location,
        tuple(
            (
                _layer(entry.layer),
                entry.argument_index,
                entry.path,
                entry.identity,
                entry.sha256,
            )
            for entry in prov.selected_by
        ),
        None
        if prov.shadowed_legacy is None
        else _normalized_provenance(prov.shadowed_legacy),
    )


def cross_front_end_differences(
    a: CompatibilityEvaluationConfig, b: CompatibilityEvaluationConfig
) -> list[str]:
    """Every way *a* and *b* differ beyond which front end stated them.

    The executable form of ADR-049 Phase 1's gate: "every front end resolves
    equivalent semantic input to an equal ``CompatibilityEvaluationConfig``
    and provenance receipt." Values must be equal outright; provenance must be
    equal after normalizing the one permitted difference
    (:func:`_normalized_provenance`). Returns a human-readable list so a
    failing comparison says *which* field diverged, not merely that one did.
    """
    differences: list[str] = []
    for section in _SECTIONS:
        if getattr(a, section) != getattr(b, section):
            differences.append(
                f"{section}: {getattr(a, section)!r} != {getattr(b, section)!r}"
            )
    if a.suppressions != b.suppressions:
        differences.append(f"suppressions: {a.suppressions!r} != {b.suppressions!r}")

    keys_a, keys_b = set(a.provenance), set(b.provenance)
    for missing in sorted(keys_a ^ keys_b):
        differences.append(f"provenance: {missing!r} present on only one side")
    for key in sorted(keys_a & keys_b):
        norm_a = _normalized_provenance(a.provenance[key])
        norm_b = _normalized_provenance(b.provenance[key])
        if norm_a != norm_b:
            differences.append(f"provenance[{key!r}]: {norm_a!r} != {norm_b!r}")
    return differences


def cross_front_end_equivalent(
    a: CompatibilityEvaluationConfig, b: CompatibilityEvaluationConfig
) -> bool:
    """``True`` when :func:`cross_front_end_differences` finds nothing."""
    return not cross_front_end_differences(a, b)


#: The tiers whose hops name an input the *caller* stated, as opposed to a
#: key inside a file the caller pointed at. Only these are checked against a
#: request type's fields -- see :func:`unstatable_selectors`.
_REQUEST_STATED_LAYERS = frozenset(
    {SelectorLayer.API_REQUEST, SelectorLayer.LEGACY_ALIAS}
)


def unstatable_selectors(
    config: CompatibilityEvaluationConfig, *, request_type: type | None = None
) -> list[str]:
    """Every hop in *config* that names an input its own layer cannot state.

    A receipt exists so a run's inputs can be identified and replayed, so a
    hop claiming an input the caller never had is worse than a missing one:
    it is confidently wrong. Four instances have now been found by review:
    the original explicit-candidate default that motivated ``spell()``,
    ``--policy``/``--scope-public-headers`` on a ``ScanRequest``,
    ``--severity-preset`` on the MCP tool, and -- once those were routed through
    ``spell()`` -- a ``ScanRequest`` receipt naming ``CompareRequest``'s
    ``scope_public``/``policy_file_path``/``suppress``, which is a *different*
    entity's field list.

    Two checks, because that fourth instance proved the first insufficient
    on its own:

    * every ``API_REQUEST`` hop must not name a CLI flag (a candidate built
      with a hard-coded ``"--flag"`` instead of going through ``spell()``);
    * given *request_type*, every hop at a *front-end-stated* tier
      (``API_REQUEST`` and ``LEGACY_ALIAS``) must name a real field of it.
      Without this, "not a flag" passes for any plausible-looking
      identifier, which is exactly how one wrong spelling was replaced by
      another. Pass the dataclass the front end actually accepts.

    ``LEGACY_ALIAS`` is included deliberately, and only under *request_type*:
    the reported ``scope_public`` hop sat at that tier, not ``API_REQUEST``
    (``--policy``/``scope_public`` are D7 aliases for the fields they
    select), so a check restricted to the request tier would have passed
    the very defect it was written for. Layers that describe a *file*
    (``PROJECT_CONFIG``, ``RUN_RECIPE``, ``RUN_PROFILE``) are excluded:
    those hops correctly name config keys such as ``severity.preset``,
    which are not request fields and never should be.

    :func:`cross_front_end_differences` structurally cannot catch either:
    :func:`_normalized_provenance` drops option spellings *on purpose*, since
    the same semantic input is legitimately spelled differently per front
    end. That normalization is what makes the equality gate meaningful and
    also what makes it blind here, so this is a separate check rather than a
    stricter setting of that one.

    Returns human-readable descriptions so a failure names the field and the
    spelling, not merely that one exists. Deliberately one-directional: a CLI
    hop carrying a bare field name is not an error, because several CLI
    inputs (a project-config key, a composed scope) genuinely have no flag.
    """
    import dataclasses

    known: frozenset[str] | None = None
    request_name = ""
    if request_type is not None and dataclasses.is_dataclass(request_type):
        known = frozenset(f.name for f in dataclasses.fields(request_type))
        request_name = request_type.__name__
    offenders: list[str] = []
    for field_name in sorted(config.provenance):
        prov = config.provenance[field_name]
        chain = [
            prov,
            *([] if prov.shadowed_legacy is None else [prov.shadowed_legacy]),
        ]
        for entry in chain:
            for hop in entry.selected_by:
                option = hop.option or ""
                if hop.layer is SelectorLayer.API_REQUEST and option.startswith("--"):
                    offenders.append(
                        f"{field_name}: api_request hop names the CLI flag "
                        f"{option!r}, which no API caller can pass"
                    )
                elif (
                    known is not None
                    and option
                    and hop.layer in _REQUEST_STATED_LAYERS
                    and option not in known
                ):
                    offenders.append(
                        f"{field_name}: {hop.layer.value} hop names {option!r}, "
                        f"which is not a field of {request_name}"
                    )
    return offenders

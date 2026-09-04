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

"""ADR-049 Phase 1's real front-end wirings: real CLI/config inputs ->
resolved ``CompatibilityEvaluationConfig`` fields.

Every other ADR-049 module built so far (``contract_relevance_types.py``,
``compatibility_evaluation_config.py``, ``compatibility_evaluation_resolver.py``)
is pure vocabulary/shape/resolution logic. This module is where real input
first reaches :func:`~abicheck.compatibility_evaluation_resolver.resolve_field`:

- :func:`resolve_legacy_contract_mode` takes the *actual*
  ``--scope-public-headers``/``--no-scope-public-headers`` CLI flag
  (``cli_options.py``'s ``scope_options``, the only scope-shaped option that
  exists in the CLI today) and produces a real ``contract.mode`` resolution,
  via the D2 alias table in
  :data:`~abicheck.contract_relevance_types.LEGACY_SCOPE_FLAG_CONTRACT_MODE`.
- :func:`resolve_internal_namespaces` takes the *actual* ``--policy-file``
  YAML's ``internal_namespaces`` list (``policy_file.py``'s ``PolicyFile``,
  the only front end that can set this field today -- there is no CLI flag
  for it) and produces a real ``surface.internal_namespaces`` resolution.
- :func:`resolve_pack_field_assignments` takes the same pack paths and one
  non-policy :class:`~abicheck.compatibility_evaluation_packs.PackKind`, and
  routes each selected pack's own ``field name -> value`` assignments onto
  the typed effective-config fields that namespace is allowed to set --
  the per-field router
  :func:`resolve_policy_pack_overrides`'s docstring names as "separate,
  still-unwired follow-up work" for the contract/gate namespaces (they have
  no open ``overrides``-shaped bag the way ``CompatibilityPolicyConfig``
  does, so each assignable field needs an explicit route).
- :func:`resolve_selected_packs` takes a real set of pack-manifest paths (the
  shape a future ``--pack <path>``-style, repeatable CLI/config option would
  supply), loads each with
  :func:`~abicheck.compatibility_evaluation_packs.load_pack_manifest`, runs
  D8's pack-vs-pack conflict check
  (:func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`)
  once per namespace, and produces the three real ``contract.packs``/
  ``policy.packs``/``gate.packs`` resolutions. This is the first wiring that
  composes two previously-separate, previously-uncomposed Phase 1 pieces
  (the pack-manifest loader and ``detect_pack_conflicts``) into one real
  front-end path, closing the gap the plan's own Phase 1 progress notes
  named explicitly: "no front end resolves a ``--pack <name>``-style CLI/
  config input ... and no call site invokes ``detect_pack_conflicts`` with
  real packs during config resolution."

None of these functions is called from any live command yet. ADR-049's
own rollout plan puts wiring a resolved value into an authoritative code
path behind a later phase (Phase 3's shadow contract evaluator validates
resolution against real traffic before it can affect a gate decision, and
the default flip is Phase 7) -- see
``docs/contribute/plans/public-contract-default.md``. Landing each wiring
function itself, fully tested against its real input's semantics, is what
Phase 1's gate asks for: "every front end resolves equivalent semantic
input to an equal ``CompatibilityEvaluationConfig`` and provenance receipt."
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from .change_registry_types import Verdict
from .compatibility_evaluation_config import (
    VALID_UNRESOLVED_BEHAVIORS,
    ImmutableIdentity,
    SelectedByEntry,
    ValueProvenance,
)
from .compatibility_evaluation_packs import (
    LoadedPack,
    PackKind,
    assignments_for_conflict_check,
    load_pack_manifest,
)
from .compatibility_evaluation_resolver import (
    FieldCandidate,
    detect_pack_conflicts,
    resolve_field,
)
from .contract_relevance_types import (
    LEGACY_SCOPE_FLAG_CONTRACT_MODE,
    ContractMode,
    LegacyScopeFlag,
    SelectorLayer,
)
from .errors import PackManifestError

if TYPE_CHECKING:
    from .policy_file import PolicyFile

CONTRACT_MODE_FIELD = "contract.mode"
_CONTRACT_MODE_FIELD = CONTRACT_MODE_FIELD  # backward-compatible alias

#: What ``contract.mode`` resolves to when the legacy flag was never
#: mentioned at all -- deliberately equal to ``--scope-public-headers``'s
#: own CLI default (``scope_public_headers=True`` in ``cli_options.py``),
#: so accepting ADR-049 does not, by itself, change today's real default
#: behavior. The actual default *flip* (if any) is Phase 7's decision.
BUILT_IN_DEFAULT_CONTRACT_MODE = LEGACY_SCOPE_FLAG_CONTRACT_MODE[
    LegacyScopeFlag.SCOPE_PUBLIC_HEADERS
]
_BUILT_IN_DEFAULT_MODE = BUILT_IN_DEFAULT_CONTRACT_MODE  # backward-compatible alias


def policy_file_pins_internal_namespaces(policy_file: Any) -> bool:
    """Does *policy_file* state ``surface.internal_namespaces``?

    The single definition of D7's "another layer already states this field"
    for this one field. The resolver uses it to build `pinned_contract` (so a
    selected pack never overrides a stated value), and
    ``cli_compare_receipt.validate_pack_manifests`` uses it to know whether a
    pack's assignment can reach runtime at all before precedence is resolved.
    Shared rather than restated: a second copy is a second thing that can
    disagree with the resolver about what shadows what (Codex review, which
    caught a coarser "was any --policy-file given" proxy treating a file that
    only sets `base_policy` as shadowing).

    An explicit ``internal_namespaces: []`` states "none", so it pins the
    field exactly as a populated list does.
    """
    if policy_file is None:
        return False
    return bool(
        getattr(policy_file, "internal_namespaces", None)
        or getattr(policy_file, "internal_namespaces_stated", False)
    )


def legacy_contract_mode_candidate(
    *,
    scope_public_headers: bool,
    scope_public_headers_is_explicit: bool,
    layer: SelectorLayer = SelectorLayer.LEGACY_ALIAS,
    option: str | None = None,
    path: str | None = None,
    sha256: str | None = None,
) -> FieldCandidate | None:
    """Build the ``contract.mode`` candidate the legacy scope flag contributes.

    Extracted from :func:`resolve_legacy_contract_mode` (whose behavior is
    unchanged -- it now calls this) so a caller that resolves ``contract.mode``
    against *more* than the legacy flag alone -- an explicit ``--contract``
    value, a project config's own ``scope.public`` -- can reuse the exact
    same candidate construction instead of re-deriving the D2 alias mapping
    a second time. ``compatibility_evaluation_frontend.py`` is that caller.

    Returns ``None`` when the flag was not actually typed
    (*scope_public_headers_is_explicit* is ``False``): an untouched flag
    contributes no candidate at all, per ADR-049 D7's "a selector layer only
    participates when it actually selected something".

    *layer* exists for the one legitimate non-CLI source of this same legacy
    spelling: ``.abicheck.yml``'s ``scope.public`` key, which states the same
    aliased value at :data:`~abicheck.contract_relevance_types.SelectorLayer.PROJECT_CONFIG`
    rather than as a flag typed on this invocation. D7 resolves precedence by
    layer, not by spelling -- but the *receipt* still has to name the real
    source, so such a caller passes its own *option* (``scope.public``) and
    *path* too. Defaulting those to the CLI flag's spelling with no path made
    a project-config receipt claim a flag the user never typed, and gave a
    replay no way to find the file it actually came from (Codex review).
    *sha256* is that file's own content digest, which such a caller should
    pass as well: naming the path alone leaves an edit to it undetectable on
    replay, the very drift a digest exists to catch (Codex review, fresh
    evidence).
    """
    if not scope_public_headers_is_explicit:
        return None
    flag = (
        LegacyScopeFlag.SCOPE_PUBLIC_HEADERS
        if scope_public_headers
        else LegacyScopeFlag.NO_SCOPE_PUBLIC_HEADERS
    )
    selector = option or (
        "--scope-public-headers"
        if scope_public_headers
        else "--no-scope-public-headers"
    )
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=layer,
            source_kind="legacy_scope_flag",
            reference=flag.value,
            sha256=sha256,
            path=path,
            selected_by=(
                SelectedByEntry(
                    layer=layer, option=selector, path=path, sha256=sha256
                ),
            ),
        ),
        value=LEGACY_SCOPE_FLAG_CONTRACT_MODE[flag],
    )


def resolve_legacy_contract_mode(
    *,
    scope_public_headers: bool,
    scope_public_headers_is_explicit: bool,
) -> tuple[ContractMode, ValueProvenance]:
    """Resolve ``contract.mode`` from the real legacy scope flag.

    ``scope_public_headers`` is the flag's boolean value (``cli_options.py``
    ``scope_options``'s ``scope_public_headers`` destination).
    ``scope_public_headers_is_explicit`` distinguishes the user actually
    typing ``--scope-public-headers``/``--no-scope-public-headers`` (a real
    ``legacy_alias``-layer input, e.g. via Click's
    ``ctx.get_parameter_source(...) is ParameterSource.COMMANDLINE``) from
    the flag merely carrying its own click default -- an untouched flag
    contributes no candidate at all and falls through to
    :data:`_BUILT_IN_DEFAULT_MODE`, matching ADR-049 D7's precedence model
    (a selector layer only participates when it actually selected
    something).

    Returns the resolved :class:`~abicheck.contract_relevance_types.ContractMode`
    and its :class:`~abicheck.compatibility_evaluation_config.ValueProvenance`.
    This performs no I/O and touches no CLI/Click objects directly, so it is
    testable with plain booleans.
    """
    default = FieldCandidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
        value=BUILT_IN_DEFAULT_CONTRACT_MODE,
    )

    legacy = legacy_contract_mode_candidate(
        scope_public_headers=scope_public_headers,
        scope_public_headers_is_explicit=scope_public_headers_is_explicit,
    )
    candidates: list[FieldCandidate] = [] if legacy is None else [legacy]

    value, provenance = resolve_field(CONTRACT_MODE_FIELD, candidates, default=default)
    return cast(ContractMode, value), provenance


INTERNAL_NAMESPACES_FIELD = "surface.internal_namespaces"
_INTERNAL_NAMESPACES_FIELD = INTERNAL_NAMESPACES_FIELD  # backward-compatible alias

#: What ``surface.internal_namespaces`` resolves to when no ``--policy-file``
#: set it -- equal to ``SurfaceConfig.internal_namespaces``'s own default
#: (empty tuple), so accepting ADR-049 does not, by itself, change today's
#: real default behavior (each consuming step falls back to its own
#: ``DEFAULT_INTERNAL_NAMESPACES``, exactly as it does today with no policy
#: file at all).
_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES: tuple[str, ...] = ()


def resolve_internal_namespaces(
    *, policy_file: PolicyFile | None
) -> tuple[tuple[str, ...], ValueProvenance]:
    """Resolve ``surface.internal_namespaces`` from a real ``--policy-file``.

    ``internal_namespaces`` has no CLI flag of its own -- ``policy_file.py``'s
    ``PolicyFile.internal_namespaces`` (populated only when a real
    ``--policy-file`` YAML sets the key) is the only front end that can set
    it today. ``policy_file is None`` (no ``--policy-file`` given at all), or
    an empty ``internal_namespaces`` list on a document that never carried
    the key (``PolicyFile.internal_namespaces_stated`` is ``False``),
    contribute no candidate at all and fall through to
    :data:`_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES` -- the same "a selector
    layer only participates when it actually selected something" principle
    :func:`resolve_legacy_contract_mode`'s untouched-flag case already
    applies (ADR-049 D7). A document that *did* carry the key with an empty
    list stated something ("this project has no internal namespaces") and
    contributes a real empty-tuple candidate, so nothing below it -- a
    contract pack assigning the same field in particular -- fills it in over
    that statement.

    Tagged :data:`~abicheck.contract_relevance_types.SelectorLayer.EXPLICIT_CLI`,
    not ``PROJECT_CONFIG`` -- ``--policy-file`` is a flag the user explicitly
    passed on *this* invocation, the same selection mechanism
    ``resolve_legacy_contract_mode``'s explicit flag case models, not an
    implicitly-discovered project file a future ``.abicheck.yml``-reading
    front end would contribute at ``PROJECT_CONFIG`` tier. Tagging it
    ``PROJECT_CONFIG`` previously meant a lower-precedence-by-mechanism
    candidate (``RUN_RECIPE``/``RUN_PROFILE``) could silently outrank an
    explicitly user-selected manifest, and the provenance receipt itself
    misrepresented how the value was actually chosen (Codex review, fresh
    evidence).

    Sorts and dedupes the list before building the candidate, mirroring
    ``SurfaceConfig.__post_init__``'s own canonicalization and
    ``compatibility_evaluation_packs.py``'s
    ``_canonicalize_order_insensitive_field`` (both already treat
    ``surface.internal_namespaces`` as an order-insensitive set, not an
    ordered sequence) -- two policy files listing the same namespaces in a
    different order must resolve to the same value, per D7's "equivalent
    semantic inputs must resolve to an equivalent object".

    Returns the resolved ``tuple[str, ...]`` and its
    :class:`~abicheck.compatibility_evaluation_config.ValueProvenance`. This
    performs no I/O -- *policy_file*, if given, must already be loaded by the
    caller (e.g. via ``PolicyFile.load``).
    """
    default = FieldCandidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
        value=_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES,
    )

    candidate = internal_namespaces_candidate(policy_file=policy_file)
    candidates: list[FieldCandidate] = [] if candidate is None else [candidate]

    value, provenance = resolve_field(
        INTERNAL_NAMESPACES_FIELD, candidates, default=default
    )
    return cast("tuple[str, ...]", value), provenance


def internal_namespaces_candidate(
    *,
    policy_file: PolicyFile | None,
    layer: SelectorLayer = SelectorLayer.EXPLICIT_CLI,
    sha256: str | None = None,
    option: str = "--policy",
) -> FieldCandidate | None:
    """Build the ``surface.internal_namespaces`` candidate a ``--policy-file``
    contributes, or ``None`` when it contributes nothing.

    Extracted from :func:`resolve_internal_namespaces` (whose behavior is
    unchanged -- it now calls this) for the same reason
    :func:`legacy_contract_mode_candidate` was: a caller resolving this field
    against more layers than the policy file alone reuses one candidate
    construction rather than re-deriving the canonicalization and provenance
    shape. *layer* mirrors that function's own parameter, for a front end
    whose policy manifest was selected by a project config rather than typed
    on this invocation. *sha256* is the selected file's own content digest,
    which a caller that has it should pass so the receipt can identify the
    exact document that supplied the value (ADR-049 D6) rather than only the
    path it was read from. *option* is the selector spelling to record --
    a non-CLI front end names its own field, not the CLI flag.
    """
    # An *explicitly* empty list still contributes: a document that carried
    # `internal_namespaces: []` said "this project has none", and treating
    # that as unstated let a contract pack assigning the field inject its own
    # namespaces over the statement (Codex review, fresh evidence). A parsed
    # empty list with no such key -- including any PolicyFile built in code --
    # still contributes nothing, and falls through to the built-in default.
    if policy_file is None or not (
        policy_file.internal_namespaces or policy_file.internal_namespaces_stated
    ):
        return None
    canonical = tuple(dict.fromkeys(sorted(policy_file.internal_namespaces)))
    source_path = str(policy_file.source_path) if policy_file.source_path else None
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=layer,
            source_kind="policy_file",
            sha256=sha256,
            path=source_path,
            selected_by=(
                SelectedByEntry(
                    layer=layer,
                    option=option,
                    path=source_path,
                ),
            ),
        ),
        value=canonical,
    )


_PACKS_FIELD_BY_KIND: dict[PackKind, str] = {
    PackKind.CONTRACT: "contract.packs",
    PackKind.POLICY: "policy.packs",
    PackKind.GATE: "gate.packs",
}

#: What each of ``contract.packs``/``policy.packs``/``gate.packs`` resolves
#: to when no ``--pack`` path names a manifest of that kind -- equal to
#: ``ContractConfig.packs``/``CompatibilityPolicyConfig.packs``/
#: ``GateConfig.packs``'s own default (empty tuple), the same "accepting
#: ADR-049 does not, by itself, change today's real default behavior"
#: principle the other two wirings in this module already follow.
_BUILT_IN_DEFAULT_PACKS: tuple[ImmutableIdentity, ...] = ()


def load_selected_packs(
    pack_paths: Sequence[str | Path],
) -> list[tuple[str, LoadedPack]]:
    """Load each manifest in *pack_paths* once, paired with its own path.

    The three pack resolvers below each accept the result as ``loaded``. A
    caller resolving one whole configuration should load here and pass the
    same list to all of them: they would otherwise re-read, re-parse and
    re-digest every manifest per call, and a manifest edited *between* those
    reads would produce receipt entries carrying different identities for
    what is meant to be one configuration (CodeRabbit review).

    Also where a selected-but-empty manifest is rejected, for the same
    reason: emptiness is a property of the file, and asking it *here* asks it
    of the revision that will configure the run. An earlier arrangement asked
    it from the front ends before resolution, which left the window this
    function's own one-read rule exists to close -- a generated or
    concurrently edited pack that was non-empty at the front end's read and
    ``assignments: {}`` at the resolver's would be recorded as selected,
    supply no field provenance, pass every later check, and succeed as
    exactly the decorative pack the rule rejects (Codex review). Every path
    that resolves or validates packs loads through here, so one call site
    covers `compare`, `compare --dry-run`, `scan`, the MCP tools and the
    typed API.
    """
    entries = [(str(path), load_pack_manifest(path)) for path in pack_paths]
    check_packs_assign_something(entries)
    return entries


def check_packs_assign_something(entries: Sequence[tuple[str, LoadedPack]]) -> None:
    """Reject a loaded manifest whose ``assignments`` mapping is empty.

    The decorative pack in its purest form: selected, recorded in the receipt
    as active configuration, and changing no verdict, finding or exit code.

    Its own named function rather than an inline loop in
    :func:`load_selected_packs` so the rule can be tested directly on
    entries, and so it reads as a rule rather than as loading trivia.

    It cannot instead be asked of the *resolved* configuration, where every
    other pack rejection lives: a pack whose value an explicit
    ``--policy-file`` outranks also supplies no provenance, so "recorded but
    contributed nothing" would be indistinguishable there from D8 precedence
    working correctly.
    """
    for path, pack in entries:
        if not pack.assignments:
            raise PackManifestError(
                f"{path}: assigns nothing. A pack that is selected, recorded "
                "as active configuration, and changes no verdict, finding or "
                "exit code is the failure this check exists to prevent -- "
                "give it an assignment or drop the --pack."
            )


def resolve_selected_packs(
    pack_paths: Sequence[str | Path],
    *,
    explicit_overrides: Mapping[PackKind, Mapping[str, Hashable]] = MappingProxyType(
        {}
    ),
    layer: SelectorLayer = SelectorLayer.EXPLICIT_CLI,
    option: str = "--pack",
    loaded: Sequence[tuple[str, LoadedPack]] | None = None,
) -> dict[str, tuple[tuple[ImmutableIdentity, ...], ValueProvenance]]:
    """Resolve a real ``--pack <path>``-style CLI/config input into
    ``contract.packs``/``policy.packs``/``gate.packs`` field values.

    Loads every manifest in *pack_paths* with
    :func:`~abicheck.compatibility_evaluation_packs.load_pack_manifest`
    (raising :class:`~abicheck.errors.PackManifestError` for a malformed
    one), groups the loaded packs by
    :class:`~abicheck.compatibility_evaluation_packs.PackKind` via
    :func:`~abicheck.compatibility_evaluation_packs.assignments_for_conflict_check`,
    and runs
    :func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`
    once per namespace (ADR-049 D8: the conflict rule is scoped *within* one
    namespace, never across contract/policy/gate) -- raising
    :class:`~abicheck.compatibility_evaluation_resolver.PackConflictError`
    if two selected packs of the same kind disagree on the same field or
    ``ChangeKind``.

    *explicit_overrides*, keyed by :class:`PackKind`, is forwarded verbatim
    as ``detect_pack_conflicts``'s own ``explicit_overrides`` for that
    namespace: D8's composition order is "explicit per-kind override >
    selected packs > base policy", so a field the effective configuration
    already explicitly overrides is exempt from pack-vs-pack conflict
    detection -- two packs may legitimately disagree on a field the caller
    has already pinned. Without this, two selected packs disagreeing on a
    field the caller *intends* to override could never be resolved: this
    function would already have raised before any later composition step
    got a chance to apply the override (Codex review, fresh evidence).
    Omitted or a kind absent from the mapping means no override applies for
    that namespace -- identical to today's unconditional-conflict behavior.

    The resolved *value* for each field is a ``tuple[ImmutableIdentity, ...]``
    -- a pack's own resolved field assignments are not part of the effective
    config's ``packs`` value itself (``ContractConfig.packs``/
    ``CompatibilityPolicyConfig.packs``/``GateConfig.packs`` are all typed
    this way already); folding those assignments into
    ``CompatibilityPolicyConfig.overrides``/etc. for a real run is later,
    still-unwired composition work, not this function's job. Two ``--pack``
    paths naming the identical manifest content (same
    ``id``/``version``/``sha256``) collapse into one contributor; the
    resolved tuple is sorted by ``(id, version, sha256)``, since D8 states
    "file order never resolves conflicts" -- two invocations naming the same
    pack set in a different order must resolve to an equal value (D7). The
    receipt still names *every* path that selected it, in sorted order, so
    which of two byte-identical files was passed first cannot change the
    resolved configuration either.

    Every returned field is tagged *layer* (default
    :data:`~abicheck.contract_relevance_types.SelectorLayer.EXPLICIT_CLI`,
    recorded under *option*)
    when at least one path selected a pack of that kind (mirroring
    :func:`resolve_internal_namespaces`'s ``--policy-file`` case: a path the
    user passed on *this* invocation, not an implicitly-discovered project
    file), falling through to :data:`_BUILT_IN_DEFAULT_PACKS` otherwise. A
    caller resolving for a non-CLI front end must pass its own layer and
    selector spelling: hard-coding the CLI's made an API-selected manifest
    produce a self-contradictory receipt, with ``contract.packs`` claiming
    ``explicit_cli`` while the fields that same pack assigned claimed
    ``api_request`` (Codex review).

    Not called from any live command yet -- see module docstring.
    """
    entries = list(loaded) if loaded is not None else load_selected_packs(pack_paths)
    grouped = {
        kind: [
            (identity, _conflict_values(kind, assignments))
            for identity, assignments in packs
        ]
        for kind, packs in assignments_for_conflict_check(
            [pack for _, pack in entries]
        ).items()
    }

    by_kind: dict[PackKind, list[tuple[str, LoadedPack]]] = {
        kind: [] for kind in PackKind
    }
    for path, pack in entries:
        by_kind[pack.kind].append((path, pack))

    result: dict[str, tuple[tuple[ImmutableIdentity, ...], ValueProvenance]] = {}
    for kind, field_name in _PACKS_FIELD_BY_KIND.items():
        detect_pack_conflicts(
            grouped[kind],
            explicit_overrides=explicit_overrides.get(kind, MappingProxyType({})),
        )

        # Every path that selected an identity is kept, sorted: two paths
        # holding byte-identical manifest content collapse to one identity in
        # the resolved *value*, but keeping only the first-seen path made the
        # receipt order-dependent -- `--pack a.yml --pack b.yml` recorded
        # `a.yml` and the reverse order recorded `b.yml`, so two invocations
        # naming the same pack set resolved to unequal configurations, which
        # is exactly what D7's "file order never resolves anything" rules out
        # (Codex review, fresh evidence). Both files really did select it, so
        # naming both is also the more faithful receipt.
        paths_by_identity: dict[ImmutableIdentity, set[str]] = {}
        for path, pack in by_kind[kind]:
            paths_by_identity.setdefault(pack.identity, set()).add(path)
        canonical = tuple(
            sorted(
                paths_by_identity,
                key=lambda identity: (identity.id, identity.version, identity.sha256),
            )
        )

        default = FieldCandidate(
            provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
            value=_BUILT_IN_DEFAULT_PACKS,
        )
        candidates: list[FieldCandidate] = []
        if canonical:
            candidates.append(
                FieldCandidate(
                    provenance=ValueProvenance(
                        layer=layer,
                        source_kind="pack_manifest",
                        selected_by=tuple(
                            SelectedByEntry(
                                layer=layer,
                                option=option,
                                path=path,
                                identity=identity,
                            )
                            for identity in canonical
                            for path in sorted(paths_by_identity[identity])
                        ),
                    ),
                    value=canonical,
                )
            )
        value, provenance = resolve_field(field_name, candidates, default=default)
        result[field_name] = (
            cast("tuple[ImmutableIdentity, ...]", value),
            provenance,
        )
    return result


def resolve_policy_pack_overrides(
    pack_paths: Sequence[str | Path],
    *,
    explicit_overrides: Mapping[str, Verdict] = MappingProxyType({}),
    loaded: Sequence[tuple[str, LoadedPack]] | None = None,
) -> Mapping[str, Verdict]:
    """Fold every selected ``kind: policy`` pack's ``ChangeKind`` slug ->
    :class:`~abicheck.change_registry_types.Verdict` assignments into one
    merged mapping, suitable to pass directly as
    :attr:`~abicheck.compatibility_evaluation_config.CompatibilityPolicyConfig.overrides`.

    :func:`resolve_selected_packs` resolves *which* packs are selected
    (``policy.packs``, an opaque tuple of :class:`ImmutableIdentity`) but
    never applies their content -- this is that missing composition step
    for the one namespace with an unambiguous target field
    (``CompatibilityPolicyConfig.overrides`` already exists and is exactly
    ``Mapping[str, Verdict]``, the same shape a policy pack's own
    assignments already carry post-:func:`~abicheck.compatibility_evaluation_packs.load_pack_manifest`).
    A contract/gate pack's own field assignments have no equivalent generic
    target -- :class:`~abicheck.compatibility_evaluation_config.ContractConfig`/
    :class:`~abicheck.compatibility_evaluation_config.GateConfig` are typed,
    fixed-field dataclasses with no open field-name -> value bag to fold an
    arbitrary assignment into -- so those go through
    :func:`resolve_pack_field_assignments`'s explicit per-field route table
    instead of this function.

    Runs the identical D8 conflict check :func:`resolve_selected_packs`
    already runs for the ``POLICY`` namespace
    (:func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`,
    scoped to only the policy-kind packs among *pack_paths* -- a contract/
    gate pack in the same list is loaded but otherwise ignored here), so
    calling both functions with the same *pack_paths* never disagrees about
    which packs are in conflict. *explicit_overrides* is forwarded verbatim
    as that check's own ``explicit_overrides`` (ADR-049 D8 composition:
    "explicit per-kind override > selected packs > base policy" -- a field
    the effective config already explicitly overrides is exempt from
    pack-vs-pack conflict detection) and is re-applied after the pack merge
    below, so an explicit override always wins even for a
    ``ChangeKind`` no two packs actually disagreed on (conflict detection
    alone would silently let a lone pack's value stand unless something
    forces the override to take precedence).

    Two selected packs assigning the *same* Verdict to the same
    ``ChangeKind`` slug is not a conflict (D7), so a plain last-write-wins
    merge over packs is safe once :func:`detect_pack_conflicts` has already
    confirmed no unresolved disagreement exists — every pack that assigned
    that key must have assigned the identical value. Performs no I/O beyond
    loading the given manifests; not called from any live command yet (see
    module docstring).
    """
    entries = list(loaded) if loaded is not None else load_selected_packs(pack_paths)
    policy_packs = [pack for _, pack in entries if pack.kind is PackKind.POLICY]

    detect_pack_conflicts(
        [(pack.identity, pack.assignments) for pack in policy_packs],
        explicit_overrides=explicit_overrides,
    )

    merged: dict[str, Verdict] = {}
    for pack in policy_packs:
        merged.update(cast("Mapping[str, Verdict]", pack.assignments))
    merged.update(explicit_overrides)
    return merged


# --------------------------------------------------------------------------
# ADR-049 D8: the contract/gate pack per-field router.
# --------------------------------------------------------------------------


def _route_choice(allowed: frozenset[str]) -> Callable[[Hashable, str, str], Hashable]:
    def convert(value: Hashable, field_name: str, source: str) -> Hashable:
        if not isinstance(value, str) or value not in allowed:
            raise PackManifestError(
                f"{source}: {field_name!r} must be one of {sorted(allowed)}, "
                f"got {value!r}"
            )
        return value

    return convert


def _route_bool(value: Hashable, field_name: str, source: str) -> Hashable:
    if not isinstance(value, bool):
        raise PackManifestError(
            f"{source}: {field_name!r} must be a bool, got {value!r}"
        )
    return value


def _route_str_tuple(value: Hashable, field_name: str, source: str) -> Hashable:
    # A one-element YAML list and a bare scalar are the same selection; both
    # are accepted, mirroring `.abicheck.yml`'s own list-or-single-str keys.
    items = (value,) if isinstance(value, str) else value
    if not isinstance(items, tuple) or any(not isinstance(v, str) for v in items):
        raise PackManifestError(
            f"{source}: {field_name!r} must be a list of str, got {value!r}"
        )
    return tuple(dict.fromkeys(sorted(items)))


def _route_severity_level(value: Hashable, field_name: str, source: str) -> Hashable:
    from .severity import SeverityLevel

    allowed = {level.value for level in SeverityLevel}
    if not isinstance(value, str) or value not in allowed:
        raise PackManifestError(
            f"{source}: {field_name!r} must be one of {sorted(allowed)}, got {value!r}"
        )
    return SeverityLevel(value)


#: Which effective-config field a ``kind: contract`` pack may assign, and how
#: its raw manifest value is validated/converted (ADR-049 D8: contract/
#: language packs "define roots, providers, and ABI closure").
#:
#: Deliberately *not* routable from a contract pack, each for its own reason:
#: ``contract.mode`` (which evidence domain a run judges against is the
#: user's own per-run selection -- a pack silently switching it is exactly
#: the hidden ``public_contract`` preset D3 forbids), ``contract.packs``
#: (self-reference), and ``policy.*``/``gate.*`` (a different namespace; D8
#: keeps the three distinct precisely so a contract pack cannot move a
#: severity or a gate scheme).
CONTRACT_PACK_FIELD_ROUTES: Mapping[str, Callable[[Hashable, str, str], Hashable]] = (
    MappingProxyType(
        {
            "contract.unresolved": _route_choice(VALID_UNRESOLVED_BEHAVIORS),
            "contract.overlays": _route_str_tuple,
            "surface.internal_namespaces": _route_str_tuple,
            "assurance.require_evidence": _route_bool,
        }
    )
)

#: Which effective-config field a ``kind: gate`` pack may assign (ADR-049 D8:
#: gate packs "affect ``GateDecision`` and compose with every compatibility
#: policy"). ``gate.preset``/``gate.packs`` are not routable: a preset is an
#: identity a pack cannot mint for itself, and packs listing packs is
#: self-reference. ``gate.exit_code_scheme`` is deliberately absent (PR G2
#: deleted the manual selector everywhere): a pack asserting it now hits the
#: same "not a route this kind may assign" ``PackManifestError``.
GATE_PACK_FIELD_ROUTES: Mapping[str, Callable[[Hashable, str, str], Hashable]] = (
    MappingProxyType(
        {
            "gate.severity.abi_breaking": _route_severity_level,
            "gate.severity.potential_breaking": _route_severity_level,
            "gate.severity.quality_issues": _route_severity_level,
            "gate.severity.addition": _route_severity_level,
        }
    )
)

PACK_FIELD_ROUTES_BY_KIND: Mapping[
    PackKind, Mapping[str, Callable[[Hashable, str, str], Hashable]]
] = MappingProxyType(
    {
        PackKind.CONTRACT: CONTRACT_PACK_FIELD_ROUTES,
        PackKind.GATE: GATE_PACK_FIELD_ROUTES,
    }
)


def _conflict_values(
    kind: PackKind, assignments: Mapping[str, Hashable]
) -> Mapping[str, Hashable]:
    """Route *assignments* before they are compared pack against pack.

    D8's conflict rule is about two packs assigning *different values* to one
    field -- not about them spelling the same value differently. A field
    routed by :func:`_route_str_tuple` accepts a bare scalar and a
    one-element list as the same selection (``contract.overlays: foo`` and
    ``contract.overlays: [foo]`` both mean ``("foo",)``), so comparing the raw
    manifest values rejected that pair as conflicting before routing ever got
    to canonicalize them (Codex review, fresh evidence).

    A value its route rejects, and a field with no route at all, are passed
    through untouched: both are real errors, and both are already reported --
    with the manifest path in the message -- by
    :func:`resolve_pack_field_assignments`'s own routing pass. Reporting them
    from here instead would only change which message a caller sees first.
    """
    routes = PACK_FIELD_ROUTES_BY_KIND.get(kind, {})
    normalized: dict[str, Hashable] = {}
    for field_name, value in assignments.items():
        convert = routes.get(field_name)
        if convert is None:
            normalized[field_name] = value
            continue
        try:
            normalized[field_name] = convert(value, field_name, "")
        except PackManifestError:
            normalized[field_name] = value
    return normalized


@dataclass(frozen=True)
class RoutedPackAssignment:
    """One effective-config field a selected pack assigns, with its source.

    The *identity* and *path* travel with the value so a caller can build a
    provenance receipt that identifies the exact manifest revision the value
    came from (ADR-049 D6: "path, digest, manifest identity/version, and
    field location identify the actual definition used for exact replay") --
    a bare ``field -> value`` mapping could name the file at best, and only
    when exactly one pack was selected.
    """

    value: Hashable
    identity: ImmutableIdentity
    path: str


def resolve_pack_field_assignments(
    pack_paths: Sequence[str | Path],
    kind: PackKind,
    *,
    explicit_overrides: Mapping[str, Hashable] = MappingProxyType({}),
    loaded: Sequence[tuple[str, LoadedPack]] | None = None,
) -> dict[str, RoutedPackAssignment]:
    """Fold every selected pack of *kind* into one routed, typed
    ``field name -> assignment`` mapping.

    The contract/gate counterpart of :func:`resolve_policy_pack_overrides`.
    A policy pack folds into one open bag (``CompatibilityPolicyConfig.
    overrides`` is literally ``Mapping[str, Verdict]``), so no routing is
    needed there; :class:`~abicheck.compatibility_evaluation_config.ContractConfig`/
    :class:`~abicheck.compatibility_evaluation_config.GateConfig` are typed,
    fixed-field dataclasses, so *which* fields a pack of each kind may assign
    -- and what a valid value for each looks like -- has to be declared
    explicitly. That declaration is :data:`CONTRACT_PACK_FIELD_ROUTES` /
    :data:`GATE_PACK_FIELD_ROUTES`; an assignment naming anything else is a
    :class:`~abicheck.errors.PackManifestError`, never a silently-ignored
    key (the same fail-loud posture ``load_pack_manifest`` already takes for
    an unknown top-level manifest field, and ADR-049 D8's "unknown config
    keys/enum values fail at load time").

    ``kind: policy`` is rejected outright rather than silently returning
    nothing: its assignments are ``ChangeKind`` slugs, a different vocabulary
    that :func:`resolve_policy_pack_overrides` owns.

    Runs the same D8 conflict check
    (:func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`)
    as the other two pack functions, scoped to only the packs of *kind* among
    *pack_paths* (packs of another kind are loaded but otherwise ignored), so
    all three agree about which packs conflict. *explicit_overrides* is
    forwarded verbatim to that check -- D8 exempts a field the caller already
    states from pack-vs-pack conflict detection, since the stated value
    resolves any disagreement about it. Only the *keys* are read, so a marker
    value is enough for a field whose real value the caller hasn't resolved
    yet; the keys are not route-checked either, since a caller may
    legitimately state a field no pack of this kind could assign.

    Unlike :func:`resolve_policy_pack_overrides`, the overrides are **not**
    re-applied onto the returned mapping: that function's result *is* the
    final ``overrides`` value, whereas this one's is an input to per-field
    resolution, where D8's "explicit override > selected packs" is applied by
    the stated value's own candidate outranking the pack's. Returning them
    merged here would hand the caller back its own marker values as if a pack
    had assigned them.

    Conflicts are detected on the packs' *raw* manifest values, before
    routing, so a manifest whose value is malformed for its field still
    reports the pack-vs-pack disagreement first if there is one -- both are
    hard errors, and the conflict is the more actionable of the two.
    """
    if kind is PackKind.POLICY:
        raise ValueError(
            "resolve_pack_field_assignments does not handle PackKind.POLICY: a "
            "policy pack's assignments are ChangeKind slug -> Verdict, which "
            "fold into CompatibilityPolicyConfig.overrides via "
            "resolve_policy_pack_overrides() instead of a per-field route."
        )
    routes = PACK_FIELD_ROUTES_BY_KIND[kind]

    entries = list(loaded) if loaded is not None else load_selected_packs(pack_paths)
    selected = [(path, pack) for path, pack in entries if pack.kind is kind]

    detect_pack_conflicts(
        [
            (pack.identity, _conflict_values(kind, pack.assignments))
            for _, pack in selected
        ],
        explicit_overrides=explicit_overrides,
    )

    # Safe as last-write-wins only because detect_pack_conflicts already
    # proved every pack assigning a given field assigned the identical value
    # (the same reasoning resolve_policy_pack_overrides documents). Which of
    # several agreeing packs is recorded as the source is therefore a choice
    # among equals -- made deterministic by sorting on the pack identity, so
    # the receipt never depends on the order the paths were given in ("pack
    # order never decides semantics", D8). The path is the last sort key
    # rather than a leftover of input order: two *byte-identical* manifests
    # at different paths share one identity, so an identity-only sort is a
    # tie between them and stable sorting would hand the receipt back to
    # argument order (Codex review, fresh evidence).
    # Every contributor is kept, not only the winner: a field another layer
    # pins is exempt from conflict detection, so several packs may assign it
    # with differing values and only the first would ever be routed. A
    # malformed assignment in one of the others then reached no converter at
    # all, and the resolver accepted a manifest it never validated (Codex
    # review, fresh evidence). The value that gets *used* is still the first
    # one in this order; the rest are validated and discarded.
    merged_raw: dict[str, list[tuple[str, LoadedPack, Hashable]]] = {}
    for path, pack in sorted(
        selected,
        key=lambda entry: (
            entry[1].identity.id,
            entry[1].identity.version,
            entry[1].identity.sha256,
            entry[0],
        ),
    ):
        for field_name, value in pack.assignments.items():
            merged_raw.setdefault(field_name, []).append((path, pack, value))

    routed: dict[str, RoutedPackAssignment] = {}
    for field_name in sorted(merged_raw):
        contributors = merged_raw[field_name]
        path, pack, value = contributors[0]
        convert = routes.get(field_name)
        if convert is None:
            raise PackManifestError(
                f"{path}: a {kind.value!r} pack may not assign {field_name!r} "
                f"(assignable fields: {sorted(routes)}) -- ADR-049 D8 keeps the "
                "contract/policy/gate namespaces distinct, and an effective-"
                "config field with no route here is one no pack of this kind "
                "is allowed to set"
            )
        for other_path, _, other_value in contributors[1:]:
            convert(other_value, field_name, other_path)
        routed[field_name] = RoutedPackAssignment(
            value=convert(value, field_name, path),
            identity=pack.identity,
            path=path,
        )
    return routed

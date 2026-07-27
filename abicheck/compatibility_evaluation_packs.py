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

"""ADR-049 Phase 1: pack-manifest loading for :func:`detect_pack_conflicts`.

``compatibility_evaluation_resolver.detect_pack_conflicts`` implements D8's
pack-vs-pack conflict rule, but -- as its own module docstring says -- "how
[pack] content is loaded (a future pack-manifest format) is a front end's job
this module doesn't own." This module is that pack-manifest format and its
loader: a small, versioned YAML document describing one contract/policy/gate
pack's field assignments, plus :func:`load_pack_manifest` to turn a file on
disk into a :class:`LoadedPack` -- an :class:`~abicheck.compatibility_evaluation_config.ImmutableIdentity`
(id/version/content-digest, ADR-049 D6) paired with the pack's own resolved
``field name -> value`` assignments, ready to feed straight into
:func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`.

Manifest shape::

    id: rust_c_ffi
    version: 1
    kind: contract        # contract | policy | gate (ADR-049 D8 namespaces)
    assignments:
      contract.mode: exports

A ``kind: policy`` pack's ``assignments`` keys are ``ChangeKind`` slugs and
values are the same ``break``/``warn``/``risk``/``ignore`` severity spellings
``--policy-file`` accepts (``policy_file.py``'s ``parse_severity_value``,
shared rather than re-declared) -- an unknown slug is a hard load error,
exactly matching ``policy_file.PolicyFile.load``'s existing rule (ADR-049 D8:
"An unknown ChangeKind in a custom policy is a hard load error"). A
``kind: contract``/``kind: gate`` pack's ``assignments`` are arbitrary
field-name -> scalar/list assignments (D8's contract/gate pack namespaces
have no single closed field vocabulary the way policy overrides do), each
converted to a hashable value (lists become tuples, recursively) since
``detect_pack_conflicts`` compares assignments by equality.

This module only loads pack *content* into the shape
``detect_pack_conflicts`` already accepts. It does not select which packs
apply to a given run (that is ``ContractConfig.packs``/
``CompatibilityPolicyConfig.packs``/``GateConfig.packs``, populated by a
future resolver front end), does not itself call
``detect_pack_conflicts``, and does not fold a policy pack's resolved
``ChangeKind -> Verdict`` assignments into ``CompatibilityPolicyConfig.overrides``
-- composing a validated pack into the effective config is that later
front-end's job, tracked alongside the rest of Phase 1's remaining wiring in
``docs/contribute/plans/public-contract-default.md``.
"""

from __future__ import annotations

import datetime
import hashlib
import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .change_registry_types import Verdict
from .checker_policy import ChangeKind
from .compatibility_evaluation_config import ImmutableIdentity
from .errors import PackManifestError
from .policy_file import parse_severity_value


class PackKind(str, Enum):
    """Which ADR-049 D8 namespace a pack manifest's assignments belong to."""

    CONTRACT = "contract"
    POLICY = "policy"
    GATE = "gate"


_VALID_PACK_KINDS: frozenset[str] = frozenset(k.value for k in PackKind)
_VALID_CHANGE_KIND_SLUGS: frozenset[str] = frozenset(k.value for k in ChangeKind)

#: The complete top-level manifest field set. ADR-049 D7: "unknown config
#: keys/enum values fail at load time" -- reading only these four fields via
#: ``.get()`` would otherwise silently ignore an extra or misspelled key
#: (e.g. ``assigments:`` for ``assignments:``) instead of rejecting it,
#: silently discarding the pack author's actual intent (Codex review).
_TOP_LEVEL_MANIFEST_FIELDS: frozenset[str] = frozenset(
    {"id", "version", "kind", "assignments"}
)


class _StrictLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that rejects a duplicate key in the same mapping.

    ``yaml.safe_load`` alone silently accepts ``{func_removed: break,
    func_removed: ignore}`` with last-value-wins semantics (PyYAML's
    ``SafeConstructor.construct_mapping`` never checks for a repeat) -- for a
    hard-load-error manifest format, that would silently drop the earlier,
    contradictory assignment instead of raising (Codex review). Mirrors
    ``dump_manifest.py``'s own ``_StrictLoader``/``_construct_mapping`` for
    the identical ADR-050 D3 gap; kept as a second, independent copy rather
    than a shared import since that one is private to its own module and this
    manifest format has no other coupling to ``dump_manifest.py``.
    """


def _construct_strict_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        # A complex YAML key (e.g. `? [a, b] : value`) constructs to an
        # unhashable list -- `key in seen`/`seen.add(key)` would otherwise
        # raise a raw TypeError instead of the documented PackManifestError
        # (Codex review).
        try:
            hash(key)
        except TypeError as exc:
            raise PackManifestError(
                f"unhashable mapping key {key!r} "
                f"(line {key_node.start_mark.line + 1}): {exc}"
            ) from exc
        if key in seen:
            raise PackManifestError(
                f"duplicate key {key!r} in the same mapping "
                f"(line {key_node.start_mark.line + 1})"
            )
        seen.add(key)
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_strict_mapping
)


@dataclass(frozen=True)
class LoadedPack:
    """One pack manifest's identity and resolved field assignments.

    ``assignments`` values are already-validated ``Hashable``s -- a
    ``kind=POLICY`` pack's values are real :class:`~abicheck.change_registry_types.Verdict`
    members (never a raw severity string), matching what
    :class:`~abicheck.compatibility_evaluation_config.CompatibilityPolicyConfig.overrides`
    itself requires.

    ``assignments`` is frozen into a ``MappingProxyType`` in ``__post_init__``
    (mirroring ``compatibility_evaluation_config.py``'s ``_frozen_mapping``
    pattern for every other config dataclass here): although ``LoadedPack``
    itself is a frozen dataclass, a plain ``dict`` passed for ``assignments``
    would otherwise stay directly mutable by the caller after construction,
    letting `identity.sha256` (digested over the manifest's original bytes)
    silently stop matching the actual assignments a later
    ``detect_pack_conflicts``/config-composition call observes -- defeating
    ADR-049 D6's exact-replay guarantee (Codex review). Freezing only the
    outer mapping is not enough for a directly constructed ``LoadedPack``
    (bypassing :func:`load_pack_manifest`, whose own ``_to_hashable`` pass
    already converts every list to a tuple): a caller-supplied mutable list
    *value* would still be the same aliased object, so mutating it after
    construction would change the pack's content without changing
    ``identity.sha256`` either. Every value is re-run through
    :func:`_to_hashable` here too, so a directly constructed pack gets the
    identical list-to-tuple deep-freeze (and the identical
    hashability/no-nested-mapping validation) the manifest-loading path
    already gets (Codex review, second round). A ``kind=POLICY`` pack's
    assignments additionally go through :func:`_parse_policy_assignments`
    instead of the generic freeze -- a directly constructed pack could
    otherwise carry a raw severity string (``"break"``) rather than the
    real ``Verdict`` member this class's own contract promises, which would
    then compare unequal to an equivalent manifest-loaded pack's ``Verdict``
    value inside ``detect_pack_conflicts`` and raise a false
    ``PackConflictError`` (Codex review, third round). ``kind`` itself is
    coerced through ``PackKind(...)`` first, for the identical reason: an
    untyped caller passing the bare string ``"policy"`` instead of
    ``PackKind.POLICY`` would fail the ``is PackKind.POLICY`` identity check
    above (a `str` value is never the same object as the enum member, even
    though it compares equal), silently skipping the policy-specific
    normalization -- while ``assignments_for_conflict_check`` still groups
    it into the ``POLICY`` bucket regardless, since *that* grouping keys off
    equality/hash, not identity. Rejecting or coercing an invalid ``kind``
    here means the two checks can never disagree about which packs are
    policy packs (Codex review, fourth round). A ``kind=CONTRACT``/
    ``kind=GATE`` pack's assignments now also route through
    :func:`_parse_field_assignments` (the same validation
    ``load_pack_manifest`` already applies) instead of a bare
    :func:`_to_hashable` comprehension -- otherwise a directly constructed
    pack could carry a non-``str`` or empty-string key (``1``, ``""``)
    that ``load_pack_manifest`` would reject outright: a non-``str`` key
    would only fail later, inside conflict detection, with a less specific
    error, and an empty key would be silently accepted as a real field and
    ignored by later config composition (Codex review, fifth round).
    """

    identity: ImmutableIdentity
    kind: PackKind
    assignments: Mapping[str, Hashable]

    def __post_init__(self) -> None:
        try:
            kind = PackKind(self.kind)
        except ValueError as exc:
            raise PackManifestError(
                f"kind must be one of {sorted(_VALID_PACK_KINDS)}, got {self.kind!r}"
            ) from exc
        object.__setattr__(self, "kind", kind)
        if kind is PackKind.POLICY:
            coerced: dict[str, Hashable] = dict(
                _parse_policy_assignments(
                    self.assignments, source="LoadedPack(...) (direct construction)"
                )
            )
            object.__setattr__(self, "assignments", MappingProxyType(coerced))
            return
        frozen = _parse_field_assignments(
            self.assignments, source="LoadedPack(...) (direct construction)"
        )
        object.__setattr__(self, "assignments", MappingProxyType(frozen))


#: The exact scalar vocabulary a real YAML manifest's ``SafeLoader`` can ever
#: produce: ``str``, ``bool``/``int``/``float`` (the implicit bool/int/float
#: resolvers), ``bytes`` (the explicit ``!!binary`` tag), and
#: ``datetime.date`` (the implicit timestamp resolver -- ``datetime.datetime``
#: is itself a ``datetime.date`` subclass, so one entry covers both). ``bool``
#: is listed even though it is an ``int`` subclass, for clarity at the call
#: site. Checking membership in this closed set, rather than merely that
#: ``hash(value)`` succeeds, matters because hashability is not proof of
#: immutability: a caller bypassing YAML entirely via direct ``LoadedPack``
#: construction (this class's own documented escape hatch) could pass a
#: custom object that defines ``__hash__`` while remaining fully mutable,
#: which would then alias into ``pack.assignments`` and let a later mutation
#: change the pack's content without changing ``identity.sha256`` -- the
#: same exact-replay violation the list-freezing fix above closed for a
#: mutable list, but for an arbitrary mutable-yet-hashable object instead
#: (Codex review, sixth round).
_HASHABLE_SCALAR_TYPES: tuple[type, ...] = (
    str,
    bool,
    int,
    float,
    bytes,
    datetime.date,
)


def _plain_str(value: str) -> str:
    """Reconstruct *value* as a genuinely plain ``str``, using an
    ``Enum`` member's own ``.value`` payload rather than ``str()``'s
    default representation of it.

    ``str(x)`` reliably strips subclass-added state for an *ordinary* str
    subclass (confirmed empirically: it returns a new plain ``str`` sharing
    only the character data). But a ``(str, Enum)`` member -- e.g.
    ``ChangeKind.FUNC_REMOVED``/``ContractMode.PUBLIC``, both of which a
    directly constructed :class:`LoadedPack` (this module's own documented
    escape hatch) can plausibly carry as a policy slug or field value,
    since both pass ``isinstance(value, str)`` and compare/hash equal to
    their own ``.value`` -- overrides ``__str__`` to return
    ``"ChangeKind.FUNC_REMOVED"``, not the member's actual string payload
    ``"func_removed"``. Confirmed empirically. Checking ``.value`` first
    (itself always the plain-``str`` payload for these mixin enums) avoids
    silently corrupting such a value into a spelling that no longer
    matches, conflicts with, or composes with the equivalent
    manifest-loaded (plain ``str``) assignment (Codex review, fresh
    evidence).
    """
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _canonicalize_tzinfo(
    value: datetime.datetime, *, where: str
) -> datetime.tzinfo | None:
    """Reconstruct *value*'s ``tzinfo`` as a plain, immutable
    ``datetime.timezone`` fixed to this exact instant's UTC offset, rather
    than passing through the original ``tzinfo`` object unchanged.

    A directly constructed :class:`LoadedPack` (this module's own
    documented escape hatch) could otherwise carry an aware ``datetime``
    whose ``tzinfo`` is a custom, mutable implementation (``utcoffset()``
    reading mutable instance state) -- the reconstructed value would still
    alias that same ``tzinfo`` object, so mutating it later changes the
    stored assignment's effective equality/hash without changing
    ``identity.sha256``, the same exact-replay violation the mutable-
    subclass fix above closed for ordinary scalars (Codex review, fresh
    evidence; confirmed empirically: two packs assigning equal-offset
    aware datetimes through distinct mutable ``tzinfo`` instances agreed
    initially, then ``detect_pack_conflicts`` flipped from no-conflict to
    conflict purely from mutating one ``tzinfo`` afterward). ``datetime``
    equality/comparison for aware values is defined by the represented UTC
    instant, not by ``tzinfo`` identity or type, so collapsing to a fixed
    ``datetime.timezone`` at this value's own ``utcoffset()`` preserves
    the exact semantics of *this* stored instant while making it immutable
    and independent of the original ``tzinfo`` instance -- the same
    "snapshot this one value, not a live rule" treatment every other field
    here already gets. A ``tzinfo`` present but returning a ``None``
    offset (a malformed/incomplete implementation) is rejected rather than
    silently treated as naive, consistent with this module's "reject
    rather than silently produce an ambiguous value" rule elsewhere.
    """
    if value.tzinfo is None:
        return None
    offset = value.utcoffset()
    if offset is None:
        raise PackManifestError(
            f"{where}: assignment value {value!r} has a tzinfo that does "
            "not report a UTC offset -- aware datetimes must resolve to a "
            "concrete offset"
        )
    return datetime.timezone(offset)


def _canonicalize_scalar(value: Any, *, where: str) -> Hashable:
    """Reconstruct *value* as a plain instance of its exact allowed base
    type, discarding any subclass-added state.

    ``isinstance(value, _HASHABLE_SCALAR_TYPES)`` alone still accepts a
    *mutable subclass* of an allowed type (``str``/``float``/etc.) whose
    overridden ``__eq__``/``__hash__`` reads mutable instance state --
    the allowlist check closed the "arbitrary unrelated object" hole, but
    not this narrower one: the original aliased subclass instance would
    stay in ``pack.assignments``, and mutating it later could flip
    ``detect_pack_conflicts()`` between agreement and conflict while
    ``identity.sha256`` stayed unchanged (Codex review, seventh round).
    Calling each allowed type's own constructor on the value reliably
    produces a genuinely plain instance -- confirmed empirically
    (``str(subclass_instance)`` returns a new plain ``str``, not the
    subclass instance or its identity). ``bool`` is passed through
    unchanged since CPython disallows subclassing it at all (``type
    'bool' is not an acceptable base type``), so ``isinstance(value,
    bool)`` already guarantees ``type(value) is bool``; it is checked
    before ``int`` since ``bool`` is itself an ``int`` subclass, and
    ``datetime.datetime`` is checked before ``datetime.date`` for the
    identical reason.

    A non-finite float (``nan``/``inf``/``-inf``) is rejected outright
    rather than passed through: IEEE 754 defines ``nan != nan``, so two
    packs both assigning a YAML ``.nan`` to the same field would each be
    kept as a *distinct* ``float('nan')`` object -- confirmed empirically
    that ``{(float, float('nan')), (float, float('nan'))}`` has length 2,
    since ``_value_identity_key``'s tuple equality falls through to the
    same ``!=`` -- so ``detect_pack_conflicts()`` would raise a spurious
    ``PackConflictError`` for what a manifest author clearly intended as
    the identical value (Codex review). There is no stable
    equality-preserving representation of ``nan`` to canonicalize to
    either, since collapsing it to a sentinel would then make it silently
    *agree* with a genuinely different pack's own unrelated ``nan``
    assignment -- rejecting is consistent with this module's existing
    "reject rather than silently produce an ambiguous value" handling of
    ``None`` and nested mappings elsewhere in this file. ``inf``/``-inf``
    are rejected alongside it: both compare and hash consistently, so
    they don't share the ``nan`` conflict-detection bug, but a pack field
    meaningfully assigned an unbounded value is exotic enough that
    rejecting it too avoids having to reason about how it should behave
    downstream in size/threshold comparisons that assume a finite value.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _plain_str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PackManifestError(
                f"{where}: assignment value {value!r} must be a finite "
                "number -- NaN and +/-infinity are not supported"
            )
        return float(value)
    if isinstance(value, bytes):
        return bytes(value)
    if isinstance(value, datetime.datetime):
        return datetime.datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
            _canonicalize_tzinfo(value, where=where),
            fold=value.fold,
        )
    if isinstance(value, datetime.date):
        return datetime.date(value.year, value.month, value.day)
    raise AssertionError(  # pragma: no cover - every _HASHABLE_SCALAR_TYPES member is handled above
        f"unreachable: {value!r} already passed the _HASHABLE_SCALAR_TYPES check"
    )


def _to_hashable(value: Any, *, where: str) -> Hashable:
    """Convert a decoded YAML scalar/list into a hashable value.

    A YAML mapping value is rejected outright: this module has no defined
    semantics for a nested-mapping pack assignment, and silently accepting
    one would either crash later inside ``detect_pack_conflicts``'s own
    ``hash()`` guard with a less specific error, or -- for a value nested
    inside a list -- bypass that guard entirely (a tuple containing an
    unhashable dict still raises only when something actually calls
    ``hash()`` on it).

    A ``tuple`` is accepted alongside ``list`` (recursing the same way, not
    just added to :data:`_HASHABLE_SCALAR_TYPES`) because ``LoadedPack.__post_init__``
    re-validates unconditionally on every construction, including
    :func:`load_pack_manifest`'s own internal ``LoadedPack(...)`` call with
    assignments this function *already* converted once -- a bare scalar
    allowlist entry for ``tuple`` would accept it without recursing into its
    elements, silently skipping their own type validation on that second
    pass.
    """
    if isinstance(value, (list, tuple)):
        return tuple(
            _to_hashable(v, where=f"{where}[{i}]") for i, v in enumerate(value)
        )
    if isinstance(value, dict):
        raise PackManifestError(
            f"{where}: nested mappings are not supported as a pack assignment "
            f"value, got {value!r}"
        )
    if value is None:
        raise PackManifestError(f"{where}: assignment value must not be null")
    if not isinstance(value, _HASHABLE_SCALAR_TYPES):
        type_names = ", ".join(t.__name__ for t in _HASHABLE_SCALAR_TYPES)
        raise PackManifestError(
            f"{where}: assignment value {value!r} has unsupported type "
            f"{type(value).__name__} (not hashable-and-immutable) -- must be "
            f"one of {type_names}, or a list of these"
        )
    return _canonicalize_scalar(value, where=where)


def _parse_policy_assignments(
    raw: Mapping[Any, Any], *, source: str
) -> dict[str, Verdict]:
    unknown_kinds: list[str] = []
    unknown_severities: list[str] = []
    result: dict[str, Verdict] = {}
    for slug, severity in raw.items():
        if not isinstance(slug, str):
            raise PackManifestError(
                f"{source}: policy pack assignment key must be a str "
                f"ChangeKind slug, got {slug!r}"
            )
        if slug not in _VALID_CHANGE_KIND_SLUGS:
            unknown_kinds.append(slug)
            continue
        # Reconstruct as a plain str (see _parse_field_assignments' identical
        # fix): a directly constructed LoadedPack could otherwise carry a
        # mutable str subclass as the dict key, aliasing the caller's object
        # (Codex review). Uses _plain_str, not a bare str(...): a directly
        # constructed pack's slug can plausibly be a real `ChangeKind`
        # member (it already passes the `slug in _VALID_CHANGE_KIND_SLUGS`
        # check above via str equality/hash) -- `str(ChangeKind.FUNC_REMOVED)`
        # is `"ChangeKind.FUNC_REMOVED"`, not `"func_removed"`, which would
        # silently store the wrong key and stop matching/conflicting with
        # an equivalent manifest-loaded pack's plain-str slug (Codex review,
        # fresh evidence).
        slug_key = _plain_str(slug)
        # A directly constructed `LoadedPack` (this class's own docstring)
        # may already carry a real `Verdict` member rather than a raw
        # severity spelling -- `parse_severity_value` only recognizes the
        # four user-facing spellings ("break"/"warn"/...), not a `Verdict`
        # member's own str value, so it would otherwise reject exactly the
        # documented-correct direct-construction input (Codex review).
        if isinstance(severity, Verdict):
            result[slug_key] = severity
            continue
        verdict = parse_severity_value(severity)
        if verdict is None:
            unknown_severities.append(f"{slug}: {severity!r}")
            continue
        result[slug_key] = verdict
    if unknown_kinds:
        # ADR-049 D8: "An unknown ChangeKind in a custom policy is a hard
        # load error" -- a pack manifest is exactly as capable of silently
        # disabling a release rule via a renamed/typo'd slug as a
        # --policy-file document is, so it gets the identical hard failure
        # policy_file.PolicyFile.load already enforces, not a second,
        # weaker warning-and-skip path.
        raise PackManifestError(
            f"{source}: unknown ChangeKind slugs in policy pack: "
            f"{sorted(unknown_kinds)}. Rename or remove them -- see "
            "`abicheck --help` or docs/reference/change-kinds.md for valid "
            "slugs."
        )
    if unknown_severities:
        raise PackManifestError(
            f"{source}: invalid severity values in policy pack: "
            f"{sorted(unknown_severities)}. Valid values: break, warn, risk, "
            "ignore"
        )
    return result


#: Pack-assignment fields whose value is an *unordered selection*, not an
#: ordered sequence -- mirrors every ``compatibility_evaluation_config.py``
#: field that field's own ``__post_init__`` canonicalizes via
#: ``_canonical_tuple`` (sorted+deduped): ``ContractConfig.overlays`` and
#: ``SurfaceConfig.internal_namespaces`` (Codex review -- the latter added
#: after the former shipped without it, confirmed via the same two-packs
#: no-conflict repro). Two packs assigning the same set in a different
#: order (``[a, b]`` vs ``[b, a]``) are semantically identical -- the real
#: config field already canonicalizes for exactly this reason (D7:
#: "equivalent semantic inputs must resolve to an equivalent object") -- but
#: a pack's own ``assignments`` mapping is compared directly by
#: ``detect_pack_conflicts()`` long before it ever reaches that config
#: object, so without the identical canonicalization here, two packs
#: assigning an equivalent set in a different order raised a spurious
#: ``PackConflictError``. Add any future ``_canonical_tuple``-canonicalized
#: config field's pack-namespace equivalent here too.
_ORDER_INSENSITIVE_LIST_FIELDS: frozenset[str] = frozenset(
    {"contract.overlays", "surface.internal_namespaces"}
)


def _canonicalize_order_insensitive_field(
    field_name: str, value: Hashable, *, source: str
) -> Hashable:
    """Sort+dedupe *value* if *field_name* is a known unordered-selection
    field and *value* is a list-derived tuple; otherwise return it unchanged.

    Mirrors ``compatibility_evaluation_config._canonical_tuple``'s own
    sort+dedupe (via ``dict.fromkeys`` on the sorted sequence), applied at
    the pack-manifest layer instead of the effective-config layer.
    """
    if field_name not in _ORDER_INSENSITIVE_LIST_FIELDS or not isinstance(value, tuple):
        return value
    non_str = [v for v in value if not isinstance(v, str)]
    if non_str:
        raise PackManifestError(
            f"{source}: {field_name!r} must be a list of str, got "
            f"non-str element(s) {non_str!r}"
        )
    return tuple(dict.fromkeys(sorted(value)))


def _parse_field_assignments(
    raw: Mapping[Any, Any], *, source: str
) -> dict[str, Hashable]:
    result: dict[str, Hashable] = {}
    for field_name, value in raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise PackManifestError(
                f"{source}: assignment key must be a non-empty str field "
                f"name, got {field_name!r}"
            )
        field_source = f"{source}:{field_name}"
        hashable_value = _to_hashable(value, where=field_source)
        # Reconstruct as a plain str (mirroring _canonicalize_scalar's own
        # str branch): a directly constructed LoadedPack could otherwise
        # carry a mutable str subclass as the dict key itself, aliasing the
        # caller's object -- mutating it later (if its __eq__/__hash__ read
        # mutable state) would change the field identity detect_pack_conflicts
        # consumes without changing identity.sha256 (Codex review). Uses
        # _plain_str, not a bare str(...), for the identical reason
        # _parse_policy_assignments' slug key does: a str-mixin Enum field
        # name would otherwise flatten to its qualified member spelling
        # instead of its actual string payload (Codex review, fresh
        # evidence).
        result[_plain_str(field_name)] = _canonicalize_order_insensitive_field(
            field_name, hashable_value, source=field_source
        )
    return result


def load_pack_manifest(path: str | Path) -> LoadedPack:
    """Load and validate one ADR-049 D8 pack manifest from *path*.

    Raises :class:`~abicheck.errors.PackManifestError` for any malformed or
    invalid manifest -- an unreadable file, non-mapping YAML document,
    missing/mistyped ``id``/``version``/``kind``/``assignments``, an unknown
    ``kind``, an unknown ``ChangeKind`` slug or severity spelling in a
    ``kind: policy`` manifest's assignments, or an unhashable assignment
    value in any manifest. Never silently drops or skips an invalid entry --
    the same fail-loud posture as ``policy_file.PolicyFile.load`` and
    ``compatibility_evaluation_config``'s constructors.

    ``identity.sha256`` digests the manifest file's raw bytes, so any content
    change -- including one that doesn't change the resolved assignments,
    e.g. a reordered key or an added comment -- is visible to exact-replay
    comparison (ADR-049 D6).
    """
    manifest_path = Path(path)
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise PackManifestError(
            f"cannot read pack manifest {manifest_path}: {exc}"
        ) from exc

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackManifestError(
            f"{manifest_path}: pack manifest is not valid UTF-8 ({exc})"
        ) from exc

    try:
        document: Any = yaml.load(raw_text, Loader=_StrictLoader)
    except PackManifestError as exc:
        raise PackManifestError(f"{manifest_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PackManifestError(f"{manifest_path}: invalid YAML ({exc})") from exc
    except ValueError as exc:
        # PyYAML's implicit timestamp resolver constructs a real
        # datetime.date/datetime for a timestamp-shaped scalar -- an
        # out-of-range value (e.g. "2020-99-99") raises a raw ValueError
        # from that stdlib constructor, not yaml.YAMLError, and would
        # otherwise escape this loader's documented PackManifestError
        # contract (Codex review, fresh evidence).
        raise PackManifestError(
            f"{manifest_path}: invalid YAML scalar ({exc})"
        ) from exc

    if not isinstance(document, dict):
        raise PackManifestError(
            f"{manifest_path}: top-level pack manifest document must be a "
            f"YAML mapping, got {type(document).__name__}"
        )

    # An unknown/misspelled top-level key (e.g. `assigments:` for
    # `assignments:`) would otherwise be silently ignored -- every field
    # below is read with `.get()`, which has no way to notice an unread key
    # sitting right next to the one it was meant to replace (Codex review).
    # Sorted by `repr`, not the bare keys themselves: a plain `sorted(...)`
    # raises TypeError if the unknown keys are heterogeneously typed (e.g.
    # both `1:` and `extra:` -- Python cannot order int against str), which
    # would surface as an uncontextualized crash instead of the documented
    # PackManifestError this validation exists to produce (Codex review,
    # fresh evidence).
    unknown_top_level = sorted((set(document) - _TOP_LEVEL_MANIFEST_FIELDS), key=repr)
    if unknown_top_level:
        raise PackManifestError(
            f"{manifest_path}: unknown top-level field(s) {unknown_top_level} "
            f"(accepted: {sorted(_TOP_LEVEL_MANIFEST_FIELDS)})"
        )

    pack_id = document.get("id")
    if not isinstance(pack_id, str) or not pack_id:
        raise PackManifestError(
            f"{manifest_path}: 'id' must be a non-empty string, got {pack_id!r}"
        )

    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PackManifestError(
            f"{manifest_path}: 'version' must be an int, got {version!r}"
        )

    kind_raw = document.get("kind")
    # isinstance check first: `kind_raw not in _VALID_PACK_KINDS` calls
    # hash(kind_raw) internally (frozenset membership), so an unhashable
    # value (e.g. `kind: [gate]` decoding to a list) previously raised a raw
    # TypeError instead of the documented PackManifestError (Codex review).
    if not isinstance(kind_raw, str) or kind_raw not in _VALID_PACK_KINDS:
        raise PackManifestError(
            f"{manifest_path}: 'kind' must be one of "
            f"{sorted(_VALID_PACK_KINDS)}, got {kind_raw!r}"
        )
    kind = PackKind(kind_raw)

    assignments_raw = document.get("assignments")
    if not isinstance(assignments_raw, dict):
        raise PackManifestError(
            f"{manifest_path}: 'assignments' must be a YAML mapping, got "
            f"{type(assignments_raw).__name__}"
        )

    source = str(manifest_path)
    assignments: Mapping[str, Hashable]
    if kind is PackKind.POLICY:
        assignments = _parse_policy_assignments(assignments_raw, source=source)
    else:
        assignments = _parse_field_assignments(assignments_raw, source=source)

    identity = ImmutableIdentity(
        id=pack_id, version=version, sha256=hashlib.sha256(raw_bytes).hexdigest()
    )
    return LoadedPack(identity=identity, kind=kind, assignments=assignments)


def assignments_for_conflict_check(
    packs: Sequence[LoadedPack],
) -> dict[PackKind, list[tuple[ImmutableIdentity, Mapping[str, Hashable]]]]:
    """Project *packs*, grouped by :attr:`LoadedPack.kind`, into the
    ``(identity, assignments)`` pairs
    :func:`~abicheck.compatibility_evaluation_resolver.detect_pack_conflicts`
    accepts directly -- call ``detect_pack_conflicts`` once per returned
    group, e.g. ``detect_pack_conflicts(grouped[PackKind.POLICY])``.

    Grouped by kind, not flattened: D8's conflict rule ("the same field or
    ``ChangeKind``") is scoped to comparing packs *within* one of D8's three
    namespaces (contract/language packs, compatibility/policy packs, gate
    packs) against each other, not across them -- a policy pack's
    ``ChangeKind`` slug (e.g. ``func_removed``) and a contract/gate pack's
    own field name are unrelated even when the strings happen to coincide.
    A flat, ungrouped projection previously let exactly that string
    coincidence raise a spurious cross-namespace ``PackConflictError``
    between, say, a policy pack's ``func_removed: break`` and an unrelated
    gate pack's own ``func_removed`` field (Codex review, fresh evidence).
    """
    grouped: dict[PackKind, list[tuple[ImmutableIdentity, Mapping[str, Hashable]]]] = {
        kind: [] for kind in PackKind
    }
    for pack in packs:
        grouped[pack.kind].append((pack.identity, pack.assignments))
    return grouped

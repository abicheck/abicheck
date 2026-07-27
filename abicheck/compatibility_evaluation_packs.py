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

import hashlib
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

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
_TOP_LEVEL_MANIFEST_FIELDS: frozenset[str] = frozenset({"id", "version", "kind", "assignments"})


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
    already gets (Codex review, second round).
    """

    identity: ImmutableIdentity
    kind: PackKind
    assignments: Mapping[str, Hashable]

    def __post_init__(self) -> None:
        frozen = {
            key: _to_hashable(value, where=f"assignments[{key!r}]")
            for key, value in self.assignments.items()
        }
        object.__setattr__(self, "assignments", MappingProxyType(frozen))


def _to_hashable(value: Any, *, where: str) -> Hashable:
    """Convert a decoded YAML scalar/list into a hashable value.

    A YAML mapping value is rejected outright: this module has no defined
    semantics for a nested-mapping pack assignment, and silently accepting
    one would either crash later inside ``detect_pack_conflicts``'s own
    ``hash()`` guard with a less specific error, or -- for a value nested
    inside a list -- bypass that guard entirely (a tuple containing an
    unhashable dict still raises only when something actually calls
    ``hash()`` on it).
    """
    if isinstance(value, list):
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
    try:
        hash(value)
    except TypeError as exc:
        raise PackManifestError(
            f"{where}: assignment value {value!r} is not hashable ({exc})"
        ) from exc
    # value is `Any` (a decoded YAML scalar) -- the hash() call above only
    # proves it's *runtime*-hashable, which mypy can't narrow from; cast
    # rather than widen this function's declared return type to Any.
    return cast(Hashable, value)


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
        verdict = parse_severity_value(severity)
        if verdict is None:
            unknown_severities.append(f"{slug}: {severity!r}")
            continue
        result[slug] = verdict
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
        result[field_name] = _to_hashable(value, where=f"{source}:{field_name}")
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
    unknown_top_level = sorted(
        (set(document) - _TOP_LEVEL_MANIFEST_FIELDS), key=repr
    )
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

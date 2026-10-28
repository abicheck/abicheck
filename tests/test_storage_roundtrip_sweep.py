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

"""One generic sweep over every ``to_dict``/``from_dict`` pair in storage and model.

The recurring defect class here is the *document boundary*: a value that
survives in memory but not through ``to_dict`` -> JSON -> ``from_dict``
(None vs empty, tuple vs list, an omitted default that reads back as a
different default), a ``to_dict`` that hands out a live reference to the
object's own state, or a ``from_dict`` that keeps a reference into its input.
Those were fixed one reported input at a time. This module tests the class of
defect instead:

1. **Discovery is introspective.** Every class in ``abicheck.storage`` and
   ``abicheck.model`` that defines both ``to_dict`` and ``from_dict`` is swept,
   so a new class is covered without editing this file. A class the sweep
   cannot generate values for fails loudly (a field type with no strategy),
   rather than being silently left out.

2. **Skips are explicit and counted.** :data:`_SKIPPED` names the classes that
   need a construction the field-type generator cannot express, each with its
   reason. :data:`_SKIP_COUNT` pins how many there are, so a skip cannot be
   added without a visible edit to this file, and a stale entry naming a class
   that no longer exists fails.

3. **Values come from field types.** Each dataclass is built with a Hypothesis
   strategy derived from its type hints. Strings include the empty string and
   qualified names such as ``ns::T``; containers range from empty to non-empty
   so ``()`` and ``None`` are both generated for an optional field. A value the
   constructor refuses (``ValueError``/``TypeError``) is rejected, so only
   instances the class accepts are checked.

Four properties are asserted for every instance:

* ``from_dict(to_dict(x))`` restores ``x`` (every dataclass field, including
  ``compare=False`` ones), both in memory and after a JSON round trip, and
  re-serializing the restored value gives the same document.
* ``to_dict`` is JSON-serializable and deterministic (two ``sort_keys`` dumps
  are byte-identical).
* Mutating the dictionary ``to_dict`` returned does not change ``x``.
* Mutating the dictionary ``from_dict`` consumed neither changes the restored
  value nor is itself changed by the call.

Comparison is by :func:`_normal`, which keeps every distinction JSON keeps
(``None`` vs ``""``, ``1`` vs ``True``) and drops only tuple-vs-list and
set/mapping order, the same normalization the snapshot round-trip test uses.

Three explicit tables say where the generator is not enough. Each is a
reviewed, reasoned exception, not a silent one:

* :data:`_FIELD_OVERRIDES` -- fields whose valid values are a grammar (ids
  that become filenames, ``<algorithm>:<hex>`` digests, a closed vocabulary).
* :data:`_CANONICALIZERS` -- documented normalizations the codec applies on
  read (``ensure_facts_and_resolve``, ``finalize``, a derived resolver index).
  A value the writer never stores cannot be expected back, so the sweep starts
  from the canonical form and still checks the round trip on it.
* :data:`_NOT_PERSISTED` -- a field the class's own document does not carry,
  excluded from the comparison only, with the owner named.

Each class runs one Hypothesis search that checks all four properties on each
example, so a single generation pass serves them. :data:`_MAX_EXAMPLES` and
:data:`_MAX_CONTAINER` bound the cost; the module runs in about 25s.
"""

from __future__ import annotations

import collections.abc
import copy
import dataclasses
import enum
import functools
import importlib
import inspect
import json
import pkgutil
import types
import typing
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

import abicheck.model as _model_pkg
import abicheck.storage as _storage_pkg
from abicheck.model.contract_conflicts import ALL_CONFLICT_KINDS, ConflictSourceClaim
from abicheck.model.entity_resolver import EntityConflict, EntityResolver
from abicheck.model.graph_facts import ensure_facts_and_resolve
from abicheck.model.ownership_rules import OwnershipRules

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover() -> dict[str, type]:
    """Every class defined in a storage or model module with both methods.

    ``obj.__module__ == module.__name__`` keeps re-exports from registering
    under the wrong home, and walking the packages (rather than a fixed list)
    is what makes a new class self-enrolling.
    """
    found: dict[str, type] = {}
    for pkg in (_storage_pkg, _model_pkg):
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            module = importlib.import_module(info.name)
            for obj in vars(module).values():
                if (
                    inspect.isclass(obj)
                    and obj.__module__ == module.__name__
                    and callable(getattr(obj, "to_dict", None))
                    and callable(getattr(obj, "from_dict", None))
                ):
                    found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return found


_DISCOVERED: dict[str, type] = _discover()

# Classes the field-type generator cannot build a *valid* instance of. Each
# entry is a reason, not a placeholder. Keep this list short: a skip removes a
# class from the sweep entirely, so prefer a builder in `_BUILDERS` when the
# class only needs a construction recipe.
_SKIPPED: dict[str, str] = {}

# The pinned size of `_SKIPPED`. Adding a skip requires changing this number
# in the same diff, so a new exclusion is reviewed rather than slipped in.
_SKIP_COUNT = 0

# Fields a class deliberately does not write to its own document. The field is
# still generated, so the rest of the instance is covered, and it is excluded
# from the comparison only. Keyed by ``(class, field)``; each entry is a reason.
_NOT_PERSISTED: dict[tuple[str, str], str] = {
    ("abicheck.model.scope_acquisition.ScopeAcquisitionRecord", "variant_pairing"): (
        "ADR-062 A1.6: report/comparison_scope.py writes the pairing beside this "
        "record's projection, and nothing reloads the record through from_dict. "
        "The pairing has no from_dict of its own, so there is no codec here to "
        "round-trip."
    ),
}

# The pinned size of `_NOT_PERSISTED`; the same review gate as `_SKIP_COUNT`.
_NOT_PERSISTED_COUNT = 1

# Classes whose valid instances need a construction recipe rather than
# independent field values. A builder returns a strategy of valid instances.
_BUILDERS: dict[type, Callable[[], st.SearchStrategy[Any]]] = {}

_MAX_EXAMPLES = 100
# Container size for generated lists, sets and mappings. Nested containers
# multiply, so this (not the example count) is what sets a class's cost.
_MAX_CONTAINER = 3

# ---------------------------------------------------------------------------
# Value generation from type hints
# ---------------------------------------------------------------------------

# The empty string, the ``::`` separator of a C++ qualified name, unicode, and
# arbitrary text. The empty string is generated on purpose: a field that
# treats "" as absent is exactly the None-vs-empty boundary under test.
_TEXT: st.SearchStrategy[str] = st.one_of(
    st.sampled_from(
        ["", "ns::T", "::", "a", "ns::T<int>", "日本::型", "été", "x\ny", "0"]
    ),
    st.text(max_size=16),
)

_INT: st.SearchStrategy[int] = st.one_of(
    st.just(0), st.integers(min_value=-3, max_value=2**40)
)
_FLOAT: st.SearchStrategy[float] = st.floats(allow_nan=False, allow_infinity=False)

# Anything JSON can carry, for `Any`-typed payloads (`attrs`, `payload`, ...).
_JSON_LEAF: st.SearchStrategy[Any] = st.one_of(
    st.none(), st.booleans(), _INT, _FLOAT, _TEXT
)
_JSON_ANY: st.SearchStrategy[Any] = st.recursive(
    _JSON_LEAF,
    lambda kids: st.one_of(
        st.lists(kids, max_size=3),
        st.dictionaries(_TEXT, kids, max_size=3),
    ),
    max_leaves=6,
)


# Recipes for fields whose valid values are a grammar, not free text. Each is
# keyed by ``(class, field)`` so the exception is named where it applies, and
# each mirrors a validator the class enforces (the reason is beside it). A
# field absent here is generated from its type hint as usual.
_REF_ID: st.SearchStrategy[str] = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
_SHA256_DIGEST: st.SearchStrategy[str] = st.from_regex(
    r"sha256:[0-9a-f]{64}", fullmatch=True
)
_NONEMPTY_TEXT: st.SearchStrategy[str] = st.one_of(
    st.sampled_from(["ns::T", "a", "日本::型", "x\ny", "0"]),
    st.text(min_size=1, max_size=16),
)
_PKG = "abicheck.storage.package"
_CC = "abicheck.model.contract_conflicts"


def _scope_rules_strategy() -> st.SearchStrategy[Any]:
    """Ownership rules as an ``ExtractionScope`` stores them.

    The scope writes four keys of its nested rules, each as a sorted,
    de-duplicated list (``extraction_scope._canonical_rules``), and not the
    nested ``dependency_evidence``: the scope's own field is the persisted one.
    So a generated nested value is in that form, and the sweep checks the rest.
    """
    names = st.lists(_REF_ID, max_size=3).map(lambda xs: tuple(sorted(set(xs))))
    return st.builds(
        OwnershipRules,
        target_roots=names,
        private_headers=names,
        private_namespaces=names,
    )


def _identity_alias_strategy() -> st.SearchStrategy[Any]:
    """Identity aliases a graph can hold without remapping its own node ids.

    ``from_dict`` rewrites node ids through this map, so a non-identity entry
    is a remap, and a remap is only valid as the result of the node-coalescing
    history of ADR-046 D4. A generated map would be a cycle no graph produces,
    so only the fixed points (``k -> k``) are drawn.
    """
    return st.dictionaries(_REF_ID, _REF_ID, max_size=3).map(
        lambda pairs: {key: key for key in pairs}
    )


_FIELD_OVERRIDES: dict[tuple[str, str], st.SearchStrategy[Any]] = {
    # `object_relpath` accepts only "<algorithm>:<hex>"; `kind`/`digest` non-empty.
    (f"{_PKG}.ObjectRef", "kind"): _NONEMPTY_TEXT,
    (f"{_PKG}.ObjectRef", "digest"): _SHA256_DIGEST,
    # Ids become literal filenames (`safe_ref_id`): non-empty, path-safe.
    (f"{_PKG}.ArtifactRef", "artifact_id"): _REF_ID,
    (f"{_PKG}.ArtifactRef", "variant_id"): _REF_ID,
    (f"{_PKG}.VariantRef", "variant_id"): _REF_ID,
    (f"{_PKG}.VariantRef", "artifact_ids"): st.lists(_REF_ID, max_size=3).map(tuple),
    # Closed vocabulary, owned by the model; restating it here would drift.
    (f"{_CC}.ContractSourceConflict", "conflict_kind"): st.sampled_from(
        sorted(ALL_CONFLICT_KINDS)
    ),
    (f"{_CC}.ConflictSourceClaim", "source_kind"): _NONEMPTY_TEXT,
    (f"{_CC}.ConflictSourceClaim", "claim"): _NONEMPTY_TEXT,
    # A conflict names an entity and at least two claims (`__post_init__`).
    (f"{_CC}.ContractSourceConflict", "entity"): _NONEMPTY_TEXT,
    (f"{_CC}.ContractSourceConflict", "sources"): st.lists(
        st.builds(
            ConflictSourceClaim, source_kind=_NONEMPTY_TEXT, claim=_NONEMPTY_TEXT
        ),
        min_size=2,
        max_size=3,
    ).map(tuple),
    ("abicheck.model.extraction_scope.ExtractionScope", "ownership_rules"): (
        _scope_rules_strategy()
    ),
    ("abicheck.model.source_graph.SourceGraphSummary", "identity_aliases"): (
        _identity_alias_strategy()
    ),
}


def _canonical_graph_entity(entity: Any) -> Any:
    """A bare node or edge reaches its stored shape only through this.

    ``ensure_facts_and_resolve`` synthesizes the fact list a bare instance
    lacks, and ``from_dict`` always runs it. An instance with no facts is
    therefore not a state the codec can return, so the sweep starts from the
    canonical one; the round trip itself is still checked on it.
    """
    ensure_facts_and_resolve(entity)
    return entity


def _canonical_source_graph(graph: Any) -> Any:
    """Both derived fields are rebuilt on load, so the sweep starts from them.

    ``from_dict`` rebuilds ``entity_resolver`` from the node set whenever one
    is stored (``resolve_entities``), so a stored conflict is never trusted;
    ``graph_id`` is derived too, and ``to_dict`` writes ``compute_graph_id()``
    for an unset one. ``finalize`` is the documented way to fill it in.
    """
    graph.resolve_entities()
    return graph.finalize()


def _canonical_member(member: Any) -> Any:
    """``from_dict`` stores a display name equal to the member key as ``""``;
    ``name`` reads the same either way, so ``""`` is the canonical spelling."""
    if member.display_name == member.member:
        return dataclasses.replace(member, display_name="")
    return member


def _canonical_surface(surface: Any) -> Any:
    """Obligations are stored ordered by ``(symbol, name)``, and a falsy
    ``source_location`` is not written at all, so it reads back as ``None``.
    Both are the codec's stated canonical form, not a loss: a value the writer
    never persists cannot be expected back."""
    obligations = tuple(
        sorted(
            (
                dataclasses.replace(o, source_location=o.source_location or None)
                for o in surface.obligations
            ),
            key=lambda o: (o.symbol, o.name),
        )
    )
    return dataclasses.replace(surface, obligations=obligations)


# Canonical starting points: a documented normalization the codec applies on
# read, so the sweep compares a value the class can actually hold. The rule
# for admitting one is that the writer never stores the value it changes.
_CANONICALIZERS: dict[str, Callable[[Any], Any]] = {
    "abicheck.model.graph_facts.GraphNode": _canonical_graph_entity,
    "abicheck.model.graph_facts.GraphEdge": _canonical_graph_entity,
    "abicheck.model.source_graph.SourceGraphSummary": _canonical_source_graph,
    "abicheck.model.scope_acquisition.MemberAcquisition": _canonical_member,
    "abicheck.model.release_surface.ReleasePublicSurface": _canonical_surface,
}


def _entity_resolver_strategy() -> st.SearchStrategy[Any]:
    """The index is derived from the aliases the way ``from_dict`` derives it.

    ``_canonical_to_v1`` is a cache of ``aliases`` (first alias wins per
    canonical id). Drawing it independently would build a resolver no codec
    can produce, so the builder derives it with the same rule instead.
    """
    conflict_strategy = _strategy(EntityConflict)

    @st.composite
    def build(draw: Callable[[st.SearchStrategy[Any]], Any]) -> Any:
        aliases = draw(st.dictionaries(_TEXT, _TEXT, max_size=4))
        conflicts = draw(st.lists(conflict_strategy, max_size=2))
        derived: dict[str, str] = {}
        for v1_id, canonical_id in aliases.items():
            derived.setdefault(canonical_id, v1_id)
        return EntityResolver(
            aliases=aliases, conflicts=conflicts, _canonical_to_v1=derived
        )

    return build()


_BUILDERS[EntityResolver] = _entity_resolver_strategy


def _field_strategy(
    class_name: str, field_name: str, hint: Any
) -> st.SearchStrategy[Any]:
    override = _FIELD_OVERRIDES.get((class_name, field_name))
    return override if override is not None else _strategy(hint)


@functools.cache
def _dataclass_strategy(cls: type) -> st.SearchStrategy[Any]:
    """Build instances of a dataclass from its init fields' type hints.

    Only ``init=True`` fields are drawn: the others are derived state the
    constructor computes. A draw the constructor rejects is discarded.
    """
    hints = typing.get_type_hints(cls)
    class_name = f"{cls.__module__}.{cls.__qualname__}"
    drawn = [
        (f.name, _field_strategy(class_name, f.name, hints[f.name]))
        for f in dataclasses.fields(cls)
        if f.init
    ]

    @st.composite
    def build(draw: Callable[[st.SearchStrategy[Any]], Any]) -> Any:
        kwargs = {name: draw(strategy) for name, strategy in drawn}
        try:
            return cls(**kwargs)
        except (TypeError, ValueError):
            assume(False)

    built = build()
    canonical = _CANONICALIZERS.get(class_name)
    return built if canonical is None else built.map(canonical)


_SCALARS: dict[Any, st.SearchStrategy[Any]] = {
    bool: st.booleans(),
    int: _INT,
    float: _FLOAT,
    str: _TEXT,
}


def _strategy(tp: Any) -> st.SearchStrategy[Any]:
    """A strategy of values of type ``tp``, or ``TypeError`` if none is known.

    The ``TypeError`` is deliberate: a field type this generator does not
    understand must fail the sweep, not quietly shrink what is swept.
    """
    if tp in _BUILDERS:
        return _BUILDERS[tp]()
    if tp is Any or tp is object:
        return _JSON_ANY
    if tp is type(None):
        return st.none()

    if tp in _SCALARS:
        return _SCALARS[tp]
    if inspect.isclass(tp) and issubclass(tp, enum.Enum):
        return st.sampled_from(list(tp))
    container = _container_strategy(tp)
    if container is not None:
        return container
    if inspect.isclass(tp) and dataclasses.is_dataclass(tp):
        return _dataclass_strategy(tp)
    raise TypeError(
        f"the round-trip sweep cannot generate values of type {tp!r}; add a "
        "strategy here, a builder in _BUILDERS, or a justified _SKIPPED entry"
    )


def _container_strategy(tp: Any) -> st.SearchStrategy[Any] | None:
    """A strategy for a union or generic container type, else ``None``."""
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Union or origin is types.UnionType:
        return st.one_of(*(_strategy(arg) for arg in args))
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return st.lists(_strategy(args[0]), max_size=_MAX_CONTAINER).map(tuple)
        return st.tuples(*(_strategy(arg) for arg in args))
    if origin is list:
        return st.lists(_strategy(args[0]), max_size=_MAX_CONTAINER)
    if origin in (set, frozenset):
        return st.frozensets(_strategy(args[0]), max_size=_MAX_CONTAINER)
    if origin in (dict, collections.abc.Mapping):
        return st.dictionaries(
            _strategy(args[0]), _strategy(args[1]), max_size=_MAX_CONTAINER
        )
    if origin is collections.abc.Sequence:
        return st.lists(_strategy(args[0]), max_size=_MAX_CONTAINER)
    return None


def _class_strategy(cls: type) -> st.SearchStrategy[Any]:
    if cls in _BUILDERS:
        return _BUILDERS[cls]()
    return _dataclass_strategy(cls)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def _dump(document: Any) -> str:
    """Canonical JSON text. ``allow_nan=False`` and no default: a value that is
    not JSON-encodable raises here, which is the serializability property."""
    return json.dumps(document, sort_keys=True, allow_nan=False)


def _normal(value: Any) -> Any:
    """A comparable form that keeps every distinction a JSON document keeps.

    It drops only what JSON itself cannot draw -- tuple versus list, and the
    order of a set or a mapping -- which is the same normalization the sibling
    snapshot round-trip test applies. Everything else stays: ``None`` is not
    ``""``, ``1`` is not ``True``, and an enum is its member name under its
    type. Every dataclass field is included, ``compare=False`` ones too,
    because dataclass ``__eq__`` skips them and a silently dropped field is
    exactly the defect this sweep exists to catch.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        owner = f"{type(value).__module__}.{type(value).__qualname__}"
        return (
            type(value).__qualname__,
            tuple(
                (f.name, _normal(getattr(value, f.name)))
                for f in dataclasses.fields(value)
                if (owner, f.name) not in _NOT_PERSISTED
            ),
        )
    if isinstance(value, enum.Enum):
        return ("enum", type(value).__qualname__, value.name)
    if isinstance(value, collections.abc.Mapping):
        items = [(_normal(k), _normal(v)) for k, v in value.items()]
        return ("map", tuple(sorted(items, key=lambda kv: repr(kv[0]))))
    if isinstance(value, (list, tuple)):
        return ("seq", tuple(_normal(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return ("set", tuple(sorted((_normal(item) for item in value), key=repr)))
    return (type(value).__qualname__, value)


_MUTATION_MARK = "__sweep_mutated__"


def _scribble(node: Any) -> None:
    """Mutate a document in place, reaching every container it holds.

    A dict gains a key, a list gains an element, and both recurse into their
    values, so any container shared with the object's own state is changed
    and the aliasing shows up as a different value afterwards.
    """
    if isinstance(node, dict):
        node[_MUTATION_MARK] = True
        for value in list(node.values()):
            _scribble(value)
    elif isinstance(node, list):
        node.append(_MUTATION_MARK)
        for item in list(node):
            _scribble(item)
    elif isinstance(node, tuple):
        for item in node:
            _scribble(item)


def _check_round_trip(cls: type, x: Any) -> None:
    document = x.to_dict()
    restored = cls.from_dict(document)
    assert _normal(restored) == _normal(x), (
        f"{cls.__qualname__}: from_dict(to_dict(x)) does not restore x"
    )

    # Document order is kept on purpose: a codec may depend on it (the entity
    # resolver's first-alias-wins index does), and a stored file keeps the
    # order `to_dict` wrote. `_dump` sorts keys, so it is not used here.
    via_json = cls.from_dict(json.loads(json.dumps(document, allow_nan=False)))
    assert _normal(via_json) == _normal(x), (
        f"{cls.__qualname__}: the JSON round trip changed a field"
    )
    assert _dump(via_json.to_dict()) == _dump(document), (
        f"{cls.__qualname__}: re-serializing the restored value changed the document"
    )


def _check_json_deterministic(cls: type, x: Any) -> None:
    first = _dump(x.to_dict())
    second = _dump(x.to_dict())
    assert first == second, f"{cls.__qualname__}: to_dict is not deterministic"


def _check_to_dict_output_does_not_alias(cls: type, x: Any) -> None:
    before = _dump(x.to_dict())
    pristine = copy.deepcopy(x)
    _scribble(x.to_dict())
    assert _dump(x.to_dict()) == before, (
        f"{cls.__qualname__}: mutating to_dict's output changed the object"
    )
    assert _normal(x) == _normal(pristine), (
        f"{cls.__qualname__}: mutating to_dict's output changed a field"
    )


def _check_from_dict_does_not_alias(cls: type, x: Any) -> None:
    document = x.to_dict()
    consumed_before = _dump(document)
    restored = cls.from_dict(document)
    assert _dump(document) == consumed_before, (
        f"{cls.__qualname__}: from_dict mutated its input document"
    )
    restored_before = _dump(restored.to_dict())
    _scribble(document)
    assert _dump(restored.to_dict()) == restored_before, (
        f"{cls.__qualname__}: the restored value shares state with its input"
    )
    assert _normal(restored) == _normal(x)


_PROPERTIES: dict[str, Callable[[type, Any], None]] = {
    "round_trip": _check_round_trip,
    "json_deterministic": _check_json_deterministic,
    "to_dict_no_alias": _check_to_dict_output_does_not_alias,
    "from_dict_no_alias": _check_from_dict_does_not_alias,
}


def _sweep(cls: type, check: Callable[[type, Any], None]) -> None:
    @settings(
        max_examples=_MAX_EXAMPLES,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(_class_strategy(cls))
    def run(x: Any) -> None:
        check(cls, x)

    run()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_SWEPT: tuple[str, ...] = tuple(
    sorted(name for name in _DISCOVERED if name not in _SKIPPED)
)


class TestDiscoveryAndSkipList:
    """The sweep's own coverage must not shrink without a visible edit."""

    def test_skip_list_size_is_pinned(self) -> None:
        assert len(_SKIPPED) == _SKIP_COUNT, (
            f"_SKIPPED has {len(_SKIPPED)} entries but _SKIP_COUNT pins "
            f"{_SKIP_COUNT}; a new exclusion must change the pin in the same diff"
        )

    def test_every_skip_names_a_real_class(self) -> None:
        stale = sorted(name for name in _SKIPPED if name not in _DISCOVERED)
        assert stale == [], f"_SKIPPED names classes that no longer exist: {stale}"

    def test_every_skip_has_a_reason(self) -> None:
        empty = sorted(name for name, reason in _SKIPPED.items() if not reason.strip())
        assert empty == [], f"these skips have no reason: {empty}"

    def test_not_persisted_size_is_pinned_and_names_real_fields(self) -> None:
        assert len(_NOT_PERSISTED) == _NOT_PERSISTED_COUNT, (
            f"_NOT_PERSISTED has {len(_NOT_PERSISTED)} entries but "
            f"_NOT_PERSISTED_COUNT pins {_NOT_PERSISTED_COUNT}"
        )
        for (owner, field_name), reason in _NOT_PERSISTED.items():
            assert reason.strip(), f"{owner}.{field_name} has no reason"
            cls = _DISCOVERED.get(owner)
            assert cls is not None, f"_NOT_PERSISTED names an unknown class {owner}"
            names = {f.name for f in dataclasses.fields(cls)}
            assert field_name in names, f"{owner} has no field {field_name!r}"

    def test_discovery_reaches_the_known_storage_and_model_classes(self) -> None:
        # A floor, not a snapshot: if the walk silently found nothing, every
        # parametrized case would vanish and the module would pass vacuously.
        assert len(_DISCOVERED) >= 30, sorted(_DISCOVERED)
        for name in (
            "abicheck.storage.entity_ids.EntityId",
            "abicheck.storage.entity_ids.OccurrenceId",
            "abicheck.storage.fact_availability.FactAvailability",
            "abicheck.storage.availability_ledger.AvailabilityLedger",
            "abicheck.storage.occurrence_identity.IdentityConflict",
            "abicheck.storage.occurrence_identity.OccurrenceSet",
            "abicheck.storage.versioning.StorageVersions",
            "abicheck.storage.package.PackageManifest",
            "abicheck.model.source_graph.SourceGraphSummary",
        ):
            assert name in _DISCOVERED, f"discovery missed {name}"

    def test_every_discovered_class_is_swept_or_skipped(self) -> None:
        assert set(_SWEPT) | set(_SKIPPED) == set(_DISCOVERED)
        assert set(_SWEPT).isdisjoint(_SKIPPED)


def _all_properties(cls: type, x: Any) -> None:
    for check in _PROPERTIES.values():
        check(cls, x)


def _flagged_edge_instances() -> list[tuple[str, Any]]:
    """Hand-built instances at the boundaries the four flagged modules guard.

    The generator reaches these by chance; naming them makes the boundary the
    test's subject. Each is the empty or absent form of a field the document
    writer omits (``discriminator``, ``container``, ``producer``, ``diagnostics``),
    a value that must not read back as its default, or an ambiguity whose
    occurrences must both survive the round trip.
    """
    from abicheck.model.identity import EntityKind, ObservationKind
    from abicheck.storage.availability_ledger import AvailabilityLedger
    from abicheck.storage.availability_status import FactStatus
    from abicheck.storage.entity_ids import EntityId, OccurrenceId
    from abicheck.storage.fact_availability import FactAvailability
    from abicheck.storage.occurrence_identity import IdentityConflict, OccurrenceSet

    kind = next(iter(EntityKind))
    observation = next(iter(ObservationKind))
    bare = EntityId(kind=kind, qualified_name="ns::T", discriminator="")
    named = EntityId(kind=kind, qualified_name="ns::T", discriminator="0")
    plain = OccurrenceId(
        entity=bare, observation=observation, container="", attributes=(), producer=""
    )
    repeated = OccurrenceId(
        entity=bare,
        observation=observation,
        container="unit",
        attributes=(("sym", "a"), ("sym", "a")),
        producer="clang",
    )
    other = OccurrenceId(
        entity=bare, observation=observation, container="unit", producer="castxml"
    )
    occurrences = OccurrenceSet()
    occurrences.add(plain)
    occurrences.add(other)
    cases: list[tuple[str, Any]] = [
        ("entity_id_empty_discriminator", bare),
        ("entity_id_zero_discriminator", named),
        ("occurrence_id_bare", plain),
        ("occurrence_id_repeated_attribute_key", repeated),
        ("occurrence_set_empty", OccurrenceSet()),
        ("occurrence_set_two_producers", occurrences),
        (
            "identity_conflict_two_producers",
            IdentityConflict(
                reason="two producers disagree",
                occurrences=(plain, other),
            ),
        ),
        ("availability_ledger_empty", AvailabilityLedger()),
        (
            "availability_ledger_one_family",
            AvailabilityLedger(
                families={"layout": FactAvailability(status=FactStatus.PRESENT)},
            ),
        ),
    ]
    for status in FactStatus:
        cases.append(
            (
                f"fact_availability_{status.name.lower()}",
                FactAvailability(status=status),
            )
        )
    return cases


_FLAGGED_CASES: list[tuple[str, Any]] = _flagged_edge_instances()


@pytest.mark.parametrize(
    ("label", "instance"),
    _FLAGGED_CASES,
    ids=[label for label, _ in _FLAGGED_CASES],
)
def test_flagged_boundary_instances_satisfy_the_document_properties(
    label: str, instance: Any
) -> None:
    _all_properties(type(instance), instance)


def test_flagged_modules_are_swept_not_skipped() -> None:
    """The four modules the review flagged must stay inside the sweep."""
    flagged = {
        "abicheck.storage.entity_ids.EntityId",
        "abicheck.storage.entity_ids.OccurrenceId",
        "abicheck.storage.fact_availability.FactAvailability",
        "abicheck.storage.availability_ledger.AvailabilityLedger",
        "abicheck.storage.occurrence_identity.IdentityConflict",
        "abicheck.storage.occurrence_identity.OccurrenceSet",
    }
    assert flagged <= set(_SWEPT), sorted(flagged - set(_SWEPT))


@pytest.mark.parametrize("qualname", _SWEPT)
def test_every_storage_and_model_class_satisfies_the_document_properties(
    qualname: str,
) -> None:
    """All four properties for one class, over generated instances.

    One Hypothesis run per class checks every property on each example, so a
    single generation pass serves all four; each assertion names the class and
    the property it checks.
    """
    _sweep(_DISCOVERED[qualname], _all_properties)

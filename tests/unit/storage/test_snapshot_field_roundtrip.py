# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Auto-enumerated per-field round-trip coverage for ``AbiSnapshot``.

The hand-written fixture in ``tests/test_snapshot_roundtrip.py`` leaves most
model dataclass fields at their defaults, so a codec that silently drops a
non-default value would pass it. This module instead enumerates every
``init`` dataclass field reachable from :class:`AbiSnapshot` (following
``Fact``/``Optional``/``list``/``tuple``/``set``/``dict`` wrappers and the
``declarations`` store) and, for each ``Class.field``:

1. synthesizes a non-default value from the field's type annotation,
2. places it in an ``AbiSnapshot`` (ancestors built with synthesized
   required fields, every other field at its dataclass default),
3. asserts ``dump -> load -> dump`` is byte-identical and the value survives.

Enum-valued status fields are not sampled by one synthesized value: each
member gets its own case (:data:`_STATUS_CASES`), so a member that the codec
drops is named, not hidden behind the first member that happened to be
picked. ``ownership_fact`` is enumerated like any other field, inside an
``extraction_scope`` (the only place ADR-075 persists it), and every
non-``PRESENT`` status is its own case as well.

Fields the encoder does not persist by design (the ``_``-prefixed lookup
caches and the ``snapshot_encode._SNAPSHOT_SKIP_FIELDS`` owners) are imported
from the encoder rather than hard-coded, and are not enumerated.

Known lossy cases live in :data:`KNOWN_LOSSY`, keyed by field id
(``Class.field``) or by status case (``Class.field[member]``, or
``ownership_fact[...]``). Each is ``xfail(strict=True)``: fixing one makes its
test XPASS, which fails the suite until the entry is removed. Two further
tests keep the table honest: :func:`test_every_known_lossy_entry_is_lossy`
re-runs each entry's own case and requires it to still be lossy (so an entry
whose defect was fixed by a path that the strict xfail does not reach still
fails), and :data:`_KNOWN_LOSSY_CEILING` ratchets the table so it may only
shrink.

Normalization (applied to both sides before comparison, and only for things
JSON cannot distinguish): ``tuple``/``list`` are the same sequence,
``set``/``frozenset`` compare as multisets, dict order is ignored, enums
compare by member name, ``Fact`` compares status, value and diagnostics.
One further equivalence is documented in :func:`_norm_slot`: an absent
``Fact`` slot reads back as ``NOT_COLLECTED``. It does not apply to
``ownership_fact``, whose decoder reads an absent entry as ``None``.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import enum
import functools
import importlib
import json
import pkgutil
import sys
import types
import typing
from collections.abc import Callable
from typing import Any, Literal, Union

import pytest

import abicheck.buildsource as _buildsource
import abicheck.model as _model_pkg
from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    Variable,
)
from abicheck.model.availability import FactStatus
from abicheck.model.declaration_store import Declarations
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, AdvancedExtractionStatus
from abicheck.model.extraction_scope import EntityOwnership, ExtractionScope
from abicheck.model.fact import Fact
from abicheck.model.header_exclusion_record import KNOWN_MATCHING_RULES
from abicheck.model.snapshot_reliability import FACT_FAMILIES
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.snapshot_encode import _SNAPSHOT_SKIP_FIELDS

_ADVANCED_STATUS_REASON = (
    "dwarf_advanced.extraction is dropped by the encoder "
    "(snapshot_encode._drop_advanced_extraction_status) because persisting it "
    "adds a snapshot key, a schema change that ADR-080 (Proposed) has not "
    "decided. Every status reads back as UNRECORDED, so a stored snapshot falls "
    "back to the payload inference; see known-gaps/"
    "advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md"
)
_OWNERSHIP_STATUS_REASON = (
    "ownership_fact is persisted only as a PRESENT decision inside the "
    "extraction_scope block (ADR-075): the decision table holds classified "
    "(owner, contract, rule) triples and nothing else, so any other status is "
    "written as unclassified and reads back as None. ownership_of() folds both "
    "to 'unknown', so no verdict changes, but the Fact itself is not "
    "round-tripped; closing it needs a per-entry status encoding (a schema "
    "change). See known-gaps/ownership-fact-persists-only-a-present-decision.md"
)

#: Case id -> reason it does not round-trip today. An id is either a field
#: (``Class.field``, the enumerated value is lossy) or one member of an enum
#: or status field (``Class.field[member]`` / ``ownership_fact[member]``).
#: Each is an ``xfail(strict=True)`` real defect, or a deliberate limitation
#: named here with its reason; remove the entry when the codec is fixed.
KNOWN_LOSSY: dict[str, str] = {
    "AbiSnapshot.from_headers_inferred": "dump is not byte-stable: the loaded snapshot re-emits from_headers=false, which the first dump omitted",
    "AbiSnapshot.fact_generations": "dump is not byte-stable: the second dump adds a key (source) absent from the first",
    "AdvancedDwarfMetadata.toolchain": "ToolchainInfo.wchar_flags is dropped on load",
    "ToolchainInfo.wchar_flags": "list of wchar_flags is dropped on load",
    "EnumInfo.decl_file": "decl_file reads back as null",
    "StructLayout.decl_file": "decl_file reads back as null",
    "MachoExport.is_data": "a True is_data reads back as False",
    "Declarations.debug_advanced": (
        "the synthesized AdvancedDwarfMetadata carries extraction=NOT_RUN, which "
        "the encoder drops; the per-status cases below name each status"
    ),
    "AdvancedDwarfMetadata.extraction[not_run]": _ADVANCED_STATUS_REASON,
    "AdvancedDwarfMetadata.extraction[completed]": _ADVANCED_STATUS_REASON,
    "AdvancedDwarfMetadata.extraction[failed]": _ADVANCED_STATUS_REASON,
    "ownership_fact[partial]": _OWNERSHIP_STATUS_REASON,
    "ownership_fact[not_collected]": _OWNERSHIP_STATUS_REASON,
    "ownership_fact[unsupported]": _OWNERSHIP_STATUS_REASON,
    "ownership_fact[failed]": _OWNERSHIP_STATUS_REASON,
    "ownership_fact[not_applicable]": _OWNERSHIP_STATUS_REASON,
    "ownership_fact[no_extraction_scope]": (
        "encode_extraction_scope writes decisions only inside an extraction_scope "
        "block; a snapshot without one loads extraction_scope=None and no decisions "
        "(ADR-075: an unrecorded scope is never read as recorded), so a PRESENT "
        "ownership fact set without a scope is dropped. stamp_ownership always "
        "records the scope with its decisions, so no producer reaches this state"
    ),
}

#: Ratchet: ``KNOWN_LOSSY`` may only shrink. Lower this when entries are removed.
_KNOWN_LOSSY_CEILING = 17

#: Fields the encoder deliberately never persists (documented in the model as
#: "Runtime-only ... never serialized"). Not enumerated.
_RUNTIME_ONLY: frozenset[str] = frozenset(
    {
        "live_source_evidence",
        "live_preprocessor_clang_bin",
        "load_notices",
        "static_archive",
    }
)

#: Fields persisted only through a dedicated, conditional codec. A standalone
#: value is intentionally not written, so the field is not enumerated.
_BY_DESIGN: dict[str, str] = {
    "AbiSnapshot.spellings_generation": "written only when it exceeds the extractor generation (generation_migrations.encode_fact_generations)",
    "ExtractionScope.ownership_rules": "ownership rules are persisted by the extraction-scope codec (ADR-075)",
    "OwnershipRules.dependency_evidence": "persisted by the extraction-scope codec (ADR-075)",
}

#: Fields replaced by per-member status cases (:data:`_STATUS_CASES`) instead of
#: one synthesized value, so every member is named rather than one sampled.
_EXPANDED_FIELDS: frozenset[str] = frozenset({"AdvancedDwarfMetadata.extraction"})

#: Field whose value is persisted only inside an ``extraction_scope`` block.
_OWNERSHIP_FIELD = "ownership_fact"

_MAX_DEPTH = 6

#: Domain-constrained ``str`` fields the decoder validates; a free-form
#: synthesized string would be rejected as a corrupt snapshot.
_STR_DOMAINS: dict[str, tuple[str, ...]] = {
    "dependency_scope": ("filtered", "full"),
    "excluded_header_matching": tuple(sorted(KNOWN_MATCHING_RULES)),
}
_NO_AVOID: Any = object()


class _Unsynthesizable(Exception):
    """The annotation has no non-default value this generator can build."""


# --------------------------------------------------------------------------
# Type hints across the model package (TYPE_CHECKING-only imports resolved
# from the union of every model/buildsource dataclass namespace).
# --------------------------------------------------------------------------


def _global_namespace() -> dict[str, Any]:
    ns: dict[str, Any] = {}
    mods = [_model_pkg, _buildsource]
    names = [
        *pkgutil.walk_packages(_model_pkg.__path__, "abicheck.model."),
        *pkgutil.walk_packages(_buildsource.__path__, "abicheck.buildsource."),
    ]
    for info in names:
        mod = importlib.import_module(info.name)
        for key, val in vars(mod).items():
            if isinstance(val, type) and dataclasses.is_dataclass(val):
                ns.setdefault(key, val)
    del mods
    return ns


_NAMESPACE = _global_namespace()


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    module_ns = vars(sys.modules[cls.__module__])
    return typing.get_type_hints(cls, globalns={**_NAMESPACE, **module_ns})


# --------------------------------------------------------------------------
# Value synthesis.
# --------------------------------------------------------------------------

_SCALAR_CANDIDATES: dict[type, tuple[Any, ...]] = {
    bool: (True, False),
    int: (7, 8),
    float: (1.5, 2.5),
    str: ("synth-a", "synth-b"),
    bytes: (b"synth",),
}


def _differs(value: Any, avoid: Any) -> bool:
    if avoid is _NO_AVOID:
        return True
    return type(value) is not type(avoid) or value != avoid


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError as exc:  # pragma: no cover - guard only
        raise _Unsynthesizable(f"unhashable {type(value).__name__}") from exc
    return value


def _synth(hint: Any, avoid: Any = _NO_AVOID, depth: int = 0) -> Any:
    """A value of ``hint``, different from ``avoid`` when one is given."""
    if depth > _MAX_DEPTH:
        raise _Unsynthesizable("nesting too deep")
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if hint is Any or hint is object:
        return "synth-any"
    if hint is type(None):
        return None
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        return _synth(non_none[0], avoid, depth)
    if origin is Literal:
        for choice in args:
            if _differs(choice, avoid):
                return choice
        raise _Unsynthesizable("Literal has no alternative")
    if origin is Fact:
        return Fact.present(_synth(args[0], _NO_AVOID, depth + 1))
    if origin is list:
        return [_synth(args[0], _NO_AVOID, depth + 1)]
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return (_synth(args[0], _NO_AVOID, depth + 1),)
        return tuple(_synth(a, _NO_AVOID, depth + 1) for a in args)
    if origin in (set, frozenset):
        return origin([_hashable(_synth(args[0], _NO_AVOID, depth + 1))])
    if origin in (dict, collections.abc.Mapping):
        key = _hashable(_synth(args[0], _NO_AVOID, depth + 1))
        return {key: _synth(args[1], _NO_AVOID, depth + 1)}
    if isinstance(hint, type):
        if issubclass(hint, enum.Enum):
            for member in hint:
                if _differs(member, avoid):
                    return member
            raise _Unsynthesizable(f"{hint.__qualname__} has one member")
        if hint in _SCALAR_CANDIDATES:
            for choice in _SCALAR_CANDIDATES[hint]:
                if _differs(choice, avoid):
                    return choice
            raise _Unsynthesizable(f"no {hint.__name__} candidate differs")
        if dataclasses.is_dataclass(hint):
            if avoid is _NO_AVOID:
                return _construct(hint, {}, depth + 1)
            return _construct(hint, _rich_overrides(hint, depth + 1), depth + 1)
    raise _Unsynthesizable(f"no synthesizer for {hint!r}")


def _rich_overrides(cls: type, depth: int) -> dict[str, Any]:
    """Every init field synthesized (a non-default instance of ``cls``)."""
    hints = _hints(cls)
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.init and not f.name.startswith("_") and f.name not in _STR_DOMAINS:
            out[f.name] = _synth(hints[f.name], _NO_AVOID, depth)
    return out


def _construct(cls: type, overrides: dict[str, Any], depth: int = 0) -> Any:
    """Instantiate ``cls``: overrides, then synthesized required fields."""
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if not f.init:
            continue
        if f.name in overrides:
            kwargs[f.name] = overrides[f.name]
        elif (
            f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        ):
            kwargs[f.name] = _synth(hints[f.name], _NO_AVOID, depth)
    return cls(**kwargs)


def _default_of(f: dataclasses.Field[Any]) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return _NO_AVOID


def _nondefault_value(cls: type, fname: str) -> Any:
    f = next(x for x in dataclasses.fields(cls) if x.name == fname)
    avoid = _default_of(f)
    if fname == "stale_fact_families":
        return frozenset({sorted(FACT_FAMILIES)[0]})
    if fname in _STR_DOMAINS:
        value = next(v for v in _STR_DOMAINS[fname] if _differs(v, avoid))
    else:
        value = _synth(_hints(cls)[fname], avoid, 1)
    if not _differs(value, avoid):
        raise _Unsynthesizable("synthesized value equals the default")
    return value


# --------------------------------------------------------------------------
# Wrapper chains: how a child dataclass sits inside a field.
# --------------------------------------------------------------------------

Chain = tuple[tuple[str, Any], ...]


def _unwrap(hint: Any) -> tuple[Chain, Any]:
    chain: list[tuple[str, Any]] = []
    while True:
        origin = typing.get_origin(hint)
        args = typing.get_args(hint)
        if origin is Union or origin is types.UnionType:
            hint = next(a for a in args if a is not type(None))
        elif origin is Fact:
            chain.append(("fact", None))
            hint = args[0]
        elif origin is list:
            chain.append(("list", None))
            hint = args[0]
        elif origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            chain.append(("tuple", None))
            hint = args[0]
        elif origin in (set, frozenset):
            chain.append(("set", origin))
            hint = args[0]
        elif origin in (dict, collections.abc.Mapping):
            chain.append(("dict", args[0]))
            hint = args[1]
        else:
            return tuple(chain), hint


def _wrap(chain: Chain, value: Any) -> Any:
    for kind, extra in reversed(chain):
        if kind == "fact":
            value = Fact.present(value)
        elif kind == "list":
            value = [value]
        elif kind == "tuple":
            value = (value,)
        elif kind == "set":
            value = extra([value])
        elif kind == "dict":
            value = {_synth(extra, _NO_AVOID, 1): value}
    return value


def _unwrap_value(kind: str, obj: Any) -> Any:
    if kind == "fact":
        return obj.value
    if kind in ("list", "tuple"):
        return obj[0]
    if kind == "set":
        return next(iter(obj))
    return next(iter(obj.values()))


# --------------------------------------------------------------------------
# Reachability graph from AbiSnapshot and placement paths.
# --------------------------------------------------------------------------

# (parent class, field name, wrapper chain, child class). ``declarations`` is a
# property backed by the semantic-IR store, not a dataclass field.
_Edge = tuple[type, str, Chain, type]
_DECLARATIONS = "declarations"


def _edges_from(cls: type) -> list[_Edge]:
    out: list[_Edge] = []
    hints = _hints(cls)
    if cls is AbiSnapshot:
        out.append((cls, _DECLARATIONS, (), Declarations))
    for f in dataclasses.fields(cls):
        if f.name.startswith("_"):
            continue
        if cls is AbiSnapshot and f.name in _SNAPSHOT_SKIP_FIELDS:
            continue
        chain, inner = _unwrap(hints[f.name])
        if isinstance(inner, type) and dataclasses.is_dataclass(inner):
            out.append((cls, f.name, chain, inner))
    return out


def _reachable_paths() -> dict[type, list[_Edge]]:
    """Shortest edge path from AbiSnapshot to every reachable dataclass."""
    paths: dict[type, list[_Edge]] = {AbiSnapshot: []}
    queue = [AbiSnapshot]
    while queue:
        cls = queue.pop(0)
        for edge in _edges_from(cls):
            child = edge[3]
            if child not in paths:
                paths[child] = paths[cls] + [edge]
                queue.append(child)
    return paths


_PATHS = _reachable_paths()


def _enumerated_fields() -> list[tuple[type, str]]:
    out: list[tuple[type, str]] = []
    for cls in _PATHS:
        for f in dataclasses.fields(cls):
            if not f.init or f.name.startswith("_"):
                continue
            if cls is AbiSnapshot and f.name in _SNAPSHOT_SKIP_FIELDS:
                continue
            if cls is AbiSnapshot and f.name in _RUNTIME_ONLY:
                continue
            if _field_id(cls, f.name) in _BY_DESIGN:
                continue
            if _field_id(cls, f.name) in _EXPANDED_FIELDS:
                continue
            out.append((cls, f.name))
    return sorted(out, key=lambda cf: (cf[0].__qualname__, cf[1]))


def _field_id(cls: type, fname: str) -> str:
    return f"{cls.__qualname__}.{fname}"


def _needs_scope(cls: type, fname: str, path: list[_Edge]) -> bool:
    """Whether the case can only be persisted inside an ``extraction_scope``.

    ADR-075 writes ``ownership_fact`` only inside the scope block, so a case
    that places one (the field itself, or anything reached through it) must
    record a scope, or it tests the documented drop rather than the codec.
    """
    return (
        fname == _OWNERSHIP_FIELD
        or cls is EntityOwnership
        or any(edge[1] == _OWNERSHIP_FIELD for edge in path)
    )


def _build_case(
    cls: type, fname: str, value: Any = _NO_AVOID
) -> tuple[AbiSnapshot, list[_Edge], Any]:
    """Place a leaf ``cls`` with ``fname`` set inside an AbiSnapshot.

    ``value`` defaults to a synthesized non-default value; a status case passes
    the member it is testing.
    """
    path = _PATHS[cls]
    if value is _NO_AVOID:
        value = _nondefault_value(cls, fname)
    leaf = _construct(cls, {fname: value})
    result: Any = leaf
    snap: AbiSnapshot | None = None
    for parent, fld, chain, _child in reversed(path):
        if parent is AbiSnapshot and fld == _DECLARATIONS:
            snap = _construct(AbiSnapshot, {})
            for df in dataclasses.fields(Declarations):
                setattr(snap.declarations, df.name, getattr(result, df.name))
            break
        result = _construct(parent, {fld: _wrap(chain, result)})
    if snap is None:
        assert isinstance(result, AbiSnapshot)
        snap = result
    if _needs_scope(cls, fname, path):
        snap.extraction_scope = ExtractionScope()
    return snap, path, leaf


def _navigate(root: AbiSnapshot, path: list[_Edge]) -> Any:
    obj: Any = root
    for _parent, fld, chain, _child in path:
        obj = getattr(obj, fld)
        for kind, _extra in chain:
            obj = _unwrap_value(kind, obj)
    return obj


# --------------------------------------------------------------------------
# Normalization allowlist.
# --------------------------------------------------------------------------


def _is_fact_slot(hint: Any) -> bool:
    chain, _inner = _unwrap(hint)
    return bool(chain) and chain[0][0] == "fact"


def _norm_slot(hint: Any, value: Any, name: str) -> Any:
    """Documented absence equivalence: a ``Fact`` slot holding ``None`` is
    ``NOT_COLLECTED``.

    The decoder reads an absent ``Fact`` key back as ``NOT_COLLECTED``
    (``fact_codec``), so a leaf's ``None`` in such a slot and a loaded
    ``Fact(NOT_COLLECTED)`` are the same absence. This is the one status-level
    equivalence the comparison makes, and it is not applied to
    ``ownership_fact``: that decoder reads an absent entry as ``None``, so a
    leaf ``NOT_COLLECTED`` there must not compare equal to what loads.
    """
    if value is None and _is_fact_slot(hint) and name != _OWNERSHIP_FIELD:
        return ("Fact", FactStatus.NOT_COLLECTED.name, None, ())
    return _norm(value)


def _norm(value: Any) -> Any:
    if isinstance(value, Fact):
        return ("Fact", value.status.name, _norm(value.value), tuple(value.diagnostics))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        hints = _hints(type(value))
        return (
            type(value).__qualname__,
            tuple(
                (f.name, _norm_slot(hints[f.name], getattr(value, f.name), f.name))
                for f in dataclasses.fields(value)
            ),
        )
    if isinstance(value, enum.Enum):
        return (type(value).__qualname__, value.name)
    if isinstance(value, (list, tuple)):
        return ("seq", [_norm(x) for x in value])
    if isinstance(value, (set, frozenset)):
        return ("set", sorted((_norm(x) for x in value), key=repr))
    if isinstance(value, dict):
        return (
            "map",
            sorted(((_norm(k), _norm(v)) for k, v in value.items()), key=repr),
        )
    return value


def _norm_attr(owner: Any, fname: str) -> Any:
    hints = _hints(type(owner))
    return _norm_slot(hints[fname], getattr(owner, fname), fname)


# --------------------------------------------------------------------------
# Cases: one id per field, per enum member, and per ownership status.
# --------------------------------------------------------------------------

#: One check: ``(locate, expected_owner, attribute)``. ``locate`` finds the
#: object on the *loaded* snapshot; ``expected_owner`` is the leaf it was
#: built from; the attribute is compared under :func:`_norm_attr`.
_Check = tuple[Callable[[AbiSnapshot], Any], Any, str]
_CaseBuilder = Callable[[], tuple[AbiSnapshot, list[_Check]]]


def _field_case(cls: type, fname: str) -> tuple[AbiSnapshot, list[_Check]]:
    snap, path, leaf = _build_case(cls, fname)
    return snap, [(lambda s, p=path: _navigate(s, p), leaf, fname)]


def _enum_member_case(
    cls: type, fname: str, member: enum.Enum
) -> tuple[AbiSnapshot, list[_Check]]:
    snap, path, leaf = _build_case(cls, fname, member)
    return snap, [(lambda s, p=path: _navigate(s, p), leaf, fname)]


#: Sample owner for a ``PRESENT`` ownership decision.
_OWNERSHIP_SAMPLE = EntityOwnership("synth-owner", "synth-contract", "synth-rule")


def _ownership_case(
    status: FactStatus, *, scoped: bool
) -> tuple[AbiSnapshot, list[_Check]]:
    """One declaration of each ownership-bearing kind, all carrying *status*.

    The four lists share one codec, so one snapshot checks all four kinds. The
    ``scoped`` flag decides whether the snapshot records an ``extraction_scope``.
    """
    fact = (
        Fact.present(_OWNERSHIP_SAMPLE)
        if status is FactStatus.PRESENT
        else Fact(status=status)
    )
    snap = _construct(AbiSnapshot, {})
    checks: list[_Check] = []
    for list_name, cls in (
        ("functions", Function),
        ("variables", Variable),
        ("types", RecordType),
        ("enums", EnumType),
    ):
        decl = _construct(cls, {_OWNERSHIP_FIELD: fact})
        setattr(snap.declarations, list_name, [decl])
        checks.append(
            (
                lambda s, n=list_name: getattr(s.declarations, n)[0],
                decl,
                _OWNERSHIP_FIELD,
            )
        )
    if scoped:
        snap.extraction_scope = ExtractionScope()
    return snap, checks


def _collect() -> tuple[dict[str, _CaseBuilder], list[str]]:
    builders: dict[str, _CaseBuilder] = {}
    unsynth: list[str] = []
    for cls, fname in _enumerated_fields():
        try:
            _build_case(cls, fname)
        except _Unsynthesizable as exc:
            unsynth.append(f"{_field_id(cls, fname)}: {exc}")
            continue
        builders[_field_id(cls, fname)] = functools.partial(_field_case, cls, fname)
    for member in AdvancedExtractionStatus:
        builders[f"AdvancedDwarfMetadata.extraction[{member.value}]"] = (
            functools.partial(
                _enum_member_case, AdvancedDwarfMetadata, "extraction", member
            )
        )
    for status in FactStatus:
        builders[f"ownership_fact[{status.value}]"] = functools.partial(
            _ownership_case, status, scoped=True
        )
    builders["ownership_fact[no_extraction_scope]"] = functools.partial(
        _ownership_case, FactStatus.PRESENT, scoped=False
    )
    return builders, unsynth


_CASE_BUILDERS, _UNSYNTHESIZABLE = _collect()


def _round_trip_defects(snap: AbiSnapshot, checks: list[_Check]) -> list[str]:
    """Every way a dump -> load -> dump fails to keep the case; empty if none."""
    first = snapshot_to_json(snap)
    loaded = snapshot_from_dict(json.loads(first))
    defects: list[str] = []
    if snapshot_to_json(loaded) != first:
        defects.append("dump -> load -> dump is not byte-identical")
    for locate, leaf, fname in checks:
        label = f"{type(leaf).__qualname__}.{fname}"
        try:
            got = locate(loaded)
        except (AttributeError, IndexError, TypeError):
            defects.append(f"{label}: owner absent after load")
            continue
        if _norm_attr(got, fname) != _norm_attr(leaf, fname):
            defects.append(f"{label} differs after load")
    return defects


_CASE_PARAMS = [
    pytest.param(
        cid,
        id=cid,
        marks=(pytest.mark.xfail(strict=True, reason=KNOWN_LOSSY[cid]),)
        if cid in KNOWN_LOSSY
        else (),
    )
    for cid in sorted(_CASE_BUILDERS)
]


@pytest.mark.parametrize("case_id", _CASE_PARAMS)
def test_case_survives_dump_load_dump(case_id: str) -> None:
    snap, checks = _CASE_BUILDERS[case_id]()
    defects = _round_trip_defects(snap, checks)
    assert not defects, defects


@pytest.mark.parametrize("case_id", sorted(KNOWN_LOSSY))
def test_every_known_lossy_entry_is_lossy(case_id: str) -> None:
    # The strict xfail above only fails once a case stops failing. This asserts
    # the entry is still a live defect now, so a stale entry (a case that never
    # tested the loss, or a loss that was fixed) fails with its own name.
    assert case_id in _CASE_BUILDERS, f"KNOWN_LOSSY names no case: {case_id}"
    snap, checks = _CASE_BUILDERS[case_id]()
    assert _round_trip_defects(snap, checks), (
        f"{case_id} round-trips faithfully now; remove its KNOWN_LOSSY entry"
    )


def test_every_reachable_field_is_synthesizable() -> None:
    assert not _UNSYNTHESIZABLE, "fields without a synthesizer:\n" + "\n".join(
        _UNSYNTHESIZABLE
    )


def test_known_lossy_is_a_real_case_list_and_only_shrinks() -> None:
    stale = sorted(set(KNOWN_LOSSY) - set(_CASE_BUILDERS))
    assert not stale, f"KNOWN_LOSSY names no case: {stale}"
    assert len(KNOWN_LOSSY) == _KNOWN_LOSSY_CEILING, (
        f"KNOWN_LOSSY has {len(KNOWN_LOSSY)} entries; lower _KNOWN_LOSSY_CEILING to match"
    )


def test_field_coverage_is_substantial() -> None:
    # Guards against the walker silently collapsing to a handful of classes.
    assert len(_CASE_PARAMS) >= 400, len(_CASE_PARAMS)


def _verdict_view(result: Any) -> tuple[str, list[str]]:
    return result.verdict, sorted(str(c.kind) for c in result.changes)


def _base() -> AbiSnapshot:
    snap = AbiSnapshot(library="libmut.so", version="1")
    snap.declarations.functions = [
        Function(
            name="f",
            mangled="_Zf",
            return_type="int",
            params=[Param(name="p", type="int")],
        ),
        Function(name="g", mangled="_Zg", return_type="void"),
    ]
    return snap


_MUTATIONS: dict[str, Callable[[AbiSnapshot], None]] = {
    "return_type_changed": lambda s: setattr(
        s.declarations.functions[0], "return_type", "long"
    ),
    "param_type_changed": lambda s: setattr(
        s.declarations.functions[0].params[0], "type", "long"
    ),
    "function_removed": lambda s: setattr(
        s.declarations, "functions", s.declarations.functions[1:]
    ),
    "function_added": lambda s: s.declarations.functions.append(
        Function(name="h", mangled="_Zh", return_type="int")
    ),
    "param_removed": lambda s: setattr(s.declarations.functions[0], "params", []),
}


@pytest.mark.parametrize("mutation", sorted(_MUTATIONS))
def test_compare_live_and_stored_agree(mutation: str) -> None:
    old = _base()
    new = _base()
    _MUTATIONS[mutation](new)
    live = compare(old, new)
    stored = compare(
        snapshot_from_dict(json.loads(snapshot_to_json(old))),
        snapshot_from_dict(json.loads(snapshot_to_json(new))),
    )
    assert _verdict_view(live) == _verdict_view(stored)
    assert live.changes, "mutation should produce at least one change"

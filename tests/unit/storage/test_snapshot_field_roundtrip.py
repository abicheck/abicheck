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

Fields the encoder does not persist by design (the ``_``-prefixed lookup
caches and the ``snapshot_encode._SNAPSHOT_SKIP_FIELDS`` owners) are imported
from the encoder rather than hard-coded, and are not enumerated.

Known lossy fields live in :data:`KNOWN_LOSSY` and are ``xfail(strict=True)``:
fixing one makes its test XPASS, which fails the suite until the entry is
removed. :data:`_KNOWN_LOSSY_CEILING` ratchets the table so it may only shrink.

Normalization allowlist (applied to both sides before comparison, and only
for things JSON cannot distinguish): ``tuple``/``list`` are the same sequence,
``set``/``frozenset`` compare as multisets, dict order is ignored, enums
compare by member name, ``Fact`` compares status, value and diagnostics.
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
from abicheck.model import AbiSnapshot, Function, Param
from abicheck.model.availability import FactStatus
from abicheck.model.declaration_store import Declarations
from abicheck.model.dwarf_facts import AdvancedExtractionStatus
from abicheck.model.fact import Fact
from abicheck.model.header_exclusion_record import KNOWN_MATCHING_RULES
from abicheck.model.snapshot_reliability import FACT_FAMILIES
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.snapshot_encode import _SNAPSHOT_SKIP_FIELDS

#: Field id -> reason it does not round-trip today. Each entry is an
#: ``xfail(strict=True)`` real bug; remove it when the codec is fixed.
KNOWN_LOSSY: dict[str, str] = {
    "AbiSnapshot.from_headers_inferred": "dump is not byte-stable: the loaded snapshot re-emits from_headers=false, which the first dump omitted",
    "AbiSnapshot.fact_generations": "dump is not byte-stable: the second dump adds a key (source) absent from the first",
    "AdvancedDwarfMetadata.toolchain": "ToolchainInfo.wchar_flags is dropped on load",
    "ToolchainInfo.wchar_flags": "list of wchar_flags is dropped on load",
    "EnumInfo.decl_file": "decl_file reads back as null",
    "StructLayout.decl_file": "decl_file reads back as null",
    "MachoExport.is_data": "a True is_data reads back as False",
}

#: Ratchet: ``KNOWN_LOSSY`` may only shrink. Lower this when entries are removed.
_KNOWN_LOSSY_CEILING = 7

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
    "Function.ownership_fact": "ownership is persisted by position through the extraction-scope codec (ADR-075), not per entity",
    "RecordType.ownership_fact": "ownership is persisted by position through the extraction-scope codec (ADR-075), not per entity",
    "EnumType.ownership_fact": "ownership is persisted by position through the extraction-scope codec (ADR-075), not per entity",
    "Variable.ownership_fact": "ownership is persisted by position through the extraction-scope codec (ADR-075), not per entity",
    "EntityOwnership.owner": "reachable only through an ownership_fact that is persisted by position",
    "EntityOwnership.contract": "reachable only through an ownership_fact that is persisted by position",
    "EntityOwnership.rule_id": "reachable only through an ownership_fact that is persisted by position",
    "ExtractionScope.ownership_rules": "ownership rules are persisted by the extraction-scope codec (ADR-075)",
    "OwnershipRules.dependency_evidence": "persisted by the extraction-scope codec (ADR-075)",
}

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
            out.append((cls, f.name))
    return sorted(out, key=lambda cf: (cf[0].__qualname__, cf[1]))


def _field_id(cls: type, fname: str) -> str:
    return f"{cls.__qualname__}.{fname}"


def _build_case(cls: type, fname: str) -> tuple[AbiSnapshot, list[_Edge], Any]:
    """Place a leaf ``cls`` with ``fname`` non-default inside an AbiSnapshot."""
    path = _PATHS[cls]
    leaf = _construct(cls, {fname: _nondefault_value(cls, fname)})
    value: Any = leaf
    for parent, fld, chain, _child in reversed(path):
        if parent is AbiSnapshot and fld == _DECLARATIONS:
            snap = _construct(AbiSnapshot, {})
            for df in dataclasses.fields(Declarations):
                setattr(snap.declarations, df.name, getattr(value, df.name))
            return snap, path, leaf
        value = _construct(parent, {fld: _wrap(chain, value)})
    return value, path, leaf


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


def _norm_slot(hint: Any, value: Any) -> Any:
    """Allowlisted: a ``Fact``-typed slot holding ``None`` is ``NOT_COLLECTED``.

    The encoder writes an absent fact as the not-collected value, so the
    loaded side reads back ``Fact(NOT_COLLECTED)`` where the leaf held ``None``.
    """
    if value is None and _is_fact_slot(hint):
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
                (f.name, _norm_slot(hints[f.name], getattr(value, f.name)))
                for f in dataclasses.fields(value)
            ),
        )
    if isinstance(value, AdvancedExtractionStatus):
        # Allowlisted: the encoder drops the advanced-extraction status
        # (encoder _drop_advanced_extraction_status), so NOT_RUN reads back as
        # UNRECORDED (ADR-080). Both sides compare as UNRECORDED.
        return (type(value).__qualname__, AdvancedExtractionStatus.UNRECORDED.name)
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


# --------------------------------------------------------------------------
# Parametrization.
# --------------------------------------------------------------------------


def _collect() -> tuple[list[Any], list[str]]:
    params: list[Any] = []
    unsynth: list[str] = []
    for cls, fname in _enumerated_fields():
        fid = _field_id(cls, fname)
        try:
            _build_case(cls, fname)
        except _Unsynthesizable as exc:
            unsynth.append(f"{fid}: {exc}")
            continue
        marks = ()
        if fid in KNOWN_LOSSY:
            marks = (pytest.mark.xfail(strict=True, reason=KNOWN_LOSSY[fid]),)
        params.append(pytest.param(cls, fname, id=fid, marks=marks))
    return params, unsynth


_PARAMS, _UNSYNTHESIZABLE = _collect()
_ALL_IDS = {_field_id(c, f) for c, f in _enumerated_fields()}


@pytest.mark.parametrize(("cls", "fname"), _PARAMS)
def test_field_survives_dump_load_dump(cls: type, fname: str) -> None:
    snap, path, leaf = _build_case(cls, fname)
    first = snapshot_to_json(snap)
    loaded = snapshot_from_dict(json.loads(first))
    assert snapshot_to_json(loaded) == first, (
        "dump -> load -> dump is not byte-identical"
    )
    got = _navigate(loaded, path)
    assert _norm(getattr(got, fname)) == _norm(getattr(leaf, fname))


def test_every_reachable_field_is_synthesizable() -> None:
    assert not _UNSYNTHESIZABLE, "fields without a synthesizer:\n" + "\n".join(
        _UNSYNTHESIZABLE
    )


def test_known_lossy_is_a_real_field_list_and_only_shrinks() -> None:
    stale = sorted(set(KNOWN_LOSSY) - _ALL_IDS)
    assert not stale, f"KNOWN_LOSSY names no enumerated field: {stale}"
    assert len(KNOWN_LOSSY) == _KNOWN_LOSSY_CEILING, (
        f"KNOWN_LOSSY has {len(KNOWN_LOSSY)} entries; lower _KNOWN_LOSSY_CEILING to match"
    )


def test_field_coverage_is_substantial() -> None:
    # Guards against the walker silently collapsing to a handful of classes.
    assert len(_PARAMS) >= 400, len(_PARAMS)


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

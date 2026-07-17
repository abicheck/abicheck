# Copyright 2026 Nikolay Petrov
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

"""Hybrid castxml+clang snapshot merge (G28 Phase 3, ``--ast-frontend hybrid``).

``dumper.py`` runs BOTH L2 header-AST backends over the same headers and hands
their two independent :class:`~abicheck.model.AbiSnapshot`\\ s to
:func:`merge_snapshots`, which combines them into one snapshot:

- **Ctor/dtor identity reconciliation** (the concrete motivating bug from the
  G28 plan): castxml sometimes cannot recover a real mangled name for a
  constructor/destructor and synthesizes a placeholder snapshot key instead
  (``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX`` / the ``"~ClassName"`` dtor
  form). That placeholder shares no identity with the SAME entity's real
  Itanium-mangled key on the clang side, so comparing a castxml-parsed
  snapshot against a clang-parsed snapshot of unchanged source reports a
  false ``FUNC_REMOVED``+``FUNC_ADDED`` pair for every such constructor/
  destructor (see
  ``tests/test_castxml_clang_parity_gate.py::TestCrossProducerUnmangledIdentityKnownLimitation``).
  This module fixes it by matching a synthetic key against a real clang
  mangled name via structural equivalence (same qualified enclosing class,
  compatible cv-normalized parameter signature for a constructor, same
  access) and rewriting the merged entry's key to the real mangled name.
- **Per-fact backfill**: castxml-only facts (``deprecated``/``is_override``
  on functions, ``deprecated`` on variables, ``is_abstract``/``deprecated``
  on types, ``default``/``deprecated`` on fields, ``is_scoped``/
  ``deprecated`` on enums) are taken from castxml when present, backfilled
  from clang only when castxml's own value is ``None`` — forward-looking
  scaffolding for once the clang backend gains any of these independently;
  a no-op today, since ``dumper_clang.py`` doesn't populate any of them yet.
  Every such fact records its source in the returned snapshot's
  ``fact_provenance`` map (see ``abicheck/fact_provenance.py``), so
  detectors can tell a castxml-backed fact apart from an unbacked one on a
  per-declaration basis instead of trusting a whole-snapshot producer tag.

**Out of scope** (explicitly, per the G28 plan): layout facts (sizes,
offsets, vtable slots, alignment) are NOT merged — castxml remains the sole
layout source; every such field is taken from the castxml snapshot as-is.
Everything not explicitly merged below (typedefs, constants, ELF/PE/Mach-O
metadata, DWARF metadata, ...) is likewise taken verbatim from the castxml
snapshot, which is used as the base via ``dataclasses.replace``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .diff_cxx_rules import itanium_scope_components
from .dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .fact_provenance import (
    enum_fact_key,
    field_fact_key,
    func_fact_key,
    type_fact_key,
    var_fact_key,
)
from .model import AbiSnapshot, EnumType, Function, RecordType, TypeField, Variable
from .name_classification import canonicalize_type_name

_CTOR_MARKER = "{ctor}"
_DTOR_MARKER = "{dtor}"


def _backfill_fact(
    own_value: Any, clang_value: Any, key: str, provenance: dict[str, str]
) -> Any:
    """Return the merged value for one castxml-only fact and record its
    provenance under *key*.

    "Prefer castxml, backfill from clang only when castxml's own value is
    null" (G28 Phase 3 design). Provenance is recorded as ``"castxml"`` even
    when *own_value* is ``None`` and no clang value was available to
    backfill from — the entity itself IS castxml-sourced; a genuinely
    "not deprecated"/"not overridden"/etc. `None` is not the same as "this
    entity was never seen by castxml at all" (the latter simply never calls
    this helper — see :func:`merge_snapshots`'s clang-only-entity handling).
    """
    if own_value is None and clang_value is not None:
        provenance[key] = "clang"
        return clang_value
    provenance[key] = "castxml"
    return own_value


def _ctor_dtor_scope(mangled: str) -> tuple[str, str] | None:
    """``(marker, qualified_scope)`` for a REAL Itanium-mangled ctor/dtor, or
    None if *mangled* isn't one (parsed structurally — see
    ``diff_cxx_rules.itanium_scope_components``, which returns the ctor/dtor
    marker as the last scope component)."""
    comps = itanium_scope_components(mangled)
    if not comps or comps[-1] not in (_CTOR_MARKER, _DTOR_MARKER):
        return None
    return comps[-1], "::".join(comps[:-1])


def _synthetic_ctor_dtor_scope(key: str) -> tuple[str, str, str] | None:
    """``(marker, qualified_scope, param_sig)`` parsed back out of a castxml
    synthetic ctor/dtor key (the exact inverse of
    ``dumper_castxml._CastxmlParser._function_mangled_name``'s synthesis).
    ``param_sig`` is ``""`` for a destructor (never overloaded)."""
    if is_synthetic_ctor_key(key):
        body = key[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
        if "(" not in body or not body.endswith(")"):
            return None
        paren = body.index("(")
        return _CTOR_MARKER, body[:paren], body[paren + 1 : -1]
    if is_synthetic_dtor_key(key):
        return _DTOR_MARKER, key[1:], ""
    return None


def _match_synthetic_ctor_dtor(
    castxml_f: Function,
    clang_ctor_dtor: dict[tuple[str, str], list[Function]],
) -> Function | None:
    """Find the real-mangled clang ``Function`` a castxml synthetic ctor/dtor
    key structurally identifies, or None if no unambiguous match exists.

    A destructor needs only (marker, scope): a class has at most one, so any
    single candidate under that key IS the match. A constructor also
    requires a cv-normalized parameter-type match (there may be several
    overloads sharing the same scope) — matching the plan's explicit caution
    against "a false match between two coincidentally-same-signature but
    genuinely different entities": ambiguity (zero or multiple candidates
    surviving all checks) yields None rather than guessing.
    """
    parsed = _synthetic_ctor_dtor_scope(castxml_f.mangled)
    if parsed is None:
        return None
    marker, scope, param_sig = parsed
    candidates = clang_ctor_dtor.get((marker, scope), [])
    if marker == _DTOR_MARKER:
        if len(candidates) == 1 and candidates[0].access == castxml_f.access:
            return candidates[0]
        return None
    # Constructor: narrow by cv-normalized signature, same as the synthetic
    # key's own identity (dumper_castxml._ctor_param_identity_type already
    # strips a top-level cv qualifier the same way real mangling would).
    wanted_sig = tuple(
        canonicalize_type_name(t) for t in (param_sig.split(",") if param_sig else [])
    )
    matches = [
        c
        for c in candidates
        if c.access == castxml_f.access
        and tuple(canonicalize_type_name(p.type) for p in c.params) == wanted_sig
    ]
    return matches[0] if len(matches) == 1 else None


def _backfill_function_facts(
    f: Function, clang_f: Function | None, provenance: dict[str, str]
) -> Function:
    updates: dict[str, Any] = {}
    for attr in ("deprecated", "is_override"):
        key = func_fact_key(f.mangled, attr)
        value = _backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    return replace(f, **updates) if updates else f


def _merge_functions(
    castxml_funcs: list[Function],
    clang_funcs: list[Function],
    provenance: dict[str, str],
) -> list[Function]:
    clang_ctor_dtor: dict[tuple[str, str], list[Function]] = {}
    for cf in clang_funcs:
        scope = _ctor_dtor_scope(cf.mangled)
        if scope is not None:
            clang_ctor_dtor.setdefault(scope, []).append(cf)

    merged: list[Function] = []
    for f in castxml_funcs:
        if is_synthetic_ctor_key(f.mangled) or is_synthetic_dtor_key(f.mangled):
            match = _match_synthetic_ctor_dtor(f, clang_ctor_dtor)
            if match is not None:
                f = replace(f, mangled=match.mangled)
        merged.append(f)

    clang_by_mangled = {cf.mangled: cf for cf in clang_funcs}
    merged = [
        _backfill_function_facts(f, clang_by_mangled.get(f.mangled), provenance)
        for f in merged
    ]

    merged_mangled = {f.mangled for f in merged}
    merged.extend(cf for cf in clang_funcs if cf.mangled not in merged_mangled)
    return merged


def _merge_variable(
    v: Variable, clang_v: Variable | None, provenance: dict[str, str]
) -> Variable:
    key = var_fact_key(v.mangled, "deprecated")
    value = _backfill_fact(
        v.deprecated, clang_v.deprecated if clang_v else None, key, provenance
    )
    return replace(v, deprecated=value) if value != v.deprecated else v


def _merge_field(
    type_name: str,
    f: TypeField,
    clang_f: TypeField | None,
    provenance: dict[str, str],
) -> TypeField:
    updates: dict[str, Any] = {}
    for attr in ("default", "deprecated"):
        key = field_fact_key(type_name, f.name, attr)
        value = _backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    return replace(f, **updates) if updates else f


def _merge_record_type(
    t: RecordType, clang_t: RecordType | None, provenance: dict[str, str]
) -> RecordType:
    updates: dict[str, Any] = {}
    for attr in ("is_abstract", "deprecated"):
        key = type_fact_key(t.name, attr)
        value = _backfill_fact(
            getattr(t, attr), getattr(clang_t, attr, None), key, provenance
        )
        if value != getattr(t, attr):
            updates[attr] = value

    clang_fields_by_name = {cf.name: cf for cf in clang_t.fields} if clang_t else {}
    merged_fields = [
        _merge_field(t.name, f, clang_fields_by_name.get(f.name), provenance)
        for f in t.fields
    ]
    if merged_fields != t.fields:
        updates["fields"] = merged_fields

    return replace(t, **updates) if updates else t


def _merge_enum_type(
    e: EnumType, clang_e: EnumType | None, provenance: dict[str, str]
) -> EnumType:
    updates: dict[str, Any] = {}
    for attr in ("is_scoped", "deprecated"):
        key = enum_fact_key(e.name, attr)
        value = _backfill_fact(
            getattr(e, attr), getattr(clang_e, attr, None), key, provenance
        )
        if value != getattr(e, attr):
            updates[attr] = value
    return replace(e, **updates) if updates else e


def merge_snapshots(castxml_snap: AbiSnapshot, clang_snap: AbiSnapshot) -> AbiSnapshot:
    """Merge a castxml-produced and a clang-produced snapshot of the SAME
    headers into one hybrid :class:`AbiSnapshot`.

    castxml remains the base (layout facts, ELF/PE/Mach-O metadata, typedefs,
    constants, and everything not explicitly merged here all come from it
    verbatim) — only the facts documented in this module's docstring are
    actually reconciled/backfilled. The result's ``ast_producer`` is
    ``"hybrid"`` and its ``fact_provenance`` records, per declaration, which
    backend's value was used for each of those facts.

    If *castxml_snap* itself never got confirmed header-AST evidence (no
    headers were supplied, or the dump ran ``dwarf_only``/``symbols_only``),
    both recursive sub-dumps are DWARF/symbols-only snapshots with nothing
    header-derived to merge — returns *castxml_snap* unchanged rather than
    falsely upgrading it to ``ast_producer="hybrid"``/confirmed
    header-aware provenance, which would make header-tier detectors (param
    defaults, constants, param renames) misread a real header-aware snapshot
    compared against this one as having lost data (Codex review).
    """
    if not castxml_snap.from_headers:
        return castxml_snap

    provenance: dict[str, str] = {}

    clang_types_by_name = {t.name: t for t in clang_snap.types}
    clang_enums_by_name = {e.name: e for e in clang_snap.enums}
    clang_vars_by_mangled = {v.mangled: v for v in clang_snap.variables}

    merged_functions = _merge_functions(
        castxml_snap.functions, clang_snap.functions, provenance
    )

    merged_types = [
        _merge_record_type(t, clang_types_by_name.get(t.name), provenance)
        for t in castxml_snap.types
    ]
    castxml_type_names = {t.name for t in castxml_snap.types}
    merged_types.extend(t for t in clang_snap.types if t.name not in castxml_type_names)

    merged_enums = [
        _merge_enum_type(e, clang_enums_by_name.get(e.name), provenance)
        for e in castxml_snap.enums
    ]
    castxml_enum_names = {e.name for e in castxml_snap.enums}
    merged_enums.extend(e for e in clang_snap.enums if e.name not in castxml_enum_names)

    merged_variables = [
        _merge_variable(v, clang_vars_by_mangled.get(v.mangled), provenance)
        for v in castxml_snap.variables
    ]
    castxml_var_mangled = {v.mangled for v in castxml_snap.variables}
    merged_variables.extend(
        v for v in clang_snap.variables if v.mangled not in castxml_var_mangled
    )

    return replace(
        castxml_snap,
        functions=merged_functions,
        variables=merged_variables,
        types=merged_types,
        enums=merged_enums,
        ast_producer="hybrid",
        fact_provenance=provenance,
        # from_headers/from_headers_inferred are inherited from castxml_snap
        # as-is via replace() (both already True/False here — the early
        # return above handles the case where they aren't).
        # Invalidate the lazy lookup caches (dataclasses.replace() otherwise
        # carries the OLD castxml-only indexes forward unchanged, since these
        # are ordinary fields with defaults, not something replace() knows to
        # reset just because functions/variables/types changed).
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )


def run_hybrid_dump(
    dump_fn: Callable[..., AbiSnapshot],
    so_path: Path,
    headers: list[Path],
    **kwargs: Any,
) -> AbiSnapshot:
    """Run *dump_fn* (``dumper.dump``) once per real backend and merge.

    Takes *dump_fn* as a parameter, rather than importing ``dumper.dump``
    directly, so this module never depends on ``dumper.py`` (which already
    depends on this one) — avoiding an import cycle without needing a
    deferred/local import on either side. Every keyword argument is forwarded
    to both sub-dumps unchanged except ``header_backend``, which this
    function sets explicitly on each call; reuses every format handler,
    ELF/PE/Mach-O metadata attachment, and provenance tagging in *dump_fn*
    completely unchanged for both sub-dumps — only the merge step
    (:func:`merge_snapshots`) is new.
    """
    castxml_snap = dump_fn(so_path, headers, header_backend="castxml", **kwargs)
    clang_snap = dump_fn(so_path, headers, header_backend="clang", **kwargs)
    return merge_snapshots(castxml_snap, clang_snap)

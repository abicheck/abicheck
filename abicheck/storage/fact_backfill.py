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

"""Legacy-load corrections for case-(a) ``Fact[T]`` fields (ADR-063 Phase 5).

Split out of ``fact_codec.py`` (which re-exports everything here unchanged,
so every existing ``from .fact_codec import apply_legacy_fact_backfill``
call site is unaffected) once that module crossed ADR-061's 800-line
production ceiling. The two concerns are genuinely different: ``fact_codec``
encodes and decodes what a document *says*, while this module answers the
narrower question a case-(a) field forces -- whether a document that
predates that field's own ``Fact[T]`` conversion may be believed at all,
given the snapshot-level ``*_facts_reliable`` flag guarding it.

Depends only on ``model`` and on ``fact_schema_versions.py`` -- the leaf
module holding the per-field thresholds both this module and ``fact_codec``
need. Importing them back from ``fact_codec`` instead would be a real import
cycle (``fact_codec`` re-exports this module's three public names for
compatibility), which the ``import-cycle-growth`` gate rejects.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..model import AccessLevel, Fact
from ..model.fact_registry import FACT_REGISTRY
from .fact_schema_versions import (
    _FACT_FIELDS_SCHEMA_VERSION,
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
)

if TYPE_CHECKING:
    from ..model import Function, RecordType

__all__ = [
    "CaseAFactRule",
    "apply_case_a_fact_backfill",
    "apply_legacy_fact_backfill",
]


@dataclass(frozen=True)
class CaseAFactRule:
    """One case-(a) field's legacy-load correction (ADR-063 Phase 5).

    ``owner``/``field`` name the legacy field; ``min_schema_version`` is the
    schema_version that field's own ``<field>_fact`` sibling started being
    persisted at; ``reliable`` is this snapshot's already-resolved answer to
    "is the flag guarding this field's availability trustworthy here"
    (``serialization.py`` computes every ``*_facts_reliable`` value, folding
    in any producer gate — see ``apply_legacy_fact_backfill``'s own
    ``ast_producer`` note); ``normalized_default`` is the value the legacy
    field is reset to when the fact is downgraded, so the pair cannot be
    left holding a placeholder beside a NOT_COLLECTED status.
    """

    owner: str
    field: str
    min_schema_version: int
    reliable: bool
    normalized_default: Any


def _owner_pairs(
    d: dict[str, Any],
    owner: str,
    decoded: dict[str, list[Any]],
) -> Iterator[tuple[dict[str, Any], Any]]:
    """Every ``(raw dict, decoded object)`` pair for one *owner* dataclass.

    The one place this module knows how a given owner's instances are
    reached from the raw snapshot document — two owners (``TypeField``,
    ``Param``) live one level below a collection rather than in one, and a
    per-field ``zip`` open-coded at each call site is exactly the kind of
    duplication a later owner's conversion would get subtly wrong.
    """
    if owner == "RecordType":
        yield from zip(d.get("types", []), decoded.get("types", []), strict=False)
    elif owner == "EnumType":
        yield from zip(d.get("enums", []), decoded.get("enums", []), strict=False)
    elif owner == "Variable":
        yield from zip(
            d.get("variables", []), decoded.get("variables", []), strict=False
        )
    elif owner == "Function":
        yield from zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        )
    elif owner == "TypeField":
        for type_dict, record in zip(
            d.get("types", []), decoded.get("types", []), strict=False
        ):
            yield from zip(type_dict.get("fields", []), record.fields, strict=False)
    elif owner == "Param":
        for func_dict, func in zip(
            d.get("functions", []), decoded.get("functions", []), strict=False
        ):
            yield from zip(func_dict.get("params", []), func.params, strict=False)
    else:  # pragma: no cover - guarded by the caller's own closed rule set
        raise ValueError(f"no raw-document navigation known for owner {owner!r}")


#: The two header-AST producers. A fact whose registered
#: ``producing_backends`` name only these cannot have been observed by a
#: run that never parsed a header, however trustworthy its reliability flag
#: reads -- see :func:`_needs_header_ast`.
_HEADER_AST_BACKENDS: frozenset[str] = frozenset({"castxml", "clang"})


def _needs_header_ast(owner: str, field: str) -> bool:
    """Whether only a header-AST parse can produce this fact.

    Answered from the fact's own ``FACT_REGISTRY`` entry rather than from a
    second hand-maintained list -- the registry ADR-063 Phase 5 exists to
    build is the one place a fact's producers are declared, so a future
    change to them reaches this decision automatically. An unregistered
    field answers ``False``: this correction only ever *narrows* a claim,
    so an unknown fact keeps the pre-existing behaviour rather than being
    silently downgraded on a guess.
    """
    entry = FACT_REGISTRY.get(f"{owner}.{field}")
    if entry is None:
        return False
    return set(entry.producing_backends) <= _HEADER_AST_BACKENDS


def apply_case_a_fact_backfill(
    d: dict[str, Any],
    *,
    schema_version: int,
    rules: tuple[CaseAFactRule, ...],
    header_provenance_confirmed: bool = True,
    **decoded: list[Any],
) -> None:
    """Downgrade every case-(a) fact a legacy document cannot vouch for.

    A document below a field's own ``min_schema_version`` carries no
    ``<field>_fact`` key at all, so the owning dataclass's ``__post_init__``
    bridge already backfilled ``Fact.present(raw_value)`` — correct when the
    snapshot-level reliability flag guarding that field says this producer's
    values are trustworthy, and exactly the "placeholder read as a confirmed
    fact" bug ``Fact[T]`` exists to prevent when it doesn't. This is the one
    correction for that whole class: :func:`apply_legacy_fact_backfill` (the
    three fields ADR-063 Phase 0 converted) is a thin wrapper over it, and
    every case-(a) field a later batch converts adds a rule rather than
    another hand-written loop.

    Only ever *downgrades*, and only for a document that predates the
    field's own conversion: a v(N)+ document's ``<field>_fact`` was decoded
    explicitly at construction time and is authoritative.
    ``header_provenance_confirmed`` carries the second downgrade reason (see
    the body): a producer that never parsed a header cannot have observed a
    header-AST-only fact, which no reliability flag expresses.
    """
    for rule in rules:
        if schema_version >= rule.min_schema_version:
            continue
        # Two independent reasons a pre-conversion document's value is not
        # evidence, and the second is not covered by the first (Codex
        # review, PR #993): every ``*_facts_reliable`` flag resolves True
        # for a snapshot whose producer never parsed a header, since the
        # describes never ran -- "trusted by irrelevance". That is the right
        # answer to "is this value a wrong placeholder", and the wrong
        # answer to "did anyone observe it": a legacy DWARF/PDB/symbols-only
        # document's `deprecated: null` / `is_restrict: false` /
        # `access: "public"` would otherwise bridge to PRESENT, claiming a
        # confirmed fact the fresh equivalent of that same snapshot reports
        # as NOT_COLLECTED.
        #
        # The caller passes header provenance that is *recorded*, never
        # inferred (Codex review, second round): a document predating the
        # `from_headers` key has it guessed from "does this snapshot carry
        # declarations at all", which a legacy DWARF-only dump satisfies
        # exactly as a header dump does -- `serialization.py` marks that
        # guess with `from_headers_inferred` for precisely this reason. An
        # inferred True is UNKNOWN provenance, and unknown fails closed
        # here, the same way an absent `ast_producer` is read as "possibly
        # clang-family" rather than silently trusted.
        unreliable = not rule.reliable
        unproduceable = not header_provenance_confirmed and _needs_header_ast(
            rule.owner, rule.field
        )
        if not (unreliable or unproduceable):
            continue
        fact_key = f"{rule.field}_fact"
        for raw, obj in _owner_pairs(d, rule.owner, decoded):
            if fact_key in raw:
                continue
            if unreliable:
                setattr(obj, rule.field, rule.normalized_default)
                setattr(obj, fact_key, Fact.not_collected())
            elif getattr(obj, rule.field) == rule.normalized_default:
                # Unproduceable-only: downgrade the *claim*, never the
                # value. A non-header document carrying a non-resting value
                # for one of these fields got it from somewhere this
                # registry doesn't model, and discarding it would lose real
                # data -- unlike the unreliable case, where the value is
                # known to be a placeholder.
                setattr(obj, fact_key, Fact.not_collected())


def apply_legacy_fact_backfill(
    d: dict[str, Any],
    types: list[RecordType],
    funcs: list[Function],
    schema_version: int,
    clang_vtable_facts_reliable_value: bool,
    clang_va_list_facts_reliable_value: bool,
    ast_producer_value: str | None,
    *,
    header_provenance_confirmed: bool = True,
    variables: list[Any] | None = None,
    enums: list[Any] | None = None,
    header_cv_facts_reliable_value: bool = True,
    clang_restrict_facts_reliable_value: bool = True,
    castxml_var_access_facts_reliable_value: bool = True,
    clang_field_initializer_facts_reliable_value: bool = True,
    clang_deprecation_facts_reliable_value: bool = True,
) -> None:
    """Correct the legacy backfill for every case-(a) fact a document predates.

    A pre-v26 snapshot carries no ``vtable_fact``/``vptr_offset_bits_fact``/
    ``is_va_list_fact`` keys at all, so each ``RecordType``/``Param``'s own
    ``__post_init__`` bridge already backfilled these to
    ``Fact.present(raw_value)`` unconditionally (there is no sentinel to
    distinguish "legacy, key absent" from "legacy, key present" here — both
    look like an ordinary explicit value to that bridge). That is correct
    for ``bases``/``virtual_bases`` (no independent reliability signal —
    see AGENTS.md's ``type_base_changed`` entry), but wrong for
    ``vtable``/``vptr_offset_bits``/``is_va_list`` when the *existing*
    reliability flags say this producer's own facts for this snapshot are
    untrustworthy: ``Fact.present(raw)`` would misread a placeholder value
    as a confirmed fact, exactly the bug this phase exists to make
    unrepresentable. Only runs for a legacy (pre-v26) load — a fresh v26+
    snapshot's ``*_fact`` keys were decoded explicitly at construction time
    via :func:`decode_fact` and must not be overridden here.

    Phase 5's own case-(a) batches extend the same correction to the fields
    they convert, each with its own ``min_schema_version`` and its own
    guarding flag (``header_cv_facts_reliable_value`` for ``TypeField``'s
    CV facts, schema v39) — one rule added to the tuple below, never a
    second hand-written loop. The keyword-only spelling keeps every
    pre-existing caller (and every test constructing this call) unchanged:
    a flag left at its default ``True`` states "trustworthy", which is what
    a caller that never heard of that field was already asserting by not
    correcting it at all.

    ``is_va_list`` needs an extra gate ``vtable``/``vptr_offset_bits`` don't
    (Codex review, fresh evidence): CastXML never determines va_list-ness at
    all — its own ``is_va_list`` is always a blanket ``False`` placeholder,
    not a computed fact the way CastXML's vtable *is* one (see
    ``clang_vtable_facts_reliable_value``'s own computation in
    ``serialization.py``: "a castxml... snapshot's own vtable extraction
    predates this field entirely, so it's always reliable"). But
    ``clang_va_list_facts_reliable_value`` reads ``True`` for a CastXML
    snapshot too, since that flag's actual meaning is "safe to trust
    `False` as not-wrong" (CastXML never reports a real va_list parameter
    as anything but `False`, so the polarity is never wrong) — a different
    question from "was this fact actually collected". Reusing that flag
    alone would silently turn "never observed" into "confirmed not
    va_list" on every legacy CastXML load. Gated here on
    ``ast_producer_value == "clang"`` in addition to the reliability flag,
    so only an actual clang-family load can reach ``Fact.present(...)``.
    """
    apply_case_a_fact_backfill(
        d,
        schema_version=schema_version,
        rules=(
            CaseAFactRule(
                "RecordType",
                "vtable",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                [],
            ),
            CaseAFactRule(
                "RecordType",
                "vptr_offset_bits",
                _FACT_FIELDS_SCHEMA_VERSION,
                clang_vtable_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "Param",
                "is_va_list",
                _FACT_FIELDS_SCHEMA_VERSION,
                ast_producer_value == "clang" and clang_va_list_facts_reliable_value,
                False,
            ),
            # ADR-063 Phase 5 (eighth batch, schema v39): TypeField's own CV
            # facts. A pre-v39 document carries no is_const_fact/
            # is_volatile_fact/is_mutable_fact key, so its blanket False
            # values were bridged to Fact.present(False);
            # header_cv_facts_reliable is exactly the signal saying whether
            # that reading is a real fact or a pre-fix castxml placeholder.
            CaseAFactRule(
                "TypeField",
                "is_const",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_volatile",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "TypeField",
                "is_mutable",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
                header_cv_facts_reliable_value,
                False,
            ),
            # TypeField's other two case-(a) fields, each with its own
            # guarding flag: a pre-v39 clang document's blanket `None`
            # default-initializer/deprecation is the same placeholder shape
            # the CV facts have, and the same two flags the detectors
            # already consult say so.
            CaseAFactRule(
                "TypeField",
                "default",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_field_initializer_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "TypeField",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            # ADR-063 Phase 5 (ninth batch, schema v40): the other four
            # `deprecated` surfaces plus EnumType.is_scoped, all guarded by
            # the same flag TypeField.deprecated is -- one rule each, which
            # is the whole point of the rule table.
            CaseAFactRule(
                "Function",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "Variable",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "RecordType",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "EnumType",
                "deprecated",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            CaseAFactRule(
                "EnumType",
                "is_scoped",
                _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
                clang_deprecation_facts_reliable_value,
                None,
            ),
            # ADR-063 Phase 5 (tenth batch, schema v41): the last two
            # case-(a) fields, each with its own guarding flag.
            CaseAFactRule(
                "Param",
                "is_restrict",
                _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
                clang_restrict_facts_reliable_value,
                False,
            ),
            CaseAFactRule(
                "Variable",
                "access",
                _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS,
                castxml_var_access_facts_reliable_value,
                AccessLevel.PUBLIC,
            ),
        ),
        header_provenance_confirmed=header_provenance_confirmed,
        types=types,
        functions=funcs,
        variables=variables or [],
        enums=enums or [],
    )

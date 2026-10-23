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

"""ADR-085 D2: a header-level change to a class template's default argument.

A class-template pattern carries its parameters' spelled defaults only when
the header backend recorded them (``RecordType.template_param_defaults_fact``,
ADR-085 D1). This compares those recorded defaults between two snapshots. It
reads only PRESENT facts, so an UNKNOWN side never pairs with a recorded one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection

from ..diff_helpers import make_change
from ..model import (
    AbiSnapshot,
    Function,
    RecordType,
    source_contract_evidence as _contract,
)
from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.evidence_status import ReachabilityState
from ..model.identity import EntityId, InlineNamespace, Namespace, Record
from ..model.signature_token_scan import _split_top_level_commas
from .fact_gate import both_facts_present
from .template_surface import (
    qualified_name_lookup,
    reconciled_abi_visible_functions,
    strip_template_args as _callable_stem,
)


def _pattern_records(snap: AbiSnapshot) -> dict[str, RecordType]:
    """``qualified template name -> class-template pattern record`` for *snap*.

    A qualified name that more than one pattern carries is ambiguous and is
    dropped, not trusted.
    """
    seen: dict[str, RecordType] = {}
    ambiguous: set[str] = set()
    for rec in snap.declarations.types:
        if not rec.is_template_pattern:
            continue
        key = rec.qualified_name or rec.name
        if key in seen or key in ambiguous:
            ambiguous.add(key)
            continue
        seen[key] = rec
    for key in ambiguous:
        seen.pop(key, None)
    return seen


def detect_template_param_default_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    instantiation_stems: Collection[str] = (),
    reported_templates: Collection[str] = (),
) -> tuple[list[Change], list[Change]]:
    """ADR-085 D2: a header-level change to a class template's default argument.

    Pairs the class-template patterns of *old* and *new* by qualified name.
    A pair fires only when both sides carry PRESENT recorded defaults (D1),
    the parameter count is the same, and at least one parameter's default
    spelling differs. A changed default that an exported instantiation already
    reports as ``DEFAULT_TEMPLATE_ARG_CHANGED`` (BREAKING, shape A) is not a
    second finding: it is returned in the second list instead, so the caller
    can record it as suppressed. *instantiation_stems* are the callable stems
    of those ``DEFAULT_TEMPLATE_ARG_CHANGED`` findings. *reported_templates* are
    the template keys a function-level finding already names through its
    signature (shape C: ``take(const Box<char>&)``); they are suppressed too.

    Returns ``(findings, suppressed)``.
    """
    old_patterns = _pattern_records(old)
    new_patterns = _pattern_records(new)
    findings: list[Change] = []
    suppressed: list[Change] = []
    for key in sorted(old_patterns.keys() & new_patterns.keys()):
        old_rec, new_rec = old_patterns[key], new_patterns[key]
        if old_rec.template_param_defaults_fact is None:
            continue
        if new_rec.template_param_defaults_fact is None:
            continue
        if not both_facts_present(old_rec, new_rec, "template_param_defaults", key):
            continue
        before = old_rec.template_param_defaults_fact.value
        after = new_rec.template_param_defaults_fact.value
        if before is None or after is None:
            continue
        if len(before) != len(after) or before == after:
            continue
        changed = [
            i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
        ]
        change = make_change(
            ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED,
            symbol=key,
            name=key,
            detail=", ".join(f"param {i}" for i in changed),
            old_value=repr(before),
            new_value=repr(after),
            **_contract.reachability_of(new_rec, header_parsed=True),
        )
        covered = key in reported_templates or any(
            _is_in_scope(stem, key) for stem in instantiation_stems
        )
        (suppressed if covered else findings).append(change)
    return findings, suppressed


def _function_pattern_groups(snap: AbiSnapshot) -> dict[EntityId, list[Function]]:
    """``entity_id -> the function-template pattern declarations of *snap*``
    (ADR-085 D1, function slice).

    A header that declares a function template and then defines it gives two
    pattern declarations with one identity, so the group keeps all of them and
    :func:`_merged_function_defaults` decides what they agree on. A declaration
    whose fact is NOT_COLLECTED is an ordinary function that never had a
    template pattern to record, so it is not grouped.
    """
    groups: dict[EntityId, list[Function]] = {}
    for fn in snap.declarations.functions:
        fact = fn.template_param_defaults_fact
        if (
            fact is None
            or fact.status is FactStatus.NOT_COLLECTED
            or fn.entity_id is None
        ):
            continue
        groups.setdefault(fn.entity_id, []).append(fn)
    return groups


def _merged_function_defaults(
    decls: list[Function],
) -> tuple[str | None, ...] | None:
    """The one default per parameter that a group of redeclarations agrees on.

    C++ allows a default argument to be stated once, on whichever redeclaration
    introduces it, so a parameter's spelling is taken from any declaration that
    spells it. Two different spellings for one parameter, a differing parameter
    count, or any declaration whose fact is not PRESENT makes the group unusable
    (``None``): the identity is not trusted, so no finding is produced for it.
    """
    rows: list[tuple[str | None, ...]] = []
    for fn in decls:
        fact = fn.template_param_defaults_fact
        if fact is None or fact.status is not FactStatus.PRESENT or fact.value is None:
            return None
        rows.append(fact.value)
    if len({len(row) for row in rows}) != 1:
        return None
    merged: list[str | None] = []
    for column in zip(*rows, strict=True):
        spelled = {spelling for spelling in column if spelling is not None}
        if len(spelled) > 1:
            return None
        merged.append(spelled.pop() if spelled else None)
    return tuple(merged)


def detect_function_template_param_default_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    instantiation_stems: Collection[str] = (),
) -> tuple[list[Change], list[Change]]:
    """ADR-085 D2 for a function template: a header-level change to a
    function template's default argument.

    Pairs the function-template patterns of *old* and *new* by their entity
    identity, which carries each template parameter's kind, so two overloads
    that share a name are never paired with each other. A pair fires only when
    both sides carry recorded defaults (D1), the parameter count is the same,
    and at least one default spelling differs. A change already reported as
    ``DEFAULT_TEMPLATE_ARG_CHANGED`` (shape A, an exported instantiation of the
    same template, named by *instantiation_stems*) is returned in the second
    list instead, so the caller can record it as deduplicated.

    Returns ``(findings, suppressed)``.
    """
    old_groups = _function_pattern_groups(old)
    new_groups = _function_pattern_groups(new)
    findings: list[Change] = []
    suppressed: list[Change] = []
    for key in sorted(old_groups.keys() & new_groups.keys(), key=repr):
        old_decls, new_decls = old_groups[key], new_groups[key]
        # The identity carries the namespace/record path and the leaf name, so
        # the qualified name needs no demangled spelling (a pattern has none).
        scope_names = [
            segment.name
            for segment in key.scope
            if isinstance(segment, (Namespace, Record, InlineNamespace))
        ]
        qname = "::".join([*scope_names, key.leaf_name])
        if not both_facts_present(
            old_decls[0], new_decls[0], "template_param_defaults", qname
        ):
            continue
        before = _merged_function_defaults(old_decls)
        after = _merged_function_defaults(new_decls)
        if (
            before is None
            or after is None
            or len(before) != len(after)
            or before == after
        ):
            continue
        changed = [
            i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
        ]
        new_fn = new_decls[0]
        symbol = f"{qname}({', '.join(param.type for param in new_fn.params)})"
        change = make_change(
            ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED,
            symbol=symbol,
            name=qname,
            detail=", ".join(f"param {i}" for i in changed),
            old_value=repr(before),
            new_value=repr(after),
            **_contract.reachability_of(new_fn, header_parsed=True),
        )
        covered = any(_is_in_scope(stem, qname) for stem in instantiation_stems)
        (suppressed if covered else findings).append(change)
    return findings, suppressed


def _stable_leading_template_args(old_args: str, new_args: str) -> bool:
    """Return ``True`` iff *old_args* and *new_args* differ ONLY in
    trailing positions — i.e. some non-empty leading prefix is identical.

    Used to gate ``DEFAULT_TEMPLATE_ARG_CHANGED`` so we don't flag a
    pairing like ``Foo<float, A>::compute`` → ``Foo<double, B>::compute``
    where every position changed (that's an explicit instantiation
    change, not a default-arg change). For a default-arg change, C++
    requires defaults to live in trailing positions, so the leading
    args supplied by the user must remain identical between old and
    new substituted forms.

    Single-arg templates are accepted as a degenerate case (no leading
    args to compare; the only arg IS the trailing position).

    >>> _stable_leading_template_args("float, A", "float, B")
    True
    >>> _stable_leading_template_args("float, A", "double, B")
    False
    >>> _stable_leading_template_args("A", "B")  # 1-arg degenerate case
    True
    """
    # An empty argument list has nothing to compare. Checked up front, not left
    # to the splitter, which yields one empty part for "" (the legacy splitter
    # yielded none, and this keeps that answer).
    if not old_args.strip() or not new_args.strip():
        return False
    old_list = [p.strip() for p in _split_top_level_commas(old_args)]
    new_list = [p.strip() for p in _split_top_level_commas(new_args)]
    # Degenerate single-arg template: nothing leading to compare.
    if len(old_list) == 1 and len(new_list) == 1:
        return True
    # Same length, identical leading prefix of size >= 1.
    if len(old_list) != len(new_list):
        return False
    diff_idx = next(
        (i for i in range(len(old_list)) if old_list[i] != new_list[i]),
        len(old_list),
    )
    return 1 <= diff_idx < len(old_list)


def _extract_template_args(demangled: str) -> str | None:
    """Return the substring inside the outermost (balanced) ``<...>`` of
    *demangled*.

    Handles all common shapes:

    * ``ns::function<float>``                       → ``float``
    * ``ns::function<float>(int)``                  → ``float``
    * ``ns::Class<float, A>::method``               → ``float, A``
    * ``Outer<X<int>>::Inner<Y>``                   → ``Y`` (innermost class)
    """
    head = demangled.split("(", 1)[0]
    # Walk right-to-left to find the rightmost balanced ``<...>`` group;
    # we want the template args of the leaf entity (method or function).
    last_open = -1
    depth = 0
    end = -1
    for i in range(len(head) - 1, -1, -1):
        ch = head[i]
        if ch == ">":
            if depth == 0:
                end = i
            depth += 1
        elif ch == "<":
            depth -= 1
            if depth == 0:
                last_open = i
                break
    if last_open < 0 or end < 0:
        return None
    return head[last_open + 1 : end]


def detect_default_template_arg_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Pair a removed instantiation with an added instantiation that
    differ only in their default-substituted template arguments.

    Heuristic: same unqualified function name, both demangled names
    show template args ``<...>``, and the args differ. Without a fully
    parsed template signature this can produce false positives if the
    user changed an instantiation explicitly — to control that, we only
    emit the finding when the args differ but the *prefix* (everything
    up to the differing arg) matches and the function unqualified name
    matches one-for-one.

    Uses :func:`~abicheck.compare.template_surface.qualified_name_lookup` (demangles when ``Function.name``
    itself carries no template args) rather than the raw ``name`` field: the
    header/castxml backend populates ``.name`` from the bare AST method name
    only, so a real templated method name (e.g. ``dimension``) never shows
    ``<...>`` there, and this check could otherwise never match (case87).

    Scoped to the public surface (``PUBLIC`` + ``ELF_ONLY``, same set
    ``diff_symbols.py`` uses): a snapshot can retain ``HIDDEN`` functions for
    cross-reference (both the header/castxml backend and, since the case06
    fix, the DWARF-only backend), and a purely-internal template
    instantiation changing is not a real ABI event — including it here could
    emit a DEFAULT_TEMPLATE_ARG_CHANGED finding for a helper no consumer can
    ever see.
    """

    old.index()
    new.index()
    old_funcs, new_funcs = reconciled_abi_visible_functions(old, new)
    new_mangled = {f.mangled for f in new_funcs}
    removed = [f for f in old_funcs if f.mangled not in new_mangled]
    # Key by *qualified* callable stem (full namespace path with all
    # template args stripped). This prevents matching ``ns1::foo::compute``
    # against ``ns2::bar::compute`` and other namespace-confusion false
    # positives — only different instantiations of the SAME callable get
    # paired.
    added_by_entity: dict[str, list[Function]] = defaultdict(list)
    old_name_of = qualified_name_lookup(old)
    new_name_of = qualified_name_lookup(new)
    for fn in new_funcs:
        qname = new_name_of(fn)
        added_by_entity[_callable_stem(qname)].append(fn)
    findings: list[Change] = []
    seen_pairs: set[tuple[str, str]] = set()
    for fn in removed:
        old_qname = old_name_of(fn)
        old_args = _extract_template_args(old_qname)
        if old_args is None:
            continue
        entity = _callable_stem(old_qname)
        for cand in added_by_entity.get(entity, []):
            new_qname = new_name_of(cand)
            new_args = _extract_template_args(new_qname)
            if new_args is None or new_args == old_args:
                continue
            # Require that the two argument lists differ only in trailing
            # positions — i.e. there is a non-empty leading prefix that
            # matches. A "default template argument changed" can only
            # affect positions the user didn't supply explicitly, which
            # are by C++ rule the trailing ones; an explicit change to a
            # LEADING arg is something else (and should not be flagged
            # here, since it's not subsumed by a default change).
            if not _stable_leading_template_args(old_args, new_args):
                continue
            key = (fn.mangled, cand.mangled)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            findings.append(
                make_change(
                    ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED,
                    symbol=fn.mangled,
                    name=fn.name,
                    detail=cand.name,
                    old_value=old_args,
                    new_value=new_args,
                    # ADR-044: old_funcs/new_funcs are scoped to _PUBLIC_VIS
                    # above, so both the removed and added instantiation
                    # symbols are reliably public.
                    public_reachable=True,
                    reachability_state=ReachabilityState.PROVEN_REACHABLE,
                    reachability_kind="direct_public_symbol",
                )
            )
            break  # one pairing per removed symbol
    return findings


def _is_in_scope(stem: str, key: str) -> bool:
    """Whether the callable *stem* is *key* itself or a member of it.

    Compared segment by segment on ``::`` boundaries, so ``ns::Box`` does not
    match ``ns::BoxExtra``.
    """
    stem_parts = stem.split("::")
    key_parts = key.split("::")
    return stem_parts[: len(key_parts)] == key_parts

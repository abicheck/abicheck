# SPDX-License-Identifier: Apache-2.0
"""ADR-085 D2 over the template-default table: every template family.

``AbiSnapshot.cxx_template_defaults`` (``model/template_default_table.py``)
holds the public headers' template-parameter defaults, read from the clang AST
by both header backends. It is the only record of them for a castxml run
(castxml 0.7 emits no element for an uninstantiated template) and for an
alias or variable template on either backend (neither has a model entity).

A template present on both sides with known defaults whose spellings differ is
one ``TEMPLATE_PARAM_DEFAULT_CHANGED`` (``API_BREAK``): code written against
the old header silently gets another type or value, or stops compiling when a
default is removed. A default only *added* to a class, alias or variable
template is not reported: every spelling valid before had to name that
argument and keeps its meaning. A function template's added default is
reported, as by the pattern-fact detector: it makes the template viable for a
call that omits the argument.

A class or function template that both snapshots also carry as a pattern with
a defaults fact (the clang backend's records, ``compare/template_param_defaults``)
is left to that detector, so one change is reported once.
"""

from __future__ import annotations

from collections.abc import Collection

from ..diff_helpers import make_change
from ..model import source_contract_evidence as _contract
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import InlineNamespace, Namespace, Record
from ..model.snapshot import AbiSnapshot
from ..model.template_default_table import (
    TemplateDefaults,
    added_defaults_only,
    split_template_default_key,
)
from .template_param_defaults import (
    _function_pattern_groups,
    _is_in_scope,
    _pattern_records,
)

__all__ = ["detect_template_default_table_changes"]


def _template_name(family: str, name: str) -> str:
    """The qualified template name of a key's *name* (a function key carries
    its function type after the name)."""
    return name.split("(", 1)[0] if family == "function" else name


def _fact_evaluated(old: AbiSnapshot, new: AbiSnapshot) -> set[tuple[str, str]]:
    """``(family, qualified name)`` of the class and function templates both
    snapshots carry as a pattern with a defaults fact: the pattern-fact
    detectors (``compare/template_param_defaults``) compare those."""
    evaluated: set[tuple[str, str]] = set()
    old_records, new_records = _pattern_records(old), _pattern_records(new)
    for key in old_records.keys() & new_records.keys():
        if (
            old_records[key].template_param_defaults_fact is not None
            and new_records[key].template_param_defaults_fact is not None
        ):
            evaluated.add(("class", key))
    for entity in (
        _function_pattern_groups(old).keys() & _function_pattern_groups(new).keys()
    ):
        scope = [
            segment.name
            for segment in entity.scope
            if isinstance(segment, (Namespace, Record, InlineNamespace))
        ]
        evaluated.add(("function", "::".join([*scope, entity.leaf_name])))
    return evaluated


def detect_template_default_table_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    instantiation_stems: Collection[str] = (),
    reported_templates: Collection[str] = (),
) -> tuple[list[Change], list[Change]]:
    """Default changes between the two snapshots' template-default tables.

    A class or function template the pattern-fact detectors already compared
    (:func:`_fact_evaluated`) is skipped. A change that
    an exported instantiation (*instantiation_stems*, shape A) or a reported
    function signature (*reported_templates*, shape C) already covers is
    returned in the second list, for the caller to record as deduplicated.

    Returns ``(findings, suppressed)``.
    """
    before_table = old.cxx_template_defaults
    after_table = new.cxx_template_defaults
    if before_table is None or after_table is None:
        return [], []
    evaluated = _fact_evaluated(old, new)
    findings: list[Change] = []
    suppressed: list[Change] = []
    for key in sorted(before_table.keys() & after_table.keys()):
        family, name = split_template_default_key(key)
        if family is None:
            continue
        qname = _template_name(family, name)
        if (family, qname) in evaluated:
            continue
        change = _change(family, name, qname, before_table[key], after_table[key])
        if change is None:
            continue
        covered = (family == "class" and qname in reported_templates) or any(
            _is_in_scope(stem, qname) for stem in instantiation_stems
        )
        (suppressed if covered else findings).append(change)
    return findings, suppressed


def _change(
    family: str,
    name: str,
    qname: str,
    before: TemplateDefaults,
    after: TemplateDefaults,
) -> Change | None:
    if before is None or after is None:
        return None  # not known on a side: the assurance rollup names it
    if len(before) != len(after) or before == after:
        return None
    if family != "function" and added_defaults_only(before, after):
        return None
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    return make_change(
        ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED,
        symbol=name,
        name=qname,
        detail=", ".join(f"param {i}" for i in changed),
        old_value=repr(before),
        new_value=repr(after),
        # Only templates declared in a public header are in the table.
        **_contract.public_reachability(True),
    )

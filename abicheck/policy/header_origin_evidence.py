# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Which types' public-surface seeding was decided on an *unread* header
origin.

``policy/public_surface_closure.py`` seeds a header-declared enum/record
from its ``source_header`` (and, for a record, ``qualified_name``) string.
When a producer stated it did not observe those facts the strings are
``None``, and the type is not seeded -- an unknown header string cannot be
told apart from "not header-declared". This module names those types
(``PublicSurface.header_origin_unknown_types``) so a finding demoted through
one is labelled ``header-origin-unknown`` and stated as a gap
(``surface.py``), never reported as a confirmed ``non-public-type``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..model.availability import FactStatus
from ..model.fact import Fact
from ..model.vocabulary import ScopeOrigin
from .public_surface import _DEMOTE_ORIGINS, PublicSurface

if TYPE_CHECKING:
    from ..model.entities import RecordType
    from ..model.snapshot import AbiSnapshot

_UNKNOWN_FACT_STATUSES = frozenset(
    {FactStatus.NOT_COLLECTED, FactStatus.UNSUPPORTED, FactStatus.FAILED}
)


def fact_is_stated_unknown(fact: Fact[Any] | None) -> bool:
    """True when a producer *stated* that it did not observe *fact*.

    ``UNSUPPORTED``/``FAILED`` always qualify, as does a ``NOT_COLLECTED``
    carrying a diagnostic or producer. A bare ``Fact.not_collected()`` does
    not: it is exactly what ``bridge_legacy_and_fact`` backfills for a legacy
    field constructed as ``None`` with no fact, which is how every existing
    producer and fixture spells "no header recorded" -- reading that as
    unknown would relabel every header-less internal type. The two readings
    are not distinguishable from the value alone; that residual is recorded
    on the ``evidence.unread_producer_read_as_confirmed_absence`` bug class."""
    if fact is None or fact.status not in _UNKNOWN_FACT_STATUSES:
        return False
    if fact.status is FactStatus.NOT_COLLECTED:
        return bool(fact.diagnostics) or fact.producer is not None
    return True


def _record_seed_blocked(
    rec: RecordType, qname_eligible: Callable[[str], bool]
) -> bool:
    """True when *rec* fails confirmed-public seeding only because its header
    origin or qualified name was not read: substituting a present value for
    each unknown one would have seeded it (``qname_eligible`` carries the
    closure's own internal-namespace and nested-record conditions)."""
    header_unknown = not rec.source_header and fact_is_stated_unknown(
        rec.source_header_fact
    )
    qname_unknown = not rec.qualified_name and fact_is_stated_unknown(
        rec.qualified_name_fact
    )
    if not (header_unknown or qname_unknown):
        return False
    if not (rec.source_header or header_unknown):
        return False
    qname = rec.qualified_name or rec.name
    return bool(
        qname and rec.origin is ScopeOrigin.PUBLIC_HEADER and qname_eligible(qname)
    )


def collect_header_origin_unknown_types(
    snap: AbiSnapshot, surface: PublicSurface, qname_eligible: Callable[[str], bool]
) -> None:
    """Fill ``surface.header_origin_unknown_types`` for *snap*."""
    for en in snap.declarations.enums:
        if (
            not en.source_header
            and fact_is_stated_unknown(en.source_header_fact)
            and en.origin not in _DEMOTE_ORIGINS
        ):
            surface.header_origin_unknown_types.update(
                n for n in (en.name, en.qualified_name) if n
            )
    for rec in snap.declarations.types:
        if _record_seed_blocked(rec, qname_eligible):
            surface.header_origin_unknown_types.update(
                n for n in (rec.name, rec.qualified_name) if n
            )

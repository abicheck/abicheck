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

"""Per-fact producer provenance for hybrid (castxml+clang merged) snapshots.

G28 Phase 3 (docs/contribute/plans/g28-castxml-clang-l2-parity-hardening.md).
A single-backend snapshot's ``ast_producer`` ("castxml"/"clang") already tells
a detector everything it needs: every fact on that snapshot came from that one
backend. A ``--ast-frontend hybrid`` snapshot (``dumper_hybrid.merge_snapshots``)
breaks that assumption — it merges castxml's and clang's independent parses of
the same headers into one ``AbiSnapshot``, so different declarations (and even
different facts on the *same* declaration) may have come from either backend.
``AbiSnapshot.fact_provenance`` records, per declaration and per castxml-only
fact, which backend's value the merge actually used; this module builds the
stable keys into that dict and answers the "is this fact castxml-backed on
this snapshot?" question every ``_both_castxml_backed``-gated detector needs.

Key scheme (all plain strings, safe to use as dict keys and stable across a
serialize/deserialize round-trip):

- ``func_fact_key(mangled, fact)``   -> ``"func:<mangled>:<fact>"``
- ``var_fact_key(mangled, fact)``    -> ``"var:<mangled>:<fact>"``
- ``type_fact_key(name, fact)``      -> ``"type:<name>:<fact>"``
- ``enum_fact_key(name, fact)``      -> ``"enum:<name>:<fact>"``
- ``field_fact_key(type, field, fact)`` -> ``"type:<type>:field:<field>:<fact>"``

A key absent from ``fact_provenance`` on a hybrid snapshot means neither
backend populated that fact for that declaration — same "unknown, don't
manufacture a finding" convention every other tri-state fact in this codebase
already uses.
"""

from __future__ import annotations

from typing import Any

from .model import AbiSnapshot


def backfill_fact(
    own_value: Any, clang_value: Any, key: str, provenance: dict[str, str]
) -> Any:
    """Return the merged value for one castxml-only fact and record its
    provenance under *key* (moved here from ``dumper_hybrid.py`` -- ADR-063
    Phase 5, that module's own architecture/debt.yaml no-growth budget).

    "Prefer castxml, backfill from clang only when castxml's own value is
    null" (G28 Phase 3 design). Provenance is recorded as ``"castxml"`` even
    when *own_value* is ``None`` and no clang value was available to
    backfill from — the entity itself IS castxml-sourced; a genuinely
    "not deprecated"/"not overridden"/etc. `None` is not the same as "this
    entity was never seen by castxml at all" (the latter simply never calls
    this helper — see ``dumper_hybrid.merge_snapshots``'s clang-only-entity
    handling).
    """
    if own_value is None and clang_value is not None:
        provenance[key] = "clang"
        return clang_value
    provenance[key] = "castxml"
    return own_value


def func_fact_key(mangled: str, fact: str) -> str:
    return f"func:{mangled}:{fact}"


def var_fact_key(mangled: str, fact: str) -> str:
    return f"var:{mangled}:{fact}"


def type_fact_key(name: str, fact: str) -> str:
    return f"type:{name}:{fact}"


def enum_fact_key(name: str, fact: str) -> str:
    return f"enum:{name}:{fact}"


def field_fact_key(type_name: str, field_name: str, fact: str) -> str:
    return f"type:{type_name}:field:{field_name}:{fact}"


def is_castxml_backed_fact(snap: AbiSnapshot, key: str) -> bool:
    """True if *key* is known to be castxml-sourced on *snap*.

    Mirrors ``diff_symbols._both_header_aware``'s "confirmed header tier"
    requirement inline rather than importing it, to avoid this low-level,
    dependency-free module reaching back into the diff layer.

    - Not (confirmed) header-aware: False — same as today's whole-snapshot
      gate, regardless of producer.
    - ``ast_producer == "castxml"``: True unconditionally, matching every
      existing single-backend snapshot's behavior (a castxml snapshot's
      *own* facts are all castxml-sourced, by construction — no per-key
      lookup needed, and none is recorded for these).
    - ``ast_producer == "hybrid"``: True only if the merge actually recorded
      this specific *key* as castxml-sourced. A hybrid snapshot's merge
      policy is "prefer castxml, backfill from clang only when castxml's own
      value is null" (see dumper_hybrid.py) — so this is False for a
      declaration that exists only via clang, or whose value for this
      specific fact was backfilled from clang rather than read from castxml.
    - Anything else (pure "clang", ``None``/unknown producer): False.
    """
    if not (snap.from_headers and not snap.from_headers_inferred):
        return False
    if snap.ast_producer == "castxml":
        return True
    if snap.ast_producer == "hybrid":
        return snap.fact_provenance.get(key) == "castxml"
    return False


def both_castxml_backed_fact(old: AbiSnapshot, new: AbiSnapshot, key: str) -> bool:
    """True if *key* is castxml-sourced on BOTH *old* and *new*.

    Drop-in per-fact replacement for ``diff_symbols._both_castxml_backed``'s
    whole-snapshot check at each individual per-declaration comparison site.
    """
    return is_castxml_backed_fact(old, key) and is_castxml_backed_fact(new, key)


def fact_producer(snap: AbiSnapshot, key: str) -> str | None:
    """Which single backend ("castxml"/"clang") actually backs *key* on
    *snap*, or ``None`` if that isn't known.

    Unlike :func:`is_castxml_backed_fact` (which answers "is this
    castxml-sourced, yes/no" for facts ONLY castxml ever populates), this is
    for a fact BOTH backends can independently produce a real, same-backend-
    comparable value for (e.g. ``Function.params[i].default`` once
    ``dumper_clang.py`` started populating it too) — the risk there isn't
    "clang has no value", it's that the two backends' value *representations*
    aren't cross-comparable (castxml keeps the source expression text; clang
    falls back to a structural fingerprint/placeholder for anything beyond a
    bare literal), so a mixed-producer pair must not be compared even though
    both sides have SOME value. A same-producer pair (both "castxml" or both
    "clang") is safe to compare — that is exactly what a same-backend
    ``--ast-frontend castxml``/``--ast-frontend clang`` run already does.

    - Not (confirmed) header-aware: None.
    - ``ast_producer == "clang"`` AND *key* is a ``deprecated``/``is_scoped``
      fact AND ``snap.clang_deprecation_facts_reliable`` is False (a
      snapshot persisted before schema v19/G31 Phase C): None — a legacy
      clang-producer snapshot's value for exactly these two facts is real
      but WRONG (unconditional None/False), not merely absent.
    - ``ast_producer == "clang"`` AND *key* is a field ``default`` fact AND
      ``snap.clang_field_initializer_facts_reliable`` is False (a snapshot
      persisted before schema v20/G31 Phase C): None — same shape, one
      schema version later.
    - ``ast_producer in ("castxml", "clang")`` (and the above didn't apply):
      that value unconditionally — every fact on a single-backend snapshot
      came from that one backend.
    - ``ast_producer == "hybrid"``: whatever the merge recorded for *key*
      (``None`` if neither backend's value made it into the map).
    - Anything else (``None``/unknown producer, legacy snapshot): None.
    """
    if not (snap.from_headers and not snap.from_headers_inferred):
        return None
    if (
        snap.ast_producer == "clang"
        and not snap.clang_field_initializer_facts_reliable
        and key.endswith(":default")
    ):
        # G31 Phase C / schema v20, exactly the reasoning the deprecation
        # gate below records: a clang-producer snapshot persisted before the
        # direct-clang backend extracted default member initializers has real
        # but WRONG data for this fact (an unconditional None,
        # indistinguishable by value alone from "this field genuinely has no
        # initializer"), so its "clang" producer tag must not be trusted for
        # it. ``:default`` is only ever the suffix of a field_fact_key(...,
        # "default") key -- Param.default's own key ends in
        # ``:param_defaults`` -- so this stays scoped to the affected fact.
        return None
    if snap.ast_producer == "clang" and not snap.clang_deprecation_facts_reliable:
        # G31 Phase C / schema v19 (Codex review, fresh evidence): a
        # deprecated/is_scoped comparison against a snapshot persisted
        # before the direct-clang backend genuinely extracted these facts
        # cannot trust a "clang" producer tag for them -- every declaration
        # on that legacy snapshot has real but WRONG data (an
        # unconditional None/False, indistinguishable by value alone from
        # a genuine "not deprecated"/"not scoped" fact). Scoped to exactly
        # the two affected fact suffixes so this doesn't spuriously
        # invalidate an unrelated multi-backend fact (e.g. param_defaults).
        if key.endswith(":deprecated") or key.endswith(":is_scoped"):
            return None
    if snap.ast_producer in ("castxml", "clang"):
        return snap.ast_producer
    if snap.ast_producer == "hybrid":
        return snap.fact_provenance.get(key)
    return None


def both_known_backed_fact(old: AbiSnapshot, new: AbiSnapshot, key: str) -> bool:
    """True if *key* has a POSITIVELY known header-AST producer on BOTH
    *old* and *new* — castxml, clang, or hybrid-with-a-recorded-value, in any
    combination (G31 Phase C).

    For a fact whose VALUE REPRESENTATION is directly cross-comparable
    between backends (e.g. ``deprecated``'s message string, or
    ``EnumType.is_scoped``'s plain bool — both backends extract the exact
    same real-world fact, not a backend-specific encoding of it), this is
    the correct gate once more than one backend populates it: unlike
    :func:`both_castxml_backed_fact` (for a fact only ONE backend, castxml,
    can produce at all — using this on a now-multi-backend fact would wrongly
    keep rejecting a perfectly good clang-vs-clang or clang-vs-castxml pair
    just because neither/one side is castxml), and unlike the same-producer
    check ``diff_symbols._diff_param_defaults`` uses via plain
    :func:`fact_producer` (needed only when the two backends' value
    representations are NOT cross-comparable, e.g. ``Param.default``'s real
    source expression on castxml vs. a structural placeholder on clang) —
    this fact family needs neither restriction, only confirmation that
    SOME known backend actually populated it on each side.
    """
    return fact_producer(old, key) is not None and fact_producer(new, key) is not None


def both_known_backed_fact_qualified(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_qualified_key: str,
    new_qualified_key: str,
    bare_key: str,
    *,
    old_bare_unambiguous: bool,
    new_bare_unambiguous: bool,
) -> bool:
    """Like :func:`both_known_backed_fact`, but for a fact whose provenance
    key was namespace-qualified after a hybrid snapshot may already have
    been persisted with the former bare key (Codex review, fresh evidence:
    a ``--ast-frontend hybrid`` baseline written before G31 Phase C's
    qualification fix has real ``"castxml"``/``"clang"`` provenance recorded
    under the bare key alone, since every matched declaration already got a
    provenance entry via ``_backfill_fact`` regardless of that fact's actual
    value — qualifying only the *lookup* key would silently treat all of
    that real data as unknown and suppress genuine transitions).

    Takes *old_qualified_key*/*new_qualified_key* SEPARATELY, not one shared
    key — a matched old/new pair can legitimately have different qualified
    identities (e.g. *old* predates ``qualified_name`` entirely, so its own
    ``type_map_key()`` is bare, while *new* carries the real namespaced
    spelling); probing both sides with only one side's key would miss the
    other side's real, qualified-keyed entry (Codex review, fresh evidence,
    second round). Falls back to the shared *bare_key* on either side only
    when the caller confirms (``old_bare_unambiguous``/``new_bare_unambiguous``
    — typically ``TypeMap.bare_name_is_unambiguous``) that no OTHER distinct
    qualified identity on that side shares the same bare name. Without that
    check, the fallback would reopen the exact bare-name collision the
    qualification was introduced to close.
    """
    old_producer = resolved_fact_producer(
        old, old_qualified_key, bare_key, bare_unambiguous=old_bare_unambiguous
    )
    new_producer = resolved_fact_producer(
        new, new_qualified_key, bare_key, bare_unambiguous=new_bare_unambiguous
    )
    return old_producer is not None and new_producer is not None


def resolved_fact_producer(
    snap: AbiSnapshot,
    qualified_key: str,
    bare_key: str,
    *,
    bare_unambiguous: bool,
) -> str | None:
    """:func:`fact_producer` for *qualified_key*, falling back to *bare_key*.

    The qualified-then-bare probe shared by every namespace-qualified
    provenance lookup: a hybrid snapshot persisted before a fact's key was
    qualified carries real provenance under the former bare key alone, so the
    fallback is what keeps that data readable — but only when *bare_unambiguous*
    (typically ``TypeMap.bare_name_is_unambiguous``) confirms no OTHER distinct
    qualified identity on this side shares the bare name, since otherwise the
    fallback reopens the collision the qualification closed.
    """
    producer = fact_producer(snap, qualified_key)
    if producer is None and bare_unambiguous:
        producer = fact_producer(snap, bare_key)
    return producer


def same_producer_backed_fact_qualified(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_qualified_key: str,
    new_qualified_key: str,
    bare_key: str,
    *,
    old_bare_unambiguous: bool,
    new_bare_unambiguous: bool,
) -> bool:
    """True only if *old* and *new* have the SAME POSITIVELY KNOWN producer
    for this fact — the per-declaration form of the same-producer check
    ``diff_symbols._diff_param_defaults`` applies inline via plain
    :func:`fact_producer` (G31 Phase C).

    This is the correct gate for a fact BOTH backends populate but whose
    VALUE REPRESENTATIONS are not cross-comparable — ``TypeField.default``
    (this function's only current caller), where castxml keeps the verbatim
    source expression and clang falls back to a literal/structural
    fingerprint. It sits between this module's two other gates:
    :func:`both_castxml_backed_fact` (for a fact only castxml can produce at
    all — too strict here, it would decline a perfectly comparable
    clang-vs-clang pair) and :func:`both_known_backed_fact` (for a fact whose
    values ARE cross-comparable — too loose here, it would compare castxml's
    source text against clang's fingerprint and read every initializer as
    changed).

    Deliberately NOT permissive on an unknown producer, unlike
    ``_diff_param_defaults``' own inline check (Codex review, fresh
    evidence): a side whose producer resolves to ``None`` — including a
    snapshot persisted before ``ast_producer`` was tracked at all — could
    just as easily be a real castxml value the OTHER, positively-known
    "clang" side's fingerprint is genuinely incomparable against as it could
    be an equivalent castxml pair, and this fact's value representation
    (unlike ``deprecated``/``is_scoped``) offers no way to tell those apart
    from the values alone. Treating "unknown" as "assume comparable" here
    would reintroduce exactly the false ``FIELD_DEFAULT_INITIALIZER_CHANGED``
    this detector's PREDECESSOR gate (``both_castxml_backed_fact``, which
    also required a POSITIVELY known ``"castxml"`` on both sides — ``None``
    never passed it either) never produced. Requiring both producers
    positively known restores that original guarantee while still comparing
    a same-known-producer pair regardless of which backend it is (unlike
    ``both_castxml_backed_fact``, which only ever accepts ``"castxml"``).
    """
    old_producer = resolved_fact_producer(
        old, old_qualified_key, bare_key, bare_unambiguous=old_bare_unambiguous
    )
    new_producer = resolved_fact_producer(
        new, new_qualified_key, bare_key, bare_unambiguous=new_bare_unambiguous
    )
    return (
        old_producer is not None
        and new_producer is not None
        and old_producer == new_producer
    )

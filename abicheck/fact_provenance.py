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


def fact_producer(snap: AbiSnapshot, key: str) -> str | None:
    """Which single backend ("castxml"/"clang") actually backs *key* on
    *snap*, or ``None`` if that isn't known.

    For a fact BOTH backends can independently produce a real, same-backend-
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
    - ``ast_producer in ("castxml", "clang")``:
      that value unconditionally — every fact on a single-backend snapshot
      came from that one backend.
    - ``ast_producer == "hybrid"``: whatever the merge recorded for *key*
      (``None`` if neither backend's value made it into the map).
    - Anything else (``None``/unknown producer, legacy snapshot): None.
    """
    if not (snap.from_headers and not snap.from_headers_inferred):
        return None
    # The legacy clang ``deprecated``/``is_scoped``/field-``default`` gates
    # that used to live here (schema v19/v20) are gone: loading such a
    # document demotes each affected declaration's own ``*_fact`` to
    # ``NOT_COLLECTED`` (``storage.fact_backfill``), and every consumer of
    # those three facts gates per declaration on that status
    # (``compare.fact_gate.both_facts_present``) before asking this function.
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
    the correct gate once more than one backend populates it: unlike the
    retired castxml-only gate (which rejected a perfectly good
    clang-vs-clang or clang-vs-castxml pair just because neither/one side
    was castxml), and unlike the same-producer
    check ``diff_symbols._diff_param_defaults`` uses via plain
    :func:`fact_producer` (needed only when the two backends' value
    representations are NOT cross-comparable, e.g. ``Param.default``'s real
    source expression on castxml vs. a structural placeholder on clang) —
    this fact family needs neither restriction, only confirmation that
    SOME known backend actually populated it on each side.
    """
    return fact_producer(old, key) is not None and fact_producer(new, key) is not None


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
    fingerprint. It is stricter than :func:`both_known_backed_fact` (for a fact whose
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
    this detector's PREDECESSOR gate (a castxml-only check, which
    also required a POSITIVELY known ``"castxml"`` on both sides — ``None``
    never passed it either) never produced. Requiring both producers
    positively known restores that original guarantee while still comparing
    a same-known-producer pair regardless of which backend it is (unlike
    that predecessor, which only ever accepted ``"castxml"``).
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

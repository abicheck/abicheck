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

from .model import AbiSnapshot


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
    - ``ast_producer in ("castxml", "clang")`` (and the above didn't apply):
      that value unconditionally — every fact on a single-backend snapshot
      came from that one backend.
    - ``ast_producer == "hybrid"``: whatever the merge recorded for *key*
      (``None`` if neither backend's value made it into the map).
    - Anything else (``None``/unknown producer, legacy snapshot): None.
    """
    if not (snap.from_headers and not snap.from_headers_inferred):
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

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

"""Canonical entity identity for source-graph nodes (G31 Phase B, ADR-048).

Every :class:`~abicheck.buildsource.source_graph.GraphNode` today is keyed by
whatever id its own producer happened to compute (``_decl_node_id``/
``_type_node_id`` in ``source_graph.py``, each a hash of exactly one identity
signal chosen ad hoc per call site — see ADR-046 D4's ``EntityResolver``
proposal, which this module is a scoped, additive slice of: it does NOT
change ``GraphNode.id``/the v1 node-id scheme, only adds a second, richer
identity computed *alongside* it for reconciliation (``graph_reconcile.py``)
and impact-linking (``graph_impact.py``) to consume).

Preference order (most to least specific):

1. **canonical** — a compiler-provided stable identity (clang USR) when a
   producer supplies one, else a real Itanium/MSVC mangled name (never a
   bare name that merely equals the mangled field, matching
   ``source_graph.function_decl_identity``'s own ``mangled_name != name``
   check and ADR-046 D4's "every other identity signal becomes an alias, not
   a replacement" framing).
2. **normalized** — a normalized fully-qualified semantic signature
   (qualified name + kind + arity/parameter-types) when no real mangling is
   available.
3. **reduced** — a source-relative declaration identity (file + enclosing
   scope + name) when even a qualified name is unavailable, or a synthetic,
   clearly-low-confidence fallback hash when nothing else is available at
   all.

Mangled-name derivation/validation reuses :mod:`abicheck.demangle` (never a
second demangling implementation). The source-relative/qualified-name
fallback chain generalizes the ad hoc ``{dname, qualified_name, ...}``
lookup-key pattern ``internal_leak.py`` used before this module existed (see
:func:`candidate_lookup_keys`) and the namespace-qualified ``TypeMap``
mechanism ADR-045 already generalized for flat old/new type matching
(``diff_helpers.py``) — this module is the L5-graph-node analogue of that
same principle, not a third independent implementation of it.

NEVER invents a fact a producer did not supply: a tier is only claimed when
the corresponding input field is actually present; an absent USR/mangled
name degrades the tier, it is never guessed at.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import demangle

if TYPE_CHECKING:
    from .source_graph import GraphNode

#: Identity-confidence tiers (ADR-048), deliberately matching the
#: ``canonical``/``normalized``/``reduced`` vocabulary requested by the
#: G31 Phase B scope doc rather than reusing ``source_graph.CONF_*`` verbatim
#: — a *confidence* label (how much to trust one fact) and an *identity tier*
#: (how specific the entity's key is) are different axes that happen to
#: share "how sure are we" framing; keeping them textually distinct avoids a
#: reader conflating "this edge is CONF_REDUCED" with "this node's identity
#: is IDENTITY_TIER_REDUCED".
IDENTITY_TIER_CANONICAL = "canonical"
IDENTITY_TIER_NORMALIZED = "normalized"
IDENTITY_TIER_REDUCED = "reduced"


@dataclass(frozen=True)
class CanonicalIdentity:
    """Canonical identity computed for one graph node / declaration / type.

    ``primary_id`` is the single key reconciliation and impact-linking should
    match on. ``aliases`` carries every other identity signal available for
    this entity (never used as the primary key, but consulted for the
    alias-match reconciliation tier — ADR-048 D2).
    """

    primary_id: str
    tier: str  # one of IDENTITY_TIER_*
    aliases: tuple[str, ...] = ()
    qualified_name: str = ""
    normalized_signature: str = ""
    source_relative: str = ""
    kind: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_id": self.primary_id,
            "tier": self.tier,
            "aliases": list(self.aliases),
            "qualified_name": self.qualified_name,
            "normalized_signature": self.normalized_signature,
            "source_relative": self.source_relative,
            "kind": self.kind,
        }


def is_real_mangled_name(mangled_name: str | None, plain_name: str | None) -> bool:
    """Whether *mangled_name* is a genuine mangling, not a bare name that
    merely rode in the "mangled" field (extern "C"/C-linkage producers report
    ``mangled_name == name`` deliberately — mirrors
    ``source_graph.function_decl_identity``'s identical check).
    """
    return bool(mangled_name) and mangled_name != plain_name


def normalize_mangled_name(
    mangled_name: str | None, plain_name: str | None
) -> str | None:
    """Return *mangled_name* if it is a real, verifiable mangled name, else
    ``None`` — never a guess.

    Reuses :mod:`abicheck.demangle` (Itanium ``_Z...``) to confirm the string
    actually demangles to something different from itself; that is the only
    verification available without a live compiler. MSVC ``?``-prefixed
    manglings have no demangler in this codebase, so they are accepted on
    the prefix convention alone (best-effort, matches how the rest of the
    codebase treats MSVC-mangled names it cannot independently demangle).
    """
    if not is_real_mangled_name(mangled_name, plain_name):
        return None
    assert mangled_name is not None  # for type-checkers; guarded above
    if mangled_name.startswith("_Z"):
        demangled = demangle.demangle(mangled_name)
        if demangled and demangled != mangled_name:
            return mangled_name
        return None
    if mangled_name.startswith("?"):
        return mangled_name
    return None


def normalized_signature(
    qualified_name: str, kind: str, param_types: tuple[str, ...] = ()
) -> str:
    """A normalized fully-qualified semantic signature: qualified name + kind
    + arity/parameter-types. Deterministic and order-preserving over
    *param_types* so two identically-declared overloads never collide.
    """
    parts = [qualified_name or "", kind or "", str(len(param_types)), *param_types]
    return "sig:" + "\x1f".join(parts)


def source_relative_identity(file: str, scope: str, name: str) -> str:
    """File + enclosing scope + name — an alias, never a primary key
    (ADR-048 D1): two distinct entities can legitimately share this triple
    across an ODR-violating build or a macro-generated declaration, so it is
    not trusted alone as a canonical id.
    """
    return f"{file or ''}\x1f{scope or ''}\x1f{name or ''}"


def resolve_canonical_identity(
    *,
    usr: str | None = None,
    mangled_name: str | None = None,
    name: str | None = None,
    qualified_name: str | None = None,
    kind: str = "",
    param_types: tuple[str, ...] = (),
    file: str = "",
    scope: str = "",
) -> CanonicalIdentity:
    """Resolve the canonical identity for one entity from whatever facts a
    producer actually supplied (ADR-048 D1). Never fabricates a fact: a tier
    is claimed only when its corresponding input is present.
    """
    qn = qualified_name or name or ""
    sig = normalized_signature(qn, kind, param_types)
    rel = source_relative_identity(file, scope, name or qn)

    aliases: list[str] = []
    if mangled_name:
        aliases.append(f"mangled:{mangled_name}")
    if name:
        aliases.append(f"name:{name}")
    if qn:
        aliases.append(f"qualified:{qn}")
    aliases.append(sig)
    if file:
        aliases.append(f"relsrc:{rel}")

    if usr:
        primary = f"usr:{usr}"
        return CanonicalIdentity(
            primary, IDENTITY_TIER_CANONICAL, tuple(aliases), qn, sig, rel, kind
        )

    real_mangled = normalize_mangled_name(mangled_name, name)
    if real_mangled:
        primary = f"mangled:{real_mangled}"
        return CanonicalIdentity(
            primary, IDENTITY_TIER_CANONICAL, tuple(aliases), qn, sig, rel, kind
        )

    if qn:
        return CanonicalIdentity(
            sig, IDENTITY_TIER_NORMALIZED, tuple(aliases), qn, sig, rel, kind
        )

    # Source-relative identity (file + enclosing scope + name) is ALWAYS an
    # alias (already appended above when *file* is set), never promoted to
    # primary_id — per the G31 Phase B scope doc's tier 4: "as an additional
    # alias, not a primary key." When neither a mangled name nor a
    # qualified/plain name is available at all, fall through to the
    # synthetic tier below even if a file/scope alias exists.

    # Synthetic fallback (ADR-048 D1 tier 5): clearly marked low-confidence
    # (IDENTITY_TIER_REDUCED, "synthetic:" prefix) — used only when nothing
    # else is available at all.
    basis = "\x1f".join(
        str(x) for x in (mangled_name, name, qualified_name, kind, file, scope) if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return CanonicalIdentity(
        synthetic, IDENTITY_TIER_REDUCED, tuple(aliases), qn, sig, rel, kind
    )


def resolve_identity_for_node(node: GraphNode) -> CanonicalIdentity:
    """Resolve a :class:`~abicheck.buildsource.source_graph.GraphNode`'s
    canonical identity from its own attrs/label — the bridge B2's
    reconciliation and B3's impact-linking use instead of re-deriving
    identity from raw facts.

    Only facts the node's producer actually stamped are read (``attrs.get``
    with no invented defaults): a header-only-graph node with no
    ``mangled_name`` attr never fabricates one.
    """
    attrs = node.attrs
    return resolve_canonical_identity(
        usr=str(attrs.get("usr")) if attrs.get("usr") else None,
        mangled_name=str(attrs.get("mangled_name"))
        if attrs.get("mangled_name")
        else None,
        name=str(attrs.get("name")) if attrs.get("name") else (node.label or None),
        qualified_name=str(attrs.get("qualified_name"))
        if attrs.get("qualified_name")
        else (node.label or None),
        kind=node.kind,
        param_types=tuple(attrs.get("param_types", ()))
        if attrs.get("param_types")
        else (),
        file=str(attrs.get("def_file") or attrs.get("file") or ""),
        scope=str(attrs.get("scope") or ""),
    )


def candidate_lookup_keys(primary: str | None, *extra: str | None) -> set[str]:
    """Ambiguity-*aware* (not ambiguity-*safe* on its own — callers still
    need to check uniqueness the way ``diff_helpers.TypeMap`` does for flat
    old/new type matching, ADR-045) set of lookup keys for a dict keyed by
    declaration identity.

    Generalizes the ad hoc ``{dname, *(t.qualified_name for t in ...)}``
    pattern ``internal_leak.py``'s call-graph-leak-path lookup used before
    this module existed, into one shared helper other L5-graph consumers can
    call instead of hand-rolling their own candidate-key set (G31 Phase B
    B1 scope: "generalize into one shared resolution path").
    """
    keys: set[str] = {primary} if primary else set()
    keys.update(k for k in extra if k)
    return keys

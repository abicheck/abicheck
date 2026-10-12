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

"""Identity of an already-emitted finding (``resolve_change_identity``)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from ...model.entity_identity import source_relative_identity
from ...model.root_relative_path import RootRelativePath
from ...name_classification import canonicalize_type_name

if TYPE_CHECKING:
    from ...model.change import Change

from .atomic import canonicalize_atomic_slot
from .change_kinds import (
    _DESCRIPTION_EMBEDS_VALUES_KINDS,
    _EQUIVALENT_CHANGE_CATEGORIES,
    _MISMATCH_SIDE_RE,
    _STRUCTURED_EVIDENCE_KIND_SLUGS,
    _TYPE_BEARING_DISCRIMINATOR_KINDS,
    _is_batch_shaped_change,
    _is_symbol_level_kind,
)
from .mangled import normalize_mangled_name
from .symbols import (
    IDENTITY_TIER_CANONICAL,
    IDENTITY_TIER_NORMALIZED,
    IDENTITY_TIER_REDUCED,
    FindingIdentity,
)


def _split_top_level_commas(value: str) -> list[str]:
    """Split *value* on ``,`` at nesting depth 0 only.

    ``_format_params()`` comma-joins ``Param.type`` spellings, but a
    parameter's own type can itself contain a comma inside template
    arguments (``std::pair<int, int>``) or a function-pointer parameter
    list (``void (*)(int, int)``) -- a naive ``str.split(",")`` would
    wrongly split those apart. Tracks ``<>``/``()``/``[]`` nesting depth
    and only treats a comma at depth 0 as a real parameter separator.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(value):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(value[start:i])
            start = i + 1
    parts.append(value[start:])
    return parts


def _canonicalize_params_list(value: str) -> str:
    """Canonicalize a ``_format_params()``-shaped comma-joined parameter
    list one parameter at a time, rejoining with ``", "`` (matching
    ``_format_params``'s own join).

    Degrades to whole-string canonicalization for the sentinel ``"(none)"``
    ``_format_params`` emits for a zero-parameter function -- splitting
    that on commas would be a no-op anyway, but routing it through the
    same call keeps this function's contract uniform.
    """
    return ", ".join(
        canonicalize_type_name(part.strip()) for part in _split_top_level_commas(value)
    )


def _stringify_change_value(value: object) -> str:
    """Deterministic string form of a ``Change.old_value``/``new_value``.

    Both fields are annotated ``str | None``, but that annotation isn't
    runtime-enforced and at least one real producer violates it:
    ``diff_python.py``'s ``PYTHON_STABLE_ABI_VIOLATION`` emissions pass a
    list (e.g. ``new_value=sorted(group)``) for several of its findings.
    Joining that list directly into ``_change_discriminator``'s
    ``\\x1f``-separated ``parts`` crashed with ``TypeError: sequence item
    ...: expected str instance, list found`` instead of producing an
    identity (Codex review, fresh evidence) -- this module must resolve an
    identity for every real flat ``Change``, not only ones matching the
    aspirational type annotation. Lists/tuples (the one structured shape
    actually observed) join deterministically, preserving the producer's
    own ordering (already sorted where order matters); any other
    unforeseen structured value falls back to ``str()`` rather than
    crashing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


# Only the bit-valued half of each _EQUIVALENT_CHANGE_CATEGORIES size/
# alignment pair needs converting -- diff_platform.py's byte-valued
# STRUCT_SIZE_CHANGED/STRUCT_ALIGNMENT_CHANGED are already the target unit.
# Used only by _change_discriminator's canonicalize_values=True path, so a
# category-collapsed kind's old/new agree regardless of the detector
# (Codex review, fresh evidence -- see that call site's own comment).
_CATEGORY_VALUE_UNIT_DIVISOR = {
    "type_size_changed": 8,
    "type_alignment_changed": 8,
}

#: Categories where old_value/new_value must be dropped from the
#: canonicalize_values discriminator entirely, rather than folded in
#: (Codex review, fresh evidence, PR #753 round 5): unlike the size/
#: alignment pair above -- where old/new differ only by *unit* across
#: producers and normalizing fixes the fold -- a removed/added symbol can
#: have at most one such event per (symbol, category) in a single
#: comparison, so old/new carries no disambiguating power here at all,
#: only producer-specific noise. Confirmed for "func_removal": the rich
#: ELF detector (diff_symbols._check_removed_function) stamps
#: old_value=f_old.name, while the PE/Mach-O export-table detectors
#: (diff_platform._diff_pe/_diff_macho_exports) leave old_value/new_value
#: unset entirely -- so the identical removed-function event folds to two
#: different discriminators ("category:func_removal\x1f<name>\x1f" vs.
#: "category:func_removal\x1f\x1f") depending purely on which detector's
#: finding survived reconciliation, defeating this fold's whole purpose
#: for the one pair it exists to reconcile. "func_addition" carries the
#: identical asymmetry for the same two detector families. The remaining
#: three categories ("var_removal"/"var_addition"/"version_def_removal")
#: are not included here: each is populated by exactly one detector
#: family with consistent old/new population (verified by reading every
#: producer of each), so there is no cross-producer inconsistency to
#: guard against for them, and dropping their values would only lose
#: information for no benefit.
_VALUE_INSENSITIVE_CATEGORIES = frozenset({"func_removal", "func_addition"})


def _divide_numeric_string(value: str, divisor: int) -> str:
    """Return ``str(int(value) // divisor)``, or *value* unchanged if it
    isn't a base-10 integer (defensive -- every real producer of a
    _CATEGORY_VALUE_UNIT_DIVISOR kind stamps a plain bit count, but this
    must never raise on an unexpected input)."""
    try:
        return str(int(value) // divisor)
    except ValueError:
        return value


def _change_discriminator(
    change: Change,
    kind_value: str,
    *,
    include_description: bool = True,
    canonicalize_values: bool = False,
) -> str:
    """The part of a finding's identity that tells it apart from another
    finding sharing the same symbol.

    Most kinds need the full ``(kind, old_value, new_value, description)``
    tuple -- e.g. ``FUNC_RETURN_CHANGED`` and ``FUNC_PARAMS_CHANGED`` on the
    same function must not collide (Codex review). But a handful of kind
    *pairs* in :data:`_EQUIVALENT_CHANGE_CATEGORIES` are one logical event
    under two different detectors' own wording (e.g. ``FUNC_REMOVED`` vs.
    ``FUNC_REMOVED_ELF_ONLY``) -- those must collide on symbol + category
    alone, ignoring the detector-specific kind/old/new/description text, or
    this identity could never actually perform the rich/L0 reconciliation
    it is meant to drive once wired in (Codex review).

    ``include_description=False`` additionally drops ``description`` alone
    (keeping ``old_value``/``new_value``) -- used by
    :func:`resolve_change_identity` for a batch-shaped change whose
    ``description`` embeds the arbitrary sampled symbol via its
    ``description_template`` (``diff_platform_elf_symbols.py``'s
    ``_check_gained_gnu_unique``: ``"Symbol binding became GNU_UNIQUE:
    {name} ..."`` where ``name`` is the sample). Clearing ``entity_symbol``
    alone is not enough, since ``description`` still varies with the sample
    even though ``old_value``/``new_value`` do not (Codex review: verified
    changing only the sampled export still produced a different id).

    ``canonicalize_values=True`` -- used only by
    :func:`report_canonical_finding_id`'s call into
    :func:`resolve_change_identity` -- runs ``old_value``/``new_value``,
    and (for the same kinds) ``description``, through
    :func:`~abicheck.name_classification.canonicalize_type_name` before
    joining, but **only** for :data:`_TYPE_BEARING_DISCRIMINATOR_KINDS`.
    Without it, a kind like ``FUNC_RETURN_CHANGED`` folds the *raw* type
    spelling into every identity tier including CANONICAL -- CastXML's
    ``"char const*"`` and Clang's ``"char const *"`` for the identical
    change would then hash to two different canonical ids (Codex review).
    Scoped to that allowlist rather than every kind (Codex review, follow-up
    round): canonicalizing an arbitrary kind's ``old_value``/``new_value``
    corrupts a non-type value where whitespace is semantically meaningful
    (e.g. ``PUBLIC_MACRO_VALUE_CHANGED``'s raw macro replacement text).
    ``description`` is **never dropped** for this reason too (an earlier
    revision of this fix did, and review found real collisions: several
    kinds -- e.g. ``struct_field_type_changed``'s own sibling
    ``type_field_type_changed`` -- embed a per-item identity, such as a
    field name, in ``description`` via ``change_registry``'s ``{detail}``
    template placeholder, with no other structured field carrying it;
    dropping description collapsed two distinct findings differing only by
    which field/parameter changed). Canonicalizing the whole sentence for
    an allowlisted kind is safe rather than corrupting: ``canonicalize_
    type_name``'s struct/const-prefix rewrites only match at the string's
    start, and its pointer/reference-sigil pass only touches ``*``/``&`` --
    neither appears in an ordinary field/parameter name, so any embedded
    raw type spelling normalizes the same way ``old_value``/``new_value`` do.
    """
    category = _EQUIVALENT_CHANGE_CATEGORIES.get(kind_value)
    if category is not None:
        if not canonicalize_values or category in _VALUE_INSENSITIVE_CATEGORIES:
            # See _VALUE_INSENSITIVE_CATEGORIES: for these categories old/
            # new is producer-inconsistent noise, not a disambiguator (at
            # most one such event exists per symbol/category in a single
            # comparison), so folding it in would defeat the fold instead
            # of refining it -- same bare-category discriminator as the
            # non-canonicalize_values path above.
            return f"category:{category}"
        # canonicalize_values (report_canonical_finding_id): a bare
        # category discriminates the rich-vs-L0 *kind* pair away, which is
        # exactly what the reconciliation use of this identity wants -- but
        # a persistent suppression identity must still tell apart two
        # structurally different transitions on the same symbol/category
        # (e.g. TYPE_SIZE_CHANGED 8->16 vs. a later, unrelated 16->32 on
        # the same type), or accepting a `finding_id:` rule for the first
        # would silently suppress the second too (Codex review, fresh
        # evidence). Folding in old_value/new_value is NOT unit-safe by
        # itself: diff_types.py's TYPE_SIZE_CHANGED/TYPE_ALIGNMENT_CHANGED
        # stamp RecordType.size_bits/alignment_bits (bits), while
        # diff_platform.py's collapsed sibling STRUCT_SIZE_CHANGED/
        # STRUCT_ALIGNMENT_CHANGED stamp StructLayout.byte_size/alignment
        # (bytes) -- the identical 8-byte layout change reports as
        # "64\x1f128" from one detector and "8\x1f16" from the other
        # (Codex review, fresh evidence), which would make the canonical
        # id depend on which detector happened to supply the *retained*
        # finding after reconciliation, defeating this fold's own point.
        # _CATEGORY_VALUE_UNIT_DIVISOR converts every such kind to bytes
        # before folding, leaving every other collapsed kind's value
        # (a removed symbol's own name, an enum value, ...) untouched.
        old_val = _stringify_change_value(change.old_value)
        new_val = _stringify_change_value(change.new_value)
        divisor = _CATEGORY_VALUE_UNIT_DIVISOR.get(kind_value)
        if divisor is not None:
            old_val = _divide_numeric_string(old_val, divisor)
            new_val = _divide_numeric_string(new_val, divisor)
        old_str = canonicalize_type_name(old_val)
        new_str = canonicalize_type_name(new_val)
        return f"category:{category}\x1f{old_str}\x1f{new_str}"
    # header_binary_context_mismatch: change.affected_symbols carries this
    # finding's complete mismatched-record set as structured data (unlike
    # description's order-dependent five-name sample) -- sorting it makes
    # the discriminator order-independent while still varying whenever the
    # actual evidence differs (see _STRUCTURED_EVIDENCE_KIND_SLUGS). The
    # side prefix (extracted via _MISMATCH_SIDE_RE, itself immune to the
    # sampled-order issue) keeps an old-side and new-side finding distinct
    # even when they happen to report the exact same mismatched-record set
    # (Codex review, fresh evidence, round 2).
    if kind_value in _STRUCTURED_EVIDENCE_KIND_SLUGS:
        # A comma join is not injective when a C++ record name itself
        # contains a comma (common in template types, e.g.
        # "A<int,int>") -- mismatch sets ("A,B", "C") and ("A", "B,C")
        # would both join to "A,B,C", colliding two distinct evidence
        # sets (Codex review, fresh evidence). "\x1f" (unit separator,
        # the same delimiter the fallback `parts` join below uses) is
        # not a byte any real record name contains, so the join stays
        # unambiguous.
        evidence = "\x1f".join(sorted(change.affected_symbols or ()))
        side_match = _MISMATCH_SIDE_RE.match(change.description or "")
        side = side_match.group(1) if side_match else ""
        return f"evidence:{side}:{evidence}"
    canonicalize_this_kind = (
        canonicalize_values and kind_value in _TYPE_BEARING_DISCRIMINATOR_KINDS
    )
    raw_old_str = _stringify_change_value(change.old_value)
    raw_new_str = _stringify_change_value(change.new_value)
    old_str = raw_old_str
    new_str = raw_new_str
    if canonicalize_this_kind:
        if kind_value == "func_params_changed":
            # See _TYPE_BEARING_DISCRIMINATOR_KINDS's own comment: a
            # comma-joined parameter list needs per-parameter
            # canonicalization, not one whole-string call.
            old_str = _canonicalize_params_list(old_str)
            new_str = _canonicalize_params_list(new_str)
        elif kind_value == "atomic_qualifier_changed":
            # See canonicalize_atomic_slot's own docstring
            # (finding_identity_atomic.py): CastXML's bare "_Atomic"
            # sentinel and Clang's real wrapped spelling must collapse to
            # the same value, which plain canonicalize_type_name cannot do
            # on its own. Each side needs the OTHER side's raw spelling to
            # tell a redundant qualifier-only wrap apart from a compound
            # change that also altered the payload.
            old_str = canonicalize_atomic_slot(raw_old_str, raw_new_str)
            new_str = canonicalize_atomic_slot(raw_new_str, raw_old_str)
        else:
            old_str = canonicalize_type_name(old_str)
            new_str = canonicalize_type_name(new_str)
    parts = [kind_value, old_str, new_str]
    if include_description:
        desc = change.description or ""
        if canonicalize_this_kind:
            if kind_value in _DESCRIPTION_EMBEDS_VALUES_KINDS:
                # See _DESCRIPTION_EMBEDS_VALUES_KINDS's own comment:
                # canonicalize_type_name's anchored passes can't reach an
                # embedded type mid-sentence, so substitute the *known*
                # raw old/new substrings with their already-canonicalized
                # forms directly, rather than canonicalizing the sentence
                # as one opaque blob.
                #
                # Substitute the LONGER raw string first (Codex review,
                # fresh evidence): atomic_qualifier_changed's own
                # "qualifier added" direction embeds the unqualified raw
                # spelling verbatim *inside* the qualified one (e.g. raw_old
                # "struct Foo *" is a literal substring of raw_new
                # "_Atomic(struct Foo *)") -- replacing the shorter raw_old
                # first would also rewrite the copy embedded inside
                # raw_new's own span before raw_new's own replace ever runs,
                # so raw_new's exact text no longer appears in desc and its
                # replace becomes a silent no-op, leaving the qualified
                # side's stale, backend-specific spelling in the discriminator.
                for raw, canon in sorted(
                    ((raw_old_str, old_str), (raw_new_str, new_str)),
                    key=lambda pair: len(pair[0]),
                    reverse=True,
                ):
                    if raw:
                        desc = desc.replace(raw, canon)
            else:
                desc = canonicalize_type_name(desc)
        parts.append(desc)
    return "\x1f".join(parts)


def resolve_change_identity(
    change: Change, *, canonicalize_values: bool = False
) -> FindingIdentity:
    """Tiered identity for an already-emitted flat finding
    (:class:`~abicheck.checker_types.Change`).

    ``change.symbol`` doubles as "mangled name or type name" (see
    ``Change``'s docstring); mangled-name interpretation is therefore only
    attempted when :func:`_is_symbol_level_kind` recognizes the kind, never
    for a type-level change whose ``symbol`` merely happens to look like a
    mangling (Codex review).

    Unlike :func:`resolve_function_identity`/:func:`resolve_variable_identity`
    (which identify one *entity*, so a bare mangled name is already
    unambiguous), this identifies one *finding* -- two distinct findings
    routinely share a symbol. :func:`_change_discriminator` is folded into
    ``primary_id`` at every tier, including CANONICAL, mirroring
    ``reporter_markdown._finding_id``'s own established minimal
    disambiguating set (Codex review: a bare ``mangled:<symbol>`` canonical
    id would collapse unrelated findings, violating this module's dedup-key
    contract).

    ``canonicalize_values=False`` (the default) preserves this function's
    pre-existing, exact-value discriminator for its established callers
    (``diff_filtering.py``'s cross-detector dedup, ``aggregate_findings.py``,
    ``contract_evaluation.py``) -- none verified against a type-spelling-
    insensitive comparison. Pass ``True`` only for a genuinely cross-backend
    use (:func:`report_canonical_finding_id`), where
    :func:`_change_discriminator`'s own docstring explains why the raw
    value must not leak into a "backend-independent" identity.

    ``change.entity_id``, when set, folds in as an additional ``entity:``
    alias (ADR-063 Phase 2) -- additive, never ``primary_id``/tier.
    """
    kind_value = str(getattr(change.kind, "value", change.kind))
    is_batch = _is_batch_shaped_change(change, kind_value)
    # A batch-shaped finding's `symbol` is an arbitrary sample (the
    # alphabetically-first affected export), not a real entity this finding
    # is "about" -- using it for `qn`/aliases/the REDUCED-tier basis, not
    # only for CANONICAL mangled-name promotion, would still let the
    # identity change whenever the sampled export changes and could still
    # collide with an unrelated finding that genuinely owns that symbol
    # (Codex review: the CANONICAL-only guard left this same leak at the
    # NORMALIZED tier and in the `symbol`/`relsrc` aliases). `entity_symbol`
    # is `None` for a batch-shaped change so every downstream identity/alias
    # below naturally omits the sample instead of needing its own check.
    entity_symbol = None if is_batch else change.symbol
    # `_enrich_source_locations` runs on every Change and can populate
    # `qualified_name` by looking up `change.symbol` -- for a batch-shaped
    # change that symbol is the arbitrary sample, so an enrichment hit
    # would derive `qualified_name` from the sample too, leaking it right
    # back in even with `entity_symbol` cleared above (Codex review: caught
    # for the sibling `_diff_allocator_replacement` batch kinds, but the
    # same channel exists for every batch-shaped kind).
    qualified_name = None if is_batch else change.qualified_name
    # `_enrich_source_locations` also populates `source_location` from the
    # sampled export for a batch-shaped change (Codex review, round 3 on
    # this same leak channel) -- feeding `change.source_location` into
    # `rel`/the `relsrc:` alias/the REDUCED-tier synthetic basis below
    # would still make the supposedly library-level identity change
    # whenever the sampled export's file changes.
    # A recorded location reaches identity only root-relative: anchored at
    # its project layout, never the checkout prefix (design-hardening
    # Phase 3, F3); an absolute spelling with no layout anchor is dropped.
    source_location = (
        None
        if is_batch
        else RootRelativePath.from_project_layout(change.source_location)
    )
    # Same is_batch guard as the three fields above, defensively -- no
    # producer sets entity_id on a batch-shaped Change today.
    entity_id = None if is_batch else change.entity_id
    is_symbol_level = _is_symbol_level_kind(kind_value) and not is_batch
    # For a symbol-level change, `entity_symbol` (the raw exported name) is
    # the more tier-stable NORMALIZED-tier basis than `qualified_name` --
    # mirroring `resolve_symbol_identity`'s identical extern-C preference
    # for entity identity. `_enrich_source_locations` scope-qualifies a
    # namespaced extern "C" removal's `qualified_name` ("ns::foo") whenever a
    # rich snapshot has the matching declaration, while the equivalent L0/
    # ELF-only finding for the SAME event has no scope info to qualify with
    # at all (`symbol="foo"`). Preferring `qualified_name` here would give
    # the two `_EQUIVALENT_CHANGE_CATEGORIES`-collapsed findings (e.g. rich
    # FUNC_REMOVED vs. L0 FUNC_REMOVED_ELF_ONLY) different `sig:`-based
    # primary ids even after the discriminator itself collapses to one
    # shared category, defeating the collapse (Codex review).
    qn = (
        entity_symbol
        if is_symbol_level and entity_symbol
        else qualified_name or entity_symbol or ""
    )
    # A batch-shaped change's `description` embeds the same arbitrary
    # sample as `symbol` (Codex review, round 2 on this same finding) --
    # `entity_symbol` alone isn't enough to make the identity
    # sample-independent, since `discriminator` (used in `sig`, every
    # alias, and the REDUCED-tier synthetic basis) would still vary.
    discriminator = _change_discriminator(
        change,
        kind_value,
        include_description=not is_batch,
        canonicalize_values=canonicalize_values,
    )
    sig = f"sig:{qn}\x1f{discriminator}"
    rel = source_relative_identity(source_location, entity_symbol or "")

    real_mangled = None
    if is_symbol_level:
        real_mangled = normalize_mangled_name(change.symbol, qualified_name)

    # Every alias below is qualified with `discriminator`, matching `primary`/
    # `sig`: a bare `mangled:<x>`/`symbol:<x>`/`qualified:<x>` alias would
    # identify the *entity*, not this *finding* -- two distinct findings on
    # the same entity (e.g. a return-type and a param-type change on the
    # same function) would share every entity-scoped alias, and a future
    # alias-match reconciliation tier would wrongly pair them whenever each
    # side has only one candidate (Codex review).
    aliases: list[str] = []
    if real_mangled:
        aliases.append(f"mangled:{real_mangled}\x1f{discriminator}")
    if entity_symbol:
        aliases.append(f"symbol:{entity_symbol}\x1f{discriminator}")
    if qualified_name:
        aliases.append(f"qualified:{qualified_name}\x1f{discriminator}")
    aliases.append(sig)
    if source_location is not None:
        aliases.append(f"relsrc:{rel}\x1f{discriminator}")
    if entity_id is not None:
        # ADR-063 Phase 2: first real read of Change.entity_id. Additive
        # only -- EntityId.key isn't yet proven stable across releases
        # enough to replace what stored suppression rules key against.
        aliases.append(f"entity:{entity_id.key}\x1f{discriminator}")

    if real_mangled:
        primary = f"mangled:{real_mangled}\x1f{discriminator}"
        return FindingIdentity(primary, IDENTITY_TIER_CANONICAL, tuple(aliases))

    if qn:
        return FindingIdentity(sig, IDENTITY_TIER_NORMALIZED, tuple(aliases))

    basis = "\x1f".join(
        str(x) for x in (entity_symbol, discriminator, source_location) if x
    )
    digest = hashlib.sha256(f"synthetic\x00{basis}".encode()).hexdigest()[:32]
    synthetic = f"synthetic:sha256:{digest}"
    aliases.append(synthetic)
    return FindingIdentity(synthetic, IDENTITY_TIER_REDUCED, tuple(aliases))


# --------------------------------------------------------------------------
# ADR-049 Phase 2: the ambiguity-safe old/new join.
# --------------------------------------------------------------------------

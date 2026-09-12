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

"""The three independent surface facts ``Visibility`` used to conflate.

``model/vocabulary.py``'s :class:`Visibility` answers one question with
three values (``PUBLIC``/``HIDDEN``/``ELF_ONLY``), and that single answer
is really three different observations folded together:

(a) **declared in the available headers** — a header the run actually
    parsed carries a declaration of this entity;
(b) **member of the promised public contract** — that declaration belongs
    to the surface the maintainer promises, per the run's scope/contract
    selection (``-H``/``--public-header-dir``/``--contract``);
(c) **exported by the binary** — a corresponding symbol is present in the
    artifact's dynamic export table.

They are independent. A public inline function is (a) yes, (b) yes,
(c) no. A version-script change that stops exporting an otherwise
unchanged declaration flips (c) alone. An export-table entry in a
headerless dump is (c) yes with (a) and (b) *unknown* — not "no".

``Visibility`` cannot express any of that: ``PUBLIC`` means "(a) and (c)",
``ELF_ONLY`` means "(c) but not (a)", ``HIDDEN`` means "not (c)" while
saying nothing reliable about (a) — and none of the three has a state for
"we did not look". Detectors then wrote ``visibility != Visibility.PUBLIC``
as if it answered (a) or (b), so a lost export read as a removed source
declaration (see the ``Visibility.PUBLIC`` entry in
``docs/contribute/known-gaps.md``).

This module is the split. Each fact is a real :class:`Fact` on
``Function``/``Variable`` — ``declared_in_headers_fact``,
``in_public_contract_fact``, ``binary_exported_fact`` — so "unknown" is
representable (``NOT_COLLECTED``/``UNSUPPORTED``/``FAILED``) and the
``fact-detector-misuse`` AI-readiness gate applies to every reader. There
is deliberately **no** fourth merged boolean: the accessors below are the
only supported way to read these, and each one names the question it
answers.

Legacy bridge: a declaration whose ``*_fact`` fields are unset (a
pre-schema-v46 snapshot, or a ``Function(...)`` a test or the typed API
constructed by hand) still carries the legacy ``visibility`` value, and
every accessor here derives its answer from it as a last resort, marked
``PARTIAL`` with a ``derived-from-legacy-visibility`` diagnostic so the
weaker provenance stays visible. The derivation is exactly what the old
enum could support and nothing more — in particular ``PUBLIC`` derives
*unknown* for (a) unless the declaration also carries header provenance,
because "exported" was never evidence of a header declaration.
"""

from __future__ import annotations

from typing import Protocol

from .availability import FactStatus
from .fact import Fact
from .vocabulary import ScopeOrigin, Visibility

__all__ = [
    "DERIVED_FROM_LEGACY",
    "SurfaceFactBearing",
    "binary_exported",
    "declaration_confirmed_absent",
    "declared_in_headers",
    "headers_discarded_surface_facts",
    "in_public_contract",
    "in_public_surface",
    "in_source_declaration_index",
    "is_abi_visible",
    "is_binary_exported",
    "is_confirmed_false",
    "is_confirmed_true",
    "is_export_confirmed_absent",
    "is_export_table_only_record",
    "is_header_declared",
    "is_legacy_derived",
    "is_public_export",
    "is_public_export",
    "is_unknown",
    "public_header_contract_fact",
    "surface_fact_summary",
]

#: Diagnostic stamped on every fact this module derives from the legacy
#: ``Visibility`` enum rather than reading from a producer-set field.
DERIVED_FROM_LEGACY = "derived-from-legacy-visibility"


class SurfaceFactBearing(Protocol):
    """The structural surface ``Function`` and ``Variable`` share here."""

    visibility: Visibility
    source_header: str | None
    source_location: str | None
    origin: ScopeOrigin
    declared_in_headers_fact: Fact[bool] | None
    in_public_contract_fact: Fact[bool] | None
    binary_exported_fact: Fact[bool] | None


# ---------------------------------------------------------------------------
# Status predicates over one Fact[bool]
# ---------------------------------------------------------------------------


def is_confirmed_true(fact: Fact[bool]) -> bool:
    """Usable evidence (``PRESENT``/``PARTIAL``) saying *yes*."""
    return fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL) and (
        fact.value is True
    )


def is_confirmed_false(fact: Fact[bool]) -> bool:
    """Usable evidence saying *no* — never "we did not look"."""
    return fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL) and (
        fact.value is False
    )


def is_unknown(fact: Fact[bool]) -> bool:
    """Neither of the above: no usable evidence either way."""
    return not is_confirmed_true(fact) and not is_confirmed_false(fact)


# ---------------------------------------------------------------------------
# The three facts
# ---------------------------------------------------------------------------


def _legacy_header_evidence(decl: SurfaceFactBearing) -> bool:
    """Whether the legacy record carries any header provenance at all."""
    return bool(
        getattr(decl, "source_header", None) or getattr(decl, "source_location", None)
    )


def declared_in_headers(decl: SurfaceFactBearing) -> Fact[bool]:
    """(a) Does a header this run parsed declare *decl*?

    Never answers "no" merely because nothing exported it: a headerless
    (L0/L1) snapshot answers *unknown*, so "public declaration not
    established" can never be misread as "no declaration".
    """
    stored: Fact[bool] | None = getattr(decl, "declared_in_headers_fact", None)
    if stored is not None:
        return stored
    if _legacy_header_evidence(decl):
        return Fact.partial(True, DERIVED_FROM_LEGACY)
    # No recorded header provenance. The legacy enum cannot close the gap
    # for *any* of its members, `ELF_ONLY` included: both header-AST
    # backends assign `ELF_ONLY` to a declaration they parsed **out of a
    # header** whose symbol turned up in `.symtab` rather than the dynamic
    # table (`extract/headers/castxml/location.visibility` and its clang
    # sibling), so reading it as "not declared in any header" would state a
    # negative about a declaration a header parse produced (Codex review,
    # P2). The record's own header provenance above is the discriminator
    # that actually answers this question -- a synthesized export-table
    # entry (`extract/export_symbol_identity`) carries none, a parsed
    # declaration does -- and it is consulted for every member alike.
    #
    # Which member it was remains a real question, just a *different* one:
    # `is_export_table_only_record` answers it, and keeps the ELF-only
    # removal kind and the stub-record consumers working off the enum.
    return Fact.not_collected(DERIVED_FROM_LEGACY)


def in_public_contract(decl: SurfaceFactBearing) -> Fact[bool]:
    """(b) Is *decl* part of the promised public contract for this run?"""
    stored: Fact[bool] | None = getattr(decl, "in_public_contract_fact", None)
    if stored is not None:
        return stored
    vis = getattr(decl, "visibility", Visibility.PUBLIC)
    if vis is Visibility.PUBLIC:
        return Fact.partial(True, DERIVED_FROM_LEGACY)
    return Fact.partial(False, DERIVED_FROM_LEGACY)


def binary_exported(decl: SurfaceFactBearing) -> Fact[bool]:
    """(c) Does the artifact's export table carry a symbol for *decl*?"""
    stored: Fact[bool] | None = getattr(decl, "binary_exported_fact", None)
    if stored is not None:
        return stored
    vis = getattr(decl, "visibility", Visibility.PUBLIC)
    if vis is Visibility.HIDDEN:
        return Fact.partial(False, DERIVED_FROM_LEGACY)
    return Fact.partial(True, DERIVED_FROM_LEGACY)


# ---------------------------------------------------------------------------
# Question-shaped accessors for consumers
# ---------------------------------------------------------------------------


def is_header_declared(decl: SurfaceFactBearing) -> bool:
    """Confirmed (a). Unknown reads as ``False`` — callers wanting the
    "not proven absent" reading use :func:`in_source_declaration_index`."""
    return is_confirmed_true(declared_in_headers(decl))


def declaration_confirmed_absent(decl: SurfaceFactBearing) -> bool:
    """Confirmed *not* (a): a header parse ran and accounted for this
    entity without finding a declaration (the old ``ELF_ONLY``)."""
    return is_confirmed_false(declared_in_headers(decl))


def is_binary_exported(decl: SurfaceFactBearing) -> bool:
    """Confirmed (c)."""
    return is_confirmed_true(binary_exported(decl))


def is_export_confirmed_absent(decl: SurfaceFactBearing) -> bool:
    """Confirmed *not* (c) — an export table was read and lacks it."""
    return is_confirmed_false(binary_exported(decl))


def in_public_surface(decl: SurfaceFactBearing) -> bool:
    """The public-API-membership question the old ``visibility ==
    Visibility.PUBLIC`` filters were *trying* to ask.

    Answers from (b) whenever (b) is established, and only falls back to
    (c) — then to (a) for a header-only snapshot with no binary at all —
    when it is not. The fallback order is what keeps every pre-split
    snapshot's behaviour identical: derived (b) reproduces
    ``visibility == Visibility.PUBLIC`` exactly.
    """
    contract = in_public_contract(decl)
    if is_confirmed_true(contract):
        return True
    if is_confirmed_false(contract):
        return False
    exported = binary_exported(decl)
    if is_confirmed_true(exported):
        return True
    if is_confirmed_false(exported):
        return False
    return is_confirmed_true(declared_in_headers(decl))


def is_export_table_only_record(decl: SurfaceFactBearing) -> bool:
    """Whether this record was *produced* from an export table alone — the
    one question the legacy :class:`Visibility` answers and these three
    facts deliberately do not.

    ``ELF_ONLY`` carries two different things, and only one of them is a
    fact about the entity. "Not declared in the available headers" is fact
    (a), and it is split out above. But it *also* says which producer built
    the record, and therefore how much the record contains at all: an
    export-table stub has a ``"?"`` return type, no parameters, and a raw
    mangled spelling for its name, because there was no header AST to
    source any of that from. Several consumers ask that second question —
    which ``ChangeKind`` a removal gets (``FUNC_REMOVED_ELF_ONLY`` is the
    weaker-evidence variant), whether a name needs demangling for display,
    whether a signature is rich enough to cross-check, whether ``origin``
    is ``EXPORT_ONLY`` — and none of them is answered by (a).

    A DWARF-derived record is the case that proves it: its fact (a) is
    *unknown* too (debug info is not header evidence), yet it is emphatically
    not an export-table stub — it has a real signature, a source location
    and a demangled name. Reading "(a) is not established" as "export-table
    only" therefore misfiles every DWARF declaration, which is exactly what
    an earlier revision of this split did (it flipped
    ``FUNC_REMOVED_ELF_ONLY`` to ``FUNC_REMOVED`` for the headerless
    binary-to-binary comparison ``catalog/cases/
    case97_api_depends_on_consumer_env`` pins).

    So the enum stays *necessary* here, on purpose — it is the only thing
    that names the producer. But it is not *sufficient*, which an earlier
    revision of this function got wrong: both header-AST backends assign
    ``ELF_ONLY`` to a declaration they parsed out of a header whose symbol
    turned up in ``.symtab`` rather than the dynamic table
    (``extract/headers/castxml/location.visibility`` and its clang sibling).
    That record is not a stub at all — it has a real return type, real
    parameters and a demangled name — so classifying it as one dropped a
    genuine source declaration out of ``in_source_declaration_index``, made
    ``bundle_signature_evidence`` reject a fully parsed signature, and could
    mislabel its removal ``FUNC_REMOVED_ELF_ONLY`` (Codex review, P2).

    Fact (a) is exactly the evidence that separates the two, and it is why
    that fact had to stop being derived from this enum before this could be
    fixed: a header-AST producer affirms (a), while a synthesized
    export-table entry leaves it unknown. So a stub is ``ELF_ONLY`` **and**
    not header-declared. Still not a fourth boolean merging the three facts
    — it answers a different question from all three, and it consults just
    one of them to rule out the one producer the enum cannot distinguish.
    The DWARF case above is untouched: its (a) is unknown, but it is not
    ``ELF_ONLY``, so the first clause already excludes it.
    """
    if getattr(decl, "visibility", None) is not Visibility.ELF_ONLY:
        return False
    return not is_header_declared(decl)


def is_legacy_derived(fact: Fact[bool]) -> bool:
    """Whether *fact* came from this module's legacy ``Visibility`` bridge
    rather than from a producer that observed it.

    A consumer that has its own, sharper reading of the old enum (see
    ``bundle_signature_evidence._was_exported``, which knows that
    ``ELF_ONLY`` means two different things depending on how the snapshot
    was dumped) uses this to keep that reading for pre-split snapshots
    while deferring to the real fact whenever one exists.
    """
    return DERIVED_FROM_LEGACY in fact.diagnostics


def is_abi_visible(decl: SurfaceFactBearing) -> bool:
    """Whether *decl* participates in the *binary* contract at all — it is
    exported, or it is part of the promised public surface.

    The union the old ``visibility in (PUBLIC, ELF_ONLY)`` filters spelled
    out: an entity is ABI-visible if a consumer can bind to it (c) or if
    the maintainer promised it (b). Unlike those filters, this one reaches
    the right answer for the combination the enum could not hold — a
    promised, unexported inline declaration is (b) without (c).
    """
    return is_binary_exported(decl) or in_public_surface(decl)


def is_public_export(decl: SurfaceFactBearing) -> bool:
    """The *intersection* of (b) and (c): promised **and** confirmed exported.

    The reading the old ``visibility is Visibility.PUBLIC`` test had, as
    opposed to :func:`is_abi_visible`'s union of the same two facts. A
    detector wants this one whenever its subject is a symbol a consumer can
    actually bind to *and* that the library promises -- so both the
    export-table-only entry (fails (b)) and the promised-but-unexported
    inline declaration (fails (c)) are excluded, exactly as the single enum
    member excluded ``ELF_ONLY`` and ``HIDDEN`` respectively.

    That equivalence is the point: it keeps every pre-split snapshot's
    answer identical, because the legacy bridge derives (c) true for
    ``PUBLIC``/``ELF_ONLY`` and (b) true for ``PUBLIC`` alone. Reaching for
    :func:`in_public_surface` where the old test said ``is PUBLIC`` is the
    mistake this function exists to make unnecessary -- it silently widens
    the subject to declarations the artifact never exported (Codex review,
    P2, on ``diff_templates``' instantiation-survival index and
    ``surface_graph``'s export-named counters).
    """
    return in_public_surface(decl) and is_binary_exported(decl)


def in_source_declaration_index(decl: SurfaceFactBearing) -> bool:
    """Whether *decl* belongs in an index of **source declarations** — the
    population a source-API *removal* event is computed against.

    Deliberately blind to (c): an export lost to a version-script or
    ``-fvisibility`` change does not remove a declaration, and keying this
    population off export evidence is exactly what made one read as the
    other. A declaration drops out on a *confirmed* negative — the header
    parse accounted for it and found nothing (a), or the contract ruled it
    out (b) — and otherwise unknown keeps it in, symmetrically on both
    sides, so weaker evidence narrows the conclusion instead of
    manufacturing a removal.

    An export-table-only record is the one exclusion that is not about
    either fact: it has no source declaration *at all* to belong to a
    source-declaration population, by construction — no header AST produced
    it, its name is a raw mangled spelling and its signature is ``"?"``.
    Admitting it (its fact (a) is unknown, not false, on a headerless dump)
    would feed raw export-table entries to source-level detectors, which is
    how a fresh symbols-only comparison of ``_ZN3lib2v13fooEv`` against
    ``_ZN3lib2v23fooEv`` started reporting an inline-namespace version bump
    with no header evidence behind it (Codex review, P2). See
    :func:`is_export_table_only_record` for why that question is not one of
    the three facts.
    """
    if is_confirmed_false(in_public_contract(decl)):
        return False
    if declaration_confirmed_absent(decl) or is_export_table_only_record(decl):
        return False
    # Past the confirmed negatives, membership needs *affirmative* evidence:
    # a header that declares it, or a contract that promises it. "Both
    # unknown" is not a source declaration -- it is no evidence at all, and
    # admitting it let a debug-info-only record with neither header nor
    # export evidence reach source-level detectors (two unexported internal
    # `lib::v1::foo`/`lib::v2::foo` functions reporting an inline-namespace
    # version bump; Codex review, P2).
    #
    # This is where the split's asymmetry earns its keep, and it is why the
    # rule is not simply "not a confirmed negative": the *export* fact still
    # never appears here, so the pair of sides in the reported bug -- header
    # evidence on both, export evidence on one -- stays symmetric and
    # produces no removal, while a record that never had source evidence in
    # the first place stays out.
    return is_header_declared(decl) or is_confirmed_true(in_public_contract(decl))


def public_header_contract_fact(
    decl: SurfaceFactBearing, origin: ScopeOrigin
) -> Fact[bool] | None:
    """The scope-aware half of fact (b), or ``None`` to leave it alone.

    A declaration whose defining header is in the caller-supplied public set
    is positive, scope-aware evidence of public-contract membership --
    stronger than the export lookup a header backend has to fall back on
    and, crucially, independent of whether the artifact exports it (an
    unexported public inline function is an ordinary member of the promised
    surface).

    Deliberately only ever *adds a positive*. A non-public origin returns
    ``None`` rather than a confirmed ``False``: origin-based exclusion is
    already its own separately-controlled scoping decision
    (``--scope-public-headers``, ``dumper_scoping``), and asserting the
    negative here would apply it a second time, silently, to every consumer
    of this fact. Called by ``provenance.tag_provenance``, which is the one
    pass that knows a run's real scope selection.
    """
    if origin is not ScopeOrigin.PUBLIC_HEADER:
        return None
    if is_confirmed_true(in_public_contract(decl)):
        return None
    return Fact.present(True, "declared in a supplied public header")


def surface_fact_summary(decl: SurfaceFactBearing) -> dict[str, str]:
    """The three facts as report-ready ``"true"``/``"false"``/``"unknown"``
    strings, keyed by the question each answers.

    One owner for the rendering so every format (JSON, SARIF, Markdown,
    HTML, text) states the same three values and the same explicit
    ``"unknown"`` — never an omitted key a reader could take for "no".
    """
    return {
        "declared_in_headers": _tri(declared_in_headers(decl)),
        "in_public_contract": _tri(in_public_contract(decl)),
        "binary_exported": _tri(binary_exported(decl)),
    }


def _tri(fact: Fact[bool]) -> str:
    if is_confirmed_true(fact):
        return "true"
    if is_confirmed_false(fact):
        return "false"
    return "unknown"


def headers_discarded_surface_facts(*, reason: str) -> dict[str, Fact[bool]]:
    """Facts for a declaration whose header evidence was *deliberately
    discarded* — an evidence-depth projection down to L0/L1.

    Both header-derived facts become unknown again, carrying *reason*:
    the projection removed the evidence, so continuing to report the
    conclusion it supported would be a claim the projected snapshot can
    no longer back. The export fact survives — it never came from a
    header.
    """
    return {
        "declared_in_headers_fact": Fact.not_collected(reason),
        "in_public_contract_fact": Fact.not_collected(reason),
    }

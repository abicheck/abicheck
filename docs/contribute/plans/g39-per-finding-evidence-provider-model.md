---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G39 — Per-finding evidence-provider model

**Status:** Proposed; not started.

**Routing note (ADR-061):** every implementation location this plan names is
qualified against the root `AGENTS.md`'s "Task routing and dependency
direction" table (`AGENTS.md`, sourced from
[ADR-061](../adr/061-responsibility-package-architecture.md)) — route new
code to the responsibility-package owner current at implementation time, not
to the flat legacy module this plan cites for orientation. See "Design" and
"Files & surfaces" below for the specific mapping.

Split out of [G38](g38-bundle-facts-model-and-multibuild-comparability.md)'s
"Out of scope" section and the root `AGENTS.md`'s "Evidence-provider model"
known-gap entry — both explicitly deferred this as its own, separately-scoped
project rather than a side effect of either G38 or the layout-finding
investigation that first raised it. This document is that separate scope: a
design, not an implementation. No code changes ship with this plan.

## Problem

`checker_types.Change` carries two provenance-adjacent fields today, and
neither answers the question this plan is about:

- `evidence_category: str | None` (ADR-033 D9) — coarse, two-valued
  (`"build_context"` / `"source_only"`), set only for L3+ findings, and its
  job is metrics bucketing (retained-finding counts per evidence bucket), not
  per-finding provenance.
- `contract_evidence_refs: tuple[str, ...] | None` (ADR-049) — real
  provenance, but scoped narrowly: populated only when a run is given
  `--contract`, and its entries reference *contract-domain evidence
  providers* (`public_header`, `export_table`, the `post_manifest`/
  `forced_public_symbols` overlays) — i.e. "what evidence justified this
  finding's *relevance* classification", not "which extractor/tier
  *produced* this finding's *content*". A run without `--contract` — the
  overwhelming majority of invocations — carries `None` here regardless of
  how the finding was actually produced.

Neither field can answer: for one specific `Change`, was it produced from L0
symbol-table evidence alone, corroborated against L1 DWARF, derived from an
L2 castxml or clang header parse, or does it rest on L3 build evidence? A
consumer — a policy file, a report reader, a future confidence-scoring
step — cannot distinguish "this `TYPE_SIZE_CHANGED` finding is grounded in
DWARF-corroborated layout" from "this one is a castxml-only computation with
no DWARF to check it against" without re-deriving that fact from the
snapshot's own `dwarf_layout_coherence`/`ast_frontend` fields and guessing
which one applied to *this* finding specifically — fragile, and already
shown to be wrong at least once (see `AGENTS.md`'s "Findings emitted from
absent evidence" entry: the vtable false positive traced to exactly this
class of missing per-finding evidence signal, closed there only for one
`ChangeKind` via a narrow, kind-specific guard rather than a general
mechanism).

**What was already investigated and found not to generalize the way a first
reading suggests** (`AGENTS.md`'s own entry, restated here for this plan's
own record so it isn't re-litigated): the risk that a header-only
(castxml/clang) finding could read as artifact-proven merely because *some
other* part of the same report had binary evidence does not reproduce for
layout findings specifically, because `RecordType.size_bits`/
`alignment_bits` are either DWARF-backfilled-and-corroborated (direct-clang)
or self-consistently computed by castxml's own bundled compiler (a prior,
intentional design decision, not a gap). That investigation is *evidence the
per-kind story is subtler than "L2 findings are always weaker than L1/L0
ones"* — which is itself an argument for a real per-finding model over a
per-report or per-kind heuristic: the correct provenance genuinely varies
finding-by-finding within one `ChangeKind`, not just kind-by-kind.

## Goal & acceptance criteria

A `Change` can name, without re-deriving it, which evidence tier(s) actually
produced and corroborated it — precise enough that a consumer no longer has
to guess from surrounding snapshot metadata, general enough that it doesn't
need a bespoke field per `ChangeKind` the way the vtable guard's
`vtable_covers_unverifiable_layout_gap` field does today.

### Phase 0 — data model (S)

**Implementation location (ADR-061):** `Change` is a shared domain value
used across every later stage (comparison, policy, reporting) — the root
`AGENTS.md`'s routing table places exactly this class of change under
`model/` ("Add an ABI entity/value shared across stages"). At the time this
plan was written, `Change` still lives in `checker_types.py` and `model/`
has not yet absorbed it (ADR-061 Phases 2-4, which include this migration,
are "in progress," per the ADR's own status line). This plan does **not**
perform that migration as a side effect: add the field wherever `Change` is
canonically defined *at implementation time* — `model/findings.py` if that
migration has already landed by then, `checker_types.py` otherwise. Landing
this field is not itself a reason to force the `model/` migration forward.

Add one new, additive field to `Change`, following the existing
`contract_evidence_refs` precedent exactly (flat `tuple[str, ...] | None`,
`field(default=None, kw_only=True)`): the field itself stays a bare
`tuple[str, ...]` of validated strings, never a typed enum — see "The
finalized vocabulary needs a single code-level owner" below for the
registry that is the single source of truth for which strings are valid
(a frozenset/enum-of-strings, following this codebase's own established
pattern for a stable, checked vocabulary — not a second, enum-typed field
competing with it):

```python
evidence_provenance: tuple[str, ...] | None = field(default=None, kw_only=True)
```

Each entry is a stable, namespaced provider-id string, not a bespoke enum —
mirroring `contract_evidence_refs`' own string-ref shape rather than
inventing a second typed vocabulary next to `contract_relevance_types.py`'s
existing one. Provisional vocabulary (finalized in Phase 1, once real call
sites are wired and the actual granularity needed is known — do **not**
freeze this before that):

| Prefix | Meaning | Existing analogue |
|---|---|---|
| `l0:elf_symtab` | ELF `.dynsym`/export table alone | `evidence_tiers.py`'s L0 |
| `l0:pe_export_table` | PE export directory (`_parse_pe_exports()`) — the `PROVIDER_BINARY_EXPORTS` platform-selectable counterpart to `l0:elf_symtab` for a PE snapshot; see the "Multi-provider `evidence_provenance`" discussion below for why this platform branch exists | L0 |
| `l0:macho_exports` | Mach-O's merged export list (`MachoMetadata.exports` — classic symbol table plus any trie-only additions, with no per-entry source marker; a generic label rather than a trie-specific claim the data model can't back per finding — see the discussion below) | L0 |
| `l1:dwarf` | DWARF debug info (ELF) | L1 |
| `l1:pdb` | Windows PDB debug info (PE snapshots) | L1 |
| `l1:btf` | Linux kernel BTF debug info (ELF snapshots) | L1 |
| `l1:ctf` | Linux kernel CTF debug info (ELF snapshots) | L1 |
| `l2:castxml` / `l2:clang` | Header-AST backend, named specifically (not just "L2") since the two backends have measurably different fact completeness — see [header-backend-capabilities.md](../../reference/header-backend-capabilities.md) | L2 |
| `l3:build_context` | ADR-039 build-context collector / L3→L2 fold | L3 |
| `l4:source_replay` | L4 source-ABI replay | L4 |
| `l5:source_graph` | L5 source/consumer graph | L5 |
| `corroborated:dwarf` | An L2 fact that was cross-checked against DWARF (`dwarf_layout_coherence`-style backfill) | — new |

`None` means "not yet computed for this finding" (every pre-Phase-1 call
site), distinguished from `()` meaning "computed, and genuinely no provider
claims this finding" (should not occur in practice once Phase 1 completes,
but the distinction matters for the completeness gate in Phase 3 the same
way it already matters for `contract_evidence_refs`).

**`l1:pdb`/`l1:btf`/`l1:ctf` name a real gap, not just a missing table row
(Codex review).** A PE snapshot populated from PDB, or an ELF snapshot using
BTF/CTF instead of (or alongside) DWARF, is normalized into the same
`AbiSnapshot` type records DWARF populates — `pdb_metadata.py`/
`btf_metadata.py`/`ctf_metadata.py` all feed the same `RecordType`/
`Function`/`Variable` model DWARF does — and nothing on the model records
*which* L1 producer actually resolved a given fact once it's merged in.
Stamping `l1:dwarf` on a PDB- or BTF/CTF-derived finding would be a false
claim; leaving `evidence_provenance` uncomputed for every such finding is a
real completeness gap Phase 3's gate would have to special-case. Retaining
enough producer identity to select the correct one of these four L1 ids
per finding — likely a small, additive per-record or per-snapshot marker,
in the same spirit as the layout-provenance model gap Phase 1 item 2 above
already identifies for DWARF backfill — is therefore a prerequisite this
phase's Phase 1 wiring must resolve *before* wiring detectors that could
draw on PDB/BTF/CTF-derived snapshots, not something to defer to the
detector-wiring step itself.

**The finalized vocabulary needs a single code-level owner, not just this
table (Codex review).** The field's *type* stays a bare `tuple[str, ...]` —
that part of the design is settled above and is not reopened here — but the
provider-id strings themselves (every `l0:`/`l1:`/`l2:`/`l3:`/`l4:`/`l5:`/
`corroborated:` combination this plan documents, expanded across Phase 1's
call-site work into the full `searched:`/side-prefixed forms items 1 and 3
below establish) must be defined exactly once in real code — a frozenset or
enum of validated strings, following this codebase's own established
pattern for a stable, checked vocabulary (`ChangeKind` in
`checker_policy.py`, validated via `change_registry.py`'s completeness
assertion; `contract_relevance_types.py`'s reserved reason-code registry) —
rather than left as free-text literals a detector call site hand-writes to
match this table by eye. Whatever validates a report's shape once this
field is emitted (a schema check, a completeness gate, a test) must check
provenance strings against that same registry, not accept an arbitrary
string — the goal is that a typo'd or independently-invented tag at a new
call site (`l2:serched:clang` for `l2:searched:clang`, say) fails a check
instead of silently shipping as an unrecognized, unmatched value no
consumer can ever key off. This plan does not pick the registry's exact
module or type here — Phase 1 is where real call sites get wired and the
actual granularity is known (per the paragraph above), and the registry
should be finalized alongside that work, not designed speculatively ahead
of it — but landing Phase 1 without *some* single source of truth for the
string set repeats the exact drift this table's own multi-round Codex
history (the `searched:`/side-prefix corrections in items 1 and 3 below)
shows this vocabulary is prone to.

**A flat, unqualified tuple cannot distinguish which *side* a provider
applies to, and that distinction is load-bearing (Codex review, fresh
evidence).** A changed fact's `Change` spans two `AbiSnapshot`s (old and
new), and their evidence can genuinely differ — an old-side `RecordType`
DWARF-corroborated per this plan's own layout-finding investigation, paired
with a new-side one that is not (a real, not hypothetical, shape: a header
changed in a way DWARF debug info for the *new* build doesn't yet cover,
or vice versa). An unordered union like `("l2:clang", "corroborated:dwarf")`
cannot say *which* side the corroboration belongs to — a consumer reading
it would have no way to avoid treating the whole finding as corroborated
when only one side actually is, silently defeating the confidence
distinction this field exists to provide. **Resolution: every entry in the
vocabulary above is side-scoped by a mandatory `old:`/`new:`/`both:` prefix
layer** ahead of the tier prefix — e.g. `old:l2:clang`, `new:corroborated:dwarf`,
or `both:l0:elf_symtab` for a provider whose evidence is genuinely identical
on both sides (the common case for e.g. a symbol-table-only detector, where
"old" and "new" mean the same extraction mechanism ran on each side, not
that the *content* matched). This is additive to the table's existing
prefixes, not a redesign of them — `evidence_provenance = ("both:l0:elf_symtab",)`
is the typical L0/L1 slice-1 value, and only the harder L2+ slices need the
`old:`/`new:` split in practice. Phase 1's own wiring for each slice states,
per detector, whether `both:` suffices or the per-side split is required —
recorded there rather than guessed here, since (mirroring this plan's own
"XL, phased" discipline) the answer genuinely varies by detector and
shouldn't be frozen before real call sites are examined.

**The `old:`/`new:`/`both:` vocabulary is mandatory-prefix but not
exhaustive over every finding shape -- a fourth, `current:` scope is
needed for genuinely unary findings (Codex review, fresh evidence,
confirmed by reading real call sites rather than assumed from the
prefix table alone).** `buildsource/crosscheck.py`'s
`_check_header_build_context_mismatch`/`_check_public_to_internal_
dependency` (and their sibling `_check_*` functions in that module) each
take exactly one `snapshot: AbiSnapshot` parameter -- there is no old/new
pair at all, so neither `old:`/`new:` (which name a *side* of a
comparison) nor `both:` (explicitly defined above as "the mechanism runs
on both sides" -- itself presupposing two sides) can truthfully describe
where this evidence came from. Forcing one of the three onto a
single-snapshot check would either mislabel it (claiming a `both:` a
consumer would read as "ran on both sides of a comparison," when only one
snapshot was ever examined) or violate the "mandatory prefix" contract
Phase 1 otherwise enforces. Resolution: a fourth scope, `current:`,
reserved specifically for a `_change()`/`Change(...)` construction site
whose function signature takes one `AbiSnapshot`, not a
`(old, new)` pair -- e.g. `current:l5:source_graph` for
`_check_public_to_internal_dependency`'s L5 reachability evidence. Phase
1's own per-call-site wiring decides, from the function signature at each
site, whether `old:`/`new:`/`both:` or `current:` applies -- the same
"recorded there rather than guessed here" discipline the paragraph above
already establishes for the `both:`-suffices question.

### Phase 1 — wire the finding-construction call sites (XL, phased itself)

**No hand-copied count — this plan has already gotten one wrong twice
(Codex review, three rounds; per `AGENTS.md`'s own "don't hand-copy a
table, count, or version number that already has a fact owner elsewhere"
rule, which this section now follows instead of fighting).** An earlier
draft cited "~45 `Change(...)` construction sites"; a later revision
"corrected" that to "about 14 sites" plus "~350" `make_change()` calls —
also wrong, confirmed by a fresh `git grep -n "Change(" -- 'abicheck/**/*.py'`
at implementation time finding several dozen direct-construction sites in
`buildsource/` alone (`build_diff.py`, `crosscheck_base.py`,
`evidence_policy.py`, `graph_reconcile.py`, `source_diff.py`,
`source_graph_findings.py`, ...), none of which the prior revision's file
list named. A hand-copied count in a plan document goes stale the moment
any PR adds, removes, or refactors a detector, and this plan's own history
is now direct proof of that. **Derive the real inventory at implementation
time instead of trusting any number written here — and search
recursively, not the flat `abicheck/*.py`/`abicheck/buildsource/*.py`
globs an earlier revision of this section used.** Those two glob patterns
only match one directory level each (`git grep`'s pathspec `*` does not
cross a `/`), so as of ADR-061's incremental `compare/`/`workflows/`
package migration (see "Implementation location" above) they already miss
a real, present-day construction site — `abicheck/impact/use_case_impact.py`
— and will miss every detector `compare/detectors/{symbols,types,cpp,
platform,build,source}.py` eventually migrates too. Use
`git grep -n "Change(" -- 'abicheck/**/*.py'` (recursive: covers the
flat `diff_*.py`/`buildsource/*.py` modules, any migrated `compare/`/
`workflows/` package, and every other first-party subtree alike),
excluding `tests/` (a separate tree entirely, not matched by this
pathspec), `checker_types.py`'s own `class Change` definition, and
`diff_helpers.py`'s own factory body, for direct constructions; and
`git grep -n "make_change(" -- 'abicheck/**/*.py'` for factory calls.

**A third category this grep pair does not catch at all (Codex review,
verified against the code): `bundle_models.BundleFinding` construction
sites.** `BundleFinding` (`bundle_models.py`) is a separate dataclass, not
a `Change` — its own `to_change()` method lowers it into a `Change` only at
report time — so a bare `git grep -n "Change("` never matches the literal
text `"BundleFinding("` at all (the substring `Change(` does not occur in
it), even though every `BundleFinding` becomes a real, user-visible
`Change` once `to_change()` runs. Search separately with
`git grep -n "BundleFinding(" -- 'abicheck/**/*.py'`, excluding
`bundle_models.py`'s own dataclass definition. The real construction sites
are `bundle.py`, `bundle_signature_evidence.py`, `bundle_multibuild.py`,
and `product_baseline.py`. This is not merely a naming gap in the
inventory: several `BundleFinding` instances combine evidence
`to_change()` cannot see by the time it runs. `bundle.py`'s
`BUNDLE_INTRA_TYPE_CHANGED` finding, for example, is built from a
provider library's own per-library `Change` (`diff.changes`, itself
already covered by the `Change(...)`/`make_change(...)` inventory above)
*plus* a second, independent piece of evidence gathered right there —
scanning a sibling library's own ELF symbol table
(`sib_meta.symbols`/`_is_public_surface_symbol`) to decide whether the
provider's type change is reachable from that consumer's public surface.
`bundle_signature_evidence.py`'s `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED`
finding is a different shape again — it has no participating per-library
`Change` at all, only a direct old/new-snapshot signature-evidence-
sufficiency check (`_symbol_evidence_sufficient`) on each side. **This one
cannot be derived as "whichever snapshot evidence the finding rests on" the
way that phrase reads for the other three sites (Codex review, verified
against `_symbol_evidence_sufficient`'s own real body): the finding fires
precisely when that check returns `False`, and it returns `False` for two
evidentiarily distinct reasons that a single positive provider tag cannot
honestly collapse — (a) the symbol is absent from `function_map`/
`variable_map` entirely, or carries `Visibility.ELF_ONLY` (no corroborating
declaration exists at all), versus (b) a declaration is present but a
specific fact within it is unresolved (`_type_spelling_is_unresolved` on
the return/variable/parameter types, or `is_variadic`/`contract_attributes`
left at their tri-state `None`).** Naming one concrete backend as "the
provider" for this finding would claim a positive fact where the finding's
entire premise is that the fact could not be confirmed. The correct model
is the same side-prefixed `<side>:<tier>:searched:<backend>` vocabulary
item 3/item 5 above already establish for a negative result — `old:`/
`new:`/`both:` naming which side(s) failed the sufficiency check, `:tier:`
and `:backend` naming what evidence was actually consulted, and
`:searched:` recording that the check ran and did not confirm
sufficiency — never a bare positive tag standing in for "insufficient."
This does not by itself distinguish reasons (a) and (b) from each other
(`_symbol_evidence_sufficient` itself returns a plain `bool`, so the
caller has no finer signal to carry forward without also changing that
function's own return shape, which is out of scope for this one Phase 1
slice) — only that both collapse to the same honest `searched:`-and-
insufficient shape rather than one of them being misrepresented as a
confirmed provider fact.

**Where `:backend` actually comes from is a real, separate problem this
paragraph glossed over on its first pass, and it is not the item-1
derivation this paragraph pointed to (Codex review, verified against the
real code, PR #866 round 16).** `snapshot.dwarf_aware` does not exist —
`DWARF_AWARE` is a value of the `EvidenceTier` enum in
`checker_policy.py`, not an `AbiSnapshot` attribute — so the parenthetical
naming it as a field this derivation "already reads" was simply wrong.
More importantly, even the *real* fields item 1's derivation reads
(`snap.function_map`, `snap.elf`, `snap.from_headers`, the header-backend
field, `snap.elf_only_mode`) are fields of a **full `AbiSnapshot`**, and
`_symbol_evidence_sufficient(symbol, old_snap)`/`(symbol, new_snap)` — the
two calls whose `False` result is what this whole paragraph is about — are
typed `AbiSnapshot | BundleSignatureEvidence`, and on the one real,
live call path (`cli_compare_release.py`'s directory/package `compare`
fan-out) they are always given a `BundleSignatureEvidence`, not an
`AbiSnapshot`: `cli_compare_release.py` calls
`BundleSignatureEvidence.from_snapshot(...)` for both sides before ever
calling `find_unverified_signature_findings`, specifically so the full
snapshot (types, layout, source graph, build-source evidence — everything
but `function_map`/`variable_map`/`elf_only_mode`) can be garbage
collected before bundle analysis runs (G38 stabilization Phase 9, see
`BundleSignatureEvidence`'s own docstring in `bundle_models.py`). By the
time `find_unverified_signature_findings`'s finding-construction site
(the loop building `BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED`) would need to
emit a `:backend` value, `old_snap`/`new_snap` there are that compact
projection, and neither `snap.from_headers` nor the header-backend field
nor `snap.elf` survived the projection — there is nothing left to read to
fill `:backend` in truthfully.

This plan's own design philosophy, applied consistently elsewhere in this
document, is "derive once from the full-evidence object, then carry the
*derived, minimal* result through a narrower structure" — not "assume the
narrower structure still carries the raw fields a full-object derivation
needs." `BundleSignatureEvidence.from_snapshot()` is exactly the seam
where the full `AbiSnapshot` is still available, so the fix is to compute
item 1's `<tier>:searched:<backend>` derivation *there*, once per side,
and carry only the small derived result (not the raw fields) into a new
`BundleSignatureEvidence` field — e.g. `evidence_backend_tag: str`
(or a small tuple, for the hybrid multi-backend case item 1 already
describes) — so `find_unverified_signature_findings` reads that field
directly instead of re-deriving anything from a snapshot it no longer has.
This is a real, if small, implementation change `BundleSignatureEvidence`
itself needs (a new field, populated in `from_snapshot()`), not merely a
documentation correction — recorded here so Phase 1's implementation
doesn't discover the gap only after already committing to deriving
`:backend` at the finding-construction site, where the information no
longer exists. **This value must survive `to_change()`'s own lowering
unchanged, not be silently dropped or overwritten** — the same requirement
the surrounding paragraph already states for `BundleFinding.
evidence_provenance` in general, restated here because a `searched:`-
shaped value is exactly the kind of "no fact, just a negative result"
entry a naive lowering step could mistake for "nothing to carry" and omit.

**A third, distinct provenance shape is needed for the version-collapse
path, which does not go through `_symbol_evidence_sufficient` at all
(Codex review, verified against the real code, PR #866 round 16).**
`find_unverified_signature_findings`'s main loop has a branch —
`if version_collapsed: old_sufficient = new_sufficient = False` — that
sets both sufficiency flags directly, bypassing
`_symbol_evidence_sufficient(symbol, old_snap)`/`(symbol, new_snap)`
entirely, whenever `_bare_name_version_collapsed()` finds that the
bare-name-keyed `AbiSnapshot`/`BundleSignatureEvidence` entry for `symbol`
can't be safely attributed to the specific co-existing GNU symbol
*version* this `provider_entry` names (see this same module's own
docstring, "A fourth check ... guards the evidence-sufficiency lookup
itself against the same version-blindness"). Labeling this case with the
same `<side>:<tier>:searched:<backend>` tag the paragraph above establishes
for a genuine "searched, found nothing" result would be dishonest in the
identical way naming one concrete backend as "the provider" already was:
no search of `old_snap`/`new_snap` for this symbol's evidence was actually
attempted here — the identity of *which* version's evidence to search for
is itself ambiguous, so `:searched:` would misrepresent an unresolved
identity question as a completed, empty search. This needs its own,
clearly distinguished tag — `<side>:ambiguous:version_collapsed` (no
`:tier:`/`:backend`, since no evidence source was consulted; `<side>` is
always `both:` for this branch, since the collapse check runs once per
`provider_entry` and sets both flags together) — so an implementation
cannot accidentally conflate "we looked and found nothing" with "we
couldn't even tell what to look for." This mirrors the same
`searched:`/no-evidence-consulted distinction item 1 and item 3 already
draw for their own negative results, generalized to a third case those
items don't have: not "insufficient evidence was found" but "no specific
evidence could be identified to search for." Neither shape's real
evidentiary basis survives into `to_change()`'s own bare
`Change(...)` call (`kind`/`symbol`/`description`/`old_value`/`new_value`/
`affected_symbols`/`effective_verdict`/`modulation_*` only, no
provenance-bearing field of any kind today) — so Phase 1 must add
`evidence_provenance` to `BundleFinding` itself (derived per finding at
each of the four construction sites, from whichever ELF/per-library-Change/
snapshot evidence that finding actually rests on, not copied from a single
source the way item 4's "carry the source finding's own
`evidence_provenance` forward" default works for a true one-to-one
transform) and thread it through `to_change()`'s own `Change(...)` call,
rather than assuming the field can be stamped onto `to_change()`'s output
alone once `bundle_models.py`'s existing Files & surfaces entry is wired.
The dominant path is still `diff_helpers.make_change()`,
which already forwards arbitrary keyword arguments straight to
`Change(...)` unchanged (`**change_kwargs: Any` in its signature) — a real,
favorable, count-independent consequence: **`make_change()` itself needs
no signature change**, any call site can already pass
`evidence_provenance=(...)` through today's `**change_kwargs`. The actual
Phase 1 work is therefore not "extend one factory, then wire N sites
downstream of it" — it's "decide and pass the *right value* at every real
call site, both `make_change()`-routed and direct," spread across every
detector module that emits findings (`diff_*.py` **and** `buildsource/*.py`
in full, not a partial file list) — a large, XL-effort inventory whichever
way it's counted, which is this section's actual, count-independent point.
**A completeness gate over construction paths themselves** (the P2 finding
this round of review also raised) is a real alternative to a static count
and is worth a follow-up investigation — e.g. an AST-based
`check_ai_readiness.py`-style check that every `Change(...)`/`make_change(...)`
call site is covered by *some* test asserting on `evidence_provenance` once
Phase 1 completes — but designing that gate is its own scoped piece of
work, not attempted in this already-corrected-twice section.

**Implementation location (ADR-061):** detector wiring is `compare/`
territory ("Match old/new entities or identify a raw change"). As with
Phase 0, this plan targets whichever module is canonical for a given
detector *at implementation time* — the flat `diff_*.py`/`buildsource/*.py`
modules named below if `compare/`'s detector migration (ADR-061 D2's
`compare/detectors/{symbols,types,cpp,platform,build,source}.py`) has not
yet reached that detector, the `compare/` package otherwise. `buildsource/*`
is a distinct, already-`AGENTS.md`-scoped package (L3-L5 build/source
evidence) and stays where it is regardless of the `compare/` migration —
see `abicheck/buildsource/CLAUDE.md`.

**Not one flag day.** Ordered by risk, matching this file's own "known gaps
over risky reactive patches" discipline and the FP-rate/mutation-score gate
structure already in place:

1. **L0/L1-only detectors first** (`diff_platform.py`'s ELF/PE/Mach-O-specific
   findings, and the direct-construction sites in `checker.py` that are
   similarly static) — the provenance is a static fact of which module
   produced the `Change`, not a per-call derivation, so this slice is close
   to mechanical: one constant tuple per detector function, passed through
   the existing `make_change(...)`/`Change(...)` call. **`versioned_symbol_
   scheme.py`'s direct-construction site does NOT belong in this mechanical
   sub-slice (Codex review, verified against the code)** — see item 4
   below, which is where it actually belongs: `_build_scheme_advisory()`
   builds the single `versioned_symbol_scheme_detected` advisory from bare
   `pairs`/`eligible` counts, but those counts (and the underlying matched
   `Change` objects `analyze_versioned_scheme()` computes alongside them)
   are derived from `FUNC_REMOVED`/`FUNC_ADDED`/`VAR_REMOVED`/`VAR_ADDED`/
   `FUNC_LIKELY_RENAMED` findings already present in the comparison's
   `changes` list — findings that, per the `diff_symbols.py` carve-out
   below, can themselves rest on L0, L1, or L2 evidence depending on which
   producer populated the participating `Function`/`Variable` record. A
   fixed module-level tuple would misstate the advisory's real basis
   whenever the matched findings used mixed or non-L0 evidence.

   **`diff_platform.py` itself is not uniformly
   mechanical, and treating the whole file as one fixed-tier module would
   mislabel its own mixed-evidence detectors (Codex review, verified against
   the code)** — the same carve-out `diff_symbols.py` needed below applies
   *within* this file too, not just to the sibling module. `_diff_elf_deleted_
   fallback` (`elf_deleted_fallback`) is the concrete case: it reads
   `old.elf`/`new.elf` (genuine L0 symbol-table evidence) to detect a symbol
   vanishing from `.dynsym`, but it gates that on `_public_functions(old)`
   and `new.function_map` — the *declaration*-side `Function` records those
   calls return, whose own evidence can rest on DWARF, castxml, or clang
   depending on how each snapshot was produced (the identical fallback-to-
   header/DWARF and synthetic-constructor/destructor behavior the
   `diff_symbols.py` carve-out below documents for `_public_functions()`
   itself). A `Change` this detector emits is therefore never purely L0: it
   names an L0 fact (the symbol's absence from the export table) *and* an
   L1/L2 declaration fact (that the symbol is still declared, still public,
   and not already marked deleted/inline) jointly, and a module-level
   constant tuple can state only the first half honestly. This slice's own
   audit must therefore go call-site by call-site *within*
   `diff_platform.py`, exactly as slice 2 (`diff_types.py`) and the
   `crosscheck.py` sub-slice already do — a detector reading only
   `old_elf`/`new_elf` (most of the ELF/PE/Mach-O-specific findings this file
   holds) is genuinely static and keeps its one-constant-tuple treatment; a
   detector that also reads a declaration-side record for gating (
   `elf_deleted_fallback`, and any sibling detector found to share the same
   shape during the audit) is not, and needs the same per-finding, multi-
   provider treatment `diff_symbols.py`'s own slice already prescribes. This
   is Phase 1 budget, not an afterthought discovered mid-implementation —
   the "close to mechanical" framing above describes the *common* case in
   this file, not a blanket exemption from the audit every other slice
   already requires. **`diff_symbols.py` does NOT belong in this
   mechanical sub-slice** despite being symbol-table-driven on its face
   (Codex review, verified against the code): `_public_functions()` falls
   back to the complete header/DWARF function set when live ELF evidence is
   absent, and separately still returns synthetic CastXML-only
   constructors/destructors that never match any exported symbol — so the
   same removal/addition call site can be driven by L0, L1, or L2 evidence
   depending on which producer's facts a given `Function` actually rests on,
   not by which module ran. Treat `diff_symbols.py` as its own slice,
   ordered after this one and before slice 2, deriving provenance per
   finding from the participating `Function`/`Variable` record's own
   evidence (which source populated it) rather than assuming a module-level
   constant. **On a `--ast-frontend hybrid` snapshot, "the participating
   record" is not a single producer either (Codex review, verified against
   the code): `fact_provenance.py` (G28 Phase 3) exists precisely because a
   hybrid snapshot's merge (`dumper_hybrid.merge_snapshots`) can backfill
   individual facts on one `Function`/`Variable` from clang while the rest
   of that same record came from castxml — `AbiSnapshot.fact_provenance` is
   keyed per-fact (`func_fact_key`/`var_fact_key`/`field_fact_key`, e.g.
   `"func:<mangled>:<fact>"`), not per-declaration.** So for a hybrid
   snapshot this slice must query `fact_provenance` for the *specific* fact
   a given detector call compares (e.g. a parameter-default or deprecation
   change), via `is_castxml_backed_fact`/`both_castxml_backed_fact` or a
   sibling per-fact lookup, rather than reading one provenance value off the
   record as a whole — the same "specific fields, not aggregate backend"
   principle slice 2 below already states for `diff_types.py`, extended to
   `diff_symbols.py`'s own record-level facts. **A removal/addition finding
   is not a fact on one record at all — it is a join result, and half of
   what it rests on is an absence (Codex review, verified against the
   code).** `_diff_functions`'s removal path (`_match_old_function`, ending
   in `_check_removed_function`) has a participating `f_old: Function`
   record, but `FUNC_REMOVED` is emitted only after `f_old`'s mangled key,
   its extern-C alias, and its deleted-symbol peer all fail to resolve
   against `new_index`/`new_all` — the new side contributes no record at
   all, only the fact that it was searched (by exact key, by alias, and for
   a deleted peer) and nothing matched. `_diff_variables` and the addition
   direction (`FUNC_ADDED`/`VAR_ADDED`, symmetric: an absence on the *old*
   side) share the identical shape. Deriving `evidence_provenance` only
   from the one participating record — the natural first reading of the
   rule two paragraphs up — either omits the searched-but-empty side's
   evidence entirely or, worse, mislabels the finding `both:<tier>` as if
   both sides contributed a fact, when only one did and the other
   contributed a negative result. The fix is the identical `searched:`
   shape item 3 below establishes for `crosscheck.py`'s own negative
   `_check_*` findings (`current:l2:searched:<frontend>`, recording which
   complete surface was consulted and found nothing, not which fact produced a
   result): generalize it here rather than treating it as a `crosscheck.py`
   -specific vocabulary entry. For a removal, `evidence_provenance` records
   the *matched* side's real per-fact provenance (whichever tier actually
   produced `f_old`, per the hybrid-aware rule above) alongside a
   `searched:<tier>` entry for the side that was searched and came back
   empty — never a bare `both:` label implying two facts where there is one
   fact and one confirmed absence. **The `<tier>` value must be derived per
   snapshot from which providers actually populated that snapshot's
   declaration surface, not hardcoded to one example (Codex review,
   verified against the code): `_public_functions()`'s own narrowing is
   evidence-conditional, not a fixed ELF+L2 pair.** It starts from
   `snap.function_map` — populated from whichever of DWARF (L1), header-AST
   parsing (L2), or a symbols-only ELF dump (L0/`elf_only_mode`) actually
   produced the snapshot — and narrows that set to the exported subset only
   when `snap.elf is not None and snap.elf.symbols`; a snapshot with no ELF
   symbol table at all (a header-only L2 dump, or a PE/Mach-O snapshot,
   since this narrowing checks `snap.elf` specifically and has no `snap.pe`/
   `snap.macho` counterpart) keeps the full DWARF/header-derived set
   untouched, so no `l0` component was ever searched for that snapshot.
   **Each entry names its own concrete backend, not just a tier — collapsing
   a real provider into a bare `searched:<tier>`/composite `<tier1>+<tier2>`
   tag (an earlier draft of this section did exactly that, flagged by
   Codex review as a real regression relative to the vocabulary this same
   item already commits to two paragraphs up) loses the castxml-vs-clang
   distinction on a header-derived surface and drops the mandatory `l0:`
   tier prefix on a PE/Mach-O entry entirely.** The corrected shape is
   `<side>:<tier>:searched:<backend>` — side prefix first (this item's own
   `old:`/`new:`/`both:` rule from two paragraphs up), then the identical
   `l0:`/`l1:`/`l2:`-plus-backend-id vocabulary the table above and item 3
   below both already use, with `searched` inserted before the concrete
   backend id rather than replacing it (matching item 3's own
   `current:l2:searched:<frontend>` shape, generalized here with the side
   prefix item 3's single-snapshot crosscheck findings use `current:` for
   instead). **A
   multi-provider search records one entry per provider actually
   consulted, never one collapsed tag** — the same "one tier-bearing entry
   per provider" rule item 3 states for its own multi-`PROVIDER_*` checks
   applies identically here. Concretely, per real backend combination:
   `new:l1:searched:dwarf` for a DWARF-derived snapshot with no ELF export
   table consulted; `new:l2:searched:castxml` or `new:l2:searched:clang`
   (never a bare `l2`) for a header-only snapshot, naming whichever backend
   actually produced it (or both, `new:l2:searched:castxml` plus
   `new:l2:searched:clang`, for a hybrid snapshot where both surfaces were
   built and consulted); `new:l0:searched:elf_symtab` when the snapshot is
   `elf_only_mode` (an ELF-only, symbols-only dump with no type-level
   evidence at all, per `_is_stripped_symbols_only`); **two** separate
   entries — e.g. `new:l0:searched:elf_symtab` *plus*
   `new:l1:searched:dwarf` (or the `l2:searched:castxml`/`clang` sibling) —
   rather than one `l0+l1`/`l0+l2` composite, when DWARF or header-AST
   evidence was narrowed by a *present* ELF export table; and, for a
   PE/Mach-O snapshot whose declaration surface came from a PE/Mach-O
   export table rather than ELF `.dynsym`, the identical `l0:`-prefixed
   shape using this plan's own PE/Mach-O backend ids from item 3 below —
   `new:l0:searched:pe_export_table` / `new:l0:searched:macho_exports` —
   never a bare, tier-less `pe-exports`/`macho-exports` tag. `_public_
   functions()` has no PE/Mach-O narrowing step today, so this slice's
   implementation must decide whether to add an equivalent narrowing step
   or record the platform's evidence tier as-is; whichever it picks, the
   emitted tag still carries the full `l0:searched:<backend>` shape, not a
   platform name standing in for the tier. Implement this as one function
   that inspects the actual snapshot fields the way `_public_functions()`
   and `_is_stripped_symbols_only()` already do, not as a table keyed by an
   assumed-fixed platform. This is Phase 0 vocabulary work for this slice,
   the same way item 3 below folds its own `searched:` form into Phase 0
   rather than deferring it — and the two must use the literal same
   per-backend vocabulary (`l2:castxml`/`l2:clang`/`l0:elf_symtab`/
   `l0:pe_export_table`/`l0:macho_exports`), not two independently-drifting
   spellings of the same idea.

   **`VAR_REMOVED`/`VAR_ADDED` need their own derivation, not a copy of the
   function one above (Codex review, verified against the code): the two
   functions do not draw from the same provider set.** `_public_functions()`
   narrows via two steps — a `visibility`/`is_abi_relevant_elf_symbol` filter
   over `snap.function_map`, *then*, only when `snap.elf.symbols` is present,
   a second narrowing against the live ELF export table
   (`exported_symbol_names(elf, FUNCTION_SYMBOL_TYPES, ...)`), which is what
   licenses an `l0:searched:elf_symtab` entry for the function case above.
   `_public_variables()` has no second step: it applies only the first
   filter (`visibility`/`is_abi_relevant_elf_symbol`/`_is_local_type_rtti`
   over `snap.variable_map`) and returns directly — it never calls
   `exported_symbol_names()` or consults `snap.elf.symbols` at all. So the
   *narrowing* rule above must not be applied to variables by extension: a
   `VAR_REMOVED`/`VAR_ADDED` finding's `searched:` entry for the empty side
   must never claim an `l0:searched:elf_symtab` *narrowing* of an
   otherwise-DWARF/header-populated `variable_map`, on an ELF, PE, or
   Mach-O snapshot alike, since no such narrowing step exists for variables
   to license one.

   **This does not mean `l0:searched:elf_symtab` (or its Mach-O sibling) is
   categorically unreachable for a variable finding — an earlier draft of
   this rule said exactly that, and it was wrong (Codex review, verified
   against the code): the tag must be derived from *how `variable_map` was
   populated in the first place*, not from a blanket always/never rule.**
   For a snapshot with headers or DWARF, `variable_map` is genuinely
   L1/L2-derived and the rule above stands unchanged: record only the
   `l1:searched:dwarf` / `l2:searched:castxml` / `l2:searched:clang` tier
   that populated it, since no ELF/PE/Mach-O narrowing step touched it. But
   for a symbols-only snapshot — `elf_only_mode`/`_is_stripped_symbols_
   only()`, the identical condition item 1's function-side rule already
   names above — `variable_map` is not narrowed by export-table evidence,
   it is *built directly from it*: `dumper_elf_fallback.py`'s symbol-only
   fallback (`build_symbol_only_snapshot`, ~lines 153–189) constructs every
   `Variable` straight from `exported_dynamic_objects | exported_dynamic_tls`
   — the parsed ELF export table — and `dumper.py`'s Mach-O symbols-only
   path (~lines 1747–1757) does the identical thing from `macho_meta.exports`;
   neither goes through DWARF or a header backend at all in this mode. A
   `VAR_REMOVED`/`VAR_ADDED` search over such a snapshot's `variable_map` is
   therefore purely L0 evidence by construction, and the emitted tag must
   say so — `new:l0:searched:elf_symtab` for the ELF fallback, the matching
   `new:l0:searched:macho_exports` for the Mach-O sibling — mirroring the
   function path's own `elf_only_mode` branch above, not omitting the empty
   side's real provider. **PE has no counterpart here, and this is not an
   oversight to fix by adding one:** `dumper.py`'s PE symbols-only path
   (`_dump_pe`'s no-headers branch, confirmed by reading it directly) builds
   its `AbiSnapshot` with `functions=[...]` from `exported_dynamic` but no
   `variables=` argument at all, so `variable_map` stays the dataclass's
   empty-list default — PE variable-symbol extraction from the export
   directory does not exist in this codebase yet, on any path, so
   `l0:searched:pe_export_table` cannot occur for a `VAR_REMOVED`/
   `VAR_ADDED` finding today. Implement this the same way item 1 already
   requires for functions: inspect whether the snapshot side is
   symbols-only before choosing the vocabulary (ELF or Mach-O only, never
   PE), rather than encoding a fixed per-`ChangeKind` answer. This is a
   difference in what evidence exists per snapshot shape, not a stylistic
   simplification — if a later pass ever gives `_public_variables()` its own
   ELF/Mach-O *narrowing* step over an otherwise header/DWARF-populated
   `variable_map` (matching how functions already treat `= delete`), the
   two-entry composite vocabulary item 1 describes generalizes then and only
   then for those two platforms; and if a later pass ever gives PE dumping
   its own variable-export extraction (a real, separate feature addition,
   out of scope for this plan), `l0:searched:pe_export_table` becomes
   reachable for variables at that point and this rule should be revisited
   — until either happens, claiming a narrowing or symbols-only tag for a
   case the code cannot produce would fabricate export-table provenance the
   join never actually consulted — the one part of the original rule that
   remains correct.
2. **L2 header-derived detectors** (`diff_types.py`'s struct/enum/typedef
   findings, `diff_type_spellings.py`) — provenance here genuinely varies
   per finding (which backend produced *this specific* `RecordType`, and
   was it DWARF-corroborated) — this is the harder, `AbiSnapshot`-inspecting
   slice the investigation above already partially scoped for one
   `ChangeKind` family (layout). Generalize that same reasoning (check the
   *specific* fields the finding rests on, not the snapshot's aggregate
   backend) across every `diff_types` detector, not just vtable/size/
   alignment. **A real prerequisite this slice cannot skip (Codex review,
   verified against the code): the snapshot model does not currently
   persist per-record backfill provenance for this slice to read.**
   `dumper_layout_backfill.resolve_snapshot_layout_coherence()` returns
   only `(coherence.status, coherence.mismatched)` — `DwarfLayoutCoherence
   .matched` (the per-type names that *were* successfully DWARF-backfilled)
   is computed and then discarded, and `RecordType` itself carries no
   producer/backfill marker a detector could read per-instance. Without
   that fact, a type-level detector in this slice can only infer from the
   snapshot's *aggregate* `dwarf_layout_coherence` status (`"matched"` /
   `"partial"` / ...) — exactly the report-level-metadata guess this slice
   exists to replace with real per-finding provenance. Closing this needs
   a small, additive extraction/model change *before* this slice's detector
   wiring starts: thread `DwarfLayoutCoherence.matched` (or an equivalent
   per-record marker) onto the record itself, or onto a snapshot-level
   lookup the detector can consult per `RecordType.qualified_name`. Scope
   that model change as its own reviewed sub-step of this slice, not a
   simultaneous side effect of the detector wiring itself.
3. **L3-L5 build/source detectors** (`buildsource/*.py`) — **not** simply
   "already carry `evidence_category`; extend rather than duplicate" as an
   earlier draft of this plan said (Codex review, verified against the
   code: `evidence_category` is a coarse binary tag,
   `"source_only"`/`"build_context"` only — `evidence_policy.
   tag_evidence_category` and the two direct call sites in
   `crosscheck_coherence.py`/`diff_reconcile.py` are the only producers,
   and it cannot express a finding that rests on more than one evidence
   *kind*, e.g. `exported_not_public` (binary exports + L2 header AST) or
   `header_build_context_mismatch` (L2 header AST + L3 build context)).
   `buildsource/crosscheck.py`'s `run_crosschecks()` already records the
   real, specific sources for every one of its checks in each
   `_CheckOutput.providers` list — a sequence of `PROVIDER_*` constants
   (`PROVIDER_BINARY_EXPORTS`, `PROVIDER_PUBLIC_HEADER_AST`,
   `PROVIDER_BUILD_CONFIG`, `PROVIDER_SOURCE_INDEX`), one entry per evidence
   kind the check actually consulted, already present at every one of this
   file's `_check_*` call sites. This slice's wiring derives
   `evidence_provenance` from that `providers` list, not from
   `evidence_category` — the richer, already-collected fact, not the coarser
   tag layered on top of a subset of it.

   **`PROVIDER_*` is not itself tier-bearing, and the list cannot be
   translated to a prefix by a fixed `PROVIDER_* -> l*:` lookup table
   (Codex review, fresh evidence, verified against the code).** The
   parenthetical originally here claimed each `PROVIDER_*` constant "maps
   cleanly to an `l0:`/`l2:`/`l3:`/`l4:` prefix" — false for
   `PROVIDER_SOURCE_INDEX`, which several distinct `_check_*` functions
   share despite consuming genuinely different evidence *tiers*:
   `_check_odr_type_variant` reads `snapshot.build_source.source_abi` (the
   L4 source-replay surface — its own docstring says so explicitly) while
   `_check_public_to_internal_dependency` reads
   `snapshot.build_source.source_graph` (the L5 source/consumer graph) —
   both stamp the identical `providers = [PROVIDER_SOURCE_INDEX]`.
   Translating the provider list directly into a prefix, as the previous
   wording of this bullet directed, would derive the same tier string for
   both and silently mislabel the L5 finding as L4 (or vice versa) — the
   `current:l5:source_graph` example already given for
   `_check_public_to_internal_dependency` earlier in this document (see
   the `old:`/`new:`/`both:`/`current:` scoping section above) is the
   correct value for that check specifically, and is *not* recoverable from
   `providers` alone. The fix is not a richer `PROVIDER_*` enum (that would
   re-litigate `crosscheck_base.py`'s own provider-agreement-matrix
   contract, which several other consumers beyond this plan already
   depend on) — it is that this slice's wiring must key the `l*:` prefix
   off **the emitting `_check_*` function's own known tier**, not off the
   `PROVIDER_*` identity alone. Concretely: build the mapping as one entry
   per `_check_*` function (`_check_odr_type_variant ->
   "l4:source_replay"`, `_check_public_to_internal_dependency ->
   "l5:source_graph"`, and so on for every check in this module), derived
   by reading each function's own body to see which `snapshot.build_source`
   field it actually consults — the same per-call-site reading discipline
   Phase 1's other slices already use — rather than assuming the
   `PROVIDER_*` constant on `_CheckOutput.providers` is itself sufficient.
   A `PROVIDER_*` constant that turns out to name only one tier across every
   one of its call sites (e.g. `PROVIDER_BUILD_CONFIG` naming only L3) may
   still resolve to a single fixed prefix in practice — the point is that
   this must be verified per constant, function by function, not assumed
   uniformly from the constant's name.

   **The per-`_check_*`-function fixed-tier mapping above is itself
   insufficient, and needs correcting rather than just caveating (Codex
   review, fresh evidence, verified against the code).** It fixed the
   `PROVIDER_SOURCE_INDEX` ambiguity (one constant, two tiers depending on
   which function emits it) by keying the mapping on the emitting function
   instead of the constant — but a fixed *function* → tier table has two
   further gaps a single static mapping cannot express, both real for
   checks this same module already contains:

   - **`PROVIDER_PUBLIC_HEADER_AST` names a backend-*selectable* evidence
     source, not a fixed tier.** Every check that lists it in `providers`
     (`_check_exported_not_public`, `_check_public_not_exported`,
     `_check_header_build_context_mismatch`, `_check_private_header_leak`,
     `_check_rtti_for_internal_type`) runs against whichever L2 header-AST
     backend actually produced the snapshot being checked — castxml,
     clang, or hybrid, selected per run via `--ast-frontend`/
     `AbiSnapshot.ast_frontend`, not per detector function. Phase 0's own
     vocabulary table above deliberately distinguishes `l2:castxml` from
     `l2:clang` because the two backends have measurably different fact
     completeness, so a `_check_exported_not_public -> "l2:..."` table
     entry fixed at design time cannot know, for any given run, which of
     the two it should actually say — that is a fact of the *snapshot*
     the check consulted, not of the function's source code.
   - **A check's own `providers` list can name more than one tier at
     once, and the finding genuinely rests on all of them jointly.**
     `_check_exported_not_public`'s `providers = [PROVIDER_BINARY_EXPORTS,
     PROVIDER_PUBLIC_HEADER_AST]` (and `_check_public_not_exported`'s
     identical pair, `_check_header_build_context_mismatch`'s
     `[PROVIDER_BUILD_CONFIG, PROVIDER_PUBLIC_HEADER_AST]`,
     `_check_rtti_for_internal_type`'s `[PROVIDER_BINARY_EXPORTS,
     PROVIDER_PUBLIC_HEADER_AST]`) are each gated on an ELF/binary-export
     fact **and** a header-AST fact agreeing — the finding would not exist
     without both. Collapsing that to one tier string, however derived,
     discards which of the two providers the consumer is actually being
     told corroborated the finding.

   **Resolution: derive per finding, from the specific check invocation,
   not from a static function-keyed table.** Two changes to the wiring
   this bullet prescribes, both additive to Phase 0's existing shape
   rather than a redesign of it:

   1. For a `PROVIDER_PUBLIC_HEADER_AST` entry specifically, the `l2:`
      suffix is read off the snapshot the check actually ran against —
      `AbiSnapshot.ast_frontend` (or, for a hybrid snapshot, the relevant
      per-fact provenance via `fact_provenance.py`, mirroring slice 1's
      own hybrid-snapshot carve-out earlier in this document) — at the
      point the finding is constructed, never hard-coded per function.
   2. For a check whose `providers` list carries more than one `PROVIDER_*`
      entry, `evidence_provenance` carries one tier-bearing entry per
      provider actually consulted for that finding, not a single collapsed
      value — e.g. `_check_exported_not_public` under a `--ast-frontend
      clang` run stamps `("current:l0:elf_symtab", "current:l2:clang")`,
      not either alone. This is exactly what `evidence_provenance` being
      `tuple[str, ...]` (rather than a single string) was always meant to
      support; Phase 0's vocabulary table simply hadn't yet stated that a
      multi-provider check must use more than one slot.
   3. **`PROVIDER_BINARY_EXPORTS` names a platform-selectable evidence
      source too, exactly like `PROVIDER_PUBLIC_HEADER_AST` does for the
      backend (Codex review, verified against the code) — the fixed
      `"current:l0:elf_symtab"` spelling used in the example above is only
      correct for an ELF snapshot.** `crosscheck_base._exported_symbol_
      names()` — the function every one of these checks actually calls —
      branches on `snapshot.elf`/`snapshot.pe`/`snapshot.macho` and reads a
      structurally different export table for each, so hard-coding the ELF
      spelling would publish false provenance for either supported non-ELF
      platform. Resolution: add `l0:pe_export_table` alongside the
      existing `l0:elf_symtab` to Phase 0's vocabulary table for the PE
      case, and derive the `l0:` suffix for a `PROVIDER_BINARY_EXPORTS`
      entry from the same platform branch `_exported_symbol_names()`
      itself takes (`snapshot.elf is not None` / `snapshot.pe is not None`
      / `snapshot.macho is not None`, in that order) — at
      finding-construction time, the identical "read it off the snapshot,
      never hard-code it" discipline point 1 above already establishes for
      the `l2:` suffix. **The Mach-O case needs a differently-named
      provider than the PE case, not the same treatment (Codex review,
      fresh evidence, correcting this bullet's own earlier draft, which
      had proposed `l0:macho_export_trie`).** Unlike the PE export
      directory (populated by exactly one function, `_parse_pe_exports()`,
      so `l0:pe_export_table` names a genuine single source), Mach-O's own
      `MachoMetadata.exports` (`abicheck/macho_metadata.py`) is a *merged*
      list: `_parse()` first populates it from the classic symbol table
      (`_parse_macho_symbols()`) and then `_parse_export_trie()` merges in
      any trie-only exports the symbol table missed, plus upgrades the
      weak/re-export flags of entries already present — with no per-entry
      marker recording which mechanism actually produced a given export.
      Since `_exported_symbol_names()` reads this already-merged list, a
      given Mach-O finding's export fact is not necessarily trie-derived
      at all — it just as often comes from the classic symbol table alone
      — so stamping every Mach-O cross-check finding with
      `l0:macho_export_trie` would publish false, overly-specific
      provenance for the (equally common) symbol-table-only case.
      Resolution: name the Mach-O entry `l0:macho_exports` — a generic
      label for the combined export fact this codebase actually retains,
      matching what the data model can support today, rather than a
      trie-specific claim it cannot back per finding. Retaining true
      per-export source identity (symtab vs. trie) would need
      `MachoExport` itself to carry that distinction — a real, separate
      `macho_metadata.py` model change, out of scope for this phase, which
      only wires the provenance vocabulary through to what already
      exists.

   **A fourth gap, distinct from the three above and not fixable by adding
   another platform/backend branch: some `_check_*` findings are negative,
   and `fact_provenance` has no fact to name for a negative result (Codex
   review, verified against the code).** `_check_exported_not_public` does
   not merely read a `PROVIDER_PUBLIC_HEADER_AST` fact — for its own
   central finding, it emits *because* no declaration anywhere in the
   snapshot's public-header surface accounts for a given export
   (`sym not in public_syms`, `decl_by_sym.get(sym)` absent or private).
   `AbiSnapshot.fact_provenance` (G28 Phase 3) is keyed per *existing*
   fact on a *specific* declaration (`func_fact_key`/`var_fact_key`/
   `field_fact_key`) — it can answer "which backend produced this field on
   this record," never "which backend(s) were exhaustively searched and
   found nothing." There is no declaration to look the fact up on, so
   point 1's "read the per-fact provenance via `fact_provenance.py`"
   instruction has no key to query for this shape of finding, and choosing
   `l2:castxml`/`l2:clang` for it either by guessing the run's configured
   frontend or by defaulting to one would misrepresent what was actually
   established: not "castxml said this symbol is undeclared" but "every
   declaration this run's public-header surface could produce, from
   whichever backend(s) actually ran, was searched and none matched."
   **Resolution: a negative finding records which complete backend
   surface(s) were searched, not which fact produced it** — a distinct,
   additive provenance shape from the positive, per-fact case points 1-3
   above cover, not a variant of it. Concretely, `evidence_provenance` for
   this shape carries a `current:l2:searched:<frontend>` entry per backend
   whose public-header surface was exhaustively built and consulted for the
   symbol in question (`current:l2:searched:castxml`,
   `current:l2:searched:clang`, or both for a hybrid snapshot where both
   surfaces were checked), rather than
   attempting to name a producer for a fact that does not exist. This
   generalizes beyond `_check_exported_not_public`: any other check in this
   module whose finding rests on an *absence* across a searched surface
   (audited alongside the positive-fact cases, function by function, per
   the same discipline below) needs the identical `searched:` shape rather
   than a `PROVIDER_*`-to-`l*:`-fact translation that assumes a fact
   exists. Phase 0's vocabulary table gains this `searched:` form
   alongside the existing per-fact `l*:` entries as part of this slice's
   work, not as a follow-up.

   The function → tier examples this bullet originally gave —
   `_check_odr_type_variant -> "current:l4:source_replay"` and
   `_check_public_to_internal_dependency -> "current:l5:source_graph"` —
   remain correct as written: neither lists `PROVIDER_PUBLIC_HEADER_AST`,
   and neither is multi-provider, so a fixed single-tier mapping is sound
   for those two specifically. They were never wrong; they were presented
   as instances of a rule general enough to cover every `_check_*`
   function in the module, which — per the four gaps above — they are not.
   The verification discipline is unchanged and now covers all four
   dimensions explicitly: for each `_check_*` function, read its body to
   determine (a) every `PROVIDER_*` constant it can stamp into `providers`
   for a given call, (b) for any `PROVIDER_PUBLIC_HEADER_AST` entry, the
   snapshot field or hybrid-provenance lookup that names the backend that
   actually produced it, (c) for any `PROVIDER_BINARY_EXPORTS` entry, the
   platform branch that names which export-table format was read, and (d)
   whether the finding is positive (a fact was found and can be named) or
   negative (an exhaustive search found nothing, and must be recorded as
   `searched:`, not misattributed to a fact) — function by function, never
   assumed uniformly from another function's shape or from the constant's
   name alone.
4. **Cross-cutting post-processing and roll-up emitters**
   (`post_processing.py`, `post_processing_reachability.py`,
   `pattern_verdicts.py`, `internal_leak.py`, `bundle_models.py`,
   `post_manifest.py`, `cli_buildsource_helpers.py`,
   `versioned_symbol_scheme.py`'s `_build_scheme_advisory()`) — these often
   construct a `Change` by *transforming* an existing one (a roll-up, a
   suppression-adjacent rewrite) rather than deriving fresh evidence; the
   right default here is usually "carry the source finding's own
   `evidence_provenance` forward," verified per emitter rather than assumed
   uniformly. `_build_scheme_advisory()` is this list's one *many-to-one*
   roll-up rather than a single-source carry-forward: its advisory
   summarizes every matched `Change` in `matched_removed`/`matched_added`/
   `matched_renamed` (`analyze_versioned_scheme()`'s own second return
   value) at once, so "carry forward" here means a union/rollup of
   whatever `evidence_provenance` each constituent finding actually
   carries, not a single value copied from one source. This also means
   `_build_scheme_advisory()`'s own signature must change to receive the
   matched findings (or their provenance) rather than only the bare
   `pairs`/`eligible` counts it takes today.

Each slice, once wired, gets its own FP-rate/mutation-score gate re-run
before merging — never the whole inventory behind one PR. The FP-rate/
mutation-score gates exist precisely because a previous incident in this
exact area (the reverted "linkage-blind removal" and "vptr-offset-bits"
attempts, `AGENTS.md`'s own record) showed field-level changes to shared
detector plumbing can silently reintroduce a false positive or false
negative that passes every hand-written example test.

### Phase 2 — completeness gate (M)

A new test, `test_evidence_provenance_completeness.py`, mirroring
`tests/canonical_identity_contract.py`'s "every `ChangeKind` must be
classified" pattern exactly (the same pattern this repo adopted specifically
*because* an earlier per-`ChangeKind` classification silently missed three
entries — PR #753 → #759, cited in the root `AGENTS.md`'s "Adding a new
ChangeKind" section). Every `ChangeKind` must appear in one of:

- `PROVENANCE_STATIC` — kinds whose provenance is a constant of the
  producing detector (most L0/L1 kinds);
- `PROVENANCE_PER_FINDING` — kinds whose provenance must be computed per
  instance (most L2+ kinds);
- `PROVENANCE_UNVERIFIED` — an explicit backlog bucket, not a silent gap,
  identical in spirit to `UNVERIFIED` in the canonical-identity contract.

A `ChangeKind` with no entry fails CI — the same "no silent omission"
mechanism `canonical_identity_contract.UNVERIFIED` already established for
identity classification, applied here to provenance classification instead.

**What this gate does and does not prove (Codex review, fresh evidence):**
it proves every `ChangeKind` is classified into one of the three buckets —
an *enum-partition* completeness, mirroring the #753 → #759 incident's own
lesson (a missing entry is silent everywhere else, so make the enum itself
un-skippable). It does **not** prove a kind's real producer(s) actually
behave the way its bucket claims — a kind moved to `PROVENANCE_STATIC`/
`PROVENANCE_PER_FINDING` whose producer forgot to actually set
`evidence_provenance`, or whose *second*, independent producer path (e.g. a
kind emitted from both a `diff_symbols.py` path and an unrelated
`diff_platform.py` path) never got wired at all, still passes this gate
outright, since it checks bucket *membership*, not producer *behavior*. That
gap is why the "completeness gate over construction paths" idea two
paragraphs above is called out as real, separate, not-yet-designed
follow-up work rather than something this phase already closes — the two
sections must not be read as contradicting each other: Phase 2's gate closes
the enum-omission failure mode specifically, and is deliberately silent on
the construction-path failure mode, which needs its own mechanism this plan
does not yet specify.

### Phase 3 — report/schema surface (S)

**Implementation location (ADR-061), corrected against the actual code
(Codex review — an earlier revision of this section named `report/
render_json.py`/`document.py` as the projection point, which is wrong):**
`report/document.py`'s `ReportDocument.from_mapping`/`to_mapping` only
freeze/thaw an *already-built* mapping, and `render_json.py` only calls
`json.dumps(document.to_mapping())` on it — neither one projects a `Change`
into a dict. That projection is still done entirely by legacy `reporter.py`
call sites: `_change_to_dict` (the main per-change dict builder), its
sibling `_leaf_entry` (leaf-mode's own, deliberately separate dict builder
for type-change leaves — see its own docstring for why it doesn't route
through `_change_to_dict`), and `cli_scan_baseline._baseline_finding_dicts`
(scan's own, separate projection). **This phase must cover all three, not
just one**, or `evidence_provenance` reaches full-mode JSON while silently
staying absent from leaf-mode and `scan --against` output. Add the field to
each builder directly (mirroring how `contract_evidence_refs` already
appears in `_change_to_dict` today — `_leaf_entry`/`_baseline_finding_dicts`
would need it added fresh, since neither currently carries
`contract_evidence_refs` either, confirmed by reading both). If `report/`'s
own migration has absorbed one or more of these builders by implementation
time, wire the field there instead — but the sub-task is "cover every
current `Change`→dict projection," not "extend whichever one happens to be
literally named `report/`."

**Four more `Change`→dict projections exist beyond those three, and this
phase must cover them too (Codex review, fresh evidence, confirmed by
reading `reporter.py` directly): `_out_of_surface_entry`, `_add_reconciled`,
`_filtered_internal_entry`, and `_suppressed_change_entry`.** These are the
audit-ledger serializers `_add_contract_decision_fields`'s own docstring
already names as the reason that helper exists (see that docstring's own
"a demoted/suppressed/reconciled finding" language) — each builds a
compact, independent dict from a real `Change` object for a finding that
was *excluded* from the main `changes` list (out-of-surface, ADR-039
reconciled, filtered-internal, or suppressed), and none of the four routes
through `_change_to_dict`/`_leaf_entry`/`_baseline_finding_dicts` to pick up
`evidence_provenance` incidentally. Implementing Phase 3 literally against
only the three builders named above would leave `evidence_provenance`
present on every *kept* finding while silently absent from every demoted
one — exactly backwards from where a report reader most needs to see the
evidence a policy or suppression decision rested on: an audit trail that
shows a finding was suppressed but not what evidence justified suppressing
it defeats the auditability this whole model exists to provide. Add
`evidence_provenance` to all four the same way `_add_contract_decision_fields`
already stamps `finding_id`/`canonical_finding_id`/the contract-decision
fields on each of them — a single shared helper call at each site, not four
independent implementations — and extend the schema-version-bump/topic-
registration steps above to cover these four projections' own output shape
(the `surface_scope.out_of_surface_changes`/`scope.filtered_internal_changes`/
reconciliation-ledger/suppressed-changes JSON blocks), not just the three
originally named.

**A new public field on an already-published report format is a real
schema change, not a cosmetic addition (Codex review — a prior revision of
this phase named all three builders but never said this)**:
`_change_to_dict`/`_leaf_entry` feed `abicheck/schemas/__init__.py`'s
`REPORT_SCHEMA_VERSION` (do not hand-copy its current value here — see
`docs/AGENTS.md`'s rule against hand-copying a volatile count), which must
gain at least a MINOR bump, and
`abicheck/schemas/compare_report.schema.json` must add `evidence_provenance`
to its `Change`-object definition — mirrored, per the repo's existing
convention for that file, into `docs/reference/schemas/v1/
compare_report.schema.json` (`scripts/publish_schemas.py`) in the same PR.
`site/` is build output (mkdocs-generated, gitignored — confirmed no
`site/` path is tracked in this repo and `publish_schemas.py` only writes
`docs/reference/schemas/v1/`), so there is no `site/reference/schemas/v1/`
tracked file to update; it regenerates from the `docs/` mirror on the next
`mkdocs build`.
`_baseline_finding_dicts` feeds `SCAN_SCHEMA_VERSION` (also in
`abicheck/schemas/__init__.py`, same "don't hand-copy the current value"
rule) the identical way — that format has
no separate published `.schema.json` mirror today (confirmed: no
`scan_report.schema.json` exists under `abicheck/schemas/` or
`docs/reference/schemas/v1/`), so only the version constant itself needs
bumping for that builder, not a schema file that doesn't exist. Landing
`evidence_provenance` in any of these three dict builders under an
*unchanged* schema version would let a new field appear to consumers doing
schema-version-gated feature detection as if it had always been there.

**Docs-ownership registration (`docs/AGENTS.md`'s topic-ownership
contract):** `evidence_provenance` is a new public-facing report field, so
this phase must register it in `docs/_meta/topics.yaml` in the same PR, not
defer it. The natural home is the existing `evidence-model` topic
(`canonical_page: learn/evidence-and-detectability.md`) — the field is
squarely evidence-tier vocabulary, not a new topic, and `docs/AGENTS.md`
already says not to add a fourth page to that deliberately three-page trio.
Extend its `fact_sources` list with the modules that actually produce and
render the field (the detector modules Phase 1 wires, and whichever of
`_change_to_dict`/`_leaf_entry`/`_baseline_finding_dicts` or their `report/`
successors carry it per Phase 3) rather than creating a separate topic; add
a short description of the field to `learn/evidence-and-detectability.md`
itself as part of this phase's PR.

SARIF and JUnit rendering had not migrated into `report/` at the time this
plan was written (only JSON and text renderers had) — reaches `sarif.py`'s
existing `properties` bag (one entry, not a new top-level SARIF concept) if
that's still the live SARIF renderer at implementation time, or `report/
render_sarif.py` if that migration has landed by then. **JUnit is not out
of scope here — `junit_report.py`'s `_add_contract_properties` already
projects per-finding contract detail onto each `<testcase>`'s `<properties>`
block (`abicheck.contract_relevance`, `abicheck.contract_evidence_refs`,
...), mirroring `reporter.py`'s JSON `properties` bag and `sarif.py`'s own
one-to-one — so this phase adds `abicheck.evidence_provenance` there the
same way: append the property only when the value is non-`None`, mirroring
`contract_evidence_refs`' own append rule exactly.** Unlike
`contract_evidence_refs` — which stays `None` on every finding unless the
run was given `--contract`, so its own append rule *does* keep a
pre-`--contract` JUnit report byte-for-byte unchanged — `evidence_provenance`
is Phase 1's whole point: once that phase lands, it is populated on every
finding regardless of `--contract` (see Phase 1's own vocabulary section
above and Phase 3's four-projection coverage below), so this addition
changes JUnit output for the ordinary, contract-free case too, not only the
`--contract` one. That is an intentional, in-scope consequence of adding a
new always-computed field to a public report format, not a defect — but it
means this phase's JUnit change is a real, unconditional schema-shape change
to every emitted `<testcase>`, and must be called out as such (the same
`REPORT_SCHEMA_VERSION`/topic-registration discipline the JSON side already
requires below applies here too), not described as a change confined to
`--contract` runs.
generated docs (`scripts/gen_detector_spec.py`'s matrix gains a column once
every kind has a real, non-`UNVERIFIED` classification — gated on Phase 2's
completeness test, so the docs generator cannot claim more coverage than
actually exists).

### Phase 4 — one real consumer (M, optional/stretch)

Explicitly **not required** for this plan's acceptance criteria, named here
so a future PR doesn't have to re-derive the target: once real, per-finding
values exist, `evidence_status_for_result`'s report-level `ARTIFACT_PROVEN`
→ `UNATTRIBUTED` downgrade (the mechanism `AGENTS.md`'s entry investigated
and found too coarse) can be re-scoped from "was the *whole comparison*
header-only" to "was *this finding* header-only, uncorroborated" — a
strictly more precise version of the same signal, using data this plan
produces rather than requiring new extraction.

## Design

Almost no new extraction. Every phase above threads a fact the producing
code already has in scope (which detector module ran, which snapshot field a
`RecordType`/`Function` value came from) into the existing `Change(...)`
call — the same "provenance plumbing, not a new evidence source" framing
`AGENTS.md`'s own entry already used to size this at "multi-day, not a quick
fix": the *volume* of call sites is the cost, not any single site's
complexity. **One real exception, stated in full in Phase 1 step 2 above and
repeated here so this section doesn't contradict it**: the L2 header-derived
slice cannot read per-record DWARF-backfill provenance today —
`DwarfLayoutCoherence.matched` is computed and discarded, and `RecordType`
carries no per-instance producer/backfill marker — so that one slice needs a
small, additive extraction/model change (thread `.matched`, or an equivalent
per-record marker, onto the record or a snapshot-level lookup) *before* its
detector wiring starts, scoped as its own reviewed sub-step, not folded
silently into "no new extraction."

Deliberately reuses `contract_evidence_refs`' shape (flat string-tuple, not
a nested dataclass) rather than introducing a second typed provenance model
alongside `contract_relevance_types.py` — a `Change` gains one more optional
tuple field, not a new sub-object graph to keep in sync with serialization
(wherever `Change.to_dict`/`from_dict` canonically live at implementation
time), SARIF/JSON rendering, and every existing consumer.

## Files & surfaces

Named per their flat, pre-migration location, since that is what exists
today; each bullet's own ADR-061 target package is the actual implementation
owner once that migration reaches it — see the phase-by-phase routing notes
above, which this list intentionally does not re-duplicate.

- `abicheck/checker_types.py` (→ `model/findings.py`) — the new field.
- `abicheck/diff_helpers.py`'s `make_change()` (→ `compare/`) — no signature
  change needed; named here because its `**change_kwargs` forwarding is
  exactly why most call sites need no factory-level change, only a passed
  value.
- `abicheck/diff_symbols.py`, `diff_types.py`, `diff_platform.py`,
  `diff_versioning.py`, `diff_vtable_layout.py`, `diff_sycl.py`,
  `diff_stdlib_impl.py`, `diff_type_spellings.py` (→ `compare/detectors/*`)
  — Phase 1's `make_change()`-routed call sites.
- `abicheck/checker.py`, `internal_leak.py`, `pattern_verdicts.py`,
  `post_manifest.py`, `post_processing.py`, `post_processing_reachability.py`,
  `stack_checker.py`, `versioned_symbol_scheme.py`,
  `cli_buildsource_helpers.py`, `bundle_models.py` (→ `compare/`/
  `workflows/`, module-dependent) — Phase 1's direct-construction sites.
- `abicheck/bundle.py`, `bundle_signature_evidence.py`,
  `bundle_multibuild.py`, `product_baseline.py` (→ `compare/`/`workflows/`,
  module-dependent) — Phase 1's `BundleFinding(...)` construction sites
  (see the grep-blind-spot note above); `bundle_models.py`'s own
  `BundleFinding`/`to_change()` is the sibling data-carrier entry already
  listed one bullet up, not a duplicate of this one.
- `abicheck/buildsource/*.py` (stays `buildsource/`, per its own scoped
  `AGENTS.md` — not part of the `compare/` migration) — the L3-L5 detectors
  already carrying `evidence_category`.
- `abicheck/reporter.py` — Phase 3's real `Change`→dict projection point
  (`_change_to_dict`, `_leaf_entry`, `_out_of_surface_entry`,
  `_add_reconciled`, `_filtered_internal_entry`, `_suppressed_change_entry`)
  — not `abicheck/report/render_json.py`/`document.py`, which only
  serialize an already-built mapping and never project a `Change`
  themselves (see Phase 3's own correction above). If `report/`'s
  migration has absorbed one or more of these six builders by
  implementation time, the successor module is the surface instead — same
  rule as Phase 3's own "cover every current projection, not whichever one
  happens to be literally named `report/`."
- `abicheck/cli_scan_baseline.py`'s `_baseline_finding_dicts` — `scan
  --against`'s own, separate projection; not reached by any of the six
  `reporter.py` builders above.
- `abicheck/sarif.py`'s `properties` bag and `abicheck/junit_report.py`'s
  `_add_contract_properties` — Phase 3's SARIF/JUnit surfaces (or their
  `report/`-migrated successors, e.g. `report/render_sarif.py`, if that
  migration has landed by implementation time).
- `tests/test_evidence_provenance_completeness.py` — Phase 2 (new).
- `scripts/gen_detector_spec.py`, `docs/reference/detector-spec.md` — Phase 3.

## Tests

- Phase 2's completeness gate (above) — the primary regression backstop,
  same shape as `test_canonical_finding_id_completeness.py`.
- Per-slice: existing detector-oracle tests (`test_detector_oracle.py`) gain
  an `evidence_provenance` assertion alongside their existing `ChangeKind`/
  verdict assertions, for every mutation the oracle already covers — no new
  mutation catalogue, just a wider assertion on the existing one.
- A property test (`test_detector_properties.py`, `slow`) stating the
  general invariant: for any generated snapshot pair, every emitted
  `Change.evidence_provenance` is non-`None` once Phase 1 completes for that
  kind (mirrors the property-test discipline `AGENTS.md`'s "Primitive-level
  property tests" section already establishes for reusable primitives —
  this is the same idea applied to a cross-cutting field rather than a
  merge primitive). **Gated on *every* independent producer of that kind,
  not on "a" producing detector being wired (Codex review) — this plan's
  own Phase 1 item 3 above already names a kind that can be emitted from
  two unrelated modules (e.g. both a `diff_symbols.py` path and a
  `diff_platform.py` path), and Phase 2's own gate is explicitly silent on
  exactly this multi-producer case (see "What this gate does and does not
  prove" above).** Hypothesis's snapshot generation cannot itself target
  "route the mutation through producer B specifically," so this property
  is only trustworthy once the per-kind inventory this plan derives at
  implementation time (Phase 1's own `git grep` inventory) records, per
  `ChangeKind`, the complete set of call sites that can emit it — the
  property is then enabled for a kind only once every entry in that
  kind's own set is wired, not the first one. Until a kind's full producer
  set is known and fully wired, the assertion stays scoped to the specific
  call path a given generator/mutation actually exercises (a path-specific
  assertion) rather than asserted as a kind-wide invariant it cannot yet
  back.

## Example fixtures

None new required — every existing detector-oracle/example fixture already
exercises the call sites this plan wires; the assertions widen, the fixtures
don't need to.

## Effort & risk

**XL**, genuinely multi-day per the investigation this plan formalizes, and
explicitly *not* a single PR — Phase 1 alone is bounded at "one detector
module's slice per PR, gated on that slice's own FP-rate/mutation-score
re-run" specifically to keep each PR's blast radius reviewable, per the
`AGENTS.md` incident history this document already cites twice. Risk is
**medium-high** on the L2 slice specifically (Phase 1 step 2) — the same
class of subtlety the layout-finding investigation surfaced (correct
provenance varies per-instance, not just per-kind) is most present there.

## Out of scope

- **Phase 4's `evidence_status_for_result` re-scoping** — real, but a
  separate change to a separate, already-shipped consumer; not required to
  call this plan's own acceptance criteria met.
- **A UI/report-rendering redesign** around the new field — Phase 3 adds it
  to existing surfaces (JSON, SARIF) unchanged in shape otherwise.
- **`abicheck/compat/cli.py`'s ABICC-compatible surface** — that format has
  its own, externally-defined schema (ABICC parity) with no slot for this;
  not extended here.
- **Retroactively re-verifying every closed `AGENTS.md` known-gap entry**
  this plan's provenance model could theoretically help diagnose faster in
  the future (the toolchain-identity-probe gap, the linkage-blind-removal
  gap) — those stay exactly as documented; this plan does not attempt to
  close them as a side effect.

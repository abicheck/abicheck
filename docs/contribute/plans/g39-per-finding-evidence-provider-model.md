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
snapshot's own `dwarf_layout_coherence`/`ast_producer` fields and guessing
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

**Normalization is part of the contract, not left to each call site
(CodeRabbit review).** A single detector's own construction site never
needs to dedupe (it names each provider it consulted exactly once), but
two paths this plan already describes genuinely can produce a duplicate or
differently-ordered entry for the same logical set: `versioned_symbol_
scheme.py`'s `_build_scheme_advisory()` roll-up (Phase 1 item 4 below)
unions the `evidence_provenance` of every matched `Change` in `matched_
removed`/`matched_added`/`matched_renamed`, where two constituent findings
can legitimately share the identical provider string (e.g. both matched
via the same `both:l0:elf_symtab` symbol-table check); and a versioned-
symbol roll-up more generally can re-order relative to whatever sequence
its constituents happened to iterate in. An unnormalized union risks
`("both:l0:elf_symtab", "both:l0:elf_symtab")` or a value that differs only
in entry order between two otherwise-identical runs — unstable JSON/SARIF/
JUnit output for the same underlying comparison, which this plan's own
Phase 2 completeness gate and Phase 3 report-golden tests would then have
to tolerate as noise instead of catching as a real regression. **Rule:
every constructor of a non-`None` `evidence_provenance` — a single-source
detector site, a `BundleFinding` lowering, and every roll-up/union site in
Phase 1 item 4 alike — MUST return it deduplicated and sorted
(`tuple(sorted(set(entries)))`), never a raw union or an as-iterated
sequence.** Sorting is plain lexicographic string sort (no locale
dependency, no custom vocabulary-aware ordering) — the side/tier/backend
segments are prefix-delimited (`old:`/`new:`/`both:`, then `l0:`…`l5:`/
`corroborated:`), so a lexicographic sort already groups entries in a
stable, predictable way without needing a bespoke comparator. This applies
uniformly at construction, at every roll-up/union point, and is preserved
(never re-ordered or re-deduplicated differently) by every downstream
renderer (JSON, SARIF, JUnit) — a renderer emits the tuple as given, it
does not itself sort or dedupe. Phase 2's completeness gate gains an
assertion that every non-`None` `evidence_provenance` it sees is already in
this normal form, so a call site that unions two tuples without
normalizing fails the gate rather than shipping non-deterministic output.

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
| `l0:elf_dynamic` | ELF `.dynamic` section entries read directly (not via the `.dynsym` export table `l0:elf_symtab` names) — `ElfMetadata.soname`/`DT_SONAME` is the first consumer (Codex review, PR #866 round 27; see `check_soname_bump_policy()` below) | L0 (new) |
| `l1:dwarf` | DWARF debug info (ELF) | L1 |
| `l1:pdb` | Windows PDB debug info (PE snapshots) | L1 |
| `l1:btf` | Linux kernel BTF debug info (ELF snapshots) | L1 |
| `l1:ctf` | Linux kernel CTF debug info (ELF snapshots) | L1 |
| `l2:castxml` / `l2:clang` | Header-AST backend, named specifically (not just "L2") since the two backends have measurably different fact completeness — see [header-backend-capabilities.md](../../reference/header-backend-capabilities.md) | L2 |
| `l2:legacy_unknown_backend` | A header snapshot persisted before `ast_producer` was tracked (`from_headers=True` recorded/confirmed, not merely guessed — i.e. `from_headers_inferred is not True` — with `ast_producer is None`; see the gap note below and `snapshot_backend_tag`'s item 4 further down for the excluded, genuinely-ambiguous `from_headers_inferred=True` case) | L2 |
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

**A legacy header snapshot has no truthful `l2:castxml`/`l2:clang` value to
emit at all, which the vocabulary above has no entry for (Codex review,
verified against `abicheck/serialization.py::snapshot_from_dict`).** A
persisted header baseline from before `ast_producer` was tracked (schema
v9 and earlier — the version number is owned by `abicheck/serialization.py`'s
own `SCHEMA_VERSION` history comment, not this plan; re-check that comment
rather than trusting this prose if the two ever disagree) round-trips as
`from_headers=True` — a real, deliberately
explicit fact `snapshot_from_dict` preserves rather than discards — with
`ast_producer=None`: not "not a header snapshot," but "a header snapshot
whose backend was never recorded." Phase 1's `AbiSnapshot.ast_producer`
derivation (point 1 above) enumerates only `"castxml"`/`"clang"`/`"hybrid"`
/`None`-meaning-non-header, so it has no truthful string for this real,
supported case; the "non-`None` once Phase 1 completes" property test in
the Tests section below would then force implementation to either
fabricate a backend (a false claim, the same failure mode the L1 gap
above and the `searched:` shape both already refuse to accept) or leave
the finding's provenance uncomputed, silently failing the very
completeness contract this field exists to guarantee for an otherwise
ordinary, comparable header-derived finding. **Resolution: a fourth,
explicit `l2:` value, `l2:legacy_unknown_backend`** — added to the
vocabulary table above alongside `l2:castxml`/`l2:clang`, for exactly the
`from_headers=True, ast_producer is None` case — carries the honest,
weaker claim "produced by *some* header-AST backend, which of the two is
not recorded" rather than either fabricating a specific one or leaving the
field empty. The property test's "non-`None`" invariant is satisfied
truthfully by this value without widening what `l2:castxml`/`l2:clang`
themselves are allowed to mean; a migration that backfills `ast_producer`
onto old persisted snapshots (recovering the real answer where it can be
determined) remains the better long-term fix and is not precluded by
adding this fallback, but is a separate, opt-in effort this plan does not
require Phase 1 to block on.

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
platform,build,source}.py` eventually migrates too.

**That `use_case_impact.py` citation is itself wrong, and the mistake is
worth naming rather than quietly dropping (Codex review, fresh evidence,
PR #866 round 37, confirmed by reading the module directly).**
`UseCaseChange(...)` (lines ~313-317 of that file, inside
`build_use_case_impact()`) is not a `Change`-construction site at all — it
is a *projection*: it copies three fields (`symbol`, `kind.value`,
`report_finding_id(change)`) off an already-constructed `Change` that
already carries its own `evidence_provenance` decision from whichever
Phase 1 site produced it, into a smaller, differently-shaped carrier
dataclass with no `evidence_provenance` field of its own, which
`UseCaseImpact.to_dict()` then serializes under the report's
`use_case_impact.by_use_case` key. Its only reason for appearing in this
paragraph is that the *text* `"UseCaseChange("` contains the substring
`"Change("`, so the grep this section builds toward matches the line
regardless of the two pathspecs' directory depth — a genuine grep hit, but
a false positive for "needs Phase 1 wiring." Sending it to Phase 1 (supply
a fresh `evidence_provenance` here) would be wrong twice over: there is no
new evidence decision to make at this call site, and the module already
has the value it should be projecting — the participating `change`'s own
`evidence_provenance` — sitting unused one line above the construction.
The real gap is downstream: `UseCaseImpact.to_dict()` is an eighth
`Change`→dict projection this plan's inventory was missing entirely, the
same shape as the seven families Phase 3 already enumerates. See that
phase's own correction below and the matching "Files & surfaces" entry —
this paragraph's own point about the two glob patterns missing nested
files still stands (`buildsource/*.py`'s several dozen sites above already
establish it), it just needed a construction-site example, not a
projection-site one; the module remains a valid illustration of "the old
globs would miss this file entirely," it is only mis-filed as Phase 1
rather than Phase 3.

**Fresh evidence after that correction (Codex review, PR #866 round 20): `'abicheck/**/*.py'`
*alone* has the opposite problem — verified directly with `git grep`
(2.43.0) against this repo, it matches only *nested* files
(`abicheck/buildsource/*.py`, `abicheck/impact/*.py`, ...) and matches
**zero** flat, root-level `abicheck/*.py` files at all, `diff_atomic.py`/
`diff_layout.py` included (`git ls-files 'abicheck/**/*.py'` confirms
this directly: every result is one directory level deep or more, never a
bare `abicheck/<name>.py`). So the single recursive glob this section
told a reader to switch to *drops the very "flat `diff_*.py`" family its
own prose claims it covers* — the flat root files need `abicheck/*.py`
running alongside `abicheck/**/*.py`, not instead of it, since neither
pattern alone covers both depths.** Use
`git grep -n "Change(" -- 'abicheck/*.py' 'abicheck/**/*.py' ':(exclude)abicheck/checker_types.py' ':(exclude)abicheck/diff_helpers.py'`
(both root-level and nested: covers the flat `diff_*.py`/`buildsource/*.py`
modules, any migrated `compare/`/`workflows/` package, and every other
first-party subtree alike; `tests/` is a separate tree entirely and is not
matched by either pathspec regardless) — the two `:(exclude)` pathspecs
are what actually drop `checker_types.py`'s own `class Change` definition
and `diff_helpers.py`'s own factory body from the results; a bare
`'abicheck/*.py' 'abicheck/**/*.py'` glob pair with no exclusion would
otherwise report both of those definition sites themselves as
"producers," for direct constructions; and
`git grep -n "make_change(" -- 'abicheck/*.py' 'abicheck/**/*.py' ':(exclude)abicheck/diff_helpers.py'`
(same reasoning — excluding `diff_helpers.py`'s own `def make_change(`
definition) for factory calls. **A third grep is needed for a wrapper
producer like `bool_transition()` (`diff_helpers.py`): a call site that
routes through such a helper (e.g. `diff_symbols.py`'s and
`diff_hidden_friends.py`'s own `bool_transition(...)` calls) contains
neither literal `"Change("` nor `"make_change("` text — the construction
happens inside the wrapper's own body — so neither grep above finds that
specific line even though the *file* it's in is still caught via its
other, direct calls. Run
`git grep -n "bool_transition(" -- 'abicheck/*.py' 'abicheck/**/*.py' ':(exclude)abicheck/diff_helpers.py'`
too, and generalize the same check to any other reusable
`Change`-producing wrapper this repo grows beyond `bool_transition`.**

**A third category this grep pair does not catch at all (Codex review,
verified against the code): `bundle_models.BundleFinding` construction
sites.** `BundleFinding` (`bundle_models.py`) is a separate dataclass, not
a `Change` — its own `to_change()` method lowers it into a `Change` only at
report time — so a bare `git grep -n "Change("` never matches the literal
text `"BundleFinding("` at all (the substring `Change(` does not occur in
it), even though every `BundleFinding` becomes a real, user-visible
`Change` once `to_change()` runs. Search separately with
`git grep -n "BundleFinding(" -- 'abicheck/*.py' 'abicheck/**/*.py' ':(exclude)abicheck/bundle_models.py'`
(the same root-level-plus-recursive pathspec pair used above, since all
four real construction sites below are flat `abicheck/*.py` files — a
bare `'abicheck/**/*.py'` alone matches none of them and returns zero
results, confirmed by running it; adding `'abicheck/*.py'` finds all 16),
excluding `bundle_models.py`'s own dataclass definition. The real
construction sites are `bundle.py`, `bundle_signature_evidence.py`,
`bundle_multibuild.py`, and `product_baseline.py`. This is not merely a naming gap in the
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
`BundleSignatureEvidence` field — so `find_unverified_signature_findings`
reads that field directly instead of re-deriving anything from a snapshot
it no longer has. This is a real, if small, implementation change
`BundleSignatureEvidence` itself needs (a new field, populated in
`from_snapshot()`), not merely a documentation correction — recorded here
so Phase 1's implementation doesn't discover the gap only after already
committing to deriving `:backend` at the finding-construction site, where
the information no longer exists. **This value must survive `to_change()`'s
own lowering unchanged, not be silently dropped or overwritten** — the same
requirement the surrounding paragraph already states for `BundleFinding.
evidence_provenance` in general, restated here because a `searched:`-
shaped value is exactly the kind of "no fact, just a negative result"
entry a naive lowering step could mistake for "nothing to carry" and omit.

**A single snapshot-level `evidence_backend_tag: str` (or a fixed small
tuple) is the wrong shape for this field — it must be per-symbol, keyed by
mangled name (Codex review, verified against the real code: `dumper_hybrid.
_merge_functions()`/`fact_provenance.py`).** A `--ast-frontend hybrid`
snapshot does not have one backend "as a whole" — `_merge_functions()`
keeps every castxml declaration as primary and separately
`merged.extend(clang_only)`s any function clang saw that castxml didn't,
recording which backend produced *each individual declaration* in
`provenance[func_fact_key(mangled, "visibility")]` (`"castxml"` vs.
`"clang"`, `dumper_hybrid.py`'s own comment: `"visibility" records which
backend contributed the DECLARATION ITSELF`). Carrying a single
`("castxml", "clang")` pair forward from `from_snapshot()` would claim both
backends were searched for every symbol, which is false for a clang-only
declaration (only clang actually looked at it); carrying one backend
mislabels whichever symbols came from the other. Since
`fact_provenance`'s `"visibility"` key is exactly the per-declaration
answer this paragraph's derivation needs, and it is still available inside
`from_snapshot()` before the full snapshot is discarded, the field must be
`evidence_backend_tag: dict[str, str]`, keyed by the same mangled name
`function_map`/`variable_map` already use. **The lookup must branch on
which map retains the symbol, not always use `func_fact_key` (Codex
review, verified against the real code, PR #866 round 21):**
`dumper_hybrid.py`'s own `_merge_variables()` sibling records a variable
declaration's producer under `var_fact_key(v.mangled, "visibility")`, a
distinct key namespace from `func_fact_key` (`fact_provenance.py`'s two
helpers prefix the same mangled name differently precisely so a function
and a variable sharing no relationship don't collide in one dict) — so
for a symbol retained via `AbiSnapshot.variable_map`, populate the entry
by reading `snap.fact_provenance[var_fact_key(mangled, "visibility")]`
instead; only a symbol retained via `function_map` reads
`func_fact_key(mangled, "visibility")`. Deriving every entry through
`func_fact_key` unconditionally would silently miss every real per-variable
provenance record (the key would never exist in `fact_provenance` for a
variable) and fall back to the snapshot-wide `ast_producer` for every
insufficient hybrid variable signature, masking exactly the
castxml-vs-clang distinction this field exists to carry. (Falling back to
the snapshot's own single `ast_producer` still applies, for either map, on
a non-hybrid snapshot where no per-declaration `"visibility"` entry is
ever written — **except when the retained entry's own `visibility` is
`Visibility.ELF_ONLY` (Codex review, verified against the real code, PR
#866 round 23): that entry has no header-AST declaration to attribute a
backend to in the first place** (`_symbol_evidence_sufficient`'s own
docstring above: "an L0-only entry with no corroborating declaration at
all"), on a hybrid snapshot or otherwise, so `fact_provenance` never holds
a `"visibility"` key for it regardless of `ast_producer`'s value — falling
back to `ast_producer` here would misreport a symbol-table-only entry as
`l2:castxml`/`l2:clang`/`l2:legacy_unknown_backend`, fabricating header-AST
provenance for a declaration no header-AST backend ever produced. This case
is handled separately below, alongside the identical mistake the
snapshot-wide fallback made for the same `Visibility.ELF_ONLY` shape.) Not
a single scalar the multi-backend case was assumed to fit into a tuple, and
not a single helper the two symbol kinds were assumed to share.

**Item 3's own case (a) is two evidentiarily distinct shapes, not one, and
conflating them was the round-22 fallback's own mistake — corrected here
(Codex review, verified against the real code, PR #866 round 23).** "The
symbol is absent from `function_map`/`variable_map` entirely, or carries
`Visibility.ELF_ONLY`" reads as one case, but `_symbol_evidence_sufficient`'s
own body (quoted above) treats them completely differently: an
`ELF_ONLY`-visibility entry is `fn is not None`/`var is not None` — a real,
present map entry, just one built directly from the ELF symbol table with no
corroborating declaration (`dumper_elf_fallback.py`, or any other backend
that degrades to it) — while a symbol truly absent from both maps has no
entry to inspect at all. The two need different tags, and routing both
through the same fallback (as round 22 did) produces a wrong answer for the
`ELF_ONLY` half specifically:

- **`Visibility.ELF_ONLY` (a real map entry, L0-only)** never reaches
  `evidence_backend_tag`'s `ast_producer` fallback or `snapshot_backend_tag`
  at all — the preceding paragraph's revision above routes it to a positive,
  correct L0 tag instead, since this case *positively knows* its only
  evidence is the binary's own export table: `l0:elf_symtab` /
  `l0:pe_export_table` / `l0:macho_exports`, selected from
  `AbiSnapshot.platform` (`"elf"`/`"pe"`/`"macho"`, `model.py`) the same way
  Phase 0's vocabulary table already distinguishes those three per-platform
  L0 providers. Whether the snapshot happens to be a header/DWARF-aware
  build overall is irrelevant here — `ELF_ONLY` visibility on *this specific
  declaration* means no header-AST or debug-info fact ever corroborated it,
  regardless of what the rest of the snapshot contains.
- **Genuinely absent from both maps** — no declaration of any kind retained
  for this symbol — is the one shape with no per-symbol entry to fall back
  from at all, and `from_snapshot()` discards the full `AbiSnapshot`
  immediately afterward, so nothing later in the pipeline can re-derive
  anything for it from `snap.function_map`/`snap.variable_map`/
  `snap.fact_provenance`. This is the shape that genuinely needs a second,
  snapshot-wide fallback field, `BundleSignatureEvidence.from_snapshot()`
  retaining `snapshot_backend_tag: tuple[str, ...]` (a tuple, not a bare
  `str` — see below for why), read only when `mangled not in
  evidence_backend_tag`. Without it, `find_unverified_signature_findings`'s
  finding-construction site would have no honest `:backend` value to emit
  for this exact, explicitly-documented finding shape — the same "no fact,
  just a negative result" trap this section's `:searched:` vocabulary
  already exists to avoid, reached from a different structural direction.

**`snap.ast_producer` alone cannot back `snapshot_backend_tag`, for the
reason the field's own docstring already states (Codex review, verified
against `model.py`'s `ast_producer` field comment: "None for non-header
snapshots (DWARF/symbols-only) and for snapshots predating this field").**
`ast_producer` is `None` in at least three genuinely different situations,
not one — an ordinary DWARF-only ELF snapshot, a PDB-derived PE snapshot, a
BTF/CTF-derived kernel snapshot, and a symbols-only (`elf_only_mode=True`)
dump all leave it `None`, exactly as much as the pre-schema-v10 legacy
header case (**not v25** — `abicheck/serialization.py`'s own `SCHEMA_VERSION`
history comment, the canonical source for every version number cited in
this section, dates `ast_producer`'s introduction to schema v10,
"`--ast-frontend hybrid` (G28 Phase 3) — `AbiSnapshot.ast_producer`"; v25 is
an unrelated later migration, `typedefs_qualified`, and does not apply
here — re-check that comment rather than trusting this prose if the two
ever disagree) does. Retaining
only `ast_producer` therefore cannot distinguish "this snapshot never had a
header-AST layer to search" (which needs an L0 or L1 tag, never an L2 one)
from "this snapshot did have a header-AST layer, but it predates
`ast_producer` being tracked" (which needs `l2:legacy_unknown_backend`) — a
`None` value means one of at least four different things, and a single
`snap.ast_producer` copy cannot tell them apart. `snapshot_backend_tag` must
instead be **derived** from the combination of fields that together do
distinguish them, all real, already-present `AbiSnapshot` fields:
`elf_only_mode`, `from_headers`, `ast_producer`, and `platform`:

1. `elf_only_mode is True` — the whole snapshot was dumped without headers
   or debug info; every declaration is `ELF_ONLY` provenance by
   construction (`model.py`'s own field comment). Tag: the same
   platform-selected L0 provider used for the per-declaration `ELF_ONLY`
   case above (`l0:elf_symtab`/`l0:pe_export_table`/`l0:macho_exports`).
2. `from_headers is True` **and `from_headers_inferred is not True`** — a
   header-AST layer was genuinely used (either recorded verbatim from a
   schema carrying the explicit `from_headers` key, or confirmed by the
   loader without guesswork). Read `ast_producer`:
   - `"castxml"` → `("l2:castxml",)`.
   - `"clang"` → `("l2:clang",)`.
   - `"hybrid"` → **`("l2:castxml", "l2:clang")` — both, not the bare string
     `"hybrid"` (Codex review, verified against Phase 0's own vocabulary
     table above, which defines exactly `l2:castxml`/`l2:clang`/
     `l2:legacy_unknown_backend` as the valid `l2:` values and has no
     `l2:hybrid` entry at all).** A `--ast-frontend hybrid` snapshot ran
     *both* backends over the same headers (`dumper_hybrid.merge_snapshots()`
     — see `AbiSnapshot.ast_producer`'s own docstring, "this snapshot was
     built by running BOTH castxml and clang over the same headers and
     merging them field-by-field"), so for a symbol with no per-declaration
     `fact_provenance` entry to attribute to one specific backend, the
     honest claim is that both backends were consulted at the snapshot
     level — exactly what item 3's own `:searched:` vocabulary is for
     (`("both:l2:searched:castxml", "both:l2:searched:clang")` once
     rendered, not a single unregistered `hybrid` token a consumer would
     have no rule to interpret).
   - `None` → `("l2:legacy_unknown_backend",)` — the pre-schema-v10 case
     Phase 0's vocabulary table already names for exactly this shape: a
     schema old enough to predate `ast_producer` tracking, but recorded
     with an explicit `from_headers` key, so the loader did not have to
     guess whether a header-AST layer ran at all — only which backend
     produced it is unknown.
3. `elf_only_mode is not True`, and either `from_headers is not True` **or
   `from_headers_inferred is True`** — a DWARF/PDB/BTF/CTF-derived snapshot
   with no *confirmed* header-AST layer. **This is the L1 producer-identity
   gap this same plan already documents above ("`l1:pdb`/`l1:btf`/`l1:ctf`
   name a real gap, not just a missing table row") and explicitly calls a
   *prerequisite* Phase 1 must resolve before wiring any detector that could
   draw on such a snapshot — it applies here identically, and fabricating
   `l1:dwarf` for a PDB/BTF/CTF-derived snapshot would repeat exactly the
   false-claim failure mode that gap note already refuses to accept.
   `snapshot_backend_tag` for this branch is therefore left unresolved by
   this document — it depends on the same per-record/per-snapshot
   L1-producer marker that gap note calls for, not a fresh design choice
   made here.**
4. **`from_headers is True` and `from_headers_inferred is True` — item 2's
   own guard routes this case into item 3 above rather than an `l2:` tag,
   and this item exists only to state why (Codex review, verified against
   `abicheck/model.py`'s `from_headers_inferred` field comment and
   `abicheck/serialization.py`'s `snapshot_from_dict`, lines ~1113-1132).**
   `from_headers_inferred=True` means `from_headers=True` itself was never
   read from the snapshot — `snapshot_from_dict` *guesses* it for a schema
   that predates the `from_headers` key entirely, on the rule "a populated,
   non-`elf_only_mode` surface (`funcs`/`variables`/`types`/`enums`/
   `typedefs`) looks like a header dump." A legacy **DWARF-only** dump
   populates that identical surface, so the guess cannot distinguish "these
   declarations came from a header-AST parse" from "these declarations came
   from debug info" — `model.py`'s own field comment states this
   explicitly ("Source-level detectors that must only fire on genuine
   header evidence ... require `from_headers and not from_headers_inferred`
   so ambiguous legacy DWARF-only baselines do not produce false API
   breaks"). Reusing item 2's `None → l2:legacy_unknown_backend` mapping
   here (`ast_producer` is always `None` for a snapshot this old, so
   nothing about item 2's own `ast_producer` dispatch would otherwise skip
   it) would claim a header-AST provider produced the snapshot's
   declarations when the true tier is just as plausibly L1 — the identical
   false-claim failure mode item 3 already refuses to accept for
   PDB/BTF/CTF, reached here from the opposite direction (a
   *tier-guessed* snapshot, not a *producer-unidentified* one).

   **Folding this into item 3 without a tag of its own is incomplete, not
   merely conservative (Codex review, fresh evidence, PR #866 round 36).**
   Item 3's own resolution is left unresolved *deliberately*, and only
   because that gap is scoped behind a stated precondition: it "is
   therefore a prerequisite this phase's Phase 1 wiring must resolve
   *before* wiring detectors that could draw on PDB/BTF/CTF-derived
   snapshots" — i.e. Phase 1 simply does not wire a detector against a
   PDB/BTF/CTF snapshot until the L1-producer marker lands, so no reachable
   finding is left without a tag in the meantime. That scoping decision does
   not transfer to this branch. `find_unverified_signature_findings`'s
   `snapshot_backend_tag` fallback (the "genuinely absent from both maps"
   shape discussed earlier in this section) fires unconditionally over
   whatever bundle snapshot pair the bundle pipeline is actually given —
   including an ordinary, currently-supported pre-schema-v10 legacy header
   baseline with no `from_headers` key at all, which is exactly what
   `from_headers_inferred=True` means. There is no equivalent "detectors are
   not yet wired against this snapshot kind" decision to lean on here, so
   routing this case through item 3's still-open fallback leaves a
   reachable, Phase-1-wired finding with no tag to emit at all — silently
   violating the same "non-`None` once Phase 1 completes" property test the
   `l2:legacy_unknown_backend` tag above was invented to satisfy for the
   sibling, non-inferred legacy case.

   **Resolution: a second, explicit fallback tag,
   `unknown:legacy_ambiguous_tier`.** Reserved for exactly this one shape
   (`from_headers is True and from_headers_inferred is True`), and — like
   `external:caller_supplied` in Phase 2 below — deliberately **not** shaped
   like the `<side>:<tier>[:backend]` grammar, since there is no tier to
   name; that is exactly the fact being reported. For the same reason
   `external:caller_supplied` is introduced at its own point of use rather
   than backfilled into the Phase 0 vocabulary table above, this tag is not
   added to that table either — both are top-level, ungrouped sentinels
   documented where they are emitted, not per-tier providers. It carries the
   honest, maximally weak claim available: "this snapshot's declarations
   came from *some* pre-`ast_producer`-tracking legacy layer, and the loader
   could not confirm whether that layer was L1 (debug info) or L2
   (header-AST)" — never a positive tier claim of either kind. It is usable
   immediately, with no further model change required:
   `from_headers_inferred` is already a real, present `AbiSnapshot` field
   (`abicheck/model.py`), so nothing about this branch depends on the
   L1-producer-identity marker item 3 still awaits. Item 3's own PDB/BTF/CTF
   gap remains genuinely open and still blocks Phase 1 wiring for detectors
   that could draw on those snapshot kinds specifically — that scoping
   decision is unchanged by this fix, which narrows only the
   `from_headers_inferred=True` branch item 3 had absorbed too broadly.**

Both fallbacks stay structurally distinct: the per-symbol dict's own inline
fallback (paragraph above) answers "this symbol has a declaration, but no
per-backend attribution was recorded for it"; `snapshot_backend_tag`
answers "this symbol has no declaration in this snapshot at all, so the
best available fact is which backend(s) this snapshot's header-AST (or,
once the L1 gap above closes, debug-info) layer used overall." Neither may
be read as a positive per-declaration fact — every one of the tags derived
above feeds the same `<side>:<tier>:searched:<backend>` "searched, not
found sufficient" shape the surrounding paragraphs already establish, never
a bare provider tag implying the symbol's declaration was actually located.

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
`:tier:`/`:backend`, since no *signature*-evidence source was consulted
for this specific version) — so an implementation cannot accidentally
conflate "we looked and found nothing" with "we couldn't even tell what
to look for."

**`<side>` is not always `both:` for this branch — it must be derived
independently per side, from the two underlying checks, not from the
fact that the branch sets both sufficiency flags together (Codex review,
verified against the real code, PR #866 round 38).** The branch's own
guard is `version_collapsed = _bare_name_version_collapsed(old,
provider_lib, symbol) or _bare_name_version_collapsed(new, provider_lib,
symbol)` — an `or` across two independent, single-side checks, each
reading only its own snapshot's `resolution.provides`. `old_sufficient =
new_sufficient = False` is a *consequence* of that `or` (both flags are
set because the code has nothing narrower to fall back to once either
side is ambiguous), not evidence that both sides actually collapsed —
an entirely ordinary case has only the old side carrying two co-existing
GNU versions of `symbol` while the new side has settled back to one, or
vice versa (a version-script cleanup landing in the same release as an
unrelated signature change). Emitting `both:ambiguous:version_collapsed`
for that case asserts a fact about the *other* side that
`_bare_name_version_collapsed()` was never even true for on that side.
The tag must be derived the same way this document's own `<side>:`
convention is derived everywhere else — per underlying fact, not per
control-flow outcome: `old_collapsed = _bare_name_version_collapsed(old,
provider_lib, symbol)`, `new_collapsed = _bare_name_version_collapsed(
new, provider_lib, symbol)`; emit `both:ambiguous:version_collapsed` only
when both are `True`, `old:ambiguous:version_collapsed` when only
`old_collapsed` is `True`, and `new:ambiguous:version_collapsed` when
only `new_collapsed` is `True`. (`version_collapsed` itself — the `or`
of the two — is unchanged; it still gates whether the branch fires at
all. Only the provenance tag's side prefix is recomputed from the two
underlying booleans instead of assumed.)

**The `:ambiguous:` tag is narrowly scoped to the unresolved signature
question, not a claim that the finding rests on no evidence at all (Codex
review, verified against `_bare_name_version_collapsed`/
`find_unverified_signature_findings` directly, PR #866 round 26).**
`_bare_name_version_collapsed()` itself reads real L0 evidence —
`snapshot.resolution.provides.get(symbol, [])`, the bundle resolution
graph's own per-version `ProviderEntry` list — to establish that more than
one GNU symbol version genuinely coexists; that is what makes the version
ambiguous, not a guess. And by the point a `version_collapsed` finding
reaches this branch, `find_unverified_signature_findings`'s surrounding
loop has already consulted further L0 resolution facts to confirm the
finding's own preconditions: `_symbol_was_exported` (the symbol was a real
old-side export, not an unrelated private declaration),
`_provider_entry_retained_from_old` (this exact version/entry, not a
freshly-added one, has old-side evidence), and the consumer-reachability
chain (`_reachable`, `_consumer_matches_provider`,
`_consumer_retained_from_old` — a real `DT_NEEDED` path and a genuine
old-side match, not a bare name-only pairing). So `evidence_provenance`
for this finding should carry the derived `<side>:ambiguous:
version_collapsed` marker (per the round-38 correction above) *alongside*
— not instead of — side-scoped provenance for that resolution/export
evidence, the same `l0:elf_symtab`/platform-selected tag the rest of this
document already uses for export-table facts, since `BundleSnapshot.
resolution.provides` is itself derived from each library's own export
surface.

**That export-provenance component alone still omits real evidence this
finding actually rests on — the consumer-reachability precondition reads
a structurally different part of the resolution graph, and its own
provenance must be named too (Codex review, verified against the real
code, PR #866 round 38).** `_reachable()` calls `reachable_intra_libraries
(new, lib)`, which walks `snapshot.resolution.intra_needed`/
`soname_to_name` — built from each library's `ElfMetadata.needed`
(`DT_NEEDED`) and `ElfMetadata.soname` (`DT_SONAME`), the same `.dynamic`
-section evidence stream this document's own `l0:elf_dynamic` tag names
(established above for `check_soname_bump_policy()`), not `.dynsym`
export-table data (`l0:elf_symtab`, populated from each library's
`meta.symbols` instead — see `bundle.py`'s `_compute_resolution_graph`).
The two are genuinely independent facts about two different sections: a
symbol can be exported (`l0:elf_symtab`) by a provider a consumer cannot
actually load (no `DT_NEEDED` edge reaches it), and this finding's own
consumer-reachability check exists specifically to rule that case out —
so citing only `l0:elf_symtab` would omit the one piece of evidence that
answers "can this consumer actually reach this provider," leaving the
finding's provenance silent about the fact that establishes the
consumer/provider pairing is real in the first place. `_reachable()` is
only ever called with `new` (line 612 above, `reachable_intra_libraries
(new, lib)`) — there is no equivalent old-side BFS in this function — so
the tag is single-sided: `new:l0:elf_dynamic`, unioned with the
export-provenance tag above. The finding's provenance tuple therefore
documents three things, not two: "the per-version signature could not be
safely attributed" (the derived `<side>:ambiguous:version_collapsed`
tag), "here is the export-table evidence that established the ambiguity
and the retained-export precondition" (`l0:elf_symtab`, side-scoped per
round 26's own reasoning), and "here is the dependency-edge evidence that
established the consumer can actually reach this provider"
(`new:l0:elf_dynamic`) — omitting any one of the three would misstate a
finding that rests on genuine L0 evidence as though nothing had been
consulted at all. This mirrors the same
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
**A completeness gate over construction paths themselves is a required
Phase 2 acceptance check, not optional follow-up work (Codex review, PR
#866 round 22 — see Phase 2's own "What this gate does and does not prove"
paragraph below for why the enum-partition gate alone cannot substitute
for it).** An AST-based `check_ai_readiness.py`-style check — walking
every `Change(...)`/`make_change(...)`/`BundleFinding(...)` construction
site this same Phase 1 section's own grep-based inventory already
enumerates (the three-grep pattern above, generalized to any further
reusable wrapper this repo grows beyond `bool_transition()`), and failing
CI on any site that does not set `evidence_provenance` — is the mechanism
that makes a *second*, unwired producer for an already-classified
`ChangeKind` (the exact gap the enum-partition gate cannot see) a build
failure instead of a silent omission a reviewer has to notice by hand.
Designing the check's own AST-walk implementation (matching this repo's
existing `check_ai_readiness.py` conventions for scanning call sites) is
Phase 2 work, tracked alongside the enum-partition gate rather than as a
separate, unscheduled follow-up — both close a "silent omission" failure
mode PR #753 → #759 already proved this codebase is vulnerable to, and
neither is sufficient without the other (see below).

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
   already requires.

   **First real sub-slice shipped:** `diff_platform_elf_dynamic.
   _diff_security_hardening`'s `STACK_CANARY_REMOVED`/
   `FORTIFY_SOURCE_WEAKENED` (deliberately the narrowest defensible cut of
   this item's own "genuinely static... keeps its one-constant-tuple
   treatment" case — the two hardening kinds this function *also* emits,
   `RELRO_WEAKENED`/`PIE_DISABLED`/`WRITABLE_EXECUTABLE_SEGMENT`/
   `EXECUTABLE_STACK`/`EXECUTABLE_STACK_REMOVED`, were checked against
   `elf_metadata._finalize_hardening` and found to rest on a genuine
   *composite* of ELF program-header/segment flags and `.dynamic`-section
   reads — `relro` alone combines a `PT_GNU_RELRO` segment presence check
   with the `.dynamic` `bind_now` flag — for which the existing vocabulary
   has no clean single tag, and inventing one without the same review
   scrutiny this document's own history shows that needs was left for its
   own dedicated slice rather than guessed at here). `has_stack_canary`/
   `has_fortify_source` are traced precisely to a pure `.dynsym` import/
   symbol-name scan (`_finalize_hardening`'s `names = [s.name for s in
   meta.imports] + [s.name for s in meta.symbols]`), read identically on
   both sides — `("both:l0:elf_symtab",)`, no new vocabulary needed, single
   producer confirmed via `git grep`. `RPATH_CHANGED`/`RPATH_TYPE_CHANGED`/
   `RUNPATH_CHANGED` (the neighboring `_diff_elf_dynamic_section` function)
   were also excluded from this slice: `RPATH_CHANGED` has a *second*,
   Mach-O-side producer (`diff_platform._diff_macho_loader_facts`,
   `LC_RPATH`) with no existing vocabulary entry for that platform's rpath
   evidence — the same "Multi-provider `evidence_provenance`" shape the
   existing `l0:pe_export_table`/`l0:macho_exports` split already
   documents for exports, needing the identical treatment here before
   this kind can be safely reclassified.

   Also extended `tests/_detector_mutations.py`'s shared mutation
   catalogue (previously function/type/enum/variable-only) with an `elf=`
   fragment key and two new binary-level mutations
   (`_m_stack_canary_removed`/`_m_fortify_source_weakened`, both in
   `ASYMMETRIC` — hardening-weakening detectors report only the regression
   direction, per `_diff_security_hardening`'s own docstring) — the first
   `MUTATIONS` entries that mutate `AbiSnapshot.elf` rather than a
   function/type/enum/variable list, satisfying Phase 2's
   `test_every_verified_kind_has_a_mutation_catalogue_entry` gate for both
   kinds. Verified end-to-end: `tests/test_evidence_provenance_completeness.py`
   fails on the pre-fix code (`stack_canary_removed`/
   `fortify_source_weakened`'s real emitted findings carry
   `evidence_provenance=None` despite the PROVENANCE_STATIC
   classification) and passes post-fix.

   **The single code-level provider-ID registry this section's own
   Phase 0 text called for did not exist yet when this slice first
   landed — closed in the same PR (Codex review, PR #900).**
   `model.vocabulary.EVIDENCE_PROVENANCE_TAGS` (a `frozenset[str]`, the
   registry's home per ADR-061's "closed vocabularies" package) is now
   the single source of truth, seeded with the one tag this slice
   introduces (`"both:l0:elf_symtab"`). `tests/test_evidence_provenance_
   completeness.py::TestProvenanceTagsAreRegistered` checks every tag any
   `PROVENANCE_STATIC`/`PROVENANCE_PER_FINDING` kind's real producer
   emits is registered — confirmed to fail when the registry is emptied
   (a real, checked gate, not a decorative frozenset).

   **A second finding on the same review round, verified real and
   deliberately NOT fixed here:** `l0:elf_symtab`'s own vocabulary
   definition (Phase 0's table, above) names `.dynsym`/the export table
   specifically — but `elf_metadata._parse_all_sections()` has a
   documented `.symtab` fallback for relocatable objects (`ET_REL` `.o`
   files, no `.dynsym` at all — a real, reachable path via
   `probe_harness.py`'s "pass a `.o` file through the existing dumper"),
   reusing `_parse_dynsym` to populate `imports`/`symbols` from `.symtab`
   instead. A stack-canary/FORTIFY regression detected between two such
   snapshots would still be stamped `both:l0:elf_symtab`, overclaiming
   `.dynsym`/export-table evidence for what was actually a `.symtab`
   read. Not fixed here: `ElfMetadata` has no field today recording
   which section supplied `imports`/`symbols` — distinguishing the two
   correctly needs a new tracked field (and the serialization/schema
   considerations that come with widening `ElfMetadata`), a real model
   change out of proportion to this slice's own narrow scope, not a
   same-PR reactive patch under review pressure (per this repo's own
   "known gaps over risky reactive patches" convention, `AGENTS.md`).
   Left as a documented, known limitation for whichever slice next
   touches `.symtab`-fallback provenance.

   **Second sub-slice shipped: the remaining hardening kinds in the same
   two detector functions.** `RELRO_WEAKENED`/`PIE_DISABLED`/
   `WRITABLE_EXECUTABLE_SEGMENT`/`EXECUTABLE_STACK`/
   `EXECUTABLE_STACK_REMOVED` were deliberately excluded from the first
   sub-slice above pending a precise trace of their evidence sources
   (`elf_metadata._parse_segments`/`_finalize_hardening`/
   `_parse_dynamic`) — now done. Three are pure ELF program-header/
   segment reads (`WRITABLE_EXECUTABLE_SEGMENT`: `PT_LOAD`;
   `EXECUTABLE_STACK`/`EXECUTABLE_STACK_REMOVED`: `PT_GNU_STACK`) —
   `both:l0:elf_program_headers`, a new tag (ELF program headers,
   distinct from `l0:elf_dynamic`'s `.dynamic`-section reads). Two are
   genuine composites: `RELRO_WEAKENED` combines a `PT_GNU_RELRO`
   segment check with the `.dynamic` `bind_now` flag
   (`both:l0:elf_dynamic`, `both:l0:elf_program_headers`, sorted per the
   normalization rule above); `PIE_DISABLED` combines the `.dynamic`
   `DF_1_PIE` flag with the ELF file header's own `e_type` (`ET_DYN`) —
   `both:l0:elf_dynamic`, `both:l0:elf_header` (`l0:elf_header`, a
   second new tag: the ELF file header itself, distinct from both the
   `.dynamic` section and program headers). `l0:elf_dynamic` itself was
   already named in Phase 0's table (`ElfMetadata.soname`/`DT_SONAME`)
   but had no real producer wired until this slice. All five kinds are
   single-producer (confirmed via `git grep`), added to `PROVENANCE_
   STATIC`, and covered by five new `tests/_detector_mutations.py`
   entries (`_m_relro_weakened`/`_m_pie_disabled`/`_m_writable_
   executable_segment`, both weakening-direction-only and added to
   `ASYMMETRIC`; `_m_executable_stack_introduced`/`_m_executable_stack_
   removed`, NOT asymmetric since the two opposite-direction kinds share
   one symbol — `"PT_GNU_STACK"` — so forward/backward symbol sets
   still agree). `RPATH_CHANGED`/`RPATH_TYPE_CHANGED`/`RUNPATH_CHANGED`
   remain excluded, for the same multi-provider reason the first
   sub-slice's note already gives (a second, Mach-O-side producer with
   no existing per-platform vocabulary entry).

   **`diff_symbols.py` does NOT belong in this
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
     clang, or hybrid, selected per run via `--ast-frontend` and recorded
     on the snapshot as `AbiSnapshot.ast_producer` (the persisted field
     name — there is no `ast_frontend` attribute on `AbiSnapshot`, that
     spelling names only the CLI flag/`ABICHECK_AST_FRONTEND` env var,
     per `abicheck/model.py`), not per detector function. Phase 0's own
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
      `AbiSnapshot.ast_producer` (`"castxml"` / `"clang"` / `"hybrid"` /
      `None` for a non-header snapshot or one predating the field — see
      `abicheck/model.py`'s own field comment), or, for a hybrid snapshot,
      the relevant per-fact provenance via `fact_provenance.py` (mirroring
      slice 1's own hybrid-snapshot carve-out earlier in this document) —
      at the point the finding is constructed, never hard-coded per
      function.
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

   **`_build_scheme_advisory()` is not this list's only many-to-one
   roll-up — `diff_versioning.check_soname_bump_policy()` is the identical
   shape and belongs in this same slice (Codex review, verified against
   the real code, PR #866 round 26).** It, too, receives the complete
   `changes` list (not a single source `Change`) and derives
   `SONAME_BUMP_RECOMMENDED` from whether *any* constituent counts as
   effectively breaking (`has_breaking = any(_is_effectively_breaking(c)
   for c in changes)`, `_is_effectively_breaking` itself reading each
   `Change.effective_verdict`/`.kind`) — the same "summarize N matched
   findings into one advisory" shape `_build_scheme_advisory()` has, just
   over the raw `changes` list rather than a matched-triple mapping. A
   fixed, static `diff_versioning`/ELF-only provenance tuple on the
   resulting `SONAME_BUMP_RECOMMENDED` finding would be wrong whenever the
   breaking constituent that actually triggered the recommendation came
   from L2/L3-L5 evidence rather than ELF alone — the recommendation is
   only ever as trustworthy as the break(s) that justify it, so it needs
   the identical union/rollup treatment: `evidence_provenance` for
   `SONAME_BUMP_RECOMMENDED` is the union of `evidence_provenance` from
   every `Change` for which `_is_effectively_breaking(c)` is `True`, not a
   value fixed to this function's own module/platform. Unlike
   `_build_scheme_advisory()`, `check_soname_bump_policy()` already
   receives the full `Change` list as its first parameter — no signature
   change is needed to reach the constituent findings, only to compute and
   attach the union while building the `SONAME_BUMP_RECOMMENDED`
   `make_change(...)` call.

   **The constituent union is not the whole story for
   `SONAME_BUMP_RECOMMENDED` either — the recommendation itself also
   rests on a direct read of both SONAMEs, the identical `l0:elf_dynamic`
   fact `SONAME_BUMP_UNNECESSARY` below is built on (Codex review,
   verified against the real code, PR #866 round 28).** The `has_breaking
   and not soname_bumped and old_elf.soname` guard reads `old_elf.soname`/
   `new_elf.soname` (via the same `both_have_soname`/`soname_bumped`
   computation `SONAME_BUMP_UNNECESSARY` shares) to establish that the new
   side did *not* bump its SONAME — that comparison is exactly as much a
   deciding fact for this finding as it is for its sibling, and omitting
   it would leave a header-derived break's advisory carrying only L2
   provenance, with no record of the L0 `.dynamic`-section read that is
   what turns "a breaking change exists" into "…and no bump covers it."
   `SONAME_BUMP_RECOMMENDED`'s `evidence_provenance` therefore unions
   **two** components, exactly mirroring `SONAME_BUMP_UNNECESSARY`'s own
   two below: the breaking-constituent union described above, and
   `"both:l0:elf_dynamic"` for the SONAME comparison that gates whether
   the recommendation fires at all.

   **That second component is only correct for one of two sub-cases the
   guard covers, and the function's own `detail` string already
   distinguishes them (Codex review, verified against the real code, PR
   #866 round 34).** `has_breaking and not soname_bumped and
   old_elf.soname` is satisfied two structurally different ways, both
   reachable: (a) `new_elf.soname` is also truthy and, once
   vendor-hash-stripped, equal to `old_elf.soname` — a genuine two-sided
   comparison of two real values, where `"both:l0:elf_dynamic"` is exactly
   right; and (b) `new_elf.soname` is falsy — the new side dropped its
   SONAME entirely, the branch the function's own `detail = f"SONAME was
   dropped (was {old_elf.soname!r})"` string (a few lines above the
   `make_change(...)` call) already narrates separately from case (a)'s
   `detail = f"SONAME remains {old_elf.soname!r}"`. In case (b) the new
   side contributes no SONAME *value* to compare against — reading
   `.dynamic` on that side is still a real, positive read, but what it
   found is an absence, the same old-has-it/new-was-searched-and-lacks-it
   distinction this plan's own `<side>:<tier>:searched:<backend>` grammar
   (established above for a removed symbol's empty side) already exists to
   preserve. Spelling case (b) as `"both:l0:elf_dynamic"` overstates it —
   it reads as "both sides carried a comparable SONAME value," when only
   the old side did. The corrected shape branches on `bool(new_elf.soname)`:
   case (a) keeps `"both:l0:elf_dynamic"` exactly as specified above; case
   (b) emits `"old:l0:elf_dynamic"` (the old side's real, positive SONAME
   value) unioned with `"new:l0:searched:elf_dynamic"` (the new side's
   `.dynamic` section was read and no `DT_SONAME` entry was found) instead,
   never `"both:l0:elf_dynamic"` for that branch. `SONAME_BUMP_UNNECESSARY`
   below is unaffected — it only ever fires when `soname_bumped` is `True`,
   which requires `both_have_soname`, so its own `"both:l0:elf_dynamic"`
   component never reaches this dropped-SONAME branch.

   **`SONAME_BUMP_UNNECESSARY`, the function's other emitted kind, is
   *not* the genuinely evidence-free case round 26 first described it as
   — it rests on real, positive L0 evidence, and treating it as a pure
   `searched:` negative would drop that evidence from the finding (Codex
   review, verified against the real code, PR #866 round 27).**
   `check_soname_bump_policy()` reads `old_elf.soname`/`new_elf.soname`
   directly — real `DT_SONAME` entries from each side's ELF `.dynamic`
   section — computes `both_have_soname`/`soname_bumped` by comparing
   them (vendor-hash-stripped), and only then emits
   `SONAME_BUMP_UNNECESSARY` when `soname_bumped` is `True` alongside
   `has_breaking` being `False`. That comparison is exactly the kind of
   fact this whole model exists to attribute: two concrete field reads,
   not an exhaustive-search-found-nothing result. The existing
   vocabulary had no provider id for it, since `l0:elf_symtab` names the
   `.dynsym`/export-table evidence stream, not the `.dynamic` section —
   closed above with a new `l0:elf_dynamic` entry. `SONAME_BUMP_
   UNNECESSARY`'s `evidence_provenance` carries the positive SONAME-read
   evidence, `"both:l0:elf_dynamic"`, since both sides' `.dynamic`
   sections were read and compared to reach `soname_bumped`. Dropping
   this half — the mistake round 26's framing made — would leave a reader
   unable to tell "this recommendation rests on a real read of both
   SONAMEs" from "nothing here was ever actually consulted," exactly the
   misattribution this model exists to prevent. **Rounds 27–31 below also
   explored unioning a second, negative "no breaking change qualified"
   component onto this one — round 38, at the end of this same discussion,
   retracts that half entirely as an unsound, fabricated claim rather than
   an approximation worth keeping; the final design carries only this one
   positive component. The rounds are kept in sequence below because each
   documents a real, independently-reviewed step in why the negative
   component was rejected, not because the two-component design they
   describe is still current.**

   **The negative half cannot actually be "a `searched:` entry per
   side/tier the comparison's own `changes` list was drawn from," as
   round 27 first phrased it — that derivation is unsound, not merely
   underspecified (Codex review, verified against the real code, PR #866
   round 28).** Two independent problems, not one: first, the degenerate
   but entirely ordinary case where a release bumps its SONAME and the
   comparison detects *no* other change at all —
   `check_soname_bump_policy()` is then called with `changes=[]`, so
   there is no constituent `Change` left to read a side/tier off of; the
   "per side/tier the comparison's own `changes` list was drawn from"
   construction has nothing to construct from. Second, and more
   fundamentally, even a non-empty `changes` list only records findings
   *emitted* by detectors — it is not, and was never meant to be, an
   inventory of which evidence tiers/backends the comparison actually
   consulted. Treating "no constituent qualified" as itself a completed
   `searched:` claim conflates "nothing broke, among what was checked"
   with "here is everything that was checked," which `changes` alone
   cannot distinguish. Item 3's own `searched:` shape does not have this
   problem, because it derives its backend list from the specific
   check's own known consultation (e.g. "which frontend(s) built the
   public-header surface this symbol was searched against"), never from
   an emitted-findings list — round 27's phrasing borrowed the
   `searched:` vocabulary from item 3 without borrowing its actual
   derivation, and that substitution is the bug.

   **Resolution: derive the negative component from the comparison's own
   evidence-tier inventory, not from `changes`.** `DiffResult.
   evidence_tiers` (`checker_types.py`) is exactly this: a comparison-level
   record of which evidence tiers were actually available/consulted (ELF,
   DWARF, header AST, ...), computed by `confidence._detect_evidence_tiers`
   purely from the two `AbiSnapshot` objects being compared — independent
   of what findings were or weren't emitted, and well-defined even when
   `changes` is empty. `check_soname_bump_policy()` does not currently
   receive the snapshots (only `changes`, `old_elf`, `new_elf`), and
   `checker.py`'s call site (line 565) runs before `evidence_tiers` is
   computed (line 1023) — so this is a real, if narrow, signature and
   sequencing change, not just a lookup: thread the two `AbiSnapshot`
   objects into `check_soname_bump_policy()`.

   **`DiffResult.evidence_tiers` itself cannot be read off directly as
   described above without over-claiming — it is a two-sided union, not a
   per-side receipt, and this plan's own mandatory `<side>:` prefix
   convention (established earlier in this document) requires the negative
   component to say which *side* was actually searched, not merely that the
   tier existed somewhere (Codex review, verified against the real code,
   PR #866 round 29).** `_detect_evidence_tiers(old, new)` computes each
   boolean with a bare `or` across both snapshots —
   `has_elf = old.elf is not None or new.elf is not None`, and identically
   for `has_dwarf`/`has_dwarf_advanced`/`has_pe`/`has_macho`/`has_headers` —
   so a tier appearing in `evidence_tiers` proves only "at least one side had
   this evidence," never "both sides did." A `searched:<tier>` entry
   spelled with the `both:` prefix (the natural reading of "we searched this
   tier" for a comparison-scoped claim) would therefore be false whenever
   only one side actually carried that tier's data — an entirely ordinary
   shape, e.g. a DWARF-only baseline compared against a header-parsed live
   binary, or a `scan --against` pairing snapshots taken at two different
   `--depth` levels. Separately, `_detect_evidence_tiers` has no notion of
   L3–L5 (build-context/source-graph) evidence at all — that evidence
   reaches `DiffResult` through the independent `extra_changes` mechanism
   (`checker.py`'s `compare(..., extra_changes=...)` parameter), never
   through this tier inventory — so no `searched:<tier>` entry derived from
   `evidence_tiers` can honestly speak to whether L3–L5 evidence was
   consulted either way. Building a genuine per-side, per-detector "what was
   actually searched and completed" receipt does not exist anywhere in this
   codebase today (`extra_changes` is a plain `list[Change]`, not a
   coverage/completion ledger) — that would be new tracking infrastructure
   on the scale of its own G-numbered plan, not a narrow fix to this one
   finding's provenance, and is explicitly out of scope here.

   **The fix that stays within what the codebase can support today is
   narrower on two axes at once: side-aware, and scoped to exactly the L0–L2
   tiers `_detect_evidence_tiers` actually inventories.** Since
   `check_soname_bump_policy()` is being given the two real `AbiSnapshot`
   objects anyway (not merely the pre-computed, already-unioned
   `evidence_tiers` list), derive tier membership *per side* directly from
   each snapshot's own fields — `old.elf is not None`, `new.elf is not
   None`, and identically for `dwarf`/`dwarf_advanced`/`pe`/`macho`/headers
   — the same predicates `_detect_evidence_tiers` already applies, just
   evaluated once per snapshot instead of OR'd together.

   **The tier and backend segments must be derived per field, not
   hard-coded to `l0` regardless of which field matched (Codex review,
   verified against the real code, PR #866 round 30) — an earlier draft of
   this paragraph wrote the emitted shape as a single `both:l0:searched:
   <tier>` template for every one of `elf`/`dwarf`/`dwarf_advanced`/`pe`/
   `macho`/headers alike, which would mislabel every DWARF- or
   header-only tier as `l0` and would also put the tier name in the
   backend slot instead of naming a real backend.** The correct shape is
   the same `<side>:<tier>:searched:<backend>` vocabulary item 1 above
   already establishes, applied per matched field with `both:` as the side
   prefix: `both.elf` present on both sides emits
   `both:l0:searched:elf_symtab`; `both.pe` emits
   `both:l0:searched:pe_export_table`; `both.macho` emits
   `both:l0:searched:macho_exports`; `both.dwarf`/`both.dwarf_advanced`
   present on both sides emits `both:l1:searched:dwarf`. A field only one
   side carries is either omitted from the negative component entirely (the
   conservative choice — this plan's own `AGENTS.md`-derived preference for
   a documented false-negative gap over a fabricated positive applies here
   too) or, if a single-sided claim is wanted, spelled with the honest
   `old:`/`new:` prefix for that side alone, never `both:`.

   **The header-AST (`l2`) field needs its own, narrower rule than "field
   present on both sides ⇒ `both:`" — `from_headers` being `True` on both
   sides does not mean every backend that produced either side's headers
   ran on *both* sides (Codex review, verified against the real code, PR
   #866 round 31).** `AbiSnapshot.ast_producer` (`model.py`) is a single
   per-snapshot value — `"castxml"` / `"clang"` / `"hybrid"` / `None` — set
   independently on each side, so an asymmetric pairing (old dumped with
   `--ast-frontend castxml`, new with `--ast-frontend clang`, an entirely
   ordinary shape for a `scan --against` a baseline dumped with a different
   default than the live binary's frontend) is not a hypothetical edge
   case. An earlier draft of this paragraph derived "whichever backend(s)
   actually produced it" as the *union* of both sides' backends and
   emitted every member of that union under one blanket `both:` prefix —
   which asserts, falsely, that castxml searched the new side and that
   clang searched the old side, neither of which happened. The fix is to
   reuse this document's own `snapshot_backend_tag` derivation (above,
   `l2` branch) *per side, independently* — expanding `"hybrid"` to both
   `l2:castxml` and `l2:clang` and a legacy `None` (pre-schema-v10) to
   `l2:legacy_unknown_backend`, exactly as that derivation already does —
   to get `old_backends`/`new_backends`, each a tuple of the `l2:<backend>`
   tags that snapshot's own header-AST layer actually used. Only then is
   the `both:`/`old:`/`new:` prefix decided, per backend tag rather than
   per field: a backend present in both tuples emits
   `both:l2:searched:<backend>`; a backend present in only one emits
   `<that side>:l2:searched:<backend>` for that side alone. So
   old-castxml/new-clang emits `old:l2:searched:castxml` +
   `new:l2:searched:clang` (never a `both:` on either); old-hybrid/
   new-clang emits `both:l2:searched:clang` (clang ran on both, once
   directly and once as hybrid's clang half) + `old:l2:searched:castxml`
   (only old's hybrid half used castxml); and old-castxml/new-castxml
   emits `both:l2:searched:castxml` alone, matching the pre-existing
   both-sides-identical case exactly. This is the same "no collapsed
   composite tag" rule item 1 states for its own multi-provider case,
   rather than a single `both:l2:searched:headers` placeholder, now applied
   with the side prefix computed honestly per backend instead of per field.

   This makes the claim actually true in the problem cases round 27 and
   round 28 already identified — non-empty even when `changes == []`,
   independent of which findings were emitted — *and* true in the case
   round 29 adds: it never asserts a side was searched when only the other
   side's snapshot carried the evidence — *and* true in the asymmetric-
   backend case round 31 adds: it never asserts a backend searched a side
   it never actually ran against — and it never asserts anything about
   L3–L5, which this tier inventory has no visibility into at all.

   **The `l0:elf_symtab` tag in this negative component is a deliberately
   different claim from the positive `l0:elf_dynamic` component
   `SONAME_BUMP_UNNECESSARY` already carries above, not a mislabeling of it
   (CodeRabbit review, PR #866 round 31, considered and not applied).** A
   review pass proposed dropping `l0:searched:elf_symtab` from this
   negative component on the grounds that `check_soname_bump_policy()`
   itself reads only `old_elf.soname`/`new_elf.soname` (the `.dynamic`
   section, `l0:elf_dynamic`) and never touches `.dynsym`/export-table data
   directly — true, but not the claim this tag makes. The two components
   answer two different questions, unioned rather than substituted for one
   another, exactly as round 27 above establishes: `l0:elf_dynamic` backs
   *this function's own* SONAME comparison; the negative `searched:`
   component backs the separate, `changes`-independent claim that *other*
   detectors — the ones this policy's `has_breaking` check summarizes,
   which do read `.dynsym`-derived function/variable data — had ELF
   evidence available on the side(s) named. Keeping only `l0:elf_dynamic`,
   as that review pass suggested, would not fix a mislabel; it would delete
   the finding's only claim about why "no other breaking change" is
   trustworthy, regressing exactly the gap round 28 opened this whole
   negative-component design to close. The broader concern underneath the
   proposal — that snapshot-field presence approximates, rather than
   proves, that a specific detector actually consulted that field for this
   comparison — is real and already acknowledged as an accepted limitation
   two paragraphs above ("Building a genuine per-side, per-detector 'what
   was actually searched and completed' receipt does not exist anywhere in
   this codebase today... explicitly out of scope here"); it is not
   specific to `elf_symtab` versus `elf_dynamic` and applies identically to
   every tag this negative component emits, `l1:dwarf`/`l2:castxml`/
   `l2:clang` included, not only the ELF one.

   **Round 31's "considered and not applied" call is itself reversed here:
   the whole field-presence-derived negative component described in rounds
   27–31 above is retracted, not merely the one `elf_symtab` sub-tag that
   round 31 declined to drop (Codex review, verified against the real code,
   PR #866 round 38).** Round 31's own closing paragraph already concedes
   the thing this round treats as decisive: "snapshot-field presence
   approximates, rather than proves, that a specific detector actually
   consulted that field for this comparison" is "real," "not specific to
   `elf_symtab` versus `elf_dynamic`," and "applies identically to every
   tag this negative component emits." A tag spelled `searched:` is not a
   neutral label for "the field happened to be present" — every other use
   of `searched:` in this document (item 1's `l0:searched:elf_symtab` for a
   completed export-table lookup that positively found nothing; item 3's
   `l2:searched:<frontend>` for a specific, known header-AST consultation)
   backs a real, completed check. `check_soname_bump_policy()` never
   queries `.dynsym`, DWARF, or the header AST at all — it reads exactly
   two fields, `old_elf.soname`/`new_elf.soname` — so a `both:l1:searched:
   dwarf` tag attached to `SONAME_BUMP_UNNECESSARY` asserts a DWARF
   consultation that this function never performs and that no other part
   of the pipeline records as completed for this specific comparison. That
   is a fabricated positive dressed as a documented approximation, and this
   plan's own already-stated principle for exactly this shape of choice —
   "a documented false-negative gap over a fabricated positive," invoked by
   name at line 1569 above for the narrower single-sided case — resolves it
   the same way here: **omit the negative component entirely.** Round 31's
   objection to dropping only the `elf_symtab` slice ("would delete the
   finding's only claim about why 'no other breaking change' is
   trustworthy") does not survive contact with this broader retraction,
   because that claim was never soundly available in the first place —
   there being no genuine per-detector completion ledger anywhere in this
   codebase (round 28/29's own finding, unchanged) means no tag in this
   negative component, ELF included, can honestly make it. **Final design:**
   `SONAME_BUMP_UNNECESSARY`'s `evidence_provenance` carries exactly the one
   positive component established at round 27 — `"both:l0:elf_dynamic"` —
   and nothing else; the "no other breaking change qualified" half of the
   finding is not represented in `evidence_provenance` at all, the same way
   an ordinary absence of a finding is never itself an evidence-tag claim
   elsewhere in this model. `check_soname_bump_policy()` therefore still
   needs the two `AbiSnapshot` objects threaded in per round 28's signature
   change (line 1509 above) for the reachability/version-collapse work
   items 4/5/6 in this same section depend on, but the per-side tier
   inventory derived from them (rounds 29–31's `old_backends`/
   `new_backends`, the `both:`/`old:`/`new:` prefix decision, the `l2`
   backend-tuple derivation) is dropped from this finding's own
   `evidence_provenance` — it remains correct, reusable machinery for a
   *different* finding whose provenance genuinely rests on a completed
   per-tier search (nothing else in this section currently needs it, so it
   is not wired anywhere else either), just not evidence this one
   `changes`-independent, two-field SONAME comparison can honestly claim
   for itself.

5. **Cross-detector deduplication (`diff_filtering.py`) is a distinct
   failure shape from the roll-up/transform emitters in item 4 above, and
   this inventory omitted it entirely (Codex review, verified against the
   real code, PR #866 round 40).** `_dedup_exact()`, `_dedup_enum_same_kind()`,
   `_dedup_cross_kind()` (chained by `dedup_and_prioritize()`), and the
   later, separately-run `_deduplicate_cross_detector()` do not build a new
   `Change` by transforming or summarizing an existing one — each keeps
   exactly one already-fully-formed `Change` object from a set of two or
   more that collapse to the same identity, and discards the rest outright.
   `_deduplicate_cross_detector()`'s own docstring names the case this
   plan's earlier phases already document as real: the L1 (DWARF) and L2
   (header-AST) enum detectors independently emit the *same*
   `ENUM_MEMBER_REMOVED`/`ENUM_MEMBER_VALUE_CHANGED`/etc. finding for one
   enum member, "first occurrence wins" (import order puts the DWARF-tier
   finding first), and the header-tier duplicate is dropped. Once Phase 1
   lands, that dropped duplicate is not evidence-free — it is a second,
   independent corroboration of the same finding from a different tier
   (`l1:dwarf` vs. `l2:castxml`/`l2:clang`), and today's "first wins, rest
   discarded" behavior silently throws that corroboration away, leaving the
   survivor's `evidence_provenance` naming only the tier that happened to
   run first. `_dedup_exact()` (same `(kind, description)` key) and
   `_dedup_enum_same_kind()` (same `(kind, symbol)` key, choosing whichever
   entry has populated `old_value`/`new_value` or the longer description)
   have the identical shape: a real dedup decision between two independently
   detected findings, not a lowering of one finding into a report format.

   **This is exactly the gap Phase 2's completeness gate (below) cannot
   catch, which is why it must be called out here rather than left for that
   gate to find**: the gate's own contract (see Phase 2's discussion) checks
   that every kept `Change` has a non-`None` `evidence_provenance` — a
   dedup pass that keeps the first occurrence and drops the rest always
   satisfies that check, since the survivor already carries its own valid,
   non-`None` tuple. The completeness gate has no way to know a second,
   differently-tagged tuple existed on the entry that got dropped.

   **Fix: when two or more `Change`s collapse to one under any of these
   four functions, the retained `Change`'s `evidence_provenance` must be
   the union of the retained and every discarded entry's own tuples**, not
   simply whichever happened to survive the existing selection rule (first
   occurrence for `_dedup_exact`/`_deduplicate_cross_detector`; the
   populated-values/longer-description winner for `_dedup_enum_same_kind`;
   the AST-side survivor for `_dedup_cross_kind`) — the selection rule
   itself is unchanged, only what gets attached to its result changes.
   Concretely: at the point each function currently does `continue`/`pass`
   to drop a losing entry, fold that entry's own `evidence_provenance`
   tuple into the survivor's (deduplicated union, preserving this plan's
   established ordering/no-re-sort convention for the field) rather than
   discarding it unread. `_deduplicate_cross_detector()`'s own L1/L2 enum
   case is the one with real, already-anticipated dual-tier evidence to
   union today; the other three functions' dedup keys are narrow enough
   (exact description match, or a single detector's own multiple emission
   paths) that a union may often be a no-op — verify per function rather
   than assuming every collapse actually has two distinct tags to merge, the
   same case-by-case discipline this whole section already applies
   elsewhere. Needs its own test: two `Change`s built with different
   `evidence_provenance` tuples and a shared dedup key, asserting the single
   surviving `Change` carries both tags after `dedup_and_prioritize()`/
   `_deduplicate_cross_detector()` runs — not merely that a non-`None` tuple
   is present, which the existing completeness-gate style of assertion
   would pass even with the union step missing.

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

**What the enum-partition gate does and does not prove, and why Phase 2
needs a second, required gate alongside it (Codex review, fresh evidence;
strengthened PR #866 round 22 — this used to describe the second gate as
optional follow-up work, which understated the actual gap):** the
enum-partition gate above proves every `ChangeKind` is classified into one
of the three buckets — mirroring the #753 → #759 incident's own lesson (a
missing entry is silent everywhere else, so make the enum itself
un-skippable). It does **not** prove a kind's real producer(s) actually
behave the way its bucket claims — a kind moved to `PROVENANCE_STATIC`/
`PROVENANCE_PER_FINDING` whose producer forgot to actually set
`evidence_provenance`, or whose *second*, independent producer path (e.g. a
kind emitted from both a `diff_symbols.py` path and an unrelated
`diff_platform.py` path) never got wired at all, still passes this gate
outright, since it checks bucket *membership*, not producer *behavior* — and
the property-test/FP-rate/mutation-score gates this plan otherwise leans on
do not close this either, since none of them assert on `evidence_provenance`
specifically and a kind's *secondary* emitter may simply never be exercised
by the existing corpus. Leaving that gap unclosed would let Phase 2 — and
the plan as a whole — be declared complete while a reportable finding from
an unwired secondary emitter still carries `evidence_provenance=None` in
production, silently violating the core guarantee this whole plan exists to
establish.

**Phase 2's second, required deliverable is therefore the construction-path
completeness gate itself** (Phase 1's own inventory section above specifies
the mechanism: an AST-based check, in the shape of
`check_ai_readiness.py`'s existing call-site scans, walking every
`Change(...)`/`make_change(...)`/`BundleFinding(...)` construction site the
three-grep inventory enumerates and failing CI on any site that constructs
one of these without setting `evidence_provenance`). The two gates are
deliberately complementary, not redundant, and Phase 2 is not done until
both exist: the enum-partition gate closes the enum-omission failure mode
(a `ChangeKind` nobody classified at all), the construction-path gate closes
the producer-behavior failure mode (a classified kind whose real emitter, or
one of several, never got wired) — dropping either one reopens exactly the
class of gap PR #753 → #759 already demonstrated this codebase needs a
mechanical check for, not a reviewer's memory.

**The gate as just described covers a `Change`-producing *wrapper* — starting
with `diff_helpers.bool_transition()`, which constructs its `Change(...)`
calls inside its own body — only if its call sites are scanned by name, not
by walking `Change(...)`/`make_change(...)`/`BundleFinding(...)` call
expressions alone (Codex review, fresh evidence, PR #866 round 29).**
`bool_transition()` (`diff_helpers.py`) has no `evidence_provenance`
parameter today, and a call site like `diff_hidden_friends.py`'s own
`bool_transition(...)` call contains none of the three literal construction
forms the gate walks — this is exactly why Phase 1's own inventory section
above needs a *third* grep for `bool_transition(` specifically, and says so
explicitly ("neither grep above finds that specific line even though the
*file* it's in is still caught via its other, direct calls"). An AST walk
that only recognizes `Change(...)`/`make_change(...)`/`BundleFinding(...)`
node shapes inherits the identical blind spot: it would see no
`Change(...)`-shaped node at a `bool_transition(...)` call site and pass it
regardless of whether that call actually threads a real
`evidence_provenance` value through to `bool_transition()`'s own internal
`Change(...)` constructions. Closing this needs one of two things, and the
gate's own implementation must pick one rather than leaving it implicit: (a)
extend the AST walk to also recognize calls to `bool_transition()` and any
further reusable `Change`-producing wrapper this repo grows (the same
generalization Phase 1's own construction-path-gate paragraph above already
states — "the three-grep pattern above, generalized to any further reusable
wrapper this repo grows beyond `bool_transition()`" — restated here because
this section's own, shorter description of the same gate dropped that
qualifier and could be read as scoping the walk to the three literal
construction forms only); or (b) give `bool_transition()` (and any sibling
wrapper) a required, no-default `evidence_provenance` parameter, so an
incomplete caller fails with a plain `TypeError`/lint error independent of
the AST gate at all, rather than being invisible to it. Either is acceptable
implementation-time judgment; leaving the gate scoped to the three literal
construction forms with no wrapper-call recognition and no required
parameter on the wrapper is not, since it reopens exactly the "second,
unwired producer path" failure mode this gate exists to close, just one
level of indirection removed from the `Change(...)`/`make_change(...)`
sites it does catch.

**A structural boundary the AST gate can never close, no matter how it is
generalized: a `Change` object supplied through the documented
`service.compare_snapshots(..., extra_changes=...)`/`checker.compare(...,
extra_changes=...)` API (Codex review, fresh evidence, confirmed by reading
both functions directly).** `service.py`'s `compare_snapshots` accepts a
keyword-only `extra_changes: list[Change] | None` and forwards it unchanged
to `checker.compare`, whose own body is exactly `if extra_changes:
changes.extend(extra_changes)` — the caller-supplied `Change` objects are
appended to the result's `changes` list verbatim, with no field validation
of any kind. This is a real, currently-documented escape hatch (see the
`extra_changes` discussion earlier in this document, in the SONAME-bump
finding's evidence-tiers analysis above) for L3–L5 build/source-derived
findings a caller assembles outside `compare()`'s own detector pipeline —
and nothing stops that same parameter from being used by **arbitrary
Python code outside this repository entirely**, since it is public typed
API, not an internal helper. No AST scan of this repo's own source — walking
`Change(...)`/`make_change(...)`/`BundleFinding(...)`/`bool_transition(...)`
call expressions, however completely generalized per the two options above —
can ever see, let alone validate, a `Change(...)` constructor invoked inside
a third-party script that imports `abicheck` and calls `compare_snapshots
(old, new, extra_changes=[Change(kind=..., symbol=..., description=...)])`.
The gate's completeness promise must therefore be stated precisely rather
than left to imply "every `Change` in `DiffResult.changes`, unconditionally":
**the AST gate guarantees completeness only for a `Change` constructed
within this repository's own detector/wrapper code — an externally supplied
`extra_changes` entry is outside its reach by construction, not by an
implementation gap the gate could close with more coverage.**

That distinction only matters if something else picks up the slack, so this
plan specifies what must happen at the one place such a `Change` actually
enters the pipeline: **`checker.compare`'s `extra_changes` append step
becomes the runtime boundary check the static AST gate cannot be.**
Immediately before `changes.extend(extra_changes)`, each appended `Change`
with `evidence_provenance is None` is given a reserved sentinel value,
`("external:caller_supplied",)`, rather than being extended into `changes`
unmodified; a `Change` that already carries a real, non-`None`
`evidence_provenance` (a well-behaved caller that already tags its own
findings) passes through untouched. `external:caller_supplied` is
deliberately **not** shaped like the `<side>:<tier>[:backend]` grammar
Phase 0/1 establish elsewhere in this vocabulary (`old:`/`new:`/`both:`/
`current:` each name a real in-repo evidence source this codebase can
verify) — it is a top-level, ungrouped tag meaning exactly "this finding
did not come from any detector this repository's provenance machinery can
speak to," the honest claim available at a boundary where the actual
evidence, if any, is unknowable from inside `compare()`. This closes the
same gap the enum-partition/construction-path pair closes for in-repo
producers, just at the one seam those two gates cannot reach: after this
change, `evidence_provenance` is `None` in `DiffResult.changes` only for a
`ChangeKind` still in `PROVENANCE_UNVERIFIED` (an explicit, tracked backlog
state), never silently for an externally-supplied finding. Phase 2's test
list gains one matching case: constructing a `Change` with
`evidence_provenance=None`, passing it through `compare_snapshots(...,
extra_changes=[...])`, and asserting the returned finding carries
`("external:caller_supplied",)` — plus a control asserting a caller-set,
non-`None` value survives unchanged. This is implementation work for
Phase 2 itself (the `checker.compare` edit and its test), not a follow-up
plan; it is recorded in this same phase because it is the other half of
the completeness gate's own guarantee, not a separate concern.

**A caller-supplied *non-`None`* `evidence_provenance` needs the same
runtime boundary check, not just the bare-`None` case above (Codex review,
fresh evidence, PR #866 round 36).** The rule as stated only distinguishes
"no value supplied" (gets the sentinel) from "some value supplied" (passes
through unmodified) — but "some value" can itself be malformed in exactly
the ways the normalization rule earlier in this document
(`tuple(sorted(set(entries)))`, drawn only from the registered vocabulary)
exists to prevent: an unregistered/made-up provider-id string no in-repo
producer would ever emit, a duplicated entry, or an unsorted tuple. The
repository-only construction gate (Phase 2, above) cannot inspect a
third-party caller's own `Change(...)` construction — the identical "the
AST gate can never close this" argument the paragraph above already makes
for the bare-`None` case applies equally to a malformed non-`None` one.
Left unhandled, a `DiffResult.changes` entry can carry a value the
completeness gate's own normal-form assertion ("every non-`None`
`evidence_provenance` it sees is already in this normal form") is defined
to require but has no way to enforce outside this repository's own call
sites — the malformed value would simply publish, contradicting Phase 0's
own normalization contract at the one boundary built specifically to
enforce contracts the static gate cannot reach.

Extending the same runtime boundary check, rather than adding a second one,
keeps this at the one seam that needs it: immediately before
`changes.extend(extra_changes)`, a supplied non-`None` tuple is first
normalized (`tuple(sorted(set(entries)))` — the identical rule every
in-repo constructor already follows, cheap and never wrong to apply
unconditionally) and then checked entry-by-entry against the registered
vocabulary (the single code-level registry Phase 0 above calls for). An
entry not in the registry is dropped from the emitted tuple rather than
trusted verbatim — the same "state the honest, weaker claim, never
fabricate" discipline this plan already applies throughout (see
`l2:legacy_unknown_backend`/`unknown:legacy_ambiguous_tier` above) — and
whenever any entry was dropped, or the tuple is empty after normalization,
`external:caller_supplied` is added *alongside* whatever validated entries
remain rather than substituted for them, so a downstream reader can still
distinguish "every claimed provider was independently verifiable" from
"part or all of this finding's provenance came from a caller this
repository could not fully verify." This is deliberately corrective, not a
hard rejection: raising out of `checker.compare` over one malformed tag in
one `extra_changes` entry would abort an entire comparison run for a
defect in a single finding — the same trade-off this plan's own `l1:`/`l2:`
gap notes above already reject in the opposite direction (fabricating a
claim rather than leaving a field honestly incomplete). Phase 2's test list
gains two further cases alongside the `None` one above: a supplied tuple
with a duplicate/unsorted-but-otherwise-registered entry set, asserting it
round-trips normalized with no entries lost; and a supplied tuple
containing an unregistered string, asserting that entry is dropped and
`external:caller_supplied` is present in the result alongside any
surviving registered entries.

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

**A fourth family of projections sits entirely outside `reporter.py` and
must be inventoried separately: the release/artifact-set JSON summaries
each build their own independent `Change`/`BundleFinding` dict, never
routing through `to_change()` or any of the seven builders above (Codex
review, fresh evidence, confirmed by reading all four directly).**
`cli_compare_release_helpers.py`'s `_format_release_json` builds
`summary["bundle_findings"]` as a hand-written dict straight off each
`BundleFinding`'s own fields (`kind`/`symbol`/`consumer_library`/
`provider_library`/`description`/`old_value`/`new_value`/
`affected_libraries`) and `summary["matrix_findings"]` as a hand-written
dict straight off each matrix-comparison `Change` (`kind`/`symbol`/
`description`/`old_value`/`new_value`) — neither calls `BundleFinding.
to_change()` or `_change_to_dict`. `cli_compare_release.py`'s
`_release_finding_dicts` is a third, deliberately separate "small, capped
dict" projection (its own docstring: "Same shape as `cli_scan_baseline.
_baseline_finding_dicts`/`stack_report._stack_finding_dicts`") feeding the
per-library `findings` array in the release summary — capped and shaped
differently from `_change_to_dict`, so it needs the field added to its own
dict literal, not inherited. And `service_scan.py`'s `ScanSetResult.
to_dict()` — the `--artifact-set` sibling of the single-binary `scan`
result the existing `_baseline_finding_dicts` entry above already covers —
builds its own `bundle_findings` list with a comment stating it
deliberately mirrors `cli_compare_release_helpers.py`'s dict shape
field-for-field, which is exactly why it shares that shape's gap: an
implementation covering only the `reporter.py`-owned builders (and
`_baseline_finding_dicts` for scan's single-binary path) would leave
`compare --release`'s JSON summary and `scan --artifact-set`'s JSON output
both silently missing `evidence_provenance` on every bundle/matrix finding
and every per-library capped finding, even once every other JSON surface
carries it. Add the field to all four dict literals (`_format_release_json`'s two,
`_release_finding_dicts`'s one, `ScanSetResult.to_dict`'s one) explicitly,
and extend the same schema-version-bump discipline
established above to whichever schema each summary format is gated by —
`compare --release`'s summary JSON has no dedicated `.schema.json` today
(confirmed: none exists under `abicheck/schemas/` or `docs/reference/
schemas/v1/` for it, the same as the scan-report case already noted below),
so for that one only the "don't add a field silently to an unversioned
format" caution applies, not a schema file to update.

**A fifth projection family, `stack_report.py`'s own `Change`-to-dict
builders, is missing from this inventory even though this same section
already names one of its two functions in a quoted docstring — it was
never actually added to the required list (Codex review, fresh evidence,
confirmed by reading `stack_report.py` directly, PR #866 round 27).**
`_stack_finding_dicts()` independently serializes each per-library
`Change` from `diff.breaking`/`diff.source_breaks`/`diff.risk` into a
capped dict (`bucket`/`kind`/`symbol`/`description`/`source_location`) —
the same "small, capped dict" shape `_release_finding_dicts`'s own
docstring cites it as a sibling of two paragraphs above — and
`stack_to_json()` separately builds `d["binding_changes"]` straight off
`StackCheckResult.binding_changes` (`list[Change]`, populated by
`diff_runtime_bindings()` — the cross-environment rebound-provider
findings `abicheck stack`'s two-environment mode reports), rendering each
one's `kind`/`symbol`/`description`/`old_value`/`new_value` by hand.
Neither routes through `to_change()`, `_change_to_dict`, `_leaf_entry`, or
any of the four builders in this family — `_stack_finding_dicts` is
`stack_to_json`'s own helper, called once per library while building
`d["stack_changes"]`, and `binding_changes` is a structurally different
list (cross-environment provider rebinding, not a single-comparison
diff) with no per-library `diff` object to draw a bucket from at all.
Implementing this phase against only the first four families would leave
`abicheck stack --format json` — the one command whose JSON output this
inventory has not yet named — silently missing `evidence_provenance` on
every per-library stack finding and every runtime-binding-provider
finding, even once every other JSON surface (including the sibling
`_baseline_finding_dicts`/`_release_finding_dicts` this same function's
docstring already lists it alongside) carries it. Add the field to both
dict literals (`_stack_finding_dicts`'s per-finding dict, `stack_to_json`'s
`binding_changes` dict comprehension) explicitly, and give both their own
test coverage the way this phase requires of every other builder — a
`stack --format json` fixture asserting `evidence_provenance` reaches both
`stack_changes[].findings[]` and `binding_changes[]`, mirroring the
existing per-builder pattern (`test_reporter_evidence_provenance.py`-
shaped) rather than assumed to follow from the `reporter.py`/release-JSON
coverage above. `stack`'s own JSON output has no dedicated
`.schema.json` today either (confirmed: no `stack_report.schema.json`
exists under `abicheck/schemas/` or `docs/reference/schemas/v1/`), so —
like `_baseline_finding_dicts`/the release-JSON family above — only the
"don't add a field silently to an unversioned format" caution applies to
these two, not a schema file to update.

**A sixth projection family, `cli_compare_fold.py`'s `--audit-suppressions`
ledger, is missing from this inventory entirely — it names no `Change`→dict
builder at all today, and it is a distinct ledger from
`_suppressed_change_entry` above, not a duplicate of it (Codex review,
fresh evidence, confirmed by reading `cli_compare_fold.py` directly,
PR #866 round 30).** `_fold_suppression_audit_into_text()`'s JSON branch
independently serializes each `(rule, change)` pair in
`audit.high_risk_matches` — the `SuppressionAudit` findings where a
suppression rule matched a `BREAKING` change — into
`suppression_audit.high_risk_matches`, a hand-built dict of `rule`/
`kind.value`/`symbol` straight off the `Change` object, with no call to
`to_change()`, `_change_to_dict()`, `_leaf_entry()`, or any of the other
five families above. This is a genuinely separate surface from
`_suppressed_change_entry` (the ordinary suppressed-change ledger already
named in the "Files & surfaces" list): that builder records every
*ordinarily* suppressed finding, while `high_risk_matches` is
`--audit-suppressions`'s own report — specifically the subset of
suppressions the audit flags as risky because they matched a BREAKING
change — and it is reachable only when `--audit-suppressions` is passed,
independent of whether ordinary suppression bookkeeping ran at all.
Implementing this phase against only the first five families would leave
`compare --format json --audit-suppressions`'s `high_risk_matches` entries
silently missing `evidence_provenance` even once the ordinary suppressed-
change ledger carries it — exactly the auditability gap the fourth
family's own paragraph above warns about, just on the audit surface that
exists specifically to justify *why* a suppression was allowed to hide a
BREAKING change. Add `evidence_provenance` to `high_risk_matches`'s dict
literal explicitly, and give it its own test coverage — a `compare
--format json --audit-suppressions` fixture asserting
`suppression_audit.high_risk_matches[].evidence_provenance` is present when
a rule matches a BREAKING change, mirroring the existing per-builder
pattern rather than assumed to follow from the ordinary suppressed-change
ledger's own coverage. `compare`'s JSON output already has a dedicated
schema (`REPORT_SCHEMA_VERSION`/`compare_report.schema.json`, the same one
`_change_to_dict`/`_leaf_entry` feed), so this field addition is not
exempt the way the unversioned scan/release/stack surfaces above are —
it needs the identical schema-version bump and `compare_report.schema.json`
update the next paragraph already requires for `_change_to_dict`/
`_leaf_entry`, applied to `suppression_audit`'s own definition too, in the
same PR.

**A seventh projection family, `abicheck/impact/correlation.py`'s
`RootCauseGroup.to_dict()`, is missing from this inventory entirely — a
public dataclass method with its own independent `Change`→dict projection,
reachable by any caller of the typed Python API even though no in-repo CLI
surface currently calls it (Codex review, fresh evidence, confirmed by
reading `correlation.py` directly and grepping every call site of
`RootCauseGroup`/`.to_dict()` in the repository — no production code path
calls it today; the only in-repo consumer of `correlate_root_causes()`,
`reporter_markdown.root_cause_evidence_lookup_for_changes`, iterates
`group.members` directly and never calls `to_dict()`).** `RootCauseGroup`
is `correlate_root_causes(changes) -> list[RootCauseGroup]`'s own return
type, and both the function and the class are public, importable API
(`abicheck.impact.correlation`) — nothing marks `to_dict()` private or
internal-only, and its presence on a public dataclass is itself an
invitation for exactly the caller this finding describes: a script that
imports `correlate_root_causes` directly and serializes the result via
`[g.to_dict() for g in groups]` rather than going through any of the six
`reporter.py`-adjacent families above. Its `members` projection builds
`{"finding_symbol": c.symbol, "kind": c.kind.value, "evidence_level":
level}` per member — the same three-field shape `_stack_finding_dicts`'s
sibling builders use, but independently written, with no call to
`to_change()`, `_change_to_dict()`, `_leaf_entry()`, or any of the other
six families. A caller relying on this method to serialize correlated
findings therefore loses `evidence_provenance` even after every JSON
surface this phase otherwise covers carries it — the same
auditability gap the fourth family's paragraph above names, reached this
time through direct API use rather than a CLI flag. Add
`evidence_provenance` to the per-member dict literal explicitly (a `None`
value serializes as JSON `null`, matching this field's nullable contract
elsewhere), and give it its own test coverage — a direct unit test
constructing a `RootCauseGroup` from `Change` objects carrying
`evidence_provenance` and asserting `to_dict()["members"][i]
["evidence_provenance"]` round-trips, mirroring the existing per-builder
pattern rather than assumed to follow from `reporter.py`'s own coverage.
`RootCauseGroup.to_dict()` feeds no dedicated `.schema.json` (it is not a
report/SARIF/JUnit surface `reporter.py` calls), so — like the
`_baseline_finding_dicts`/release-JSON/stack-JSON families above — only the
"don't add a field silently to an unversioned format" caution applies, not
a schema file to update.

**An eighth projection family, `abicheck/impact/use_case_impact.py`'s
`UseCaseImpact.to_dict()`, is missing from this inventory entirely — Phase
1's own inventory paragraph mis-cited this module's `UseCaseChange(...)`
call as a construction site, which it is not (Codex review, fresh
evidence, PR #866 round 37, confirmed by reading the module directly; see
Phase 1's own correction above for the full account of the mis-citation).**
`build_use_case_impact()` builds one `UseCaseChange` per (already-
constructed) `Change`/use-case pair it attributes, copying `symbol`,
`kind.value`, and `report_finding_id(change)` off the participating
`Change` — never a fresh evidence decision — into `UseCaseChange`, a
carrier dataclass with no `evidence_provenance` field at all.
`UseCaseImpact.to_dict()` then serializes each use case's tuple of
`UseCaseChange` objects under `use_case_impact.by_use_case[<use
case>][i]`, straight off `UseCaseChange.to_dict()`'s own three-key dict
(`symbol`/`kind`/`finding_id`) — the identical "small, independently
projected carrier" shape the fifth family's `_stack_finding_dicts` and the
seventh family's `RootCauseGroup.to_dict()` already establish, not reached
by `to_change()`, `_change_to_dict()`, `_leaf_entry()`, or any of the
other seven families. Implementing this phase against only the first
seven families would leave `compare --use-cases MANIFEST --format json`
silently missing `evidence_provenance` on every attributed finding in
`use_case_impact.by_use_case`, even once every other JSON surface this
phase covers carries it — the reader loses exactly the information needed
to judge how solid the *use-case attribution itself* is for a given
finding, on the one report block whose entire purpose is telling a reader
which of their own use cases a change actually touches. Fix by widening
`UseCaseChange` with an `evidence_provenance` field carrying the
participating `Change.evidence_provenance` forward unchanged (populated at
the one construction call site inside `build_use_case_impact()`'s loop,
mirroring how `finding_id` is already carried the same way), and adding it
to `UseCaseChange.to_dict()`'s dict literal; give it its own test
coverage — a `compare --use-cases MANIFEST --format json` fixture
asserting `use_case_impact.by_use_case[<use case>][i].evidence_provenance`
matches the corresponding finding's own `evidence_provenance` in the
report's main `changes`/`findings` array, mirroring the existing
per-builder pattern rather than assumed to follow from `reporter.py`'s own
coverage. `UseCaseImpact.to_dict()` feeds no dedicated `.schema.json`
(confirmed: no `use_case_impact.schema.json` exists under
`abicheck/schemas/` or `docs/reference/schemas/v1/`, and it is not part of
`compare_report.schema.json`'s own `Change`-object definition either,
since `by_use_case` entries are `UseCaseChange`, a distinct, smaller
shape) — so, like the `_baseline_finding_dicts`/release-JSON/stack-JSON/
`RootCauseGroup` families above, only the "don't add a field silently to
an unversioned format" caution applies, not a schema file to update.

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
render_sarif.py` if that migration has landed by then.

**That one entry covers only the ordinary per-result `properties` bag —
`sarif.py` also builds two *audit-ledger* `Change`→dict projections
independently, and both are outside this coverage as stated (Codex review,
verified against the real code, PR #866 round 28).** `sarif.py`'s
`runs[].properties.surfaceScope.outOfSurfaceChanges` (the ADR-024 §D4/D5
header-scope ledger, ~line 1139) and `runs[].properties.
buildContextReconciled.changes` (the ADR-039 reconciliation ledger, ~line
1167) each build a compact, hand-written dict — `kind`/`symbol`/
`description`/`sourceLocation`/`reason` — straight off `result.
out_of_surface_changes`/`result.reconciled_changes`, at run-level scope
rather than per-result scope, and neither routes through the per-result
`properties` construction this paragraph's "one entry" describes. This is
the identical gap Phase 3 already found and closed on the JSON side for
exactly these same two excluded-finding categories: `reporter.py`'s
`_out_of_surface_entry`/`_add_reconciled` (see the "four more `Change`→dict
projections" discussion above) are the audit-ledger siblings of these two
SARIF projections, and Phase 3 explicitly requires `evidence_provenance` on
both so an audit trail doesn't go silent on exactly the findings a reader
most needs the evidence for. Add `evidence_provenance` to both SARIF dict
literals the same way (one field per ledger entry, mirroring the JSON
ledgers' own shape rather than inventing a new spelling), and give both
their own test coverage — a SARIF fixture asserting
`runs[].properties.surfaceScope.outOfSurfaceChanges[].evidenceProvenance`
and `runs[].properties.buildContextReconciled.changes[].evidenceProvenance`
are populated, mirroring the JSON-side `_out_of_surface_entry`/
`_add_reconciled` test coverage rather than assumed to follow from the
ordinary per-result SARIF coverage below.

**JUnit is not out
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
*finding* regardless of `--contract` (see Phase 1's own vocabulary section
above and Phase 3's four-projection coverage below), so this addition
changes JUnit output for the ordinary, contract-free case too, not only the
`--contract` one. That is an intentional, in-scope consequence of adding a
new always-computed field to a public report format, not a defect — but it
means this phase's JUnit change is a real, unconditional schema-shape change
to every emitted `<testcase>`, and must be called out as such (the same
`REPORT_SCHEMA_VERSION`/topic-registration discipline the JSON side already
requires below applies here too), not described as a change confined to
`--contract` runs.

**"Every emitted `<testcase>`" overstates the reach, though, and needs
narrowing to "every finding-backed `<testcase>`" (CodeRabbit review,
verified against `junit_report.py`'s own real code).** JUnit's own
per-symbol schema (this module's docstring: "Each exported symbol/type
that was checked is a `<testcase>`") means not every `<testcase>` has a
backing `Change` at all — `_emit_testcases()` emits one testcase per entry
in `all_symbols` (every checked symbol, changed or not) and only calls
`_add_contract_properties`/`_maybe_add_failure` (the function this
addition hooks into) `if sym in change_by_symbol`; an *unchanged* symbol's
testcase is emitted with no `<properties>` call at all, since there is no
`Change` for it to read `evidence_provenance` off of.
`_emit_missing_contract_testcases()` is a second, structurally distinct
case: a required-symbol/version/entrypoint gap has "no backing diff
`Change`" (that function's own docstring) and is rendered through its own
dedicated path (`sarif._missing_contract_result`'s JUnit counterpart), not
`_add_contract_properties`, for the identical reason `contract_evidence_refs`
already has nothing to attach there today. Both categories are therefore
out of reach for this addition by construction, not by an oversight this
phase needs to close: an unchanged-symbol testcase and a missing-contract-
label testcase omit the `abicheck.evidence_provenance` property entirely,
the same way they already omit `abicheck.contract_evidence_refs` and every
other per-finding property this module emits — there is no sentinel value
to substitute, since "no property" already is this module's own convention
for "no `Change` to read from." The scope of this phase's JUnit change is
therefore: every `<testcase>` for a symbol with a backing `Change`
(`sym in change_by_symbol`) gains the property; a checked-but-unchanged
symbol's testcase and a missing-contract-label testcase are unaffected.
generated docs (`scripts/gen_detector_spec.py`'s matrix gains a column once
every kind has a real, non-`UNVERIFIED` classification — gated on Phase 2's
completeness test, so the docs generator cannot claim more coverage than
actually exists).

**Two more human-facing per-finding renderers already carry the identical
`contract_evidence_refs` precedent this phase extends for JSON/SARIF/JUnit,
and this section's own inventory must cover them too, not stop at the
machine-readable three (Codex review, verified against the real code, PR
#866 round 38).** `html_report.py`'s `_changes_table` (the function every
`compare --format html` finding row renders through) already reads
`getattr(ch, "contract_evidence_refs", None)` and, when set, renders an
`"evidence: ..."` line inside each finding's own description cell — real,
existing per-finding evidence-string rendering, gated on `contract_relevance
is not None` the same way the JSON/SARIF/JUnit additions above are gated on
`--contract`. `reporter_markdown.py` carries the same pattern independently
(`_format_leaf_type_change`, `_build_not_evaluated_section`,
`_append_suppression_note` each read `c.contract_relevance`/render a
per-finding contract-decision line via `_contract_decision_text`). Neither
renderer is a hypothetical future surface: both are live, shipped output
formats a user selects with `--format html`/`--format markdown` today, and
a user on either format has no way to see `evidence_provenance` at all once
Phase 1 populates it, unless these two renderers are wired the same
additive way `_change_to_dict`/SARIF's `properties`/JUnit's `<properties>`
already are. Add an `"evidence_provenance: ..."` line to `_changes_table`
immediately alongside its existing `contract_evidence_refs` rendering (not
gated on `contract_relevance`, since `evidence_provenance` is populated
independently of `--contract`), and an equivalent rendering to
`reporter_markdown.py`'s per-finding text (a natural home is
`_contract_decision_text`'s sibling text-building path, or a new small
helper called from the same finding-rendering call sites
`contract_relevance` already reaches, for the ordinary contract-free case
too). Each gets its own format-specific test — an HTML fixture asserting
the rendered `<table>` cell contains the expected evidence tags for a
finding with `evidence_provenance` set, and a Markdown fixture asserting
the corresponding text line appears in `to_review_digest`'s output —
mirroring the SARIF/JUnit fixture tests Phase 3 already requires above,
rather than folding this into the JSON-only regression coverage. This is
additive rendering of an existing, already-populated field through an
established per-finding-metadata pattern each renderer already has, the
same category as the JSON/SARIF/JUnit work above — not the UI/report-
rendering *redesign* the "Out of scope" section below disclaims, and that
section is corrected accordingly.

**`_changes_table` does not render every HTML finding row, and
`stack_to_markdown()` is a third, wholly independent human-facing
renderer this section's inventory missed entirely — both confirmed
against the real code, not the round-38 correction's own claims (Codex
review, PR #866 round 39).** `html_report._build_sections_html()`
renders its `not_evaluated` section (ADR-049 D1 — findings a selected
`--contract` domain excluded from every verdict bucket) via its own
hand-built row loop (`html_report.py`, the `if not_evaluated:` block),
not through `_changes_table` at all — it builds `<tr>` markup directly
from `ch.symbol`/`ch.kind`/`ch.contract_reason_code`/
`ch.correlated_change_kind`, with no call to `_changes_table` or any
shared per-finding renderer anywhere in that block. So the fix prescribed
above for `_changes_table` — adding an `"evidence_provenance: ..."` line
"immediately alongside its existing `contract_evidence_refs`
rendering" — provably cannot reach a `not_evaluated` row: that row is
never built by `_changes_table` in the first place. A finding excluded
by `--contract` is exactly the shape most likely to need its evidence
provenance explained (a reader asking "why wasn't this scored" is the
same reader who'd want to know *what evidence was searched*), making this
the higher-value gap of the two HTML paths, not a minor omission.
Closing it needs the identical rendering added a second time, directly
in `_build_sections_html`'s own row-building loop (mirroring how
`correlated_change_kind`'s "See also" line is already added there
independently of `_changes_table`), with its own HTML fixture test
asserting the `not_evaluated` table's own `<tr>` markup carries the
evidence-provenance text — a `--contract` run's fixture, not the
plain-comparison one the `_changes_table` fixture above already covers.

Separately, `stack_report.stack_to_markdown()` (the Markdown renderer for
`abicheck compat-stack`/`abicheck deps --check` results, not covered by
either `_changes_table` or `reporter_markdown.py`) has two of its own
independent per-`Change` rendering loops: `_render_stack_changes_section`
renders each library's own gating findings directly off
`sc.abi_diff.breaking` (`f"  - `{c.kind.value}`: {c.description}"`, no
other field read), and `_render_binding_changes_section` renders runtime
binding-provider findings the same way
(`f"- `{bc.kind.value}`: {bc.description}"`). Neither reads
`evidence_provenance`, `contract_evidence_refs`, or any other per-finding
metadata field — both are pure `kind`/`description` bullet lists. This is
not the same gap `stack_report.py`'s existing JSON coverage already
closes: `_stack_finding_dicts` (used only by `stack_to_json`, this
section's already-covered "fifth projection family") and
`stack_to_markdown` are two entirely separate code paths over the same
underlying `Change` objects — fixing the dict projection changes nothing
about what the Markdown bullets render, and vice versa. Closing this
needs its own additive line in each of the two render loops (matching the
`- `{c.kind.value}`: {c.description}` bullet's own established style,
e.g. an indented `- Evidence: ...` line rendered only when
`evidence_provenance` is set, so a plain `stack_to_markdown()` run with no
evidence data produces byte-identical output to today), plus its own
Markdown fixture test — a `stack_to_markdown()` snapshot over a
`StackCheckResult` carrying a `Change` with `evidence_provenance` set,
distinct from the HTML/plain-`reporter_markdown.py` fixtures already
required above, since neither of those exercises this module at all.

**Three more human-facing renderers sit outside `stack_to_markdown()`'s own
module and outside `_changes_table`/`reporter_markdown.py`, and none is
reached by anything named above (Codex review, verified against the real
code, PR #866 round 40).** `stack_html.stack_to_html()` — the HTML renderer
both `deps tree --format html` and `deps compare --format html`
(`cli_stack.py`) route through — has its own, third independent per-
`Change` rendering loop for `result.binding_changes` (the same
`list[Change]` `stack_to_json`'s `d["binding_changes"]`/
`stack_to_markdown()`'s `_render_binding_changes_section` already render
elsewhere): `f"<tr><td><code>{h(bc.kind.value)}</code></td>
<td>{h(bc.description)}</td></tr>"`, kind and description only, no other
field read. This is a fourth, wholly separate code path over the same
`BindingChange`-carried `Change` objects — fixing `stack_to_json`'s dict
projection or `stack_to_markdown()`'s bullet list changes nothing about
what this HTML `<tr>` renders. Closing it needs its own additive
`<td>`/line in this one loop (rendered only when `evidence_provenance` is
set, so a run with no evidence data produces byte-identical HTML to
today), plus its own HTML fixture test distinct from the `_changes_table`
HTML fixture required above, since that fixture never exercises
`stack_html.py`.

Separately, `bundle.render_bundle_findings_markdown()` — shared, per its
own docstring, by `cli_compare_release_helpers._release_md_bundle_findings`
(the `compare --release` Markdown summary's bundle-findings section) and
`cli_scan._render_artifact_set_text` (`scan --artifact-set`'s text output)
— renders each `BundleFinding` directly (`kind`/`symbol`/
`consumer_library`/`provider_library`/`description`), never through
`BundleFinding.to_change()`. This is the identical "carrier bypasses
`to_change()`" gap the JSON-projection inventory above already establishes
for `_format_release_json`'s `summary["bundle_findings"]` dict and
`ScanSetResult.to_dict()`'s `bundle_findings` list — except here the two
*Markdown* call sites of the same underlying function are missing
entirely, not merely under-covered. Since this plan already requires
`evidence_provenance` on `BundleFinding` itself (not only on its
`to_change()` lowering — see the `BundleFinding` construction-sites
discussion above), this one function needs an additive
`- Evidence: ...` line (rendered only when set) to cover both call sites
at once, plus its own Markdown fixture — a `render_bundle_findings_markdown()`
snapshot over a `BundleFinding` with `evidence_provenance` set, distinct
from `_release_finding_dicts`'/`ScanSetResult.to_dict()`'s JSON fixtures,
since neither exercises this Markdown function.

`cli_compare_release_helpers._release_md_matrix_findings()` is a third,
independent Markdown path in the same module as the `BundleFinding`
renderer above, but over ordinary matrix-comparison `Change` objects, not
`BundleFinding`s: `f"- **{c.kind.value}**" + (...symbol...)`, then
`f"  - {c.description}"` — kind, symbol, and description only, mirroring
the exact gap the JSON-side sibling `_format_release_json`'s
`summary["matrix_findings"]` dict already has and that the inventory above
already requires closing, except this is the *Markdown* rendering of the
identical `matrix_result.changes`, reached by neither `_changes_table` nor
`reporter_markdown.py` (both of which render the primary comparison's
findings, not the release fan-out's per-configuration matrix findings).
Needs the same additive `- Evidence: ...` line, gated on
`evidence_provenance` being set, plus its own Markdown fixture over a
matrix `Change` carrying `evidence_provenance`.

Explicitly **not required** for this plan's acceptance criteria, named here
so a future PR doesn't have to re-derive the target: once real, per-finding
values exist, `evidence_status_for_result`'s report-level `ARTIFACT_PROVEN`
→ `UNATTRIBUTED` downgrade (the mechanism `AGENTS.md`'s entry investigated
and found too coarse) can be re-scoped from "was the *whole comparison*
header-only" to "was *this finding* header-only, uncorroborated" — a
strictly more precise version of the same signal, using data this plan
produces rather than requiring new extraction.

### Phase 5 — Pack-level producer receipt (prerequisite for declarative-project consumers)

**Origin:** external upstream-only review (base commit `327df7b5616bcf
aea8c330aad418b796c17f3970`, PRs #860/#883 merged), item 10. Distinct from
Phases 0-3 above, which answer *per-finding* provenance ("which extractor
and evidence tier produced or corroborated this one `Change`"). This phase
answers a coarser, prerequisite question at the *evidence pack* level: does
the pack a `check-project.yml` target consumes carry enough of a receipt to
be validated and normalized before its facts ever reach a finding at all?

`build-output.json` today carries a coarse top-level `evidence_producer`
(`abicheck/buildsource/build_output.py`) — enough to answer "what kind of
tool produced this," not enough to reject a pack that is subtly
incompatible with the context consuming it. Extend the pack-level receipt
to carry:

```
producer kind
abicheck version
facts schema version
Clang/plugin major
compiler path and digest
source-tree digest
per-projection: { identity, compile-context fingerprint, public-header-root digest, translation-unit inventory }
```

(`compile-context fingerprint`/`public-header-root digest`/`translation-unit
inventory` are keyed per canonical target projection, not singular pack-wide
scalars — see the correction below for why a shared, multi-target pack
cannot honestly carry one value for any of these three.)

This receipt must be **validated and normalized, not merely informational**
— a consumer (`check-project.yml`'s evidence-routing step, or a direct
Python API caller) rejects a pack whose receipt disagrees with the
resolved consumption context, with a typed reason, rather than accepting it
and letting a downstream mismatch surface as a confusing comparability
error several steps later. This is the same "fail closed with a named
reason" discipline `comparability.py`'s existing `ScopeMismatchError`
already establishes for the scope-fingerprint axis — extend that pattern to
the producer-receipt axis rather than inventing a second one.

**A bare singular "target id" field is wrong here, confirmed by a fresh
review round cross-checking this phase against G43's own scenario, not
assumed.** G43's inferred-projection case is exactly one build-wide
`abicheck_inputs/` pack intentionally consumed by *several* targets, with
`attribute_sources_to_targets()`/`_filter_tus_by_attribution()` selecting
each target's own TUs out of that one shared pack — the pack itself has no
honest single target it "belongs to." A singular `target id` field
combined with fail-closed equality matching would force one of two wrong
outcomes: either the pack is stamped with one target's id and every other
target's legitimate consumption of the identical pack is rejected as a
receipt mismatch, or the field is left blank/absent for a shared pack and
loses fail-closed validation for this class of pack entirely — both defeat
the purpose of this phase for precisely the scenario G43 exists to wire
up. The receipt's identity field must therefore be **projection-aware**:
either a single target id (the ordinary, non-shared case — validated by
equality exactly as before) *or* a build-wide/shared-scope marker paired
with an **attribution digest — which must be newly defined by this phase,
not treated as something G43 or existing code already computes.** A fresh
review round found this claim false: neither `link_attribution.py` nor
`inputs_pack.py`/`build_output.py` computes or persists any such digest
today — `attribute_sources_to_targets()` returns a plain
`{normalized_source_path: frozenset[target_identity]}` mapping, and
`_inferred_evidence_projection_issues()`/`_filter_tus_by_attribution()`
only ever re-derive that mapping and test set intersection/membership
against it; nothing hashes it. Without a real digest to validate against,
this phase's own shared-pack receipt path has no value a producer can
emit or a consumer can verify, and stays unimplementable for exactly the
G43 scenario it was written to cover.

This phase must therefore define, not merely reference, the attribution
digest:

- **Canonical normalization**: the digest is computed over
  `attribute_sources_to_targets()`'s own return shape — sort the mapping's
  keys (already-normalized source paths), and for each key sort its
  `frozenset[target_identity]` members — so two structurally identical
  mappings always serialize to the same canonical byte sequence regardless
  of iteration/insertion order (the same "sort before hash" discipline
  this codebase already applies to other content-addressed digests, e.g.
  `BuildSourcePack.content_hash()`).
- **Hashing algorithm**: reuse whatever primitive this codebase's other
  content digests already use (confirm and reuse — don't introduce a
  second hashing convention for one new field), applied to the canonical
  serialization above.
- **Persisted field**: the digest is computed once, at the point the
  attribution mapping is produced/validated (natural point: alongside
  `_inferred_evidence_projection_issues()`'s own re-derivation, or a new
  sibling function next to it), and persisted as part of this phase's
  receipt (artifact 2, `InputsManifest` — see "Relationship to other
  plans" below) — not recomputed ad hoc by each consumer, which would
  reintroduce the same "two independent statements of one fact can
  disagree" risk this plan's own `comparability.py` precedent exists to
  avoid.
- **Producer/consumer wiring**: the producer (whatever emits the
  `abicheck_inputs/` pack and its `attribution_path` file) computes and
  stores the digest; the consumer (this phase's own fail-closed receipt
  validator) recomputes it from the attribution mapping it independently
  loaded and compares — a real equality check, not a reference to a value
  that was merely asserted.

Validated by checking that the *consumer's resolved projection* — this
target, selected via attribution — is one the pack's own attribution
digest actually covers (i.e. the target's canonical identity, from G43's
own corrected identity-set resolution, appears among the values the
digested mapping ties to at least one TU), not by requiring the whole
pack to name one target. A consumer validates whichever shape the receipt
declares; a shared pack is never forced through the single-target equality
check that only applies to the non-shared case.

**Two more receipt fields have the identical singular-value problem the
identity field above was already fixed for, and a fresh review round found
they were never fixed alongside it: `compile-context fingerprint` and
`public-header-root digest`.** A build-wide shared pack is, by this
phase's own design, consumed by multiple targets — and there is no reason
those targets share one compiler context or one set of public-header
roots; the whole point of a per-target attribution split is that
different targets can be genuinely different components of one build.
A singular compile-context fingerprint or public-header-root digest on
the receipt therefore repeats exactly the mistake the identity field
already had to be corrected for: fail-closed comparison against each
target's own resolved context would either reject every projection except
the one the singular value happens to describe, or — worse, silently —
validate one target's context against a *different* target's actually-
resolved facts, defeating the "fail closed with a named reason"
discipline this whole receipt exists to provide. These two fields must
therefore be defined **per canonical target projection**, not once for
the whole pack: either (a) a mapping from each accepted projection
identity (the same `target://<id>`/`output://<basename>`/shared-scope
identity this phase's own identity field already resolves) to that
projection's own compile-context fingerprint and public-header-root
digest, each computed by restricting to the TUs G43's attribution
mapping ties to that projection before fingerprinting; or (b) a single,
projection-keyed subreceipt object bundling identity + fingerprint +
header-root digest together per projection, rather than three
independently-keyed parallel structures that could drift out of sync
with each other. Either shape is acceptable; three separate singular
scalars, as an earlier draft of this phase's field list had them, is not
— it silently assumes every consumer of a shared pack shares one compile
context, which is precisely the assumption G43's own attribution
mechanism exists to *not* require.

**Relationship to Phases 0-3 above:** the per-finding provenance tags this
plan's earlier phases add (`l0:elf_symtab`, `l2:castxml`, `l4:source_
replay`, ...) name *which evidence tier* produced or corroborated a
finding; this phase's pack-level receipt is what lets a consumer trust that
tier's claim in the first place — a `l4:source_replay` tag is only as
trustworthy as the receipt proving the L4 replay ran against the compiler
version, source tree, and target it claims to. Sequence this phase before
relying on per-finding tags in a fail-closed consumer (a declarative
project's evidence-routing gate); the report-surface work in Phase 3 above
does not depend on it and can ship independently.

**Relationship to other plans — corrected: these are not two sections of
one manifest, they are two genuinely separate storage envelopes, confirmed
by reading `build_output.py`/`inputs_pack.py` directly.** An earlier draft
of this phase claimed G43's attribution data and this phase's producer
receipt "live in the same pack manifest... two receipt sections" — false.
Three distinct artifacts are in play, not one:

1. **`build-output.json` itself** — carries its own top-level
   `evidence_producer` (`BuildOutputEvidenceProducer`), which this phase
   extends. This part of the design is unchanged.
2. **The `abicheck_inputs/` pack's own `manifest.json`**
   (`InputsManifest`, `inputs_pack.py`) — the actual Flow-2 source-facts
   pack a per-target `evidence.path` in `build-output.json` points at.
   This is the pack whose *own* schema is the natural home for a
   producer receipt describing the facts it carries (Clang/plugin
   version, compiler identity, source-tree digest, TU inventory) — not
   `build-output.json`, which only points at this pack, doesn't embed it.
3. **G43's `attribution_path`-referenced file** — a *third*, separate
   artifact: `BuildOutputEvidence.attribution_path` is a per-target field
   on `build-output.json` naming yet another file, one holding a raw
   serialized `BuildEvidence` (parsed via `BuildEvidence.from_dict()`)
   used only to re-derive TU→target attribution for validation — it is
   neither part of `build-output.json` nor of the `abicheck_inputs/`
   pack's own manifest.

This phase's receipt therefore belongs in **artifact 2** (the
`abicheck_inputs/manifest.json` schema, `InputsManifest`) for the facts it
actually describes, plus the already-correct top-level `evidence_producer`
extension in artifact 1 for coarse producer identity — not merged with
G43's artifact 3. The three stay separate, cross-referenced by path
(`evidence.path`/`evidence.attribution_path` in artifact 1), not folded
into one schema. G34's `consumer_compile`/toolchain-binding work is still
the source of the "compile-context fingerprint"/"compiler path and digest"
fields this phase reuses rather than re-deriving.

**Acceptance test:** a Clang-18 plugin pack consumed by an incompatible
producer context is rejected with a typed reason. A stale source-tree
digest is rejected. A clean-job reuse of a valid pack (identical receipt,
re-run in a fresh CI job) reproduces the same normalized L4 findings. Every
reported finding can identify its producing and corroborating evidence
without reconstructing that answer from the whole snapshot (this last
clause is Phases 0-3's own acceptance bar, restated here to make explicit
that this phase and Phases 0-3 together are what the review's item 10
acceptance test actually requires).

**Files & surfaces — routed through ADR-061's canonical owners, matching
the same correction already applied to G41/G45's manifest-schema work,
not `abicheck/buildsource/build_output.py` directly:**

- **`abicheck/model/`** — the receipt's own field shapes (producer kind,
  version identifiers, digests, TU inventory) as a shared value type, per
  ADR-061's "add an ABI entity/value shared across stages" routing.
- **`abicheck/storage/`** — the receipt's schema/serialization and version
  bump, per ADR-061's "own their schemas/migrations" routing — the same
  home G41 Phase 1 routes the baseline-manifest schema to. This covers
  both artifact 1 (`build-output.json`'s `evidence_producer` extension)
  and, as a separate schema, artifact 2 (`InputsManifest`'s new receipt
  fields) — see the corrected "Relationship to other plans" note above for
  why these must stay two schemas, not one.
- `abicheck/buildsource/inputs_pack.py` — the `InputsManifest` reader/
  writer this phase's artifact-2 receipt fields actually extend (currently
  missing from this file list; the receipt has nowhere to live without it).
- **`abicheck/workflows/`** — the new validation entry point
  `check-project.yml`'s evidence-routing step consults, coordinating the
  fail-closed rejection.
- `abicheck/buildsource/build_output.py` — orchestration only (calling the
  `storage/` reader/writer), not schema logic grown here directly.
- `abicheck/comparability.py`'s existing fail-closed rejection pattern
  (`ScopeMismatchError`) is the *pattern* to extend, not necessarily the
  *module* — `comparability.py` is itself an unclassified `legacy_root_
  modules` entry per `architecture/modules.yaml`, so whether this phase's
  new rejection lives there or in a `policy/`-owned sibling is a decision
  for whoever migrates `comparability.py` into the classified inventory,
  not a blocking prerequisite for this phase (the same "don't relocate an
  unrelated legacy module as a side effect" reasoning G41 Phase 2 already
  states for `run_plan.py`).

**Effort:** M — mostly additive schema fields plus one new validation
entry point; the design risk is keeping this receipt's fields cleanly
separated from the existing `attribution_path` fields in the same manifest
rather than letting the two blur into one field family that answers
neither question cleanly. The projection-aware identity field (single
target vs. shared/build-wide-plus-attribution-digest) adds one real design
decision — a two-shape union rather than a bare string — but stays
additive schema work, not new extraction logic. **The attribution digest
itself is a genuinely new piece of logic, not previously existing
anywhere in this codebase** (confirmed by a fresh review round — no
current code computes one) — canonical normalization, a hashing
algorithm, a persisted field, and producer/consumer wiring, all defined
above rather than assumed to already exist. This is real, if narrow, new
work; it does not push the phase out of M, but it is not free the way the
rest of this phase's additive schema fields are. **The per-projection
compile-context-fingerprint/public-header-root-digest/TU-inventory
correction adds the identical class of real work**: restricting to a
projection's own attributed TUs before fingerprinting is new logic
alongside the schema shape change (a mapping or subreceipt, not three
bare scalars) — confirmed by a fresh review round to be a second real
gap in the same shared-pack scenario the identity field was already
fixed for. Still additive schema/computation work overall, not a new
extraction pipeline; the phase stays M.

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
- `abicheck/cli_compare_release_helpers.py`'s `_format_release_json`
  (`summary["bundle_findings"]`/`summary["matrix_findings"]`),
  `abicheck/cli_compare_release.py`'s `_release_finding_dicts`, and
  `abicheck/service_scan.py`'s `ScanSetResult.to_dict` — Phase 3's fourth
  projection family (see that phase's own correction above): `compare
  --release`'s and `scan --artifact-set`'s independent `Change`/
  `BundleFinding` dict builders, none reached by `reporter.py`'s six
  builders or by `_baseline_finding_dicts`.
- `abicheck/stack_report.py`'s `_stack_finding_dicts` and `stack_to_json`
  (its `binding_changes` dict comprehension) — Phase 3's fifth projection
  family (see that phase's own correction above): `abicheck stack
  --format json`'s independent per-library and runtime-binding `Change`
  dict builders, none reached by `reporter.py`'s six builders or by
  `_baseline_finding_dicts`/`_release_finding_dicts` despite the latter's
  own docstring naming `_stack_finding_dicts` as a sibling shape.
- `abicheck/cli_compare_fold.py`'s `_fold_suppression_audit_into_text` —
  Phase 3's sixth projection family (see that phase's own correction
  above): `compare --format json --audit-suppressions`'s independent
  `suppression_audit.high_risk_matches` dict builder, a distinct ledger
  from `_suppressed_change_entry` above, not reached by `reporter.py`'s
  six builders or by any of the other projection families.
- `abicheck/impact/correlation.py`'s `RootCauseGroup.to_dict()` — Phase 3's
  seventh projection family (see that phase's own correction above): a
  public dataclass method's independent `members` dict projection,
  reachable by any typed-API caller of `correlate_root_causes()` even
  though no in-repo CLI surface calls it today, not reached by
  `reporter.py`'s six builders or by any of the other projection families.
- `abicheck/sarif.py`'s `properties` bag, its two run-level audit-ledger
  dict projections (`surfaceScope.outOfSurfaceChanges`,
  `buildContextReconciled.changes`), and `abicheck/junit_report.py`'s
  `_add_contract_properties` — Phase 3's SARIF/JUnit surfaces (or their
  `report/`-migrated successors, e.g. `report/render_sarif.py`, if that
  migration has landed by implementation time).
- `abicheck/impact/use_case_impact.py`'s `UseCaseChange`/
  `UseCaseImpact.to_dict()` — Phase 3's eighth projection family (see that
  phase's own correction above): `compare --use-cases`'s independent
  `use_case_impact.by_use_case` dict builder, not reached by `reporter.py`'s
  six builders or by any of the other projection families. Not a Phase 1
  construction site despite matching the `"Change("` grep textually — see
  Phase 1's own correction above.
- `tests/test_evidence_provenance_completeness.py` — Phase 2 (new).
- `scripts/gen_detector_spec.py`, `docs/reference/detector-spec.md` — Phase 3.

## Tests

- Phase 2's completeness gate (above) — the primary regression backstop,
  same shape as `test_canonical_finding_id_completeness.py`.
- Per-slice: existing detector-oracle tests (`test_detector_oracle.py`) gain
  an `evidence_provenance` assertion alongside their existing `ChangeKind`/
  verdict assertions, for every mutation the oracle already covers — no new
  mutation catalogue, just a wider assertion on the existing one.
- **`BundleFinding` provenance, both on the carrier and after `to_change()`
  lowering** (Codex review, fresh evidence — neither `test_detector_
  oracle.py` nor `test_detector_properties.py` constructs a `BundleFinding`,
  since both are scoped to ordinary snapshot-pair detector output, so
  neither the kind-partition completeness gate nor the property test above
  can catch a `bundle.py`/`bundle_signature_evidence.py`/
  `bundle_multibuild.py`/`product_baseline.py` construction site that
  leaves `evidence_provenance` unset, or a regression in `BundleFinding.
  to_change()` that drops it during lowering). A direct, `bundle_models.py`
  -scoped test (alongside this plan's own Files & surfaces `BundleFinding`
  construction-site inventory above) must assert both: (1) each of the four
  real construction sites populates `BundleFinding.evidence_provenance`
  with the shape that site's own evidence supports — the per-side
  `<side>:<tier>:searched:<backend>` form for the `_symbol_evidence_
  sufficient` sites, never a bare positive tag standing in for an unresolved
  case; and, for the version-collapse branch specifically, **the complete
  tuple round 38's correction above establishes, not the two-tag shape an
  earlier draft of this bullet pinned (Codex review, fresh evidence, PR
  #866 round 38 — this bullet previously described a fixed `both:ambiguous:
  version_collapsed` marker paired with only `l0:elf_symtab`, which is
  stale against the per-side derivation and the `elf_dynamic` component
  the corrected paragraphs above now require, and would let a test pass
  on an implementation that either fabricates a `both:` claim for a
  single-sided collapse or drops the dependency-edge evidence)**: (a) the
  side prefix on the `ambiguous:version_collapsed` marker itself — cases
  covering an old-only collapse, a new-only collapse, and a genuine
  both-sides collapse must each assert the *matching* one of
  `old:ambiguous:version_collapsed` / `new:ambiguous:version_collapsed` /
  `both:ambiguous:version_collapsed`, and a test asserting only the
  both-sides case would not catch a regression that always emits `both:`
  regardless of which side(s) `_bare_name_version_collapsed()` actually
  flagged; and (b) the accompanying side-scoped `l0:elf_symtab`
  resolution/export provenance *and* the `new:l0:elf_dynamic`
  dependency-edge provenance together, that
  `_bare_name_version_collapsed()`/`_symbol_was_exported`/
  `_provider_entry_retained_from_old`/`_reachable()` actually establish —
  asserting the ambiguity marker in isolation must fail this test, the
  same way asserting either evidence tag in isolation (with the marker or
  the other tag dropped) must also fail it, since any one of the three
  alone misstates what the finding rests on; and (2) `BundleFinding.to_change()` carries that
  exact value — the full tuple, both before and after lowering, not just the
  marker half of it — through onto the lowered `Change.evidence_provenance`
  unchanged, the same way it already carries `effective_verdict`/
  `modulation_reason`/`modulation_rule` — a regression here is otherwise
  invisible to the "kind partition and ordinary snapshot-pair detector
  output" checks above, since a `BundleFinding`'s only way to reach a
  reporter is through this one lowering call.
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
  to existing surfaces (JSON, SARIF, JUnit, HTML, Markdown) unchanged in
  shape otherwise; see Phase 3's own HTML/Markdown correction above (round
  38) — those two are in scope as additive rendering through each format's
  already-existing per-finding metadata pattern, not excluded by this
  bullet. What stays out of scope is a genuinely new UI surface or layout
  (e.g. a dedicated evidence-provenance panel, a new report section, an
  interactive filter by provider) — not the two renderers themselves.
- **`abicheck/compat/cli.py`'s ABICC-compatible surface** — that format has
  its own, externally-defined schema (ABICC parity) with no slot for this;
  not extended here.
- **Retroactively re-verifying every closed `AGENTS.md` known-gap entry**
  this plan's provenance model could theoretically help diagnose faster in
  the future (the toolchain-identity-probe gap, the linkage-blind-removal
  gap) — those stay exactly as documented; this plan does not attempt to
  close them as a side effect.

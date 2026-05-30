# ADR-024: Public ABI Surface Resolution and False-Positive Traceability

**Date:** 2026-05-30
**Status:** Proposed
**Decision maker:** Nikolay Petrov

---

## Context

[Issue #235](https://github.com/napetrov/abicheck/issues/235) reports that abicheck
does not filter the ABI surface to the symbols declared in the public headers a user
supplies via `-H/--header` (and `--old-header`/`--new-header`). Both reference tools do
this: libabigail filters via `abidw --headers-dir`, and abi-compliance-checker derives
the public surface from the headers fed to `abi-dumper`. Without it, changes in private
/ internal-but-exported symbols are reported as compliance failures — noise that can
drown the signal consumers actually care about (the *public* ABI).

There are **two distinct defects** behind the single issue:

| Layer | Defect | Status |
|-------|--------|--------|
| **L1 — header plumbing** | On PE/Mach-O the CLI dropped `--header`/`--include` entirely (`service._dump_pe`/`cli._dump_macho` never received them), so headers were a silent no-op; directory inputs were not expanded. | **Fixed in [PR #259](https://github.com/napetrov/abicheck/pull/259)** |
| **L2 — surface resolution** | Even where headers *are* honored (ELF), the surface is decided by *export-table membership only*, not by what is declared in the provided public headers. castxml parses the public headers **plus everything they transitively `#include`** (private/internal headers, system headers) with no source-location filtering. Any such declaration that is also exported is treated as public and compared. | **Open — this ADR** |

### Why the current model is insufficient

ADR-016 introduced a three-tier `Visibility` (`PUBLIC` / `HIDDEN` / `ELF_ONLY`). In
practice this **conflates two orthogonal facts** into one axis:

1. **Linkage / export** — is the symbol in the dynamic export table (`.dynsym`, PE
   export directory, Mach-O export trie)?
2. **Declaration scope / provenance** — *where* is the entity declared: in one of the
   explicitly-provided public headers, in a privately-included header, in a system
   header, or only in the binary with no declaration at all?

Today `_visibility()` (`dumper_castxml.py`) sets `PUBLIC` iff the name is in the export
set, `ELF_ONLY` iff present only in `.symtab`, else `HIDDEN`. The variable is named for
"headers" but never consults *which header* a declaration came from. And the diff treats
`ELF_ONLY` as part of the comparable surface (`_PUBLIC_VIS = (PUBLIC, ELF_ONLY)` in
`diff_symbols.py`) — only *removals* of `ELF_ONLY` symbols are softened
(`FUNC_REMOVED_ELF_ONLY`). So a private-but-exported symbol whose declaration leaked in
through a transitively-included header is still compared as public.

### Existing assets we can build on

- `dumper_castxml.py` already reads each element's `file`/`location` attribute (used today
  only to skip compiler built-ins) — the raw material for provenance is present.
- DWARF carries `DW_AT_decl_file`; PDB carries module/source info — provenance is
  available on the debug-info paths too.
- `internal_leak.py` already detects "internal namespace type reachable from public API"
  (`INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`, `VISIBILITY_LEAK`). This is exactly the
  anti-hiding guard a scoping feature must not regress.
- The suppression system (ADR-013) already provides per-symbol / regex / type / member /
  source-file user controls; the ABICC layer adds `-skip-symbols`, `-skip-headers`,
  `-symbols-list`.

### The risk we must design against

Filtering is double-edged. A surface filter that is too aggressive — or a user
suppression that is too broad — can **hide a real break**. The issue reporter is right
that private-ABI noise is unhelpful; but the cure must not silently delete signal. The
guiding constraint for this ADR is therefore: **filtering is demotion + disclosure, never
silent deletion.**

---

## Decision (proposed)

### D1. De-conflate the surface into two axes

Model linkage and provenance independently, and derive the public surface from both.

```text
Linkage  ∈ { EXPORTED, LOCAL_ONLY, HIDDEN }              # from export table / st_other
Origin   ∈ { PUBLIC_HEADER, PRIVATE_HEADER, SYSTEM_HEADER,
             GENERATED, EXPORT_ONLY (no declaration), UNKNOWN }   # from decl source file

PublicSurface(entity) := Linkage == EXPORTED AND Origin == PUBLIC_HEADER
```

`Visibility` (ADR-016) is retained as a backward-compatible *derived* view, but the two
underlying facts become first-class so detectors and reports can reason about them
separately.

### D2. Provenance capture (the enabling mechanism)

Thread the **set of explicitly-provided public headers** (post `expand_header_inputs`)
into the parser and tag every `Function`/`Variable`/`RecordType`/`EnumType` with:

- `source_header: Path | None` — the file the declaration physically came from.
- `origin: ScopeOrigin` — classified against the provided public-header set (a declaration
  whose `source_header` is one of the provided headers ⇒ `PUBLIC_HEADER`; otherwise
  `PRIVATE_HEADER`/`SYSTEM_HEADER`/…).

Sources of provenance, by pipeline: castxml `file`/`location` (header AST), DWARF
`DW_AT_decl_file` (debug path), PDB module/line info (Windows). Persist provenance in
`model.py` and the serialized snapshot (schema bump per ADR-015).

### D3. Reachability closure

The public surface is not just the public *symbols* — it is the transitive **type
closure** reachable from them. From each public+exported root, walk: parameter types,
return types, **public/protected** data members, base classes, typedef targets, template
arguments. A type reachable **only** through private members is out of surface.

Crucially, a type declared in a *private* header that is reachable from a *public* API
stays **in** surface — that is the internal-leak case (D5), which must be reported, not
hidden.

### D4. Surface modes — demote, don't delete

| Mode | Behavior |
|------|----------|
| `export` (current default) | No header scoping; everything exported is surface. Preserves today's behavior. |
| `header-scoped` (opt-in now → default after validation) | Surface = exported ∧ public-header (+ reachable closure). Out-of-surface changes are **still computed**, but re-classified to a compatible/informational tier and **labeled with the reason**. |

Out-of-surface findings are never dropped from the data model — they are demoted (e.g. an
out-of-surface signature change becomes `*_NON_PUBLIC` / reduced-confidence) and remain
visible in verbose / machine-readable output. This is the key difference from libabigail's
hard `--headers-dir` drop: we keep auditability.

### D5. Traceability and anti-hiding (the core requirement)

1. **Surface ledger.** Every report carries a summary: counts (and, with `--show-filtered`,
   the full list) of entities **included** vs **excluded/demoted**, each with a reason:
   `private-header`, `system-header`, `not-exported`, `suppressed-by-user`,
   `mangling-fallback`, `no-provenance`. Available in text, JSON, and SARIF.
2. **Leak guard always wins.** If a `PRIVATE_HEADER`-origin type is reachable from a
   `PUBLIC_HEADER`+exported root, emit `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` (extend
   `internal_leak.py`) **regardless of scoping mode**. Scoping must never suppress a leak.
3. **Confidence is explicit.** Distinguish full-confidence (provenance + types known) from
   reduced-confidence (export-only, mangling fallback, missing provenance). The PE/Mach-O
   `UserWarning` fallbacks from PR #259 become structured confidence signals.
4. **No silent suppression of breaks.** When a *user* control (suppression/allowlist) would
   hide a finding that is `abi_breaking`, require an explicit acknowledgment (e.g. a
   `reason:` field) and emit a warning in the ledger. Suppressing breakage should be a
   deliberate, logged act.
5. **Determinism.** Provenance classification and closure are deterministic and order-stable;
   the snapshot cache key includes the resolved public-header set and build flags.

### D6. Public headers vs. user filtering controls (the explicit question)

These are **complementary layers with different authority**, and should remain distinct,
composable, and individually auditable.

| Aspect | Public-header scope (D2–D4) | User filtering controls (suppression / allow-lists) |
|--------|------------------------------|------------------------------------------------------|
| Source of truth | The API **contract** — declarative, derived from real headers | Human **intent** — imperative, hand-authored |
| Granularity | Header file + reachability closure | Per-symbol / per-type / regex / member / source-file |
| Evolution | Auto-tracks API as headers change | Static; must be maintained, can drift |
| Typical use | "What *is* the public API" | Exceptions: known/intentional changes, experimental symbols, escape hatches |
| Failure mode | Wrong header set ⇒ wrong surface — **but visible** in the ledger | Over-broad rule ⇒ can **silently hide a real break** |
| Direction | Defines the **default** surface | **Overlay** that narrows *or* widens it |

**Precedence / composition.** Public headers establish the *default* surface. User controls
are an explicit overlay in two directions:

- **Narrowing** (exclude): the existing suppression system. Use for symbols that are in
  public headers but you don't want to commit to yet (experimental), or known/intentional
  changes. Logged distinctly from `private-header` exclusions; break-hiding requires a reason.
- **Widening** (force-include / public allowlist, à la ABICC `-symbols-list`): for symbols
  you *do* guarantee but that header provenance can't see — hand-written asm stubs, `.def`
  exports, `extern "C"` shims, or the MSVC-mangling case where castxml can't match names.
  Promotes them into the public surface.

So: rely on **public headers** to *define* the surface automatically and verifiably; use
**user controls** for the precise, intentional exceptions headers can't express — but keep
the two as separate, traceable inputs so an exception can never quietly masquerade as "not
public."

### D7. Handling the specific cases from #235

| Case | Behavior under this design |
|------|----------------------------|
| **A.** PE/Mach-O headers ignored | **Fixed (PR #259):** headers plumbed through, directories expanded, explicit warning + export-table fallback when castxml is unavailable or names don't match. |
| **B.** Private *symbol* change/removal flagged | `header-scoped` mode: demoted to compatible/informational, recorded in the ledger as `private-header`/`not-exported`. Removal already maps to `FUNC_REMOVED_ELF_ONLY` (compatible). |
| **C.** Private *field* inside a public struct changes layout | **Still a real break** — reachability (D3) keeps it in surface. Documented as *not* a false positive: it is observable to consumers. |
| **D.** Private declaration pulled in via transitive `#include` of a public header | Excluded by provenance (D2): its `source_header` is not in the provided public-header set — *unless* it is reachable from a public API, in which case the leak guard (D5.2) reports it. |

---

## Validation & testing strategy

The feature is only credible if we can prove it neither over- nor under-filters.

1. **Golden surface corpus.** Fixtures with a known public/private split; assert the
   *resolved surface* (symbols + reachable types) equals the expected set. Snapshot the
   ledger.
2. **Parity tests** (`libabigail`, `abicc` markers). Run `abidw --headers-dir` and
   `abi-dumper` on identical inputs; assert our scoped surface matches theirs within a
   documented delta table (extends `docs/reference/tool-comparison.md`).
3. **Property-based** (hypothesis, `slow`):
   - *Monotonicity:* adding a purely-private symbol or a non-public header changes no finding.
   - *Subset:* the `header-scoped` finding set ⊆ the `export`-mode finding set (filtering only
     removes/demotes, never invents).
   - *Idempotence / order-independence* of provenance classification and closure.
4. **Anti-hiding negative tests** (the most important):
   - A real break on a public-header type **still fires** under scoping.
   - An internal-type leak **still surfaces** under scoping.
   - A user exclude that would hide an `abi_breaking` change emits a warning / requires a reason.
5. **Cross-platform:** ELF (castxml + DWARF provenance), PE (MSVC mangling fallback), Mach-O.
6. **#235 regression fixtures:** cases A–D above as concrete, checked-in examples with
   `ground_truth.json` entries (per the AI-readiness `examples-ground-truth` gate).
7. **False-positive-rate gate:** track FP count on a benchmark corpus; fail CI on regression,
   analogous to the mypy baseline gate.
8. **Edge cases:** transitive includes, `#ifdef` build variants (interacts with ADR-020
   build context), templates / inline-only declarations, generated headers, anonymous types,
   ordinal-only PE exports, missing/partial provenance.

---

## Implementation phasing

| Phase | Scope |
|-------|-------|
| **0** | PE/Mach-O header plumbing + directory expansion + fallback warnings — **done (PR #259)** |
| **1** | Provenance capture: source-header tagging in castxml/DWARF/PDB parsers; model + serialization fields (schema bump) — *future (see note)* |
| **2** | Header-scope resolution + surface ledger + `--scope-public-headers`/`--show-filtered` (opt-in, default off) — **done** |
| **3** | Reachability closure + leak-guard integration (extend `internal_leak.py`) — **done (closure shipped; leak exemption wired)** |
| **4** | User-control overlay: widening public allowlist; integrate suppression as the narrowing layer; precedence + anti-hiding guard |
| **5** | Parity + FP-rate gates; flip default to `header-scoped` once validated |

### Implementation note (Phase 2/3 as shipped)

The first cut derives the public surface from data the dumper *already*
captures, avoiding the schema churn of full Phase 1 provenance:

* **Public roots** = functions/variables with :data:`Visibility.PUBLIC`
  (ADR-016 already means "exported **and** declared in a provided public
  header"). When no headers were supplied (`elf_only_mode`), the surface is
  declared *unresolvable* and scoping is a no-op.
* **Public types** = the transitive reachability closure over those roots'
  return/parameter/field/base/typedef types (`abicheck/surface.py`).
* Findings outside the surface are moved to an audit ledger
  (`DiffResult.out_of_surface_changes`, surfaced by `--show-filtered`),
  never dropped; internal-leak kinds are exempt.

This is wired as the opt-in `FilterNonPublicSurface` post-processing step
(`compare(..., scope_to_public_surface=True)` /
`abicheck compare --scope-public-headers`). Example cases
`case118`–`case120` exercise it end-to-end; `tests/test_surface.py` covers
the resolver, classifier, and anti-hiding guarantees.

The remaining Phase 1 work — recording *which* header each declaration came
from, so the surface can distinguish "private header transitively included"
from "public header" independently of reachability — is still future work
and is what unlocks the per-finding `private-header` ledger reason and the
widening/narrowing user overlay (Phase 4).

---

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep export-table-only (status quo) | The #235 complaint — noisy, reports private ABI |
| Pure user suppression | Manual, drifts, and silently hides real breaks; fails the traceability requirement |
| Visibility attributes only (`-fvisibility=hidden`) | No effect when the library is built `-fvisibility=default` (the common case; see `tool-comparison.md`) |
| libabigail-style **hard drop** of non-public | Simple, but loses auditability and can hide leaks — rejected in favor of *demote + disclose* (D4/D5) |

## Consequences

**Positive:** materially fewer false positives; parity with libabigail/abicc; an auditable
"why included/excluded" trail; structurally cannot hide a real break or an internal leak.

**Negative / risks:** provenance plumbing across three debug formats; snapshot schema bump
and cache-key changes; reliance on castxml/DWARF source-location accuracy; the MSVC
C++-mangling gap on PE remains a reduced-confidence fallback (documented, not solved here).

## References

- Issue #235; PR #259 (Phase 0)
- ADR-011 — Change Classification Taxonomy
- ADR-013 — Suppression System (the user-control narrowing layer)
- ADR-015 — Snapshot Serialization (schema versioning for provenance fields)
- ADR-016 — Three-Tier Visibility Model (the axis this ADR de-conflates)
- ADR-020 — Build-Context Aware Header Extraction (which defers "public header scope
  resolution" to this ADR; correct build context is a prerequisite for accurate provenance)
- `abicheck/internal_leak.py`, `abicheck/dumper_castxml.py`, `abicheck/diff_symbols.py`

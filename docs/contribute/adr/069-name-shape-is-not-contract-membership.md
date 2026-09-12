# ADR-069: Name Shape Is Not Contract Membership

**Date:** 2026-09-12
**Status:** Accepted — implemented. Records one product rule and the three
behaviour changes it forces, of which one (`v0` leaving
`DEFAULT_EXPERIMENTAL_NAMESPACES`) is a change to an existing default and is
the reason this ADR exists at all: `AGENTS.md`'s "Authority" rule requires an
ADR and a migration path before an established default moves. Amends
[044](044-reachability-aware-suppression.md) (adds the
`experimental_namespaces:` policy key beside its `internal_namespaces:`) and
constrains what [049](049-contract-relevance-and-compatibility-configuration.md)'s
consumers may claim without it. Owners: `abicheck/model/symbol_ownership.py`,
`abicheck/diff_namespaces.py`, `abicheck/report/render_markdown.py`,
`abicheck/policy/policy_file_namespaces.py`.

## Context

A user comparison of a real C++ library produced a report that was correct in
its detection and wrong in its explanation, three separate ways. All three
traced to one habit: **abicheck answered "is this part of the public
compatibility contract?" from how a symbol or namespace is spelled.**

1. The Markdown surface breakdown said RTTI and internal-namespace breaking
   findings were "typically … not public-API breaks" and labelled the
   remainder the "genuine public-surface" count. The library's real break was
   a vtable layout change: virtual functions added to a public base shifted
   the dispatch slots of a derived class's own virtuals, confirmed by both
   GCC and Clang emitting `jmp *0x60(%rax)` before and `jmp *0x70(%rax)`
   after. The report told the user to disregard it.
2. `symbol_origin` scanned the **whole** mangled string for an
   internal-namespace component. A mangled name embeds its parameter types,
   so `svs::consume(const svs::detail::Token&)` —
   `_ZN3svs7consumeERKNS_6detail5TokenE` — was classified internal on the
   strength of a `6detail` belonging to the *parameter*.
3. `v0` was an unconditional entry in `DEFAULT_EXPERIMENTAL_NAMESPACES`, so a
   library whose current public API lives in an inline-versioned
   `namespace v0` had every removal from that API reported as an
   *experimental*-API removal.

## Decision

**D1. Symbol representation and naming convention are not contract
membership.** A vtable, typeinfo or VTT symbol says how an entity is
*represented*; a `detail`/`impl` segment says what convention a project
follows; a version segment says which API version a declaration belongs to.
None is evidence about the compatibility contract. Contract membership is
ADR-049's question, answered from declaration identity, provenance and
reachability against a resolved `--contract`.

**D2. A name-derived bucket may be counted, never used to discount a
finding.** Reporting "N of these findings are vtable/RTTI artifacts" is
useful. Concluding "therefore they are not public breaks" is not supported by
that evidence and must not appear in any output. Where a report shows such a
breakdown it states what the counts do not establish and points at
`--contract`.

**D3. Only the scope that *owns* an entity determines its scope-convention
classification.** Neither a parameter type's namespace nor the entity's own
leaf name may. This is resolved structurally (the Itanium nested-name
parser), not textually; where the parser does not model a production, the
fallback is restricted to the nested-name region and errs toward
*under*-detecting the convention.

**D4. A version namespace is not a stability promise.** `v0` leaves the
built-in experimental set. Projects that do use a version segment to mean
"experimental" state it explicitly.

## The default change and its migration (D4)

This is the part `AGENTS.md` requires this record for.

**What changes.** `DEFAULT_EXPERIMENTAL_NAMESPACES` goes from
`("experimental", "preview", "v0")` to `("experimental", "preview")`.

**Who is affected.** Only a project with a namespace segment spelled exactly
`v0`, and only for the findings `DetectNamespacePatterns` produces
(`EXPERIMENTAL_*`). No other project's output changes at all.

**`EXPERIMENTAL_*` is an overlay, not a relabelling.** This ADR's first draft
said the finding was "still emitted, only under a different kind"; that was
wrong, and the correction matters for reading the rest of this section.
`DetectNamespacePatterns` *appends* to what the ordinary detectors already
produced — it never replaces it. Measured on a removed `ns::v0::foo`:

| Configuration | Findings | Verdict |
|---|---|---|
| `v0` not experimental (this change's default) | `func_removed` (BREAKING) | `BREAKING` |
| `v0` configured experimental | `func_removed` (BREAKING) **+** `experimental_removed_without_replacement` (API_BREAK) | `BREAKING` |

So the default change **removes one additional contextual finding**; it does
not alter, downgrade, or hide the underlying removal.

**Is this a loosening?** No, and the overlay structure is why. The plain
`FUNC_REMOVED` is emitted either way and is already `BREAKING`, which
dominates the overlay's `API_BREAK`, so the verdict and the exit code are
identical under both configurations — a break cannot be hidden by this change,
because the finding that carries the break was never the one being removed.
What is dropped is only the annotation asserting the removal was *expected* —
the assertion this ADR's D4 says a version segment cannot support in the first
place. A project that wants the annotation back states it and gets both
findings, exactly as before.

**Migration.** One line in a `--policy` document restores the previous
behaviour exactly, overlay included:

```yaml
experimental_namespaces:
  - experimental
  - preview
  - v0
```

The key is new in this change (`PolicyFile.experimental_namespaces`, parsed by
`policy/policy_file_namespaces.py`, threaded through
`PipelineContext.experimental_namespaces`), and it is the mechanism this ADR
offers in exchange for the default: the behaviour is not withdrawn, it stops
being *assumed*. It is deliberately a policy key rather than a CLI flag —
which segments a project treats as unstable is a stable property of the
project, not a per-run operand (ADR-068's operand-vs-property split).

**Why not a deprecation cycle.** There is no spelling to deprecate: the old
behaviour was an unnamed default, so there is nothing a warning could tell a
user to stop writing. Emitting a warning on every comparison that happens to
contain a `v0` segment would fire mostly for projects the default was wrong
about, which inverts the signal. Hard change plus a documented one-line
restoration, consistent with ADR-068's "hard removal, no deprecation aliases".

## Consequences

- The `experimental_namespaces:` key becomes public policy surface,
  registered in `docs/_meta/topics.yaml` and documented in
  `docs/use/policies.md`, and contributes
  `surface.experimental_namespaces` to the effective-config digest — two
  runs differing only in this key must not report identical effective
  configuration, since it changes which findings are emitted.
- The JSON `abi_surface_breakdown` block keeps its `rtti_churn`/
  `internal_churn` key names for report-schema stability. They now read
  against D2's prose, which is an accepted wart: renaming them is a schema
  break for a cosmetic gain, and the block's counts were never the defect —
  the conclusion drawn from them was.
- D1/D2 are enforced negatively (outputs stop claiming what they cannot
  establish). The positive half — classifying these findings against a
  resolved contract — is ADR-049's `--contract` machinery, which the surface
  breakdown does not consult. That gap is recorded against the bug class
  rather than closed here.
- A fourth defect from the same report is **not** addressed by this ADR:
  `Visibility.PUBLIC` used as a proxy for "declared in the public API",
  which conflates source presence with dynamic export. It is an
  evidence-selection problem, not a naming one, and needs an `AbiSnapshot`
  schema change inside ADR-050's comparability contract. Recorded in
  `docs/contribute/known-gaps.md`.

## Alternatives considered

- **Keep `v0` and special-case inline namespaces.** Rejected: inline-ness is
  not reliably observable from every evidence tier, and the rule would still
  be a guess about intent from spelling — the thing D1 rejects.
- **Treat `v0` as experimental only when no `v1`+ exists.** Rejected for the
  same reason, with an added failure mode: a project's first stable release
  would silently reclassify its whole history.
- **Drop the RTTI/internal breakdown entirely.** Rejected: the counts are
  genuinely useful for diagnosing a missing `-fvisibility=hidden`. D2 keeps
  the observation and removes only the unsupported conclusion.
- **Fix the mangled-name scan by lengthening the component list.** Rejected:
  the defect is positional, not lexical — no list of components distinguishes
  a parameter's namespace from the owner's.

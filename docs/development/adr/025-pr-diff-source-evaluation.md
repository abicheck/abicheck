# ADR-025: PR-Diff-Aware ABI Evaluation (Source Diff as Trigger and Localizer)

**Date:** 2026-06-06
**Status:** Proposed
**Decision maker:** Nikolay Petrov

---

## Context

When abicheck runs in CI on a pull request (via the GitHub Action, ADR-017, or
any CI integration), an additional signal is available that the core
`compare` pipeline does not use today: **the PR diff** — the set of changed
files and changed line ranges between the base and head commits.

This raises a natural question: should abicheck consume the diff to decide
*what* to analyse, *how* to present findings, and *whether* to run at all?

The temptation is to treat the diff as the primary input — "scan the changed
lines, flag the ones that touch the ABI." That framing is **wrong for abicheck**
and this ADR exists primarily to record *why*, and to define the role the diff
*should* play.

### Why diff-as-primary-input is the wrong model

abicheck's entire value proposition is that it compares **built artifacts**
(shared libraries + their debug info / headers), not source text. The hardest
and most valuable ABI breaks have **no corresponding ABI-relevant line in the
diff**:

| Class of break | What the diff shows | Why source-diff analysis misses it |
|---|---|---|
| **Macro / `#define` driven layout** | a changed `-DFOO` flag or a one-line `#define` | The *type's* source is untouched; only the preprocessed result changes. |
| **Build-system / flag drift** | a `CMakeLists.txt` / `configure` / `CFLAGS` edit | `_GLIBCXX_USE_CXX11_ABI`, `-fabi-version`, `-std`, `-fpack-struct`, visibility, LP64/ILP64, LTO — zero type-source diff, real ABI shift. (See `case103_toolchain_flag_drift`, `case104_glibcxx_dual_abi_flip`, `case112_lp64_ilp64`.) |
| **Transitive header impact** | an edit to struct `A` only | An unedited type `B` that embeds `A` by value silently changes layout. |
| **Dependency / toolchain bump** | a lockfile or CI image bump | No source diff at all; re-exported inline/template code can shift. |

A source-AST or pure-diff tool (for example the Clang-based
[abicorn](https://github.com/isuckatcs/abicorn-on-graduation-ceremony), which
diffs two C++ source trees directly) cannot see these by construction. abicheck
can, *because it does not depend on the diff*. Any design that makes the diff
authoritative would regress abicheck's core differentiator.

### What the diff is genuinely good for

The diff is a high-quality **trigger** (decide whether / what to rebuild and
compare) and **localizer** (map a binary-derived finding back onto the source
the reviewer is looking at). Those are the two roles this ADR scopes.

---

## Decision (proposed)

Adopt the diff as an **auxiliary** signal layered on top of the existing
artifact comparison, never as a replacement for it. Three capabilities, in
priority order:

### D1. Diff-aware triggering and path triage

Classify each changed path into a coarse bucket and let the bucket decide the
analysis depth:

| Changed paths | Action |
|---|---|
| Build files (`CMakeLists.txt`, `configure*`, `*.cmake`, `Makefile*`, `meson.build`, CI flag files), dependency manifests / lockfiles | **Force a full rebuild-and-compare**, even when no `.c/.cpp/.h` changed — this is the "no source change, real ABI change" bucket. |
| Public headers / sources that contribute to a tracked library | Rebuild-and-compare, scoped to the affected library/target. |
| Tests, docs, CI unrelated to build flags | Skip ABI analysis (report "no ABI-relevant change"). |

The key behaviour is the **first row**: a build-config-only PR must *not* be
treated as "nothing to check." This is where diff-as-primary-input tools fail
silently.

Triage is advisory and always fail-open: if path classification is uncertain,
run the full comparison.

### D2. Source localization of findings (diff annotation)

Every `DiffResult` already can carry a declaration location when header
(castxml) or debug-info (`DW_AT_decl_file`/`DW_AT_decl_line`, PDB module/line)
data is present (ADR-024 notes provenance is available on these paths). Use it
to:

- map each finding to the nearest changed hunk and emit an **inline review
  comment** at that location (GitHub Action), instead of only a summary table;
- for findings with **no source line** (macro/build/transitive breaks), attach
  them to the build file that changed, or to a clearly-labelled
  "no corresponding source line" section of the summary — making the
  invisible-in-diff breaks *more* prominent, not less.

### D3. Two-bucket framing in PR output

Partition the PR report into:

1. **Touched & changed** — a changed line maps to a detected ABI change
   (expected, easy to review).
2. **Untouched source, changed ABI** — the high-value bucket: no relevant diff
   line, yet the ABI moved (macro/build/transitive/dependency). Surface this
   loudest; it is exactly what human reviewers miss.

A third, optional bucket — **touched but ABI-stable** — can reassure reviewers
that an edit to a public type did *not* move the ABI.

### D4. (Optional, future) Source-AST pre-filter

Because a source-diff/AST pass needs no build, it can run as a **fast
pre-check** on the diff to surface obvious source-level/API breaks early
(removed declaration, narrowed access, `final` added — see ADR-026 / the
"source-only undetectable" limitation), while the artifact comparison remains
the authoritative gate. This mirrors how source tools (abicorn) and binary
tools (abicheck) are complementary: **source for speed and source-only signals,
binary for ground truth and the no-source-change cases.** Scoped as future work;
not part of the initial implementation.

---

## Consequences

**Positive**
- Faster PR feedback (triage skips irrelevant PRs; scoping limits rebuild cost).
- Findings land where reviewers read them (inline), improving signal.
- The "build-config-only / dependency-bump" blind spot of diff-centric tools
  becomes a *highlighted* abicheck strength rather than a silent gap.

**Negative / risks**
- Path classification is heuristic; it must fail-open (run full compare on
  doubt) so triage never hides a break. This is the same demotion-not-deletion
  constraint as ADR-024.
- Inline annotation depends on decl-location provenance, which is only present
  in header/debug-info modes — symbols-only mode degrades to summary-only
  output (acceptable).
- Requires the CI integration to provide base/head refs and the changed-file
  list; the core `compare` CLI stays diff-agnostic.

**Non-goals**
- Replacing artifact comparison with source-text scanning.
- Inferring an ABI verdict from the diff alone.

---

## Relationship to other ADRs

- **ADR-017 (GitHub Action)** — the consumer of D1–D3; the diff/refs come from
  the Action context.
- **ADR-024 (Public ABI surface resolution)** — supplies the decl-location
  provenance D2 needs, and the fail-open philosophy D1 reuses.
- **ADR-020a (Build-context capture)** — the build-file trigger in D1 dovetails
  with build-context-aware header extraction.
- **ADR-026 (Source-only undetectable changes)** *(companion)* — defines the
  class of changes the optional D4 pre-filter would target.

# ADR-026: Source-Only Changes and the Evidence-Tier Boundary

**Date:** 2026-06-06
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

abicheck compares **built artifacts** (shared libraries + their DWARF/PDB debug
info + public headers via castxml). A recurring question — sharpened by a
comparison against the source-AST tool
[abicorn](https://github.com/isuckatcs/abicorn-on-graduation-ceremony) — is:
*which real source/API breaks can abicheck never see from binaries, and do we
need a standalone source front-end to cover them?*

Running abicheck binary-only against abicorn's ~80-case corpus made the boundary
concrete. With type DWARF present, abicheck agreed on the large majority of
type/layout/vtable/function changes; the residual misses fell into three groups:

1. **Recovered by supplying headers.** `final`, method access, ref-qualifiers,
   `inline`, `noexcept`, `explicit`, default arguments — castxml
   (`header_aware` tier) carries these; DWARF/symbols do not. These are not
   capability gaps, they are *tier* gaps: run abicheck with headers and they are
   detected. The one true catalogue gap here — the `final` class-key, which
   castxml exposes but abicheck did not model — is closed in the current implementation
   (`TYPE_BECAME_FINAL` / `TYPE_LOST_FINAL`, `case121`).

2. **Invisible to any artifact comparison.** Code that never becomes a symbol
   *and* is not modelled by castxml — uninstantiated templates, never-included
   inline bodies — leaves nothing to diff in either the binary or the castxml
   AST (`case122`). No amount of header- or debug-info plumbing recovers it; a
   pure source-AST diff is the only thing that can.

3. **Artifacts of declaration-only inputs** (no emitted symbol/vtable) — not
   real limitations; with real symbols abicheck detects them.

## Decision

1. **Headers are the source-of-record tier, not a standalone source front-end.**
   We will *not* embed Clang to build a parallel source-AST comparator. The
   `header_aware` tier (castxml) already recovers the source-level qualifiers
   that matter, and adding a second full source pipeline duplicates that for a
   thin residual (group 2). This keeps abicheck lightweight (ADR-001).

2. **Model the evidence-tier boundary explicitly in docs and ground truth.**
   The `final` gap (group 1) is closed. The genuinely-undetectable residual
   (group 2) is documented as a *known limitation with calibration fixtures*
   (`case122`, `known_gap` in `ground_truth.json`) rather than pretended away.
   [Limitations → Source-only changes](../../concepts/limitations.md) gives the
   per-change detectability matrix.

3. **CI guidance: feed headers and/or debug builds.** Because detectability is
   tier-dependent, the recommended CI input is a debug build *with public
   headers supplied*, even when the shipped artifact is stripped. Comparing a
   stripped release binary alone yields only `elf_only` coverage. This guidance
   is now in the limitations doc and the getting-started/CLI docs.

4. **A source-AST pre-filter is the only place a source pass earns its keep**
   (ADR-025 D4, optional/future): operating directly on a PR diff with no build,
   to surface group-2 breaks early. Even then the artifact comparison remains
   authoritative.

## Consequences

- **Positive:** the boundary is honest and testable; users know that supplying
  headers (and debug info) is required for full coverage, and why. No heavyweight
  Clang dependency.
- **Negative:** group-2 changes (uninstantiated templates) remain undetected in
  pure abicheck usage — accepted, documented, and fixture-pinned.
- **Non-goal:** a standalone source-AST comparator inside abicheck.

## Relationship to other ADRs

- **ADR-001 (Technology stack)** — the lightweight-tool constraint behind D1.
- **ADR-003 (Data-source architecture)** / **ADR-016 (Visibility model)** — the
  tier model this ADR makes user-visible.
- **ADR-024 (Public ABI surface resolution)** — reachability scoping interacts
  with type-level findings such as `type_became_final` (a class enters the
  surface by being referenced from a public API).
- **ADR-025 (PR-diff source evaluation)** — the optional source-AST pre-filter.

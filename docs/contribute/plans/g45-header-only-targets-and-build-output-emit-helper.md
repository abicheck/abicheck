---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G45 — Header-only project targets and a validated `build-output.json` producer helper

## Problem

Two smaller, independent usability gaps in the declarative project layer,
grouped into one plan because both are P2 hardening work with no shared
design dependency, and each is small enough on its own not to warrant a
separate numbered plan:

1. **No native header-only target kind.** `abicheck/buildsource/
   project_targets.py`'s `kind: library` target validation hard-requires a
   `binary_pattern` (`"target {target.id!r}: kind: library requires
   binary_pattern."`, confirmed present at the review's base commit). There
   is no way to declare a project target whose complete contract is
   headers, inline functions, templates, macros, and `constexpr` values —
   the increasingly common "header-only library" shape — without
   fabricating a placeholder `.so` that doesn't correspond to anything the
   project actually ships.
2. **No validated `build-output.json` producer API.** The current upstream
   documentation states there is no `abicheck project emit-build` — every
   integrating project hand-authors the manifest itself, which means
   repeated, drift-prone hand-written JSON generation instead of one typed
   builder that computes digests/tool-identities/normalizes paths and
   guarantees the result passes `project validate-build`.

## Goal & acceptance criteria

### Header-only targets

A project can declare a target with no binary requirement whenever every
check declared against it uses `depth: headers|build|source` — no ABI
detection at `depth: binary`/`depth: symbols` is possible for a target with
no compiled artifact, so those depths remain invalid for this target kind,
same as they'd be meaningless today. Baseline publication for such a target
stores header/source snapshots without fabricating a placeholder `.so`.

**Acceptance test**: a pure header library supports detecting, all without
requiring a binary:

- a compatible inline API addition;
- a default-argument removal;
- a macro value change;
- a template body change.

### `build-output.json` emit helper

A typed builder plus a small CLI (`abicheck project emit-build`) that:

- accepts already-built artifact/header/evidence paths as input (this tool
  does not run the user's build system — see Out of scope);
- computes content digests and tool identities for whatever it's given;
- normalizes relative paths consistently with what `project validate-build`
  expects;
- writes a document that passes `project validate-build` without further
  hand-editing.

**Acceptance test**: for a representative set of already-built artifacts
(a binary, a header root, a generated-header root, an evidence pack),
`abicheck project emit-build` produces a `build-output.json` that
`project validate-build` accepts with zero errors, and that a hand-written
manifest for the identical inputs would also have to satisfy (i.e. the
helper doesn't accept anything the validator itself wouldn't).

## Design

### Header-only target kind

Add either a new `kind: header-library` value, or relax `kind: library`
to accept a target with no `binary_pattern` **only** when every check
declared for it uses a headers/build/source depth — the review names both
options and defers the choice; prefer the narrower relaxation
(`kind: library` with an optional `binary_pattern`) over a wholly new kind
if it can be validated without weakening the existing "library requires
binary_pattern" invariant for every *other* target that does have one,
since a new kind duplicates every other place `kind: library` is already
matched against (`_validate_library_target` and its neighbors in
`project_targets.py`) while a conditional relaxation extends one validation
function. Make the actual choice empirically: prototype both against
`project_targets.py`'s existing test suite and pick whichever needs fewer,
more localized changes.

Depth-gating for this target shape: `_validate_library_target` (or its
header-only-aware successor) must reject a check declaring
`depth: binary`/`depth: symbols`/`depth: build`-without-source-facts
against a target with no `binary_pattern` — there is nothing for those
depths to extract. `depth: headers`/`depth: build` (header+build evidence
only)/`depth: source` remain valid.

Baseline publication (this plan depends on nothing from G41, but shares
its baseline-manifest shape): a header-only target's stored baseline omits
whatever fields describe a binary (no ELF/PE/Mach-O metadata, no exported
symbol table) and is explicit about that omission rather than leaving
those fields as an ambiguous absence indistinguishable from "binary
evidence wasn't collected this run."

**Relaxing `project_targets.py` alone is not sufficient — `build-output.json`'s
own model and validator must change too, or the target can never pass
`project validate-build` regardless of what the project schema allows.**
Confirmed by reading `abicheck/buildsource/build_output.py`:
`BuildOutputTarget` has no target-kind discriminator at all and
unconditionally serializes a `binary` field, and `validate_build_output()`
unconditionally calls `_binary_issues()` for every target
(`abicheck/buildsource/build_output.py:488-492`: `if not target.binary:
return [f"target {target.id!r}: no binary declared."]`) — so a header-only
target's `build-output.json` entry fails validation with "no binary
declared" the moment it's checked, before baseline publication is ever
reached. This plan must therefore also cover:

- a target-kind (or equivalent) field on `BuildOutputTarget` distinguishing
  "intentionally binary-less" from "binary evidence missing" — the same
  distinction `project_targets.py`'s own relaxation needs, kept consistent
  between the two rather than each inventing its own signal;
- `_binary_issues()` (or its header-only-aware successor) skipping the
  "no binary declared" error specifically for a target the schema marks
  header-only, while still requiring it for every other target kind —
  never relaxing the check globally;
- a schema-version bump for `BuildOutputTarget` if the discriminator is a
  new required-when-present field, per this repo's existing schema-versioning
  discipline for `build-output.json`-shaped documents.

Prototype `project_targets.py`'s relaxation and `build_output.py`'s model/
validator change together — a design that only touches one half will not
produce a working end-to-end path, which is exactly the gap a first
implementation attempt found here.

### `build-output.json` emit helper

A new typed builder (likely `abicheck/buildsource/build_output_emit.py`, a
sibling of the existing `build_output.py` schema/validation module) with
one clear separation of responsibility: **abicheck does not discover or
run a build** — build-system-specific discovery (walking a CMake/Bazel/Make
output tree to find the artifacts in the first place) stays the
integrating project's or a separate adapter's job, matching this codebase's
long-standing "abicheck doesn't own the build" stance (see G18's Bazel
build-evidence work for the precedent: abicheck consumes build evidence,
it doesn't produce it). What abicheck should own is safe, correct
*construction* of the interchange format once the paths are known:

```
abicheck project emit-build \
  --target core --binary build/libcore.so \
  --public-header-root headers/core \
  --generated-header-root generated-headers/core \
  --evidence-pack build/core.factspack \
  --out build-output.json
```

The builder computes digests (reusing whatever digest primitives
`build_output.py`'s existing validation already relies on — don't invent a
second hashing convention), fills in tool identity (abicheck version,
compiler identity where discoverable), normalizes every path relative to
the manifest's own location, and — critically — runs the identical
validation `project validate-build` runs before writing, so the emitted
file can never itself fail the validator it's meant to satisfy.

## Files & surfaces

- `abicheck/buildsource/project_targets.py` — header-only target
  kind/relaxation and its depth-gating.
- `abicheck/buildsource/build_output.py` — `BuildOutputTarget`'s target-kind
  discriminator, `_binary_issues()`'s header-only exemption, and the
  accompanying schema-version bump — see the Design section above; without
  this, the target still fails `project validate-build` regardless of what
  `project_targets.py` allows.
- `abicheck/buildsource/baseline_publish.py` — header-only baseline
  handling (no fabricated binary fields).
- `abicheck/buildsource/build_output_emit.py` (new) — the typed builder.
- `abicheck/cli_project.py` — new `emit-build` subcommand, following the
  existing `project validate`/`project validate-build`/`project plan`
  subcommand precedent (root `AGENTS.md`'s "Adding a new top-level command"
  section already establishes that this class of operation belongs under
  the existing `project` group, not as a new root command).
- `docs/reference/cli-reference.md` (generated) — new subcommand entry.

## Tests

- `project_targets.py` unit tests: header-only target validation (valid
  depths accepted, invalid depths rejected), alongside the existing
  `kind: library` test suite (extend, don't duplicate).
- An `integration` fixture: a real header-only library (templates, macros,
  `constexpr` values, inline functions) exercising all four acceptance-test
  changes above.
- `emit-build` unit tests: each accepted input combination produces a
  manifest that round-trips through `project validate-build` cleanly; a
  malformed/missing input path produces a clear, typed error rather than a
  manifest that later fails validation silently.

## Effort & risk

M combined (S–M each):

- Header-only targets: S–M — mostly validation-logic relaxation plus
  baseline-publication field omission; low architectural risk since it's
  additive to an existing, well-tested validation path.
- `emit-build` helper: M — the builder itself is straightforward once the
  digest/normalization conventions are reused rather than reinvented; the
  main risk is scope creep into build-system discovery, which is
  explicitly out of scope (see below) and must stay that way to keep this
  a small, well-bounded addition.

## Out of scope

- Any build-system-specific discovery logic (walking a CMake/Ninja/Bazel
  output tree to *find* artifacts) — `emit-build` accepts already-known
  paths; discovery adapters, if ever built, are a separate, later plan.
- A header-only *bundle* member (a header-only library participating in
  G38's bundle/multibuild analysis) — this plan covers a single header-only
  target; cross-referencing it from bundle analysis is G38's concern if and
  when it comes up.

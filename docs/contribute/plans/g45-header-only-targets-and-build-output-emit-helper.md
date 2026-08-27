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

Depth-gating for this target shape, stated as one unambiguous rule (an
earlier draft of this plan phrased this two contradictory ways in
adjacent sentences — corrected here): `_validate_library_target` (or its
header-only-aware successor) must reject `depth: binary`/`depth: symbols`
against a target with no `binary_pattern` — those two depths need a
compiled artifact to extract from, and there is none. `depth: headers`/
`depth: build`/`depth: source` all remain valid for a header-only target,
since none of the three requires a binary — `depth: build` here means
ordinary L3 build evidence (compile flags/macros/target facts from a
compile database or build-system adapter), which a header-only target can
supply exactly as any other target does.

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
- **`actions/baseline/action.yml`/`actions/baseline/run.sh`** — required,
  not optional, confirmed by reading `run.sh` directly: it hard-rejects any
  `libraries` entry lacking `artifact` (`"entry {i} must be an object with
  at least \"name\" and \"artifact\""`) and always builds
  `CMD=(abicheck dump "$artifact")` — so neither reusable baseline workflow
  can publish a header-only target's baseline today regardless of what
  `baseline_publish.py` does. This Action needs an `artifact`-optional
  library-entry shape and a no-binary `CMD` branch dispatching into the new
  binary-less `dump` mode below — the same class of gap G41 Phase 1
  documents for `consumer_compile_*` forwarding through this identical
  Action, but a distinct fix (accepting no artifact at all, vs. forwarding
  extra compiler flags alongside one that's present) — implement both
  needs in the same PR that touches this Action rather than two
  uncoordinated passes over it.
- **`abicheck/storage/`** (new module, not `abicheck/buildsource/
  build_output_emit.py` as an earlier draft of this plan proposed) — the
  typed builder's actual `build-output.json` schema/write path. Per
  ADR-061's routing table, `storage/` is the canonical owner of
  serialization/schema/write logic for this class of artifact —
  `build-output.json` is exactly a schema this tool writes and validates,
  the same category as the baseline manifest G41 Phase 1 routes there.
  Keep any digest/tool-identity computation here too, alongside the
  writer, rather than split across two modules.
- **`abicheck/frontends/`** — the `project emit-build` CLI command adapter
  itself (parsing `--target`/`--binary`/`--public-header-root`/etc. and
  calling the `storage/` builder), following ADR-061's `frontends/` role
  ("CLI flag, Python adapter") — `abicheck/cli_project.py` is a
  `frozen_root_families["cli_"]` legacy module per `architecture/
  modules.yaml`, so it should gain only the thin `@project_group.command`
  registration shim, not the builder logic itself.
- `docs/reference/cli-reference.md` (generated) — new subcommand entry.
- **`actions/check-target/action.yml`** — declares `new-library` as
  `required: true`; must be relaxed to optional so a header-only target's
  check invocation can omit it.
- **`actions/check-target/run.sh`** — forwards its own inputs into the
  nested root Action; needs to keep working with `new-library` absent
  (it does not itself construct the `dump`/`compare` CLI command — that
  happens one layer further in, corrected below).
- **Repository-root `action/run.sh`** (not `actions/check-target/run.sh` —
  an earlier draft of this plan attributed the enforcement to the wrong
  `run.sh`; corrected after further review) — this is the file whose
  compare-mode dispatch does `CMD+=("${INPUT_NEW_LIBRARY:?new-library is
  required}")`, a hard failure with no binary operand. Confirmed by reading
  it directly. This is where the actual "no binary, headers/source-only"
  branch must be added, dispatching into the new binary-less `dump`/
  `compare` capability described next — **there is no existing binary-less
  CLI path to mirror here**: the next bullet's own fresh evidence
  (`dump_source_only()` discards `-H`; `compare.py` has no header-only
  operand shape at all) rules that out. This root-Action dispatch change
  and the new CLI capability are one dependent pair, not two independent
  fixes — without both, the `build-output.json` exemption above is
  necessary but not sufficient: the target still can't run a candidate
  check end to end.
- **`abicheck/frontends/cli/commands/dump.py`/`compare.py`** — this is a
  real implementation gap, not a confirmation task (an earlier draft of
  this plan wrongly assumed the underlying CLI already supported this).
  Confirmed by reading both directly:
  - `dump.py`'s `so_path` argument is already `required=False`, so a
    binary-less `dump --sources tree -H api.h` *runs* — but its
    `dump_source_only()` path embeds only L3/L4/L5 build/source facts and
    never runs an L2 header-AST pass at all, so **the given headers are
    silently ignored in the written snapshot** (the function's own
    docstring/comment: "`-H`/`--header` has no effect on the WRITTEN
    snapshot here... exits 0 with an empty (0 functions/enums) snapshot and
    no visible sign `-H` was ignored," beyond a stderr warning). A
    header-only library's *entire* contract is exactly the L2 facts this
    path discards — a real header-AST-only dump mode (no binary, no
    `--sources`/`--build-info` requirement, just `-H`/`--header` parsed
    through the existing L2 backend) needs to be added, not merely wired
    to an existing capability.
  - `compare.py`'s two positional operands (`old_input`/`new_input`) are
    both plain, non-optional `click.argument`s with no header-only/
    source-only variant evident — closing this needs either making both
    positionals optional when paired with header/source flags (mirroring
    `dump`'s own `so_path: required=False` precedent) or an equivalent
    dedicated code path, verified against this module directly before
    committing to either shape.
  - This is the load-bearing half of "a real check can run," alongside the
    Action-layer relaxation above — neither alone is sufficient.

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

L combined (revised up twice from the original S–M estimate: first for the
Action-layer gap, then again once reading `dump.py`/`compare.py` directly
showed the binary-less *CLI* path doesn't exist yet either — see below):

- Header-only targets: L — `project_targets.py`/`build_output.py`
  relaxation is S–M on its own, but reaching a real, runnable check needs
  three layers, not one: (1) `actions/check-target/action.yml`'s
  `new-library: required: true` relaxed, plus the repository-root
  `action/run.sh` (not `actions/check-target/run.sh`, which only forwards
  inputs — confirmed by reading both) given a no-binary compare-mode
  branch, since it's the root `run.sh` whose
  `CMD+=("${INPUT_NEW_LIBRARY:?new-library is required}")` fails hard with
  no operand today; (2) a genuine new `dump`/`compare` CLI capability —
  confirmed by reading `dump.py`/`compare.py` directly, not assumed: today's
  binary-less `dump` path (`dump_source_only()`) discards `-H`/`--header`
  entirely rather than running an L2 header-AST pass, and `compare.py`'s
  two positional operands have no header-only variant at all. This is the
  largest, least-scoped piece of this plan and should be estimated
  independently once a design for it exists — a real L2-only dump/compare
  mode is not a small addition; (3) `actions/baseline/action.yml`/`run.sh`
  (confirmed by reading `run.sh` directly — it hard-rejects any library
  entry without `artifact`), needed so a header-only target's baseline can
  be published at all, not only checked. Without all three, the
  `build-output.json`/`project_targets.py` relaxation alone leaves the
  target validated but still unable to run a check or publish a baseline
  — verify this end-to-end via the acceptance test, not just via
  `project validate-build` passing.
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

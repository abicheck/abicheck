# Scenario S17: Multiple Build and Compiler Profiles

The same library gets checked on more than one
[build profile](../concepts.md#build-profile) — Linux/GCC, Linux/Clang, and
Windows/MSVC release, say — and a break on one platform/compiler is still a
break, even if the others look fine. [ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S17: which lanes are actual ABI contracts (gate CI, get a baseline) vs.
test-only CI lanes is an explicit `.abicheck.yml` allowlist, never "every CI
lane that happens to build this library."

`profiles:` has grown well beyond `contract`/`os`/`arch` since this scenario
was first written — it now also declares which compiler/dialect built each
lane, where that lane runs, and how it provisions its own toolchain. This
page covers the full picture; [Project Targets
Schema](../../reference/project-targets-schema.md) stays the exhaustive
field-by-field reference — link there rather than re-deriving its tables.

## A full multi-compiler, multi-OS example

```yaml
profiles:
  linux-gcc14:
    contract: true
    os: linux                          # -> runs-on: ubuntu-latest
    dependency_source: conda-forge-gcc14
    compile:
      compiler_family: gcc
      compiler_version: ">=14,<15"
      binding: gcc14
      standard: gnu++20
      stdlib: libstdc++
      frontend: castxml
  linux-clang20:
    contract: true
    os: linux
    dependency_source: conda-forge-clang20
    compile:
      compiler_family: clang
      binding: clang20
      standard: c++20
      frontend: clang
  windows-msvc:
    contract: true
    os: windows                        # -> runs-on: windows-latest
    # No compile.binding here, unlike the two Linux profiles above — this
    # is intentional, not an omission. MSVC's cl.exe isn't resolved through
    # the binding/bindings-file mechanism at all (project validate skips
    # identity probing for compiler_family: msvc, cl.exe having no
    # --version flag to probe); this cell relies on the runner's
    # system-default toolchain instead.

targets:
  libfoo:
    # A single glob, resolved by the calling workflow against each
    # profile's OWN build-output directory — not per-OS. Linux's
    # `libfoo.so`/`libfoo.so.1` and Windows' `foo.dll` don't share a
    # naming convention a single glob can match, so a target checked on
    # both platforms needs its build to stage a consistently-named/staged
    # artifact per profile (e.g. always under `lib/libfoo.*`), not a
    # pattern this schema resolves for you. This example's `lib/libfoo.*`
    # only actually works if the Windows build is arranged to produce
    # `lib/libfoo.dll` in the same relative location.
    binary_pattern: "lib/libfoo.*"
    checks:
      - channel: accepted-main
        depth: headers
        # no explicit `profiles:` selector — the implicit sweep runs on
        # every `contract: true` profile that actually built this target

baseline:
  channels:
    accepted-main: {source: actions-cache, key_prefix: "abicheck-baseline-main"}
```

Every `checks[].channel` must resolve to a declared `baseline.channels` id
(or the `"none"` no-baseline sentinel) — `project validate` rejects an
undeclared channel like a bare `accepted-main` above with no matching
`baseline:` block.

```yaml
# bindings.yml — operator/CI-managed, never auto-discovered
schema: abicheck.toolchain-bindings/v1
bindings:
  gcc14: /opt/gcc-14.2.0/bin/g++
  clang20: /opt/llvm-20/bin/clang++
```

## The sequence: validate → plan → per-cell checks → aggregate

```bash
abicheck project validate --toolchain-bindings bindings.yml
abicheck project plan --toolchain-bindings bindings.yml \
  --build-output linux-gcc14=build-output/linux-gcc14 \
  --build-output linux-clang20=build-output/linux-clang20 \
  --build-output windows-msvc=build-output/windows-msvc \
  -o run-plan.json
```

- `project validate` checks the declared topology — target/bundle/profile
  cross-references, `checks[].channel`/`depth`/`gate_mode`/`profiles`
  resolution, and (with `--toolchain-bindings`) that every declared
  `profiles.<id>.compile.binding` actually resolves against the bindings
  file. It never reads a build output.
- `project plan` **requires** the config to already pass validation, then
  creates one check cell for each applicable `checks[]` entry × profile
  assignment — only where the target actually appears in that profile's
  own `build-output.json`, never a blind cross-product. A single
  `(target, profile)` pair can produce **more than one** cell when the
  target declares several `checks[]` entries (distinct `channel`/`depth`
  pairs); an implicit sweep (no `checks[].profiles` selector) silently
  skips a profile the target doesn't apply to, while an *explicit*
  `checks[].profiles` selector naming a profile the target doesn't build
  on is a validation error, not a skip. Each cell resolves its own
  compiler binding/options and toolchain-identity probe along the way
  (skipped for a profile with no cells in the resolved plan, so an unused
  profile can't abort an otherwise-fine plan).
- [`check-project.yml`](../../reference/reusable-workflows.md) consumes the
  run plan and fans out one matrix job per cell — each job runs on the
  profile's own `runs_on` (native `windows-latest` for the MSVC lane above),
  provisions dependencies via that profile's own `dependency_source`, and
  forwards the profile's own `compile_ast_frontend`/`compile_gcc_path`/
  `compile_gcc_options` as that cell's `ast-frontend`/`gcc-path`/
  `gcc-options` Action inputs.
- Each cell uploads its own report; [`aggregate`](../../use/aggregate-reports.md)
  folds them back into one gate — and, since report ids are
  `target@profile#channel@depth`-shaped, reconciles the same finding across
  profiles into `finding_matrix`/`profile_matrix` (see [Aggregate
  Reports](../../use/aggregate-reports.md#reconciling-findings-across-compilerbuild-profiles)
  for what "the same finding on two compilers" actually means).

## Two different questions this schema answers

1. **One library, built under different producer compilers.** Each
   `profiles.<id>.compile:` block is that profile's own *producer* toolchain
   — the one that actually compiled the library binary (mangling, layout,
   vtables, calling convention, linked standard-library ABI). The example
   above is this case: `linux-gcc14` and `linux-clang20` are two producer
   builds of the same library.
2. **One producer artifact, consumed by clients built with a different
   compiler.** `consumer_compile:` (a separate, optional sub-block, same
   shape as `compile:`) declares a *client* toolchain — what a user of the
   library compiles their own code with against the public headers, when it
   differs from the producer (which `#ifdef` branch a client sees, which
   standard-library ABI, which template instantiation). A profile with no
   `consumer_compile:` behaves exactly as before — its `compile:` doubles as
   the consumer's too:
   ```yaml
   profiles:
     linux-gcc14-build-clang20-client:
       contract: true
       compile:
         binding: gcc14
         standard: gnu++17
       consumer_compile:
         binding: clang20
         standard: gnu++20
         stdlib: libc++
   ```

These are genuinely different questions — don't reach for `consumer_compile`
to model "we also build this on Clang" (that's a second `profiles:` entry,
question 1); reach for it only when the *same* producer artifact needs to be
checked against a *different* client toolchain's view of the headers.

## Current support / Not yet supported

Be precise about what actually runs today before wiring a CI matrix around
it — several of these fields resolve into the run plan correctly but don't
(yet) drive a second extraction pass:

| Capability | Status |
|---|---|
| `compile.frontend` on a normal (non-bundle) target cell | **Applied end to end.** `check-project.yml` forwards the resolved `compile_ast_frontend` as that cell's own `--ast-frontend`, taking precedence over the workflow-level input. |
| `compile.frontend` on a `kind: bundle` check | **Projected, not applied.** A bundle's operand is a staging *directory*, and the root Action rejects any non-`auto` frontend for a directory/package operand — such a cell keeps the workflow-global value. |
| `consumer_compile:` fields reaching the run plan (`consumer_compile_gcc_path`/`consumer_compile_gcc_options`) | **Projected.** Resolved into their own pair of run-plan fields, independent of the producer overlay. |
| `consumer_compile:` driving a separate candidate extraction pass | **Applied, for the candidate side only.** `check-project.yml` runs a distinct `dump` invocation of the (unchanged) candidate binary, parsing its public headers under the consumer overlay's frontend/binding/options, and the comparison consumes that snapshot instead of reparsing candidate headers under the producer context. This is a *separate dump*, not the L0/L1-producer-plus-L2-consumer merge originally scoped in `docs/contribute/plans/g34-producer-consumer-compiler-profile-separation.md`'s Phase 0 — see that plan for the design this superseded. |
| `consumer_compile:` and a real (non-`none`) `baseline-channel` together | **Not yet supported.** The baseline (old) side is dumped by `publish-baseline.yml`/`update-main-baseline.yml`, which never apply a `consumer_compile:` overlay — comparing a consumer-context candidate against a producer-context baseline usually resolves `NOT_COMPARABLE`/`ProfileMismatchError` rather than a wrong verdict. `baseline-channel: none` (audit-only) is unaffected. |
| `consumer_compile.frontend` forwarding | **Forwarded** to the separate candidate dump above, falling back to the workflow-global `--ast-frontend` (never the producer overlay's own value, and never the empty string) when the overlay omits it — same for `consumer_compile`'s compiler binding and options against `gcc-path`/`gcc-options`. |
| `os: windows` scheduling a native `windows-latest` cell | **Works.** An unset `os:` still resolves to `ubuntu-latest` (every profile written before this landed keeps its old scheduling); an unroutable value (e.g. `os: freebsd`) is a validation error, not a silent Linux fallback. |
| `dependency_source:` unset on a Windows profile | **Falls back to `system`**, not `conda-forge` — every `conda-forge*` source is Linux/macOS-only, and defaulting Windows to one would hard-fail before analysis even starts. An *explicit* `conda-forge*` on Windows is not silently rewritten; it still errors. |
| `compile.compiler_family`/`compiler_version`/`target` | **Not forwarded as extraction flags** — no invocation is composed from these fields directly. **Separately, with `--toolchain-bindings`, both `project validate` and `project plan` do run a real identity check**: they probe the resolved binding's actual executable and reject a mismatch against the declared `compiler_family`/`compiler_version`/`target` (`validate` checks every declared profile; `plan` checks only the profiles used by the resolved run). Without `--toolchain-bindings`, no probe runs and these fields are validated for shape only. |
| Native MSVC+PDB end-to-end verdicts | **Experimental.** A real fixture and CI lane exist (compiles with real `cl.exe`, runs a full dump+compare, marker `msvc`), but that CI job is non-blocking (`continue-on-error`) — abicheck's pure-Python PDB parser is still maturing against real MSVC output. Don't treat a green MSVC lane as an equally strict gate to the GCC/Clang lanes yet. |

## When to move past this scenario

- **You need an *explicit* profile selector** (a check that should only run
  on some, not every, contract profile) → `checks[].profiles:` — see the
  [Project Targets Schema](../../reference/project-targets-schema.md) —
  which is also a **hard error**, not a silent skip, if the named profile
  turns out not to build the target (a real misconfiguration, unlike the
  implicit sweep's legitimate "doesn't apply here").
- **One target needs two baselines gated independently, on the same
  profile** → [S21](../index.md), two `check-target` calls differing only in
  `baseline-channel`.
- **You want to know whether a break is universal or profile-specific once
  the matrix reports back** → [Aggregate Reports](../../use/aggregate-reports.md),
  specifically the `finding_matrix`'s `scope` field.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Project Targets Schema](../../reference/project-targets-schema.md) — the full `profiles:`/`checks[].profiles:` contract, including `compile:`/`consumer_compile:`/`os:`/`dependency_source:` field-by-field.
- [Run Plan Schema](../../reference/run-plan-schema.md) — the generated `RunPlanCheck` shape each matrix cell resolves to.
- [Aggregate Reports](../../use/aggregate-reports.md) — folding the matrix's reports back into one gate, and reconciling findings across profiles.
- [Build Output Schema](../../reference/build-output-schema.md#schema-abicheckbuild-outputv1) — why one artifact is always exactly one profile.

# `.abicheck.yml` Project Targets Reference

`.abicheck.yml`'s `targets:`/`bundles:`/`profiles:`/`baseline:`/`aggregate:`
block is the
portable, project-owned surface that declares a project's CI-integration
topology: which libraries/consumers/plugin-contracts exist, how they group
into release bundles, which build profiles are ABI contracts, which baseline
channels exist, and exactly which `{channel, depth, required, gate_mode}`
checks run against each target (G30/ADR-047 §3).

> **Status.** This page documents the schema and the
> `abicheck project validate` command shipped in G30 P1.5. The
> run-plan generator that reads a validated block to fan out CI checks
> (`abicheck project plan`, `check-single.yml`/`check-project.yml`) is
> G30 P1.4 — see the [run-plan schema](run-plan-schema.md) and the
> [reusable workflows reference](reusable-workflows.md). A project not using
> G30's CI-integration primitives sees no behavior change at all from adding
> (or omitting) this block: nothing in `dump`/`compare`/`scan` reads it
> today.

## Example

```yaml
# .abicheck.yml (excerpt)
targets:
  libpvxs:
    kind: library          # default
    binary_pattern: "lib/libpvxs.so*"
    public_headers: ["headers/pvxs"]
    bundle: pvxs-release
    bundle_only: false     # run libpvxs both standalone AND as a bundle member
    checks:
      - channel: accepted-main
        depth: headers
        required: true
        gate_mode: local
  libpvxsIoc:
    kind: library
    binary_pattern: "lib/libpvxsIoc.so*"
    public_headers: ["headers/pvxsIoc"]
    bundle: pvxs-release
  myapp-consumer:
    kind: app-consumer     # compare --used-by
    consumer_binary_pattern: "bin/myapp"
    library: libpvxs
  ioc-plugin-contract:
    kind: plugin-contract  # compare --required-symbols
    contract_file: "contracts/ioc-plugin.syms"
    library: libpvxsIoc

bundles:
  pvxs-release:
    targets: [libpvxs, libpvxsIoc]

profiles:
  linux-x86_64-gcc13-release:
    contract: true          # this lane IS an ABI contract — gets a baseline, gates CI
    os: linux
    arch: x86_64
  ubuntu-latest-clang-debug-sanitizer:
    contract: false         # test-only CI lane — never gets a baseline

baseline:
  channels:
    release-contract: {source: github-release, asset_pattern: "abicheck-baseline-*.tar.zst"}
    accepted-main: {source: actions-cache, key_prefix: "abicheck-baseline-main"}
```

`abicheck project validate` — like the rest of `.abicheck.yml` —
loads this via [PyYAML's `safe_load`](https://pyyaml.org/wiki/PyYAMLDocumentation#loading-yaml),
so no custom YAML tags are ever evaluated.

## `targets:`

A mapping of target id → target entry. Every id must match
`^[A-Za-z0-9][A-Za-z0-9._-]*$` — the same charset the report-identity
envelope (ADR-047 §7) requires for `check_id`'s
`target@profile#baseline_channel@depth` components, so a valid id here can
never produce an ambiguous identifier downstream.

`kind` (default `library`) is a discriminator; the remaining fields it
accepts/requires depend on it:

| `kind` | Required fields | Forbidden fields | Meaning |
|--------|------------------|-------------------|---------|
| `library` (default) | `binary_pattern` | `consumer_binary_pattern`, `contract_file` | An ordinary shared-library ABI contract (S1–S17, S26). |
| `app-consumer` | `consumer_binary_pattern`, `library` | `binary_pattern`, `contract_file` | An application compatibility check (S22, `compare --used-by`). |
| `plugin-contract` | `contract_file`, `library` | `binary_pattern`, `consumer_binary_pattern` | A plugin/dlopen entrypoint contract (S23, `compare --required-symbols`). |

Common optional fields for `kind: library`:

| Field | Type | Meaning |
|-------|------|---------|
| `public_headers` | list of string | Public header roots for this target, forwarded as `check-target`'s own `header` input for this target's generated cells (space-joined) — see [`run-plan-schema.md`](run-plan-schema.md#runplancheck-fields)'s `header` field and [`reusable-workflows.md`](reusable-workflows.md#shared-analysis-options)'s per-cell-override exception. Not yet honored for a `kind: bundle` member (no per-bundle-member header staging exists). |
| `bundle` | string | The `bundles:` entry this target belongs to. Must be declared under `bundles:`, and that bundle's own `targets:` list must include this target back (the two must agree). |
| `bundle_only` | boolean, default `false` | When `true`, this target is checked only as a bundle member, never standalone. Requires `bundle` to be set, and must **not** declare its own `checks:` — a `bundle_only` target's own checks would never run standalone, so declare the policy under `bundles:<id>.checks` instead. |
| `checks` | list of check tuple | See [`checks:`](#checks) below. |

`app-consumer`/`plugin-contract` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `consumer_binary_pattern` | string | (`app-consumer` only) Path pattern to the consumer binary under test. |
| `contract_file` | string | (`plugin-contract` only) A **`.syms` file** — one required linker symbol per line, `#` comments allowed. This is `--required-symbols`'s actual on-disk format (`abicheck/cli_compare_helpers.py`'s `_load_required_symbols`), not YAML. |
| `library` | string | The `kind: library` target this entry resolves its baseline **and** candidate-artifact lookup through (ADR-047 §3's "unstated rule" correction). Must name a real, declared `kind: library` target — never another `app-consumer`/`plugin-contract` entry. The check's own reporting identity (`check_id`/`target_id`) stays this entry's own name; only the *lookup* redirects to `library`. |

### `checks:`

Each `targets:<id>.checks[]` entry is a `{channel, depth, required,
gate_mode, profiles, allow_new_target}` tuple — the assignment ADR-047 §3
itself identifies as missing from the plain `targets:`/`baseline: channels:`
excerpt: declaring which channels *exist* doesn't say which channel/depth/
policy a given target actually runs.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `channel` | string | — (required) | A `baseline.channels` id, or the literal `"none"` for a no-baseline audit check (ADR-047 §6 S5 — `check-target` must skip `resolve-baseline` entirely for this sentinel, never look it up as a declared channel). `channel: "none"` is only supported for a `kind: library` target — rejected at validation time for `app-consumer`/`plugin-contract` (no `--used-by`/`--required-symbols` equivalent for a one-build audit) and for any [`bundles:` check](#bundles) (a bundle's candidate is always a staged directory of member binaries, which the root Action's `scan` mode rejects outright). |
| `depth` | string | — (required) | One of `binary`, `headers`, `build`, `source` — the same four rungs `--depth`/the report envelope's `requested_depth` accept. |
| `required` | boolean | `true` | Whether this check gates `aggregate`'s coverage requirement. |
| `gate_mode` | string | `local` (`advisory` when `channel: "none"`) | One of `local`, `deferred`, `advisory` (ADR-047 §4/§7). A `channel: "none"` no-baseline audit check defaults to `advisory`, not `local` — it has no baseline-drift verdict to gate CI on, so a minimal `{channel: none, depth: ...}` entry must not unexpectedly block CI (ADR-047 §8's S5 row: "Advisory by default"). Set `gate_mode` explicitly to override either default. |
| `profiles` | list of string | *(unset)* | An **explicit** profile-id selector — see [Profile scoping](#profile-scoping-for-checks) below. A profile with `contract: false` may only be named here by a `channel: "none"` audit check — a real-channel check can never resolve a baseline on a lane that's documented to never get one (S17). |
| `allow_new_target` | boolean | `false` | Forwarded as `check-target`'s `allow-new-target` input — `true` turns a target genuinely absent from this check's otherwise-resolved baseline-set into the advisory `new_target` outcome instead of `ambiguous` (e.g. a new library's first release). Pair with `required: false`, or a required-coverage gate would still block on the target's first appearance. Rejected at validation time for any [`bundles:` check](#bundles) — a bundle comparison needs one coherent release where every member already coexisted, so there is no well-defined old side for a member that's new. See [Baseline Management → A new library's first release](../use/baseline-management.md#a-new-librarys-first-release). |

### Profile scoping for `checks:`

ADR-047 §3 flags an open gap: naively crossing every `checks:` entry with
every `contract: true` profile produces impossible cells for a target that
doesn't exist on every profile (a Windows-only library, a Linux-only `.so`).
This schema resolves it with two complementary mechanisms:

- An **explicit `profiles:` selector** on a `checks:` entry restricts that
  check to the listed profile ids (each validated against `profiles:`).
  Use this when a check is genuinely profile-specific.
- When `profiles:` is **omitted**, this schema does not itself resolve a
  profile list — G30 P1.4's run-plan generator is responsible for deriving
  the actual `(target, profile)` cells from each profile's own
  `build-output.json` `targets[]` list (only generating a cell where the
  target actually appears in that profile's declared targets), never from a
  blind cross-product. `abicheck project validate` cannot check that
  downstream behavior — it only validates that an explicit selector, when
  given, names real profile ids.

## `bundles:`

A mapping of bundle id → `{targets: [...], checks: [...]}`. Every listed
target must be a declared `kind: library` target, and if that target itself
sets a `bundle:` field, it must name this same bundle back — the validator
flags a mismatch (e.g. a target claims `bundle: bundle-a` but only
`bundle-b` lists it as a member) as an integrity error, not a silent
inconsistency.

`checks:` on a bundle uses the exact same `{channel, depth, required,
gate_mode, profiles}` shape [described above](#checks) for a target — the
ADR-047 §5 run-plan emits a `kind: "bundle"` check entry alongside
per-target ones (S14 bundle-scoped analysis, e.g. soname/provider-set
checks across the whole release), and that cell needs its own
baseline-channel/depth/gate policy independent of its member targets'.
**Two restrictions that don't apply to a target check:** `depth` must be
`binary` (never `headers`/`build`/`source` — a bundle check always
compares directories, which the CLI's per-library release fan-out never
collects inline build/source evidence for; `headers` is additionally
unsafe because a bundle's baseline is always raw binaries with no
historical header snapshot, so both sides would be parsed against the
same current checkout's headers, silently missing a header-only change),
and `channel` may not be `"none"` (a bundle's candidate is always a staged
directory of member binaries, which the root Action's `scan` mode — the
no-baseline routing — rejects outright). Both are rejected at validation
time.

## `profiles:`

A mapping of profile id → `{contract, os, arch, dependency_source, compile,
consumer_compile}`. `contract`
(default `true`) decides whether this build lane is an ABI contract (gets a
baseline, gates CI) or a test-only CI lane that never gets one — "not every
CI lane gets a baseline" is the whole point of this field (S17). The map
key is the same `profile.id` string used throughout `build-output.json`,
`run-plan.json`, and the report envelope's `profile_id` field.

The optional `compile:` sub-block (P1 toolchain-profile audit) declares the
compiler/dialect/ABI-macro axes this profile pins — additive over the root
`compile:` block: `compiler_family`, `compiler_version` (a version
constraint string), `target` (a target triple), `standard`, `stdlib`,
`binding` (see below), `abi_macros` (a string→string mapping), and `args`
(a list of normalized extra compiler-flag atoms). Every string value must
be a single whitespace-free atom — a `.abicheck.yml` found by auto-discovery
is untrusted, and whitespace would let one YAML scalar smuggle multiple
argv tokens. `standard`/`stdlib`/`target`/`abi_macros`/`args` reach `abicheck
project plan` (P1 toolchain-profile audit, closing this gap) as each
resolved cell's composed `compile_gcc_options`; `binding` additionally
reaches `compile_gcc_path`, but only when `project plan
--toolchain-bindings <path>` resolves it (see below) — see
[`run-plan-schema.md`'s `RunPlanCheck` fields](run-plan-schema.md#runplancheck-fields)
for the exact composition rule and `reusable-workflows.md`'s "Shared
analysis options" for how `check-project.yml` forwards them per cell.
`compiler_family`/`compiler_version` are validated here but not yet
projected into any invocation — see that same section for why.

```yaml
profiles:
  linux-x86_64-gcc14-libstdcxx-gnu17-default:
    contract: true
    os: linux
    arch: x86_64
    compile:
      compiler_family: gcc
      compiler_version: ">=14.0,<15"
      standard: gnu++17
      stdlib: libstdc++
      binding: gcc14
```

### `consumer_compile:` — a separate client-toolchain overlay (G34 Phase 0)

The optional `consumer_compile:` sub-block accepts the identical shape as
`compile:` (same fields, same validation), but declares a **different**
axis: `compile:` is the *producer/artifact* toolchain the library binary
was actually built with (mangling, layout, vtables, calling convention,
linked standard-library ABI); `consumer_compile:` is a *client* toolchain a
user of the library compiles their own code with against the public
headers, when it differs from the producer (which `#ifdef __GNUC__`/
`__clang__`/`_MSC_VER` branch, which standard-library ABI, which template
instantiation the client actually sees). A profile with no
`consumer_compile:` behaves exactly as today — its `compile:` block doubles
as the consumer's, so existing single-toolchain projects need no edits:

```yaml
profiles:
  linux-gcc14-build-clang20-client:
    contract: true
    os: linux
    compile:
      binding: gcc14
      standard: gnu++17
    consumer_compile:
      binding: clang20
      standard: gnu++20
      stdlib: libc++
```

`consumer_compile:`'s fields reach `abicheck project plan` the same way
`compile:`'s do, into their own separate pair —
[`consumer_compile_gcc_path`/`consumer_compile_gcc_options`](run-plan-schema.md#runplancheck-fields)
— never falling back to the producer overlay's own resolved values when
absent. **Applied, for the candidate side only:** `check-project.yml` runs
a separate `dump` of the (unchanged) candidate binary under this overlay's
frontend/binding/options, and the comparison consumes that materialized
snapshot as the candidate's entire new side — see the
`compile.frontend`/`consumer_compile.frontend` section immediately below
for how that separate dump is wired, and its "Known gap" note for what
still isn't covered (the baseline/old side). This is a real, working
extraction pass, not the L0/L1-producer-plus-L2-consumer merge originally
scoped in
[`docs/contribute/plans/g34-producer-consumer-compiler-profile-separation.md`](../contribute/plans/g34-producer-consumer-compiler-profile-separation.md)'s
Phase 0 — see that plan for the design this superseded.

### `compile.frontend` / `consumer_compile.frontend` — per-profile AST frontend (G34 Phase B)

Either overlay may set `frontend:` to one of the same four values the
global `--ast-frontend` flag accepts (`auto`/`castxml`/`clang`/`hybrid`),
overriding the global default for that profile's cell only:

```yaml
profiles:
  linux-gcc14-build-clang20-client:
    contract: true
    compile:
      binding: gcc14
      frontend: castxml
    consumer_compile:
      binding: clang20
      frontend: clang
```

Reaches `abicheck project plan` as
[`compile_ast_frontend`/`consumer_compile_ast_frontend`](run-plan-schema.md#runplancheck-fields),
resolved independently for each overlay (a profile with no `frontend:` set
on an overlay leaves that field empty, deferring to a caller's own global
`--ast-frontend`/default — it never falls back to the *other* overlay's
`frontend:` value).

`compile.frontend` is applied end to end: `check-project.yml`'s check job
forwards the projected `compile_ast_frontend` as that cell's own
`--ast-frontend`, preferring it over the workflow-level input the same way
`gcc-path`/`gcc-options` already prefer their per-cell overlay. So the
example above genuinely runs its producer pass under castxml. The one
exception is a `kind: bundle` check, whose operand is a staging *directory*
— the root Action rejects any non-`auto` frontend there, so such a cell
keeps resolving the workflow-global value.
`consumer_compile.frontend` drives a separate candidate dump in the native
`check-project` pipeline. The dump reads the same producer binary but parses
its public headers with the consumer overlay's frontend, compiler binding,
and options; the comparison then consumes that materialized snapshot instead
of parsing the candidate headers again under the producer context.

**Known gap:** only the *candidate* side is dumped under the consumer
context today. The baseline (old) side of a real (non-`none`)
`baseline-channel` comparison is produced hours or days earlier by
`publish-baseline.yml`/`update-main-baseline.yml`, which read only
`build-output.json` and have no way to apply a `consumer_compile:` overlay
to that dump. Comparing a consumer-context candidate against a
producer-context baseline usually differs enough in extraction-profile
fingerprint that `compare`'s own comparability gate refuses the pair as
`NOT_COMPARABLE`/`ProfileMismatchError` rather than silently comparing
mismatched contexts — so `consumer_compile:` combined with a real baseline
channel does not yet work end to end. It is unaffected when
`baseline-channel: none` (an audit-only scan has no baseline snapshot to
mismatch against). See `abicheck/buildsource/run_plan.py`'s own docstring
and the G34 plan doc's Phase 0 for what closing this needs.

### `os:` and `dependency_source:` — how a profile schedules its own check cell (G34 Phase C)

These two decide *where* a profile's `check-project.yml` check cell runs and
*how* it provisions its own system dependencies. Before this phase both were
fixed for the whole run: every cell ran on a hardcoded `ubuntu-latest`, and
dependency installation came from one workflow-level `install-deps` boolean —
so an `os: windows` profile could not be checked natively, and a GCC-profile
cell and a Clang-profile cell in the same run could not each get a matching
toolchain.

```yaml
profiles:
  linux-gcc14:
    contract: true
    os: linux                            # → runs-on: ubuntu-latest
    dependency_source: conda-forge-gcc14
  windows-msvc:
    contract: true
    os: windows                          # → runs-on: windows-latest
  linux-clang20:
    contract: true
    os: linux
    dependency_source: conda-forge-clang20
```

`os:` accepts `linux`, `windows`, `macos` (or `darwin`), case-insensitively,
and additionally passes a GitHub-hosted runner label through verbatim
(`ubuntu-24.04`, `windows-2022`, `macos-14`) so a project that already wrote
an image there keeps working. It reaches `abicheck project plan` as each
cell's [`runs_on`](run-plan-schema.md#runplancheck-fields). **A profile with
no `os:` resolves to `ubuntu-latest`** — that is every profile written before
this phase, so their scheduling is unchanged. A value naming no schedulable
platform (`os: freebsd`) is a *validation error* rather than a silent
fallback to Linux: a cell scheduled on the wrong platform reports success
having gated the wrong thing.

`dependency_source:` accepts the same five values as the Action's own
[`dependency-source` input](github-action-inputs.md) — `conda-forge`,
`conda-forge-gcc14`, `conda-forge-clang20`, `system`, `none` — and reaches
the cell as [`dependency_source`](run-plan-schema.md#runplancheck-fields),
forwarded to `check-target`. It is optional: an undeclared value leaves the
caller's workflow-level `dependency-source` input standing, and with both
unset the legacy `install-deps` boolean still decides, exactly as before.

### `compile.binding` — resolving a logical toolchain id

`binding` is a *logical* identifier (e.g. `"gcc14"`), never a raw
executable path or command — the same untrusted-config trust boundary as
every other `compile:` field. Resolving it to an exact executable requires
a **separately trusted** bindings file (schema
`abicheck.toolchain-bindings/v1`):

```yaml
# bindings.yml — operator/CI-managed, never auto-discovered
schema: abicheck.toolchain-bindings/v1
bindings:
  gcc14: /opt/gcc-14.2.0/bin/g++
  castxml07: /opt/conda/bin/castxml
```

`abicheck project validate --toolchain-bindings bindings.yml` checks
that every declared `profiles.<id>.compile.binding` resolves against it,
in addition to the ordinary validation checks below — a config author can
catch a typo'd or undeclared binding id before CI runs. Omitting
`--toolchain-bindings` skips this check entirely (a profile declaring a
`binding` with no bindings file given is not itself a validation error);
loading a bindings file with the wrong `schema` or a malformed document is
a usage error (exit `64`), matching the rest of this command's strict-parsing
convention.

## `baseline:`

Currently one recognized sub-key, `channels:` — a mapping of channel id →
`{source, asset_pattern, key_prefix}`:

| `source` | Requires | Backend (ADR-047 §10) |
|----------|----------|------------------------|
| `github-release` | `asset_pattern` | A GitHub Release asset — atomic single-tarball upload. |
| `actions-cache` | `key_prefix` | GitHub Actions cache — cheap, no push, naturally ages out. |
| `git` | *(neither)* | Committed to the repo — S1's minimal case only, must go through a PR. |

An external object store (a fourth backend ADR-047 §10 lists) is out of
scope for P0/P1 and not a valid `source` value here.

## `aggregate:`

The durable, project-owned home for `abicheck aggregate`'s
missing-required/unexpected-target gate policy (CLI cleanup phase two, PR 2
follow-up), stated once instead of re-typed on every `project plan`
invocation. `abicheck project plan` stamps this block's values onto the
generated `run-plan.json`'s own top-level `gate` block (see the
[run-plan schema](run-plan-schema.md#top-level-gate-block-and-the-schema-discriminator))
— the same `gate` shape a hand-authored `aggregate --manifest` carries
directly (see [Aggregate Reports](../use/aggregate-reports.md)).

One recognized sub-key, `gate:`, with two independently optional fields:

```yaml
aggregate:
  gate:
    missing_required: fail       # fail | warn (default: fail)
    unexpected_target: include   # include | warn | fail | ignore (default: include)
```

Omitting `aggregate:` entirely, or either `gate:` sub-key, leaves that field
unset on the generated plan — `aggregate`'s own hard-coded defaults apply,
unchanged from before this block existed. There is no per-invocation CLI
override: `project plan`'s former `--gate-missing-required`/
`--gate-unexpected-target` flags were removed (no CLI alias, same "no
deprecation window" stance as the rest of this cleanup) — set the policy
here instead.

## Validation

`abicheck project validate [CONFIG]` (`CONFIG` defaults to
`.abicheck.yml` in the current directory) checks, per ADR-047 §3:

1. Every target's `kind`-specific required fields are set, and no
   kind-inappropriate field is (see the table above).
2. `app-consumer`/`plugin-contract` targets' `library` resolves to a real,
   declared `kind: library` target — never to another
   `app-consumer`/`plugin-contract` entry, and never to an undeclared name.
3. `bundle_only: true` requires `bundle` to be set, and forbids the target
   from declaring its own `checks:` (it's checked only as a bundle member;
   a standalone check on it would never run).
4. Every `bundle:` reference resolves to a declared `bundles:` entry, and
   every `bundles:<id>.targets[]` member resolves to a declared `kind:
   library` target whose own `bundle:` field (if set) agrees.
5. Every `checks[].channel` resolves to a declared `baseline.channels` id,
   or is the `"none"` no-baseline sentinel.
6. `checks[].depth` is one of the four valid rungs; `checks[].gate_mode` is
   one of `local`/`deferred`/`advisory`.
7. Every `checks[].profiles` entry resolves to a declared `profiles:` id,
   and a `contract: false` profile may only be named by a `channel: "none"`
   audit check.
8. Every target/bundle/profile/channel id matches the `check_id`-safe
   identifier charset.
9. Rules 5-7 apply identically to a bundle's own `checks[]`, not just a
   target's.
10. `checks[].allow_new_target: true` is rejected on any of a bundle's own
    `checks[]` — a bundle comparison needs one coherent release where every
    member already coexisted, so there is no well-defined old side for a
    member that's new.

Structural/type errors in the YAML itself (an unknown key at any level —
including a misspelled top-level block like `tagrets:`, checked against the
*full* `.abicheck.yml` key set, not just this block's five keys — or a
value of the wrong type, e.g. `contract: "yes"` instead of a boolean) fail
immediately, as a usage error, matching `.abicheck.yml`'s existing
strict-parsing convention (ADR-043) — the validation report above only
covers cross-reference/semantic issues on an already-well-formed block.

### CLI

```console
$ abicheck project validate .abicheck.yml
project-targets validation: .abicheck.yml
OK — no errors.

$ abicheck project validate .abicheck.yml --format json
{
  "ok": true,
  "errors": [],
  "warnings": []
}
```

Exit codes: `0` valid (warnings may still be present), `1` one or more
validation errors, `64` usage error (`CONFIG` is not readable YAML, or its
`targets:`/`bundles:`/`profiles:`/`baseline:`/`aggregate:` block fails
strict parsing).

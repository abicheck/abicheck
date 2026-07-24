# `.abicheck.yml` Project Targets Reference

`.abicheck.yml`'s `targets:`/`bundles:`/`profiles:`/`baseline:` block is the
portable, project-owned surface that declares a project's CI-integration
topology: which libraries/consumers/plugin-contracts exist, how they group
into release bundles, which build profiles are ABI contracts, which baseline
channels exist, and exactly which `{channel, depth, required, gate_mode}`
checks run against each target (G30/ADR-047 §3).

> **Status.** This page documents the schema and the
> `abicheck project-targets validate` command shipped in G30 P1.5. The
> run-plan generator that reads a validated block to fan out CI checks
> (`abicheck run-plan generate`, `check-single.yml`/`check-project.yml`) is
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

`abicheck project-targets validate` — like the rest of `.abicheck.yml` —
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
| `public_headers` | list of string | Public header roots for this target. |
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
gate_mode, profiles}` tuple — the assignment ADR-047 §3 itself identifies as
missing from the plain `targets:`/`baseline: channels:` excerpt: declaring
which channels *exist* doesn't say which channel/depth/policy a given
target actually runs.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `channel` | string | — (required) | A `baseline.channels` id, or the literal `"none"` for a no-baseline audit check (ADR-047 §6 S5 — `check-target` must skip `resolve-baseline` entirely for this sentinel, never look it up as a declared channel). `channel: "none"` is only supported for a `kind: library` target — rejected at validation time for `app-consumer`/`plugin-contract` (no `--used-by`/`--required-symbols` equivalent for a one-build audit) and for any [`bundles:` check](#bundles) (a bundle's candidate is always a staged directory of member binaries, which the root Action's `scan` mode rejects outright). |
| `depth` | string | — (required) | One of `binary`, `headers`, `build`, `source` — the same four rungs `--depth`/the report envelope's `requested_depth` accept. |
| `required` | boolean | `true` | Whether this check gates `aggregate`'s coverage requirement. |
| `gate_mode` | string | `local` (`advisory` when `channel: "none"`) | One of `local`, `deferred`, `advisory` (ADR-047 §4/§7). A `channel: "none"` no-baseline audit check defaults to `advisory`, not `local` — it has no baseline-drift verdict to gate CI on, so a minimal `{channel: none, depth: ...}` entry must not unexpectedly block CI (ADR-047 §8's S5 row: "Advisory by default"). Set `gate_mode` explicitly to override either default. |
| `profiles` | list of string | *(unset)* | An **explicit** profile-id selector — see [Profile scoping](#profile-scoping-for-checks) below. A profile with `contract: false` may only be named here by a `channel: "none"` audit check — a real-channel check can never resolve a baseline on a lane that's documented to never get one (S17). |

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
  blind cross-product. `abicheck project-targets validate` cannot check that
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
`binary` or `headers` (never `build`/`source` — a bundle check always
compares directories, which the CLI's per-library release fan-out never
collects inline build/source evidence for), and `channel` may not be
`"none"` (a bundle's candidate is always a staged directory of member
binaries, which the root Action's `scan` mode — the no-baseline routing —
rejects outright). Both are rejected at validation time.

## `profiles:`

A mapping of profile id → `{contract, os, arch}`. `contract` (default
`true`) decides whether this build lane is an ABI contract (gets a
baseline, gates CI) or a test-only CI lane that never gets one — "not every
CI lane gets a baseline" is the whole point of this field (S17). The map
key is the same `profile.id` string used throughout `build-output.json`,
`run-plan.json`, and the report envelope's `profile_id` field.

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

## Validation

`abicheck project-targets validate [CONFIG]` (`CONFIG` defaults to
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

Structural/type errors in the YAML itself (an unknown key at any level —
including a misspelled top-level block like `tagrets:`, checked against the
*full* `.abicheck.yml` key set, not just this block's four keys — or a
value of the wrong type, e.g. `contract: "yes"` instead of a boolean) fail
immediately, as a usage error, matching `.abicheck.yml`'s existing
strict-parsing convention (ADR-043) — the validation report above only
covers cross-reference/semantic issues on an already-well-formed block.

### CLI

```console
$ abicheck project-targets validate .abicheck.yml
project-targets validation: .abicheck.yml
OK — no errors.

$ abicheck project-targets validate .abicheck.yml --format json
{
  "ok": true,
  "errors": [],
  "warnings": []
}
```

Exit codes: `0` valid (warnings may still be present), `1` one or more
validation errors, `64` usage error (`CONFIG` is not readable YAML, or its
`targets:`/`bundles:`/`profiles:`/`baseline:` block fails strict parsing).

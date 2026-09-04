---
doc_type: reference
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - config-keys
depends_on:
  - abicheck/buildsource/inline.py
  - abicheck/config_paths.py
lifecycle: active
generated: false
---

# The `.abicheck.yml` config file

`.abicheck.yml` is the per-project configuration file (ADR-037 D4). It holds
the *stable, reviewed-in-a-PR* properties of a project's ABI contract — build
system, header compile context, severity policy, public-surface scoping, and
suppression hygiene — as opposed to per-run invocation flags. See the
[Config Keys Reference](config-keys-reference.md) for the exhaustive,
generated list of every key/sub-key `BuildConfig` itself validates, and its
exact required type (other recognized top-level keys, parsed by a sibling
module, are also listed there but without a type); this page covers
effective defaults, precedence, and a worked example.

Every field is optional; an absent, empty, or non-mapping file yields the
all-defaults configuration. **CLI flags always override the config**, which in
turn overrides the built-in defaults (`CLI > config > default`).

- Loader (build/source blocks): `load_build_config()` in
  `abicheck/buildsource/inline.py`; parsed into the `BuildConfig` dataclass.
- Precedence resolver (`compare` project-contract blocks):
  `resolve_compare_config()` in `abicheck/cli_helpers_compare.py`.

---

## File discovery

Within any one directory, three locations are recognized, checked in this
order (first match wins):

1. `.abicheck.yml` — the original, project-root spelling.
2. `.github/.abicheck.yml` — alongside workflows/`CODEOWNERS`, for a project
   that keeps tool configuration out of its own root.
3. `.github/abicheck/.abicheck.yml` — a dedicated subdirectory, for a project
   that wants its abicheck config kept apart from other `.github` content (or
   that already groups per-tool config under `.github/<tool>/`).

Only the file's *location* changes between these three — its content, schema,
and strictness rules are identical regardless of which one is used. A file
present at a higher-precedence location always wins over one at a
lower-precedence location in the *same* directory; see
`abicheck/config_paths.py` for the exact, shared candidate list every
discovery entry point below draws from.

| Command | Discovery | Code |
|---------|-----------|------|
| `compare` | Walks up from the current directory to the filesystem root, checking all three locations in each directory, and uses the first one found. | `discover_project_config()` in `cli_helpers_compare.py` |
| `dump --sources` / `--build-info` | Checks all three locations at the **source-tree root** only — no parent walk. | `discover_build_config()` in `buildsource/inline.py` |
| any | An explicit `--config <path>` overrides discovery. | `cli_options.py` (`--config`) |

> **Note:** an auto-discovered (untrusted) `.abicheck.yml` never causes a build
> command in `build.query` to run — it is skipped with a diagnostic. A
> `build.query` runs **only** when the config is supplied **explicitly** with
> `--config` (which marks it trusted for subprocess execution).
> `--allow-build-query` is a deprecated no-op and is **not** required.

### Strict loading (ADR-043)

Config loading is **strict**: an unknown top-level key, an unknown sub-key
inside a recognized block, a value of the wrong type, or a bad enum value are
all **hard errors** — not warnings. This is a behavior change from earlier
abicheck versions, which warned on unknown keys and kept going. A malformed
YAML file is also a hard error. On the CLI, any of these surfaces as a usage
error (exit `64`; see [Exit Codes](exit-codes.md)) — the run never proceeds
on a config abicheck could not fully validate.

This "hard error" rule applies unconditionally to `compare`'s own project
config — severity, scope, policy — whether the file came from an explicit
`--config` or was auto-discovered by walking up from the current directory:
a parse failure always raises a usage error (exit `64`), never a
warn-and-continue. It only reaches as far as `BuildConfig.from_dict()`
itself validates, though: `targets:`/`bundles:`/`profiles:`/`baseline:` are
recognized top-level keys (so an unrecognized sibling key still errors) but
their *contents* are opaque to this loader — `compare` never inspects them
at all, so e.g. an invalid `targets.foo.kind` passes silently here. Only
`abicheck project validate`/`abicheck project plan` (`project_targets.py`)
actually validate that block's contents, deeply and independently of this
loader.

A *separate* load of the same `.abicheck.yml` — the `compile:` block shared
by `compare`/`dump`/`scan`'s L2 compile context (`gcc-path`, includes,
sysroot, …), resolved by `merge_compile_config()` in `cli_options.py` — does
distinguish explicit from auto-discovered: an explicit `--config` that
fails to parse still fails loudly, but an
**auto-discovered** file that fails only prints a warning and continues
with the CLI's own compile context, since a config the user never pointed
at explicitly could otherwise silently break an unrelated invocation just
by existing somewhere upward of the working directory. See
[Build-context capture](../use/dump-compare-flags.md#build-context-capture-compile_commandsjson-evidence-layer-l3)
for that path in detail. Don't assume this leniency extends to the rest of
the file's blocks — it's specific to that one loader, and only when it's
the only one invoked: a `dump`/`compare` run with `--sources`/`--build-info`
also goes through `cli_buildsource.embed_build_source()`'s own, separate
config resolution (L3/L4/L5 evidence collection, not the L2 compile
context), and *that* one raises a hard `click.UsageError` on a parse
failure unconditionally — explicit `--config` or auto-discovered alike,
same as the project-config rule above. Passing `--sources` therefore loses
the auto-discovered warn-and-continue leniency even for a run that would
otherwise get it through `merge_compile_config()` alone.

There is no longer an `init`/`config` scaffolding or diagnostic command
(`abicheck init`, `config validate`, `config show-effective` are all gone —
ADR-043) — write `.abicheck.yml` by hand, using this page as the schema/key
reference. Since unknown keys are now a hard error rather than a silent
warning, a typo or a key from a newer abicheck release will fail loudly
instead of being ignored — set the top-level `version:` if you need to
signal a schema generation to tooling, though it does not by itself suppress
an unknown-key error.

---

## Top-level keys

`build:`, `sources:`, `severity:`, `scope:`, `suppression:`, `source:`,
`compile:`, `debug:`, `bundle:`, `exit_code_scheme:`, `version:`,
`risk_rules:`, `crosschecks:`, `targets:`, `bundles:`, `profiles:`, and
`baseline:` are the recognized top-level keys. See the
[Config Keys Reference](config-keys-reference.md) for the exhaustive,
generated key/type list (`BuildConfig`'s own schema); the sections below
cover what each block does, its effective defaults, and behavior that isn't
visible from the type alone.

---

### `build:`

Drives inline build/source collection: an advisory build-system hint
(`system:`, default `auto`), a build-query command (`query:`) to produce a
compile DB, and/or an explicit `compile_db:` path or glob. `query` runs
**only** when the config is passed explicitly with `--config` (trusted) —
never from an auto-discovered config; `--allow-build-query` is a deprecated
no-op. See [Producing source facts](../use/producing-source-facts.md) and
[Build & source data](../learn/build-source-data.md).

---

### `sources:`

Public-header roots/globs (`public_headers:`, default `[]`) defining the
public surface, paths/globs excluded from source collection (`exclude:`,
default `[]`), and the L5 source-graph detail cap (`graph:`, `summary`
(default, a cheap changed-scope CI graph) or `full`, a full replay scope).

---

### `severity:`

Per-category severity map consumed by `compare`: a baseline `preset`
(`default`/`strict`/`info-only`) plus per-category overrides
(`abi_breaking`/`potential_breaking`/`quality_issues`/`addition`, each
`error`/`warning`/`info`) — per-category levels override the preset. When any
severity value is in effect, `compare` uses the severity-aware exit-code path.
See [Severity](../use/severity.md) and [Exit codes](exit-codes.md).

---

### `scope:`

Public-surface scoping — the main false-positive control. `public:` (default
effectively `true`) restricts analysis to the public exported surface;
`collapse_versioned_symbols:` (default `false`) collapses symbol-versioned
duplicates before diffing; `show_redundant:` (default `false`) disables
redundancy filtering. `public_symbols:` is an explicit public-symbol overlay,
the only spelling for it — the per-run CLI duplicates that used to shadow
this key were removed, so a project states it once, here — entries match
**exactly**
(the raw symbol, or a qualified name's trailing `::` segment, so `foo` also
matches `ns::foo`); **globs/wildcards are not supported** (`mylib_*` matches
nothing), list each symbol. See
[API-surface intelligence](../use/api-surface-intelligence.md).

---

### `suppression:`

Suppression **hygiene policy** (a project rule, distinct from the suppression
*rules file* — see [Related files](#related-files-not-abicheckyml-keys)):
`strict:` (default `false`) treats suppression-file problems strictly;
`require_justification:` (default `false`) requires a justification on every
suppression entry. See [Suppressions](../use/suppressions.md).

---

### `source:`

`method:` pins the precise S-axis (evidence method, `s0`..`s6`) for power
users.

> **Use a concrete `s0`..`s6`, not `auto`.** When `compare` reads `source.method`
> from the config (i.e. no `--depth` on the command line), the value must
> resolve to a concrete method — `compare` rejects `auto` with a usage error. Pin
> a specific level here, or leave the key unset and let `--depth`
> (`binary`/`headers`/`build`/`source` — `--max` and the old `full` depth no
> longer exist) drive the collection depth per run.

See [Scan levels](../use/scan-levels.md) and the
[`--depth` dial](../learn/evidence-and-detectability.md#the-depth-dial-how-much-evidence-to-collect).
(`graph` is not a valid `source:` sub-key — a config with `source: {graph:
...}` now fails with an unknown-key error. The L5 graph-detail knob is
`sources.graph`, in the plural [`sources:`](#sources) block above.)

---

### `compile:`

The stable half of the L2 header compile context (ADR-037 D4): AST
`frontend:` (`auto`/`castxml`/`clang`/`hybrid`, case-insensitive — `hybrid`
runs castxml and clang together and merges them), `std:` (C/C++ standard,
e.g. `c++17`), `include_dirs:`/`defines:` (lists), `sysroot:`, and
`nostdinc:` (boolean). Per-invocation cross-compile flags stay CLI overrides
(`CLI > config`).

> Values in `compile.std`/`compile.defines` must be a single whitespace-free
> compiler-option atom (a config scalar cannot expand into multiple compiler
> arguments).

> A relative `compile.include_dirs` entry resolves against the *project
> root* — the directory containing the discovered config, or the directory
> containing `.github/` when the config was found under `.github/` or
> `.github/abicheck/` (see [File discovery](#file-discovery)) — never
> against `.github/` itself.

---

### `debug:`

Separate-debug-file resolution for ELF (ADR-021a), demoted off the CLI in
ADR-040 Lever 2 — stable per-project debug-artifact knobs, each corresponding
to a now-hidden CLI flag that still overrides the config value
(`CLI > config`); the coarse per-run `--debug-root` stays a visible CLI flag.
`format:` (`auto`/`dwarf`/`btf`/`ctf`, case-insensitive, default auto-pick)
forces the ELF debug format for both sides (was `--debug-format`);
`dwarf_only:` (default `false`) uses DWARF as the primary source even when
headers are available (was `--dwarf-only`); `debuginfod:` (default `false`)
enables debuginfod network resolution (was `--debuginfod`); `debuginfod_url:`
overrides `DEBUGINFOD_URLS` (was `--debuginfod-url`).

---

### `bundle:`

Cross-library bundle-analysis topology (CLI cleanup phase two, PR J) — the
sole source for both settings now, replacing the removed
`--bundle-system-providers`/`--bundle-cohort` CLI flags: `system_providers:`
(a list of extra sonames to treat as system-provided, extending the built-in
libc/libstdc++/libgcc/libtbb allow-list) and `cohorts:` (a list of
co-versioned library name prefixes enabling the `BUNDLE_SONAME_SKEW` check).
Entries are stripped of surrounding whitespace and empty entries dropped at
parse time. `system_providers:` applies to `compare`'s directory/package
fan-out and `scan --artifact-set` alike; `cohorts:` (the SONAME-skew check)
applies to compare only — an `--artifact-set` audit has no old/new release
pair to detect a skew between, so it has no effect there. Distinct from the
plural `bundles:` block below, which serves a different, unrelated purpose
(the `project` command family's target declarations). See
[Multi-binary § The bundle-analysis flags](../use/multi-binary.md#the-bundle-analysis-flags).

---

### `exit_code_scheme:`

Top-level string, one of `auto`, `legacy`, `severity`. Default `auto`.

- `auto` → `severity` when a severity map is in effect, otherwise `legacy`.
- `legacy` / `severity` force that scheme.

See [Exit codes](exit-codes.md).

---

### `version:`

Top-level integer. Default `0` (unset). Declares the config schema version for
forward compatibility.

---

### `risk_rules:` and `crosschecks:`

Both are recognized top-level keys (so they do not trigger the unknown-key
error), but they are handled outside the `compare` config merge:

- **`risk_rules:`** — a mapping of rule-name → `{ paths: [...], weight: <int> }`
  path-glob risk profile. It is loaded by `scan`'s `--risk-rules <file>` option
  (which reads a `risk_rules:` block from the given YAML file); it is **not**
  auto-loaded from a discovered `.abicheck.yml`. Parsed by `RiskRules.from_dict`
  in `buildsource/risk.py`. See [Scan levels](../use/scan-levels.md).
- **`crosschecks:`** — reserved. The active mechanism for tuning cross-checks is
  `scan`'s repeatable `--crosscheck KEY=LEVEL` flag; the current code does not
  read a `crosschecks:` block from the file.

---

### `targets:`, `bundles:`, `profiles:`, and `baseline:`

Recognized top-level keys (so they do not trigger the unknown-key error),
but — like `risk_rules:`/`crosschecks:` above — not parsed by `BuildConfig`
itself. `dump`/`compare`/`scan` never read this block; it exists solely for
G30's GitHub Actions CI-integration primitives: `abicheck project validate`
validates it, and `abicheck project plan` consumes it to generate
`run-plan.json`. Parsed and validated by `buildsource/project_targets.py`;
see the **[Project Targets Schema reference](project-targets-schema.md)**
for the full field-by-field schema, the `checks:` list, and both
`abicheck project` subcommands.

---

## Related files (not `.abicheck.yml` keys)

Some settings often discussed alongside the config live in **separate YAML
files**, not in `.abicheck.yml`:

| Concept | File / flag | Top-level schema | Docs |
|---------|-------------|------------------|------|
| Policy profile | `--policy <file>` (`PolicyFile.load`, `policy_file.py`) — note `--policy` only takes the built-in names `strict_abi`/`sdk_vendor`/`plugin_abi` | `base_policy`, `overrides`, `reclassify`, `frozen_namespaces`, `evidence_policy` | [Policies](../use/policies.md) |
| Suppression rules | `--suppress <file>` (`suppression.py`) | Suppression rule entries (YAML or ABICC format) | [Suppressions](../use/suppressions.md) |

The `evidence_policy` block is part of the **policy file**, not `.abicheck.yml`.

---

## Complete example

A `.abicheck.yml` using only verified keys:

```yaml
# Config schema version (forward-compat marker)
version: 1

# Build-system hint + where the compile DB lands
build:
  system: cmake
  compile_db: build/compile_commands.json

# Public surface definition for source collection
sources:
  public_headers:
    - include/**
  exclude:
    - include/**/detail/**
  graph: summary

# Stable L2 header compile context
compile:
  frontend: castxml
  std: c++17
  include_dirs:
    - include
  defines:
    - MYLIB_STATIC=0
  nostdinc: false

# Separate-debug-file resolution (coarse --debug-root stays a CLI flag)
debug:
  format: auto
  dwarf_only: false
  debuginfod: false

# Severity policy consumed by `compare`
severity:
  preset: default
  abi_breaking: error
  potential_breaking: warning
  addition: info

# Public-surface scoping (false-positive control)
scope:
  public: true
  collapse_versioned_symbols: false
  show_redundant: false
  public_symbols:
    - mylib_foo
    - mylib_bar

# Suppression hygiene
suppression:
  strict: true
  require_justification: true

# Precise evidence method (optional; a concrete s0..s6, never `auto`)
source:
  method: s6

# Exit-code scheme for CI
exit_code_scheme: auto
```

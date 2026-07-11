# The `.abicheck.yml` config file

`.abicheck.yml` is the per-project configuration file (ADR-037 D4). It holds
the *stable, reviewed-in-a-PR* properties of a project's ABI contract — build
system, header compile context, severity policy, public-surface scoping, and
suppression hygiene — as opposed to per-run invocation flags.

Every field is optional; an absent, empty, or non-mapping file yields the
all-defaults configuration. **CLI flags always override the config**, which in
turn overrides the built-in defaults (`CLI > config > default`).

- Loader (build/source blocks): `load_build_config()` in
  `abicheck/buildsource/inline.py`; parsed into the `BuildConfig` dataclass.
- Precedence resolver (`compare` project-contract blocks):
  `resolve_compare_config()` in `abicheck/cli_helpers_compare.py`.

---

## File discovery

| Command | Discovery | Code |
|---------|-----------|------|
| `compare` | Walks up from the current directory to the filesystem root and uses the first `.abicheck.yml` found. | `discover_project_config()` in `cli_helpers_compare.py` |
| `dump --sources` / `--build-info` | Uses `.abicheck.yml` at the **source-tree root** only. | `discover_build_config()` in `buildsource/inline.py` |
| any | An explicit `--config <path>` overrides discovery. | `cli_options.py` (`--config`) |

> **Note:** an auto-discovered (untrusted) `.abicheck.yml` never causes a build
> command in `build.query` to run — it is skipped with a diagnostic. A
> `build.query` runs **only** when the config is supplied **explicitly** with
> `--config` (which marks it trusted for subprocess execution).
> `--allow-build-query` is a deprecated no-op and is **not** required.

### Forward compatibility

Unknown top-level keys, and unknown sub-keys inside a recognized block, **warn
but never error** — an older abicheck reading a newer config keeps working. Set
the top-level `version:` once a future key ships to silence the warning. A
malformed YAML file is a hard error.

---

## Top-level keys

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| [`build:`](#build) | mapping | — | Build-system hint and compile-DB location |
| [`sources:`](#sources) | mapping | — | Public-header roots, excludes, L5 graph detail |
| [`severity:`](#severity) | mapping | — | Per-category severity levels + preset |
| [`scope:`](#scope) | mapping | — | Public-surface scoping (false-positive tuning) |
| [`suppression:`](#suppression) | mapping | — | Suppression hygiene policy |
| [`source:`](#source) | mapping | — | Precise S-axis (evidence method) selection |
| [`compile:`](#compile) | mapping | — | Stable half of the L2 header compile context |
| [`debug:`](#debug) | mapping | — | Separate-debug-file resolution (format / debuginfod / dwarf-only) |
| [`exit_code_scheme:`](#exit_code_scheme) | string | `auto` | Which exit-code scheme `compare` uses |
| [`version:`](#version) | integer | `0` | Config schema version (forward-compat) |
| [`risk_rules:`](#risk_rules-and-crosschecks) | mapping | — | Path-glob risk profile (loaded via `--risk-rules`) |
| [`crosschecks:`](#risk_rules-and-crosschecks) | mapping | — | Reserved (recognized so it does not warn) |

Recognized keys and defaults live in `BuildConfig` (`buildsource/inline.py`).

---

### `build:`

Drives inline build/source collection. See
[Producing source facts](../user-guide/producing-source-facts.md) and
[Build & source data](../concepts/build-source-data.md).

| Sub-key | Type | Default | Purpose |
|---------|------|---------|---------|
| `system` | string | `auto` | Advisory build-system hint (e.g. `auto`, `cmake`, `bazel`, `make`, `ninja`). |
| `query` | string | `""` | Build-query command to produce a compile DB. Runs **only** when the config is passed explicitly with `--config` (trusted); ignored from an auto-discovered config. `--allow-build-query` is a deprecated no-op. |
| `compile_db` | string | `""` | Path (or glob) to a `compile_commands.json`. |

---

### `sources:`

| Sub-key | Type | Default | Purpose |
|---------|------|---------|---------|
| `public_headers` | list of strings (or a single string) | `[]` | Public-header roots/globs defining the public surface. |
| `exclude` | list of strings (or a single string) | `[]` | Paths/globs excluded from source collection. |
| `graph` | `summary` \| `full` | `summary` | L5 source-graph detail cap (`summary` = cheap changed-scope CI graph; `full` = full replay scope). |

---

### `severity:`

Per-category severity map consumed by `compare`. See
[Severity](../user-guide/severity.md) and [Exit codes](exit-codes.md).

| Sub-key | Type | Default | Purpose |
|---------|------|---------|---------|
| `preset` | `default` \| `strict` \| `info-only` | unset | Baseline severity preset. |
| `abi_breaking` | `error` \| `warning` \| `info` | unset | Level for the `abi_breaking` category. |
| `potential_breaking` | `error` \| `warning` \| `info` | unset | Level for the `potential_breaking` category. |
| `quality_issues` | `error` \| `warning` \| `info` | unset | Level for the `quality_issues` category. |
| `addition` | `error` \| `warning` \| `info` | unset | Level for the `addition` category. |

Per-category levels override the preset. When any severity value is in effect,
`compare` uses the severity-aware exit-code path (see
[Exit codes](exit-codes.md)).

---

### `scope:`

Public-surface scoping — the main false-positive control. See
[API-surface intelligence](../user-guide/api-surface-intelligence.md).

| Sub-key | Type | Default (effective) | Purpose |
|---------|------|---------------------|---------|
| `public` | boolean | unset → `true` | Restrict analysis to the public exported surface. |
| `collapse_versioned_symbols` | boolean | unset → `false` | Collapse symbol-versioned duplicates before diffing. |
| `public_symbols` | list of strings | `[]` | Explicit public-symbol overlay. Additive with any CLI `--public-symbol` values. Entries are matched **exactly** — by the raw symbol, or (for a qualified name) its trailing `::` segment, so `foo` also matches `ns::foo`. **Globs/wildcards are not supported** (`mylib_*` matches nothing); list each symbol. |
| `show_redundant` | boolean | unset → `false` | Disable redundancy filtering (show all changes, including those derived from a root type change). Demoted from `--show-redundant` (ADR-040 L2); the hidden CLI flag still overrides it. |

---

### `suppression:`

Suppression **hygiene policy** (a project rule, distinct from the suppression
*rules file* — see [Related files](#related-files-not-abicheckyml-keys)). See
[Suppressions](../user-guide/suppressions.md).

| Sub-key | Type | Default (effective) | Purpose |
|---------|------|---------------------|---------|
| `strict` | boolean | unset → `false` | Treat suppression-file problems strictly. |
| `require_justification` | boolean | unset → `false` | Require a justification on every suppression entry. |

---

### `source:`

| Sub-key | Type | Default | Purpose |
|---------|------|---------|---------|
| `method` | `s0`..`s6` | unset | Pins the precise S-axis (evidence method) for power users. |

> **Use a concrete `s0`..`s6`, not `auto`.** When `compare` reads `source.method`
> from the config (i.e. no `--depth`/`--max` on the command line), the value must
> resolve to a concrete method — `compare` rejects `auto` with a usage error. Pin
> a specific level here, or leave the key unset and let `--depth`/`--max` drive
> the collection depth per run.

See [Scan levels](../user-guide/scan-levels.md) and the
[`--depth` dial](../concepts/evidence-and-detectability.md#the-depth-dial-how-much-evidence-to-collect). (A `graph`
sub-key is accepted here for forward-compat but not consumed; the effective L5
detail is `sources.graph`.)

---

### `compile:`

The stable half of the L2 header compile context (ADR-037 D4). Per-invocation
cross-compile flags stay CLI overrides (`CLI > config`).

| Sub-key | Type | Default | Purpose |
|---------|------|---------|---------|
| `frontend` | `auto` \| `castxml` \| `clang` (case-insensitive) | unset | AST frontend for header parsing. |
| `std` | string (single option atom, no whitespace) | unset | C/C++ standard, e.g. `c++17`. |
| `include_dirs` | list of strings | `[]` | Include roots added to the compile context. |
| `defines` | list of strings (each a single option atom) | `[]` | Preprocessor defines, e.g. `FEATURE=1`. |
| `sysroot` | string | unset | Sysroot for header resolution. |
| `nostdinc` | boolean | unset | Suppress the standard system include paths. |

> Values in `compile.std`/`compile.defines` must be a single whitespace-free
> compiler-option atom (a config scalar cannot expand into multiple compiler
> arguments).

---

### `debug:`

Separate-debug-file resolution for ELF (ADR-021a), demoted off the CLI in
ADR-040 Lever 2. These are stable per-project debug-artifact knobs; the coarse
per-run `--debug-root` stays a visible CLI flag, while the settings below move
here. Each corresponds to a now-hidden CLI flag that still overrides the config
value (`CLI > config`).

| Sub-key | Type | Default (effective) | Purpose (was) |
|---------|------|---------------------|---------------|
| `format` | `auto` \| `dwarf` \| `btf` \| `ctf` (case-insensitive) | unset → auto-pick | Force the ELF debug format for both sides (`--debug-format`). |
| `dwarf_only` | boolean | unset → `false` | Use DWARF debug info as the primary source even when headers are available (`--dwarf-only`). |
| `debuginfod` | boolean | unset → `false` | Enable debuginfod network resolution (`--debuginfod`). |
| `debuginfod_url` | string | unset | debuginfod server URL, overriding `DEBUGINFOD_URLS` (`--debuginfod-url`). |

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
warning), but they are handled outside the `compare` config merge:

- **`risk_rules:`** — a mapping of rule-name → `{ paths: [...], weight: <int> }`
  path-glob risk profile. It is loaded by `scan`'s `--risk-rules <file>` option
  (which reads a `risk_rules:` block from the given YAML file); it is **not**
  auto-loaded from a discovered `.abicheck.yml`. Parsed by `RiskRules.from_dict`
  in `buildsource/risk.py`. See [Scan levels](../user-guide/scan-levels.md).
- **`crosschecks:`** — reserved. The active mechanism for tuning cross-checks is
  `scan`'s repeatable `--crosscheck KEY=LEVEL` flag; the current code does not
  read a `crosschecks:` block from the file.

---

## Related files (not `.abicheck.yml` keys)

Some settings often discussed alongside the config live in **separate YAML
files**, not in `.abicheck.yml`:

| Concept | File / flag | Top-level schema | Docs |
|---------|-------------|------------------|------|
| Policy profile | `--policy-file <file>` (`PolicyFile.load`, `policy_file.py`) — note `--policy` only takes the built-in names `strict_abi`/`sdk_vendor`/`plugin_abi` | `base_policy`, `overrides`, `frozen_namespaces`, `evidence_policy` | [Policies](../user-guide/policies.md) |
| Suppression rules | `--suppress <file>` (`suppression.py`) | Suppression rule entries (YAML or ABICC format) | [Suppressions](../user-guide/suppressions.md) |

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

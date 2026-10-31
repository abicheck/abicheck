---
doc_type: migration
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: migration
generated: false
---

# Upgrading from 0.6 to 0.7

This page is the migration for the 0.7 option-vocabulary renames. The same
concept had two spellings on the command line; 0.7 keeps one, and the old
spelling is removed. A script that still uses a retired spelling stops with
exit 64 and a `No such option` error naming the flag, so it fails loudly
rather than running with a different meaning.

**Do this first:** run your existing command lines against 0.7. Each failure
names the retired flag; the table below gives its replacement.

## CLI renames

| 0.6 spelling | 0.7 spelling | Where |
|---|---|---|
| `--version LABEL` (per-side version label) | `--label LABEL` | `compare`, `dump`, `project history` |
| `--abi3 VERSION` (stable-ABI floor) | `--abi3-floor VERSION` | `compare` |
| `deps compare --old-root PATH` | `deps compare --root old=PATH` | `deps compare` |
| `deps compare --new-root PATH` | `deps compare --root new=PATH` | `deps compare` |
| `deps tree --sysroot PATH` | `deps tree --root PATH` | `deps tree` |

`--label` takes the same `old=`/`new=` qualifier as `--version` did:
`--label old=1.0 --label new=2.0`. A bare value labels both sides. The root
`abicheck --version` is unchanged; it still prints the tool version.

`--root` on `deps compare` takes the same `old=`/`new=` qualifier as
`--header`. The two old flags map one-to-one onto it, and you may combine
`--root old=` with `--root new=`.

## What a retired spelling does

- **It is an unknown option.** The command exits 64 before it analyses
  anything, and the error names the flag, for example
  `Error: No such option '--version'`.
- **Nothing runs with a different meaning.** The retired spelling is not
  kept as a hidden alias, so there is no warning period to watch for.

## Config keys

Config keys have no transition period: the old key is removed in this
release, so a `.abicheck.yml` that still uses it fails with the strict schema's
unknown-key error, and the run stops instead of changing meaning.

| 0.6 key | 0.7 key | Notes |
|---|---|---|
| `acknowledgment:` (the `.abicheck.yml` block) | `acknowledgments:` | Same keys inside (`file`, `unacknowledged_additions`, `breaks`). A `--policy` file's own `acknowledgment:` block is a different document and is unchanged. |
| `source: {method: ...}` | `sources: {method: ...}` | Joins the existing `sources:` block (`public_headers`, `exclude`, `graph`). |

## Action and typed API

The typed Python API (`run_compare`, `run_dump`, `CompareRequest`, `DumpRequest`,
`InputSpec`) renames these keyword arguments and fields. Each old spelling is
removed outright, so passing it raises `TypeError` naming the unexpected keyword.

| 0.6 spelling | 0.7 spelling | Where |
|---|---|---|
| `old_version` / `new_version` | `old_label` / `new_label` | `run_compare` |
| `policy_file_path` | `policy` (a document path; `policy` takes a profile name or a document) | `run_compare`, `CompareRequest` |
| `acknowledgments_path` | `acknowledgments` | `run_compare`, `CompareRequest` |
| `force_public_symbols` | `public_symbols` | `run_compare`, `CompareRequest` |
| `enable_debuginfod` | `debuginfod` | `run_compare`, `run_dump`, `CompareRequest`, `DumpRequest` |
| `follow_dependencies` | `follow_deps` | `CompareRequest`, `DumpRequest` |
| `include_dependencies` | `include_system_declarations` | `run_compare`, `run_dump`, `InputSpec` |
| `contract_evaluation` and `contract_mode` | `contract` (`public`, `exports`, `all`, `auto`) | `run_compare`, `CompareRequest` |
| `header_backend` | `frontend` | `run_dump` |

`contract_evaluation=False` has no replacement: contract relevance is always
evaluated, as the CLI does. `InputSpec.version` and `run_dump(version=)` keep
their names.

The composite GitHub Action forwards these flags itself, so the CLI renames
above need no change in a workflow that uses only the Action. The Action's own
inputs are renamed too; see the table below.

## Action input renames

Unlike the CLI, the Action has **no transition period**: each renamed input
below is retired as of this release. The new name replaces the old one at once,
and setting the old name fails the run before any analysis, with an
`::error::` that names the new input. Change the workflow in the same edit
that moves to this release.

| 0.6 input | 0.7 input | Notes |
|---|---|---|
| `ast-frontend` | `frontend` | |
| `gcc-path` | `compiler` | Takes a compiler path or a cross-toolchain prefix. |
| `gcc-prefix` | `compiler` | Removed. `compiler` takes the same value. |
| `gcc-options` | `compile-options` | |
| `analysis-assurance-complete` | `require-complete` | Same `true`/`false` override of `assurance.require_complete`. |
| `public-header-dir` | `public-header-dirs` | |
| `debug-info1`, `debug-info2` | `old-debug-info`, `new-debug-info` | |
| `devel-pkg1`, `devel-pkg2` | `old-header`, `new-header` | These now also accept a development package. |
| `old-version`, `new-version` | `old-label`, `new-label` | Report labels; the defaults stay `old` and `new`. |
| `policy-file` | `policy` | `policy` takes either a built-in profile name or a policy file path. |
| `requested-depth` (`check-target`) | `depth` | |
| `contract-file` (`check-target`) | `contract` | |
| `dso-only` | `set: release.dso_only=true` | Config key `release.dso_only`. |
| `include-private-dso` | `set: release.include_private_dso=true` | Config key `release.include_private_dso`. |
| `fail-on-removed-library` | `set: gate.fail_on_removed_library=true` | Config key `gate.fail_on_removed_library`. |
| `public-header-dirs` | `header: <dir> ...` | It was forwarded as `-H`, which is what `header` does. Use `old-header`/`new-header` for one side. |
| `frontend` | `set: compile.frontend=<value>` | |
| `compile-options` | `set: compile.options=[<flag>, ...]` | A YAML list, one flag per element. |
| `nostdinc` | `set: compile.nostdinc=true` | |
| `jobs` | none | Removed with no replacement. The worker count is controlled by the runner or workflow. |
| `allow-build-query` | none | Removed with no replacement. abicheck runs its inferred compile-database query whenever a sources input needs build context. A trusted query is set through `build-config`'s `build.query`. |
| `consumer-ast-frontend` (`check-target`) | `consumer-frontend` | |
| `consumer-gcc-path` (`check-target`) | `consumer-compiler` | |
| `consumer-gcc-options` (`check-target`) | `consumer-compile-options` | |

`check-target`'s `analysis-assurance-complete`, `ast-frontend`, `gcc-path`,
`gcc-options`, `gcc-prefix` and `policy-file` map to the same new names as the
root Action.

## Reusable workflow input renames

`check-single.yml` and `check-project.yml` take the Action's vocabulary, with no
transition period. Each old `workflow_call` input is still declared as an empty
tombstone, and a non-empty value fails the run before any analysis with an
`::error::` that names the replacement.

| 0.6 input | 0.7 input | Workflows |
|---|---|---|
| `requested-depth` | `depth` | `check-single.yml` |
| `ast-frontend` | `frontend` | both |
| `gcc-path`, `gcc-prefix` | `compiler` | both |
| `gcc-options` | `compile-options` | both |
| `contract-file` | `contract` | `check-single.yml` |
| `policy-file` | `policy` | both |

The `extra-args` input is passed to the CLI as raw arguments. A workflow
that passes a retired spelling there, for example `extra-args: --version 2.0`,
now fails with exit 64. Change it to the new spelling (`--label 2.0`).

## Compare options moved to `.abicheck.yml`

Three project-stable `compare` options are removed from the command line. Each
now fails as an unknown option (exit 64). Set the same value in `.abicheck.yml`,
or pass it once with `--set PATH=VALUE`, whose value is YAML.

| 0.6 spelling | 0.7 spelling | Notes |
|---|---|---|
| `compare --view TOKEN` | `output.view` (`--set 'output.view=["show=breaking"]'`) | A YAML list of the same tokens. |
| `compare --used-by-manifest FILE` | `consumers.manifests` (`--set 'consumers.manifests=[FILE]'`) | Relative paths resolve against the config file. |
| `compare --required-symbol NAME` | `consumers.required_symbols` (`--set 'consumers.required_symbols=[NAME]'`) | Entries may be `@FILE`. Still mutually exclusive with `--used-by`. |

`--used-by`, `--define` and `--severity-preset` stay on the command line. `consumers.*` and `output.view` are
refused, not ignored, under `--no-baseline` and on a stored BundleFacts operand.

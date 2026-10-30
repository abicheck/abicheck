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

This page is the migration for the 0.7 option-vocabulary renames.
The same concept had two spellings on the command line; 0.7 keeps one. Each old spelling still
works for one release, so existing scripts keep running while you change them.

**Do this first:** run your existing command lines once against 0.7. A
retired spelling still runs, but prints a `DeprecationWarning` on stderr that
names its replacement. That warning is the list of what to change.

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

## What the old spellings do

- **They still work** for the 0.7 release, with the same meaning.
- **They warn once** per invocation, on stderr, naming the new spelling, for
  example: `DeprecationWarning: The option '--version' is deprecated. Use --label instead.`
- **They are hidden** from `--help`. `--help-all` does not list them either.
- **Passing both spellings for the same thing is a usage error (exit 64)**,
  for example `--label 1.0 --version 2.0`. Pick one.

The old spellings are removed in the next minor release. After that they fail
as unknown options (exit 64), so a script that still uses one stops loudly
rather than running with a different meaning.

## Action and typed API

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

`check-target`'s `analysis-assurance-complete`, `ast-frontend`, `gcc-path`,
`gcc-options`, `gcc-prefix` and `policy-file` map to the same new names as the
root Action. Its `consumer-*` inputs are unchanged.

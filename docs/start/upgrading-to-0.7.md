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

## Action and typed API

The composite GitHub Action forwards these flags itself, so workflows that use
the Action need no change for this page. The Action's own inputs
(`old-version`, `new-version`, `sysroot`, `old-root`, `new-root`) are renamed
in a later step of the same ADR; this page will list them when they are.

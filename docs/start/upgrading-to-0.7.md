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

The composite GitHub Action forwards these flags itself, so workflows that use
the Action need no change for this page. The Action's own inputs
(`old-version`, `new-version`, `sysroot`, `old-root`, `new-root`) are renamed
in a later step of the same ADR; this page will list them when they are.

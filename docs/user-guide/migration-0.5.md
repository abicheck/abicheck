# Migrating to 0.5.0 — `compare` flag changes

> **Superseded by a later, larger reset.** A subsequent pre-1.0 CLI reset
> (ADR-043) went further than this page describes: `appcompat` and
> `plugin-check` (mentioned below as still-current commands) were removed
> entirely and folded into `compare --used-by`/`compare --required-symbol(s)`;
> `scan --baseline` was renamed to `scan --against`; `deps compare
> --baseline`/`--candidate` was renamed to `--old-root`/`--new-root`; the
> `--depth`/evidence-collection surface was narrowed to exactly `binary`,
> `headers`, `build`, `source` (no more `full`, `--mode`, `--source-method`);
> and several standalone commands (`baseline`, `collect`, `merge`,
> `debian-symbols`, `doctor`, `config`, `init`, `surface-report`,
> `pr-comment`, `suggest-suppressions`, `probe`) were removed from the CLI
> with no replacement command. See [CLI Usage](cli-usage.md),
> [Companion Commands](companion-commands.md), and
> [Source-Scan Depth](scan-levels.md) for the current surface. This page is
> kept as the historical record of the 0.5.0 side-aware-flags change, which
> is otherwise still accurate for the flags it covers.

0.5.0 reshapes the `compare` (and, at the time, `appcompat`) command line under
[ADR-040](../development/adr/040-compare-surface-reduction.md). Two kinds of
change affect existing invocations and CI scripts:

1. **Side-aware flags (Lever 1).** The per-side `--old-X` / `--new-X` pairs
   collapse into one repeatable `--X` that takes an optional `old=` / `new=`
   value prefix. A bare value (or the `-H` / `-I` short forms) still applies to
   both sides.
2. **Config demotion (Lever 2).** A few stable debug-resolution knobs and the
   redundancy-filter toggle move into `.abicheck.yml`. The old flags still work
   (they are hidden overrides), but the documented home is now the config file.

There is **no alias window** for the removed side-aware spellings — the old
`--old-header` / `--new-header` etc. are gone, matching how 0.4.0 removed
`--header-backend`. Update scripts before upgrading.

## Side-aware flags (Lever 1)

Each concept below is now a single repeatable flag. Scope a value to one side
with an `old=` / `new=` prefix, **repeating the flag per side**; a bare value
applies to both.

| Removed (0.4.x) | Replacement (0.5.0) |
|-----------------|---------------------|
| `--old-header v1/f.h --new-header v2/f.h` | `--header old=v1/f.h --header new=v2/f.h` |
| `--old-include i1 --new-include i2` | `--include old=i1 --include new=i2` |
| `--old-version 1.0 --new-version 2.0` | `--version old=1.0 --version new=2.0` |
| `--old-sources src1 --new-sources src2` | `--sources old=src1 --sources new=src2` |
| `--old-build-info b1 --new-build-info b2` | `--build-info old=b1 --build-info new=b2` |
| `--old-pdb-path a.pdb --new-pdb-path b.pdb` | `--pdb-path old=a.pdb --pdb-path new=b.pdb` |
| `--debug-root1 d1 --debug-root2 d2` | `--debug-root old=d1 --debug-root new=d2` |
| `--debug-info1 x --debug-info2 y` | `--debug-info old=x --debug-info new=y` |
| `--devel-pkg1 p --devel-pkg2 q` | `--devel-pkg old=p --devel-pkg new=q` |
| `--probe-matrix-old m1 --probe-matrix-new m2` | `--probe-matrix old=m1 --probe-matrix new=m2` |

Notes:

- **Repeat the flag**, don't chain values: `--header old=a new=b` is wrong (the
  second token is not a value). Write `--header old=a --header new=b`.
- **`-H` / `-I` are unchanged** and still mean "both sides"; use them for the
  common case where the same header/include applies to both versions.
- **`both=`** is an escape hatch for a path that literally begins `old=` /
  `new=` (rare): `--header both=old=weird.h`.
- The **version** flag defaults per side stay `old` / `new` — pass `--version`
  only when your `.so` files need explicit labels.

The `--ast-frontend` per-side overrides (`--old-ast-frontend` /
`--new-ast-frontend`) are **unchanged**: the base `--ast-frontend` is shared with
`dump` and `scan`, so that family was deliberately left alone.

## Config demotion (Lever 2)

These flags are no longer in `compare --help`. They still function as overrides,
but the reviewed home is `.abicheck.yml`. See the
[config-file reference](../reference/config-file.md#debug).

| Was a flag | Now a config key (block → key) |
|------------|-------------------------------|
| `--debug-format dwarf` | `debug.format: dwarf` |
| `--dwarf-only` | `debug.dwarf_only: true` |
| `--debuginfod` | `debug.debuginfod: true` |
| `--debuginfod-url URL` | `debug.debuginfod_url: URL` |
| `--show-redundant` | `scope.show_redundant: true` |

Example `.abicheck.yml`:

```yaml
debug:
  format: auto
  dwarf_only: false
scope:
  show_redundant: false
```

Precedence is **CLI > config > default**, so a script that still passes
`--dwarf-only` keeps working and overrides the config value. The boolean
toggles are two-way, so a one-off run can also force the value *off* over a
config `true`: `--no-dwarf-only` (restore header parsing), `--no-debuginfod`,
`--no-show-redundant`.

**Not demoted (still visible flags):**

- `--debug-root` — the coarse per-run debug-tree override (now side-aware, see
  the table above).
- The toolchain family (`--gcc-path` / `--gcc-prefix` / `--gcc-options` /
  `--gcc-option` / `--sysroot` / `--nostdinc`) — shared with `dump` / `scan`.
- `--scope-public-headers` / `--no-scope-public-headers` — the everyday on/off
  switch for public-surface scoping.

## Run profiles (Lever 3, additive)

New in the same line of work: `--profile {ci-gate,release,quick}` bundles a
workflow's common defaults into one token (explicit flags still win). It is
additive — nothing to migrate — but it can replace habitual flag stacks. See
[CLI usage](cli-usage.md).

## GitHub Action

The Action's per-side inputs (`old-header`, `new-header`, `old-version`,
`new-version`, `debug-info1`, `devel-pkg1`, …) are **unchanged** — the wrapper
maps them to the new side-aware flags internally. No workflow edits are needed
for Action users.

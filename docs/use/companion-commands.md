# Migrating to the Current CLI (pre-1.0)

abicheck's CLI went through two rounds of change before 1.0: a 0.5.0 flag
reset ([ADR-040](../contribute/adr/040-compare-surface-reduction.md)) that
reshaped `compare`'s flags, followed by a larger command-surface reset
([ADR-043](../contribute/adr/043-cli-pre-1.0-surface-reset.md)) that removed
or folded most of the standalone companion commands. This page is the
combined map: which commands survive, which became flags, which are gone,
and which flags/inputs were renamed.

## Removed commands

Running any of these now just fails with Click's normal "No such command"
error. Where a library function survives for programmatic/Python API use,
that's noted — none of these are documented as a public CLI path anymore.

| Deleted command | Status |
|---|---|
| `baseline` (registry group: push/pull/list/delete) | No replacement command. Use `scan --against OLD` for point-in-time comparisons, or keep JSON snapshots yourself (plain files, your own storage/naming convention). See [Baseline Management](baseline-management.md). |
| `collect`, `merge`, `recommend-collect-mode` | Gone from the CLI. `dump --sources`/`--build-info` auto-collects build/source evidence inline; `compare` auto-ingests each side's embedded build-source pack, or an out-of-band pack via `--build-info old=PATH`/`--build-info new=PATH` (auto-detects `abicheck_inputs/` packs too). Library functions survive for internal/programmatic use only. |
| `debian-symbols` | No CLI replacement. Library functions still exist in `abicheck/debian_symbols.py` (`generate_symbols_file`, `validate_symbols`, `diff_symbols_files`, `parse_symbols_file`, etc.) for programmatic/Python API use only. See [Debian Symbols](debian-symbols.md). |
| `doctor` | No replacement command. |
| `config` (scaffolding subcommand: `config validate`, `config show-effective`) | No replacement command. Config loading is strict now (unknown keys, wrong types, bad enum values are hard errors, exit `64`), so `validate` is less necessary; there is no `show-effective` equivalent. |
| `init` | No replacement command — no more `.abicheck.yml` scaffolding generator. Write the file by hand; see [Config File Reference](../reference/config-file.md) for the schema/keys. |
| `surface-report` | No replacement command. |
| `graph compare` / `graph explain` | No replacement command. |
| `pr-comment` | Moved off the public CLI. Now invoked only as `python -m abicheck.cli_pr_comment`, used internally by the GitHub Action — not a documented end-user command. |
| `suggest-suppressions` | No replacement command. |
| `probe` (`probe run`, etc.) | No replacement command. `compare --probe-matrix` still consumes a previously captured matrix snapshot file, but there's no CLI to *generate* one anymore. |

## Folded into `compare` flags

Two of the old standalone commands became scoping flags on `compare` instead
of separate commands — the full library comparison still runs once, and the
worst app/plugin-scoped result becomes the primary verdict/exit code, with
the full verdict kept as informational context.

| Old command | New flag | What it scopes to |
|---|---|---|
| `appcompat` | `compare --used-by APP` (repeatable) | An application binary's actual imports/required symbol versions. Mutually exclusive with `--required-symbol`/`--required-symbols`. |
| `plugin-check` | `compare --required-symbol SYM` (repeatable) / `--required-symbols FILE` (one symbol per line, `#` comments ignored) | An explicit plugin-host entrypoint contract instead of the full diff. Mutually exclusive with `--used-by`. |

```bash
# Was: abicheck appcompat --app myapp old.so new.so
abicheck compare old.so new.so -H include/ --used-by build/myapp

# Was: abicheck plugin-check --required-symbol foo_init old.so new.so
abicheck compare old.so new.so -H include/ --required-symbol foo_init
```

See [Application Compatibility](appcompat.md) and [Plugin & Host
Systems](plugin-systems.md) for the full guides — treat their command-line
examples as the ones that matter, this page only summarizes the mapping.

## Still commands today

Some of the old companion functionality survives as a **command**:

| Command | What it does |
|---|---|
| [`deps tree`](#deps-tree) | Resolve one binary's dependency closure and symbol bindings. |
| [`deps compare`](#deps-compare) | Diff a binary's full dependency stack across two environments (was `stack-check`). |
| `compat check` / `compat dump` | ABICC-compatible drop-in replacement commands — see [Migrating from ABICC](from-abicc.md) if you're moving from `abi-compliance-checker`. |

The CLI surface today has five core per-library analysis commands
(`compare`, `compat`, `deps`, `dump`, `scan`) plus project-orchestration
commands — `aggregate` and the `project` group (`project plan`/`project
validate`/`project validate-build`, ADR-054) — that compose those five
across a multi-target project — neither group is part of the
companion-command consolidation this page describes; see the
[CLI Reference](../reference/cli-reference.md) for the full command tree.

### `deps tree`

```bash
abicheck deps tree ./build/libfoo.so
abicheck deps tree /usr/bin/myapp --format json -o deps.json
abicheck deps tree ./app --sysroot /path/to/container/rootfs
```

Exit codes: `0` all dependencies resolved, `1` missing dependencies/symbols.

### `deps compare`

```bash
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
abicheck deps compare usr/lib/libfoo.so.1 \
  --old-root ./image-v1 --new-root ./image-v2 --format json
```

`--old-root`/`--new-root` (each default `/`) point at the two sysroots to
compare `BINARY` across. Exit codes: `0` PASS, `1` WARN (loads but ABI risk),
`4` FAIL (load failure or binary ABI break).

## Renamed/restructured flags (0.5.0, ADR-040)

Before the command-surface reset above, 0.5.0 reshaped `compare`'s (and, at
the time, `appcompat`'s) flags. Two kinds of change affect scripts still
written against a pre-0.5.0 invocation:

### Side-aware flags

Each concept below is now a single repeatable flag. Scope a value to one side
with an `old=` / `new=` prefix, **repeating the flag per side**; a bare value
applies to both. There is **no alias window** for the removed spellings —
update scripts before upgrading.

| Removed (0.4.x) | Replacement (0.5.0+) |
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
- The `--ast-frontend` per-side overrides (`--ast-frontend old=` /
  `--ast-frontend new=`) are **unchanged**: the base `--ast-frontend` is shared
  with `dump` and `scan`, so that family was deliberately left alone.

### Config demotion

These flags are no longer in `compare --help`. They still function as
overrides, but the reviewed home is `.abicheck.yml`. See the
[config-file reference](../reference/config-file.md#debug).

| Was a flag | Now a config key (block → key) |
|------------|-------------------------------|
| `--debug-format dwarf` | `debug.format: dwarf` |
| `--dwarf-only` | `debug.dwarf_only: true` |
| `--debuginfod` | `debug.debuginfod: true` |
| `--debuginfod-url URL` | `debug.debuginfod_url: URL` |
| `scope.show_redundant: true` | `scope.show_redundant: true` |

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

**Not demoted (still visible flags):** `--debug-root` (the coarse per-run
debug-tree override, now side-aware, see the table above); the toolchain
family (`--compiler` / `--compiler-prefix` / `--compiler-option` /
`--sysroot` / `--nostdinc`, shared with `dump`/`scan`); and
`--scope-public-headers` / `--no-scope-public-headers` (the everyday on/off
switch for public-surface scoping).

### Run profiles (additive)

New in the same line of work: `--profile {ci-gate,release-cut,quick}` bundles
a workflow's common defaults into one token (explicit flags still win). It is
additive — nothing to migrate — but it can replace habitual flag stacks.

### GitHub Action

The Action's per-side inputs (`old-header`, `new-header`, `old-version`,
`new-version`, `debug-info1`, `devel-pkg1`, …) were **unaffected** by the
side-aware-flags change above — the wrapper maps them to the new side-aware
flags internally. No workflow edits were needed for Action users at the time.

## Related pages

- [CLI Usage](cli-usage.md) — the core `dump`/`compare` flow
- [Application Compatibility](appcompat.md) — `compare --used-by`
- [Plugin & Host Systems](plugin-systems.md) — `compare --required-symbol(s)`
- [Baseline Management](baseline-management.md) — storing/producing comparison baselines now that `baseline` is gone
- [Debian Symbols](debian-symbols.md) — the surviving library-only API

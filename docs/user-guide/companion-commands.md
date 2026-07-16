# Companion Commands: What Happened to Them

Before the pre-1.0 CLI reset, abicheck shipped a wide set of standalone
companion commands (`appcompat`, `plugin-check`, `baseline`, `collect`,
`merge`, `debian-symbols`, `doctor`, `config`, `init`, `surface-report`,
`graph compare`/`graph explain`, `pr-comment`, `suggest-suppressions`,
`probe`). The CLI surface has since been narrowed to exactly five top-level
commands:

```
compare  Compare two ABI surfaces and report changes.
compat   ABICC-compatible commands (drop-in replacement for abi-compliance-checker).
deps     Inspect a binary's shared-library dependency stack.
dump     Dump ABI snapshot of a shared library to JSON.
scan     Deterministic source-intelligence scan (classify → always-on tier → level).
```

Some of the old companion functionality survives as a **command** (`deps
tree`, `deps compare`, `compat check`/`compat dump`); some folded into a
**flag** on `compare`; the rest is **gone with no CLI replacement**. This
page is the map.

## Still commands today

| Command | What it does |
|---|---|
| [`deps tree`](#deps-tree) | Resolve one binary's dependency closure and symbol bindings. |
| [`deps compare`](#deps-compare) | Diff a binary's full dependency stack across two environments (was `stack-check`). |
| `compat check` / `compat dump` | ABICC-compatible drop-in replacement commands — see [Migrating from ABICC](from-abicc.md) if you're moving from `abi-compliance-checker`. |

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
Systems](plugin-systems.md) for the full guides — both are being updated in
parallel to reflect this same reset; treat their command-line examples as
the ones that matter, this page only summarizes the mapping.

## Gone entirely — no CLI replacement

Running any of these now just fails with Click's normal "No such command"
error. Where a library function survives for programmatic/Python API use,
that's noted — none of these are documented as a public CLI path anymore.

| Deleted command | Status |
|---|---|
| `baseline` (registry group: push/pull/list/delete) | No replacement command. Use `scan --against OLD` for point-in-time comparisons, or keep JSON snapshots yourself (plain files, your own storage/naming convention). See [Baseline Management](baseline-management.md) (being updated in parallel). |
| `collect`, `merge`, `recommend-collect-mode` | Gone from the CLI. `dump --sources`/`--build-info` auto-collects build/source evidence inline; `compare` auto-ingests each side's embedded build-source pack, or an out-of-band pack via `--build-info old=PATH`/`--build-info new=PATH` (auto-detects `abicheck_inputs/` packs too). Library functions survive for internal/programmatic use only. |
| `debian-symbols` | No CLI replacement. Library functions still exist in `abicheck/debian_symbols.py` (`generate_symbols_file`, `validate_symbols`, `diff_symbols_files`, `parse_symbols_file`, etc.) for programmatic/Python API use only. See [Debian Symbols](debian-symbols.md) (being updated in parallel). |
| `doctor` | No replacement command. |
| `config` (scaffolding subcommand: `config validate`, `config show-effective`) | No replacement command. Config loading is strict now (unknown keys, wrong types, bad enum values are hard errors, exit `64`), so `validate` is less necessary; there is no `show-effective` equivalent. |
| `init` | No replacement command — no more `.abicheck.yml` scaffolding generator. Write the file by hand; see [Config File Reference](../reference/config-file.md) for the schema/keys. |
| `surface-report` | No replacement command. |
| `graph compare` / `graph explain` | No replacement command. |
| `pr-comment` | Moved off the public CLI. Now invoked only as `python -m abicheck.cli_pr_comment`, used internally by the GitHub Action — not a documented end-user command. |
| `suggest-suppressions` | No replacement command. |
| `probe` (`probe run`, etc.) | No replacement command. `compare --probe-matrix` still consumes a previously captured matrix snapshot file, but there's no CLI to *generate* one anymore. |

## Related pages

- [CLI Usage](cli-usage.md) — the core `dump`/`compare` flow
- [Application Compatibility](appcompat.md) — `compare --used-by`
- [Plugin & Host Systems](plugin-systems.md) — `compare --required-symbol(s)`
- [Baseline Management](baseline-management.md) — storing/producing comparison baselines now that `baseline` is gone
- [Debian Symbols](debian-symbols.md) — the surviving library-only API

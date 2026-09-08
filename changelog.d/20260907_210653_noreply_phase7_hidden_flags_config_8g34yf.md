### Removed

- **Phase 7 CLI cleanup: hidden debug-resolution flags and the whole L2
  compile-context family are gone from `compare`/`dump`** (one-comparison-
  product.md §4.1/§4.2, ADR-037 D8.1, ADR-068 D5) — no CLI spelling
  survives, matching this repo's no-deprecation-window policy. `.abicheck.yml`
  is each setting's only source now:
  - `--dwarf-only`/`--no-dwarf-only`, `--debuginfod`/`--no-debuginfod`,
    `--debuginfod-url`, `--debug-format` on `compare` (previously hidden,
    already config-overridable) → `debug.dwarf_only`, `debug.debuginfod`,
    `debug.debuginfod_url`, `debug.format`.
  - `--dwarf-only`, `--debug-format`, `--debuginfod`, `--debuginfod-url`,
    `--pdb-path` on `dump` (previously visible) → the same `debug.*` keys,
    plus `debug.pdb_path`.
  - `--ast-frontend`, `--allow-ast-frontend-fallback`,
    `--allow-unsupported-castxml`, `--compiler`, `--compiler-prefix`,
    `--compiler-option`, `--sysroot`, `--nostdinc`/`--no-nostdinc`,
    `--frontend-context`, `--lang` on both `compare` and `dump` →
    `compile.frontend`, `compile.ast_frontend_fallback`,
    `compile.allow_unsupported_castxml`, `compile.compiler` (merges the
    former `--compiler`/`--compiler-prefix` pair — a value ending in `-` is
    a toolchain prefix, anything else a compiler path), `compile.options`,
    `compile.sysroot`, `compile.nostdinc`, `compile.frontend_context`, and
    `compile.lang` (defaults to `c++` when unset).
  - `scan` is unaffected — it keeps every one of these as a real CLI flag.
  - The GitHub Action's matching inputs (`ast-frontend`/`gcc-path`/
    `gcc-prefix`/`gcc-options`/`sysroot`/`nostdinc`/`lang`) still work on
    `dump` and single-pair `compare`: `action/run.sh` now synthesizes a
    `.abicheck.yml` `compile:` block from them and forwards it via
    `--config`, and rejects combining any of them with an explicit
    `build-config` input (no config-merging is attempted).

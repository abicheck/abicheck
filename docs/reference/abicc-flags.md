# ABICC Flag Reference

`abicheck compat check` and `abicheck compat dump` accept the same single-hyphen
flags as `abi-compliance-checker` (ABICC), so existing ABICC command lines work
unchanged. This page is the exhaustive parity reference: every ABICC flag abicheck
recognises, its aliases, and whether it is functional or accepted-but-inert.

For the migration walkthrough (why migrate, before/after invocations, exit-code
mapping), see [Migrating from ABICC](../user-guide/from-abicc.md).

## Core flags

| Flag | Alias(es) | Required | Description |
|------|-----------|:--------:|-------------|
| `-lib NAME` | `-l`, `-library` | ✅ | Library name (used in report and output path) |
| `-old PATH` | `-d1` | ✅ | Path to old version XML descriptor or ABI dump (the `-o` alias is removed to avoid collision with `-o`/`--output`) |
| `-new PATH` | `-d2`, `-n` | ✅ | Path to new version XML descriptor or ABI dump |
| `-report-path PATH` | | | Output report path (default: `compat_reports/<lib>/<v1>_to_<v2>/report.html`) |
| `-report-format FMT` | | | Report format: `html` (default), `json`, `md` (ABICC used `htm`/`xml`; `htm` is accepted as an alias for `html`) |
| `-bin-report-path PATH` | | | Separate binary-mode report output path |
| `-src-report-path PATH` | | | Separate source-mode report output path |

## Analysis mode flags

| Flag | Alias(es) | Description |
|------|-----------|-------------|
| `-source` | `-src`, `-api` | Source/API compatibility only — filters out ELF-level symbol metadata changes (SONAME, symbol binding, versioning) |
| `-binary` | `-bin`, `-abi` | Binary ABI mode (default behavior, explicit flag is a no-op) |
| `-s` | `-strict` | Strict mode: any change (COMPATIBLE or API_BREAK) is treated as BREAKING → exit 1 |
| `-warn-newsym` | | Treat new symbols (FUNC_ADDED, VAR_ADDED) as compatibility breaks → exit 1 |
| `-show-retval` | | Include return-value changes in the HTML report |
| `-headers-only` | | Header-only analysis mode (accepted; ELF/DWARF checks still run) |
| `-use-dumps` | | Interpret -old/-new as pre-built dumps (auto-detected by `.json` extension) |

## Output flags

| Flag | Alias(es) | Description |
|------|-----------|-------------|
| `-stdout` | | Print the report content to stdout in addition to writing to file |
| `-title NAME` | | Custom report title (wired to HTML `<title>` and `<h1>`) |
| `-component NAME` | | Component name shown in report (sets title to "ABI Report — LIB (COMPONENT)" if no -title) |
| `-limit-affected N` | | Maximum number of affected symbols shown per change kind |
| `-list-affected` | | Generate a separate `.affected.txt` file listing all affected symbols |
| `-q` | `-quiet` | Suppress console output (reports still written to file) |
| `-old-style` | | Legacy-style report layout (accepted for compatibility, no visual effect) |

## Version label overrides

| Flag | Alias(es) | Description |
|------|-----------|-------------|
| `-v1 NUM` | `-vnum1`, `-version1` | Override the version label for the old library |
| `-v2 NUM` | `-vnum2`, `-version2` | Override the version label for the new library |

These override what is in the `<version>` element of the XML descriptor.

## Symbol/type filtering

| Flag | Description |
|------|-------------|
| `-skip-symbols PATH` | File with newline-separated symbol names or patterns to suppress (blacklist) |
| `-skip-types PATH` | File with newline-separated type names or patterns to suppress (blacklist) |
| `-symbols-list PATH` | File with symbols to check (whitelist). Only changes on these symbols are reported. |
| `-types-list PATH` | File with types to check (whitelist). Only changes on these types are reported. |
| `-skip-internal-symbols PATTERN` | Regex pattern for internal symbols to skip |
| `-skip-internal-types PATTERN` | Regex pattern for internal types to skip |
| `-keep-cxx` | Include `_ZS*`, `_ZNS*`, `_ZNKS*` (C++ std) mangled symbols (accepted; abicheck includes all exported symbols by default) |
| `-keep-reserved` | Report changes in reserved fields (accepted; abicheck reports all field changes by default) |
| `--suppress PATH` | abicheck-native suppression YAML file (merged with all other filters; supports `label`, `source_location`, `expires`) |

`-skip-symbols` / `-skip-types` file format:
```text
# Lines starting with # are comments
_Z3foov
_ZN3Foo3barEv
# Regex patterns (any of: * ? . [) are matched as full-symbol patterns:
_ZN.*detailEv
```

`-symbols-list` / `-types-list` file format (same syntax):
```text
# Only check these symbols — everything else is suppressed
_Z10public_apiv
_Z12another_funcv
```

## Header filtering

| Flag | Description |
|------|-------------|
| `-headers-list PATH` | File listing specific header files to include in analysis |
| `-header PATH` | Single header file to analyze |
| `-skip-headers PATH` | File listing headers to exclude (accepted, not yet wired) |

## Cross-compilation / toolchain flags

| Flag | Alias(es) | Description |
|------|-----------|-------------|
| `-gcc-path PATH` | `-cross-gcc` | Path to GCC/G++ cross-compiler binary (passed to castxml) |
| `-gcc-prefix PREFIX` | `-cross-prefix` | Cross-toolchain prefix, e.g. `aarch64-linux-gnu-` (builds compiler name as `<prefix>g++`) |
| `-gcc-options FLAGS` | | Extra compiler flags passed through to castxml |
| `-sysroot PATH` | | Alternative system root directory (passed as `--sysroot=` to castxml) |
| `-nostdinc` | | Do not search standard system include paths |
| `-lang LANG` | | Force language: `C` or `C++` (affects header extension and castxml mode) |
| `-arch ARCH` | | Target architecture (informational, recorded in dump metadata) |

## Relpath macros

| Flag | Description |
|------|-------------|
| `-relpath PATH` | Replace `{RELPATH}` macros in both old and new descriptor paths |
| `-relpath1 PATH` | Replace `{RELPATH}` macros in old descriptor paths only |
| `-relpath2 PATH` | Replace `{RELPATH}` macros in new descriptor paths only |

Relpath substitution is an ABICC feature for portable XML descriptors:
```xml
<version>2025.0</version>
<headers>{RELPATH}/include/</headers>
<libs>{RELPATH}/lib/libfoo.so</libs>
```

```bash
abicheck compat check -lib libfoo -old desc.xml -new desc.xml \
  -relpath1 /builds/v1 -relpath2 /builds/v2
```

## Logging flags

| Flag | Description |
|------|-------------|
| `-log-path PATH` | Redirect log output to file |
| `-log1-path PATH` | Separate log path for old library analysis |
| `-log2-path PATH` | Separate log path for new library analysis |
| `-logging-mode MODE` | Logging mode: `w` (overwrite, default), `a` (append), `n` (none) |

## Input filtering flags

| Flag | Description |
|------|-------------|
| `-d` / `-f` / `-filter PATH` | Path to XML descriptor with skip rules (accepted for compatibility) |
| `-p` / `-params PATH` | Path to parameters file (accepted for compatibility) |
| `-app` / `-application PATH` | Application binary for portability checking (accepted for compatibility) |

## `compat dump` flags

The two-stage dump workflow (`abicheck compat dump`) accepts this flag set:

| Flag | Alias(es) | Required | Description |
|------|-----------|:--------:|-------------|
| `-lib NAME` | `-l`, `-library` | ✅ | Library name |
| `-dump PATH` | | ✅ | Path to ABICC XML descriptor |
| `-dump-path PATH` | | | Output dump file path |
| `-dump-format FMT` | | | Only `json` supported |
| `-vnum VERSION` | | | Override version label |
| `-gcc-path` | `-cross-gcc` | | Cross-compiler path |
| `-gcc-prefix` | `-cross-prefix` | | Cross-toolchain prefix |
| `-gcc-options` | | | Extra compiler flags |
| `-sysroot PATH` | | | Alternative system root |
| `-nostdinc` | | | No standard includes |
| `-lang LANG` | | | Force C or C++ |
| `-arch ARCH` | | | Target architecture |
| `-relpath PATH` | | | Relpath macro substitution |
| `-q` | `-quiet` | | Suppress console output |

## Stub flags (accepted for ABICC CLI compatibility, no effect)

These flags are accepted silently to ensure drop-in compatibility with ABICC
CI scripts. They produce a warning when used but do not change behavior:

| Flag | Description |
|------|-------------|
| `-mingw-compatible` | MinGW ABI mode |
| `-cxx-incompatible` / `-cpp-incompatible` | C++ incompatibility mode |
| `-cpp-compatible` | C++ compatibility mode |
| `-static` / `-static-libs` | Static library analysis |
| `-ext` / `-extended` | Extended analysis mode |
| `-quick` | Quick analysis mode |
| `-force` | Force analysis |
| `-check` | Dump validity check |
| `-extra-info DIR` | Extra analysis output directory |
| `-extra-dump` | Extended dump |
| `-sort` | Sort dump output |
| `-xml` | XML dump format |
| `-skip-typedef-uncover` | Skip typedef uncovering |
| `-check-private-abi` | Check private ABI |
| `-skip-unidentified` | Skip unidentified headers |
| `-tolerance LEVEL` | Header parsing tolerance |
| `-tolerant` | Enable all tolerance levels |
| `-disable-constants-check` | Skip constant checking |
| `-skip-added-constants` | Skip new constants |
| `-skip-removed-constants` | Skip removed constants |
| `-count-symbols PATH` | Count symbols in library |
| `-count-all-symbols PATH` | Count all symbols in library |

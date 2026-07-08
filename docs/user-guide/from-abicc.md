# Migrating from ABICC

`abicheck compat check` is a drop-in replacement for `abi-compliance-checker` (ABICC).
It accepts the same single-hyphen flags, reads the same XML descriptors, and produces
compatible exit codes — so you can swap it into existing ABICC pipelines with
a one-line change.

> **Looking for the exhaustive flag list?** Every ABICC flag abicheck recognises —
> functional and stub, with aliases — lives in the
> [ABICC Flag Reference](../reference/abicc-flags.md). This page is the migration
> walkthrough: why migrate, before/after invocations, and behavior differences.

## Migrating from ABICC

### Step 1: Swap the command

Replace the ABICC binary call with `abicheck compat check`. Keep your existing XML descriptors — no changes needed:

```bash
# Before (ABICC):
abi-compliance-checker -lib libfoo -old OLD.xml -new NEW.xml -report-path report.html

# After (abicheck — same flags):
abicheck compat check -lib libfoo -old OLD.xml -new NEW.xml -report-path report.html
```

### Step 2: Update CI exit code checks

| Exit code | ABICC | abicheck compat |
|-----------|-------|-----------------|
| `0` | Compatible | Compatible / no change |
| `1` | Breaking | BREAKING |
| `2` | Error | `API_BREAK` (source-level break) |
| `3`–`11` | — | Non-verdict failures (missing tool, file access, parse error, etc.) |

> Non-verdict failures use extended error codes (`3`–`11`) instead of overloading exit `2`. See [Exit Codes](../reference/exit-codes.md#extended-compat-error-codes-abicc-style) for the full table.
>
> **Note:** In `-strict` mode, `API_BREAK` is promoted to exit `1` (BREAKING).

### Step 3: Validate on historical releases

```bash
for ver in v1.0 v1.1 v1.2; do
  abicheck compat check -lib libfoo -old ${ver}.xml -new current.xml \
    -report-path report-${ver}.html
  echo "vs ${ver}: exit $?"
done
```

### Step 4 (optional): Switch to native mode

When ready, switch from XML descriptors to the simpler native workflow:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
```

Benefits: unambiguous `API_BREAK` verdict, JSON/SARIF output, no XML descriptors needed, exit code `4` = BREAKING (separate from tool errors).

## Common flags for day-to-day use

Most migrations only touch a handful of flags. See the
[ABICC Flag Reference](../reference/abicc-flags.md) for the complete set.

| Flag | Alias(es) | What it does |
|------|-----------|--------------|
| `-lib NAME` | `-l`, `-library` | Library name (required) |
| `-old PATH` | `-d1` | Old version XML descriptor or ABI dump (required) |
| `-new PATH` | `-d2`, `-n` | New version XML descriptor or ABI dump (required) |
| `-report-path PATH` | | Output report path |
| `-report-format FMT` | | `html` (default), `htm`, `xml`, `json`, `md` |
| `-source` | `-src`, `-api` | Source/API compatibility only (see below) |
| `-s` | `-strict` | Any change is BREAKING → exit 1 (see below) |
| `-warn-newsym` | | Treat new symbols as breaks (see below) |
| `-skip-symbols PATH` | | Suppress listed symbols (blacklist) |
| `-symbols-list PATH` | | Only check listed symbols (whitelist) |
| `-q` | `-quiet` | Suppress console output |

## Behavior differences

### `-source` mode: what gets filtered

In `-source` mode, ELF/binary-only changes are removed from the report and verdict:

**Filtered out (binary-only):**
- `SONAME_CHANGED`
- `NEEDED_ADDED` / `NEEDED_REMOVED`
- `RPATH_CHANGED` / `RUNPATH_CHANGED`
- `SYMBOL_BINDING_CHANGED` / `SYMBOL_BINDING_STRENGTHENED`
- `SYMBOL_TYPE_CHANGED` / `SYMBOL_SIZE_CHANGED`
- `IFUNC_INTRODUCED` / `IFUNC_REMOVED`
- `COMMON_SYMBOL_RISK`
- `SYMBOL_VERSION_DEFINED_REMOVED` / `SYMBOL_VERSION_REQUIRED_*`
- `DWARF_INFO_MISSING`
- `TOOLCHAIN_FLAG_DRIFT`

**Retained (source/API breaks):**
- `FUNC_PARAMS_CHANGED`, `FUNC_RETURN_CHANGED`
- `FUNC_NOEXCEPT_ADDED` / `FUNC_NOEXCEPT_REMOVED`
- `FUNC_DELETED`
- `TYPE_FIELD_REMOVED` / `TYPE_FIELD_TYPE_CHANGED`
- `TYPE_REMOVED` / `TYPE_BECAME_OPAQUE`
- `TYPEDEF_REMOVED` / `TYPEDEF_BASE_CHANGED`
- `ENUM_MEMBER_REMOVED` / `ENUM_MEMBER_VALUE_CHANGED` / `ENUM_MEMBER_ADDED`

### `-strict` mode

Without `-strict`:
- `COMPATIBLE` changes → exit 0
- `API_BREAK` → exit 2
- `BREAKING` → exit 1

With `-strict`:
- `NO_CHANGE` → exit 0
- Anything else (`COMPATIBLE`, `API_BREAK`, `BREAKING`) → exit 1

Matches ABICC's `-strict` semantics: any deviation from the old ABI is an error.

### `-warn-newsym` mode

Without `-warn-newsym`:
- New symbols (`FUNC_ADDED`, `VAR_ADDED`) are COMPATIBLE → exit 0

With `-warn-newsym`:
- New symbols promote verdict to BREAKING → exit 1

Useful for strict CI pipelines that need to flag any ABI surface change.

### Detector coverage vs ABICC

`abicheck compat check` mode uses **all abicheck detectors** — it does not emulate
ABICC's blind spots. This means abicheck may report issues that ABICC would miss:

| Scenario | ABICC | abicheck compat |
|----------|:-----:|:---------------:|
| Enum value changed | ✅ | ✅ |
| Base class position reordered | ✅ | ✅ |
| Function `= delete` added | ✅ | ✅ |
| Global var became const | ❌ | ✅ |
| Type became opaque | ✅ | ✅ |
| C++ templates (timeout) | ⏱️ | ✅ |
| ELF symbol metadata | ❌ | ✅ |

Full coverage comparison: see [gap_report.md](../development/abicc-parity-status.md).

## XML descriptor format

Same format as ABICC:

```xml
<version>2025.0</version>
<headers>/path/to/include/</headers>
<libs>/path/to/libfoo.so</libs>
```

Multiple `<headers>` and `<libs>` entries are supported. If multiple `<libs>` are
provided, only the first is used (with a warning). The `{RELPATH}` macro is supported
for portable descriptors (see the [ABICC Flag Reference](../reference/abicc-flags.md#relpath-macros)).

## ABI dump workflow

abicheck supports a two-stage workflow: dump first, compare later. This is
useful for CI pipelines that build versions at different times. See the
[`compat dump` flags](../reference/abicc-flags.md#compat-dump-flags) for the full flag set.

```bash
# Create an ABI dump from an XML descriptor (default output:
# abi_dumps/<lib>/<version>/dump.json):
abicheck compat dump -lib libfoo -dump v1.xml

# With explicit output path and version label:
abicheck compat dump -lib libfoo -dump v1.xml -dump-path libfoo-v1.json -vnum 2025.1

# Cross-compilation:
abicheck compat dump -lib libfoo -dump v1.xml -gcc-prefix aarch64-linux-gnu- -sysroot /opt/cross/sysroot
```

JSON dumps can be passed directly to `compat` (auto-detected by `.json` extension)
or to the native `compare` command:

```bash
# Via compat mode (ABICC-style exit codes):
abicheck compat check -lib libfoo -old libfoo-v1.json -new libfoo-v2.json

# Via native compare (abicheck exit codes):
abicheck compare libfoo-v1.json libfoo-v2.json --format html -o report.html
```

**Dump format support:** abicheck reads native **JSON** dumps and minimal ABICC Perl
`Data::Dumper` (`ABI.dump`) input for migration workflows. ABICC XML dump variants
(`<ABI_dump...>` / `<abi_dump...>`) are still unsupported — regenerate them from a
descriptor via `compat dump` instead.

## CI cross-validation

To validate abicheck produces correct results for your CI pipeline:

```bash
# 1. Run both tools on the same inputs
abi-compliance-checker -lib libfoo -old old.xml -new new.xml; ABICC_EXIT=$?
abicheck compat check -lib libfoo -old old.xml -new new.xml; ABICHECK_EXIT=$?

# 2. Compare exit codes
test $ABICC_EXIT -eq $ABICHECK_EXIT && echo "PASS" || echo "FAIL: $ABICC_EXIT vs $ABICHECK_EXIT"
```

## Examples

```bash
# Basic comparison
abicheck compat check -lib mylib -old v1.xml -new v2.xml

# Strict: any change fails CI
abicheck compat check -lib mylib -old v1.xml -new v2.xml -s

# Source/API compat only (ignore ELF metadata changes)
abicheck compat check -lib mylib -old v1.xml -new v2.xml -source

# Skip known-breaking symbols (blacklist)
echo "_Z14legacy_internalv" > skip.txt
abicheck compat check -lib mylib -old v1.xml -new v2.xml -skip-symbols skip.txt

# Whitelist: only check public API symbols
abicheck compat check -lib mylib -old v1.xml -new v2.xml -symbols-list public_api.txt

# JSON output
abicheck compat check -lib mylib -old v1.xml -new v2.xml -report-format json

# Two-stage workflow: dump then compare
abicheck compat dump -lib mylib -dump v1.xml
abicheck compat dump -lib mylib -dump v2.xml
abicheck compat check -lib mylib -old abi_dumps/mylib/1.0/dump.json -new abi_dumps/mylib/2.0/dump.json
```

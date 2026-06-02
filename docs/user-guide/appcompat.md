# Application Compatibility Check

The `abicheck appcompat` command answers: **"Will my application still work with the new library version?"**

Unlike `compare` (which reports all library changes), `appcompat` filters the diff to show only changes that affect the specific application binary you provide. This is the application-centric view of ABI compatibility.

---

## When to use `appcompat`

| Scenario | Command |
|----------|---------|
| Library maintainer checking all ABI changes | `abicheck compare` |
| App developer checking if *their app* is affected | `abicheck appcompat` |
| Distro packager checking if app X works with new libfoo | `abicheck appcompat` |
| Quick symbol availability check (no old library) | `abicheck appcompat <app-binary> --check-against <target-lib>` |

---

## Full mode (old + new library)

Provide the application binary, old library, and new library:

```bash
abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2
```

With headers for deeper analysis:

```bash
abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2 \
  -H include/foo.h
```

This will:

1. Parse the application binary to extract required symbols
2. Run a full library comparison (same as `compare`)
3. Check symbol availability in the new library
4. Filter changes to show only those affecting the application
5. Compute an app-specific verdict

### Example output

```text
# Application Compatibility Report

**Application:** `./myapp`
**Library:** `libfoo.so.1` → `libfoo.so.2`
**Verdict:** ✅ `COMPATIBLE`

## Symbol Coverage

App requires **12** library symbols.
All 12 required symbols present in new version (100% coverage).

## Relevant Changes (1 of 7 total)

These library changes affect symbols your application uses:

| Kind | Symbol | Description |
|------|--------|-------------|
| `func_params_changed` | `foo_process` | parameter type changed |

_6 library ABI change(s) do NOT affect your application. Use `--show-irrelevant` to see them._
```

---

## Weak mode (symbol availability only)

When you don't have the old library — just check if the new library provides everything the application needs:

```bash
abicheck appcompat ./myapp --check-against libfoo.so.2
```

Weak mode checks:

- All symbols the application imports from the library are present
- All required ELF symbol versions are defined

It does **not** compare old vs. new (no diff, no change detection).

---

## List required symbols

Diagnose what symbols your application imports from a library:

```bash
abicheck appcompat ./myapp --list-required-symbols --check-against libfoo.so.2
```

```text
Application: ./myapp
Library filter: libfoo.so.1
Needed libraries: libfoo.so.1, libc.so.6
Required symbols (3):
  foo_cleanup
  foo_init
  foo_process
Required versions (1):
  FOO_1.0 (from libfoo.so.1)
```

JSON output:

```bash
abicheck appcompat ./myapp --list-required-symbols --check-against libfoo.so.2 --format json
```

---

## Options reference

| Option | Description |
|--------|-------------|
| `APP` | Path to application binary (ELF, PE, or Mach-O) |
| `OLD_LIB` | Path to old library version |
| `NEW_LIB` | Path to new library version |
| `--check-against LIB` | Weak mode: check symbol availability only (no old library needed) |
| `-H` / `--header` | Public header file or directory (for full mode) |
| `-I` / `--include` | Extra include directory for castxml |
| `--lang` | Language mode: `c++` (default) or `c` |
| `--format` | Output format: `markdown` (default) or `json` |
| `-o` / `--output` | Write report to file |
| `--show-irrelevant` | Include library changes that don't affect the application |
| `--list-required-symbols` | List symbols the application requires and exit |
| `--scope-public-headers` / `--no-scope-public-headers` | Restrict findings to the public-header ABI surface (on by default; matches `compare`) |
| `--severity-preset` | `default`, `strict`, or `info-only` (switches to the severity-aware exit scheme — full mode only) |
| `--severity-abi-breaking` / `--severity-potential-breaking` / `--severity-quality-issues` / `--severity-addition` | Per-category severity overrides (`error`/`warning`/`info`; full mode only) |
| `--suppress` | Suppression file (YAML) |
| `--policy` | Verdict policy: `strict_abi` (default), `sdk_vendor`, `plugin_abi` |
| `--policy-file` | Custom YAML policy overrides |
| `-v` / `--verbose` | Debug output |

---

## Exit codes

`appcompat` uses the same exit codes as `compare`:

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `COMPATIBLE` / `NO_CHANGE` | Application is safe with the new library |
| `1` | `TOOL_ERROR` | Operational or runtime error (tool failure, invalid input, or unexpected exception) |
| `2` | `API_BREAK` | Source-level break affecting app's symbols |
| `4` | `BREAKING` | Binary ABI break or missing symbols |

### Severity-aware exit codes

Passing any `--severity-*` option switches `appcompat` to the same severity-aware
exit scheme as `compare`, classifying the **app-relevant** changes (the ones that
affect the application, not the whole library diff) by category. Two caveats:

- It applies in **full mode only** (with `OLD_LIB NEW_LIB`). Weak mode
  (`--check-against`) has no extracted library ABI, so it always uses the
  verdict-based exit codes above.
- Missing required symbols/versions are treated as a hard runtime break and
  always floor the exit code at `4`, even under `--severity-preset info-only` —
  an application that can't resolve a symbol it needs is not something a severity
  downgrade should hide.

---

## How symbol filtering works

The application binary is parsed to extract:

- **Imported symbols** — undefined symbols in `.dynsym` (ELF), import table (PE), or symbol table (Mach-O)
- **Library filter** — only symbols imported from the target library are considered (using ELF `.gnu.version_r`, PE DLL name, or Mach-O two-level namespace)
- **Required versions** — ELF version tags from `.gnu.version_r`

A library change is **relevant** to the application if any of these conditions hold:

1. The change's symbol is in the app's imported symbol set
2. The change's `affected_symbols` overlap with the app's imports (type change propagation)
3. The change is `SONAME_CHANGED` (affects all consumers)
4. The change is `COMPAT_VERSION_CHANGED` (Mach-O, affects all consumers)
5. The change is `SYMBOL_VERSION_DEFINED_REMOVED` for a version the app requires

All other changes are classified as **irrelevant** — the library changed, but the application doesn't use the affected symbols.

---

## Supported binary formats

| Format | Application | Library | Symbol filtering |
|--------|------------|---------|-----------------|
| **ELF** (Linux) | `.so`, executables | `.so` | `.gnu.version` + `.gnu.version_r` correlation |
| **PE** (Windows) | `.exe`, `.dll` | `.dll` | Import table DLL name matching (incl. ordinal imports) |
| **Mach-O** (macOS) | executables, `.dylib` | `.dylib` | Two-level namespace library ordinal |

---

## CI integration

### GitHub Actions example

Check if your application works with a library update in CI:

```yaml
- name: Check app compatibility
  run: |
    abicheck appcompat ./build/myapp \
      libfoo.so.1 ./build/libfoo.so.2 \
      -H include/foo.h \
      --format json -o appcompat.json
```

### Weak mode in CI (no old library)

Quick check that a library provides all symbols an application needs:

```yaml
- name: Check symbol availability
  run: |
    abicheck appcompat ./build/myapp \
      --check-against ./build/libfoo.so
```

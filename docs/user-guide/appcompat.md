# Application Compatibility Check

`compare --used-by APP` answers: **"Will my application still work with the new library version?"**

Unlike a plain `compare` (which reports all library changes), `--used-by`
scopes the diff to show only changes that affect the specific application
binary you provide. This is the application-centric view of ABI
compatibility.

> **History note:** this used to be a standalone `abicheck appcompat`
> command. The pre-1.0 CLI reset folded it into `compare --used-by` (ADR-043)
> — the full library comparison runs once, and the worst app-scoped result
> becomes the primary verdict/exit code, with the full-library verdict and
> unrelated changes kept as informational context. `OLD_INPUT`/`NEW_INPUT`
> must be real library binaries (not JSON snapshots) when `--used-by` is
> used, since the app's imports have to be resolved against the actual
> library files.

---

## When to use `--used-by`

| Scenario | Command |
|----------|---------|
| Library maintainer checking all ABI changes | `abicheck compare` |
| App developer checking if *their app* is affected | `abicheck compare --used-by ./myapp` |
| Distro packager checking if app X works with new libfoo | `abicheck compare --used-by ./appX` |

---

## Full mode (old + new library)

Provide the old library, the new library, and the application binary via `--used-by`:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp
```

With headers for deeper analysis:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp \
  -H include/foo.h
```

`--used-by` is repeatable, so one comparison can be scoped to several
consumer applications at once:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --used-by ./myapp --used-by ./otherapp -H include/foo.h
```

This will:

1. Parse each application binary to extract required symbols
2. Run the full library comparison (same as plain `compare`)
3. Check symbol availability in the new library
4. Filter changes to show only those affecting each application
5. Compute an app-specific verdict per `--used-by` app, and fold the worst
   one into the run's primary verdict/exit code

### Example output

```text
# Comparison Report

**Library:** `libfoo.so.1` → `libfoo.so.2`
**Verdict:** `BREAKING` (worst of: full-library `COMPATIBLE_WITH_RISK`, `./myapp` `BREAKING`)

## used_by: ./myapp

App requires **12** library symbols.
11 of 12 required symbols present in new version.

### Relevant Changes (1 of 7 total)

These library changes affect symbols this application uses:

| Kind | Symbol | Description |
|------|--------|-------------|
| `func_params_changed` | `foo_process` | parameter type changed |

_6 library ABI change(s) do NOT affect this application._
```

(Exact rendering depends on `--format`; see `abicheck compare --help` for the
full output-format list.)

---

## What's no longer directly available

Two pieces of the old standalone `appcompat` command don't have a CLI
replacement after the ADR-043 reset — both were narrower diagnostic modes
that didn't fit the unified `compare` surface:

- **Weak mode** (`appcompat APP --check-against LIB`, checking symbol
  availability with no old library at all — no diff, no change detection) —
  no CLI replacement. The underlying logic still exists as
  `abicheck.appcompat.check_against()` for Python API use.
- **`--list-required-symbols`** (dump the app's imported symbols/versions and
  exit) — no CLI replacement. Use `abicheck.appcompat.parse_app_requirements()`
  from the Python API to get the same `AppRequirements` data (imported
  symbols, needed libraries, required ELF symbol versions) programmatically.

If you relied on either of these in a script, the closest CLI-only fallback
is `abicheck deps tree ./myapp` (see [Companion Commands](companion-commands.md)),
which reports whether the application's dependencies resolve and its
required symbols bind — a different, broader check (whole dependency stack,
not one candidate library file) but often enough to catch the same class of
problem in CI.

---

## Options reference

| Option | Description |
|--------|-------------|
| `OLD_INPUT` / `NEW_INPUT` | Old and new library (`.so`/`.dll`/`.dylib`, JSON snapshot, or ABICC dump) — same as plain `compare`. Must be real library binaries, not snapshots, when `--used-by` is given. |
| `--used-by FILE` | Application binary whose imports/required symbol versions scope the comparison (repeatable). Mutually exclusive with `--required-symbol`/`--required-symbols`. |
| `-H` / `--header` | Public header file or directory (repeatable, side-aware with `old=`/`new=`) |
| `-I` / `--include` | Extra include directory for castxml (repeatable, side-aware) |
| `--lang` | Language mode: `c++` (default) or `c` |
| `--format` | Output format: `markdown` (default), `json`, `sarif`, `html`, `junit`, `review` |
| `-o` / `--output` | Write report to file |
| `--scope-public-headers` / `--no-scope-public-headers` | Restrict findings to the public-header ABI surface (on by default) |
| `--severity-preset` | `default`, `strict`, or `info-only` (switches to the severity-aware exit scheme) |
| `--severity-abi-breaking` / `--severity-potential-breaking` / `--severity-quality-issues` / `--severity-addition` | Per-category severity overrides (`error`/`warning`/`info`) |
| `--suppress` | Suppression file (YAML) |
| `--policy` | Verdict policy: `strict_abi` (default), `sdk_vendor`, `plugin_abi` |
| `--policy-file` | Custom YAML policy overrides |
| `-v` / `--verbose` | Debug output |

See `abicheck compare --help` for the complete flag set — `--used-by` is one
option among the full `compare` surface, not a separate command with its own
flags.

---

## Exit codes

`compare --used-by` uses the same exit codes as plain `compare`, computed
from the worst of the full-library verdict and every `--used-by` app's
scoped verdict:

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `COMPATIBLE` / `NO_CHANGE` | Application(s) safe with the new library |
| `2` | `API_BREAK` | Source-level break affecting an app's symbols |
| `4` | `BREAKING` | Binary ABI break or missing symbols |
| `64` | usage error | Bad arguments/invocation |

### Severity-aware exit codes

Passing any `--severity-*` option switches to the same severity-aware exit
scheme as plain `compare` (see [Exit Codes](../reference/exit-codes.md)),
classifying the **app-relevant** changes (the ones that affect an app, not
the whole library diff) by category. Missing required symbols/versions are
still treated as a hard runtime break and always floor the exit code at `4`,
even under `--severity-preset info-only` — an application that can't resolve
a symbol it needs is not something a severity downgrade should hide.

---

## How symbol filtering works

Each `--used-by` application binary is parsed to extract:

- **Imported symbols** — undefined symbols in `.dynsym` (ELF), import table (PE), or symbol table (Mach-O)
- **Library filter** — only symbols imported from the target library are considered (using ELF `.gnu.version_r`, PE DLL name, or Mach-O two-level namespace)
- **Required versions** — ELF version tags from `.gnu.version_r`

A library change is **relevant** to an app if any of these conditions hold:

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
    abicheck compare libfoo.so.1 ./build/libfoo.so.2 \
      --used-by ./build/myapp \
      -H include/foo.h \
      --format json -o appcompat.json
```

---

## Python API

```python
from pathlib import Path
from abicheck.appcompat import check_appcompat, check_against, parse_app_requirements

# Full mode (old + new library) — app_path, old_lib_path, new_lib_path
result = check_appcompat(
    Path("./myapp"), Path("libfoo.so.1"), Path("libfoo.so.2"),
)
print(result.verdict, result.symbol_coverage)

# Weak mode (no old library — symbol availability only)
weak = check_against(Path("./myapp"), Path("libfoo.so.2"))
print(weak.missing_symbols)

# List required symbols only (library_name filters which needed-lib's
# imports are reported, e.g. the SONAME)
reqs = parse_app_requirements(Path("./myapp"), "libfoo.so.1")
print(reqs.undefined_symbols, reqs.needed_libs, reqs.required_versions)
```

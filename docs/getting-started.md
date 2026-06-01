# Getting Started

**abicheck** compares two versions of a C/C++ shared library and tells you whether existing binaries will break. It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries.

On all platforms it provides binary metadata analysis (exports, imports, dependencies) and header AST analysis (via castxml). Debug info cross-check uses DWARF (Linux, macOS) and PDB (Windows).

> **Platforms:** Linux, Windows, macOS.

---

## Which command do I need?

abicheck ships several commands. Pick the one that matches your question:

| Your question | Command | See |
|---------------|---------|-----|
| Does upgrading this library break existing consumers? | `abicheck compare` | [§2 below](#2-first-check-using-repo-examples) |
| Does **my application** still work with the new library version? | `abicheck appcompat` | [§5 below](#5-application-compatibility-check) |
| Will this binary load and run correctly in this sysroot? | `abicheck stack-check` | [CLI Usage](user-guide/cli-usage.md) |
| Does my library dependency tree resolve without unresolved symbols? | `abicheck deps` | [CLI Usage](user-guide/cli-usage.md) |
| I'm migrating from `abi-compliance-checker` and want the same flags. | `abicheck compat` | [Migrating from ABICC](user-guide/from-abicc.md) |
| Save a reusable ABI baseline for CI. | `abicheck dump` | [§4 below](#4-snapshot-workflow-for-ci-baselines) |

If you're unsure, start with `abicheck compare` — it's the default workflow.

---

## 1) Install abicheck

```bash
pip install abicheck
# or
conda install -c conda-forge abicheck
```

### Requirements

- Python 3.10+
- `castxml` + a C/C++ compiler — **required for header AST analysis** (all platforms)

All Python dependencies (`pyelftools`, `pefile`, `macholib`) come with the `abicheck` install.

> **Important:** `pip install abicheck` does **not** install `castxml`. Any command
> that takes headers (`--old-header` / `--new-header` / `-H`) needs `castxml` on
> your `PATH` — without it those commands fail with `castxml not found`. Install it
> with the system/conda packages below (the conda-forge package pulls it in
> automatically). If you have no `castxml`, run **binary-only mode** by omitting the
> header flags — abicheck falls back to DWARF/symbols analysis (weaker, but works).

#### Option A: system packages

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y castxml gcc g++
```

```bash
# macOS
brew install castxml
# plus Xcode Command Line Tools for clang
```

```powershell
# Windows (PowerShell, as administrator)
choco install castxml
# plus MSVC Build Tools (cl.exe) for PE/PDB debug-info analysis
```

#### Option B: conda-forge (recommended for reproducible envs)

```bash
# create env and install abicheck (recipe includes required analysis deps)
# Python >= 3.10 is required; any supported version works
conda create -n abicheck -c conda-forge python=3.12 abicheck
conda activate abicheck
```

No extra manual dependency installation is required when using the conda-forge package.

### Install from source

```bash
git clone https://github.com/napetrov/abicheck.git
cd abicheck
pip install -e .
```

---

## 2) First check (using repo examples)

The repo includes 121 ABI scenario examples. Most are single-library cases with
paired `v1`/`v2` sources and headers; bundle/release-level cases use
release-style layouts.
Browse the generated single-library pages in the
[Examples & Case Encyclopedia](examples/index.md), or pick one and run it locally:

```bash
cd examples/case01_symbol_removal
```

```bash
# Build v1 and v2 shared libraries
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
```

```bash
# Compare (header-aware — needs castxml; see Requirements above)
abicheck compare libv1.so libv2.so --old-header v1.h --new-header v2.h
# Verdict: BREAKING (symbol 'helper' was removed)
```

> **No `castxml`?** The command above will fail with `castxml not found`. Either
> install castxml (see [Requirements](#requirements)), or run the same comparison
> in binary-only mode by dropping the header flags — it still catches the removed
> symbol from the ELF/DWARF metadata:
>
> ```bash
> abicheck compare libv1.so libv2.so   # binary-only fallback, no castxml needed
> ```

For your own library:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header include/v1/foo.h --new-header include/v2/foo.h
```

If the header is the same for both versions:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
```

You can also pass a header **directory** (recursive scan for `*.h`, `*.hpp`, ...):

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H include/
```

If no headers are provided for ELF inputs, abicheck falls back to **symbols-only** mode
and prints a warning (weaker analysis: may miss type/signature ABI breaks).

---

## 3) Output formats

abicheck supports four output formats: `markdown` (default), `json`, `sarif`, `html`.

Markdown (default, printed to stdout):

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h
```

JSON — machine-readable, includes precise verdict field:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h --format json -o result.json
```

SARIF — for GitHub Code Scanning:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h --format sarif -o abi.sarif
```

HTML — standalone human-readable report:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h --format html -o report.html
```

---

## 4) Snapshot workflow (for CI baselines)

Save a snapshot once per release, then compare against new builds without re-dumping:

```bash
# Save baseline (header is baked into the snapshot)
abicheck dump libfoo.so -H include/foo.h --version 1.0 -o baseline.json
```

```bash
# Compare saved baseline against current build
abicheck compare baseline.json ./build/libfoo.so \
  --new-header include/foo.h --new-version 2.0-dev
```

```bash
# Or compare two snapshots (no headers needed — already baked in)
abicheck compare old.json new.json
```

`compare` auto-detects each input: `.so` files are dumped on-the-fly, `.json` snapshots are loaded directly. You can mix them freely.

### Language mode

Use `--lang c` for pure C libraries (default is `c++`):

```bash
abicheck dump libfoo.so -H foo.h --lang c -o snap.json
```

### Cross-compilation

When analysing libraries built for a different architecture:

```bash
abicheck dump libfoo.so -H include/foo.h \
  --gcc-prefix aarch64-linux-gnu- \
  --sysroot /opt/sysroots/aarch64 \
  -o snap.json
```

Available flags: `--gcc-path`, `--gcc-prefix`, `--gcc-options`, `--sysroot`, `--nostdinc`.

### Verbose output

```bash
abicheck compare old.json new.json -v
```

---

## 5) Application compatibility check

Check whether your **application** is affected by a library update — filtering out irrelevant changes:

```bash
abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2 -H include/foo.h
```

This parses your application binary to find which library symbols it actually uses, then shows only the changes that matter. If the library removed a function your app never calls, it won't appear in the report.

Quick symbol availability check (no old library needed):

```bash
abicheck appcompat ./myapp --check-against libfoo.so.2
```

See [Application Compatibility](user-guide/appcompat.md) for the full reference.

---

## 6) Exit codes and CI

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `1` | — | Tool/runtime error |
| `2` | `API_BREAK` | Source-level API break (binary still works) |
| `4` | `BREAKING` | Binary ABI break |

Full reference (including `compat` mode): [Exit Codes](reference/exit-codes.md)

### GitHub Actions example

Save a baseline once at release time, then compare every new build:

```bash
# Release step — save baseline as an artifact
abicheck dump ./build/libfoo.so -H include/foo.h \
  --version 1.0 -o abi-baseline.json
# Upload abi-baseline.json as a release artifact
```

```yaml
# CI step — compare new build against saved baseline
steps:
  - name: Download ABI baseline
    uses: actions/download-artifact@v4
    with:
      name: abi-baseline

  - name: Compare ABI
    run: |
      abicheck compare abi-baseline.json ./build/libfoo.so \
        --new-header include/foo.h \
        --format sarif -o abi.sarif

  - uses: github/codeql-action/upload-sarif@v3
    if: always()
    with:
      sarif_file: abi.sarif
```

---

## Next steps

- [Verdicts](concepts/verdicts.md) — what each verdict means
- [Policy Profiles](user-guide/policies.md) — control how changes are classified
- [Examples & Breakage Guide](concepts/abi-breaks-explained.md) — real-world ABI/API break scenarios
- [ABICC Compatibility](user-guide/from-abicc.md) — migrating from abi-compliance-checker
- [Limitations](concepts/limitations.md)

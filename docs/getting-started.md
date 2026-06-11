# Getting Started

**abicheck** compares two versions of a C/C++ shared library and tells you whether existing binaries will break. It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries.

On all platforms it provides binary metadata analysis (exports, imports, dependencies) and header AST analysis (via castxml). Debug info cross-check uses DWARF (Linux, macOS) and PDB (Windows).

> **Platforms:** Linux, Windows, macOS.

> **In CI already?** Skip straight to the [GitHub Action](user-guide/github-action.md)
> — it installs everything and runs the check in a few lines of YAML.

> **Not sure which command or options fit your situation?** Jump to
> [**Choose Your Workflow**](user-guide/choose-your-workflow.md) — a decision
> guide that maps your artifacts (one library, a release bundle, a package, an
> application, stripped binaries…) and your CI policy to the exact command to run.

---

## What question are you asking?

abicheck answers three plain-language questions. Pick yours:

| Your question | Start here |
|---|---|
| **Did my library break?** | [`abicheck compare`](#3-first-check-using-repo-examples) |
| **Does my app still work?** | [`abicheck appcompat`](#6-application-compatibility-check) |
| **Did my whole package / release break?** | [`abicheck compare-release`](user-guide/multi-binary.md) |

For the full decision matrix — every artifact layout, accuracy tier, and CI
policy — see [Choose Your Workflow](user-guide/choose-your-workflow.md).

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

## 2) Which command do I need?

abicheck ships several commands. Pick the one that matches your question:

| Your question | Command | See |
|---------------|---------|-----|
| Does upgrading this library break existing consumers? | `abicheck compare` | [§3 below](#3-first-check-using-repo-examples) |
| Does **my application** still work with the new library version? | `abicheck appcompat` | [§6 below](#6-application-compatibility-check) |
| Will this binary load and run correctly in this sysroot? | `abicheck stack-check` | [CLI Usage](user-guide/cli-usage.md) |
| Does my library dependency tree resolve without unresolved symbols? | `abicheck deps` | [CLI Usage](user-guide/cli-usage.md) |
| I'm migrating from `abi-compliance-checker` and want the same flags. | `abicheck compat` | [Migrating from ABICC](user-guide/from-abicc.md) |
| Save a reusable ABI baseline for CI. | `abicheck dump` | [§5 below](#5-snapshot-workflow-for-ci-baselines) |

If you're unsure, start with `abicheck compare` — it's the default workflow.

---

## 3) First check (using repo examples)

**Best first run:** compare two shared libraries with their public headers — it
gives abicheck the most evidence to work with (see the
[input-quality ladder](#input-quality-what-each-tier-catches) below).

The repo includes 127 ABI scenario examples. Most are single-library cases with
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

### Input quality: what each tier catches

How much abicheck can *prove* depends on what you give it. More evidence catches
more breaks — start at the tier your artifacts allow and add headers + debug info
when you need more confidence:

| Inputs | Confidence | What it catches |
|---|---|---|
| Binaries only | **Low** | Symbol add/remove, basic metadata |
| Binaries + debug info | **Medium** | Layout, enum, calling convention, emitted ABI |
| Binaries + headers | **High** | Public API surface, source-level API, inline/template surface |
| Binaries + debug info + headers + build flags | **Best** | The most accurate practical setup |

This is why the header flags matter. The
[ABI/API Handling overview](concepts/abi-api-handling.md) explains the full
picture: debug info **plus** headers is the highest-coverage setup, while
stripped binaries without headers only give symbol-level coverage. For stripped
production builds, point abicheck at separate debug files (`--debug-root1/2`) or
fetch them with `--debuginfod` — see
[CLI Usage](user-guide/cli-usage.md#debug-artifact-resolution).

---

## 4) Output formats

abicheck supports five output formats: `markdown` (default), `json`, `sarif`, `html`, and `junit` (plus a compact `review` digest). See [Output Formats](user-guide/output-formats.md) for the full reference.

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

## 5) Snapshot workflow (for CI baselines)

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

## 6) Application compatibility check

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

## 7) Exit codes and CI

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `1` | — | Tool/runtime error |
| `2` | `API_BREAK` | Source-level API break (binary still works) |
| `4` | `BREAKING` | Binary ABI break |

Full reference (including `compat` mode): [Exit Codes](reference/exit-codes.md)

### Policy recipes — what should fail the build?

abicheck separates *what fails CI* (severity → exit code) from *what shows up in
the report* (display filtering). These three recipes cover the common cases; the
[Choose Your Workflow → policy recipes](user-guide/choose-your-workflow.md#4-how-should-ci-behave-policy-recipes)
and [Severity Configuration](user-guide/severity.md) pages have the rest.

```bash
# Breakage-only gate: report everything, fail ONLY on binary ABI breaks
abicheck compare baseline.json build/libfoo.so \
  --new-header include/ \
  --severity-preset info-only \
  --severity-abi-breaking error

# Strict API-surface governance: also fail on new public ABI/API additions
abicheck compare baseline.json build/libfoo.so \
  --new-header include/ \
  --severity-addition error

# Show only additions in a review report — verdict and exit code unchanged
abicheck compare baseline.json build/libfoo.so \
  --new-header include/ \
  --show-only compatible,added
```

The first maps to "just alert me on breakages"; the second to "fail when new
public ABI/API appears." The third is **display-only** — `--show-only` filters
what the report renders without changing the verdict or exit code.

### GitHub Actions — the easy way

The fastest way to gate ABI in CI is the **first-class
[GitHub Action](user-guide/github-action.md)**. It installs Python, `castxml`,
and abicheck for you, runs the comparison, sets the step exit code, and can
upload SARIF — all in a few lines of YAML:

```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library: abi-baseline.json   # committed or downloaded baseline
    new-library: build/libfoo.so
    new-header: include/foo.h
    upload-sarif: true
```

See the [GitHub Action reference](user-guide/github-action.md) for every input,
baseline workflows, package/`compare-release` mode, and multi-platform matrices.

### GitHub Actions — raw CLI

If you prefer to drive the CLI directly, save a baseline once at release time,
then compare every new build:

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

**Find your workflow:** [Choose Your Workflow](user-guide/choose-your-workflow.md)
maps your artifacts and CI policy to the exact command. Or jump straight to your
persona:

- **Library maintainer** → [Verdicts](concepts/verdicts.md), [Policy Profiles](user-guide/policies.md)
- **App developer** → [Application Compatibility](user-guide/appcompat.md)
- **SDK / package maintainer** → [Multi-Binary Releases](user-guide/multi-binary.md), [Baseline Management](user-guide/baseline-management.md)
- **CI owner** → [GitHub Action](user-guide/github-action.md), [Severity Configuration](user-guide/severity.md), [Output Formats](user-guide/output-formats.md)
- **Plugin author** → [Plugin Systems](user-guide/plugin-systems.md)
- **Distro / package maintainer** → [Multi-Binary Releases](user-guide/multi-binary.md)
- **Migrating from ABICC / libabigail** → [from ABICC](user-guide/from-abicc.md), [from libabigail](user-guide/from-libabigail.md)

Background reading:

- [ABI/API Handling & Recommendations](concepts/abi-api-handling.md) — real-world ABI/API break scenarios and how to prevent them
- [Limitations](concepts/limitations.md) — what abicheck does *not* catch

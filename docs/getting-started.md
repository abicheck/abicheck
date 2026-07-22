---
doc_type: tutorial
audience:
  - library-maintainer
level: beginner
summarizes:
  - evidence-model
  - verdicts
lifecycle: active
generated: false
---

# Getting Started

**abicheck** compares two versions of a C/C++ shared library and tells you whether existing binaries will break. It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries.

On all platforms it provides binary metadata analysis (exports, imports, dependencies) and header AST analysis (via castxml). Debug info cross-check uses DWARF (Linux, macOS) and PDB (Windows).

> **In CI already?** Skip straight to the [GitHub Action](user-guide/github-action.md)
> — it installs everything and runs the check in a few lines of YAML.

---

## What question are you asking?

abicheck ships several commands; pick the one that matches your question. If
you're unsure, start with `abicheck compare` — it's the default workflow.

| Your question | Command | See |
|---------------|---------|-----|
| **Did my library break?** — does upgrading it break existing consumers? | `abicheck compare` | [§2 below](#2-first-check-using-repo-examples) |
| **Does my application still work** with the new library version? | `abicheck compare --used-by` | [§5 below](#5-application-compatibility-check) |
| **Did my whole package / release break?** | `abicheck compare` | [Multi-Binary Releases](user-guide/multi-binary.md) |
| **Gate a pull request** with the deepest evidence available (headers + build + sources)? | `abicheck scan` | [Source-Scan Depth](user-guide/scan-levels.md) |
| Will this binary load and resolve correctly in this sysroot — and does its dependency tree have unresolved symbols? | `abicheck deps tree` (`--sysroot /rootfs` for a specific root) | [CLI Usage](user-guide/cli-usage.md) |
| Did anything in the dependency stack change between two sysroots / images? | `abicheck deps compare --old-root … --new-root …` | [CLI Usage](user-guide/cli-usage.md) |
| I'm migrating from `abi-compliance-checker` and want the same flags. | `abicheck compat` | [Migrating from ABICC](user-guide/from-abicc.md) |
| Save a reusable ABI baseline for CI. | `abicheck dump` | [§4 below](#4-snapshot-workflow-for-ci-baselines) |

For the full decision matrix — every artifact layout, accuracy tier, and CI
policy — see [**Choose Your Workflow**](user-guide/choose-your-workflow.md).

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
> that takes headers (`--header` / `-H`) needs `castxml` on
> your `PATH` — without it those commands fail with `castxml not found`. Install it
> with the system/conda packages below (the conda-forge package pulls it in
> automatically). If you have no `castxml`, run **binary-only mode** by omitting the
> header flags — abicheck falls back to DWARF/symbols analysis (weaker, but works).

#### Option A: conda-forge (recommended)

Conda-forge supplies CastXML together with a compatible compiler toolchain:

```bash
conda install -c conda-forge castxml
```

#### Option B: pinned CastXML Superbuild (Ubuntu CI/reproducers)

Ubuntu 24.04's `apt` package currently bundles Clang 17, which cannot parse
some GCC 13 libstdc++ headers. For reproducible Ubuntu runs, use a
[CastXML Superbuild release](https://github.com/CastXML/CastXMLSuperbuild/releases),
pin its tag and SHA256, extract it to a versioned directory, and prepend its
`bin` directory to `PATH`. The abicheck GitHub Action does this automatically.
The current CI pin is `v2026.01.30` (bundled Clang 21.1.8).

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

#### Option C: conda-forge abicheck environment

```bash
# create env and install abicheck (recipe includes required analysis deps)
# Python >= 3.10 is required; any supported version works
conda create -n abicheck -c conda-forge python=3.12 abicheck
conda activate abicheck
```

No extra manual dependency installation is required when using the conda-forge package.

### Install from source

```bash
git clone https://github.com/abicheck/abicheck.git
cd abicheck
pip install -e .
```

---

## 2) First check (using repo examples)

**Best first run:** compare two shared libraries with their public headers — it
gives abicheck the most evidence to work with (see
[how much evidence you need](#how-much-evidence-do-you-need) below).

The repo includes 195 ABI scenario examples. Most are single-library cases with
paired `v1`/`v2` sources and headers; the L3/L4/L5 build/source-only cases
(152–164) ship hand-built evidence-model fixture pairs; bundle/release-level
cases use release-style layouts.
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
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
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
  --header old=include/v1/foo.h --header new=include/v2/foo.h
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

### How much evidence do you need?

Binary-only detects exported-symbol changes (add/remove, SONAME, visibility).
Adding debug info catches layout and calling-convention breaks; adding headers
adds the full public API surface and scopes out internal types; adding build
and source context catches the facts that never reach the binary at all
(macros, default-argument values, uninstantiated templates). Each source is
additive — more evidence only ever finds more, never hides an artifact-proven
break. Run `abicheck dump libfoo.so --dry-run` to see which layers abicheck
found for a binary. For the full model, the exact `L0`–`L4` layer table, and a
worked example, see [Evidence & Detectability](concepts/evidence-and-detectability.md)
and [What Each Level Sees](concepts/what-each-level-sees.md).

---

## 3) Output formats

`abicheck compare` prints `markdown` by default; pass `--format json` for
machine-readable output (CI logic, agents), or `--format sarif`/`html`/`junit`
for Code Scanning, standalone reports, or CI test dashboards respectively:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h --format json -o result.json
```

See [Output Formats](user-guide/output-formats.md) for the full reference
(field-by-field JSON schema, SARIF/JUnit details, the `review` digest).

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
  --header new=include/foo.h --version new=2.0-dev
```

`compare` auto-detects each input: `.so` files are dumped on-the-fly, `.json`
snapshots are loaded directly (and can be compared to each other with no
headers/castxml/network needed) — mix them freely. See [Baseline
Management](user-guide/baseline-management.md) for where to store baselines
and how to compare across releases, and [Evidence, Build-Context & Debug
Flags](user-guide/dump-compare-flags.md) for `--lang c`, cross-compilation
(`--gcc-prefix`, `--sysroot`), and verbose output.

---

## 5) Application compatibility check

Check whether your **application** is affected by a library update — filtering out irrelevant changes — with `compare --used-by` (repeatable; OLD and NEW may be real library binaries or JSON snapshots carrying binary evidence):

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp -H include/foo.h
```

This parses your application binary to find which library symbols it actually uses. The full library comparison still runs once, but the worst app-scoped result becomes the primary verdict/exit code, with the full verdict and unrelated changes kept as informational context — if the library removed a function your app never calls, it won't drive the verdict.

See [Application Compatibility](user-guide/appcompat.md) for the full reference.

---

## 6) Exit codes and CI

By default, `abicheck compare` exits with the verdict:

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `2` | `API_BREAK` | Source-level API break (binary still works) |
| `4` | `BREAKING` | Binary ABI break |
| `64` | — | Invalid invocation (bad args/options, unreadable input) — outside the verdict space |

> **Note:** passing any `--severity-*` flag switches `compare` to
> **severity-aware** exit codes: `0` = no error-level findings,
> `1` = error-level findings in addition/quality categories, `2` = in
> potential-breaking, `4` = in ABI-breaking. The shape stays the same —
> `0` passes, `4` is worst — but `1` then means a *finding*, not a tool error.

Other commands add their own codes on top of this space — `scan` can exit `5`
(a `--budget` time guard tripped) and a multi-library release compare can exit
`8` (a library was removed with `--fail-on-removed-library`). The full
per-command matrix, including `compat` mode, is the
[Exit Codes reference](reference/exit-codes.md).

Suppressions/policies/baselines all interact with the same pipeline before
the exit code is computed — see [CI Gating](user-guide/ci-gating.md) for how
those pieces fit together, [Severity Configuration](user-guide/severity.md)
for the full severity-aware scheme and policy recipes, and the
[GitHub Action](user-guide/github-action.md) for the fastest way to wire this
into CI (it installs Python/castxml/abicheck and runs the comparison in a few
lines of YAML).

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

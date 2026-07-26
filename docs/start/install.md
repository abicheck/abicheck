---
doc_type: how-to
audience:
  - library-maintainer
level: beginner
lifecycle: active
generated: false
---

# Install abicheck

**Full installation (recommended)** — conda-forge bundles `abicheck` with `castxml`
and a compatible compiler toolchain, so header AST analysis (L2) works immediately:

```bash
conda create -n abicheck -c conda-forge python=3.12 abicheck
conda activate abicheck
```

No extra manual dependency installation is required when using the conda-forge package.

**Lightweight/core installation** — the PyPI package is pure Python with no native
scanner dependency:

```bash
pip install abicheck
```

## Requirements

- Python 3.10+
- `castxml` + a C/C++ compiler — **required for header AST analysis** (all platforms)

All Python dependencies (`pyelftools`, `pefile`, `macholib`) come with the `abicheck` install.

> **Important:** `pip install abicheck` does **not** install `castxml`. Any command
> that takes headers (`--header` / `-H`) needs `castxml` on
> your `PATH` — without it those commands fail with `castxml not found`. Install it
> with the system/conda packages below (the conda-forge package pulls it in
> automatically). If you have no `castxml`, run **binary-only mode** by omitting the
> header flags — abicheck falls back to DWARF/symbols analysis (weaker, but works).
> **Don't run `pip install castxml`** to fill the gap: that installs the unmaintained
> legacy PyPI distribution (last released 0.4.5 in 2018), which abicheck's version
> gate rejects by default for an authoritative L2 scan — use one of the options below
> (conda-forge, the pinned Superbuild, or your platform's package manager) instead.

### Option A: conda-forge (recommended)

Conda-forge supplies CastXML together with a compatible compiler toolchain:

```bash
conda install -c conda-forge castxml
```

### Option B: pinned CastXML Superbuild (Ubuntu CI/reproducers)

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

### Option C: conda-forge abicheck environment

Already covered by the **Full installation** command at the top of this
page (`conda create -n abicheck -c conda-forge python=3.12 abicheck`) —
listed here too since it's also a valid answer to "how do I get `castxml`"
if you already have `abicheck` installed a different way and are switching.

## Install from source

```bash
git clone https://github.com/abicheck/abicheck.git
cd abicheck
pip install -e .
```

## Next

➡️ **[Run your first check](first-check.md)**

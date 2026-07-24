# Troubleshooting

Use this page when a run fails to start (setup/environment) or when results look
surprising (false positive, false negative, or unexpected verdict).

---

## 0) Setup & environment failures

### "castxml not found in PATH"

Header AST analysis requires `castxml`. `pip install abicheck` does **not** install it,
so any command that passes headers (`--header` / `-H`) fails with
this error until `castxml` is on your `PATH`.

```bash
# conda (any OS) — bundles castxml + compiler automatically
conda install -c conda-forge abicheck
# macOS
brew install castxml
# Windows (PowerShell, admin)
choco install castxml
```

On Ubuntu CI, prefer a checksum-pinned
[CastXML Superbuild](https://github.com/CastXML/CastXMLSuperbuild/releases)
over `apt install castxml`. Ubuntu 24.04 currently packages CastXML with bundled
Clang 17, which is too old for some GCC 13 libstdc++ headers. The abicheck
GitHub Action installs the pinned `v2026.01.30` Superbuild automatically.

No castxml and can't install it? Run **binary-only mode** by omitting the header flags —
abicheck falls back to DWARF/symbols analysis (weaker, but catches symbol- and
layout-level breaks):

```bash
abicheck compare old.so new.so   # no -H / --*-header → binary-only fallback
```

### "command not found: abicheck" or wrong tool runs

Some distros ship unrelated tools with similar names (`abi-compliance-checker`
wrappers in Debian `devscripts`, or `abicheck` in Fedora's `libabigail-tools`).
Confirm you're running this project:

```bash
abicheck --version   # should print: abicheck X.Y.Z (abicheck/abicheck)
```

If a different tool shadows it, invoke via the module form: `python -m abicheck`.

### Header parsing fails or finds nothing

If castxml runs but reports parse errors or an empty surface, the inputs usually
don't match the build environment of the analyzed `.so`:

- Pass the same include dirs the library was built with: `-I include/ -I deps/include/`.
- Pass the same preprocessor macros: `--gcc-options "-DFEATURE_X=1 -DNDEBUG"`.
- Best option: feed the real build flags from `compile_commands.json` with `-p build/`
  (see [CLI Usage → Build-context capture](user-guide/cli-usage.md)).
- For pure C libraries, add `--lang c` (the default is `c++`).

### castxml aborts in system headers (`_Float32`, `__assume__`)

castxml drives an internal Clang while emulating your host GCC. If that bundled
Clang is **older than your host gcc/glibc**, parsing your library's headers can
fail inside the *system* headers — before abicheck compares anything — with
errors like:

- `unknown type name '_Float32'` (also `_Float64` / `_Float128`) — glibc's
  sized-float types, understood by **Clang ≥ 16**.
- a parse failure on the GCC 13+ libstdc++ `__assume__` attribute — understood
  by **Clang ≥ 18**.

The fix is a **castxml built against a newer Clang** — the recommended floor is
**bundled Clang ≥ 18**. Ubuntu 24.04's `castxml` package currently bundles Clang
17 and is therefore not suitable for GCC 13 C++ header analysis. Use either the
`conda-forge` package or a release- and checksum-pinned official Superbuild:

```bash
conda install -c conda-forge castxml

# Reproducible CI: download the matching asset from this pinned release,
# verify its published SHA256, extract it, then prepend its bin/ to PATH.
# https://github.com/CastXML/CastXMLSuperbuild/releases/tag/v2026.01.30
```

The pinned abicheck Linux CI release bundles Clang 21.1.8. Always inspect the
full `castxml --version` output: the CastXML version alone does not identify the
bundled Clang frontend.

abicheck detects this case and appends your detected `castxml --version` plus the
recommended floor to the error. As an alternative, point abicheck at a
clang-parsable toolchain/sysroot with `--gcc-path` / `--sysroot`. A
`#ifdef __cplusplus extern "C"` C header that fails only under `--lang c` should
be scanned **without** `--lang c` (castxml always parses in a C++-aware mode).

### "CastXML \<version\> ... is not a supported default scanner setup"

An authoritative L2 scan runs a version gate (`castxml_policy.py`) before
parsing any header: it rejects a resolved `castxml` build outside the
supported range (currently `>=0.6.11,<0.8.0`, bundled/linked Clang `>=18`).
This most commonly fires against the legacy PyPI `castxml` distribution
(`pip install castxml`), which is **not** a supported default scanner setup —
`pip install abicheck` deliberately never installs CastXML for you, and the
PyPI `castxml` package's own release line predates this floor.

Fix: install a supported CastXML from conda-forge (recommended) or a pinned
Superbuild release, same as the two sections above:

```bash
conda install -c conda-forge castxml
```

Only for deliberate legacy reproduction (never as a normal workflow), the gate
can be overridden explicitly with `ABICHECK_ALLOW_UNSUPPORTED_CASTXML=1`. The
resulting snapshot records `ast_toolchain_supported: false` and the specific
`ast_toolchain_unsupported_reasons`, so it is never silently indistinguishable
from a normal, policy-compliant scan — treat it as a degraded, non-baseline
result.

---

## 1) "Why did I get API_BREAK/BREAKING unexpectedly?"

### Check header/binary mismatch first

- Are these the exact headers used to build the analyzed `.so`?
- Are required `-D` macros the same as build time?
- Is include search path the same as build environment?

If not, fix input parity and rerun.

---

## 2) "Why is verdict COMPATIBLE, but I expected NO_CHANGE?"

`COMPATIBLE` means real differences exist (new symbols, policy changes) but no binary break.

Run JSON output for detail:

```bash
abicheck compare old.json new.json --format json -o result.json
python3 -c "import json; r=json.load(open('result.json')); print(r['verdict']); print(len(r['changes']))"
```

---

## 3) "How does `compat` mode report API_BREAK?"

`abicheck compat` uses ABICC-style report text, but still returns **exit code `2`**
for source-level `API_BREAK` conditions.

If you need an explicit `API_BREAK` verdict string in machine-readable output,
use `abicheck compare --format json`.

---

## 4) "Why are deep type changes not detected?"

Check if the binary has DWARF debug info:

```bash
# Check for embedded DWARF sections
readelf -S libfoo.so | grep -E "\.debug_info|\.zdebug_info" || echo "No DWARF sections"

# Check for externally linked split-debug files
readelf --debug-dump=links libfoo.so   # shows .gnu_debuglink / .gnu_debugaltlink references
readelf --debug-dump=follow-links libfoo.so  # follows the link and inspects linked debug-info
```

Without DWARF, the layout-level checks that depend on debug info (`L1`) are
limited — abicheck falls back toward symbol-only (`L0`) analysis. Use debug
builds (`-g`) for deeper analysis.
If the binary uses split debug (separate `.debug` file), the linked debug info is still
analysed automatically when `--debug-dump=follow-links` can resolve the path.

---

## 5) CI script says success but report shows changes

Remember: `compare` exit code `0` includes both `NO_CHANGE` and `COMPATIBLE`.
If you need exact policy, parse JSON verdict instead of checking `$? == 0`.

---

## 6) Still unsure?

Open an issue with:
- command line used
- tool version (`abicheck --version`)
- minimal header + `.so` pair
- JSON output (`--format json`)

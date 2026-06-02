# Troubleshooting

Use this page when a run fails to start (setup/environment) or when results look
surprising (false positive, false negative, or unexpected verdict).

---

## 0) Setup & environment failures

### "castxml not found in PATH"

Header AST analysis requires `castxml`. `pip install abicheck` does **not** install it,
so any command that passes headers (`--old-header` / `--new-header` / `-H`) fails with
this error until `castxml` is on your `PATH`.

```bash
# Ubuntu / Debian
sudo apt-get install -y castxml gcc g++
# macOS
brew install castxml
# Windows (PowerShell, admin)
choco install castxml
# conda (any OS) — bundles castxml + compiler automatically
conda install -c conda-forge abicheck
```

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
abicheck --version   # should print: abicheck X.Y.Z (napetrov/abicheck)
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

Without DWARF, Tier 3/4 checks are limited. Use debug builds (`-g`) for deeper analysis.
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

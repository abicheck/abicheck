# Migrating from libabigail

This guide maps a `libabigail` workflow — `abidiff`, `abidw`, `abipkgdiff` —
onto the abicheck equivalents. Unlike the [ABICC migration](from-abicc.md),
there is no flag-compatible wrapper mode: `abidiff` and `abicheck compare`
share the same shape (`tool old.so new.so` + header/suppression/debug-info
options), so you migrate by swapping the command and translating a handful of
flags, not by keeping the old ones.

## Step 1: Swap the command

```bash
# Before (libabigail):
abidiff libfoo.so.1 libfoo.so.2

# After (abicheck):
abicheck compare libfoo.so.1 libfoo.so.2
```

Both tools read DWARF automatically when the binaries carry it. As with
`abidiff`, results are much stronger with public headers (see the flag map
below) — headers also let abicheck scope out internal types, the equivalent of
`abidiff --drop-private-types`.

```bash
# Before (libabigail):
abidiff --headers-dir1 include-v1/ --headers-dir2 include-v2/ \
  --drop-private-types libfoo.so.1 libfoo.so.2

# After (abicheck — public-surface scoping is automatic with headers):
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=include-v1/ --header new=include-v2/
```

## Step 2: Update CI exit-code checks

`abidiff` returns a **bitmask**; abicheck returns a **scalar verdict code**.
Translate your gates:

| Condition | `abidiff` exit | abicheck `compare` exit |
|---|---|---|
| No differences | `0` | `0` (`NO_CHANGE`) |
| Compatible changes only | bit 2 set (`4`) | `0` (`COMPATIBLE` / `COMPATIBLE_WITH_RISK`) |
| Source-level (recompile-needed) break | — *(folded into bit 3)* | `2` (`API_BREAK`) |
| Incompatible ABI change | bit 3 set (`8`, or `12` with bit 2) | `4` (`BREAKING`) |
| Tool/runtime error (e.g. malformed input that exists) | bit 0 set (`1`) | `1` |
| Usage error (bad arguments/options, unreadable input) | bit 1 set (`2`) | `64` |

With `abidiff`, failing a pipeline on incompatible changes means testing the
bitmask (`rc=$?; if [ $((rc & 8)) -ne 0 ]; then exit 1; fi`). With abicheck
the exit code is already CI-shaped: a plain `abicheck compare …` step fails
on any break (`2` or `4`). To fail **only** on binary ABI breaks and tolerate
source-level ones:

```bash
# capture the status first — under `set -e` (GitHub Actions' default shell
# options) a bare compare exiting 2 would kill the step before the test runs
rc=0
abicheck compare libfoo.so.1 libfoo.so.2 -H include/ || rc=$?
# succeeds only for compatible/API_BREAK verdicts; fails for tool errors (1)
# and BREAKING (4)
test "$rc" -eq 0 || test "$rc" -eq 2
```

See [Exit Codes](../reference/exit-codes.md) for the full matrix (including
the severity-aware scheme).

## Flag-by-flag map

| libabigail (`abidiff`) | abicheck equivalent | Notes |
|---|---|---|
| `lib1.so lib2.so` positional args | `compare OLD NEW` positional args | abicheck also accepts JSON snapshots and directories/packages |
| `--headers-dir1 DIR` / `--hd1` | `--header old=DIR` | Directories are scanned recursively; needs `castxml` or `clang` |
| `--headers-dir2 DIR` / `--hd2` | `--header new=DIR` | Use `-H DIR` once when the same headers apply to both sides |
| `--header-file1` / `--header-file2` | `--header old=FILE` / `--header new=FILE` | Same flags accept files or directories |
| `--drop-private-types` | *(automatic)* | With headers, abicheck scopes findings to the public surface by default; opt out with `--no-scope-public-headers` |
| `--suppressions FILE` / `--suppr` | `--suppress FILE` | Different file format: YAML instead of libabigail's INI sections — see [Suppressions](suppressions.md) and the translation section below |
| `--no-default-suppression` | *(not needed)* | abicheck applies no default suppression specs |
| `--debug-info-dir1 DIR` / `--d1` | `--debug-root old=DIR` | Sidecar/split debug trees |
| `--debug-info-dir2 DIR` / `--d2` | `--debug-root new=DIR` | `--debug-root DIR` applies to both sides |
| *(no equivalent)* | `--debuginfod` | Fetch debug info from a debuginfod server |
| `--stat` | `--stat` | One-line summary instead of the full report |
| `--leaf-changes-only` / `-l` | `--report-mode leaf` | Root-type-grouped leaf view |
| `--impacted-interfaces` | `--show-impact` | Impact summary appended to the report |
| `--no-added-syms` | `--show-only removed,changed` | Display-only filter; verdict and exit code unchanged |
| `--harmless` | *(default)* | Compatible changes are already reported; isolate them with `--show-only compatible` |
| `--exported-interfaces-only` | *(default)* | abicheck always analyses the exported ABI surface |
| `--fail-no-debug-info` | *(no direct flag)* | abicheck degrades gracefully and reports the evidence it had — check `abicheck dump LIB --dry-run` or the report's evidence tier |
| `--verbose` | `-v` / `--verbose` | |

Output formats: where `abidiff` emits its text report, `abicheck compare`
defaults to Markdown and adds `--format json|sarif|html|junit` — see
[Output Formats](output-formats.md).

## Snapshot workflow: `abidw` → `abicheck dump`

If you store `abidw` ABIXML baselines, the equivalent is a JSON snapshot:

```bash
# Before (libabigail):
abidw --out-file libfoo.abi libfoo.so
abidiff libfoo.abi build/libfoo.so

# After (abicheck):
abicheck dump libfoo.so -H include/ --version 1.0 -o libfoo.abi.json
abicheck compare libfoo.abi.json build/libfoo.so --header new=include/
```

Snapshots and binaries mix freely on either side of `compare`; the input
format is auto-detected. ABIXML files are **not** readable by abicheck —
re-dump each stored baseline once from the original binary (see
[Storing Baselines](baseline-storage.md) for storage recipes).

## Package comparison: `abipkgdiff` → `compare` on directories/packages

```bash
# Before (libabigail):
abipkgdiff --d1 foo-debuginfo-1.rpm --d2 foo-debuginfo-2.rpm foo-1.rpm foo-2.rpm

# After (abicheck — directory, archive, or package inputs):
abicheck compare foo-1.rpm foo-2.rpm \
  --debug-info old=foo-debuginfo-1.rpm --debug-info new=foo-debuginfo-2.rpm
```

Multi-library inputs are compared as a co-versioned bundle, with per-library
verdicts and a bundle-level worst-wins verdict; add `--fail-on-removed-library`
to exit `8` when a library disappeared — see
[Multi-Binary Releases](multi-binary.md).

## Translating suppression files

libabigail INI suppressions translate mechanically to abicheck's YAML schema:

```ini
# Before (libabigail INI):
[suppress_function]
name_regexp = ^internal_.*

[suppress_type]
name = FooPrivate
```

```yaml
# After (abicheck YAML — patterns are fullmatch regexes):
version: 1
suppressions:
  - symbol_pattern: "internal_.*"
    reason: "internal namespace, not public API"
  - type_pattern: "FooPrivate"
    reason: "private type"
```

The YAML schema also supports change-kind filters, expiry dates, and required
justifications — see [Suppressions](suppressions.md) for the full schema.

## Semantics to be aware of

- **Enum values.** abicheck intentionally classifies enum member *value*
  changes as `BREAKING` (they break switch statements and serialized data);
  `abidiff` reports them as compatible.
- **A separate source-level verdict.** Changes that require recompilation but
  don't break existing binaries get their own `API_BREAK` verdict and exit
  code `2`, instead of being folded into a single incompatible bit.
- **Evidence layers.** Like `abidiff`, abicheck works binary-only, but its
  verdict strengthens with each added source — debug info, headers, build
  data, sources. See [Evidence & Detectability](../concepts/evidence-and-detectability.md).

The per-case verdict agreement between the two tools is tracked in the
[libabigail parity matrix](../development/libabigail-parity.md), and the
benchmark comparison (including `abidiff` accuracy on the example catalog) in
[Tool Comparison & Benchmarks](../reference/tool-comparison.md).

## Validate side-by-side

Keep both tools installed during the transition and compare verdicts on a few
historical releases:

```bash
for ver in 1.0 1.1 1.2; do
  abidiff libfoo.so.${ver} libfoo.so.current; echo "abidiff: $?"
  abicheck compare libfoo.so.${ver} libfoo.so.current; echo "abicheck: $?"
done
```

Where the tools disagree, the
[parity matrix](../development/libabigail-parity.md) documents the known,
intentional differences.

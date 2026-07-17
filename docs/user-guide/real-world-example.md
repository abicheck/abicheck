# Worked Example: Scanning a Library

This page walks an end-to-end check of a shared library: what to collect from a
project, how to pass it to `abicheck`, and what the reports look like. It links
out to the detailed pages for each step.

We use a hypothetical `libfoo.so` (versions `2.3.0` → `2.4.0`) — the inputs and
commands are the same for any C/C++ shared library.

---

## 1. What you need from a project

You always need the **two builds**. Adding the **public headers** makes the
result reliable (see [§3](#3-the-reliable-baseline-header-aware-l2)); adding your
sources + build command enables the **recommended** deeper [source scan](#5-going-deeper-the-source-scan-recommended).

| Input | Need it for | What it is |
|-------|-------------|-----------|
| **Old + new library** | always (required) | the two builds (`.so`/`.dll`/`.dylib`), or a saved `abicheck dump` JSON |
| **Public headers** | a reliable verdict | the headers a consumer `#include`s — your **API surface**; abicheck parses them to tell public API from internal churn and to see types |
| **Include root(s)** | parsing those headers | the `-I` directories the headers' *own* `#include`s resolve against — not analysed, they just let the parse succeed |
| **C/C++ std + `-D` macros** | correct parsing | the dialect / feature macros the library was built with (set once in a [config file](#4-configure-once-abicheckyml)) |
| **Sources + build command** | the recommended source scan | your source tree plus the command that builds it — lets abicheck replay changed code ([§5](#5-going-deeper-the-source-scan-recommended)) |
| Debug info (DWARF/PDB) | optional | cross-checks types when headers are absent |

!!! tip "Public headers vs. include roots"
    They answer different questions. **Public headers** are *what to analyse*
    (your API). **Include roots** are *how to parse it* — the search path the
    headers need so their nested includes resolve. Often the same `include/`
    serves both, but a header that pulls in `<bar/baz.h>` from `third_party/`
    needs `third_party/` as an extra include root. See
    [CLI Usage](cli-usage.md#public-headers-vs-include-roots).

!!! tip "ELF: pass the real file, not the symlink"
    Point at the fully-versioned file (`libfoo.so.2.4.0`), not the `libfoo.so` →
    … symlink, so the report records exactly which build was compared.

---

## 2. Where the inputs come from

- **Local build:** `build/libfoo.so` for each tag; headers from `include/`.
- **A package:** extract both versions (`dpkg -x`, `rpm2cpio | cpio`, a conda
  download) — the `.so` lives under `lib/`, headers under `include/` (sometimes
  in a separate `-dev`/`-devel`/`-include` package).

---

## 3. The reliable baseline: header-aware (L2)

Public headers are the **minimum for a trustworthy verdict**. Binary-only works
and is always available, but it must treat *every* exported symbol as ABI — it
can't tell your public API from internal churn. Give abicheck the public headers
and it scopes internal/leaked symbols out, sees type/enum/signature changes a
binary can't show, and reports at **HIGH** confidence. (It isn't the deepest
analysis — the [source scan](#5-going-deeper-the-source-scan-recommended) goes
further — it's the floor for results you can trust.)

```bash
abicheck compare old/lib/libfoo.so.2.3.0 new/lib/libfoo.so.2.4.0 \
  --header old=old/include --header new=new/include \
  --include old=old/include --include new=new/include
```

- `-H/--header` accepts a header **directory** (best — a lone file can't
  establish a public/internal boundary) or a file; use `--header old=`/`--header new=`
  when the versions differ.
- `--include old=`/`--include new=` (`-I` for both) are the include roots.
- Add `--ast-frontend clang` on a clang-only host (`castxml` is the default);
  abicheck auto-detects the host libstdc++.
- Need a specific `-std`/`-D` to parse the headers? Pass `--gcc-options
  "-std=c++20 -DFOO=1"` (`compare`, `dump`, and `scan` all share the same
  compile-context flags), or commit them once in a
  [`.abicheck.yml` `compile:` block](#4-configure-once-abicheckyml) — every
  command folds it in via `--config`.

See [CLI Usage](cli-usage.md) for every flag.

### Why headers matter (same compare, two ways)

**Without** headers, abicheck treats every exported symbol as ABI — correct but
noisy, at LOW confidence:

```text
| **Verdict**   | ❌ `BREAKING` |   | Confidence    | LOW |
| Breaking      | 6 |               | Evidence tier | elf_only |

> ℹ️ 5 of 6 breaking findings are internal/RTTI churn — likely a missing
> `-fvisibility=hidden`, not public-API breaks. Genuine public breaks: 1.
```

**With** headers, the internal churn is scoped out, leaving the real change at
HIGH confidence:

```text
| **Verdict**   | ❌ `BREAKING` |   | Confidence    | HIGH |
| Breaking      | 1 |               | Evidence tier | header_aware |
```

Same binaries, same verdict label — headers cut 6 findings to the 1 that matters
and raised confidence `LOW → HIGH`.

---

## 4. Configure once: `.abicheck.yml`

Commit a `.abicheck.yml` so the compile context isn't re-typed each run. The
`compile:` block is shared by **`dump`, `compare`, and `scan`** — pass it with
`--config` and one file pins the dialect/macros/include roots for every command.
Auto-discovery differs by command: `compare` finds the nearest `.abicheck.yml`
from the working directory upward, while `dump`/`scan` pick it up automatically
only from the `--sources` tree root — so give a header-only `dump`/`scan` (no
`--sources`) an explicit `--config`, or its `compile:` settings are silently
skipped. CLI flags (`--gcc-options`/`-I`/`--ast-frontend`) always override the
config per run.

```yaml
# .abicheck.yml
compile:
  frontend: auto                 # auto | castxml | clang | hybrid
  std: c++20                     # the standard the library is built with
  include_dirs: [include]        # add every root your public headers need
  defines: [FOO_ENABLE_FEATURE=1]
```

```bash
# 1) Baseline once per release: dump the OLD library with its OWN headers.
#    Pin the OLD build's dialect/macros inline here — the baseline comes from a
#    different checkout than the new-tree .abicheck.yml (dump also reads a
#    compile: block via --config; point it at the old tree's config to reuse one).
abicheck dump libfoo-2.3.0/lib/libfoo.so -H libfoo-2.3.0/include \
  -I libfoo-2.3.0/include --gcc-options "-std=c++20 -DFOO_ENABLE_FEATURE=1" \
  -o baselines/libfoo-2.3.0.abi.json

# 2) Run from the NEW source checkout (where .abicheck.yml lives, so its relative
#    include_dirs resolve), and gate the new build against that snapshot:
abicheck scan build/libfoo.so -H include/ \
  --against baselines/libfoo-2.3.0.abi.json --config .abicheck.yml
```

Run the scan from the project root so the config's `include_dirs` (relative to
`.abicheck.yml`) point at the checked-out tree. Each side is parsed with **its
own** headers — the baseline is a snapshot dumped from the old headers, not the
raw old `.so` (a raw `--against` library would be re-parsed with the *new* `-H`,
fine only when the headers didn't change). Give the baseline `dump` the same
include roots, dialect, and macros as the scan side so the comparison isn't noisy
— passed inline as `-I`/`--gcc-options` here because the baseline is dumped from
the old checkout (or point `dump --config` at the old tree's `.abicheck.yml` to
reuse a `compile:` block).

!!! warning "Match the build's dialect and macros"
    A wrong `-std` or missing `-D` changes which declarations are visible and
    produces phantom churn — parse at the standard/macros the library was built
    with.

Every field and the CLI-vs-config precedence are in
[Source-Scan Depth](scan-levels.md#where-each-setting-belongs-cli-vs-config).

---

## 5. Going deeper: the source scan (recommended)

Headers give a reliable verdict; the source scan goes further and is
**recommended** for thorough checking — it replays your code to catch
*source-level* ABI changes (inline/template/macro/default-argument body changes)
that neither the binary nor the headers reveal. The simple model: give it your
**source tree** and the **command that builds it**.

```bash
# run from the new checkout (as in §4), with a diff seed so the source replay
# (--depth source) only re-parses the changed TUs
abicheck scan build/libfoo.so -H include/ --sources . --since origin/main \
  --against baselines/libfoo-2.3.0.abi.json --config .abicheck.yml --depth source
```

- `--sources .` — your checkout.
- a `build:` query in `.abicheck.yml` — the command that builds it, so abicheck
  learns your real compile flags:
  ```yaml
  build:
    query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  ```
- `--since origin/main` (or `--changed-path …`) — which files changed, so it
  replays only the changed translation units. Without a seed, `--depth source`
  falls back to a headers-only replay.

!!! note "Cross-release body-change diff needs a source-aware baseline"
    The inline/template/macro/default-argument **body**-change comparison runs
    only when *both* sides carry source evidence. The [§4](#4-configure-once-abicheckyml)
    baseline is a headers-only (L2) snapshot, so a `--depth source` scan against
    it adds the **new** build's source checks; to diff body changes *across
    releases*, dump the baseline with source evidence too (`abicheck dump … --sources …`).

The depth knob is `--depth {binary,headers,build,source}` (`binary` =
binary-only, up to `source` = source-ABI replay — unseeded, it replays the
whole library; with a `--since`/`--changed-path` seed, just the changed TUs);
leave it off and abicheck **auto**-picks by changed-path risk. **How each depth
works, how to produce a compile database for `make`/`cmake`/`bazel`/`meson`,
and the per-level input table live in [Source-Scan Depth](scan-levels.md)** —
that's the home for the build-system details, kept out of this walkthrough on
purpose. (The old `--source-method s0…s6`/`--mode` axes and the separate
`--depth full` rung have been removed outright.)

---

## 6. Example reports

The default format is Markdown. A fuller report for the header-aware run from §3
— **illustrative**: the section structure and `ChangeKind`s are real, the values
are for the hypothetical `libfoo`:

```markdown
# ABI Report: libfoo.so.2

| | |
|---|---|
| **Old version**         | `2.3.0` |
| **New version**         | `2.4.0` |
| **Verdict**             | ❌ `BREAKING` |
| Breaking changes        | 1 |
| Source-level breaks     | 0 |
| Deployment risk changes | 2 |
| Compatible changes      | 3 |

## Analysis Confidence

| Field         | Value |
|---------------|-------|
| Confidence    | HIGH |
| Evidence tier | `header_aware` |
| Evidence tiers| `elf`, `header` |

> **Policy**: `strict_abi`

## Library Files

| | Old | New |
|---|---|---|
| **Path**    | `old/lib/libfoo.so.2.3.0` | `new/lib/libfoo.so.2.4.0` |
| **SHA-256** | `b53cc7b0bfee…` | `83593d6a88b6…` |
| **Size**    | 4.2 MB | 4.3 MB |

## ❌ Breaking Changes

- **type_field_added**: Field `int flags` added to public struct `foo_options`
  (size 48 → 56). Callers that pass the struct by value use the old layout; the
  new code reads/writes past their buffer. (`48` → `56`)
  > A field added to a public by-value struct changes its size and layout —
  > existing binaries are incompatible even though no symbol was removed.

## ⚠️ Deployment Risk Changes

- **symbol_leaked_from_dependency_changed**: A leaked dependency/libstdc++ symbol
  (e.g. RTTI for `std::_Sp_counted_deleter`) changed — not part of your public
  API, so recorded as risk rather than a break.
- **symbol_version_required_added**: New required symbol version `FOO_2.4`; may
  fail to load against an older runtime.

## ✅ Compatible Changes

- **func_added**: `foo_reset(foo_ctx*)` — new public entry point.
- **enum_member_added**: `FOO_MODE_TURBO` appended to `foo_mode`.

## Legend

| Verdict | Meaning |
|---------|---------|
| ✅ NO_CHANGE | Identical ABI |
| ✅ COMPATIBLE | Only additions (backward compatible) |
| ⚠️ COMPATIBLE_WITH_RISK | Binary-compatible; verify target environment |
| ⚠️ API_BREAK | Source-level API change — recompilation required |
| ❌ BREAKING | Binary ABI break — recompilation required |
```

The `type_field_added` break is a struct layout change — **only** detectable with
headers; the binary-only run could not have found it.

### Machine-readable (`--format json`)

For CI, add `--format json`. Key fields (**abridged** — a full report also has
`report_schema_version`, `library`, `old_version`/`new_version`,
`old_file`/`new_file`, `policy`, `suppression`, `detectors`, `evidence_tiers`,
and `summary.source_breaks`/`summary.affected_pct`):

```json
{
  "verdict": "BREAKING",
  "summary": {
    "breaking": 1,
    "risk_changes": 2,
    "compatible_additions": 3,
    "total_changes": 6,
    "binary_compatibility_pct": 98.9
  },
  "confidence": "high",
  "evidence_tier": "header_aware",
  "changes": [
    { "kind": "type_field_added", "symbol": "foo_options",
      "severity": "breaking", "old_value": "48", "new_value": "56" }
  ]
}
```

`compare` exits non-zero on a break, so CI can gate on the exit status alone.
Other formats — **HTML**, SARIF, JUnit — are in [Output Formats](output-formats.md).

---

## 7. Recap

1. **Collect** the old + new library, and — for a reliable verdict — the public
   headers and their include root; pin `-std`/`-D` in a `.abicheck.yml`.
2. **Run** the header-aware compare (or `abicheck scan … --config .abicheck.yml`
   as a CI gate).
3. **Go deeper** (recommended) with `abicheck scan --sources . --since … --depth source`
   when you can give it your sources and build command — see
   [Source-Scan Depth](scan-levels.md).
4. **Read** the verdict + confidence: headers give a high-confidence,
   public-API-scoped result that names the changes that matter.

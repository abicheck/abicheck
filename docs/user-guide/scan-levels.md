# Source-scan levels (`abicheck scan`)

`abicheck scan` is the one-shot orchestrator over `dump`/`compare`: it classifies
the changed paths, runs the always-on compiler-free pattern pre-scan, then runs a
**pinned evidence level** and (with `--baseline`) compares against it.

!!! tip "First time here? Read the model, then the flags."
    The `s0…s6`, `L0…L5`, `--mode`, and `--depth` knobs name **two different
    axes** (`S` = the method, `L` = the evidence) plus presets over them. If they
    look like they overlap, read [Scan Levels (S vs L)](../concepts/scan-and-evidence-levels.md)
    for the mental model first — this page is the practical flag reference and the
    [worked examples](#worked-examples) below.

Two orthogonal knobs select how deep it goes (`abicheck/buildsource/scan_levels.py`):

- **`--source-method s0…s6`** — the precise S-axis (the *how*). Deterministic.
- **`--depth headers|build|source|full|graph`** — a coarse, lossy L-axis. The
  `--source-method` wins if both are given.
- **`--mode pr|pr-deep|baseline|audit`** — a fixed `(S, L)` preset (the default
  is `pr`). A pinned mode produces the same scan for the same inputs.

## What each level reaches

| Level | Technique | Evidence reached |
|-------|-----------|------------------|
| `s0` | diff classifier (risk tags) | L0/L1 binary + DWARF + always-on pattern scan |
| `s1` | compile-DB / build-flag scan | + **L3** build context |
| `s2` | preprocessor (macros/includes) | conditional S2 tier over L3 (`clang -E` macro/include capture) |
| `s3` | lexical pattern scan | pattern facts only (same always-on scan) |
| `s4` | symbol / reference index | + L3 + **L5** source graph (no L4) |
| `s5` | targeted semantic AST (changed TUs) | + **L4** source-ABI replay + L5 edges |
| `s6` | full AST (all TUs) | + L4 over the whole library |

`--mode` presets: `pr` = `(s5, source)`, `pr-deep` = `(s5, graph)` (full L5
reachability), `baseline` = `(s6, full)`, `audit` = `(s5, source)` intra-version
(single-build hygiene, no baseline).

## Worked examples

Each example shows the command, what level it pins, and what to read in the
output. Every `scan` ends with a coverage block — always read it before trusting
the verdict (see [Reading the coverage block](#reading-the-coverage-block)).

### PR gate (the default) — diff-seeded `s5`

The common CI case: gate a PR by comparing the just-built library against the
baseline from `main`, scoping the expensive L4 replay to the files the PR
touched. The `--since` seed is what makes `pr` cheaper than a full `baseline`
scan — without it, `s5` replays every TU.

```bash
abicheck scan \
  --binary build/libfoo.so --headers include/ \
  --sources . --since origin/main \
  --baseline artifacts/libfoo-main.abi.json
```

- **Level:** `--mode pr` (the default) = `(s5, source)`.
- **Exit code:** `0` compatible, `2` source/API break, `4` ABI break (from the
  baseline compare), `5` `--budget` overflow.
- Add `--mode pr-deep` to also fold the full L5 reachability graph when you want
  cross-symbol impact in the report.

### Single-build audit — no baseline

`--audit` runs the intra-version cross-source hygiene checks against **one**
build — no previous version required. Use it to catch accidental exports,
private-header leaks, unversioned symbols, and `header_build_context_mismatch`
on a single artifact:

```bash
abicheck scan --binary libfoo.so --headers include/ --audit
```

This is `(s5, source)` run intra-version; it reports the eight ADR-035
cross-source / single-release findings rather than a two-version diff.

### Cheap gate — no compiler, no sources

When you only have the two binaries (or want a fast pre-check), pin a cheap
level. `--depth build` (`s1`) adds build-flag/toolchain drift from a compile DB;
`--depth headers` (`s0`) stays on the always-on pattern scan and the artifact
tiers only:

```bash
# build-flag drift only, flat ~0.3–0.5s regardless of project size
abicheck scan --binary new/libfoo.so --baseline old/libfoo.abi.json --depth build

# artifact + always-on lexical scan only (no L3/L4/L5)
abicheck scan --binary new/libfoo.so --baseline old/libfoo.abi.json --depth headers
```

### Estimate before you spend — `--estimate`

L4 cost scales with C++ template depth, so on a heavy library project the per-TU
replay cost first. `--estimate` is a dry run: it prints the projected per-layer
cost for *this* project and scans nothing.

```bash
abicheck scan --binary libfoo.so --sources . --mode pr --estimate
```

### Release baseline — full `s6`

Generate the amortized, full-depth snapshot of a release (replays every TU, folds
the full graph). Run it once per release and reuse it as the `--baseline` for PR
scans:

```bash
abicheck scan --binary build/libfoo.so --headers include/ \
  --sources . --mode baseline -o artifacts/libfoo-1.0.abi.json
```

### Let risk pick the depth — `--source-method auto` (local/dev only)

`auto` reads the risk of the changed paths and picks an S-method (capped at
`s5`). It is opt-in and **never** fires for a pinned CI level — keep CI on a
fixed `--mode`/`--source-method` for reproducibility.

```bash
abicheck scan --binary new.so -H include/ --source-method auto --since origin/main
```

### Reading the coverage block

`S` is a *method* and `L` is *evidence*, so a scan can request a deep level and
only reach a shallow one (clang missing, no sources, a parse error). `scan` never
reports that as "failed" — it states the depth it **actually reached** and, for
each disabled check, the input or tool to add:

```text
Checks enabled for this scan (and why others are not):
  [on]  Symbol presence & linkage … — from the binary's dynamic symbol table
  [on]  Build-flag & toolchain drift … — from build-system data
  [off] Macros, default args, inline/template/constexpr bodies — no sources/clang:
        source-only API changes are not detected
```

An `[off]` line is the precise input to add (here: install clang and pass
`--sources`). See
[Build Info & Sources § Evidence coverage](../concepts/build-source-data.md#evidence-coverage)
for the full coverage and capability report.

## Cost guide (rules of thumb)

Measured on two UXL libraries (full data: `validation/`):

| Tier | Levels | Relative cost |
|------|--------|---------------|
| **Cheap** | `s0`–`s4` | One price — dominated by the binary dump + lexical scan, *not* the source layer. |
| **Expensive** | `s5`, `s6`, and the `pr`/`pr-deep`/`baseline`/`audit` modes | clang per-TU AST replay (L4). |

- **The cliff is at L4 (`s4`→`s5`), and its height tracks C++ complexity.** L4
  cost scales with template/STL instantiation depth, not `.so`/TU count — a
  heavy-C++ library can be ~7× slower at `s5` than `s4`, while a plain-C library
  is barely affected (~1.3×).
- **Choose a cheap level by coverage, not cost.** `s0` ≈ `s3` (binary + pattern
  only); `s1` adds L3 build context; **`s4` adds the L5 reachability graph
  without paying for L4** — the best cheap level when you want impact/call
  structure.
- **`s5`/`pr` is only cheaper than `s6` if you give it a diff seed.** Without
  `--since <ref>` or `--changed-path <file>`, the changed-TU set is empty and
  `s5` replays every TU — the same cost as `s6`. With a real PR diff, `s5`
  scopes L4 to the touched TUs and can be **an order of magnitude faster** for
  the identical verdict. Always pass `--since`/`--changed-path` in PR CI.
- **The verdict usually does not change with depth** — the binary diff sets the
  gate; L3–L5 add localization/explanation. For a pass/fail **gate**, the cheap
  tier is enough; spend on L4 (`s5`/`s6`) when you want source-body semantics or
  per-PR localization for humans.

See [Comparison Performance](../development/performance.md#scan-level-cost-model-one-cliff-at-l4)
for the measured numbers.

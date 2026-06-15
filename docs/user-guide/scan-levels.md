# Source-scan levels (`abicheck scan`)

`abicheck scan` is the one-shot orchestrator over `dump`/`compare`: it classifies
the changed paths, runs the always-on compiler-free pattern pre-scan, then runs a
**pinned evidence level** and (with `--baseline`) compares against it.

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
| `s2` | preprocessor (macros/includes) | *not yet implemented — the CLI rejects it* |
| `s3` | lexical pattern scan | pattern facts only (same always-on scan) |
| `s4` | symbol / reference index | + L3 + **L5** source graph (no L4) |
| `s5` | targeted semantic AST (changed TUs) | + **L4** source-ABI replay + L5 edges |
| `s6` | full AST (all TUs) | + L4 over the whole library |

`--mode` presets: `pr` = `(s5, source)`, `pr-deep` = `(s5, graph)` (full L5
reachability), `baseline` = `(s6, full)`, `audit` = `(s5, source)` intra-version
(single-build hygiene, no baseline).

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

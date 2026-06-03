# ADR-002: Multi-binary / release compare UX and architecture

**Date:** 2026-03-16  
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

Real-world releases contain multiple binaries per package (e.g. `libfoo.so`, `libfoo_gpu.so`,
`libbar.so`, `libbar_cxx.so`). Today `abicheck compare` accepts exactly one OLD and one NEW binary.
Users must script their own loops and manually aggregate verdicts.

---

## Proposed UX

### Pattern A — Multiple explicit pairs (extend current `compare`)

```bash
# Multiple binaries, same header dir for all
abicheck compare old/libfoo.so old/libbar.so \
           new/libfoo.so new/libbar.so \
           -H include/

# Per-pair per-side headers (run separately)
abicheck compare old/libfoo.so new/libfoo.so --old-header v1/foo.h --new-header v2/foo.h
abicheck compare old/libbar.so new/libbar.so --old-header v1/bar.h --new-header v2/bar.h
```

**Problem:** CLI gets ambiguous for 3+ binaries.

### Pattern B — Directory-vs-directory (preferred for releases)

```bash
# Auto-match by filename, single header dir
abicheck compare-release release-1.0/ release-2.0/ -H include/

# Per-side header directories
abicheck compare-release release-1.0/ release-2.0/ \
    --old-header include/v1/ --new-header include/v2/

# With mapping file (when binary names differ between versions)
abicheck compare-release release-1.0/ release-2.0/ \
    -H include/ --map mappings.yaml

# Policy: fail if a binary was removed
abicheck compare-release release-1.0/ release-2.0/ -H include/ \
    --fail-on-removed-library
```

**mappings.yaml:**
```yaml
map:
  - old: libfoo.so.1.2
    new: libfoo.so.1.3
  - old: liblegacy.so
    new: null       # intentionally removed (suppress verdict)
```

### Pattern C — Glob / list via file

```bash
# Compare specific set of binaries
abicheck compare-release --libs-list libs.txt old/ new/ -H include/
```

**libs.txt:**
```text
libfoo.so
libbar.so
libbaz.so
```

---

## Matching Rules (auto-mode)

1. Match by **filename stem** ignoring version suffix (e.g. `libfoo.so.1.2` → `libfoo.so`).
2. If ambiguous (multiple versions of same stem): pick latest by **version-aware** sort, warn.
3. **Unmatched in old (removed):** report as `LIBRARY_REMOVED` (configurable fail/warn/ignore).
4. **Unmatched in new (added):** report as `LIBRARY_ADDED` (configurable fail/warn/ignore).
5. **Override:** `--map mappings.yaml` wins over auto-match.

---

## Output

### Summary table (markdown/stdout):

```text
╔══════════════════════╦══════════════╦══════════════╗
║ Library              ║ Verdict      ║ Changes      ║
╠══════════════════════╬══════════════╬══════════════╣
║ libfoo.so            ║ ❌ BREAKING  ║ 3 breaking   ║
║ libbar.so            ║ ✅ COMPATIBLE║ 2 additions  ║
║ libbaz.so            ║ ✅ NO_CHANGE ║ 0 changes    ║
╚══════════════════════╩══════════════╩══════════════╝
Overall: BREAKING (worst of 3 libs)
```

### Machine JSON (`--format json`):

```json
{
  "verdict": "BREAKING",
  "libraries": [
    { "library": "libfoo.so", "verdict": "BREAKING", "changes": [...] },
    { "library": "libbar.so", "verdict": "COMPATIBLE", "changes": [...] },
    { "library": "libbaz.so", "verdict": "NO_CHANGE", "changes": [] }
  ],
  "unmatched_old": [],
  "unmatched_new": []
}
```

### Per-library detail report (`--output-dir`)

```bash
abicheck compare-release release-1.0/ release-2.0/ -H include/ \
    --output-dir reports/
# Generates: reports/libfoo.so.json, reports/libbar.so.json, ...
# And: reports/summary.json, reports/summary.md
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All libraries: NO_CHANGE or COMPATIBLE |
| 1 | Severity-driven error (with `--severity-*` flags) |
| 2 | At least one: API_BREAK |
| 4 | At least one: BREAKING |
| 8 | Missing/unmatched libraries (only when --fail-on-removed-library) |

---

## GitHub Action Impact

New inputs needed:

| Input | Description |
|-------|-------------|
| `old-library-dir` | Directory of old binaries |
| `new-library-dir` | Directory of new binaries |
| `library-map` | YAML mapping file for non-trivial name changes |
| `fail-on-removed-library` | Fail if a library disappeared |
| `severity-preset` / `severity-addition` | Severity configuration for exit codes |
| `output-dir` | Per-library report output directory |

Example:
```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library-dir: release-1.0/lib/
    new-library-dir: release-2.0/lib/
    old-header: include/v1/
    new-header: include/v2/
    format: json
    output-dir: abi-reports/
```

---

## Implementation Plan

1. **CLI:** new `compare-release` subcommand in `abicheck/cli.py`
2. **Matcher:** `abicheck/multi_compare.py` — filename-stem matching + mapping file
3. **Aggregator:** collect DiffResult per pair → aggregate verdict (worst-of)
4. **Output:** extend reporter to handle multi-library summary
5. **Tests:** all combinations — files, dirs, globs, missing pairs, explicit maps
6. **Action:** new inputs + updated `action.yml`
7. **Docs:** `docs/adr/002-multi-binary-release-compare.md` + update `docs/github-action.md`

---

## Open Questions

- Should `compare-release` also accept explicit list `OLD1 OLD2 ... NEW1 NEW2 ...` positionally?  
  → Leaning no — ambiguous for 3+ libs. Use directory or `--libs-list`.
- Should we support SONAME-based matching (not just filename stem)?  
  → Yes, as secondary strategy when filename stem fails.
- Parallel execution?  
  → Yes (ThreadPoolExecutor) — each pair is independent.

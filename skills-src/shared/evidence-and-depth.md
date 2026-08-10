---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - evidence-model
---

# Evidence depth: what each layer can and cannot see

Compatibility findings are only as good as the evidence they were derived
from. abicheck exposes one dial, `--depth`, shared by `dump`, `compare`, and
`scan`, named by what you get rather than by an internal tier number:

| `--depth` | Adds | Newly answerable | Still blind to |
|---|---|---|---|
| `binary` | export tables only (ELF `.dynsym` / PE export dir / Mach-O trie) | removed/added exports, mangling changes, `_ZTV`/`_ZTI` vtable-size drift | anything about types, layout, or signatures |
| `headers` (default) | header AST | signatures, struct/enum/union layout, member visibility, source-API breaks | changes only the build reveals (flags, macros, conditional compilation) |
| `build` | build context (compile flags, macros, include order) | ABI-relevant flag drift, macro-conditional layout, dialect mismatches | which code actually reaches which symbol |
| `source` | source replay + call graph | reachability, consumer-relative impact, source-level root causes | nothing further in this ladder |

`--depth build` and `--depth source` require real build evidence — pass
`--sources` and/or `--build-info`. Requesting a depth the inputs cannot
satisfy is an error, not a silent downgrade.

The full mental model, including what each transition buys in false-positive
and false-negative terms, is owned by
[the evidence and detectability page](../../docs/learn/evidence-and-detectability.md);
the practical flag-choice guide is
[scan levels](../../docs/use/scan-levels.md), and a level-by-level worked
example is [what each level sees](../../docs/learn/what-each-level-sees.md).

## Reading the depth actually achieved

Read what the run actually reached, not what you asked for. Every JSON report
carries:

- `evidence_tier` — the canonical ordered scalar (`elf_only`,
  `dwarf_aware`, `header_aware`). **Key trust decisions off this.**
- `evidence_tiers` — the raw list of sources that were available.

and, **only when there were coverage gaps**:

- `coverage_warnings` — where coverage fell short. A clean run omits the key
  entirely; that is good news, not a malformed or under-evidenced report.

You will not find a "depth achieved" echo alongside them: `requested_depth`
and `effective_depth` are in the report schema but only the GitHub Action's
`check-target` envelope populates them, never a direct `abicheck compare`.
Confirming the depth is **mandatory**, because `compare` does *not* fail when
an explicit `--depth` cannot be reached — unlike `dump`, it warns and
silently downgrades. Which field confirms it depends on the depth asked for:

- **`binary` / `headers`** — `evidence_tier`. A headerless `--depth headers`
  run produces an exit-`0` report at `elf_only` or `dwarf_aware`; require
  `header_aware` before treating that verdict as a header-depth answer.
- **`build` / `source`** — **`layer_coverage`**, not `evidence_tier`. The
  tier scale stops at `header_aware` and has no value for build context,
  source replay, or a call graph, so it cannot express whether L3–L5 were
  collected. A `--depth source` run over header-only snapshots completes at
  exit `0` with `evidence_tier: header_aware` while `layer_coverage` shows
  `L3_build` and `L4_source_abi` as `not_collected`. Read the entry for each
  layer the workflow depends on and require `status: present`; a reachability
  claim resting on a `not_collected` layer is unbacked.

In either case, rerun with the missing evidence supplied — headers via
`-H/--header`/`-I/--include`, build/source evidence via its own flags —
rather than reporting the downgraded result.

Per finding, `changes[].evidence_status` records what backed that specific
finding.

## The rule this exists to enforce

Adding a layer reduces both over-calling and under-calling. Removing one
does the opposite — and the direction that matters for safety is that a
shallower run **under-calls**: it misses breaks it structurally cannot see.

So: a `binary`-depth run reporting no findings has not shown the change is
compatible. It has shown that nothing visible in the export table changed.
Say exactly that. See
[safety-invariants.md](safety-invariants.md) item 1.

## Choosing a depth

- Reviewing a source change → `headers` at minimum. Anything less cannot see
  a signature or layout change.
- Gating a release → `headers`, plus `build` when the build system is
  reachable and toolchain/flag drift is plausible.
- Answering a consumer-scoped question (`--used-by`) → `source` when you
  need real reachability; otherwise state that the answer is
  surface-level, not call-graph-proven.
- Investigating a suspicious mass of findings → deeper, not shallower.
  Over-calling is usually an evidence problem, not a detector problem. See
  [public-surface-and-scoping.md](public-surface-and-scoping.md).

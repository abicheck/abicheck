---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - verdicts
---

# Three compatibility contracts, not one

The domain routinely says "compatible" for three different guarantees. Decide
which one the user is actually asking about before doing anything else — the
answers genuinely diverge, and giving the wrong one is the most common way a
compatibility review misleads.

| Contract | The promise | Broken by | Who cares |
|---|---|---|---|
| **ABI** (binary) | An already-compiled consumer keeps loading and running against the new library **without recompiling** | removed/renamed exports, changed layout or size, vtable reordering, calling-convention or mangling changes, enum value changes reaching a signature | distro packagers, plugin hosts, anyone shipping a `.so`/`.dll`/`.dylib` under a stable SONAME |
| **Source API** | A consumer's source still **compiles** against the new headers | removed declarations, changed signatures/defaults, stricter constraints, removed headers or macros | library users upgrading with a rebuild |
| **Runtime / environment** | The same binary keeps working in a different environment | dependency floors (glibc, libstdc++), transitive SONAME drift, missing runtime symbols after an OS/container move | deployment, container base-image upgrades |

Key asymmetries worth stating explicitly to a user:

- A change can be **source-compatible but ABI-breaking** (adding a member to
  a public struct, adding a virtual function, changing an inline function's
  layout assumptions). This is the single most common surprise.
- A change can be **ABI-compatible but source-breaking** (removing an
  overload that was never emitted, tightening a template constraint,
  renaming a macro).
- Runtime compatibility is orthogonal to both: a perfectly ABI-compatible
  library still fails to load if the new build raised its glibc floor.

## How abicheck expresses this

`abicheck compare` reports a single ordinal `verdict` — `NO_CHANGE`,
`COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, `BREAKING` — where
`API_BREAK` is the source-only break and `BREAKING` is the binary break. Its
exit codes follow the same split (`0` / `2` / `4`), plus `16` for a pair that
could not be compared at all.

Runtime questions are **partly** `compare`'s job, and it is worth knowing
which part. A new build that requires a newer `GLIBC_*`/`GLIBCXX_*`/`CXXABI_*`
symbol version *is* detected — `runtime_floor_raised`, reported as a risk
because whether it breaks anyone depends on runtimes the tool cannot see. Give
it declared floors (an environment matrix) and that risk becomes a checkable
contract: a requirement above a declared floor is promoted to a real break.

So do not discard a runtime finding on the grounds that "`compare` doesn't do
runtime" — read it. What `compare` does *not* cover is the rest of the
dependency graph: transitive SONAME drift, the full dependency stack across
sysroots, missing runtime providers. That is `abicheck deps compare` and
`abicheck deps tree`.

Full semantics, including the verdict-to-exit-code chain, are owned by
[the verdicts page](../../docs/learn/verdicts.md); the exhaustive per-command
exit-code matrix is in
[the exit-code reference](../../docs/reference/exit-codes.md).

## Practical routing

- "Will installed consumers keep working without a rebuild?" → ABI. Judge on
  `verdict` reaching `BREAKING`.
- "Will my users' code still compile?" → source API. `API_BREAK` matters as
  much as `BREAKING`.
- "Will this binary still run on the new base image?" → runtime. Start with
  `compare`'s own `runtime_floor_raised` finding, which answers the
  symbol-version half; `compare` cannot answer the rest on its own, so reach
  for `deps compare` for the dependency graph and state the limitation.

If the user has not said which one they mean, and the answers would differ,
say so and answer both — do not silently pick the one that reads better.

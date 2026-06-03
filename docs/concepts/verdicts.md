# Verdicts

Every `abicheck compare` run produces one of five core verdicts, ordered from
safest to most severe: `NO_CHANGE`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`,
`API_BREAK`, `BREAKING`. The verdict is the *worst* classification across all
detected changes under the active [policy](../user-guide/policies.md).

Each change kind is partitioned into exactly one classification set in
`checker_policy.py` — `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`, or
`COMPATIBLE_KINDS` — and `COMPATIBLE_KINDS` is further split into **additions**
(`ADDITION_KINDS`, new public surface) and **quality** signals
(`QUALITY_KINDS`, hygiene/metadata). The [Examples Encyclopedia](../examples/index.md)
groups every fixture by both verdict and category.

> **Beyond the five core verdicts.** `compare` in severity-aware mode (any
> `--severity-*` flag) can also report **`SEVERITY_ERROR`** with exit code `1`
> when an addition/quality finding is promoted to error level — for example to
> block accidental public-API expansion. The `compare-release` package mode adds
> **`REMOVED_LIBRARY`** (exit `8`) when a shared object present in the old
> package is absent from the new one. See the
> [GitHub Action](../user-guide/github-action.md#outputs) and
> [Exit Codes](../reference/exit-codes.md) for the full matrix.

---

## The five verdicts

### `NO_CHANGE`
The two snapshots are **identical** — no differences found.

**CI action:** pass.

---

### `COMPATIBLE`
Changes found, but **backwards-compatible** — existing compiled consumers can upgrade without recompiling. abicheck splits this tier into two reportable categories:

**Additions** (`ADDITION_KINDS`) — new public surface:
- New exported symbol or global variable added
- Enum member appended at the end of an enum (no value shift)
- Union field added without growing the union's size
- Inline function outlined into the `.so` (new export, old inlined copies still work)
- `experimental::` graduated to stable while keeping the old alias

**Quality** (`QUALITY_KINDS`) — hygiene/metadata signals, not ABI breaks:
- `GLOBAL` → `WEAK` symbol binding (ELF/Linux; relaxes interposition only)
- GNU IFUNC introduced/removed
- SONAME/visibility/versioning hygiene findings (missing SONAME, RPATH leak, executable stack)

> **Note:** `noexcept` removal is **not** `COMPATIBLE` — it is `COMPATIBLE_WITH_RISK` (see below), because callers compiled assuming `noexcept` omit exception landing pads.

**CI action:** warn; do not fail. Use a severity flag (e.g. `--severity-addition error`) to promote additions/quality to an error-level `SEVERITY_ERROR` if your policy requires it.

---

### `COMPATIBLE_WITH_RISK`
A change that **does not break** existing compiled consumers (they are already linked and continue to work), but introduces a **deployment risk** that must be verified manually.

The library upgrade may fail on some target environments — for example, if the new library requires a newer glibc version that is absent on the deployment target — or the change is binary-linkable but semantically unsafe for binaries built under the old contract.

Examples (`RISK_KINDS`):
- New symbol version requirement added to `DT_VERNEED` (e.g. `GLIBC_2.17`) — existing binaries are safe, but the new `.so` won't load on systems with older glibc
- `noexcept` removed ([case15](../examples/case15_noexcept_change.md)) — links fine, but callers built assuming `noexcept` omit landing pads, so a real throw calls `std::terminate`
- A CPU-dispatch ISA family dropped ([case83](../examples/case83_cpu_dispatch_isa_dropped.md)) — loads fine, but the optimized path a consumer expected is gone

**CI action:** warn; inspect the specific change kind and verify target environment requirements. Do not fail automatically unless your policy mandates it.

> Use `abicheck compare --format json` to check the exact `verdict` field — `COMPATIBLE_WITH_RISK` exits with code `0`, same as `COMPATIBLE`.

---

### `API_BREAK`
A **source-level API break** — the public header contract changed in a way that breaks downstream source code, but **does not break already-compiled binaries**. Pre-compiled consumers continue to work at runtime. Consumers that **recompile** against new headers may get compile errors or semantic changes.

Examples:
- Field rename (same binary layout, different source name)
- Enum member rename
- Parameter default value removed
- Reduced access level (`public` → `protected`)

**CI action:** fail in API-strict pipelines or pipelines that test building from source; warn in ABI-only gates.

> **Note:** `abicheck compat` *does* emit exit code `2` for `API_BREAK` conditions.
> However, the `compat` HTML/text report uses ABICC-style phrasing
> ("⚠️ API_BREAK — Source-level API change — recompilation required") rather than a bare
> `API_BREAK` verdict string. Use `abicheck compare --format json` for machine-readable
> verdict values.

---

### `BREAKING`
A **binary ABI break** — existing compiled consumers malfunction when the library is updated.

Examples:
- Symbol removed from `.so`
- Function parameter type changed
- Struct field removed or offset shifted
- C++ vtable reordered (virtual method inserted)
- `const` qualifier added to global variable (moves to `.rodata`, breaks writes)

**CI action:** always fail; do not ship.

---

## CI policy templates (compare mode)

### Strict production gate
```bash
abicheck compare old.json new.json
ret=$?
[ $ret -eq 1 ] && echo "ERROR — check tool inputs" && exit 1
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
echo "OK (NO_CHANGE or COMPATIBLE)"
```

### Warning-only gate
```bash
abicheck compare old.json new.json --format json -o result.json
ret=$?
[ $ret -eq 1 ] && echo "::error::tool error" && exit 1
[ $ret -eq 4 ] && echo "::error::BREAKING ABI change" && exit 1
[ $ret -eq 2 ] && echo "::warning::API_BREAK (source-level)"
verdict=$(python3 -c "import json; print(json.load(open('result.json'))['verdict'])" 2>/dev/null || echo "")
[ "$verdict" = "COMPATIBLE" ] && echo "::warning::COMPATIBLE ABI change (new symbols or compatible modifications)"
echo "ABI check passed"
```

### Permissive gate (binary breaks only)
```bash
abicheck compare old.json new.json
ret=$?
[ $ret -eq 1 ] && exit 1   # tool error
[ $ret -eq 4 ] && exit 1   # BREAKING only; API_BREAK (exit 2) allowed
exit 0
```

> For `compat` mode CI patterns, see [ABICC Compatibility](../user-guide/from-abicc.md).
> Note: in compat mode, exit `1` = BREAKING, exit `2` = API_BREAK.
> Non-verdict failures use extended codes (`3`–`11`) — see [Exit Codes](../reference/exit-codes.md).

---

Full exit code reference: [Exit Codes](../reference/exit-codes.md)

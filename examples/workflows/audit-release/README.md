# Workflow: audit a single release

**Task:** "Did I accidentally ship an undocumented export consumers could
start depending on?"

This is a Phase 5 slice of the examples/catalog split (retired plan
record, `docs/contribute/plans/index.md`) — a small,
curated, task-oriented example independent of the 197-case calibration
catalog under `catalog/cases/` (which exists to calibrate detectors, not to
teach the CLI). See that plan's "What is left" section for the rest of this
curated set, not yet built.

Unlike [`compare-release`](../compare-release/README.md), there is no
second release here at all — an audit checks a single build against
*itself*, before any consumer has had the chance to depend on a mistake in
it.

## The project

A tiny shared library, `greet`, has one intended public function:

```text
include/greet.h   const char *greet(const char *name);
greet.c           greet() -- plus debug_dump(), a helper the author
                  believes is private
```

`debug_dump()` is never declared in `include/greet.h`, and it isn't marked
`static` or given hidden ELF visibility either — an easy, common mistake.
It compiles and links exactly as intended, and nothing about the header
looks wrong, but the default visibility means it lands in the shared
library's dynamic symbol table anyway: any consumer can already `dlsym()`
or link against it, whether the author meant to publish it or not.

## Run it

```bash
cd examples/workflows/audit-release

# Build the release as a shared library
python3 build_shared_lib.py -fPIC -g -Iinclude greet.c -o libgreet.so

# Audit it: no baseline needed -- just check that everything the ELF
# export table exposes is something the public headers actually declare
abicheck scan libgreet.so --header include
```

## What you get

```text
crosscheck:exported_not_public present       binary exports ↔ public headers: 1 of 2 export(s) undocumented (1 accounted as documented API / compiler artifact); by reason: undeclared_export=1

ABI-hygiene catalog (intra-version, advisory)
  [warning] exported_not_public: 1

Verdict: COMPATIBLE
```

Exit code is `0` — nothing is *broken* today, since there's no prior release
to break anything relative to. The `exported_not_public` finding is
advisory ABI hygiene: `debug_dump()` is exported but undocumented, so
whoever maintains this library still believes it's free to change or remove
at will. In reality it's already load-bearing ABI for anyone who found it
via `nm -D libgreet.so` — the fix belongs in *this* release, not after a
consumer files a breakage report against a function nobody meant to
publish. See [Verdicts](../../../docs/learn/verdicts.md) for what
`COMPATIBLE` does and doesn't promise, and
[Evidence & Detectability](../../../docs/learn/evidence-and-detectability.md)
for why this specific check (`crosscheck:exported_not_public`) needs *both*
the binary's export table (L0) and its public headers (L2) — neither alone
can tell an intentional export from an accidental one.

## Next steps

- Fix it by marking `debug_dump()` hidden
  (`__attribute__((visibility("hidden")))`) or `static`, or, if it's
  genuinely meant to be public, add it to `include/greet.h` so the audit
  stops flagging it and the contract is explicit.
- Want machine-readable output for CI? Add `--format json` — the same
  finding shows up under `crosscheck.counts_by_check.exported_not_public`.
- Want this to also catch a *removed* export against a previous release?
  See [`compare-release`](../compare-release/README.md) — that's a
  different task (comparing two builds), not an audit of one.

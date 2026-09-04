# Application Compatibility Check

`compare --used-by APP` answers: **"Will my application still work with the new library version?"**

Unlike a plain `compare` (whose verdict and exit code reflect the whole
library), `--used-by` scopes the **verdict and exit code** to just the
changes that affect the specific application binary you provide — the
report still lists every library change, but adds a per-app verdict/summary
and makes that scoped verdict (not the full-library one) drive the exit
code. This is the application-centric view of ABI compatibility.

> **History note:** this used to be a standalone `abicheck appcompat`
> command. The pre-1.0 CLI reset folded it into `compare --used-by` (ADR-043)
> — the full library comparison runs once, and the worst app-scoped result
> becomes the primary verdict/exit code, with the full-library verdict and
> unrelated changes kept as informational context. `OLD_INPUT`/`NEW_INPUT`
> may be real library binaries or JSON snapshots that carry binary evidence
> (a `dump` of a real library, not headers-only) when `--used-by` is used —
> the app's imports are resolved against whichever the caller gives. The
> application binary itself always has to be real: its imports can only be
> read from a genuine ELF/PE/Mach-O file.

---

## When to use `--used-by`

| Scenario | Command |
|----------|---------|
| Library maintainer checking all ABI changes | `abicheck compare` |
| App developer checking if *their app* is affected | `abicheck compare --used-by ./myapp` |
| Distro packager checking if app X works with new libfoo | `abicheck compare --used-by ./appX` |

---

## Full mode (old + new library)

Provide the old library, the new library, and the application binary via `--used-by`:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp
```

With headers for deeper analysis:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp \
  -H include/foo.h
```

`--used-by` is repeatable, so one comparison can be scoped to several
consumer applications at once:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --used-by ./myapp --used-by ./otherapp -H include/foo.h
```

This will:

1. Parse each application binary to extract required symbols
2. Run the full library comparison (same as plain `compare`) — the report
   still lists every library change, not just the app-relevant ones
3. Check symbol availability in the new library
4. Internally partition the library's changes into those relevant to each
   application's imports and those that are not, to compute a per-app count
   and verdict (see "How symbol filtering works" below)
5. Compute an app-specific verdict per `--used-by` app, and fold the worst
   one into the run's primary verdict/exit code

### Example output

The full-library report (same body plain `compare` would produce) is
rendered first, followed by an appended `--used-by` summary. When the
app-scoped verdict differs from the full-library verdict, a banner states
which one the exit code actually reflects:

```text
**Scoped verdict: BREAKING** (this is what the exit code reflects; the full
library verdict above is COMPATIBLE_WITH_RISK).

# Comparison Report

**Library:** `libfoo.so.1` → `libfoo.so.2`
**Verdict:** `COMPATIBLE_WITH_RISK`

... (the full, unfiltered set of library changes) ...

## Scoped to --used-by applications

- ./myapp: BREAKING (missing 1 symbol(s), 0 version(s), 1 relevant change(s))
```

The full-library report body is **not** filtered down to app-relevant
changes — every change is still listed there. The `--used-by` section names
each app's scoped verdict and a small missing-symbol/relevant-change count;
the `json` format instead adds `used_by` (per-app detail, including
`missing_symbols`/`missing_versions`/`relevant_change_count`) and
`full_verdict` keys alongside the usual payload, with `verdict` overwritten
to the scoped verdict. (Exact rendering depends on `--format`; see
`abicheck compare --help` for the full output-format list.)

---

## What's no longer directly available

Two pieces of the old standalone `appcompat` command don't have a CLI
replacement after the ADR-043 reset — both were narrower diagnostic modes
that didn't fit the unified `compare` surface:

- **Weak mode** (`appcompat APP --check-against LIB`, checking symbol
  availability with no old library at all — no diff, no change detection) —
  no CLI replacement. The underlying logic still exists as
  `abicheck.appcompat.check_against()` for Python API use.
- **`--list-required-symbols`** (dump the app's imported symbols/versions and
  exit) — no CLI replacement. Use `abicheck.appcompat.parse_app_requirements()`
  from the Python API to get the same `AppRequirements` data (imported
  symbols, needed libraries, required ELF symbol versions) programmatically.

If you relied on either of these in a script, the closest CLI-only fallback
is `abicheck deps tree ./myapp` (see [Companion Commands](companion-commands.md)),
which reports whether the application's dependencies resolve and its
required symbols bind — a different, broader check (whole dependency stack,
not one candidate library file) but often enough to catch the same class of
problem in CI.

---

## Options reference

| Option | Description |
|--------|-------------|
| `OLD_INPUT` / `NEW_INPUT` | Old and new library (`.so`/`.dll`/`.dylib`, JSON snapshot, or ABICC dump) — same as plain `compare`. With `--used-by`, a JSON snapshot works only if it carries binary evidence (a `dump` of a real library, not headers-only) — its `elf`/`pe`/`macho` field is what the app's imports resolve against. |
| `--used-by FILE` | Application binary whose imports/required symbol versions scope the comparison (repeatable). Mutually exclusive with `--required-symbol`/`--required-symbols`. |
| `-H` / `--header` | Public header file or directory (repeatable, side-aware with `old=`/`new=`) |
| `-I` / `--include` | Extra include directory for castxml (repeatable, side-aware) |
| `--lang` | Language mode: `c++` (default) or `c` |
| `--format` | Output format: `markdown` (default), `json`, `sarif`, `html`, `junit`, `review` |
| `-o` / `--output` | Write report to file |
| `--scope-public-headers` / `--no-scope-public-headers` | Restrict findings to the public-header ABI surface (on by default) |
| `--severity-preset` | `default`, `strict`, or `info-only` (switches to the severity-aware exit scheme) |
| `.abicheck.yml`'s `severity:` block | Per-category overrides (`abi_breaking`/`potential_breaking`/`quality_issues`/`addition`, each `error`/`warning`/`info`) — config-only; `--severity-preset` is the per-run CLI knob |
| `--suppress` | Suppression file (YAML) |
| `--policy` | `NAME\|PATH` — a built-in profile (`strict_abi` (default), `sdk_vendor`, `plugin_abi`) or a policy document (a path, or a packaged built-in like `security`) |
| `-v` / `--verbose` | Debug output |

See `abicheck compare --help` for the complete flag set — `--used-by` is one
option among the full `compare` surface, not a separate command with its own
flags.

---

## Exit codes

`compare --used-by` computes the exit code from the worst of every
`--used-by` app's own scoped result — the full-library verdict is folded
into the rendered report as informational context (see "Example output"
above) but does **not** participate in the exit-code calculation. Which
*scheme* computes that scoped exit code follows the exact same, fully
automatic resolution as plain `compare` — see [The two exit-code
schemes](ci-gating.md#the-two-exit-code-schemes) for the resolution rule
(purely derived from whether any severity setting is active; there is no
manual pin). Scoped and unscoped runs share that one resolution — nothing
here overrides it.

**Legacy scheme (no severity setting active):**

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `COMPATIBLE` / `NO_CHANGE` | Application(s) safe with the new library |
| `2` | `API_BREAK` | Source-level break affecting an app's symbols |
| `4` | `BREAKING` | Binary ABI break or missing symbols |
| `64` | usage error | Bad arguments/invocation |

### `--severity-*` flags *do* apply to a scoped run

A scoped `--used-by` (or `--required-symbol(s)`) run respects
`--severity-*`/`--severity-preset` the same way plain
`compare` does (see above). When the scheme does resolve to severity-aware, it applies to the
scoped exit code too: `0`/`1`/`2`/`4` as described in [Exit
Codes](../reference/exit-codes.md), computed over the changes relevant to
that app (`compute_exit_code`/`compute_gate_decision` run against the
app-scoped change set, not the full library's). One consequence: a missing
required symbol/version/entrypoint has no matching diff `Change` for the
severity machinery to see on its own, so it is floored in separately —
under the severity scheme it counts toward, and can trip, the
`abi_breaking` category exactly as a real `FUNC_REMOVED` finding would,
including respecting a demoted `severity.abi_breaking: info` (i.e. a
missing-contract symbol is not a hidden, unconfigurable floor to `4`
anymore).

The JSON report distinguishes the two levels explicitly:

- `verdict` / `severity` — the **scoped** result (what the exit code
  reflects). Under the severity scheme, `severity.categories.*.count` and
  `severity.blocking_categories` are the scoped tallies too, not the
  full-library ones.
- `full_verdict` / `full_severity` — the full-library result, moved aside as
  informational context. Both `severity` and `full_severity` are present
  only when the run resolved to the severity scheme; under the legacy
  scheme neither key is emitted at all (there is no gate config to render),
  so their absence alone doesn't distinguish "legacy" from "not rendered
  yet" — check `scoped_exit_code_scheme` via SARIF/JUnit (below) if a
  consumer needs to tell the two apart explicitly.
- `used_by` — per-app detail (`missing_symbols`/`missing_versions`/
  `relevant_change_count`), unchanged by which scheme computed the exit
  code.

SARIF and JUnit output additionally state the scheme explicitly —
`gateExitCodeScheme`/`scopedExitCodeScheme` in the SARIF run properties, and
an `abicheck.scoped_exit_code_scheme` JUnit property — for a consumer that
needs to know legacy-vs-severity without inferring it from field presence.

Note that `--show-only`/the JSON report alone, **without** `--used-by`,
cannot substitute for this: only `--used-by` actually reads the app's
imports and computes the app-relevant subset in the first place — plain
`compare` (even with `--severity-*`) has no app to scope against, and gates
on the full library diff regardless of what `--show-only` filters out of
the *rendered* output.

---

## How symbol filtering works

Each `--used-by` application binary is parsed to extract:

- **Imported symbols** — undefined symbols in `.dynsym` (ELF), import table (PE), or symbol table (Mach-O)
- **Library filter** — only symbols imported from the target library are considered (using ELF `.gnu.version_r`, PE DLL name, or Mach-O two-level namespace)
- **Required versions** — ELF version tags from `.gnu.version_r`

A library change is **relevant** to an app if any of these conditions hold:

1. The change's symbol is in the app's imported symbol set
2. The change's `affected_symbols` overlap with the app's imports (type change propagation)
3. The change is `SONAME_CHANGED` (affects all consumers)
4. The change is `COMPAT_VERSION_CHANGED` (Mach-O, affects all consumers)
5. The change is `SYMBOL_VERSION_DEFINED_REMOVED` for a version the app requires

All other changes are classified as **irrelevant** — the library changed, but the application doesn't use the affected symbols.

---

## Why does this consumer depend on the changed declaration?

The relevance test above tells you *whether* a change touches the app's
imports. When the old library side also carries a **source graph** (ADR-057),
abicheck can additionally explain *why* — the chain of calls inside the old
library that connects a symbol the app actually imports to the internal
declaration that changed.

That source graph comes from one of two producers, and it matters which one
supplied it: a full **L4/L5 build/source graph** (`--old-sources`/
`--old-build-info`) sees real call chains through the library's whole
implementation, while an **L2, header-only graph** — attached automatically
whenever headers are parsed, no extra flag needed — only sees
inline/template bodies visible directly in the header text. Both are stored
as the same `SourceGraphSummary` shape, so the join below works identically
either way; the L2 graph is just narrower in what it can reach.

The L2 graph's automatic attach still needs `clang`/`clang++` on `PATH` to
actually see call/type edges — that's true even when the main extraction
used the default CastXML header backend, since the header-graph attach
always shells out to clang itself. Without a usable clang, it silently
degrades to a declaration-visibility-only graph (no `DECL_CALLS_DECL`
edges), so the join below never fires and findings keep their plain
symbol-level wording instead of erroring. In a clang-less environment,
`--old-sources`/`--old-build-info` is the reliable way to get proof-path
chains.

### The two evidence sides of the chain

- **The app's import table proves the app requires the removed symbol
  itself** — parsed the same way as the relevance check above (`CONF_HIGH`:
  "a fact about a real linked binary, not an inference"). The proof-path
  join only fires for a symbol the app's own binary directly names as
  undefined; it does not explain a change to some other internal symbol the
  app never referenced.
- **The old library's own source graph explains *why* the app ended up
  requiring that symbol directly** — walking `DECL_CALLS_DECL`/
  `SOURCE_DECL_MAPS_TO_SYMBOL` edges from every **consumer-compiled** public
  entry (a declaration whose body was compiled straight into the consumer's
  own binary — in practice, an `inline` function or template instantiation
  the app's compiler expanded) to the removed declaration.

Joining the two answers a question neither side can answer alone: not "some
symbol went missing" but "the inline/template entry point `Y` your own
binary expanded is what made you require the now-removed `X` directly." An
ordinary out-of-line exported function the app calls has no such chain to
show — if the app requires `Y` itself and `Y` was removed, that is the
direct case below, not this join.

### What evidence this needs

The join only fires when the **old** library side carries a source graph —
either shape above. A plain `-H`/`--header` pass already supplies the L2
header-only one, for inline/template-reachable chains; passing
`--old-sources`/`--old-build-info` (or their `--sources`/`--build-info`
equivalents) reaches further, into call chains a header alone can't see:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp \
  -H old=include/v1/foo.h -H new=include/v2/foo.h
```

**Without a source graph on the old side, abicheck doesn't invent an
explanation** — the finding keeps exactly the plain symbol-level wording it
always had (`public_reachable: true`, no proof path), the same as before
this feature existed. Absence of a graph edge is never treated as evidence
of absence of a dependency.

### Two worked examples

**1. Direct dependency** — the app imports the removed function itself:

```text
myapp requires public entry train directly
```

No chain to show — the app's own import table already names the removed
symbol.

**2. Indirect dependency** — `train()` is an `inline` function defined in the
header, so the app's compiler expanded it straight into the app's own
binary; the app's import table therefore directly names the internal,
now-removed declaration `train()`'s body called, not `train` itself (which
was never a real exported symbol to begin with):

```text
myapp requires detail::train_ops_dispatcher via public entry train:
  train() → detail::train_ops_dispatcher()
```

The app's import table alone would only show that it requires
`detail::train_ops_dispatcher` — an internal-looking name with no obvious
reason to be a consumer's problem. The proof path is what explains that
`train`, the header's own inline public entry point, is what made the app
depend on it directly.

### Where this shows up in the report

- On the synthesized "missing required symbol" finding (for a symbol the
  library diff itself has no ordinary change for), and
- On an **ordinary** library change (e.g. `FUNC_REMOVED`) that already
  covers the same symbol — in that case the wording is consumer-neutral
  (`"... is reachable from public entry train: ..."`, no app name), since
  that finding is also rendered in the unscoped, full-library report.

In `--format json`, the structured chain is `impact_proof_path` — an
alternating list of node/edge dicts — alongside `affected_public_roots`
(the public entry point name(s)) and `impact_is_direct`. These live on the
`ImpactAssessment.proof_path` object described in the [Impact
Analysis](../learn/impact-analysis.md) reference for the general model this
feature builds on; this section only covers the consumer-scoped join.

### Current limits

This is a **static** proof over the graphs abicheck already builds, not a
runtime trace: it does not ingest anything the app actually did at
runtime, doesn't yet read a project-declared use-case manifest, and doesn't
yet join **multiple** `--used-by` apps into one shared graph (each app's
scoping is computed independently, repeatably — not a unified
multi-consumer picture). There is also no consumer-side build-evidence
edge yet (what the *consumer's own* source does with the symbol, as opposed
to what the library's old implementation does) — only the library side of
the chain is graph-backed.

---

## Supported binary formats

| Format | Application | Library | Symbol filtering |
|--------|------------|---------|-----------------|
| **ELF** (Linux) | `.so`, executables | `.so` | `.gnu.version` + `.gnu.version_r` correlation |
| **PE** (Windows) | `.exe`, `.dll` | `.dll` | Import table DLL name matching (incl. ordinal imports) |
| **Mach-O** (macOS) | executables, `.dylib` | `.dylib` | Two-level namespace library ordinal |

---

## CI integration

### GitHub Actions example

Check if your application works with a library update in CI:

```yaml
- name: Check app compatibility
  run: |
    abicheck compare libfoo.so.1 ./build/libfoo.so.2 \
      --used-by ./build/myapp \
      -H include/foo.h \
      --format json -o appcompat.json
```

---

## Python API

```python
from pathlib import Path
from abicheck.appcompat import check_appcompat, check_against, parse_app_requirements

# Full mode (old + new library) — app_path, old_lib_path, new_lib_path
result = check_appcompat(
    Path("./myapp"), Path("libfoo.so.1"), Path("libfoo.so.2"),
)
print(result.verdict, result.symbol_coverage)

# Weak mode (no old library — symbol availability only)
weak = check_against(Path("./myapp"), Path("libfoo.so.2"))
print(weak.missing_symbols)

# List required symbols only (library_name filters which needed-lib's
# imports are reported, e.g. the SONAME)
reqs = parse_app_requirements(Path("./myapp"), "libfoo.so.1")
print(reqs.undefined_symbols, reqs.needed_libs, reqs.required_versions)
```
